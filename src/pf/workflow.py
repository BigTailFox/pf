from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from pf.environment import PreparedEnvironment
from pf.errors import ConfigurationError, InfrastructureError
from pf.evaluation import require_full_evaluation_contract
from pf.policy import evaluation_policy_identity
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    ActivityEvent,
    Attempt,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CellFailureScope,
    CellMatrixEvent,
    CheckCellOutcome,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    CheckResult,
    Evaluation,
    FailureCause,
    FailureRecord,
    HighestVersionOutcome,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    PrepareFailure,
    ProcessResult,
    ProgressEvent,
    SearchFailureEvent,
    SmokeIndeterminate,
    SmokeBaselineRejection,
    SmokePass,
    SmokeResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticFailEvaluation,
    StatusEvent,
    TestFailEvaluation,
    ToolFailure,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationRole,
)
from pf.report import PackageReportBuilder, ReportStore
from pf.scheduling import ProgressConsumer, ScheduledCellTask, Scheduler
from pf.schemas.project import Cell, PackagePlan
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    DiagnoseRequest,
    MergeRequest,
    ReportRequest,
    SearchRequest,
    SmokeRequest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
    CoordinateSuccess,
    PackageFloorReportV1,
    ProbeIndeterminate,
    ProbeRejection,
    ProjectEditResult,
)
from pf.project import ProjectLoader, host_target as current_host_target
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot


def selected_host_cells(
    packages: tuple[PackagePlan, ...], host_target: str
) -> tuple[Cell, ...]:
    return tuple(
        cell
        for package in packages
        for cell in package.cells
        if cell.target == host_target
    )


def persist_verification_journal(
    logs: JournalStore | None,
    *,
    command: Literal["smoke", "check", "search"],
    packages: tuple[PackagePlan, ...],
    snapshot: SourceSnapshot,
    entries: tuple[VerificationJournalEntry, ...],
) -> None:
    if logs is None or not packages or not hasattr(logs, "write_journal"):
        return
    logs.write_journal(
        VerificationJournal(
            run_id=logs.run_id,
            command=command,
            packages=tuple(package.name for package in packages),
            source_snapshot_digest=snapshot.identity.digest,
            evaluation_policy_identity=evaluation_policy_identity(packages[0].config),
            entries=entries,
        )
    )


def _journal_entry_for_failure(
    *,
    package: str,
    cell: Cell,
    role: VerificationRole | None,
    failure: FailureRecord,
) -> VerificationJournalEntry:
    attempt = (
        failure.scope.attempt
        if isinstance(failure.scope, AttemptFailureScope)
        else None
    )
    resolved: VerificationRole
    if role is not None:
        resolved = role
    elif attempt is None:
        resolved = "probe"
    elif attempt.identity.requested_resolution == "highest":
        resolved = "baseline"
    elif attempt.identity.requested_resolution == "lowest-direct":
        resolved = "declaration"
    else:
        resolved = "probe"
    return VerificationJournalEntry(
        package=package,
        cell=cell,
        role=resolved,
        attempt=attempt,
        failure=failure,
    )


class _JournalGate:
    """Persist journal entries before the presenter freezes a Diagnose card."""

    def __init__(
        self,
        inner: ProgressConsumer,
        *,
        logs: JournalStore | None,
        command: Literal["smoke", "check", "search"],
        packages: tuple[PackagePlan, ...],
        snapshot: SourceSnapshot,
    ) -> None:
        self._inner = inner
        self._logs = logs
        self._command = command
        self._packages = packages
        self._snapshot = snapshot
        self._entries: list[VerificationJournalEntry] = []
        self._cell_ok: dict[tuple[str, str, str, tuple[str, ...]], bool] = {}
        self._lock = Lock()
        self.error: InfrastructureError | None = None

    def consume(self, event: ActivityEvent) -> None:
        forwarded: ActivityEvent = event
        if isinstance(event, (ProgressEvent, SearchFailureEvent)) and event.failure is not None:
            available = self._persist(
                _journal_entry_for_failure(
                    package=event.package if isinstance(event, ProgressEvent) else event.cell.package,
                    cell=event.cell,
                    role=event.verification_role if isinstance(event, ProgressEvent) else None,
                    failure=event.failure,
                ),
                cell=event.cell,
            )
            if isinstance(event, ProgressEvent):
                forwarded = event.model_copy(
                    update={"diagnose_available": available}
                )
        elif isinstance(event, ProgressEvent) and event.phase != "start":
            key = _cell_identity(event.cell)
            with self._lock:
                available = self._cell_ok.get(key, True)
            if not available:
                forwarded = event.model_copy(update={"diagnose_available": False})
        self._inner.consume(forwarded)

    def _persist(self, entry: VerificationJournalEntry, *, cell: Cell) -> bool:
        key = _cell_identity(cell)
        with self._lock:
            if all(
                existing.failure.failure_id != entry.failure.failure_id
                for existing in self._entries
            ):
                self._entries.append(entry)
            if self.error is not None:
                self._cell_ok[key] = False
                return False
            try:
                persist_verification_journal(
                    self._logs,
                    command=self._command,
                    packages=self._packages,
                    snapshot=self._snapshot,
                    entries=tuple(self._entries),
                )
            except InfrastructureError as error:
                self.error = error
                self._cell_ok[key] = False
                return False
            self._cell_ok[key] = self._cell_ok.get(key, True)
            return self._cell_ok[key]

    def raise_if_failed(self) -> None:
        if self.error is not None:
            raise self.error


def _cell_identity(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.target, cell.python_minor, cell.extra_surface)


class CheckEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
    ) -> PreparedEnvironment | PrepareFailure: ...


class CheckStaticOperations(Protocol):
    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture | IndeterminateEvaluation: ...


class CheckFullOperations(Protocol):
    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> Evaluation: ...


class CheckCellOperations(Protocol):
    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CheckCellOutcome: ...


class CompatibilityChecker:
    """Validate current declarations for one cell without searching."""

    def __init__(
        self,
        *,
        environments: CheckEnvironmentOperations,
        static: CheckStaticOperations,
        full: CheckFullOperations,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._failures = failures or FailurePolicy()

    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CheckCellOutcome:
        require_full_evaluation_contract(package, "check")
        highest = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="highest",
        )
        if isinstance(highest, ToolFailure):
            raise ValueError("check prepare must establish an Attempt")
        if isinstance(highest, PrepareFailure):
            return self._prepare_outcome(highest, role="declaration-capture")
        try:
            capture = self._static.capture(highest, package=package)
        finally:
            highest.close()
        if isinstance(capture, IndeterminateEvaluation):
            return self._evaluation_outcome(
                attempt=highest.attempt,
                role="declaration-capture",
                evaluation=capture,
                static_baseline=None,
            )
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="lowest-direct",
        )
        if isinstance(prepared, ToolFailure):
            raise ValueError("check prepare must establish an Attempt")
        if isinstance(prepared, PrepareFailure):
            return self._prepare_outcome(prepared, role="declaration")
        try:
            evaluation = self._full.evaluate(
                prepared,
                package=package,
                baseline=capture.baseline,
            )
        finally:
            prepared.close()
        return self._evaluation_outcome(
            attempt=prepared.attempt,
            role="declaration",
            evaluation=evaluation,
            static_baseline=capture.baseline,
        )

    def _prepare_outcome(
        self,
        prepared: PrepareFailure,
        *,
        role: Literal["declaration-capture", "declaration"],
    ) -> CheckCellOutcome:
        failure = self._failures.classify(
            scope=AttemptFailureScope(attempt=prepared.attempt),
            cause=prepared.failure.cause,
            stage=prepared.failure.stage,
            process=prepared.failure.process,
            summary_code=prepared.failure.summary_code,
        )
        return CheckCellOutcome(
            status=failure.disposition,
            role=role,
            attempt=prepared.attempt,
            failure=failure,
        )

    def _evaluation_outcome(
        self,
        *,
        attempt: Attempt,
        role: Literal["declaration-capture", "declaration"],
        evaluation: Evaluation,
        static_baseline: StaticBaseline | None,
    ) -> CheckCellOutcome:
        if isinstance(evaluation, PassEvaluation):
            return CheckCellOutcome(
                status="PASS",
                role=role,
                attempt=attempt,
                evaluation=evaluation,
                static_baseline=static_baseline,
            )
        if isinstance(evaluation, StaticFailEvaluation):
            cause: FailureCause = "STATIC_REGRESSION"
            stage = "ty"
            process = evaluation.ty.process
            summary_code = None
        elif isinstance(evaluation, TestFailEvaluation):
            cause = "TEST_FAILURE"
            stage = "test"
            process = evaluation.test.process
            summary_code = None
        else:
            cause = evaluation.cause
            stage = evaluation.failure.stage
            process = evaluation.failure.process
            summary_code = evaluation.failure.summary_code
        failure = self._failures.classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
        )
        return CheckCellOutcome(
            status=failure.disposition,
            role=role,
            attempt=attempt,
            failure=failure,
            evaluation=evaluation,
            static_baseline=static_baseline,
        )


class JournalStore(Protocol):
    run_id: str

    def write_journal(self, journal: VerificationJournal) -> Path: ...


class CheckCommandWorkflow:
    """Load, snapshot, and check every package selected by one CLI request."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        checker: CheckCellOperations,
        scheduler: Scheduler,
        events: ProgressConsumer,
        logs: JournalStore | None = None,
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._checker = checker
        self._scheduler = scheduler
        self._events = events
        self._logs = logs
        self._host_target = host_target or current_host_target()

    def run(self, request: CheckRequest) -> CheckResult:
        root = Path(request.root)
        self._emit(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        self._emit(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(root)
        try:
            self._emit(StatusEvent(message="checking declarations"))
            cells = selected_host_cells(project.packages, self._host_target)
            self._emit(CellMatrixEvent(cells=cells))
            for package in project.packages:
                require_full_evaluation_contract(package, "check")
            if not cells:
                raise ConfigurationError(
                    f"no configured cell matches host target: {self._host_target}"
                )
            package_by_name = {package.name: package for package in project.packages}
            gate = _JournalGate(
                self._events,
                logs=self._logs,
                command="check",
                packages=project.packages,
                snapshot=snapshot,
            )
            outcomes = self._scheduler.run(
                tuple(
                    ScheduledCellTask(
                        cell=cell,
                        run=self._cell_task(
                            package_by_name[cell.package],
                            cell,
                            snapshot,
                        ),
                    )
                    for cell in cells
                ),
                jobs=request.jobs,
                max_duration_seconds=None,
                events=gate,
            )
            result = self._aggregate(outcomes)
            persist_verification_journal(
                self._logs,
                command="check",
                packages=project.packages,
                snapshot=snapshot,
                entries=tuple(
                    VerificationJournalEntry(
                        package=outcome.attempt.identity.cell.package,
                        cell=outcome.attempt.identity.cell,
                        role=outcome.role,
                        attempt=outcome.attempt,
                        failure=outcome.failure,
                    )
                    for outcome in outcomes
                    if outcome.failure is not None
                ),
            )
            gate.raise_if_failed()
            return result
        finally:
            snapshot.close()

    def _cell_task(
        self,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> Callable[[], CheckCellOutcome]:
        def run() -> CheckCellOutcome:
            return self._checker.check(
                package=package,
                cell=cell,
                snapshot=snapshot,
            )

        return run

    @staticmethod
    def _aggregate(
        outcomes: tuple[CheckCellOutcome, ...],
    ) -> CheckResult:
        evaluations = tuple(
            outcome.evaluation
            for outcome in outcomes
            if outcome.evaluation is not None
        )
        if any(outcome.status == "REJECTED" for outcome in outcomes):
            return CheckCompatibilityFailure(
                evaluations=evaluations,
                outcomes=outcomes,
            )
        failed = next(
            (outcome for outcome in outcomes if outcome.status == "INDETERMINATE"),
            None,
        )
        if failed is not None:
            assert failed.failure is not None
            return CheckIndeterminate(
                evaluations=tuple(
                    item for item in evaluations if isinstance(item, PassEvaluation)
                ),
                failure=failed.failure,
                outcomes=outcomes,
            )
        return CheckPass(
            evaluations=tuple(
                item for item in evaluations if isinstance(item, PassEvaluation)
            ),
            outcomes=outcomes,
        )

    def _emit(self, event: StatusEvent | CellMatrixEvent) -> None:
        self._events.consume(event)


class SmokeCellOperations(Protocol):
    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> HighestVersionOutcome: ...


class SmokeCommandWorkflow:
    """Load, snapshot, and verify highest resolutions for selected host cells."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        verifier: SmokeCellOperations,
        scheduler: Scheduler,
        events: ProgressConsumer,
        logs: JournalStore | None = None,
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._verifier = verifier
        self._scheduler = scheduler
        self._events = events
        self._logs = logs
        self._host_target = host_target or current_host_target()

    def run(self, request: SmokeRequest) -> SmokeResult:
        root = Path(request.root)
        self._emit(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        self._emit(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(root)
        try:
            self._emit(StatusEvent(message="smoke testing"))
            cells = selected_host_cells(project.packages, self._host_target)
            self._emit(CellMatrixEvent(cells=cells))
            for package in project.packages:
                require_full_evaluation_contract(package, "smoke")
            if not cells:
                raise ConfigurationError(
                    f"no configured cell matches host target: {self._host_target}"
                )
            package_by_name = {package.name: package for package in project.packages}
            gate = _JournalGate(
                self._events,
                logs=self._logs,
                command="smoke",
                packages=project.packages,
                snapshot=snapshot,
            )
            outcomes = self._scheduler.run(
                tuple(
                    ScheduledCellTask(
                        cell=cell,
                        run=self._cell_task(
                            package_by_name[cell.package],
                            cell,
                            snapshot,
                        ),
                    )
                    for cell in cells
                ),
                jobs=request.jobs,
                max_duration_seconds=None,
                events=gate,
            )
            result = self._aggregate(outcomes)
            persist_verification_journal(
                self._logs,
                command="smoke",
                packages=project.packages,
                snapshot=snapshot,
                entries=tuple(
                    VerificationJournalEntry(
                        package=outcome.cell.package,
                        cell=outcome.cell,
                        role="baseline",
                        attempt=outcome.attempt,
                        failure=outcome.failure,
                    )
                    for outcome in outcomes
                    if isinstance(
                        outcome, (BaselineRejection, BaselineIndeterminate)
                    )
                ),
            )
            gate.raise_if_failed()
            return result
        finally:
            snapshot.close()

    def _cell_task(
        self,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> Callable[[], HighestVersionOutcome]:
        def run() -> HighestVersionOutcome:
            return self._verifier.verify(
                package=package,
                cell=cell,
                snapshot=snapshot,
            )

        return run

    @staticmethod
    def _aggregate(
        outcomes: tuple[HighestVersionOutcome, ...],
    ) -> SmokeResult:
        if any(isinstance(item, BaselineRejection) for item in outcomes):
            return SmokeBaselineRejection(outcomes=outcomes)
        if any(isinstance(item, BaselineIndeterminate) for item in outcomes):
            narrowed = tuple(
                item
                for item in outcomes
                if isinstance(item, (HighestVersionPass, BaselineIndeterminate))
            )
            return SmokeIndeterminate(outcomes=narrowed)
        return SmokePass(
            outcomes=tuple(
                item for item in outcomes if isinstance(item, HighestVersionPass)
            )
        )

    def _emit(self, event: StatusEvent | CellMatrixEvent) -> None:
        self._events.consume(event)


class CellSearchOperations(Protocol):
    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CellResult: ...


class FailureLogAssociations(Protocol):
    def replace_associations(
        self,
        report_generation_id: str,
        failures: tuple[tuple[str, ProcessResult | None], ...],
        *,
        replace_generation: bool = True,
        remove_failure_ids: tuple[str, ...] = (),
    ) -> None: ...


class SearchLogStore(JournalStore, FailureLogAssociations, Protocol):
    pass


class SearchCommandWorkflow:
    """Own load, snapshot, bounded cell scheduling, and report persistence."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        coordinator: CellSearchOperations,
        scheduler: Scheduler,
        reports: ReportStore,
        report_builder: PackageReportBuilder,
        events: ProgressConsumer,
        logs: SearchLogStore | None = None,
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._coordinator = coordinator
        self._scheduler = scheduler
        self._reports = reports
        self._report_builder = report_builder
        self._events = events
        self._logs = logs
        self._host_target = host_target or current_host_target()

    def run(self, request: SearchRequest) -> tuple[PackageFloorReportV1, ...]:
        root = Path(request.root)
        self._events.consume(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        self._events.consume(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(root)
        try:
            self._events.consume(StatusEvent(message="searching cells"))
            tasks = tuple(
                ScheduledCellTask(
                    cell=cell,
                    run=self._cell_task(package, cell, snapshot),
                    deadline_scope=CellFailureScope(
                        package=package.name,
                        cell=cell,
                        source_snapshot_digest=snapshot.identity.digest,
                        evaluation_policy_identity=evaluation_policy_identity(
                            package.config
                        ),
                    ),
                )
                for package in project.packages
                for cell in package.cells
                if cell.target == self._host_target
            )
            self._events.consume(
                CellMatrixEvent(cells=tuple(task.cell for task in tasks))
            )
            gate = _JournalGate(
                self._events,
                logs=self._logs,
                command="search",
                packages=project.packages,
                snapshot=snapshot,
            )
            results = self._scheduler.run(
                tasks,
                jobs=request.jobs,
                max_duration_seconds=request.max_duration_seconds,
                events=gate,
            )
            reports = []
            for package in project.packages:
                package_results = tuple(
                    result for result in results if result.cell.package == package.name
                )
                report = self._report_builder.build(
                    package=package,
                    source_snapshot=snapshot.identity,
                    cell_results=package_results,
                )
                report_path = (
                    root / Path(package.pyproject_path).parent / "package-floor.json"
                )
                existing = None
                if report_path.is_file():
                    existing = self._reports.read_if_same_generation(
                        report_path,
                        report,
                    )
                    if existing is not None:
                        report = self._reports.update(existing, report)
                self._reports.write(report_path, report)
                if self._logs is not None:
                    replaced_cells = {result.cell for result in package_results}
                    remove_failure_ids = (
                        tuple(
                            failure.failure_id
                            for old_result in existing.cell_results
                            if old_result.cell in replaced_cells
                            for failure in self._failure_records(old_result)
                        )
                        if existing is not None
                        else ()
                    )
                    current_failures = tuple(
                        (failure.failure_id, failure.process)
                        for result in package_results
                        for failure in self._failure_records(result)
                    )
                    self._logs.replace_associations(
                        report.report_generation_id,
                        current_failures,
                        replace_generation=existing is None,
                        remove_failure_ids=remove_failure_ids,
                    )
                reports.append(report)
            persist_verification_journal(
                self._logs,
                command="search",
                packages=project.packages,
                snapshot=snapshot,
                entries=self._journal_entries(results),
            )
            gate.raise_if_failed()
            return tuple(reports)
        finally:
            snapshot.close()

    def _cell_task(
        self,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> Callable[[], CellResult]:
        def run() -> CellResult:
            return self._coordinator.search(
                package=package,
                cell=cell,
                snapshot=snapshot,
            )

        return run

    @classmethod
    def _journal_entries(
        cls,
        results: tuple[CellResult, ...],
    ) -> tuple[VerificationJournalEntry, ...]:
        entries: list[VerificationJournalEntry] = []
        for result in results:
            for failure in cls._failure_records(result):
                if isinstance(failure.scope, AttemptFailureScope):
                    attempt = failure.scope.attempt
                    role: VerificationRole = (
                        "baseline"
                        if attempt.identity.requested_resolution == "highest"
                        else "probe"
                    )
                    cell = attempt.identity.cell
                else:
                    role = "probe"
                    attempt = None
                    cell = failure.scope.cell
                entries.append(
                    VerificationJournalEntry(
                        package=cell.package,
                        cell=cell,
                        role=role,
                        attempt=attempt,
                        failure=failure,
                    )
                )
        return tuple(entries)

    @staticmethod
    def _failure_records(result: CellResult) -> tuple[FailureRecord, ...]:
        if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
            return (result.failure,)
        return result.failure_records


class ExplainCommandWorkflow:
    """Locate and read reports without owning any evaluation capability."""

    def __init__(self, *, projects: ProjectLoader, reports: ReportStore) -> None:
        self._projects = projects
        self._reports = reports

    def run(self, request: ReportRequest) -> tuple[PackageFloorReportV1, ...]:
        root = Path(request.root)
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        return tuple(
            self._reports.read(
                root / Path(package.pyproject_path).parent / "package-floor.json"
            )
            for package in project.packages
        )


class DiagnosisLogLocator(Protocol):
    def lookup(
        self,
        report_generation_id: str,
        failure_id: str,
    ) -> Path | None: ...

    def lookup_run(self, run_id: str, failure_id: str) -> Path | None: ...

    def read_latest_journal(self, package: str) -> VerificationJournal | None: ...


@dataclass(frozen=True)
class FailureDiagnosis:
    report_generation_id: str
    package: str
    failure: FailureRecord
    proposal_id: str | None
    boundary_role: Literal["predecessor"] | None
    log_path: Path | None
    source: Literal["package-floor.json", "journal"] = "package-floor.json"
    verification_role: VerificationRole | None = None
    command: Literal["smoke", "check", "search"] | None = None


class DiagnoseCommandWorkflow:
    """Resolve portable report failures and optional local logs without execution."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        reports: ReportStore,
        logs: DiagnosisLogLocator,
    ) -> None:
        self._projects = projects
        self._reports = reports
        self._logs = logs

    def run(self, request: DiagnoseRequest) -> tuple[FailureDiagnosis, ...]:
        root = Path(request.root)
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        entries: list[FailureDiagnosis] = []
        for package in project.packages:
            report_path = (
                root / Path(package.pyproject_path).parent / "package-floor.json"
            )
            seen: set[str] = set()
            if report_path.is_file():
                report = self._reports.read(report_path)
                for result in report.cell_results:
                    for failure in self._failure_records(result):
                        if (
                            request.failure_id is not None
                            and failure.failure_id != request.failure_id
                        ):
                            continue
                        seen.add(failure.failure_id)
                        proposal_id, boundary_role = self._search_context(
                            result,
                            failure.failure_id,
                        )
                        entries.append(
                            FailureDiagnosis(
                                report_generation_id=report.report_generation_id,
                                package=report.package.name,
                                failure=failure,
                                proposal_id=proposal_id,
                                boundary_role=boundary_role,
                                log_path=self._logs.lookup(
                                    report.report_generation_id,
                                    failure.failure_id,
                                ),
                                source="package-floor.json",
                            )
                        )
            reader = getattr(self._logs, "read_latest_journal", None)
            journal = reader(package.name) if callable(reader) else None
            if journal is None:
                continue
            for item in journal.entries:
                if item.failure.failure_id in seen:
                    continue
                if (
                    request.failure_id is not None
                    and item.failure.failure_id != request.failure_id
                ):
                    continue
                seen.add(item.failure.failure_id)
                entries.append(
                    FailureDiagnosis(
                        report_generation_id=journal.run_id,
                        package=item.package,
                        failure=item.failure,
                        proposal_id=None,
                        boundary_role=None,
                        log_path=(
                            self._logs.lookup_run(
                                journal.run_id,
                                item.failure.failure_id,
                            )
                            if hasattr(self._logs, "lookup_run")
                            else None
                        ),
                        source="journal",
                        verification_role=item.role,
                        command=journal.command,
                    )
                )
        if request.failure_id is not None and not entries:
            raise ConfigurationError(f"failure ID not found: {request.failure_id}")
        return tuple(sorted(entries, key=self._sort_key))

    @staticmethod
    def _failure_records(result: CellResult) -> tuple[FailureRecord, ...]:
        if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
            return (result.failure,)
        return result.failure_records

    @staticmethod
    def _search_context(
        result: CellResult,
        failure_id: str,
    ) -> tuple[str | None, Literal["predecessor"] | None]:
        if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
            proposal_id = (
                result.evaluation.proposal.proposal_id
                if result.evaluation is not None
                else None
            )
            return proposal_id, None
        searches = ()
        if isinstance(result, CellSuccess):
            searches = (
                result.static_search,
                *(
                    (result.dynamic_search,)
                    if result.dynamic_search is not None
                    else ()
                ),
            )
        elif isinstance(result, (CellIndeterminate, CellSearchFailure)) and (
            result.coordinate_failure is not None
        ):
            searches = (result.coordinate_failure,)
        proposal_id: str | None = None
        boundary_role: Literal["predecessor"] | None = None
        for search in searches:
            for observation in search.observations:
                evidence = observation.evidence
                if isinstance(evidence, (ProbeRejection, ProbeIndeterminate)) and (
                    evidence.failure_id == failure_id
                ):
                    proposal_id = evidence.proposal_id
            if isinstance(search, CoordinateSuccess) and any(
                boundary.predecessor_failure_id == failure_id
                for boundary in search.boundaries
            ):
                boundary_role = "predecessor"
        return proposal_id, boundary_role

    @staticmethod
    def _sort_key(entry: FailureDiagnosis) -> tuple[object, ...]:
        scope = entry.failure.scope
        cell = (
            scope.attempt.identity.cell
            if isinstance(scope, AttemptFailureScope)
            else scope.cell
        )
        if isinstance(scope, AttemptFailureScope):
            identity = scope.attempt.identity
            resolution_rank = 0 if identity.requested_resolution == "highest" else 1
            vector = tuple(
                (pin.name, pin.version)
                for pin in (identity.requested_managed_vector or ())
            )
        else:
            resolution_rank = -1
            vector = ()
        return (
            entry.package,
            cell.target,
            cell.python_minor,
            cell.extra_surface,
            resolution_rank,
            vector,
            entry.failure.failure_id,
        )


class MergeCommandWorkflow:
    def __init__(self, *, reports: ReportStore) -> None:
        self._reports = reports

    def run(self, request: MergeRequest) -> PackageFloorReportV1:
        merged = self._reports.merge(
            tuple(self._reports.read(Path(path)) for path in request.reports)
        )
        self._reports.write(Path(request.output), merged)
        return merged


class ProjectEditOperations(Protocol):
    def apply_many(
        self,
        *,
        reports: tuple[PackageFloorReportV1, ...],
        root: Path,
    ) -> tuple[ProjectEditResult, ...]: ...


class ApplyCommandWorkflow:
    """Transform complete reports into metadata edits without evaluation."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        reports: ReportStore,
        editor: ProjectEditOperations,
        events: ProgressConsumer | None = None,
    ) -> None:
        self._projects = projects
        self._reports = reports
        self._editor = editor
        self._events = events

    def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]:
        root = Path(request.root)
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        if self._events is not None:
            self._events.consume(
                StatusEvent(
                    message="applying floors",
                    total=len(project.packages) or None,
                )
            )
        reports = []
        for package in project.packages:
            report_path = (
                root / Path(package.pyproject_path).parent / "package-floor.json"
            )
            report = self._reports.read(report_path)
            if (
                report.package.name != package.name
                or report.package.pyproject_path != package.pyproject_path
            ):
                raise ConfigurationError("report package identity mismatch")
            if report.policy_identity != evaluation_policy_identity(package.config):
                raise ConfigurationError("report policy identity mismatch")
            reports.append(report)
        return self._editor.apply_many(reports=tuple(reports), root=root)

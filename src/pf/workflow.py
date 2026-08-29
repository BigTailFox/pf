from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pf.environment import (
    HighestResolution,
    LowestDirectResolution,
    PreparedEnvironment,
    ResolutionRequest,
)
from pf.errors import ConfigurationError
from pf.evaluation import require_full_evaluation_contract
from pf.policy import evaluation_policy_identity
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    BaselineDetailIdentity,
    CellContextEvent,
    CellFailureScope,
    CellMatrixEvent,
    CheckCellOutcome,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    CheckResult,
    DeclarationDetailIdentity,
    Evaluation,
    FailureRecord,
    HighestVersionOutcome,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    PrepareFailure,
    ProcessObservation,
    ProcessTerminalUnavailable,
    RuntimeEvaluationRun,
    SmokeIndeterminate,
    SmokeBaselineRejection,
    SmokePass,
    SmokeResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StatusEvent,
    ToolFailure,
    VerificationJournalEntry,
    VerificationJournalRecord,
    VerificationRole,
)
from pf.schemas.apply import ApplyCommandResult, AuthorizedWorkspaceApply
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.schemas.project import Cell, PackagePlan, ProjectPlan
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
    CellResult,
    ProjectEditResult,
    failure_records_for_result,
    failure_runtime_runs_for_result,
)
from pf.project import ProjectLoader, host_target as current_host_target
from pf.project_discovery import ProjectDiscovery
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot
from pf.verification import (
    ActivityConsumer,
    VerificationRun,
    VerificationRunner,
    VerificationTask,
)


def selected_host_cells(
    packages: tuple[PackagePlan, ...], host_target: str
) -> tuple[Cell, ...]:
    return tuple(
        cell
        for package in packages
        for cell in package.cells
        if cell.target == host_target
    )


def _cell_matrix_event(
    packages: tuple[PackagePlan, ...],
    cells: tuple[Cell, ...],
) -> CellMatrixEvent:
    packages_by_name = {package.name: package for package in packages}
    active_names: set[str] = set()
    pinned_names: set[str] = set()
    for cell in cells:
        package = packages_by_name[cell.package]
        active_ids = set(cell.active_declaration_ids)
        for declaration in package.declarations:
            if declaration.declaration_id not in active_ids:
                continue
            active_names.add(declaration.name)
            if declaration.kind == "fixed":
                pinned_names.add(declaration.name)
    return CellMatrixEvent(
        cells=cells,
        active_packages=len(active_names),
        pinned_packages=len(pinned_names),
    )


class CheckEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: ResolutionRequest,
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
    ) -> RuntimeEvaluationRun: ...


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
        events: ActivityConsumer | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._failures = failures or FailurePolicy()
        self._events = events

    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CheckCellOutcome:
        require_full_evaluation_contract(package, "check")
        if self._events is not None:
            self._events.consume(
                CellContextEvent(cell=cell, detail=BaselineDetailIdentity())
            )
        highest = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=HighestResolution(),
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
                project_plan_digest=highest.project_plan.semantic_digest,
                environment_plan_digest=highest.environment_plan.semantic_digest,
            )
        if self._events is not None:
            self._events.consume(
                CellContextEvent(cell=cell, detail=DeclarationDetailIdentity())
            )
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=LowestDirectResolution(highest.harness_baseline),
        )
        if isinstance(prepared, ToolFailure):
            raise ValueError("check prepare must establish an Attempt")
        if isinstance(prepared, PrepareFailure):
            return self._prepare_outcome(prepared, role="declaration")
        try:
            runtime = self._full.evaluate(
                prepared,
                package=package,
                baseline=capture.baseline,
            )
        finally:
            prepared.close()
        return self._evaluation_outcome(
            attempt=prepared.attempt,
            role="declaration",
            evaluation=runtime.evaluation,
            runtime=runtime,
            static_baseline=capture.baseline,
            project_plan_digest=prepared.project_plan.semantic_digest,
            environment_plan_digest=prepared.environment_plan.semantic_digest,
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
            project_plan_digest=prepared.project_plan_digest,
            environment_plan_digest=prepared.environment_plan_digest,
        )
        return CheckCellOutcome(
            status=failure.disposition,
            role=role,
            attempt=prepared.attempt,
            failure=failure,
            failure_process=(
                prepared.failure.process
                if isinstance(
                    prepared.failure.process,
                    ProcessTerminalUnavailable,
                )
                else None
            ),
        )

    def _evaluation_outcome(
        self,
        *,
        attempt: Attempt,
        role: Literal["declaration-capture", "declaration"],
        evaluation: Evaluation,
        runtime: RuntimeEvaluationRun | None = None,
        static_baseline: StaticBaseline | None,
        project_plan_digest: str,
        environment_plan_digest: str,
    ) -> CheckCellOutcome:
        if isinstance(evaluation, PassEvaluation):
            return CheckCellOutcome(
                status="PASS",
                role=role,
                attempt=attempt,
                evaluation=evaluation,
                static_baseline=static_baseline,
                runtime=runtime,
            )
        failure = self._failures.record_evaluation(
            AttemptFailureScope(attempt=attempt),
            evaluation,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )
        assert failure is not None
        return CheckCellOutcome(
            status=failure.disposition,
            role=role,
            attempt=attempt,
            failure=failure,
            evaluation=evaluation,
            static_baseline=static_baseline,
            runtime=runtime,
        )


class CheckCommandWorkflow:
    """Load, snapshot, and check every package selected by one CLI request."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        checker: CheckCellOperations,
        verification: VerificationRunner,
        events: ActivityConsumer,
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._checker = checker
        self._verification = verification
        self._events = events
        self._host_target = host_target or current_host_target()

    def run(self, request: CheckRequest) -> CheckResult:
        root = Path(request.root)
        self._emit(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        self._emit(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            self._emit(StatusEvent(message="checking declarations"))
            cells = selected_host_cells(project.packages, self._host_target)
            self._emit(_cell_matrix_event(project.packages, cells))
            for package in project.packages:
                require_full_evaluation_contract(package, "check")
            if not cells:
                raise ConfigurationError(
                    f"no configured cell matches host target: {self._host_target}"
                )
            package_by_name = {package.name: package for package in project.packages}
            outcomes = self._verification.run(
                VerificationRun(
                    command="check",
                    packages=project.packages,
                    snapshot=snapshot,
                    tasks=tuple(
                        VerificationTask(
                            cell=cell,
                            execute=self._cell_task(
                                package_by_name[cell.package],
                                cell,
                                snapshot,
                            ),
                            journal_entries=self._journal_entries,
                        )
                        for cell in cells
                    ),
                    jobs=request.jobs,
                    max_duration_seconds=None,
                )
            )
            result = self._aggregate(outcomes)
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
    def _journal_entries(
        outcome: CheckCellOutcome,
    ) -> tuple[VerificationJournalEntry, ...]:
        if outcome.failure is None:
            return ()
        return (
            VerificationJournalEntry(
                package=outcome.attempt.identity.cell.package,
                cell=outcome.attempt.identity.cell,
                role=outcome.role,
                attempt=outcome.attempt,
                failure=outcome.failure,
            ),
        )

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
        verification: VerificationRunner,
        events: ActivityConsumer,
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._verifier = verifier
        self._verification = verification
        self._events = events
        self._host_target = host_target or current_host_target()

    def run(self, request: SmokeRequest) -> SmokeResult:
        root = Path(request.root)
        self._emit(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        self._emit(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            self._emit(StatusEvent(message="smoke testing"))
            cells = selected_host_cells(project.packages, self._host_target)
            self._emit(_cell_matrix_event(project.packages, cells))
            for package in project.packages:
                require_full_evaluation_contract(package, "smoke")
            if not cells:
                raise ConfigurationError(
                    f"no configured cell matches host target: {self._host_target}"
                )
            package_by_name = {package.name: package for package in project.packages}
            outcomes = self._verification.run(
                VerificationRun(
                    command="smoke",
                    packages=project.packages,
                    snapshot=snapshot,
                    tasks=tuple(
                        VerificationTask(
                            cell=cell,
                            execute=self._cell_task(
                                package_by_name[cell.package],
                                cell,
                                snapshot,
                            ),
                            journal_entries=self._journal_entries,
                        )
                        for cell in cells
                    ),
                    jobs=request.jobs,
                    max_duration_seconds=None,
                ),
            )
            result = self._aggregate(outcomes)
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
            self._events.consume(
                CellContextEvent(cell=cell, detail=BaselineDetailIdentity())
            )
            return self._verifier.verify(
                package=package,
                cell=cell,
                snapshot=snapshot,
            )

        return run

    @staticmethod
    def _journal_entries(
        outcome: HighestVersionOutcome,
    ) -> tuple[VerificationJournalEntry, ...]:
        if not isinstance(outcome, (BaselineRejection, BaselineIndeterminate)):
            return ()
        return (
            VerificationJournalEntry(
                package=outcome.cell.package,
                cell=outcome.cell,
                role="baseline",
                attempt=outcome.attempt,
                failure=outcome.failure,
            ),
        )

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
        failures: tuple[tuple[str, ProcessObservation | None], ...],
        *,
        replace_generation: bool = True,
        remove_failure_ids: tuple[str, ...] = (),
    ) -> None: ...


class SearchCommandWorkflow:
    """Own load, snapshot, bounded cell scheduling, and report persistence."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        coordinator: CellSearchOperations,
        verification: VerificationRunner,
        reports: ReportStore,
        report_builder: PackageReportBuilder,
        events: ActivityConsumer,
        associations: FailureLogAssociations | None = None,
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._coordinator = coordinator
        self._verification = verification
        self._reports = reports
        self._report_builder = report_builder
        self._events = events
        self._associations = associations
        self._host_target = host_target or current_host_target()

    def run(self, request: SearchRequest) -> tuple[ValidatedReport, ...]:
        root = Path(request.root)
        self._events.consume(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            package_selection=request.package,
        )
        self._events.consume(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            self._events.consume(StatusEvent(message="searching cells"))
            tasks = tuple(
                VerificationTask(
                    cell=cell,
                    execute=self._cell_task(package, cell, snapshot),
                    journal_entries=self._journal_entries_for_result,
                    runtime_associations=self._runtime_associations_for_result,
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
                _cell_matrix_event(
                    project.packages,
                    tuple(task.cell for task in tasks),
                )
            )
            results = self._verification.run(
                VerificationRun(
                    command="search",
                    packages=project.packages,
                    snapshot=snapshot,
                    tasks=tasks,
                    jobs=request.jobs,
                    max_duration_seconds=request.max_duration_seconds,
                )
            )
            self._assert_source_snapshot_current(root=root, expected=snapshot)
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
                update = self._reports.update_path(report_path, report)
                report = update.report
                if self._associations is not None:
                    runtime_processes = {
                        item.failure_id: item.process_observation
                        for result in package_results
                        for item in failure_runtime_runs_for_result(result)
                    }
                    current_failures = tuple(
                        (
                            failure.failure_id,
                            runtime_processes.get(
                                failure.failure_id,
                                failure.process,
                            ),
                        )
                        for result in package_results
                        for failure in failure_records_for_result(result)
                    )
                    self._associations.replace_associations(
                        report.report_generation_id,
                        current_failures,
                        replace_generation=update.replace_generation,
                        remove_failure_ids=update.removed_failure_ids,
                    )
                reports.append(report)
            return tuple(reports)
        finally:
            snapshot.close()

    def _assert_source_snapshot_current(
        self,
        *,
        root: Path,
        expected: SourceSnapshot,
    ) -> None:
        current = self._snapshots.build(
            root,
            owned_pyproject_paths=tuple(
                identity.path
                for identity in expected.identity.pyproject_identities
            ),
        )
        try:
            if current.identity != expected.identity:
                raise ConfigurationError(
                    "project source snapshot drifted during search"
                )
        finally:
            current.close()

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
    def _journal_entries_for_result(
        cls,
        result: CellResult,
    ) -> tuple[VerificationJournalEntry, ...]:
        return cls._journal_entries((result,))

    @staticmethod
    def _runtime_associations_for_result(
        result: CellResult,
    ) -> tuple[tuple[str, ProcessObservation], ...]:
        return tuple(
            (item.failure_id, process)
            for item in failure_runtime_runs_for_result(result)
            if (process := item.process_observation) is not None
        )

    @classmethod
    def _journal_entries(
        cls,
        results: tuple[CellResult, ...],
    ) -> tuple[VerificationJournalEntry, ...]:
        entries: list[VerificationJournalEntry] = []
        for result in results:
            for failure in failure_records_for_result(result):
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


class ExplainCommandWorkflow:
    """Locate and read reports without owning any evaluation capability."""

    def __init__(self, *, discovery: ProjectDiscovery, reports: ReportStore) -> None:
        self._discovery = discovery
        self._reports = reports

    def run(self, request: ReportRequest) -> tuple[ValidatedReport, ...]:
        root = Path(request.root)
        locations = self._discovery.discover(
            root=root,
            package_selection=request.package,
        )
        reports = []
        for location in locations:
            report = self._reports.read(location.report_path)
            relative_pyproject = (
                location.pyproject_path.relative_to(root.resolve()).as_posix()
            )
            if (
                report.package.name != location.name
                or report.package.pyproject_path != relative_pyproject
            ):
                raise ConfigurationError("report package identity mismatch")
            reports.append(report)
        return tuple(reports)


class DiagnosisLogLocator(Protocol):
    def lookup(
        self,
        report_generation_id: str,
        failure_id: str,
    ) -> Path | None: ...

    def lookup_run(self, run_id: str, failure_id: str) -> Path | None: ...

    def read_latest_journal(
        self, package: str
    ) -> VerificationJournalRecord | None: ...

    def read_tail(self, path: Path) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class FailureDiagnosis:
    report_generation_id: str
    package: str
    failure: FailureRecord
    proposal_id: str | None
    boundary_role: Literal["predecessor"] | None
    log_path: Path | None
    output_tail: tuple[str, ...] = ()
    source: Literal["package-floor.json", "journal"] = "package-floor.json"
    verification_role: VerificationRole | None = None
    command: Literal["smoke", "check", "search"] | None = None


class DiagnoseCommandWorkflow:
    """Resolve portable report failures and optional local logs without execution."""

    def __init__(
        self,
        *,
        discovery: ProjectDiscovery,
        reports: ReportStore,
        logs: DiagnosisLogLocator,
    ) -> None:
        self._discovery = discovery
        self._reports = reports
        self._logs = logs

    def run(self, request: DiagnoseRequest) -> tuple[FailureDiagnosis, ...]:
        root = Path(request.root)
        locations = self._discovery.discover(
            root=root,
            package_selection=request.package,
        )
        entries: list[FailureDiagnosis] = []
        for location in locations:
            report_path = location.report_path
            seen: set[str] = set()
            if report_path.is_file():
                report = self._reports.read(report_path)
                if report.package.name != location.name:
                    raise ConfigurationError("report package identity mismatch")
                for failure in report.failure_records:
                    if (
                        request.failure_id is not None
                        and failure.failure_id != request.failure_id
                    ):
                        continue
                    context = report.failure_context(failure.failure_id)
                    if context is None:
                        raise ConfigurationError(
                            f"report failure has no resolved context: {failure.failure_id}"
                        )
                    seen.add(failure.failure_id)
                    log_path = self._logs.lookup(
                        report.report_generation_id,
                        failure.failure_id,
                    )
                    entries.append(
                        FailureDiagnosis(
                            report_generation_id=report.report_generation_id,
                            package=report.package.name,
                            failure=failure,
                            proposal_id=context.proposal_id,
                            boundary_role=context.boundary_role,
                            log_path=log_path,
                            output_tail=(
                                self._logs.read_tail(log_path)
                                if log_path is not None
                                else ()
                            ),
                            source="package-floor.json",
                        )
                    )
            journal = self._logs.read_latest_journal(location.name)
            if journal is None:
                continue
            for item in journal.entries:
                if item.package != location.name:
                    raise ConfigurationError("journal package identity mismatch")
                if item.failure.failure_id in seen:
                    continue
                if (
                    request.failure_id is not None
                    and item.failure.failure_id != request.failure_id
                ):
                    continue
                seen.add(item.failure.failure_id)
                log_path = self._logs.lookup_run(
                    journal.run_id,
                    item.failure.failure_id,
                )
                entries.append(
                    FailureDiagnosis(
                        report_generation_id=journal.run_id,
                        package=item.package,
                        failure=item.failure,
                        proposal_id=None,
                        boundary_role=None,
                        log_path=log_path,
                        output_tail=(
                            self._logs.read_tail(log_path)
                            if log_path is not None
                            else ()
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

    def run(self, request: MergeRequest) -> ValidatedReport:
        merged = self._reports.merge(
            tuple(self._reports.read(Path(path)) for path in request.reports)
        )
        self._reports.write(Path(request.output), merged)
        return merged


class ApplyAuthorizationOperations(Protocol):
    def authorize(
        self,
        *,
        reports: tuple[ValidatedReport, ...],
        project: ProjectPlan,
        current_snapshot: SourceSnapshot,
        force: bool,
    ) -> AuthorizedWorkspaceApply: ...


class ProjectEditOperations(Protocol):
    def apply_many(
        self,
        *,
        authorization: AuthorizedWorkspaceApply,
        root: Path,
    ) -> tuple[ProjectEditResult, ...]: ...


class ApplyCommandWorkflow:
    """Transform complete reports into metadata edits without evaluation."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        reports: ReportStore,
        authorizer: ApplyAuthorizationOperations,
        editor: ProjectEditOperations,
        events: ActivityConsumer | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._reports = reports
        self._authorizer = authorizer
        self._editor = editor
        self._events = events

    def run(self, request: ApplyRequest) -> ApplyCommandResult:
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
        reports = tuple(
            self._reports.read(
                root / Path(package.pyproject_path).parent / "package-floor.json"
            )
            for package in project.packages
        )
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            authorization = self._authorizer.authorize(
                reports=reports,
                project=project,
                current_snapshot=snapshot,
                force=request.force,
            )
            edits = self._editor.apply_many(
                authorization=authorization,
                root=root,
            )
            return ApplyCommandResult(
                edits=edits,
                presentation_facts=authorization.presentation_facts,
            )
        finally:
            snapshot.close()

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pf.environment import PreparedEnvironment
from pf.errors import ConfigurationError
from pf.evaluation import require_full_evaluation_contract
from pf.policy import evaluation_policy_identity
from pf.schemas.evaluation import (
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CellFailureScope,
    CellMatrixEvent,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    CheckResult,
    Evaluation,
    FailureRecord,
    HighestVersionOutcome,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    PrepareFailure,
    ProcessResult,
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


class CheckEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
    ) -> PreparedEnvironment | PrepareFailure | ToolFailure: ...


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
    ) -> Evaluation | ToolFailure: ...


class CompatibilityChecker:
    """Validate current declarations for one cell without searching."""

    def __init__(
        self,
        *,
        environments: CheckEnvironmentOperations,
        static: CheckStaticOperations,
        full: CheckFullOperations,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full

    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> Evaluation | ToolFailure:
        require_full_evaluation_contract(package, "check")
        highest = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="highest",
        )
        if not isinstance(highest, PreparedEnvironment):
            return highest.failure if isinstance(highest, PrepareFailure) else highest
        try:
            capture = self._static.capture(highest, package=package)
        finally:
            highest.close()
        if isinstance(capture, IndeterminateEvaluation):
            return capture
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="lowest-direct",
        )
        if not isinstance(prepared, PreparedEnvironment):
            return (
                prepared.failure if isinstance(prepared, PrepareFailure) else prepared
            )
        try:
            return self._full.evaluate(
                prepared,
                package=package,
                baseline=capture.baseline,
            )
        finally:
            prepared.close()


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
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._checker = checker
        self._scheduler = scheduler
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
                events=self._events,
            )
            return self._aggregate(outcomes)
        finally:
            snapshot.close()

    def _cell_task(
        self,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> Callable[[], Evaluation | ToolFailure]:
        def run() -> Evaluation | ToolFailure:
            return self._checker.check(
                package=package,
                cell=cell,
                snapshot=snapshot,
            )

        return run

    @staticmethod
    def _aggregate(
        outcomes: tuple[Evaluation | ToolFailure, ...],
    ) -> CheckResult:
        evaluations: list[Evaluation] = []
        infra: list[ToolFailure] = []
        for outcome in outcomes:
            if isinstance(outcome, ToolFailure):
                infra.append(outcome)
                continue
            evaluations.append(outcome)
            if isinstance(outcome, IndeterminateEvaluation):
                infra.append(outcome.failure)
        if any(
            isinstance(item, (StaticFailEvaluation, TestFailEvaluation))
            for item in evaluations
        ):
            return CheckCompatibilityFailure(evaluations=tuple(evaluations))
        if infra:
            return CheckIndeterminate(
                evaluations=tuple(
                    item for item in evaluations if isinstance(item, PassEvaluation)
                ),
                failure=infra[0],
            )
        return CheckPass(
            evaluations=tuple(
                item for item in evaluations if isinstance(item, PassEvaluation)
            )
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
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._verifier = verifier
        self._scheduler = scheduler
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
                events=self._events,
            )
            return self._aggregate(outcomes)
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
        logs: FailureLogAssociations | None = None,
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
            results = self._scheduler.run(
                tasks,
                jobs=request.jobs,
                max_duration_seconds=request.max_duration_seconds,
                events=self._events,
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


@dataclass(frozen=True)
class FailureDiagnosis:
    report_generation_id: str
    package: str
    failure: FailureRecord
    proposal_id: str | None
    boundary_role: Literal["predecessor"] | None
    log_path: Path | None


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
            report = self._reports.read(
                root / Path(package.pyproject_path).parent / "package-floor.json"
            )
            for result in report.cell_results:
                for failure in self._failure_records(result):
                    if (
                        request.failure_id is not None
                        and failure.failure_id != request.failure_id
                    ):
                        continue
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

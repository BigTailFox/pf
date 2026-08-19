from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pf.environment import PreparedEnvironment
from pf.errors import ConfigurationError
from pf.schemas.evaluation import (
    CellMatrixEvent,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    CheckResult,
    Evaluation,
    IndeterminateEvaluation,
    PassEvaluation,
    ProgressEvent,
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
    MergeRequest,
    ReportRequest,
    SearchRequest,
)
from pf.schemas.report import CellResult, PackageFloorReportV1, ProjectEditResult
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
    ) -> PreparedEnvironment | ToolFailure: ...


class CheckEvaluationOperations(Protocol):
    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> Evaluation: ...


class CompatibilityChecker:
    """Validate current declarations for every configured cell without searching."""

    def __init__(
        self,
        *,
        environments: CheckEnvironmentOperations,
        evaluator: CheckEvaluationOperations,
        events: ProgressConsumer | None = None,
        host_target: str | None = None,
    ) -> None:
        self._environments = environments
        self._evaluator = evaluator
        self._events = events
        self._host_target = host_target or current_host_target()

    def check(
        self,
        *,
        package: PackagePlan,
        snapshot: SourceSnapshot,
    ) -> CheckResult:
        if not package.config.test_command:
            raise ConfigurationError("test-command is required for check")
        if not package.test_group_present:
            raise ConfigurationError(
                f"test dependency group is required: {package.config.test_group}"
            )
        local_cells = tuple(
            cell for cell in package.cells if cell.target == self._host_target
        )
        if not local_cells:
            raise ConfigurationError(
                f"no configured cell matches host target: {self._host_target}"
            )
        evaluations: list[PassEvaluation] = []
        total = len(local_cells)
        for index, cell in enumerate(local_cells, start=1):
            self._emit(
                ProgressEvent(
                    package=package.name,
                    cell=cell,
                    phase="check",
                    completed=index - 1,
                    total=total,
                    message="running",
                )
            )
            prepared = self._environments.prepare(
                package=package,
                cell=cell,
                snapshot=snapshot,
                resolution="lowest-direct",
            )
            if not isinstance(prepared, PreparedEnvironment):
                self._emit(
                    ProgressEvent(
                        package=package.name,
                        cell=cell,
                        phase="check",
                        completed=index,
                        total=total,
                        message=prepared.status,
                    )
                )
                return CheckIndeterminate(
                    evaluations=tuple(evaluations),
                    failure=prepared,
                )
            try:
                evaluation = self._evaluator.evaluate(prepared, package=package)
            finally:
                prepared.close()
            self._emit(
                ProgressEvent(
                    package=package.name,
                    cell=cell,
                    phase="check",
                    completed=index,
                    total=total,
                    message=evaluation.status,
                )
            )
            if isinstance(evaluation, PassEvaluation):
                evaluations.append(evaluation)
                continue
            if isinstance(evaluation, (StaticFailEvaluation, TestFailEvaluation)):
                return CheckCompatibilityFailure(
                    evaluations=(*evaluations, evaluation)
                )
            if isinstance(evaluation, IndeterminateEvaluation):
                return CheckIndeterminate(
                    evaluations=tuple(evaluations),
                    failure=evaluation.failure,
                )
        return CheckPass(evaluations=tuple(evaluations))

    def _emit(self, event: ProgressEvent) -> None:
        if self._events is not None:
            self._events.consume(event)


class CheckCommandWorkflow:
    """Load, snapshot, and check every package selected by one CLI request."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        checker: CompatibilityChecker,
        events: ProgressConsumer | None = None,
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._checker = checker
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
        evaluations: list[PassEvaluation] = []
        try:
            self._emit(StatusEvent(message="checking declarations"))
            self._emit(
                CellMatrixEvent(
                    cells=selected_host_cells(project.packages, self._host_target)
                )
            )
            for package in project.packages:
                result = self._checker.check(package=package, snapshot=snapshot)
                if result.status != "PASS":
                    return result
                evaluations.extend(result.evaluations)
            return CheckPass(evaluations=tuple(evaluations))
        finally:
            snapshot.close()

    def _emit(self, event: StatusEvent | CellMatrixEvent) -> None:
        if self._events is not None:
            self._events.consume(event)


class CellSearchOperations(Protocol):
    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CellResult: ...


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
        host_target: str | None = None,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._coordinator = coordinator
        self._scheduler = scheduler
        self._reports = reports
        self._report_builder = report_builder
        self._events = events
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
                    root
                    / Path(package.pyproject_path).parent
                    / "package-floor.json"
                )
                if report_path.is_file():
                    existing = self._reports.read(report_path)
                    if self._same_report_generation(existing, report):
                        report = self._reports.update(existing, report)
                self._reports.write(report_path, report)
                reports.append(report)
            return tuple(reports)
        finally:
            snapshot.close()

    @staticmethod
    def _same_report_generation(
        left: PackageFloorReportV1,
        right: PackageFloorReportV1,
    ) -> bool:
        return (
            left.generator == right.generator
            and left.package == right.package
            and left.source_snapshot == right.source_snapshot
            and left.policy_identity == right.policy_identity
            and left.requirement_declarations == right.requirement_declarations
            and left.target_cells == right.target_cells
        )

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
                root
                / Path(package.pyproject_path).parent
                / "package-floor.json"
            )
            for package in project.packages
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
            reports.append(report)
        return self._editor.apply_many(reports=tuple(reports), root=root)

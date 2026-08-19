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
        evaluator: CheckEvaluationOperations,
    ) -> None:
        self._environments = environments
        self._evaluator = evaluator

    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> Evaluation | ToolFailure:
        require_check_contract(package)
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="lowest-direct",
        )
        if not isinstance(prepared, PreparedEnvironment):
            return prepared
        try:
            return self._evaluator.evaluate(prepared, package=package)
        finally:
            prepared.close()


def require_check_contract(package: PackagePlan) -> None:
    if not package.config.test_command:
        raise ConfigurationError("test-command is required for check")
    if not package.test_group_present:
        raise ConfigurationError(
            f"test dependency group is required: {package.config.test_group}"
        )


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
                require_check_contract(package)
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

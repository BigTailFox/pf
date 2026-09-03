from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pf.errors import (
    ConfigurationError,
    DiagnoseNotFoundError,
    ExplainReportError,
    MergeCompatibilityError,
    MergeInputError,
    MergeOutputError,
)
from pf.schemas.evaluation import (
    BaselineIndeterminate,
    BaselineRejection,
    CheckCellOutcome,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    CheckResult,
    FailureRecord,
    HighestVersionOutcome,
    HighestVersionPass,
    PassEvaluation,
    ProcessObservation,
    SmokeIndeterminate,
    SmokeBaselineRejection,
    SmokePass,
    SmokeResult,
    StatusEvent,
    VerificationJournalRecord,
    VerificationRole,
)
from pf.schemas.apply import ApplyCommandResult, AuthorizedWorkspaceApply
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.schemas.project import (
    ProjectPlan,
    SourcePlan,
)
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    DiagnoseRequest,
    MergeRequest,
    ReportRequest,
    SearchRequest,
    SmokeRequest,
    WorkspacePackage,
)
from pf.schemas.report import (
    ProjectEditResult,
    failure_records_for_result,
    failure_runtime_runs_for_result,
)
from pf.project import ProjectLoader
from pf.project_discovery import ProjectDiscovery
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot
from pf.verification import (
    ActivityConsumer,
    CellSearchOperations,
    CheckCellOperations,
    CheckVerificationRun,
    SearchVerificationRun,
    SmokeCellOperations,
    SmokeVerificationRun,
    VerificationRunner,
)


class CheckCommandWorkflow:
    """Load, snapshot, and check the target selected by one CLI request."""

    def __init__(
        self,
        *,
        projects: ProjectLoader,
        snapshots: SnapshotBuilder,
        checker: CheckCellOperations,
        verification: VerificationRunner,
        events: ActivityConsumer,
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._checker = checker
        self._verification = verification
        self._events = events

    def run(self, request: CheckRequest) -> CheckResult:
        root = Path(request.root)
        self._emit(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            selector=request.selector,
        )
        self._emit(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            self._emit(StatusEvent(message="checking declarations"))
            package = project.target
            source_plan = SourcePlan.for_package(package, "SEARCH")
            outcomes = self._verification.run(
                CheckVerificationRun(
                    package=package,
                    source_plan=source_plan,
                    snapshot=snapshot,
                    operation=self._checker,
                    jobs=request.jobs,
                )
            )
            result = self._aggregate(outcomes)
            return result
        finally:
            snapshot.close()

    @staticmethod
    def _aggregate(
        outcomes: tuple[CheckCellOutcome, ...],
    ) -> CheckResult:
        evaluations = tuple(
            outcome.evaluation for outcome in outcomes if outcome.evaluation is not None
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

    def _emit(self, event: StatusEvent) -> None:
        self._events.consume(event)


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
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._verifier = verifier
        self._verification = verification
        self._events = events

    def run(self, request: SmokeRequest) -> SmokeResult:
        root = Path(request.root)
        self._emit(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            selector=request.selector,
        )
        self._emit(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            self._emit(StatusEvent(message="smoke testing"))
            package = project.target
            source_plan = SourcePlan.for_package(package, "DEVELOPMENT")
            outcomes = self._verification.run(
                SmokeVerificationRun(
                    package=package,
                    source_plan=source_plan,
                    snapshot=snapshot,
                    operation=self._verifier,
                    jobs=request.jobs,
                ),
            )
            result = self._aggregate(outcomes)
            return result
        finally:
            snapshot.close()

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

    def _emit(self, event: StatusEvent) -> None:
        self._events.consume(event)


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
    ) -> None:
        self._projects = projects
        self._snapshots = snapshots
        self._coordinator = coordinator
        self._verification = verification
        self._reports = reports
        self._report_builder = report_builder
        self._events = events
        self._associations = associations

    def run(self, request: SearchRequest) -> ValidatedReport:
        root = Path(request.root)
        self._events.consume(StatusEvent(message="loading project"))
        project = self._projects.load(
            root=root,
            selector=request.selector,
        )
        self._events.consume(StatusEvent(message="building snapshot"))
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            self._events.consume(StatusEvent(message="searching cells"))
            package = project.target
            source_plan = SourcePlan.for_package(package, "SEARCH")
            results = self._verification.run(
                SearchVerificationRun(
                    package=package,
                    source_plan=source_plan,
                    snapshot=snapshot,
                    operation=self._coordinator,
                    jobs=request.jobs,
                    max_duration_seconds=request.max_duration_seconds,
                )
            )
            self._assert_source_snapshot_current(root=root, expected=snapshot)
            report = self._report_builder.build(
                package=package,
                source_plan=source_plan,
                source_snapshot=snapshot.identity,
                cell_results=results,
            )
            report_path = (
                root / Path(package.pyproject_path).parent / "package-floor.json"
            )
            update = self._reports.update_path(report_path, report)
            report = update.report
            if self._associations is not None:
                runtime_processes = {
                    item.failure_id: item.process_observation
                    for result in results
                    for item in failure_runtime_runs_for_result(result)
                }
                current_failures = tuple(
                    (
                        failure.failure_id,
                        runtime_processes.get(failure.failure_id, failure.process),
                    )
                    for result in results
                    for failure in failure_records_for_result(result)
                )
                self._associations.replace_associations(
                    report.report_generation_id,
                    current_failures,
                    replace_generation=update.replace_generation,
                    remove_failure_ids=update.removed_failure_ids,
                )
            return report
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
                identity.path for identity in expected.identity.pyproject_identities
            ),
        )
        try:
            if current.identity != expected.identity:
                raise ConfigurationError(
                    "project source snapshot drifted during search"
                )
        finally:
            current.close()


class ExplainCommandWorkflow:
    """Locate and read reports without owning any evaluation capability."""

    def __init__(self, *, discovery: ProjectDiscovery, reports: ReportStore) -> None:
        self._discovery = discovery
        self._reports = reports

    def run(self, request: ReportRequest) -> ValidatedReport:
        root = Path(request.root)
        location = self._discovery.select(
            root=root,
            selector=request.selector,
        )
        report_path = location.report_path
        display_path = report_path.relative_to(root.resolve()).as_posix()
        recovery_command = (
            f"pf search --package {request.selector.canonical_name}"
            if isinstance(request.selector, WorkspacePackage)
            else "pf search"
        )
        if not report_path.is_file():
            raise ExplainReportError(
                report_path=display_path,
                reason="report is unavailable",
                recovery_command=recovery_command,
            )
        try:
            report = self._reports.read(report_path)
        except ConfigurationError as error:
            raise ExplainReportError(
                report_path=display_path,
                reason="report is unreadable or invalid",
            ) from error
        relative_pyproject = location.pyproject_path.relative_to(
            root.resolve()
        ).as_posix()
        if (
            report.package.name != location.name
            or report.package.pyproject_path != relative_pyproject
        ):
            raise ExplainReportError(
                report_path=display_path,
                reason="report package identity does not match the selected package",
            )
        return report


class DiagnosisLogLocator(Protocol):
    def lookup(
        self,
        report_generation_id: str,
        failure_id: str,
    ) -> Path | None: ...

    def lookup_run(self, run_id: str, failure_id: str) -> Path | None: ...

    def read_latest_journal(self, package: str) -> VerificationJournalRecord | None: ...

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
    source: Literal["report", "journal"] = "report"
    source_path: str | None = None
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

    def run(self, request: DiagnoseRequest) -> FailureDiagnosis:
        root = Path(request.root)
        location = self._discovery.select(
            root=root,
            selector=request.selector,
        )
        report_path = location.report_path
        if report_path.is_file():
            report = self._reports.read(report_path)
            relative_pyproject = location.pyproject_path.relative_to(
                root.resolve()
            ).as_posix()
            if (
                report.package.name != location.name
                or report.package.pyproject_path != relative_pyproject
            ):
                raise ConfigurationError("report package identity mismatch")
            failure = report.failure(request.failure_id)
            if failure is not None:
                context = report.failure_context(request.failure_id)
                if context is None:
                    raise ConfigurationError(
                        f"report failure has no resolved context: {failure.failure_id}"
                    )
                log_path = self._logs.lookup(
                    report.report_generation_id,
                    failure.failure_id,
                )
                return FailureDiagnosis(
                    report_generation_id=report.report_generation_id,
                    package=report.package.name,
                    failure=failure,
                    proposal_id=context.proposal_id,
                    boundary_role=context.boundary_role,
                    log_path=log_path,
                    output_tail=(
                        self._logs.read_tail(log_path) if log_path is not None else ()
                    ),
                    source="report",
                    source_path=report_path.relative_to(root.resolve()).as_posix(),
                )
        journal = self._logs.read_latest_journal(location.name)
        if journal is not None:
            for item in journal.entries:
                if item.package != location.name:
                    raise ConfigurationError("journal package identity mismatch")
                if item.failure.failure_id != request.failure_id:
                    continue
                log_path = self._logs.lookup_run(
                    journal.run_id,
                    item.failure.failure_id,
                )
                return FailureDiagnosis(
                    report_generation_id=journal.run_id,
                    package=item.package,
                    failure=item.failure,
                    proposal_id=None,
                    boundary_role=None,
                    log_path=log_path,
                    output_tail=(
                        self._logs.read_tail(log_path) if log_path is not None else ()
                    ),
                    source="journal",
                    verification_role=item.role,
                    command=journal.command,
                )
        raise DiagnoseNotFoundError(
            failure_id=request.failure_id,
            package=location.name,
        )


@dataclass(frozen=True)
class MergeCommandResult:
    report: ValidatedReport
    input_paths: tuple[str, ...]
    output_path: str


class MergeCommandWorkflow:
    def __init__(self, *, reports: ReportStore) -> None:
        self._reports = reports

    def run(self, request: MergeRequest) -> MergeCommandResult:
        input_paths = tuple(Path(path).as_posix() for path in request.reports)
        output_path = Path(request.output).as_posix()
        reports: list[ValidatedReport] = []
        for path in input_paths:
            try:
                reports.append(self._reports.read(Path(path)))
            except ConfigurationError as error:
                raise MergeInputError(
                    input_paths=input_paths,
                    output_path=output_path,
                    failed_input_path=path,
                ) from error
        try:
            merged = self._reports.merge(tuple(reports))
        except ConfigurationError as error:
            raise MergeCompatibilityError(
                input_paths=input_paths,
                output_path=output_path,
                detail=str(error),
            ) from error
        try:
            self._reports.write(Path(output_path), merged)
        except OSError as error:
            raise MergeOutputError(
                input_paths=input_paths,
                output_path=output_path,
            ) from error
        return MergeCommandResult(
            report=merged,
            input_paths=input_paths,
            output_path=output_path,
        )


class ApplyAuthorizationOperations(Protocol):
    def authorize(
        self,
        *,
        report: ValidatedReport,
        project: ProjectPlan,
        current_snapshot: SourceSnapshot,
        force: bool,
    ) -> AuthorizedWorkspaceApply: ...


class ProjectEditOperations(Protocol):
    def apply(
        self,
        *,
        authorization: AuthorizedWorkspaceApply,
        root: Path,
    ) -> ProjectEditResult: ...


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
            selector=request.selector,
        )
        if self._events is not None:
            self._events.consume(
                StatusEvent(
                    message="applying floors",
                    total=1,
                )
            )
        report = self._reports.read(
            root / Path(project.target.pyproject_path).parent / "package-floor.json"
        )
        snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=project.owned_pyproject_paths,
        )
        try:
            authorization = self._authorizer.authorize(
                report=report,
                project=project,
                current_snapshot=snapshot,
                force=request.force,
            )
            edit = self._editor.apply(
                authorization=authorization,
                root=root,
            )
            return ApplyCommandResult(
                package=authorization.package_apply.package.name,
                edit=edit,
                presentation_facts=authorization.presentation_facts,
            )
        finally:
            snapshot.close()

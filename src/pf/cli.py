from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Annotated, Literal, Protocol, TypeVar, cast

from cyclopts import App, Group, Parameter
from cyclopts.exceptions import CycloptsError, MissingArgumentError
from packaging.utils import InvalidName, canonicalize_name

from pf.adapters.process import SecretRedactor, SubprocessRunner
from pf.adapters.runtime_witness import RuntimeWitnessAdapter
from pf.adapters.test_command import ConfiguredVerifier
from pf.adapters.ty import TyAdapter
from pf.adapters.uv import RegistryAccess, UvAdapter
from pf.baseline import HighestVersionVerifier
from pf.authorization import ApplyAuthorizer
from pf.candidates import CandidateBuilder
from pf.check import CompatibilityChecker
from pf.config import parse_max_duration, parse_scheduling_limit
from pf.coordinate_search import CoordinateSearch
from pf.environment import EnvironmentFactory
from pf.editor import ProjectEditor
from pf.errors import ConfigurationError, ExitCode, InvocationError, PfError
from pf.evaluation import RuntimeEvaluator, StagePermitPools, StaticEvaluator
from pf.project import ProjectLoader, host_target
from pf.project_discovery import ProjectDiscovery
from pf.report import PackageReportBuilder, ReportStore
from pf.runlog import RunLogStore
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    DiagnoseRequest,
    MergeRequest,
    ReportRequest,
    SearchRequest,
    SmokeRequest,
    RootPackage,
    TargetSelector,
    WorkspacePackage,
)
from pf.schemas.evaluation import CheckResult, SmokeResult
from pf.schemas.apply import ApplyCommandResult
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder
from pf.terminal import TerminalPresenter, command_usage, command_usage_line
from pf.verification import VerificationRunner
from pf.workflow import (
    CheckCommandWorkflow,
    DiagnoseCommandWorkflow,
    FailureDiagnosis,
    ApplyCommandWorkflow,
    ExplainCommandResult,
    ExplainCommandWorkflow,
    MergeCommandResult,
    MergeCommandWorkflow,
    SearchCommandResult,
    SearchCommandWorkflow,
    SmokeCommandWorkflow,
)


class CheckWorkflow(Protocol):
    def run(self, request: CheckRequest) -> CheckResult: ...


class SearchWorkflow(Protocol):
    def run(self, request: SearchRequest) -> SearchCommandResult: ...


class SmokeWorkflow(Protocol):
    def run(self, request: SmokeRequest) -> SmokeResult: ...


class ExplainWorkflow(Protocol):
    def run(self, request: ReportRequest) -> ExplainCommandResult: ...


class DiagnoseWorkflow(Protocol):
    def run(self, request: DiagnoseRequest) -> FailureDiagnosis: ...


class MergeWorkflow(Protocol):
    def run(self, request: MergeRequest) -> MergeCommandResult: ...


class ApplyWorkflow(Protocol):
    def run(self, request: ApplyRequest) -> ApplyCommandResult: ...


T = TypeVar("T")


def _assembled(value: T | None) -> T:
    return cast(T, value)


@dataclass
class CliContext:
    presenter: TerminalPresenter
    run_logs: RunLogStore
    root: Path = field(default_factory=Path.cwd)
    _closed: bool = field(default=False, init=False, repr=False)
    _discovery: ProjectDiscovery | None = field(default=None, init=False, repr=False)
    _reports: ReportStore | None = field(default=None, init=False, repr=False)
    _runner: SubprocessRunner | None = field(default=None, init=False, repr=False)
    _uv: UvAdapter | None = field(default=None, init=False, repr=False)
    _projects: ProjectLoader | None = field(default=None, init=False, repr=False)
    _snapshots: SnapshotBuilder | None = field(default=None, init=False, repr=False)
    _environments: EnvironmentFactory | None = field(default=None, init=False, repr=False)
    _static: StaticEvaluator | None = field(default=None, init=False, repr=False)
    _full: RuntimeEvaluator | None = field(default=None, init=False, repr=False)
    _checker: CompatibilityChecker | None = field(default=None, init=False, repr=False)
    _highest: HighestVersionVerifier | None = field(default=None, init=False, repr=False)
    _verification: VerificationRunner | None = field(default=None, init=False, repr=False)
    _coordinator: SearchCoordinator | None = field(default=None, init=False, repr=False)
    _report_builder: PackageReportBuilder | None = field(default=None, init=False, repr=False)
    _check_workflow: CheckWorkflow | None = field(default=None, init=False, repr=False)
    _smoke_workflow: SmokeWorkflow | None = field(default=None, init=False, repr=False)
    _search_workflow: SearchWorkflow | None = field(default=None, init=False, repr=False)
    _explain_workflow: ExplainWorkflow | None = field(default=None, init=False, repr=False)
    _diagnose_workflow: DiagnoseWorkflow | None = field(default=None, init=False, repr=False)
    _merge_workflow: MergeWorkflow | None = field(default=None, init=False, repr=False)
    _apply_workflow: ApplyWorkflow | None = field(default=None, init=False, repr=False)

    def __enter__(self) -> "CliContext":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.presenter.close()
        except KeyboardInterrupt:
            try:
                self.presenter.close()
            except KeyboardInterrupt:
                pass
        try:
            self.run_logs.close()
        except KeyboardInterrupt:
            try:
                self.run_logs.close()
            except KeyboardInterrupt:
                pass

    def interrupt_processes(self) -> None:
        runner = self._runner
        if runner is not None:
            runner.interrupt()

    @property
    def check_workflow(self) -> CheckWorkflow:
        if self._check_workflow is None:
            self._assemble_check()
        return _assembled(self._check_workflow)

    @property
    def smoke_workflow(self) -> SmokeWorkflow:
        if self._smoke_workflow is None:
            self._assemble_smoke()
        return _assembled(self._smoke_workflow)

    @property
    def search_workflow(self) -> SearchWorkflow:
        if self._search_workflow is None:
            self._assemble_search()
        return _assembled(self._search_workflow)

    @property
    def explain_workflow(self) -> ExplainWorkflow:
        if self._explain_workflow is None:
            self._assemble_explain()
        return _assembled(self._explain_workflow)

    @property
    def diagnose_workflow(self) -> DiagnoseWorkflow:
        if self._diagnose_workflow is None:
            self._assemble_diagnose()
        return _assembled(self._diagnose_workflow)

    @property
    def merge_workflow(self) -> MergeWorkflow:
        if self._merge_workflow is None:
            self._assemble_merge()
        return _assembled(self._merge_workflow)

    @property
    def apply_workflow(self) -> ApplyWorkflow:
        if self._apply_workflow is None:
            self._assemble_apply()
        return _assembled(self._apply_workflow)

    def _ensure_discovery(self) -> ProjectDiscovery:
        if self._discovery is None:
            self._discovery = ProjectDiscovery()
        return self._discovery

    def _ensure_reports(self) -> ReportStore:
        if self._reports is None:
            self._reports = ReportStore()
        return self._reports

    def _ensure_process_runtime(self) -> tuple[UvAdapter, SubprocessRunner]:
        if self._uv is None or self._runner is None:
            registry_access = RegistryAccess.from_environment(os.environ)
            redactor = SecretRedactor(registry_access.secret_literals)
            runner = SubprocessRunner(
                redactor=redactor,
                listener=self.presenter,
                logs=self.run_logs,
            )
            self._runner = runner
            self._uv = UvAdapter(
                runner,
                registry_access=registry_access,
                redactor=redactor,
            )
        return self._uv, self._runner

    def _ensure_planning(self) -> tuple[ProjectLoader, SnapshotBuilder]:
        if self._projects is None or self._snapshots is None:
            uv, runner = self._ensure_process_runtime()
            self._projects = ProjectLoader(
                pythons=uv,
                discovery=self._ensure_discovery(),
            )
            self._snapshots = SnapshotBuilder(runner)
        return self._projects, self._snapshots

    def _ensure_evaluation(self) -> None:
        if self._verification is not None:
            return
        uv, runner = self._ensure_process_runtime()
        environments = EnvironmentFactory(uv, events=self.presenter)
        permits = StagePermitPools()
        static = StaticEvaluator(
            TyAdapter(runner),
            events=self.presenter,
            permits=permits,
        )
        full = RuntimeEvaluator(
            static=static,
            verifier=ConfiguredVerifier(runner),
            witnesses=RuntimeWitnessAdapter(runner),
            events=self.presenter,
            permits=permits,
        )
        self._environments = environments
        self._static = static
        self._full = full
        self._checker = CompatibilityChecker(
            environments=environments,
            static=static,
            full=full,
            events=self.presenter,
        )
        self._highest = HighestVersionVerifier(
            environments=environments,
            static=static,
            full=full,
        )
        self._verification = VerificationRunner(
            events=self.presenter,
            logs=self.run_logs,
            host_target=host_target(),
            permits=permits,
        )

    def _assemble_explain(self) -> None:
        self._explain_workflow = ExplainCommandWorkflow(
            discovery=self._ensure_discovery(),
            reports=self._ensure_reports(),
        )

    def _assemble_diagnose(self) -> None:
        self._diagnose_workflow = DiagnoseCommandWorkflow(
            discovery=self._ensure_discovery(),
            reports=self._ensure_reports(),
            logs=self.run_logs,
        )

    def _assemble_merge(self) -> None:
        self._merge_workflow = MergeCommandWorkflow(reports=self._ensure_reports())

    def _assemble_apply(self) -> None:
        projects, snapshots = self._ensure_planning()
        self._apply_workflow = ApplyCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            reports=self._ensure_reports(),
            authorizer=ApplyAuthorizer(),
            editor=ProjectEditor(snapshots=snapshots),
            events=self.presenter,
        )

    def _assemble_check(self) -> None:
        self._ensure_evaluation()
        projects, snapshots = self._ensure_planning()
        self._check_workflow = CheckCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            checker=_assembled(self._checker),
            verification=_assembled(self._verification),
            events=self.presenter,
        )

    def _assemble_smoke(self) -> None:
        self._ensure_evaluation()
        projects, snapshots = self._ensure_planning()
        self._smoke_workflow = SmokeCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            verifier=_assembled(self._highest),
            verification=_assembled(self._verification),
            events=self.presenter,
        )

    def _assemble_search(self) -> None:
        self._ensure_evaluation()
        projects, snapshots = self._ensure_planning()
        uv, _runner = self._ensure_process_runtime()
        if self._coordinator is None:
            self._coordinator = SearchCoordinator(
                environments=_assembled(self._environments),
                candidates=CandidateBuilder(uv),
                static=_assembled(self._static),
                full=_assembled(self._full),
                highest=_assembled(self._highest),
                coordinate_search=CoordinateSearch(),
                diagnostics=self.presenter,
                events=self.presenter,
            )
        if self._report_builder is None:
            self._report_builder = PackageReportBuilder()
        self._search_workflow = SearchCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            coordinator=self._coordinator,
            verification=_assembled(self._verification),
            reports=self._ensure_reports(),
            report_builder=self._report_builder,
            events=self.presenter,
            logs=self.run_logs,
        )


_PACKAGE_HELP = (
    "Canonical distribution name of one installable workspace package. "
    "Omit to select the installable workspace root package."
)
_MAX_CELLS_HELP = (
    "Maximum concurrent cells. Omit to use project configuration; "
    "use auto or a positive integer."
)
_TY_JOBS_HELP = (
    "Maximum concurrent ty checks. Omit to use project configuration; "
    "use auto or a positive integer."
)
_TEST_JOBS_HELP = (
    "Maximum concurrent configured test commands. Omit to use project "
    "configuration; use auto or a positive integer."
)
_DURATION_HELP = (
    "Stop scheduling after DURATION and save an incomplete report. "
    "Accepts a positive integer followed by s, m, or h; use none for no limit."
)
_SEARCH_RESOLUTION_HELP = (
    "Series representative granularity. Omit to use project configuration; "
    "accepts major, minor, or patch."
)
_PACKAGE = Annotated[str | None, Parameter(help=_PACKAGE_HELP)]
_MAX_CELLS = Annotated[str | None, Parameter(help=_MAX_CELLS_HELP)]
_TY_JOBS = Annotated[str | None, Parameter(help=_TY_JOBS_HELP)]
_TEST_JOBS = Annotated[str | None, Parameter(help=_TEST_JOBS_HELP)]
_DURATION = Annotated[str | None, Parameter(help=_DURATION_HELP)]
_SEARCH_RESOLUTION = Annotated[
    Literal["major", "minor", "patch"] | None,
    Parameter(help=_SEARCH_RESOLUTION_HELP),
]
_FAILURE_ID = Annotated[
    str,
    Parameter(
        help=(
            "A failure ID containing 16 lowercase hexadecimal characters, "
            "optionally prefixed with failure-."
        )
    ),
]
_VERIFY = Group("Verify", sort_key=1)
_FIND = Group("Find and apply floors", sort_key=2)
_INSPECT = Group("Inspect and combine reports", sort_key=3)


def _cli_scheduling_limit(
    value: str | None,
    *,
    field: str,
) -> Literal["auto"] | int | None:
    if value is None:
        return None
    try:
        return parse_scheduling_limit(value, field=field)
    except ConfigurationError as error:
        raise InvocationError(str(error)) from error


def _cli_duration(value: str | None) -> int | None:
    try:
        return parse_max_duration(value)
    except ConfigurationError:
        raise InvocationError(
            "invalid duration: use 30s, 10m, 2h, or none"
        ) from None


def _cli_selector(value: str | None) -> TargetSelector:
    if value is None:
        return RootPackage()
    try:
        canonical_name = canonicalize_name(value, validate=True)
    except InvalidName:
        raise InvocationError(
            "invalid package name: use a distribution name such as 'demo-package'"
        ) from None
    return WorkspacePackage(canonical_name=canonical_name)


def _cli_failure_id(value: str) -> str:
    candidate = value if value.startswith("failure-") else f"failure-{value}"
    if re.fullmatch(r"failure-[0-9a-f]{16}", candidate) is None:
        raise InvocationError(
            "invalid failure ID: expected failure-<16 hex> or <16 hex>"
        )
    return candidate


def _invocation_error(error: CycloptsError) -> str:
    message = str(error.msg) if error.msg is not None else str(error)
    if (
        isinstance(error, MissingArgumentError)
        and error.argument is not None
        and error.argument.name == "FAILURE_ID"
    ):
        message = "Missing argument 'FAILURE_ID'."
    chain = tuple(error.command_chain or ())
    command = "pf" + ((" " + " ".join(chain)) if chain else "")
    usage = command_usage(chain[-1] if chain else None)
    hint = f"Try '{command} --help' for more information."
    return f"Error: {message}\n{usage}\n{hint}"


def create_app(context: CliContext) -> App:
    """Create a Cyclopts app; handlers assemble the current command graph."""
    app = App(
        name="pf",
        help="Find verified lower bounds for direct Python dependencies.",
        help_epilogue=(
            "Typical workflow: pf smoke -> pf search -> pf explain -> pf apply\n"
            "Use pf minimize to search and apply in one command."
        ),
        help_on_error=False,
        print_error=True,
        exit_on_error=False,
        error_formatter=_invocation_error,
        console=context.presenter.stdout,
        error_console=context.presenter.stderr,
    )

    @app.command(group=_VERIFY, sort_key=1)
    def smoke(
        *,
        package: _PACKAGE = None,
        max_cells: _MAX_CELLS = None,
        ty_jobs: _TY_JOBS = None,
        test_jobs: _TEST_JOBS = None,
    ) -> int:
        """Verify a fresh install with the newest versions allowed by current declarations."""
        context.presenter.bind_command("smoke")
        request = SmokeRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            max_cells=_cli_scheduling_limit(max_cells, field="max-cells"),
            ty_jobs=_cli_scheduling_limit(ty_jobs, field="ty-jobs"),
            test_jobs=_cli_scheduling_limit(test_jobs, field="test-jobs"),
        )
        return context.presenter.render_smoke(context.smoke_workflow.run(request))

    @app.command(group=_VERIFY, sort_key=2)
    def check(
        *,
        package: _PACKAGE = None,
        max_cells: _MAX_CELLS = None,
        ty_jobs: _TY_JOBS = None,
        test_jobs: _TEST_JOBS = None,
    ) -> int:
        """Verify the lower bounds declared by the project."""
        context.presenter.bind_command("check")
        request = CheckRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            max_cells=_cli_scheduling_limit(max_cells, field="max-cells"),
            ty_jobs=_cli_scheduling_limit(ty_jobs, field="ty-jobs"),
            test_jobs=_cli_scheduling_limit(test_jobs, field="test-jobs"),
        )
        result = context.check_workflow.run(request)
        return context.presenter.render_check(result)

    @app.command(group=_FIND, sort_key=1)
    def search(
        *,
        package: _PACKAGE = None,
        max_cells: _MAX_CELLS = None,
        ty_jobs: _TY_JOBS = None,
        test_jobs: _TEST_JOBS = None,
        max_duration: _DURATION = None,
        search_resolution: _SEARCH_RESOLUTION = None,
    ) -> int:
        """Find verified floors and write package-floor.json.

        Configure search-space and search-space-defaults in tool.pf. Omitted
        space selects majors[declaration-1:] with a lower bound, otherwise
        majors[baseline-2:]. Resolution selects major, minor, or patch series.
        """
        context.presenter.bind_command("search")
        request = SearchRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            max_cells=_cli_scheduling_limit(max_cells, field="max-cells"),
            ty_jobs=_cli_scheduling_limit(ty_jobs, field="ty-jobs"),
            test_jobs=_cli_scheduling_limit(test_jobs, field="test-jobs"),
            max_duration_seconds=_cli_duration(max_duration),
            search_resolution=search_resolution,
        )
        return context.presenter.render_search(context.search_workflow.run(request))

    @app.command(group=_FIND, sort_key=2)
    def explain(*, package: _PACKAGE = None) -> int:
        """Show verified floors, coverage, and apply blockers in an existing report."""
        context.presenter.bind_command("explain")
        request = ReportRequest(
            root=Path.cwd().as_posix(), selector=_cli_selector(package)
        )
        return context.presenter.render_explain(context.explain_workflow.run(request))

    @app.command(group=_FIND, sort_key=3)
    def apply(
        *,
        package: _PACKAGE = None,
        search_resolution: _SEARCH_RESOLUTION = None,
        force: Annotated[
            bool,
            Parameter(
                help="Accept source-layer drift after structural authorization.",
                negative=(),
            ),
        ] = False,
    ) -> int:
        """Update project metadata from authorized final floor evidence."""
        context.presenter.bind_command("apply")
        request = ApplyRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            search_resolution=search_resolution,
            force=force,
        )
        return context.presenter.render_apply(context.apply_workflow.run(request))

    @app.command(group=_FIND, sort_key=4)
    def minimize(
        *,
        package: _PACKAGE = None,
        max_cells: _MAX_CELLS = None,
        ty_jobs: _TY_JOBS = None,
        test_jobs: _TEST_JOBS = None,
        max_duration: _DURATION = None,
        search_resolution: _SEARCH_RESOLUTION = None,
    ) -> int:
        """Search for floors, then apply the authorized result.

        Uses the same tool.pf search-space and conditional defaults as search.
        """
        context.presenter.bind_command("minimize")
        root = Path.cwd().as_posix()
        search = context.search_workflow.run(
            SearchRequest(
                root=root,
                selector=_cli_selector(package),
                max_cells=_cli_scheduling_limit(max_cells, field="max-cells"),
                ty_jobs=_cli_scheduling_limit(ty_jobs, field="ty-jobs"),
                test_jobs=_cli_scheduling_limit(test_jobs, field="test-jobs"),
                max_duration_seconds=_cli_duration(max_duration),
                search_resolution=search_resolution,
            )
        )
        result = context.apply_workflow.run(
            ApplyRequest(
                root=root,
                selector=_cli_selector(package),
                search_resolution=search_resolution,
            )
        )
        return context.presenter.render_minimize(search.report, result)

    @app.command(group=_INSPECT, sort_key=1)
    def diagnose(
        failure_id: _FAILURE_ID,
        /,
        *,
        package: _PACKAGE = None,
    ) -> int:
        """Explain a recorded rejection or indeterminate result."""
        context.presenter.bind_command("diagnose")
        request = DiagnoseRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            failure_id=_cli_failure_id(failure_id),
        )
        return context.presenter.render_diagnose(context.diagnose_workflow.run(request))

    @app.command(group=_INSPECT, sort_key=2)
    def merge(
        report: Annotated[
            Path,
            Parameter(help="A package-floor.json report to merge."),
        ],
        /,
        *reports: Annotated[
            Path,
            Parameter(help="A package-floor.json report to merge."),
        ],
        output: Annotated[
            Path,
            Parameter(help="Destination for the merged report."),
        ],
    ) -> int:
        """Combine compatible reports produced on different hosts."""
        context.presenter.bind_command("merge")
        request = MergeRequest(
            reports=tuple(path.as_posix() for path in (report, *reports)),
            output=output.as_posix(),
        )
        return context.presenter.render_merge(context.merge_workflow.run(request))

    for name in (
        "smoke",
        "check",
        "search",
        "explain",
        "apply",
        "minimize",
        "diagnose",
        "merge",
    ):
        app[name].usage = command_usage_line(name)
    return app


def build_context() -> CliContext:
    root = Path.cwd()
    logs = RunLogStore(root=root)
    presenter: TerminalPresenter | None = None
    try:
        presenter = TerminalPresenter(logs=logs, root=root)
        return CliContext(presenter=presenter, run_logs=logs, root=root)
    except BaseException:
        if presenter is not None:
            presenter.close(abandon_pending=True)
        logs.close()
        raise


def main() -> None:
    try:
        context = build_context()
    except KeyboardInterrupt:
        raise SystemExit(int(ExitCode.INTERRUPTED)) from None
    try:
        try:
            create_app(context)()
        except PfError as error:
            raise SystemExit(context.presenter.render_error(error)) from error
        except KeyboardInterrupt:
            context.interrupt_processes()
            try:
                raise SystemExit(context.presenter.render_interrupt()) from None
            except KeyboardInterrupt:
                raise SystemExit(int(ExitCode.INTERRUPTED)) from None
        except CycloptsError:
            raise SystemExit(1)
    finally:
        try:
            context.close()
        except KeyboardInterrupt:
            try:
                context.close()
            except KeyboardInterrupt:
                pass

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Annotated, Literal, Protocol

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
from pf.config import parse_jobs, parse_max_duration
from pf.coordinate_search import CoordinateSearch
from pf.environment import EnvironmentFactory
from pf.editor import ProjectEditor
from pf.errors import ConfigurationError, InvocationError, PfError
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.project import ProjectLoader
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
from pf.report import ValidatedReport
from pf.schemas.apply import ApplyCommandResult
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder
from pf.terminal import TerminalPresenter, command_usage, command_usage_line
from pf.verification import VerificationRunner
from pf.workflow import (
    CheckCommandWorkflow,
    CompatibilityChecker,
    DiagnoseCommandWorkflow,
    FailureDiagnosis,
    ApplyCommandWorkflow,
    ExplainCommandWorkflow,
    MergeCommandResult,
    MergeCommandWorkflow,
    SearchCommandWorkflow,
    SmokeCommandWorkflow,
)


class CheckWorkflow(Protocol):
    def run(self, request: CheckRequest) -> CheckResult: ...


class SearchWorkflow(Protocol):
    def run(self, request: SearchRequest) -> ValidatedReport: ...


class SmokeWorkflow(Protocol):
    def run(self, request: SmokeRequest) -> SmokeResult: ...


class ExplainWorkflow(Protocol):
    def run(self, request: ReportRequest) -> ValidatedReport: ...


class DiagnoseWorkflow(Protocol):
    def run(self, request: DiagnoseRequest) -> FailureDiagnosis: ...


class MergeWorkflow(Protocol):
    def run(self, request: MergeRequest) -> MergeCommandResult: ...


class ApplyWorkflow(Protocol):
    def run(self, request: ApplyRequest) -> ApplyCommandResult: ...


@dataclass
class CliContext:
    check_workflow: CheckWorkflow
    smoke_workflow: SmokeWorkflow
    search_workflow: SearchWorkflow
    explain_workflow: ExplainWorkflow
    diagnose_workflow: DiagnoseWorkflow
    merge_workflow: MergeWorkflow
    apply_workflow: ApplyWorkflow
    presenter: TerminalPresenter
    run_logs: RunLogStore
    _closed: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> "CliContext":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.presenter.close()
        self.run_logs.close()


_PACKAGE_HELP = (
    "Canonical distribution name of one installable workspace package. "
    "Omit to select the installable workspace root package."
)
_JOBS_HELP = "Maximum concurrent cells. Use auto or a positive integer."
_DURATION_HELP = (
    "Stop scheduling after DURATION and save an incomplete report. "
    "Accepts a positive integer followed by s, m, or h; use none for no limit."
)
_PACKAGE = Annotated[str | None, Parameter(help=_PACKAGE_HELP)]
_JOBS = Annotated[str, Parameter(help=_JOBS_HELP)]
_DURATION = Annotated[str | None, Parameter(help=_DURATION_HELP)]
_FAILURE_ID = Annotated[
    str,
    Parameter(help="A failure-<id> value; the failure- prefix may be omitted."),
]
_VERIFY = Group("Verify", sort_key=1)
_FIND = Group("Find and apply floors", sort_key=2)
_INSPECT = Group("Inspect and combine reports", sort_key=3)


def _cli_jobs(value: str) -> Literal["auto"] | int:
    try:
        return parse_jobs(value)
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
    """Create a Cyclopts app around already assembled application modules."""
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
        jobs: _JOBS = "auto",
    ) -> int:
        """Verify a fresh install with the newest versions allowed by current declarations."""
        context.presenter.bind_command("smoke")
        request = SmokeRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            jobs=_cli_jobs(jobs),
        )
        return context.presenter.render_smoke(context.smoke_workflow.run(request))

    @app.command(group=_VERIFY, sort_key=2)
    def check(
        *,
        package: _PACKAGE = None,
        jobs: _JOBS = "auto",
    ) -> int:
        """Verify the lower bounds declared by the project."""
        context.presenter.bind_command("check")
        request = CheckRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            jobs=_cli_jobs(jobs),
        )
        result = context.check_workflow.run(request)
        return context.presenter.render_check(result)

    @app.command(group=_FIND, sort_key=1)
    def search(
        *,
        package: _PACKAGE = None,
        jobs: _JOBS = "auto",
        max_duration: _DURATION = None,
    ) -> int:
        """Find verified floors and write package-floor.json."""
        context.presenter.bind_command("search")
        request = SearchRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            jobs=_cli_jobs(jobs),
            max_duration_seconds=_cli_duration(max_duration),
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
        force: Annotated[
            bool,
            Parameter(
                help="Accept source-layer drift after structural authorization."
            ),
        ] = False,
    ) -> int:
        """Update project metadata from authorized final floor evidence."""
        context.presenter.bind_command("apply")
        request = ApplyRequest(
            root=Path.cwd().as_posix(),
            selector=_cli_selector(package),
            force=force,
        )
        return context.presenter.render_apply(context.apply_workflow.run(request))

    @app.command(group=_FIND, sort_key=4)
    def minimize(
        *,
        package: _PACKAGE = None,
        jobs: _JOBS = "auto",
        max_duration: _DURATION = None,
    ) -> int:
        """Search for floors, then apply the authorized result."""
        context.presenter.bind_command("minimize")
        root = Path.cwd().as_posix()
        reports = context.search_workflow.run(
            SearchRequest(
                root=root,
                selector=_cli_selector(package),
                jobs=_cli_jobs(jobs),
                max_duration_seconds=_cli_duration(max_duration),
            )
        )
        result = context.apply_workflow.run(
            ApplyRequest(root=root, selector=_cli_selector(package))
        )
        return context.presenter.render_minimize(reports, result)

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
        return _assemble_context(root=root, logs=logs, presenter=presenter)
    except BaseException:
        if presenter is not None:
            presenter.close(abandon_pending=True)
        logs.close()
        raise


def _assemble_context(
    *,
    root: Path,
    logs: RunLogStore,
    presenter: TerminalPresenter,
) -> CliContext:
    registry_access = RegistryAccess.from_environment(os.environ)
    redactor = SecretRedactor(registry_access.secret_literals)
    runner = SubprocessRunner(redactor=redactor, listener=presenter, logs=logs)
    uv = UvAdapter(
        runner,
        registry_access=registry_access,
        redactor=redactor,
    )
    environments = EnvironmentFactory(uv, events=presenter)
    static = StaticEvaluator(TyAdapter(runner), events=presenter)
    full = RuntimeEvaluator(
        static=static,
        verifier=ConfiguredVerifier(runner),
        witnesses=RuntimeWitnessAdapter(runner),
        events=presenter,
    )
    checker = CompatibilityChecker(
        environments=environments,
        static=static,
        full=full,
        events=presenter,
    )
    highest = HighestVersionVerifier(
        environments=environments,
        static=static,
        full=full,
    )
    discovery = ProjectDiscovery()
    projects = ProjectLoader(pythons=uv, discovery=discovery)
    snapshots = SnapshotBuilder(runner)
    reports = ReportStore()
    verification = VerificationRunner(
        events=presenter,
        logs=logs,
    )
    return CliContext(
        check_workflow=CheckCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            checker=checker,
            verification=verification,
            events=presenter,
        ),
        presenter=presenter,
        smoke_workflow=SmokeCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            verifier=highest,
            verification=verification,
            events=presenter,
        ),
        search_workflow=SearchCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            coordinator=SearchCoordinator(
                environments=environments,
                candidates=CandidateBuilder(uv),
                static=static,
                full=full,
                highest=highest,
                coordinate_search=CoordinateSearch(),
                diagnostics=presenter,
                events=presenter,
            ),
            verification=verification,
            reports=reports,
            report_builder=PackageReportBuilder(),
            events=presenter,
            associations=logs,
        ),
        explain_workflow=ExplainCommandWorkflow(
            discovery=discovery,
            reports=reports,
        ),
        diagnose_workflow=DiagnoseCommandWorkflow(
            discovery=discovery,
            reports=reports,
            logs=logs,
        ),
        merge_workflow=MergeCommandWorkflow(reports=reports),
        apply_workflow=ApplyCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            reports=reports,
            authorizer=ApplyAuthorizer(),
            editor=ProjectEditor(snapshots=snapshots),
            events=presenter,
        ),
        run_logs=logs,
    )


def main() -> None:
    with build_context() as context:
        try:
            create_app(context)()
        except PfError as error:
            raise SystemExit(context.presenter.render_error(error)) from error
        except CycloptsError:
            raise SystemExit(1)

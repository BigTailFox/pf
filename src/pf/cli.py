from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Annotated, Literal, Protocol

from cyclopts import App, Group, Parameter
from cyclopts.exceptions import CycloptsError

from pf.adapters.process import SecretRedactor, SubprocessRunner
from pf.adapters.test_command import TestAdapter
from pf.adapters.ty import TyAdapter
from pf.adapters.uv import RegistryAccess, UvAdapter
from pf.baseline import HighestVersionVerifier
from pf.candidates import CandidateBuilder
from pf.config import parse_jobs, parse_max_duration
from pf.coordinate_search import CoordinateSearch
from pf.environment import EnvironmentFactory
from pf.editor import ProjectEditor
from pf.errors import ConfigurationError, InvocationError, PfError
from pf.evaluation import FullEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.project_discovery import ProjectDiscovery
from pf.report import PackageReportBuilder, ReportStore
from pf.runlog import RunLogStore
from pf.scheduling import Scheduler
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    DiagnoseRequest,
    MergeRequest,
    ReportRequest,
    SearchRequest,
    SmokeRequest,
)
from pf.schemas.evaluation import CheckResult, SmokeResult
from pf.schemas.report import PackageFloorReportV1, ProjectEditResult
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
    MergeCommandWorkflow,
    SearchCommandWorkflow,
    SmokeCommandWorkflow,
)


class CheckWorkflow(Protocol):
    def run(self, request: CheckRequest) -> CheckResult: ...


class SearchWorkflow(Protocol):
    def run(self, request: SearchRequest) -> tuple[PackageFloorReportV1, ...]: ...


class SmokeWorkflow(Protocol):
    def run(self, request: SmokeRequest) -> SmokeResult: ...


class ExplainWorkflow(Protocol):
    def run(self, request: ReportRequest) -> tuple[PackageFloorReportV1, ...]: ...


class DiagnoseWorkflow(Protocol):
    def run(self, request: DiagnoseRequest) -> tuple[FailureDiagnosis, ...]: ...


class MergeWorkflow(Protocol):
    def run(self, request: MergeRequest) -> PackageFloorReportV1: ...


class ApplyWorkflow(Protocol):
    def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]: ...


@dataclass(frozen=True)
class CliContext:
    check_workflow: CheckWorkflow
    presenter: TerminalPresenter
    smoke_workflow: SmokeWorkflow | None = None
    search_workflow: SearchWorkflow | None = None
    explain_workflow: ExplainWorkflow | None = None
    diagnose_workflow: DiagnoseWorkflow | None = None
    merge_workflow: MergeWorkflow | None = None
    apply_workflow: ApplyWorkflow | None = None
    run_logs: RunLogStore | None = None


_PACKAGE_HELP = (
    "Package name, directory, or pyproject.toml path. Omit to select all "
    "installable packages allowed by the root configuration."
)
_JOBS_HELP = "Maximum concurrent cells. Use auto or a positive integer."
_DURATION_HELP = (
    "Stop scheduling after DURATION and save an incomplete report. "
    "Accepts a positive integer followed by s, m, or h; use none for no limit."
)
_PACKAGE = Annotated[str | None, Parameter(help=_PACKAGE_HELP)]
_JOBS = Annotated[str, Parameter(help=_JOBS_HELP)]
_DURATION = Annotated[str | None, Parameter(help=_DURATION_HELP)]
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


def _invocation_error(error: CycloptsError) -> str:
    message = str(error.msg) if error.msg is not None else str(error)
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
    )

    @app.command(group=_VERIFY, sort_key=1)
    def smoke(
        package: _PACKAGE = None,
        /,
        *,
        jobs: _JOBS = "auto",
    ) -> int:
        """Verify a fresh install with the newest versions allowed by current declarations."""
        context.presenter.bind_command("smoke")
        if context.smoke_workflow is None:
            raise ConfigurationError("smoke workflow is not assembled")
        request = SmokeRequest(
            root=Path.cwd().as_posix(),
            package=package,
            jobs=_cli_jobs(jobs),
        )
        return context.presenter.render_smoke(context.smoke_workflow.run(request))

    @app.command(group=_VERIFY, sort_key=2)
    def check(
        package: _PACKAGE = None,
        /,
        *,
        jobs: _JOBS = "auto",
    ) -> int:
        """Verify the lower bounds declared by the project."""
        context.presenter.bind_command("check")
        request = CheckRequest(
            root=Path.cwd().as_posix(),
            package=package,
            jobs=_cli_jobs(jobs),
        )
        result = context.check_workflow.run(request)
        return context.presenter.render_check(result)

    @app.command(group=_FIND, sort_key=1)
    def search(
        package: _PACKAGE = None,
        /,
        *,
        jobs: _JOBS = "auto",
        max_duration: _DURATION = None,
    ) -> int:
        """Find verified floors and write package-floor.json."""
        context.presenter.bind_command("search")
        if context.search_workflow is None:
            raise ConfigurationError("search workflow is not assembled")
        request = SearchRequest(
            root=Path.cwd().as_posix(),
            package=package,
            jobs=_cli_jobs(jobs),
            max_duration_seconds=_cli_duration(max_duration),
        )
        return context.presenter.render_search(context.search_workflow.run(request))

    @app.command(group=_FIND, sort_key=2)
    def explain(package: _PACKAGE = None, /) -> int:
        """Show verified floors, coverage, and apply blockers in an existing report."""
        context.presenter.bind_command("explain")
        if context.explain_workflow is None:
            raise ConfigurationError("explain workflow is not assembled")
        request = ReportRequest(root=Path.cwd().as_posix(), package=package)
        return context.presenter.render_explain(context.explain_workflow.run(request))

    @app.command(group=_FIND, sort_key=3)
    def apply(package: _PACKAGE = None, /) -> int:
        """Update project metadata from a complete, current floor report."""
        context.presenter.bind_command("apply")
        if context.apply_workflow is None:
            raise ConfigurationError("apply workflow is not assembled")
        request = ApplyRequest(root=Path.cwd().as_posix(), package=package)
        return context.presenter.render_apply(context.apply_workflow.run(request))

    @app.command(group=_FIND, sort_key=4)
    def minimize(
        package: _PACKAGE = None,
        /,
        *,
        jobs: _JOBS = "auto",
        max_duration: _DURATION = None,
    ) -> int:
        """Search for floors, then apply only a complete result."""
        context.presenter.bind_command("minimize")
        if context.search_workflow is None or context.apply_workflow is None:
            raise ConfigurationError("minimize workflows are not assembled")
        root = Path.cwd().as_posix()
        reports = context.search_workflow.run(
            SearchRequest(
                root=root,
                package=package,
                jobs=_cli_jobs(jobs),
                max_duration_seconds=_cli_duration(max_duration),
            )
        )
        if any(report.result.status != "complete" for report in reports):
            return context.presenter.render_minimize(reports, None)
        edits = context.apply_workflow.run(ApplyRequest(root=root, package=package))
        return context.presenter.render_minimize(reports, edits)

    @app.command(group=_INSPECT, sort_key=1)
    def diagnose(
        package: _PACKAGE = None,
        /,
        *,
        failure: Annotated[
            str | None,
            Parameter(
                help=(
                    "Inspect one recorded failure. Omit to list every recorded "
                    "rejection or indeterminate result."
                )
            ),
        ] = None,
    ) -> int:
        """Explain a recorded rejection or indeterminate result."""
        context.presenter.bind_command("diagnose")
        if context.diagnose_workflow is None:
            raise ConfigurationError("diagnose workflow is not assembled")
        request = DiagnoseRequest(
            root=Path.cwd().as_posix(),
            package=package,
            failure_id=failure,
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
        if context.merge_workflow is None:
            raise ConfigurationError("merge workflow is not assembled")
        request = MergeRequest(
            reports=tuple(path.as_posix() for path in (report, *reports)),
            output=output.as_posix(),
        )
        merged = context.merge_workflow.run(request)
        return context.presenter.render_merge(merged, request.output)

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
    presenter = TerminalPresenter(logs=logs, root=root)
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
    full = FullEvaluator(static=static, tests=TestAdapter(runner), events=presenter)
    checker = CompatibilityChecker(
        environments=environments,
        static=static,
        full=full,
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
    scheduler = Scheduler()
    verification = VerificationRunner(
        scheduler=scheduler,
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
            reports=reports,
            editor=ProjectEditor(snapshots=snapshots),
            events=presenter,
        ),
        run_logs=logs,
    )


def main() -> None:
    context = build_context()
    try:
        create_app(context)()
    except PfError as error:
        raise SystemExit(context.presenter.render_error(error)) from error
    except CycloptsError:
        raise SystemExit(3)
    finally:
        context.presenter.close()
        if context.run_logs is not None:
            context.run_logs.close()

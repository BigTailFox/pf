from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cyclopts import App

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import TestAdapter
from pf.adapters.ty import TyAdapter
from pf.adapters.uv import UvAdapter
from pf.candidates import CandidateBuilder
from pf.config import parse_jobs, parse_max_duration
from pf.environment import EnvironmentFactory
from pf.editor import ProjectEditor
from pf.errors import ConfigurationError, PfError
from pf.evaluation import FullEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.scheduling import Scheduler
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    MergeRequest,
    ReportRequest,
    SearchRequest,
)
from pf.schemas.evaluation import CheckResult
from pf.schemas.report import PackageFloorReportV1, ProjectEditResult
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder
from pf.terminal import TerminalPresenter
from pf.workflow import (
    CheckCommandWorkflow,
    CompatibilityChecker,
    ApplyCommandWorkflow,
    ExplainCommandWorkflow,
    MergeCommandWorkflow,
    SearchCommandWorkflow,
)


class CheckWorkflow(Protocol):
    def run(self, request: CheckRequest) -> CheckResult: ...


class SearchWorkflow(Protocol):
    def run(self, request: SearchRequest) -> tuple[PackageFloorReportV1, ...]: ...


class ExplainWorkflow(Protocol):
    def run(self, request: ReportRequest) -> tuple[PackageFloorReportV1, ...]: ...


class MergeWorkflow(Protocol):
    def run(self, request: MergeRequest) -> PackageFloorReportV1: ...


class ApplyWorkflow(Protocol):
    def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]: ...


@dataclass(frozen=True)
class CliContext:
    check_workflow: CheckWorkflow
    presenter: TerminalPresenter
    search_workflow: SearchWorkflow | None = None
    explain_workflow: ExplainWorkflow | None = None
    merge_workflow: MergeWorkflow | None = None
    apply_workflow: ApplyWorkflow | None = None


def create_app(context: CliContext) -> App:
    """Create a Cyclopts app around already assembled application modules."""
    app = App(
        name="pf",
        help="Find verified lower bounds for direct Python dependencies.",
    )

    @app.command
    def check(
        package: str | None = None,
        *,
        jobs: str = "auto",
    ) -> int:
        """Verify the package's current dependency declarations.

        Parameters
        ----------
        package
            Package name or path to verify.
        jobs
            Global worker limit: ``auto`` or a positive integer.
        """
        request = CheckRequest(
            root=Path.cwd().as_posix(),
            package=package,
            jobs=parse_jobs(jobs),
        )
        result = context.check_workflow.run(request)
        return context.presenter.render_check(result)

    @app.command
    def search(
        package: str | None = None,
        *,
        jobs: str = "auto",
        max_duration: str | None = None,
    ) -> int:
        """Search for verified direct-dependency floors.

        Parameters
        ----------
        package
            Package name or path to search.
        jobs
            Global worker limit: ``auto`` or a positive integer.
        max_duration
            Stop scheduling after this duration and save an incomplete report.
        """
        if context.search_workflow is None:
            raise ConfigurationError("search workflow is not assembled")
        request = SearchRequest(
            root=Path.cwd().as_posix(),
            package=package,
            jobs=parse_jobs(jobs),
            max_duration_seconds=parse_max_duration(max_duration),
        )
        return context.presenter.render_search(context.search_workflow.run(request))

    @app.command
    def apply(package: str | None = None) -> int:
        """Apply an authorized floor report to project metadata.

        Parameters
        ----------
        package
            Package name or path to update.
        """
        if context.apply_workflow is None:
            raise ConfigurationError("apply workflow is not assembled")
        request = ApplyRequest(root=Path.cwd().as_posix(), package=package)
        return context.presenter.render_apply(context.apply_workflow.run(request))

    @app.command
    def minimize(
        package: str | None = None,
        *,
        jobs: str = "auto",
        max_duration: str | None = None,
    ) -> int:
        """Search for floors and apply them when the report is complete.

        Parameters
        ----------
        package
            Package name or path to minimize.
        jobs
            Global worker limit: ``auto`` or a positive integer.
        max_duration
            Stop scheduling after this duration and save an incomplete report.
        """
        if context.search_workflow is None or context.apply_workflow is None:
            raise ConfigurationError("minimize workflows are not assembled")
        root = Path.cwd().as_posix()
        reports = context.search_workflow.run(
            SearchRequest(
                root=root,
                package=package,
                jobs=parse_jobs(jobs),
                max_duration_seconds=parse_max_duration(max_duration),
            )
        )
        search_exit = context.presenter.render_search(reports)
        if search_exit != 0:
            return search_exit
        edits = context.apply_workflow.run(ApplyRequest(root=root, package=package))
        return context.presenter.render_apply(edits)

    @app.command
    def explain(package: str | None = None) -> int:
        """Explain evidence in an existing floor report.

        Parameters
        ----------
        package
            Package name or path whose report should be explained.
        """
        if context.explain_workflow is None:
            raise ConfigurationError("explain workflow is not assembled")
        request = ReportRequest(root=Path.cwd().as_posix(), package=package)
        return context.presenter.render_explain(context.explain_workflow.run(request))

    @app.command
    def merge(*reports: Path, output: Path) -> int:
        """Merge compatible reports produced on different hosts.

        Parameters
        ----------
        reports
            Reports to merge.
        output
            Destination for the merged report.
        """
        if context.merge_workflow is None:
            raise ConfigurationError("merge workflow is not assembled")
        request = MergeRequest(
            reports=tuple(path.as_posix() for path in reports),
            output=output.as_posix(),
        )
        merged = context.merge_workflow.run(request)
        return context.presenter.render_merge(merged, request.output)

    return app


def build_context() -> CliContext:
    presenter = TerminalPresenter()
    runner = SubprocessRunner(listener=presenter)
    uv = UvAdapter(runner)
    environments = EnvironmentFactory(uv, events=presenter)
    static = StaticEvaluator(TyAdapter(runner), events=presenter)
    full = FullEvaluator(static=static, tests=TestAdapter(runner), events=presenter)
    checker = CompatibilityChecker(
        environments=environments,
        static=static,
        full=full,
    )
    projects = ProjectLoader(pythons=uv)
    snapshots = SnapshotBuilder(runner)
    reports = ReportStore()
    scheduler = Scheduler()
    return CliContext(
        check_workflow=CheckCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            checker=checker,
            scheduler=scheduler,
            events=presenter,
        ),
        presenter=presenter,
        search_workflow=SearchCommandWorkflow(
            projects=projects,
            snapshots=snapshots,
            coordinator=SearchCoordinator(
                environments=environments,
                candidates=CandidateBuilder(uv),
                static=static,
                full=full,
            ),
            scheduler=scheduler,
            reports=reports,
            report_builder=PackageReportBuilder(),
            events=presenter,
        ),
        explain_workflow=ExplainCommandWorkflow(
            projects=projects,
            reports=reports,
        ),
        merge_workflow=MergeCommandWorkflow(reports=reports),
        apply_workflow=ApplyCommandWorkflow(
            projects=projects,
            reports=reports,
            editor=ProjectEditor(snapshots=snapshots),
            events=presenter,
        ),
    )


def main() -> None:
    context = build_context()
    try:
        create_app(context)()
    except PfError as error:
        raise SystemExit(context.presenter.render_error(error)) from error
    finally:
        context.presenter.close()

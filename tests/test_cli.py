from __future__ import annotations

from dataclasses import replace
import os
import re
import runpy
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import NoReturn, cast

import pytest
from cyclopts.exceptions import CycloptsError
from rich.console import Console

from pf.cli import (
    ApplyWorkflow as ApplyWorkflowProtocol,
    CheckWorkflow as CheckWorkflowProtocol,
    CliContext,
    DiagnoseWorkflow as DiagnoseWorkflowProtocol,
    ExplainWorkflow as ExplainWorkflowProtocol,
    MergeWorkflow as MergeWorkflowProtocol,
    SearchWorkflow as SearchWorkflowProtocol,
    SmokeWorkflow as SmokeWorkflowProtocol,
    build_context,
    create_app,
    main,
)
from pf.errors import (
    ApplyAuthorizationError,
    ConfigurationError,
    NoApplicableFloorError,
    PfError,
    SearchSpaceResolutionError,
)
from pf.report import PackageReportBuilder, ValidatedReport
from pf.runlog import RunLogStore
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    DiagnoseRequest,
    EffectiveConfig,
    MergeRequest,
    ReportRequest,
    WorkspacePackage,
    SearchRequest,
    SmokeRequest,
)
from pf.schemas.apply import ApplyCommandResult, ApplyPresentationFacts
from pf.schemas.evaluation import CheckPass, SmokePass, StatusEvent
from pf.schemas.project import (
    ApplySelector,
    Cell,
    PackagePlan,
    SourcePlan,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)
from pf.schemas.report import (
    CellSuccess,
    IncompleteReportResult,
    ProjectEditResult,
)
from pf.terminal import TerminalPresenter
from pf.workflow import ExplainCommandResult, MergeCommandResult, SearchCommandResult


class NeverCheck:
    def run(self, request: CheckRequest) -> CheckPass:
        raise AssertionError("check should not run")


class NeverCalledWorkflow:
    def run(self, request: object) -> NoReturn:
        raise AssertionError("unselected workflow should not run")


class NoOpRunLogs:
    def close(self) -> None:
        return


def apply_result(
    *,
    changed: bool,
    selected: tuple[ApplySelector, ...] = (),
    preserved: tuple[ApplySelector, ...] = (),
    source_drift_path_count: int = 0,
    source_drift_paths: tuple[str, ...] = (),
) -> ApplyCommandResult:
    return ApplyCommandResult(
        package="demo",
        edit=ProjectEditResult(
            changed=changed,
            pyproject_path="pyproject.toml",
            recovery_log_path=".pf/apply-recovery.json",
        ),
        presentation_facts=ApplyPresentationFacts(
            observed_cells=2,
            selected_selectors=selected,
            preserved_selectors=preserved,
            source_drift_path_count=source_drift_path_count,
            source_drift_paths=source_drift_paths,
        ),
    )


def make_context(
    *,
    presenter: TerminalPresenter,
    check_workflow: CheckWorkflowProtocol | None = None,
    smoke_workflow: SmokeWorkflowProtocol | None = None,
    search_workflow: SearchWorkflowProtocol | None = None,
    explain_workflow: ExplainWorkflowProtocol | None = None,
    diagnose_workflow: DiagnoseWorkflowProtocol | None = None,
    merge_workflow: MergeWorkflowProtocol | None = None,
    apply_workflow: ApplyWorkflowProtocol | None = None,
    run_logs: RunLogStore | None = None,
) -> CliContext:
    context = CliContext(
        presenter=presenter,
        run_logs=(
            run_logs if run_logs is not None else cast(RunLogStore, NoOpRunLogs())
        ),
    )
    context._check_workflow = (
        check_workflow if check_workflow is not None else NeverCalledWorkflow()
    )
    context._smoke_workflow = (
        smoke_workflow if smoke_workflow is not None else NeverCalledWorkflow()
    )
    context._search_workflow = (
        search_workflow if search_workflow is not None else NeverCalledWorkflow()
    )
    context._explain_workflow = (
        explain_workflow if explain_workflow is not None else NeverCalledWorkflow()
    )
    context._diagnose_workflow = (
        diagnose_workflow if diagnose_workflow is not None else NeverCalledWorkflow()
    )
    context._merge_workflow = (
        merge_workflow if merge_workflow is not None else NeverCalledWorkflow()
    )
    context._apply_workflow = (
        apply_workflow if apply_workflow is not None else NeverCalledWorkflow()
    )
    return context


def minimal_report() -> ValidatedReport:
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(),
        source_routes=(),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest((), ()),
        entries=(),
        pyproject_identities=(),
    )
    return PackageReportBuilder().build(
        package=package,
        source_plan=SourcePlan.for_package(package, "SEARCH"),
        source_snapshot=snapshot,
        cell_results=(),
    )


def located_search(report: ValidatedReport) -> SearchCommandResult:
    return SearchCommandResult(report=report, report_path="package-floor.json")


def located_explain(report: ValidatedReport) -> ExplainCommandResult:
    return ExplainCommandResult(report=report, report_path="package-floor.json")


def host_partial_report() -> ValidatedReport:
    local = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    remote = Cell(
        package="demo",
        target="aarch64-apple-darwin",
        python_minor="3.10",
        extra_surface=(),
    )
    return replace(
        minimal_report(),
        target_cells=(local, remote),
        cell_results=(CellSuccess.model_construct(cell=local),),
        result=IncompleteReportResult(status="incomplete", reasons=("MISSING_CELL",)),
    )


def invoke_app(*args: str) -> subprocess.CompletedProcess[str]:
    stdout = StringIO()
    stderr = StringIO()
    context = make_context(
        presenter=TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
        )
    )
    try:
        result = create_app(context)(
            list(args),
            exit_on_error=False,
            result_action="return_value",
        )
        return_code = int(result or 0)
    except PfError as error:
        return_code = context.presenter.render_error(error)
    except CycloptsError:
        return_code = 1
    finally:
        context.close()
    return subprocess.CompletedProcess(
        args=("pf", *args),
        returncode=return_code,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


@pytest.fixture(scope="module")
def module_help() -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["COLUMNS"] = "200"
    return subprocess.run(
        [sys.executable, "-m", "pf", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


class TestCliInterface:
    def test_search_space_resolution_error_is_exit_two_with_facts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stdout, stderr = StringIO(), StringIO()
        class Search:
            def run(self, request: SearchRequest) -> NoReturn:
                raise SearchSpaceResolutionError(dependency="demo-dep", cell="demo/3.10/base",
                    expression="majors[baseline]", reason="missing-anchor-series",
                    anchors=(("baseline", "3"),), series_keys=((0, 1),), source="https://pypi.org/simple")
        context = make_context(search_workflow=Search(), presenter=TerminalPresenter(
            stdout=Console(file=stdout), stderr=Console(file=stderr, width=180)))
        monkeypatch.setattr("pf.cli.build_context", lambda: context)
        monkeypatch.setattr(sys, "argv", ["pf", "search"])
        with pytest.warns(UserWarning, match="Cyclopts application invoked without tokens"):
            with pytest.raises(SystemExit) as caught:
                main()
        assert caught.value.code == 2
        output = stderr.getvalue()
        assert "search-space-resolution" in output
        assert "majors[baseline]" in output
        assert "missing-anchor-series" in output
        assert "demo-dep" in output
        assert "Traceback" not in output

    def test_module_entrypoint_routes_to_cli_help(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = False

        def record_call() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr("pf.cli.main", record_call)

        runpy.run_module("pf.__main__", run_name="__main__")

        assert called

    def test_module_help_lists_every_v1_command(
        self,
        module_help: subprocess.CompletedProcess[str],
    ) -> None:
        assert module_help.returncode == 0, module_help.stderr
        for command in (
            "check",
            "smoke",
            "search",
            "apply",
            "minimize",
            "explain",
            "diagnose",
            "merge",
        ):
            assert command in module_help.stdout
        assert "Verify" in module_help.stdout
        assert "Find and apply floors" in module_help.stdout
        assert "Inspect and combine reports" in module_help.stdout
        assert (
            "Typical workflow: pf smoke -> pf search -> pf explain -> pf apply"
            in module_help.stdout
        )
        stdout = module_help.stdout
        assert stdout.index("Verify") < stdout.index("Find and apply floors")
        assert stdout.index("smoke") < stdout.index("check")

    def test_module_help_caps_the_outer_canvas_at_120_columns(
        self,
        module_help: subprocess.CompletedProcess[str],
    ) -> None:
        assert module_help.returncode == 0, module_help.stderr
        assert max(map(len, module_help.stdout.splitlines())) <= 120

    def test_invocation_errors_use_the_120_column_error_console(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COLUMNS", "200")
        invalid_option = "--" + "not-a-real-option-" * 10

        result = subprocess.run(
            [sys.executable, "-m", "pf", "check", invalid_option],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert result.stdout == ""
        assert "Error:" in result.stderr
        assert max(map(len, result.stderr.splitlines())) <= 120

    def test_merge_help_usage_names_reports_and_hides_report_option(self) -> None:
        result = invoke_app("merge", "--help")

        assert result.returncode == 0, result.stderr
        assert "Usage: pf merge REPORT [REPORT ...] --output PATH" in result.stdout
        assert "[ARGS]" not in result.stdout
        assert "--report" not in result.stdout
        assert "--package" not in result.stdout

    def test_merge_without_reports_is_a_usage_error(self) -> None:
        result = invoke_app("merge", "--output", "merged.json")

        assert result.returncode == 1
        assert "Error:" in result.stderr
        assert "Usage: pf merge REPORT [REPORT ...] --output PATH" in result.stderr
        assert "Try 'pf merge --help'" in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_console_script_help_matches_module_help(
        self,
        module_help: subprocess.CompletedProcess[str],
    ) -> None:
        environment = os.environ.copy()
        environment["COLUMNS"] = "200"
        script = subprocess.run(
            ["uv", "run", "--no-sync", "pf", "--help"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        assert module_help.returncode == 0, module_help.stderr
        assert script.returncode == 0, script.stderr
        assert module_help.stdout == script.stdout

    def test_unknown_option_is_an_invocation_error(self) -> None:
        result = invoke_app("check", "--not-a-flag")

        assert result.returncode == 1
        assert result.stdout == ""
        assert "Error:" in result.stderr
        assert "Usage:" in result.stderr
        assert "Try 'pf check --help'" in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_illegal_stage_limit_is_an_invocation_error(self) -> None:
        result = invoke_app("check", "--ty-jobs", "nope")

        assert result.returncode == 1
        assert "Error:" in result.stderr
        assert "positive integer" in result.stderr
        assert "Usage: pf check [OPTIONS]" in result.stderr
        assert "Try 'pf check --help'" in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_illegal_duration_restates_accepted_format(self) -> None:
        result = invoke_app("search", "--max-duration", "10minutes")

        assert result.returncode == 1
        assert "Error:" in result.stderr
        assert "30s" in result.stderr
        assert "10m" in result.stderr
        assert "2h" in result.stderr
        assert "none" in result.stderr
        assert "Usage:" in result.stderr
        assert "Try 'pf search --help'" in result.stderr
        assert "Traceback" not in result.stderr

    def test_unknown_package_is_a_configuration_error(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "__init__.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [build-system]
    requires = ["uv_build>=0.8.22,<0.9.0"]
    build-backend = "uv_build"

    [tool.pf]
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, "-m", "pf", "check", "--package", "other"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 3
        assert "configuration:" in result.stderr
        assert "unknown package selection: other" in result.stderr
        assert "Known packages: demo" in result.stderr
        assert "Usage:" not in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_module_entrypoint_reexports_cli_main(self) -> None:
        import pf.__main__ as module
        from pf.cli import main

        assert module.main is main

    def test_apply_failure_does_not_claim_floors_were_applied(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=True),
            stderr=Console(file=stderr, force_terminal=True),
        )

        class ApplyWorkflow:
            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                presenter.consume(StatusEvent(message="applying floors"))
                raise NoApplicableFloorError("cannot apply an incomplete floor report")

        context = make_context(
            check_workflow=NeverCheck(),
            apply_workflow=ApplyWorkflow(),
            presenter=presenter,
        )

        with pytest.raises(SystemExit) as caught:
            try:
                create_app(context)(
                    ["apply"],
                    exit_on_error=False,
                    result_action="return_value",
                )
            except PfError as error:
                raise SystemExit(context.presenter.render_error(error)) from error
            finally:
                context.presenter.close()

        assert caught.value.code == 2
        assert stdout.getvalue() == ""
        assert "applied floors" not in stderr.getvalue()
        assert (
            "no-applicable-floor: cannot apply an incomplete floor report"
            in stderr.getvalue()
        )

    def test_apply_authorization_failure_exits_three_without_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ApplyWorkflow:
            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                raise ApplyAuthorizationError("dependency declarations drifted")

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        context = make_context(
            apply_workflow=ApplyWorkflow(),
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        with pytest.raises(ApplyAuthorizationError) as caught:
            create_app(context)(
                ["apply"],
                exit_on_error=False,
                result_action="return_value",
            )

        assert context.presenter.render_error(caught.value) == 3
        assert stdout.getvalue() == ""
        assert "dependency declarations drifted" in stderr.getvalue()
        assert "Applied floors" not in stderr.getvalue()


class TestCommandDispatch:
    @pytest.mark.parametrize(
        ("command", "expected_fragments"),
        (
            ("smoke", ("--max-cells", "--ty-jobs", "--test-jobs", "auto")),
            ("check", ("--max-cells", "--ty-jobs", "--test-jobs", "auto")),
            (
                "search",
                ("--max-cells", "--ty-jobs", "--test-jobs", "--max-duration", "none"),
            ),
            ("explain", ()),
            ("apply", ("--force",)),
            ("minimize", ("--max-cells", "--ty-jobs", "--test-jobs")),
            (
                "diagnose",
                (
                    "recorded rejection or indeterminate result",
                    "FAILURE_ID",
                    "failure ID containing 16",
                    "lowercase hexadecimal characters",
                    "optionally prefixed with failure-",
                ),
            ),
        ),
    )
    def test_command_help_describes_public_interface(
        self,
        command: str,
        expected_fragments: tuple[str, ...],
    ) -> None:
        result = invoke_app(command, "--help")

        assert result.returncode == 0, result.stderr
        usage = (
            "Usage: pf diagnose FAILURE_ID [OPTIONS]"
            if command == "diagnose"
            else f"Usage: pf {command} [OPTIONS]"
        )
        assert usage in result.stdout
        assert "[ARGS]" not in result.stdout
        assert "--package" in result.stdout
        normalized_help = " ".join(result.stdout.split())
        assert all(fragment in normalized_help for fragment in expected_fragments)

    def test_verification_commands_do_not_expose_pruning_options(self) -> None:
        from pf.schemas.config import TestConfig

        assert "prun" not in " ".join(TestConfig.model_fields)
        assert "failed_case" not in " ".join(TestConfig.model_fields)
        for command in ("smoke", "check", "search"):
            result = invoke_app(command, "--help")
            text = f"{result.stdout}\n{result.stderr}".casefold()
            assert result.returncode == 0, result.stderr
            assert "prun" not in text
            assert "failed-case" not in text
            assert "maxfail" not in text

    def test_apply_exposes_only_the_defined_force_option(self) -> None:
        help_result = invoke_app("apply", "--help")
        invocation_result = invoke_app("apply", "--no-force")

        assert help_result.returncode == 0
        assert "--force" in help_result.stdout
        assert "--no-force" not in help_result.stdout
        assert invocation_result.returncode == 1
        assert invocation_result.stdout == ""
        assert "--no-force" in invocation_result.stderr
        assert "Usage: pf apply [OPTIONS]" in invocation_result.stderr
        assert "pf apply --help" in invocation_result.stderr

    @pytest.mark.parametrize(
        ("arguments", "error_fragment"),
        (
            ((), "Missing argument 'FAILURE_ID'"),
            (("not-a-failure",), "expected failure-<16 hex> or <16 hex>"),
            (
                ("02cc9a72fbcd6cf0", "unexpected"),
                "unexpected",
            ),
        ),
    )
    def test_diagnose_invocation_errors_use_the_single_id_usage(
        self,
        arguments: tuple[str, ...],
        error_fragment: str,
    ) -> None:
        result = invoke_app("diagnose", *arguments)

        assert result.returncode == 1
        assert result.stdout == ""
        assert error_fragment in result.stderr
        assert "Usage: pf diagnose FAILURE_ID [OPTIONS]" in result.stderr
        assert "Try 'pf diagnose --help' for more information." in result.stderr

    def test_check_command_normalizes_limits_before_workflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class CheckWorkflow:
            def __init__(self) -> None:
                self.request: CheckRequest | None = None

            def run(self, request: CheckRequest) -> CheckPass:
                self.request = request
                return CheckPass(evaluations=())

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        workflow = CheckWorkflow()
        context = make_context(
            check_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        exit_code = create_app(context)(
            [
                "check",
                "--package",
                "demo",
                "--max-cells",
                "2",
                "--ty-jobs",
                "auto",
                "--test-jobs",
                "3",
            ],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == CheckRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
            max_cells=2,
            ty_jobs="auto",
            test_jobs=3,
        )

    def test_check_command_builds_a_request_and_renders_the_workflow_result(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class CheckWorkflow:
            def __init__(self) -> None:
                self.request: CheckRequest | None = None

            def run(self, request: CheckRequest) -> CheckPass:
                self.request = request
                return CheckPass(evaluations=())

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        workflow = CheckWorkflow()
        context = make_context(
            check_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        exit_code = create_app(context)(
            ["check", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request is not None
        assert workflow.request.selector == WorkspacePackage(canonical_name="demo")
        assert workflow.request.root == tmp_path.as_posix()
        assert workflow.request.max_cells is None
        assert workflow.request.ty_jobs is None
        assert workflow.request.test_jobs is None
        assert stdout.getvalue() == "✓  Check passed · 0 cells\n"
        assert stderr.getvalue() == ""

    def test_smoke_command_builds_a_request_and_renders_the_workflow_result(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SmokeWorkflow:
            def __init__(self) -> None:
                self.request: SmokeRequest | None = None

            def run(self, request: SmokeRequest) -> SmokePass:
                self.request = request
                return SmokePass(outcomes=())

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        workflow = SmokeWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            smoke_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        exit_code = create_app(context)(
            ["smoke", "--package", "demo", "--max-cells", "2"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == SmokeRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
            max_cells=2,
        )
        assert stdout.getvalue() == "✓  Smoke passed · 0 cells\n"
        assert stderr.getvalue() == ""

    def test_search_command_normalizes_limits_and_duration_before_workflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def __init__(self) -> None:
                self.request: SearchRequest | None = None

            def run(self, request: SearchRequest) -> SearchCommandResult:
                self.request = request
                return located_search(minimal_report())

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        workflow = SearchWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            search_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        exit_code = create_app(context)(
            [
                "search",
                "--package",
                "demo",
                "--max-cells",
                "2",
                "--max-duration",
                "1m",
            ],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == SearchRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
            max_cells=2,
            max_duration_seconds=60,
        )
        assert "Search complete · package-floor.json" in stdout.getvalue()
        assert stderr.getvalue() == ""

    def test_explain_command_only_requests_existing_reports(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ExplainWorkflow:
            def __init__(self) -> None:
                self.request: ReportRequest | None = None

            def run(self, request: ReportRequest) -> ExplainCommandResult:
                self.request = request
                return located_explain(minimal_report())

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        workflow = ExplainWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            explain_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(
                    file=StringIO(), force_terminal=False, color_system=None
                ),
            ),
        )

        exit_code = create_app(context)(
            ["explain", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == ReportRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
        )
        assert "demo · package-floor.json" in stdout.getvalue()

    def test_diagnose_command_normalizes_one_failure_id_before_the_workflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class DiagnoseWorkflow:
            def __init__(self) -> None:
                self.request: DiagnoseRequest | None = None

            def run(self, request: DiagnoseRequest):
                self.request = request
                return object()

        class Presenter(TerminalPresenter):
            def render_diagnose(self, diagnosis: object) -> int:
                assert diagnosis is not None
                return 0

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        workflow = DiagnoseWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            diagnose_workflow=workflow,
            presenter=Presenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(
                    file=StringIO(), force_terminal=False, color_system=None
                ),
            ),
        )

        exit_code = create_app(context)(
            ["diagnose", "02cc9a72fbcd6cf0", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == DiagnoseRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
            failure_id="failure-02cc9a72fbcd6cf0",
        )
        assert stdout.getvalue() == ""

    def test_merge_command_passes_explicit_inputs_and_output_to_workflow(
        self,
        tmp_path: Path,
    ) -> None:
        class MergeWorkflow:
            def __init__(self) -> None:
                self.request: MergeRequest | None = None

            def run(self, request: MergeRequest) -> MergeCommandResult:
                self.request = request
                return MergeCommandResult(
                    report=minimal_report(),
                    input_paths=request.reports,
                    output_path=request.output,
                )

        stdout = StringIO()
        workflow = MergeWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            merge_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(
                    file=StringIO(), force_terminal=False, color_system=None
                ),
            ),
        )
        source = tmp_path / "source.json"
        output = tmp_path / "merged.json"

        exit_code = create_app(context)(
            ["merge", source.as_posix(), "--output", output.as_posix()],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == MergeRequest(
            reports=(source.as_posix(),),
            output=output.as_posix(),
        )
        rendered = stdout.getvalue()
        normalized = " ".join(rendered.split())
        assert "Merge completed" in normalized
        assert source.as_posix() in normalized
        assert f"Merge complete · {output.as_posix()}" in normalized
        assert "1 report" not in rendered

    def test_apply_command_uses_report_only_workflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ApplyWorkflow:
            def __init__(self) -> None:
                self.request: ApplyRequest | None = None

            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                self.request = request
                return apply_result(changed=True)

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        workflow = ApplyWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            apply_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(
                    file=StringIO(), force_terminal=False, color_system=None
                ),
            ),
        )

        exit_code = create_app(context)(
            ["apply", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == ApplyRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
        )
        rendered = " ".join(stdout.getvalue().split())
        assert "demo · applied verified floors" in rendered
        assert "Metadata pyproject.toml updated" in rendered
        assert rendered.endswith("✓ Applied floors · project updated")

    @pytest.mark.parametrize("force_terminal", (False, True))
    def test_apply_reports_platform_scope_in_one_stdout_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        force_terminal: bool,
    ) -> None:
        class ApplyWorkflow:
            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                assert request.force is False
                return apply_result(
                    changed=True,
                    selected=(
                        ApplySelector(sys_platform="linux", platform_machine="x86_64"),
                    ),
                    preserved=(
                        ApplySelector(sys_platform="win32", platform_machine="AMD64"),
                    ),
                )

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        context = make_context(
            presenter=TerminalPresenter(
                stdout=Console(
                    file=stdout,
                    force_terminal=force_terminal,
                    color_system=None,
                ),
                stderr=Console(
                    file=stderr,
                    force_terminal=force_terminal,
                    color_system=None,
                ),
            ),
            apply_workflow=ApplyWorkflow(),
        )

        exit_code = create_app(context)(
            ["apply", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        rendered = " ".join(stdout.getvalue().split())
        assert "Scope linux/x86_64 verified" in rendered
        assert (
            "Preserved windows/x86_64 · original constraints retained" in rendered
        )
        assert rendered.endswith("✓ Applied floors · project updated")

    @pytest.mark.parametrize("force_terminal", (False, True))
    def test_apply_force_reports_the_used_source_waiver_only_on_stderr(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        force_terminal: bool,
    ) -> None:
        class ApplyWorkflow:
            def __init__(self) -> None:
                self.request: ApplyRequest | None = None

            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                self.request = request
                return apply_result(
                    changed=True,
                    selected=(
                        ApplySelector(sys_platform="linux", platform_machine="x86_64"),
                    ),
                    source_drift_path_count=10,
                    source_drift_paths=tuple(
                        f"src/path-{index}.py" for index in range(8)
                    ),
                )

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        workflow = ApplyWorkflow()
        context = make_context(
            presenter=TerminalPresenter(
                stdout=Console(
                    file=stdout,
                    force_terminal=force_terminal,
                    color_system=None,
                ),
                stderr=Console(
                    file=stderr,
                    force_terminal=force_terminal,
                    color_system=None,
                ),
            ),
            apply_workflow=workflow,
        )

        exit_code = create_app(context)(
            ["apply", "--package", "demo", "--force"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == ApplyRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
            force=True,
        )
        assert stdout.getvalue() == ""
        rendered = stderr.getvalue()
        assert "Evidence" in rendered
        assert "2 observed cells passed" in rendered
        assert "Override" in rendered
        assert "source drift accepted · 10 paths" in rendered
        normalized = " ".join(rendered.split())
        assert "src/path-7.py" in normalized
        assert "(+2" in normalized
        assert "more)" in normalized
        assert rendered.count("Applied floors") == 1
        assert (
            "⚠  Applied floors with source-drift override · project updated" in rendered
        )

    def test_cli_context_bootstraps_without_command_workflows(self) -> None:
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        )
        context = CliContext(
            presenter=presenter,
            run_logs=cast(RunLogStore, NoOpRunLogs()),
        )

        assert context._check_workflow is None
        assert context._search_workflow is None
        assert context._apply_workflow is None
        context.close()

    def test_cli_context_closes_presenter_then_logs_once(self) -> None:
        events: list[str] = []

        class Presenter:
            def close(self) -> None:
                events.append("presenter")

        class Logs:
            def close(self) -> None:
                events.append("logs")

        never = NeverCalledWorkflow()
        context = CliContext(
            presenter=cast(TerminalPresenter, Presenter()),
            run_logs=cast(RunLogStore, Logs()),
        )
        context._check_workflow = never

        with context:
            pass
        context.close()

        assert events == ["presenter", "logs"]


class TestMinimizeCommand:
    def test_minimize_reuses_default_apply_for_an_incomplete_search_report(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def run(self, request: SearchRequest) -> SearchCommandResult:
                return located_search(minimal_report())

        class ApplyWorkflow:
            def __init__(self) -> None:
                self.request: ApplyRequest | None = None

            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                self.request = request
                return apply_result(changed=False)

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        apply = ApplyWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            search_workflow=SearchWorkflow(),
            apply_workflow=apply,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        exit_code = create_app(context)(
            ["minimize", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert apply.request == ApplyRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
        )
        rendered = " ".join(stdout.getvalue().split())
        assert "demo · minimized verified floors" in rendered
        assert rendered.endswith("✓ Minimized floors · no metadata changes")

    def test_minimize_warns_when_host_partial_search_still_applies(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def run(self, request: SearchRequest) -> SearchCommandResult:
                return located_search(host_partial_report())

        class ApplyWorkflow:
            def __init__(self) -> None:
                self.request: ApplyRequest | None = None

            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                self.request = request
                return apply_result(
                    changed=False,
                    selected=(
                        ApplySelector(sys_platform="linux", platform_machine="x86_64"),
                    ),
                    preserved=(
                        ApplySelector(sys_platform="darwin", platform_machine="arm64"),
                    ),
                )

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        apply = ApplyWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            search_workflow=SearchWorkflow(),
            apply_workflow=apply,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        exit_code = create_app(context)(
            ["minimize", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert apply.request == ApplyRequest(
            root=tmp_path.as_posix(),
            selector=WorkspacePackage(canonical_name="demo"),
        )
        assert stdout.getvalue() == ""
        rendered = " ".join(stderr.getvalue().split())
        assert "demo · minimized verified floors" in rendered
        assert "Scope linux/x86_64 verified" in rendered
        assert "Preserved macos/arm64 · original constraints retained" in rendered
        preserved_text = rendered.split("Preserved", 1)[1].split("Metadata", 1)[0]
        assert "passed" not in preserved_text
        assert "1 cell awaits another host" in rendered
        assert "collect reports and run pf merge" in rendered
        assert rendered.endswith(
            "⚠ Minimized floors · no metadata changes · "
            "1 cell awaits another host · next: collect reports and run pf merge"
        )

    def test_minimize_combines_source_drift_and_host_partial_on_stderr(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def run(self, request: SearchRequest) -> SearchCommandResult:
                return located_search(host_partial_report())

        class ApplyWorkflow:
            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                return apply_result(
                    changed=True,
                    source_drift_path_count=1,
                    source_drift_paths=("src/a.py",),
                    selected=(
                        ApplySelector(sys_platform="linux", platform_machine="x86_64"),
                    ),
                    preserved=(
                        ApplySelector(sys_platform="darwin", platform_machine="arm64"),
                    ),
                )

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        context = make_context(
            check_workflow=NeverCheck(),
            search_workflow=SearchWorkflow(),
            apply_workflow=ApplyWorkflow(),
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=stderr, force_terminal=False, color_system=None),
            ),
        )

        exit_code = create_app(context)(
            ["minimize", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert stdout.getvalue() == ""
        rendered = " ".join(stderr.getvalue().split())
        assert "source-drift override" in rendered
        assert "original constraints retained" in rendered
        assert "1 cell awaits another host" in rendered
        assert rendered.endswith(
            "⚠ Minimized floors with source-drift override · project updated · "
            "1 cell awaits another host · next: collect reports and run pf merge"
        )


class TestResultCardWidths:
    @pytest.mark.parametrize("width", (56, 80, 120))
    def test_apply_card_preserves_public_facts_at_common_widths(
        self,
        tmp_path: Path,
        width: int,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=width,
            ),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                color_system=None,
                width=width,
            ),
            root=tmp_path,
        )

        assert presenter.render_apply(apply_result(changed=False)) == 0

        rendered = stdout.getvalue()
        assert stderr.getvalue() == ""
        assert "demo · applied verified floors" in rendered
        assert "pyproject.toml" in rendered
        assert "Applied floors · no metadata changes" in rendered
        plain = re.sub(r"\x1b]8;[^\x1b]*\x1b\\", "", rendered)
        for line in plain.splitlines():
            assert len(line) <= width

    @pytest.mark.parametrize("width", (56, 80, 120))
    def test_merge_card_preserves_paths_at_common_widths(
        self,
        width: int,
    ) -> None:
        stdout = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=width,
            ),
            stderr=Console(file=StringIO(), force_terminal=False),
        )
        result = MergeCommandResult(
            report=minimal_report(),
            input_paths=(
                "reports/linux/package-floor.json",
                "reports/windows/package-floor.json",
            ),
            output_path="reports/merged/package-floor.json",
        )

        assert presenter.render_merge(result) == 0

        rendered = stdout.getvalue()
        assert "reports/linux/package-floor.json" in rendered
        assert "reports/windows/package-floor.json" in rendered
        assert "reports/merged/package-floor.json" in rendered
        plain = re.sub(r"\x1b]8;[^\x1b]*\x1b\\", "", rendered)
        for line in plain.splitlines():
            assert len(line) <= width


class TestMinimizeCommandCompleteReport:
    def test_minimize_applies_after_a_complete_search(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def __init__(self) -> None:
                self.request: SearchRequest | None = None

            def run(self, request: SearchRequest) -> SearchCommandResult:
                self.request = request
                return located_search(minimal_report())

        class ApplyWorkflow:
            def __init__(self) -> None:
                self.request: ApplyRequest | None = None

            def run(self, request: ApplyRequest) -> ApplyCommandResult:
                self.request = request
                return apply_result(changed=False)

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        search = SearchWorkflow()
        apply = ApplyWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            search_workflow=search,
            apply_workflow=apply,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(
                    file=StringIO(), force_terminal=False, color_system=None
                ),
            ),
        )

        exit_code = create_app(context)(
            ["minimize", "--package", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        expected_root = tmp_path.as_posix()
        assert exit_code == 0
        selector = WorkspacePackage(canonical_name="demo")
        assert search.request == SearchRequest(root=expected_root, selector=selector)
        assert apply.request == ApplyRequest(root=expected_root, selector=selector)
        rendered = " ".join(stdout.getvalue().split())
        assert "demo · minimized verified floors" in rendered
        assert rendered.endswith("✓ Minimized floors · no metadata changes")


class TestDefaultContext:
    def test_default_context_does_not_detect_host_until_verification_graph(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = 0

        def detect_host() -> str:
            nonlocal calls
            calls += 1
            return "x86_64-unknown-linux-gnu"

        monkeypatch.setattr("pf.cli.host_target", detect_host)

        context = build_context()
        assert calls == 0
        context.explain_workflow
        context.merge_workflow
        context.apply_workflow
        assert calls == 0
        context.search_workflow
        assert calls == 1
        context.smoke_workflow
        context.check_workflow
        assert calls == 1
        context.close()

    def test_search_assembly_surfaces_host_target_configuration_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "pf.cli.host_target",
            lambda: (_ for _ in ()).throw(
                ConfigurationError("unsupported host platform: plan9")
            ),
        )
        context = build_context()
        try:
            with pytest.raises(ConfigurationError, match="plan9"):
                context.search_workflow
        finally:
            context.close()

    def test_help_and_version_do_not_construct_online_capabilities(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        def boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("online capability must not be constructed")

        monkeypatch.setattr("pf.cli.UvAdapter", boom)
        monkeypatch.setattr("pf.cli.host_target", boom)
        monkeypatch.setattr("pf.cli.SearchCoordinator", boom)
        monkeypatch.setattr("pf.cli.StaticEvaluator", boom)

        context = build_context()
        app = create_app(context)
        try:
            for tokens in (["--help"], ["--version"]):
                exit_code = app(
                    tokens,
                    exit_on_error=False,
                    result_action="return_value",
                )
                assert int(exit_code or 0) == 0
        finally:
            context.close()

    def test_explain_does_not_construct_uv_or_evaluation_graph(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        def boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("online capability must not be constructed")

        monkeypatch.setattr("pf.cli.UvAdapter", boom)
        monkeypatch.setattr("pf.cli.host_target", boom)
        monkeypatch.setattr("pf.cli.SearchCoordinator", boom)
        monkeypatch.setattr("pf.cli.StaticEvaluator", boom)

        context = build_context()
        context.explain_workflow
        context.diagnose_workflow
        context.merge_workflow
        context.close()

    def test_apply_does_not_construct_evaluators_or_search(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        def boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("evaluation graph must not be constructed")

        monkeypatch.setattr("pf.cli.host_target", boom)
        monkeypatch.setattr("pf.cli.SearchCoordinator", boom)
        monkeypatch.setattr("pf.cli.StaticEvaluator", boom)
        monkeypatch.setattr("pf.cli.RuntimeEvaluator", boom)

        context = build_context()
        context.apply_workflow
        context.close()

    def test_composition_configuration_error_uses_typed_error_surface(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)

        def raise_during_command(context: CliContext) -> object:
            def run() -> None:
                raise ConfigurationError("unsupported host platform: plan9")

            return run

        monkeypatch.setattr("pf.cli.create_app", raise_during_command)

        with pytest.raises(SystemExit) as caught:
            main()

        captured = capsys.readouterr()
        assert caught.value.code == 3
        assert "configuration:" in captured.err
        assert "unsupported host platform: plan9" in captured.err
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out

    def test_main_maps_keyboard_interrupt_to_exit_130(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)

        def interrupt_app(context: CliContext) -> object:
            def run() -> None:
                context.presenter.bind_command("search")
                raise KeyboardInterrupt

            return run

        monkeypatch.setattr("pf.cli.create_app", interrupt_app)

        with pytest.raises(SystemExit) as caught:
            main()

        captured = capsys.readouterr()
        assert caught.value.code == 130
        assert "Interrupted" in captured.err
        assert captured.err.count("Interrupted") == 1
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out

    def test_main_maps_bootstrap_interrupt_to_exit_130(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "pf.cli.build_context",
            lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
        )

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code == 130

    def test_main_swallows_nested_interrupt_while_rendering(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)

        def interrupt_app(context: CliContext) -> object:
            def run() -> None:
                raise KeyboardInterrupt

            def boom() -> int:
                raise KeyboardInterrupt

            monkeypatch.setattr(context.presenter, "render_interrupt", boom)
            return run

        monkeypatch.setattr("pf.cli.create_app", interrupt_app)

        with pytest.raises(SystemExit) as caught:
            main()

        captured = capsys.readouterr()
        assert caught.value.code == 130
        assert "Traceback" not in captured.err

    def test_close_completes_after_nested_keyboard_interrupt(self) -> None:
        events: list[str] = []

        class Resource:
            def __init__(self, name: str) -> None:
                self.name = name
                self.calls = 0

            def close(self, *_args: object, **_kwargs: object) -> None:
                self.calls += 1
                events.append(f"{self.name}:{self.calls}")
                if self.calls == 1:
                    raise KeyboardInterrupt

        context = CliContext(
            presenter=cast(TerminalPresenter, Resource("presenter")),
            run_logs=cast(RunLogStore, Resource("logs")),
        )
        context.close()
        context.close()

        assert events == ["presenter:1", "presenter:2", "logs:1", "logs:2"]

    def test_assembled_search_interrupt_stops_runner_without_children(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        context = build_context()
        context.search_workflow
        context.interrupt_processes()
        context.close()

    def test_build_context_cleans_created_resources_when_presenter_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []

        class Logs:
            def __init__(self, *, root: Path) -> None:
                return

            def close(self) -> None:
                events.append("logs")

        class Presenter:
            def __init__(self, *, logs: object, root: Path) -> None:
                raise RuntimeError("presenter failed")

            def close(self, *, abandon_pending: bool = False) -> None:
                events.append(f"presenter:{abandon_pending}")

        monkeypatch.setattr("pf.cli.RunLogStore", Logs)
        monkeypatch.setattr("pf.cli.TerminalPresenter", Presenter)

        with pytest.raises(RuntimeError, match="presenter failed"):
            build_context()

        assert events == ["logs"]

    def test_build_context_closes_presenter_when_context_construction_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[str] = []

        class Logs:
            def __init__(self, *, root: Path) -> None:
                return

            def close(self) -> None:
                events.append("logs")

        class Presenter:
            def __init__(self, *, logs: object, root: Path) -> None:
                return

            def close(self, *, abandon_pending: bool = False) -> None:
                events.append(f"presenter:{abandon_pending}")

        monkeypatch.setattr("pf.cli.RunLogStore", Logs)
        monkeypatch.setattr("pf.cli.TerminalPresenter", Presenter)
        monkeypatch.setattr(
            "pf.cli.CliContext",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("context failed")),
        )

        with pytest.raises(RuntimeError, match="context failed"):
            build_context()

        assert events == ["presenter:True", "logs"]

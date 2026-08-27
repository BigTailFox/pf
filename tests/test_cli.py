from __future__ import annotations

import os
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
)
from pf.errors import NoApplicableFloorError, PfError
from pf.report import PackageReportBuilder, ValidatedReport
from pf.runlog import RunLogStore
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    DiagnoseRequest,
    EffectiveConfig,
    MergeRequest,
    ReportRequest,
    SearchRequest,
    SmokeRequest,
)
from pf.schemas.evaluation import CheckPass, SmokePass, StatusEvent
from pf.schemas.project import (
    PackagePlan,
    SourcePlan,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)
from pf.schemas.report import (
    ProjectEditResult,
)
from pf.terminal import TerminalPresenter


class NeverCheck:
    def run(self, request: CheckRequest) -> CheckPass:
        raise AssertionError("check should not run")


class NeverCalledWorkflow:
    def run(self, request: object) -> NoReturn:
        raise AssertionError("unselected workflow should not run")


class NoOpRunLogs:
    def close(self) -> None:
        return


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
    return CliContext(
        check_workflow=(
            check_workflow if check_workflow is not None else NeverCalledWorkflow()
        ),
        smoke_workflow=(
            smoke_workflow if smoke_workflow is not None else NeverCalledWorkflow()
        ),
        search_workflow=(
            search_workflow if search_workflow is not None else NeverCalledWorkflow()
        ),
        explain_workflow=(
            explain_workflow if explain_workflow is not None else NeverCalledWorkflow()
        ),
        diagnose_workflow=(
            diagnose_workflow
            if diagnose_workflow is not None
            else NeverCalledWorkflow()
        ),
        merge_workflow=(
            merge_workflow if merge_workflow is not None else NeverCalledWorkflow()
        ),
        apply_workflow=(
            apply_workflow if apply_workflow is not None else NeverCalledWorkflow()
        ),
        presenter=presenter,
        run_logs=(
            run_logs if run_logs is not None else cast(RunLogStore, NoOpRunLogs())
        ),
    )


def minimal_report() -> ValidatedReport:
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(),
        source_plan=SourcePlan(identities=()),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(()),
        entries=(),
    )
    return PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(),
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
        return_code = 3
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

        assert result.returncode == 3
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

        assert result.returncode == 3
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

        assert result.returncode == 3
        assert result.stdout == ""
        assert "Error:" in result.stderr
        assert "Usage:" in result.stderr
        assert "Try 'pf check --help'" in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_illegal_jobs_is_an_invocation_error(self) -> None:
        result = invoke_app("check", "--jobs", "nope")

        assert result.returncode == 3
        assert "Error:" in result.stderr
        assert "positive integer" in result.stderr
        assert "Usage: pf check [OPTIONS] [PACKAGE]" in result.stderr
        assert "Try 'pf check --help'" in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_illegal_duration_restates_accepted_format(self) -> None:
        result = invoke_app("search", "--max-duration", "10minutes")

        assert result.returncode == 3
        assert "Error:" in result.stderr
        assert "30s" in result.stderr
        assert "10m" in result.stderr
        assert "2h" in result.stderr
        assert "none" in result.stderr
        assert "Usage:" in result.stderr
        assert "Try 'pf search --help'" in result.stderr
        assert "Traceback" not in result.stderr

    def test_unknown_package_is_an_invocation_error(self, tmp_path: Path) -> None:
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
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, "-m", "pf", "check", "other"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 3
        assert "Error:" in result.stderr
        assert "unknown package selection: other" in result.stderr
        assert "Known packages: demo" in result.stderr
        assert "Usage:" in result.stderr
        assert "Try 'pf check --help'" in result.stderr
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
            def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]:
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


class TestCommandDispatch:
    @pytest.mark.parametrize(
        ("command", "expected_fragments"),
        (
            ("smoke", ("--jobs", "auto")),
            ("check", ("--jobs", "auto")),
            ("search", ("--jobs", "auto", "--max-duration", "none")),
            ("explain", ()),
            ("apply", ()),
            ("minimize", ()),
            (
                "diagnose",
                ("recorded rejection or indeterminate result", "--failure"),
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
        assert f"Usage: pf {command} [OPTIONS] [PACKAGE]" in result.stdout
        assert "[ARGS]" not in result.stdout
        assert "--package" not in result.stdout
        assert all(fragment in result.stdout for fragment in expected_fragments)

    def test_check_command_normalizes_jobs_before_workflow(
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
            ["check", "demo", "--jobs", "2"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == CheckRequest(
            root=tmp_path.as_posix(),
            package="demo",
            jobs=2,
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
            ["check", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request is not None
        assert workflow.request.package == "demo"
        assert workflow.request.root == tmp_path.as_posix()
        assert workflow.request.jobs == "auto"
        assert stdout.getvalue() == "✓ Check passed · 0 cells\n"
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
            ["smoke", "demo", "--jobs", "2"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == SmokeRequest(
            root=tmp_path.as_posix(),
            package="demo",
            jobs=2,
        )
        assert stdout.getvalue() == "✓ Smoke passed · 0 cells\n"
        assert stderr.getvalue() == ""

    def test_search_command_normalizes_jobs_and_duration_before_workflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def __init__(self) -> None:
                self.request: SearchRequest | None = None

            def run(self, request: SearchRequest):
                self.request = request
                return ()

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
            ["search", "demo", "--jobs", "2", "--max-duration", "1m"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == SearchRequest(
            root=tmp_path.as_posix(),
            package="demo",
            jobs=2,
            max_duration_seconds=60,
        )
        assert stdout.getvalue() == "✓ Search complete · 0 reports\n"
        assert stderr.getvalue() == ""

    def test_explain_command_only_requests_existing_reports(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ExplainWorkflow:
            def __init__(self) -> None:
                self.request: ReportRequest | None = None

            def run(self, request: ReportRequest):
                self.request = request
                return ()

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
            ["explain", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == ReportRequest(
            root=tmp_path.as_posix(), package="demo"
        )
        assert stdout.getvalue() == "explained 0 reports\n"

    def test_diagnose_command_only_requests_recorded_failures(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class DiagnoseWorkflow:
            def __init__(self) -> None:
                self.request: DiagnoseRequest | None = None

            def run(self, request: DiagnoseRequest):
                self.request = request
                return ()

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        workflow = DiagnoseWorkflow()
        context = make_context(
            check_workflow=NeverCheck(),
            diagnose_workflow=workflow,
            presenter=TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(
                    file=StringIO(), force_terminal=False, color_system=None
                ),
            ),
        )

        exit_code = create_app(context)(
            ["diagnose", "demo", "--failure", "failure-a"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == DiagnoseRequest(
            root=tmp_path.as_posix(),
            package="demo",
            failure_id="failure-a",
        )
        assert stdout.getvalue() == "diagnosed 0 failures\n"

    def test_merge_command_passes_explicit_inputs_and_output_to_workflow(
        self,
        tmp_path: Path,
    ) -> None:
        class MergeWorkflow:
            def __init__(self) -> None:
                self.request: MergeRequest | None = None

            def run(self, request: MergeRequest) -> ValidatedReport:
                self.request = request
                return minimal_report()

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
        assert stdout.getvalue() == f"✓ Merged 1 report · {output.as_posix()}\n"

    def test_apply_command_uses_report_only_workflow(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ApplyWorkflow:
            def __init__(self) -> None:
                self.request: ApplyRequest | None = None

            def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]:
                self.request = request
                return (
                    ProjectEditResult(
                        changed=True,
                        pyproject_path="pyproject.toml",
                        recovery_log_path=".pf/apply-recovery.json",
                    ),
                )

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
            ["apply", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 0
        assert workflow.request == ApplyRequest(
            root=tmp_path.as_posix(), package="demo"
        )
        assert (
            stdout.getvalue()
            == "✓ Applied floors · 1 project updated · pyproject.toml\n"
        )

    def test_cli_context_requires_the_complete_object_graph(self) -> None:
        with pytest.raises(TypeError, match="required positional argument"):
            CliContext(  # ty: ignore[missing-argument]
                check_workflow=NeverCheck(),
                presenter=TerminalPresenter(
                    stdout=Console(
                        file=StringIO(), force_terminal=False, color_system=None
                    ),
                    stderr=Console(
                        file=StringIO(), force_terminal=False, color_system=None
                    ),
                ),
            )

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
            check_workflow=never,
            smoke_workflow=never,
            search_workflow=never,
            explain_workflow=never,
            diagnose_workflow=never,
            merge_workflow=never,
            apply_workflow=never,
            presenter=cast(TerminalPresenter, Presenter()),
            run_logs=cast(RunLogStore, Logs()),
        )

        with context:
            pass
        context.close()

        assert events == ["presenter", "logs"]


class TestMinimizeCommand:
    def test_minimize_does_not_apply_when_search_report_is_incomplete(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def run(self, request: SearchRequest) -> tuple[ValidatedReport, ...]:
                return (minimal_report(),)

        class ApplyWorkflow:
            def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]:
                raise AssertionError("incomplete search must not enter apply")

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
            ["minimize", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "Minimize stopped before apply" in stderr.getvalue()
        assert "search completed" not in stderr.getvalue()
        assert "Search complete" not in stdout.getvalue()

    def test_minimize_applies_after_a_complete_search(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def __init__(self) -> None:
                self.request: SearchRequest | None = None

            def run(self, request: SearchRequest) -> tuple[ValidatedReport, ...]:
                self.request = request
                return ()

        class ApplyWorkflow:
            def __init__(self) -> None:
                self.request: ApplyRequest | None = None

            def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]:
                self.request = request
                return (
                    ProjectEditResult(
                        changed=False,
                        pyproject_path="pyproject.toml",
                        recovery_log_path=".pf/apply-recovery.json",
                    ),
                )

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
            ["minimize", "demo"],
            exit_on_error=False,
            result_action="return_value",
        )

        expected_root = tmp_path.as_posix()
        assert exit_code == 0
        assert search.request == SearchRequest(root=expected_root, package="demo")
        assert apply.request == ApplyRequest(root=expected_root, package="demo")
        assert stdout.getvalue() == "✓ Minimized floors · no metadata changes\n"


class TestDefaultContext:
    def test_default_context_assembles_every_v1_workflow(self) -> None:
        context = build_context()

        assert context.check_workflow is not None
        assert context.smoke_workflow is not None
        assert context.search_workflow is not None
        assert context.apply_workflow is not None
        assert context.explain_workflow is not None
        assert context.diagnose_workflow is not None
        assert context.merge_workflow is not None
        context.close()

    def test_build_context_cleans_created_resources_when_assembly_fails(
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
            "pf.cli.RegistryAccess.from_environment",
            lambda environment: (_ for _ in ()).throw(RuntimeError("assembly failed")),
        )

        with pytest.raises(RuntimeError, match="assembly failed"):
            build_context()

        assert events == ["presenter:True", "logs"]

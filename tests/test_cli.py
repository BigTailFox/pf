from __future__ import annotations

import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from pf.cli import CliContext, build_context, create_app
from pf.errors import ConfigurationError, NoApplicableFloorError, PfError
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    DiagnoseRequest,
    MergeRequest,
    ReportRequest,
    SearchRequest,
    SmokeRequest,
)
from pf.schemas.evaluation import CheckPass, SmokePass, StatusEvent
from pf.schemas.project import SourceSnapshotIdentity
from pf.schemas.report import (
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectEditResult,
    report_generation_id,
)
from pf.terminal import TerminalPresenter


class NeverCheck:
    def run(self, request: CheckRequest) -> CheckPass:
        raise AssertionError("check should not run")


def minimal_report() -> PackageFloorReportV1:
    generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
    package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
    snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
    return PackageFloorReportV1(
        report_generation_id=report_generation_id(
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity="policy",
            requirement_declarations=(),
            target_cells=(),
        ),
        generator=generator,
        package=package,
        source_snapshot=snapshot,
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        cell_results=(),
        projection_evidence=(),
        result=IncompleteReportResult(reasons=("MISSING_CELL",)),
    )


@pytest.fixture(scope="module")
def module_help() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pf", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )


class TestCliInterface:
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

    def test_merge_help_usage_names_reports_and_hides_report_option(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pf", "merge", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert "Usage: pf merge REPORT [REPORT ...] --output PATH" in result.stdout
        assert "[ARGS]" not in result.stdout
        assert "--report" not in result.stdout
        assert "--package" not in result.stdout

    def test_merge_without_reports_is_a_usage_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pf", "merge", "--output", "merged.json"],
            check=False,
            capture_output=True,
            text=True,
        )

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
        script = subprocess.run(
            ["uv", "run", "--no-sync", "pf", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert module_help.returncode == 0, module_help.stderr
        assert script.returncode == 0, script.stderr
        assert module_help.stdout == script.stdout

    def test_unknown_option_is_an_invocation_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pf", "check", "--not-a-flag"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 3
        assert result.stdout == ""
        assert "Error:" in result.stderr
        assert "Usage:" in result.stderr
        assert "Try 'pf check --help'" in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_illegal_jobs_is_an_invocation_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pf", "check", "--jobs", "nope"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 3
        assert "Error:" in result.stderr
        assert "positive integer" in result.stderr
        assert "Usage: pf check [OPTIONS] [PACKAGE]" in result.stderr
        assert "Try 'pf check --help'" in result.stderr
        assert "Traceback" not in result.stderr
        assert "\x1b" not in result.stderr

    def test_illegal_duration_restates_accepted_format(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pf", "search", "--max-duration", "10minutes"],
            check=False,
            capture_output=True,
            text=True,
        )

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

        context = CliContext(
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
        result = subprocess.run(
            [sys.executable, "-m", "pf", command, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

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
        context = CliContext(
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
        context = CliContext(
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
        context = CliContext(
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
        context = CliContext(
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
        context = CliContext(
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
        context = CliContext(
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

            def run(self, request: MergeRequest) -> PackageFloorReportV1:
                self.request = request
                return minimal_report()

        stdout = StringIO()
        workflow = MergeWorkflow()
        context = CliContext(
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
        context = CliContext(
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

    @pytest.mark.parametrize(
        "argv",
        (
            ("smoke",),
            ("search",),
            ("apply",),
            ("minimize",),
            ("explain",),
            ("diagnose",),
            ("merge", "report.json", "--output", "merged.json"),
        ),
    )
    def test_commands_reject_an_unassembled_workflow(
        self, argv: tuple[str, ...]
    ) -> None:
        context = CliContext(
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

        with pytest.raises(ConfigurationError, match="workflow.*not assembled"):
            create_app(context)(
                list(argv),
                exit_on_error=False,
                result_action="return_value",
            )


class TestMinimizeCommand:
    def test_minimize_does_not_apply_when_search_report_is_incomplete(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class SearchWorkflow:
            def run(self, request: SearchRequest) -> tuple[PackageFloorReportV1, ...]:
                return (minimal_report(),)

        class ApplyWorkflow:
            def run(self, request: ApplyRequest) -> tuple[ProjectEditResult, ...]:
                raise AssertionError("incomplete search must not enter apply")

        monkeypatch.chdir(tmp_path)
        stdout = StringIO()
        stderr = StringIO()
        context = CliContext(
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

            def run(self, request: SearchRequest) -> tuple[PackageFloorReportV1, ...]:
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
        context = CliContext(
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

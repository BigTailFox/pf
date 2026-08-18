from __future__ import annotations

import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from pf.cli import CliContext, build_context, create_app
from pf.errors import ConfigurationError
from pf.schemas.config import (
    ApplyRequest,
    CheckRequest,
    MergeRequest,
    ReportRequest,
    SearchRequest,
)
from pf.schemas.evaluation import CheckPass
from pf.schemas.project import SourceSnapshotIdentity
from pf.schemas.report import (
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectEditResult,
)
from pf.terminal import TerminalPresenter


class NeverCheck:
    def run(self, request: CheckRequest) -> CheckPass:
        raise AssertionError("check should not run")


def minimal_report() -> PackageFloorReportV1:
    return PackageFloorReportV1(
        generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
        package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
        source_snapshot=SourceSnapshotIdentity(digest="snapshot", entries=()),
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        cell_results=(),
        projection_evidence=(),
        result=IncompleteReportResult(reasons=("MISSING_CELL",)),
    )


def test_module_help_lists_every_v1_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pf", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    for command in ("check", "search", "apply", "minimize", "explain", "merge"):
        assert command in result.stdout


def test_search_help_documents_scheduling_options_and_defaults() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pf", "search", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--jobs" in result.stdout
    assert "auto" in result.stdout
    assert "--max-duration" in result.stdout


def test_check_command_builds_a_request_and_renders_the_workflow_result(
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
    assert stdout.getvalue() == "check passed (0 cells)\n"
    assert stderr.getvalue() == ""


def test_search_command_normalizes_jobs_and_duration_before_workflow(
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
    assert stdout.getvalue() == "search completed (0 reports)\n"
    assert stderr.getvalue() == ""


def test_explain_command_only_requests_existing_reports(
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
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        ),
    )

    exit_code = create_app(context)(
        ["explain", "demo"],
        exit_on_error=False,
        result_action="return_value",
    )

    assert exit_code == 0
    assert workflow.request == ReportRequest(root=tmp_path.as_posix(), package="demo")
    assert stdout.getvalue() == "explained 0 reports\n"


def test_merge_command_passes_explicit_inputs_and_output_to_workflow(
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
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
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
    assert stdout.getvalue() == f"merged demo report -> {output.as_posix()}\n"


def test_apply_command_uses_report_only_workflow(
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
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        ),
    )

    exit_code = create_app(context)(
        ["apply", "demo"],
        exit_on_error=False,
        result_action="return_value",
    )

    assert exit_code == 0
    assert workflow.request == ApplyRequest(root=tmp_path.as_posix(), package="demo")
    assert stdout.getvalue() == "apply completed (1 changed)\n"


def test_minimize_does_not_apply_when_search_report_is_incomplete(
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
    context = CliContext(
        check_workflow=NeverCheck(),
        search_workflow=SearchWorkflow(),
        apply_workflow=ApplyWorkflow(),
        presenter=TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        ),
    )

    exit_code = create_app(context)(
        ["minimize", "demo"],
        exit_on_error=False,
        result_action="return_value",
    )

    assert exit_code == 2
    assert stdout.getvalue() == "search completed (1 reports)\n"


def test_minimize_applies_after_a_complete_search(
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
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
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
    assert stdout.getvalue() == (
        "search completed (0 reports)\napply completed (0 changed)\n"
    )


@pytest.mark.parametrize(
    "argv",
    (
        ("search",),
        ("apply",),
        ("minimize",),
        ("explain",),
        ("merge", "--output", "merged.json"),
    ),
)
def test_commands_reject_an_unassembled_workflow(argv: tuple[str, ...]) -> None:
    context = CliContext(
        check_workflow=NeverCheck(),
        presenter=TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=False, color_system=None),
            stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
        ),
    )

    with pytest.raises(ConfigurationError, match="workflow.*not assembled"):
        create_app(context)(
            list(argv),
            exit_on_error=False,
            result_action="return_value",
        )


def test_default_context_assembles_every_v1_workflow() -> None:
    context = build_context()

    assert context.check_workflow is not None
    assert context.search_workflow is not None
    assert context.apply_workflow is not None
    assert context.explain_workflow is not None
    assert context.merge_workflow is not None

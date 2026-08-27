from __future__ import annotations

from dataclasses import replace
import re
import sys
import time
from io import StringIO
from pathlib import Path
from typing import Literal

import pytest

from conftest import empty_harness_baseline
from rich.console import Console

from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.failure import FailurePolicy
from pf.runlog import RunLogStore
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellContextEvent,
    BaselineDetailIdentity,
    CellDetailIdentity,
    CellFailed,
    CellResultDetail,
    CellFailureScope,
    CellMatrixEvent,
    CellStageEvent,
    CellSucceeded,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    DeclarationDetailIdentity,
    DiagnosticClassification,
    PassEvaluation,
    ProcessEvent,
    ProcessResult,
    ProcessSpec,
    PytestFailureCase,
    PytestFailureDetail,
    FailureDetail,
    FailureCause,
    FailureRecord,
    HighestVersionPass,
    SearchFailureEvent,
    SearchProbeDetailIdentity,
    SmokeBaselineRejection,
    SmokePass,
    SmokeIndeterminate,
    StaticBaseline,
    StaticIssueDetail,
    StaticRegressionEvaluation,
    StaticUnchangedEvaluation,
    StatusEvent,
    StageProgress,
    TestFail,
    TestFailEvaluation,
    TestPass,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    VerificationRole,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    PackagePlan,
    Proposal,
    RequirementDeclaration,
    SourceIdentity,
    SourcePlan,
    SourceSnapshotIdentity,
    VersionPin,
    source_snapshot_digest,
)
from pf.schemas.config import EffectiveConfig
from pf.report import PackageReportBuilder, ValidatedReport
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CompleteReportResult,
    CoordinateFailure,
    IncompleteReportResult,
    ProjectionEvidence,
    ProbeObservation,
    ProbeRejection,
    failure_records_for_result,
)
from pf.terminal import PF_THEME, TerminalPresenter
from pf.static_transition import static_fingerprint

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SGR = re.compile(r"\x1b\[([0-9;]*)m")


def visible(text: str) -> str:
    return _ANSI.sub("", text)


def sgr_codes(text: str) -> set[str]:
    return {
        code
        for parameters in _SGR.findall(text)
        for code in parameters.split(";")
        if code
    }


class TTYBuffer(StringIO):
    def isatty(self) -> bool:
        return True


def process_result(
    *,
    exit_code: int | None = 1,
    stderr: str = "",
    stdout: str = "",
    timed_out: bool = False,
    start_error: str | None = None,
) -> ProcessResult:
    if start_error is not None:
        exit_code = None
        signal = None
    elif exit_code is None:
        signal = 9
    else:
        signal = None
    return ProcessResult(
        exit_code=exit_code,
        signal=signal,
        duration_seconds=1,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        start_error=start_error,
    )


def general_classifications(
    *diagnostics: TyDiagnostic,
) -> tuple[DiagnosticClassification, ...]:
    return tuple(
        DiagnosticClassification(
            diagnostic_identity=diagnostic.identity,
            classification="general",
            reason_code="test-fixture",
        )
        for diagnostic in diagnostics
    )


def completed_event(
    cell: Cell,
    *,
    status: str,
    completed: int = 1,
    total: int = 1,
    phase: str = "complete",
    detail: CellResultDetail | None = None,
    process: ProcessResult | None = None,
    failure: FailureRecord | None = None,
    role: VerificationRole | None = None,
    stage: str | None = None,
    diagnose_available: bool = True,
) -> CellCompletedEvent:
    if status in {"PASS", "SUCCESS"}:
        outcome = CellSucceeded(
            status=status,
            phase=phase,
        )
    else:
        outcome = CellFailed(
            status=status,
            phase=stage or phase,
            detail=detail,
            detail_failure_id=(
                failure.failure_id
                if detail is not None and failure is not None
                else None
            ),
            process=process,
            failures=() if failure is None else (failure,),
            verification_role=role,
        )
    return CellCompletedEvent(
        cell=cell,
        completed=completed,
        total=total,
        outcome=outcome,
        diagnose_available=diagnose_available,
    )


def recorded_failure(
    *,
    cause: FailureCause,
    stage: str,
    process: ProcessResult,
) -> FailureRecord:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    return FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause=cause,
        stage=stage,
        process=process,
    )


def presenter() -> tuple[TerminalPresenter, StringIO, StringIO]:
    stdout = StringIO()
    stderr = StringIO()
    return (
        TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
        ),
        stdout,
        stderr,
    )


def incomplete_report(
    *reasons: str,
    projections: tuple[ProjectionEvidence, ...] = (),
    cell_results: tuple[CellResult, ...] = (),
    declarations: tuple[RequirementDeclaration, ...] = (),
) -> ValidatedReport:
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
    target_cells = tuple(result.cell for result in cell_results)
    base = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(),
    )
    return replace(
        base,
        requirement_declarations=declarations,
        target_cells=target_cells,
        cell_results=cell_results,
        projection_evidence=projections,
        result=IncompleteReportResult(status="incomplete", reasons=reasons),
        failure_records=tuple(
            failure
            for result in cell_results
            for failure in failure_records_for_result(result)
        ),
    )


def requirement_declaration(
    declaration_id: str,
    *,
    name: str,
    raw: str,
) -> RequirementDeclaration:
    return RequirementDeclaration(
        declaration_id=declaration_id,
        package="demo",
        location="base",
        name=name,
        source=SourceIdentity(kind="registry"),
        pyproject_path="pyproject.toml",
        raw=raw,
        kind="searchable",
        managed=True,
    )


def attempt_for(
    cell: Cell,
    *,
    resolution: Literal["highest", "lowest-direct", "exact-vector"] = "highest",
    vector: tuple[VersionPin, ...] | None = None,
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=vector,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )


def cell_indeterminate(
    cell: Cell,
    *,
    cause: FailureCause,
    stage: str,
    process: ProcessResult | None = None,
) -> CellIndeterminate:
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package=cell.package,
            cell=cell,
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause=cause,
        stage=stage,
        process=process,
        detail=(
            None
            if process is not None
            else FailureDetail(code="terminal", message="index unavailable")
        ),
    )
    return CellIndeterminate(
        cell=cell,
        phase=stage,
        failure_id=failure.failure_id,
        failure_records=(failure,),
    )



def tty_presenter() -> TerminalPresenter:
    return TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=StringIO(), force_terminal=True),
    )


class TestErrorRendering:
    def test_configuration_error_uses_stderr_without_terminal_escape_codes(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
        )

        exit_code = presenter.render_error(ConfigurationError("unknown key: surprise"))

        assert exit_code == 3
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == "✗ configuration: unknown key: surprise\n"
        assert "\x1b[" not in stderr.getvalue()

    def test_render_error_lists_known_package_candidates(self) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_error(
            ConfigurationError(
                "unknown package selection: other",
                candidates=("alpha", "beta", "demo"),
            )
        )

        assert exit_code == 3
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "Error: unknown package selection: other\n"
            "Known packages: alpha, beta, demo\n"
            "Usage: pf COMMAND\n"
            "Try 'pf --help' for more information.\n"
        )

    def test_render_error_truncates_known_package_candidates_after_ten(self) -> None:
        terminal, _, stderr = presenter()
        names = tuple(f"pkg{index:02d}" for index in range(12))

        terminal.render_error(
            ConfigurationError("unknown package selection: other", candidates=names)
        )

        assert stderr.getvalue() == (
            "Error: unknown package selection: other\n"
            "Known packages: pkg00, pkg01, pkg02, pkg03, pkg04, pkg05, pkg06, "
            "pkg07, pkg08, pkg09, ... and 2 more\n"
            "Usage: pf COMMAND\n"
            "Try 'pf --help' for more information.\n"
        )

    def test_infrastructure_error_prints_the_captured_detail(self) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_error(
            InfrastructureError(
                "uv could not list available Python versions",
                detail="uv: failed to execute 'uv python list'",
            )
        )

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ infrastructure: uv could not list available Python versions\n"
            "uv: failed to execute 'uv python list'\n"
        )

    def test_render_error_does_not_claim_in_progress_apply_succeeded(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=terminal, force_terminal=True),
        )

        presenter.consume(StatusEvent(message="applying floors"))
        exit_code = presenter.render_error(
            NoApplicableFloorError("cannot apply an incomplete floor report")
        )

        output = visible(terminal.getvalue())
        assert "applied floors" not in output
        assert "✓" not in output
        assert exit_code == 2
        assert (
            "✗ no-applicable-floor: cannot apply an incomplete floor report" in output
        )

    def test_render_error_keeps_completed_steps_and_drops_in_progress(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=terminal, force_terminal=True),
        )

        presenter.consume(StatusEvent(message="loading project"))
        presenter.consume(StatusEvent(message="building snapshot"))
        presenter.render_error(
            ConfigurationError("project source snapshot has drifted since search")
        )

        output = visible(terminal.getvalue())
        assert "✓ loaded project" in output
        assert "built snapshot" not in output
        assert (
            "✗ configuration: project source snapshot has drifted since search"
            in output
        )


class TestProgressRendering:
    @pytest.mark.parametrize(
        ("terminal_columns", "expected_width"),
        ((80, 80), (200, 120)),
    )
    def test_default_cli_canvas_uses_terminal_width_up_to_120_columns(
        self,
        terminal_columns: int,
        expected_width: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setenv("COLUMNS", str(terminal_columns))
        monkeypatch.setenv("LINES", "40")
        monkeypatch.setattr(sys, "stdout", TTYBuffer())
        stderr = TTYBuffer()
        monkeypatch.setattr(sys, "stderr", stderr)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        terminal = TerminalPresenter()

        terminal.consume(StatusEvent(message="smoke testing"))
        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.close()

        rendered_border = next(
            line
            for line in visible(stderr.getvalue()).splitlines()
            if "╭" in line
        )
        border = rendered_border[rendered_border.index("╭") :]
        assert len(border) == expected_width

    def test_live_view_only_renders_started_cells_and_footer_counts_work(
        self,
    ) -> None:
        cells = tuple(
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor=python_minor,
                extra_surface=(),
            )
            for python_minor in ("3.9", "3.10", "3.11", "3.12")
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(StatusEvent(message="smoke testing"))
        terminal.consume(CellMatrixEvent(cells=cells))
        terminal.consume(
            CellContextEvent(cell=cells[0], detail=BaselineDetailIdentity())
        )
        terminal.consume(
            CellContextEvent(cell=cells[1], detail=BaselineDetailIdentity())
        )

        lines = visible(stderr.getvalue()).splitlines()
        footer_at = max(
            index for index, line in enumerate(lines) if "smoke testing" in line
        )
        previous_footer = max(
            (
                index
                for index, line in enumerate(lines[:footer_at])
                if "smoke testing" in line
            ),
            default=-1,
        )
        frame = "\n".join(lines[previous_footer + 1 : footer_at + 1])
        footer = lines[footer_at]
        terminal.close()

        assert "[py3.9]" in frame
        assert "[py3.10]" in frame
        assert "[py3.11]" not in frame
        assert "[py3.12]" not in frame
        assert "2 running · 2 left" in footer
        assert "□" not in footer and "■" not in footer
        assert "0/4" not in footer
        assert footer.rstrip().endswith("0:00:00")

    def test_footer_excludes_completed_cells_from_running_and_left(self) -> None:
        first = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        second = first.model_copy(update={"python_minor": "3.12"})
        third = first.model_copy(update={"python_minor": "3.13"})
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(StatusEvent(message="smoke testing"))
        terminal.consume(CellMatrixEvent(cells=(first, second, third)))
        terminal.consume(
            CellContextEvent(cell=first, detail=BaselineDetailIdentity())
        )
        terminal.consume(
            CellContextEvent(cell=second, detail=BaselineDetailIdentity())
        )
        terminal.consume(
            completed_event(first, status="SUCCESS", completed=1, total=3)
        )
        terminal.consume(
            CellContextEvent(cell=third, detail=BaselineDetailIdentity())
        )
        footer = next(
            line
            for line in reversed(visible(stderr.getvalue()).splitlines())
            if "smoke testing" in line
        )
        terminal.close()

        assert "2 running · 0 left" in footer
        assert "1/3" not in footer

    def test_narrow_footer_keeps_work_counts_and_total_elapsed(self) -> None:
        cells = tuple(
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor=f"3.{index}",
                extra_surface=(),
            )
            for index in range(40)
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=56, height=200),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=56,
                height=200,
                theme=PF_THEME,
            ),
        )

        terminal.consume(StatusEvent(message="smoke testing"))
        terminal.consume(CellMatrixEvent(cells=cells))
        terminal.consume(
            CellContextEvent(cell=cells[0], detail=BaselineDetailIdentity())
        )
        footer = next(
            line
            for line in reversed(visible(stderr.getvalue()).splitlines())
            if "smoke testing" in line
        )
        terminal.close()

        assert "1 running · 39 left" in footer
        assert footer.rstrip().endswith("0:00:00")
        assert "□" not in footer and "■" not in footer

    @pytest.mark.parametrize("width", (56, 80, 120))
    def test_tty_failure_cards_wrap_at_rich_console_width(
        self,
        width: int,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        process = process_result(exit_code=1)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process,
        )
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=width),
            stderr=Console(file=stderr, force_terminal=True, width=width),
        )

        terminal.consume(
            completed_event(
                cell,
                status="REJECTED",
                failure=failure,
                detail=PytestFailureDetail(
                    first=PytestFailureCase(
                        nodeid="tests/test_search_workflow.py::test_candidate_failure",
                        phase="call",
                    ),
                    total=2,
                ),
                stage="test",
            )
        )

        plain = visible(stderr.getvalue())
        collapsed = " ".join(
            "".join(" " if char in "│╭╮╰╯─" else char for char in plain).split()
        )
        border = next(line for line in plain.splitlines() if line.startswith("╭"))
        assert len(border) == width
        compact = "".join(
            char
            for char in plain
            if not char.isspace() and char not in "│╭╮╰╯─"
        )
        assert "tests/test_search_workflow.py::test_candidate_failure" in compact
        assert "... and 1 more" in collapsed

    @pytest.mark.parametrize(
        ("status", "result_color"),
        (
            ("SUCCESS", "32"),
            ("REJECTED", "31"),
            ("NO_PASS_IN_SEARCH_SPACE", "33"),
            ("INDETERMINATE", "33"),
        ),
    )
    def test_tty_cell_card_border_keeps_result_color_and_is_dim(
        self,
        status: str,
        result_color: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        terminal.consume(completed_event(cell, status=status))
        terminal.close()

        raw = stderr.getvalue()
        border_at = raw.rindex("╭")
        style_at = raw.rfind("\x1b[", 0, border_at)
        border_codes = sgr_codes(raw[style_at:border_at])
        assert result_color in border_codes
        assert "2" in border_codes
        assert "1" not in border_codes

    def test_progress_is_stable_lines_off_tty_and_dynamic_on_tty(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        first = completed_event(
            cell,
            completed=1,
            total=2,
            status="SUCCESS",
        )
        last = first.model_copy(update={"completed": 2})
        plain = StringIO()
        plain_presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=False, color_system=None),
            stderr=Console(file=plain, force_terminal=False, color_system=None),
        )
        terminal = StringIO()
        tty_presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=terminal, force_terminal=True),
        )

        plain_presenter.consume(first)
        tty_presenter.consume(first)
        tty_presenter.consume(last)
        tty_presenter.close()

        assert plain.getvalue() == "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00" in visible(
            terminal.getvalue()
        )
        assert "╭" in visible(terminal.getvalue())

    def test_completed_cell_log_omits_unstructured_process_output(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            CellStageEvent(cell=cell, stage="static check")
        )
        terminal.consume(
            completed_event(
                cell,
                status="STATIC_REGRESSION",
                process=process_result(
                    exit_code=1,
                    stderr="error: Unresolved import 'missing'",
                ),
                stage="ty",
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
            "failed at [static checking]\n"
        )

    def test_completed_cell_with_failure_record_prints_title_and_diagnose(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="lowest-direct")
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(
                exit_code=1, stderr="error: Unresolved import 'missing'"
            ),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            completed_event(
                cell,
                status="REJECTED",
                failure=failure,
                role="declaration",
                stage="test",
            )
        )

        output = stderr.getvalue()
        assert stdout.getvalue() == ""
        assert "failed at [testing]" in output
        assert "The full test command failed for this version combination." in output
        assert "The declared lower bounds did not pass" not in output
        assert f"pf diagnose demo --failure {failure.failure_id}" in output
        assert "STATIC_REGRESSION" not in output
        assert "REJECTED" not in output

    def test_completed_cell_shows_first_causal_static_issue_and_count(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="lowest-direct")
        process = process_result(exit_code=0)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="RUNTIME_INTERFACE_MISSING",
            stage="witness",
            process=process,
        )
        issue = TyDiagnostic(
            identity="snapshot|demo.py|9|2|unresolved-import",
            origin="snapshot",
            path="demo.py",
            line=9,
            column=2,
            code="unresolved-import",
            severity="error",
            message="Module `legacy` is unavailable",
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            completed_event(
                cell,
                status="REJECTED",
                failure=failure,
                detail=StaticIssueDetail(first=issue, total=4),
                role="declaration",
                stage="witness",
            )
        )

        output = stderr.getvalue()
        assert stdout.getvalue() == ""
        assert "failed at [witness]" in output
        assert "A required runtime interface is missing" in output
        assert "demo.py:9:2 [unresolved-import] Module `legacy` is unavailable" in output
        assert "... and 3 more" in output
        assert f"pf diagnose demo --failure {failure.failure_id}" in output

    def test_completed_cell_falls_back_to_log_when_journal_is_unavailable(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="highest")
        process = process_result(
            exit_code=1,
            stderr="Failed to build `numpy==1.24.0`\nBecause cmake is missing",
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="BUILD_FAILURE",
            stage="install-project",
            process=process,
        )
        logs = RunLogStore(root=tmp_path, run_id="fallback-run")
        logs.record(
            1,
            ProcessSpec(
                argv=("build",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            process,
        )
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )

        event = completed_event(
            cell,
            status="REJECTED",
            failure=failure,
            process=process,
            role="declaration-capture",
            stage="install-project",
            diagnose_available=False,
        )
        terminal.consume(event)

        output = stderr.getvalue()
        assert stdout.getvalue() == ""
        assert "failed at [installing dependencies]" in output
        assert "This version combination could not be built." in output
        assert "pf diagnose" not in output
        assert failure.failure_id not in output
        assert "Failed to build `numpy==1.24.0`" not in output
        assert ".pf/logs/fallback-run/process-0001.log" in output

        tty_stderr = StringIO()
        tty_terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=tty_stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            logs=logs,
            root=tmp_path,
        )
        tty_terminal.consume(event)

        styled_output = tty_stderr.getvalue()
        hint_at = styled_output.index("->")
        hint_start = styled_output.rfind("\x1b[", 0, hint_at)
        hint_end = styled_output.index("for details.", hint_at) + len("for details.")
        hint_codes = re.findall(
            r"\x1b\[([0-9;]+)m", styled_output[hint_start:hint_end]
        )
        assert any("36" in code.split(";") for code in hint_codes)
        assert any("3" in code.split(";") for code in hint_codes)
        assert all(
            token not in {"1", "2", "34", "94", "96"}
            for code in hint_codes
            for token in code.split(";")
        )

    def test_successful_cell_does_not_print_probe_diagnose(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="exact-vector", vector=())
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=1, stderr="1 failed"),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(SearchFailureEvent(cell=cell, failure=failure))
        terminal.consume(
            completed_event(cell, status="SUCCESS")
        )

        output = stderr.getvalue()
        assert stdout.getvalue() == ""
        assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in output
        assert "failed at" not in output
        assert "pf diagnose" not in output
        assert failure.failure_id not in output

    def test_completed_cell_without_structured_detail_only_prints_the_stage(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.12",
            extra_surface=(),
        )
        terminal, stdout, stderr = presenter()

        terminal.consume(
            completed_event(
                cell,
                status="BUILD_UNAVAILABLE",
                process=process_result(
                    exit_code=1,
                    stderr=(
                        "Failed to build `numpy==1.24.0`\nBecause cmake is missing"
                    ),
                ),
                stage="install-project",
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.12][x86_64-unknown-linux-gnu][no-extra]\n"
            "failed at [installing dependencies]\n"
        )

    def test_cell_matrix_summary_lists_count_and_axes(self) -> None:
        terminal, stdout, stderr = presenter()

        terminal.consume(
            CellMatrixEvent(
                cells=(
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.12",
                        extra_surface=("cuda",),
                    ),
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.10",
                        extra_surface=(),
                    ),
                    Cell(
                        package="demo",
                        target="aarch64-apple-darwin",
                        python_minor="3.12",
                        extra_surface=("arrow", "cuda"),
                    ),
                )
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✓ selected 3 cells\n"
            "  python: 3.10, 3.12\n"
            "  platform: aarch64-apple-darwin, x86_64-unknown-linux-gnu\n"
            "  extra surfaces: no-extra, cuda, arrow+cuda\n"
        )

    def test_tty_setup_facts_render_in_one_rounded_card(self) -> None:
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=stderr, force_terminal=True),
        )
        cells = (
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.11",
                extra_surface=(),
            ),
            Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.12",
                extra_surface=(),
            ),
        )

        terminal.consume(StatusEvent(message="loading project"))
        terminal.consume(StatusEvent(message="building snapshot"))
        terminal.consume(StatusEvent(message="smoke testing"))
        terminal.consume(CellMatrixEvent(cells=cells))

        output = visible(stderr.getvalue())
        assert output.count("╭") == 1
        assert "✓ loaded project" in output
        assert "✓ built snapshot" in output
        assert "✓ selected 3 cells" in output
        assert "python: 3.10, 3.11, 3.12" in output
        assert "platform: x86_64-unknown-linux-gnu" in output
        assert "extra surfaces: no-extra" in output
        terminal.close()

    def test_tty_live_lifecycle_renders_through_public_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM", "xterm-256color")
        cell_a = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        cell_b = cell_a.model_copy(update={"python_minor": "3.11"})
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(file=stderr, force_terminal=True),
        )

        terminal.consume(StatusEvent(message="searching cells"))
        terminal.consume(CellMatrixEvent(cells=(cell_a, cell_b)))
        terminal.consume(CellStageEvent(cell=cell_a, stage="installing dependencies"))
        terminal.consume(
            completed_event(cell_a, status="SUCCESS", completed=1, total=2)
        )
        terminal.close()

        output = visible(stderr.getvalue())
        assert "✓ selected 2 cells" in output
        assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00" in output
        assert "[py3.11][x86_64-unknown-linux-gnu][no-extra]" not in output
        assert "0 running · 1 left" in output
        assert "╭" in output

    def test_tty_stage_and_known_total_render_without_private_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM", "xterm-256color")
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        terminal.consume(StatusEvent(message="applying floors", completed=1, total=2))
        terminal.consume(CellStageEvent(cell=cell, stage="installing dependencies"))
        terminal.close()

        output = stderr.getvalue()
        plain = visible(output)
        assert "applying floors" in plain
        assert "1/2" in plain
        assert "installing dependencies" in plain
        assert "━" not in plain
        stage_at = output.index("installing dependencies")
        assert "\x1b[2m" in output[: stage_at + 1]

    def test_tty_live_cell_card_border_is_dim_without_adding_a_hue(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellStageEvent(cell=cell, stage="dynamic tests"))
        terminal.close()

        raw = stderr.getvalue()
        border_at = raw.rindex("╭")
        style_at = raw.rfind("\x1b[", 0, border_at)
        border_codes = sgr_codes(raw[style_at:border_at])
        assert "2" in border_codes
        assert not ({"31", "32", "33", "36"} & border_codes)

    def test_tty_live_cell_renders_structured_baseline_identity(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellContextEvent(
                cell=cell,
                detail=BaselineDetailIdentity(),
            )
        )
        terminal.consume(CellStageEvent(cell=cell, stage="resolving project"))
        terminal.close()

        raw = stderr.getvalue()
        output = visible(raw)
        frame = output[output.rfind("╭") :]
        title = "[py3.10][x86_64-unknown-linux-gnu][no-extra]"
        title_line = next(line for line in frame.splitlines() if title in line)
        identity_line = next(
            line for line in frame.splitlines() if "[baseline][highest]" in line
        )

        assert title_line != identity_line
        assert identity_line.index("[baseline]") == title_line.index(title)
        assert frame.index("[baseline][highest]") < frame.index("resolving project")
        identity_at = raw.rindex("[baseline]")
        style_at = raw.rfind("\x1b[", 0, identity_at)
        identity_end = identity_at + len("[baseline][highest]")
        identity_codes = sgr_codes(raw[style_at:identity_end])
        assert "36" in identity_codes
        assert "2" not in identity_codes

    def test_tty_live_cell_renders_declaration_identity_as_first_detail(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=120),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                width=120,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellContextEvent(
                cell=cell,
                detail=DeclarationDetailIdentity(),
            )
        )
        terminal.consume(CellStageEvent(cell=cell, stage="dynamic tests"))
        terminal.close()

        raw = stderr.getvalue()
        output = visible(raw)
        title = "[py3.10][x86_64-unknown-linux-gnu][no-extra]"
        title_line = next(line for line in reversed(output.splitlines()) if title in line)
        detail_line = next(
            line
            for line in reversed(output.splitlines())
            if "[declaration][lowest-direct]" in line
        )
        assert title_line != detail_line
        assert detail_line.index("[declaration]") == title_line.index(title)
        assert output.index("[declaration][lowest-direct]") < output.index(
            "dynamic tests"
        )
        identity_at = raw.rindex("[declaration]")
        style_at = raw.rfind("\x1b[", 0, identity_at)
        identity_end = identity_at + len("[declaration][lowest-direct]")
        identity_codes = sgr_codes(raw[style_at:identity_end])
        assert "36" in identity_codes
        assert "2" not in identity_codes

    def test_tty_live_identity_updates_keep_spinner_gap_and_elapsed_column_stable(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.12",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=120),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=120,
                theme=PF_THEME,
            ),
        )
        title = "[py3.12][x86_64-unknown-linux-gnu][no-extra]"

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(CellContextEvent(cell=cell, detail=BaselineDetailIdentity()))
        baseline_output = visible(stderr.getvalue())
        baseline_title_line = next(
            line
            for line in reversed(baseline_output.splitlines())
            if title in line
        )
        baseline_detail_line = next(
            line
            for line in reversed(baseline_output.splitlines())
            if "[baseline][highest]" in line
        )

        terminal.consume(
            CellContextEvent(cell=cell, detail=DeclarationDetailIdentity())
        )
        declaration_output = visible(stderr.getvalue())
        declaration_title_line = next(
            line
            for line in reversed(declaration_output.splitlines())
            if title in line
        )
        declaration_detail_line = next(
            line
            for line in reversed(declaration_output.splitlines())
            if "[declaration][lowest-direct]" in line
        )
        terminal.close()

        baseline_title_at = baseline_title_line.index(title)
        declaration_title_at = declaration_title_line.index(title)
        assert not baseline_title_line[baseline_title_at - 2].isspace()
        assert baseline_title_line[baseline_title_at - 1] == " "
        assert not declaration_title_line[declaration_title_at - 2].isspace()
        assert declaration_title_line[declaration_title_at - 1] == " "
        assert baseline_title_line.index("0:00:") == declaration_title_line.index(
            "0:00:"
        )
        assert baseline_detail_line.index("[baseline]") == baseline_title_at
        assert declaration_detail_line.index("[declaration]") == declaration_title_at

    def test_tty_live_stage_and_bar_align_with_the_cell_title(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.12",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=120),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=120,
                theme=PF_THEME,
            ),
        )
        title = "[py3.12][x86_64-unknown-linux-gnu][no-extra]"

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(CellContextEvent(cell=cell, detail=BaselineDetailIdentity()))
        terminal.consume(CellStageEvent(cell=cell, stage="static check"))
        static_output = visible(stderr.getvalue())
        static_title_line = next(
            line for line in reversed(static_output.splitlines()) if title in line
        )
        static_stage_line = next(
            line
            for line in reversed(static_output.splitlines())
            if "static check" in line
        )

        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        dynamic_output = visible(stderr.getvalue())
        dynamic_title_line = next(
            line for line in reversed(dynamic_output.splitlines()) if title in line
        )
        dynamic_stage_line = next(
            line
            for line in reversed(dynamic_output.splitlines())
            if "dynamic tests" in line
        )
        terminal.close()

        assert static_stage_line.index("static check") == static_title_line.index(title)
        assert dynamic_stage_line.index("dynamic tests") == dynamic_title_line.index(
            title
        )
        assert re.search(r"dynamic tests [━╺╸]", dynamic_stage_line) is not None

    def test_tty_frozen_cell_moves_identity_to_result_colored_first_detail(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.12",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=120),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                width=120,
                theme=PF_THEME,
            ),
        )
        title = "[py3.12][x86_64-unknown-linux-gnu][no-extra]"
        terminal.bind_command("smoke")

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(CellContextEvent(cell=cell, detail=BaselineDetailIdentity()))
        terminal.consume(CellStageEvent(cell=cell, stage="static check"))
        terminal.consume(completed_event(cell, status="SUCCESS"))
        terminal.close()

        output = stderr.getvalue()
        frozen = next(
            line
            for line in reversed(visible(output).splitlines())
            if f"✓ {title}" in line
        )
        assert f"✓ {title} 0:00:00" in frozen
        detail = next(
            line
            for line in reversed(visible(output).splitlines())
            if "smoke passed at [baseline][highest]" in line
        )
        assert "smoke passed at [baseline][highest]" in detail
        completion_at = output.rindex("smoke passed")
        detail_end = output.index("[highest]", completion_at) + len("[highest]")
        detail_style = output.rfind("\x1b[", 0, completion_at)
        detail_codes = sgr_codes(output[detail_style:detail_end])
        assert "32" in detail_codes
        assert "2" not in detail_codes
        assert "36" not in detail_codes

    def test_tty_live_cell_renders_search_probe_identity_above_stage(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellContextEvent(
                cell=cell,
                detail=SearchProbeDetailIdentity(
                    dependency="pydantic",
                    version="1.5",
                    lower_version="1.0",
                    upper_version="2.0",
                    candidate_count=7,
                ),
            )
        )
        terminal.consume(CellStageEvent(cell=cell, stage="static check"))
        terminal.consume(CellStageEvent(cell=cell, stage="dynamic tests"))
        terminal.close()

        raw = stderr.getvalue()
        output = visible(raw)
        identity = "[pydantic=1.5][1.0..2.0#7]"
        assert identity in output
        assert output.rindex(identity) < output.rindex("dynamic tests")
        title = "[py3.10][x86_64-unknown-linux-gnu][no-extra]"
        title_line = next(
            line for line in reversed(output.splitlines()) if title in line
        )
        identity_line = next(
            line for line in reversed(output.splitlines()) if identity in line
        )
        assert identity_line.index(identity) == title_line.index(title)
        identity_at = raw.rindex("[pydantic=")
        style_at = raw.rfind("\x1b[", 0, identity_at)
        identity_end = identity_at + len(identity)
        identity_codes = sgr_codes(raw[style_at:identity_end])
        assert "36" in identity_codes
        assert "2" not in identity_codes

    def test_tty_live_stage_renders_uv_style_determinate_progress(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        terminal.close()

        output = visible(stderr.getvalue())
        assert "dynamic tests" in output
        assert "3/8 tests" in output
        assert re.search(r"ETA \d+:\d{2}:\d{2}", output) is not None
        stage_at = output.rindex("dynamic tests")
        assert re.search(r"dynamic tests [━╺╸]", output) is not None, repr(
            output[stage_at : stage_at + 40]
        )
        cell_line = next(
            line
            for line in reversed(output.splitlines())
            if "[py3.10][x86_64-unknown-linux-gnu][no-extra]" in line
        )
        elapsed_gap = re.search(r"\[no-extra\]( +)0:00:", cell_line)
        assert elapsed_gap is not None
        assert "━" in output
        assert "●" not in output
        dynamic_line = next(
            line for line in reversed(output.splitlines()) if "dynamic tests" in line
        )
        assert "·" not in dynamic_line

    def test_tty_live_progress_updates_keep_the_same_stage_row(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=4, total=8, unit="tests"),
            )
        )
        output = visible(stderr.getvalue())
        terminal.close()

        latest_frame = output[output.rfind("╭") :]
        assert latest_frame.count("dynamic tests") == 1
        assert "4/8 tests" in latest_frame
        assert "3/8 tests" not in latest_frame

    def test_tty_live_view_refreshes_spinner_at_a_fixed_cadence(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(CellStageEvent(cell=cell, stage="resolving project"))
        time.sleep(0.08)
        before_tick = stderr.getvalue()
        time.sleep(0.12)
        after_tick = stderr.getvalue()
        terminal.close()

        assert len(after_tick) > len(before_tick)
        assert after_tick.count("[py3.10]") > before_tick.count("[py3.10]")
        before_frame = next(
            line
            for line in reversed(visible(before_tick).splitlines())
            if "[py3.10]" in line
        )
        after_frame = next(
            line
            for line in reversed(visible(after_tick).splitlines())
            if "[py3.10]" in line
        )
        assert after_frame != before_frame

    def test_narrow_tty_keeps_exact_stage_count_when_bar_does_not_fit(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=56),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=56,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        terminal.close()

        output = visible(stderr.getvalue())
        assert "3/8 tests" in output
        assert re.search(r"ETA \d+:\d{2}:\d{2}", output) is not None

    def test_dynamic_eta_is_unknown_before_the_first_completed_test(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=0, total=8, unit="tests"),
            )
        )
        terminal.close()

        assert "ETA --:--:--" in visible(stderr.getvalue())

    @pytest.mark.parametrize("width", (56, 80))
    def test_narrow_tty_wraps_without_losing_live_cell_header_fields(
        self,
        width: int,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.12",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=width),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=width,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellContextEvent(cell=cell, detail=DeclarationDetailIdentity())
        )
        output = visible(stderr.getvalue())
        terminal.close()

        frame = output[output.rfind("╭") :]
        compact = "".join(
            character
            for character in frame.replace("0:00:00", "")
            if not character.isspace() and character not in "│╭╮╰╯─"
        )
        assert "[py3.12][x86_64-unknown-linux-gnu][no-extra]" in compact
        assert "[declaration][lowest-direct]" in compact
        assert re.search(r"\S \[py3\.12", frame) is not None
        assert "0:00:00" in frame
        assert "…" not in frame

    def test_missing_same_stage_progress_keeps_the_last_determinate_value(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                width=80,
                theme=PF_THEME,
            ),
        )

        terminal.consume(CellMatrixEvent(cells=(cell,)))
        terminal.consume(
            CellStageEvent(
                cell=cell,
                stage="dynamic tests",
                progress=StageProgress(completed=3, total=8, unit="tests"),
            )
        )
        terminal.consume(CellStageEvent(cell=cell, stage="dynamic tests"))
        terminal.close()

        output = visible(stderr.getvalue())
        latest_stage = output[output.rindex("dynamic tests") :]
        assert "3/8 tests" in latest_stage
        assert re.search(r"ETA \d+:\d{2}:\d{2}", latest_stage) is not None
        assert "━" in latest_stage



    def test_tty_frozen_failure_card_leads_with_diagnose_details_and_process_summary(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        proposal = Proposal(
            proposal_id="highest",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        ty_process = process_result(exit_code=0, stdout="[]")
        check = TyCheck(process=ty_process, diagnostics=())
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(()),
        )
        test_process = process_result(
            stderr=(
                "==================== test session starts ====================\n"
                "collected 3 items\n"
                "FAILED tests/test_cli.py::test_example\n"
                "FAILED tests/test_project.py::test_load\n"
                "=== 2 failed, 1 passed in 0.51s ==="
            )
        )
        baseline = StaticBaseline(
            proposal=proposal,
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(
                process=test_process,
                detail=PytestFailureDetail(
                    first=PytestFailureCase(
                        nodeid="tests/test_cli.py::test_example",
                        phase="call",
                    ),
                    total=2,
                ),
            ),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=test_process,
        )
        logs = RunLogStore(root=tmp_path, run_id="tty-run")
        logs.record(
            2,
            ProcessSpec(
                argv=("pytest",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            test_process,
        )
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
                width=120,
            ),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_smoke(
            SmokeBaselineRejection(
                outcomes=(
                    BaselineRejection(
                        attempt=attempt,
                        failure=failure,
                        static_baseline=baseline,
                        evaluation=evaluation,
                    ),
                )
            )
        )

        output = stderr.getvalue()
        plain = visible(output)
        stripped = "".join(" " if ch in "│╭╮╰╯─" else ch for ch in plain)
        collapsed = " ".join(stripped.split())
        diagnose = f"`pf diagnose demo --failure {failure.failure_id}`"
        assert exit_code == 1
        assert "╭" in plain
        assert "smoke failed at [baseline][highest][testing]" in collapsed
        assert diagnose in collapsed
        assert "-> run" in collapsed
        assert "-> see" not in collapsed
        assert "for more information" in collapsed
        reason = "The full test command failed for this version combination."
        reason_at = output.index(reason)
        reason_style_at = output.rfind("\x1b[", 0, reason_at)
        reason_codes = re.findall(
            r"\x1b\[([0-9;]+)m",
            output[reason_style_at : reason_at + len(reason)],
        )
        assert any("31" in code.split(";") for code in reason_codes)
        assert all(
            token not in {"1", "91"}
            for code in reason_codes
            for token in code.split(";")
        )
        hint_at = output.index("->")
        hint_start = output.rfind("\x1b[", 0, hint_at)
        hint_end = output.index("for more information.", hint_at) + len(
            "for more information."
        )
        hint_codes = re.findall(r"\x1b\[([0-9;]+)m", output[hint_start:hint_end])
        assert any("2" in code.split(";") for code in hint_codes)
        assert any("3" in code.split(";") for code in hint_codes)
        assert all(
            token
            not in {
                "1",
                "30",
                "31",
                "32",
                "33",
                "34",
                "35",
                "36",
                "37",
                "90",
                "91",
                "92",
                "93",
                "94",
                "95",
                "96",
                "97",
            }
            for code in hint_codes
            for token in code.split(";")
        )
        assert "The full test command failed for this version combination." in collapsed
        assert "The highest-version resolution did not pass" not in collapsed
        assert ".pf/logs/tty-run/process-0002.log" not in collapsed
        assert "test session starts" not in collapsed
        assert "collected 3 items" not in collapsed
        assert "FAILED tests/test_cli.py::test_example" in collapsed
        assert "FAILED tests/test_project.py::test_load" not in collapsed
        assert "... and 1 more" in collapsed
        assert "=== 2 failed, 1 passed in 0.51s ===" not in collapsed
        assert collapsed.index("smoke failed at [baseline][highest][testing]") < collapsed.index(
            "The full test command failed for this version combination."
        )
        assert collapsed.index("The full test command failed") < collapsed.index(
            "FAILED tests/test_cli.py"
        )
        assert collapsed.index("... and 1 more") < collapsed.index(
            "for more information"
        )








    def test_non_tty_hides_process_activity_behind_run_logs(self) -> None:
        terminal, stdout, stderr = presenter()

        terminal.consume(StatusEvent(message="loading project"))
        terminal.consume(
            ProcessEvent(
                process_id=1,
                argv=("uv", "python", "list", "--output-format", "json"),
                state="started",
            )
        )
        terminal.consume(
            ProcessEvent(
                process_id=1,
                argv=("uv", "python", "list", "--output-format", "json"),
                state="finished",
                duration_seconds=0.4,
            )
        )

        terminal.consume(
            ProcessEvent(
                process_id=2,
                argv=("mystery",),
                state="finished",
            )
        )

        assert stdout.getvalue() == ""
        assert stderr.getvalue() == "loading project\n"




    def test_tty_completed_status_checkmark_is_green(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=StringIO(),
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        presenter.consume(StatusEvent(message="loading project"))
        presenter.consume(StatusEvent(message="building snapshot"))
        presenter.consume(
            CellMatrixEvent(
                cells=(
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.10",
                        extra_surface=(),
                    ),
                )
            )
        )

        output = terminal.getvalue()
        assert "✓ loaded project" in visible(output)
        check_at = output.index("✓")
        assert "\x1b[32m" in output[: check_at + 1]

    def test_tty_matrix_axis_lines_are_dim(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        presenter.consume(
            CellMatrixEvent(
                cells=(
                    Cell(
                        package="demo",
                        target="x86_64-unknown-linux-gnu",
                        python_minor="3.10",
                        extra_surface=(),
                    ),
                )
            )
        )

        output = terminal.getvalue()
        python_at = output.index("python:")
        assert "\x1b[2m" in output[: python_at + 1]
        plain = visible(output)
        assert "✓ selected 1 cell" in plain
        assert "python: 3.10" in plain
        assert "platform: x86_64-unknown-linux-gnu" in plain
        assert "extra surfaces: no-extra" in plain
        assert "╭" in plain

    def test_tty_completed_cell_log_omits_unstructured_process_output(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )

        presenter.consume(StatusEvent(message="checking declarations"))
        presenter.consume(
            CellStageEvent(cell=cell, stage="static check")
        )
        presenter.consume(
            completed_event(
                cell,
                status="STATIC_REGRESSION",
                process=process_result(
                    exit_code=1,
                    stderr="error: Unresolved import 'missing'",
                ),
                stage="ty",
            )
        )

        output = terminal.getvalue()
        plain = visible(output)
        assert "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
        assert "failed at [static checking]" in plain
        assert "0:00:00" in plain
        assert "error: Unresolved import 'missing'" not in plain
        assert "  error: Unresolved import 'missing'" not in plain
        assert "STATIC_REGRESSION" not in plain
        assert "╭" in plain
        title_at = output.index("[py3.10]")
        assert "31" in output[:title_at]

    def test_tty_failed_progress_uses_a_red_cross(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=StringIO(),
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )

        presenter.consume(StatusEvent(message="checking declarations"))
        presenter.consume(
            completed_event(
                cell,
                status="STATIC_REGRESSION",
                stage="ty",
            )
        )

        output = terminal.getvalue()
        plain = visible(output)
        assert "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
        assert "STATIC_REGRESSION" not in plain
        assert "checked declarations" not in plain
        cross_at = output.index("✗")
        assert "31" in output[: cross_at + 1]

    def test_tty_warning_progress_uses_a_warning_icon(self) -> None:
        terminal = StringIO()
        presenter = TerminalPresenter(
            stdout=Console(
                file=StringIO(),
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            stderr=Console(
                file=terminal,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )

        presenter.consume(StatusEvent(message="searching cells"))
        presenter.consume(
            completed_event(cell, status="NO_PASS_IN_SEARCH_SPACE")
        )

        output = terminal.getvalue()
        plain = visible(output)
        assert "⚠ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
        assert "NO_PASS_IN_SEARCH_SPACE" not in plain
        assert "searched cells" not in plain
        warn_at = output.index("⚠")
        assert "33" in output[: warn_at + 1]

    def test_check_indeterminate_does_not_emit_a_process_log_hyperlink(
        self, tmp_path: Path
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        logs = RunLogStore(root=tmp_path, run_id="linked-run")
        process = process_result(stderr="test process failed")
        path = logs.record(
            1,
            ProcessSpec(
                argv=("pytest",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            process,
        )
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=True, color_system="truecolor"),
            stderr=Console(file=stderr, force_terminal=True, color_system="truecolor"),
            logs=logs,
            root=tmp_path,
        )

        terminal.render_check(
            CheckIndeterminate(
                failure=recorded_failure(
                    cause="TOOL_FAILURE",
                    stage="test",
                    process=process,
                )
            )
        )

        output = stderr.getvalue()
        assert "\x1b]8;" not in output
        assert path.resolve().as_uri() not in output
        assert ".pf/logs/linked-run/process-0001.log" not in visible(output)


class TestVerificationRendering:
    @pytest.mark.parametrize(
        ("result", "expected_exit", "fragments"),
        (
            (
                CheckCompatibilityFailure(evaluations=()),
                1,
                ("Check failed", "lower bounds are incompatible", "0 cells"),
            ),
            (
                CheckIndeterminate(
                    failure=recorded_failure(
                        cause="TIMEOUT",
                        stage="test",
                        process=process_result(
                            stderr="timeout",
                            timed_out=True,
                            start_error="timeout",
                        ),
                    )
                ),
                4,
                ("Check indeterminate", "timed out", "compatibility is unknown"),
            ),
        ),
    )
    def test_check_failures_have_stable_exit_codes(
        self,
        result: CheckCompatibilityFailure | CheckIndeterminate,
        expected_exit: int,
        fragments: tuple[str, ...],
    ) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_check(result)

        assert exit_code == expected_exit
        assert stdout.getvalue() == ""
        assert all(fragment in stderr.getvalue() for fragment in fragments)

    def test_check_indeterminate_omits_process_output_and_log_link(
        self,
        tmp_path: Path,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        logs = RunLogStore(root=tmp_path, run_id="test-run")
        process = process_result(
            stderr=(
                "No solution found when resolving dependencies:\nbecause tomli==2.0.0"
            ),
        )
        logs.record(
            1,
            ProcessSpec(
                argv=("uv", "pip", "install"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            process,
        )
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_check(
            CheckIndeterminate(
                failure=recorded_failure(
                    cause="RESOLUTION_CONFLICT",
                    stage="install-harness",
                    process=process,
                )
            )
        )

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "! Check indeterminate · This version combination has conflicting dependency requirements and cannot be installed. · 0 cells\n"
        )

    def test_smoke_test_failure_prints_structured_detail_without_output_tail(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        proposal = Proposal(
            proposal_id="highest",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        ty_process = process_result(exit_code=0, stdout="[]")
        check = TyCheck(process=ty_process, diagnostics=())
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(()),
        )
        test_process = process_result(stderr="1 failed\n2 passed")
        baseline = StaticBaseline(
            proposal=proposal,
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(
                process=test_process,
                detail=PytestFailureDetail(
                    first=PytestFailureCase(
                        nodeid="tests/test_cli.py::test_example",
                        phase="call",
                    ),
                    total=2,
                ),
            ),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=test_process,
        )
        logs = RunLogStore(root=tmp_path, run_id="smoke-run")
        logs.record(
            2,
            ProcessSpec(
                argv=("pytest",),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            test_process,
        )
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_smoke(
            SmokeBaselineRejection(
                outcomes=(
                    BaselineRejection(
                        attempt=attempt,
                        failure=failure,
                        static_baseline=baseline,
                        evaluation=evaluation,
                    ),
                )
            )
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.11][x86_64-unknown-linux-gnu][no-extra]\n"
            "smoke failed at [baseline][highest][testing]\n"
            "The full test command failed for this version combination.\n"
            "FAILED tests/test_cli.py::test_example\n"
            "... and 1 more\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "✗ Smoke failed · highest-version resolution did not pass · 1 cell\n"
        )

    @pytest.mark.parametrize(
        ("adapter_stage", "failed_at"),
        (
            ("install", "installing dependencies"),
            ("install-harness", "installing harness"),
            ("ty", "static checking"),
            ("test", "testing"),
        ),
    )
    def test_smoke_tool_failures_use_stable_user_stage_names(
        self,
        adapter_stage: str,
        failed_at: str,
    ) -> None:
        terminal, stdout, stderr = presenter()
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TOOL_FAILURE",
            stage=adapter_stage,
            process=process_result(stderr="tool failed"),
        )

        exit_code = terminal.render_smoke(
            SmokeIndeterminate(
                outcomes=(BaselineIndeterminate(attempt=attempt, failure=failure),)
            )
        )

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert (
            f"smoke failed at [baseline][highest][{failed_at}]"
            in stderr.getvalue()
        )
        assert (
            "PF could not complete a verification tool operation reliably."
            in stderr.getvalue()
        )
        assert "this candidate" not in stderr.getvalue()
        assert "TOOL_FAILURE" not in stderr.getvalue()
        assert "BASELINE_INDETERMINATE" not in stderr.getvalue()

    def test_smoke_hides_baseline_ty_warnings(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        proposal = Proposal(
            proposal_id="highest",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        diagnostic = TyDiagnostic(
            identity="snapshot|src/demo.py|4|7|invalid-type",
            origin="snapshot",
            path="src/demo.py",
            line=4,
            column=7,
            code="invalid-type",
            severity="major",
            message="Expected str,\n  found int",
        )
        process = process_result(exit_code=1, stdout="[]")
        logs = RunLogStore(root=tmp_path, run_id="ty-run")
        logs.record(
            3,
            ProcessSpec(
                argv=("ty", "check"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=10,
            ),
            process,
        )
        check = TyCheck(process=process, diagnostics=(diagnostic,))
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(check.diagnostics),
        )
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )

        exit_code = terminal.render_smoke(
            SmokePass(
                outcomes=(
                    HighestVersionPass(
                        attempt=attempt,
                        harness_baseline=empty_harness_baseline(
                            attempt.identity.cell
                        ),
                        baseline=StaticBaseline(
                            proposal=proposal,
                            ty=check,
                            digest=ty_diagnostic_digest(check.diagnostics),
                        ),
                        evaluation=PassEvaluation(
                            proposal=proposal,
                            static=static,
                            test=TestPass(
                                process=process.model_copy(update={"exit_code": 0})
                            ),
                        ),
                    ),
                )
            )
        )

        assert exit_code == 0
        assert stdout.getvalue() == "✓ Smoke passed · 1 cell\n"
        assert stderr.getvalue() == ""

    def test_check_hides_baseline_ty_warnings(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        proposal = Proposal(
            proposal_id="lowest",
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        diagnostic = TyDiagnostic(
            identity="external|site-packages/demo.pyi|invalid-return-type",
            origin="external",
            path="site-packages/demo.pyi",
            line=None,
            column=None,
            code="invalid-return-type",
            severity="major",
            message="Returned int instead of str",
        )
        process = process_result(exit_code=1, stdout="[]")
        check = TyCheck(process=process, diagnostics=(diagnostic,))
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=ty_diagnostic_digest(check.diagnostics),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_check(
            CheckPass(
                evaluations=(
                    PassEvaluation(
                        proposal=proposal,
                        static=static,
                        test=TestPass(
                            process=process.model_copy(update={"exit_code": 0})
                        ),
                    ),
                )
            )
        )

        assert exit_code == 0
        assert stdout.getvalue() == "✓ Check passed · 1 cell\n"
        assert stderr.getvalue() == ""

    def test_test_failure_does_not_blame_a_static_increment(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        proposal = Proposal(
            proposal_id="lowest",
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        existing = TyDiagnostic(
            identity="snapshot|demo.py|1|1|existing",
            origin="snapshot",
            path="demo.py",
            line=1,
            column=1,
            code="existing",
            severity="major",
            message="existing diagnostic",
        )
        increment = TyDiagnostic(
            identity="snapshot|demo.py|9|2|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=9,
            column=2,
            code="dependency-regression",
            severity="major",
            message="new dependency regression",
        )
        process = process_result(exit_code=1, stdout="[]")
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=(existing, increment)),
            baseline_digest=ty_diagnostic_digest((existing,)),
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        terminal, stdout, stderr = presenter()
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )

        exit_code = terminal.render_check(
            CheckCompatibilityFailure(evaluations=(evaluation,))
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.11][x86_64-unknown-linux-gnu][no-extra]\n"
            "check failed at [declaration][lowest-direct][testing]\n"
            "✗ Check failed · declared lower bounds are incompatible · 1 cell\n"
        )

    def test_check_does_not_show_static_increment_for_test_failure(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        proposal = Proposal(
            proposal_id="lowest",
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        increment = TyDiagnostic(
            identity="snapshot|demo.py|9|2|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=9,
            column=2,
            code="dependency-regression",
            severity="major",
            message="new dependency regression",
        )
        process = process_result(exit_code=1, stdout="[]")
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=(increment,)),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        terminal, stdout, stderr = presenter()
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )
        terminal.consume(
            completed_event(
                cell,
                status="TEST_FAIL",
                process=process,
                stage="test",
            )
        )

        exit_code = terminal.render_check(
            CheckCompatibilityFailure(evaluations=(evaluation,))
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        output = stderr.getvalue()
        assert "demo.py:9:2 [dependency-regression]" not in output
        assert "STATIC_REGRESSION" not in output
        assert "ty: 1 new diagnostic" not in output
        assert output.endswith(
            "✗ Check failed · declared lower bounds are incompatible · 1 cell\n"
        )

    def test_smoke_live_completion_omits_smoke_baseline_impact(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=1, stderr="1 failed"),
        )
        terminal, _, stderr = presenter()
        terminal.bind_command("smoke")

        terminal.consume(
            CellContextEvent(cell=cell, detail=BaselineDetailIdentity())
        )
        terminal.consume(
            completed_event(
                cell,
                status="BASELINE_REJECTION",
                failure=failure,
                role="baseline",
                stage="test",
            )
        )

        output = stderr.getvalue()
        lines = output.splitlines()
        assert lines[0] == "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]"
        assert lines[1] == "smoke failed at [baseline][highest][testing]"
        assert "The highest-version resolution did not pass" not in output
        assert "The full test command failed for this version combination." in output
        assert "did not start the floor search" not in output

    def test_smoke_completion_defaults_to_baseline_identity_without_context(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        terminal, _, stderr = presenter()
        terminal.bind_command("smoke")

        terminal.consume(completed_event(cell, status="SUCCESS"))

        assert stderr.getvalue().splitlines() == [
            "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra]",
            "smoke passed at [baseline][highest]",
        ]

    @pytest.mark.parametrize("command", ("smoke", "check"))
    def test_unstarted_cell_completion_does_not_invent_attempt_identity(
        self,
        command: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        failure = recorded_failure(
            cause="TIMEOUT",
            stage="scheduler-deadline",
            process=process_result(timed_out=True),
        )
        terminal, _, stderr = presenter()
        terminal.bind_command(command)

        terminal.consume(
            completed_event(
                cell,
                status="INDETERMINATE",
                failure=failure,
                role="probe",
                stage="scheduler-deadline",
            )
        )

        output = stderr.getvalue()
        assert f"{command} failed at [scheduler deadline]" in output
        assert "[baseline]" not in output
        assert "[declaration]" not in output

    @pytest.mark.parametrize(
        ("command", "identity", "role", "expected"),
        (
            (
                "check",
                DeclarationDetailIdentity(),
                "declaration",
                "check failed at [declaration][lowest-direct][testing]",
            ),
            (
                "search",
                SearchProbeDetailIdentity(
                    dependency="pydantic",
                    version="2.0.1",
                    lower_version="1.0",
                    upper_version="3.0",
                    candidate_count=14,
                ),
                "probe",
                "search stopped at [pydantic=2.0.1][1.0..3.0#14][testing]",
            ),
        ),
    )
    def test_command_completion_identity_uses_shared_first_detail_prefix(
        self,
        command: str,
        identity: CellDetailIdentity,
        role: VerificationRole,
        expected: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt_for(cell)),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=1),
        )
        terminal, _, stderr = presenter()
        terminal.bind_command(command)

        terminal.consume(CellContextEvent(cell=cell, detail=identity))
        terminal.consume(
            completed_event(
                cell,
                status="REJECTED",
                failure=failure,
                role=role,
                stage="test",
            )
        )

        assert stderr.getvalue().splitlines()[1] == expected

    @pytest.mark.parametrize(
        ("command", "status", "identity", "role", "result_color", "expected"),
        (
            (
                "check",
                "REJECTED",
                DeclarationDetailIdentity(),
                "declaration",
                "31",
                "check failed at [declaration][lowest-direct][testing]",
            ),
            (
                "search",
                "NO_PASS_IN_SEARCH_SPACE",
                SearchProbeDetailIdentity(
                    dependency="pydantic",
                    version="2.0.1",
                    lower_version="1.0",
                    upper_version="3.0",
                    candidate_count=14,
                ),
                "probe",
                "33",
                "search stopped at [pydantic=2.0.1][1.0..3.0#14][testing]",
            ),
        ),
    )
    def test_completed_identity_and_stage_use_only_default_result_color(
        self,
        command: str,
        status: str,
        identity: CellDetailIdentity,
        role: VerificationRole,
        result_color: str,
        expected: str,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt_for(cell)),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=1),
        )
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )
        terminal.bind_command(command)

        terminal.consume(CellContextEvent(cell=cell, detail=identity))
        terminal.consume(
            completed_event(
                cell,
                status=status,
                failure=failure,
                role=role,
                stage="test",
            )
        )
        terminal.close()

        raw = stderr.getvalue()
        assert expected in visible(raw)
        action_at = raw.rindex(expected.split(" at ", 1)[0])
        style_start = raw.rfind("\x1b[", 0, action_at)
        stage_end = raw.index("[testing]", action_at) + len("[testing]")
        codes = sgr_codes(raw[style_start:stage_end])
        assert result_color in codes
        assert not ({"1", "2", "36"} & codes)


class TestSearchRendering:
    def test_search_stopped_summary_uses_bold_result_color_for_the_full_line(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
            stderr=Console(
                file=stderr,
                force_terminal=True,
                no_color=False,
                color_system="standard",
                theme=PF_THEME,
            ),
        )

        exit_code = terminal.render_search((incomplete_report("INDETERMINATE"),))

        output = stderr.getvalue()
        assert exit_code == 4
        assert re.search(
            r"\x1b\[[0-9;]*1[0-9;]*33m! Search stopped",
            output,
        ) is not None

    def test_search_cell_reason_distinguishes_early_unknown_from_exhaustion(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="exact-vector", vector=())
        rejected = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=1),
        )
        unknown = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TIMEOUT",
            stage="test",
            process=process_result(timed_out=True),
        )
        identity = SearchProbeDetailIdentity(
            dependency="pydantic",
            version="2.0.1",
            lower_version="1.0",
            upper_version="3.0",
            candidate_count=14,
        )

        exhausted, _, exhausted_stderr = presenter()
        exhausted.bind_command("search")
        exhausted.consume(CellContextEvent(cell=cell, detail=identity))
        exhausted.consume(
            completed_event(
                cell,
                status="NO_PASS_IN_SEARCH_SPACE",
                failure=rejected,
                role="probe",
                stage="test",
            )
        )
        indeterminate, _, indeterminate_stderr = presenter()
        indeterminate.bind_command("search")
        indeterminate.consume(CellContextEvent(cell=cell, detail=identity))
        indeterminate.consume(
            completed_event(
                cell,
                status="INDETERMINATE",
                failure=unknown,
                role="probe",
                stage="test",
            )
        )

        assert (
            "The configured search space was fully evaluated, but no compatible "
            "version combination was found."
            in exhausted_stderr.getvalue()
        )
        assert "full test command failed" not in exhausted_stderr.getvalue()
        assert (
            "Search stopped before the configured search space was fully evaluated. "
            "The operation timed out, so compatibility is unknown."
            in indeterminate_stderr.getvalue()
        )
        assert "fully evaluated, but no compatible" not in indeterminate_stderr.getvalue()

    def test_search_cell_uses_latest_terminal_failure_not_historical_detail(
        self,
        tmp_path: Path,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        attempt = attempt_for(cell, resolution="exact-vector", vector=())
        proposal = Proposal(
            proposal_id="candidate",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        static_process = process_result(exit_code=1, stdout="[]")
        increment = TyDiagnostic(
            identity="snapshot|demo.py|4|2|bad-argument-type",
            origin="snapshot",
            path="demo.py",
            line=4,
            column=2,
            code="bad-argument-type",
            severity="error",
            message="argument has the wrong type",
        )
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=static_process, diagnostics=(increment,)),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        dynamic_process = process_result(stderr="1 failed\n2 passed")
        dynamic = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(
                process=dynamic_process,
                detail=PytestFailureDetail(
                    first=PytestFailureCase(
                        nodeid="tests/test_search.py::test_candidate",
                        phase="call",
                    ),
                    total=2,
                ),
            ),
        )
        install_process = process_result(stderr="No solution found\nconflicting pins")
        install = ToolFailure(
            cause="HARNESS_CONFLICT",
            stage="install-harness",
            process=install_process,
        )
        logs = RunLogStore(root=tmp_path, run_id="search-run")
        for process_id, process in enumerate(
            (static_process, dynamic_process, install_process), start=1
        ):
            logs.record(
                process_id,
                ProcessSpec(
                    argv=("tool",),
                    cwd=tmp_path.as_posix(),
                    timeout_seconds=10,
                ),
                process,
            )
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
            logs=logs,
            root=tmp_path,
        )
        dynamic_failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=dynamic_process,
        )
        earlier_failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process_result(exit_code=2, stderr="earlier failure"),
        )
        install_failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=install.cause,
            stage=install.stage,
            process=install.process,
        )
        terminal.consume(SearchFailureEvent(cell=cell, failure=earlier_failure))
        terminal.consume(
            SearchFailureEvent(cell=cell, failure=dynamic_failure, evaluation=dynamic)
        )
        terminal.consume(SearchFailureEvent(cell=cell, failure=install_failure))

        exit_code = terminal.render_search(
            (incomplete_report("NO_PASS_IN_SEARCH_SPACE"),)
        )

        output = stderr.getvalue()
        assert exit_code == 2
        assert "demo.py:4:2 [bad-argument-type]" not in output
        assert "The full test command failed for this version combination." not in output
        assert "FAILED tests/test_search.py::test_candidate" not in output
        assert "... and 1 more" not in output
        assert "test dependencies cannot be installed" in output
        assert "RESOLUTION_CONFLICT" not in output
        assert ".pf/logs/search-run/" not in output
        assert output.count("pf diagnose demo --failure") == 1
        assert f"--failure {install_failure.failure_id}" in output
        assert f"--failure {dynamic_failure.failure_id}" not in output
        assert f"--failure {earlier_failure.failure_id}" not in output

    @pytest.mark.parametrize(
        ("reasons", "expected_exit"),
        (
            ((), 0),
            (("BASELINE_REJECTION",), 1),
            (("INDETERMINATE",), 4),
            (("BASELINE_REJECTION", "INDETERMINATE"), 1),
            (("NO_PASS_IN_SEARCH_SPACE",), 2),
        ),
    )
    def test_search_reasons_determine_the_exit_code(
        self,
        reasons: tuple[str, ...],
        expected_exit: int,
    ) -> None:
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((incomplete_report(*reasons),))

        assert exit_code == expected_exit
        expected_stderr: dict[tuple[str, ...], str] = {
            (): "",
            ("BASELINE_REJECTION",): (
                "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
            ),
            ("INDETERMINATE",): (
                "! Search stopped · compatibility is unknown · 1 report written\n"
            ),
            ("BASELINE_REJECTION", "INDETERMINATE"): (
                "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
            ),
            ("NO_PASS_IN_SEARCH_SPACE",): (
                "⚠ Search incomplete · 1 report written · no applicable floor\n"
            ),
        }
        if not reasons:
            assert stderr.getvalue() == ""
            assert (
                stdout.getvalue()
                == "✓ Search complete · 1 report · package-floor.json\n"
            )
        else:
            assert stdout.getvalue() == ""
            assert stderr.getvalue() == expected_stderr[reasons]

    def test_search_baseline_rejection_prints_user_guidance(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="HARNESS_CONFLICT",
            stage="resolve-environment",
            process=process_result(
                stderr="No solution found when resolving dependencies",
            ),
        )
        report = incomplete_report(
            "BASELINE_REJECTION",
            cell_results=(BaselineRejection(attempt=attempt, failure=failure),),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
            "search stopped at [baseline][highest][resolving the test environment]\n"
            "The test dependencies cannot be installed without changing the versions being checked.\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
        )

    def test_search_infra_failure_prints_message_detail_without_a_process(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        terminal_result = cell_indeterminate(
            cell,
            cause="SOURCE_FAILURE",
            stage="candidate-discovery",
        )
        report = incomplete_report(
            "INDETERMINATE",
            cell_results=(terminal_result,),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "! [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
            "search stopped at [candidate discovery]\n"
            "Search stopped before the configured search space was fully evaluated. PF could not reach or read a configured package source.\n"
            f"-> run `pf diagnose demo --failure {terminal_result.failure_id}` for more information.\n"
            "! Search stopped · compatibility is unknown · 1 report written\n"
        )

    def test_search_probe_indeterminate_prints_candidate_unknown_impact(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(
            cell,
            resolution="exact-vector",
            vector=(VersionPin(name="idna", version="2.0"),),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TIMEOUT",
            stage="test",
            process=process_result(timed_out=True),
        )
        report = incomplete_report(
            "INDETERMINATE",
            cell_results=(
                CellIndeterminate(
                    cell=cell,
                    phase="test",
                    failure_id=failure.failure_id,
                    failure_records=(failure,),
                ),
            ),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 4
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "! [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
            "search stopped at [testing]\n"
            "Search stopped before the configured search space was fully evaluated. The operation timed out, so compatibility is unknown.\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "! Search stopped · compatibility is unknown · 1 report written\n"
        )

    def test_search_hides_highest_baseline_ty_warnings(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        attempt = attempt_for(cell)
        proposal = Proposal(
            proposal_id="highest",
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        diagnostic = TyDiagnostic(
            identity="snapshot|demo.py|3|unresolved-reference",
            origin="snapshot",
            path="demo.py",
            line=3,
            column=None,
            code="unresolved-reference",
            severity="major",
            message="Name is not defined",
        )
        process = process_result(exit_code=1, stdout="[]")
        check = TyCheck(process=process, diagnostics=(diagnostic,))
        baseline = StaticBaseline(
            proposal=proposal,
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=baseline.digest,
        )
        test_process = process_result(stderr="1 failed, 2 passed")
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=test_process),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=test_process,
        )
        report = incomplete_report(
            "BASELINE_REJECTION",
            cell_results=(
                BaselineRejection(
                    attempt=attempt,
                    failure=failure,
                    static_baseline=baseline,
                    evaluation=evaluation,
                ),
            ),
        )
        terminal, stdout, stderr = presenter()

        exit_code = terminal.render_search((report,))

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
            "search stopped at [baseline][highest][testing]\n"
            "The full test command failed for this version combination.\n"
            f"-> run `pf diagnose demo --failure {failure.failure_id}` for more information.\n"
            "✗ Search stopped · highest-version baseline did not pass · 1 report written\n"
        )


class TestExplainRendering:
    def test_explain_separates_reports_and_marks_an_empty_projection(
        self,
    ) -> None:
        declaration = requirement_declaration(
            "demo:dependencies:foo",
            name="foo",
            raw="foo>=1",
        )
        report = incomplete_report(
            "MISSING_CELL",
            declarations=(declaration,),
            projections=(
                ProjectionEvidence(
                    declaration_id=declaration.declaration_id,
                    floors=(),
                    projected_requirements=(),
                    representable=True,
                ),
            ),
        )
        terminal, stdout, _ = presenter()

        terminal.render_explain((report, report))

        rendered = stdout.getvalue()
        assert rendered.count("demo · package-floor.json") == 2
        assert rendered.count("no applicable floor") == 2

    def test_explain_renders_the_complete_report_next_action(self) -> None:
        declaration = requirement_declaration(
            "demo:dependencies:foo",
            name="foo",
            raw="foo>=1",
        )
        report = replace(
            incomplete_report(
                declarations=(declaration,),
                projections=(
                    ProjectionEvidence(
                        declaration_id=declaration.declaration_id,
                        floors=(),
                        projected_requirements=("foo>=1",),
                        representable=True,
                    ),
                ),
            ),
            result=CompleteReportResult(status="complete"),
        )
        terminal, stdout, _ = presenter()

        terminal.render_explain((report,))

        rendered = stdout.getvalue()
        assert "Apply: authorized by this report" in rendered
        assert "1 dependency declaration have verified floors" in rendered
        assert "Next: pf apply demo" in rendered

    def test_explain_renders_report_strings_as_literal_text(self) -> None:
        malicious = "[link=https://evil.example]foo>=1[/link]"
        report = incomplete_report(
            "MISSING_CELL",
            declarations=(
                requirement_declaration(
                    "demo:dependencies:foo",
                    name="foo",
                    raw=malicious,
                ),
            ),
        )
        stdout = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=True),
            stderr=Console(file=StringIO(), force_terminal=True),
        )

        assert terminal.render_explain((report,)) == 0

        rendered = stdout.getvalue()
        assert malicious in rendered
        assert "\x1b]8;" not in rendered

    def test_explain_renders_incomplete_reasons_and_projection_requirements(
        self,
    ) -> None:
        report = incomplete_report(
            "MISSING_CELL",
            declarations=(
                requirement_declaration(
                    "demo:dependencies:foo",
                    name="foo",
                    raw="foo>=1",
                ),
                requirement_declaration(
                    "demo:dependencies:bar",
                    name="bar",
                    raw="bar>=2",
                ),
            ),
            projections=(
                ProjectionEvidence(
                    declaration_id="demo:dependencies:foo",
                    floors=(),
                    projected_requirements=("foo>=1",),
                    representable=True,
                ),
                ProjectionEvidence(
                    declaration_id="demo:dependencies:bar",
                    floors=(),
                    projected_requirements=(),
                    representable=False,
                ),
            ),
        )
        terminal, stdout, _ = presenter()

        exit_code = terminal.render_explain((report,))

        assert exit_code == 0
        rendered = stdout.getvalue()
        assert "demo · package-floor.json" in rendered
        assert "Status: incomplete" in rendered
        assert "Apply: not authorized by this report" in rendered
        assert "foo>=1" in rendered
        assert "projection blocked" in rendered
        assert "Summary: report is incomplete and cannot be applied." in rendered
        assert "Apply: ready" not in rendered
        assert "reasons: MISSING_CELL" not in rendered
        assert "demo:dependencies:foo" not in rendered
        assert "demo:dependencies:bar" not in rendered

    def test_explain_does_not_silently_use_declaration_digest(self) -> None:
        digest = "a" * 64
        report = incomplete_report(
            "MISSING_CELL",
            projections=(
                ProjectionEvidence(
                    declaration_id=digest,
                    floors=(),
                    projected_requirements=("foo>=1",),
                    representable=True,
                ),
            ),
        )
        terminal, stdout, _ = presenter()

        with pytest.raises(
            ConfigurationError,
            match="report projection is missing its requirement declaration",
        ):
            terminal.render_explain((report,))

        assert digest not in stdout.getvalue()

    def test_explain_hides_static_history_and_renders_the_final_search_conclusion(
        self,
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        baseline_attempt = attempt_for(cell)
        baseline_proposal = Proposal(
            proposal_id="highest",
            attempt_id=baseline_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        candidate_vector = (VersionPin(name="demo-dep", version="1"),)
        candidate_attempt = attempt_for(
            cell,
            resolution="exact-vector",
            vector=candidate_vector,
        )
        proposal = Proposal(
            proposal_id="candidate",
            attempt_id=candidate_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=candidate_vector,
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        existing = TyDiagnostic(
            identity="snapshot|demo.py|1|1|existing-error",
            origin="snapshot",
            path="demo.py",
            line=1,
            column=1,
            code="existing-error",
            severity="major",
            message="existing project error",
        )
        increment = TyDiagnostic(
            identity="snapshot|demo.py|2|1|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=2,
            column=1,
            code="dependency-regression",
            severity="major",
            message="dependency API is unavailable",
        )
        process = process_result(stdout="[]")
        baseline = StaticBaseline(
            proposal=baseline_proposal,
            ty=TyCheck(process=process, diagnostics=(existing,)),
            digest=ty_diagnostic_digest((existing,)),
        )
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=(existing, increment)),
            baseline_digest=baseline.digest,
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=general_classifications(increment),
        )
        runtime_failure = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )
        rejection = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=candidate_attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=runtime_failure.test.process,
        )
        baseline_static = StaticUnchangedEvaluation(
            proposal=baseline_proposal,
            ty=baseline.ty,
            baseline_digest=baseline.digest,
        )
        failure = CellSearchFailure(
            reason="NO_PASS_IN_SEARCH_SPACE",
            cell=cell,
            phase="runtime-search",
            baseline_attempt=baseline_attempt,
            static_baseline=baseline,
            baseline=PassEvaluation(
                proposal=baseline_proposal,
                static=baseline_static,
                test=TestPass(process=process.model_copy(update={"exit_code": 0})),
            ),
            failure_records=(rejection,),
            coordinate_failure=CoordinateFailure(
                status="NO_PASS_IN_SEARCH_SPACE",
                observations=(
                    ProbeObservation(
                        dependency="demo-dep",
                        candidate_version="1",
                        vector=candidate_vector,
                        evidence=ProbeRejection(
                            attempt=candidate_attempt,
                            proposal_id=proposal.proposal_id,
                            failure_id=rejection.failure_id,
                            cause="TEST_FAILURE",
                            evaluation=runtime_failure,
                        ),
                    ),
                ),
            ),
        )
        report = incomplete_report(
            "NO_PASS_IN_SEARCH_SPACE",
            cell_results=(failure,),
        )
        terminal, stdout, _ = presenter()

        exit_code = terminal.render_explain((report,))

        assert exit_code == 0
        rendered = stdout.getvalue()
        assert "Apply: not authorized by this report" in rendered
        assert "configured search space was fully evaluated" in rendered
        assert "no compatible version" in rendered
        assert rejection.failure_id not in rendered
        assert "What happened:" not in rendered
        assert "ty baseline" not in rendered
        assert "demo.py" not in rendered
        assert "dependency-regression" not in rendered
        assert "NO_PASS_IN_SEARCH_SPACE" not in rendered
        assert "Apply: ready" not in rendered

    def test_explain_does_not_render_large_static_diagnostic_history(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        baseline_attempt = attempt_for(cell)
        baseline_proposal = Proposal(
            proposal_id="highest",
            attempt_id=baseline_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        candidate_vector = (VersionPin(name="demo-dep", version="1"),)
        candidate_attempt = attempt_for(
            cell,
            resolution="exact-vector",
            vector=candidate_vector,
        )
        proposal = Proposal(
            proposal_id="candidate",
            attempt_id=candidate_attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=candidate_vector,
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        existing = TyDiagnostic(
            identity="snapshot|demo.py|1|1|existing-error",
            origin="snapshot",
            path="demo.py",
            line=1,
            column=1,
            code="existing-error",
            severity="major",
            message="existing project error",
        )
        process = process_result(stdout="[]")
        baseline = StaticBaseline(
            proposal=baseline_proposal,
            ty=TyCheck(process=process, diagnostics=(existing,)),
            digest=ty_diagnostic_digest((existing,)),
        )
        repeated = TyDiagnostic(
            identity="snapshot|demo.py|2|1|dependency-regression",
            origin="snapshot",
            path="demo.py",
            line=2,
            column=1,
            code="dependency-regression",
            severity="major",
            message="dependency API is unavailable",
        )
        extras = tuple(
            TyDiagnostic(
                identity=f"snapshot|demo.py|{index}|1|extra-{index}",
                origin="snapshot",
                path="demo.py",
                line=index,
                column=1,
                code=f"extra-{index}",
                severity="major",
                message=f"extra diagnostic {index}",
            )
            for index in range(3, 14)
        )
        incremental: tuple[TyDiagnostic, ...] = tuple(
            sorted(
                (repeated, repeated, repeated, *extras),
                key=lambda item: item.identity,
            )
        )
        diagnostics: tuple[TyDiagnostic, ...] = tuple(
            sorted((existing, *incremental), key=lambda item: item.identity)
        )
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=diagnostics),
            baseline_digest=baseline.digest,
            incremental=incremental,
            static_fingerprint=static_fingerprint(
                tuple(item.identity for item in incremental)
            ),
            classifications=general_classifications(*incremental),
        )
        runtime_failure = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=process),
        )
        rejection = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=candidate_attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=runtime_failure.test.process,
        )
        baseline_static = StaticUnchangedEvaluation(
            proposal=baseline_proposal,
            ty=baseline.ty,
            baseline_digest=baseline.digest,
        )
        failure = CellSearchFailure(
            reason="NO_PASS_IN_SEARCH_SPACE",
            cell=cell,
            phase="runtime-search",
            baseline_attempt=baseline_attempt,
            static_baseline=baseline,
            baseline=PassEvaluation(
                proposal=baseline_proposal,
                static=baseline_static,
                test=TestPass(process=process.model_copy(update={"exit_code": 0})),
            ),
            failure_records=(rejection,),
            coordinate_failure=CoordinateFailure(
                status="NO_PASS_IN_SEARCH_SPACE",
                observations=(
                    ProbeObservation(
                        dependency="demo-dep",
                        candidate_version="1",
                        vector=candidate_vector,
                        evidence=ProbeRejection(
                            attempt=candidate_attempt,
                            proposal_id=proposal.proposal_id,
                            failure_id=rejection.failure_id,
                            cause="TEST_FAILURE",
                            evaluation=runtime_failure,
                        ),
                    ),
                ),
            ),
        )
        terminal, stdout, _ = presenter()

        terminal.render_explain(
            (incomplete_report("NO_PASS_IN_SEARCH_SPACE", cell_results=(failure,)),)
        )

        rendered = stdout.getvalue()
        assert "configured search space was fully evaluated" in rendered
        assert "no compatible version" in rendered
        assert "×3" not in rendered
        assert "extra diagnostic 3" not in rendered
        assert "more unique diagnostics" not in rendered
        assert "extra diagnostic 9" not in rendered
        assert "pf diagnose demo" not in rendered

    @pytest.mark.parametrize("width", (56, 80, 120))
    def test_explain_keeps_required_fields_readable_at_common_widths(
        self, width: int
    ) -> None:
        report = incomplete_report("MISSING_CELL")
        stdout = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(
                file=stdout,
                force_terminal=True,
                color_system=None,
                width=width,
            ),
            stderr=Console(
                file=StringIO(),
                force_terminal=True,
                color_system=None,
                width=width,
            ),
        )

        terminal.render_explain((report,))

        rendered = stdout.getvalue()
        assert "demo · package-floor.json" in rendered
        assert "Status: incomplete" in rendered
        assert "Apply: not authorized by this report" in rendered
        assert "Summary:" in rendered
        for line in rendered.splitlines():
            assert len(line) <= width

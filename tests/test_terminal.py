from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Literal

import pytest
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
    CellFailureScope,
    CellMatrixEvent,
    CheckCompatibilityFailure,
    CheckIndeterminate,
    CheckPass,
    PassEvaluation,
    ProcessEvent,
    ProcessResult,
    ProcessSpec,
    ProgressEvent,
    FailureDetail,
    FailureCause,
    HighestVersionPass,
    SearchFailureEvent,
    SmokeBaselineRejection,
    SmokePass,
    SmokeIndeterminate,
    StaticBaseline,
    StaticFailEvaluation,
    StaticPassEvaluation,
    StatusEvent,
    TestFail,
    TestFailEvaluation,
    TestPass,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, Proposal, SourceSnapshotIdentity, VersionPin
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CoordinateFailure,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectionEvidence,
    ProbeObservation,
    ProbeRejection,
    report_generation_id,
)
from pf.terminal import PF_THEME, TerminalPresenter

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def visible(text: str) -> str:
    return _ANSI.sub("", text)


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
        stdout_summary=stdout,
        stderr_summary=stderr,
        stdout_tail=stdout,
        stderr_tail=stderr,
        timed_out=timed_out,
        start_error=start_error,
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
) -> PackageFloorReportV1:
    generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
    package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
    snapshot = SourceSnapshotIdentity(digest="snapshot", entries=())
    target_cells = tuple(result.cell for result in cell_results)
    return PackageFloorReportV1(
        report_generation_id=report_generation_id(
            generator=generator,
            package=package,
            source_snapshot=snapshot,
            policy_identity="policy",
            requirement_declarations=(),
            target_cells=target_cells,
        ),
        generator=generator,
        package=package,
        source_snapshot=snapshot,
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        target_cells=target_cells,
        cell_results=cell_results,
        projection_evidence=projections,
        result=IncompleteReportResult(reasons=reasons),
    )


def attempt_for(
    cell: Cell,
    *,
    resolution: Literal["highest", "exact-vector"] = "highest",
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


def test_configuration_error_uses_stderr_without_terminal_escape_codes() -> None:
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


def test_render_error_lists_known_package_candidates() -> None:
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
        "✗ configuration: unknown package selection: other\n"
        "Known packages: alpha, beta, demo\n"
    )


def test_render_error_truncates_known_package_candidates_after_ten() -> None:
    terminal, _, stderr = presenter()
    names = tuple(f"pkg{index:02d}" for index in range(12))

    terminal.render_error(
        ConfigurationError("unknown package selection: other", candidates=names)
    )

    assert stderr.getvalue() == (
        "✗ configuration: unknown package selection: other\n"
        "Known packages: pkg00, pkg01, pkg02, pkg03, pkg04, pkg05, pkg06, "
        "pkg07, pkg08, pkg09, ... and 2 more\n"
    )


def test_infrastructure_error_prints_the_captured_detail() -> None:
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


def test_render_error_does_not_claim_in_progress_apply_succeeded() -> None:
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
    assert "✗ no-applicable-floor: cannot apply an incomplete floor report" in output


def test_render_error_keeps_completed_steps_and_drops_in_progress() -> None:
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
    assert "✓ loaded project\n" in output
    assert "built snapshot" not in output
    assert "✗ configuration: project source snapshot has drifted since search" in output


def test_progress_is_stable_lines_off_tty_and_dynamic_on_tty() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    first = ProgressEvent(
        package="demo",
        cell=cell,
        phase="complete",
        completed=1,
        total=2,
        message="SUCCESS",
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
    assert (
        "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00\n"
        in visible(terminal.getvalue())
    )


def test_completed_cell_log_includes_indented_status_and_diagnostic() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    terminal, stdout, stderr = presenter()

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="static check",
            completed=0,
            total=1,
            message="running",
        )
    )
    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="complete",
            completed=1,
            total=1,
            message="STATIC_FAIL",
            detail="error: Unresolved import 'missing'",
        )
    )

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        "  error: Unresolved import 'missing'\n"
    )


def test_completed_cell_log_collapses_multiline_diagnostics_to_a_summary() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
    )
    terminal, stdout, stderr = presenter()

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="complete",
            completed=1,
            total=1,
            message="BUILD_UNAVAILABLE",
            detail="Failed to build `numpy==1.24.0`\nBecause cmake is missing",
        )
    )

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "✗ [py3.12][x86_64-unknown-linux-gnu][no-extra]\n"
        "  Failed to build `numpy==1.24.0` Because cmake is missing\n"
    )


def test_cell_matrix_summary_lists_count_and_axes() -> None:
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


def tty_task_table(terminal: TerminalPresenter) -> str:
    assert terminal._progress is not None
    rendered = StringIO()
    Console(file=rendered, force_terminal=True, color_system=None, width=120).print(
        terminal._progress.make_tasks_table(terminal._ordered_tasks())
    )
    return rendered.getvalue()


def tty_presenter() -> TerminalPresenter:
    return TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=StringIO(), force_terminal=True),
    )


def test_tty_progress_spins_when_total_is_unknown() -> None:
    terminal = tty_presenter()

    terminal.consume(StatusEvent(message="searching cells"))
    terminal.consume(
        ProcessEvent(
            process_id=1,
            argv=("uv", "pip", "install"),
            state="started",
        )
    )

    table = tty_task_table(terminal)
    assert "searching cells" in table
    assert "uv pip install" not in table
    assert "0/?" not in table
    assert "━" not in table
    terminal.close()


def test_tty_progress_shows_a_bar_when_total_is_known() -> None:
    terminal = tty_presenter()
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )

    terminal.consume(StatusEvent(message="searching cells"))
    assert "0/?" not in tty_task_table(terminal)

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="start",
            completed=1,
            total=3,
            message="running",
        )
    )

    table = tty_task_table(terminal)
    assert "1/3" in table
    assert "━" in table
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" in table
    assert "0/?" not in table
    terminal.close()


def test_tty_cell_rows_use_titles_and_freeze_completed_on_top() -> None:
    cell_a = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    cell_b = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.11",
        extra_surface=("cuda",),
    )
    stderr = StringIO()
    terminal = TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=stderr, force_terminal=True),
    )

    terminal.consume(StatusEvent(message="searching cells"))
    terminal.consume(CellMatrixEvent(cells=(cell_a, cell_b)))
    output = visible(stderr.getvalue())
    assert "✓ selected 2 cells\n" in output
    assert "  python: 3.10, 3.11\n" in output
    assert "  platform: x86_64-unknown-linux-gnu\n" in output
    assert "  extra surfaces: no-extra, cuda\n" in output
    table = tty_task_table(terminal)
    assert "0/2" in table
    assert "searching cells" in table
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" in table
    assert "[py3.11][x86_64-unknown-linux-gnu][cuda]" in table

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_a,
            phase="start",
            completed=0,
            total=2,
            message="running",
        )
    )
    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_b,
            phase="start",
            completed=0,
            total=2,
            message="running",
        )
    )
    terminal.consume(
        ProcessEvent(
            process_id=1,
            argv=("uv", "pip", "install"),
            state="started",
        )
    )
    table = tty_task_table(terminal)
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" in table
    assert "[py3.11][x86_64-unknown-linux-gnu][cuda]" in table
    assert "uv pip install" not in table
    assert "⠋" in table

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_b,
            phase="complete",
            completed=1,
            total=2,
            message="SUCCESS",
        )
    )
    table = tty_task_table(terminal)
    assert "[py3.11][x86_64-unknown-linux-gnu][cuda]" not in table
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" in table
    assert table.rindex("[py3.10][x86_64-unknown-linux-gnu][no-extra]") < table.index(
        "searching cells"
    )
    assert "SUCCESS" not in table
    assert "━" in table
    assert "1/2" in table
    assert (
        "✓ [py3.11][x86_64-unknown-linux-gnu][cuda] 0:00:00\n"
        in visible(stderr.getvalue())
    )

    terminal.close()
    output = visible(stderr.getvalue())
    assert "✓ [py3.11][x86_64-unknown-linux-gnu][cuda] 0:00:00" in output


def test_tty_running_cell_shows_dim_stage_under_the_title() -> None:
    cell_a = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    cell_b = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.11",
        extra_surface=(),
    )
    stderr = StringIO()
    terminal = TerminalPresenter(
        stdout=Console(
            file=StringIO(),
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

    terminal.consume(StatusEvent(message="searching cells"))
    terminal.consume(CellMatrixEvent(cells=(cell_a, cell_b)))
    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_a,
            phase="start",
            completed=0,
            total=2,
            message="running",
        )
    )
    table = tty_task_table(terminal)
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" in table
    assert "start" not in table
    assert "installing dependencies" not in table

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_a,
            phase="installing dependencies",
            completed=0,
            total=2,
            message="running",
        )
    )
    table = tty_task_table(terminal)
    title_at = table.index("[py3.10][x86_64-unknown-linux-gnu][no-extra]")
    stage_at = table.index("installing dependencies")
    assert title_at < stage_at
    assert "0/2" in table

    colored = StringIO()
    assert terminal._progress is not None
    Console(
        file=colored,
        force_terminal=True,
        no_color=False,
        color_system="standard",
        theme=PF_THEME,
        width=120,
    ).print(terminal._progress.make_tasks_table(terminal._ordered_tasks()))
    output = colored.getvalue()
    dim_at = output.index("installing dependencies")
    assert "\x1b[2m" in output[: dim_at + 1]

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_a,
            phase="complete",
            completed=1,
            total=2,
            message="SUCCESS",
        )
    )
    table = tty_task_table(terminal)
    assert "installing dependencies" not in table
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" not in table
    assert "[py3.11][x86_64-unknown-linux-gnu][no-extra]" in table
    terminal.close()
    frozen = visible(stderr.getvalue())
    assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00\n" in frozen


def test_tty_search_and_cell_rows_use_the_same_indent_as_other_stages() -> None:
    cell_a = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    cell_b = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.11",
        extra_surface=(),
    )
    stderr = StringIO()
    terminal = TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=stderr, force_terminal=True),
    )

    terminal.consume(StatusEvent(message="searching cells"))
    terminal.consume(CellMatrixEvent(cells=(cell_a, cell_b)))
    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_a,
            phase="installing dependencies",
            completed=0,
            total=2,
            message="running",
        )
    )

    frozen = visible(stderr.getvalue()).splitlines()
    assert frozen[0] == "✓ selected 2 cells"
    assert frozen[1] == "  python: 3.10, 3.11"
    table_lines = [
        line.rstrip() for line in tty_task_table(terminal).splitlines() if line.strip()
    ]
    search_line = next(line for line in table_lines if "searching cells" in line)
    running_line = next(
        line
        for line in table_lines
        if "[py3.10][x86_64-unknown-linux-gnu][no-extra]" in line
    )
    stage_line = next(line for line in table_lines if "installing dependencies" in line)
    pending_line = next(
        line
        for line in table_lines
        if "[py3.11][x86_64-unknown-linux-gnu][no-extra]" in line
    )
    assert search_line[1] == " "
    assert not search_line.startswith("  searching")
    assert running_line[1] == " "
    assert stage_line.startswith("  installing dependencies")
    assert not stage_line.startswith("   installing")
    assert pending_line.startswith("  [py3.11][x86_64-unknown-linux-gnu][no-extra]")
    assert table_lines[-1].endswith("searching cells") or "searching cells" in table_lines[-1]
    terminal.close()


def test_tty_completed_cell_freezes_into_the_log_immediately() -> None:
    cell_a = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    cell_b = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.11",
        extra_surface=(),
    )
    stderr = StringIO()
    terminal = TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=stderr, force_terminal=True),
    )

    terminal.consume(StatusEvent(message="checking declarations"))
    terminal.consume(CellMatrixEvent(cells=(cell_a, cell_b)))
    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_a,
            phase="complete",
            completed=1,
            total=2,
            message="STATIC_FAIL",
            detail="error: Unresolved import 'missing'",
        )
    )
    table = tty_task_table(terminal)
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" not in table
    assert "[py3.11][x86_64-unknown-linux-gnu][no-extra]" in table
    assert table.rindex("[py3.11][x86_64-unknown-linux-gnu][no-extra]") < table.index(
        "checking declarations"
    )
    frozen = visible(stderr.getvalue())
    assert (
        "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00\n"
        "  error: Unresolved import 'missing'\n"
    ) in frozen

    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell_b,
            phase="complete",
            completed=2,
            total=2,
            message="SUCCESS",
        )
    )
    frozen = visible(stderr.getvalue())
    assert (
        "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00\n"
        "  error: Unresolved import 'missing'\n"
        "✓ [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:00\n"
    ) in frozen
    assert "checked declarations" not in frozen


def test_tty_status_with_total_shows_a_bar() -> None:
    terminal = tty_presenter()

    terminal.consume(StatusEvent(message="applying floors", completed=1, total=2))

    table = tty_task_table(terminal)
    assert "applying floors" in table
    assert "1/2" in table
    assert "━" in table
    terminal.close()


def test_non_tty_hides_process_activity_behind_run_logs() -> None:
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


def test_tty_status_stages_spin_then_complete_in_past_tense() -> None:
    terminal = StringIO()
    presenter = TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=terminal, force_terminal=True),
    )

    presenter.consume(StatusEvent(message="loading project"))
    table = tty_task_table(presenter)
    assert "loading project" in table
    assert "⠋" in table
    assert "━" not in table
    assert terminal.getvalue() == ""

    presenter.consume(StatusEvent(message="building snapshot"))
    assert "✓ loaded project\n" in visible(terminal.getvalue())
    assert "loading project\n" not in visible(terminal.getvalue())
    table = tty_task_table(presenter)
    assert "building snapshot" in table
    assert "⠋" in table

    presenter.consume(StatusEvent(message="searching cells"))
    assert "✓ built snapshot\n" in visible(terminal.getvalue())
    table = tty_task_table(presenter)
    assert "searching cells" in table
    assert "⠋" in table

    presenter.close()
    output = visible(terminal.getvalue())
    assert "✓ loaded project\n" in output
    assert "✓ built snapshot\n" in output
    assert "✓ searched cells\n" not in output
    assert "loading project\n" not in output
    assert "building snapshot\n" not in output
    assert "searching cells\n" not in output


def test_tty_keeps_completed_steps_without_clearing_them() -> None:
    terminal = StringIO()
    presenter = TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=terminal, force_terminal=True),
    )
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )

    presenter.consume(StatusEvent(message="loading project"))
    presenter.consume(StatusEvent(message="building snapshot"))
    presenter.consume(StatusEvent(message="searching cells"))
    presenter.consume(
        ProcessEvent(
            process_id=1,
            argv=("uv", "pip", "install"),
            state="started",
        )
    )
    presenter.consume(
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="complete",
            completed=1,
            total=2,
            message="SUCCESS",
        )
    )

    output = visible(terminal.getvalue())
    assert "✓ loaded project\n" in output
    assert "✓ built snapshot\n" in output
    assert "searching cells\n" not in output
    table = tty_task_table(presenter)
    assert "searching cells" in table
    assert "1/2" in table
    assert "[py3.10][x86_64-unknown-linux-gnu][no-extra]" not in table
    assert "uv pip install" not in table
    assert (
        "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00\n"
        in visible(terminal.getvalue())
    )

    presenter.close()
    output = visible(terminal.getvalue())
    assert "✓ loaded project\n" in output
    assert "✓ built snapshot\n" in output
    assert "✓ searched cells\n" not in output
    assert "✓ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00" in output


def test_status_and_process_activity_are_dynamic_on_tty() -> None:
    terminal = StringIO()
    tty_presenter = TerminalPresenter(
        stdout=Console(file=StringIO(), force_terminal=True),
        stderr=Console(file=terminal, force_terminal=True),
    )

    tty_presenter.consume(StatusEvent(message="checking declarations", package="demo"))
    tty_presenter.consume(
        ProcessEvent(
            process_id=1,
            argv=("uv", "pip", "install"),
            state="started",
        )
    )
    table = tty_task_table(tty_presenter)
    assert "demo checking declarations" in table
    assert "⠋" in table
    assert "uv pip install" not in table
    tty_presenter.consume(
        ProcessEvent(
            process_id=1,
            argv=("uv", "pip", "install"),
            state="finished",
            duration_seconds=1.2,
        )
    )
    tty_presenter.consume(
        ProcessEvent(
            process_id=99,
            argv=("missing",),
            state="finished",
            duration_seconds=0.1,
        )
    )
    tty_presenter.close()

    output = visible(terminal.getvalue())
    assert "checked declarations" not in output
    assert "running: uv pip install\n" not in output
    assert "done (1.2s): uv pip install\n" not in output


def test_tty_completed_status_checkmark_is_green() -> None:
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

    output = terminal.getvalue()
    assert "✓ loaded project" in visible(output)
    check_at = output.index("✓")
    assert "\x1b[32m" in output[: check_at + 1]


def test_tty_matrix_axis_lines_are_dim() -> None:
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
    assert visible(output) == (
        "✓ selected 1 cell\n"
        "  python: 3.10\n"
        "  platform: x86_64-unknown-linux-gnu\n"
        "  extra surfaces: no-extra\n"
    )


def test_tty_completed_cell_log_keeps_dim_status_and_diagnostic() -> None:
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
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="static check",
            completed=0,
            total=1,
            message="running",
        )
    )
    presenter.consume(
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="complete",
            completed=1,
            total=1,
            message="STATIC_FAIL",
            detail="error: Unresolved import 'missing'",
        )
    )

    output = terminal.getvalue()
    plain = visible(output)
    assert (
        "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:00\n"
        "  error: Unresolved import 'missing'\n"
    ) in plain
    assert "STATIC_FAIL" not in plain
    title_at = output.index("[py3.10]")
    assert "31" in output[:title_at]
    detail_at = output.rindex("error: Unresolved import")
    assert "\x1b[2m" in output[: detail_at + 1]


def test_tty_failed_progress_uses_a_red_cross() -> None:
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
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="complete",
            completed=1,
            total=1,
            message="STATIC_FAIL",
        )
    )

    output = terminal.getvalue()
    plain = visible(output)
    assert "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
    assert "STATIC_FAIL" not in plain
    assert "checked declarations" not in plain
    cross_at = output.index("✗")
    assert "31" in output[: cross_at + 1]


def test_tty_warning_progress_uses_a_warning_icon() -> None:
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
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="complete",
            completed=1,
            total=1,
            message="NO_PASS_IN_SEARCH_SPACE",
        )
    )

    output = terminal.getvalue()
    plain = visible(output)
    assert "⚠ [py3.10][x86_64-unknown-linux-gnu][no-extra]" in plain
    assert "NO_PASS_IN_SEARCH_SPACE" not in plain
    assert "searched cells" not in plain
    warn_at = output.index("⚠")
    assert "33" in output[: warn_at + 1]


@pytest.mark.parametrize(
    ("result", "expected_exit", "message"),
    (
        (
            CheckCompatibilityFailure(evaluations=()),
            1,
            "✗ check failed: current declarations are incompatible",
        ),
        (
            CheckIndeterminate(
                failure=ToolFailure(
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
            "✗ check indeterminate: The operation timed out, so compatibility is unknown.",
        ),
    ),
)
def test_check_failures_have_stable_exit_codes(
    result: CheckCompatibilityFailure | CheckIndeterminate,
    expected_exit: int,
    message: str,
) -> None:
    terminal, stdout, stderr = presenter()

    exit_code = terminal.render_check(result)

    assert exit_code == expected_exit
    assert stdout.getvalue() == ""
    assert message in stderr.getvalue()


def test_check_indeterminate_prints_a_short_reason_and_log_link(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    logs = RunLogStore(root=tmp_path, run_id="test-run")
    process = process_result(
        stderr=("No solution found when resolving dependencies:\nbecause tomli==2.0.0"),
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
            failure=ToolFailure(
                cause="RESOLUTION_CONFLICT",
                stage="install-harness",
                process=process,
            )
        )
    )

    assert exit_code == 4
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "✗ check indeterminate: This version combination has conflicting dependency requirements and cannot be installed. (harness)\n"
        "  No solution found when resolving dependencies: because tomli==2.0.0\n"
        "  details: .pf/logs/test-run/process-0001.log\n"
    )


def test_tty_process_log_path_is_a_local_file_hyperlink(tmp_path: Path) -> None:
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
            failure=ToolFailure(
                cause="TOOL_FAILURE",
                stage="test",
                process=process,
            )
        )
    )

    assert "\x1b]8;" in stderr.getvalue()
    assert path.resolve().as_uri() in stderr.getvalue()
    assert ".pf/logs/linked-run/process-0001.log" in visible(stderr.getvalue())


def test_smoke_test_failure_prints_dynamic_summary_and_log_link(
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
    static = StaticPassEvaluation(
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
        test=TestFail(process=test_process),
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
        "  The full test command failed for this version combination.\n"
        "  The highest-version baseline did not pass, so PF did not start the floor search for this cell.\n"
        f"  Diagnose: pf diagnose demo --failure {failure.failure_id}\n"
        "  details: .pf/logs/smoke-run/process-0002.log\n"
    )


def test_search_candidate_diagnostics_use_stage_summaries_and_log_links(
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
    static = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(process=static_process, diagnostics=(increment,)),
        baseline_digest=ty_diagnostic_digest(()),
        incremental=(increment,),
    )
    static_pass = StaticPassEvaluation(
        proposal=proposal,
        ty=TyCheck(process=static_process, diagnostics=()),
        baseline_digest=ty_diagnostic_digest(()),
    )
    dynamic_process = process_result(stderr="1 failed\n2 passed")
    dynamic = TestFailEvaluation(
        proposal=proposal,
        static=static_pass,
        test=TestFail(process=dynamic_process),
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
    static_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="STATIC_REGRESSION",
        stage="ty",
        process=static_process,
    )
    dynamic_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="TEST_FAILURE",
        stage="test",
        process=dynamic_process,
    )
    install_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause=install.cause,
        stage=install.stage,
        process=install.process,
    )
    terminal.consume(
        SearchFailureEvent(cell=cell, failure=static_failure, evaluation=static)
    )
    terminal.consume(
        SearchFailureEvent(cell=cell, failure=dynamic_failure, evaluation=dynamic)
    )
    terminal.consume(SearchFailureEvent(cell=cell, failure=install_failure))

    exit_code = terminal.render_search((incomplete_report("NO_PASS_IN_SEARCH_SPACE"),))

    output = stderr.getvalue()
    assert exit_code == 2
    assert "demo.py:4:2 [bad-argument-type] argument has the wrong type" in output
    assert "The full test command failed for this version combination." in output
    assert "test dependencies cannot be installed" in output
    assert "RESOLUTION_CONFLICT" not in output
    assert output.count("details: .pf/logs/search-run/") == 3


@pytest.mark.parametrize(
    ("adapter_stage", "user_stage"),
    (
        ("install", "install"),
        ("install-harness", "harness"),
        ("ty", "static"),
        ("test", "dynamic"),
    ),
)
def test_smoke_tool_failures_use_stable_user_stage_names(
    adapter_stage: str,
    user_stage: str,
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
        "PF could not complete a verification tool operation reliably."
        in stderr.getvalue()
    )
    assert (
        "PF could not determine whether the highest-version baseline "
        "works, so it stopped this cell."
        in stderr.getvalue()
    )
    assert "this candidate" not in stderr.getvalue()
    assert "TOOL_FAILURE" not in stderr.getvalue()
    assert "BASELINE_INDETERMINATE" not in stderr.getvalue()


def test_smoke_ty_diagnostics_are_warnings_with_one_line_summaries(
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
    static = StaticPassEvaluation(
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
    assert stdout.getvalue() == "✓ smoke passed (1 cells)\n"
    assert stderr.getvalue() == (
        "⚠ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        "  src/demo.py:4:7 [invalid-type] Expected str, found int\n"
        "  details: .pf/logs/ty-run/process-0003.log\n"
    )


def test_check_reuses_ty_warning_summaries() -> None:
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
    static = StaticPassEvaluation(
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
                    test=TestPass(process=process.model_copy(update={"exit_code": 0})),
                ),
            )
        )
    )

    assert exit_code == 0
    assert stdout.getvalue() == "✓ check passed (1 cells)\n"
    assert stderr.getvalue() == (
        "⚠ [py3.11][x86_64-unknown-linux-gnu][no-extra]\n"
        "  site-packages/demo.pyi [invalid-return-type] Returned int instead of str\n"
    )


def test_check_static_failure_summarizes_only_incremental_diagnostics() -> None:
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
    static = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(process=process, diagnostics=(existing, increment)),
        baseline_digest=ty_diagnostic_digest((existing,)),
        incremental=(increment,),
    )
    terminal, stdout, stderr = presenter()

    exit_code = terminal.render_check(CheckCompatibilityFailure(evaluations=(static,)))

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "✗ [py3.11][x86_64-unknown-linux-gnu][no-extra]\n"
        "  demo.py:9:2 [dependency-regression] new dependency regression\n"
        "✗ check failed: current declarations are incompatible\n"
    )


def test_check_does_not_repeat_diagnostics_already_frozen_from_progress() -> None:
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
    static = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(process=process, diagnostics=(increment,)),
        baseline_digest=ty_diagnostic_digest(()),
        incremental=(increment,),
    )
    terminal, stdout, stderr = presenter()
    terminal.consume(
        ProgressEvent(
            package="demo",
            cell=cell,
            phase="complete",
            completed=1,
            total=1,
            message="STATIC_FAIL",
            diagnostics=(increment,),
            process=process,
        )
    )

    exit_code = terminal.render_check(CheckCompatibilityFailure(evaluations=(static,)))

    assert exit_code == 1
    assert stdout.getvalue() == ""
    output = stderr.getvalue()
    assert output.count("demo.py:9:2 [dependency-regression]") == 1
    assert "STATIC_FAIL" not in output
    assert "ty: 1 new diagnostic" not in output
    assert output.endswith(
        "✗ check failed: current declarations are incompatible\n"
    )


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
    reasons: tuple[str, ...],
    expected_exit: int,
) -> None:
    terminal, stdout, stderr = presenter()

    exit_code = terminal.render_search((incomplete_report(*reasons),))

    assert exit_code == expected_exit
    assert stdout.getvalue() == "search completed (1 reports)\n"
    expected_stderr: dict[tuple[str, ...], str] = {
        (): "",
        ("BASELINE_REJECTION",): "",
        ("INDETERMINATE",): "",
        ("BASELINE_REJECTION", "INDETERMINATE"): "",
        ("NO_PASS_IN_SEARCH_SPACE",): "⚠ NO_PASS_IN_SEARCH_SPACE\n",
    }
    assert stderr.getvalue() == expected_stderr[reasons]


def test_search_baseline_rejection_prints_user_guidance() -> None:
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
        stage="install-harness",
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
    assert stdout.getvalue() == "search completed (1 reports)\n"
    assert stderr.getvalue() == (
        "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        "  The test dependencies cannot be installed without changing the versions being checked.\n"
        "  The highest-version baseline did not pass, so PF did not start the floor search for this cell.\n"
        f"  Diagnose: pf diagnose demo --failure {failure.failure_id}\n"
    )


def test_search_infra_failure_prints_message_detail_without_a_process() -> None:
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
    assert stdout.getvalue() == "search completed (1 reports)\n"
    assert stderr.getvalue() == (
        "! [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        "  PF could not reach or read a configured package source.\n"
        "  PF could not obtain the information needed to start or continue this cell.\n"
        f"  Diagnose: pf diagnose demo --failure {terminal_result.failure_id}\n"
    )


def test_search_probe_indeterminate_prints_candidate_unknown_impact() -> None:
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
    assert stdout.getvalue() == "search completed (1 reports)\n"
    assert stderr.getvalue() == (
        "! [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        "  The operation timed out, so compatibility is unknown.\n"
        "  PF could not determine whether this candidate works, so it stopped this cell.\n"
        f"  Diagnose: pf diagnose demo --failure {failure.failure_id}\n"
    )


def test_search_reuses_highest_baseline_ty_warning_summaries() -> None:
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
    static = StaticPassEvaluation(
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
    assert stdout.getvalue() == "search completed (1 reports)\n"
    assert stderr.getvalue() == (
        "✗ [py3.10][x86_64-unknown-linux-gnu][no-extra]\n"
        "  demo.py:3 [unresolved-reference] Name is not defined\n"
        "  The full test command failed for this version combination.\n"
        "  The highest-version baseline did not pass, so PF did not start the floor search for this cell.\n"
        f"  Diagnose: pf diagnose demo --failure {failure.failure_id}\n"
    )


def test_explain_renders_incomplete_reasons_and_projection_requirements() -> None:
    report = incomplete_report(
        "MISSING_CELL",
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
    assert stdout.getvalue() == (
        "demo: incomplete\n"
        "  reasons: MISSING_CELL\n"
        "  demo:dependencies:foo: foo>=1\n"
        "  demo:dependencies:bar: none\n"
    )


def test_explain_distinguishes_baseline_diagnostics_from_static_increments() -> None:
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
    candidate_attempt = attempt_for(cell, resolution="exact-vector", vector=())
    proposal = Proposal(
        proposal_id="candidate",
        attempt_id=candidate_attempt.attempt_id,
        snapshot_digest="snapshot",
        cell=cell,
        managed_vector=(),
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
    static = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(process=process, diagnostics=(existing, increment)),
        baseline_digest=baseline.digest,
        incremental=(increment,),
    )
    rejection = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=candidate_attempt),
        cause="STATIC_REGRESSION",
        stage="ty",
        process=process,
    )
    baseline_static = StaticPassEvaluation(
        proposal=baseline_proposal,
        ty=baseline.ty,
        baseline_digest=baseline.digest,
    )
    failure = CellSearchFailure(
        reason="NO_PASS_IN_SEARCH_SPACE",
        cell=cell,
        phase="static-search",
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
                    vector=(),
                    evidence=ProbeRejection(
                        attempt=candidate_attempt,
                        proposal_id=proposal.proposal_id,
                        failure_id=rejection.failure_id,
                        cause="STATIC_REGRESSION",
                        evaluation=static,
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
    assert stdout.getvalue() == (
        "demo: incomplete\n"
        "  reasons: NO_PASS_IN_SEARCH_SPACE\n"
        "  This version combination introduces new type-checking diagnostics.\n"
        f"    Diagnose: pf diagnose demo --failure {rejection.failure_id}\n"
        "  [py3.10][x86_64-unknown-linux-gnu][no-extra] ty baseline: 1 diagnostic\n"
        "    + demo.py:2:1 [dependency-regression] dependency API is unavailable\n"
    )

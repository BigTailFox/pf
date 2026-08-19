from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from pf.errors import ConfigurationError, InfrastructureError
from pf.schemas.evaluation import (
    CheckCompatibilityFailure,
    CheckIndeterminate,
    ProcessEvent,
    ProcessResult,
    ProgressEvent,
    StatusEvent,
    ToolFailure,
)
from pf.schemas.project import Cell, SourceSnapshotIdentity
from pf.schemas.report import (
    CellFailure,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectionEvidence,
)
from pf.terminal import TerminalPresenter


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
    cell_results: tuple[CellFailure, ...] = (),
) -> PackageFloorReportV1:
    return PackageFloorReportV1(
        generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
        package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
        source_snapshot=SourceSnapshotIdentity(digest="snapshot", entries=()),
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        cell_results=cell_results,
        projection_evidence=projections,
        result=IncompleteReportResult(reasons=reasons),
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
    assert stderr.getvalue() == "configuration: unknown key: surprise\n"
    assert "\x1b[" not in stderr.getvalue()


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
        "infrastructure: uv could not list available Python versions\n"
        "uv: failed to execute 'uv python list'\n"
    )


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

    assert plain.getvalue() == (
        "[1/2] demo 3.10 x86_64-unknown-linux-gnu SUCCESS\n"
    )
    assert "[1/2] demo 3.10 x86_64-unknown-linux-gnu SUCCESS" not in terminal.getvalue()
    tty_presenter.close()


def tty_task_table(terminal: TerminalPresenter) -> str:
    assert terminal._progress is not None
    rendered = StringIO()
    Console(file=rendered, force_terminal=True, color_system=None, width=120).print(
        terminal._progress.make_tasks_table(terminal._progress.tasks)
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
    assert "uv pip install" in table
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
    assert "⠋" not in table
    assert "0/?" not in table
    terminal.close()


def test_tty_status_with_total_shows_a_bar() -> None:
    terminal = tty_presenter()

    terminal.consume(StatusEvent(message="applying floors", completed=1, total=2))

    table = tty_task_table(terminal)
    assert "applying floors" in table
    assert "1/2" in table
    assert "━" in table
    assert "⠋" not in table
    terminal.close()


def test_status_and_process_activity_are_stable_lines_off_tty() -> None:
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
    assert stderr.getvalue() == (
        "loading project\n"
        "running: uv python list --output-format json\n"
        "done (0.4s): uv python list --output-format json\n"
        "done (?): mystery\n"
    )


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

    output = terminal.getvalue()
    assert "checking declarations\n" not in output
    assert "running: uv pip install\n" not in output
    assert "done (1.2s): uv pip install\n" not in output


@pytest.mark.parametrize(
    ("result", "expected_exit", "message"),
    (
        (
            CheckCompatibilityFailure(evaluations=()),
            1,
            "current declarations are incompatible",
        ),
        (
            CheckIndeterminate(
                failure=ToolFailure(
                    status="TIMEOUT",
                    stage="test",
                    process=process_result(
                        stderr="timeout",
                        timed_out=True,
                        start_error="timeout",
                    ),
                )
            ),
            4,
            "check indeterminate: TIMEOUT",
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


def test_check_indeterminate_prints_the_process_diagnostic() -> None:
    terminal, stdout, stderr = presenter()

    exit_code = terminal.render_check(
        CheckIndeterminate(
            failure=ToolFailure(
                status="UNRESOLVABLE",
                stage="install-harness",
                process=process_result(
                    stderr=(
                        "No solution found when resolving dependencies:\n"
                        "because tomli==2.0.0"
                    ),
                ),
            )
        )
    )

    assert exit_code == 4
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "check indeterminate: UNRESOLVABLE (install-harness)\n"
        "No solution found when resolving dependencies:\n"
        "because tomli==2.0.0\n"
    )


@pytest.mark.parametrize(
    ("reasons", "expected_exit"),
    (
        ((), 0),
        (("BASELINE_FAILED",), 1),
        (("TIMEOUT",), 4),
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
    if reasons == ("TIMEOUT",):
        assert stderr.getvalue() == "TIMEOUT\n"
    else:
        assert stderr.getvalue() == ""


def test_search_infra_failure_prints_cell_and_process_diagnostic() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    report = incomplete_report(
        "UNRESOLVABLE",
        cell_results=(
            CellFailure(
                status="UNRESOLVABLE",
                cell=cell,
                phase="baseline-prepare",
                failure=ToolFailure(
                    status="UNRESOLVABLE",
                    stage="install-harness",
                    process=process_result(
                        stderr="No solution found when resolving dependencies",
                    ),
                ),
            ),
        ),
    )
    terminal, stdout, stderr = presenter()

    exit_code = terminal.render_search((report,))

    assert exit_code == 4
    assert stdout.getvalue() == "search completed (1 reports)\n"
    assert stderr.getvalue() == (
        "UNRESOLVABLE (baseline-prepare): demo 3.10 x86_64-unknown-linux-gnu (install-harness)\n"
        "No solution found when resolving dependencies\n"
    )


def test_search_infra_failure_prints_message_detail_without_a_process() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    report = incomplete_report(
        "SOURCE_ERROR",
        cell_results=(
            CellFailure(
                status="SOURCE_ERROR",
                cell=cell,
                phase="candidate-discovery",
                detail="index unavailable",
            ),
        ),
    )
    terminal, stdout, stderr = presenter()

    exit_code = terminal.render_search((report,))

    assert exit_code == 4
    assert stdout.getvalue() == "search completed (1 reports)\n"
    assert stderr.getvalue() == (
        "SOURCE_ERROR (candidate-discovery): demo 3.10 x86_64-unknown-linux-gnu\n"
        "index unavailable\n"
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

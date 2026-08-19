from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from pf.errors import ConfigurationError
from pf.schemas.evaluation import (
    CheckCompatibilityFailure,
    CheckIndeterminate,
    ProcessResult,
    ProgressEvent,
    ToolFailure,
)
from pf.schemas.project import Cell, SourceSnapshotIdentity
from pf.schemas.report import (
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProjectionEvidence,
)
from pf.terminal import TerminalPresenter


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
) -> PackageFloorReportV1:
    return PackageFloorReportV1(
        generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
        package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
        source_snapshot=SourceSnapshotIdentity(digest="snapshot", entries=()),
        policy_identity="policy",
        requirement_declarations=(),
        candidate_snapshots=(),
        cell_results=(),
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
                    process=ProcessResult(
                        exit_code=None,
                        signal=None,
                        duration_seconds=1,
                        stdout_summary="",
                        stderr_summary="timeout",
                        stdout_tail="",
                        stderr_tail="timeout",
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
    assert stderr.getvalue() == ""


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

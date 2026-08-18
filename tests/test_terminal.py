from __future__ import annotations

from io import StringIO

from rich.console import Console

from pf.errors import ConfigurationError
from pf.schemas.evaluation import ProgressEvent
from pf.schemas.project import Cell
from pf.terminal import TerminalPresenter


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
    assert "\r" in terminal.getvalue()

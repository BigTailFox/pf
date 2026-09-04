from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from rich.console import Console, RenderableType
from rich.text import Text

from pf.schemas.evaluation import (
    AttemptFailureScope,
    ConfiguredVerifierFailureAuthority,
    FailureRecord,
    NormalExit,
    ProcessResult,
    Signaled,
    StartFailed,
    TimedOut,
    Unavailable,
    VerifierTerminal,
    VerificationRole,
)
from pf.terminal import _fact_grid, _path_text, _plain_result_card, _result_card
from pf.terminal._presentation import OutcomeKind, cell_title_text

if TYPE_CHECKING:
    from pf.workflow import FailureDiagnosis


class FailureView(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def impact(self) -> str: ...

    @property
    def next_step(self) -> str: ...


class DiagnosePresenter(Protocol):
    stdout: Console

    def close(self, *, abandon_pending: bool = False) -> None: ...

    def failure_presentation(
        self,
        failure: FailureRecord,
        *,
        role: VerificationRole | None = None,
        command: str | None = None,
    ) -> FailureView: ...

    def _print_outcome(
        self,
        kind: OutcomeKind,
        message: str,
        *,
        console: Console | None = None,
    ) -> None: ...


def render(
    presenter: DiagnosePresenter,
    diagnosis: FailureDiagnosis,
    *,
    root: Path,
) -> int:
    presenter.close()
    failure = diagnosis.failure
    presentation = presenter.failure_presentation(
        failure,
        role=diagnosis.verification_role,
        command=diagnosis.command,
    )
    scope = failure.scope
    cell = (
        scope.attempt.identity.cell
        if isinstance(scope, AttemptFailureScope)
        else scope.cell
    )
    kind: OutcomeKind = (
        "failure" if failure.disposition == "REJECTED" else "indeterminate"
    )
    outcome = (
        "rejected candidate"
        if failure.disposition == "REJECTED"
        else "compatibility unknown"
    )
    impact = presentation.impact
    if diagnosis.boundary_role == "predecessor" and failure.disposition == "REJECTED":
        impact += " It helped establish the verified floor."

    source: Text
    if diagnosis.source == "report":
        source_path = diagnosis.source_path
        if source_path is None:
            raise ValueError("report diagnosis requires source_path")
        source = _path_text(
            source_path,
            base=root,
            terminal=presenter.stdout.is_terminal,
        )
    else:
        source = Text(f"latest pf {diagnosis.command or 'search'}")

    if isinstance(scope, AttemptFailureScope):
        identity = scope.attempt.identity
        attempt = scope.attempt.attempt_id
        resolution = identity.requested_resolution
        vector = identity.requested_managed_vector
        vector_text = (
            ", ".join(f"{pin.name}=={pin.version}" for pin in vector)
            if vector is not None
            else "not applicable"
        )
    else:
        attempt = "not available"
        resolution = "not applicable"
        vector_text = "not applicable"

    technical: list[tuple[Text | str, RenderableType]] = [
        ("disposition", Text(failure.disposition)),
        ("cause", Text(failure.cause)),
        ("attempt", Text(attempt)),
        ("resolution", Text(resolution)),
        ("vector", Text(vector_text)),
        ("proposal", Text(diagnosis.proposal_id or "not available")),
        ("boundary", Text(diagnosis.boundary_role or "none")),
    ]
    if failure.process is not None:
        technical.append(("process", Text(_process_terminal(failure.process))))
    elif isinstance(failure.authority, ConfiguredVerifierFailureAuthority):
        technical.append(
            ("process", Text(_verifier_terminal(failure.authority.terminal)))
        )
    if failure.detail is not None:
        technical.extend(
            (
                ("detail code", Text(failure.detail.code)),
                ("detail", Text(_single_line_summary(failure.detail.message))),
            )
        )
    if diagnosis.output_tail:
        tail = diagnosis.output_tail[-3:]
        technical.append(("output", Text("\n".join(tail), style="dim")))
    if diagnosis.log_path is not None:
        technical.append(
            (
                "log",
                _path_text(
                    diagnosis.log_path.as_posix(),
                    base=root,
                    terminal=presenter.stdout.is_terminal,
                ),
            )
        )
    else:
        technical.append(("log", Text("Detailed local log is unavailable.")))

    header = Text.assemble(
        (failure.failure_id, "bold"),
        " · ",
        (outcome, f"reason.{kind} bold"),
    )
    next_action = Text("-> ", style="hint")
    next_action.append(presentation.next_step, style="hint")
    rows: tuple[tuple[RenderableType | None, RenderableType], ...] = (
        (Text("✗" if kind == "failure" else "!", style=kind), header),
        (None, Text()),
        (
            None,
            _fact_grid(
                (
                    ("What happened", Text(presentation.title)),
                    ("Impact", Text(impact)),
                )
            ),
        ),
        (None, next_action),
        (None, Text()),
        (None, Text("Context", style="bold")),
        (
            None,
            _fact_grid(
                (
                    ("package", Text(diagnosis.package, style="bold cyan")),
                    ("cell", cell_title_text(cell)),
                    ("stage", Text(failure.stage)),
                    ("source", source),
                )
            ),
        ),
        (None, Text()),
        (None, Text("Technical details", style="bold")),
        (None, _fact_grid(tuple(technical))),
    )
    presenter.stdout.print(
        _result_card(rows, kind=kind)
        if presenter.stdout.is_terminal
        else _plain_result_card(rows)
    )
    presenter._print_outcome(
        "success",
        f"Diagnosis complete · {failure.failure_id}",
        console=presenter.stdout,
    )
    return 0


def _single_line_summary(value: str) -> str:
    return " ".join(value.split())


def _process_terminal(process: ProcessResult) -> str:
    if process.timed_out:
        return "timed out"
    if process.start_error is not None:
        return "could not start"
    if process.signal is not None:
        return f"terminated by signal {process.signal}"
    assert process.exit_code is not None
    return f"exited {process.exit_code}"


def _verifier_terminal(terminal: VerifierTerminal) -> str:
    if isinstance(terminal, NormalExit):
        return f"exited {terminal.exit_code}"
    if isinstance(terminal, TimedOut):
        return "timed out"
    if isinstance(terminal, StartFailed):
        return "could not start"
    if isinstance(terminal, Signaled):
        return f"terminated by signal {terminal.signal}"
    assert isinstance(terminal, Unavailable)
    return "terminal unavailable"

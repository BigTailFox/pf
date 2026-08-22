from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from rich.console import Console
from rich.text import Text

from pf.schemas.evaluation import (
    AttemptFailureScope,
    FailureRecord,
    ProcessResult,
    VerificationRole,
)

if TYPE_CHECKING:
    from pf.workflow import FailureDiagnosis

_SUMMARY_WIDTH = 240


class FailureView(Protocol):
    title: str
    impact: str
    next_step: str


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


def render(
    presenter: DiagnosePresenter,
    diagnoses: tuple[FailureDiagnosis, ...],
    *,
    root: Path,
) -> int:
    presenter.close()
    if not diagnoses:
        presenter.stdout.print("diagnosed 0 failures")
        return 0
    for index, diagnosis in enumerate(diagnoses):
        if index:
            presenter.stdout.print()
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
        outcome = (
            "The verification attempt was rejected"
            if failure.disposition == "REJECTED"
            else "Compatibility is unknown"
        )
        presenter.stdout.print(f"Failure: {failure.failure_id}")
        presenter.stdout.print(f"Outcome: {outcome}")
        presenter.stdout.print(f"What happened: {presentation.title}")
        presenter.stdout.print(f"Impact: {presentation.impact}")
        presenter.stdout.print(f"Next step: {presentation.next_step}")
        presenter.stdout.print()
        presenter.stdout.print("Context:")
        presenter.stdout.print(f"  package: {diagnosis.package}")
        presenter.stdout.print(
            "  cell: "
            f"py{cell.python_minor} / {cell.target} / "
            f"{_format_extra_surface(cell.extra_surface)}"
        )
        presenter.stdout.print(f"  stage: {failure.stage}")
        source = (
            "package-floor.json"
            if diagnosis.source == "package-floor.json"
            else f"latest pf {diagnosis.command or 'search'}"
        )
        presenter.stdout.print(f"  source: {source}")
        presenter.stdout.print()
        presenter.stdout.print("Technical details:")
        presenter.stdout.print(f"  disposition: {failure.disposition}")
        presenter.stdout.print(f"  cause: {failure.cause}")
        if isinstance(scope, AttemptFailureScope):
            identity = scope.attempt.identity
            presenter.stdout.print(f"  attempt: {scope.attempt.attempt_id}")
            presenter.stdout.print(
                f"  requested resolution: {identity.requested_resolution}"
            )
            vector = identity.requested_managed_vector
            presenter.stdout.print(
                "  requested vector: "
                + (
                    ", ".join(f"{pin.name}=={pin.version}" for pin in vector)
                    if vector is not None
                    else "not applicable"
                )
            )
        else:
            presenter.stdout.print("  attempt: not available")
            presenter.stdout.print("  requested vector: not applicable")
        presenter.stdout.print(
            f"  proposal: {diagnosis.proposal_id or 'not available'}"
        )
        presenter.stdout.print(
            f"  boundary role: {diagnosis.boundary_role or 'none'}"
        )
        if failure.detail is not None:
            presenter.stdout.print(f"  detail code: {failure.detail.code}")
            presenter.stdout.print(
                f"  detail: {_single_line_summary(failure.detail.message)}"
            )
        if failure.process is not None:
            presenter.stdout.print(f"  process: {_process_terminal(failure.process)}")
            output = (
                failure.process.stderr.strip()
                or failure.process.stdout.strip()
                or (failure.process.start_error or "").strip()
            )
            summary = _single_line_summary(output)
            if summary:
                presenter.stdout.print(f"  summary: {summary}")
        if diagnosis.log_path is not None:
            resolved = (root / diagnosis.log_path).resolve()
            presenter.stdout.print(
                Text.assemble(
                    "  log: ",
                    (
                        diagnosis.log_path.as_posix(),
                        f"link {resolved.as_uri()}",
                    ),
                )
            )
        else:
            presenter.stdout.print("  Detailed local log is unavailable.")
    return 0


def _format_extra_surface(surface: tuple[str, ...]) -> str:
    return "default" if not surface else ",".join(surface)


def _single_line_summary(value: str) -> str:
    summary = " ".join(value.split())
    if len(summary) <= _SUMMARY_WIDTH:
        return summary
    return f"{summary[: _SUMMARY_WIDTH - 3]}..."


def _process_terminal(process: ProcessResult) -> str:
    if process.timed_out:
        return "timed out"
    if process.start_error is not None:
        return "could not start"
    if process.signal is not None:
        return f"terminated by signal {process.signal}"
    assert process.exit_code is not None
    return f"exited {process.exit_code}"

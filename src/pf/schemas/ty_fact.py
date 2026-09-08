"""Raw static observations, independent of their consumers and comparisons."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.evaluation import ExecutionTerminal, NormalExit, TyDiagnostic, ProcessObservation, ProcessResult, execution_terminal
from pf.schemas.policy import TyObservationPolicy
from pf.schemas.static import StaticSubject


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _identity(payload: dict) -> str:
    return hashlib.sha256(b"pf:ty-fact:v1\0" + canonical_identity_json(payload)).hexdigest()


class TyCheckFact(FrozenSchema):
    kind: Literal["ty-check"] = "ty-check"
    subject_identity: Digest
    observation_policy_identity: Digest
    terminal: NormalExit
    diagnostics: tuple[TyDiagnostic, ...]

    @model_validator(mode="after")
    def validate_observation(self) -> TyCheckFact:
        if self.terminal.exit_code not in {0, 1}:
            raise ValueError("complete ty observation requires exit 0 or 1")
        order = tuple((item.identity, item.severity, item.message) for item in self.diagnostics)
        if order != tuple(sorted(order)):
            raise ValueError("raw ty diagnostics must be canonically ordered")
        return self

    @property
    def identity(self) -> str:
        return _identity({
            "kind": self.kind, "subject_identity": self.subject_identity,
            "observation_policy_identity": self.observation_policy_identity,
            "terminal": self.terminal.model_dump(mode="json"),
            "diagnostics": [item.identity for item in self.diagnostics],
        })


class TyCheckUnavailable(FrozenSchema):
    kind: Literal["ty-check-unavailable"] = "ty-check-unavailable"
    subject_identity: Digest
    observation_policy_identity: Digest
    reason: Literal["timeout", "start-failed", "signal", "exit-code", "output-incomplete", "invalid-output", "terminal-unavailable"]
    terminal: ExecutionTerminal

    @model_validator(mode="after")
    def validate_failure(self) -> TyCheckUnavailable:
        expected = {
            "timeout": "timed-out", "start-failed": "start-failed",
            "signal": "signaled", "terminal-unavailable": "unavailable",
            "exit-code": "normal-exit", "output-incomplete": "normal-exit",
            "invalid-output": "normal-exit",
        }[self.reason]
        if self.terminal.kind != expected:
            raise ValueError("ty failure reason does not match its terminal")
        if isinstance(self.terminal, NormalExit):
            accepted = self.terminal.exit_code in {0, 1}
            if accepted != (self.reason != "exit-code"):
                raise ValueError("ty failure reason does not match its exit code")
        return self

    @property
    def identity(self) -> str:
        return _identity(self.model_dump(mode="json"))


TyFact = Annotated[Union[TyCheckFact, TyCheckUnavailable], Field(discriminator="kind")]


class TyFactDocument(FrozenSchema):
    """Minimal offline input/fact codec; Run and producer refs live elsewhere.

    A valid document verifies its saved preimage. It neither rechecks files nor
    registers a cache reference or grants any dynamic execution authority.
    """

    subject: StaticSubject
    observation_policy: TyObservationPolicy
    fact: TyFact
    fact_identity: Digest

    @model_validator(mode="after")
    def validate_binding(self) -> TyFactDocument:
        if self.fact.subject_identity != self.subject.identity:
            raise ValueError("ty fact does not match its static subject")
        if self.fact.observation_policy_identity != self.observation_policy.identity:
            raise ValueError("ty fact does not match its observation policy")
        if self.fact_identity != self.fact.identity:
            raise ValueError("ty fact identity does not match its observation")
        return self


def validate_ty_fact_process(observation: TyFactDocument, process: ProcessObservation) -> None:
    """Shared runtime and offline binding to the actual lower process."""
    raw = observation.fact
    if execution_terminal(process) != raw.terminal:
        raise ValueError("ty fact terminal must match its actual process")
    if isinstance(process, ProcessResult):
        complete = process.stdout_complete
        if (isinstance(raw, TyCheckFact) and not complete) or (
            isinstance(raw, TyCheckUnavailable)
            and raw.reason in {"output-incomplete", "invalid-output"}
            and complete != (raw.reason == "invalid-output")
        ):
            raise ValueError("ty fact output completeness mismatch")

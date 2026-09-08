"""Project an actual lower ty outcome into a provenance-independent fact."""

from __future__ import annotations

from pf.schemas.evaluation import NormalExit, ProcessResult, ToolFailure, TyCheck, execution_terminal
from pf.schemas.policy import TyObservationPolicy
from pf.schemas.static import StaticSubject
from pf.schemas.ty_fact import TyCheckFact, TyCheckUnavailable, TyFactDocument


def ty_fact_document(
    subject: StaticSubject, observation_policy: TyObservationPolicy,
    outcome: TyCheck | ToolFailure,
) -> TyFactDocument:
    process = outcome.process
    if process is None:
        raise ValueError("raw ty failure requires an actual process observation")
    terminal = execution_terminal(process)
    fact: TyCheckFact | TyCheckUnavailable
    if isinstance(outcome, TyCheck):
        assert isinstance(terminal, NormalExit)
        fact = TyCheckFact(subject_identity=subject.identity, observation_policy_identity=observation_policy.identity,
                           terminal=terminal, diagnostics=outcome.diagnostics)
    else:
        if outcome.stage != "ty":
            raise ValueError("only an actual ty outcome can produce a raw ty failure")
        if terminal.kind == "timed-out":
            reason = "timeout"
        elif terminal.kind == "start-failed":
            reason = "start-failed"
        elif terminal.kind == "signaled":
            reason = "signal"
        elif terminal.kind == "unavailable":
            reason = "terminal-unavailable"
        elif terminal.exit_code not in {0, 1}:
            reason = "exit-code"
        elif isinstance(process, ProcessResult) and not process.stdout_complete:
            reason = "output-incomplete"
        else:
            reason = "invalid-output"
        fact = TyCheckUnavailable(subject_identity=subject.identity, observation_policy_identity=observation_policy.identity,
                                  reason=reason, terminal=terminal)
    return TyFactDocument(subject=subject, observation_policy=observation_policy, fact=fact, fact_identity=fact.identity)

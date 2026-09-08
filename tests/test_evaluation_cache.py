from __future__ import annotations

from typing import Literal

import pytest

from pf.evaluation import EvaluationCache
from pf.schemas.evaluation import (
    RuntimeEvaluationRun,
    CacheConflict,
    IndeterminateEvaluation,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    Signaled,
    TyCheck,
    TyDiagnostic,
    TimedOut,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
)
from pf.schemas.project import Cell, Proposal


def process(exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def check(exit_code: int = 0) -> TyCheck:
    return TyCheck(process=process(exit_code), diagnostics=())


def diagnostic() -> TyDiagnostic:
    return TyDiagnostic(
        identity="snapshot|demo.py|1|1|invalid-type",
        origin="snapshot",
        path="demo.py",
        line=1,
        column=1,
        code="invalid-type",
        severity="major",
        message="invalid type",
    )


class TestEvaluationCache:
    @pytest.fixture
    def proposal(self) -> Proposal:
        return Proposal(proposal_id="proposal-1", snapshot_digest="snapshot",
                        cell=Cell(package="demo", target="x86_64-unknown-linux-gnu",
                                  python_minor="3.10", extra_surface=()),
                        managed_vector=(), fixed_declaration_ids=(), resolved_graph=(),
                        policy_identity="policy")

    @pytest.mark.parametrize("changed_authority", [False, True])
    def test_dynamic_authority_controls_cache_reuse(self, proposal, changed_authority):
        first = PassEvaluation(proposal=proposal,
                               verifier=VerifierPass(terminal=NormalExit(exit_code=0)))
        second = (VerifierRejectedEvaluation(proposal=proposal,
                                             verifier=VerifierRejected(terminal=NormalExit(exit_code=1)))
                  if changed_authority else PassEvaluation(proposal=proposal,
                                                            verifier=first.verifier))
        first_run = RuntimeEvaluationRun(evaluation=first)
        second_run = RuntimeEvaluationRun(evaluation=second)
        if not changed_authority:
            assert first_run.evaluation.model_dump(mode="json") == second_run.evaluation.model_dump(mode="json")
        cache = EvaluationCache()
        assert cache.get_full(proposal) is None
        assert cache.record_full(first) is first
        stored = cache.record_full(second)
        if changed_authority:
            assert isinstance(stored, CacheConflict)
            assert stored.observed_statuses == ("PASS", "VERIFIER_REJECTED")
        else:
            assert stored is first
        assert cache.get_full(proposal) is first

    @pytest.mark.parametrize("field", ["proposal_id", "policy_identity"])
    def test_distinct_execution_identity_has_distinct_evidence(self, proposal, field):
        cache = EvaluationCache()
        first = PassEvaluation(proposal=proposal,

                               verifier=VerifierPass(terminal=NormalExit(exit_code=0)))
        other = Proposal.model_validate({**proposal.model_dump(), field: "another"})
        second = VerifierRejectedEvaluation(proposal=other,

                                            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)))
        assert cache.record_full(first) is first
        assert cache.get_full(other) is None
        assert cache.record_full(second) is second
        assert cache.get_full(proposal) is first
        assert cache.get_full(other) is second

    def test_same_identity_requires_exact_proposal_facts(self, proposal):
        cache = EvaluationCache()
        first = PassEvaluation(proposal=proposal,

                               verifier=VerifierPass(terminal=NormalExit(exit_code=0)))
        cache.record_full(first)
        inconsistent = Proposal.model_validate({**proposal.model_dump(), "snapshot_digest": "another-snapshot"})
        with pytest.raises(ValueError, match="inconsistent Proposal facts"):
            cache.get_full(inconsistent)

    def test_cache_conflicts_when_rejected_exit_code_changes(self) -> None:
        proposal = Proposal(
            proposal_id="proposal-1",
            snapshot_digest="snapshot",
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        first = VerifierRejectedEvaluation(
            proposal=proposal,

            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        second = first.model_copy(
            update={"verifier": VerifierRejected(terminal=NormalExit(exit_code=4))}
        )
        cache = EvaluationCache()

        assert cache.record_full(first) == first
        conflict = cache.record_full(second)

        assert isinstance(conflict, CacheConflict)
        assert conflict.observed_statuses == (
            "VERIFIER_REJECTED",
            "VERIFIER_REJECTED",
        )
        assert cache.get_full(proposal) == first

    @pytest.mark.parametrize(
        ("first_terminal", "second_terminal", "first_cause", "second_cause"),
        (
            (
                Signaled(signal=9),
                Signaled(signal=15),
                "TOOL_FAILURE",
                "TOOL_FAILURE",
            ),
            (TimedOut(), Signaled(signal=9), "TIMEOUT", "TOOL_FAILURE"),
        ),
        ids=("signal", "terminal-kind"),
    )
    def test_cache_conflicts_when_indeterminate_terminal_facts_change(
        self,
        first_terminal: Signaled | TimedOut,
        second_terminal: Signaled | TimedOut,
        first_cause: Literal["TIMEOUT", "TOOL_FAILURE"],
        second_cause: Literal["TIMEOUT", "TOOL_FAILURE"],
    ) -> None:
        proposal = Proposal(
            proposal_id="proposal-1",
            snapshot_digest="snapshot",
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )

        def evaluation(
            terminal: Signaled | TimedOut,
            cause: Literal["TIMEOUT", "TOOL_FAILURE"],
        ) -> IndeterminateEvaluation:
            return IndeterminateEvaluation(
                proposal=proposal,
                cause=cause,
                verifier=VerifierIndeterminate(
                    terminal=terminal,
                    reason=(
                        "process-timed-out"
                        if isinstance(terminal, TimedOut)
                        else "process-signaled"
                    ),
                ),

            )

        first = evaluation(first_terminal, first_cause)
        second = evaluation(second_terminal, second_cause)
        cache = EvaluationCache()

        assert cache.record_full(first) == first
        conflict = cache.record_full(second)

        assert isinstance(conflict, CacheConflict)
        assert conflict.observed_statuses == ("INDETERMINATE", "INDETERMINATE")

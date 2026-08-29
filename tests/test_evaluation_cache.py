from __future__ import annotations

import pytest

from pf.evaluation import EvaluationCache
from pf.schemas.evaluation import (
    CacheConflict,
    DiagnosticClassification,
    FailureCause,
    IndeterminateEvaluation,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    Signaled,
    StaticUnchangedEvaluation,
    StaticRegressionEvaluation,
    TyCheck,
    TyDiagnostic,
    TimedOut,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
)
from pf.schemas.project import Cell, Proposal
from pf.static_transition import static_fingerprint


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
    def test_cache_separates_static_and_full_and_detects_conflicting_full_results(
        self,
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
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check(),
            baseline_digest="baseline",
            incremental=(),
        )
        passed = PassEvaluation(
            proposal=proposal,
            static=static,
            verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
        )
        failed = VerifierRejectedEvaluation(
            proposal=proposal,
            static=static,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        cache = EvaluationCache()

        cache.record_static(static, baseline_digest="baseline")
        assert cache.get_static("proposal-1", baseline_digest="baseline") == static
        assert cache.get_full("proposal-1", baseline_digest="baseline") is None
        assert cache.record_full(passed, baseline_digest="baseline") == passed
        conflict = cache.record_full(failed, baseline_digest="baseline")

        assert isinstance(conflict, CacheConflict)
        assert conflict.status == "NONDETERMINISTIC"
        assert conflict.observed_statuses == ("PASS", "VERIFIER_REJECTED")
        assert cache.get_full("proposal-1", baseline_digest="baseline") == passed

    def test_cache_reuses_identical_evidence_and_detects_static_conflicts(self) -> None:
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
        passed = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check(),
            baseline_digest="baseline",
            incremental=(),
        )
        increment = diagnostic()
        failed = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process(exit_code=1), diagnostics=(increment,)),
            baseline_digest="baseline",
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=(
                DiagnosticClassification(
                    diagnostic_identity=increment.identity,
                    classification="general",
                    reason_code="test-fixture",
                ),
            ),
        )
        cache = EvaluationCache()

        assert cache.record_static(passed, baseline_digest="baseline") == passed
        assert cache.record_static(passed, baseline_digest="baseline") == passed
        conflict = cache.record_static(failed, baseline_digest="baseline")

        assert isinstance(conflict, CacheConflict)
        assert conflict.observed_statuses == ("STATIC_UNCHANGED", "STATIC_REGRESSION")

        static = passed
        full = PassEvaluation(
            proposal=proposal,
            static=static,
            verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
        )
        assert cache.record_full(full, baseline_digest="baseline") == full
        assert cache.record_full(full, baseline_digest="baseline") == full

    def test_cache_separates_the_same_proposal_under_different_static_baselines(
        self,
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
        first = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check(),
            baseline_digest="baseline-a",
            incremental=(),
        )
        increment = diagnostic()
        second = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process(exit_code=1), diagnostics=(increment,)),
            baseline_digest="baseline-b",
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=(
                DiagnosticClassification(
                    diagnostic_identity=increment.identity,
                    classification="general",
                    reason_code="test-fixture",
                ),
            ),
        )
        first_full = PassEvaluation(
            proposal=proposal,
            static=first,
            verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
        )
        second_full = VerifierRejectedEvaluation(
            proposal=proposal,
            static=second,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        cache = EvaluationCache()

        with pytest.raises(ValueError, match="does not match its cache baseline"):
            cache.record_static(first, baseline_digest="baseline-b")

        cache.record_static(first, baseline_digest="baseline-a")
        cache.record_full(first_full, baseline_digest="baseline-a")

        assert cache.get_static("proposal-1", baseline_digest="baseline-b") is None
        assert cache.get_full("proposal-1", baseline_digest="baseline-b") is None
        assert cache.record_static(second, baseline_digest="baseline-b") == second
        assert (
            cache.record_full(second_full, baseline_digest="baseline-b") == second_full
        )
        assert cache.get_static("proposal-1", baseline_digest="baseline-a") == first
        assert cache.get_full("proposal-1", baseline_digest="baseline-a") == first_full

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
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check(),
            baseline_digest="baseline",
        )
        first = VerifierRejectedEvaluation(
            proposal=proposal,
            static=static,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        second = first.model_copy(
            update={"verifier": VerifierRejected(terminal=NormalExit(exit_code=4))}
        )
        cache = EvaluationCache()

        assert cache.record_full(first, baseline_digest="baseline") == first
        conflict = cache.record_full(second, baseline_digest="baseline")

        assert isinstance(conflict, CacheConflict)
        assert conflict.observed_statuses == (
            "VERIFIER_REJECTED",
            "VERIFIER_REJECTED",
        )
        assert cache.get_full("proposal-1", baseline_digest="baseline") == first

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
        first_cause: FailureCause,
        second_cause: FailureCause,
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
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check(),
            baseline_digest="baseline",
        )

        def evaluation(
            terminal: Signaled | TimedOut,
            cause: FailureCause,
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
                static=static,
            )

        first = evaluation(first_terminal, first_cause)
        second = evaluation(second_terminal, second_cause)
        cache = EvaluationCache()

        assert cache.record_full(first, baseline_digest="baseline") == first
        conflict = cache.record_full(second, baseline_digest="baseline")

        assert isinstance(conflict, CacheConflict)
        assert conflict.observed_statuses == ("INDETERMINATE", "INDETERMINATE")

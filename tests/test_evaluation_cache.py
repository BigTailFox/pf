from __future__ import annotations

from pf.evaluation import EvaluationCache
from pf.schemas.evaluation import (
    CacheConflict,
    PassEvaluation,
    ProcessResult,
    StaticPassEvaluation,
    StaticFailEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    TyCheck,
    TyDiagnostic,
)
from pf.schemas.project import Cell, Proposal


def process(exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="",
        stderr_summary="",
        stdout_tail="",
        stderr_tail="",
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


def test_cache_separates_static_and_full_and_detects_conflicting_full_results() -> None:
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
    static = StaticPassEvaluation(
        proposal=proposal,
        ty=check(),
        baseline_digest="baseline",
        incremental=(),
    )
    passed = PassEvaluation(
        proposal=proposal,
        static=static,
        test=TestPass(process=process()),
    )
    failed = TestFailEvaluation(
        proposal=proposal,
        static=static,
        test=TestFail(process=process(exit_code=1)),
    )
    cache = EvaluationCache()

    cache.record_static(static)
    assert cache.get_static("proposal-1") == static
    assert cache.get_full("proposal-1") is None
    assert cache.record_full(passed) == passed
    conflict = cache.record_full(failed)

    assert isinstance(conflict, CacheConflict)
    assert conflict.status == "NONDETERMINISTIC"
    assert conflict.observed_statuses == ("PASS", "TEST_FAIL")
    assert cache.get_full("proposal-1") == passed


def test_cache_reuses_identical_evidence_and_detects_static_conflicts() -> None:
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
    passed = StaticPassEvaluation(
        proposal=proposal,
        ty=check(),
        baseline_digest="baseline",
        incremental=(),
    )
    increment = diagnostic()
    failed = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(process=process(exit_code=1), diagnostics=(increment,)),
        baseline_digest="baseline",
        incremental=(increment,),
    )
    cache = EvaluationCache()

    assert cache.record_static(passed) == passed
    assert cache.record_static(passed) == passed
    conflict = cache.record_static(failed)

    assert isinstance(conflict, CacheConflict)
    assert conflict.observed_statuses == ("STATIC_PASS", "STATIC_FAIL")

    static = passed
    full = PassEvaluation(
        proposal=proposal,
        static=static,
        test=TestPass(process=process()),
    )
    assert cache.record_full(full) == full
    assert cache.record_full(full) == full

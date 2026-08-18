from __future__ import annotations

from pf.evaluation import EvaluationCache
from pf.schemas.evaluation import (
    CacheConflict,
    PassEvaluation,
    ProcessResult,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    TyPass,
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
    static = StaticPassEvaluation(proposal=proposal, ty=TyPass(process=process()))
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

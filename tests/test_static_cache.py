from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from static_fixtures import (
    scripted_static_request as scripted_static_request,
    static_request as static_request, preparation as preparation,
    static_subject as static_subject, policy as policy,
)
from pf.cancellation import OperationCancelled
from pf.schemas.evaluation import ProcessResult, ToolFailure, TyCheck
from pf.static_cache import CacheMiss, RunTyFactRef, TyCheckCache
from pf.ty_fact import ty_fact_document


class TestRunTyCache:
    @pytest.mark.parametrize("failed", [False, True])
    def test_overlapping_consumers_share_one_actual_terminal(self, preparation, static_subject, policy, failed):
        entered, release = Event(), Event()
        cache = TyCheckCache()
        calls = []
        process = ProcessResult(exit_code=2 if failed else 0, duration_seconds=1)
        outcome = (ToolFailure(cause="TOOL_FAILURE", stage="ty", process=process)
                   if failed else TyCheck(process=process, diagnostics=()))
        document = ty_fact_document(static_subject, policy, outcome)

        def observe(cancellation):
            calls.append(cancellation)
            entered.set()
            assert release.wait(5)
            return document, process

        with ThreadPoolExecutor(max_workers=3) as pool:
            owner = pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True)
            try:
                assert entered.wait(5)
                assert isinstance(cache.lookup(static_subject, policy), CacheMiss)
                waiters = [pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True) for _ in range(2)]
            finally:
                release.set()
            result = owner.result(5)
            assert isinstance(result, RunTyFactRef)
            assert all(waiter.result(5) is result for waiter in waiters)
        assert len(calls) == 1
        assert cache.lookup(static_subject, policy) is result
        assert result.observation == document
        assert result.process == process
        assert cache.collect(preparation, policy, observe, revalidate=lambda: True) is result
        assert len(calls) == 1
        cache.close()

    def test_membership_is_run_owned_even_with_identical_payload(self, preparation, static_subject, policy):
        process = ProcessResult(exit_code=0, duration_seconds=1)
        document = ty_fact_document(static_subject, policy, TyCheck(process=process, diagnostics=()))
        first, second = TyCheckCache(), TyCheckCache()
        a = first.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        b = second.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        assert isinstance(a, RunTyFactRef)
        assert isinstance(b, RunTyFactRef)
        assert a.observation == b.observation
        assert first.admits(a, subject=static_subject)
        assert second.admits(b, subject=static_subject)
        assert not second.admits(a, subject=static_subject)
        assert not first.admits(b, subject=static_subject)
        assert not first.admits(replace(a), subject=static_subject)
        assert not first.admits(RunTyFactRef(document, process, preparation), subject=static_subject)
        assert first.stop() == (a,)
        assert first.lookup(static_subject, policy) is a
        assert not first.admits(a, subject=static_subject)
        first.close()
        assert isinstance(first.lookup(static_subject, policy), CacheMiss)
        assert a.observation == document
        with pytest.raises(OperationCancelled):
            first.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        second.close()

    def test_stop_cancels_then_drains_owner_cleanup_before_return(self, preparation, static_subject, policy):
        cache = TyCheckCache()
        entered, cancelled, cleanup, cleaned = (Event() for _ in range(4))

        def observe(cancellation):
            release = cancellation.register(cancelled.set)
            try:
                entered.set()
                assert cancelled.wait(5)
                assert cleanup.wait(5)
                cancellation.raise_if_cancelled()
                raise AssertionError("cancelled operation cannot return a raw fact")
            finally:
                release()
                cleaned.set()

        with ThreadPoolExecutor(max_workers=3) as pool:
            owner = pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True)
            try:
                assert entered.wait(5)
                waiter = pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True)
                stopped = pool.submit(cache.stop)
                assert cancelled.wait(5)
                assert not stopped.done()
                assert not cleaned.is_set()
            finally:
                cleanup.set()
            assert stopped.result(5) == ()
            assert cleaned.is_set()
            for result in (owner, waiter):
                with pytest.raises(OperationCancelled):
                    result.result(5)
        assert isinstance(cache.lookup(static_subject, policy), CacheMiss)
        cache.close()

    def test_unmodeled_exception_wakes_consumers_and_prevents_retry(self, preparation, static_subject, policy):
        cache = TyCheckCache()
        entered, release = Event(), Event()
        calls = []

        def observe(_):
            calls.append(1)
            entered.set()
            assert release.wait(5)
            raise ValueError("unmodeled collector failure")

        with ThreadPoolExecutor(max_workers=2) as pool:
            owner = pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True)
            try:
                assert entered.wait(5)
                waiter = pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True)
            finally:
                release.set()
            with pytest.raises(ValueError, match="unmodeled collector failure"):
                owner.result(5)
            # The concurrent caller may enter before or after Run shutdown.
            with pytest.raises((ValueError, OperationCancelled)):
                waiter.result(5)
        assert calls == [1]
        with pytest.raises(OperationCancelled):
            cache.collect(preparation, policy, observe, revalidate=lambda: True)
        assert isinstance(cache.lookup(static_subject, policy), CacheMiss)
        cache.close()


class TestRunStaticScope:
    @pytest.mark.process
    def test_global_comparison_replays_after_actual_environment_close(self, static_request):
        preparation = static_request.preparation
        policy = static_request.observation_policy
        from pf.adapters.process import SubprocessRunner
        from pf.adapters.ty import TyAdapter
        from pf.static_request import StaticRequestFactory
        from pf.evaluation import StaticEvaluator
        from pf.schemas.policy import GuidancePolicy
        from pf.schemas.static_comparison import GlobalComparisonContext, StaticUncompared
        from pf.schemas.static_scope import StaticScopeEvidence

        cache, other = TyCheckCache(), TyCheckCache()
        static = StaticEvaluator(TyAdapter(SubprocessRunner()), requests=StaticRequestFactory(SubprocessRunner()))
        process = ProcessResult(exit_code=0, duration_seconds=1)
        document = ty_fact_document(preparation.subject, policy, TyCheck(process=process, diagnostics=()))
        fact = cache.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        foreign_fact = other.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        assert isinstance(fact, RunTyFactRef)
        assert isinstance(foreign_fact, RunTyFactRef)
        consumer = cache.consumer(fact, preparation)
        foreign = other.consumer(foreign_fact, preparation)
        cache.set_highest(consumer)
        other.set_highest(foreign)
        context = GlobalComparisonContext(highest_proposal_id=preparation.proposal.proposal_id)
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        result = static.compare(consumer, consumer, run_cache=cache, context=context, guidance_policy=guidance)
        assert result.status == "COMPARED"
        assert result.state == "STATIC_UNCHANGED"
        for left, right in ((foreign, consumer), (consumer, foreign), (replace(consumer), consumer)):
            assert static.compare(left, right, run_cache=cache, context=context, guidance_policy=guidance) == StaticUncompared(reason="context-mismatch")
        assert cache.snapshot(preparation.proposal.cell).facts[0].observation == document
        static_request.prepared.close()
        assert not static_request.prepared.environment_root.exists()
        assert static.compare(consumer, consumer, run_cache=cache, context=context, guidance_policy=guidance) == result
        assert cache.lookup(preparation.subject, policy) is fact
        cache.stop()
        scope = cache.snapshot(preparation.proposal.cell)
        assert len(scope.facts) == len(scope.processes) == len(scope.consumers) == 1
        assert len(scope.comparisons) == 1
        assert scope.comparisons[0].result == result
        assert static.compare(foreign, foreign, run_cache=other, context=context, guidance_policy=guidance) == result
        other_scope = other.snapshot(preparation.proposal.cell)
        assert scope.scope_ref != other_scope.scope_ref
        assert scope.comparisons[0].identity == other_scope.comparisons[0].identity
        for field, value in (("identity", "0" * 64), ("subject_ref", "foreign-consumer"), ("reference_ref", None)):
            forged = scope.model_dump(mode="json")
            forged["comparisons"][0][field] = value
            with pytest.raises(ValueError):
                StaticScopeEvidence.model_validate(forged)
        forged = scope.model_dump(mode="json")
        forged["comparisons"][0]["result"]["fingerprint"] = "0" * 64
        with pytest.raises(ValueError, match="semantic evidence"):
            StaticScopeEvidence.model_validate(forged)
        producer = next(item for item in scope.consumers if item.ref == scope.facts[0].producer_ref)
        assert producer.preparation == preparation
        assert fact.producer == preparation
        saved = scope.model_dump_json()
        cache.close()
        restored = StaticScopeEvidence.model_validate_json(saved)
        assert restored.compare(scope_ref=restored.scope_ref, subject_ref=producer.ref,
                                reference_ref=producer.ref, context=context, guidance=guidance).result == result
        assert static.compare(consumer, consumer, run_cache=cache, context=context, guidance_policy=guidance) == StaticUncompared(reason="context-mismatch")
        other.close()

    @pytest.mark.parametrize("unavailable", [False, True])
    def test_query_before_capture_does_not_create_a_provisional_global_root(self, preparation, policy, unavailable):
        from pf.schemas.policy import GuidancePolicy
        from pf.schemas.static_comparison import GlobalComparisonContext

        process = ProcessResult(exit_code=2 if unavailable else 0, duration_seconds=1)
        outcome = (ToolFailure(cause="TOOL_FAILURE", stage="ty", process=process)
                   if unavailable else TyCheck(process=process, diagnostics=()))
        document = ty_fact_document(preparation.subject, policy, outcome)
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        with TyCheckCache() as cache:
            fact = cache.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
            assert isinstance(fact, RunTyFactRef)
            consumer = cache.consumer(fact, preparation)
            context = GlobalComparisonContext(highest_proposal_id=None)
            before = cache.compare(consumer, None, context=context, guidance=guidance)
            assert before.status == ("UNAVAILABLE" if unavailable else "UNCOMPARED")
            scope = cache.snapshot(preparation.proposal.cell)
            assert scope.highest_reference_ref is None
            assert scope.highest_uncollected is None
            assert scope.comparisons == ()
            cache.set_highest(consumer)
            context = GlobalComparisonContext(highest_proposal_id=preparation.proposal.proposal_id)
            result = cache.compare(consumer, consumer, context=context, guidance=guidance)
            scope = cache.snapshot(preparation.proposal.cell)
            assert len(scope.comparisons) == 1
            assert scope.comparisons[0].result == result
            assert scope.highest_reference_ref == scope.comparisons[0].reference_ref

    def test_comparison_identity_distinguishes_unavailable_references_from_empty_diagnostics(self, preparation, policy):
        from pf.schemas.static_baseline import StaticUncollectedBaseline
        from pf.schemas.static import StaticContentUnavailable
        from pf.schemas.static_comparison import GlobalComparisonContext, StaticComparisonDocument
        from pf.schemas.static_consumer import StaticConsumerEvidence
        from pf.schemas.policy import GuidancePolicy

        process = ProcessResult(exit_code=0, duration_seconds=1)
        empty = StaticConsumerEvidence(preparation=preparation, observation=ty_fact_document(
            preparation.subject, policy, TyCheck(process=process, diagnostics=()),
        ))
        failed = StaticConsumerEvidence(preparation=preparation, observation=ty_fact_document(
            preparation.subject, policy,
            ToolFailure(cause="TOOL_FAILURE", stage="ty", process=ProcessResult(exit_code=2, duration_seconds=1)),
        ))
        context = GlobalComparisonContext(highest_proposal_id=preparation.proposal.proposal_id)
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        comparisons = [
            StaticComparisonDocument.compare(context=context, subject=subject, reference=reference, guidance=guidance)
            for subject, reference in ((empty, empty), (empty, failed), (failed, empty), (empty, None))
        ]
        for reason in ("unreadable-content", "content-changed"):
            comparisons.append(StaticComparisonDocument.compare(
                context=context, subject=empty, reference=None, guidance=guidance,
                uncollected_reference=StaticUncollectedBaseline(
                    attempt=preparation.attempt, proposal=preparation.proposal,
                    unavailable=StaticContentUnavailable(detail=reason),
                ),
            ))
        assert len({comparison.identity for comparison in comparisons}) == len(comparisons)
        changed_policy = type(policy).model_validate({
            **policy.model_dump(), "config": {**policy.config.model_dump(), "timeout_seconds": 17},
        })
        changed_guidance = GuidancePolicy(observation=changed_policy, observation_identity=changed_policy.identity)
        changed = StaticComparisonDocument.compare(context=context, subject=empty, reference=None, guidance=changed_guidance)
        assert changed.result == comparisons[3].result
        assert changed.identity != comparisons[3].identity
        assert comparisons[0].result.status == "COMPARED"
        for comparison in comparisons:
            assert type(comparison).model_validate_json(comparison.model_dump_json()).identity == comparison.identity
            if comparison.result.status != "COMPARED":
                assert "fingerprint" not in comparison.result.model_dump()

    @pytest.mark.parametrize("field,value", [
        ("proposal_id", "another-proposal"),
        ("attempt_id", "another-attempt"), ("snapshot_digest", "another-snapshot"),
        ("policy_identity", "another-policy"), ("interpreter", None),
        ("project_plan_digest", None),
    ])
    def test_uncollected_highest_requires_matching_prepared_proposal(self, preparation, field, value):
        from pf.schemas.static import StaticContentUnavailable
        from pf.schemas.static_baseline import StaticUncollectedBaseline

        proposal = preparation.proposal.model_dump(mode="json")
        proposal[field] = value
        with pytest.raises(ValueError, match="actual highest preparation"):
            StaticUncollectedBaseline.model_validate({
                "attempt": preparation.attempt.model_dump(mode="json"), "proposal": proposal,
                "unavailable": StaticContentUnavailable(detail="unreadable-content").model_dump(mode="json"),
            })

    def test_uncollected_highest_is_fixed_and_replayed_without_a_raw_fact(self, preparation, policy):
        from pf.schemas.static import StaticContentUnavailable
        from pf.schemas.static_baseline import StaticUncollectedBaseline
        from pf.schemas.static_comparison import GlobalComparisonContext, StaticUncompared
        from pf.schemas.static_scope import StaticScopeEvidence
        from pf.schemas.policy import GuidancePolicy

        missing = StaticUncollectedBaseline(
            attempt=preparation.attempt, proposal=preparation.proposal,
            unavailable=StaticContentUnavailable(detail="unreadable-content"),
        )
        context = GlobalComparisonContext(highest_proposal_id=preparation.proposal.proposal_id)
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        with TyCheckCache() as cache:
            cache.set_highest_uncollected(missing)
            empty_scope = cache.snapshot(preparation.proposal.cell)
            assert empty_scope.highest_uncollected == missing
            assert empty_scope.highest_reference_ref is None
            assert empty_scope.facts == empty_scope.consumers == empty_scope.processes == ()
            assert isinstance(cache.lookup(preparation.subject, policy), CacheMiss)
            process = ProcessResult(exit_code=0, duration_seconds=1)
            document = ty_fact_document(preparation.subject, policy, TyCheck(process=process, diagnostics=()))
            fact = cache.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
            assert isinstance(fact, RunTyFactRef)
            consumer = cache.consumer(fact, preparation)
            with pytest.raises(ValueError, match="fixed"):
                cache.set_highest(consumer)
            changed = missing.model_copy(update={"unavailable": StaticContentUnavailable(detail="content-changed")})
            with pytest.raises(ValueError, match="fixed"):
                cache.set_highest_uncollected(changed)
            expected = StaticUncompared(reason="reference-unavailable")
            assert cache.compare(consumer, None, context=context, guidance=guidance) == expected
            assert cache.compare(consumer, None, context=GlobalComparisonContext(highest_proposal_id="other"),
                                 guidance=guidance) == StaticUncompared(reason="context-mismatch")
            scope = cache.snapshot(preparation.proposal.cell)
            conflicting = scope.model_dump(mode="json")
            conflicting["highest_reference_ref"] = scope.consumers[0].ref
            with pytest.raises(ValueError, match="sole baseline"):
                StaticScopeEvidence.model_validate(conflicting)
            saved = scope.model_dump_json()
        restored = StaticScopeEvidence.model_validate_json(saved)
        comparison = restored.compare(scope_ref=restored.scope_ref, subject_ref=restored.consumers[0].ref,
                                      reference_ref=None, context=context, guidance=guidance)
        assert comparison.result == expected
        assert comparison.uncollected_reference == missing
        assert len(restored.comparisons) == 1
        assert restored.comparisons[0].identity == comparison.identity
        assert restored.comparisons[0].result == expected
        assert type(comparison).model_validate_json(comparison.model_dump_json()) == comparison
        with pytest.raises(ValueError, match="uncollected highest"):
            restored.compare(scope_ref=restored.scope_ref, subject_ref=restored.consumers[0].ref,
                             reference_ref=None, context=GlobalComparisonContext(highest_proposal_id="other"),
                             guidance=guidance)
        with pytest.raises(ValueError, match="open"):
            cache.set_highest_uncollected(missing)

    def test_missing_input_does_not_create_a_negative_observation(self, preparation, policy):
        from pf.schemas.static import StaticContentUnavailable

        cache = TyCheckCache()
        unavailable = StaticContentUnavailable(detail="content-changed")
        assert cache.collect(preparation, policy, lambda _: unavailable, revalidate=lambda: True) == unavailable
        assert isinstance(cache.lookup(preparation.subject, policy), CacheMiss)
        scope = cache.snapshot(preparation.proposal.cell)
        assert scope.facts == scope.consumers == scope.processes == ()
        process = ProcessResult(exit_code=0, duration_seconds=1)
        document = ty_fact_document(preparation.subject, policy, TyCheck(process=process, diagnostics=()))
        fact = cache.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        assert isinstance(fact, RunTyFactRef)
        assert cache.consumer(fact, preparation).fact is fact
        cache.close()

    def test_cached_unavailable_is_not_a_cache_miss_and_does_not_rerun(
        self, preparation, static_subject, policy,
    ):
        process = ProcessResult(exit_code=2, duration_seconds=1)
        outcome = ToolFailure(cause="TOOL_FAILURE", stage="ty", process=process)
        document = ty_fact_document(static_subject, policy, outcome)
        calls: list[int] = []

        def observe(_cancellation):
            calls.append(1)
            return document, process

        cache = TyCheckCache()
        first = cache.collect(preparation, policy, observe, revalidate=lambda: True)
        assert isinstance(first, RunTyFactRef)
        hit = cache.lookup(static_subject, policy)
        assert hit is first
        assert not isinstance(hit, CacheMiss)
        assert first.observation.fact.kind == "ty-check-unavailable"
        assert cache.collect(preparation, policy, observe, revalidate=lambda: True) is first
        assert calls == [1]
        cache.close()

    def test_closed_cache_rejects_foreign_and_closed_comparison_refs(
        self, preparation, static_subject, policy,
    ):
        from pf.schemas.policy import GuidancePolicy
        from pf.schemas.static_comparison import GlobalComparisonContext, StaticUncompared
        from pf.evaluation import StaticEvaluator
        from pf.adapters.process import SubprocessRunner
        from pf.adapters.ty import TyAdapter
        from pf.static_request import StaticRequestFactory

        process = ProcessResult(exit_code=0, duration_seconds=1)
        document = ty_fact_document(static_subject, policy, TyCheck(process=process, diagnostics=()))
        first, second = TyCheckCache(), TyCheckCache()
        a = first.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        b = second.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        assert isinstance(a, RunTyFactRef) and isinstance(b, RunTyFactRef)
        consumer = first.consumer(a, preparation)
        foreign = second.consumer(b, preparation)
        first.set_highest(consumer)
        static = StaticEvaluator(TyAdapter(SubprocessRunner()), requests=StaticRequestFactory(SubprocessRunner()))
        context = GlobalComparisonContext(highest_proposal_id=preparation.proposal.proposal_id)
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        assert static.compare(consumer, consumer, run_cache=first, context=context, guidance_policy=guidance).status == "COMPARED"
        assert static.compare(foreign, consumer, run_cache=first, context=context, guidance_policy=guidance) == StaticUncompared(reason="context-mismatch")
        first.close()
        assert isinstance(first.lookup(static_subject, policy), CacheMiss)
        assert static.compare(consumer, consumer, run_cache=first, context=context, guidance_policy=guidance) == StaticUncompared(reason="context-mismatch")
        second.close()

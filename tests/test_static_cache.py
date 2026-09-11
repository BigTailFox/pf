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
from pf.schemas.static import StaticContentUnavailable
from pf.static_cache import CacheMiss, RunTyFactRef
from pf.static import TyCheckCache
from pf.ty_fact import ty_fact_document


def _signal_when_waiter_awaits_pending(cache: TyCheckCache, joined: Event) -> None:
    pending = next(iter(cache._pending.values()))
    original = pending.result.result

    def result(timeout=None):
        joined.set()
        return original(timeout)

    pending.result.result = result  # type: ignore[method-assign]


class TestRunTyCache:
    @pytest.mark.parametrize("failed", [False, True])
    def test_overlapping_consumers_share_one_actual_terminal(self, preparation, static_subject, policy, failed):
        entered, release, joined = Event(), Event(), Event()
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
                _signal_when_waiter_awaits_pending(cache, joined)
                waiters = [pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True) for _ in range(2)]
                assert joined.wait(5)
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
        other_process = process.model_copy()
        document = ty_fact_document(static_subject, policy, TyCheck(process=process, diagnostics=()))
        other_document = ty_fact_document(static_subject, policy, TyCheck(process=other_process, diagnostics=()))
        first, second = TyCheckCache(), TyCheckCache()
        a = first.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        b = second.collect(preparation, policy, lambda _: (other_document, other_process), revalidate=lambda: True)
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

    def test_unmodeled_exception_wakes_consumers_and_allows_retry(self, preparation, static_subject, policy):
        cache = TyCheckCache()
        entered, release, joined = Event(), Event(), Event()
        calls = []
        fail = True

        def observe(_):
            calls.append(1)
            if fail:
                entered.set()
                assert release.wait(5)
                raise ValueError("unmodeled collector failure")
            process = ProcessResult(exit_code=0, duration_seconds=1)
            document = ty_fact_document(static_subject, policy, TyCheck(process=process, diagnostics=()))
            return document, process

        with ThreadPoolExecutor(max_workers=2) as pool:
            owner = pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True)
            try:
                assert entered.wait(5)
                _signal_when_waiter_awaits_pending(cache, joined)
                waiter = pool.submit(cache.collect, preparation, policy, observe, revalidate=lambda: True)
                assert joined.wait(5)
            finally:
                release.set()
            owner_result = owner.result(5)
            waiter_result = waiter.result(5)
        assert isinstance(owner_result, StaticContentUnavailable)
        assert owner_result.detail == "invalid-layout"
        assert waiter_result == owner_result
        assert calls == [1]
        assert isinstance(cache.lookup(static_subject, policy), CacheMiss)
        fail = False
        recovered = cache.collect(preparation, policy, observe, revalidate=lambda: True)
        assert isinstance(recovered, RunTyFactRef)
        assert calls == [1, 1]
        cache.close()


class TestRunStaticScope:
    @pytest.mark.process
    def test_global_comparison_replays_after_actual_environment_close(self, static_request):
        from pathlib import Path
        import shutil

        from pf.adapters.process import SubprocessRunner
        from pf.static_request import StaticRequestFactory, StaticTyRequest

        executable = shutil.which("ty")
        assert executable is not None
        captured = StaticRequestFactory(SubprocessRunner(), ty_executable=Path(executable)).capture(
            static_request.prepared, package=static_request.package,
            environment=static_request.environment,
        )
        assert isinstance(captured, StaticTyRequest)
        preparation = captured.preparation
        policy = captured.observation_policy
        from pf.schemas.policy import GuidancePolicy
        from pf.schemas.static_comparison import GlobalComparisonContext, StaticUncompared
        from pf.schemas.static_scope import StaticScopeEvidence

        cache, other = TyCheckCache(), TyCheckCache()
        process = ProcessResult(exit_code=0, duration_seconds=1)
        other_process = process.model_copy()
        document = ty_fact_document(preparation.subject, policy, TyCheck(process=process, diagnostics=()))
        other_document = ty_fact_document(
            preparation.subject, policy, TyCheck(process=other_process, diagnostics=()),
        )
        fact = cache.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        foreign_fact = other.collect(
            preparation, policy, lambda _: (other_document, other_process), revalidate=lambda: True,
        )
        assert isinstance(fact, RunTyFactRef)
        assert isinstance(foreign_fact, RunTyFactRef)
        consumer = cache.consumer(fact, preparation)
        foreign = other.consumer(foreign_fact, preparation)
        cache.set_highest(consumer)
        other.set_highest(foreign)
        context = GlobalComparisonContext(highest_proposal_id=preparation.proposal.proposal_id)
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        result = cache.compare(consumer, consumer, context=context, guidance=guidance)
        assert result.status == "COMPARED"
        assert result.state == "STATIC_UNCHANGED"
        for left, right in ((foreign, consumer), (consumer, foreign), (replace(consumer), consumer)):
            assert cache.compare(left, right, context=context, guidance=guidance) == StaticUncompared(reason="context-mismatch")
        assert cache.snapshot(preparation.proposal.cell).facts[0].observation == document
        static_request.prepared.close()
        assert not static_request.prepared.environment_root.exists()
        assert cache.compare(consumer, consumer, context=context, guidance=guidance) == result
        assert cache.lookup(preparation.subject, policy) is fact
        cache.stop()
        scope = cache.snapshot(preparation.proposal.cell)
        assert len(scope.facts) == len(scope.processes) == len(scope.consumers) == 1
        assert len(scope.comparisons) == 1
        assert scope.comparisons[0].result == result
        assert other.compare(foreign, foreign, context=context, guidance=guidance) == result
        other_scope = other.snapshot(preparation.proposal.cell)
        assert scope.scope_ref != other_scope.scope_ref
        assert scope.comparisons[0].identity == other_scope.comparisons[0].identity
        for field, value in (("subject_ref", "foreign-consumer"),):
            forged = scope.model_dump(mode="json")
            forged["comparisons"][0][field] = value
            with pytest.raises(ValueError):
                StaticScopeEvidence.model_validate(forged)
        from pf.static.audit import _admit_saved_static_audit
        forged = scope.model_dump(mode="json")
        forged["comparisons"][0]["result"]["fingerprint"] = "0" * 64
        with pytest.raises(ValueError, match="semantic evidence"):
            _admit_saved_static_audit(StaticScopeEvidence.model_validate(forged))
        producer = next(item for item in scope.consumers if item.ref == scope.facts[0].producer_ref)
        assert scope.preparation(producer.preparation_ref) == preparation
        assert fact.producer == preparation
        saved = scope.model_dump_json()
        cache.close()
        restored = StaticScopeEvidence.model_validate_json(saved)
        from pf.static.audit import compare_in_document
        assert compare_in_document(
            restored, scope_ref=restored.scope_ref, subject_ref=producer.ref,
            reference_ref=producer.ref, context=context, guidance=guidance,
        ).result == result
        assert cache.compare(consumer, consumer, context=context, guidance=guidance) == StaticUncompared(reason="context-mismatch")
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
        from pf.schemas.static_comparison import GlobalComparisonContext
        from pf.static.comparison import compare_static_document
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
            compare_static_document(context=context, subject=subject, reference=reference, guidance=guidance)
            for subject, reference in ((empty, empty), (empty, failed), (failed, empty), (empty, None))
        ]
        for reason in ("unreadable-content", "content-changed"):
            comparisons.append(compare_static_document(
                context=context, subject=empty, reference=None, guidance=guidance,
                uncollected_reference=StaticUncollectedBaseline(
                    attempt=preparation.attempt, proposal=preparation.proposal,
                    unavailable=StaticContentUnavailable(detail=reason),
                ),
            ))
        assert len({comparison.identity for comparison in comparisons}) == len(comparisons)
        changed_policy = type(policy).model_validate({
            **policy.model_dump(mode="json"), "timeout_seconds": 17,
        })
        changed_guidance = GuidancePolicy(observation=changed_policy, observation_identity=changed_policy.identity)
        changed = compare_static_document(context=context, subject=empty, reference=None, guidance=changed_guidance)
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
        from pf.static.audit import compare_in_document
        comparison = compare_in_document(
            restored, scope_ref=restored.scope_ref, subject_ref=restored.consumers[0].ref,
            reference_ref=None, context=context, guidance=guidance,
        )
        assert comparison.result == expected
        assert comparison.uncollected_reference == missing
        assert len(restored.comparisons) == 1
        assert restored.comparisons[0].identity == comparison.identity
        assert restored.comparisons[0].result == expected
        assert type(comparison).model_validate_json(comparison.model_dump_json()) == comparison
        with pytest.raises(ValueError, match="uncollected highest"):
            compare_in_document(
                restored, scope_ref=restored.scope_ref, subject_ref=restored.consumers[0].ref,
                reference_ref=None, context=GlobalComparisonContext(highest_proposal_id="other"),
                guidance=guidance,
            )
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
        process = ProcessResult(exit_code=0, duration_seconds=1)
        other_process = process.model_copy()
        document = ty_fact_document(static_subject, policy, TyCheck(process=process, diagnostics=()))
        other_document = ty_fact_document(static_subject, policy, TyCheck(process=other_process, diagnostics=()))
        first, second = TyCheckCache(), TyCheckCache()
        a = first.collect(preparation, policy, lambda _: (document, process), revalidate=lambda: True)
        b = second.collect(preparation, policy, lambda _: (other_document, other_process), revalidate=lambda: True)
        assert isinstance(a, RunTyFactRef) and isinstance(b, RunTyFactRef)
        consumer = first.consumer(a, preparation)
        foreign = second.consumer(b, preparation)
        first.set_highest(consumer)
        context = GlobalComparisonContext(highest_proposal_id=preparation.proposal.proposal_id)
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        assert first.compare(consumer, consumer, context=context, guidance=guidance).status == "COMPARED"
        assert first.compare(foreign, consumer, context=context, guidance=guidance) == StaticUncompared(reason="context-mismatch")
        first.close()
        assert isinstance(first.lookup(static_subject, policy), CacheMiss)
        assert first.compare(consumer, consumer, context=context, guidance=guidance) == StaticUncompared(reason="context-mismatch")
        second.close()


def _direct_bound_skip(scope, *, floor, predecessor):
    assert scope.skips
    skip = scope.skips[-1]
    assert skip.reason == "direct-bound"
    actual = next(pin.version for pin in skip.proposal.managed_vector if pin.name == skip.candidates.dependency)
    assert actual == floor
    assert skip.predecessor == predecessor
    assert floor in skip.window
    return skip


class TestSearchAuditLedger:
    def test_guided_search_binds_hint_skip_selection_and_rejects_forged_admission(self, tmp_path, run_cache):
        from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process
        from pf.schemas.evaluation import NormalExit, TyDiagnostic, VerifierDiagnostics, VerifierPass, VerifierRejected, VerifierRun
        from pf.schemas.report import CellSuccess
        from pf.schemas.static_comparison import SliceComparisonContext
        from pf.static.audit import _admit_saved_static_audit

        diagnostic = TyDiagnostic(
            identity="snapshot|src/demo/__init__.py|1|1|example", origin="snapshot",
            path="src/demo/__init__.py", line=1, column=1, code="example",
            severity="error", message="static suspicion",
        )

        def ty(vector, call):
            version = int(vector[0].version)
            regression = version < 3
            return TyCheck(
                process=successful_process(exit_code=1 if regression else 0),
                diagnostics=(diagnostic,) if regression else (),
            )

        def verifier(vector, call):
            version = int(vector[0].version)
            return VerifierRun(
                authoritative=(
                    VerifierPass(terminal=NormalExit(exit_code=0)) if version >= 2
                    else VerifierRejected(terminal=NormalExit(exit_code=1))
                ),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if version >= 2 else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(ty_handler=ty, verifier_handler=verifier)
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            scope = run_cache.snapshot(project.package.cells[0])
            local = [item for item in scope.comparisons if isinstance(item.context, SliceComparisonContext)]
            global_ids = {
                item.identity for item in scope.comparisons
                if not isinstance(item.context, SliceComparisonContext)
            }
            local_ids = {item.identity for item in local}
            assert len(local) == 3
            assert {item.result.state for item in local} == {"STATIC_UNCHANGED", "STATIC_REGRESSION"}
            assert global_ids and local_ids and global_ids.isdisjoint(local_ids)
            assert len(scope.passes) == 2
            assert len(scope.searches) == 1
            audit = scope.searches[0]
            assert audit.reason is None and audit.hint is not None
            skip = _direct_bound_skip(scope, floor="2", predecessor="1")
            assert skip.observed_search_refs == (audit.ref,)
            by_attempt = {}
            for selection in scope.selections:
                by_attempt.setdefault(selection.attempt.attempt_id, []).append(selection)
                assert selection.attempt.identity.execution_policy_identity
                assert "static-" not in selection.attempt.identity.execution_policy_identity
            reused = [group for group in by_attempt.values() if len(group) > 1]
            assert reused
            assert any(len({item.request.selection_reason for item in group}) > 1 for group in reused)
            suspects = [item for item in scope.selections if item.request.selection_reason == "static-suspect"]
            assert len(suspects) == 1
            assert suspects[0].request.candidate_version == "2"
            assert suspects[0].request.static_search_ref == audit.ref
            assert suspects[0].status == "PASS" and suspects[0].reused is False
            assert audit.ref in suspects[0].observed_search_refs
            saved = scope.model_dump_json()
            assert type(scope).model_validate_json(saved).model_dump_json() == saved
            extra_selection = scope.model_dump(mode="json")
            extra_selection["searches"][0]["candidates"]["selection"]["unrecognized_rule"] = True
            with pytest.raises(ValueError):
                type(scope).model_validate(extra_selection)
            forged = scope.model_dump(mode="json")
            forged["searches"][0]["hint"]["suspect_index"] = 1
            with pytest.raises(ValueError, match="static hint"):
                _admit_saved_static_audit(type(scope).model_validate(forged))
            future = scope.model_dump(mode="json")
            future["skips"][0]["observed_search_refs"] = [*skip.observed_search_refs, "static-search-99"]
            with pytest.raises(ValueError, match="completed static search prefix"):
                _admit_saved_static_audit(type(scope).model_validate(future))
            detached = scope.model_dump(mode="json")
            index = next(
                offset for offset, item in enumerate(detached["selections"])
                if item["request"]["selection_reason"] == "static-suspect"
            )
            detached["selections"][index]["observed_search_refs"] = []
            with pytest.raises(ValueError, match="future static search"):
                _admit_saved_static_audit(type(scope).model_validate(detached))
        finally:
            project.snapshot.close()

    def test_unavailable_search_reason_and_prepare_point_are_admitted(self, tmp_path, run_cache):
        from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process
        from pf.schemas.evaluation import (
            ExecutionFailure, NormalExit, OperationFailureResult, Unattributed,
            TyDiagnostic, VerifierDiagnostics, VerifierPass, VerifierRejected, VerifierRun,
        )
        from pf.schemas.project import VersionPin
        from pf.schemas.report import CellSuccess
        from pf.static.audit import _admit_saved_static_audit

        diagnostic = TyDiagnostic(
            identity="snapshot|src/demo/__init__.py|1|1|example", origin="snapshot",
            path="src/demo/__init__.py", line=1, column=1, code="example",
            severity="error", message="static suspicion",
        )

        def ty(vector, call):
            version = int(vector[0].version)
            return TyCheck(
                process=successful_process(exit_code=1 if version < 3 else 0),
                diagnostics=(diagnostic,) if version < 3 else (),
            )

        def verifier(vector, call):
            version = int(vector[0].version)
            return VerifierRun(
                authoritative=(
                    VerifierPass(terminal=NormalExit(exit_code=0)) if version >= 2
                    else VerifierRejected(terminal=NormalExit(exit_code=1))
                ),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if version >= 2 else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(ty_handler=ty, verifier_handler=verifier)
        assembly.uv.install_failures_by_vector[(VersionPin(name="demo-dep", version="1"),)] = OperationFailureResult(
            failure=ExecutionFailure(terminal=NormalExit(exit_code=2), attribution=Unattributed()),
            stage="install-project", process=successful_process(exit_code=2),
        )
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            scope = run_cache.snapshot(project.package.cells[0])
            assert len(scope.searches) == 1
            audit = scope.searches[0]
            assert audit.reason == "static-unavailable"
            unavailable = audit.points[-1].unavailable
            assert unavailable.proposal is None
            assert unavailable.failure.stage == "install-project"
            assert unavailable.process is not None
            assert audit.points[-1].comparison_identity is None
            skip = _direct_bound_skip(scope, floor="2", predecessor="1")
            assert skip.observed_search_refs == (audit.ref,)
            assert all(not item.request.selection_reason.startswith("static-") for item in scope.selections)
            assert all(item.request.static_search_ref is None for item in scope.selections)
            wrong_terminal = scope.model_dump(mode="json")
            wrong_terminal["searches"][0]["points"][-1]["unavailable"]["process"]["exit_code"] = 0
            with pytest.raises(ValueError, match="static prepare process"):
                type(scope).model_validate(wrong_terminal)
            forged = scope.model_dump(mode="json")
            forged["searches"][0]["reason"] = "anchor-unavailable"
            with pytest.raises(ValueError, match="static search"):
                _admit_saved_static_audit(type(scope).model_validate(forged))
        finally:
            project.snapshot.close()

    def test_baseline_only_skip_window_is_the_floor(self, tmp_path, run_cache):
        from evaluation_fixtures import evaluation_assembly, evaluation_project
        from pf.schemas.report import CellSuccess

        project = evaluation_project(tmp_path / "project", search_space="all")
        assembly = evaluation_assembly(candidate_versions=("3",))
        result = assembly.coordinator.search(
            run_cache=run_cache, package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
        )
        assert isinstance(result, CellSuccess)
        scope = run_cache.snapshot(project.package.cells[0])
        skip = _direct_bound_skip(scope, floor="3", predecessor=None)
        assert skip.predecessor_failure_id is None
        assert skip.window == ("3",)

    def test_illegal_record_rolls_back_then_legal_record_succeeds(self, tmp_path, run_cache):
        from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process
        from pf.schemas.evaluation import NormalExit, TyDiagnostic, VerifierDiagnostics, VerifierPass, VerifierRejected, VerifierRun
        from pf.schemas.report import CellSuccess

        diagnostic = TyDiagnostic(
            identity="snapshot|src/demo/__init__.py|1|1|example", origin="snapshot",
            path="src/demo/__init__.py", line=1, column=1, code="example",
            severity="error", message="static suspicion",
        )

        def ty(vector, call):
            version = int(vector[0].version)
            regression = version < 3
            return TyCheck(
                process=successful_process(exit_code=1 if regression else 0),
                diagnostics=(diagnostic,) if regression else (),
            )

        def verifier(vector, call):
            version = int(vector[0].version)
            return VerifierRun(
                authoritative=(
                    VerifierPass(terminal=NormalExit(exit_code=0)) if version >= 2
                    else VerifierRejected(terminal=NormalExit(exit_code=1))
                ),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if version >= 2 else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(ty_handler=ty, verifier_handler=verifier)
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            scope = run_cache.snapshot(project.package.cells[0])
            original = scope.searches
            assert original
            before = [item.model_dump_json() for item in original]
            illegal = original[0].model_copy(update={"reason": "anchor-unavailable"})
            with pytest.raises(ValueError, match="static search"):
                run_cache.record_search(illegal)
            rolled = run_cache.snapshot(project.package.cells[0])
            assert [item.model_dump_json() for item in rolled.searches] == before
            legal_ref = run_cache.record_search(original[0])
            assert legal_ref
            admitted = run_cache.snapshot(project.package.cells[0])
            assert len(admitted.searches) == len(original) + 1
            assert admitted.searches[-1].ref == legal_ref
            assert run_cache.admitted_membership(project.package.cells[0]) is not None
        finally:
            project.snapshot.close()

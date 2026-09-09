"""Product static evaluator: collect, capture, compare, runtime ledger, slices."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol

from pf.adapters.process import ProcessRunner
from pf.cancellation import Cancellation
from pf.environment import PreparedEnvironment, StageConsumer, emit_cell_stage
from pf.evaluation import StagePermitPools
from pf.schemas.evaluation import PassEvaluation, RuntimeEvaluationRun
from pf.schemas.policy import GuidancePolicy
from pf.schemas.project import CandidateSnapshot, PackagePlan, Proposal
from pf.schemas.static import StaticContentUnavailable
from pf.schemas.static_baseline import StaticUncollectedBaseline
from pf.schemas.static_comparison import (
    SliceComparisonContext,
    StaticComparisonDocument,
    StaticComparisonResult,
    StaticComparisonUnavailable,
)
from pf.schemas.static_search import (
    OracleSelectionAudit,
    StaticHintEvidence,
    StaticPhaseOmission,
    StaticPhaseSkip,
    StaticProbeUnavailableEvidence,
    StaticSearchAudit,
    StaticSearchPointEvidence,
)
from pf.static_cache import (
    CacheMiss,
    RunStaticConsumerRef,
    RunTyFactRef,
    TyCheckCache,
)
from pf.static_request import StaticRequestFactory, StaticTyRequest, static_preparation_evidence
from pf.static.guidance import StaticPoint, StaticSearchResult, StaticSlice
from pf.ty_fact import ty_fact_document
from pf.schemas.evaluation import ToolFailure, TyCheck


class TyOperations(Protocol):
    def observe(
        self, request: StaticTyRequest, *, cancellation: Cancellation | None = None,
    ) -> TyCheck | ToolFailure | StaticContentUnavailable: ...


class StaticSliceCollector(Protocol):
    def inspect(self, version: str) -> CollectedStaticSubject | StaticProbeUnavailableEvidence: ...

    def finish(self, keep_versions: tuple[str, ...]) -> None: ...


@dataclass(frozen=True, eq=False)
class CollectedStaticSubject:
    """Opaque Run handle for one collected raw static document."""

    _run_identity: str
    _consumer: RunStaticConsumerRef
    _from_highest: bool = False


class StaticEvaluator:
    """Collect raw static facts, compare GLOBAL/SLICE, and record direct PASSes."""

    def __init__(
        self,
        ty: TyOperations,
        *,
        processes: ProcessRunner,
        events: StageConsumer | None = None,
        permits: StagePermitPools | None = None,
    ) -> None:
        self._ty = ty
        self._requests = StaticRequestFactory(processes)
        self._events = events
        self._permits = permits or StagePermitPools()

    def collect_prepared(
        self, prepared: PreparedEnvironment, *, package: PackagePlan, run_cache: TyCheckCache,
    ) -> CollectedStaticSubject | StaticContentUnavailable:
        run_cache.require_accepting()
        preparation = static_preparation_evidence(prepared, package)
        if isinstance(preparation, StaticContentUnavailable):
            raise ValueError(
                f"prepared has no portable static preparation: {preparation.detail}"
            )
        run_cache.register_prepared(prepared, preparation)
        with prepared.static_use(cancellation=run_cache.cancellation) as available:
            if not available:
                return StaticContentUnavailable(detail="content-changed")
            request = self._requests.capture(
                prepared, package=package, environment=os.environ,
                cancellation=run_cache.cancellation,
            )
            if isinstance(request, StaticContentUnavailable):
                return request
            if isinstance(run_cache.lookup(request.subject, request.observation_policy), CacheMiss):
                emit_cell_stage(self._events, prepared.proposal.cell, "static-probe")
            result = self._collect(prepared, request, run_cache=run_cache)
            if isinstance(result, StaticContentUnavailable):
                return result
            consumer = run_cache.consumer(result, request.preparation)
            run_cache.bind_direct_pass_consumer(prepared.proposal, consumer)
            return CollectedStaticSubject(
                _run_identity=run_cache.run_identity, _consumer=consumer,
            )

    def capture_highest(
        self, prepared: PreparedEnvironment, *, package: PackagePlan, run_cache: TyCheckCache,
    ) -> CollectedStaticSubject | StaticContentUnavailable:
        if prepared.attempt.identity.requested_resolution != "highest":
            raise ValueError("capture_highest requires a highest-resolution preparation")
        result = self.collect_prepared(prepared, package=package, run_cache=run_cache)
        if isinstance(result, StaticContentUnavailable):
            run_cache.set_highest_uncollected(StaticUncollectedBaseline(
                attempt=prepared.attempt, proposal=prepared.proposal, unavailable=result,
            ))
            return result
        run_cache.set_highest(result._consumer)
        return CollectedStaticSubject(
            _run_identity=result._run_identity,
            _consumer=result._consumer,
            _from_highest=True,
        )

    def compare_global(
        self, collected: CollectedStaticSubject, *, run_cache: TyCheckCache,
    ) -> StaticComparisonResult:
        self._require_handle(collected, run_cache)
        if collected._from_highest:
            raise ValueError("compare_global does not accept a capture_highest handle")
        policy = collected._consumer.fact.observation.observation_policy
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        return run_cache.compare_global(collected._consumer, guidance=guidance)

    def record_runtime(
        self, prepared: PreparedEnvironment, runtime: RuntimeEvaluationRun, *,
        run_cache: TyCheckCache,
    ) -> None:
        run_cache.require_accepting()
        if not run_cache.has_prepared(prepared):
            raise ValueError("prepared is not registered in this static Run")
        if runtime.evaluation.proposal != prepared.proposal:
            raise ValueError("runtime evaluation must use the prepared Proposal")
        if not isinstance(runtime.evaluation, PassEvaluation) or runtime.diagnostics is None:
            return
        run_cache.record_direct_pass(prepared, runtime)

    def open_slice(
        self,
        *,
        run_cache: TyCheckCache,
        upper_proposal: Proposal,
        dependency: str,
        versions: tuple[str, ...],
        candidates: CandidateSnapshot,
        collector: StaticSliceCollector,
    ) -> StaticSlice | None:
        run_cache.require_accepting()
        entry = run_cache.find_direct_pass(upper_proposal)
        if entry is None:
            raise ValueError("open_slice requires a Direct-PASS ledger row")
        names = tuple(pin.name for pin in upper_proposal.managed_vector)
        if dependency not in names:
            raise ValueError("slice dependency must be on the upper Proposal")
        if candidates.dependency != dependency:
            raise ValueError("slice candidates must match the dependency")
        fixed = tuple(pin for pin in upper_proposal.managed_vector if pin.name != dependency)
        window = tuple(candidates.select(version) for version in versions)
        attempt = entry.preparation.attempt
        if entry.consumer is None:
            version = next(pin.version for pin in upper_proposal.managed_vector if pin.name == dependency)
            collected = collector.inspect(version)
            if isinstance(collected, CollectedStaticSubject):
                self._require_handle(collected, run_cache)
                run_cache.bind_direct_pass_consumer(upper_proposal, collected._consumer)
                entry = run_cache.find_direct_pass(upper_proposal)
            if entry is None or entry.consumer is None:
                run_cache.record_omission(StaticPhaseOmission(
                    ref="pending", attempt=attempt, proposal=upper_proposal,
                    candidates=candidates, window=versions,
                ))
                return None
        assert entry.consumer is not None
        context = SliceComparisonContext(
            dependency=dependency, fixed_other_coordinates=fixed, window=window,
            anchor_pass=entry.evidence,
        )
        policy = entry.consumer.fact.observation.observation_policy
        guidance = GuidancePolicy(observation=policy, observation_identity=policy.identity)
        derive = getattr(collector, "derivation_policy", None)
        if derive is not None:
            derivation = derive(guidance)
        else:
            from pf.schemas.config import SearchConfig
            from pf.schemas.policy import SearchDerivationPolicy
            derivation = SearchDerivationPolicy(
                candidates=SearchConfig(), guidance_identity=guidance.identity, small_threshold=8,
            )
        return _EvaluatorStaticSlice(
            cache=run_cache, entry=entry, context=context, guidance=guidance,
            collector=collector, candidates=candidates, derivation=derivation,
        )

    def record_phase_skip(
        self, *, run_cache: TyCheckCache, skip: StaticPhaseSkip,
    ) -> None:
        run_cache.record_skip(skip)

    def record_oracle_selection(
        self, *, run_cache: TyCheckCache, selection: OracleSelectionAudit,
    ) -> str:
        return run_cache.record_selection(selection)

    def _require_handle(self, collected: CollectedStaticSubject, run_cache: TyCheckCache) -> None:
        if collected._run_identity != run_cache.run_identity:
            raise ValueError("collected static subject does not belong to this Run")
        if not run_cache.admits_consumer(
            collected._consumer, proposal=collected._consumer.preparation.proposal,
        ):
            raise ValueError("collected static subject is not admitted in this Run")

    def _collect(
        self, prepared: PreparedEnvironment, request: StaticTyRequest, *,
        run_cache: TyCheckCache,
    ) -> RunTyFactRef | StaticContentUnavailable:
        with prepared.static_use(cancellation=run_cache.cancellation) as available:
            if (not available or request.prepared is not prepared
                    or request.preparation.proposal != prepared.proposal or not request.revalidate()):
                return StaticContentUnavailable(detail="content-changed")

            def observe(cancellation: Cancellation):
                with self._permits.ty(cancellation=cancellation):
                    if not request.revalidate():
                        return StaticContentUnavailable(detail="content-changed")
                    outcome = self._ty.observe(request, cancellation=cancellation)
                    if not request.revalidate():
                        return StaticContentUnavailable(detail="content-changed")
                    if isinstance(outcome, StaticContentUnavailable):
                        return outcome
                    document = ty_fact_document(request.subject, request.observation_policy, outcome)
                    assert outcome.process is not None
                    return document, outcome.process

            return run_cache.collect(
                request.preparation, request.observation_policy, observe,
                revalidate=request.revalidate,
            )


class _EvaluatorStaticSlice:
    def __init__(
        self, *, cache: TyCheckCache, entry, context: SliceComparisonContext,
        guidance: GuidancePolicy, collector: StaticSliceCollector,
        candidates: CandidateSnapshot, derivation,
    ) -> None:
        self._cache = cache
        self._entry = entry
        self._context = context
        self._guidance = guidance
        self._collector = collector
        self._candidates = candidates
        self._derivation = derivation
        assert entry.consumer is not None
        self.anchor = self._compare(entry.consumer)
        self._prior = tuple(
            point.comparison_identity for point in self.known_points
            if point.comparison_identity is not None
        )
        self._unavailable: dict[str, StaticProbeUnavailableEvidence] = {}

    def _compare(self, consumer: RunStaticConsumerRef) -> StaticPoint:
        compared = self._cache.compare_document(
            consumer, self._entry.consumer, context=self._context,
            guidance=self._guidance, anchor_pass=self._entry,
        )
        version = next(
            pin.version for pin in consumer.preparation.proposal.managed_vector
            if pin.name == self._context.dependency
        )
        return StaticPoint(
            version,
            compared.result if isinstance(compared, StaticComparisonDocument) else compared,
            compared.identity if isinstance(compared, StaticComparisonDocument) else None,
        )

    @property
    def known_points(self) -> tuple[StaticPoint, ...]:
        return tuple(StaticPoint(
            next(
                pin.version for pin in document.subject.preparation.proposal.managed_vector
                if pin.name == self._context.dependency
            ),
            document.result, document.identity,
        ) for document in self._cache.local_comparisons(
            self._entry, context=self._context, guidance=self._guidance,
        ))

    def inspect(self, version: str) -> StaticPoint:
        assert self._entry.consumer is not None
        anchor_version = next(
            pin.version for pin in self._entry.consumer.preparation.proposal.managed_vector
            if pin.name == self._context.dependency
        )
        if version == anchor_version:
            return self.anchor
        collected = self._collector.inspect(version)
        if isinstance(collected, CollectedStaticSubject):
            return self._compare(collected._consumer)
        self._unavailable[version] = collected
        reason = collected.unavailable.detail if collected.unavailable is not None else "prepare-unavailable"
        return StaticPoint(version, StaticComparisonUnavailable(reason=reason), None)

    def finish(self, result: StaticSearchResult) -> str:
        keep: list[str] = []
        hint = result.hint
        if hint is not None:
            keep.extend((hint.suspect.version, hint.clean_neighbor.version))
        self._collector.finish(tuple(keep))
        return self._cache.record_search(StaticSearchAudit(
            ref="pending", context=self._context, guidance=self._guidance,
            policy=self._derivation, candidates=self._candidates,
            prior_comparison_identities=self._prior,
            points=tuple(StaticSearchPointEvidence(
                version=point.version, comparison_identity=point.comparison_identity,
                unavailable=self._unavailable.get(point.version) if point.comparison_identity is None else None,
            ) for point in result.points),
            hint=StaticHintEvidence(
                suspect_index=result.points.index(hint.suspect),
                clean_index=result.points.index(hint.clean_neighbor),
                clean_is_anchor=hint.clean_is_anchor,
            ) if hint is not None else None,
            reason=result.reason,
        ))

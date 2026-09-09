"""Run-owned, atomic storage of completed raw ty observations.

Collection callbacks borrow inputs only while they execute. Neither completed
entries nor comparison audit records retain prepared environments. Comparisons
are derived anew; the audit ledger is never used to answer a comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Condition, get_ident
from uuid import UUID, uuid4, uuid5

from pf.cancellation import Cancellation, OperationCancelled
from pf.schemas.evaluation import ProcessObservation, VerifierRun, execution_terminal
from pf.schemas.policy import TyObservationPolicy
from pf.schemas.static import StaticContentUnavailable, StaticSubject
from pf.schemas.ty_fact import TyFactDocument, validate_ty_fact_process
from pf.schemas.static_baseline import StaticUncollectedBaseline
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.schemas.project import Cell, Proposal
from pf.schemas.static_scope import StaticScopeEvidence, StaticProcessRecord, StaticFactMembership, StaticConsumerMembership, StaticPassMembership, StaticComparisonMembership
from pf.schemas.static_comparison import GlobalComparisonContext, SliceComparisonContext, SliceAnchorPass, StaticComparisonContext, StaticComparisonResult, StaticUncompared, StaticComparisonDocument
from pf.schemas.policy import GuidancePolicy
from pf.schemas.static_search import StaticSearchAudit, StaticPhaseOmission, StaticPhaseSkip, OracleSelectionAudit
from pf.static_subject import TyCheckKey, ty_check_key


@dataclass(frozen=True)
class CacheMiss:
    """There is no completed observation; an operation may still be running."""


@dataclass(frozen=True, eq=False)
class RunTyFactRef:
    """Read-only payload; only the issuing cache can admit its membership."""

    observation: TyFactDocument
    process: ProcessObservation
    producer: StaticPreparationEvidence


@dataclass(frozen=True, eq=False)
class RunStaticConsumerRef:
    fact: RunTyFactRef
    preparation: StaticPreparationEvidence

    @property
    def evidence(self) -> StaticConsumerEvidence:
        return StaticConsumerEvidence(preparation=self.preparation, observation=self.fact.observation)


@dataclass(frozen=True, eq=False)
class RunStaticPassRef:
    consumer: RunStaticConsumerRef
    evidence: SliceAnchorPass
    process: ProcessObservation


@dataclass(frozen=True)
class _ComparisonAudit:
    subject: RunStaticConsumerRef
    reference: RunStaticConsumerRef | None
    anchor_pass: RunStaticPassRef | None
    document: StaticComparisonDocument


@dataclass(frozen=True)
class _Pending:
    result: Future[RunTyFactRef | StaticContentUnavailable]
    owner: int


class TyCheckCache:
    """One explicit Verification Run, partitioned by the complete subject key.

    The callback is the collector's private owner seam. Callers must validate
    their prepared projection and retain each consumer lease through completion.
    The elected owner alone calls the lower operation; every consumer revalidates
    before registration, including cache hits and joined operations.
    """

    def __init__(self) -> None:
        self._condition = Condition()
        self._completed: dict[TyCheckKey, RunTyFactRef] = {}
        self._pending: dict[TyCheckKey, _Pending] = {}
        self._consumers: dict[tuple[RunTyFactRef, str], RunStaticConsumerRef] = {}
        self._highest: dict[str, RunStaticConsumerRef] = {}
        self._highest_uncollected: dict[str, StaticUncollectedBaseline] = {}
        self._passes: list[RunStaticPassRef] = []
        self._comparison_audit: dict[str, _ComparisonAudit] = {}
        self._search_audit: list[StaticSearchAudit] = []
        self._omissions: list[StaticPhaseOmission] = []
        self._skips: list[StaticPhaseSkip] = []
        self._selections: list[OracleSelectionAudit] = []
        self._run_identity = uuid4().hex
        self._cancellation = Cancellation()
        self._accepting = True
        self._closed = False

    @property
    def cancellation(self) -> Cancellation:
        """The Run control shared by request preparation and lower collection."""
        return self._cancellation

    def __enter__(self) -> TyCheckCache:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def lookup(self, subject: StaticSubject, policy: TyObservationPolicy) -> RunTyFactRef | CacheMiss:
        with self._condition:
            if self._closed:
                return CacheMiss()
            return self._completed.get(ty_check_key(subject, policy), CacheMiss())

    def admits(self, ref: RunTyFactRef, *, subject: StaticSubject) -> bool:
        with self._condition:
            return (
                self._accepting and not self._closed
                and ref.observation.subject.cell == subject.cell
                and self._completed.get(ty_check_key(ref.observation.subject,
                                                ref.observation.observation_policy)) is ref
            )

    def collect(
        self, preparation: StaticPreparationEvidence, policy: TyObservationPolicy,
        operation: Callable[[Cancellation], tuple[TyFactDocument, ProcessObservation] | StaticContentUnavailable],
        *, revalidate: Callable[[], bool],
    ) -> RunTyFactRef | StaticContentUnavailable:
        subject = preparation.subject
        key = ty_check_key(subject, policy)
        pending = None
        owner = False
        with self._condition:
            if not self._accepting:
                raise OperationCancelled("static Run stopped")
            existing = self._completed.get(key)
            if existing is None:
                pending = self._pending.get(key)
                owner = pending is None
                if pending is None:
                    pending = _Pending(Future(), get_ident())
                    self._pending[key] = pending
                elif pending.owner == get_ident():
                    raise RuntimeError("recursive collection of the same static request")
        if existing is not None:
            return self._accept_collected_consumer(existing, preparation, revalidate)
        assert pending is not None
        if not owner:
            result = pending.result.result()
            return (self._accept_collected_consumer(result, preparation, revalidate)
                    if isinstance(result, RunTyFactRef) else result)
        try:
            self._cancellation.raise_if_cancelled()
            outcome = operation(self._cancellation)
            self._cancellation.raise_if_cancelled()
            if not isinstance(outcome, StaticContentUnavailable) and not revalidate():
                outcome = StaticContentUnavailable(detail="content-changed")
            if isinstance(outcome, StaticContentUnavailable):
                with self._condition:
                    if not self._accepting:
                        raise OperationCancelled("static Run stopped")
                    pending.result.set_result(outcome)
                return outcome
            observation, process = outcome
            if observation.subject != subject or observation.observation_policy != policy:
                raise ValueError("collected fact does not match the elected static request")
            validate_ty_fact_process(observation, process)
            ref = RunTyFactRef(observation=observation, process=process, producer=preparation)
            with self._condition:
                if not self._accepting:
                    raise OperationCancelled("static Run stopped")
                self._register_consumer(ref, preparation)
                self._completed[key] = ref
                pending.result.set_result(ref)
            return ref
        except BaseException as failure:
            # An unmodeled exception stops this Run. It is never a negative
            # observation, and later consumers cannot retry its request.
            with self._condition:
                self._accepting = False
            try:
                self._cancellation.cancel()
            finally:
                pending.result.set_exception(failure)
            raise
        finally:
            with self._condition:
                del self._pending[key]
                self._condition.notify_all()

    def _accept_collected_consumer(
        self, fact: RunTyFactRef, preparation: StaticPreparationEvidence,
        revalidate: Callable[[], bool],
    ) -> RunTyFactRef | StaticContentUnavailable:
        # The caller retains the materialization lease. Filesystem validation
        # must not hold the cache condition or block cancellation of other keys.
        if not revalidate():
            return StaticContentUnavailable(detail="content-changed")
        with self._condition:
            if not self._accepting:
                raise OperationCancelled("static Run stopped")
            self._register_consumer(fact, preparation)
        return fact

    def _register_consumer(self, fact: RunTyFactRef, preparation: StaticPreparationEvidence) -> RunStaticConsumerRef:
        StaticConsumerEvidence(preparation=preparation, observation=fact.observation)
        key = (fact, preparation.model_dump_json())
        if key not in self._consumers:
            self._consumers[key] = RunStaticConsumerRef(fact, preparation)
        return self._consumers[key]

    def consumer(self, fact: RunTyFactRef, preparation: StaticPreparationEvidence) -> RunStaticConsumerRef:
        """Resolve an already collected consumer; this never imports evidence."""
        with self._condition:
            if not self.admits(fact, subject=preparation.subject):
                raise ValueError("static consumer requires a fact in this open Run/Cell")
            consumer = self._consumers.get((fact, preparation.model_dump_json()))
            if consumer is None:
                raise ValueError("static consumer preparation has not been collected")
            return consumer

    def _admits_consumer(self, consumer: RunStaticConsumerRef) -> bool:
        return self.admits(consumer.fact, subject=consumer.preparation.subject) and (
            self._consumers.get((consumer.fact, consumer.preparation.model_dump_json())) is consumer
        )

    def admits_consumer(self, consumer: RunStaticConsumerRef, *, proposal: Proposal) -> bool:
        """Admit an execution owner's exact consumer without importing refs."""
        with self._condition:
            return consumer.preparation.proposal == proposal and self._admits_consumer(consumer)

    def set_highest(self, consumer: RunStaticConsumerRef) -> None:
        with self._condition:
            if not self._admits_consumer(consumer) or consumer.preparation.attempt.identity.requested_resolution != "highest":
                raise ValueError("highest static reference must be collected in this open Run")
            key = consumer.preparation.proposal.cell.model_dump_json()
            existing = self._highest.get(key)
            if key in self._highest_uncollected or (existing is not None and existing is not consumer):
                raise ValueError("highest static reference is fixed for the Run/Cell")
            self._highest[key] = consumer

    def set_highest_uncollected(self, baseline: StaticUncollectedBaseline) -> None:
        """Freeze a capture failure without creating a raw observation or process."""
        with self._condition:
            if not self._accepting or self._closed:
                raise ValueError("highest static baseline requires an open collecting Run")
            key = baseline.proposal.cell.model_dump_json()
            existing = self._highest_uncollected.get(key)
            if key in self._highest or (existing is not None and existing != baseline):
                raise ValueError("highest static reference is fixed for the Run/Cell")
            self._highest_uncollected[key] = baseline

    def record_pass(self, consumer: RunStaticConsumerRef, run: VerifierRun) -> RunStaticPassRef:
        """Register the configured verifier owner's actual completed PASS."""
        with self._condition:
            if not self._admits_consumer(consumer) or run.diagnostics is None:
                raise ValueError("static anchor requires this Run consumer and actual verifier process")
            evidence = SliceAnchorPass.from_run(proposal=consumer.preparation.proposal, run=run)
            process = run.diagnostics.process
            if execution_terminal(process) != evidence.verifier.terminal:
                raise ValueError("static anchor PASS does not match its actual process")
            if any(fact.process is process for fact in self._completed.values()):
                raise ValueError("static anchor cannot reuse a ty process")
            for passed in self._passes:
                if passed.process is process:
                    if passed.consumer is not consumer or passed.evidence != evidence:
                        raise ValueError("verifier process is already bound to another static consumer")
                    return passed
            result = RunStaticPassRef(consumer, evidence, process)
            self._passes.append(result)
            return result

    def documents(self) -> tuple[TyFactDocument, ...]:
        """Completed Run-local fact documents for ty-cache persistence."""
        with self._condition:
            if self._closed:
                raise ValueError("static cache is closed")
            return tuple(item.observation for item in self._completed.values())

    def snapshot(self, cell: Cell) -> StaticScopeEvidence:
        """Portable completed facts, including elected producer and consumers."""
        with self._condition:
            if self._closed:
                raise ValueError("static cache is closed")
            facts = {fact: f"fact-{i}" for i, fact in enumerate(self._completed.values())
                     if fact.observation.subject.cell.package == cell.package
                     and fact.observation.subject.cell.python_minor == cell.python_minor
                     and fact.observation.subject.cell.target == cell.target
                     and fact.observation.subject.cell.extra_surface == cell.extra_surface}
            consumers = {item: f"consumer-{i}" for i, item in enumerate(self._consumers.values())
                         if item.preparation.proposal.cell == cell}
            highest = self._highest.get(cell.model_dump_json())
            return StaticScopeEvidence(
                scope_ref=uuid5(UUID(self._run_identity), cell.model_dump_json()).hex, cell=cell,
                processes=tuple(StaticProcessRecord(ref=f"ty-{ref}", process=fact.process)
                                for fact, ref in facts.items()) + tuple(
                                    StaticProcessRecord(ref=f"verifier-{i}", process=item.process)
                                    for i, item in enumerate(self._passes) if item.consumer in consumers),
                facts=tuple(StaticFactMembership(ref=ref, observation=fact.observation,
                            producer_ref=consumers[self._consumers[(fact, fact.producer.model_dump_json())]],
                            process_ref=f"ty-{ref}") for fact, ref in facts.items()),
                consumers=tuple(StaticConsumerMembership(ref=ref, preparation=item.preparation,
                                fact_ref=facts[item.fact]) for item, ref in consumers.items()),
                passes=tuple(StaticPassMembership(ref=f"pass-{i}", evidence=item.evidence,
                            consumer_ref=consumers[item.consumer], process_ref=f"verifier-{i}")
                            for i, item in enumerate(self._passes) if item.consumer in consumers),
                comparisons=tuple(StaticComparisonMembership(
                    ref=f"comparison-{i}", identity=identity,
                    subject_ref=consumers[item.subject],
                    reference_ref=consumers[item.reference] if item.reference is not None else None,
                    anchor_pass_ref=f"pass-{self._passes.index(item.anchor_pass)}" if item.anchor_pass is not None else None,
                    context=item.document.context, guidance=item.document.guidance,
                    result=item.document.result,
                ) for i, (identity, item) in enumerate(self._comparison_audit.items()) if item.subject in consumers),
                highest_reference_ref=consumers[highest] if highest is not None else None,
                highest_uncollected=self._highest_uncollected.get(cell.model_dump_json()),
                searches=tuple(item for item in self._search_audit if item.candidates.cell == cell),
                omissions=tuple(item for item in self._omissions if item.proposal.cell == cell),
                skips=tuple(item for item in self._skips if item.proposal.cell == cell),
                selections=tuple(item for item in self._selections if item.candidates.cell == cell),
            )

    def record_selection(self, selection: OracleSelectionAudit) -> str:
        with self._condition:
            if not self._accepting:
                raise OperationCancelled("static Run stopped")
            scope = self.snapshot(selection.candidates.cell)
            selection = selection.model_copy(update={
                "ref": f"oracle-selection-{len(self._selections)}",
                "observed_search_refs": tuple(item.ref for item in scope.searches),
            })
            selection.validate_in_scope(scope)
            self._selections.append(selection)
            return selection.ref

    def record_omission(self, omission: StaticPhaseOmission) -> None:
        with self._condition:
            if not self._accepting:
                raise OperationCancelled("static Run stopped")
            scope = self.snapshot(omission.proposal.cell)
            omission = omission.model_copy(update={
                "ref": f"static-omission-{len(self._omissions)}",
                "observed_pass_refs": tuple(item.ref for item in scope.passes),
            })
            omission.validate_in_scope(scope)
            self._omissions.append(omission)

    def record_skip(self, skip: StaticPhaseSkip) -> None:
        with self._condition:
            if not self._accepting:
                raise OperationCancelled("static Run stopped")
            scope = self.snapshot(skip.proposal.cell)
            skip = skip.model_copy(update={
                "ref": f"static-skip-{len(self._skips)}",
                "observed_pass_refs": tuple(item.ref for item in scope.passes),
                "observed_search_refs": tuple(item.ref for item in scope.searches),
            })
            skip.validate_in_scope(scope)
            self._skips.append(skip)

    def record_search(self, search: StaticSearchAudit) -> str:
        """Append a completed search audit after resolving its local evidence."""
        with self._condition:
            if not self._accepting:
                raise OperationCancelled("static Run stopped")
            search = search.model_copy(update={"ref": f"static-search-{len(self._search_audit)}"})
            search.validate_in_scope(self.snapshot(search.candidates.cell))
            self._search_audit.append(search)
            return search.ref

    def compare_global(
        self, subject: RunStaticConsumerRef, *, guidance: GuidancePolicy,
    ) -> StaticComparisonResult:
        """Compare with this Run/Cell's fixed highest reference and retain its audit."""
        with self._condition:
            key = subject.preparation.proposal.cell.model_dump_json()
            reference = self._highest.get(key)
            uncollected = self._highest_uncollected.get(key)
            proposal = (reference.preparation.proposal if reference is not None else
                        uncollected.proposal if uncollected is not None else None)
            return self.compare(
                subject, reference, guidance=guidance,
                context=GlobalComparisonContext(
                    highest_proposal_id=proposal.proposal_id if proposal is not None else None,
                ),
            )

    def find_pass(self, proposal: Proposal) -> RunStaticPassRef | None:
        """Read a completed exact execution PASS without importing external evidence."""
        with self._condition:
            return next((item for item in self._passes
                         if item.consumer.preparation.proposal == proposal
                         and self._admits_consumer(item.consumer)), None)

    def find_consumer(self, proposal: Proposal) -> RunStaticConsumerRef | None:
        with self._condition:
            return next((item for item in self._consumers.values()
                         if item.preparation.proposal == proposal and self._admits_consumer(item)), None)

    def local_comparisons(
        self, anchor: RunStaticPassRef, *, context: SliceComparisonContext, guidance: GuidancePolicy,
    ) -> tuple[StaticComparisonDocument, ...]:
        """Read prior admitted comparisons for this exact local anchor and Slice."""
        with self._condition:
            if not any(item is anchor for item in self._passes) or not self._admits_consumer(anchor.consumer):
                return ()
            return tuple(item.document for item in self._comparison_audit.values()
                         if item.anchor_pass is anchor and item.document.guidance == guidance
                         and isinstance(item.document.context, SliceComparisonContext)
                         and item.document.context.dependency == context.dependency
                         and item.document.context.fixed_other_coordinates == context.fixed_other_coordinates)

    def compare(
        self, subject: RunStaticConsumerRef, reference: RunStaticConsumerRef | None, *,
        context: StaticComparisonContext, guidance: GuidancePolicy,
        anchor_pass: RunStaticPassRef | None = None,
    ) -> StaticComparisonResult:
        compared = self.compare_document(subject, reference, context=context, guidance=guidance,
                                         anchor_pass=anchor_pass)
        return compared.result if isinstance(compared, StaticComparisonDocument) else compared

    def compare_document(
        self, subject: RunStaticConsumerRef, reference: RunStaticConsumerRef | None, *,
        context: StaticComparisonContext, guidance: GuidancePolicy,
        anchor_pass: RunStaticPassRef | None = None,
    ) -> StaticComparisonDocument | StaticUncompared:
        with self._condition:
            if not self._admits_consumer(subject) or (reference is not None and (
                not self._admits_consumer(reference)
                or subject.preparation.proposal.cell != reference.preparation.proposal.cell
            )):
                return StaticUncompared(reason="context-mismatch")
            cell = subject.preparation.proposal.cell
            uncollected = None
            if isinstance(context, GlobalComparisonContext):
                uncollected = self._highest_uncollected.get(cell.model_dump_json())
                if uncollected is not None and context.highest_proposal_id != uncollected.proposal.proposal_id:
                    return StaticUncompared(reason="context-mismatch")
                if anchor_pass is not None or self._highest.get(cell.model_dump_json()) is not reference:
                    return StaticUncompared(reason="context-mismatch")
            elif isinstance(context, SliceComparisonContext):
                if context.anchor_pass is None:
                    if anchor_pass is not None:
                        return StaticUncompared(reason="context-mismatch")
                elif (anchor_pass is None or not any(item is anchor_pass for item in self._passes)
                      or anchor_pass.consumer is not reference or anchor_pass.evidence != context.anchor_pass):
                    return StaticUncompared(reason="context-mismatch")
            document = StaticComparisonDocument.compare(
                context=context, subject=subject.evidence,
                reference=reference.evidence if reference is not None else None,
                guidance=guidance, uncollected_reference=uncollected,
            )
            # Intern only after fresh semantic derivation and membership admission.
            # This ledger retains portable audit evidence, never a comparison hit.
            # Before capture there is no fixed GLOBAL root to bind a membership.
            # Return the current availability without freezing a provisional root.
            if not isinstance(context, GlobalComparisonContext) or reference is not None or uncollected is not None:
                self._comparison_audit.setdefault(
                    document.identity, _ComparisonAudit(subject, reference, anchor_pass, document),
                )
            return document

    def stop(self) -> tuple[RunTyFactRef, ...]:
        """Reject new collection, cancel/drain owners, retain completed facts.

        The caller persists the returned observations before calling close.
        Cancellation callbacks must stop their lower operation and collection
        owners must complete cleanup before returning from that operation.
        """
        with self._condition:
            if any(item.owner == get_ident() for item in self._pending.values()):
                raise RuntimeError("a static collection owner cannot drain itself")
            self._accepting = False
        try:
            self._cancellation.cancel()
        finally:
            with self._condition:
                self._condition.wait_for(lambda: not self._pending)
        with self._condition:
            return tuple(self._completed.values())

    def close(self) -> None:
        try:
            self.stop()
        finally:
            with self._condition:
                # An owner cannot release its cache from inside collection.
                if not self._pending:
                    self._closed = True
                    self._completed.clear()
                    self._consumers.clear()
                    self._highest.clear()
                    self._highest_uncollected.clear()
                    self._passes.clear()
                    self._comparison_audit.clear()
                    self._search_audit.clear()
                    self._omissions.clear()
                    self._skips.clear()
                    self._selections.clear()

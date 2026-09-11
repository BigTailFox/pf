from __future__ import annotations

from pf.static import (
    CollectedStaticSubject,
    StaticEvaluator,
    StaticSliceCollector,
    TyCheckCache,
)
from pf.policy import search_derivation_policy
from pf.schemas.static_search import StaticProbeUnavailableEvidence, StaticPhaseSkip, OracleSelectionAudit
from pf.schemas.static import StaticContentUnavailable
from pf.schemas.policy import GuidancePolicy

from dataclasses import dataclass
from typing import Literal, Protocol

from pf.baseline import HighestVersionVerifier
from pf.candidates import CandidateBuilder
from pf.coordinate_search import CoordinateProgressConsumer, CoordinateSearch
from pf.errors import InfrastructureError, NoApplicableFloorError
from pf.environment import EnvironmentFactory, ExactSelection, PreparedEnvironment
from pf.evaluation import EvaluationCache, RuntimeEvaluator
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CacheConflict,
    CellContextEvent,
    CellSearchProgressEvent,
    CellStageEvent,
    Evaluation,
    FailureDetail,
    FailureCause,
    FailureRecord,
    FailureEvaluationRuntimeRun,
    FailureProcessRuntimeRun,
    FailureRuntimeRun,
    HighestVersionPass,
    CellFailureScope,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    ProcessObservation,
    PrepareFailure,
    StructuredOperationFailure,
    ProposalVectorMismatchFact,
    RuntimeEvaluationRun,
    SearchFailureEvent,
    SearchProbeRequest,
    SearchProbeDetailIdentity,
    VerifierRejectedEvaluation,
    runtime_process_observation,
)
from pf.schemas.project import (
    CandidateSnapshot,
    Cell,
    HarnessBaseline,
    PackagePlan,
    Proposal,
    SelectedCandidate,
    SourcePlan,
    VersionPin,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
    CoordinateFailure,
    CoordinateSuccess,
    ProbeEvidence,
    ProbeIndeterminate,
    ProbeObservation,
    ProbePass,
    ProbeRejection,
)
from pf.snapshot import SourceSnapshot


class ProbeSelectionError(RuntimeError):
    """An exact vector escaped the frozen CandidateSnapshot selection domain."""


def select_probe(
    vector: tuple[VersionPin, ...],
    snapshots: tuple[CandidateSnapshot, ...],
) -> tuple[SelectedCandidate, ...]:
    """Bind an exact vector to the frozen artifact selected for each version."""
    vector_by_name = {pin.name: pin.version for pin in vector}
    snapshot_by_name = {snapshot.dependency: snapshot for snapshot in snapshots}
    if len(vector_by_name) != len(vector) or len(snapshot_by_name) != len(snapshots):
        raise ProbeSelectionError("probe dependencies must be unique")
    if set(vector_by_name) != set(snapshot_by_name):
        raise ProbeSelectionError(
            "probe vector and candidate snapshots must cover the same dependencies"
        )
    try:
        return tuple(
            snapshot_by_name[dependency].select(vector_by_name[dependency])
            for dependency in sorted(vector_by_name)
        )
    except ValueError as error:
        raise ProbeSelectionError(str(error)) from error


class SearchDiagnosticConsumer(Protocol):
    def consume(self, event: SearchFailureEvent) -> None: ...


class SearchActivityConsumer(Protocol):
    def consume(
        self,
        event: CellContextEvent | CellSearchProgressEvent | CellStageEvent,
    ) -> None: ...


@dataclass(frozen=True)
class ProbeRun:
    evidence: ProbeEvidence
    evaluation: Evaluation | None
    runtime: RuntimeEvaluationRun | None = None


class _RuntimeBackedVectorEvaluator:
    def __init__(self, runner: "_ProposalRunner") -> None:
        self._runner = runner

    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        return self._runner.evaluate_full(vector).evidence

    def evaluate_in_slice(
        self,
        request: SearchProbeRequest,
    ) -> ProbeEvidence:
        return self._runner.evaluate_in_slice(request)

    def lookup_direct_in_slice(self, request: SearchProbeRequest) -> ProbeEvidence | None:
        return self._runner.lookup_direct_in_slice(request)

    def consume_direct_in_slice(self, request: SearchProbeRequest, evidence: ProbeEvidence) -> None:
        self._runner.consume_direct_in_slice(request, evidence)

    def record_direct_bound(
        self, vector, *, dependency, versions, predecessor=None, predecessor_failure_id=None,
    ) -> None:
        self._runner.record_direct_bound(
            vector, dependency=dependency, versions=versions,
            predecessor=predecessor, predecessor_failure_id=predecessor_failure_id,
        )

    def open_static_slice(self, vector, *, dependency, versions):
        return self._runner.open_static_slice(vector, dependency=dependency, versions=versions)

    def finish_coordinate(self) -> None:
        self._runner.finish_coordinate()


class _SearchStaticCollector:
    def __init__(
        self,
        runner: "_ProposalRunner",
        *,
        dependency: str,
        upper: tuple[VersionPin, ...],
        versions: tuple[str, ...],
    ):
        self._runner = runner
        self._dependency = dependency
        self._upper = upper
        self._versions = versions

    def inspect(self, version: str) -> CollectedStaticSubject | StaticProbeUnavailableEvidence:
        vector = tuple(sorted(
            (*(pin for pin in self._upper if pin.name != self._dependency),
             VersionPin(name=self._dependency, version=version)),
            key=lambda pin: pin.name,
        ))
        self._runner._emit_probe_context_for(
            dependency=self._dependency, version=version, kind="static",
            lower_version=self._versions[0],
            upper_version=self._versions[-1],
            candidate_count=len(self._versions),
        )
        return self._runner.collect_static_handle(vector)

    def finish(self, keep_versions: tuple[str, ...]) -> None:
        keep: set[tuple[tuple[str, str], ...]] = set()
        for version in keep_versions:
            vector = tuple(sorted(
                (*(pin for pin in self._upper if pin.name != self._dependency),
                 VersionPin(name=self._dependency, version=version)),
                key=lambda pin: pin.name,
            ))
            keep.add(self._runner._key(vector))
        self._runner._release_prepared(keep=keep)

    def derivation_policy(self, guidance: GuidancePolicy):
        return search_derivation_policy(
            self._runner._package.config, guidance=guidance,
            small_threshold=self._runner._small_threshold,
        )


class _ProposalRunner:
    def __init__(
        self,
        *,
        environments: EnvironmentFactory,
        static: StaticEvaluator,
        full: RuntimeEvaluator,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        harness_baseline: HarnessBaseline,
        baseline_capture: HighestVersionPass,
        candidate_snapshots: tuple[CandidateSnapshot, ...],
        source_plan: SourcePlan,
        run_cache: TyCheckCache,
        diagnostics: SearchDiagnosticConsumer | None = None,
        events: SearchActivityConsumer | None = None,
        small_threshold: int = 8,
    ) -> None:
        self._small_threshold = small_threshold
        self._environments = environments
        self._static = static
        self._full = full
        self._package = package
        self._cell = cell
        self._snapshot = snapshot
        self._harness_baseline = harness_baseline
        self._candidate_snapshots = candidate_snapshots
        self._source_plan = source_plan
        self._run_cache = run_cache
        self._diagnostics = diagnostics
        self._events = events
        self._failures = FailurePolicy()
        self._static_handles: dict[tuple[tuple[str, str], ...], CollectedStaticSubject] = {}
        self._failure_records: dict[str, FailureRecord] = {}
        self._failure_runtime_runs: dict[str, FailureRuntimeRun] = {}
        self._emitted_diagnostics: set[str] = set()
        self._cache = EvaluationCache()
        self._prepared: dict[tuple[tuple[str, str], ...], PreparedEnvironment] = {}
        self._prepare_failures: dict[tuple[tuple[str, str], ...], PrepareFailure] = {}
        baseline_evidence = self._pass_evidence(
            baseline_capture.attempt,
            baseline_capture.evaluation,
        )
        self._full_runs: dict[tuple[tuple[str, str], ...], ProbeRun] = {
            self._key(baseline_capture.evaluation.proposal.managed_vector): ProbeRun(
                evidence=baseline_evidence,
                evaluation=baseline_capture.evaluation,
            )
        }
        self._failed_cases: dict[str, tuple[str, ...]] = {}

    def __enter__(self) -> "_ProposalRunner":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def evaluate_full(
        self,
        vector: tuple[VersionPin, ...],
        *,
        request: SearchProbeRequest | None = None,
    ) -> ProbeRun:
        key = self._key(vector)
        existing = self._full_runs.get(key)
        if existing is not None:
            return existing
        if request is not None:
            self._emit_probe_context(request)
        self._emit_stage("oracle-probe")
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            run = ProbeRun(
                evidence=self._prepare_evidence(prepared),
                evaluation=None,
            )
            self._full_runs[key] = run
            return run
        try:
            collected = self._static.collect_prepared(
                prepared, package=self._package, run_cache=self._run_cache,
            )
            if isinstance(collected, CollectedStaticSubject):
                self._static_handles[key] = collected
                self._static.compare_global(collected, run_cache=self._run_cache)
            runtime = self._full.evaluate(
                prepared,
                package=self._package,
                failed_case_nodeids=self._failed_case_nodeids(request),
            )
            self._static.record_runtime(prepared, runtime, run_cache=self._run_cache)
            self._merge_failed_cases(request, runtime.failed_case_additions)
            stored = self._cache.record_full(runtime.evaluation)
            if isinstance(stored, CacheConflict):
                run = ProbeRun(
                    evidence=self._indeterminate_evidence(
                        attempt=prepared.attempt,
                        proposal_id=prepared.proposal.proposal_id,
                        stage="full-cache",
                        detail=FailureDetail(
                            code="conflicting-full-evaluation",
                            message=(
                                "the same proposal produced conflicting full results"
                            ),
                        ),
                    ),
                    evaluation=None,
                    runtime=None,
                )
            else:
                run = ProbeRun(
                    evidence=self._full_evidence(prepared, stored, runtime=runtime),
                    evaluation=stored,
                    runtime=runtime,
                )
            self._full_runs[key] = run
            return run
        finally:
            prepared.close()
            self._prepared.pop(key, None)

    def evaluate_in_slice(self, request: SearchProbeRequest) -> ProbeEvidence:
        reused = self._key(request.vector) in self._full_runs
        evidence = self.evaluate_full(request.vector, request=request).evidence
        self._record_selection(request, reused=reused, evidence=evidence)
        return evidence

    def consume_direct_in_slice(self, request: SearchProbeRequest, evidence: ProbeEvidence) -> None:
        existing = self._full_runs.get(self._key(request.vector))
        if existing is None or existing.evidence != evidence:
            raise ValueError("direct consumption requires this runner's completed exact result")
        self._record_selection(request, reused=True, evidence=evidence)

    def record_direct_bound(
        self,
        vector: tuple[VersionPin, ...],
        *,
        dependency: str,
        versions: tuple[str, ...],
        predecessor: str | None = None,
        predecessor_failure_id: str | None = None,
    ) -> None:
        run = self._full_runs.get(self._key(vector))
        if run is None or not isinstance(run.evaluation, PassEvaluation):
            raise ValueError("direct-bound skip requires an existing directly verified floor")
        if predecessor is not None:
            predicted = tuple(
                VersionPin(name=pin.name, version=predecessor if pin.name == dependency else pin.version)
                for pin in vector
            )
            prior = self._full_runs.get(self._key(predicted))
            if (
                prior is None
                or not isinstance(prior.evidence, ProbeRejection)
                or prior.evidence.failure_id != predecessor_failure_id
            ):
                raise ValueError("direct-bound skip predecessor must be this runner's rejection")
        snapshot = next(item for item in self._candidate_snapshots if item.dependency == dependency)
        self._static.record_phase_skip(run_cache=self._run_cache, skip=StaticPhaseSkip(
            ref="pending", attempt=run.evidence.attempt, proposal=run.evaluation.proposal,
            candidates=snapshot, window=versions,
            predecessor=predecessor, predecessor_failure_id=predecessor_failure_id,
        ))

    def _record_selection(
        self, request: SearchProbeRequest, *, reused: bool, evidence: ProbeEvidence,
    ) -> None:
        snapshot = next(item for item in self._candidate_snapshots if item.dependency == request.active_dependency)
        self._static.record_oracle_selection(run_cache=self._run_cache, selection=OracleSelectionAudit(
            ref="pending", request=request, candidates=snapshot, reused=reused, observed_search_refs=(),
            attempt=evidence.attempt, proposal_id=getattr(evidence, "proposal_id", None),
            status=evidence.status, failure_id=getattr(evidence, "failure_id", None),
        ))


    def open_static_slice(
        self, vector: tuple[VersionPin, ...], *, dependency: str, versions: tuple[str, ...],
    ):
        run = self._full_runs.get(self._key(vector))
        if run is None or not isinstance(run.evaluation, PassEvaluation):
            raise ValueError("static guidance requires an existing directly verified upper point")
        snapshot = next(item for item in self._candidate_snapshots if item.dependency == dependency)
        collector: StaticSliceCollector = _SearchStaticCollector(
            self, dependency=dependency, upper=vector, versions=versions,
        )
        return self._static.open_slice(
            run_cache=self._run_cache,
            upper_proposal=run.evaluation.proposal,
            dependency=dependency,
            versions=versions,
            candidates=snapshot,
            collector=collector,
        )

    def collect_static_handle(
        self, vector: tuple[VersionPin, ...],
    ) -> CollectedStaticSubject | StaticProbeUnavailableEvidence:
        key = self._key(vector)
        existing = self._static_handles.get(key)
        if existing is not None:
            return existing
        run = self._full_runs.get(key)
        if run is not None and run.evaluation is not None:
            if isinstance(run.evaluation, PassEvaluation):
                return self._collect_via_reprepare(
                    run.evaluation.proposal, attempt=run.evidence.attempt, key=key,
                )
            return StaticProbeUnavailableEvidence(
                attempt=run.evidence.attempt, proposal=run.evaluation.proposal,
                unavailable=StaticContentUnavailable(detail="content-changed"), failure=None, process=None,
            )
        self._emit_stage("static-probe")
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            return StaticProbeUnavailableEvidence(
                attempt=prepared.attempt, proposal=None, unavailable=None,
                failure=prepared.model_copy(update={"process": None}), process=prepared.process,
            )
        result = self._static.collect_prepared(prepared, package=self._package, run_cache=self._run_cache)
        if isinstance(result, StaticContentUnavailable):
            if not prepared.inputs_valid:
                prepared.close()
                self._prepared.pop(key, None)
            return StaticProbeUnavailableEvidence(
                attempt=prepared.attempt, proposal=prepared.proposal,
                unavailable=result, failure=None, process=None,
            )
        self._static_handles[key] = result
        self._release_prepared(keep=frozenset({key}), retain_recent=1)
        return result

    def _collect_via_reprepare(
        self, proposal: Proposal, *, attempt: Attempt, key: tuple[tuple[str, str], ...],
    ) -> CollectedStaticSubject | StaticProbeUnavailableEvidence:
        rebuilt = self._environments.reprepare(proposal, self._snapshot, self._source_plan)
        if isinstance(rebuilt, StaticContentUnavailable):
            return StaticProbeUnavailableEvidence(
                attempt=attempt, proposal=proposal, unavailable=rebuilt, failure=None, process=None,
            )
        try:
            result = self._static.collect_prepared(
                rebuilt, package=self._package, run_cache=self._run_cache,
            )
            if isinstance(result, StaticContentUnavailable):
                return StaticProbeUnavailableEvidence(
                    attempt=attempt, proposal=proposal, unavailable=result, failure=None, process=None,
                )
            self._static_handles[key] = result
            return result
        finally:
            rebuilt.close()

    def lookup_direct_in_slice(self, request: SearchProbeRequest) -> ProbeEvidence | None:
        """Read only this runner's exact execution context and completed facts."""
        run = self._full_runs.get(self._key(request.vector))
        return run.evidence if run is not None else None

    def finish_coordinate(self) -> None:
        """Release unconsumed materializations while retaining immutable facts."""
        self.close()

    def _release_prepared(
        self,
        *,
        keep: set[tuple[tuple[str, str], ...]] | frozenset[tuple[tuple[str, str], ...]],
        retain_recent: int = 0,
    ) -> None:
        """Close static-only proposal trees that will not be reused immediately.

        D003 closes unused materializations at coordinate end; a wide static
        window can otherwise retain every inspect venv until then. Hint
        endpoints stay available for same-Proposal oracle reuse.
        """
        overflow = [
            key for key in self._prepared if key not in keep
        ]
        protected = set(overflow[-retain_recent:]) if retain_recent else set()
        for key in overflow:
            if key in protected:
                continue
            prepared = self._prepared.pop(key)
            prepared.close()

    def _emit_stage(self, stage: str) -> None:
        if self._events is None:
            return
        self._events.consume(CellStageEvent(cell=self._cell, stage=stage))

    def _emit_probe_context(self, request: SearchProbeRequest) -> None:
        self._emit_probe_context_for(
            dependency=request.active_dependency,
            version=request.candidate_version,
            lower_version=request.lower_version,
            upper_version=request.upper_version,
            candidate_count=request.candidate_count,
            kind="oracle",
        )

    def _emit_probe_context_for(
        self,
        *,
        dependency: str,
        version: str,
        kind: Literal["static", "oracle"],
        window: tuple | None = None,
        lower_version: str | None = None,
        upper_version: str | None = None,
        candidate_count: int | None = None,
    ) -> None:
        if self._events is None:
            return
        if window is not None:
            versions = tuple(item.version for item in window)
            lower_version = versions[0]
            upper_version = versions[-1]
            candidate_count = len(versions)
        assert lower_version is not None and upper_version is not None and candidate_count is not None
        self._events.consume(
            CellContextEvent(
                cell=self._cell,
                detail=SearchProbeDetailIdentity(
                    dependency=dependency,
                    version=version,
                    lower_version=lower_version,
                    upper_version=upper_version,
                    candidate_count=candidate_count,
                    window=kind,
                ),
            )
        )

    def _failed_case_nodeids(
        self,
        request: SearchProbeRequest | None,
    ) -> tuple[str, ...]:
        if request is None:
            return ()
        return self._failed_cases.get(request.active_dependency, ())

    def _merge_failed_cases(
        self,
        request: SearchProbeRequest | None,
        additions: tuple[str, ...],
    ) -> None:
        if request is None or not additions:
            return
        current = self._failed_cases.get(request.active_dependency, ())
        seen = set(current)
        extra = tuple(nodeid for nodeid in additions if nodeid not in seen)
        if extra:
            self._failed_cases[request.active_dependency] = (*current, *extra)


    @property
    def failure_records(self) -> tuple[FailureRecord, ...]:
        return tuple(self._failure_records.values())

    @property
    def failure_runtime_runs(self) -> tuple[FailureRuntimeRun, ...]:
        return tuple(self._failure_runtime_runs.values())

    def close(self) -> None:
        for prepared in self._prepared.values():
            prepared.close()
        self._prepared.clear()

    def _record(
        self,
        failure: FailureRecord,
        *,
        evaluation: VerifierRejectedEvaluation
        | IndeterminateEvaluation
        | None,
        runtime: RuntimeEvaluationRun | None = None,
        runtime_process: ProcessObservation | None = None,
    ) -> None:
        existing = self._failure_records.get(failure.failure_id)
        if existing is not None and existing != failure:
            raise ValueError("failure ID collision within one cell search")
        self._failure_records.setdefault(failure.failure_id, failure)
        if runtime is not None and runtime_process_observation(runtime) is not None:
            runtime_run: FailureRuntimeRun = FailureEvaluationRuntimeRun(
                failure_id=failure.failure_id,
                runtime=runtime,
            )
            self._failure_runtime_runs.setdefault(failure.failure_id, runtime_run)
        elif runtime_process is not None and failure.process is None:
            self._failure_runtime_runs.setdefault(
                failure.failure_id,
                FailureProcessRuntimeRun(
                    failure_id=failure.failure_id,
                    process=runtime_process,
                ),
            )
        if self._diagnostics is None or failure.failure_id in self._emitted_diagnostics:
            return
        self._emitted_diagnostics.add(failure.failure_id)
        self._diagnostics.consume(
            SearchFailureEvent(
                cell=self._cell,
                failure=failure,
                evaluation=evaluation,
                runtime=runtime,
            )
        )

    def _prepare(
        self,
        vector: tuple[VersionPin, ...],
    ) -> PreparedEnvironment | PrepareFailure:
        key = self._key(vector)
        existing = self._prepared.get(key)
        if existing is not None:
            return existing
        failed = self._prepare_failures.get(key)
        if failed is not None:
            return failed
        prepared = self._environments.prepare(
            package=self._package,
            cell=self._cell,
            snapshot=self._snapshot,
            resolution=ExactSelection(
                select_probe(vector, self._candidate_snapshots),
                harness_baseline=self._harness_baseline,
            ),
            source_plan=self._source_plan,
        )
        if isinstance(prepared, PrepareFailure):
            self._prepare_failures[key] = prepared
            return prepared
        if self._key(prepared.proposal.managed_vector) != key:
            prepared.close()
            return PrepareFailure(
                attempt=prepared.attempt,
                stage="proposal-vector",
                failure=StructuredOperationFailure(fact=ProposalVectorMismatchFact(), terminal=None),
                project_plan_digest=prepared.project_plan.semantic_digest,
                environment_plan_digest=prepared.environment_identity.environment_plan_digest,
            )
        self._prepared[key] = prepared
        return prepared

    def _prepare_evidence(self, prepared: PrepareFailure) -> ProbeEvidence:
        record = self._failures.record_prepare(prepared)
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=None,
            cause=record.cause,
            stage=prepared.stage,
            process=prepared.process,
            record=record,
            project_plan_digest=prepared.project_plan_digest,
            environment_plan_digest=prepared.environment_plan_digest,
            evaluation=None,
        )

    def _full_evidence(
        self,
        prepared: PreparedEnvironment,
        result: Evaluation,
        *,
        runtime: RuntimeEvaluationRun,
    ) -> ProbeEvidence:
        if isinstance(result, PassEvaluation):
            return self._pass_evidence(prepared.attempt, result)
        failure = self._failures.record_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            result,
            project_plan_digest=prepared.project_plan.semantic_digest,
            environment_plan_digest=prepared.environment_identity.environment_plan_digest,
        )
        assert failure is not None
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=result.proposal.proposal_id,
            cause=failure.cause,
            stage=failure.stage,
            process=(
                runtime.diagnostics.process
                if runtime.diagnostics is not None
                else failure.process
            ),
            summary_code=failure.summary_code,
            evaluation=result,
            record=failure,
            runtime=runtime,
        )

    @staticmethod
    def _pass_evidence(
        attempt: Attempt,
        evaluation: PassEvaluation,
    ) -> ProbeEvidence:
        proposal = evaluation.proposal
        if proposal.attempt_id is None:
            raise ValueError("probe proposal must reference its attempt")
        return ProbePass(
            attempt=attempt,
            proposal_id=proposal.proposal_id,
            evaluation=evaluation,
        )

    def _failure_evidence(
        self,
        *,
        attempt: Attempt,
        proposal_id: str | None,
        cause: FailureCause,
        stage: str,
        process: ProcessObservation | None,
        evaluation: VerifierRejectedEvaluation
        | IndeterminateEvaluation
        | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
        record: FailureRecord | None = None,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
        runtime: RuntimeEvaluationRun | None = None,
    ) -> ProbeEvidence:
        failure = record or self._failures.classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )
        self._record(
            failure,
            evaluation=evaluation,
            runtime=runtime,
            runtime_process=process,
        )
        if failure.disposition == "REJECTED":
            assert not isinstance(evaluation, IndeterminateEvaluation)
            return ProbeRejection(
                attempt=attempt,
                proposal_id=proposal_id,
                failure_id=failure.failure_id,
                cause=failure.cause,
                evaluation=evaluation,
            )
        indeterminate_evaluation = (
            evaluation if isinstance(evaluation, IndeterminateEvaluation) else None
        )
        return ProbeIndeterminate(
            attempt=attempt,
            proposal_id=proposal_id,
            failure_id=failure.failure_id,
            cause=failure.cause,
            evaluation=indeterminate_evaluation,
        )

    def _indeterminate_evidence(
        self,
        *,
        attempt: Attempt,
        proposal_id: str,
        stage: str,
        detail: FailureDetail,
    ) -> ProbeEvidence:
        return self._failure_evidence(
            attempt=attempt,
            proposal_id=proposal_id,
            cause="NONDETERMINISTIC",
            stage=stage,
            process=None,
            detail=detail,
            evaluation=None,
        )

    @staticmethod
    def _key(vector: tuple[VersionPin, ...]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((pin.name, pin.version) for pin in vector))

    @staticmethod
    def _synthetic_process() -> ProcessResult:
        return ProcessResult(
            exit_code=None,
            signal=None,
            duration_seconds=0,
            stderr="proposal vector drift",
            start_error="proposal vector drift",
        )


class SearchCoordinator:
    """Own the baseline and single runtime-backed cell search state machine."""

    def __init__(
        self,
        *,
        environments: EnvironmentFactory,
        candidates: CandidateBuilder,
        static: StaticEvaluator,
        full: RuntimeEvaluator,
        highest: HighestVersionVerifier,
        coordinate_search: CoordinateSearch,
        diagnostics: SearchDiagnosticConsumer | None = None,
        events: SearchActivityConsumer | None = None,
    ) -> None:
        self._environments = environments
        self._candidates = candidates
        self._static = static
        self._full = full
        self._failures = FailurePolicy()
        self._highest = highest
        self._diagnostics = diagnostics
        self._events = events
        self._coordinate_search = coordinate_search

    def _coordinate_progress(
        self,
        cell: Cell,
    ) -> CoordinateProgressConsumer | None:
        events = self._events
        if events is None:
            return None
        previous: tuple[tuple[VersionPin, ...], tuple[VersionPin, ...]] | None = None

        def publish(
            packages: tuple[VersionPin, ...],
            completed_packages: tuple[VersionPin, ...],
        ) -> None:
            nonlocal previous
            progress = (packages, completed_packages)
            if progress == previous:
                return
            previous = progress
            events.consume(
                CellSearchProgressEvent(
                    cell=cell,
                    packages=packages,
                    completed_packages=completed_packages,
                )
            )

        return publish

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
        run_cache: TyCheckCache,
    ) -> CellResult:
        capture = self._highest.verify(
            package=package,
            cell=cell,
            snapshot=snapshot,
            source_plan=source_plan,
            run_cache=run_cache,
        )
        if isinstance(capture, (BaselineRejection, BaselineIndeterminate)):
            return capture
        baseline_evaluation = capture.evaluation
        coordinate_progress = self._coordinate_progress(cell)
        if self._events is not None:
            self._events.consume(CellContextEvent(cell=cell, detail=None))
            self._events.consume(
                CellStageEvent(cell=cell, stage="discovering candidates")
            )
        try:
            candidate_snapshots = self._candidates.build(
                package=package,
                cell=cell,
                baseline=baseline_evaluation.proposal.managed_vector,
                source_plan=source_plan,
            )
        except InfrastructureError:
            failure = self._failures.classify(
                scope=CellFailureScope(
                    package=package.name,
                    cell=cell,
                    source_snapshot_digest=snapshot.identity.digest,
                    execution_policy_identity=baseline_evaluation.proposal.policy_identity,
                ),
                cause="SOURCE_FAILURE",
                stage="candidate-discovery",
                process=None,
                detail=FailureDetail(
                    code="candidate-discovery-failed",
                    message="candidate discovery failed",
                ),
            )
            return CellIndeterminate(

                cell=cell,
                phase="candidate-discovery",
                failure_id=failure.failure_id,
                failure_records=(failure,),
                baseline_attempt=capture.attempt,

                baseline=baseline_evaluation,
            )
        except NoApplicableFloorError:
            return CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",

                cell=cell,
                phase="candidate-discovery",
                baseline_attempt=capture.attempt,

                baseline=baseline_evaluation,
            )
        with _ProposalRunner(
            environments=self._environments,
            static=self._static,
            full=self._full,
            package=package,
            cell=cell,
            snapshot=snapshot,
            harness_baseline=capture.harness_baseline,
            baseline_capture=capture,
            candidate_snapshots=candidate_snapshots,
            source_plan=source_plan,
            run_cache=run_cache,
            diagnostics=self._diagnostics,
            events=self._events,
            small_threshold=self._coordinate_search.small_threshold,
        ) as runner:
            try:
                search = self._coordinate_search.minimize(
                    start=baseline_evaluation.proposal.managed_vector,
                    candidates=candidate_snapshots,
                    evaluator=_RuntimeBackedVectorEvaluator(runner),
                    progress=coordinate_progress,
                )
            except ProbeSelectionError as error:
                failure = self._failures.classify(
                    scope=CellFailureScope(
                        package=package.name,
                        cell=cell,
                        source_snapshot_digest=snapshot.identity.digest,
                        execution_policy_identity=(
                            baseline_evaluation.proposal.policy_identity
                        ),
                    ),
                    cause="INTERNAL_INVARIANT",
                    stage="candidate-selection",
                    process=None,
                    detail=FailureDetail(
                        code="probe-outside-frozen-selection",
                        message=str(error),
                    ),
                )
                failures = {
                    item.failure_id: item for item in runner.failure_records
                }
                failures[failure.failure_id] = failure
                return CellIndeterminate(

                    cell=cell,
                    phase="runtime-search",
                    failure_id=failure.failure_id,
                    failure_records=tuple(failures.values()),
                    baseline_attempt=capture.attempt,

                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    failure_runtime_runs=runner.failure_runtime_runs,
                )
            if isinstance(search, CoordinateFailure):
                search = CoordinateFailure(
                    status=search.status,
                    dependency=search.dependency,
                    observations=search.observations,
                    counterexample=search.counterexample,
                    failure_id=search.failure_id,
                )
                return self._coordinate_failure(
                    cell=cell,
                    phase="runtime-search",

                    baseline=baseline_evaluation,
                    candidates=candidate_snapshots,
                    outcome=search,
                    runner=runner,
                    baseline_attempt=capture.attempt,
                )
            if search.vector == baseline_evaluation.proposal.managed_vector:
                final_evaluation = baseline_evaluation
            else:
                final_run = runner.evaluate_full(search.vector)
                final_evidence = final_run.evidence
                search = self._append_observation(
                    search,
                    vector=search.vector,
                    evidence=final_evidence,
                )
                final_evaluation = final_run.evaluation
                if not isinstance(final_evidence, ProbePass) or not isinstance(
                    final_evaluation, PassEvaluation
                ):
                    return CellSearchFailure(
                        reason="NONDETERMINISTIC",

                        cell=cell,
                        phase="runtime-final",
                        baseline_attempt=capture.attempt,

                        baseline=baseline_evaluation,
                        candidate_snapshots=candidate_snapshots,
                        failure_records=runner.failure_records,
                        failure_runtime_runs=runner.failure_runtime_runs,
                    )
            search = CoordinateSuccess(
                vector=search.vector,
                observations=search.observations,
                boundaries=search.boundaries,
                sweeps=search.sweeps,
            )
            return CellSuccess(

                cell=cell,
                baseline_attempt=capture.attempt,

                baseline=baseline_evaluation,
                candidate_snapshots=candidate_snapshots,
                search=search,
                final_vector=search.vector,
                final_evaluation=final_evaluation,
                failure_records=runner.failure_records,
                failure_runtime_runs=runner.failure_runtime_runs,
            )

    @staticmethod
    def _append_observation(
        search: CoordinateSuccess,
        *,
        vector: tuple[VersionPin, ...],
        evidence: ProbeEvidence,
    ) -> CoordinateSuccess:
        if any(
            observation.vector == vector
            and observation.evidence.attempt == evidence.attempt
            and isinstance(
                observation.evidence,
                (ProbePass, ProbeRejection, ProbeIndeterminate),
            )
            and observation.evidence.status == evidence.status
            for observation in search.observations
        ):
            return search
        return search.model_copy(
            update={
                "observations": (
                    *search.observations,
                    ProbeObservation(
                        dependency=None,
                        candidate_version=None,
                        vector=vector,
                        evidence=evidence,
                        selection_reason=None,
                    ),
                )
            }
        )

    @staticmethod
    def _coordinate_failure(
        *,
        cell: Cell,
        phase: str,
        baseline: PassEvaluation,
        candidates: tuple[CandidateSnapshot, ...],
        outcome: CoordinateFailure,
        runner: _ProposalRunner,
        baseline_attempt: Attempt,
    ) -> CellIndeterminate | CellSearchFailure:
        if outcome.status == "INDETERMINATE":
            assert outcome.failure_id is not None
            return CellIndeterminate(

                cell=cell,
                phase=phase,
                failure_id=outcome.failure_id,
                failure_records=runner.failure_records,
                baseline_attempt=baseline_attempt,

                baseline=baseline,
                candidate_snapshots=candidates,
                coordinate_failure=outcome,
                failure_runtime_runs=runner.failure_runtime_runs,
            )
        return CellSearchFailure(
            reason=outcome.status,

            cell=cell,
            phase=phase,
            baseline_attempt=baseline_attempt,

            baseline=baseline,
            candidate_snapshots=candidates,
            coordinate_failure=outcome,
            failure_records=runner.failure_records,
            failure_runtime_runs=runner.failure_runtime_runs,
        )

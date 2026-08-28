from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Protocol

from pf.coordinate_search import CoordinateProgressConsumer, CoordinateSearch
from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.environment import ExactSelection, PreparedEnvironment, ResolutionRequest
from pf.evaluation import EvaluationCache, require_full_evaluation_contract
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineDetailIdentity,
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
    CellFailureScope,
    HighestVersionOutcome,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    ProcessObservation,
    ProcessTerminalUnavailable,
    PrepareFailure,
    RuntimeInterfaceMissingEvaluation,
    RuntimeEvaluationRun,
    SearchFailureEvent,
    SearchProbeRequest,
    SearchProbeDetailIdentity,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticRegressionEvaluation,
    StaticUnchangedEvaluation,
    VerifierRejectedEvaluation,
    ToolFailure,
    runtime_process_observation,
)
from pf.schemas.project import (
    CandidateSnapshot,
    Cell,
    HarnessBaseline,
    PackagePlan,
    SelectedCandidate,
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
    StaticOnlyEvidence,
    StaticRegion,
    StaticRegionRuntimeReference,
    StaticRegionSlice,
)
from pf.snapshot import SourceSnapshot


def select_probe(
    vector: tuple[VersionPin, ...],
    snapshots: tuple[CandidateSnapshot, ...],
) -> tuple[SelectedCandidate, ...]:
    """Bind an exact vector to the frozen artifact selected for each version."""
    vector_by_name = {pin.name: pin.version for pin in vector}
    snapshot_by_name = {snapshot.dependency: snapshot for snapshot in snapshots}
    if len(vector_by_name) != len(vector) or len(snapshot_by_name) != len(snapshots):
        raise ConfigurationError("probe dependencies must be unique")
    if set(vector_by_name) != set(snapshot_by_name):
        raise ConfigurationError(
            "probe vector and candidate snapshots must cover the same dependencies"
        )
    selected: list[SelectedCandidate] = []
    for dependency in sorted(vector_by_name):
        matches = tuple(
            candidate
            for candidate in snapshot_by_name[dependency].candidates
            if candidate.version == vector_by_name[dependency]
        )
        if len(matches) != 1:
            raise ConfigurationError(
                f"probe version is not uniquely frozen for dependency: {dependency}"
            )
        candidate = matches[0]
        artifact = candidate.artifact
        if (
            not artifact.filename.strip()
            or artifact.locator is None
            or not artifact.locator.strip()
            or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", artifact.content_hash) is None
        ):
            raise ConfigurationError(
                f"probe artifact is incomplete for dependency: {dependency}"
            )
        selected.append(
            SelectedCandidate(
                dependency=dependency,
                version=candidate.version,
                artifact=artifact,
            )
        )
    return tuple(selected)


class SearchEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: ResolutionRequest,
    ) -> PreparedEnvironment | PrepareFailure: ...


class CandidateOperations(Protocol):
    def build(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        baseline: tuple[VersionPin, ...],
    ) -> tuple[CandidateSnapshot, ...]: ...


class StaticOperations(Protocol):
    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture | IndeterminateEvaluation: ...

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
    ) -> StaticEvaluation: ...


class FullOperations(Protocol):
    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> RuntimeEvaluationRun: ...


class HighestOperations(Protocol):
    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> HighestVersionOutcome: ...


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


@dataclass(frozen=True)
class _RegionPoint:
    index: int
    version: str
    static: StaticUnchangedEvaluation | StaticRegressionEvaluation
    evidence: ProbeEvidence | None


class _RuntimeBackedVectorEvaluator:
    def __init__(self, runner: "_ProposalRunner") -> None:
        self._runner = runner

    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        return self._runner.evaluate_full(vector).evidence

    def evaluate_in_slice(
        self,
        request: SearchProbeRequest,
    ) -> ProbeEvidence | StaticOnlyEvidence:
        return self._runner.evaluate_in_slice(request)

    def promote(
        self,
        request: SearchProbeRequest,
    ) -> ProbeEvidence:
        return self._runner.promote(request)

    @property
    def regions(self) -> tuple[StaticRegion, ...]:
        return self._runner.regions


class _ProposalRunner:
    def __init__(
        self,
        *,
        environments: SearchEnvironmentOperations,
        static: StaticOperations,
        full: FullOperations,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        static_baseline: StaticBaseline,
        harness_baseline: HarnessBaseline,
        candidate_snapshots: tuple[CandidateSnapshot, ...],
        diagnostics: SearchDiagnosticConsumer | None = None,
        events: SearchActivityConsumer | None = None,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._package = package
        self._cell = cell
        self._snapshot = snapshot
        self._static_baseline = static_baseline
        self._harness_baseline = harness_baseline
        self._candidate_snapshots = candidate_snapshots
        self._diagnostics = diagnostics
        self._events = events
        self._failures = failures or FailurePolicy()
        self._failure_records: dict[str, FailureRecord] = {}
        self._failure_runtime_runs: dict[str, FailureRuntimeRun] = {}
        self._emitted_diagnostics: set[str] = set()
        self._cache = EvaluationCache()
        self._prepared: dict[tuple[tuple[str, str], ...], PreparedEnvironment] = {}
        self._prepare_failures: dict[tuple[tuple[str, str], ...], PrepareFailure] = {}
        self._full_runs: dict[tuple[tuple[str, str], ...], ProbeRun] = {}
        self._snapshot_by_dependency = {
            item.dependency: item for item in candidate_snapshots
        }
        self._region_points: dict[StaticRegionSlice, dict[int, _RegionPoint]] = {}

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
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            run = ProbeRun(
                evidence=self._prepare_evidence(prepared),
                evaluation=None,
            )
            self._full_runs[key] = run
            return run
        assert prepared.attempt is not None
        try:
            static = self._cache.get_static(
                prepared.proposal.proposal_id,
                baseline_digest=self._static_baseline.digest,
            )
            runtime = self._full.evaluate(
                prepared,
                package=self._package,
                baseline=self._static_baseline,
                static_result=static,
            )
            stored = self._cache.record_full(
                runtime.evaluation,
                baseline_digest=self._static_baseline.digest,
            )
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

    def evaluate_in_slice(
        self,
        request: SearchProbeRequest,
    ) -> ProbeEvidence | StaticOnlyEvidence:
        vector = request.vector
        dependency = request.active_dependency
        key = self._key(vector)
        existing = self._full_runs.get(key)
        if existing is not None:
            return existing.evidence
        self._emit_probe_context(request)
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            return self._prepare_evidence(prepared)
        assert prepared.attempt is not None
        try:
            static = self._cache.get_static(
                prepared.proposal.proposal_id,
                baseline_digest=self._static_baseline.digest,
            )
            if static is None:
                evaluated = self._static.evaluate(
                    prepared,
                    package=self._package,
                    baseline=self._static_baseline,
                )
                stored = self._cache.record_static(
                    evaluated,
                    baseline_digest=self._static_baseline.digest,
                )
                if isinstance(stored, CacheConflict):
                    return self._indeterminate_evidence(
                        attempt=prepared.attempt,
                        proposal_id=prepared.proposal.proposal_id,
                        stage="static-cache",
                        detail=FailureDetail(
                            code="conflicting-static-evaluation",
                            message=(
                                "the same proposal produced conflicting static results"
                            ),
                        ),
                    )
                static = stored
            if isinstance(static, IndeterminateEvaluation):
                return self._static_evidence(prepared, static)
            region_slice, index, version = self._region_slice(vector, dependency)
            guidance = self._region_guidance(
                region_slice,
                index=index,
                fingerprint=static.static_fingerprint,
            )
            if guidance is not None:
                status, representative = guidance
                self._record_region_point(
                    region_slice,
                    _RegionPoint(
                        index=index,
                        version=version,
                        static=static,
                        evidence=None,
                    ),
                )
                return StaticOnlyEvidence(
                    attempt=prepared.attempt,
                    proposal_id=prepared.proposal.proposal_id,
                    static_evaluation=static,
                    guidance=status,
                    region_slice=region_slice,
                    representative_proposal_id=representative,
                )
            runtime = self._full.evaluate(
                prepared,
                package=self._package,
                baseline=self._static_baseline,
                static_result=static,
            )
            stored_full = self._cache.record_full(
                runtime.evaluation,
                baseline_digest=self._static_baseline.digest,
            )
            if isinstance(stored_full, CacheConflict):
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
                    evidence=self._full_evidence(
                        prepared,
                        stored_full,
                        runtime=runtime,
                    ),
                    evaluation=stored_full,
                    runtime=runtime,
                )
            self._full_runs[key] = run
            direct = (
                run.evidence
                if run.evidence.proposal_id is not None
                else None
            )
            self._record_region_point(
                region_slice,
                _RegionPoint(
                    index=index,
                    version=version,
                    static=static,
                    evidence=direct,
                ),
            )
            return run.evidence
        finally:
            prepared.close()
            self._prepared.pop(key, None)

    def promote(
        self,
        request: SearchProbeRequest,
    ) -> ProbeEvidence:
        vector = request.vector
        dependency = request.active_dependency
        run = self.evaluate_full(vector, request=request)
        region_slice, index, _ = self._region_slice(vector, dependency)
        point = self._region_points.get(region_slice, {}).get(index)
        if point is not None and run.evidence.proposal_id is not None:
            self._record_region_point(
                region_slice,
                _RegionPoint(
                    index=point.index,
                    version=point.version,
                    static=point.static,
                    evidence=run.evidence,
                ),
            )
        return run.evidence

    def _emit_probe_context(self, request: SearchProbeRequest) -> None:
        if self._events is None:
            return
        self._events.consume(
            CellContextEvent(
                cell=self._cell,
                detail=SearchProbeDetailIdentity(
                    dependency=request.active_dependency,
                    version=request.candidate_version,
                    lower_version=request.lower_version,
                    upper_version=request.upper_version,
                    candidate_count=request.candidate_count,
                ),
            )
        )

    @property
    def regions(self) -> tuple[StaticRegion, ...]:
        regions: list[StaticRegion] = []
        for region_slice in sorted(
            self._region_points,
            key=lambda item: (
                item.active_dependency,
                tuple((pin.name, pin.version) for pin in item.other_coordinates),
            ),
        ):
            points = sorted(
                self._region_points[region_slice].values(),
                key=lambda item: item.index,
            )
            component: list[_RegionPoint] = []
            for point in points:
                if component and (
                    point.index != component[-1].index + 1
                    or point.static.static_fingerprint
                    != component[-1].static.static_fingerprint
                ):
                    regions.append(self._build_region(region_slice, component))
                    component = []
                component.append(point)
            if component:
                regions.append(self._build_region(region_slice, component))
        return tuple(regions)

    def _region_slice(
        self,
        vector: tuple[VersionPin, ...],
        dependency: str,
    ) -> tuple[StaticRegionSlice, int, str]:
        snapshot = self._snapshot_by_dependency[dependency]
        order = tuple(candidate.version for candidate in snapshot.candidates)
        versions = {pin.name: pin.version for pin in vector}
        version = versions[dependency]
        return (
            StaticRegionSlice(
                cell=self._cell,
                source_snapshot_digest=self._static_baseline.proposal.snapshot_digest,
                policy_identity=self._static_baseline.proposal.policy_identity,
                baseline_digest=self._static_baseline.digest,
                active_dependency=dependency,
                other_coordinates=tuple(
                    VersionPin(name=name, version=versions[name])
                    for name in sorted(versions)
                    if name != dependency
                ),
                candidate_order=order,
            ),
            order.index(version),
            version,
        )

    def _region_guidance(
        self,
        region_slice: StaticRegionSlice,
        *,
        index: int,
        fingerprint: str,
    ) -> tuple[Literal["PASS", "REJECTED"], str] | None:
        points = self._region_points.get(region_slice, {})
        component: list[_RegionPoint] = []
        cursor = index - 1
        while (
            cursor in points
            and points[cursor].static.static_fingerprint == fingerprint
        ):
            component.append(points[cursor])
            cursor -= 1
        cursor = index + 1
        while (
            cursor in points
            and points[cursor].static.static_fingerprint == fingerprint
        ):
            component.append(points[cursor])
            cursor += 1
        direct = sorted(
            (
                point
                for point in component
                if isinstance(point.evidence, (ProbePass, ProbeRejection))
            ),
            key=lambda item: item.index,
        )
        statuses = {point.evidence.status for point in direct if point.evidence}
        if len(statuses) != 1 or not direct:
            return None
        status = next(iter(statuses))
        if status == "PASS":
            guidance: Literal["PASS", "REJECTED"] = "PASS"
        elif status == "REJECTED":
            guidance = "REJECTED"
        else:
            return None
        assert direct[0].evidence is not None
        proposal_id = direct[0].evidence.proposal_id
        if proposal_id is None:
            return None
        return guidance, proposal_id

    def _record_region_point(
        self,
        region_slice: StaticRegionSlice,
        point: _RegionPoint,
    ) -> None:
        points = self._region_points.setdefault(region_slice, {})
        existing = points.get(point.index)
        if existing is not None and (
            existing.version != point.version or existing.static != point.static
        ):
            raise ValueError("static region point changed within one search")
        if existing is None or point.evidence is not None:
            points[point.index] = point

    @staticmethod
    def _build_region(
        region_slice: StaticRegionSlice,
        points: list[_RegionPoint],
    ) -> StaticRegion:
        references = tuple(
            StaticRegionRuntimeReference(
                proposal_id=point.evidence.proposal_id,
                status=point.evidence.status,
            )
            for point in points
            if point.evidence is not None and point.evidence.proposal_id is not None
        )
        unique_references = tuple(
            dict.fromkeys(
                (reference.proposal_id, reference.status) for reference in references
            )
        )
        fingerprint = points[0].static.static_fingerprint
        return StaticRegion(
            slice=region_slice,
            static_fingerprint=fingerprint,
            observed_versions=tuple(point.version for point in points),
            runtime_references=tuple(
                StaticRegionRuntimeReference(proposal_id=proposal_id, status=status)
                for proposal_id, status in unique_references
            ),
        )

    @property
    def failure_records(self) -> tuple[FailureRecord, ...]:
        return tuple(self._failure_records.values())

    @property
    def failure_runtime_runs(self) -> tuple[FailureRuntimeRun, ...]:
        return tuple(self._failure_runtime_runs.values())

    def failure_record(self, failure_id: str) -> FailureRecord:
        return self._failure_records[failure_id]

    def close(self) -> None:
        for prepared in self._prepared.values():
            prepared.close()
        self._prepared.clear()

    def _record(
        self,
        failure: FailureRecord,
        *,
        evaluation: RuntimeInterfaceMissingEvaluation
        | VerifierRejectedEvaluation
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
        elif isinstance(runtime_process, ProcessTerminalUnavailable):
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
        )
        if isinstance(prepared, PrepareFailure):
            self._prepare_failures[key] = prepared
            return prepared
        if isinstance(prepared, ToolFailure):
            raise ValueError("probe prepare must establish an Attempt")
        if prepared.attempt is None:
            prepared.close()
            raise ValueError("probe prepare must retain its attempt")
        if self._key(prepared.proposal.managed_vector) != key:
            prepared.close()
            return PrepareFailure(
                attempt=prepared.attempt,
                failure=ToolFailure(
                    cause="INTERNAL_INVARIANT",
                    stage="proposal-vector",
                    process=self._synthetic_process(),
                ),
                project_plan_digest=prepared.project_plan.semantic_digest,
                environment_plan_digest=prepared.environment_plan.semantic_digest,
            )
        self._prepared[key] = prepared
        return prepared

    def _prepare_evidence(self, prepared: PrepareFailure) -> ProbeEvidence:
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=None,
            cause=prepared.failure.cause,
            stage=prepared.failure.stage,
            process=prepared.failure.process,
            summary_code=prepared.failure.summary_code,
            project_plan_digest=prepared.project_plan_digest,
            environment_plan_digest=prepared.environment_plan_digest,
            evaluation=None,
        )

    def _static_evidence(
        self,
        prepared: PreparedEnvironment,
        result: IndeterminateEvaluation,
    ) -> ProbeEvidence:
        assert prepared.attempt is not None
        failure = self._failures.record_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            result,
            project_plan_digest=prepared.project_plan.semantic_digest,
            environment_plan_digest=prepared.environment_plan.semantic_digest,
        )
        assert failure is not None
        assert result.failure is not None
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=result.proposal.proposal_id,
            cause=failure.cause,
            stage=failure.stage,
            process=result.failure.process,
            summary_code=failure.summary_code,
            evaluation=result,
            record=failure,
        )

    def _full_evidence(
        self,
        prepared: PreparedEnvironment,
        result: Evaluation,
        *,
        runtime: RuntimeEvaluationRun,
    ) -> ProbeEvidence:
        assert prepared.attempt is not None
        if isinstance(result, PassEvaluation):
            return self._pass_evidence(prepared.attempt, result)
        failure = self._failures.record_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            result,
            project_plan_digest=prepared.project_plan.semantic_digest,
            environment_plan_digest=prepared.environment_plan.semantic_digest,
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
                else result.failure.process
                if isinstance(result, IndeterminateEvaluation)
                and result.failure is not None
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
        evaluation: RuntimeInterfaceMissingEvaluation
        | VerifierRejectedEvaluation
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
        environments: SearchEnvironmentOperations,
        candidates: CandidateOperations,
        static: StaticOperations,
        full: FullOperations,
        highest: HighestOperations,
        coordinate_search: CoordinateSearch,
        diagnostics: SearchDiagnosticConsumer | None = None,
        events: SearchActivityConsumer | None = None,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._candidates = candidates
        self._static = static
        self._full = full
        self._failures = failures or FailurePolicy()
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
    ) -> CellResult:
        require_full_evaluation_contract(package, "search")
        if self._events is not None:
            self._events.consume(
                CellContextEvent(cell=cell, detail=BaselineDetailIdentity())
            )
        capture = self._highest.verify(
            package=package,
            cell=cell,
            snapshot=snapshot,
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
            )
        except InfrastructureError:
            failure = self._failures.classify(
                scope=CellFailureScope(
                    package=package.name,
                    cell=cell,
                    source_snapshot_digest=snapshot.identity.digest,
                    evaluation_policy_identity=baseline_evaluation.proposal.policy_identity,
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
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
            )
        except NoApplicableFloorError:
            return CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=cell,
                phase="candidate-discovery",
                baseline_attempt=capture.attempt,
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
            )
        with _ProposalRunner(
            environments=self._environments,
            static=self._static,
            full=self._full,
            package=package,
            cell=cell,
            snapshot=snapshot,
            static_baseline=capture.baseline,
            harness_baseline=capture.harness_baseline,
            candidate_snapshots=candidate_snapshots,
            diagnostics=self._diagnostics,
            events=self._events,
            failures=self._failures,
        ) as runner:
            search = self._coordinate_search.minimize(
                start=baseline_evaluation.proposal.managed_vector,
                candidates=candidate_snapshots,
                evaluator=_RuntimeBackedVectorEvaluator(runner),
                start_is_known_pass=True,
                progress=coordinate_progress,
            )
            if isinstance(search, CoordinateFailure):
                search = CoordinateFailure.model_validate(
                    {**search.model_dump(), "regions": runner.regions}
                )
                return self._coordinate_failure(
                    cell=cell,
                    phase="runtime-search",
                    static_baseline=capture.baseline,
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
                        static_baseline=capture.baseline,
                        baseline=baseline_evaluation,
                        candidate_snapshots=candidate_snapshots,
                        failure_records=runner.failure_records,
                        failure_runtime_runs=runner.failure_runtime_runs,
                    )
            search = CoordinateSuccess.model_validate(
                {**search.model_dump(), "regions": runner.regions}
            )
            return CellSuccess(
                cell=cell,
                baseline_attempt=capture.attempt,
                static_baseline=capture.baseline,
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
                    ),
                )
            }
        )

    @staticmethod
    def _coordinate_failure(
        *,
        cell: Cell,
        phase: str,
        static_baseline: StaticBaseline,
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
                static_baseline=static_baseline,
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
            static_baseline=static_baseline,
            baseline=baseline,
            candidate_snapshots=candidates,
            coordinate_failure=outcome,
            failure_records=runner.failure_records,
            failure_runtime_runs=runner.failure_runtime_runs,
        )

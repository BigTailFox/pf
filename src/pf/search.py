from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol

from packaging.version import Version

from pf.baseline import HighestVersionVerifier
from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.environment import PreparedEnvironment
from pf.evaluation import EvaluationCache, require_full_evaluation_contract
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CacheConflict,
    Evaluation,
    FailureDetail,
    FailureCause,
    FailureRecord,
    CellFailureScope,
    HighestVersionOutcome,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    PrepareFailure,
    SearchFailureEvent,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFailEvaluation,
    ToolFailure,
)
from pf.schemas.project import (
    CandidateSnapshot,
    Cell,
    PackagePlan,
    VersionPin,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateOutcome,
    CoordinateSuccess,
    ProbeEvidence,
    ProbeIndeterminate,
    ProbeObservation,
    ProbePass,
    ProbeRejection,
)
from pf.snapshot import SourceSnapshot


class VectorEvaluator(Protocol):
    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence: ...


@dataclass
class _SearchStopped(Exception):
    result: CoordinateFailure


@dataclass(frozen=True)
class _KnownPass:
    status: Literal["PASS"] = "PASS"


SearchEvidence = ProbeEvidence | _KnownPass


class CoordinateSearch:
    """Find a deterministic coordinate fixpoint over frozen discrete versions."""

    def __init__(self, *, small_threshold: int = 8) -> None:
        if small_threshold < 1:
            raise ValueError("small_threshold must be positive")
        self.small_threshold = small_threshold

    def minimize(
        self,
        *,
        start: tuple[VersionPin, ...],
        candidates: tuple[CandidateSnapshot, ...],
        evaluator: VectorEvaluator,
        hints: tuple[VersionPin, ...] = (),
        start_is_known_pass: bool = False,
    ) -> CoordinateOutcome:
        self._evaluator = evaluator
        self._evidence_cache: dict[tuple[tuple[str, str], ...], ProbeEvidence] = {}
        self._observations: list[ProbeObservation] = []
        self._observation_keys: set[tuple[str | None, tuple[tuple[str, str], ...]]] = (
            set()
        )
        self._slice_observations: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[Version, str]
        ] = {}
        snapshot_by_name = {snapshot.dependency: snapshot for snapshot in candidates}
        current = {pin.name: pin.version for pin in start}
        if tuple(sorted(current)) != tuple(snapshot_by_name):
            raise ConfigurationError(
                "start vector and candidate coordinates must match"
            )
        hint_by_name = {pin.name: pin.version for pin in hints}
        self._known_pass_keys = (
            {tuple((pin.name, pin.version) for pin in self._vector(current))}
            if start_is_known_pass
            else set()
        )
        try:
            baseline = self._probe(current, dependency=None)
            if baseline.status != "PASS":
                return CoordinateFailure(
                    status="NONDETERMINISTIC",
                    observations=tuple(self._observations),
                )
            sweeps = 0
            final_boundaries: dict[str, CoordinateBoundary] = {}
            while True:
                sweeps += 1
                changed = False
                round_boundaries: dict[str, CoordinateBoundary] = {}
                for dependency in sorted(snapshot_by_name):
                    floor, boundary = self._find_floor(
                        current=current,
                        snapshot=snapshot_by_name[dependency],
                        hint=hint_by_name.get(dependency),
                    )
                    round_boundaries[dependency] = boundary
                    if Version(floor) < Version(current[dependency]):
                        current[dependency] = floor
                        changed = True
                final_boundaries = round_boundaries
                if not changed:
                    break
            return CoordinateSuccess(
                vector=self._vector(current),
                observations=tuple(self._observations),
                boundaries=tuple(
                    final_boundaries[name] for name in sorted(final_boundaries)
                ),
                sweeps=sweeps,
            )
        except _SearchStopped as stopped:
            return stopped.result

    def _find_floor(
        self,
        *,
        current: dict[str, str],
        snapshot: CandidateSnapshot,
        hint: str | None,
    ) -> tuple[str, CoordinateBoundary]:
        dependency = snapshot.dependency
        current_version = Version(current[dependency])
        versions = [
            Version(candidate.version)
            for candidate in snapshot.candidates
            if Version(candidate.version) <= current_version
        ]
        if not versions:
            self._stop("NO_PASS_IN_SEARCH_SPACE", dependency=dependency)
        if versions[0] == current_version:
            return str(current_version), CoordinateBoundary(
                dependency=dependency,
                floor=str(current_version),
            )

        probe_hint = versions[0]
        if hint is not None:
            requested = Version(hint)
            eligible_hints = [version for version in versions if version <= requested]
            if eligible_hints:
                probe_hint = eligible_hints[-1]
        hint_evidence = self._probe_version(current, dependency, probe_hint)
        if hint_evidence.status == "PASS":
            if probe_hint == versions[0]:
                floor = probe_hint
            else:
                earliest_evidence = self._probe_version(
                    current, dependency, versions[0]
                )
                if earliest_evidence.status == "PASS":
                    floor = versions[0]
                else:
                    floor = self._locate(
                        current=current,
                        dependency=dependency,
                        points=[
                            version
                            for version in versions
                            if versions[0] <= version <= probe_hint
                        ],
                        low=versions[0],
                        high=probe_hint,
                    )
        else:
            points = [version for version in versions if version >= probe_hint]
            high_evidence = self._probe_version(current, dependency, current_version)
            if high_evidence.status != "PASS":
                self._stop("NONDETERMINISTIC", dependency=dependency)
            floor = self._locate(
                current=current,
                dependency=dependency,
                points=points,
                low=probe_hint,
                high=current_version,
            )
        if floor is None or floor not in versions:
            self._stop("NO_PASS_IN_SEARCH_SPACE", dependency=dependency)
        index = versions.index(floor)
        if index == 0:
            boundary = CoordinateBoundary(
                dependency=dependency,
                floor=str(floor),
            )
        else:
            predecessor = versions[index - 1]
            evidence = self._probe_version(current, dependency, predecessor)
            if not isinstance(evidence, ProbeRejection):
                self._stop("NONDETERMINISTIC", dependency=dependency)
            boundary = CoordinateBoundary(
                dependency=dependency,
                floor=str(floor),
                predecessor=str(predecessor),
                predecessor_failure_id=evidence.failure_id,
            )
        return str(floor), boundary

    def _locate(
        self,
        *,
        current: dict[str, str],
        dependency: str,
        points: list[Version],
        low: Version,
        high: Version,
    ) -> Version | None:
        low_index = points.index(low)
        virtual_high = high not in points
        high_index = len(points) if virtual_high else points.index(high)
        if high_index - low_index <= self.small_threshold:
            candidate_high = min(high_index, len(points) - 1)
            for version in points[low_index + 1 : candidate_high + 1]:
                evidence = self._probe_version(current, dependency, version)
                if evidence.status == "PASS":
                    return version
            return None if virtual_high else points[high_index]
        while high_index - low_index > 1:
            middle = (low_index + high_index) // 2
            evidence = self._probe_version(current, dependency, points[middle])
            if evidence.status == "PASS":
                high_index = middle
            else:
                low_index = middle
        return None if high_index == len(points) else points[high_index]

    def _probe_version(
        self,
        current: dict[str, str],
        dependency: str,
        version: Version,
    ) -> SearchEvidence:
        vector = dict(current)
        vector[dependency] = str(version)
        return self._probe(vector, dependency=dependency)

    def _probe(
        self,
        versions: dict[str, str],
        *,
        dependency: str | None,
    ) -> SearchEvidence:
        vector = self._vector(versions)
        vector_key = tuple((pin.name, pin.version) for pin in vector)
        if vector_key in self._known_pass_keys:
            return _KnownPass()
        evidence = self._evidence_cache.get(vector_key)
        if evidence is None:
            evidence = self._evaluator.evaluate(vector)
            self._evidence_cache[vector_key] = evidence
        observation_key = (dependency, vector_key)
        if observation_key not in self._observation_keys:
            self._observation_keys.add(observation_key)
            candidate_version = versions[dependency] if dependency is not None else None
            self._observations.append(
                ProbeObservation(
                    dependency=dependency,
                    candidate_version=candidate_version,
                    vector=vector,
                    evidence=evidence,
                )
            )
        if isinstance(evidence, ProbeIndeterminate):
            self._stop(
                "INDETERMINATE",
                dependency=dependency,
                failure_id=evidence.failure_id,
            )
        if dependency is not None:
            slice_key = (
                dependency,
                tuple(
                    (name, version)
                    for name, version in vector_key
                    if name != dependency
                ),
            )
            points = self._slice_observations.setdefault(slice_key, {})
            points[Version(versions[dependency])] = evidence.status
            for low, low_status in points.items():
                for high, high_status in points.items():
                    if (
                        low < high
                        and low_status == "PASS"
                        and high_status == "REJECTED"
                    ):
                        self._stop(
                            "NON_MONOTONIC",
                            dependency=dependency,
                            counterexample=(str(low), str(high)),
                        )
        return evidence

    def _stop(
        self,
        status: str,
        *,
        dependency: str | None,
        counterexample: tuple[str, str] | None = None,
        failure_id: str | None = None,
    ) -> NoReturn:
        raise _SearchStopped(
            CoordinateFailure.model_validate(
                {
                    "status": status,
                    "dependency": dependency,
                    "observations": tuple(self._observations),
                    "counterexample": counterexample,
                    "failure_id": failure_id,
                }
            )
        )

    @staticmethod
    def _vector(versions: dict[str, str]) -> tuple[VersionPin, ...]:
        return tuple(
            VersionPin(name=name, version=versions[name]) for name in sorted(versions)
        )


class SearchEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
        managed_vector: tuple[VersionPin, ...] | None = None,
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
    ) -> Evaluation: ...


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


class _StaticVectorEvaluator:
    def __init__(self, runner: "_ProposalRunner") -> None:
        self._runner = runner

    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        return self._runner.evaluate_static(vector)


class _FullVectorEvaluator:
    def __init__(self, runner: "_ProposalRunner") -> None:
        self._runner = runner

    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        return self._runner.evaluate_full(vector)


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
        diagnostics: SearchDiagnosticConsumer | None = None,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._package = package
        self._cell = cell
        self._snapshot = snapshot
        self._static_baseline = static_baseline
        self._diagnostics = diagnostics
        self._failures = failures or FailurePolicy()
        self._failure_records: dict[str, FailureRecord] = {}
        self._emitted_diagnostics: set[str] = set()
        self._cache = EvaluationCache()
        self._prepared: dict[tuple[tuple[str, str], ...], PreparedEnvironment] = {}
        self._prepare_failures: dict[tuple[tuple[str, str], ...], PrepareFailure] = {}
        self._attempts: dict[tuple[tuple[str, str], ...], Attempt] = {}
        self._evaluations: dict[tuple[tuple[str, str], ...], Evaluation] = {}
        self._full_evidence_by_key: dict[
            tuple[tuple[str, str], ...], ProbeEvidence
        ] = {}

    def evaluate_static(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        key = self._key(vector)
        full = self._evaluations.get(key)
        if isinstance(full, PassEvaluation):
            return self._pass_evidence(
                self._attempts[key],
                full,
            )
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            return self._prepare_evidence(prepared)
        assert prepared.attempt is not None
        cached = self._cache.get_static(
            prepared.proposal.proposal_id,
            baseline_digest=self._static_baseline.digest,
        )
        if cached is None:
            result = self._static.evaluate(
                prepared,
                package=self._package,
                baseline=self._static_baseline,
            )
            stored = self._cache.record_static(
                result,
                baseline_digest=self._static_baseline.digest,
            )
            if isinstance(stored, CacheConflict):
                prepared.close()
                self._prepared.pop(key, None)
                return self._indeterminate_evidence(
                    attempt=prepared.attempt,
                    proposal_id=prepared.proposal.proposal_id,
                    stage="static-cache",
                    detail=FailureDetail(
                        code="conflicting-static-evaluation",
                        message="the same proposal produced conflicting static results",
                    ),
                )
        else:
            result = cached
        evidence = self._static_evidence(prepared, result)
        if not isinstance(result, StaticPassEvaluation):
            prepared.close()
            self._prepared.pop(key, None)
        return evidence

    def evaluate_full(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        key = self._key(vector)
        existing = self._full_evidence_by_key.get(key)
        if existing is not None:
            return existing
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            return self._prepare_evidence(prepared)
        assert prepared.attempt is not None
        static = self._cache.get_static(
            prepared.proposal.proposal_id,
            baseline_digest=self._static_baseline.digest,
        )
        result = self._full.evaluate(
            prepared,
            package=self._package,
            baseline=self._static_baseline,
            static_result=static,
        )
        stored = self._cache.record_full(
            result,
            baseline_digest=self._static_baseline.digest,
        )
        if isinstance(stored, CacheConflict):
            evidence = self._indeterminate_evidence(
                attempt=prepared.attempt,
                proposal_id=prepared.proposal.proposal_id,
                stage="full-cache",
                detail=FailureDetail(
                    code="conflicting-full-evaluation",
                    message="the same proposal produced conflicting full results",
                ),
            )
        else:
            self._evaluations[key] = stored
            evidence = self._full_evidence(prepared, stored)
        self._full_evidence_by_key[key] = evidence
        prepared.close()
        self._prepared.pop(key, None)
        return evidence

    def full_evaluation(self, vector: tuple[VersionPin, ...]) -> Evaluation | None:
        return self._evaluations.get(self._key(vector))

    @property
    def failure_records(self) -> tuple[FailureRecord, ...]:
        return tuple(self._failure_records.values())

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
        evaluation: StaticFailEvaluation
        | TestFailEvaluation
        | IndeterminateEvaluation
        | None,
    ) -> None:
        existing = self._failure_records.get(failure.failure_id)
        if existing is not None and existing != failure:
            raise ValueError("failure ID collision within one cell search")
        self._failure_records.setdefault(failure.failure_id, failure)
        if self._diagnostics is None or failure.failure_id in self._emitted_diagnostics:
            return
        self._emitted_diagnostics.add(failure.failure_id)
        self._diagnostics.consume(
            SearchFailureEvent(
                cell=self._cell,
                failure=failure,
                evaluation=evaluation,
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
            resolution="highest",
            managed_vector=vector,
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
            )
        self._prepared[key] = prepared
        self._attempts[key] = prepared.attempt
        return prepared

    def _prepare_evidence(self, prepared: PrepareFailure) -> ProbeEvidence:
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=None,
            cause=prepared.failure.cause,
            stage=prepared.failure.stage,
            process=prepared.failure.process,
            summary_code=prepared.failure.summary_code,
            evaluation=None,
        )

    def _static_evidence(
        self,
        prepared: PreparedEnvironment,
        result: StaticEvaluation,
    ) -> ProbeEvidence:
        assert prepared.attempt is not None
        if isinstance(result, StaticPassEvaluation):
            return self._pass_evidence(prepared.attempt, result)
        if isinstance(result, StaticFailEvaluation):
            return self._failure_evidence(
                attempt=prepared.attempt,
                proposal_id=result.proposal.proposal_id,
                cause="STATIC_REGRESSION",
                stage="ty",
                process=result.ty.process,
                evaluation=result,
            )
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=result.proposal.proposal_id,
            cause=result.cause,
            stage=result.failure.stage,
            process=result.failure.process,
            summary_code=result.failure.summary_code,
            evaluation=result,
        )

    def _full_evidence(
        self,
        prepared: PreparedEnvironment,
        result: Evaluation,
    ) -> ProbeEvidence:
        assert prepared.attempt is not None
        if isinstance(result, PassEvaluation):
            return self._pass_evidence(prepared.attempt, result)
        if isinstance(result, StaticFailEvaluation):
            return self._failure_evidence(
                attempt=prepared.attempt,
                proposal_id=result.proposal.proposal_id,
                cause="STATIC_REGRESSION",
                stage="ty",
                process=result.ty.process,
                evaluation=result,
            )
        if isinstance(result, TestFailEvaluation):
            return self._failure_evidence(
                attempt=prepared.attempt,
                proposal_id=result.proposal.proposal_id,
                cause="TEST_FAILURE",
                stage="test",
                process=result.test.process,
                evaluation=result,
            )
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=result.proposal.proposal_id,
            cause=result.cause,
            stage=result.failure.stage,
            process=result.failure.process,
            summary_code=result.failure.summary_code,
            evaluation=result,
        )

    @staticmethod
    def _pass_evidence(
        attempt: Attempt,
        evaluation: StaticPassEvaluation | PassEvaluation,
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
        process: ProcessResult | None,
        evaluation: StaticFailEvaluation
        | TestFailEvaluation
        | IndeterminateEvaluation
        | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
    ) -> ProbeEvidence:
        failure = self._failures.classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
        )
        self._record(failure, evaluation=evaluation)
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
    """Own the complete baseline, static, fast-path, and dynamic cell state machine."""

    def __init__(
        self,
        *,
        environments: SearchEnvironmentOperations,
        candidates: CandidateOperations,
        static: StaticOperations,
        full: FullOperations,
        highest: HighestOperations | None = None,
        coordinate_search: CoordinateSearch | None = None,
        diagnostics: SearchDiagnosticConsumer | None = None,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._candidates = candidates
        self._static = static
        self._full = full
        self._failures = failures or FailurePolicy()
        self._highest = highest or HighestVersionVerifier(
            environments=environments,
            static=static,
            full=full,
            failures=self._failures,
        )
        self._diagnostics = diagnostics
        self._coordinate_threshold = (
            coordinate_search.small_threshold if coordinate_search is not None else 8
        )

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CellResult:
        require_full_evaluation_contract(package, "search")
        capture = self._highest.verify(
            package=package,
            cell=cell,
            snapshot=snapshot,
        )
        if isinstance(capture, (BaselineRejection, BaselineIndeterminate)):
            return capture
        baseline_evaluation = capture.evaluation
        try:
            candidate_snapshots = self._candidates.build(
                package=package,
                cell=cell,
                baseline=baseline_evaluation.proposal.managed_vector,
            )
        except InfrastructureError as error:
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
                    message=(
                        f"{error}: {error.detail}" if error.detail else str(error)
                    ),
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
        runner = _ProposalRunner(
            environments=self._environments,
            static=self._static,
            full=self._full,
            package=package,
            cell=cell,
            snapshot=snapshot,
            static_baseline=capture.baseline,
            diagnostics=self._diagnostics,
            failures=self._failures,
        )
        try:
            static_search = CoordinateSearch(
                small_threshold=self._coordinate_threshold
            ).minimize(
                start=baseline_evaluation.proposal.managed_vector,
                candidates=candidate_snapshots,
                evaluator=_StaticVectorEvaluator(runner),
                start_is_known_pass=True,
            )
            if isinstance(static_search, CoordinateFailure):
                return self._coordinate_failure(
                    cell=cell,
                    phase="static-search",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidates=candidate_snapshots,
                    outcome=static_search,
                    runner=runner,
                    baseline_attempt=capture.attempt,
                )
            fast_evidence = runner.evaluate_full(static_search.vector)
            static_search = self._append_observation(
                static_search,
                vector=static_search.vector,
                evidence=fast_evidence,
            )
            fast_evaluation = runner.full_evaluation(static_search.vector)
            if fast_evidence.status == "PASS" and isinstance(
                fast_evaluation, PassEvaluation
            ):
                return CellSuccess(
                    cell=cell,
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    static_search=static_search,
                    final_vector=static_search.vector,
                    final_evaluation=fast_evaluation,
                    failure_records=runner.failure_records,
                )
            if isinstance(fast_evidence, ProbeIndeterminate):
                return CellIndeterminate(
                    cell=cell,
                    phase="static-fast-path",
                    failure_id=fast_evidence.failure_id,
                    failure_records=runner.failure_records,
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    coordinate_failure=CoordinateFailure(
                        status="INDETERMINATE",
                        observations=static_search.observations,
                        failure_id=fast_evidence.failure_id,
                    ),
                )
            if not (
                isinstance(fast_evidence, ProbeRejection)
                and fast_evidence.cause == "TEST_FAILURE"
            ):
                return CellSearchFailure(
                    reason="NONDETERMINISTIC",
                    cell=cell,
                    phase="static-fast-path",
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    coordinate_failure=CoordinateFailure(
                        status="NONDETERMINISTIC",
                        observations=static_search.observations,
                    ),
                    failure_records=runner.failure_records,
                )
            dynamic_search = CoordinateSearch(
                small_threshold=self._coordinate_threshold
            ).minimize(
                start=baseline_evaluation.proposal.managed_vector,
                candidates=candidate_snapshots,
                evaluator=_FullVectorEvaluator(runner),
                hints=static_search.vector,
                start_is_known_pass=True,
            )
            if isinstance(dynamic_search, CoordinateFailure):
                return self._coordinate_failure(
                    cell=cell,
                    phase="dynamic-search",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidates=candidate_snapshots,
                    outcome=dynamic_search,
                    runner=runner,
                    baseline_attempt=capture.attempt,
                )
            final_evidence = runner.evaluate_full(dynamic_search.vector)
            dynamic_search = self._append_observation(
                dynamic_search,
                vector=dynamic_search.vector,
                evidence=final_evidence,
            )
            final_evaluation = runner.full_evaluation(dynamic_search.vector)
            if not isinstance(final_evidence, ProbePass) or not isinstance(
                final_evaluation, PassEvaluation
            ):
                return CellSearchFailure(
                    reason="NONDETERMINISTIC",
                    cell=cell,
                    phase="dynamic-final",
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    failure_records=runner.failure_records,
                )
            return CellSuccess(
                cell=cell,
                baseline_attempt=capture.attempt,
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
                candidate_snapshots=candidate_snapshots,
                static_search=static_search,
                dynamic_search=dynamic_search,
                final_vector=dynamic_search.vector,
                final_evaluation=final_evaluation,
                failure_records=runner.failure_records,
            )
        finally:
            runner.close()

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
        )

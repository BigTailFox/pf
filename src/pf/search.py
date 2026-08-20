from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol

from packaging.version import Version

from pf.baseline import HighestVersionVerifier
from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.environment import PreparedEnvironment
from pf.evaluation import EvaluationCache, require_full_evaluation_contract
from pf.schemas.evaluation import (
    CacheConflict,
    Evaluation,
    HighestVersionVerification,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    SearchDynamicDiagnosticEvent,
    SearchDiagnosticEvent,
    SearchIndeterminateDiagnosticEvent,
    SearchStaticDiagnosticEvent,
    SearchToolDiagnosticEvent,
    TestFailEvaluation,
    ToolFailure,
)
from pf.schemas.project import CandidateSnapshot, Cell, PackagePlan, VersionPin
from pf.schemas.report import (
    CellFailure,
    CellResult,
    CellSuccess,
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateOutcome,
    CoordinateSuccess,
    ProbeEvidence,
    ProbeObservation,
)
from pf.snapshot import SourceSnapshot


_COMPATIBILITY_FAILURES = frozenset({"STATIC_FAIL", "TEST_FAIL"})
_NON_EVIDENCE = frozenset(
    {
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
        "NONDETERMINISTIC",
    }
)


class VectorEvaluator(Protocol):
    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence: ...


@dataclass
class _SearchStopped(Exception):
    result: CoordinateFailure


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
        try:
            baseline = self._probe(current, dependency=None)
            if baseline.status != "PASS":
                if baseline.status in _COMPATIBILITY_FAILURES:
                    return CoordinateFailure(
                        status="BASELINE_FAILED",
                        observations=tuple(self._observations),
                    )
                return CoordinateFailure.model_validate(
                    {
                        "status": baseline.status,
                        "observations": tuple(self._observations),
                    }
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
                self._stop(high_evidence.status, dependency=dependency)
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
            if evidence.status not in _COMPATIBILITY_FAILURES:
                self._stop(evidence.status, dependency=dependency)
            predecessor_status = (
                "STATIC_FAIL" if evidence.status == "STATIC_FAIL" else "TEST_FAIL"
            )
            boundary = CoordinateBoundary(
                dependency=dependency,
                floor=str(floor),
                predecessor=str(predecessor),
                predecessor_status=predecessor_status,
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
    ) -> ProbeEvidence:
        vector = dict(current)
        vector[dependency] = str(version)
        return self._probe(vector, dependency=dependency)

    def _probe(
        self,
        versions: dict[str, str],
        *,
        dependency: str | None,
    ) -> ProbeEvidence:
        vector = self._vector(versions)
        vector_key = tuple((pin.name, pin.version) for pin in vector)
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
        if evidence.status in _NON_EVIDENCE:
            self._stop(evidence.status, dependency=dependency)
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
                        and high_status in _COMPATIBILITY_FAILURES
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
    ) -> NoReturn:
        raise _SearchStopped(
            CoordinateFailure.model_validate(
                {
                    "status": status,
                    "dependency": dependency,
                    "observations": tuple(self._observations),
                    "counterexample": counterexample,
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
    ) -> PreparedEnvironment | ToolFailure: ...


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
    ) -> HighestVersionVerification | ToolFailure | IndeterminateEvaluation: ...


class SearchDiagnosticConsumer(Protocol):
    def consume(self, event: SearchDiagnosticEvent) -> None: ...


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
        baseline: PassEvaluation,
        static_baseline: StaticBaseline,
        diagnostics: SearchDiagnosticConsumer | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._package = package
        self._cell = cell
        self._snapshot = snapshot
        self._static_baseline = static_baseline
        self._diagnostics = diagnostics
        self._emitted_diagnostics: set[int] = set()
        self._cache = EvaluationCache()
        self._prepared: dict[tuple[tuple[str, str], ...], PreparedEnvironment] = {}
        self._evaluations: dict[tuple[tuple[str, str], ...], Evaluation] = {}
        baseline_key = self._key(baseline.proposal.managed_vector)
        self._evaluations[baseline_key] = baseline
        self._cache.record_static(
            baseline.static,
            baseline_digest=static_baseline.digest,
        )
        self._cache.record_full(
            baseline,
            baseline_digest=static_baseline.digest,
        )

    def evaluate_static(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        key = self._key(vector)
        full = self._evaluations.get(key)
        if isinstance(full, PassEvaluation):
            return ProbeEvidence(status="PASS", proposal_id=full.proposal.proposal_id)
        prepared = self._prepare(vector)
        if isinstance(prepared, ToolFailure):
            self._emit_diagnostic(prepared)
            return ProbeEvidence(
                status=prepared.status,
                proposal_id=f"prepare:{prepared.status}",
            )
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
                return ProbeEvidence(
                    status="NONDETERMINISTIC",
                    proposal_id=prepared.proposal.proposal_id,
                )
        else:
            result = cached
        self._emit_diagnostic(result)
        evidence = self._static_evidence(result)
        if not isinstance(result, StaticPassEvaluation):
            prepared.close()
            self._prepared.pop(key, None)
        return evidence

    def evaluate_full(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        key = self._key(vector)
        existing = self._evaluations.get(key)
        if existing is not None:
            return self._full_evidence(existing)
        prepared = self._prepare(vector)
        if isinstance(prepared, ToolFailure):
            self._emit_diagnostic(prepared)
            return ProbeEvidence(
                status=prepared.status,
                proposal_id=f"prepare:{prepared.status}",
            )
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
            evidence = ProbeEvidence(
                status="NONDETERMINISTIC",
                proposal_id=prepared.proposal.proposal_id,
            )
        else:
            self._evaluations[key] = stored
            self._emit_diagnostic(stored)
            evidence = self._full_evidence(stored)
        prepared.close()
        self._prepared.pop(key, None)
        return evidence

    def full_evaluation(self, vector: tuple[VersionPin, ...]) -> Evaluation | None:
        return self._evaluations.get(self._key(vector))

    def close(self) -> None:
        for prepared in self._prepared.values():
            prepared.close()
        self._prepared.clear()

    def _emit_diagnostic(
        self,
        outcome: StaticEvaluation | Evaluation | ToolFailure,
    ) -> None:
        if not isinstance(
            outcome,
            (
                StaticFailEvaluation,
                TestFailEvaluation,
                IndeterminateEvaluation,
                ToolFailure,
            ),
        ):
            return
        identity = id(outcome)
        if self._diagnostics is None or identity in self._emitted_diagnostics:
            return
        self._emitted_diagnostics.add(identity)
        if isinstance(outcome, StaticFailEvaluation):
            event: SearchDiagnosticEvent = SearchStaticDiagnosticEvent(
                cell=self._cell,
                outcome=outcome,
            )
        elif isinstance(outcome, TestFailEvaluation):
            event = SearchDynamicDiagnosticEvent(
                cell=self._cell,
                outcome=outcome,
            )
        elif isinstance(outcome, IndeterminateEvaluation):
            event = SearchIndeterminateDiagnosticEvent(
                cell=self._cell,
                outcome=outcome,
            )
        else:
            event = SearchToolDiagnosticEvent(
                cell=self._cell,
                outcome=outcome,
            )
        self._diagnostics.consume(event)

    def _prepare(
        self,
        vector: tuple[VersionPin, ...],
    ) -> PreparedEnvironment | ToolFailure:
        key = self._key(vector)
        existing = self._prepared.get(key)
        if existing is not None:
            return existing
        prepared = self._environments.prepare(
            package=self._package,
            cell=self._cell,
            snapshot=self._snapshot,
            resolution="highest",
            managed_vector=vector,
        )
        if isinstance(prepared, ToolFailure):
            return prepared
        if self._key(prepared.proposal.managed_vector) != key:
            prepared.close()
            return ToolFailure(
                status="HARNESS_ERROR",
                stage="proposal-vector",
                process=self._synthetic_process(),
            )
        self._prepared[key] = prepared
        return prepared

    @staticmethod
    def _static_evidence(result: StaticEvaluation) -> ProbeEvidence:
        if isinstance(result, StaticPassEvaluation):
            status = "PASS"
        else:
            status = result.status
        return ProbeEvidence(
            status=status,
            proposal_id=result.proposal.proposal_id,
            static=result,
        )

    @staticmethod
    def _full_evidence(result: Evaluation) -> ProbeEvidence:
        if isinstance(result, (PassEvaluation, TestFailEvaluation)):
            static: StaticEvaluation | None = result.static
        elif isinstance(result, StaticFailEvaluation):
            static = result
        else:
            static = None
        return ProbeEvidence(
            status=result.status,
            proposal_id=result.proposal.proposal_id,
            static=static,
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
            stdout_summary="",
            stderr_summary="proposal vector drift",
            stdout_tail="",
            stderr_tail="proposal vector drift",
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
    ) -> None:
        self._environments = environments
        self._candidates = candidates
        self._static = static
        self._full = full
        self._highest = highest or HighestVersionVerifier(
            environments=environments,
            static=static,
            full=full,
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
        if isinstance(capture, ToolFailure):
            return CellFailure(
                status=capture.status,
                cell=cell,
                phase="baseline-prepare",
                failure=capture,
            )
        if isinstance(capture, IndeterminateEvaluation):
            return CellFailure(
                status=capture.status,
                cell=cell,
                phase="baseline-static-capture",
                baseline=capture,
                failure=capture.failure,
            )
        baseline_evaluation = capture.evaluation
        if not isinstance(baseline_evaluation, PassEvaluation):
            status = (
                "BASELINE_FAILED"
                if isinstance(
                    baseline_evaluation,
                    (StaticFailEvaluation, TestFailEvaluation),
                )
                else baseline_evaluation.status
            )
            return CellFailure(
                status=status,
                cell=cell,
                phase="baseline-evaluation",
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
            )
        try:
            candidate_snapshots = self._candidates.build(
                package=package,
                cell=cell,
                baseline=baseline_evaluation.proposal.managed_vector,
            )
        except InfrastructureError as error:
            return CellFailure(
                status="SOURCE_ERROR",
                cell=cell,
                phase="candidate-discovery",
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
                detail=str(error),
            )
        except NoApplicableFloorError:
            return CellFailure(
                status="NO_PASS_IN_SEARCH_SPACE",
                cell=cell,
                phase="candidate-discovery",
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
            baseline=baseline_evaluation,
            static_baseline=capture.baseline,
            diagnostics=self._diagnostics,
        )
        try:
            static_search = CoordinateSearch(
                small_threshold=self._coordinate_threshold
            ).minimize(
                start=baseline_evaluation.proposal.managed_vector,
                candidates=candidate_snapshots,
                evaluator=_StaticVectorEvaluator(runner),
            )
            if isinstance(static_search, CoordinateFailure):
                return CellFailure(
                    status=static_search.status,
                    cell=cell,
                    phase="static-search",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    coordinate_failure=static_search,
                )
            fast_evidence = runner.evaluate_full(static_search.vector)
            fast_evaluation = runner.full_evaluation(static_search.vector)
            if fast_evidence.status == "PASS" and isinstance(
                fast_evaluation, PassEvaluation
            ):
                return CellSuccess(
                    cell=cell,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    static_search=static_search,
                    final_vector=static_search.vector,
                    final_evaluation=fast_evaluation,
                )
            if fast_evidence.status != "TEST_FAIL":
                failure_status = self._fast_path_failure_status(fast_evidence.status)
                return CellFailure(
                    status=failure_status,
                    cell=cell,
                    phase="static-fast-path",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    failure=(
                        fast_evaluation.failure
                        if isinstance(fast_evaluation, IndeterminateEvaluation)
                        else None
                    ),
                )
            dynamic_search = CoordinateSearch(
                small_threshold=self._coordinate_threshold
            ).minimize(
                start=baseline_evaluation.proposal.managed_vector,
                candidates=candidate_snapshots,
                evaluator=_FullVectorEvaluator(runner),
                hints=static_search.vector,
            )
            if isinstance(dynamic_search, CoordinateFailure):
                return CellFailure(
                    status=dynamic_search.status,
                    cell=cell,
                    phase="dynamic-search",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    coordinate_failure=dynamic_search,
                )
            final_evaluation = runner.full_evaluation(dynamic_search.vector)
            if not isinstance(final_evaluation, PassEvaluation):
                return CellFailure(
                    status="NONDETERMINISTIC",
                    cell=cell,
                    phase="dynamic-final",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                )
            return CellSuccess(
                cell=cell,
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
                candidate_snapshots=candidate_snapshots,
                static_search=static_search,
                dynamic_search=dynamic_search,
                final_vector=dynamic_search.vector,
                final_evaluation=final_evaluation,
            )
        finally:
            runner.close()

    @staticmethod
    def _fast_path_failure_status(
        status: str,
    ) -> Literal[
        "NONDETERMINISTIC",
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
    ]:
        match status:
            case "UNAVAILABLE":
                return "UNAVAILABLE"
            case "BUILD_UNAVAILABLE":
                return "BUILD_UNAVAILABLE"
            case "UNRESOLVABLE":
                return "UNRESOLVABLE"
            case "HARNESS_ERROR":
                return "HARNESS_ERROR"
            case "SOURCE_ERROR":
                return "SOURCE_ERROR"
            case "TOOL_ERROR":
                return "TOOL_ERROR"
            case "TIMEOUT":
                return "TIMEOUT"
            case _:
                return "NONDETERMINISTIC"

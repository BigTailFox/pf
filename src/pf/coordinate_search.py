from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol, runtime_checkable

from packaging.version import Version

from pf.errors import ConfigurationError
from pf.static import StaticGuidanceEvaluator, StaticHint, locate_static_hint
from pf.schemas.evaluation import CoordinateSelectionReason, SearchProbeRequest
from pf.schemas.project import CandidateSnapshot, VersionPin
from pf.schemas.report import (
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateOutcome,
    CoordinateSuccess,
    ProbeEvidence,
    ProbePass,
    ProbeIndeterminate,
    ProbeObservation,
    ProbeRejection,
)


class VectorEvaluator(Protocol):
    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence: ...


@runtime_checkable
class RuntimeBackedVectorEvaluator(Protocol):

    def evaluate_in_slice(
        self,
        request: SearchProbeRequest,
    ) -> ProbeEvidence: ...


@runtime_checkable
class DirectEvidenceLookup(Protocol):
    def lookup_direct_in_slice(self, request: SearchProbeRequest) -> ProbeEvidence | None: ...


@runtime_checkable
class DirectEvidenceConsumer(Protocol):
    def consume_direct_in_slice(self, request: SearchProbeRequest, evidence: ProbeEvidence) -> None: ...


@runtime_checkable
class CoordinateEnvironmentOwner(Protocol):
    def finish_coordinate(self) -> None: ...


@runtime_checkable
class DirectBoundRecorder(Protocol):
    def record_direct_bound(
        self,
        vector: tuple[VersionPin, ...],
        *,
        dependency: str,
        versions: tuple[str, ...],
        predecessor: str | None,
        predecessor_failure_id: str | None,
    ) -> None: ...


@dataclass
class _SearchStopped(Exception):
    result: CoordinateFailure


CoordinateProgressConsumer = Callable[
    [tuple[VersionPin, ...], tuple[VersionPin, ...]],
    None,
]


class CoordinateSearch:
    """Find a deterministic fixpoint with invocation-local mutable state."""

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
        progress: CoordinateProgressConsumer | None = None,
    ) -> CoordinateOutcome:
        return _CoordinateRun(
            small_threshold=self.small_threshold,
            evaluator=evaluator,
            progress=progress,
        ).minimize(start=start, candidates=candidates, hints=hints)


class _CoordinateRun:
    """Mutable search state owned by exactly one minimize invocation."""

    def __init__(
        self,
        *,
        small_threshold: int,
        evaluator: VectorEvaluator,
        progress: CoordinateProgressConsumer | None,
    ) -> None:
        self._small_threshold = small_threshold
        self._evaluator = evaluator
        self._observations: list[ProbeObservation] = []
        self._observation_keys: set[
            tuple[str | None, tuple[tuple[str, str], ...], str]
        ] = set()
        self._slice_observations: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[Version, str]
        ] = {}
        self._progress = progress

    def minimize(
        self,
        *,
        start: tuple[VersionPin, ...],
        candidates: tuple[CandidateSnapshot, ...],
        hints: tuple[VersionPin, ...],
    ) -> CoordinateOutcome:
        snapshots = {snapshot.dependency: snapshot for snapshot in candidates}
        current = {pin.name: pin.version for pin in start}
        if len(current) != len(start) or len(snapshots) != len(candidates):
            raise ConfigurationError("search coordinates must be unique")
        if tuple(sorted(current)) != tuple(sorted(snapshots)):
            raise ConfigurationError("start vector and candidate coordinates must match")
        hint_by_name = {pin.name: pin.version for pin in hints}
        try:
            if self._status(self._probe(current, dependency=None)) != "PASS":
                return CoordinateFailure(
                    status="NONDETERMINISTIC",
                    observations=tuple(self._observations),
                )
            sweeps = 0
            boundaries: dict[str, CoordinateBoundary] = {}
            while True:
                sweeps += 1
                changed = False
                completed: list[VersionPin] = []
                self._publish_progress(current, completed)
                current_boundaries: dict[str, CoordinateBoundary] = {}
                for dependency in sorted(snapshots):
                    try:
                        floor, boundary = self._find_floor(
                            current=current,
                            snapshot=snapshots[dependency],
                            hint=hint_by_name.get(dependency),
                            history=boundaries.get(dependency),
                        )
                    finally:
                        if isinstance(self._evaluator, CoordinateEnvironmentOwner):
                            self._evaluator.finish_coordinate()
                    current_boundaries[dependency] = boundary
                    if Version(floor) < Version(current[dependency]):
                        current[dependency] = floor
                        changed = True
                    completed.append(VersionPin(name=dependency, version=floor))
                    self._publish_progress(current, completed)
                boundaries = current_boundaries
                if not changed:
                    break
            return CoordinateSuccess(
                vector=self._vector(current),
                observations=tuple(self._observations),
                boundaries=tuple(boundaries[name] for name in sorted(boundaries)),
                sweeps=sweeps,
            )
        except _SearchStopped as stopped:
            return stopped.result

    def _publish_progress(
        self,
        current: dict[str, str],
        completed: list[VersionPin],
    ) -> None:
        if self._progress is not None:
            self._progress(self._vector(current), tuple(completed))

    def _find_floor(
        self,
        *,
        current: dict[str, str],
        snapshot: CandidateSnapshot,
        hint: str | None,
        history: CoordinateBoundary | None,
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
        search_current = current
        current_direct = None
        if isinstance(self._evaluator, DirectEvidenceLookup):
            # Consume existing direct observations before any early boundary or
            # static phase. A cached lower PASS/higher rejection is still a
            # counterexample, even when it was first seen under another axis.
            passes = []
            for version in versions:
                vector = dict(current)
                vector[dependency] = str(version)
                request = self._probe_request(self._vector(vector), dependency=dependency, window=versions)
                existing = self._evaluator.lookup_direct_in_slice(request)
                if existing is not None:
                    self._probe(
                        vector, dependency=dependency, window=versions, direct=existing,
                        selection_reason="direct-existing",
                    )
                    if isinstance(existing, ProbePass):
                        passes.append((version, existing))
            if passes:
                current_version, current_direct = min(passes, key=lambda item: item[0])
                search_current = dict(current)
                search_current[dependency] = str(current_version)
                versions = [version for version in versions if version <= current_version]
        while current_version in versions:
            current_index = versions.index(current_version)
            current_evidence = self._probe(
                search_current, dependency=dependency,
                window=[current_version], direct=current_direct,
                selection_reason="current-upper",
            )
            if self._status(current_evidence) != "PASS":
                self._stop("NONDETERMINISTIC", dependency=dependency)
            if current_index == 0:
                return str(current_version), self._direct_bound(
                    current=search_current,
                    snapshot=snapshot,
                    versions=versions,
                    floor=str(current_version),
                )
            expected_predecessor = versions[current_index - 1]
            predecessor_vector = dict(search_current)
            predecessor_vector[dependency] = str(expected_predecessor)
            predecessor_request = self._probe_request(
                self._vector(predecessor_vector), dependency=dependency,
                window=[expected_predecessor, current_version],
            )
            cached_predecessor = (
                self._evaluator.lookup_direct_in_slice(predecessor_request)
                if isinstance(self._evaluator, DirectEvidenceLookup) else None
            )
            if cached_predecessor is not None or (
                history is not None
                and history.floor == str(current_version)
                and history.predecessor == str(expected_predecessor)
            ):
                predecessor_evidence = self._probe(
                    predecessor_vector, dependency=dependency,
                    window=[expected_predecessor, current_version],
                    direct=cached_predecessor, selection_reason="history",
                )
                if isinstance(predecessor_evidence, ProbeRejection):
                    return str(current_version), self._direct_bound(
                        current=search_current,
                        snapshot=snapshot,
                        versions=versions,
                        floor=str(current_version),
                        predecessor=str(expected_predecessor),
                        predecessor_failure_id=predecessor_evidence.failure_id,
                    )
                if self._status(predecessor_evidence) != "PASS":
                    self._stop("NONDETERMINISTIC", dependency=dependency)
                versions = versions[:current_index]
                search_current = dict(search_current)
                search_current[dependency] = str(expected_predecessor)
                current_version = expected_predecessor
                current_direct = predecessor_evidence
            else:
                break

        static_hint = None
        static_search_ref = None
        if (any(version < Version(search_current[dependency]) for version in versions)
                and isinstance(self._evaluator, StaticGuidanceEvaluator)):
            static_slice = self._evaluator.open_static_slice(
                self._vector(search_current), dependency=dependency,
                versions=tuple(str(version) for version in versions),
            )
            if static_slice is not None:
                static_result = locate_static_hint(static_slice, tuple(str(version) for version in versions))
                static_hint, static_search_ref = static_result.hint, static_result.search_ref

        for _ in range(len(versions) * 2 + 1):
            floor = self._guided_floor(
                current=search_current,
                dependency=dependency,
                versions=versions,
                hint=hint,
                static_hint=static_hint, static_search_ref=static_search_ref,
            )
            static_hint = None
            if floor is None or floor not in versions:
                self._stop("NO_PASS_IN_SEARCH_SPACE", dependency=dependency)
            index = versions.index(floor)
            boundary_window = versions[max(0, index - 1) : index + 1]
            floor_evidence = self._probe_version(
                search_current,
                dependency,
                floor,
                window=boundary_window,
            )
            if self._status(floor_evidence) != "PASS":
                continue
            if index == 0:
                return str(floor), CoordinateBoundary(
                    dependency=dependency,
                    floor=str(floor),
                )
            predecessor = versions[index - 1]
            evidence = self._probe_version(
                search_current,
                dependency,
                predecessor,
                window=boundary_window,
            )
            if isinstance(evidence, ProbeRejection):
                return str(floor), CoordinateBoundary(
                    dependency=dependency,
                    floor=str(floor),
                    predecessor=str(predecessor),
                    predecessor_failure_id=evidence.failure_id,
                )
            if self._status(evidence) == "PASS":
                continue
            self._stop("NONDETERMINISTIC", dependency=dependency)
        self._stop("NONDETERMINISTIC", dependency=dependency)

    def _direct_bound(
        self,
        *,
        current: dict[str, str],
        snapshot: CandidateSnapshot,
        versions: list[Version],
        floor: str,
        predecessor: str | None = None,
        predecessor_failure_id: str | None = None,
    ) -> CoordinateBoundary:
        if isinstance(self._evaluator, DirectBoundRecorder):
            self._evaluator.record_direct_bound(
                self._vector(current),
                dependency=snapshot.dependency,
                versions=tuple(str(version) for version in versions),
                predecessor=predecessor,
                predecessor_failure_id=predecessor_failure_id,
            )
        return CoordinateBoundary(
            dependency=snapshot.dependency,
            floor=floor,
            predecessor=predecessor,
            predecessor_failure_id=predecessor_failure_id,
        )

    def _guided_floor(
        self,
        *,
        current: dict[str, str],
        dependency: str,
        versions: list[Version],
        hint: str | None,
        static_hint: StaticHint | None = None,
        static_search_ref: str | None = None,
    ) -> Version | None:
        current_version = Version(current[dependency])
        probe_hint = versions[0]
        if hint is not None:
            eligible = [version for version in versions if version <= Version(hint)]
            if eligible:
                probe_hint = eligible[-1]
        if static_hint is not None and Version(static_hint.suspect.version) in versions:
            probe_hint = Version(static_hint.suspect.version)
        else:
            static_hint = None
        if static_hint is not None:
            guided_reason: CoordinateSelectionReason = "static-suspect"
        elif hint is not None:
            guided_reason = "external-hint"
        else:
            guided_reason = "mechanical-lowest"
        hint_evidence = self._probe_version(
            current,
            dependency,
            probe_hint,
            window=versions,
            selection_reason=guided_reason,
            static_search_ref=static_search_ref if static_hint is not None else None,
        )
        if self._status(hint_evidence) == "PASS":
            if probe_hint == versions[0]:
                floor = probe_hint
            elif (
                self._status(
                    self._probe_version(
                        current,
                        dependency,
                        versions[0],
                        window=[
                            version for version in versions if version <= probe_hint
                        ],
                    )
                )
                == "PASS"
            ):
                floor = versions[0]
            else:
                floor = self._locate(
                    current=current,
                    dependency=dependency,
                    points=[version for version in versions if version <= probe_hint],
                    low=versions[0],
                    high=probe_hint,
                )
        else:
            if static_hint is not None and not static_hint.clean_is_anchor:
                clean = Version(static_hint.clean_neighbor.version)
                if clean in versions and clean > probe_hint:
                    clean_evidence = self._probe_version(
                        current, dependency, clean,
                        window=[version for version in versions if version >= probe_hint],
                        selection_reason="static-clean-neighbor", static_search_ref=static_search_ref,
                    )
                    if self._status(clean_evidence) == "PASS":
                        return self._locate(
                            current=current, dependency=dependency,
                            points=[version for version in versions if probe_hint <= version <= clean],
                            low=probe_hint, high=clean,
                        )
                    probe_hint = clean
            points = [version for version in versions if version >= probe_hint]
            current_window = (
                points
                if current_version in points
                else [*points, current_version]
            )
            current_evidence = (
                self._probe_version(
                    current,
                    dependency,
                    current_version,
                    window=current_window,
                    selection_reason="current-upper",
                )
                if current_version in points
                else self._probe(current, dependency=None)
            )
            if (
                self._status(current_evidence)
                != "PASS"
            ):
                self._stop("NONDETERMINISTIC", dependency=dependency)
            floor = self._locate(
                current=current,
                dependency=dependency,
                points=points,
                low=probe_hint,
                high=current_version,
            )
        return floor

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
        if high_index - low_index <= self._small_threshold:
            candidate_high = min(high_index, len(points) - 1)
            for index, version in enumerate(
                points[low_index + 1 : candidate_high + 1],
                start=low_index + 1,
            ):
                if (
                    self._status(
                        self._probe_version(
                            current,
                            dependency,
                            version,
                            window=points[index : candidate_high + 1],
                            selection_reason="mechanical-lowest",
                        )
                    )
                    == "PASS"
                ):
                    return version
            return None if virtual_high else points[high_index]
        while high_index - low_index > 1:
            middle = (low_index + high_index) // 2
            if (
                self._status(
                    self._probe_version(
                        current,
                        dependency,
                        points[middle],
                        window=points[
                            low_index : min(high_index, len(points) - 1) + 1
                        ],
                        selection_reason="mechanical-midpoint",
                    )
                )
                == "PASS"
            ):
                high_index = middle
            else:
                low_index = middle
        return None if high_index == len(points) else points[high_index]

    def _probe_version(
        self,
        current: dict[str, str],
        dependency: str,
        version: Version,
        *,
        window: list[Version],
        selection_reason: CoordinateSelectionReason = "mechanical-lowest",
        static_search_ref: str | None = None,
    ) -> ProbeEvidence:
        vector = dict(current)
        vector[dependency] = str(version)
        return self._probe(vector, dependency=dependency, window=window,
                           selection_reason=selection_reason, static_search_ref=static_search_ref)

    def _probe(
        self,
        versions: dict[str, str],
        *,
        dependency: str | None,
        window: list[Version] | None = None,
        direct: ProbeEvidence | None = None,
        selection_reason: CoordinateSelectionReason = "mechanical-lowest",
        static_search_ref: str | None = None,
    ) -> ProbeEvidence:
        vector = self._vector(versions)
        key = tuple((pin.name, pin.version) for pin in vector)
        evidence = direct
        looked_up = False
        if evidence is None and dependency is not None and isinstance(self._evaluator, DirectEvidenceLookup):
            evidence = self._evaluator.lookup_direct_in_slice(
                self._probe_request(vector, dependency=dependency, window=window),
            )
            looked_up = evidence is not None
        effective_reason: CoordinateSelectionReason | None
        if dependency is None:
            effective_reason = None
        elif looked_up:
            effective_reason = "direct-existing"
        else:
            effective_reason = selection_reason
        if evidence is not None and dependency is not None and isinstance(self._evaluator, DirectEvidenceConsumer):
            self._evaluator.consume_direct_in_slice(
                self._probe_request(vector, dependency=dependency, window=window).model_copy(update={
                    "selection_reason": effective_reason, "static_search_ref": static_search_ref,
                }), evidence,
            )
        if evidence is None:
            if dependency is not None and isinstance(self._evaluator, RuntimeBackedVectorEvaluator):
                evidence = self._evaluator.evaluate_in_slice(
                    self._probe_request(vector, dependency=dependency, window=window).model_copy(update={
                        "selection_reason": effective_reason, "static_search_ref": static_search_ref,
                    }),
                )
            else:
                evidence = self._evaluator.evaluate(vector)
        if not isinstance(evidence, (ProbePass, ProbeRejection, ProbeIndeterminate)):
            raise TypeError("oracle requires direct probe evidence")
        self._record_observation(
            versions=versions,
            dependency=dependency,
            vector=vector,
            key=key,
            evidence=evidence,
            selection_reason=effective_reason,
        )
        self._check_terminal(evidence, dependency=dependency)
        self._record_runtime_status(
            evidence,
            versions=versions,
            dependency=dependency,
            key=key,
        )
        return evidence

    @staticmethod
    def _probe_request(
        vector: tuple[VersionPin, ...],
        *,
        dependency: str,
        window: list[Version] | None,
    ) -> SearchProbeRequest:
        if not window:
            raise ValueError("runtime-backed probe requires a candidate window")
        versions = {pin.name: pin.version for pin in vector}
        return SearchProbeRequest(
            vector=vector,
            active_dependency=dependency,
            candidate_version=versions[dependency],
            lower_version=str(window[0]),
            upper_version=str(window[-1]),
            candidate_count=len(window),
        )

    def _record_observation(
        self,
        *,
        versions: dict[str, str],
        dependency: str | None,
        vector: tuple[VersionPin, ...],
        key: tuple[tuple[str, str], ...],
        evidence: ProbeEvidence,
        selection_reason: CoordinateSelectionReason | None,
    ) -> None:
        observation_key = (dependency, key, evidence.status)
        if observation_key not in self._observation_keys:
            self._observation_keys.add(observation_key)
            self._observations.append(
                ProbeObservation(
                    dependency=dependency,
                    candidate_version=(
                        versions[dependency] if dependency is not None else None
                    ),
                    vector=vector,
                    evidence=evidence,
                    selection_reason=None if dependency is None else selection_reason,
                )
            )

    def _check_terminal(
        self,
        evidence: ProbeEvidence,
        *,
        dependency: str | None,
    ) -> None:
        if isinstance(evidence, ProbeIndeterminate):
            self._stop(
                "INDETERMINATE",
                dependency=dependency,
                failure_id=evidence.failure_id,
            )

    def _record_runtime_status(
        self,
        evidence: ProbeEvidence,
        *,
        versions: dict[str, str],
        dependency: str | None,
        key: tuple[tuple[str, str], ...],
    ) -> None:
        if dependency is not None:
            slice_key = (
                dependency,
                tuple((name, value) for name, value in key if name != dependency),
            )
            points = self._slice_observations.setdefault(slice_key, {})
            previous = points.get(Version(versions[dependency]))
            if previous is not None and previous != evidence.status:
                self._stop("NONDETERMINISTIC", dependency=dependency)
            points[Version(versions[dependency])] = evidence.status
            for low, low_status in points.items():
                for high, high_status in points.items():
                    if low < high and low_status == "PASS" and high_status == "REJECTED":
                        self._stop(
                            "NON_MONOTONIC",
                            dependency=dependency,
                            counterexample=(str(low), str(high)),
                        )

    @staticmethod
    def _status(evidence: ProbeEvidence) -> Literal["PASS", "REJECTED", "INDETERMINATE"]:
        return evidence.status

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

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol, runtime_checkable

from packaging.version import Version

from pf.errors import ConfigurationError
from pf.schemas.project import CandidateSnapshot, VersionPin
from pf.schemas.report import (
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateOutcome,
    CoordinateSuccess,
    ProbeEvidence,
    ProbeIndeterminate,
    ProbeObservation,
    ProbeRejection,
    StaticOnlyEvidence,
    StaticRegion,
)


class VectorEvaluator(Protocol):
    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence: ...


@runtime_checkable
class RuntimeBackedVectorEvaluator(Protocol):
    @property
    def regions(self) -> tuple[StaticRegion, ...]: ...

    def evaluate_in_slice(
        self,
        vector: tuple[VersionPin, ...],
        *,
        dependency: str,
    ) -> ProbeEvidence | StaticOnlyEvidence: ...

    def promote(
        self,
        vector: tuple[VersionPin, ...],
        *,
        dependency: str,
    ) -> ProbeEvidence: ...


@dataclass
class _SearchStopped(Exception):
    result: CoordinateFailure


@dataclass(frozen=True)
class _KnownPass:
    status: Literal["PASS"] = "PASS"


SearchEvidence = ProbeEvidence | StaticOnlyEvidence | _KnownPass


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
        start_is_known_pass: bool = False,
    ) -> CoordinateOutcome:
        return _CoordinateRun(
            small_threshold=self.small_threshold,
            evaluator=evaluator,
            start_is_known_pass=start_is_known_pass,
            start=start,
        ).minimize(start=start, candidates=candidates, hints=hints)


class _CoordinateRun:
    """Mutable search state owned by exactly one minimize invocation."""

    def __init__(
        self,
        *,
        small_threshold: int,
        evaluator: VectorEvaluator,
        start_is_known_pass: bool,
        start: tuple[VersionPin, ...],
    ) -> None:
        self._small_threshold = small_threshold
        self._evaluator = evaluator
        self._evidence_cache: dict[
            tuple[str | None, tuple[tuple[str, str], ...]],
            ProbeEvidence | StaticOnlyEvidence,
        ] = {}
        self._observations: list[ProbeObservation] = []
        self._observation_keys: set[
            tuple[str | None, tuple[tuple[str, str], ...], str]
        ] = set()
        self._slice_observations: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[Version, str]
        ] = {}
        self._known_pass_keys = (
            {tuple((pin.name, pin.version) for pin in self._vector_from_pins(start))}
            if start_is_known_pass
            else set()
        )

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
                current_boundaries: dict[str, CoordinateBoundary] = {}
                for dependency in sorted(snapshots):
                    floor, boundary = self._find_floor(
                        current=current,
                        snapshot=snapshots[dependency],
                        hint=hint_by_name.get(dependency),
                    )
                    current_boundaries[dependency] = boundary
                    if Version(floor) < Version(current[dependency]):
                        current[dependency] = floor
                        changed = True
                boundaries = current_boundaries
                if not changed:
                    break
            return CoordinateSuccess(
                vector=self._vector(current),
                observations=tuple(self._observations),
                boundaries=tuple(boundaries[name] for name in sorted(boundaries)),
                regions=self._regions(),
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
                dependency=dependency, floor=str(current_version)
            )

        for _ in range(len(versions) * 2 + 1):
            floor = self._guided_floor(
                current=current,
                dependency=dependency,
                versions=versions,
                hint=hint,
            )
            if floor is None or floor not in versions:
                self._stop("NO_PASS_IN_SEARCH_SPACE", dependency=dependency)
            floor_evidence = self._promote_version(current, dependency, floor)
            if self._status(floor_evidence) != "PASS":
                continue
            index = versions.index(floor)
            if index == 0:
                return str(floor), CoordinateBoundary(
                    dependency=dependency,
                    floor=str(floor),
                )
            predecessor = versions[index - 1]
            evidence = self._promote_version(current, dependency, predecessor)
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

    def _guided_floor(
        self,
        *,
        current: dict[str, str],
        dependency: str,
        versions: list[Version],
        hint: str | None,
    ) -> Version | None:
        current_version = Version(current[dependency])
        probe_hint = versions[0]
        if hint is not None:
            eligible = [version for version in versions if version <= Version(hint)]
            if eligible:
                probe_hint = eligible[-1]
        hint_evidence = self._probe_version(current, dependency, probe_hint)
        if self._status(hint_evidence) == "PASS":
            if probe_hint == versions[0]:
                floor = probe_hint
            elif (
                self._status(self._probe_version(current, dependency, versions[0]))
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
            points = [version for version in versions if version >= probe_hint]
            if (
                self._status(
                    self._probe_version(current, dependency, current_version)
                )
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
            for version in points[low_index + 1 : candidate_high + 1]:
                if (
                    self._status(self._probe_version(current, dependency, version))
                    == "PASS"
                ):
                    return version
            return None if virtual_high else points[high_index]
        while high_index - low_index > 1:
            middle = (low_index + high_index) // 2
            if (
                self._status(
                    self._probe_version(current, dependency, points[middle])
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
        key = tuple((pin.name, pin.version) for pin in vector)
        if key in self._known_pass_keys:
            return _KnownPass()
        cache_key = (dependency, key)
        evidence = self._evidence_cache.get(cache_key)
        if evidence is None:
            if dependency is not None and isinstance(
                self._evaluator, RuntimeBackedVectorEvaluator
            ):
                evidence = self._evaluator.evaluate_in_slice(
                    vector,
                    dependency=dependency,
                )
            else:
                evidence = self._evaluator.evaluate(vector)
            self._evidence_cache[cache_key] = evidence
        self._record_observation(
            versions=versions,
            dependency=dependency,
            vector=vector,
            key=key,
            evidence=evidence,
        )
        self._check_terminal(evidence, dependency=dependency)
        self._record_runtime_status(
            evidence,
            versions=versions,
            dependency=dependency,
            key=key,
        )
        return evidence

    def _promote_version(
        self,
        current: dict[str, str],
        dependency: str,
        version: Version,
    ) -> SearchEvidence:
        versions = dict(current)
        versions[dependency] = str(version)
        vector = self._vector(versions)
        key = tuple((pin.name, pin.version) for pin in vector)
        if key in self._known_pass_keys:
            return _KnownPass()
        cache_key = (dependency, key)
        existing = self._evidence_cache.get(cache_key)
        if existing is not None and not isinstance(existing, StaticOnlyEvidence):
            return self._probe(versions, dependency=dependency)
        if not isinstance(self._evaluator, RuntimeBackedVectorEvaluator):
            self._stop("NONDETERMINISTIC", dependency=dependency)
        evidence = self._evaluator.promote(vector, dependency=dependency)
        self._evidence_cache[cache_key] = evidence
        self._record_observation(
            versions=versions,
            dependency=dependency,
            vector=vector,
            key=key,
            evidence=evidence,
        )
        self._check_terminal(evidence, dependency=dependency)
        self._record_runtime_status(
            evidence,
            versions=versions,
            dependency=dependency,
            key=key,
        )
        return evidence

    def _record_observation(
        self,
        *,
        versions: dict[str, str],
        dependency: str | None,
        vector: tuple[VersionPin, ...],
        key: tuple[tuple[str, str], ...],
        evidence: ProbeEvidence | StaticOnlyEvidence,
    ) -> None:
        evidence_kind = (
            evidence.kind if isinstance(evidence, StaticOnlyEvidence) else evidence.status
        )
        observation_key = (dependency, key, evidence_kind)
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
                )
            )

    def _check_terminal(
        self,
        evidence: ProbeEvidence | StaticOnlyEvidence,
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
        evidence: ProbeEvidence | StaticOnlyEvidence,
        *,
        versions: dict[str, str],
        dependency: str | None,
        key: tuple[tuple[str, str], ...],
    ) -> None:
        if dependency is not None and not isinstance(evidence, StaticOnlyEvidence):
            slice_key = (
                dependency,
                tuple((name, value) for name, value in key if name != dependency),
            )
            points = self._slice_observations.setdefault(slice_key, {})
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
    def _status(evidence: SearchEvidence) -> Literal["PASS", "REJECTED", "INDETERMINATE"]:
        if isinstance(evidence, StaticOnlyEvidence):
            return evidence.guidance
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
                    "regions": self._regions(),
                    "counterexample": counterexample,
                    "failure_id": failure_id,
                }
            )
        )

    def _regions(self) -> tuple[StaticRegion, ...]:
        if isinstance(self._evaluator, RuntimeBackedVectorEvaluator):
            return self._evaluator.regions
        return ()

    @staticmethod
    def _vector(versions: dict[str, str]) -> tuple[VersionPin, ...]:
        return tuple(
            VersionPin(name=name, version=versions[name]) for name in sorted(versions)
        )

    @staticmethod
    def _vector_from_pins(
        vector: tuple[VersionPin, ...],
    ) -> tuple[VersionPin, ...]:
        return tuple(sorted(vector, key=lambda pin: pin.name))

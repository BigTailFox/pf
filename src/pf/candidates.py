from __future__ import annotations

import hashlib
import json
import threading
from typing import Protocol

from packaging.requirements import Requirement
from packaging.specifiers import Specifier
from packaging.version import Version

from pf.errors import ConfigurationError, NoApplicableFloorError
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    Candidate,
    CandidateSnapshot,
    Cell,
    PackagePlan,
    SourceIdentity,
    VersionPin,
    candidate_snapshot_digest,
    cell_identity,
)


class CandidateProvider(Protocol):
    def query(
        self,
        *,
        dependency: str,
        source: SourceIdentity,
        cell: Cell,
    ) -> tuple[AvailableCandidate, ...]: ...


class CandidateBuilder:
    """Freeze every candidate policy decision for one package/cell."""

    def __init__(self, provider: CandidateProvider) -> None:
        self._provider = provider
        self._query_lock = threading.Lock()
        self._queries: dict[
            tuple[str, SourceIdentity, tuple[str, str, str, tuple[str, ...]]],
            tuple[AvailableCandidate, ...],
        ] = {}

    def build(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        baseline: tuple[VersionPin, ...],
    ) -> tuple[CandidateSnapshot, ...]:
        baseline_versions = {pin.name: Version(pin.version) for pin in baseline}
        active_ids = set(cell.active_declaration_ids)
        managed_names = sorted(
            {
                declaration.name
                for declaration in package.declarations
                if declaration.managed and declaration.declaration_id in active_ids
            }
        )
        policy_json = json.dumps(
            package.config.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        policy_identity = hashlib.sha256(
            f"pf:candidate-policy:v1\0{policy_json}".encode()
        ).hexdigest()
        snapshots = []
        for dependency in managed_names:
            if dependency not in baseline_versions:
                raise ConfigurationError(
                    f"baseline is missing managed dependency: {dependency}"
                )
            declarations = tuple(
                declaration
                for declaration in package.declarations
                if declaration.name == dependency
                and declaration.managed
                and declaration.declaration_id in active_ids
            )
            sources = {declaration.source for declaration in declarations}
            if len(sources) != 1:
                raise ConfigurationError(
                    f"ambiguous source for dependency: {dependency}"
                )
            source = next(iter(sources))
            query_key = (dependency, source, cell_identity(cell))
            with self._query_lock:
                available = self._queries.get(query_key)
                if available is None:
                    available = self._provider.query(
                        dependency=dependency,
                        source=source,
                        cell=cell,
                    )
                    self._queries[query_key] = available
            restrictions = tuple(
                specifier
                for declaration in declarations
                for specifier in declaration.specifier.split(",")
                if specifier and Specifier(specifier).operator in {"<", "<=", "!="}
            )
            search_requirement = self._search_requirement(package, dependency)
            eligible: list[tuple[Version, AvailableArtifact]] = []
            for raw_candidate in available:
                version = Version(raw_candidate.version)
                if raw_candidate.yanked:
                    continue
                if version.is_prerelease and not package.config.allow_prereleases:
                    continue
                if version > baseline_versions[dependency]:
                    continue
                if any(
                    not Specifier(specifier).contains(version, prereleases=True)
                    for specifier in restrictions
                ):
                    continue
                if (
                    search_requirement is not None
                    and version not in search_requirement.specifier
                ):
                    continue
                if isinstance(package.config.search_space, str):
                    baseline_version = baseline_versions[dependency]
                    if (
                        package.config.search_space == "current-major"
                        and self._release(version)[0]
                        != self._release(baseline_version)[0]
                    ):
                        continue
                    if (
                        package.config.search_space == "current-minor"
                        and self._release(version)[:2]
                        != self._release(baseline_version)[:2]
                    ):
                        continue
                artifact = self._artifact(
                    raw_candidate.artifacts,
                    cell=cell,
                    distribution=package.config.distribution,
                )
                if artifact is not None:
                    eligible.append((version, artifact))
            representatives: dict[str, tuple[Version, AvailableArtifact]] = {}
            for version, artifact in eligible:
                key = self._series_key(version, package.config.release_granularity)
                current = representatives.get(key)
                if current is None or version > current[0]:
                    representatives[key] = (version, artifact)
            ordered = sorted(representatives.items(), key=lambda item: item[1][0])
            if not ordered:
                raise NoApplicableFloorError(f"empty candidate space for: {dependency}")
            candidates = tuple(
                Candidate(
                    version=str(version),
                    series_key=key,
                    artifact=artifact,
                    prerelease=version.is_prerelease,
                )
                for key, (version, artifact) in ordered
            )
            representatives_record = tuple(
                (candidate.series_key, candidate.version) for candidate in candidates
            )
            digest = candidate_snapshot_digest(
                dependency=dependency,
                cell=cell,
                policy_identity=policy_identity,
                source=source,
                candidates=candidates,
                series_representatives=representatives_record,
            )
            snapshots.append(
                CandidateSnapshot(
                    dependency=dependency,
                    cell=cell,
                    policy_identity=policy_identity,
                    source=source,
                    candidates=candidates,
                    series_representatives=representatives_record,
                    digest=digest,
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _search_requirement(
        package: PackagePlan,
        dependency: str,
    ) -> Requirement | None:
        if isinstance(package.config.search_space, str):
            return None
        for text in package.config.search_space:
            requirement = Requirement(text)
            if requirement.name == dependency:
                return requirement
        return None

    @staticmethod
    def _artifact(
        artifacts: tuple[AvailableArtifact, ...],
        *,
        cell: Cell,
        distribution: str,
    ) -> AvailableArtifact | None:
        wheels = sorted(
            (
                artifact
                for artifact in artifacts
                if artifact.kind == "wheel"
                and cell.python_minor in artifact.python_minors
                and cell.target in artifact.targets
            ),
            key=lambda artifact: artifact.filename,
        )
        sdists = sorted(
            (artifact for artifact in artifacts if artifact.kind == "sdist"),
            key=lambda artifact: artifact.filename,
        )
        if distribution == "wheel":
            return wheels[0] if wheels else None
        if distribution == "sdist":
            return sdists[0] if sdists else None
        if wheels:
            return wheels[0]
        return sdists[0] if sdists else None

    @staticmethod
    def _release(version: Version) -> tuple[int, int, int]:
        release = (*version.release, 0, 0, 0)
        return release[0], release[1], release[2]

    @classmethod
    def _series_key(cls, version: Version, granularity: str) -> str:
        major, minor, patch = cls._release(version)
        if granularity == "major":
            release = f"{major}"
        elif granularity == "minor":
            release = f"{major}.{minor}"
        else:
            release = f"{major}.{minor}.{patch}"
        return f"{version.epoch}!{release}" if version.epoch else release

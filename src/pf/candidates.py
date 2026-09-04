from __future__ import annotations

import hashlib
import threading
from typing import Protocol

from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version

from pf.errors import ConfigurationError, NoApplicableFloorError
from pf.schemas.base import canonical_identity_json
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    Candidate,
    CandidateSnapshot,
    Cell,
    NamedSearchPolicy,
    PackagePlan,
    SourceIdentity,
    SourcePlan,
    VersionPin,
    candidate_snapshot_digest,
    cell_identity,
)


def candidate_policy_identity(
    policy: NamedSearchPolicy,
    *,
    artifact: str,
) -> str:
    """Identify only the facts that select one dependency's candidates."""
    return hashlib.sha256(
        b"pf:candidate-policy:v1\0"
        + canonical_identity_json(
            {
                "policy": policy.model_dump(mode="json"),
                "artifact": artifact,
            }
        )
    ).hexdigest()


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
        source_plan: SourcePlan,
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
        snapshots = []
        plan_identity = source_plan.identity
        for dependency in managed_names:
            if dependency not in baseline_versions:
                raise ConfigurationError(
                    f"baseline is missing managed dependency: {dependency}"
                )
            policy = package.search_policy_for(dependency)
            policy_identity = candidate_policy_identity(
                policy,
                artifact=package.config.resolution.artifact,
            )
            declarations = tuple(
                declaration
                for declaration in package.declarations
                if declaration.name == dependency
                and declaration.managed
                and declaration.declaration_id in active_ids
            )
            source = source_plan.source_for(dependency)
            if source.kind != "registry":
                raise ConfigurationError(
                    f"managed dependency has no registry search source: {dependency}"
                )
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
            search_specifier = (
                SpecifierSet(policy.space)
                if policy.space
                not in {"all", "current-major", "current-minor"}
                else None
            )
            eligible: list[tuple[Version, AvailableArtifact]] = []
            for raw_candidate in available:
                version = Version(raw_candidate.version)
                if raw_candidate.yanked:
                    continue
                if version.is_prerelease and not policy.prereleases:
                    continue
                if version > baseline_versions[dependency]:
                    continue
                if any(
                    not Specifier(specifier).contains(version, prereleases=True)
                    for specifier in restrictions
                ):
                    continue
                if (
                    search_specifier is not None
                    and not search_specifier.contains(
                        version,
                        prereleases=policy.prereleases,
                    )
                ):
                    continue
                baseline_version = baseline_versions[dependency]
                if (
                    policy.space == "current-major"
                    and self._release(version)[0]
                    != self._release(baseline_version)[0]
                ):
                    continue
                if (
                    policy.space == "current-minor"
                    and self._release(version)[:2]
                    != self._release(baseline_version)[:2]
                ):
                    continue
                artifact = self._artifact(
                    raw_candidate.artifacts,
                    cell=cell,
                    artifact_policy=package.config.resolution.artifact,
                )
                if artifact is not None:
                    eligible.append((version, artifact))
            representatives: dict[str, tuple[Version, AvailableArtifact]] = {}
            for version, artifact in eligible:
                key = self._series_key(version, policy.step)
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
                source_plan_identity=plan_identity,
                source=source,
                candidates=candidates,
                series_representatives=representatives_record,
            )
            snapshots.append(
                CandidateSnapshot(
                    dependency=dependency,
                    cell=cell,
                    policy_identity=policy_identity,
                    source_plan_identity=plan_identity,
                    source=source,
                    candidates=candidates,
                    series_representatives=representatives_record,
                    digest=digest,
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _artifact(
        artifacts: tuple[AvailableArtifact, ...],
        *,
        cell: Cell,
        artifact_policy: str,
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
        if artifact_policy == "wheel":
            return wheels[0] if wheels else None
        if artifact_policy == "sdist":
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

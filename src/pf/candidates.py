from __future__ import annotations

import hashlib
import threading
from typing import Protocol

from packaging.specifiers import Specifier
from packaging.version import Version

from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.search_space import SEARCH_SPACE_PROFILE, bind_policy, evaluate
from pf.schemas.base import canonical_identity_json
from pf.schemas.project import (
    AvailableArtifact,
    RegistryCandidates,
    SeriesInventory,
    Candidate,
    CandidateSnapshot,
    Cell,
    NamedSearchPolicy,
    PackagePlan,
    SelectedCandidate,
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
    effective_space: str,
) -> str:
    """Identify only the facts that select one dependency's candidates."""
    return hashlib.sha256(
        b"pf:candidate-policy:v1\0"
        + canonical_identity_json(
            {
                "profile": SEARCH_SPACE_PROFILE,
                "policy": {
                    "name": policy.name,
                    "space": effective_space,
                    "resolution": policy.resolution,
                    "prereleases": policy.prereleases,
                },
                "artifact": artifact,
                "artifact_admission": "cell-eligibility-before-sha256",
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
    ) -> RegistryCandidates: ...


class CandidateBuilder:
    """Freeze every candidate policy decision for one package/cell."""

    def __init__(self, provider: CandidateProvider) -> None:
        self._provider = provider
        self._query_lock = threading.Lock()
        self._queries: dict[
            tuple[str, SourceIdentity, tuple[str, str, str, tuple[str, ...]]],
            RegistryCandidates,
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
            bound = bind_policy(policy, declarations=package.declarations, cell=cell)
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
            selection = evaluate(
                bound,
                baseline=baseline_versions[dependency],
                release_versions=available.release_versions,
                dependency=dependency,
                cell=cell,
                source=source,
            )
            series_inventory = (
                SeriesInventory(
                    dependency=dependency,
                    source=source,
                    family=selection.family,
                    series_keys=selection.series_keys,
                )
                if selection.family is not None
                else None
            )
            policy_identity = candidate_policy_identity(
                policy,
                artifact=package.config.resolution.artifact,
                effective_space=selection.expression,
            )
            baseline_selection = self._baseline_selection(
                dependency=dependency,
                version=baseline_versions[dependency],
                available=available,
                cell=cell,
                artifact_policy=package.config.resolution.artifact,
            )
            eligible: list[tuple[Version, AvailableArtifact]] = []
            for raw_candidate in available.candidates:
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
                if not selection.contains(version):
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
                key = candidate_series_key(version, policy.resolution)
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
                baseline_selection=baseline_selection,
                candidates=candidates,
                series_representatives=representatives_record,
                selection=selection,
                series_inventory=series_inventory,
            )
            snapshots.append(
                CandidateSnapshot(
                    dependency=dependency,
                    cell=cell,
                    policy_identity=policy_identity,
                    source_plan_identity=plan_identity,
                    source=source,
                    baseline_selection=baseline_selection,
                    candidates=candidates,
                    series_representatives=representatives_record,
                    selection=selection,
                    series_inventory=series_inventory,
                    digest=digest,
                )
            )
        return tuple(snapshots)

    @classmethod
    def _baseline_selection(
        cls,
        *,
        dependency: str,
        version: Version,
        available: RegistryCandidates,
        cell: Cell,
        artifact_policy: str,
    ) -> SelectedCandidate:
        matches = tuple(
            candidate
            for candidate in available.candidates
            if Version(candidate.version) == version
        )
        if len(matches) != 1:
            raise InfrastructureError(
                f"registry observation cannot select baseline artifact: {dependency}"
            )
        artifact = cls._artifact(
            matches[0].artifacts,
            cell=cell,
            artifact_policy=artifact_policy,
        )
        if artifact is None:
            raise InfrastructureError(
                f"registry observation cannot select baseline artifact: {dependency}"
            )
        try:
            return SelectedCandidate(
                dependency=dependency,
                version=str(version),
                artifact=artifact,
            )
        except ValueError as error:
            raise InfrastructureError(
                f"registry observation cannot close baseline artifact: {dependency}"
            ) from error

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


def candidate_series_key(version: Version, granularity: str) -> str:
    release_parts = (*version.release, 0, 0, 0)
    major, minor, patch = release_parts[:3]
    if granularity == "major":
        release = f"{major}"
    elif granularity == "minor":
        release = f"{major}.{minor}"
    else:
        release = f"{major}.{minor}.{patch}"
    return f"{version.epoch}!{release}" if version.epoch else release

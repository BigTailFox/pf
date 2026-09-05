from __future__ import annotations

from packaging.version import Version

from pf.schemas.project import (
    AvailableCandidate,
    RegistryCandidates,
    Candidate,
    CandidateSnapshot,
    PackagePlan,
    Cell,
    SourcePlan,
    VersionPin,
)
from pf.candidates import CandidateBuilder
from pf.search_space import declaration_anchor


def registry_candidates(
    candidates: tuple[AvailableCandidate, ...],
) -> RegistryCandidates:
    """An unfiltered fixture response in which every release has a candidate."""
    return RegistryCandidates(
        release_versions=tuple(
            str(version)
            for version in sorted({Version(item.version) for item in candidates})
        ),
        candidates=candidates,
    )


def frozen_candidate_snapshot(
    package: PackagePlan, cell: Cell, candidates: tuple[Candidate, ...]
) -> CandidateSnapshot:
    """Freeze a small fixture registry with baseline 3.11 and declared series."""
    name = next(
        d.name
        for d in package.declarations
        if d.managed and d.declaration_id in cell.active_declaration_ids
    )
    lower = declaration_anchor(
        d.specifier
        for d in package.declarations
        if d.name == name and d.declaration_id in cell.active_declaration_ids
    )
    versions = {Version("3.11"), *(Version(item.version) for item in candidates)}
    if lower is not None:
        versions.add(lower)
    inventory = RegistryCandidates(
        release_versions=tuple(str(version) for version in sorted(versions)),
        candidates=tuple(
            AvailableCandidate(version=item.version, artifacts=(item.artifact,))
            for item in candidates
        ),
    )

    class FixtureRegistry:
        def query(self, **kwargs: object) -> RegistryCandidates:
            return inventory

    return CandidateBuilder(FixtureRegistry()).build(
        package=package,
        cell=cell,
        baseline=(VersionPin(name=name, version="3.11"),),
        source_plan=SourcePlan.for_package(package, "SEARCH"),
    )[0]

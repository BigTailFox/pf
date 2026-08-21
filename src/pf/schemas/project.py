from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.config import EffectiveConfig


class SourceIdentity(FrozenSchema):
    kind: Literal["registry", "path", "workspace", "git", "url"]
    locator: str | None = None
    index: str | None = None
    commit: str | None = None
    content_hash: str | None = None


class SourcePlan(FrozenSchema):
    identities: tuple[SourceIdentity, ...]


class SnapshotEntry(FrozenSchema):
    path: str
    kind: Literal["directory", "file", "symlink"]
    mode: int
    content_digest: str | None = None
    link_target: str | None = None


class SourceSnapshotIdentity(FrozenSchema):
    digest: str
    entries: tuple[SnapshotEntry, ...]


class RequirementDeclaration(FrozenSchema):
    declaration_id: str
    package: str
    location: Literal["base", "optional"]
    extra: str | None = None
    name: str
    requested_extras: tuple[str, ...] = ()
    specifier: str = ""
    marker: str | None = None
    source: SourceIdentity
    pyproject_path: str
    raw: str
    kind: Literal["searchable", "fixed"]
    managed: bool


class Cell(FrozenSchema):
    package: str
    target: str
    python_minor: str
    extra_surface: tuple[str, ...]
    active_declaration_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_normalized_cell(self) -> "Cell":
        if self.target in {"linux", "macos", "windows"}:
            raise ValueError("cell target must be an exact uv target triple")
        if tuple(sorted(set(self.extra_surface))) != self.extra_surface:
            raise ValueError("extra surface must be sorted and unique")
        if (
            tuple(sorted(set(self.active_declaration_ids)))
            != self.active_declaration_ids
        ):
            raise ValueError("active declaration IDs must be sorted and unique")
        return self


class ResolvedNode(FrozenSchema):
    name: str
    version: str
    dependencies: tuple[str, ...] = ()


class VersionPin(FrozenSchema):
    name: str
    version: str


class InterpreterIdentity(FrozenSchema):
    implementation: Literal["cpython"]
    version: str
    abi: str


class Proposal(FrozenSchema):
    proposal_id: str
    attempt_id: str | None = None
    snapshot_digest: str
    cell: Cell
    managed_vector: tuple[VersionPin, ...]
    fixed_declaration_ids: tuple[str, ...]
    resolved_graph: tuple[ResolvedNode, ...]
    policy_identity: str
    interpreter: InterpreterIdentity | None = None


class AvailableArtifact(FrozenSchema):
    filename: str
    kind: Literal["wheel", "sdist"]
    content_hash: str
    locator: str | None = None
    python_minors: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()


class AvailableCandidate(FrozenSchema):
    version: str
    yanked: bool = False
    artifacts: tuple[AvailableArtifact, ...]


class Candidate(FrozenSchema):
    version: str
    series_key: str
    artifact: AvailableArtifact
    prerelease: bool = False


class CandidateSnapshot(FrozenSchema):
    dependency: str
    cell: Cell
    policy_identity: str
    source: SourceIdentity
    candidates: tuple[Candidate, ...]
    series_representatives: tuple[tuple[str, str], ...]
    digest: str

    @model_validator(mode="after")
    def validate_candidate_order(self) -> "CandidateSnapshot":
        if not self.candidates:
            raise ValueError("candidate snapshot cannot be empty")
        versions = [candidate.version for candidate in self.candidates]
        if len(set(versions)) != len(versions):
            raise ValueError("candidate snapshot versions must be unique")
        return self


class PackagePlan(FrozenSchema):
    name: str
    pyproject_path: str
    config: EffectiveConfig
    declarations: tuple[RequirementDeclaration, ...]
    cells: tuple[Cell, ...]
    source_plan: SourcePlan
    test_requirements: tuple[str, ...] = ()
    test_group_present: bool = False


class ProjectPlan(FrozenSchema):
    root: str = "."
    packages: tuple[PackagePlan, ...]

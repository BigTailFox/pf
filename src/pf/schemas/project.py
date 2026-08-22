from __future__ import annotations

import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.config import EffectiveConfig


def public_locator(value: str) -> str:
    """Return a portable URL without userinfo or query."""
    parsed = urlsplit(value)
    if not parsed.scheme:
        return value
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = host
    if parsed.scheme == "file" and parsed.path.startswith("/"):
        netloc = host
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


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


def cell_identity(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    """Lookup key for a compatibility cell. Not an order contract."""
    return (cell.package, cell.target, cell.python_minor, cell.extra_surface)


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


def candidate_snapshot_digest(
    *,
    dependency: str,
    cell: Cell,
    policy_identity: str,
    source: SourceIdentity,
    candidates: tuple[Candidate, ...],
    series_representatives: tuple[tuple[str, str], ...],
) -> str:
    identity = {
        "dependency": dependency,
        "cell": cell.model_dump(mode="json"),
        "policy_identity": policy_identity,
        "source": source.model_dump(mode="json"),
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        "series_representatives": series_representatives,
    }
    return hashlib.sha256(
        b"pf:candidate-snapshot:v1\0"
        + json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class SelectedCandidate(FrozenSchema):
    dependency: str
    version: str
    artifact: AvailableArtifact

    @model_validator(mode="after")
    def validate_installable_artifact(self) -> "SelectedCandidate":
        if not self.dependency.strip() or not self.version.strip():
            raise ValueError("selected candidate identity cannot be empty")
        if not self.artifact.filename.strip() or self.artifact.locator is None:
            raise ValueError("selected candidate requires an artifact locator")
        if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", self.artifact.content_hash) is None:
            raise ValueError("selected candidate requires a complete SHA-256 hash")
        if public_locator(self.artifact.locator) != self.artifact.locator:
            raise ValueError("selected candidate locator must be public")
        return self


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
        expected_digest = candidate_snapshot_digest(
            dependency=self.dependency,
            cell=self.cell,
            policy_identity=self.policy_identity,
            source=self.source,
            candidates=self.candidates,
            series_representatives=self.series_representatives,
        )
        if self.digest != expected_digest:
            raise ValueError("candidate snapshot digest does not match its evidence")
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

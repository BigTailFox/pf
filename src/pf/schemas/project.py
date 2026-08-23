from __future__ import annotations

import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from packaging.specifiers import InvalidSpecifier, Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
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


class HarnessGroupProvenance(FrozenSchema):
    owner: Literal["root", "package"]
    pyproject_path: str
    group_path: tuple[str, ...]
    item_path: tuple[int, ...]

    @model_validator(mode="after")
    def validate_group_path(self) -> "HarnessGroupProvenance":
        if not self.pyproject_path or not self.group_path:
            raise ValueError("harness provenance requires a pyproject and group")
        if len(self.group_path) != len(self.item_path):
            raise ValueError("harness group and item paths must have equal depth")
        if any(not group for group in self.group_path) or any(
            index < 0 for index in self.item_path
        ):
            raise ValueError("harness provenance path entries must be valid")
        return self


class HarnessSpecifierClause(FrozenSchema):
    operator: Literal["~=", "==", "!=", "<=", ">=", "<", ">", "==="]
    version: str

    @model_validator(mode="after")
    def validate_clause(self) -> "HarnessSpecifierClause":
        try:
            Specifier(f"{self.operator}{self.version}")
        except InvalidSpecifier as error:
            raise ValueError("invalid harness specifier clause") from error
        return self


class HarnessRequirement(FrozenSchema):
    declaration_id: str
    package: str
    provenance: HarnessGroupProvenance
    name: str
    requested_extras: tuple[str, ...] = ()
    specifier: tuple[HarnessSpecifierClause, ...] = ()
    marker: str | None = None
    source: SourceIdentity
    prerelease_allowed: bool = False
    original_text: str

    @staticmethod
    def identity_digest(
        *,
        package: str,
        provenance: HarnessGroupProvenance,
        name: str,
        requested_extras: tuple[str, ...],
        specifier: tuple[HarnessSpecifierClause, ...],
        marker: str | None,
        source: SourceIdentity,
        original_text: str,
    ) -> str:
        identity = {
            "package": package,
            "provenance": provenance.model_dump(mode="json"),
            "name": name,
            "requested_extras": requested_extras,
            "specifier": [item.model_dump(mode="json") for item in specifier],
            "marker": marker,
            "source": source.model_dump(mode="json"),
            "original_text": original_text,
        }
        return hashlib.sha256(
            b"pf:harness-declaration:v1\0"
            + json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @model_validator(mode="after")
    def validate_requirement(self) -> "HarnessRequirement":
        if canonicalize_name(self.name) != self.name or not self.original_text:
            raise ValueError("harness requirement must retain normalized identity")
        if tuple(sorted(set(self.requested_extras))) != self.requested_extras:
            raise ValueError("harness extras must be sorted and unique")
        clause_keys = tuple((item.operator, item.version) for item in self.specifier)
        if clause_keys != tuple(sorted(set(clause_keys))):
            raise ValueError("harness specifier clauses must be sorted and unique")
        expected = self.identity_digest(
            package=self.package,
            provenance=self.provenance,
            name=self.name,
            requested_extras=self.requested_extras,
            specifier=self.specifier,
            marker=self.marker,
            source=self.source,
            original_text=self.original_text,
        )
        if self.declaration_id != expected:
            raise ValueError("harness declaration ID does not match its identity")
        return self


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
    kind: Literal["wheel", "sdist", "archive"]
    content_hash: str
    locator: str | None = None
    python_minors: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()


class AvailableCandidate(FrozenSchema):
    version: str
    yanked: bool = False
    artifacts: tuple[AvailableArtifact, ...]


class HarnessSelection(FrozenSchema):
    name: str
    version: str
    source: SourceIdentity
    selected_artifact: AvailableArtifact | None = None
    ceiling_bound: bool

    @model_validator(mode="after")
    def validate_selection(self) -> "HarnessSelection":
        if canonicalize_name(self.name) != self.name:
            raise ValueError("harness selection name must be canonical")
        try:
            Version(self.version)
        except InvalidVersion as error:
            raise ValueError("harness selection version must be normalized") from error
        return self


def harness_baseline_digest(
    *,
    cell: Cell,
    declaration_ids: tuple[str, ...],
    selections: tuple[HarnessSelection, ...],
) -> str:
    identity = {
        "cell": cell.model_dump(mode="json"),
        "declaration_ids": declaration_ids,
        "selections": [item.model_dump(mode="json") for item in selections],
    }
    return hashlib.sha256(
        b"pf:harness-baseline:v1\0"
        + json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class HarnessBaseline(FrozenSchema):
    cell: Cell
    declaration_ids: tuple[str, ...]
    selections: tuple[HarnessSelection, ...]
    digest: str

    @classmethod
    def from_evidence(
        cls,
        *,
        cell: Cell,
        declaration_ids: tuple[str, ...],
        selections: tuple[HarnessSelection, ...],
    ) -> "HarnessBaseline":
        return cls(
            cell=cell,
            declaration_ids=declaration_ids,
            selections=selections,
            digest=harness_baseline_digest(
                cell=cell,
                declaration_ids=declaration_ids,
                selections=selections,
            ),
        )

    @model_validator(mode="after")
    def validate_baseline(self) -> "HarnessBaseline":
        if self.declaration_ids != tuple(sorted(set(self.declaration_ids))):
            raise ValueError("harness baseline declarations must be sorted and unique")
        names = tuple(item.name for item in self.selections)
        if names != tuple(sorted(set(names))):
            raise ValueError("harness baseline selections must be sorted and unique")
        expected = harness_baseline_digest(
            cell=self.cell,
            declaration_ids=self.declaration_ids,
            selections=self.selections,
        )
        if self.digest != expected:
            raise ValueError("harness baseline digest does not match its evidence")
        return self


class HarnessResolutionRequirement(FrozenSchema):
    declaration: HarnessRequirement
    specifier: tuple[HarnessSpecifierClause, ...]
    relaxed_minimum: bool
    ceiling: str | None = None

    @model_validator(mode="after")
    def validate_resolution_requirement(self) -> "HarnessResolutionRequirement":
        clause_keys = tuple((item.operator, item.version) for item in self.specifier)
        if clause_keys != tuple(sorted(set(clause_keys))):
            raise ValueError("resolved harness clauses must be sorted and unique")
        if self.ceiling is not None:
            try:
                Version(self.ceiling)
            except InvalidVersion as error:
                raise ValueError("harness ceiling must be a normalized version") from error
        return self


def relaxed_harness_digest(
    *, baseline_digest: str, requirements: tuple[HarnessResolutionRequirement, ...]
) -> str:
    identity = {
        "policy": "harness-relaxation-v1",
        "baseline_digest": baseline_digest,
        "requirements": [item.model_dump(mode="json") for item in requirements],
    }
    return hashlib.sha256(
        b"pf:relaxed-harness:v1\0"
        + json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RelaxedHarness(FrozenSchema):
    policy_identity: Literal["harness-relaxation-v1"] = "harness-relaxation-v1"
    baseline_digest: str
    requirements: tuple[HarnessResolutionRequirement, ...]
    digest: str

    @classmethod
    def from_requirements(
        cls,
        *,
        baseline_digest: str,
        requirements: tuple[HarnessResolutionRequirement, ...],
    ) -> "RelaxedHarness":
        return cls(
            baseline_digest=baseline_digest,
            requirements=requirements,
            digest=relaxed_harness_digest(
                baseline_digest=baseline_digest,
                requirements=requirements,
            ),
        )

    @model_validator(mode="after")
    def validate_relaxed_harness(self) -> "RelaxedHarness":
        expected = relaxed_harness_digest(
            baseline_digest=self.baseline_digest,
            requirements=self.requirements,
        )
        if self.digest != expected:
            raise ValueError("relaxed harness digest does not match its evidence")
        return self


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
    harness_requirements: tuple[HarnessRequirement, ...] = ()
    test_group_present: bool = False


class ProjectPlan(FrozenSchema):
    root: str = "."
    packages: tuple[PackagePlan, ...]

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from packaging.specifiers import InvalidSpecifier, Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import field_validator, model_validator

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.config import EffectiveConfig


_CANONICAL_DISTRIBUTION_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def is_canonical_distribution_name(value: str) -> bool:
    """Return whether ``value`` is a canonical public distribution name."""
    return _CANONICAL_DISTRIBUTION_NAME.fullmatch(value) is not None


def public_relative_path(value: str, *, allow_parent: bool = False) -> str:
    """Validate one portable, non-host-absolute path stored in a report."""
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or any(ord(character) < 32 for character in value)
        or (not allow_parent and (".." in posix.parts or ".." in windows.parts))
    ):
        raise ValueError("report path must be public and relative")
    return value


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


ResolutionSourceMode = Literal["DEVELOPMENT", "SEARCH"]


class StaticWorkspaceMemberVersion(FrozenSchema):
    kind: Literal["static"] = "static"
    value: str

    @field_validator("value")
    @classmethod
    def validate_version(cls, value: str) -> str:
        try:
            normalized = str(Version(value))
        except InvalidVersion as error:
            raise ValueError(
                "workspace member version must be valid PEP 440"
            ) from error
        if normalized != value:
            raise ValueError("workspace member version must be normalized")
        return value


class DynamicWorkspaceMemberVersion(FrozenSchema):
    kind: Literal["dynamic"] = "dynamic"


WorkspaceMemberVersion = StaticWorkspaceMemberVersion | DynamicWorkspaceMemberVersion


class DependencySourceRoute(FrozenSchema):
    dependency: str
    development_source: SourceIdentity
    search_source: SourceIdentity
    workspace_member_version: WorkspaceMemberVersion | None = None

    @model_validator(mode="after")
    def validate_route(self) -> "DependencySourceRoute":
        if not is_canonical_distribution_name(self.dependency):
            raise ValueError("source route dependency must be canonical")
        if self.development_source.kind == "workspace":
            if self.workspace_member_version is None:
                raise ValueError(
                    "workspace source route requires member version metadata"
                )
        elif self.workspace_member_version is not None:
            raise ValueError(
                "non-workspace source route cannot retain member version metadata"
            )
        if (
            self.development_source.kind != "workspace"
            and self.development_source != self.search_source
        ):
            raise ValueError("only workspace development sources may use dual routes")
        if (
            self.search_source.kind == "workspace"
            and self.search_source != self.development_source
        ):
            raise ValueError("a local workspace route must use one source identity")
        if self.search_source.kind not in {
            self.development_source.kind,
            "registry",
        }:
            raise ValueError(
                "workspace search source must remain local or use a registry"
            )
        return self


class SourcePlan(FrozenSchema):
    source_mode: ResolutionSourceMode
    routes: tuple[DependencySourceRoute, ...]

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            b"pf:source-plan:v1\0"
            + json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @classmethod
    def for_package(
        cls,
        package: PackagePlan,
        mode: ResolutionSourceMode,
    ) -> SourcePlan:
        return cls(source_mode=mode, routes=package.source_routes)

    def source_for(self, dependency: str) -> SourceIdentity:
        route = next(
            (item for item in self.routes if item.dependency == dependency),
            None,
        )
        if route is None:
            raise ValueError(f"source plan route is missing: {dependency}")
        if self.source_mode == "DEVELOPMENT":
            return route.development_source
        return route.search_source

    def registry_routed_workspace_dependencies(self) -> tuple[str, ...]:
        if self.source_mode == "DEVELOPMENT":
            return ()
        return tuple(
            route.dependency
            for route in self.routes
            if route.development_source.kind == "workspace"
            and route.search_source.kind == "registry"
        )

    def workspace_member_version_for(
        self,
        dependency: str,
    ) -> WorkspaceMemberVersion | None:
        route = next(
            (item for item in self.routes if item.dependency == dependency),
            None,
        )
        if route is None:
            raise ValueError(f"source plan route is missing: {dependency}")
        return route.workspace_member_version

    @model_validator(mode="after")
    def validate_routes(self) -> "SourcePlan":
        names = tuple(route.dependency for route in self.routes)
        if names != tuple(sorted(set(names))):
            raise ValueError("source plan routes must be sorted and unique")
        return self


class SnapshotEntry(FrozenSchema):
    path: str
    kind: Literal["directory", "file", "symlink"]
    mode: int
    content_digest: str | None = None
    link_target: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return public_relative_path(value)

    @field_validator("link_target")
    @classmethod
    def validate_link_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return public_relative_path(value, allow_parent=True)


class PyprojectIdentity(FrozenSchema):
    path: str
    mode: int
    remainder_digest: str
    dependency_arrays_digest: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return public_relative_path(value)


def source_snapshot_digest(
    entries: tuple[SnapshotEntry, ...],
    pyproject_identities: tuple[PyprojectIdentity, ...],
) -> str:
    """Return the canonical identity for a complete source manifest."""
    canonical_entries = tuple(sorted(entries, key=lambda entry: entry.path))
    canonical_pyprojects = tuple(
        sorted(pyproject_identities, key=lambda identity: identity.path)
    )
    payload = {
        "entries": [entry.model_dump(mode="json") for entry in canonical_entries],
        "pyproject_identities": [
            identity.model_dump(mode="json") for identity in canonical_pyprojects
        ],
    }
    return hashlib.sha256(
        b"pf:source-snapshot:v1\0" + canonical_identity_json(payload)
    ).hexdigest()


class SourceSnapshotIdentity(FrozenSchema):
    digest: str
    entries: tuple[SnapshotEntry, ...]
    pyproject_identities: tuple[PyprojectIdentity, ...]


class RequirementDeclaration(FrozenSchema):
    declaration_id: str
    package: str
    location: Literal["base", "optional"]
    extra: str | None = None
    name: str
    requested_extras: tuple[str, ...] = ()
    specifier: str = ""
    marker: str | None = None
    pyproject_path: str
    raw: str
    kind: Literal["searchable", "fixed"]
    managed: bool

    @field_validator("package", "name")
    @classmethod
    def validate_distribution_name(cls, value: str) -> str:
        if not is_canonical_distribution_name(value):
            raise ValueError("requirement names must be canonical distribution names")
        return value

    @field_validator("pyproject_path")
    @classmethod
    def validate_pyproject_path(cls, value: str) -> str:
        return public_relative_path(value)


class ApplySelector(FrozenSchema):
    sys_platform: str
    platform_machine: str


class DependencyGroupKey(FrozenSchema):
    pyproject_path: str
    location: Literal["base", "optional"]
    optional_group: str | None = None
    name: str

    @field_validator("pyproject_path")
    @classmethod
    def validate_pyproject_path(cls, value: str) -> str:
        return public_relative_path(value)


def dependency_group_key(declaration: RequirementDeclaration) -> DependencyGroupKey:
    return DependencyGroupKey(
        pyproject_path=declaration.pyproject_path,
        location=declaration.location,
        optional_group=declaration.extra,
        name=declaration.name,
    )


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
        original_text: str,
    ) -> str:
        identity = {
            "package": package,
            "provenance": provenance.model_dump(mode="json"),
            "name": name,
            "requested_extras": requested_extras,
            "specifier": [item.model_dump(mode="json") for item in specifier],
            "marker": marker,
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
        if not is_canonical_distribution_name(self.package):
            raise ValueError("cell package must be a canonical distribution name")
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


def cell_id(cell: Cell) -> str:
    """Return the stable Schema 1 reference identity for a compatibility Cell."""
    payload = {
        "package": cell.package,
        "target": cell.target,
        "python_minor": cell.python_minor,
        "extra_surface": cell.extra_surface,
    }
    return (
        "cell-"
        + hashlib.sha256(b"pf:cell:v1\0" + canonical_identity_json(payload)).hexdigest()
    )


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
    project_plan_digest: str | None = None
    environment_plan_digest: str | None = None
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
                raise ValueError(
                    "harness ceiling must be a normalized version"
                ) from error
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
    source_plan_identity: str,
    source: SourceIdentity,
    candidates: tuple[Candidate, ...],
    series_representatives: tuple[tuple[str, str], ...],
) -> str:
    identity = {
        "dependency": dependency,
        "cell": cell.model_dump(mode="json"),
        "policy_identity": policy_identity,
        "source_plan_identity": source_plan_identity,
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


def selected_candidate_evidence_digest(
    selection: tuple[SelectedCandidate, ...],
) -> str:
    names = tuple(item.dependency for item in selection)
    if names != tuple(sorted(set(names))):
        raise ValueError("selected candidate dependencies must be sorted and unique")
    return hashlib.sha256(
        b"pf:selected-candidates:v1\0"
        + json.dumps(
            [item.model_dump(mode="json") for item in selection],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class CandidateSnapshot(FrozenSchema):
    dependency: str
    cell: Cell
    policy_identity: str
    source_plan_identity: str
    source: SourceIdentity
    candidates: tuple[Candidate, ...]
    series_representatives: tuple[tuple[str, str], ...]
    digest: str

    @model_validator(mode="after")
    def validate_candidate_order(self) -> "CandidateSnapshot":
        if not self.candidates:
            raise ValueError("candidate snapshot cannot be empty")
        try:
            versions = tuple(
                Version(candidate.version) for candidate in self.candidates
            )
        except InvalidVersion as error:
            raise ValueError(
                "candidate snapshot versions must be normalized"
            ) from error
        if any(
            str(version) != candidate.version
            for version, candidate in zip(versions, self.candidates, strict=True)
        ):
            raise ValueError("candidate snapshot versions must be normalized")
        if versions != tuple(sorted(set(versions))):
            raise ValueError("candidate snapshot versions must be sorted and unique")
        representatives = tuple(
            (candidate.series_key, candidate.version) for candidate in self.candidates
        )
        if self.series_representatives != representatives or len(
            {key for key, _ in representatives}
        ) != len(representatives):
            raise ValueError(
                "candidate snapshot series representatives must match candidates"
            )
        expected_digest = candidate_snapshot_digest(
            dependency=self.dependency,
            cell=self.cell,
            policy_identity=self.policy_identity,
            source_plan_identity=self.source_plan_identity,
            source=self.source,
            candidates=self.candidates,
            series_representatives=self.series_representatives,
        )
        if self.digest != expected_digest:
            raise ValueError("candidate snapshot digest does not match its evidence")
        return self


class NamedSearchPolicy(FrozenSchema):
    name: str
    space: str
    step: Literal["major", "minor", "patch"]
    prereleases: bool

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not is_canonical_distribution_name(value):
            raise ValueError("search policy name must be canonical")
        return value


class PackagePlan(FrozenSchema):
    name: str
    pyproject_path: str
    requires_python: str | None = None
    config: EffectiveConfig
    declarations: tuple[RequirementDeclaration, ...]
    cells: tuple[Cell, ...]
    source_routes: tuple[DependencySourceRoute, ...]
    dependency_search_policies: tuple[NamedSearchPolicy, ...] = ()
    harness_requirements: tuple[HarnessRequirement, ...] = ()
    test_group_present: bool = False

    @model_validator(mode="after")
    def validate_source_routes(self) -> "PackagePlan":
        names = tuple(route.dependency for route in self.source_routes)
        if names != tuple(sorted(set(names))):
            raise ValueError("package source routes must be sorted and unique")
        dependency_names = {
            *(declaration.name for declaration in self.declarations),
            *(requirement.name for requirement in self.harness_requirements),
        }
        if not dependency_names <= set(names):
            raise ValueError("package source routes must cover every direct dependency")
        policy_names = tuple(
            policy.name for policy in self.dependency_search_policies
        )
        if policy_names != tuple(sorted(set(policy_names))):
            raise ValueError("dependency search policies must be sorted and unique")
        managed_searchable = {
            declaration.name
            for declaration in self.declarations
            if declaration.managed and declaration.kind == "searchable"
        }
        if set(policy_names) != managed_searchable:
            raise ValueError(
                "dependency search policies must cover managed searchable dependencies"
            )
        return self

    def search_policy_for(self, dependency: str) -> NamedSearchPolicy:
        if canonicalize_name(dependency) != dependency:
            raise ValueError("search policy lookup requires a canonical name")
        for policy in self.dependency_search_policies:
            if policy.name == dependency:
                return policy
        raise ValueError(f"dependency search policy is missing: {dependency}")


class ProjectPlan(FrozenSchema):
    root: str = "."
    target: PackagePlan
    owned_pyproject_paths: tuple[str, ...] = ()

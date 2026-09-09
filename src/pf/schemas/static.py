"""Canonical, offline-verifiable inputs for static observations.

Content manifests are input facts, not compatibility or comparison evidence.
"""

from __future__ import annotations

import hashlib
import json
import csv
import io
from pathlib import PurePosixPath
import posixpath
from typing import Annotated, Literal, Union

from packaging.version import Version
from pydantic import Field, field_validator, model_validator, model_serializer

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.project import (
    Cell, InterpreterIdentity, SourceIdentity, SourcePlan, public_relative_path,
    is_canonical_distribution_name,
)


class StaticContentPath(FrozenSchema):
    root: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    path: str

    @field_validator("path")
    @classmethod
    def canonical_path(cls, value: str) -> str:
        public_relative_path(value)
        if "\\" in value or PurePosixPath(value).as_posix() != value:
            raise ValueError("static content path must be canonical POSIX")
        return value


class StaticTextLiteral(FrozenSchema):
    kind: Literal["literal"] = "literal"
    text: str = Field(min_length=1)


class StaticTextRoot(FrozenSchema):
    kind: Literal["logical-root"] = "logical-root"
    root: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    encoding: Literal["path", "file-uri"]


class StaticTextFileValue(FrozenSchema):
    kind: Literal["file-value"] = "file-value"
    column: Literal["hash", "size"]
    location: StaticContentPath
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


StaticTextPart = Annotated[Union[StaticTextLiteral, StaticTextRoot, StaticTextFileValue], Field(discriminator="kind")]


def _record_references(parts: tuple[StaticTextPart, ...]) -> tuple[tuple[str, StaticTextFileValue], ...]:
    markers: dict[str, StaticTextFileValue] = {}
    text: list[str] = []
    for index, part in enumerate(parts):
        if isinstance(part, StaticTextLiteral):
            text.append(part.text)
        elif isinstance(part, StaticTextFileValue):
            marker = f"__pf_record_value_{index}__"
            markers[marker] = part
            text.append(marker)
        else:
            raise ValueError("RECORD does not accept logical root tokens")
    refs: list[tuple[str, StaticTextFileValue]] = []
    used: set[str] = set()
    try:
        rows = list(csv.reader(io.StringIO("".join(text), newline=""), strict=True))
    except csv.Error as error:
        raise ValueError("invalid relocated RECORD CSV") from error
    for row in rows:
        if len(row) != 3:
            raise ValueError("RECORD must retain three CSV columns")
        values = [markers.get(value) for value in row]
        if values[0] is not None:
            raise ValueError("RECORD filename cannot be a file value token")
        if values[1] is not None or values[2] is not None:
            digest, size = values[1:]
            if (
                digest is None or size is None or digest.column != "hash" or size.column != "size"
                or digest.location != size.location or digest.content_digest != size.content_digest
            ):
                raise ValueError("RECORD hash and size must reference the same file")
            used.update(row[1:])
            refs.append((row[0], digest))
    if used != set(markers):
        raise ValueError("RECORD file values must occupy complete hash and size fields")
    return tuple(refs)


class StaticTextProjection(FrozenSchema):
    profile: Literal["editable-pth-v1", "direct-url-v1", "venv-activation-v1", "script-shebang-v1", "installed-record-v1"]
    parts: tuple[StaticTextPart, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_parts(self) -> StaticTextProjection:
        if not any(not isinstance(part, StaticTextLiteral) for part in self.parts):
            raise ValueError("relocated text must reference logical content")
        if any(isinstance(a, StaticTextLiteral) and isinstance(b, StaticTextLiteral) for a, b in zip(self.parts, self.parts[1:])):
            raise ValueError("relocated text literals must be coalesced")
        roots = [part for part in self.parts if isinstance(part, StaticTextRoot)]
        files = [part for part in self.parts if isinstance(part, StaticTextFileValue)]
        if self.profile == "installed-record-v1":
            if roots or not files:
                raise ValueError("RECORD relocation requires file value references")
            _record_references(self.parts)
            return self
        if files:
            raise ValueError("file value references are only valid in RECORD")
        rendered = "".join(
            part.text if isinstance(part, StaticTextLiteral) else
            ("file://" if part.encoding == "file-uri" else "") + "/__pf_static_root__/" + part.root
            for part in self.parts
            if isinstance(part, (StaticTextLiteral, StaticTextRoot))
        )
        if self.profile == "direct-url-v1":
            document = json.loads(rendered)
            if (
                not isinstance(document, dict)
                or not isinstance(document.get("dir_info"), dict)
                or not isinstance(document.get("url"), str)
                or not document["url"].startswith("file://")
                or document["url"].count("/__pf_static_root__/") != len(roots)
                or any(root.encoding != "file-uri" for root in roots)
            ):
                raise ValueError("direct URL relocation must occur only in its file URL")
        else:
            if any(root.encoding != "path" for root in roots):
                raise ValueError("installed text relocation requires path encoding")
            if self.profile == "editable-pth-v1" and any(line.startswith(("import ", "import\t")) for line in rendered.splitlines()):
                raise ValueError("executable pth content cannot use path-only relocation")
            if self.profile == "script-shebang-v1" and (
                not rendered.startswith("#!")
                or rendered.partition("\n")[0].count("/__pf_static_root__/") != len(roots)
            ):
                raise ValueError("script relocation must occur only in its shebang")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            b"pf:static-relocated-text:v1\0" + canonical_identity_json(self.model_dump(mode="json"))
        ).hexdigest()


class StaticContentEntry(FrozenSchema):
    location: StaticContentPath
    kind: Literal["directory", "file", "symlink"]
    content_digest: str | None = Field(pattern=r"^[0-9a-f]{64}$", json_schema_extra={"x-pf-preserve-null": True})
    link_target: StaticContentPath | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    relocation: StaticTextProjection | None = Field(default=None, json_schema_extra={"x-pf-preserve-null": True})

    @model_serializer(mode="wrap")
    def serialize_required_nulls(self, handler):
        result = handler(self)
        if self.content_digest is None:
            result["content_digest"] = None
        if self.link_target is None:
            result["link_target"] = None
        if self.relocation is None:
            result["relocation"] = None
        return result

    @model_validator(mode="after")
    def validate_content(self) -> StaticContentEntry:
        if (self.content_digest is not None) != (self.kind == "file"):
            raise ValueError("only static files have content digests")
        if (self.link_target is not None) != (self.kind == "symlink"):
            raise ValueError("only static symlinks have targets")
        if self.relocation is not None:
            if self.kind != "file" or self.content_digest != self.relocation.identity:
                raise ValueError("relocated file digest must match its canonical content")
            path = PurePosixPath(self.location.path)
            if self.location.root != "environment":
                raise ValueError("relocation requires an installed environment file")
            admitted = {
                "editable-pth-v1": path.suffix == ".pth",
                "direct-url-v1": path.name == "direct_url.json" and path.parent.name.endswith(".dist-info"),
                "venv-activation-v1": path.parent.as_posix() in {"bin", "Scripts"} and path.name in {"activate", "activate.bat", "activate.csh", "activate.fish", "activate.nu", "activate.ps1", "Activate.ps1"},
                "script-shebang-v1": path.parent.as_posix() in {"bin", "Scripts"},
                "installed-record-v1": path.name == "RECORD" and path.parent.name.endswith(".dist-info"),
            }
            if not admitted[self.relocation.profile]:
                raise ValueError("relocation profile does not match the installed file")
            if self.relocation.profile == "installed-record-v1":
                for filename, value in _record_references(self.relocation.parts):
                    expected = StaticContentPath(
                        root="environment",
                        path=posixpath.normpath((path.parent.parent / filename).as_posix()),
                    )
                    if expected != value.location:
                        raise ValueError("RECORD value does not match its named file")
        return self


class StaticContentManifest(FrozenSchema):
    format: Literal["static-content-v1"] = "static-content-v1"
    entries: tuple[StaticContentEntry, ...]

    @model_validator(mode="after")
    def validate_closure(self) -> StaticContentManifest:
        keys = [(item.location.root, item.location.path) for item in self.entries]
        if not keys or keys != sorted(set(keys)):
            raise ValueError("static content entries must be nonempty, unique and sorted")
        by_path = {item.location: item for item in self.entries}
        for item in self.entries:
            if item.relocation is not None:
                for part in item.relocation.parts:
                    if isinstance(part, StaticTextRoot) and StaticContentPath(root=part.root, path=".") not in by_path:
                        raise ValueError("relocated text root is outside content closure")
                    if isinstance(part, StaticTextFileValue):
                        target = by_path.get(part.location)
                        if target is None or target.content_digest != part.content_digest or target.relocation is None or target.relocation.profile == "installed-record-v1":
                            raise ValueError("RECORD value must reference relocated installed content")
            location = item.location
            if location.path != ".":
                parent = StaticContentPath(
                    root=location.root,
                    path=PurePosixPath(location.path).parent.as_posix(),
                )
                if parent not in by_path or by_path[parent].kind != "directory":
                    raise ValueError("static content parent directory is missing")
            seen: set[StaticContentPath] = set()
            current = item
            while current.link_target is not None:
                if current.location in seen:
                    raise ValueError("static content symlink cycle")
                seen.add(current.location)
                target = by_path.get(current.link_target)
                if target is None:
                    raise ValueError("static content symlink target is outside closure")
                current = target
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            b"pf:static-content:v1\0"
            + canonical_identity_json(self.model_dump(mode="json"))
        ).hexdigest()

    def entry_at(self, location: StaticContentPath) -> StaticContentEntry:
        """Resolve a registered path through this manifest's closed symlinks."""
        entries = {item.location: item for item in self.entries}
        entry = entries.get(location)
        if entry is None:
            raise ValueError("static content path is outside closure")
        while entry.link_target is not None:
            entry = entries[entry.link_target]
        return entry

    def for_roots(self, roots: frozenset[str]) -> StaticContentManifest:
        """Select whole logical roots, including their registered link closure."""
        available = {entry.location.root for entry in self.entries}
        if not roots or not roots <= available:
            raise ValueError("static content roots are outside closure")
        included = set(roots)
        while True:
            linked = {
                entry.link_target.root for entry in self.entries
                if entry.location.root in included and entry.link_target is not None
            }
            linked.update(
                part.root for entry in self.entries
                if entry.location.root in included and entry.relocation is not None
                for part in entry.relocation.parts if isinstance(part, StaticTextRoot)
            )
            linked.update(
                part.location.root for entry in self.entries
                if entry.location.root in included and entry.relocation is not None
                for part in entry.relocation.parts if isinstance(part, StaticTextFileValue)
            )
            if linked <= included:
                break
            included.update(linked)
        return StaticContentManifest(entries=tuple(
            entry for entry in self.entries if entry.location.root in included
        ))


class StaticContentUnavailable(FrozenSchema):
    reason: Literal["static-subject-unavailable"] = "static-subject-unavailable"
    detail: Literal[
        "unreadable-content", "unsupported-file-kind", "unclosed-symlink",
        "content-changed", "invalid-layout",
        "inspection-unavailable", "installed-input-mismatch",
        "undeclared-analysis-root", "resolution-artifact-unbound",
        "configuration-context-unavailable", "configuration-unreadable",
    ]


class StaticPackageMapping(FrozenSchema):
    package: str
    source: StaticContentPath

    @field_validator("package")
    @classmethod
    def canonical_package(cls, value: str) -> str:
        if not is_canonical_distribution_name(value):
            raise ValueError("static package name must be canonical")
        return value


class StaticSourceInput(FrozenSchema):
    snapshot_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: StaticContentManifest
    packages: tuple[StaticPackageMapping, ...] = Field(min_length=1)
    source_plan: SourcePlan

    @model_validator(mode="after")
    def validate_packages(self) -> StaticSourceInput:
        names = tuple(item.package for item in self.packages)
        if names != tuple(sorted(set(names))):
            raise ValueError("static source packages must be sorted and unique")
        locations = {entry.location for entry in self.content.entries}
        if any(item.source not in locations for item in self.packages):
            raise ValueError("static source package must belong to its content closure")
        return self


class StaticTargetInput(FrozenSchema):
    cell: Cell
    interpreter: InterpreterIdentity
    content: StaticContentManifest
    executable: StaticContentPath
    stdlib_roots: tuple[StaticContentPath, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target(self) -> StaticTargetInput:
        version = Version(self.interpreter.version)
        if len(version.release) < 3:
            raise ValueError("static interpreter requires a complete version")
        if ".".join(str(part) for part in version.release[:2]) != self.cell.python_minor:
            raise ValueError("static interpreter must match the Cell Python")
        locations = {item.location for item in self.content.entries}
        if self.executable not in locations or not set(self.stdlib_roots) <= locations:
            raise ValueError("static interpreter inputs must belong to content closure")
        if self.content.entry_at(self.executable).kind != "file":
            raise ValueError("static interpreter executable must be a file")
        return self


class StaticInstalledArtifact(FrozenSchema):
    filename: str = Field(min_length=1)
    kind: Literal["wheel", "sdist", "archive"]
    locator: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class StaticInstalledNode(FrozenSchema):
    name: str
    version: str = Field(min_length=1)
    source: SourceIdentity
    artifact: StaticInstalledArtifact | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    dependencies: tuple[str, ...]
    install_mode: Literal["wheel", "editable", "source"]
    source_mapping: StaticContentPath | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    files: tuple[StaticContentPath, ...] = Field(min_length=1)

    @model_serializer(mode="wrap")
    def serialize_required_nulls(self, handler):
        result = handler(self)
        if self.artifact is None:
            result["artifact"] = None
        if self.source_mapping is None:
            result["source_mapping"] = None
        return result

    @model_validator(mode="after")
    def validate_node(self) -> StaticInstalledNode:
        if not is_canonical_distribution_name(self.name):
            raise ValueError("static installed name must be canonical")
        if str(Version(self.version)) != self.version:
            raise ValueError("static installed version must be normalized")
        if self.dependencies != tuple(sorted(set(self.dependencies))):
            raise ValueError("static installed dependencies must be sorted and unique")
        paths = tuple((item.root, item.path) for item in self.files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("static installed files must be sorted and unique")
        if self.install_mode == "editable" and self.source_mapping is None:
            raise ValueError("static editable installation requires a source mapping")
        return self


class StaticInstalledWorld(FrozenSchema):
    content: StaticContentManifest
    nodes: tuple[StaticInstalledNode, ...] = Field(min_length=1)
    support_files: tuple[StaticContentPath, ...]

    @model_validator(mode="after")
    def validate_world(self) -> StaticInstalledWorld:
        names = tuple(item.name for item in self.nodes)
        if names != tuple(sorted(set(names))):
            raise ValueError("static installed nodes must be sorted and unique")
        entries = {item.location: item for item in self.content.entries}
        covered = set(self.support_files)
        for node in self.nodes:
            if not set(node.dependencies) <= set(names):
                raise ValueError("static installed graph is not closed")
            if node.source_mapping is not None and node.source_mapping not in entries:
                raise ValueError("static installed source mapping is outside closure")
            covered.update(node.files)
        if not covered <= entries.keys():
            raise ValueError("static installed file association is outside closure")
        required = {path for path, item in entries.items() if item.kind != "directory"}
        if not required <= covered:
            raise ValueError("static installed content requires complete file association")
        support = tuple((item.root, item.path) for item in self.support_files)
        if support != tuple(sorted(set(support))):
            raise ValueError("static support files must be sorted and unique")
        return self


class StaticRootPlacement(FrozenSchema):
    root: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    location: StaticContentPath


class StaticAnalysisLayout(FrozenSchema):
    project_root: StaticContentPath
    targets: tuple[StaticContentPath, ...] = Field(min_length=1)
    import_roots: tuple[StaticContentPath, ...]
    type_roots: tuple[StaticContentPath, ...]
    cwd: StaticContentPath
    root_placements: tuple[StaticRootPlacement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_placements(self) -> StaticAnalysisLayout:
        roots = tuple(item.root for item in self.root_placements)
        if roots != tuple(sorted(set(roots))):
            raise ValueError("static root placements must be sorted and unique")
        return self


class StaticConfigurationInput(FrozenSchema):
    content: StaticContentManifest
    effective_file: StaticContentPath
    files_in_precedence_order: tuple[StaticContentPath, ...]
    discovery_boundaries: tuple[StaticContentPath, ...]
    external_roots: tuple[StaticContentPath, ...]

    @model_validator(mode="after")
    def validate_configuration(self) -> StaticConfigurationInput:
        entries = {item.location: item for item in self.content.entries}
        if not self.files_in_precedence_order or self.files_in_precedence_order[-1] != self.effective_file:
            raise ValueError("static configuration must end with its effective file")
        for location in self.files_in_precedence_order:
            if location not in entries or entries[location].kind != "file":
                raise ValueError("static configuration file must have content facts")
        if not set((*self.discovery_boundaries, *self.external_roots)) <= entries.keys():
            raise ValueError("static configuration discovery must be closed")
        return self


class StaticEnvironmentValue(FrozenSchema):
    name: str = Field(min_length=1)
    value_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_paths: tuple[StaticContentPath, ...]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if "=" in value or "\0" in value:
            raise ValueError("static process environment name is invalid")
        return value


class StaticProcessContext(FrozenSchema):
    environment: tuple[StaticEnvironmentValue, ...]
    filesystem_case: Literal["sensitive", "insensitive"]
    environment_case: Literal["sensitive", "insensitive"]

    @model_validator(mode="after")
    def validate_environment(self) -> StaticProcessContext:
        names = tuple(item.name for item in self.environment)
        if names != tuple(sorted(set(names))):
            raise ValueError("static process environment must be sorted and unique")
        if self.environment_case == "insensitive" and len({name.upper() for name in names}) != len(names):
            raise ValueError("static process environment has case-colliding names")
        return self


class StaticSubjectCell(FrozenSchema):
    package: str
    python_minor: str
    target: str
    extra_surface: tuple[str, ...]

    @field_validator("package")
    @classmethod
    def canonical_package(cls, value: str) -> str:
        if not is_canonical_distribution_name(value):
            raise ValueError("static subject package must be canonical")
        return value

    @model_validator(mode="after")
    def validate_cell(self) -> StaticSubjectCell:
        if self.extra_surface != tuple(sorted(set(self.extra_surface))):
            raise ValueError("static subject extra surface must be sorted and unique")
        return self

    @classmethod
    def from_cell(cls, cell: Cell) -> StaticSubjectCell:
        return cls(
            package=cell.package, python_minor=cell.python_minor,
            target=cell.target, extra_surface=cell.extra_surface,
        )


class StaticSubjectInterpreter(FrozenSchema):
    implementation: Literal["cpython"]
    abi: str


class ResolutionArtifactSelected(FrozenSchema):
    kind: Literal["selected"] = "selected"
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ResolutionArtifactAvailableSet(FrozenSchema):
    kind: Literal["available-set"] = "available-set"
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolutionArtifactSourceTree(FrozenSchema):
    kind: Literal["source-tree"] = "source-tree"


ResolutionBindingArtifact = Annotated[
    Union[ResolutionArtifactSelected, ResolutionArtifactAvailableSet, ResolutionArtifactSourceTree],
    Field(discriminator="kind"),
]


class ResolutionBinding(FrozenSchema):
    name: str
    version: str | None = Field(json_schema_extra={"x-pf-preserve-null": True})
    source: SourceIdentity
    artifact: ResolutionBindingArtifact

    @model_serializer(mode="wrap")
    def serialize_required_nulls(self, handler):
        result = handler(self)
        if self.version is None:
            result["version"] = None
        return result

    @model_validator(mode="after")
    def validate_binding(self) -> ResolutionBinding:
        if not is_canonical_distribution_name(self.name):
            raise ValueError("resolution binding name must be canonical")
        if self.version is not None and str(Version(self.version)) != self.version:
            raise ValueError("resolution binding version must be normalized")
        return self


class StaticSubject(FrozenSchema):
    projection: Literal["static-subject-v2"] = "static-subject-v2"
    source_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cell: StaticSubjectCell
    interpreter: StaticSubjectInterpreter
    resolution_projection: tuple[ResolutionBinding, ...]

    @model_validator(mode="after")
    def validate_projection(self) -> StaticSubject:
        names = tuple(item.name for item in self.resolution_projection)
        if names != tuple(sorted(set(names))):
            raise ValueError("resolution projection must be sorted and unique by name")
        return self

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            b"pf:static-subject:v2\0" + canonical_identity_json(self.model_dump(mode="json"))
        ).hexdigest()

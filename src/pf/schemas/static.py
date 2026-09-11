"""Canonical, offline-verifiable inputs for static observations.

Content manifests are input facts, not compatibility or comparison evidence.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Union

from packaging.version import Version
from pydantic import Field, field_validator, model_validator, model_serializer

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.project import (
    Cell, SourceIdentity,
    is_canonical_distribution_name,
)


class StaticContentUnavailable(FrozenSchema):
    reason: Literal["static-subject-unavailable"] = "static-subject-unavailable"
    detail: Literal[
        "unreadable-content", "unsupported-file-kind", "unclosed-symlink",
        "content-changed", "invalid-layout",
        "inspection-unavailable", "installed-input-mismatch",
        "undeclared-analysis-root", "resolution-artifact-unbound",
        "configuration-context-unavailable", "configuration-unreadable",
    ]


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

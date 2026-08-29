from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.project import (
    ApplySelector,
    DependencyGroupKey,
    PyprojectIdentity,
    SourceSnapshotIdentity,
    public_relative_path,
)
from pf.schemas.report import PackageIdentity, ProjectEditResult


class AuthorizedDependencyGroupEdit(FrozenSchema):
    key: DependencyGroupKey
    replacement_requirements: tuple[str, ...]


class AuthorizedProjectEdit(FrozenSchema):
    pyproject_path: str
    expected_pyproject_identity: PyprojectIdentity
    group_edits: tuple[AuthorizedDependencyGroupEdit, ...]


class AuthorizedPackageApply(FrozenSchema):
    package: PackageIdentity
    scope: Literal["DECLARED_MATRIX", "PLATFORM_SCOPED"]
    declared_platforms: tuple[str, ...]
    selected_selectors: tuple[ApplySelector, ...]
    preserved_selectors: tuple[ApplySelector, ...]
    dependency_state: Literal["WRITABLE", "NOOP"]
    observed_cells: int
    authorized_edits: tuple[AuthorizedProjectEdit, ...]


class ApplyPresentationFacts(FrozenSchema):
    observed_cells: Annotated[int, Field(ge=0)]
    selected_selectors: tuple[ApplySelector, ...]
    preserved_selectors: tuple[ApplySelector, ...]
    source_drift_path_count: Annotated[int, Field(ge=0)] = 0
    source_drift_paths: Annotated[tuple[str, ...], Field(max_length=8)] = ()

    @field_validator("source_drift_paths")
    @classmethod
    def validate_source_drift_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        paths = tuple(public_relative_path(path) for path in value)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("source drift paths must be unique and sorted")
        return paths

    @model_validator(mode="after")
    def validate_source_drift_count(self) -> "ApplyPresentationFacts":
        if len(self.source_drift_paths) > self.source_drift_path_count:
            raise ValueError("source drift path count is smaller than shown paths")
        return self


class AuthorizedWorkspaceApply(FrozenSchema):
    mode: Literal["DEFAULT", "FORCE"]
    waivers_used: tuple[Literal["SOURCE_SNAPSHOT_DRIFT"], ...] = ()
    expected_snapshot: SourceSnapshotIdentity
    owned_pyproject_paths: tuple[str, ...]
    package_applies: tuple[AuthorizedPackageApply, ...]
    presentation_facts: ApplyPresentationFacts


class ApplyCommandResult(FrozenSchema):
    edits: tuple[ProjectEditResult, ...]
    presentation_facts: ApplyPresentationFacts

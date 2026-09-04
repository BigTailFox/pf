from __future__ import annotations

import math
import re
from typing import Literal

from packaging.utils import InvalidName, canonicalize_name
from pydantic import StrictBool, StrictInt, field_validator, model_validator

from pf.schemas.base import FrozenSchema


class AllSearchableDependencies(FrozenSchema):
    kind: Literal["all-searchable"] = "all-searchable"


class ManagedDependencies(FrozenSchema):
    kind: Literal["managed"] = "managed"
    names: tuple[str, ...]


class UnmanagedDependencies(FrozenSchema):
    kind: Literal["unmanaged"] = "unmanaged"
    names: tuple[str, ...]


DependencySelection = (
    AllSearchableDependencies | ManagedDependencies | UnmanagedDependencies
)
SchedulingLimit = Literal["auto"] | StrictInt
OptionalSchedulingLimit = Literal["auto"] | StrictInt | None


class ExtraConfig(FrozenSchema):
    policy: Literal["none", "each", "all"] = "each"
    custom_surfaces: tuple[tuple[str, ...], ...] = ()


class TargetConfig(FrozenSchema):
    python_minors: tuple[str, ...] | None = None
    platforms: tuple[str, ...] | None = None
    dependency_selection: DependencySelection = AllSearchableDependencies()
    extras: ExtraConfig = ExtraConfig()


class SearchPolicy(FrozenSchema):
    space: str = "all"
    step: Literal["major", "minor", "patch"] = "minor"
    prereleases: StrictBool = False


class DependencySearchPolicy(SearchPolicy):
    name: str


class SearchConfig(FrozenSchema):
    default: SearchPolicy = SearchPolicy()
    overrides: tuple[DependencySearchPolicy, ...] = ()


class ResolutionConfig(FrozenSchema):
    artifact: Literal["wheel", "sdist", "any"] = "wheel"
    timeout_seconds: StrictInt | None = 600

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("resolution timeout must be positive or None")
        return value


class TyConfig(FrozenSchema):
    args: tuple[str, ...] = ()
    timeout_seconds: StrictInt | None = 600

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("ty timeout must be positive or None")
        return value


class TestConfig(FrozenSchema):
    group: str = "test"
    command: tuple[str, ...] | None = None
    cwd: Literal["package", "root"] = "package"
    timeout_seconds: StrictInt | None = 1800

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("test timeout must be positive or None")
        return value


class SchedulingConfig(FrozenSchema):
    max_cells: SchedulingLimit = "auto"
    ty_jobs: SchedulingLimit = "auto"
    test_jobs: SchedulingLimit = "auto"

    @model_validator(mode="after")
    def validate_limits(self) -> "SchedulingConfig":
        for field in ("max_cells", "ty_jobs", "test_jobs"):
            value = getattr(self, field)
            if isinstance(value, bool) or (
                isinstance(value, int) and value <= 0
            ):
                raise ValueError(f"{field} must be 'auto' or a positive integer")
        return self


class EffectiveConfig(FrozenSchema):
    """Normalized configuration grouped by its product consumers."""

    target: TargetConfig = TargetConfig()
    search: SearchConfig = SearchConfig()
    resolution: ResolutionConfig = ResolutionConfig()
    ty: TyConfig = TyConfig()
    test: TestConfig = TestConfig()
    scheduling: SchedulingConfig = SchedulingConfig()


class RunLimits(FrozenSchema):
    """Resolved invocation-local limits consumed by execution modules."""

    max_cells: StrictInt
    ty_jobs: StrictInt
    test_jobs: StrictInt
    max_duration_seconds: float | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> "RunLimits":
        for field in ("max_cells", "ty_jobs", "test_jobs"):
            value = getattr(self, field)
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")
        if self.max_duration_seconds is not None and (
            isinstance(self.max_duration_seconds, bool)
            or not math.isfinite(self.max_duration_seconds)
            or self.max_duration_seconds <= 0
        ):
            raise ValueError("max duration must be a positive finite value or None")
        return self


class RootPackage(FrozenSchema):
    kind: Literal["root-package"] = "root-package"


class WorkspacePackage(FrozenSchema):
    kind: Literal["workspace-package"] = "workspace-package"
    canonical_name: str

    @field_validator("canonical_name")
    @classmethod
    def validate_canonical_name(cls, value: str) -> str:
        try:
            normalized = canonicalize_name(value, validate=True)
        except InvalidName as error:
            raise ValueError("workspace package name must be valid") from error
        if normalized != value:
            raise ValueError("workspace package name must be canonical")
        return value


TargetSelector = RootPackage | WorkspacePackage


class CheckRequest(FrozenSchema):
    root: str
    selector: TargetSelector = RootPackage()
    max_cells: OptionalSchedulingLimit = None
    ty_jobs: OptionalSchedulingLimit = None
    test_jobs: OptionalSchedulingLimit = None

    @model_validator(mode="after")
    def validate_scheduling(self) -> "CheckRequest":
        _validate_optional_scheduling(self)
        return self


class SmokeRequest(FrozenSchema):
    root: str
    selector: TargetSelector = RootPackage()
    max_cells: OptionalSchedulingLimit = None
    ty_jobs: OptionalSchedulingLimit = None
    test_jobs: OptionalSchedulingLimit = None

    @model_validator(mode="after")
    def validate_scheduling(self) -> "SmokeRequest":
        _validate_optional_scheduling(self)
        return self


class SearchRequest(FrozenSchema):
    root: str
    selector: TargetSelector = RootPackage()
    max_cells: OptionalSchedulingLimit = None
    ty_jobs: OptionalSchedulingLimit = None
    test_jobs: OptionalSchedulingLimit = None
    max_duration_seconds: float | None = None

    @model_validator(mode="after")
    def validate_scheduling(self) -> "SearchRequest":
        _validate_optional_scheduling(self)
        if self.max_duration_seconds is not None and self.max_duration_seconds <= 0:
            raise ValueError("max duration must be positive or None")
        return self


def _validate_optional_scheduling(
    request: CheckRequest | SmokeRequest | SearchRequest,
) -> None:
    for field in ("max_cells", "ty_jobs", "test_jobs"):
        value = getattr(request, field)
        if isinstance(value, bool) or (
            isinstance(value, int) and value <= 0
        ):
            raise ValueError(f"{field} must be 'auto', a positive integer, or None")


class ReportRequest(FrozenSchema):
    root: str
    selector: TargetSelector = RootPackage()


class DiagnoseRequest(FrozenSchema):
    root: str
    selector: TargetSelector = RootPackage()
    failure_id: str

    @field_validator("failure_id")
    @classmethod
    def validate_failure_id(cls, value: str) -> str:
        if re.fullmatch(r"failure-[0-9a-f]{16}", value) is None:
            raise ValueError("failure ID must be canonical failure-<16 hex>")
        return value


class ApplyRequest(FrozenSchema):
    root: str
    selector: TargetSelector = RootPackage()
    force: StrictBool = False


class MergeRequest(FrozenSchema):
    reports: tuple[str, ...]
    output: str

    @model_validator(mode="after")
    def validate_inputs(self) -> "MergeRequest":
        if not self.reports:
            raise ValueError("merge requires at least one report")
        return self

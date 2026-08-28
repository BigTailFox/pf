from __future__ import annotations

from typing import Literal

from pydantic import StrictInt, field_validator, model_validator

from pf.schemas.base import FrozenSchema


class EffectiveConfig(FrozenSchema):
    """Normalized configuration consumed by project planning modules."""

    python: tuple[str, ...] = ()
    platform: tuple[str, ...] = ()
    extras: Literal["none", "each", "all"] | None = "each"
    extra_surfaces: tuple[tuple[str, ...], ...] | None = None
    release_granularity: Literal["major", "minor", "patch"] = "minor"
    search_space: Literal["all", "current-major", "current-minor"] | tuple[str, ...] = (
        "all"
    )
    distribution: Literal["wheel", "sdist", "any"] = "wheel"
    allow_prereleases: bool = False
    managed_deps: tuple[str, ...] | None = None
    unmanaged_deps: tuple[str, ...] | None = None
    ty_args: tuple[str, ...] = ()
    test_group: str = "test"
    test_command: tuple[str, ...] = ()
    command_cwd: Literal["package", "root"] = "package"
    jobs: Literal["auto"] | StrictInt = "auto"
    resolve_timeout: int | None = 600
    ty_timeout: int | None = 600
    test_timeout: int | None = 1800

    @field_validator("python")
    @classmethod
    def validate_python_minors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        import re

        if any(re.fullmatch(r"3\.[0-9]+", minor) is None for minor in value):
            raise ValueError(
                "python entries must be CPython minor versions like '3.11'"
            )
        if tuple(sorted(set(value))) != value:
            raise ValueError("python entries must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_runtime_policy(self) -> "EffectiveConfig":
        if isinstance(self.jobs, bool) or (
            isinstance(self.jobs, int) and self.jobs <= 0
        ):
            raise ValueError("jobs must be 'auto' or a positive integer")
        return self


class CheckRequest(FrozenSchema):
    root: str
    package: str | None = None
    jobs: Literal["auto"] | StrictInt = "auto"

    @model_validator(mode="after")
    def validate_scheduling(self) -> "CheckRequest":
        if isinstance(self.jobs, bool) or (
            isinstance(self.jobs, int) and self.jobs <= 0
        ):
            raise ValueError("jobs must be 'auto' or a positive integer")
        return self


class SmokeRequest(FrozenSchema):
    root: str
    package: str | None = None
    jobs: Literal["auto"] | StrictInt = "auto"

    @model_validator(mode="after")
    def validate_scheduling(self) -> "SmokeRequest":
        if isinstance(self.jobs, bool) or (
            isinstance(self.jobs, int) and self.jobs <= 0
        ):
            raise ValueError("jobs must be 'auto' or a positive integer")
        return self


class SearchRequest(FrozenSchema):
    root: str
    package: str | None = None
    jobs: Literal["auto"] | StrictInt = "auto"
    max_duration_seconds: float | None = None

    @model_validator(mode="after")
    def validate_scheduling(self) -> "SearchRequest":
        if isinstance(self.jobs, bool) or (
            isinstance(self.jobs, int) and self.jobs <= 0
        ):
            raise ValueError("jobs must be 'auto' or a positive integer")
        if self.max_duration_seconds is not None and self.max_duration_seconds <= 0:
            raise ValueError("max duration must be positive or None")
        return self


class ReportRequest(FrozenSchema):
    root: str
    package: str | None = None


class DiagnoseRequest(FrozenSchema):
    root: str
    package: str | None = None
    failure_id: str | None = None


class ApplyRequest(FrozenSchema):
    root: str
    package: str | None = None


class MergeRequest(FrozenSchema):
    reports: tuple[str, ...]
    output: str

    @model_validator(mode="after")
    def validate_inputs(self) -> "MergeRequest":
        if not self.reports:
            raise ValueError("merge requires at least one report")
        return self

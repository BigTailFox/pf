from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from packaging.utils import InvalidName, canonicalize_name
from pydantic import ValidationError

from pf.errors import ConfigurationError
from pf.project_discovery import PyprojectObservation
from pf.search_space import defaults, parse
from pf.schemas.config import (
    AllSearchableDependencies,
    DependencySearchPolicy,
    EffectiveConfig,
    ExtraConfig,
    ManagedDependencies,
    ResolutionConfig,
    RunLimits,
    SchedulingLimit,
    SchedulingConfig,
    SearchConfig,
    SearchPolicy,
    SpaceDefaults,
    TargetConfig,
    TestConfig,
    TyConfig,
    UnmanagedDependencies,
)


_FIELDS = frozenset(
    {
        "pythons",
        "platforms",
        "managed-deps",
        "unmanaged-deps",
        "extra-policy",
        "extra-surfaces",
        "max-cells",
        "search-space",
        "search-space-defaults",
        "search-step",
        "search-prereleases",
        "resolve-artifact",
        "resolve-timeout",
        "ty-args",
        "ty-timeout",
        "ty-jobs",
        "test-group",
        "test-command",
        "test-cwd",
        "test-timeout",
        "test-jobs",
        "dep",
    }
)
_DEP_FIELDS = frozenset(
    {"name", "search-space", "search-space-defaults", "search-step", "search-prereleases"}
)
_SEARCH_STEPS = frozenset({"major", "minor", "patch"})


def parse_scheduling_limit(
    value: str,
    *,
    field: str,
) -> Literal["auto"] | int:
    if value == "auto":
        return "auto"
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise ConfigurationError(f"{field} must be 'auto' or a positive integer")
    return int(value)


def parse_max_duration(value: str | None) -> int | None:
    if value is None or value == "none":
        return None
    parsed = ConfigLoader._duration(value, field="max-duration")
    assert parsed is not None
    return parsed


def resolve_run_limits(
    scheduling: SchedulingConfig,
    *,
    max_cells: SchedulingLimit | None = None,
    ty_jobs: SchedulingLimit | None = None,
    test_jobs: SchedulingLimit | None = None,
    max_duration_seconds: float | None = None,
    logical_cpus: int | None = None,
) -> RunLimits:
    """Resolve persistent and invocation-local scheduling policy once per run."""

    cpu_count = max(1, logical_cpus if logical_cpus is not None else (os.cpu_count() or 1))
    selected_max_cells = scheduling.max_cells if max_cells is None else max_cells
    resolved_max_cells = (
        cpu_count if selected_max_cells == "auto" else selected_max_cells
    )

    def stage_limit(
        override: SchedulingLimit | None,
        persistent: SchedulingLimit,
    ) -> int:
        selected = persistent if override is None else override
        return resolved_max_cells if selected == "auto" else selected

    return RunLimits(
        max_cells=resolved_max_cells,
        ty_jobs=stage_limit(ty_jobs, scheduling.ty_jobs),
        test_jobs=stage_limit(test_jobs, scheduling.test_jobs),
        max_duration_seconds=max_duration_seconds,
    )


class ConfigLoader:
    """Own raw PF validation and the root-to-member effective configuration."""

    def load(
        self,
        *,
        root_observation: PyprojectObservation,
        target_observation: PyprojectObservation,
    ) -> EffectiveConfig:
        root = self._config_layer(
            root_observation.document,
            location="[tool.pf]",
        )
        layers = [root]
        if target_observation.path != root_observation.path:
            layers.append(
                self._config_layer(
                    target_observation.document,
                    location="package [tool.pf]",
                )
            )

        merged: dict[str, Any] = {}
        for layer in layers:
            if "managed-deps" in layer or "unmanaged-deps" in layer:
                merged.pop("managed-deps", None)
                merged.pop("unmanaged-deps", None)
            merged.update(layer)

        default_space = merged.get("search-space")
        default_step = merged.get("search-step", "minor")
        default_policy = SearchPolicy(
            space=default_space,
            space_defaults=merged.get("search-space-defaults", SpaceDefaults()),
            step=default_step,
            prereleases=merged.get("search-prereleases", False),
        )

        overrides: list[DependencySearchPolicy] = []
        for raw in merged.get("dep", ()):
            space = (
                self._dependency_space(raw["search-space"])
                if "search-space" in raw
                else default_policy.space
            )
            step = raw.get("search-step", default_policy.step)
            overrides.append(
                DependencySearchPolicy(
                    name=self._canonical_name(raw["name"], field="dep name"),
                    space=space,
                    space_defaults=raw.get("search-space-defaults", default_policy.space_defaults),
                    step=step,
                    prereleases=raw.get(
                        "search-prereleases",
                        default_policy.prereleases,
                    ),
                )
            )

        test_command = (
            tuple(merged["test-command"]) if "test-command" in merged else ("pytest",)
        )
        if test_command[:2] == ("uv", "run"):
            raise ConfigurationError("test-command cannot start with 'uv run'")
        overrides.sort(key=lambda item: item.name)

        try:
            return EffectiveConfig(
                target=TargetConfig(
                    python_minors=(
                        tuple(merged["pythons"]) if "pythons" in merged else None
                    ),
                    platforms=(
                        tuple(merged["platforms"]) if "platforms" in merged else None
                    ),
                    dependency_selection=self._dependency_selection(merged),
                    extras=ExtraConfig(
                        policy=merged.get("extra-policy", "each"),
                        custom_surfaces=self._extra_surfaces(
                            merged.get("extra-surfaces", ())
                        ),
                    ),
                ),
                search=SearchConfig(
                    default=default_policy,
                    overrides=tuple(overrides),
                ),
                resolution=ResolutionConfig(
                    artifact=merged.get("resolve-artifact", "any"),
                    timeout_seconds=self._duration(
                        merged.get("resolve-timeout", "10m"),
                        field="resolve-timeout",
                    ),
                ),
                ty=TyConfig(
                    args=tuple(merged.get("ty-args", ())),
                    timeout_seconds=self._duration(
                        merged.get("ty-timeout", "10m"),
                        field="ty-timeout",
                    ),
                ),
                test=TestConfig(
                    group=merged.get("test-group", "test"),
                    command=test_command,
                    cwd=merged.get("test-cwd", "package"),
                    timeout_seconds=self._duration(
                        merged.get("test-timeout", "30m"),
                        field="test-timeout",
                    ),
                ),
                scheduling=SchedulingConfig(
                    max_cells=merged.get("max-cells", "auto"),
                    ty_jobs=merged.get("ty-jobs", "auto"),
                    test_jobs=merged.get("test-jobs", "auto"),
                ),
            )
        except ValidationError as error:
            raise ConfigurationError(str(error)) from error

    def _config_layer(
        self,
        document: Mapping[str, Any],
        *,
        location: str,
    ) -> dict[str, Any]:
        tool = document.get("tool", {})
        if not isinstance(tool, Mapping):
            raise ConfigurationError("[tool] metadata must be a table")
        layer = tool.get("pf", {})
        if not isinstance(layer, Mapping):
            raise ConfigurationError(f"{location} metadata must be a table")
        unknown = sorted(set(layer) - _FIELDS)
        if unknown:
            raise ConfigurationError(f"unknown {location} key: {unknown[0]}")
        if "managed-deps" in layer and "unmanaged-deps" in layer:
            raise ConfigurationError(
                "managed-deps and unmanaged-deps are mutually exclusive"
            )

        normalized = dict(layer)
        if "pythons" in layer:
            pythons = self._string_list(layer["pythons"], field="pythons")
            if not pythons:
                raise ConfigurationError("pythons must be non-empty")
            if tuple(sorted(set(pythons))) != pythons:
                raise ConfigurationError("pythons must be sorted and unique")
            if any(re.fullmatch(r"3\.[0-9]+", value) is None for value in pythons):
                raise ConfigurationError(
                    "pythons entries must be CPython minor versions like '3.11'"
                )
            normalized["pythons"] = pythons
        if "platforms" in layer:
            platforms = self._string_list(layer["platforms"], field="platforms")
            if not platforms:
                raise ConfigurationError("platforms must be non-empty")
            if tuple(sorted(set(platforms))) != platforms:
                raise ConfigurationError("platforms must be sorted and unique")
            normalized["platforms"] = platforms
        for field in ("managed-deps", "unmanaged-deps"):
            if field in layer:
                normalized[field] = self._dependency_names(
                    layer[field],
                    field=field,
                )
        if "extra-policy" in layer:
            self._literal(
                layer["extra-policy"],
                field="extra-policy",
                allowed={"none", "each", "all"},
            )
        if "extra-surfaces" in layer:
            self._validate_extra_surfaces(layer["extra-surfaces"])
        if "search-space" in layer:
            normalized["search-space"] = parse(layer["search-space"]).canonical
        if "search-space-defaults" in layer:
            normalized["search-space-defaults"] = self._space_defaults(layer["search-space-defaults"])
        if "search-step" in layer:
            self._literal(
                layer["search-step"],
                field="search-step",
                allowed=_SEARCH_STEPS,
            )
        if "search-prereleases" in layer:
            self._boolean(layer["search-prereleases"], field="search-prereleases")
        if "resolve-artifact" in layer:
            self._literal(
                layer["resolve-artifact"],
                field="resolve-artifact",
                allowed={"wheel", "sdist", "any"},
            )
        for field in ("resolve-timeout", "ty-timeout", "test-timeout"):
            if field in layer:
                self._duration(layer[field], field=field)
        if "ty-args" in layer:
            normalized["ty-args"] = self._string_list(
                layer["ty-args"],
                field="ty-args",
            )
        if "test-group" in layer:
            value = layer["test-group"]
            if not isinstance(value, str) or not value:
                raise ConfigurationError("test-group must be a non-empty string")
        if "test-command" in layer:
            command = self._string_list(layer["test-command"], field="test-command")
            if not command or any(not item for item in command):
                raise ConfigurationError("test-command must be non-empty")
            normalized["test-command"] = command
        if "test-cwd" in layer:
            self._literal(
                layer["test-cwd"],
                field="test-cwd",
                allowed={"package", "root"},
            )
        for field in ("max-cells", "ty-jobs", "test-jobs"):
            if field in layer:
                self._scheduling_value(layer[field], field=field)
        if "dep" in layer:
            normalized["dep"] = self._dependency_entries(
                layer["dep"],
                location=location,
            )
        return normalized

    @classmethod
    def _dependency_entries(
        cls,
        value: Any,
        *,
        location: str,
    ) -> tuple[dict[str, Any], ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ConfigurationError(f"{location}.dep must be an array of tables")
        entries = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, Mapping):
                raise ConfigurationError(f"{location}.dep entries must be tables")
            unknown = sorted(set(raw) - _DEP_FIELDS)
            if unknown:
                raise ConfigurationError(f"unknown {location}.dep key: {unknown[0]}")
            if "name" not in raw:
                raise ConfigurationError(f"{location}.dep entry requires name")
            name = cls._canonical_name(raw["name"], field="dep name")
            if name in seen:
                raise ConfigurationError(f"duplicate dep dependency: {name}")
            seen.add(name)
            entry = dict(raw)
            entry["name"] = name
            if "search-space" in raw:
                entry["search-space"] = cls._dependency_space(raw["search-space"])
            if "search-space-defaults" in raw:
                entry["search-space-defaults"] = cls._space_defaults(raw["search-space-defaults"])
            if "search-step" in raw:
                cls._literal(
                    raw["search-step"],
                    field="dep search-step",
                    allowed=_SEARCH_STEPS,
                )
            if "search-prereleases" in raw:
                cls._boolean(
                    raw["search-prereleases"],
                    field="dep search-prereleases",
                )
            entries.append(entry)
        return tuple(entries)

    @staticmethod
    def _dependency_selection(
        merged: Mapping[str, Any],
    ) -> AllSearchableDependencies | ManagedDependencies | UnmanagedDependencies:
        if "managed-deps" in merged:
            return ManagedDependencies(names=tuple(merged["managed-deps"]))
        if "unmanaged-deps" in merged:
            return UnmanagedDependencies(names=tuple(merged["unmanaged-deps"]))
        return AllSearchableDependencies()

    @classmethod
    def _dependency_names(cls, value: Any, *, field: str) -> tuple[str, ...]:
        names = cls._string_list(value, field=field)
        normalized = []
        seen: set[str] = set()
        for raw in names:
            name = cls._canonical_name(raw, field=field)
            if name in seen:
                raise ConfigurationError(f"duplicate {field} dependency: {name}")
            seen.add(name)
            normalized.append(name)
        return tuple(sorted(normalized))

    @staticmethod
    def _canonical_name(value: Any, *, field: str) -> str:
        if not isinstance(value, str):
            raise ConfigurationError(f"{field} must be a distribution name")
        try:
            return canonicalize_name(value, validate=True)
        except InvalidName as error:
            raise ConfigurationError(f"invalid {field}: {value}") from error

    @staticmethod
    def _string_list(value: Any, *, field: str) -> tuple[str, ...]:
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or any(not isinstance(item, str) for item in value)
        ):
            raise ConfigurationError(f"{field} must be an array of strings")
        return tuple(value)

    @classmethod
    def _validate_extra_surfaces(cls, value: Any) -> None:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ConfigurationError("extra-surfaces must be an array of arrays")
        for surface in value:
            cls._string_list(surface, field="extra-surfaces entry")

    @classmethod
    def _extra_surfaces(cls, value: Any) -> tuple[tuple[str, ...], ...]:
        cls._validate_extra_surfaces(value)
        surfaces = {tuple(sorted(set(surface))) for surface in value}
        return tuple(sorted(surfaces, key=lambda surface: (len(surface), surface)))

    @staticmethod
    def _literal(value: Any, *, field: str, allowed: set[str] | frozenset[str]) -> None:
        if not isinstance(value, str) or value not in allowed:
            raise ConfigurationError(f"invalid {field}: {value}")

    @staticmethod
    def _boolean(value: Any, *, field: str) -> None:
        if not isinstance(value, bool):
            raise ConfigurationError(f"{field} must be a boolean")

    @staticmethod
    def _scheduling_value(value: Any, *, field: str) -> None:
        if value == "auto":
            return
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigurationError(
                f"{field} must be 'auto' or a valid integer greater than zero"
            )

    @staticmethod
    def _duration(value: Any, *, field: str) -> int | None:
        if value == "none":
            return None
        if not isinstance(value, str):
            raise ConfigurationError(f"{field} must be a duration or 'none'")
        match = re.fullmatch(r"([1-9][0-9]*)(s|m|h)", value)
        if match is None:
            raise ConfigurationError(f"invalid {field}: {value}")
        amount, unit = match.groups()
        multiplier = {"s": 1, "m": 60, "h": 3600}[unit]
        return int(amount) * multiplier

    @staticmethod
    def _dependency_space(value: Any) -> str:
        try:
            return parse(value, allow_specifier=True).canonical
        except ConfigurationError as error:
            raise ConfigurationError(f"invalid dep search-space: {value}") from error

    @staticmethod
    def _space_defaults(value: Any) -> SpaceDefaults:
        if not isinstance(value, Mapping) or set(value) != {"with-lower-bound", "without-lower-bound"}:
            raise ConfigurationError("search-space-defaults requires exactly with-lower-bound and without-lower-bound")
        parsed = defaults(value["with-lower-bound"], value["without-lower-bound"])
        return SpaceDefaults(
            with_lower_bound=parsed.with_lower_bound.canonical,
            without_lower_bound=parsed.without_lower_bound.canonical,
        )

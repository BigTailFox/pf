from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import tomli

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from pydantic import ValidationError

from pf.errors import ConfigurationError
from pf.schemas.config import EffectiveConfig


_PACKAGE_FIELDS = frozenset(
    {
        "python",
        "platform",
        "extras",
        "extra-surfaces",
        "release-granularity",
        "search-space",
        "distribution",
        "allow-prereleases",
        "managed-deps",
        "unmanaged-deps",
        "ty-args",
        "test-group",
        "test-command",
        "command-cwd",
        "jobs",
        "resolve-timeout",
        "ty-timeout",
        "test-timeout",
    }
)
_ROOT_FIELDS = _PACKAGE_FIELDS | {"package"}
_OVERRIDE_FIELDS = _PACKAGE_FIELDS


def parse_jobs(value: str) -> Literal["auto"] | int:
    if value == "auto":
        return "auto"
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise ConfigurationError("jobs must be 'auto' or a positive integer")
    return int(value)


def parse_max_duration(value: str | None) -> int | None:
    if value is None or value == "none":
        return None
    parsed = ConfigLoader._duration(value, field="max-duration")
    assert parsed is not None
    return parsed


class ConfigLoader:
    """Merge PF configuration layers into one immutable effective config."""

    def load(self, *, root: Path, package: Path) -> EffectiveConfig:
        root_document = self._read(root / "pyproject.toml")
        package_document = (
            root_document
            if package.resolve() == root.resolve()
            else self._read(package / "pyproject.toml")
        )
        package_name = canonicalize_name(package_document["project"]["name"])

        root_config = root_document.get("tool", {}).get("pf", {})
        for field in ("packages", "exclude-packages"):
            if field in root_config:
                raise ConfigurationError(
                    f"[tool.pf].{field} is no longer supported; "
                    "select one target with --package PACKAGE"
                )
        self._validate_layer(root_config, location="[tool.pf]", allowed=_ROOT_FIELDS)
        package_overrides = root_config.get("package", {})
        matching_override: dict[str, Any] = {}
        for name, patch in package_overrides.items():
            if isinstance(patch, dict) and "path" in patch:
                raise ConfigurationError(
                    f"[tool.pf.package.{name}].path is no longer supported; "
                    "workspace discovery owns package paths and --package selects the target"
                )
            self._validate_layer(
                patch,
                location=f"[tool.pf.package.{name}]",
                allowed=_OVERRIDE_FIELDS,
            )
            if canonicalize_name(name) == package_name:
                matching_override = patch

        package_config = package_document.get("tool", {}).get("pf", {})
        if package.resolve() != root.resolve():
            self._validate_layer(
                package_config,
                location="package [tool.pf]",
                allowed=_PACKAGE_FIELDS,
            )
        merged: dict[str, Any] = {}
        for layer in (root_config, matching_override, package_config):
            for key in _PACKAGE_FIELDS:
                if key in layer:
                    merged[key] = layer[key]

        if "managed-deps" in merged and "unmanaged-deps" in merged:
            raise ConfigurationError(
                "managed-deps and unmanaged-deps are mutually exclusive"
            )
        if "extras" in merged and "extra-surfaces" in merged:
            raise ConfigurationError("extras and extra-surfaces are mutually exclusive")
        scalar_space = merged.get("search-space", "all")
        granularity = merged.get("release-granularity", "minor")
        if scalar_space == "current-major" and granularity == "major":
            raise ConfigurationError(
                "current-major search-space requires minor or patch granularity"
            )
        if scalar_space == "current-minor" and granularity != "patch":
            raise ConfigurationError(
                "current-minor search-space requires patch granularity"
            )

        test_command = tuple(merged.get("test-command", ()))
        if test_command[:2] == ("uv", "run"):
            raise ConfigurationError("test-command cannot start with 'uv run'")

        try:
            return EffectiveConfig(
                python=tuple(sorted(set(merged.get("python", ())))),
                platform=tuple(merged.get("platform", ())),
                extras=(
                    None if "extra-surfaces" in merged else merged.get("extras", "each")
                ),
                extra_surfaces=(
                    self._extra_surfaces(merged["extra-surfaces"])
                    if "extra-surfaces" in merged
                    else None
                ),
                release_granularity=merged.get("release-granularity", "minor"),
                search_space=self._search_space(merged.get("search-space", "all")),
                distribution=merged.get("distribution", "wheel"),
                allow_prereleases=merged.get("allow-prereleases", False),
                managed_deps=self._dependency_names(merged.get("managed-deps")),
                unmanaged_deps=self._dependency_names(merged.get("unmanaged-deps")),
                ty_args=tuple(merged.get("ty-args", ())),
                test_group=merged.get("test-group", "test"),
                test_command=test_command,
                command_cwd=merged.get("command-cwd", "package"),
                jobs=merged.get("jobs", "auto"),
                resolve_timeout=self._duration(
                    merged.get("resolve-timeout", "10m"),
                    field="resolve-timeout",
                ),
                ty_timeout=self._duration(
                    merged.get("ty-timeout", "10m"),
                    field="ty-timeout",
                ),
                test_timeout=self._duration(
                    merged.get("test-timeout", "30m"),
                    field="test-timeout",
                ),
            )
        except ValidationError as error:
            raise ConfigurationError(str(error)) from error

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        with path.open("rb") as stream:
            return tomli.load(stream)

    @staticmethod
    def _validate_layer(
        layer: dict[str, Any],
        *,
        location: str,
        allowed: frozenset[str],
    ) -> None:
        unknown = sorted(set(layer) - allowed)
        if unknown:
            raise ConfigurationError(f"unknown {location} key: {unknown[0]}")

    @staticmethod
    def _dependency_names(value: Any) -> tuple[str, ...] | None:
        if value is None:
            return None
        return tuple(sorted({canonicalize_name(name) for name in value}))

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
    def _search_space(
        value: Any,
    ) -> Literal["all", "current-major", "current-minor"] | tuple[str, ...]:
        if isinstance(value, str):
            if value not in {"all", "current-major", "current-minor"}:
                raise ConfigurationError(f"invalid search-space: {value}")
            return value
        normalized: dict[str, str] = {}
        for text in value:
            try:
                requirement = Requirement(text)
            except InvalidRequirement as error:
                raise ConfigurationError(
                    f"invalid search-space entry: {text}"
                ) from error
            if (
                requirement.extras
                or requirement.marker is not None
                or requirement.url is not None
                or not requirement.specifier
            ):
                raise ConfigurationError(
                    f"search-space entry must contain only a name and specifier: {text}"
                )
            name = canonicalize_name(requirement.name)
            if name in normalized:
                raise ConfigurationError(f"duplicate search-space dependency: {name}")
            normalized[name] = f"{name}{requirement.specifier}"
        return tuple(normalized[name] for name in sorted(normalized))

    @staticmethod
    def _extra_surfaces(value: Any) -> tuple[tuple[str, ...], ...]:
        surfaces = {tuple(sorted(set(surface))) for surface in value}
        return tuple(sorted(surfaces))

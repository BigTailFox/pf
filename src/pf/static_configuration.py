"""Freeze snapshot ty configuration without consulting host user files."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import os
import re
from typing import Literal

import tomli
import tomlkit

from pf.schemas.policy import (
    SnapshotTyConfig, SnapshotTyConfigMaterialized, SnapshotTyConfigUnavailable,
)


@dataclass(frozen=True)
class TyConfigurationFile:
    role: Literal["project", "explicit"]
    path: Path
    content: str
    format: Literal["pyproject", "ty"]


@dataclass(frozen=True)
class TyConfigurationResolution:
    files: tuple[TyConfigurationFile, ...]
    effective_toml: str
    analysis_root: Path
    queried_paths: tuple[Path, ...]


@dataclass(frozen=True)
class TyConfigurationUnavailable:
    reason: Literal["static-subject-unavailable"] = "static-subject-unavailable"
    detail: Literal[
        "configuration-context-unavailable",
        "configuration-unreadable",
        "undeclared-analysis-root",
    ] = "configuration-unreadable"


@dataclass(frozen=True)
class TyConfigurationMaterialization:
    directory: Path
    effective_file: Path
    digest: str
    content: bytes


def ty_config_digest(effective_config_bytes: bytes) -> str:
    return hashlib.sha256(b"pf:ty-config:v2\0" + effective_config_bytes).hexdigest()


def snapshot_ty_config_from_resolution(
    resolution: TyConfigurationResolution | TyConfigurationUnavailable,
    *,
    directory: Path,
) -> tuple[SnapshotTyConfig, TyConfigurationMaterialization | None]:
    if isinstance(resolution, TyConfigurationUnavailable):
        return SnapshotTyConfigUnavailable(reason=resolution.detail), None
    materialized = materialize_ty_configuration(resolution, directory=directory)
    if isinstance(materialized, TyConfigurationUnavailable):
        return SnapshotTyConfigUnavailable(reason=materialized.detail), None
    return SnapshotTyConfigMaterialized(digest=materialized.digest), materialized


def materialize_ty_configuration(
    resolution: TyConfigurationResolution, *, directory: Path,
) -> TyConfigurationMaterialization | TyConfigurationUnavailable:
    """Write one effective TOML. Digest hashes the exact --config-file bytes."""
    try:
        directory.mkdir()
        effective = directory / "effective.ty.toml"
        content = resolution.effective_toml.encode("utf-8")
        effective.write_bytes(content)
        written = effective.read_bytes()
        if written != content:
            return TyConfigurationUnavailable()
        return TyConfigurationMaterialization(
            directory, effective, ty_config_digest(written), written,
        )
    except OSError:
        return TyConfigurationUnavailable()


class TyConfigurationResolver:
    """Resolve snapshot-only configuration. Host user files are fail-closed."""

    def resolve(
        self, *, project_directory: Path, environment: Mapping[str, str],
        platform: Literal["posix", "windows"],
        snapshot_root: Path | None = None,
    ) -> TyConfigurationResolution | TyConfigurationUnavailable:
        queried: list[Path] = []
        try:
            if not project_directory.is_absolute():
                return TyConfigurationUnavailable(detail="configuration-context-unavailable")
            root = snapshot_root if snapshot_root is not None else project_directory
            if not root.is_absolute():
                return TyConfigurationUnavailable(detail="configuration-context-unavailable")
            try:
                project_directory.resolve().relative_to(root.resolve())
            except ValueError:
                return TyConfigurationUnavailable(detail="undeclared-analysis-root")
            host = self._host_user_config(environment, platform)
            if host is None:
                return TyConfigurationUnavailable(detail="configuration-context-unavailable")
            queried.append(host)
            if self._exists(host):
                return TyConfigurationUnavailable(detail="undeclared-analysis-root")
            if "TY_CONFIG_FILE" in environment:
                path = self._expand(environment["TY_CONFIG_FILE"], environment)
                if not path.is_absolute():
                    path = project_directory / path
                queried.append(path)
                if not self._inside(path, root):
                    return TyConfigurationUnavailable(detail="undeclared-analysis-root")
                file, settings = self._read(path, "explicit", "ty")
                return TyConfigurationResolution(
                    (file,), tomlkit.dumps(settings or {}), project_directory, tuple(queried),
                )
            files: list[TyConfigurationFile] = []
            effective: dict = {}
            analysis_root = project_directory
            for directory in (project_directory, *project_directory.parents):
                if not self._inside(directory, root):
                    return TyConfigurationUnavailable(detail="undeclared-analysis-root")
                ty_path = directory / "ty.toml"
                queried.append(ty_path)
                if self._exists(ty_path):
                    file, settings = self._read(ty_path, "project", "ty")
                else:
                    path = directory / "pyproject.toml"
                    queried.append(path)
                    if not self._exists(path):
                        if directory == root:
                            break
                        continue
                    file, settings = self._read(path, "project", "pyproject")
                    if settings is None:
                        if directory == root:
                            break
                        continue
                files.append(file)
                effective = self._merge(effective, settings or {})
                analysis_root = directory
                break
            return TyConfigurationResolution(
                tuple(files), tomlkit.dumps(effective), analysis_root, tuple(queried),
            )
        except (OSError, UnicodeError, ValueError, TypeError):
            return TyConfigurationUnavailable()

    @staticmethod
    def _host_user_config(
        environment: Mapping[str, str], platform: Literal["posix", "windows"],
    ) -> Path | None:
        if platform == "windows":
            base = environment.get("APPDATA")
        else:
            base = environment.get("XDG_CONFIG_HOME")
            if base is None and environment.get("HOME"):
                base = str(Path(environment["HOME"]) / ".config")
        if not base or not Path(base).is_absolute():
            return None
        return Path(base) / "ty" / "ty.toml"

    @staticmethod
    def _inside(path: Path, snapshot_root: Path) -> bool:
        try:
            path.resolve().relative_to(snapshot_root.resolve())
        except ValueError:
            return False
        return True

    @staticmethod
    def _exists(path: Path) -> bool:
        try:
            path.stat()
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _read(path, role, format):
        with path.open(encoding="utf-8") as stream:
            before = os.fstat(stream.fileno())
            content = stream.read()
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise ValueError("configuration changed during capture")
        document = tomli.loads(content)
        if format == "pyproject":
            tool = document.get("tool", {})
            if not isinstance(tool, dict):
                raise ValueError("pyproject tool configuration must be a table")
            document = tool.get("ty")
        if document is not None and not isinstance(document, dict):
            raise ValueError("ty configuration must be a table")
        return TyConfigurationFile(role, path, content, format), document

    @classmethod
    def _merge(cls, lower: dict, higher: dict) -> dict:
        result = deepcopy(lower)
        for key, value in higher.items():
            previous = result.get(key)
            if isinstance(value, dict) and isinstance(previous, dict):
                result[key] = cls._merge(previous, value)
            elif isinstance(value, list) and isinstance(previous, list):
                result[key] = previous + deepcopy(value)
            else:
                result[key] = deepcopy(value)
        return result

    @staticmethod
    def _expand(value: str, environment: Mapping[str, str]) -> Path:
        if value == "~" or value.startswith("~/"):
            home = environment.get("HOME")
            if home is None:
                raise ValueError("configuration tilde has no fixed HOME")
            value = home + value[1:]
        elif value.startswith("~"):
            raise ValueError("named-user expansion is outside the fixed environment")

        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            if name not in environment:
                raise ValueError("configuration expansion has an unbound variable")
            return environment[name]

        return Path(re.sub(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)", replace, value))

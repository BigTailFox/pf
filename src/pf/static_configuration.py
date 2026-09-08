"""Freeze ty configuration selection without consulting an implicit environment."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import os
import re
from typing import Literal

import tomli
import tomlkit

from pf.schemas.static import StaticContentManifest, StaticContentPath, StaticContentUnavailable
from pf.static_subject import StaticContentCollector


@dataclass(frozen=True)
class TyConfigurationFile:
    role: Literal["user", "project", "explicit"]
    path: Path
    content: str
    format: Literal["pyproject", "ty"]


@dataclass(frozen=True)
class TyConfigurationResolution:
    files: tuple[TyConfigurationFile, ...]
    effective_toml: str
    analysis_root: Path
    # Record every actual presence/absence query. Later materialization uses the
    # captured text, so original host files are not inputs to the ty subprocess.
    queried_paths: tuple[Path, ...]


@dataclass(frozen=True)
class TyConfigurationUnavailable:
    reason: Literal["static-subject-unavailable"] = "static-subject-unavailable"
    detail: Literal["configuration-context-unavailable", "configuration-unreadable"] = "configuration-unreadable"


@dataclass(frozen=True)
class TyConfigurationMaterialization:
    directory: Path
    effective_file: StaticContentPath
    files_in_precedence_order: tuple[StaticContentPath, ...]
    content: StaticContentManifest


def materialize_ty_configuration(
    resolution: TyConfigurationResolution, *, directory: Path,
) -> TyConfigurationMaterialization | TyConfigurationUnavailable:
    """Write captured inputs into a new caller-owned directory.

    No original configuration is reopened. The caller retains and cleans the
    directory with its prepared environment, including partial writes on error.
    """
    try:
        directory.mkdir()
        files = []
        for index, file in enumerate(resolution.files):
            name = f"input-{index:03d}-{file.role}.toml"
            (directory / name).write_text(file.content, encoding="utf-8")
            files.append(StaticContentPath(root="configuration", path=name))
        effective = StaticContentPath(root="configuration", path="effective.ty.toml")
        (directory / effective.path).write_text(resolution.effective_toml, encoding="utf-8")
        content = StaticContentCollector().collect({"configuration": directory})
        if isinstance(content, StaticContentUnavailable):
            return TyConfigurationUnavailable()
        return TyConfigurationMaterialization(directory, effective, tuple(files), content)
    except OSError:
        return TyConfigurationUnavailable()


class TyConfigurationResolver:
    """Resolve explicit, project and user configuration in documented precedence."""

    def resolve(
        self, *, project_directory: Path, environment: Mapping[str, str],
        platform: Literal["posix", "windows"],
    ) -> TyConfigurationResolution | TyConfigurationUnavailable:
        queried: list[Path] = []
        try:
            if not project_directory.is_absolute():
                return TyConfigurationUnavailable(detail="configuration-context-unavailable")
            if "TY_CONFIG_FILE" in environment:
                path = self._expand(environment["TY_CONFIG_FILE"], environment)
                if not path.is_absolute():
                    path = project_directory / path
                queried.append(path)
                file, settings = self._read(path, "explicit", "ty")
                return TyConfigurationResolution((file,), tomlkit.dumps(settings), project_directory, tuple(queried))
            if platform == "windows":
                base = environment.get("APPDATA")
            else:
                base = environment.get("XDG_CONFIG_HOME")
                if base is None and environment.get("HOME"):
                    base = str(Path(environment["HOME"]) / ".config")
            if not base or not Path(base).is_absolute():
                return TyConfigurationUnavailable(detail="configuration-context-unavailable")
            user_path = Path(base) / "ty" / "ty.toml"
            files: list[TyConfigurationFile] = []
            effective: dict = {}
            analysis_root = project_directory
            queried.append(user_path)
            if self._exists(user_path):
                file, effective = self._read(user_path, "user", "ty")
                files.append(file)
            for directory in (project_directory, *project_directory.parents):
                ty_path = directory / "ty.toml"
                queried.append(ty_path)
                if self._exists(ty_path):
                    file, settings = self._read(ty_path, "project", "ty")
                else:
                    path = directory / "pyproject.toml"
                    queried.append(path)
                    if not self._exists(path):
                        continue
                    file, settings = self._read(path, "project", "pyproject")
                    if settings is None:
                        continue
                files.append(file)
                effective = self._merge(effective, settings)
                analysis_root = directory
                break
            return TyConfigurationResolution(tuple(files), tomlkit.dumps(effective), analysis_root, tuple(queried))
        except (OSError, UnicodeError, ValueError, TypeError):
            return TyConfigurationUnavailable()

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
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
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

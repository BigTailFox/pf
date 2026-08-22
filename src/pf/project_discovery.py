from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name
import tomli

from pf.errors import ConfigurationError


@dataclass(frozen=True)
class PackageLocation:
    name: str
    package_root: Path
    pyproject_path: Path
    report_path: Path


class ProjectDiscovery:
    """Locate package identities and reports without planning an environment."""

    def discover(
        self,
        *,
        root: Path,
        package_selection: str | None,
    ) -> tuple[PackageLocation, ...]:
        root = root.resolve()
        document = self._read(root / "pyproject.toml")
        candidates = self._candidate_paths(root, document)
        selected_names, excluded_names = self._configured_names(document)

        discovered: list[PackageLocation] = []
        for package_root in candidates:
            pyproject_path = package_root / "pyproject.toml"
            if not pyproject_path.is_file():
                relative = self._relative(package_root, root)
                raise ConfigurationError(
                    f"package path has no pyproject.toml: {relative}"
                )
            package_document = self._read(pyproject_path)
            project = package_document.get("project")
            if not isinstance(project, Mapping) or "name" not in project:
                continue
            raw_name = project["name"]
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ConfigurationError(
                    f"invalid project name: {self._relative(package_root, root)}"
                )
            name = canonicalize_name(raw_name)
            if selected_names is not None and name not in selected_names:
                continue
            if name in excluded_names:
                continue
            discovered.append(
                PackageLocation(
                    name=name,
                    package_root=package_root,
                    pyproject_path=pyproject_path,
                    report_path=package_root / "package-floor.json",
                )
            )

        self._validate_unique_names(discovered, root=root)
        available_names = tuple(sorted(location.name for location in discovered))
        if package_selection is not None:
            selected_name = canonicalize_name(package_selection)
            selected_path = (
                Path(package_selection).as_posix().removeprefix("./").rstrip("/")
            )
            discovered = [
                location
                for location in discovered
                if location.name == selected_name
                or self._relative(location.pyproject_path, root) == selected_path
                or self._relative(location.package_root, root) == selected_path
            ]
            if not discovered:
                raise ConfigurationError(
                    f"unknown package selection: {package_selection}",
                    candidates=available_names,
                )
        if not discovered:
            raise ConfigurationError("no installable packages selected")
        return tuple(
            sorted(
                discovered,
                key=lambda location: (
                    location.name,
                    self._relative(location.package_root, root),
                ),
            )
        )

    def _candidate_paths(
        self,
        root: Path,
        document: Mapping[str, Any],
    ) -> set[Path]:
        candidates: set[Path] = set()
        if "project" in document:
            candidates.add(root)

        tool = document.get("tool", {})
        if not isinstance(tool, Mapping):
            raise ConfigurationError("project tool metadata must be a table")
        uv = tool.get("uv", {})
        if not isinstance(uv, Mapping):
            raise ConfigurationError("tool.uv metadata must be a table")
        workspace = uv.get("workspace", {})
        if not isinstance(workspace, Mapping):
            raise ConfigurationError("tool.uv.workspace must be a table")
        excluded_paths: set[Path] = set()
        for pattern in self._string_sequence(workspace.get("exclude", ()), "workspace exclude"):
            for path in root.glob(pattern):
                excluded_paths.add(self._within_root(path.resolve(), root))
        for pattern in self._string_sequence(workspace.get("members", ()), "workspace members"):
            for path in root.glob(pattern):
                resolved = self._within_root(path.resolve(), root)
                if resolved in excluded_paths:
                    continue
                pyproject = resolved / "pyproject.toml"
                if pyproject.is_file() and "project" in self._read(pyproject):
                    candidates.add(resolved)

        pf = tool.get("pf", {})
        if not isinstance(pf, Mapping):
            raise ConfigurationError("tool.pf metadata must be a table")
        package_patches = pf.get("package", {})
        if not isinstance(package_patches, Mapping):
            raise ConfigurationError("tool.pf.package metadata must be a table")
        for patch in package_patches.values():
            if not isinstance(patch, Mapping):
                raise ConfigurationError("tool.pf.package entry must be a table")
            explicit_path = patch.get("path")
            if explicit_path is None:
                continue
            if not isinstance(explicit_path, str):
                raise ConfigurationError("package path must be a string")
            candidates.add(self._within_root((root / explicit_path).resolve(), root))
        return candidates

    def _configured_names(
        self,
        document: Mapping[str, Any],
    ) -> tuple[set[str] | None, set[str]]:
        tool = document.get("tool", {})
        pf = tool.get("pf", {}) if isinstance(tool, Mapping) else {}
        if not isinstance(pf, Mapping):
            raise ConfigurationError("tool.pf metadata must be a table")
        selected = pf.get("packages")
        selected_names = (
            {
                str(canonicalize_name(name))
                for name in self._string_sequence(selected, "tool.pf packages")
            }
            if selected is not None
            else None
        )
        excluded_names = {
            str(canonicalize_name(name))
            for name in self._string_sequence(
                pf.get("exclude-packages", ()),
                "tool.pf exclude-packages",
            )
        }
        return selected_names, excluded_names

    @staticmethod
    def _validate_unique_names(
        locations: list[PackageLocation],
        *,
        root: Path,
    ) -> None:
        by_name: dict[str, list[PackageLocation]] = {}
        for location in locations:
            by_name.setdefault(location.name, []).append(location)
        for name in sorted(by_name):
            conflicts = by_name[name]
            if len(conflicts) < 2:
                continue
            paths = ", ".join(
                sorted(ProjectDiscovery._relative(item.package_root, root) for item in conflicts)
            )
            raise ConfigurationError(
                f"duplicate canonical package name: {name} ({paths})"
            )

    @staticmethod
    def _within_root(path: Path, root: Path) -> Path:
        if path != root and root not in path.parents:
            raise ConfigurationError("package path escapes the workspace root")
        return path

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        relative = path.relative_to(root).as_posix()
        return relative or "."

    @staticmethod
    def _string_sequence(value: object, label: str) -> tuple[str, ...]:
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise ConfigurationError(f"{label} must be an array of strings")
        if not all(isinstance(item, str) for item in value):
            raise ConfigurationError(f"{label} must be an array of strings")
        return tuple(item for item in value if isinstance(item, str))

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        with path.open("rb") as stream:
            return tomli.load(stream)

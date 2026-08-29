from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name
import tomli

from pf.errors import ConfigurationError
from pf.schemas.config import RootPackage, TargetSelector, WorkspacePackage


@dataclass(frozen=True)
class PackageLocation:
    name: str
    package_root: Path
    pyproject_path: Path
    report_path: Path


class ProjectDiscovery:
    """Locate package identities and reports without planning an environment."""

    def owned_pyproject_paths(self, *, root: Path) -> tuple[str, ...]:
        """Return root, workspace, and recursive in-tree path metadata."""
        root = root.resolve()
        root_pyproject = root / "pyproject.toml"
        root_document = self._read(root_pyproject)
        pending = {
            root_pyproject,
            *(
                path / "pyproject.toml"
                for path in self._candidate_paths(root, root_document)
            ),
        }
        observed: set[Path] = set()
        while pending:
            pyproject = pending.pop().resolve()
            self._within_root(pyproject, root)
            if pyproject in observed:
                continue
            if not pyproject.is_file():
                raise ConfigurationError(
                    "owned package path has no pyproject.toml: "
                    f"{self._relative(pyproject, root)}"
                )
            observed.add(pyproject)
            document = self._read(pyproject)
            tool = document.get("tool", {})
            uv = tool.get("uv", {}) if isinstance(tool, Mapping) else {}
            sources = uv.get("sources", {}) if isinstance(uv, Mapping) else {}
            if not isinstance(sources, Mapping):
                raise ConfigurationError("tool.uv.sources must be a table")
            for value in sources.values():
                if not isinstance(value, Mapping) or "path" not in value:
                    continue
                raw_path = value["path"]
                if not isinstance(raw_path, str):
                    raise ConfigurationError("uv source path must be a string")
                package_path = (pyproject.parent / raw_path).resolve()
                self._within_root(package_path, root)
                candidate = (
                    package_path
                    if package_path.name == "pyproject.toml"
                    else package_path / "pyproject.toml"
                )
                if candidate.is_file():
                    pending.add(candidate)
        return tuple(
            sorted(path.relative_to(root).as_posix() for path in observed)
        )

    def select(
        self,
        *,
        root: Path,
        selector: TargetSelector,
    ) -> PackageLocation:
        root = root.resolve()
        document = self._read(root / "pyproject.toml")
        self._validate_obsolete_selection(document)
        candidates = self._candidate_paths(root, document)

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
        if isinstance(selector, RootPackage):
            selected = next(
                (location for location in discovered if location.package_root == root),
                None,
            )
            if selected is None:
                raise ConfigurationError(
                    "workspace root has no installable [project]; "
                    "select one workspace package with --package PACKAGE",
                    candidates=available_names,
                )
            return selected
        if not isinstance(selector, WorkspacePackage):
            raise TypeError("unsupported target selector")
        selected = next(
            (
                location
                for location in discovered
                if location.name == selector.canonical_name
            ),
            None,
        )
        if selected is None:
            raise ConfigurationError(
                f"unknown package selection: {selector.canonical_name}",
                candidates=available_names,
            )
        return selected

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

        return candidates

    @staticmethod
    def _validate_obsolete_selection(document: Mapping[str, Any]) -> None:
        tool = document.get("tool", {})
        pf = tool.get("pf", {}) if isinstance(tool, Mapping) else {}
        if not isinstance(pf, Mapping):
            raise ConfigurationError("tool.pf metadata must be a table")
        for field in ("packages", "exclude-packages"):
            if field in pf:
                raise ConfigurationError(
                    f"[tool.pf].{field} is no longer supported; "
                    "select one target with --package PACKAGE"
                )
        package_patches = pf.get("package", {})
        if not isinstance(package_patches, Mapping):
            raise ConfigurationError("tool.pf.package metadata must be a table")
        for name, patch in package_patches.items():
            if not isinstance(patch, Mapping):
                raise ConfigurationError("tool.pf.package entry must be a table")
            if "path" in patch:
                raise ConfigurationError(
                    f"[tool.pf.package.{name}].path is no longer supported; "
                    "workspace discovery owns package paths and --package selects the target"
                )

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

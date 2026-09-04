from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
import tomli

from pf.errors import ConfigurationError
from pf.schemas.config import RootPackage, TargetSelector, WorkspacePackage
from pf.schemas.project import (
    DynamicWorkspaceMemberVersion,
    StaticWorkspaceMemberVersion,
    WorkspaceMemberVersion,
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class PackageLocation:
    name: str
    package_root: Path
    pyproject_path: Path
    report_path: Path


@dataclass(frozen=True)
class PyprojectObservation:
    path: Path
    document: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.resolve())
        object.__setattr__(self, "document", _freeze(self.document))


@dataclass(frozen=True)
class WorkspaceMemberFact:
    name: str
    locator: str
    version: WorkspaceMemberVersion


@dataclass(frozen=True)
class WorkspaceInventory:
    target: PackageLocation
    root_observation: PyprojectObservation
    target_observation: PyprojectObservation
    owned_pyproject_paths: tuple[str, ...]
    _members: Mapping[str, WorkspaceMemberFact] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_members",
            MappingProxyType(dict(sorted(self._members.items()))),
        )

    def workspace_member_for(self, canonical_name: str) -> WorkspaceMemberFact | None:
        if canonicalize_name(canonical_name) != canonical_name:
            raise ValueError("workspace member lookup requires a canonical name")
        return self._members.get(canonical_name)


@dataclass(frozen=True)
class _PackageCatalog:
    root: Path
    root_observation: PyprojectObservation
    target: PackageLocation
    locations: tuple[PackageLocation, ...]
    observations: Mapping[Path, PyprojectObservation] = field(repr=False)


class ProjectDiscovery:
    """Locate package identities and build one immutable planning inventory."""

    def select(
        self,
        *,
        root: Path,
        selector: TargetSelector,
    ) -> PackageLocation:
        return self._catalog(root=root, selector=selector).target

    def inventory(
        self,
        *,
        root: Path,
        selector: TargetSelector,
    ) -> WorkspaceInventory:
        catalog = self._catalog(root=root, selector=selector)
        observations = dict(catalog.observations)
        members = {
            location.name: self._member_fact(
                root=catalog.root,
                location=location,
                observation=observations[location.pyproject_path],
            )
            for location in catalog.locations
        }
        owned = self._owned_observations(
            root=catalog.root,
            initial_paths={
                catalog.root_observation.path,
                *(location.pyproject_path for location in catalog.locations),
            },
            observations=observations,
        )
        target_observation = observations[catalog.target.pyproject_path]
        return WorkspaceInventory(
            target=catalog.target,
            root_observation=catalog.root_observation,
            target_observation=target_observation,
            owned_pyproject_paths=tuple(
                sorted(path.relative_to(catalog.root).as_posix() for path in owned)
            ),
            _members=members,
        )

    def _catalog(
        self,
        *,
        root: Path,
        selector: TargetSelector,
    ) -> _PackageCatalog:
        root = root.resolve()
        root_pyproject = root / "pyproject.toml"
        root_observation = self._observe(root_pyproject)
        observations = {root_pyproject: root_observation}
        document = root_observation.document
        members, excludes = self._workspace_patterns(document)

        excluded_paths: set[Path] = set()
        for pattern in excludes:
            for path in root.glob(pattern):
                excluded_paths.add(self._within_root(path.resolve(), root))
        candidate_paths = {root}
        for pattern in members:
            for path in root.glob(pattern):
                resolved = self._within_root(path.resolve(), root)
                if resolved in excluded_paths:
                    continue
                pyproject = resolved / "pyproject.toml"
                if pyproject.is_file():
                    candidate_paths.add(resolved)

        discovered: list[PackageLocation] = []
        for package_root in sorted(candidate_paths):
            pyproject_path = package_root / "pyproject.toml"
            observation = observations.get(pyproject_path)
            if observation is None:
                observation = self._observe(pyproject_path)
                observations[pyproject_path] = observation
            project = observation.document.get("project")
            if not isinstance(project, Mapping) or "name" not in project:
                continue
            raw_name = project["name"]
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ConfigurationError(
                    f"invalid project name: {self._relative(package_root, root)}"
                )
            discovered.append(
                PackageLocation(
                    name=canonicalize_name(raw_name),
                    package_root=package_root,
                    pyproject_path=pyproject_path,
                    report_path=package_root / "package-floor.json",
                )
            )

        self._validate_unique_names(discovered, root=root)
        target = self._select(
            locations=discovered,
            root=root,
            selector=selector,
        )
        return _PackageCatalog(
            root=root,
            root_observation=root_observation,
            target=target,
            locations=tuple(discovered),
            observations=MappingProxyType(observations),
        )

    @staticmethod
    def _select(
        *,
        locations: list[PackageLocation],
        root: Path,
        selector: TargetSelector,
    ) -> PackageLocation:
        available_names = tuple(sorted(location.name for location in locations))
        if isinstance(selector, RootPackage):
            selected = next(
                (location for location in locations if location.package_root == root),
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
                for location in locations
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

    @staticmethod
    def _member_fact(
        *,
        root: Path,
        location: PackageLocation,
        observation: PyprojectObservation,
    ) -> WorkspaceMemberFact:
        project = observation.document["project"]
        assert isinstance(project, Mapping)
        raw_dynamic = project.get("dynamic", ())
        if isinstance(raw_dynamic, str) or not isinstance(raw_dynamic, Sequence) or any(
            not isinstance(field_name, str) for field_name in raw_dynamic
        ):
            raise ConfigurationError(
                f"invalid project.dynamic for workspace package: {location.name}"
            )
        if "version" in project:
            raw_version = project["version"]
            if not isinstance(raw_version, str):
                raise ConfigurationError(
                    f"invalid workspace member version: {location.name}"
                )
            try:
                version: WorkspaceMemberVersion = StaticWorkspaceMemberVersion(
                    value=str(Version(raw_version))
                )
            except InvalidVersion as error:
                raise ConfigurationError(
                    f"invalid workspace member version: {location.name}"
                ) from error
        elif "version" in raw_dynamic:
            version = DynamicWorkspaceMemberVersion()
        else:
            raise ConfigurationError(
                "workspace package must declare project.version or dynamic version: "
                f"{location.name}"
            )
        return WorkspaceMemberFact(
            name=location.name,
            locator=location.package_root.relative_to(root).as_posix(),
            version=version,
        )

    def _owned_observations(
        self,
        *,
        root: Path,
        initial_paths: set[Path],
        observations: dict[Path, PyprojectObservation],
    ) -> set[Path]:
        pending = set(initial_paths)
        owned: set[Path] = set()
        while pending:
            pyproject = pending.pop().resolve()
            self._within_root(pyproject, root)
            if pyproject in owned:
                continue
            observation = observations.get(pyproject)
            if observation is None:
                if not pyproject.is_file():
                    continue
                observation = self._observe(pyproject)
                observations[pyproject] = observation
            owned.add(pyproject)
            for package_path in self._path_sources(observation.document):
                resolved = (pyproject.parent / package_path).resolve()
                self._within_root(resolved, root)
                candidate = (
                    resolved
                    if resolved.name == "pyproject.toml"
                    else resolved / "pyproject.toml"
                )
                if candidate.is_file():
                    pending.add(candidate)
        return owned

    @staticmethod
    def _path_sources(document: Mapping[str, Any]) -> tuple[str, ...]:
        tool = document.get("tool", {})
        uv = tool.get("uv", {}) if isinstance(tool, Mapping) else {}
        sources = uv.get("sources", {}) if isinstance(uv, Mapping) else {}
        if not isinstance(sources, Mapping):
            raise ConfigurationError("tool.uv.sources must be a table")
        paths: list[str] = []
        for value in sources.values():
            if not isinstance(value, Mapping) or "path" not in value:
                continue
            raw_path = value["path"]
            if not isinstance(raw_path, str):
                raise ConfigurationError("uv source path must be a string")
            paths.append(raw_path)
        return tuple(paths)

    @staticmethod
    def _workspace_patterns(
        document: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        tool = document.get("tool", {})
        if not isinstance(tool, Mapping):
            raise ConfigurationError("project tool metadata must be a table")
        uv = tool.get("uv", {})
        if not isinstance(uv, Mapping):
            raise ConfigurationError("tool.uv metadata must be a table")
        workspace = uv.get("workspace", {})
        if not isinstance(workspace, Mapping):
            raise ConfigurationError("tool.uv.workspace must be a table")
        return (
            ProjectDiscovery._string_sequence(
                workspace.get("members", ()), "workspace members"
            ),
            ProjectDiscovery._string_sequence(
                workspace.get("exclude", ()), "workspace exclude"
            ),
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
                sorted(
                    ProjectDiscovery._relative(item.package_root, root)
                    for item in conflicts
                )
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
    def _observe(path: Path) -> PyprojectObservation:
        with path.open("rb") as stream:
            document = tomli.load(stream)
        return PyprojectObservation(path=path, document=document)

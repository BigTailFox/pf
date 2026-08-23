from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Literal, Protocol, cast
from urllib.parse import parse_qs, urlsplit

import tomli

from packaging.requirements import InvalidRequirement, Requirement
from packaging.markers import Marker, default_environment
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from pf.config import ConfigLoader
from pf.errors import ConfigurationError
from pf.project_discovery import ProjectDiscovery
from pf.schemas.config import EffectiveConfig
from pf.schemas.project import (
    Cell,
    HarnessGroupProvenance,
    HarnessRequirement,
    HarnessSpecifierClause,
    PackagePlan,
    ProjectPlan,
    RequirementDeclaration,
    SourceIdentity,
    SourcePlan,
    public_locator,
)


_MARKER_VARIABLES = frozenset(
    {
        "python_version",
        "python_full_version",
        "os_name",
        "sys_platform",
        "platform_release",
        "platform_system",
        "platform_version",
        "platform_machine",
        "platform_python_implementation",
        "implementation_name",
        "implementation_version",
        "extra",
        "dependency_groups",
    }
)
_PROJECTABLE_MARKER_VARIABLES = frozenset(
    {"python_version", "sys_platform", "platform_machine"}
)


@dataclass(frozen=True)
class _ExpandedGroupRequirement:
    raw: str
    group_path: tuple[str, ...]
    item_path: tuple[int, ...]


def host_target() -> str:
    import platform
    import sys

    raw_machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(
        raw_machine,
        raw_machine,
    )
    if sys.platform.startswith("linux"):
        libc = "musl" if platform.libc_ver()[0].lower() == "musl" else "gnu"
        return f"{machine}-unknown-linux-{libc}"
    if sys.platform == "darwin":
        return f"{machine}-apple-darwin"
    if sys.platform == "win32":
        return f"{machine}-pc-windows-msvc"
    raise ConfigurationError(f"unsupported host platform: {sys.platform}")


def marker_platform(target: str) -> dict[str, str]:
    architecture = target.split("-", 1)[0]
    if "-linux-" in target:
        return {"sys_platform": "linux", "platform_machine": architecture}
    if "-apple-darwin" in target:
        machine = "arm64" if architecture == "aarch64" else architecture
        return {"sys_platform": "darwin", "platform_machine": machine}
    if "-windows-" in target:
        machine = {
            "x86_64": "AMD64",
            "aarch64": "ARM64",
        }.get(architecture, architecture)
        return {"sys_platform": "win32", "platform_machine": machine}
    raise ConfigurationError(f"unsupported target platform: {target}")


def marker_applies(marker: str | None, cell: Cell) -> bool:
    """Evaluate one supported declaration marker for a frozen target cell."""
    if marker is None:
        return True
    environment = default_environment()
    environment["python_version"] = cell.python_minor
    platform_values = marker_platform(cell.target)
    environment["sys_platform"] = platform_values["sys_platform"]
    environment["platform_machine"] = platform_values["platform_machine"]
    parsed = Marker(marker)
    extras = ("", *cell.extra_surface)
    return any(parsed.evaluate({**environment, "extra": extra}) for extra in extras)


class PythonMinorProvider(Protocol):
    def available_cpython_minors(self, *, root: Path) -> tuple[str, ...]: ...


class ProjectLoader:
    """Turn a project or workspace into an immutable search plan."""

    def __init__(
        self,
        config_loader: ConfigLoader | None = None,
        *,
        pythons: PythonMinorProvider | None = None,
        discovery: ProjectDiscovery | None = None,
    ) -> None:
        self._config_loader = config_loader or ConfigLoader()
        self._pythons = pythons
        self._discovery = discovery or ProjectDiscovery()

    def load(
        self,
        *,
        root: Path,
        package_selection: str | None,
    ) -> ProjectPlan:
        root = root.resolve()
        locations = self._discovery.discover(
            root=root,
            package_selection=package_selection,
        )
        packages = tuple(
            self._load_package(root=root, package_path=location.package_root)
            for location in locations
        )
        if any(
            package.name != location.name
            for package, location in zip(packages, locations, strict=True)
        ):
            raise ConfigurationError("package identity changed during project loading")
        return ProjectPlan(packages=packages)

    def _load_package(self, *, root: Path, package_path: Path) -> PackagePlan:
        document = self._read(package_path / "pyproject.toml")
        project = document.get("project")
        if not isinstance(project, dict) or "name" not in project:
            raise ConfigurationError(f"not an installable package: {package_path}")
        dynamic = set(project.get("dynamic", ()))
        unsupported_dynamic = dynamic & {"dependencies", "optional-dependencies"}
        if unsupported_dynamic:
            field = sorted(unsupported_dynamic)[0]
            raise ConfigurationError(f"dynamic project.{field} is not supported")

        package_name = canonicalize_name(project["name"])
        config = self._config_loader.load(root=root, package=package_path)
        pyproject_path = (package_path / "pyproject.toml").relative_to(root).as_posix()
        root_document = self._read(root / "pyproject.toml")
        workspace_paths = self._workspace_paths(
            root=root,
            root_document=root_document,
        )
        registry = self._default_registry(
            root_document=root_document,
            package_document=document,
        )
        sources = self._sources(
            root=root,
            package_path=package_path,
            root_document=root_document,
            package_document=document,
            workspace_paths=workspace_paths,
        )
        declarations: list[RequirementDeclaration] = []
        for raw in project.get("dependencies", ()):
            requirement_name = self._requirement_name(raw)
            declarations.append(
                self._declaration(
                    package=package_name,
                    pyproject_path=pyproject_path,
                    location="base",
                    extra=None,
                    raw=raw,
                    source=sources.get(requirement_name, registry),
                    config=config,
                )
            )
        optional = project.get("optional-dependencies", {})
        for extra in sorted(optional):
            for raw in optional[extra]:
                requirement_name = self._requirement_name(raw)
                declarations.append(
                    self._declaration(
                        package=package_name,
                        pyproject_path=pyproject_path,
                        location="optional",
                        extra=extra,
                        raw=raw,
                        source=sources.get(requirement_name, registry),
                        config=config,
                    )
                )

        if config.managed_deps is not None:
            fixed_names = {
                declaration.name
                for declaration in declarations
                if declaration.kind == "fixed"
            }
            explicitly_fixed = sorted(set(config.managed_deps) & fixed_names)
            if explicitly_fixed:
                raise ConfigurationError(
                    f"fixed dependency cannot be managed: {explicitly_fixed[0]}"
                )
        for declaration in declarations:
            if not declaration.managed or declaration.marker is None:
                continue
            marker_without_strings = re.sub(
                r"(['\"]).*?\1",
                "",
                declaration.marker,
            )
            variables = (
                set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", marker_without_strings))
                & _MARKER_VARIABLES
            )
            unsupported = sorted(variables - _PROJECTABLE_MARKER_VARIABLES)
            if unsupported:
                raise ConfigurationError(
                    "unsupported managed marker dimension: " + unsupported[0]
                )

        surfaces = self._extra_surfaces(tuple(sorted(optional)), config)
        targets = config.platform or (host_target(),)
        requires_python = project.get("requires-python")
        python_minors = config.python or self._python_minors(
            requires_python,
            root=root,
        )
        if config.python and requires_python:
            try:
                required = SpecifierSet(requires_python)
            except InvalidSpecifier as error:
                raise ConfigurationError(
                    f"invalid project.requires-python: {requires_python}"
                ) from error
            unsupported = tuple(
                minor
                for minor in config.python
                if Version(f"{minor}.0") not in required
            )
            if unsupported:
                raise ConfigurationError(
                    f"configured Python {unsupported[0]} violates requires-python"
                )
        cells = tuple(
            Cell(
                package=package_name,
                target=target,
                python_minor=python_minor,
                extra_surface=surface,
            )
            for target in sorted(set(targets))
            for python_minor in sorted(set(python_minors))
            for surface in surfaces
        )
        self._validate_declaration_overlap(tuple(declarations), cells)
        cells = tuple(
            Cell.model_validate(
                {
                    **cell.model_dump(mode="python"),
                    "active_declaration_ids": tuple(
                        sorted(
                            declaration.declaration_id
                            for declaration in declarations
                            if self._declaration_active(declaration, cell)
                        )
                    ),
                }
            )
            for cell in cells
        )
        root_groups = root_document.get("dependency-groups", {})
        package_groups = document.get("dependency-groups", {})
        group_name = config.test_group
        test_group_present = group_name in root_groups or group_name in package_groups
        expanded_harness = (
            *(
                ("root", root / "pyproject.toml", item)
                for item in self._expand_group(root_groups, group_name)
            ),
            *(
                ()
                if package_path == root
                else (
                    ("package", package_path / "pyproject.toml", item)
                    for item in self._expand_group(package_groups, group_name)
                )
            ),
        )
        harness_requirements = tuple(
            self._harness_requirement(
                package=package_name,
                root=root,
                owner=owner,
                pyproject_path=group_pyproject,
                expanded=item,
                sources=sources,
                registry=registry,
                allow_prereleases=config.allow_prereleases,
            )
            for owner, group_pyproject, item in expanded_harness
        )
        return PackagePlan(
            name=package_name,
            pyproject_path=pyproject_path,
            config=config,
            declarations=tuple(declarations),
            cells=cells,
            source_plan=SourcePlan(
                identities=tuple(
                    sorted(
                        {
                            *(declaration.source for declaration in declarations),
                            *(
                                requirement.source
                                for requirement in harness_requirements
                            ),
                        },
                        key=lambda item: item.model_dump_json(),
                    )
                )
            ),
            harness_requirements=harness_requirements,
            test_group_present=test_group_present,
        )

    @staticmethod
    def _harness_requirement(
        *,
        package: str,
        root: Path,
        owner: Literal["root", "package"],
        pyproject_path: Path,
        expanded: _ExpandedGroupRequirement,
        sources: dict[str, SourceIdentity],
        registry: SourceIdentity,
        allow_prereleases: bool,
    ) -> HarnessRequirement:
        raw = expanded.raw
        try:
            requirement = Requirement(raw)
        except InvalidRequirement as error:
            raise ConfigurationError(
                f"invalid harness dependency declaration: {raw}"
            ) from error
        name = canonicalize_name(requirement.name)
        source = sources.get(name, registry)
        if requirement.url is not None:
            parsed_url = urlsplit(requirement.url)
            hashes = parse_qs(parsed_url.fragment).get("sha256", ())
            if (
                parsed_url.scheme != "https"
                or parsed_url.username is not None
                or parsed_url.password is not None
                or len(hashes) != 1
                or re.fullmatch(r"[0-9a-fA-F]{64}", hashes[0]) is None
            ):
                raise ConfigurationError(
                    f"direct URL harness dependency requires an HTTPS sha256 hash: {name}"
                )
            source = SourceIdentity(
                kind="url",
                locator=ProjectLoader._public_url(requirement.url),
                content_hash=f"sha256:{hashes[0].lower()}",
            )
        specifier = tuple(
            sorted(
                (
                    HarnessSpecifierClause(
                        operator=cast(
                            Literal["~=", "==", "!=", "<=", ">=", "<", ">", "==="],
                            item.operator,
                        ),
                        version=item.version,
                    )
                    for item in requirement.specifier
                ),
                key=lambda item: (item.operator, item.version),
            )
        )
        provenance = HarnessGroupProvenance(
            owner=owner,
            pyproject_path=pyproject_path.relative_to(root).as_posix(),
            group_path=expanded.group_path,
            item_path=expanded.item_path,
        )
        marker = str(requirement.marker) if requirement.marker else None
        extras = tuple(sorted(requirement.extras))
        declaration_id = HarnessRequirement.identity_digest(
            package=package,
            provenance=provenance,
            name=name,
            requested_extras=extras,
            specifier=specifier,
            marker=marker,
            source=source,
            original_text=raw,
        )
        try:
            specifier_allows_prereleases = requirement.specifier.prereleases is True
        except InvalidVersion:
            specifier_allows_prereleases = False
        return HarnessRequirement(
            declaration_id=declaration_id,
            package=package,
            provenance=provenance,
            name=name,
            requested_extras=extras,
            specifier=specifier,
            marker=marker,
            source=source,
            prerelease_allowed=(
                allow_prereleases or specifier_allows_prereleases
            ),
            original_text=raw,
        )

    @staticmethod
    def _declaration(
        *,
        package: str,
        pyproject_path: str,
        location: Literal["base", "optional"],
        extra: str | None,
        raw: str,
        source: SourceIdentity,
        config: EffectiveConfig,
    ) -> RequirementDeclaration:
        try:
            requirement = Requirement(raw)
        except InvalidRequirement as error:
            raise ConfigurationError(
                f"invalid dependency declaration: {raw}"
            ) from error
        name = canonicalize_name(requirement.name)
        if requirement.url is not None:
            parsed_url = urlsplit(requirement.url)
            hashes = parse_qs(parsed_url.fragment).get("sha256", ())
            if (
                parsed_url.scheme != "https"
                or parsed_url.username is not None
                or parsed_url.password is not None
                or len(hashes) != 1
                or re.fullmatch(r"[0-9a-fA-F]{64}", hashes[0]) is None
            ):
                raise ConfigurationError(
                    f"direct URL dependency requires an HTTPS sha256 hash: {name}"
                )
            source = SourceIdentity(
                kind="url",
                locator=ProjectLoader._public_url(requirement.url),
                content_hash=f"sha256:{hashes[0].lower()}",
            )
        fixed = (
            source.kind != "registry"
            or requirement.url is not None
            or any(
                spec.operator in {"==", "===", "~="} for spec in requirement.specifier
            )
        )
        managed_deps = config.managed_deps
        unmanaged_deps = config.unmanaged_deps
        if fixed:
            managed = False
        elif managed_deps is not None:
            managed = name in managed_deps
        elif unmanaged_deps is not None:
            managed = name not in unmanaged_deps
        else:
            managed = True
        identity = {
            "pyproject_path": pyproject_path,
            "location": location,
            "extra": extra,
            "name": name,
            "requested_extras": sorted(requirement.extras),
            "marker": str(requirement.marker) if requirement.marker else None,
            "source": source.model_dump(mode="json"),
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return RequirementDeclaration(
            declaration_id=digest,
            package=package,
            location=location,
            extra=extra,
            name=name,
            requested_extras=tuple(sorted(requirement.extras)),
            specifier=str(requirement.specifier),
            marker=str(requirement.marker) if requirement.marker else None,
            source=source,
            pyproject_path=pyproject_path,
            raw=raw,
            kind="fixed" if fixed else "searchable",
            managed=managed,
        )

    @staticmethod
    def _requirement_name(raw: str) -> str:
        try:
            return canonicalize_name(Requirement(raw).name)
        except InvalidRequirement as error:
            raise ConfigurationError(
                f"invalid dependency declaration: {raw}"
            ) from error

    @staticmethod
    def _sources(
        *,
        root: Path,
        package_path: Path,
        root_document: dict[str, Any],
        package_document: dict[str, Any],
        workspace_paths: dict[str, str],
    ) -> dict[str, SourceIdentity]:
        root_sources = root_document.get("tool", {}).get("uv", {}).get("sources", {})
        package_sources = (
            {}
            if package_path == root
            else package_document.get("tool", {}).get("uv", {}).get("sources", {})
        )
        index_urls: dict[str, str] = {}
        for document in (root_document, package_document):
            raw_indexes = document.get("tool", {}).get("uv", {}).get("index", ())
            if isinstance(raw_indexes, dict):
                raw_indexes = (raw_indexes,)
            for raw_index in raw_indexes:
                if not isinstance(raw_index, dict):
                    raise ConfigurationError("invalid uv index declaration")
                name = raw_index.get("name")
                url = raw_index.get("url")
                if not isinstance(name, str) or not isinstance(url, str):
                    raise ConfigurationError("uv index requires name and url")
                if raw_index.get("format", "simple") != "simple":
                    raise ConfigurationError(
                        f"unsupported uv index format: {raw_index.get('format')}"
                    )
                public_url = ProjectLoader._public_url(url)
                previous = index_urls.get(name)
                if previous is not None and previous != public_url:
                    raise ConfigurationError(f"ambiguous uv index: {name}")
                index_urls[name] = public_url
        result: dict[str, SourceIdentity] = {}
        for source_table, declaration_root in (
            (root_sources, root),
            (package_sources, package_path),
        ):
            for raw_name, value in source_table.items():
                name = canonicalize_name(raw_name)
                identity = ProjectLoader._source_identity(
                    name=name,
                    value=value,
                    root=root,
                    declaration_root=declaration_root,
                    index_urls=index_urls,
                    workspace_paths=workspace_paths,
                )
                previous = result.get(name)
                if previous is not None and previous != identity:
                    raise ConfigurationError(
                        f"ambiguous uv source for dependency: {name}"
                    )
                result[name] = identity
        return result

    @staticmethod
    def _default_registry(
        *,
        root_document: dict[str, Any],
        package_document: dict[str, Any],
    ) -> SourceIdentity:
        indexes: dict[str, tuple[str, bool, bool]] = {}
        for document in (root_document, package_document):
            raw_indexes = document.get("tool", {}).get("uv", {}).get("index", ())
            if isinstance(raw_indexes, dict):
                raw_indexes = (raw_indexes,)
            for raw_index in raw_indexes:
                if not isinstance(raw_index, dict):
                    raise ConfigurationError("invalid uv index declaration")
                name = raw_index.get("name")
                url = raw_index.get("url")
                if not isinstance(name, str) or not isinstance(url, str):
                    raise ConfigurationError("uv index requires name and url")
                if raw_index.get("format", "simple") != "simple":
                    raise ConfigurationError(
                        f"unsupported uv index format: {raw_index.get('format')}"
                    )
                record = (
                    ProjectLoader._public_url(url),
                    bool(raw_index.get("explicit", False)),
                    bool(raw_index.get("default", False)),
                )
                previous = indexes.get(name)
                if previous is not None and previous != record:
                    raise ConfigurationError(f"ambiguous uv index: {name}")
                indexes[name] = record
        non_explicit = tuple(
            (name, record) for name, record in indexes.items() if not record[1]
        )
        prioritized = tuple(item for item in non_explicit if not item[1][2])
        if prioritized:
            raise ConfigurationError(
                "unscoped first-index registry combinations are not supported"
            )
        defaults = tuple(item for item in non_explicit if item[1][2])
        if len(defaults) > 1:
            raise ConfigurationError("multiple default uv indexes are not supported")
        if defaults:
            name, (locator, _, _) = defaults[0]
            return SourceIdentity(kind="registry", index=name, locator=locator)
        return SourceIdentity(kind="registry")

    @staticmethod
    def _source_identity(
        *,
        name: str,
        value: Any,
        root: Path,
        declaration_root: Path,
        index_urls: dict[str, str],
        workspace_paths: dict[str, str],
    ) -> SourceIdentity:
        if isinstance(value, list):
            raise ConfigurationError(
                f"multiple uv sources are not supported for: {name}"
            )
        if not isinstance(value, dict):
            raise ConfigurationError(f"invalid uv source for dependency: {name}")
        if value.get("workspace") is True:
            locator = workspace_paths.get(name)
            if locator is None:
                raise ConfigurationError(
                    f"workspace source does not name a workspace package: {name}"
                )
            return SourceIdentity(kind="workspace", locator=locator)
        if "path" in value:
            resolved = (declaration_root / value["path"]).resolve()
            if root not in resolved.parents and resolved != root:
                raise ConfigurationError(f"source path escapes snapshot root: {name}")
            return SourceIdentity(
                kind="path",
                locator=resolved.relative_to(root).as_posix(),
            )
        if "git" in value:
            commit = value.get("rev")
            if (
                not isinstance(commit, str)
                or re.fullmatch(r"[0-9a-fA-F]{40,64}", commit) is None
            ):
                raise ConfigurationError(f"git source must use an exact commit: {name}")
            return SourceIdentity(
                kind="git",
                locator=ProjectLoader._public_url(value["git"]),
                commit=commit.lower(),
            )
        if "url" in value:
            content_hash = value.get("hash")
            if (
                not isinstance(content_hash, str)
                or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", content_hash) is None
            ):
                raise ConfigurationError(
                    f"URL source requires integrity information: {name}"
                )
            return SourceIdentity(
                kind="url",
                locator=ProjectLoader._public_url(value["url"]),
                content_hash=content_hash.lower(),
            )
        if "index" in value:
            index = str(value["index"])
            locator = index_urls.get(index)
            if locator is None:
                raise ConfigurationError(f"unknown uv index for dependency: {name}")
            return SourceIdentity(kind="registry", index=index, locator=locator)
        raise ConfigurationError(f"unsupported uv source for dependency: {name}")

    @staticmethod
    def _workspace_paths(
        *,
        root: Path,
        root_document: dict[str, Any],
    ) -> dict[str, str]:
        candidates = {root}
        workspace = (
            root_document.get("tool", {}).get("uv", {}).get("workspace", {})
        )
        if not isinstance(workspace, dict):
            raise ConfigurationError("tool.uv.workspace must be a table")
        members = workspace.get("members", [])
        if not isinstance(members, list) or any(
            not isinstance(pattern, str) for pattern in members
        ):
            raise ConfigurationError("workspace members must be an array of strings")
        excludes = workspace.get("exclude", [])
        if not isinstance(excludes, list) or any(
            not isinstance(pattern, str) for pattern in excludes
        ):
            raise ConfigurationError("workspace exclude must be an array of strings")
        excluded: set[Path] = set()
        for pattern in excludes:
            for path in root.glob(pattern):
                resolved = path.resolve()
                if root not in resolved.parents and resolved != root:
                    raise ConfigurationError("workspace exclude escapes project root")
                excluded.add(resolved)
        for pattern in members:
            for path in root.glob(pattern):
                resolved = path.resolve()
                if root not in resolved.parents and resolved != root:
                    raise ConfigurationError("workspace member escapes project root")
                if resolved in excluded:
                    continue
                candidates.add(resolved)
        result: dict[str, str] = {}
        for candidate in sorted(candidates):
            pyproject = candidate / "pyproject.toml"
            if not pyproject.is_file():
                continue
            project = ProjectLoader._read(pyproject).get("project")
            if not isinstance(project, dict) or not isinstance(
                project.get("name"), str
            ):
                continue
            name = canonicalize_name(project["name"])
            locator = candidate.relative_to(root).as_posix()
            previous = result.get(name)
            if previous is not None and previous != locator:
                raise ConfigurationError(
                    f"duplicate canonical workspace package name: {name}"
                )
            result[name] = locator
        return result

    @staticmethod
    def _public_url(value: Any) -> str:
        if not isinstance(value, str):
            raise ConfigurationError("source URL must be a string")
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            raise ConfigurationError("source URL must be absolute")
        return public_locator(value)

    @staticmethod
    def _extra_surfaces(
        extras: tuple[str, ...],
        config: EffectiveConfig,
    ) -> tuple[tuple[str, ...], ...]:
        if config.extra_surfaces is not None:
            surfaces = config.extra_surfaces
            if () not in surfaces:
                raise ConfigurationError(
                    "extra-surfaces must include the base surface []"
                )
            known = set(extras)
            for surface in surfaces:
                unknown = sorted(set(surface) - known)
                if unknown:
                    raise ConfigurationError(
                        f"unknown extra in extra-surfaces: {unknown[0]}"
                    )
            for extra in extras:
                if (extra,) not in surfaces:
                    raise ConfigurationError(
                        f"extra-surfaces must include each single extra: {extra}"
                    )
        elif config.extras == "none":
            surfaces = ((),)
        elif config.extras == "each":
            surfaces = ((), *((extra,) for extra in extras))
        else:
            maximal = (extras,) if len(extras) > 1 else ()
            surfaces = ((), *((extra,) for extra in extras), *maximal)
        return tuple(sorted(set(surfaces), key=lambda value: (len(value), value)))

    @staticmethod
    def _validate_declaration_overlap(
        declarations: tuple[RequirementDeclaration, ...],
        cells: tuple[Cell, ...],
    ) -> None:
        for index, left in enumerate(declarations):
            for right in declarations[index + 1 :]:
                if (
                    left.name != right.name
                    or left.location != right.location
                    or left.extra != right.extra
                ):
                    continue
                if any(
                    ProjectLoader._declaration_active(left, cell)
                    and ProjectLoader._declaration_active(right, cell)
                    for cell in cells
                ):
                    location = (
                        "base" if left.location == "base" else f"optional:{left.extra}"
                    )
                    raise ConfigurationError(
                        f"overlapping declarations for {left.name} in {location}"
                    )

    @staticmethod
    def _declaration_active(declaration: RequirementDeclaration, cell: Cell) -> bool:
        if (
            declaration.location == "optional"
            and declaration.extra not in cell.extra_surface
        ):
            return False
        return marker_applies(declaration.marker, cell)

    def _python_minors(
        self,
        requires_python: Any,
        *,
        root: Path,
    ) -> tuple[str, ...]:
        import sys

        available = (
            self._pythons.available_cpython_minors(root=root)
            if self._pythons is not None
            else (f"{sys.version_info.major}.{sys.version_info.minor}",)
        )
        try:
            specifier = SpecifierSet(requires_python or "")
        except InvalidSpecifier as error:
            raise ConfigurationError(
                f"invalid project.requires-python: {requires_python}"
            ) from error
        selected = tuple(
            sorted(
                {minor for minor in available if Version(f"{minor}.0") in specifier},
                key=Version,
            )
        )
        if not selected:
            raise ConfigurationError(
                "no available stable CPython minor satisfies requires-python"
            )
        return selected

    @staticmethod
    def _host_target() -> str:
        return host_target()

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        with path.open("rb") as stream:
            return tomli.load(stream)

    @staticmethod
    def _expand_group(
        groups: dict[str, Any],
        name: str,
        stack: tuple[str, ...] = (),
        item_stack: tuple[int, ...] = (),
    ) -> tuple[_ExpandedGroupRequirement, ...]:
        if name not in groups:
            return ()
        if name in stack:
            raise ConfigurationError(f"dependency group include cycle: {name}")
        expanded: list[_ExpandedGroupRequirement] = []
        for index, item in enumerate(groups[name]):
            if isinstance(item, str):
                expanded.append(
                    _ExpandedGroupRequirement(
                        raw=item,
                        group_path=(*stack, name),
                        item_path=(*item_stack, index),
                    )
                )
            elif isinstance(item, dict) and set(item) == {"include-group"}:
                expanded.extend(
                    ProjectLoader._expand_group(
                        groups,
                        item["include-group"],
                        (*stack, name),
                        (*item_stack, index),
                    )
                )
            else:
                raise ConfigurationError(f"unsupported dependency group item in {name}")
        return tuple(expanded)

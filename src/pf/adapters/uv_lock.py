from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
import re
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from packaging.markers import InvalidMarker, Marker, UndefinedComparison
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError
import tomli
import tomlkit
from tomlkit.exceptions import ParseError

from pf.errors import ConfigurationError
from pf.project import marker_platform
from pf.resolution import ResolutionArtifact, ResolutionPackage
from pf.schemas.project import SourceIdentity, public_locator


class UvLockError(ValueError):
    """The native uv plan cannot be projected into PF evidence."""


def normalize_uv_pylock_paths(
    content: str,
    *,
    source_root: Path,
    lock_root: Path,
) -> str:
    """Make snapshot directory sources portable across proposal replicas."""
    root = source_root.resolve()
    base = lock_root.resolve()
    if root != base and base not in root.parents:
        raise UvLockError("pylock source root must stay within its lock root")
    try:
        document = tomlkit.parse(content)
    except ParseError as error:
        raise UvLockError("uv pylock is not valid TOML") from error
    packages = document.get("packages")
    if not isinstance(packages, list):
        raise UvLockError("pylock packages must be an array")
    for package in packages:
        if not isinstance(package, MutableMapping):
            raise UvLockError("pylock package must be a table")
        directory = package.get("directory")
        if directory is None:
            continue
        if not isinstance(directory, MutableMapping):
            raise UvLockError("pylock directory requires a path")
        raw_path = directory.get("path")
        if not isinstance(raw_path, str):
            raise UvLockError("pylock directory requires a path")
        path = Path(raw_path)
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        if resolved != root and root not in resolved.parents:
            raise UvLockError("pylock directory path must stay within its root")
        directory["path"] = resolved.relative_to(base).as_posix()
    return tomlkit.dumps(document)


def parse_uv_pylock(
    content: str,
    *,
    python_version: str,
    target: str,
    source_root: Path | None = None,
    lock_root: Path | None = None,
) -> tuple[ResolutionPackage, ...]:
    environment = _marker_environment(python_version, target)
    try:
        document = tomli.loads(content)
    except tomli.TOMLDecodeError as error:
        raise UvLockError("uv pylock is not valid TOML") from error
    if document.get("lock-version") != "1.0":
        raise UvLockError("unsupported pylock version")
    if document.get("created-by") != "uv":
        raise UvLockError("pylock was not created by uv")
    requires_python = document.get("requires-python")
    if requires_python is not None:
        if not isinstance(requires_python, str):
            raise UvLockError("pylock requires-python must be a string")
        try:
            if Version(python_version) not in SpecifierSet(requires_python):
                raise UvLockError("pylock does not cover the requested Python")
        except (InvalidSpecifier, InvalidVersion) as error:
            raise UvLockError("pylock contains invalid Python compatibility") from error
    environments = document.get("environments", ())
    if environments not in ((), []):
        raise UvLockError("cell-specific pylock cannot contain environment forks")
    if any(
        document.get(key, []) != []
        for key in ("extras", "dependency-groups", "default-groups")
    ):
        raise UvLockError("cell-specific pylock cannot contain selectable groups")
    raw_packages = document.get("packages")
    if not isinstance(raw_packages, list):
        raise UvLockError("pylock packages must be an array")
    entries: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for item in raw_packages:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise UvLockError("pylock package requires a name")
        entries.append(item)
        if _marker_applies(item.get("marker"), environment):
            active.append(item)
    projected: list[ResolutionPackage] = []
    for item in active:
        requires_python = item.get("requires-python")
        if requires_python is not None:
            try:
                if not isinstance(requires_python, str) or Version(
                    python_version
                ) not in SpecifierSet(requires_python):
                    raise UvLockError(
                        "active pylock package does not cover the actual Python"
                    )
            except InvalidSpecifier as error:
                raise UvLockError(
                    "pylock package contains invalid Python compatibility"
                ) from error
        package = _package(item, source_root=source_root, lock_root=lock_root)
        dependencies: set[str] = set()
        for reference in item.get("dependencies", []):
            matches = [
                entry
                for entry in entries
                if all(
                    (canonicalize_name(entry["name"]) == canonicalize_name(value))
                    if key == "name"
                    else entry.get(key) == value
                    for key, value in reference.items()
                )
            ]
            if not matches:
                raise UvLockError("cell-specific pylock dependency graph is incomplete")
            selected = [entry for entry in matches if entry in active]
            if len(selected) > 1:
                raise UvLockError("pylock dependency reference is ambiguous")
            if selected:
                dependencies.add(canonicalize_name(selected[0]["name"]))
        projected.append(
            package.model_copy(update={"dependencies": tuple(sorted(dependencies))})
        )
    packages = tuple(sorted(projected, key=lambda package: package.name))
    names = tuple(item.name for item in packages)
    if names != tuple(sorted(set(names))):
        raise UvLockError("cell-specific pylock must select one entry per package")
    name_set = set(names)
    if any(
        dependency not in name_set
        for package in packages
        for dependency in package.dependencies
    ):
        raise UvLockError("cell-specific pylock dependency graph is incomplete")
    return packages


def _marker_environment(python_version: str, target: str) -> dict[str, str]:
    try:
        version = Version(python_version)
    except InvalidVersion as error:
        raise UvLockError(
            "pylock projection requires an actual Python version"
        ) from error
    if len(version.release) != 3:
        raise UvLockError("pylock projection requires an actual Python patch")
    try:
        platform = marker_platform(target)
    except ConfigurationError as error:
        raise UvLockError("unsupported pylock target") from error
    system = {"linux": "Linux", "darwin": "Darwin", "win32": "Windows"}[
        platform["sys_platform"]
    ]
    return {
        **platform,
        "python_version": ".".join(map(str, version.release[:2])),
        "python_full_version": str(version),
        "implementation_name": "cpython",
        "implementation_version": str(version),
        "platform_python_implementation": "CPython",
        "platform_system": system,
        "os_name": "nt" if system == "Windows" else "posix",
    }


def _marker_applies(raw: object, environment: dict[str, str]) -> bool:
    if raw is None:
        return True
    if not isinstance(raw, str):
        raise UvLockError("pylock package marker must be a string")
    try:
        marker = Marker(raw)
    except InvalidMarker as error:
        raise UvLockError("pylock package has an invalid marker") from error
    unquoted = re.sub(r"(['\"]).*?\1", "", str(marker))
    variables = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", unquoted)) - {
        "and",
        "or",
        "in",
        "not",
    }
    unsupported = variables - environment.keys()
    if unsupported:
        raise UvLockError(
            "unsupported pylock marker dimension: " + sorted(unsupported)[0]
        )
    try:
        return marker.evaluate(environment)
    except UndefinedComparison as error:
        raise UvLockError("pylock marker comparison cannot be evaluated") from error


def _package(
    raw: object,
    *,
    source_root: Path | None,
    lock_root: Path | None,
) -> ResolutionPackage:
    if not isinstance(raw, dict):
        raise UvLockError("pylock package must be a table")
    package = cast(dict[str, Any], raw)
    raw_name = package.get("name")
    if not isinstance(raw_name, str):
        raise UvLockError("pylock package requires a name")
    name = canonicalize_name(raw_name)
    raw_version = package.get("version")
    if raw_version is None:
        version = None
    elif isinstance(raw_version, str):
        try:
            version = str(Version(raw_version))
        except InvalidVersion as error:
            raise UvLockError("pylock package has an invalid version") from error
    else:
        raise UvLockError("pylock package version must be a string")
    raw_marker = package.get("marker")
    if raw_marker is None:
        marker = None
    elif isinstance(raw_marker, str):
        try:
            marker = str(Marker(raw_marker))
        except InvalidMarker as error:
            raise UvLockError("pylock package has an invalid marker") from error
    else:
        raise UvLockError("pylock package marker must be a string")
    raw_dependencies = package.get("dependencies", [])
    if not isinstance(raw_dependencies, list):
        raise UvLockError("pylock package dependencies must be an array")
    dependencies: list[str] = []
    for dependency in raw_dependencies:
        if not isinstance(dependency, dict):
            raise UvLockError("pylock dependency requires a name")
        dependency_name = dependency.get("name")
        if not isinstance(dependency_name, str):
            raise UvLockError("pylock dependency requires a name")
        dependencies.append(canonicalize_name(dependency_name))

    source_fields = tuple(
        key
        for key in ("vcs", "directory", "archive")
        if package.get(key) is not None
    )
    distribution_fields = bool(package.get("sdist")) or bool(
        package.get("wheels")
    )
    if len(source_fields) + int(distribution_fields) != 1:
        raise UvLockError("pylock package must have one install source")

    artifacts: tuple[ResolutionArtifact, ...] = ()
    selected: ResolutionArtifact | None = None
    if source_fields == ("directory",):
        source = _directory_source(
            package["directory"],
            source_root=source_root,
            lock_root=lock_root,
        )
        if version is None:
            version = _static_directory_version(
                source=source,
                source_root=source_root,
            )
    elif source_fields == ("vcs",):
        source = _vcs_source(package["vcs"])
    elif source_fields == ("archive",):
        source, selected = _archive_source(package["archive"])
        artifacts = (selected,)
    else:
        source, artifacts = _distribution_source(package)
    try:
        return ResolutionPackage(
            name=name,
            version=version,
            source=source,
            dependencies=tuple(sorted(set(dependencies))),
            marker=marker,
            available_artifacts=artifacts,
            selected_artifact=selected,
        )
    except ValidationError as error:
        raise UvLockError("pylock package evidence is invalid") from error


def _static_directory_version(
    *,
    source: SourceIdentity,
    source_root: Path | None,
) -> str | None:
    if source_root is None or source.locator is None:
        return None
    pyproject = source_root / source.locator / "pyproject.toml"
    try:
        document = tomli.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomli.TOMLDecodeError):
        return None
    project = document.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        return None
    try:
        return str(Version(project["version"]))
    except InvalidVersion:
        return None


def _directory_source(
    raw: object,
    *,
    source_root: Path | None,
    lock_root: Path | None,
) -> SourceIdentity:
    if not isinstance(raw, dict):
        raise UvLockError("pylock directory requires a path")
    directory = cast(dict[str, Any], raw)
    raw_path = directory.get("path")
    if not isinstance(raw_path, str):
        raise UvLockError("pylock directory requires a path")
    path = Path(raw_path)
    resolved: Path | None = None
    if path.is_absolute():
        resolved = path.resolve()
    elif lock_root is not None:
        resolved = (lock_root / path).resolve()
    if resolved is not None:
        if source_root is None:
            raise UvLockError("absolute pylock directory requires its source root")
        root = source_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise UvLockError("pylock directory path must stay within its root")
        locator = resolved.relative_to(root).as_posix()
    else:
        if ".." in path.parts:
            raise UvLockError("pylock directory path must stay within its root")
        locator = path.as_posix()
    return SourceIdentity(kind="path", locator=locator)


def _vcs_source(raw: object) -> SourceIdentity:
    if not isinstance(raw, dict):
        raise UvLockError("pylock VCS source must be a table")
    vcs = cast(dict[str, Any], raw)
    if vcs.get("type") != "git":
        raise UvLockError("uv profile only supports Git VCS plans")
    url = vcs.get("url")
    commit = vcs.get("commit-id")
    if not isinstance(url, str) or not isinstance(commit, str):
        raise UvLockError("pylock Git source requires URL and commit")
    return SourceIdentity(
        kind="git",
        locator=public_locator(url),
        commit=commit,
    )


def _archive_source(
    raw: object,
) -> tuple[SourceIdentity, ResolutionArtifact]:
    if not isinstance(raw, dict):
        raise UvLockError("pylock archive source must be a table")
    artifact = _artifact(cast(dict[str, Any], raw), kind="archive")
    return (
        SourceIdentity(
            kind="url",
            locator=artifact.locator,
            content_hash=artifact.content_hash,
        ),
        artifact,
    )


def _distribution_source(
    raw: dict[str, Any],
) -> tuple[SourceIdentity, tuple[ResolutionArtifact, ...]]:
    index = raw.get("index")
    if index is not None and not isinstance(index, str):
        raise UvLockError("pylock index must be a URL string")
    artifacts: list[ResolutionArtifact] = []
    sdist = raw.get("sdist")
    if sdist is not None:
        artifacts.append(_artifact(sdist, kind="sdist"))
    wheels = raw.get("wheels", [])
    if not isinstance(wheels, list):
        raise UvLockError("pylock wheels must be an array")
    artifacts.extend(_artifact(item, kind="wheel") for item in wheels)
    if not artifacts:
        raise UvLockError("registry pylock package requires an artifact")
    ordered = tuple(
        sorted(
            artifacts,
            key=lambda item: (
                item.kind,
                item.filename,
                item.locator,
                item.content_hash,
            ),
        )
    )
    return (
        SourceIdentity(
            kind="registry",
            locator=public_locator(index) if index is not None else None,
        ),
        ordered,
    )


def _artifact(
    raw: object,
    *,
    kind: Literal["wheel", "sdist", "archive"],
) -> ResolutionArtifact:
    if not isinstance(raw, dict):
        raise UvLockError("pylock artifact must be a table")
    artifact = cast(dict[str, Any], raw)
    raw_url = artifact.get("url")
    raw_path = artifact.get("path")
    if isinstance(raw_url, str):
        locator = public_locator(raw_url)
        inferred = Path(urlsplit(raw_url).path).name
    elif isinstance(raw_path, str):
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            raise UvLockError("pylock artifact path must stay within its root")
        locator = path.as_posix()
        inferred = path.name
    else:
        raise UvLockError("pylock artifact requires URL or path")
    filename = artifact.get("name", inferred)
    hashes = artifact.get("hashes")
    sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
    if not isinstance(filename, str) or not filename or not isinstance(sha256, str):
        raise UvLockError("pylock artifact requires filename and SHA-256")
    try:
        return ResolutionArtifact(
            filename=filename,
            kind=kind,
            locator=locator,
            content_hash=f"sha256:{sha256.lower()}",
        )
    except ValidationError as error:
        raise UvLockError("pylock artifact evidence is invalid") from error

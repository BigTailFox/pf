from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import fnmatch
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any

import tomli

from pf.adapters.process import ProcessRunner, read_process_output
from pf.errors import ConfigurationError
from pf.errors import InfrastructureError
from pf.schemas.base import canonical_identity_json
from pf.schemas.evaluation import ProcessSpec, ProcessTerminalUnavailable
from pf.schemas.project import (
    PyprojectIdentity,
    SnapshotEntry,
    SourceSnapshotIdentity,
    source_snapshot_digest,
)


_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".cache",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".ty_cache",
        ".tox",
        ".nox",
        ".pf",
        "package-floor.json",
        "uv.lock",
    }
)


class SourceSnapshot:
    """An immutable staged source tree and its serializable identity."""

    def __init__(
        self,
        *,
        identity: SourceSnapshotIdentity,
        temporary_directory: tempfile.TemporaryDirectory[str],
    ) -> None:
        self.identity = identity
        self._temporary_directory = temporary_directory
        self._staged_root = Path(temporary_directory.name)

    def materialize(self, destination: Path) -> None:
        """Create an independent writable proposal copy of this snapshot."""
        shutil.copytree(self._staged_root, destination, symlinks=True)

    def uv_project_configuration_identity(self, target_pyproject_path: str) -> str:
        """Identify the frozen root and target files uv receives as project config."""
        return uv_project_configuration_identity(
            self.identity,
            target_pyproject_path,
        )

    def close(self) -> None:
        self._temporary_directory.cleanup()


def uv_project_configuration_identity(
    snapshot: SourceSnapshotIdentity,
    target_pyproject_path: str,
    *,
    target_dependency_arrays_digest: str | None = None,
) -> str:
    """Identify the frozen root and target files uv receives as project config."""
    paths = tuple(sorted({"pyproject.toml", target_pyproject_path}))
    identities = {
        identity.path: identity for identity in snapshot.pyproject_identities
    }
    entries = {entry.path: entry for entry in snapshot.entries}
    inputs = []
    for path in paths:
        identity = identities.get(path)
        if identity is not None:
            document = identity.model_dump(mode="json")
            if (
                path == target_pyproject_path
                and target_dependency_arrays_digest is not None
            ):
                document["dependency_arrays_digest"] = (
                    target_dependency_arrays_digest
                )
            inputs.append(
                {"kind": "owned-pyproject", **document}
            )
            continue
        entry = entries.get(path)
        if entry is None or entry.kind != "file" or entry.content_digest is None:
            raise ConfigurationError(f"uv project configuration input is missing: {path}")
        inputs.append({"kind": "snapshot-file", **entry.model_dump(mode="json")})
    return hashlib.sha256(
        b"pf:uv-project-configuration:v1\0" + canonical_identity_json(inputs)
    ).hexdigest()


class SnapshotBuilder:
    """Own safe source traversal, identity hashing, and immutable staging."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner: ProcessRunner | None = runner

    @classmethod
    def without_processes(cls) -> "SnapshotBuilder":
        builder = cls.__new__(cls)
        builder._runner = None
        return builder

    def build(
        self,
        root: Path,
        *,
        owned_pyproject_paths: tuple[str, ...] = (),
    ) -> SourceSnapshot:
        root = root.resolve()
        owned_paths = frozenset(owned_pyproject_paths)
        if len(owned_paths) != len(owned_pyproject_paths) or any(
            Path(path).is_absolute() or ".." in Path(path).parts
            for path in owned_paths
        ):
            raise ConfigurationError("owned pyproject paths must be unique and relative")
        temporary_directory = tempfile.TemporaryDirectory(prefix="pf-source-")
        staged_root = Path(temporary_directory.name)
        entries: list[SnapshotEntry] = []
        pyproject_identities: list[PyprojectIdentity] = []
        if (root / ".git").exists() and self._runner is None:
            temporary_directory.cleanup()
            raise ConfigurationError(
                "Git source snapshots require an explicit process runner"
            )
        manifest = self._git_manifest(root) if (root / ".git").exists() else None
        ignore_patterns = () if manifest is not None else self._ignore_patterns(root)
        try:
            self._copy_directory(
                source_root=root,
                source_directory=root,
                staged_root=staged_root,
                entries=entries,
                pyproject_identities=pyproject_identities,
                owned_pyproject_paths=owned_paths,
                ignore_patterns=ignore_patterns,
                manifest=manifest,
            )
            canonical_entries = tuple(sorted(entries, key=lambda entry: entry.path))
            canonical_pyprojects = tuple(
                sorted(pyproject_identities, key=lambda identity: identity.path)
            )
            observed_owned = {identity.path for identity in canonical_pyprojects}
            if observed_owned != owned_paths:
                missing = sorted(owned_paths - observed_owned)
                raise ConfigurationError(
                    f"owned pyproject is not a regular source file: {missing[0]}"
                )
            return SourceSnapshot(
                identity=SourceSnapshotIdentity(
                    digest=source_snapshot_digest(
                        canonical_entries,
                        canonical_pyprojects,
                    ),
                    entries=canonical_entries,
                    pyproject_identities=canonical_pyprojects,
                ),
                temporary_directory=temporary_directory,
            )
        except Exception:
            temporary_directory.cleanup()
            raise

    def _copy_directory(
        self,
        *,
        source_root: Path,
        source_directory: Path,
        staged_root: Path,
        entries: list[SnapshotEntry],
        pyproject_identities: list[PyprojectIdentity],
        owned_pyproject_paths: frozenset[str],
        ignore_patterns: tuple[str, ...],
        manifest: frozenset[str] | None,
    ) -> None:
        for source in sorted(source_directory.iterdir(), key=lambda path: path.name):
            if self._excluded(
                source,
                source_root=source_root,
                ignore_patterns=ignore_patterns,
                manifest=manifest,
            ):
                continue
            relative = source.relative_to(source_root)
            destination = staged_root / relative
            metadata = source.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(source)
                target_path = Path(target)
                if target_path.is_absolute():
                    raise ConfigurationError(
                        f"absolute source symlink is unsafe: {relative}"
                    )
                resolved_target = (source.parent / target_path).resolve()
                if (
                    source_root not in resolved_target.parents
                    and resolved_target != source_root
                ):
                    raise ConfigurationError(
                        f"source symlink escapes snapshot root: {relative}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(target)
                entries.append(
                    SnapshotEntry(
                        path=relative.as_posix(),
                        kind="symlink",
                        mode=mode,
                        link_target=target,
                    )
                )
            elif stat.S_ISDIR(metadata.st_mode):
                destination.mkdir(parents=True, exist_ok=True)
                destination.chmod(mode)
                entries.append(
                    SnapshotEntry(
                        path=relative.as_posix(),
                        kind="directory",
                        mode=mode,
                    )
                )
                self._copy_directory(
                    source_root=source_root,
                    source_directory=source,
                    staged_root=staged_root,
                    entries=entries,
                    pyproject_identities=pyproject_identities,
                    owned_pyproject_paths=owned_pyproject_paths,
                    ignore_patterns=ignore_patterns,
                    manifest=manifest,
                )
            elif stat.S_ISREG(metadata.st_mode):
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination, follow_symlinks=False)
                content = destination.read_bytes()
                relative_path = relative.as_posix()
                owned = relative_path in owned_pyproject_paths
                content_digest = (
                    None if owned else hashlib.sha256(content).hexdigest()
                )
                entries.append(
                    SnapshotEntry(
                        path=relative_path,
                        kind="file",
                        mode=mode,
                        content_digest=content_digest,
                    )
                )
                if owned:
                    pyproject_identities.append(
                        self.pyproject_identity(
                            path=relative_path,
                            mode=mode,
                            content=content,
                        )
                    )
            else:
                raise ConfigurationError(f"unsupported special source file: {relative}")

    @staticmethod
    def pyproject_identity(
        *,
        path: str,
        mode: int,
        content: bytes,
    ) -> PyprojectIdentity:
        """Return the structured identity for one owned pyproject file."""
        try:
            document = tomli.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, tomli.TOMLDecodeError) as error:
            raise ConfigurationError(f"invalid owned pyproject: {path}") from error
        project = document.get("project")
        dependency_arrays: dict[str, Any] = {}
        remainder = dict(document)
        if isinstance(project, dict):
            remainder_project = dict(project)
            if "dependencies" in project:
                dependency_arrays["dependencies"] = project["dependencies"]
                remainder_project.pop("dependencies", None)
            if "optional-dependencies" in project:
                dependency_arrays["optional-dependencies"] = project[
                    "optional-dependencies"
                ]
                remainder_project.pop("optional-dependencies", None)
            remainder["project"] = remainder_project
        tagged_remainder = SnapshotBuilder._tag_toml(remainder)
        tagged_dependencies = SnapshotBuilder._tag_toml(dependency_arrays)
        return PyprojectIdentity(
            path=path,
            mode=mode,
            remainder_digest=hashlib.sha256(
                b"pf:pyproject-remainder:v1\0"
                + canonical_identity_json(tagged_remainder)
            ).hexdigest(),
            dependency_arrays_digest=hashlib.sha256(
                b"pf:pyproject-dependencies:v1\0"
                + canonical_identity_json(tagged_dependencies)
            ).hexdigest(),
        )

    @staticmethod
    def _tag_toml(value: Any) -> object:
        if isinstance(value, dict):
            return [
                "table",
                [
                    [key, SnapshotBuilder._tag_toml(value[key])]
                    for key in sorted(value)
                ],
            ]
        if isinstance(value, list):
            return ["array", [SnapshotBuilder._tag_toml(item) for item in value]]
        if isinstance(value, str):
            return ["string", value]
        if isinstance(value, bool):
            return ["bool", value]
        if isinstance(value, int):
            return ["int", str(value)]
        if isinstance(value, float):
            if math.isnan(value):
                token = "nan"
            elif math.isinf(value):
                token = "inf" if value > 0 else "-inf"
            else:
                token = value.hex()
            return ["float", token]
        if isinstance(value, datetime):
            tag = "offset-datetime" if value.tzinfo is not None else "local-datetime"
            return [tag, value.isoformat()]
        if isinstance(value, date):
            return ["local-date", value.isoformat()]
        if isinstance(value, time):
            return ["local-time", value.isoformat()]
        raise ConfigurationError(
            f"unsupported TOML identity value: {type(value).__name__}"
        )

    @staticmethod
    def _excluded(
        path: Path,
        *,
        source_root: Path,
        ignore_patterns: tuple[str, ...],
        manifest: frozenset[str] | None,
    ) -> bool:
        if path.name in _EXCLUDED_NAMES or path.name.startswith(".pf-tmp-"):
            return True
        relative = path.relative_to(source_root).as_posix()
        if manifest is not None:
            return relative not in manifest and not any(
                item.startswith(f"{relative}/") for item in manifest
            )
        ignored = False
        for raw_pattern in ignore_patterns:
            negated = raw_pattern.startswith("!")
            pattern = raw_pattern[1:] if negated else raw_pattern
            anchored = pattern.startswith("/")
            pattern = pattern.lstrip("/")
            directory_pattern = pattern.endswith("/")
            pattern = pattern.rstrip("/")
            if directory_pattern:
                matched = relative == pattern or relative.startswith(f"{pattern}/")
            elif anchored or "/" in pattern:
                matched = fnmatch.fnmatchcase(relative, pattern)
            else:
                matched = any(
                    fnmatch.fnmatchcase(part, pattern) for part in Path(relative).parts
                )
            if matched:
                ignored = not negated
        return ignored

    @staticmethod
    def _ignore_patterns(root: Path) -> tuple[str, ...]:
        path = root / ".gitignore"
        if not path.is_file():
            return ()
        return tuple(
            line
            for raw_line in path.read_text(encoding="utf-8").splitlines()
            if (line := raw_line.strip()) and not line.startswith("#")
        )

    def _git_manifest(self, root: Path) -> frozenset[str]:
        runner = self._runner
        if runner is None:
            raise ConfigurationError(
                "Git source snapshots require an explicit process runner"
            )
        result = runner.run(
            ProcessSpec(
                argv=(
                    "git",
                    "-C",
                    root.as_posix(),
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                ),
                cwd=root.as_posix(),
                timeout_seconds=30,
            )
        )
        if isinstance(result, ProcessTerminalUnavailable):
            raise InfrastructureError(
                "git could not enumerate the source snapshot",
                detail="process terminal observation was unavailable",
            )
        if (
            result.timed_out
            or result.start_error is not None
            or result.signal is not None
            or result.exit_code != 0
        ):
            raise InfrastructureError(
                "git could not enumerate the source snapshot",
                detail=result.diagnostic() or None,
            )
        if not result.stdout_complete:
            raise InfrastructureError("git source manifest was not recorded completely")
        paths = frozenset(
            path
            for path in read_process_output(runner, result).stdout.split("\0")
            if path
        )
        for path in paths:
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ConfigurationError(f"unsafe path in git source manifest: {path}")
        return paths

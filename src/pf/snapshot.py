from __future__ import annotations

import hashlib
import fnmatch
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from pf.adapters.process import ProcessRunner, read_process_output
from pf.errors import ConfigurationError
from pf.errors import InfrastructureError
from pf.schemas.evaluation import ProcessSpec
from pf.schemas.project import SnapshotEntry, SourceSnapshotIdentity


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

    def close(self) -> None:
        self._temporary_directory.cleanup()


class SnapshotBuilder:
    """Own safe source traversal, identity hashing, and immutable staging."""

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner: ProcessRunner | None = runner

    @classmethod
    def without_processes(cls) -> "SnapshotBuilder":
        builder = cls.__new__(cls)
        builder._runner = None
        return builder

    def build(self, root: Path) -> SourceSnapshot:
        root = root.resolve()
        temporary_directory = tempfile.TemporaryDirectory(prefix="pf-source-")
        staged_root = Path(temporary_directory.name)
        entries: list[SnapshotEntry] = []
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
                ignore_patterns=ignore_patterns,
                manifest=manifest,
            )
            canonical_entries = tuple(sorted(entries, key=lambda entry: entry.path))
            payload = json.dumps(
                [entry.model_dump(mode="json") for entry in canonical_entries],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            digest = hashlib.sha256(b"pf:snapshot:v1\0" + payload).hexdigest()
            return SourceSnapshot(
                identity=SourceSnapshotIdentity(
                    digest=digest,
                    entries=canonical_entries,
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
                    ignore_patterns=ignore_patterns,
                    manifest=manifest,
                )
            elif stat.S_ISREG(metadata.st_mode):
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination, follow_symlinks=False)
                content_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
                entries.append(
                    SnapshotEntry(
                        path=relative.as_posix(),
                        kind="file",
                        mode=mode,
                        content_digest=content_digest,
                    )
                )
            else:
                raise ConfigurationError(f"unsupported special source file: {relative}")

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

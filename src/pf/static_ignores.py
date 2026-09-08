"""Capture global ignore configuration and replay it from owned content.

Global discovery is replayed from owned files. Local discovery uses closed
analysis trees and explicit, revalidated absence outside those trees.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal

from pf.schemas.static import StaticContentManifest, StaticContentPath, StaticContentUnavailable
from pf.static_subject import StaticContentCollector


@dataclass(frozen=True)
class TyIgnoreFile:
    role: str
    path: Path
    content: bytes | None


@dataclass(frozen=True)
class TyGlobalIgnoreInputs:
    files: tuple[TyIgnoreFile, ...]
    patterns: bytes | None


@dataclass(frozen=True)
class TyIgnoreUnavailable:
    reason: Literal["static-subject-unavailable"] = "static-subject-unavailable"
    detail: Literal["ignore-context-unavailable"] = "ignore-context-unavailable"


@dataclass(frozen=True)
class TyGlobalIgnoreMaterialization:
    directory: Path
    environment: tuple[tuple[str, str], ...]
    content: StaticContentManifest
    inputs: tuple[StaticContentPath, ...]


@dataclass(frozen=True)
class TyIgnoreBoundaries:
    roots: tuple[str, ...]
    external_absences: tuple[Path, ...]
    external_git_directories: tuple[Path, ...]

    def revalidate(self) -> bool:
        """Guard outer discovery immediately before and after observation.

        This does not validate analysis-tree content or grant a lease on it;
        the prepared/request owner separately retains and verifies those roots.
        A failed guard invalidates the static request, not dynamic preparation.
        """
        try:
            return all(_absent(path) for path in self.external_absences) and all(
                stat.S_ISDIR(path.lstat().st_mode) for path in self.external_git_directories
            )
        except OSError:
            return False


def capture_ty_ignore_boundaries(
    *, roots: Mapping[str, Path], content: StaticContentManifest,
) -> TyIgnoreBoundaries | TyIgnoreUnavailable:
    """Admit complete owned analysis trees with an empty outer ignore context.

    Existing external ancestor ignore files or Git boundaries need additional
    immutable layout facts and are unavailable in this profile. They are never
    silently ignored or replaced with different exclusion semantics.
    """
    try:
        if not roots or any(not path.is_absolute() or not path.is_dir() for path in roots.values()):
            return TyIgnoreUnavailable()
        for name in roots:
            if content.entry_at(StaticContentPath(root=name, path=".")).kind != "directory":
                return TyIgnoreUnavailable()
        for entry in content.entries:
            if entry.location.root in roots and Path(entry.location.path).parts[-2:] == (".git", "commondir"):
                return TyIgnoreUnavailable()
            if entry.location.root not in roots or Path(entry.location.path).name != ".git":
                continue
            if content.entry_at(entry.location).kind != "directory":
                # Worktree gitdir files can redirect info/exclude outside the
                # registered tree; interpreting them is not this profile.
                return TyIgnoreUnavailable()
        queries: set[Path] = set()
        git_directories: set[Path] = set()
        for root in roots.values():
            for parent in root.parents:
                if any(parent.is_relative_to(base) for base in roots.values()):
                    continue
                for name in (".ignore", ".gitignore", ".git"):
                    path = parent / name
                    if name == ".git" and not _absent(path):
                        if not stat.S_ISDIR(path.lstat().st_mode):
                            return TyIgnoreUnavailable()
                        git_directories.add(path)
                        for relative in ("info/exclude", "commondir"):
                            outside = path / relative
                            if not _absent(outside):
                                return TyIgnoreUnavailable()
                            queries.add(outside)
                        continue
                    if not _absent(path):
                        return TyIgnoreUnavailable()
                    queries.add(path)
        return TyIgnoreBoundaries(tuple(sorted(roots)), tuple(sorted(queries)), tuple(sorted(git_directories)))
    except (OSError, ValueError, KeyError):
        return TyIgnoreUnavailable()


def materialize_ty_ignore_boundaries(
    boundaries: TyIgnoreBoundaries, *, directory: Path,
) -> StaticContentManifest | TyIgnoreUnavailable:
    """Save the admitted logical discovery profile, without host locators."""
    try:
        if not directory.is_absolute() or not boundaries.revalidate():
            return TyIgnoreUnavailable()
        directory.mkdir(mode=0o700)
        (directory / "boundaries.json").write_text(json.dumps({
            "profile": "closed-trees-empty-outer-ignore-v1", "roots": boundaries.roots,
        }, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        content = StaticContentCollector().collect({"ignore-boundaries": directory})
        return TyIgnoreUnavailable() if isinstance(content, StaticContentUnavailable) else content
    except (OSError, ValueError):
        return TyIgnoreUnavailable()


def _absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def capture_ty_global_ignores(
    *, environment: Mapping[str, str], cwd: Path,
) -> TyGlobalIgnoreInputs | TyIgnoreUnavailable:
    """Read only explicit discovery inputs, with no implicit home lookup.

    Unsupported excludesFile syntax fails closed. In particular, this does not
    substitute Git's INI semantics for ty's global-ignore discovery behavior.
    """
    try:
        if os.name != "posix":
            return TyIgnoreUnavailable()
        home = Path(environment["HOME"])
        if not cwd.is_absolute() or not home.is_absolute():
            return TyIgnoreUnavailable()
        xdg = Path(environment.get("XDG_CONFIG_HOME") or str(home / ".config"))
        if not xdg.is_absolute():
            return TyIgnoreUnavailable()
        candidates = []
        if environment.get("GIT_CONFIG_GLOBAL"):
            candidates.append(("global", Path(environment["GIT_CONFIG_GLOBAL"])))
        candidates.extend((("home", home / ".gitconfig"), ("xdg", xdg / "git" / "config")))
        candidates.append(("system", Path(environment.get("GIT_CONFIG_SYSTEM") or "/etc/gitconfig")))
        files = []
        selected = xdg / "git" / "ignore"
        for role, path in candidates:
            path = path if path.is_absolute() else cwd / path
            content = _read(path)
            files.append(TyIgnoreFile(role, path, content))
            if content is None:
                continue
            text = content.decode("utf-8")
            setting = None
            for line in text.splitlines():
                if not re.match(r"\s*excludesfile\s*=", line, re.IGNORECASE | re.ASCII):
                    continue
                value = line.partition("=")[2].strip()
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1].strip()
                # Reject ambiguous escaping, spaces, comments and expansion.
                # The first supported excludesFile line wins, as in ty.
                if not value or any(char.isspace() for char in value) or any(char in value for char in '\\"#$'):
                    return TyIgnoreUnavailable()
                if "~" in value:
                    if not value.startswith("~/") or "~" in value[1:]:
                        return TyIgnoreUnavailable()
                    value = str(home) + value[1:]
                setting = Path(value)
                break
            if setting is not None:
                selected = setting if setting.is_absolute() else cwd / setting
                break
        patterns = _read(selected)
        files.append(TyIgnoreFile("patterns", selected, patterns))
        return TyGlobalIgnoreInputs(tuple(files), patterns)
    except (KeyError, OSError, UnicodeError, ValueError):
        return TyIgnoreUnavailable()


def materialize_ty_global_ignores(
    inputs: TyGlobalIgnoreInputs, *, directory: Path,
) -> TyGlobalIgnoreMaterialization | TyIgnoreUnavailable:
    """Replay captured bytes without reopening host files.

    Caller owns the new directory, including cleanup after a partial failure.
    HOME/XDG rewriting requires other expanded paths to be resolved first.
    """
    try:
        if not directory.is_absolute():
            return TyIgnoreUnavailable()
        directory.mkdir(mode=0o700)
        home = directory / "home"
        home.mkdir()
        xdg = home / ".config"
        xdg.mkdir()
        # A concrete existing file makes global discovery terminate even when
        # the captured input had no patterns; no fallback reaches host config.
        (home / "patterns").write_bytes(inputs.patterns or b"")
        (home / ".gitconfig").write_text('[core]\nexcludesFile = ~/patterns\n', encoding="utf-8")
        refs = []
        discovery = []
        for index, file in enumerate(inputs.files):
            name = f"input-{index:03d}-{file.role}"
            discovery.append({"role": file.role, "present": file.content is not None})
            if file.content is not None:
                (directory / name).write_bytes(file.content)
                refs.append(StaticContentPath(root="global-ignores", path=name))
        (directory / "discovery.json").write_text(json.dumps(discovery, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        content = StaticContentCollector().collect({"global-ignores": directory})
        if isinstance(content, StaticContentUnavailable):
            return TyIgnoreUnavailable()
        environment = (
            ("GIT_CONFIG_GLOBAL", str(home / ".gitconfig")),
            ("GIT_CONFIG_SYSTEM", str(home / ".gitconfig")),
            ("HOME", str(home)), ("XDG_CONFIG_HOME", str(xdg)),
        )
        return TyGlobalIgnoreMaterialization(directory, environment, content, tuple(refs))
    except (OSError, ValueError):
        return TyIgnoreUnavailable()


def _read(path: Path) -> bytes | None:
    try:
        before = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("ignore input is not a regular file")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read()
        after = os.fstat(stream.fileno())
    current = path.stat()
    if any((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns) !=
           (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
           for item in (opened, after, current)):
        raise ValueError("ignore input changed during capture")
    return content

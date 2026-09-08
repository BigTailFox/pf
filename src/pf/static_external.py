"""Materialize explicitly selected external search trees into owned snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from pf.schemas.static import StaticContentManifest, StaticContentPath, StaticContentUnavailable
from pf.static_subject import StaticContentCollector


@dataclass(frozen=True)
class FrozenSearchRoots:
    roots: tuple[tuple[str, Path], ...]
    selections: tuple[StaticContentPath, ...]
    content: StaticContentManifest


def freeze_external_search_roots(
    selections: tuple[Path, ...], *, registered: Mapping[str, Path], directory: Path,
) -> FrozenSearchRoots | StaticContentUnavailable:
    """Freeze full trees without dropping files or following unknown symlinks.

    The caller owns the new directory and its cleanup, including partial copies
    on error. Existing registered roots are already owned by that same caller.
    """
    try:
        if not directory.is_absolute():
            return StaticContentUnavailable(detail="invalid-layout")
        normalized = tuple(Path(os.path.abspath(path)) for path in selections)
        original = {name: Path(os.path.abspath(path)) for name, path in registered.items()}
        if any(directory.is_relative_to(root) for root in (*original.values(), *normalized)):
            return StaticContentUnavailable(detail="invalid-layout")
        external = list(dict.fromkeys(
            path for path in normalized if not any(path.is_relative_to(root) for root in original.values())
        ))
        external = [path for path in external if not any(path != other and path.is_relative_to(other) for other in external)]
        for index, path in enumerate(external):
            if path.is_symlink() or not path.is_dir():
                return StaticContentUnavailable(detail="unclosed-symlink")
            name = f"external-{index:03d}"
            if name in original:
                return StaticContentUnavailable(detail="invalid-layout")
            original[name] = path
        before = StaticContentCollector().collect(original)
        if isinstance(before, StaticContentUnavailable):
            return before
        mapped = tuple(_reference(path, original) for path in normalized)
        if any(before.entry_at(ref).kind != "directory" for ref in mapped):
            return StaticContentUnavailable(detail="invalid-layout")
        directory.mkdir()
        materialized = dict(original)
        for index, path in enumerate(external):
            name = f"external-{index:03d}"
            destination = directory / name
            shutil.copytree(path, destination, symlinks=True)
            materialized[name] = destination
        external_names = {f"external-{index:03d}" for index in range(len(external))}
        for entry in before.entries:
            if entry.location.root not in external_names or entry.link_target is None:
                continue
            link = materialized[entry.location.root] / entry.location.path
            target = materialized[entry.link_target.root] / entry.link_target.path
            link.unlink()
            link.symlink_to(target, target_is_directory=before.entry_at(entry.link_target).kind == "directory")
        after = StaticContentCollector().collect(materialized)
        if isinstance(after, StaticContentUnavailable):
            return after
        if before != after:
            return StaticContentUnavailable(detail="content-changed")
        return FrozenSearchRoots(tuple(sorted(materialized.items())), mapped, after)
    except (OSError, ValueError):
        return StaticContentUnavailable(detail="unreadable-content")


def _reference(path: Path, roots: Mapping[str, Path]) -> StaticContentPath:
    _, name, relative = max(
        (len(root.parts), name, path.relative_to(root).as_posix())
        for name, root in roots.items() if path.is_relative_to(root)
    )
    return StaticContentPath(root=name, path=relative)

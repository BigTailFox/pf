"""Collect actual content facts before constructing a static subject.

The caller supplies the closed set of immutable roots. This collector never
discovers additional host roots or grants authority to an incomplete manifest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from pf.schemas.policy import TyObservationPolicy

from pf.schemas.static import (
    StaticContentEntry,
    StaticContentManifest,
    StaticContentPath,
    StaticContentUnavailable,
    StaticSubject,
)


@dataclass(frozen=True)
class TyCheckKey:
    subject_identity: str
    observation_policy_identity: str


def ty_check_key(subject: StaticSubject, observation: TyObservationPolicy) -> TyCheckKey:
    """Project a validated static request, independent of any dynamic Proposal."""
    return TyCheckKey(subject.identity, observation.identity)


class StaticContentCollector:
    """Hash files, directory membership and closed logical symlink targets."""

    def collect(
        self, roots: Mapping[str, Path]
    ) -> StaticContentManifest | StaticContentUnavailable:
        if not roots:
            return StaticContentUnavailable(detail="invalid-layout")
        # Roots must already be resolved by the owner; resolving here could hide
        # an unregistered external input behind a root symlink.
        physical = {name: Path(os.path.abspath(path)) for name, path in roots.items()}
        entries: list[StaticContentEntry] = []
        try:
            for name, root in sorted(physical.items()):
                StaticContentPath(root=name, path=".")
                unavailable = self._visit(root, name, root, physical, entries)
                if unavailable is not None:
                    return unavailable
            entries.sort(key=lambda item: (item.location.root, item.location.path))
            return StaticContentManifest(entries=tuple(entries))
        except OSError:
            return StaticContentUnavailable(detail="unreadable-content")
        except ValueError:
            return StaticContentUnavailable(detail="invalid-layout")

    def _visit(
        self,
        path: Path,
        name: str,
        root: Path,
        roots: Mapping[str, Path],
        entries: list[StaticContentEntry],
    ) -> StaticContentUnavailable | None:
        before = path.lstat()
        location = StaticContentPath(root=name, path=path.relative_to(root).as_posix())
        if stat.S_ISLNK(before.st_mode):
            raw_target = os.readlink(path)
            target = Path(os.path.abspath(path.parent / raw_target))
            matches = [
                (len(base.parts), label, target.relative_to(base).as_posix())
                for label, base in roots.items() if target.is_relative_to(base)
            ]
            if not matches:
                return StaticContentUnavailable(detail="unclosed-symlink")
            _, target_root, relative = max(matches)
            entry = StaticContentEntry(
                location=location, kind="symlink", content_digest=None,
                link_target=StaticContentPath(root=target_root, path=relative),
            )
        elif stat.S_ISDIR(before.st_mode):
            children = sorted(path.iterdir())
            entry = StaticContentEntry(
                location=location, kind="directory", content_digest=None, link_target=None,
            )
            entries.append(entry)
            for child in children:
                unavailable = self._visit(child, name, root, roots, entries)
                if unavailable is not None:
                    return unavailable
            if children != sorted(path.iterdir()):
                return StaticContentUnavailable(detail="content-changed")
        elif stat.S_ISREG(before.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            entry = StaticContentEntry(
                location=location, kind="file", content_digest=digest.hexdigest(),
                link_target=None,
            )
        else:
            return StaticContentUnavailable(detail="unsupported-file-kind")
        after = path.lstat()
        if (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            return StaticContentUnavailable(detail="content-changed")
        if entry.kind != "directory":
            entries.append(entry)
        return None

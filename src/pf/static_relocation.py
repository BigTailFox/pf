"""Normalize only recognized installed-file references to materialization roots."""

from __future__ import annotations

from collections.abc import Mapping
import base64
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Literal

from pf.schemas.static import (
    StaticContentEntry, StaticContentManifest, StaticContentPath,
    StaticTextLiteral, StaticTextProjection, StaticTextRoot, StaticTextFileValue,
)


def relocate_installed_content(
    content: StaticContentManifest, roots: Mapping[str, Path],
) -> StaticContentManifest:
    """Verify captured bytes before replacing registered path references.

    Timestamps, unknown metadata and user source bytes keep their exact digest.
    This operation does not modify the prepared environment.
    """
    entries: list[StaticContentEntry] = []
    for entry in content.entries:
        if entry.location.root != "environment" or entry.kind != "file":
            entries.append(entry)
            continue
        path = roots[entry.location.root] / entry.location.path
        profile = _profile(entry.location)
        if profile is None:
            entries.append(entry)
            continue
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry.content_digest:
            raise ValueError("installed content changed during relocation capture")
        try:
            text = payload.decode("utf-8")
        except UnicodeError:
            entries.append(entry)
            continue
        limit = len(text)
        if profile == "editable-pth-v1":
            if any(line.startswith(("import ", "import\t")) for line in text.splitlines()):
                entries.append(entry)
                continue
        elif profile == "direct-url-v1":
            document = json.loads(text)
            if not isinstance(document, dict) or not isinstance(document.get("dir_info"), dict):
                entries.append(entry)
                continue
        elif profile == "script-shebang-v1":
            if not text.startswith("#!"):
                entries.append(entry)
                continue
            limit = text.find("\n") if "\n" in text else len(text)
        encoding: Literal["path", "file-uri"] = "file-uri" if profile == "direct-url-v1" else "path"
        candidates = {
            (path.as_uri() if encoding == "file-uri" else str(path)): name
            for name, path in roots.items()
        }
        expression = re.compile("|".join(re.escape(path) for path in sorted(candidates, key=len, reverse=True)))
        parts: list[StaticTextLiteral | StaticTextRoot] = []
        offset = 0
        for match in expression.finditer(text, 0, limit):
            end = match.end()
            if end < len(text) and text[end] not in "/\\\"'\r\n \t;":
                continue
            if match.start() > offset:
                parts.append(StaticTextLiteral(text=text[offset:match.start()]))
            parts.append(StaticTextRoot(root=candidates[match.group()], encoding=encoding))
            offset = end
        if offset == 0:
            entries.append(entry)
            continue
        if offset < len(text):
            parts.append(StaticTextLiteral(text=text[offset:]))
        projection = StaticTextProjection(profile=profile, parts=tuple(parts))
        entries.append(StaticContentEntry(
            location=entry.location, kind="file", content_digest=projection.identity,
            link_target=None, relocation=projection,
        ))
    relocated = {entry.location: entry for entry in entries}
    for index, entry in enumerate(entries):
        path = PurePosixPath(entry.location.path)
        if entry.location.root == "environment" and path.name == "RECORD" and path.parent.name.endswith(".dist-info"):
            projection = _record_projection(entry, roots, relocated)
            if projection is not None:
                entries[index] = StaticContentEntry(
                    location=entry.location, kind="file", content_digest=projection.identity,
                    link_target=None, relocation=projection,
                )
    return StaticContentManifest(entries=tuple(entries))


_CSV_CELL = r'(?:"(?:[^"]|"")*"|[^,\r\n"]*)'
_RECORD_ROW = re.compile(rf'(?P<path>{_CSV_CELL}),(?P<hash>{_CSV_CELL}),(?P<size>{_CSV_CELL})(?:\r?\n)?\Z')


def _record_projection(
    entry: StaticContentEntry, roots: Mapping[str, Path],
    relocated: Mapping[StaticContentPath, StaticContentEntry],
) -> StaticTextProjection | None:
    path = roots[entry.location.root] / entry.location.path
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry.content_digest:
        raise ValueError("installed RECORD changed during relocation capture")
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        return None
    replacements: list[tuple[int, int, StaticTextFileValue]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = _RECORD_ROW.fullmatch(line)
        if match is None:
            return None
        cells = next(csv.reader([line]))
        physical = Path(os.path.abspath(path.parent.parent / cells[0]))
        root = roots["environment"]
        if not physical.is_relative_to(root):
            return None
        location = StaticContentPath(root="environment", path=physical.relative_to(root).as_posix())
        target = relocated.get(location)
        if target is not None and target.relocation is not None:
            assert target.content_digest is not None
            target_bytes = physical.read_bytes()
            actual_hash = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(target_bytes).digest()).rstrip(b"=").decode()
            if cells[1] != actual_hash or cells[2] != str(len(target_bytes)):
                # An unverified RECORD stays exact raw content. It must not
                # acquire an equivalence relation by trusting claimed hashes.
                return None
            for column in ("hash", "size"):
                start, end = match.span(column)
                if line[start:end].startswith('"'):
                    start, end = start + 1, end - 1
                replacements.append((offset + start, offset + end, StaticTextFileValue(
                    column=column, location=location, content_digest=target.content_digest,
                )))
        offset += len(line)
    if not replacements:
        return None
    parts: list[StaticTextLiteral | StaticTextRoot | StaticTextFileValue] = []
    offset = 0
    for start, end, value in replacements:
        if start > offset:
            parts.append(StaticTextLiteral(text=text[offset:start]))
        parts.append(value)
        offset = end
    if offset < len(text):
        parts.append(StaticTextLiteral(text=text[offset:]))
    return StaticTextProjection(profile="installed-record-v1", parts=tuple(parts))


def _profile(location: StaticContentPath) -> Literal["editable-pth-v1", "direct-url-v1", "venv-activation-v1", "script-shebang-v1"] | None:
    path = PurePosixPath(location.path)
    if path.suffix == ".pth":
        return "editable-pth-v1"
    if path.name == "direct_url.json" and path.parent.name.endswith(".dist-info"):
        return "direct-url-v1"
    if path.parent.as_posix() in {"bin", "Scripts"}:
        if path.name in {"activate", "activate.bat", "activate.csh", "activate.fish", "activate.nu", "activate.ps1", "Activate.ps1"}:
            return "venv-activation-v1"
        return "script-shebang-v1"
    return None

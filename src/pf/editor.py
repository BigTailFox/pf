from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
import tomli
import tomlkit
from tomlkit.items import Array

from pf.errors import ConfigurationError, InfrastructureError
from pf.schemas.apply import (
    AuthorizedDependencyGroupEdit,
    AuthorizedProjectEdit,
    AuthorizedWorkspaceApply,
)
from pf.schemas.project import DependencyGroupKey
from pf.schemas.report import ProjectEditResult
from pf.snapshot import SnapshotBuilder


@dataclass
class _PreparedApply:
    authorization: AuthorizedProjectEdit
    pyproject: Path
    relative: str
    original: bytes
    rendered: bytes


class ProjectEditor:
    """Execute a frozen workspace authorization with recovery and raw CAS."""

    _RECOVERY_SCHEMA = 2

    def __init__(self, *, snapshots: SnapshotBuilder) -> None:
        self._snapshots = snapshots

    def apply_many(
        self,
        *,
        authorization: AuthorizedWorkspaceApply,
        root: Path,
    ) -> tuple[ProjectEditResult, ...]:
        root = root.resolve()
        journal = root / ".pf" / "apply-recovery.json"
        self._recover(root=root, journal=journal)
        if not authorization.package_applies:
            return ()
        authorized_edits = tuple(
            edit
            for package_apply in authorization.package_applies
            for edit in package_apply.authorized_edits
        )
        if len({edit.pyproject_path for edit in authorized_edits}) != len(
            authorized_edits
        ):
            raise ConfigurationError("workspace authorization has duplicate edits")
        prepared = tuple(
            self._prepare_edit(edit, root) for edit in authorized_edits
        )
        changing = tuple(item for item in prepared if item.rendered != item.original)
        current_snapshot = self._snapshots.build(
            root,
            owned_pyproject_paths=authorization.owned_pyproject_paths,
        )
        try:
            if current_snapshot.identity != authorization.expected_snapshot:
                raise ConfigurationError(
                    "project source snapshot changed after apply authorization"
                )
        finally:
            current_snapshot.close()
        if not changing:
            return self._results(
                authorization,
                changing=(),
                journal=journal,
                root=root,
            )
        for item in changing:
            if self._digest(item.pyproject.read_bytes()) != self._digest(item.original):
                raise ConfigurationError(
                    f"pyproject changed after apply prepare: {item.relative}"
                )

        state_dir = root / ".pf"
        state_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for item in changing:
            original_digest = self._digest(item.original)
            backup = state_dir / (
                f"apply-{original_digest[:16]}-"
                f"{item.relative.replace('/', '_')}.toml.backup"
            )
            self._atomic_write(backup, item.original)
            files.append(
                {
                    "pyproject_path": item.relative,
                    "original_digest": original_digest,
                    "target_digest": self._digest(item.rendered),
                    "backup_path": backup.relative_to(root).as_posix(),
                }
            )
        recovery: dict[str, Any] = {
            "schema_version": self._RECOVERY_SCHEMA,
            "state": "PREPARED",
            "files": files,
        }
        self._write_journal(journal, recovery)
        try:
            for item in changing:
                if self._digest(item.pyproject.read_bytes()) != self._digest(
                    item.original
                ):
                    raise ConfigurationError(
                        f"pyproject changed after apply prepare: {item.relative}"
                    )
                self._atomic_write(item.pyproject, item.rendered)
            recovery["state"] = "PROJECTS_REPLACED"
            self._write_journal(journal, recovery)
            for item in changing:
                self._validate_written(item)
            recovery["state"] = "VALIDATED"
            self._write_journal(journal, recovery)
            recovery["state"] = "COMMITTED"
            self._write_journal(journal, recovery)
            for record in files:
                (root / record["backup_path"]).unlink(missing_ok=True)
        except Exception:
            self._rollback(root=root, journal=journal, recovery=recovery)
            raise
        return self._results(
            authorization,
            changing=changing,
            journal=journal,
            root=root,
        )

    def _prepare_edit(
        self,
        authorization: AuthorizedProjectEdit,
        root: Path,
    ) -> _PreparedApply:
        pyproject = (root / authorization.pyproject_path).resolve()
        if root != pyproject and root not in pyproject.parents:
            raise ConfigurationError("authorized pyproject path escapes project root")
        if not pyproject.is_file():
            raise ConfigurationError(f"pyproject does not exist: {pyproject}")
        original = pyproject.read_bytes()
        current_identity = self._snapshots.pyproject_identity(
            path=authorization.pyproject_path,
            mode=stat.S_IMODE(pyproject.stat().st_mode),
            content=original,
        )
        if current_identity != authorization.expected_pyproject_identity:
            raise ConfigurationError(
                "pyproject identity changed after apply authorization"
            )
        document = tomlkit.parse(original.decode("utf-8"))
        for edit in authorization.group_edits:
            self._apply_group_edit(document, edit)
        rendered = tomlkit.dumps(document).encode("utf-8")
        return _PreparedApply(
            authorization=authorization,
            pyproject=pyproject,
            relative=authorization.pyproject_path,
            original=original,
            rendered=rendered,
        )

    def _validate_written(self, item: _PreparedApply) -> None:
        raw = item.pyproject.read_bytes()
        with item.pyproject.open("rb") as stream:
            tomli.load(stream)
        document = tomlkit.parse(raw.decode("utf-8"))
        current_identity = self._snapshots.pyproject_identity(
            path=item.relative,
            mode=stat.S_IMODE(item.pyproject.stat().st_mode),
            content=raw,
        )
        if (
            current_identity.mode
            != item.authorization.expected_pyproject_identity.mode
            or current_identity.remainder_digest
            != item.authorization.expected_pyproject_identity.remainder_digest
        ):
            raise ConfigurationError("edited pyproject changed unauthorized metadata")
        for edit in item.authorization.group_edits:
            values = self._group_requirements(document, edit.key)
            if values != edit.replacement_requirements:
                raise ConfigurationError(
                    f"authorized dependency group was not applied: {edit.key.name}"
                )

    @staticmethod
    def _dependency_array(
        document: Any,
        key: DependencyGroupKey,
    ) -> Array:
        try:
            if key.location == "base":
                value = document["project"]["dependencies"]
            else:
                assert key.optional_group is not None
                value = document["project"]["optional-dependencies"][
                    key.optional_group
                ]
        except (KeyError, TypeError) as error:
            raise ConfigurationError(
                f"dependency location has drifted: {key.name}"
            ) from error
        if not isinstance(value, Array):
            raise ConfigurationError("dependency metadata is not a TOML array")
        return value

    def _group_requirements(
        self,
        document: Any,
        key: DependencyGroupKey,
    ) -> tuple[str, ...]:
        array = self._dependency_array(document, key)
        return tuple(
            str(value)
            for value in array
            if self._requirement_name(str(value)) == key.name
        )

    def _apply_group_edit(
        self,
        document: Any,
        edit: AuthorizedDependencyGroupEdit,
    ) -> None:
        array = self._dependency_array(document, edit.key)
        values = tuple(str(value) for value in array)
        indices = tuple(
            index
            for index, value in enumerate(values)
            if self._requirement_name(value) == edit.key.name
        )
        if not indices:
            raise ConfigurationError(
                f"authorized dependency group is missing: {edit.key.name}"
            )
        first_index = indices[0]
        for index in reversed(indices[1:]):
            del array[index]
        if not edit.replacement_requirements:
            del array[first_index]
            return
        first, *remaining = edit.replacement_requirements
        array[first_index] = first
        for offset, requirement in enumerate(remaining, start=1):
            array.insert(first_index + offset, requirement)

    @staticmethod
    def _requirement_name(raw: str) -> str:
        try:
            return canonicalize_name(Requirement(raw).name)
        except InvalidRequirement as error:
            raise ConfigurationError("invalid dependency metadata during apply") from error

    @staticmethod
    def _results(
        authorization: AuthorizedWorkspaceApply,
        *,
        changing: tuple[_PreparedApply, ...],
        journal: Path,
        root: Path,
    ) -> tuple[ProjectEditResult, ...]:
        changed_paths = {item.relative for item in changing}
        return tuple(
            ProjectEditResult(
                changed=package_apply.package.pyproject_path in changed_paths,
                pyproject_path=package_apply.package.pyproject_path,
                recovery_log_path=journal.relative_to(root).as_posix(),
            )
            for package_apply in authorization.package_applies
        )

    def _recover(self, *, root: Path, journal: Path) -> None:
        if not journal.is_file():
            return
        try:
            recovery = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigurationError(
                f"invalid apply recovery log: {journal}"
            ) from error
        if recovery.get("schema_version") != self._RECOVERY_SCHEMA:
            raise ConfigurationError(
                f"unrecognized apply recovery schema: {journal}"
            )
        if recovery.get("state") in {"COMMITTED", "ROLLED_BACK"}:
            return
        self._rollback(root=root, journal=journal, recovery=recovery)

    def _rollback(
        self,
        *,
        root: Path,
        journal: Path,
        recovery: dict[str, Any],
    ) -> None:
        recovery["state"] = "ROLLING_BACK"
        self._write_journal(journal, recovery)
        try:
            for record in recovery.get("files", ()):
                pyproject = root / record["pyproject_path"]
                current = self._digest(pyproject.read_bytes())
                if current == record["original_digest"]:
                    continue
                if current != record["target_digest"]:
                    raise ConfigurationError(
                        f"cannot recover apply after unknown project changes: {journal}"
                    )
                backup = root / record["backup_path"]
                self._atomic_write(pyproject, backup.read_bytes())
            recovery["state"] = "ROLLED_BACK"
            self._write_journal(journal, recovery)
        except OSError as error:
            raise InfrastructureError(
                f"apply rollback failed; recovery log: {journal}",
                detail=str(error),
            ) from error

    @staticmethod
    def _write_journal(path: Path, recovery: dict[str, Any]) -> None:
        content = (
            json.dumps(recovery, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        ProjectEditor._atomic_write(path, content)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_mode = (
            stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if existing_mode is not None:
                temporary.chmod(existing_mode)
            os.replace(temporary, path)
            ProjectEditor._sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

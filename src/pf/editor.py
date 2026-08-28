from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import tomli
import tomlkit
from tomlkit.items import Array

from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.report import PackageReportBuilder, ValidatedReport
from pf.schemas.project import RequirementDeclaration
from pf.schemas.report import (
    ProjectEditResult,
    ProjectionEvidence,
)
from pf.snapshot import SnapshotBuilder


@dataclass
class _PreparedApply:
    report: ValidatedReport
    pyproject: Path
    relative: str
    original: bytes
    rendered: bytes
    declarations: dict[str, RequirementDeclaration]
    projections: tuple[ProjectionEvidence, ...]


class ProjectEditor:
    """Apply only projection evidence authorized by a complete Schema 1 report."""

    _RECOVERY_SCHEMA = 2

    def __init__(self, *, snapshots: SnapshotBuilder) -> None:
        self._snapshots = snapshots

    def apply(
        self,
        *,
        report: ValidatedReport,
        root: Path,
    ) -> ProjectEditResult:
        return self.apply_many(reports=(report,), root=root)[0]

    def apply_many(
        self,
        *,
        reports: tuple[ValidatedReport, ...],
        root: Path,
    ) -> tuple[ProjectEditResult, ...]:
        root = root.resolve()
        journal = root / ".pf" / "apply-recovery.json"
        self._recover(root=root, journal=journal)
        if not reports:
            return ()
        source_identity = reports[0].source_snapshot
        if any(report.source_snapshot != source_identity for report in reports[1:]):
            raise ConfigurationError("workspace reports use different source snapshots")
        prepared = tuple(self._prepare_report(report, root) for report in reports)
        changing = tuple(item for item in prepared if item.rendered != item.original)
        if not changing:
            return tuple(
                ProjectEditResult(
                    changed=False,
                    pyproject_path=item.relative,
                    recovery_log_path=journal.relative_to(root).as_posix(),
                )
                for item in prepared
            )
        current_snapshot = self._snapshots.build(root)
        try:
            if current_snapshot.identity != source_identity:
                raise ConfigurationError(
                    "project source snapshot has drifted since search"
                )
        finally:
            current_snapshot.close()

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
        return tuple(
            ProjectEditResult(
                changed=item.rendered != item.original,
                pyproject_path=item.relative,
                recovery_log_path=journal.relative_to(root).as_posix(),
            )
            for item in prepared
        )

    def _prepare_report(
        self,
        report: ValidatedReport,
        root: Path,
    ) -> _PreparedApply:
        if report.result.status != "complete":
            raise NoApplicableFloorError("cannot apply an incomplete floor report")
        pyproject = (root / report.package.pyproject_path).resolve()
        if root != pyproject and root not in pyproject.parents:
            raise ConfigurationError("report pyproject path escapes project root")
        if not pyproject.is_file():
            raise ConfigurationError(f"pyproject does not exist: {pyproject}")
        original = pyproject.read_bytes()
        document = tomlkit.parse(original.decode("utf-8"))
        declarations = {
            declaration.declaration_id: declaration
            for declaration in report.requirement_declarations
        }
        projections = tuple(report.projection_evidence)
        for projection in projections:
            if not projection.representable or not projection.projected_requirements:
                raise NoApplicableFloorError(
                    f"projection is not applicable: {projection.declaration_id}"
                )
            if projection.declaration_id not in declarations:
                raise ConfigurationError("projection references an unknown declaration")
            declaration = declarations[projection.declaration_id]
            active_cells = tuple(
                cell
                for cell in report.target_cells
                if declaration.declaration_id in cell.active_declaration_ids
            )
            expected = PackageReportBuilder().project(
                declaration=declaration,
                target_cells=report.target_cells,
                active_cells=active_cells,
                floors=projection.floors,
            )
            if projection != expected:
                raise ConfigurationError(
                    f"unauthorized projected requirement: {projection.declaration_id}"
                )
        if all(
            self._projection_is_applied(
                document,
                declarations[projection.declaration_id],
                projection,
            )
            for projection in projections
        ):
            rendered = original
        else:
            for projection in projections:
                self._apply_projection(
                    document,
                    declarations[projection.declaration_id],
                    projection,
                )
            rendered = tomlkit.dumps(document).encode("utf-8")
        return _PreparedApply(
            report=report,
            pyproject=pyproject,
            relative=report.package.pyproject_path,
            original=original,
            rendered=rendered,
            declarations=declarations,
            projections=projections,
        )

    def _validate_written(self, item: _PreparedApply) -> None:
        raw = item.pyproject.read_bytes()
        with item.pyproject.open("rb") as stream:
            parsed = tomli.load(stream)
        name = parsed.get("project", {}).get("name")
        if name != item.report.package.name:
            raise ConfigurationError("edited package identity does not match the report")
        document = tomlkit.parse(raw.decode("utf-8"))
        for projection in item.projections:
            if not self._projection_is_applied(
                document,
                item.declarations[projection.declaration_id],
                projection,
            ):
                raise ConfigurationError(
                    f"edited projection was not applied: {projection.declaration_id}"
                )

    @staticmethod
    def _dependency_array(
        document: Any,
        declaration: RequirementDeclaration,
    ) -> Array:
        try:
            if declaration.location == "base":
                value = document["project"]["dependencies"]
            else:
                assert declaration.extra is not None
                value = document["project"]["optional-dependencies"][declaration.extra]
        except (KeyError, TypeError) as error:
            raise ConfigurationError(
                f"dependency location has drifted: {declaration.declaration_id}"
            ) from error
        if not isinstance(value, Array):
            raise ConfigurationError("dependency metadata is not a TOML array")
        return value

    def _projection_is_applied(
        self,
        document: Any,
        declaration: RequirementDeclaration,
        projection: ProjectionEvidence,
    ) -> bool:
        array = self._dependency_array(document, declaration)
        values = tuple(str(value) for value in array)
        projected = projection.projected_requirements
        if declaration.raw in projected:
            return all(value in values for value in projected)
        return declaration.raw not in values and all(
            value in values for value in projected
        )

    def _apply_projection(
        self,
        document: Any,
        declaration: RequirementDeclaration,
        projection: ProjectionEvidence,
    ) -> None:
        array = self._dependency_array(document, declaration)
        values = tuple(str(value) for value in array)
        try:
            index = values.index(declaration.raw)
        except ValueError as error:
            raise ConfigurationError(
                f"dependency declaration has drifted: {declaration.declaration_id}"
            ) from error
        first, *remaining = projection.projected_requirements
        array[index] = first
        for offset, requirement in enumerate(remaining, start=1):
            array.insert(index + offset, requirement)

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

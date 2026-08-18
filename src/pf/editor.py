from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import tomli
import tomlkit
from tomlkit.items import Array

from pf.errors import ConfigurationError, NoApplicableFloorError
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder
from pf.schemas.project import RequirementDeclaration
from pf.schemas.report import (
    PackageFloorReportV1,
    ProjectEditResult,
    ProjectionEvidence,
)
from pf.snapshot import SnapshotBuilder


class ProjectEditor:
    """Apply only projection evidence authorized by a complete v1 report."""

    def __init__(self, *, snapshots: SnapshotBuilder) -> None:
        self._snapshots = snapshots

    def apply(
        self,
        *,
        report: PackageFloorReportV1,
        root: Path,
        _source_verified: bool = False,
    ) -> ProjectEditResult:
        if report.result.status != "complete":
            raise NoApplicableFloorError("cannot apply an incomplete floor report")
        root = root.resolve()
        pyproject = (root / report.package.pyproject_path).resolve()
        if root != pyproject and root not in pyproject.parents:
            raise ConfigurationError("report pyproject path escapes project root")
        if not pyproject.is_file():
            raise ConfigurationError(f"pyproject does not exist: {pyproject}")

        state_dir = root / ".pf"
        journal = state_dir / "apply-recovery.json"
        self._recover(journal=journal, pyproject=pyproject)

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
            return ProjectEditResult(
                changed=False,
                pyproject_path=report.package.pyproject_path,
                recovery_log_path=journal.relative_to(root).as_posix(),
            )

        if not _source_verified:
            current_snapshot = self._snapshots.build(root)
            try:
                if current_snapshot.identity != report.source_snapshot:
                    raise ConfigurationError(
                        "project source snapshot has drifted since search"
                    )
            finally:
                current_snapshot.close()

        for projection in projections:
            self._apply_projection(
                document,
                declarations[projection.declaration_id],
                projection,
            )
        rendered = tomlkit.dumps(document).encode("utf-8")
        if rendered == original:
            return ProjectEditResult(
                changed=False,
                pyproject_path=report.package.pyproject_path,
                recovery_log_path=journal.relative_to(root).as_posix(),
            )

        state_dir.mkdir(parents=True, exist_ok=True)
        original_digest = self._digest(original)
        target_digest = self._digest(rendered)
        backup = state_dir / f"apply-{original_digest[:16]}.toml.backup"
        self._atomic_write(backup, original)
        recovery = {
            "schema_version": 1,
            "state": "PREPARED",
            "pyproject_path": report.package.pyproject_path,
            "original_digest": original_digest,
            "target_digest": target_digest,
            "backup_path": backup.relative_to(root).as_posix(),
        }
        self._write_journal(journal, recovery)
        self._atomic_write(pyproject, rendered)
        recovery["state"] = "PROJECT_REPLACED"
        self._write_journal(journal, recovery)

        try:
            with pyproject.open("rb") as stream:
                tomli.load(stream)
            ProjectLoader().load(root=root, package_selection=report.package.name)
        except Exception as error:
            raise ConfigurationError(
                f"edited project failed validation; recovery log: {journal}"
            ) from error
        recovery["state"] = "REPORT_CONFIRMED"
        self._write_journal(journal, recovery)
        recovery["state"] = "COMMITTED"
        self._write_journal(journal, recovery)
        backup.unlink(missing_ok=True)
        return ProjectEditResult(
            changed=True,
            pyproject_path=report.package.pyproject_path,
            recovery_log_path=journal.relative_to(root).as_posix(),
        )

    def apply_many(
        self,
        *,
        reports: tuple[PackageFloorReportV1, ...],
        root: Path,
    ) -> tuple[ProjectEditResult, ...]:
        if not reports:
            return ()
        source_identity = reports[0].source_snapshot
        if any(report.source_snapshot != source_identity for report in reports[1:]):
            raise ConfigurationError("workspace reports use different source snapshots")
        current_snapshot = self._snapshots.build(root)
        try:
            source_verified = current_snapshot.identity == source_identity
        finally:
            current_snapshot.close()
        return tuple(
            self.apply(
                report=report,
                root=root,
                _source_verified=source_verified,
            )
            for report in reports
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

    def _recover(self, *, journal: Path, pyproject: Path) -> None:
        if not journal.is_file():
            return
        try:
            recovery = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigurationError(f"invalid apply recovery log: {journal}") from error
        if recovery.get("state") == "COMMITTED":
            return
        current_digest = self._digest(pyproject.read_bytes())
        if current_digest not in {
            recovery.get("original_digest"),
            recovery.get("target_digest"),
        }:
            raise ConfigurationError(
                f"cannot recover apply after unknown project changes: {journal}"
            )
        recovery["state"] = "COMMITTED"
        self._write_journal(journal, recovery)

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

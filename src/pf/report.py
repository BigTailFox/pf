from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from packaging.requirements import Requirement
from packaging.version import Version

from pydantic import ValidationError

from pf.errors import ConfigurationError
from pf import __version__
from pf.policy import evaluation_policy_identity
from pf.project import marker_platform
from pf.schemas.project import (
    CandidateSnapshot,
    Cell,
    PackagePlan,
    RequirementDeclaration,
    SourceSnapshotIdentity,
)
from pf.schemas.report import (
    CellFailure,
    CellSuccess,
    CompleteReportResult,
    FloorProjection,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageIdentity,
    PackageFloorReportV1,
    ProjectionEvidence,
)


class PackageReportBuilder:
    """Build apply authority from canonical package, cell, and snapshot records."""

    def build(
        self,
        *,
        package: PackagePlan,
        source_snapshot: SourceSnapshotIdentity,
        cell_results: tuple[CellSuccess | CellFailure, ...],
    ) -> PackageFloorReportV1:
        result_by_cell = {
            self._cell_key(result.cell): result for result in cell_results
        }
        projections = tuple(
            self._projection(
                declaration=declaration,
                package=package,
                result_by_cell=result_by_cell,
            )
            for declaration in package.declarations
            if declaration.managed
        )
        projections = tuple(
            projection for projection in projections if projection is not None
        )
        target_keys = {self._cell_key(cell) for cell in package.cells}
        coverage_complete = set(result_by_cell) == target_keys
        all_success = bool(package.cells) and coverage_complete and all(
            isinstance(result_by_cell[key], CellSuccess) for key in target_keys
        )
        all_representable = all(
            projection.representable for projection in projections
        )
        if all_success and all_representable:
            result_summary = CompleteReportResult()
        else:
            reasons: set[str] = {
                result.status
                for result in cell_results
                if isinstance(result, CellFailure)
            }
            if not coverage_complete:
                reasons.add("MISSING_CELL")
            if not all_representable:
                reasons.add("UNREPRESENTABLE_PROJECTION")
            result_summary = IncompleteReportResult(reasons=tuple(sorted(reasons)))

        candidate_snapshots: dict[tuple[str, str], CandidateSnapshot] = {}
        for result in cell_results:
            if not isinstance(result, CellSuccess):
                continue
            for snapshot in result.candidate_snapshots:
                key = (self._cell_key(snapshot.cell), snapshot.dependency)
                candidate_snapshots[key] = snapshot

        return PackageFloorReportV1(
            generator=GeneratorIdentity(name="pf", version=__version__, algorithm="v1"),
            package=PackageIdentity(
                name=package.name,
                pyproject_path=package.pyproject_path,
            ),
            source_snapshot=source_snapshot,
            policy_identity=self._policy_identity(package, cell_results),
            requirement_declarations=package.declarations,
            candidate_snapshots=tuple(
                candidate_snapshots[key] for key in sorted(candidate_snapshots)
            ),
            target_cells=package.cells,
            cell_results=tuple(
                result_by_cell[key] for key in sorted(result_by_cell)
            ),
            projection_evidence=projections,
            result=result_summary,
        )

    def _projection(
        self,
        *,
        declaration: RequirementDeclaration,
        package: PackagePlan,
        result_by_cell: dict[str, CellSuccess | CellFailure],
    ) -> ProjectionEvidence | None:
        active_cells = tuple(
            cell
            for cell in package.cells
            if declaration.declaration_id in cell.active_declaration_ids
        )
        if not active_cells:
            return None
        floors: list[FloorProjection] = []
        for cell in active_cells:
            result = result_by_cell.get(self._cell_key(cell))
            if not isinstance(result, CellSuccess):
                continue
            floor = next(
                (pin.version for pin in result.final_vector if pin.name == declaration.name),
                None,
            )
            if floor is not None:
                floors.append(FloorProjection(cell=cell, version=floor))
        return self.project(
            declaration=declaration,
            active_cells=active_cells,
            floors=tuple(floors),
        )

    def project(
        self,
        *,
        declaration: RequirementDeclaration,
        active_cells: tuple[Cell, ...],
        floors: tuple[FloorProjection, ...],
    ) -> ProjectionEvidence:
        ordered_floors = tuple(
            sorted(floors, key=lambda floor: self._cell_key(floor.cell))
        )
        complete = {
            self._cell_key(floor.cell) for floor in ordered_floors
        } == {self._cell_key(cell) for cell in active_cells}
        versions = {floor.version for floor in ordered_floors}
        if complete and len(versions) == 1:
            requirements = (self._project_requirement(declaration, versions.pop()),)
        elif complete:
            requirements = self._project_distinct_floors(
                declaration,
                ordered_floors,
            )
        else:
            requirements = ()
        return ProjectionEvidence(
            declaration_id=declaration.declaration_id,
            floors=ordered_floors,
            projected_requirements=requirements,
            representable=complete and bool(requirements),
        )

    def _project_distinct_floors(
        self,
        declaration: RequirementDeclaration,
        floors: tuple[FloorProjection, ...],
    ) -> tuple[str, ...]:
        attributes: dict[str, dict[str, str] | None] = {
            self._cell_key(floor.cell): self._marker_attributes(floor.cell)
            for floor in floors
        }
        if any(value is None for value in attributes.values()):
            return ()
        varying = tuple(
            name
            for name in ("python_version", "sys_platform", "platform_machine")
            if len(
                {
                    value[name]
                    for value in attributes.values()
                    if value is not None
                }
            )
            > 1
        )
        selector_floors: dict[tuple[tuple[str, str], ...], str] = {}
        floor_selectors: dict[str, list[tuple[tuple[str, str], ...]]] = {}
        for floor in floors:
            value = attributes[self._cell_key(floor.cell)]
            assert value is not None
            selector = tuple((name, value[name]) for name in varying)
            previous = selector_floors.get(selector)
            if previous is not None and previous != floor.version:
                return ()
            selector_floors[selector] = floor.version
            floor_selectors.setdefault(floor.version, []).append(selector)
        if not varying:
            return ()
        requirements = []
        for version in sorted(floor_selectors, key=Version):
            alternatives = []
            for selector in sorted(set(floor_selectors[version])):
                parts = [f'{name} == "{value}"' for name, value in selector]
                alternatives.append(" and ".join(parts))
            marker = " or ".join(
                f"({alternative})" if " and " in alternative else alternative
                for alternative in alternatives
            )
            requirements.append(
                self._project_requirement(declaration, version, selector=marker)
            )
        return tuple(requirements)

    @staticmethod
    def _project_requirement(
        declaration: RequirementDeclaration,
        floor: str,
        *,
        selector: str | None = None,
    ) -> str:
        original = Requirement(declaration.raw)
        name = original.name
        extras = (
            f"[{','.join(sorted(original.extras))}]" if original.extras else ""
        )
        preserved = sorted(
            str(specifier)
            for specifier in original.specifier
            if specifier.operator not in {">", ">="}
        )
        specifiers = ",".join((*preserved, f">={floor}"))
        original_marker = str(original.marker) if original.marker is not None else None
        if original_marker is not None and selector is not None:
            marker_value = f"({original_marker}) and ({selector})"
        else:
            marker_value = original_marker or selector
        marker = f"; {marker_value}" if marker_value is not None else ""
        return f"{name}{extras}{specifiers}{marker}"

    @staticmethod
    def _marker_attributes(cell: Cell) -> dict[str, str] | None:
        try:
            platform = marker_platform(cell.target)
        except ConfigurationError:
            return None
        return {
            "python_version": cell.python_minor,
            **platform,
        }

    @staticmethod
    def _policy_identity(
        package: PackagePlan,
        cell_results: tuple[CellSuccess | CellFailure, ...],
    ) -> str:
        for result in cell_results:
            if isinstance(result, CellSuccess):
                return result.final_evaluation.proposal.policy_identity
            if result.baseline is not None:
                return result.baseline.proposal.policy_identity
        return evaluation_policy_identity(package.config)

    @staticmethod
    def _cell_key(cell: Cell) -> str:
        return "|".join(
            (cell.target, cell.python_minor, ",".join(cell.extra_surface))
        )


class ReportStore:
    """Own canonical, versioned, atomic package-floor report persistence."""

    def write(self, path: Path, report: PackageFloorReportV1) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = report.model_dump(mode="json", exclude_none=False)
        content = (
            json.dumps(
                document,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            self._sync_directory(path.parent)
        finally:
            temporary_path.unlink(missing_ok=True)

    def read(self, path: Path) -> PackageFloorReportV1:
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ConfigurationError(f"cannot read report: {path}") from error
        if len(content) > 64 * 1024 * 1024:
            raise ConfigurationError("report exceeds the 64 MiB read limit")
        try:
            document = json.loads(content)
        except json.JSONDecodeError as error:
            raise ConfigurationError(f"invalid report JSON: {path}") from error
        schema_version = document.get("schema_version") if isinstance(document, dict) else None
        if schema_version != 1:
            raise ConfigurationError(
                f"unsupported report schema_version: {schema_version}"
            )
        try:
            return PackageFloorReportV1.model_validate(document)
        except ValidationError as error:
            raise ConfigurationError(f"invalid v1 report: {error}") from error

    def merge(
        self,
        reports: tuple[PackageFloorReportV1, ...],
    ) -> PackageFloorReportV1:
        if not reports:
            raise ConfigurationError("merge requires at least one report")
        first = reports[0]
        for report in reports[1:]:
            if report.generator != first.generator:
                raise ConfigurationError("report generator identity mismatch")
            if report.package != first.package:
                raise ConfigurationError("report package identity mismatch")
            if report.source_snapshot != first.source_snapshot:
                raise ConfigurationError("report source snapshot identity mismatch")
            if report.policy_identity != first.policy_identity:
                raise ConfigurationError("report policy identity mismatch")
            if report.requirement_declarations != first.requirement_declarations:
                raise ConfigurationError("report declarations mismatch")
            if report.target_cells != first.target_cells:
                raise ConfigurationError("report target cell coverage mismatch")

        cell_results = {}
        candidate_snapshots = {}
        projections: dict[str, ProjectionEvidence] = {}
        reasons: set[str] = set()
        for report in reports:
            if isinstance(report.result, IncompleteReportResult):
                reasons.update(report.result.reasons)
            for result in report.cell_results:
                key = self._cell_key(result.cell)
                existing = cell_results.get(key)
                if existing is not None and existing != result:
                    raise ConfigurationError(f"conflicting result for cell: {key}")
                cell_results[key] = result
            for snapshot in report.candidate_snapshots:
                key = (self._cell_key(snapshot.cell), snapshot.dependency)
                existing = candidate_snapshots.get(key)
                if existing is not None and existing != snapshot:
                    raise ConfigurationError(
                        f"conflicting candidate snapshot: {snapshot.dependency}"
                    )
                candidate_snapshots[key] = snapshot
            for projection in report.projection_evidence:
                existing = projections.get(projection.declaration_id)
                if existing is None:
                    projections[projection.declaration_id] = projection
                    continue
                floor_by_cell = {
                    self._cell_key(floor.cell): floor for floor in existing.floors
                }
                for floor in projection.floors:
                    key = self._cell_key(floor.cell)
                    previous = floor_by_cell.get(key)
                    if previous is not None and previous != floor:
                        raise ConfigurationError(
                            f"conflicting projection for declaration: {projection.declaration_id}"
                        )
                    floor_by_cell[key] = floor
                projected_requirements = tuple(
                    sorted(
                        set(existing.projected_requirements)
                        | set(projection.projected_requirements)
                    )
                )
                projections[projection.declaration_id] = ProjectionEvidence(
                    declaration_id=projection.declaration_id,
                    floors=tuple(floor_by_cell[key] for key in sorted(floor_by_cell)),
                    projected_requirements=projected_requirements,
                    representable=existing.representable and projection.representable,
                )

        ordered_results = tuple(cell_results[key] for key in sorted(cell_results))
        target_keys = {self._cell_key(cell) for cell in first.target_cells}
        coverage_complete = set(cell_results) == target_keys
        all_success = bool(first.target_cells) and coverage_complete and all(
            isinstance(result, CellSuccess) for result in ordered_results
        )
        declaration_by_id = {
            declaration.declaration_id: declaration
            for declaration in first.requirement_declarations
        }
        unknown_projections = sorted(set(projections) - set(declaration_by_id))
        if unknown_projections:
            raise ConfigurationError(
                f"unknown projection declaration: {unknown_projections[0]}"
            )
        rebuilt_projections: dict[str, ProjectionEvidence] = {}
        projection_builder = PackageReportBuilder()
        for declaration in first.requirement_declarations:
            active_cells = tuple(
                cell
                for cell in first.target_cells
                if declaration.managed
                and declaration.declaration_id in cell.active_declaration_ids
            )
            if not active_cells:
                continue
            aggregated = projections.get(declaration.declaration_id)
            rebuilt_projections[declaration.declaration_id] = projection_builder.project(
                declaration=declaration,
                active_cells=active_cells,
                floors=aggregated.floors if aggregated is not None else (),
            )
        all_representable = all(
            projection.representable for projection in rebuilt_projections.values()
        )
        if all_success and all_representable:
            reasons.discard("MISSING_CELL")
            reasons.discard("UNREPRESENTABLE_PROJECTION")
            result_summary = CompleteReportResult()
        else:
            if coverage_complete:
                reasons.discard("MISSING_CELL")
            elif target_keys:
                reasons.add("MISSING_CELL")
            result_summary = IncompleteReportResult(reasons=tuple(sorted(reasons)))
        return PackageFloorReportV1(
            generator=first.generator,
            package=first.package,
            source_snapshot=first.source_snapshot,
            policy_identity=first.policy_identity,
            requirement_declarations=first.requirement_declarations,
            candidate_snapshots=tuple(
                candidate_snapshots[key] for key in sorted(candidate_snapshots)
            ),
            target_cells=first.target_cells,
            cell_results=ordered_results,
            projection_evidence=tuple(
                rebuilt_projections[key] for key in sorted(rebuilt_projections)
            ),
            result=result_summary,
        )

    def update(
        self,
        existing: PackageFloorReportV1,
        replacement: PackageFloorReportV1,
    ) -> PackageFloorReportV1:
        """Replace cells produced by one search while retaining other-host cells."""
        self._validate_generation(existing, replacement)
        replaced_keys = {
            self._cell_key(result.cell) for result in replacement.cell_results
        }
        if not replaced_keys:
            return existing
        retained_results = tuple(
            result
            for result in existing.cell_results
            if self._cell_key(result.cell) not in replaced_keys
        )
        if not retained_results:
            return replacement
        retained_projections = tuple(
            ProjectionEvidence(
                declaration_id=projection.declaration_id,
                floors=tuple(
                    floor
                    for floor in projection.floors
                    if self._cell_key(floor.cell) not in replaced_keys
                ),
                projected_requirements=(),
                representable=False,
            )
            for projection in existing.projection_evidence
        )
        reasons: set[str] = {
            result.status
            for result in retained_results
            if isinstance(result, CellFailure)
        }
        reasons.update({"MISSING_CELL", "UNREPRESENTABLE_PROJECTION"})
        retained = PackageFloorReportV1(
            generator=existing.generator,
            package=existing.package,
            source_snapshot=existing.source_snapshot,
            policy_identity=existing.policy_identity,
            requirement_declarations=existing.requirement_declarations,
            candidate_snapshots=tuple(
                snapshot
                for snapshot in existing.candidate_snapshots
                if self._cell_key(snapshot.cell) not in replaced_keys
            ),
            target_cells=existing.target_cells,
            cell_results=retained_results,
            projection_evidence=retained_projections,
            result=IncompleteReportResult(reasons=tuple(sorted(reasons))),
        )
        return self.merge((retained, replacement))

    @staticmethod
    def _validate_generation(
        left: PackageFloorReportV1,
        right: PackageFloorReportV1,
    ) -> None:
        comparisons = (
            (left.generator, right.generator, "generator"),
            (left.package, right.package, "package"),
            (left.source_snapshot, right.source_snapshot, "source snapshot"),
            (left.policy_identity, right.policy_identity, "policy"),
            (
                left.requirement_declarations,
                right.requirement_declarations,
                "declarations",
            ),
            (left.target_cells, right.target_cells, "target cell coverage"),
        )
        for left_value, right_value, label in comparisons:
            if left_value != right_value:
                raise ConfigurationError(f"report {label} identity mismatch")

    @staticmethod
    def _cell_key(cell: Cell) -> str:
        return "|".join(
            (
                cell.target,
                cell.python_minor,
                ",".join(cell.extra_surface),
            )
        )

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

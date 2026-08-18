from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import Evaluation, PassEvaluation
from pf.schemas.project import (
    CandidateSnapshot,
    Cell,
    RequirementDeclaration,
    SourceSnapshotIdentity,
    VersionPin,
)


class ProbeEvidence(FrozenSchema):
    status: Literal[
        "PASS",
        "STATIC_FAIL",
        "TEST_FAIL",
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
        "NONDETERMINISTIC",
    ]
    proposal_id: str


class ProbeObservation(FrozenSchema):
    dependency: str | None
    candidate_version: str | None
    vector: tuple[VersionPin, ...]
    evidence: ProbeEvidence


class CoordinateBoundary(FrozenSchema):
    dependency: str
    floor: str
    predecessor: str | None = None
    predecessor_status: Literal["STATIC_FAIL", "TEST_FAIL"] | None = None


class CoordinateSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    vector: tuple[VersionPin, ...]
    observations: tuple[ProbeObservation, ...]
    boundaries: tuple[CoordinateBoundary, ...]
    sweeps: int


class CoordinateFailure(FrozenSchema):
    status: Literal[
        "BASELINE_FAILED",
        "NON_MONOTONIC",
        "NO_PASS_IN_SEARCH_SPACE",
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
        "NONDETERMINISTIC",
    ]
    dependency: str | None = None
    observations: tuple[ProbeObservation, ...]
    counterexample: tuple[str, str] | None = None


CoordinateOutcome = Annotated[
    Union[CoordinateSuccess, CoordinateFailure],
    Field(discriminator="status"),
]


class CellSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    cell: Cell
    baseline: PassEvaluation
    candidate_snapshots: tuple[CandidateSnapshot, ...]
    static_search: CoordinateSuccess
    dynamic_search: CoordinateSuccess | None = None
    final_vector: tuple[VersionPin, ...]
    final_evaluation: PassEvaluation
    observed_upper: None = None


class CellFailure(FrozenSchema):
    status: Literal[
        "BASELINE_FAILED",
        "NON_MONOTONIC",
        "NONDETERMINISTIC",
        "NO_PASS_IN_SEARCH_SPACE",
        "UNAVAILABLE",
        "BUILD_UNAVAILABLE",
        "UNRESOLVABLE",
        "HARNESS_ERROR",
        "SOURCE_ERROR",
        "TOOL_ERROR",
        "TIMEOUT",
    ]
    cell: Cell
    phase: str
    baseline: Evaluation | None = None
    candidate_snapshots: tuple[CandidateSnapshot, ...] = ()
    coordinate_failure: CoordinateFailure | None = None


CellResult = Annotated[
    Union[CellSuccess, CellFailure],
    Field(discriminator="status"),
]


class GeneratorIdentity(FrozenSchema):
    name: str
    version: str
    algorithm: str


class PackageIdentity(FrozenSchema):
    name: str
    pyproject_path: str


class FloorProjection(FrozenSchema):
    cell: Cell
    version: str


class ProjectionEvidence(FrozenSchema):
    declaration_id: str
    floors: tuple[FloorProjection, ...]
    projected_requirements: tuple[str, ...]
    representable: bool


class CompleteReportResult(FrozenSchema):
    status: Literal["complete"] = "complete"


class IncompleteReportResult(FrozenSchema):
    status: Literal["incomplete"] = "incomplete"
    reasons: tuple[str, ...]


ReportResult = Annotated[
    Union[CompleteReportResult, IncompleteReportResult],
    Field(discriminator="status"),
]


class PackageFloorReportV1(FrozenSchema):
    schema_version: Literal[1] = 1
    generator: GeneratorIdentity
    package: PackageIdentity
    source_snapshot: SourceSnapshotIdentity
    policy_identity: str
    requirement_declarations: tuple[RequirementDeclaration, ...]
    candidate_snapshots: tuple[CandidateSnapshot, ...]
    target_cells: tuple[Cell, ...] = ()
    cell_results: tuple[CellResult, ...]
    projection_evidence: tuple[ProjectionEvidence, ...]
    result: ReportResult

    @model_validator(mode="after")
    def validate_completion_authority(self) -> "PackageFloorReportV1":
        target_keys = tuple(self._cell_key(cell) for cell in self.target_cells)
        result_keys = tuple(self._cell_key(result.cell) for result in self.cell_results)
        if len(set(target_keys)) != len(target_keys):
            raise ValueError("report target cells must be unique")
        if len(set(result_keys)) != len(result_keys):
            raise ValueError("report cell results must be unique")
        if self.result.status == "complete":
            if not self.cell_results or any(
                not isinstance(result, CellSuccess) for result in self.cell_results
            ):
                raise ValueError("complete report requires every target cell to succeed")
            if set(result_keys) != set(target_keys):
                raise ValueError("complete report requires exact cell coverage")
            if any(not projection.representable for projection in self.projection_evidence):
                raise ValueError("complete report requires representable projections")
            projections = {
                projection.declaration_id: projection
                for projection in self.projection_evidence
            }
            if len(projections) != len(self.projection_evidence):
                raise ValueError("projection declaration IDs must be unique")
            result_by_key = {
                self._cell_key(result.cell): result for result in self.cell_results
            }
            for declaration in self.requirement_declarations:
                if not declaration.managed:
                    continue
                active_keys = {
                    self._cell_key(cell)
                    for cell in self.target_cells
                    if declaration.declaration_id in cell.active_declaration_ids
                }
                if not active_keys:
                    continue
                projection = projections.get(declaration.declaration_id)
                if projection is None or {
                    self._cell_key(floor.cell) for floor in projection.floors
                } != active_keys:
                    raise ValueError(
                        "complete report requires exact projection coverage"
                    )
                for floor in projection.floors:
                    result = result_by_key[self._cell_key(floor.cell)]
                    assert isinstance(result, CellSuccess)
                    verified = next(
                        (
                            pin.version
                            for pin in result.final_vector
                            if pin.name == declaration.name
                        ),
                        None,
                    )
                    if verified != floor.version:
                        raise ValueError(
                            "projection floor must match the verified final vector"
                        )
        return self

    @staticmethod
    def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
        return (cell.package, cell.target, cell.python_minor, cell.extra_surface)


class ProjectEditResult(FrozenSchema):
    changed: bool
    pyproject_path: str
    recovery_log_path: str

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import (
    Evaluation,
    IndeterminateEvaluation,
    PassEvaluation,
    StaticBaseline,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFailEvaluation,
    ToolFailure,
)
from pf.schemas.project import (
    CandidateSnapshot,
    Cell,
    RequirementDeclaration,
    SourceSnapshotIdentity,
    VersionPin,
)


def _require_proposal_scope(
    evaluation: Evaluation | StaticPassEvaluation,
    *,
    cell: Cell,
    baseline: StaticBaseline,
) -> None:
    proposal = evaluation.proposal
    if (
        proposal.cell != cell
        or proposal.snapshot_digest != baseline.proposal.snapshot_digest
        or proposal.policy_identity != baseline.proposal.policy_identity
    ):
        raise ValueError(
            "evaluation must match the cell, snapshot, and policy of its static baseline"
        )


def _require_static_evidence(
    static: StaticEvaluation,
    *,
    cell: Cell,
    baseline: StaticBaseline,
) -> None:
    _require_proposal_scope(static, cell=cell, baseline=baseline)
    if isinstance(static, (StaticPassEvaluation, StaticFailEvaluation)) and (
        static.baseline_digest != baseline.digest
    ):
        raise ValueError("static evidence must use the cell frozen static baseline")


def _require_evaluation_evidence(
    evaluation: Evaluation,
    *,
    cell: Cell,
    baseline: StaticBaseline,
) -> None:
    _require_proposal_scope(evaluation, cell=cell, baseline=baseline)
    if isinstance(evaluation, (PassEvaluation, TestFailEvaluation)):
        if evaluation.static.proposal != evaluation.proposal:
            raise ValueError("evaluation static evidence must match its proposal")
        _require_static_evidence(evaluation.static, cell=cell, baseline=baseline)
    elif isinstance(evaluation, StaticFailEvaluation):
        _require_static_evidence(evaluation, cell=cell, baseline=baseline)


def _searches_for_cell(
    result: "CellSuccess | CellFailure",
) -> tuple[CoordinateSuccess | CoordinateFailure, ...]:
    if isinstance(result, CellSuccess):
        return (
            result.static_search,
            *((result.dynamic_search,) if result.dynamic_search is not None else ()),
        )
    if result.coordinate_failure is not None:
        return (result.coordinate_failure,)
    return ()


def _require_search_evidence(
    result: "CellSuccess | CellFailure",
    *,
    baseline: StaticBaseline,
) -> None:
    for search in _searches_for_cell(result):
        for observation in search.observations:
            if observation.evidence.static is not None:
                _require_static_evidence(
                    observation.evidence.static,
                    cell=result.cell,
                    baseline=baseline,
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
    static: StaticEvaluation | None = None

    @model_validator(mode="after")
    def validate_static_evidence(self) -> "ProbeEvidence":
        if self.static is not None and self.static.proposal.proposal_id != self.proposal_id:
            raise ValueError("probe static evidence must match its proposal")
        if isinstance(self.static, StaticFailEvaluation) and (
            self.status != "STATIC_FAIL"
        ):
            raise ValueError("probe status must match STATIC_FAIL evidence")
        if isinstance(self.static, StaticPassEvaluation) and self.status not in {
            "PASS",
            "TEST_FAIL",
        }:
            raise ValueError("probe status must match STATIC_PASS evidence")
        if isinstance(self.static, IndeterminateEvaluation) and (
            self.status != self.static.status
        ):
            raise ValueError("probe status must match indeterminate static evidence")
        return self


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
    static_baseline: StaticBaseline
    baseline: PassEvaluation
    candidate_snapshots: tuple[CandidateSnapshot, ...]
    static_search: CoordinateSuccess
    dynamic_search: CoordinateSuccess | None = None
    final_vector: tuple[VersionPin, ...]
    final_evaluation: PassEvaluation
    observed_upper: None = None

    @model_validator(mode="after")
    def validate_static_baseline(self) -> "CellSuccess":
        if self.static_baseline.proposal.cell != self.cell:
            raise ValueError("cell static baseline must match the result cell")
        if self.static_baseline.proposal != self.baseline.proposal:
            raise ValueError("cell static baseline must identify V_hi")
        if self.baseline.static.ty != self.static_baseline.ty:
            raise ValueError("cell baseline evaluation must reuse the captured TyCheck")
        if self.baseline.static.baseline_digest != self.static_baseline.digest:
            raise ValueError("cell baseline evaluation must use the captured digest")
        _require_evaluation_evidence(
            self.baseline,
            cell=self.cell,
            baseline=self.static_baseline,
        )
        _require_evaluation_evidence(
            self.final_evaluation,
            cell=self.cell,
            baseline=self.static_baseline,
        )
        _require_search_evidence(self, baseline=self.static_baseline)
        return self


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
    static_baseline: StaticBaseline | None = None
    baseline: Evaluation | None = None
    candidate_snapshots: tuple[CandidateSnapshot, ...] = ()
    coordinate_failure: CoordinateFailure | None = None
    failure: ToolFailure | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_available_static_baseline(self) -> "CellFailure":
        baseline_required = isinstance(
            self.baseline,
            (PassEvaluation, StaticFailEvaluation, TestFailEvaluation),
        ) or any(
            observation.evidence.static is not None
            for search in _searches_for_cell(self)
            for observation in search.observations
        )
        if baseline_required:
            if self.static_baseline is None:
                raise ValueError("cell failure with static evidence requires S_hi")
        if self.static_baseline is not None:
            if self.static_baseline.proposal.cell != self.cell:
                raise ValueError("cell failure static baseline must match its cell")
            if self.baseline is not None:
                if self.static_baseline.proposal != self.baseline.proposal:
                    raise ValueError(
                        "cell failure baseline must be the captured V_hi proposal"
                    )
                if isinstance(
                    self.baseline,
                    (PassEvaluation, StaticFailEvaluation, TestFailEvaluation),
                ):
                    static_ty = (
                        self.baseline.static.ty
                        if isinstance(
                            self.baseline,
                            (PassEvaluation, TestFailEvaluation),
                        )
                        else self.baseline.ty
                    )
                    if static_ty != self.static_baseline.ty:
                        raise ValueError(
                            "cell failure baseline must reuse the captured V_hi TyCheck"
                        )
                _require_evaluation_evidence(
                    self.baseline,
                    cell=self.cell,
                    baseline=self.static_baseline,
                )
            _require_search_evidence(self, baseline=self.static_baseline)
        return self


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
        for cell_result in self.cell_results:
            static_baseline = cell_result.static_baseline
            if static_baseline is not None and (
                static_baseline.proposal.snapshot_digest
                != self.source_snapshot.digest
                or static_baseline.proposal.policy_identity != self.policy_identity
            ):
                raise ValueError(
                    "static baseline must match report source and policy"
                )
            for search in _searches_for_cell(cell_result):
                for observation in search.observations:
                    evidence = observation.evidence
                    if evidence.status == "STATIC_FAIL" and not isinstance(
                        evidence.static, StaticFailEvaluation
                    ):
                        raise ValueError(
                            "STATIC_FAIL probe requires structured static evidence"
                        )
        return self

    @staticmethod
    def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
        return (cell.package, cell.target, cell.python_minor, cell.extra_surface)


class ProjectEditResult(FrozenSchema):
    changed: bool
    pyproject_path: str
    recovery_log_path: str

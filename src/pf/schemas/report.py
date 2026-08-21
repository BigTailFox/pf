from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    Evaluation,
    FailureCause,
    FailureRecord,
    IndeterminateEvaluation,
    PassEvaluation,
    StaticBaseline,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFailEvaluation,
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


def _failure_scope_cell(failure: FailureRecord) -> Cell:
    scope = failure.scope
    return (
        scope.attempt.identity.cell
        if isinstance(scope, AttemptFailureScope)
        else scope.cell
    )


def _searches_for_cell(
    result: "CellSuccess | CellIndeterminate | CellSearchFailure | BaselineRejection | BaselineIndeterminate",
) -> tuple[CoordinateSuccess | CoordinateFailure, ...]:
    if isinstance(result, CellSuccess):
        return (
            result.static_search,
            *((result.dynamic_search,) if result.dynamic_search is not None else ()),
        )
    if isinstance(result, (CellIndeterminate, CellSearchFailure)) and (
        result.coordinate_failure is not None
    ):
        return (result.coordinate_failure,)
    return ()


def _require_search_evidence(
    result: "CellSuccess | CellIndeterminate | CellSearchFailure",
    *,
    baseline: StaticBaseline,
    baseline_attempt: Attempt,
) -> None:
    for search in _searches_for_cell(result):
        requires_full_pass = (
            isinstance(result, CellSuccess)
            and result.dynamic_search is search
            or not isinstance(result, CellSuccess)
            and result.phase.startswith("dynamic")
        )
        for observation in search.observations:
            evidence = observation.evidence
            _require_shared_evaluation_context(
                evidence.attempt,
                baseline_attempt=baseline_attempt,
            )
            static = evidence.static_evaluation
            if (
                requires_full_pass
                and isinstance(evidence, ProbePass)
                and not isinstance(evidence.evaluation, PassEvaluation)
            ):
                raise ValueError("dynamic probe PASS requires full evaluation")
            if (
                isinstance(evidence, ProbeRejection)
                and evidence.cause in {"STATIC_REGRESSION", "TEST_FAILURE"}
                and evidence.evaluation is None
            ):
                raise ValueError(
                    "reported static/test rejection requires structured evaluation"
                )
            if static is not None:
                _require_static_evidence(
                    static,
                    cell=result.cell,
                    baseline=baseline,
                )


def _require_shared_evaluation_context(
    attempt: Attempt,
    *,
    baseline_attempt: Attempt,
) -> None:
    identity = attempt.identity
    baseline = baseline_attempt.identity
    if identity.requested_resolution != "exact-vector":
        raise ValueError("probe evidence requires an exact-vector Attempt")
    if (
        identity.source_snapshot_digest != baseline.source_snapshot_digest
        or identity.cell != baseline.cell
        or identity.active_declaration_ids != baseline.active_declaration_ids
        or identity.source_plan_identity != baseline.source_plan_identity
        or identity.evaluation_policy_identity != baseline.evaluation_policy_identity
    ):
        raise ValueError("probe Attempt must share the baseline evaluation context")


def _require_attempt_proposal(
    attempt: Attempt,
    evaluation: StaticPassEvaluation
    | PassEvaluation
    | StaticFailEvaluation
    | TestFailEvaluation
    | IndeterminateEvaluation,
) -> None:
    proposal = evaluation.proposal
    identity = attempt.identity
    if proposal.attempt_id != attempt.attempt_id:
        raise ValueError("probe evaluation proposal must reference its Attempt")
    if proposal.managed_vector != identity.requested_managed_vector:
        raise ValueError("probe Proposal must match its requested exact vector")
    if (
        proposal.snapshot_digest != identity.source_snapshot_digest
        or proposal.cell != identity.cell
        or proposal.policy_identity != identity.evaluation_policy_identity
    ):
        raise ValueError("probe Proposal must match its Attempt context")


class ProbePass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    attempt: Attempt
    proposal_id: str
    evaluation: StaticPassEvaluation | PassEvaluation

    @model_validator(mode="after")
    def validate_evaluation(self) -> "ProbePass":
        if self.attempt.identity.requested_resolution != "exact-vector":
            raise ValueError("probe pass requires an exact-vector Attempt")
        if self.proposal_id != self.evaluation.proposal.proposal_id:
            raise ValueError("probe pass must match its evaluation proposal")
        _require_attempt_proposal(self.attempt, self.evaluation)
        return self

    @property
    def static_evaluation(self) -> StaticPassEvaluation:
        if isinstance(self.evaluation, PassEvaluation):
            return self.evaluation.static
        return self.evaluation


class ProbeRejection(FrozenSchema):
    status: Literal["REJECTED"] = "REJECTED"
    attempt: Attempt
    proposal_id: str | None = None
    failure_id: str
    cause: FailureCause
    evaluation: StaticFailEvaluation | TestFailEvaluation | None = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> "ProbeRejection":
        if self.attempt.identity.requested_resolution != "exact-vector":
            raise ValueError("probe rejection requires an exact-vector Attempt")
        if self.cause in {"STATIC_REGRESSION", "TEST_FAILURE"} and (
            self.evaluation is None
        ):
            raise ValueError(
                "static/test probe rejection requires structured evaluation"
            )
        if self.evaluation is None and self.proposal_id is not None:
            raise ValueError("prepare rejection cannot claim a Proposal")
        if isinstance(self.evaluation, StaticFailEvaluation) and (
            self.cause != "STATIC_REGRESSION"
        ):
            raise ValueError("static probe rejection cause must match its evaluation")
        if isinstance(self.evaluation, TestFailEvaluation) and (
            self.cause != "TEST_FAILURE"
        ):
            raise ValueError("test probe rejection cause must match its evaluation")
        if self.evaluation is not None and self.cause not in {
            "STATIC_REGRESSION",
            "TEST_FAILURE",
        }:
            raise ValueError("prepare rejection cannot retain evaluation evidence")
        if self.evaluation is not None:
            if self.proposal_id != self.evaluation.proposal.proposal_id:
                raise ValueError("probe rejection must match its evaluation proposal")
            _require_attempt_proposal(self.attempt, self.evaluation)
        return self

    @property
    def static_evaluation(self) -> StaticEvaluation | None:
        if isinstance(self.evaluation, TestFailEvaluation):
            return self.evaluation.static
        return self.evaluation


class ProbeIndeterminate(FrozenSchema):
    status: Literal["INDETERMINATE"] = "INDETERMINATE"
    attempt: Attempt
    proposal_id: str | None = None
    failure_id: str
    cause: FailureCause
    evaluation: IndeterminateEvaluation | None = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> "ProbeIndeterminate":
        if self.attempt.identity.requested_resolution != "exact-vector":
            raise ValueError("probe indeterminate requires an exact-vector Attempt")
        if self.evaluation is not None:
            if self.cause != self.evaluation.cause:
                raise ValueError("probe indeterminate cause must match its evaluation")
            if self.proposal_id != self.evaluation.proposal.proposal_id:
                raise ValueError(
                    "probe indeterminate must match its evaluation proposal"
                )
            _require_attempt_proposal(self.attempt, self.evaluation)
        return self

    @property
    def static_evaluation(self) -> None:
        return None


ProbeEvidence = Annotated[
    Union[ProbePass, ProbeRejection, ProbeIndeterminate],
    Field(discriminator="status"),
]


def _require_failure_matches_evidence(
    failure: FailureRecord | None,
    evidence: ProbeRejection | ProbeIndeterminate,
) -> None:
    if (
        failure is None
        or failure.cause != evidence.cause
        or not isinstance(failure.scope, AttemptFailureScope)
        or failure.scope.attempt != evidence.attempt
        or failure.disposition != evidence.status
    ):
        raise ValueError("probe evidence must match its FailureRecord")
    evaluation = evidence.evaluation
    if isinstance(evaluation, StaticFailEvaluation) and (
        failure.stage != "ty" or failure.process != evaluation.ty.process
    ):
        raise ValueError("static rejection diagnosis must match its evaluation")
    if isinstance(evaluation, TestFailEvaluation) and (
        failure.stage != "test" or failure.process != evaluation.test.process
    ):
        raise ValueError("test rejection diagnosis must match its evaluation")
    if isinstance(evaluation, IndeterminateEvaluation) and (
        failure.stage != evaluation.failure.stage
        or failure.process != evaluation.failure.process
    ):
        raise ValueError("indeterminate diagnosis must match its evaluation")


class ProbeObservation(FrozenSchema):
    dependency: str | None
    candidate_version: str | None
    vector: tuple[VersionPin, ...]
    evidence: ProbeEvidence

    @model_validator(mode="after")
    def validate_attempt(self) -> "ProbeObservation":
        identity = self.evidence.attempt.identity
        if identity.requested_resolution == "exact-vector" and (
            identity.requested_managed_vector != self.vector
        ):
            raise ValueError("probe observation vector must match its exact attempt")
        return self


class CoordinateBoundary(FrozenSchema):
    dependency: str
    floor: str
    predecessor: str | None = None
    predecessor_failure_id: str | None = None

    @model_validator(mode="after")
    def validate_predecessor(self) -> "CoordinateBoundary":
        if (self.predecessor is None) != (self.predecessor_failure_id is None):
            raise ValueError("coordinate predecessor requires its failure ID")
        return self


class CoordinateSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    vector: tuple[VersionPin, ...]
    observations: tuple[ProbeObservation, ...]
    boundaries: tuple[CoordinateBoundary, ...]
    sweeps: int

    @model_validator(mode="after")
    def validate_success_evidence(self) -> "CoordinateSuccess":
        if any(
            isinstance(observation.evidence, ProbeIndeterminate)
            for observation in self.observations
        ):
            raise ValueError("coordinate success cannot contain indeterminate evidence")
        for boundary in self.boundaries:
            if boundary.predecessor is None:
                continue
            if not any(
                observation.dependency == boundary.dependency
                and observation.candidate_version == boundary.predecessor
                and isinstance(observation.evidence, ProbeRejection)
                and observation.evidence.failure_id == boundary.predecessor_failure_id
                for observation in self.observations
            ):
                raise ValueError(
                    "coordinate predecessor must reference its rejection observation"
                )
        return self


class CoordinateFailure(FrozenSchema):
    status: Literal[
        "NON_MONOTONIC",
        "NO_PASS_IN_SEARCH_SPACE",
        "INDETERMINATE",
        "NONDETERMINISTIC",
    ]
    dependency: str | None = None
    observations: tuple[ProbeObservation, ...]
    counterexample: tuple[str, str] | None = None
    failure_id: str | None = None

    @model_validator(mode="after")
    def validate_failure_reference(self) -> "CoordinateFailure":
        if self.status == "INDETERMINATE" and self.failure_id is None:
            raise ValueError("indeterminate coordinate search requires a failure ID")
        if self.status == "INDETERMINATE" and not any(
            isinstance(observation.evidence, ProbeIndeterminate)
            and observation.evidence.failure_id == self.failure_id
            for observation in self.observations
        ):
            raise ValueError(
                "indeterminate coordinate search must reference its observation"
            )
        return self


CoordinateOutcome = Annotated[
    Union[CoordinateSuccess, CoordinateFailure],
    Field(discriminator="status"),
]


class CellSuccess(FrozenSchema):
    status: Literal["SUCCESS"] = "SUCCESS"
    cell: Cell
    baseline_attempt: Attempt
    static_baseline: StaticBaseline
    baseline: PassEvaluation
    candidate_snapshots: tuple[CandidateSnapshot, ...]
    static_search: CoordinateSuccess
    dynamic_search: CoordinateSuccess | None = None
    final_vector: tuple[VersionPin, ...]
    final_evaluation: PassEvaluation
    failure_records: tuple[FailureRecord, ...] = ()
    observed_upper: None = None

    @model_validator(mode="after")
    def validate_static_baseline(self) -> "CellSuccess":
        if self.baseline_attempt.identity.requested_resolution != "highest":
            raise ValueError("cell baseline evidence requires a highest Attempt")
        if self.baseline_attempt.identity.cell != self.cell:
            raise ValueError("cell baseline Attempt must match the result cell")
        if self.static_baseline.proposal.attempt_id != self.baseline_attempt.attempt_id:
            raise ValueError("cell baseline Proposal must reference its Attempt")
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
        _require_search_evidence(
            self,
            baseline=self.static_baseline,
            baseline_attempt=self.baseline_attempt,
        )
        final_attempts = {
            observation.evidence.attempt.attempt_id
            for search in _searches_for_cell(self)
            for observation in search.observations
            if observation.evidence.proposal_id
            == self.final_evaluation.proposal.proposal_id
        }
        if self.final_evaluation.proposal.attempt_id not in final_attempts:
            raise ValueError("final Proposal must resolve to a reported probe Attempt")
        self._validate_failure_references()
        return self

    def _validate_failure_references(self) -> None:
        known = self._failure_map()
        referenced = {
            evidence.failure_id
            for search in _searches_for_cell(self)
            for observation in search.observations
            if isinstance(
                (evidence := observation.evidence),
                (ProbeRejection, ProbeIndeterminate),
            )
        }
        referenced.update(
            boundary.predecessor_failure_id
            for search in _searches_for_cell(self)
            if isinstance(search, CoordinateSuccess)
            for boundary in search.boundaries
            if boundary.predecessor_failure_id is not None
        )
        if not referenced <= set(known):
            raise ValueError("cell search references an unknown FailureRecord")
        self._validate_observation_failures(known)

    def _failure_map(self) -> dict[str, FailureRecord]:
        known = {failure.failure_id: failure for failure in self.failure_records}
        if len(known) != len(self.failure_records):
            raise ValueError("cell FailureRecord IDs must be unique")
        if any(_failure_scope_cell(failure) != self.cell for failure in known.values()):
            raise ValueError("cell FailureRecord scope must match its result cell")
        return known

    def _validate_observation_failures(
        self,
        known: dict[str, FailureRecord],
    ) -> None:
        for search in _searches_for_cell(self):
            for observation in search.observations:
                evidence = observation.evidence
                if evidence.attempt.identity.cell != self.cell:
                    raise ValueError("probe attempt must match its result cell")
                if not isinstance(evidence, (ProbeRejection, ProbeIndeterminate)):
                    continue
                _require_failure_matches_evidence(
                    known.get(evidence.failure_id),
                    evidence,
                )


class CellIndeterminate(FrozenSchema):
    status: Literal["CELL_INDETERMINATE"] = "CELL_INDETERMINATE"
    cell: Cell
    phase: str
    failure_id: str
    failure_records: tuple[FailureRecord, ...]
    baseline_attempt: Attempt | None = None
    static_baseline: StaticBaseline | None = None
    baseline: PassEvaluation | None = None
    candidate_snapshots: tuple[CandidateSnapshot, ...] = ()
    coordinate_failure: CoordinateFailure | None = None

    @model_validator(mode="after")
    def validate_indeterminate(self) -> "CellIndeterminate":
        failures = {failure.failure_id: failure for failure in self.failure_records}
        if len(failures) != len(self.failure_records):
            raise ValueError("cell FailureRecord IDs must be unique")
        if any(
            _failure_scope_cell(failure) != self.cell for failure in failures.values()
        ):
            raise ValueError("cell FailureRecord scope must match its result cell")
        terminal = failures.get(self.failure_id)
        if terminal is None or terminal.disposition != "INDETERMINATE":
            raise ValueError("cell indeterminate requires its FailureRecord")
        if self.coordinate_failure is not None and (
            self.coordinate_failure.status == "INDETERMINATE"
            and self.coordinate_failure.failure_id != self.failure_id
        ):
            raise ValueError(
                "cell indeterminate must reference its coordinate terminal failure"
            )
        has_search_evidence = any(
            (
                self.baseline_attempt is not None,
                self.static_baseline is not None,
                self.baseline is not None,
                bool(self.candidate_snapshots),
                self.coordinate_failure is not None,
            )
        )
        if has_search_evidence and (
            self.baseline_attempt is None
            or self.static_baseline is None
            or self.baseline is None
        ):
            raise ValueError("cell search evidence requires its complete PASS baseline")
        if self.static_baseline is not None:
            assert self.baseline_attempt is not None
            assert self.baseline is not None
            if self.baseline_attempt.identity.requested_resolution != "highest":
                raise ValueError("cell baseline evidence requires a highest Attempt")
            if (
                self.static_baseline.proposal.attempt_id
                != self.baseline_attempt.attempt_id
            ):
                raise ValueError("cell baseline Proposal must reference its Attempt")
            if self.static_baseline.proposal.cell != self.cell:
                raise ValueError("cell indeterminate baseline must match its cell")
            if self.static_baseline.proposal != self.baseline.proposal:
                raise ValueError("cell indeterminate baseline must identify V_hi")
            if self.baseline.static.ty != self.static_baseline.ty:
                raise ValueError(
                    "cell indeterminate baseline must reuse the captured V_hi TyCheck"
                )
            _require_evaluation_evidence(
                self.baseline,
                cell=self.cell,
                baseline=self.static_baseline,
            )
            _require_search_evidence(
                self,
                baseline=self.static_baseline,
                baseline_attempt=self.baseline_attempt,
            )
        self._validate_search_failure_references(failures)
        return self

    def _validate_search_failure_references(
        self,
        failures: dict[str, FailureRecord],
    ) -> None:
        for search in _searches_for_cell(self):
            for observation in search.observations:
                evidence = observation.evidence
                if evidence.attempt.identity.cell != self.cell:
                    raise ValueError("probe attempt must match its result cell")
                if isinstance(evidence, (ProbeRejection, ProbeIndeterminate)):
                    _require_failure_matches_evidence(
                        failures.get(evidence.failure_id),
                        evidence,
                    )


class CellSearchFailure(FrozenSchema):
    status: Literal["SEARCH_FAILED"] = "SEARCH_FAILED"
    reason: Literal[
        "NON_MONOTONIC",
        "NONDETERMINISTIC",
        "NO_PASS_IN_SEARCH_SPACE",
    ]
    cell: Cell
    phase: str
    baseline_attempt: Attempt
    static_baseline: StaticBaseline
    baseline: PassEvaluation
    candidate_snapshots: tuple[CandidateSnapshot, ...] = ()
    coordinate_failure: CoordinateFailure | None = None
    failure_records: tuple[FailureRecord, ...] = ()

    @model_validator(mode="after")
    def validate_search_failure(self) -> "CellSearchFailure":
        failures = {failure.failure_id: failure for failure in self.failure_records}
        if len(failures) != len(self.failure_records):
            raise ValueError("cell FailureRecord IDs must be unique")
        if any(
            _failure_scope_cell(failure) != self.cell for failure in failures.values()
        ):
            raise ValueError("cell FailureRecord scope must match its result cell")
        if self.static_baseline.proposal != self.baseline.proposal:
            raise ValueError("search failure baseline must identify V_hi")
        if self.baseline_attempt.identity.requested_resolution != "highest":
            raise ValueError("cell baseline evidence requires a highest Attempt")
        if self.baseline_attempt.identity.cell != self.cell:
            raise ValueError("cell baseline Attempt must match the result cell")
        if self.static_baseline.proposal.attempt_id != self.baseline_attempt.attempt_id:
            raise ValueError("cell baseline Proposal must reference its Attempt")
        if self.coordinate_failure is not None and (
            self.coordinate_failure.status != self.reason
        ):
            raise ValueError("search failure reason must match coordinate outcome")
        _require_evaluation_evidence(
            self.baseline,
            cell=self.cell,
            baseline=self.static_baseline,
        )
        _require_search_evidence(
            self,
            baseline=self.static_baseline,
            baseline_attempt=self.baseline_attempt,
        )
        for search in _searches_for_cell(self):
            for observation in search.observations:
                evidence = observation.evidence
                if evidence.attempt.identity.cell != self.cell:
                    raise ValueError("probe attempt must match its result cell")
                if isinstance(evidence, (ProbeRejection, ProbeIndeterminate)):
                    _require_failure_matches_evidence(
                        failures.get(evidence.failure_id),
                        evidence,
                    )
        return self


CellResult = Annotated[
    Union[
        CellSuccess,
        BaselineRejection,
        BaselineIndeterminate,
        CellIndeterminate,
        CellSearchFailure,
    ],
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
    report_generation_id: str
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
        expected_generation = report_generation_id(
            generator=self.generator,
            package=self.package,
            source_snapshot=self.source_snapshot,
            policy_identity=self.policy_identity,
            requirement_declarations=self.requirement_declarations,
            target_cells=self.target_cells,
        )
        if self.report_generation_id != expected_generation:
            raise ValueError("report generation ID does not match its identity")
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
                raise ValueError(
                    "complete report requires every target cell to succeed"
                )
            if set(result_keys) != set(target_keys):
                raise ValueError("complete report requires exact cell coverage")
            if any(
                not projection.representable for projection in self.projection_evidence
            ):
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
                if (
                    projection is None
                    or {self._cell_key(floor.cell) for floor in projection.floors}
                    != active_keys
                ):
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
                static_baseline.proposal.snapshot_digest != self.source_snapshot.digest
                or static_baseline.proposal.policy_identity != self.policy_identity
            ):
                raise ValueError("static baseline must match report source and policy")
            if static_baseline is not None and (
                static_baseline.proposal.attempt_id is None
            ):
                raise ValueError("public report Proposal must reference an Attempt")
            for search in _searches_for_cell(cell_result):
                for observation in search.observations:
                    evidence = observation.evidence
                    if (
                        isinstance(evidence, ProbeRejection)
                        and evidence.cause == "STATIC_REGRESSION"
                        and not isinstance(evidence.evaluation, StaticFailEvaluation)
                    ):
                        raise ValueError(
                            "static rejection requires structured static evidence"
                        )
        failures = tuple(
            failure
            for result in self.cell_results
            for failure in _failure_records_for_result(result)
        )
        if len({failure.failure_id for failure in failures}) != len(failures):
            raise ValueError("FailureRecord IDs must be unique within one report")
        for failure in failures:
            scope = failure.scope
            scope_cell = (
                scope.attempt.identity.cell
                if isinstance(scope, AttemptFailureScope)
                else scope.cell
            )
            scope_snapshot = (
                scope.attempt.identity.source_snapshot_digest
                if isinstance(scope, AttemptFailureScope)
                else scope.source_snapshot_digest
            )
            scope_policy = (
                scope.attempt.identity.evaluation_policy_identity
                if isinstance(scope, AttemptFailureScope)
                else scope.evaluation_policy_identity
            )
            if (
                scope_cell.package != self.package.name
                or scope_snapshot != self.source_snapshot.digest
                or scope_policy != self.policy_identity
            ):
                raise ValueError("FailureRecord scope must match its report generation")
        return self

    @property
    def failure_records(self) -> tuple[FailureRecord, ...]:
        return tuple(
            failure
            for result in self.cell_results
            for failure in _failure_records_for_result(result)
        )

    @staticmethod
    def _cell_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
        return (cell.package, cell.target, cell.python_minor, cell.extra_surface)


class ProjectEditResult(FrozenSchema):
    changed: bool
    pyproject_path: str
    recovery_log_path: str


def _failure_records_for_result(result: CellResult) -> tuple[FailureRecord, ...]:
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        return (result.failure,)
    return result.failure_records


def report_generation_id(
    *,
    generator: GeneratorIdentity,
    package: PackageIdentity,
    source_snapshot: SourceSnapshotIdentity,
    policy_identity: str,
    requirement_declarations: tuple[RequirementDeclaration, ...],
    target_cells: tuple[Cell, ...],
) -> str:
    identity = {
        "generator": generator.model_dump(mode="json"),
        "package": package.model_dump(mode="json"),
        "source_snapshot": source_snapshot.model_dump(mode="json"),
        "policy_identity": policy_identity,
        "requirement_declarations": [
            declaration.model_dump(mode="json")
            for declaration in requirement_declarations
        ],
        "target_cells": [cell.model_dump(mode="json") for cell in target_cells],
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"pf:report-generation:v1\0" + canonical).hexdigest()

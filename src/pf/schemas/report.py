from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Union

from packaging.version import Version
from pydantic import Field, field_validator, model_validator, model_serializer

from pf.schemas.base import FrozenSchema, canonical_identity_json
from pf.schemas.policy import ExecutionPolicy
from pf.schemas.evaluation import (
    Attempt,
    CoordinateSelectionReason,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    Evaluation,
    FailureCause,
    FailureAuthority,
    ConfiguredVerifierFailureAuthority,
    FailureRecord,
    FailureEvaluationRuntimeRun,
    FailureProcessRuntimeRun,
    FailureRuntimeRun,
    IndeterminateEvaluation,
    NormalExit,
    PassEvaluation,
    SearchFailureEvent,
    VerifierRejectedEvaluation,
    runtime_process_observation,
    failure_process_matches,
)
from pf.schemas.project import (
    SearchPolicyInputs,
    SeriesInventory,
    DependencyGroupKey,
    CandidateSnapshot,
    Candidate,
    Cell,
    InterpreterIdentity,
    RequirementDeclaration,
    ResolvedNode,
    SelectedCandidate,
    SourceSnapshotIdentity,
    SourceIdentity,
    SourcePlan,
    VersionPin,
    cell_identity,
    is_canonical_distribution_name,
    public_relative_path,
    selected_candidate_evidence_digest,
)


def _require_proposal_scope(
    evaluation: Evaluation,
    *,
    cell: Cell,
    baseline: PassEvaluation,
) -> None:
    proposal = evaluation.proposal
    if (
        proposal.cell != cell
        or proposal.snapshot_digest != baseline.proposal.snapshot_digest
        or proposal.policy_identity != baseline.proposal.policy_identity
    ):
        raise ValueError(
            "evaluation must match the cell, snapshot, and policy of its highest baseline"
        )


def _require_evaluation_evidence(
    evaluation: Evaluation,
    *,
    cell: Cell,
    baseline: PassEvaluation,
) -> None:
    _require_proposal_scope(evaluation, cell=cell, baseline=baseline)


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
        return (result.search,)
    if isinstance(result, (CellIndeterminate, CellSearchFailure)) and (
        result.coordinate_failure is not None
    ):
        return (result.coordinate_failure,)
    return ()


def _require_search_evidence(
    result: "CellSuccess | CellIndeterminate | CellSearchFailure",
    *,
    baseline: PassEvaluation,
    baseline_attempt: Attempt,
) -> None:
    snapshots = {
        snapshot.dependency: snapshot for snapshot in result.candidate_snapshots
    }
    baseline_vector = {
        pin.name: pin.version for pin in baseline.proposal.managed_vector
    }
    for dependency, snapshot in snapshots.items():
        if (
            snapshot.cell != result.cell
            or baseline_vector.get(dependency)
            != snapshot.baseline_selection.version
        ):
            raise ValueError(
                "candidate baseline selection must match its Cell baseline"
            )
    for search in _searches_for_cell(result):
        if isinstance(search, CoordinateSuccess):
            for boundary in search.boundaries:
                snapshot = snapshots.get(boundary.dependency)
                if snapshot is None:
                    raise ValueError(
                        "coordinate boundary must match a frozen CandidateSnapshot"
                    )
                order = tuple(candidate.version for candidate in snapshot.candidates)
                try:
                    index = order.index(boundary.floor)
                except ValueError as error:
                    raise ValueError(
                        "coordinate floor must belong to its CandidateSnapshot"
                    ) from error
                expected_predecessor = order[index - 1] if index else None
                if boundary.predecessor != expected_predecessor:
                    raise ValueError(
                        "coordinate boundary must use the exact frozen predecessor"
                    )
        for observation in search.observations:
            evidence = observation.evidence
            if (
                isinstance(evidence, ProbePass)
                and evidence.attempt.identity.requested_resolution == "highest"
            ):
                if (
                    evidence.attempt != baseline_attempt
                    or evidence.evaluation != result.baseline
                    or observation.vector
                    != evidence.evaluation.proposal.managed_vector
                ):
                    raise ValueError(
                        "highest probe PASS must be the Cell's verified baseline"
                    )
            else:
                _require_shared_evaluation_context(
                    evidence.attempt,
                    baseline_attempt=baseline_attempt,
                )
            identity = evidence.attempt.identity
            if identity.requested_resolution == "exact-vector":
                try:
                    vector = {pin.name: pin.version for pin in observation.vector}
                    if set(vector) != set(snapshots):
                        raise ValueError(
                            "probe vector and CandidateSnapshots must cover one domain"
                        )
                    selected = tuple(
                        snapshots[name].select(vector[name])
                        for name in sorted(snapshots)
                    )
                except (KeyError, ValueError) as error:
                    raise ValueError(
                        "probe Attempt vector must use frozen candidate selections"
                    ) from error
                if (
                    identity.selected_candidate_evidence_digest
                    != selected_candidate_evidence_digest(selected)
                ):
                    raise ValueError(
                        "probe Attempt selected candidate evidence must match its vector"
                    )
            if isinstance(evidence, ProbePass) and not isinstance(
                evidence.evaluation, PassEvaluation
            ):
                raise ValueError("runtime-backed probe PASS requires full evaluation")
            if (
                isinstance(evidence, ProbeRejection)
                and evidence.cause
                in {"VERIFIER_EXITED_NONZERO"}
                and evidence.evaluation is None
            ):
                raise ValueError(
                    "reported runtime/test rejection requires structured evaluation"
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
        or identity.execution_policy_identity != baseline.execution_policy_identity
    ):
        raise ValueError("probe Attempt must share the baseline evaluation context")


def _require_attempt_proposal(
    attempt: Attempt,
    evaluation: Evaluation,
) -> None:
    proposal = evaluation.proposal
    identity = attempt.identity
    if proposal.attempt_id != attempt.attempt_id:
        raise ValueError("probe evaluation proposal must reference its Attempt")
    if (
        identity.requested_resolution == "exact-vector"
        and proposal.managed_vector != identity.requested_managed_vector
    ):
        raise ValueError("probe Proposal must match its requested exact vector")
    if identity.requested_resolution not in {"exact-vector", "highest"}:
        raise ValueError("probe Proposal requires a direct evaluation request")
    if (
        proposal.snapshot_digest != identity.source_snapshot_digest
        or proposal.cell != identity.cell
        or proposal.policy_identity != identity.execution_policy_identity
    ):
        raise ValueError("probe Proposal must match its Attempt context")


class ProbePass(FrozenSchema):
    status: Literal["PASS"] = "PASS"
    attempt: Attempt
    proposal_id: str
    evaluation: PassEvaluation

    @model_validator(mode="after")
    def validate_evaluation(self) -> "ProbePass":
        if self.attempt.identity.requested_resolution not in {
            "exact-vector",
            "highest",
        }:
            raise ValueError(
                "probe pass requires an exact-vector or highest Attempt"
            )
        if self.proposal_id != self.evaluation.proposal.proposal_id:
            raise ValueError("probe pass must match its evaluation proposal")
        _require_attempt_proposal(self.attempt, self.evaluation)
        return self



class ProbeRejection(FrozenSchema):
    status: Literal["REJECTED"] = "REJECTED"
    attempt: Attempt
    proposal_id: str | None = None
    failure_id: str
    cause: FailureCause
    evaluation: (
        VerifierRejectedEvaluation | None
    ) = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> "ProbeRejection":
        if self.attempt.identity.requested_resolution != "exact-vector":
            raise ValueError("probe rejection requires an exact-vector Attempt")
        if self.cause in {
            "VERIFIER_EXITED_NONZERO",
        } and (
            self.evaluation is None
        ):
            raise ValueError(
                "runtime/test probe rejection requires structured evaluation"
            )
        if self.evaluation is None and self.proposal_id is not None:
            raise ValueError("prepare rejection cannot claim a Proposal")
        if isinstance(self.evaluation, VerifierRejectedEvaluation) and (
            self.cause != "VERIFIER_EXITED_NONZERO"
        ):
            raise ValueError("test probe rejection cause must match its evaluation")
        if self.evaluation is not None and self.cause not in {
            "VERIFIER_EXITED_NONZERO",
        }:
            raise ValueError("prepare rejection cannot retain evaluation evidence")
        if self.evaluation is not None:
            if self.proposal_id != self.evaluation.proposal.proposal_id:
                raise ValueError("probe rejection must match its evaluation proposal")
            _require_attempt_proposal(self.attempt, self.evaluation)
        return self



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
    if isinstance(evaluation, VerifierRejectedEvaluation):
        authority = failure.authority
        if not (
            failure.stage == "test"
            and isinstance(authority, ConfiguredVerifierFailureAuthority)
            and authority.terminal == evaluation.verifier.terminal
        ):
            raise ValueError("verifier rejection diagnosis must match its evaluation")
    if isinstance(evaluation, IndeterminateEvaluation):
        authority = failure.authority
        matches = (
            failure.stage == "test"
            and isinstance(authority, ConfiguredVerifierFailureAuthority)
            and authority.terminal == evaluation.verifier.terminal
        )
        if not matches:
            raise ValueError("indeterminate diagnosis must match its evaluation")


def _validate_failure_runtime_runs(
    cell: Cell,
    failures: dict[str, FailureRecord],
    runs: tuple[FailureRuntimeRun, ...],
) -> None:
    by_failure = {item.failure_id: item for item in runs}
    if len(by_failure) != len(runs):
        raise ValueError("cell failure runtime IDs must be unique")
    for failure_id, item in by_failure.items():
        failure = failures.get(failure_id)
        if failure is None:
            raise ValueError("cell runtime diagnostics require their FailureRecord")
        if isinstance(item, FailureProcessRuntimeRun):
            if not failure_process_matches(failure, item.process):
                raise ValueError("failure process sidecar must match its authority")
            continue
        assert isinstance(item, FailureEvaluationRuntimeRun)
        evaluation = item.runtime.evaluation
        if isinstance(evaluation, PassEvaluation):
            raise ValueError("failure runtime diagnostics cannot retain a PASS")
        SearchFailureEvent(
            cell=cell,
            failure=failure,
            evaluation=evaluation,
            runtime=item.runtime,
        )


class ProbeObservation(FrozenSchema):
    dependency: str | None
    candidate_version: str | None
    vector: tuple[VersionPin, ...]
    evidence: ProbeEvidence
    selection_reason: CoordinateSelectionReason | None

    @model_validator(mode="after")
    def validate_attempt(self) -> "ProbeObservation":
        identity = self.evidence.attempt.identity
        if identity.requested_resolution == "exact-vector" and (
            identity.requested_managed_vector != self.vector
        ):
            raise ValueError("probe observation vector must match its exact attempt")
        if identity.requested_resolution == "highest" and (
            not isinstance(self.evidence, ProbePass)
            or self.evidence.evaluation.proposal.managed_vector != self.vector
        ):
            raise ValueError(
                "highest probe observation vector must match its PASS Proposal"
            )
        if (self.dependency is None) != (self.candidate_version is None):
            raise ValueError(
                "probe observation dependency and candidate version must be paired"
            )
        if self.dependency is not None and not any(
            pin.name == self.dependency and pin.version == self.candidate_version
            for pin in self.vector
        ):
            raise ValueError(
                "probe observation candidate must match its vector coordinate"
            )
        if (self.dependency is None) != (self.selection_reason is None):
            raise ValueError(
                "probe observation selection reason must be null exactly when "
                "dependency is absent"
            )
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
        vector = {pin.name: pin.version for pin in self.vector}
        boundaries = {
            boundary.dependency: boundary.floor for boundary in self.boundaries
        }
        if len(vector) != len(self.vector) or len(boundaries) != len(self.boundaries):
            raise ValueError("coordinate result dependencies must be unique")
        if boundaries != vector:
            raise ValueError("coordinate boundaries must match the committed vector")
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
                and observation.vector
                == tuple(
                    VersionPin(
                        name=pin.name,
                        version=(
                            boundary.predecessor
                            if pin.name == boundary.dependency
                            else pin.version
                        ),
                    )
                    for pin in self.vector
                )
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
        if self.status == "NON_MONOTONIC":
            if self.dependency is None or self.counterexample is None:
                raise ValueError(
                    "non-monotonic coordinate search requires its counterexample"
                )
            low, high = self.counterexample
            if Version(low) >= Version(high):
                raise ValueError("non-monotonic counterexample must be ordered")
            lows = tuple(
                observation
                for observation in self.observations
                if observation.dependency == self.dependency
                and observation.candidate_version == low
                and isinstance(observation.evidence, ProbePass)
            )
            highs = tuple(
                observation
                for observation in self.observations
                if observation.dependency == self.dependency
                and observation.candidate_version == high
                and isinstance(observation.evidence, ProbeRejection)
            )
            if not any(
                tuple(
                    pin for pin in low_observation.vector if pin.name != self.dependency
                )
                == tuple(
                    pin
                    for pin in high_observation.vector
                    if pin.name != self.dependency
                )
                for low_observation in lows
                for high_observation in highs
            ):
                raise ValueError(
                    "non-monotonic counterexample requires direct evidence in one Slice"
                )
        elif self.counterexample is not None:
            raise ValueError(
                "only non-monotonic coordinate search can retain a counterexample"
            )
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
        if self.status != "INDETERMINATE" and self.failure_id is not None:
            raise ValueError(
                "only indeterminate coordinate search can retain a terminal failure"
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
    baseline: PassEvaluation
    candidate_snapshots: tuple[CandidateSnapshot, ...]
    search: CoordinateSuccess
    final_vector: tuple[VersionPin, ...]
    final_evaluation: PassEvaluation
    failure_records: tuple[FailureRecord, ...] = ()
    failure_runtime_runs: tuple[FailureRuntimeRun, ...] = Field(
        default=(), exclude=True
    )
    observed_upper: None = None

    @model_validator(mode="after")
    def validate_baseline(self) -> "CellSuccess":
        if self.baseline_attempt.identity.requested_resolution != "highest":
            raise ValueError("cell baseline evidence requires a highest Attempt")
        if self.baseline_attempt.identity.cell != self.cell:
            raise ValueError("cell baseline Attempt must match the result cell")
        if self.baseline.proposal.attempt_id != self.baseline_attempt.attempt_id:
            raise ValueError("cell baseline Proposal must reference its Attempt")
        if self.baseline.proposal.cell != self.cell:
            raise ValueError("cell baseline must match the result cell")

        _require_evaluation_evidence(
            self.baseline,
            cell=self.cell,
            baseline=self.baseline,
        )
        _require_evaluation_evidence(
            self.final_evaluation,
            cell=self.cell,
            baseline=self.baseline,
        )
        _require_search_evidence(
            self,
            baseline=self.baseline,
            baseline_attempt=self.baseline_attempt,
        )
        final_attempts = {
            observation.evidence.attempt.attempt_id
            for search in _searches_for_cell(self)
            for observation in search.observations
            if observation.evidence.proposal_id
            == self.final_evaluation.proposal.proposal_id
        }
        if (
            self.final_evaluation != self.baseline
            and self.final_evaluation.proposal.attempt_id not in final_attempts
        ):
            raise ValueError("final Proposal must resolve to a reported probe Attempt")
        self._validate_final_authority()
        self._validate_failure_references()
        return self

    def _validate_final_authority(self) -> None:
        terminal = self.search
        names = tuple(pin.name for pin in self.final_vector)
        if names != tuple(sorted(set(names))):
            raise ValueError("final vector dependency names must be unique and sorted")
        if self.final_vector != terminal.vector:
            raise ValueError("final vector must equal the terminal search vector")
        if self.final_vector != self.final_evaluation.proposal.managed_vector:
            raise ValueError("final vector must equal the PASS Proposal managed vector")
        if (
            self.final_vector == self.baseline.proposal.managed_vector
            and self.final_evaluation != self.baseline
        ):
            raise ValueError(
                "a baseline final vector must reuse its highest PASS evaluation"
            )
        if self.final_evaluation != self.baseline:
            final_pass = next(
                (
                    observation
                    for observation in terminal.observations
                    if isinstance(observation.evidence, ProbePass)
                    and observation.evidence.proposal_id
                    == self.final_evaluation.proposal.proposal_id
                ),
                None,
            )
            if final_pass is None:
                raise ValueError("terminal search must include the final ProbePass")
            if final_pass.vector != self.final_vector:
                raise ValueError(
                    "final ProbePass observation vector must match final vector"
                )
            if (
                final_pass.evidence.attempt.identity.requested_managed_vector
                != self.final_vector
            ):
                raise ValueError(
                    "final ProbePass Attempt vector must match final vector"
                )
            if (
                final_pass.evidence.attempt.attempt_id
                != self.final_evaluation.proposal.attempt_id
            ):
                raise ValueError("final ProbePass Attempt must own the PASS Proposal")
        snapshots = {
            snapshot.dependency: snapshot for snapshot in self.candidate_snapshots
        }
        if len(snapshots) != len(self.candidate_snapshots):
            raise ValueError("CandidateSnapshot dependencies must be unique")
        if names != tuple(sorted(snapshots)):
            raise ValueError(
                "final vector dependencies must match CandidateSnapshot names"
            )
        for pin in self.final_vector:
            matches = tuple(
                candidate
                for candidate in snapshots[pin.name].candidates
                if candidate.version == pin.version
            )
            if len(matches) != 1:
                raise ValueError(
                    "final vector must uniquely select CandidateSnapshot evidence"
                )
            SelectedCandidate(
                dependency=pin.name,
                version=pin.version,
                artifact=matches[0].artifact,
            )

    def _validate_failure_references(self) -> None:
        known = self._failure_map()
        _validate_failure_runtime_runs(self.cell, known, self.failure_runtime_runs)
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
    baseline: PassEvaluation | None = None
    candidate_snapshots: tuple[CandidateSnapshot, ...] = ()
    coordinate_failure: CoordinateFailure | None = None
    failure_runtime_runs: tuple[FailureRuntimeRun, ...] = Field(
        default=(), exclude=True
    )

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
        _validate_failure_runtime_runs(self.cell, failures, self.failure_runtime_runs)
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
                self.baseline is not None,
                bool(self.candidate_snapshots),
                self.coordinate_failure is not None,
            )
        )
        if has_search_evidence and (
            self.baseline_attempt is None
            or self.baseline is None
        ):
            raise ValueError("cell search evidence requires its complete PASS baseline")
        if self.baseline is not None:
            assert self.baseline_attempt is not None
            assert self.baseline is not None
            if self.baseline_attempt.identity.requested_resolution != "highest":
                raise ValueError("cell baseline evidence requires a highest Attempt")
            if (
                self.baseline.proposal.attempt_id
                != self.baseline_attempt.attempt_id
            ):
                raise ValueError("cell baseline Proposal must reference its Attempt")
            if self.baseline.proposal.cell != self.cell:
                raise ValueError("cell indeterminate baseline must match its cell")

            _require_evaluation_evidence(
                self.baseline,
                cell=self.cell,
                baseline=self.baseline,
            )
            _require_search_evidence(
                self,
                baseline=self.baseline,
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
    baseline: PassEvaluation
    candidate_snapshots: tuple[CandidateSnapshot, ...] = ()
    coordinate_failure: CoordinateFailure | None = None
    failure_records: tuple[FailureRecord, ...] = ()
    failure_runtime_runs: tuple[FailureRuntimeRun, ...] = Field(
        default=(), exclude=True
    )

    @model_validator(mode="after")
    def validate_search_failure(self) -> "CellSearchFailure":
        failures = {failure.failure_id: failure for failure in self.failure_records}
        if len(failures) != len(self.failure_records):
            raise ValueError("cell FailureRecord IDs must be unique")
        if any(
            _failure_scope_cell(failure) != self.cell for failure in failures.values()
        ):
            raise ValueError("cell FailureRecord scope must match its result cell")
        _validate_failure_runtime_runs(self.cell, failures, self.failure_runtime_runs)
        if self.baseline_attempt.identity.requested_resolution != "highest":
            raise ValueError("cell baseline evidence requires a highest Attempt")
        if self.baseline_attempt.identity.cell != self.cell:
            raise ValueError("cell baseline Attempt must match the result cell")
        if self.baseline.proposal.attempt_id != self.baseline_attempt.attempt_id:
            raise ValueError("cell baseline Proposal must reference its Attempt")
        if self.coordinate_failure is not None and (
            self.coordinate_failure.status != self.reason
        ):
            raise ValueError("search failure reason must match coordinate outcome")

        _require_evaluation_evidence(
            self.baseline,
            cell=self.cell,
            baseline=self.baseline,
        )
        _require_search_evidence(
            self,
            baseline=self.baseline,
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
    requires_python: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not is_canonical_distribution_name(value):
            raise ValueError("package name must be a canonical distribution name")
        return value

    @field_validator("pyproject_path")
    @classmethod
    def validate_pyproject_path(cls, value: str) -> str:
        return public_relative_path(value)


class FloorProjection(FrozenSchema):
    cell: Cell
    version: str


class ProjectionEvidence(FrozenSchema):
    declaration_id: str
    floors: tuple[FloorProjection, ...]
    projected_requirements: tuple[str, ...]
    representable: bool


class DependencyGroupProjection(FrozenSchema):
    key: DependencyGroupKey
    floors: tuple[FloorProjection, ...]
    original_requirements: tuple[str, ...]
    projected_requirements: tuple[str, ...]
    representable: bool


class CompleteReportResult(FrozenSchema):
    status: Literal["complete"]


class IncompleteReportResult(FrozenSchema):
    status: Literal["incomplete"]
    reasons: tuple[str, ...]


ReportResult = Annotated[
    Union[CompleteReportResult, IncompleteReportResult],
    Field(discriminator="status"),
]


class ReportIdentityV1(FrozenSchema):
    report_generation_id: str
    generator: GeneratorIdentity
    package: PackageIdentity
    source_snapshot: SourceSnapshotIdentity
    policy_identity: str
    guidance_policy_identity: str
    search_derivation_identity: str
    execution_policy: ExecutionPolicy
    verifier_outcome_policy: Literal["configured-verifier-terminal-v1"]
    failure_policy: Literal["failure-execution-v4"]


class TargetCellV1(FrozenSchema):
    cell_id: str
    package: str
    target: str
    python_minor: str
    extra_surface: tuple[str, ...]
    active_declaration_refs: tuple[str, ...]


class CandidateSnapshotV1(FrozenSchema):
    candidate_snapshot_id: str
    dependency: str
    cell_ref: str
    policy_identity: str
    source_plan_identity: str
    source: SourceIdentity
    baseline_selection: SelectedCandidate
    candidates: tuple[Candidate, ...]
    series_representatives: tuple[tuple[str, str], ...]
    series_inventory_ref: str | None = Field(
        json_schema_extra={"x-pf-preserve-null": True}
    )

    @model_serializer(mode="wrap")
    def serialize_required_null(self, handler):
        result = handler(self)
        result["series_inventory_ref"] = self.series_inventory_ref
        return result


class SeriesInventoryV1(FrozenSchema):
    series_inventory_id: str
    dependency: str
    source: SourceIdentity
    family: Literal["majors", "minors"]
    series_keys: tuple[tuple[int, ...], ...]

    def expand(self) -> SeriesInventory:
        return SeriesInventory(
            dependency=self.dependency,
            source=self.source,
            family=self.family,
            series_keys=self.series_keys,
        )

    @model_validator(mode="after")
    def validate_identity(self) -> "SeriesInventoryV1":
        if self.series_inventory_id != self.expand().inventory_id:
            raise ValueError("series inventory identity mismatch")
        return self


class ReportInputsV1(FrozenSchema):
    search_policy: SearchPolicyInputs
    series_inventories: tuple[SeriesInventoryV1, ...]
    source_plan: SourcePlan
    requirement_declarations: tuple[RequirementDeclaration, ...]
    target_cells: tuple[TargetCellV1, ...]
    candidate_snapshots: tuple[CandidateSnapshotV1, ...]


class CellFailureScopeV1(FrozenSchema):
    kind: Literal["cell"]
    cell_ref: str


class AttemptFailureScopeV1(FrozenSchema):
    kind: Literal["attempt"]
    attempt_ref: str


FailureScopeV1 = Annotated[
    Union[CellFailureScopeV1, AttemptFailureScopeV1],
    Field(discriminator="kind"),
]


class FailureRecordV1(FrozenSchema):
    failure_id: str
    scope: FailureScopeV1
    disposition: Literal["REJECTED", "INDETERMINATE"]
    cause: FailureCause
    stage: str
    authority: FailureAuthority
    project_plan_digest: str | None = None
    environment_plan_digest: str | None = None


class AttemptV1(FrozenSchema):
    attempt_id: str
    cell_ref: str
    requested_resolution: Literal["highest", "lowest-direct", "exact-vector"]
    requested_managed_vector: tuple[VersionPin, ...] | None = None
    source_plan_identity: str
    resolution_context_digest: str
    harness_policy_identity: Literal["original-harness-v1", "harness-relaxation-v1"]
    harness_declaration_ids: tuple[str, ...]
    harness_baseline_digest: str | None = None
    selected_candidate_evidence_digest: str | None = None


class ResolutionGraphV1(FrozenSchema):
    resolution_graph_id: str
    nodes: tuple[ResolvedNode, ...]


class ProposalV1(FrozenSchema):
    proposal_id: str
    attempt_ref: str
    managed_vector: tuple[VersionPin, ...]
    fixed_declaration_refs: tuple[str, ...]
    resolution_graph_ref: str
    project_plan_digest: str
    environment_plan_digest: str | None = Field(
        json_schema_extra={"x-pf-preserve-null": True}
    )
    interpreter: InterpreterIdentity

    @model_serializer(mode="wrap")
    def serialize_required_null(self, handler):
        result = handler(self)
        result["environment_plan_digest"] = self.environment_plan_digest
        return result


class PassEvaluationV1(FrozenSchema):
    proposal_ref: str
    status: Literal["PASS"]
    terminal: NormalExit


class VerifierRejectedEvaluationV1(FrozenSchema):
    proposal_ref: str
    status: Literal["VERIFIER_REJECTED"]
    failure_ref: str


class IndeterminateEvaluationV1(FrozenSchema):
    proposal_ref: str
    status: Literal["INDETERMINATE"]
    failure_ref: str


TerminalEvaluationV1 = Annotated[
    Union[
        PassEvaluationV1,
        VerifierRejectedEvaluationV1,
        IndeterminateEvaluationV1,
    ],
    Field(discriminator="status"),
]


class ReportEvidenceV1(FrozenSchema):
    resolution_graphs: tuple[ResolutionGraphV1, ...]
    attempts: tuple[AttemptV1, ...]
    proposals: tuple[ProposalV1, ...]
    evaluations: tuple[TerminalEvaluationV1, ...]
    failures: tuple[FailureRecordV1, ...]


class BaselineRefsV1(FrozenSchema):
    attempt_ref: str
    proposal_ref: str


class DirectPassV1(FrozenSchema):
    kind: Literal["DIRECT"]
    attempt_ref: str
    status: Literal["PASS"]


class DirectRejectionV1(FrozenSchema):
    kind: Literal["DIRECT"]
    attempt_ref: str
    status: Literal["REJECTED"]
    failure_ref: str


class DirectIndeterminateV1(FrozenSchema):
    kind: Literal["DIRECT"]
    attempt_ref: str
    status: Literal["INDETERMINATE"]
    failure_ref: str


DirectEvidenceV1 = Annotated[
    Union[DirectPassV1, DirectRejectionV1, DirectIndeterminateV1],
    Field(discriminator="status"),
]


class ProbeObservationV1(FrozenSchema):
    dependency: str | None = None
    candidate_version: str | None = None
    evidence: DirectEvidenceV1
    selection_reason: CoordinateSelectionReason | None = Field(
        json_schema_extra={"x-pf-preserve-null": True},
    )

    @model_validator(mode="after")
    def validate_selection_reason(self) -> ProbeObservationV1:
        if (self.dependency is None) != (self.selection_reason is None):
            raise ValueError(
                "probe observation selection reason must be null exactly when "
                "dependency is absent"
            )
        return self

    @model_serializer(mode="wrap")
    def emit_required_nullable_selection_reason(self, handler):
        result = handler(self)
        result["selection_reason"] = self.selection_reason
        return result


class CoordinateBoundaryV1(FrozenSchema):
    dependency: str
    floor: str
    predecessor: str | None = None
    predecessor_failure_ref: str | None = None


class CoordinateSuccessV1(FrozenSchema):
    status: Literal["SUCCESS"]
    observations: tuple[ProbeObservationV1, ...]
    boundaries: tuple[CoordinateBoundaryV1, ...]
    sweeps: int


class CoordinateFailureV1(FrozenSchema):
    status: Literal[
        "NON_MONOTONIC",
        "NO_PASS_IN_SEARCH_SPACE",
        "INDETERMINATE",
        "NONDETERMINISTIC",
    ]
    dependency: str | None = None
    observations: tuple[ProbeObservationV1, ...]
    counterexample: tuple[str, str] | None = None
    failure_ref: str | None = None


class CellIndeterminateV1(FrozenSchema):
    status: Literal["CELL_INDETERMINATE"]
    cell_ref: str
    phase: str
    failure_ref: str
    failure_refs: tuple[str, ...]
    baseline: BaselineRefsV1 | None = None
    candidate_snapshot_refs: tuple[str, ...] | None = None
    coordinate_failure: CoordinateFailureV1 | None = None


class BaselineRejectionV1(FrozenSchema):
    status: Literal["BASELINE_REJECTION"]
    cell_ref: str
    attempt_ref: str
    failure_refs: tuple[str, ...]
    proposal_ref: str | None = None


class BaselineIndeterminateV1(FrozenSchema):
    status: Literal["BASELINE_INDETERMINATE"]
    cell_ref: str
    attempt_ref: str
    failure_refs: tuple[str, ...]
    proposal_ref: str | None = None


class CellSuccessV1(FrozenSchema):
    status: Literal["SUCCESS"]
    cell_ref: str
    baseline: BaselineRefsV1
    candidate_snapshot_refs: tuple[str, ...]
    search: CoordinateSuccessV1
    final_proposal_ref: str
    failure_refs: tuple[str, ...]


class CellSearchFailureV1(FrozenSchema):
    status: Literal["SEARCH_FAILED"]
    cell_ref: str
    phase: str
    reason: Literal[
        "NON_MONOTONIC",
        "NONDETERMINISTIC",
        "NO_PASS_IN_SEARCH_SPACE",
    ]
    baseline: BaselineRefsV1
    candidate_snapshot_refs: tuple[str, ...]
    coordinate_failure: CoordinateFailureV1 | None = None
    failure_refs: tuple[str, ...]


class FloorProjectionV1(FrozenSchema):
    cell_ref: str
    version: str


class ProjectionEvidenceV1(FrozenSchema):
    declaration_ref: str
    floors: tuple[FloorProjectionV1, ...]
    projected_requirements: tuple[str, ...]
    representable: bool


CellResultV1 = Annotated[
    Union[
        CellSuccessV1,
        CellIndeterminateV1,
        BaselineRejectionV1,
        BaselineIndeterminateV1,
        CellSearchFailureV1,
    ],
    Field(discriminator="status"),
]


class PackageFloorReportV1Wire(FrozenSchema):
    schema_version: Literal[1]
    identity: ReportIdentityV1
    inputs: ReportInputsV1
    evidence: ReportEvidenceV1
    cell_results: tuple[CellResultV1, ...]
    projections: tuple[ProjectionEvidenceV1, ...]
    result: ReportResult


class ProjectEditResult(FrozenSchema):
    changed: bool
    pyproject_path: str
    recovery_log_path: str


def failure_records_for_result(result: CellResult) -> tuple[FailureRecord, ...]:
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        return (result.failure,)
    return result.failure_records


def failure_runtime_runs_for_result(
    result: CellResult,
) -> tuple[FailureRuntimeRun, ...]:
    if isinstance(result, (CellSuccess, CellIndeterminate, CellSearchFailure)):
        return result.failure_runtime_runs
    if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
        if (
            result.runtime is not None
            and runtime_process_observation(result.runtime) is not None
        ):
            return (
                FailureEvaluationRuntimeRun(
                    failure_id=result.failure.failure_id,
                    runtime=result.runtime,
                ),
            )
        if result.failure_process is not None:
            return (
                FailureProcessRuntimeRun(
                    failure_id=result.failure.failure_id,
                    process=result.failure_process,
                ),
            )
    return ()


def report_generation_id(
    *,
    generator: GeneratorIdentity,
    package: PackageIdentity,
    source_snapshot: SourceSnapshotIdentity,
    policy_identity: str,
    execution_policy_identity: str,
    guidance_policy_identity: str,
    search_derivation_identity: str,
    verifier_outcome_policy: Literal["configured-verifier-terminal-v1"],
    source_plan: SourcePlan,
    requirement_declarations: tuple[RequirementDeclaration, ...],
    target_cells: tuple[Cell, ...],
    search_policy: SearchPolicyInputs,
) -> str:
    declarations = tuple(
        sorted(
            requirement_declarations,
            key=lambda declaration: declaration.declaration_id,
        )
    )
    cells = tuple(sorted(target_cells, key=cell_identity))
    identity = {
        "generator": generator.model_dump(mode="json"),
        "package": package.model_dump(mode="json"),
        "source_snapshot": source_snapshot.model_dump(mode="json"),
        "policy_identity": policy_identity,
        "execution_policy_identity": execution_policy_identity,
        "guidance_policy_identity": guidance_policy_identity,
        "search_derivation_identity": search_derivation_identity,
        "verifier_outcome_policy": verifier_outcome_policy,
        "failure_policy": "failure-execution-v4",
        "source_plan": source_plan.model_dump(mode="json"),
        "requirement_declarations": [
            declaration.model_dump(mode="json") for declaration in declarations
        ],
        "target_cells": [cell.model_dump(mode="json") for cell in cells],
        "search_policy": search_policy.model_dump(mode="json"),
    }
    return hashlib.sha256(
        b"pf:report-generation:v1\0" + canonical_identity_json(identity)
    ).hexdigest()

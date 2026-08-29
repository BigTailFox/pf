from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
from typing import Literal
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.version import Version

from pydantic import ValidationError

from pf.errors import ConfigurationError
from pf import __version__
from pf.policy import evaluation_policy_identity
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellFailureScope,
    ConfiguredVerifierFailureAuthority,
    FailureRecord,
    IndeterminateEvaluation,
    NormalExit,
    PassEvaluation,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessAttempt,
    RuntimeWitnessResult,
    StaticBaseline,
    StaticRegressionEvaluation,
    StaticUnchangedEvaluation,
    Signaled,
    StartFailed,
    TimedOut,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
    ToolFailure,
)
from pf.resolution import environment_identity_digest, resolution_graph_id
from pf.project import marker_applies, marker_platform
from pf.policy import CONFIGURED_VERIFIER_OUTCOME_POLICY
from pf.schemas.project import (
    ApplySelector,
    AvailableArtifact,
    CandidateSnapshot,
    Cell,
    PackagePlan,
    Proposal,
    RequirementDeclaration,
    SourcePlan,
    SourceIdentity,
    SourceSnapshotIdentity,
    cell_id,
    cell_identity,
    dependency_group_key,
    public_locator,
    source_snapshot_digest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellIndeterminateV1,
    CellFailureScopeV1,
    AttemptFailureScopeV1,
    AttemptV1,
    BaselineRefsV1,
    BaselineIndeterminateV1,
    BaselineRejectionV1,
    CellSuccessV1,
    CoordinateFailure,
    CoordinateFailureV1,
    CoordinateSuccessV1,
    CoordinateBoundary,
    CoordinateSuccess,
    CoordinateBoundaryV1,
    DirectPassV1,
    DirectIndeterminateV1,
    DirectRejectionV1,
    ProbeObservationV1,
    ProbePass,
    ProbeIndeterminate,
    ProbeRejection,
    ProbeObservation,
    StaticOnlyEvidence,
    StaticOnlyEvidenceV1,
    StaticRegion,
    StaticRegionSlice,
    StaticRegionV1,
    StaticRegionRuntimeReference,
    StaticRegionRuntimeReferenceV1,
    CellResult,
    CellSearchFailure,
    CellSearchFailureV1,
    CellSuccess,
    CompleteReportResult,
    DependencyGroupProjection,
    FloorProjection,
    FloorProjectionV1,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageIdentity,
    PackageFloorReportV1Wire,
    ProjectionEvidence,
    ProjectionEvidenceV1,
    FailureRecordV1,
    ReportEvidenceV1,
    ReportIdentityV1,
    ReportInputsV1,
    TargetCellV1,
    PassEvaluationV1,
    IndeterminateEvaluationV1,
    RuntimeInterfaceMissingEvaluationV1,
    RuntimeWitnessAttemptV1,
    RuntimeWitnessPositiveV1,
    RuntimeWitnessTerminalV1,
    ProposalV1,
    ResolutionGraphV1,
    StaticUnchangedEvaluationV1,
    StaticRegressionEvaluationV1,
    VerifierRejectedEvaluationV1,
    CandidateSnapshotV1,
    failure_records_for_result,
    report_generation_id,
    static_region_id,
)

CellKey = tuple[str, str, str, tuple[str, ...]]


def _same_explicit_json(left: object, right: object) -> bool:
    """Compare parsed JSON without Python's bool/int numeric equivalence."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_explicit_json(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_explicit_json(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


_PUBLIC_REPORT_ID = re.compile(
    r"(?:[0-9a-f]{64}|(?:cell|resolution|region)-[0-9a-f]{64}|"
    r"failure-[0-9a-f]{16,64})"
)


def _safe_report_id(value: object) -> str:
    candidate = value if isinstance(value, str) else ""
    return candidate if _PUBLIC_REPORT_ID.fullmatch(candidate) else "<invalid-id>"


def _sanitize_report_error(message: str) -> str:
    """Keep error categories and valid stable IDs without echoing wire input."""
    for marker in (
        " in Cell ",
        " in CandidateSnapshot ",
        " in Attempt ",
        " in Proposal ",
        " in StaticEvaluation ",
        " in Evaluation ",
        " in FailureRecord ",
        " for Cell ",
    ):
        prefix, separator, suffix = message.rpartition(marker)
        if (
            separator
            and prefix.startswith("invalid v1 report:")
            and not _PUBLIC_REPORT_ID.fullmatch(suffix)
        ):
            message = f"{prefix}{marker}<invalid-id>"
            break
    prefix, separator, suffix = message.rpartition(": ")
    if (
        separator
        and prefix.startswith("invalid v1 report:")
        and not _PUBLIC_REPORT_ID.fullmatch(suffix)
    ):
        message = f"{prefix}: <invalid-id>"
    return "".join(character for character in message if character.isprintable())[:512]


def _is_public_locator(value: str) -> bool:
    if not value or any(ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        canonical = public_locator(value)
    except ValueError:
        return False
    if canonical != value or value.startswith("file:"):
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False
    if parsed.scheme:
        return True
    return ".." not in posix.parts and ".." not in windows.parts


def _is_public_source(source: SourceIdentity) -> bool:
    if any(
        value is not None and not _is_public_locator(value)
        for value in (source.locator, source.index)
    ):
        return False
    if source.kind not in {"path", "workspace"} or source.locator is None:
        return True
    posix = PurePosixPath(source.locator)
    windows = PureWindowsPath(source.locator)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and ".." not in posix.parts
    )


def _is_public_artifact(artifact: AvailableArtifact) -> bool:
    return artifact.locator is None or _is_public_locator(artifact.locator)


@dataclass(frozen=True)
class FailureContext:
    cell: Cell
    proposal_id: str | None
    boundary_role: Literal["predecessor"] | None


@dataclass(frozen=True)
class ValidatedReport:
    """Immutable resolved facade for one fully validated Schema 1 report."""

    report_generation_id: str
    generator: GeneratorIdentity
    package: PackageIdentity
    source_snapshot: SourceSnapshotIdentity
    policy_identity: str
    verifier_outcome_policy: Literal["configured-verifier-terminal-v1"]
    source_plan: SourcePlan
    requirement_declarations: tuple[RequirementDeclaration, ...]
    target_cells: tuple[Cell, ...]
    cell_results: tuple[CellResult, ...]
    projection_evidence: tuple[ProjectionEvidence, ...]
    result: CompleteReportResult | IncompleteReportResult
    failure_records: tuple[FailureRecord, ...]
    _wire: PackageFloorReportV1Wire = field(repr=False, compare=False)

    def cell_result(self, reference: str) -> CellResult | None:
        return next(
            (
                result
                for result in self.cell_results
                if cell_id(result.cell) == reference
            ),
            None,
        )

    def failure(self, failure_id: str) -> FailureRecord | None:
        return next(
            (
                failure
                for failure in self.failure_records
                if failure.failure_id == failure_id
            ),
            None,
        )

    def failure_context(self, failure_id: str) -> FailureContext | None:
        for result in self.cell_results:
            if not any(
                failure.failure_id == failure_id
                for failure in failure_records_for_result(result)
            ):
                continue
            proposal_id = None
            boundary_role = None
            searches = (
                (result.search,)
                if isinstance(result, CellSuccess)
                else (result.coordinate_failure,)
                if isinstance(result, (CellIndeterminate, CellSearchFailure))
                and result.coordinate_failure is not None
                else ()
            )
            for search in searches:
                if isinstance(search, CoordinateSuccess) and any(
                    boundary.predecessor_failure_id == failure_id
                    for boundary in search.boundaries
                ):
                    boundary_role = "predecessor"
                for observation in search.observations:
                    evidence = observation.evidence
                    if (
                        isinstance(evidence, (ProbeRejection, ProbeIndeterminate))
                        and evidence.failure_id == failure_id
                    ):
                        proposal_id = evidence.proposal_id
            if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
                proposal_id = (
                    result.evaluation.proposal.proposal_id
                    if result.evaluation is not None
                    else None
                )
            return FailureContext(
                cell=result.cell,
                proposal_id=proposal_id,
                boundary_role=boundary_role,
            )
        return None


@dataclass(frozen=True)
class ReportUpdate:
    report: ValidatedReport
    replace_generation: bool
    removed_failure_ids: tuple[str, ...]


class PackageReportBuilder:
    """Build apply authority from canonical package, cell, and snapshot records."""

    def build(
        self,
        *,
        package: PackagePlan,
        source_snapshot: SourceSnapshotIdentity,
        cell_results: tuple[CellResult, ...],
        _generator: GeneratorIdentity | None = None,
        _policy_identity: str | None = None,
    ) -> ValidatedReport:
        result_by_cell = {
            self._cell_key(result.cell): result for result in cell_results
        }
        if len(result_by_cell) != len(cell_results):
            raise ConfigurationError("duplicate CellResult in report input")
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
        all_success = (
            bool(package.cells)
            and coverage_complete
            and all(isinstance(result_by_cell[key], CellSuccess) for key in target_keys)
        )
        all_representable = all(projection.representable for projection in projections)
        if all_success and all_representable:
            result_summary = CompleteReportResult(status="complete")
        else:
            reasons = {
                reason
                for result in cell_results
                if (reason := incomplete_reason(result)) is not None
            }
            if not coverage_complete:
                reasons.add("MISSING_CELL")
            if not all_representable:
                reasons.add("UNREPRESENTABLE_PROJECTION")
            result_summary = IncompleteReportResult(
                status="incomplete",
                reasons=tuple(sorted(reasons)),
            )

        candidate_snapshots: dict[tuple[CellKey, str], CandidateSnapshot] = {}
        for result in cell_results:
            if not isinstance(
                result, (CellSuccess, CellIndeterminate, CellSearchFailure)
            ):
                continue
            for snapshot in result.candidate_snapshots:
                key = (self._cell_key(snapshot.cell), snapshot.dependency)
                existing = candidate_snapshots.get(key)
                if existing is not None and existing != snapshot:
                    raise ConfigurationError(
                        f"conflicting CandidateSnapshot payload: {snapshot.dependency}"
                    )
                candidate_snapshots[key] = snapshot

        generator = _generator or GeneratorIdentity(
            name="pf", version=__version__, algorithm="v1"
        )
        package_identity = PackageIdentity(
            name=package.name,
            pyproject_path=package.pyproject_path,
            requires_python=package.requires_python,
        )
        policy_identity = _policy_identity or self._policy_identity(
            package, cell_results
        )
        declarations = tuple(
            sorted(package.declarations, key=lambda item: item.declaration_id)
        )
        target_cells = tuple(sorted(package.cells, key=cell_identity))
        generation_id = report_generation_id(
            generator=generator,
            package=package_identity,
            source_snapshot=source_snapshot,
            policy_identity=policy_identity,
            verifier_outcome_policy=CONFIGURED_VERIFIER_OUTCOME_POLICY,
            source_plan=package.source_plan,
            requirement_declarations=declarations,
            target_cells=target_cells,
        )
        ordered_results = tuple(result_by_cell[key] for key in sorted(result_by_cell))
        ordered_snapshots = tuple(
            sorted(
                candidate_snapshots.values(),
                key=lambda item: (cell_id(item.cell), item.dependency),
            )
        )
        search_outcomes = tuple(
            outcome
            for result in ordered_results
            if (
                outcome := (
                    result.search
                    if isinstance(result, CellSuccess)
                    else result.coordinate_failure
                    if isinstance(result, (CellIndeterminate, CellSearchFailure))
                    else None
                )
            )
            is not None
        )
        failure_records = tuple(
            failure
            for result in ordered_results
            for failure in failure_records_for_result(result)
        )
        failure_by_id = {failure.failure_id: failure for failure in failure_records}
        if len(failure_by_id) != len(failure_records):
            raise ConfigurationError("duplicate FailureRecord ID in report input")
        attempts = (
            tuple(
                (
                    result.attempt
                    for result in ordered_results
                    if isinstance(result, (BaselineRejection, BaselineIndeterminate))
                )
            )
            + tuple(
                failure.scope.attempt
                for failure in failure_records
                if isinstance(failure.scope, AttemptFailureScope)
            )
            + tuple(
                result.baseline_attempt
                for result in ordered_results
                if isinstance(
                    result, (CellSuccess, CellIndeterminate, CellSearchFailure)
                )
                and result.baseline_attempt is not None
            )
            + tuple(
                observation.evidence.attempt
                for outcome in search_outcomes
                for observation in outcome.observations
            )
        )
        attempt_by_id = {attempt.attempt_id: attempt for attempt in attempts}
        if any(attempt_by_id[item.attempt_id] != item for item in attempts):
            raise ConfigurationError("conflicting Attempt payload in report input")
        wire_attempts = tuple(
            self._wire_attempt(
                attempt_by_id[attempt_id],
                source_snapshot=source_snapshot,
                policy_identity=policy_identity,
            )
            for attempt_id in sorted(attempt_by_id)
        )
        wire_failures = tuple(
            self._wire_failure(
                failure_by_id[failure_id],
                source_snapshot=source_snapshot,
                policy_identity=policy_identity,
            )
            for failure_id in sorted(failure_by_id)
        )
        proposals = (
            tuple(
                proposal
                for result in ordered_results
                if isinstance(result, CellSuccess)
                for proposal in (
                    result.baseline.proposal,
                    result.final_evaluation.proposal,
                )
            )
            + tuple(
                result.baseline.proposal
                for result in ordered_results
                if isinstance(result, CellIndeterminate) and result.baseline is not None
            )
            + tuple(
                result.baseline.proposal
                for result in ordered_results
                if isinstance(result, CellSearchFailure)
            )
            + tuple(
                result.evaluation.proposal
                for result in ordered_results
                if isinstance(result, (BaselineRejection, BaselineIndeterminate))
                and result.evaluation is not None
            )
            + tuple(
                evaluation.proposal
                for outcome in search_outcomes
                for observation in outcome.observations
                if (
                    evaluation := (
                        observation.evidence.static_evaluation
                        if isinstance(observation.evidence, StaticOnlyEvidence)
                        else observation.evidence.evaluation
                        if isinstance(
                            observation.evidence,
                            (ProbePass, ProbeRejection, ProbeIndeterminate),
                        )
                        else None
                    )
                )
                is not None
            )
        )
        proposal_by_id = {proposal.proposal_id: proposal for proposal in proposals}
        if any(proposal_by_id[item.proposal_id] != item for item in proposals):
            raise ConfigurationError("conflicting Proposal payload in report input")
        wire_proposals = tuple(
            self._wire_proposal(
                proposal_by_id[proposal_id],
                attempt_by_id=attempt_by_id,
                declarations=declarations,
                source_snapshot=source_snapshot,
                policy_identity=policy_identity,
            )
            for proposal_id in sorted(proposal_by_id)
        )
        graphs = {
            resolution_graph_id(proposal.resolved_graph): proposal.resolved_graph
            for proposal in proposal_by_id.values()
        }
        wire_graphs = tuple(
            ResolutionGraphV1(
                resolution_graph_id=reference,
                nodes=graphs[reference],
            )
            for reference in sorted(graphs)
        )
        static_evaluations = (
            tuple(
                static
                for result in ordered_results
                if isinstance(result, CellSuccess)
                for static in (result.baseline.static, result.final_evaluation.static)
            )
            + tuple(
                result.baseline.static
                for result in ordered_results
                if isinstance(result, CellIndeterminate) and result.baseline is not None
            )
            + tuple(
                result.baseline.static
                for result in ordered_results
                if isinstance(result, CellSearchFailure)
            )
            + tuple(
                result.evaluation.static
                for result in ordered_results
                if isinstance(result, (BaselineRejection, BaselineIndeterminate))
                and result.evaluation is not None
                and result.evaluation.static is not None
            )
            + tuple(
                static
                for outcome in search_outcomes
                for observation in outcome.observations
                if (static := observation.evidence.static_evaluation) is not None
            )
        )
        wire_static_candidates = tuple(
            self._wire_static(static) for static in static_evaluations
        )
        static_by_proposal = {
            static.proposal_ref: static for static in wire_static_candidates
        }
        if any(
            static_by_proposal[item.proposal_ref].model_dump(mode="json")
            != item.model_dump(mode="json")
            for item in wire_static_candidates
        ):
            raise ConfigurationError("conflicting StaticEvaluation for one Proposal")
        wire_static = tuple(
            static_by_proposal[reference]
            for reference in sorted(static_by_proposal)
        )
        evaluations = (
            tuple(
                evaluation
                for result in ordered_results
                if isinstance(result, CellSuccess)
                for evaluation in (result.baseline, result.final_evaluation)
            )
            + tuple(
                result.baseline
                for result in ordered_results
                if isinstance(result, CellIndeterminate) and result.baseline is not None
            )
            + tuple(
                result.baseline
                for result in ordered_results
                if isinstance(result, CellSearchFailure)
            )
            + tuple(
                result.evaluation
                for result in ordered_results
                if isinstance(result, (BaselineRejection, BaselineIndeterminate))
                and result.evaluation is not None
            )
            + tuple(
                evaluation
                for outcome in search_outcomes
                for observation in outcome.observations
                if not isinstance(observation.evidence, StaticOnlyEvidence)
                if (
                    evaluation := (
                        observation.evidence.evaluation
                        if isinstance(
                            observation.evidence,
                            (ProbePass, ProbeRejection, ProbeIndeterminate),
                        )
                        else None
                    )
                )
                is not None
            )
        )
        evaluation_failure_refs = {
            evidence.evaluation.proposal.proposal_id: evidence.failure_id
            for outcome in search_outcomes
            for observation in outcome.observations
            if isinstance(
                (evidence := observation.evidence),
                (ProbeRejection, ProbeIndeterminate),
            )
            and evidence.evaluation is not None
        }
        evaluation_failure_refs.update(
            {
                result.evaluation.proposal.proposal_id: result.failure.failure_id
                for result in ordered_results
                if isinstance(result, (BaselineRejection, BaselineIndeterminate))
                and result.evaluation is not None
            }
        )
        wire_evaluation_candidates = tuple(
            self._wire_evaluation(
                evaluation,
                failure_ref=evaluation_failure_refs.get(
                    evaluation.proposal.proposal_id
                ),
            )
            for evaluation in evaluations
        )
        evaluation_by_proposal = {
            item.proposal_ref: item for item in wire_evaluation_candidates
        }
        if any(
            evaluation_by_proposal[item.proposal_ref].model_dump(mode="json")
            != item.model_dump(mode="json")
            for item in wire_evaluation_candidates
        ):
            raise ConfigurationError("conflicting Evaluation for one Proposal")
        wire_evaluations = tuple(
            evaluation_by_proposal[reference]
            for reference in sorted(evaluation_by_proposal)
        )
        wire_result_list = []
        for result in ordered_results:
            if isinstance(result, CellIndeterminate):
                wire_result_list.append(
                    CellIndeterminateV1(
                        status="CELL_INDETERMINATE",
                        cell_ref=cell_id(result.cell),
                        phase=result.phase,
                        failure_ref=result.failure_id,
                        failure_refs=tuple(
                            failure.failure_id for failure in result.failure_records
                        ),
                        baseline=(
                            BaselineRefsV1(
                                attempt_ref=result.baseline_attempt.attempt_id,
                                proposal_ref=result.baseline.proposal.proposal_id,
                                static_baseline_digest=result.static_baseline.digest,
                            )
                            if result.baseline_attempt is not None
                            and result.baseline is not None
                            and result.static_baseline is not None
                            else None
                        ),
                        candidate_snapshot_refs=(
                            tuple(
                                snapshot.digest
                                for snapshot in sorted(
                                    result.candidate_snapshots,
                                    key=lambda item: item.dependency,
                                )
                            )
                            or None
                        ),
                        coordinate_failure=(
                            self._wire_coordinate_failure(
                                result.coordinate_failure,
                                candidate_snapshots=(result.candidate_snapshots),
                            )
                            if result.coordinate_failure is not None
                            else None
                        ),
                    )
                )
            elif isinstance(result, CellSuccess):
                wire_result_list.append(
                    CellSuccessV1(
                        status="SUCCESS",
                        cell_ref=cell_id(result.cell),
                        baseline=BaselineRefsV1(
                            attempt_ref=result.baseline_attempt.attempt_id,
                            proposal_ref=result.baseline.proposal.proposal_id,
                            static_baseline_digest=result.static_baseline.digest,
                        ),
                        candidate_snapshot_refs=tuple(
                            snapshot.digest
                            for snapshot in sorted(
                                result.candidate_snapshots,
                                key=lambda item: item.dependency,
                            )
                        ),
                        search=self._wire_coordinate_success(
                            result.search,
                            candidate_snapshots=result.candidate_snapshots,
                        ),
                        final_proposal_ref=(
                            result.final_evaluation.proposal.proposal_id
                        ),
                        failure_refs=tuple(
                            failure.failure_id for failure in result.failure_records
                        ),
                    )
                )
            elif isinstance(result, CellSearchFailure):
                wire_result_list.append(
                    CellSearchFailureV1(
                        status="SEARCH_FAILED",
                        cell_ref=cell_id(result.cell),
                        phase=result.phase,
                        reason=result.reason,
                        baseline=BaselineRefsV1(
                            attempt_ref=result.baseline_attempt.attempt_id,
                            proposal_ref=result.baseline.proposal.proposal_id,
                            static_baseline_digest=result.static_baseline.digest,
                        ),
                        candidate_snapshot_refs=tuple(
                            snapshot.digest
                            for snapshot in sorted(
                                result.candidate_snapshots,
                                key=lambda item: item.dependency,
                            )
                        ),
                        coordinate_failure=(
                            self._wire_coordinate_failure(
                                result.coordinate_failure,
                                candidate_snapshots=(result.candidate_snapshots),
                            )
                            if result.coordinate_failure is not None
                            else None
                        ),
                        failure_refs=tuple(
                            failure.failure_id for failure in result.failure_records
                        ),
                    )
                )
            elif isinstance(result, BaselineRejection):
                wire_result_list.append(
                    BaselineRejectionV1(
                        status="BASELINE_REJECTION",
                        cell_ref=cell_id(result.cell),
                        attempt_ref=result.attempt.attempt_id,
                        failure_refs=(result.failure.failure_id,),
                        proposal_ref=(
                            result.evaluation.proposal.proposal_id
                            if result.evaluation is not None
                            else None
                        ),
                        static_baseline_digest=(
                            result.static_baseline.digest
                            if result.static_baseline is not None
                            else None
                        ),
                    )
                )
            else:
                assert isinstance(result, BaselineIndeterminate)
                wire_result_list.append(
                    BaselineIndeterminateV1(
                        status="BASELINE_INDETERMINATE",
                        cell_ref=cell_id(result.cell),
                        attempt_ref=result.attempt.attempt_id,
                        failure_refs=(result.failure.failure_id,),
                        proposal_ref=(
                            result.evaluation.proposal.proposal_id
                            if result.evaluation is not None
                            else None
                        ),
                        static_baseline_digest=(
                            result.static_baseline.digest
                            if result.static_baseline is not None
                            else None
                        ),
                    )
                )
        wire_results = tuple(wire_result_list)
        wire = PackageFloorReportV1Wire(
            schema_version=1,
            identity=ReportIdentityV1(
                report_generation_id=generation_id,
                generator=generator,
                package=package_identity,
                source_snapshot=source_snapshot,
                policy_identity=policy_identity,
                verifier_outcome_policy=CONFIGURED_VERIFIER_OUTCOME_POLICY,
            ),
            inputs=ReportInputsV1(
                source_plan=package.source_plan,
                requirement_declarations=declarations,
                target_cells=tuple(
                    TargetCellV1(
                        cell_id=cell_id(cell),
                        package=cell.package,
                        target=cell.target,
                        python_minor=cell.python_minor,
                        extra_surface=cell.extra_surface,
                        active_declaration_refs=cell.active_declaration_ids,
                    )
                    for cell in target_cells
                ),
                candidate_snapshots=tuple(
                    CandidateSnapshotV1(
                        candidate_snapshot_id=snapshot.digest,
                        dependency=snapshot.dependency,
                        cell_ref=cell_id(snapshot.cell),
                        policy_identity=snapshot.policy_identity,
                        source=snapshot.source,
                        candidates=snapshot.candidates,
                        series_representatives=snapshot.series_representatives,
                    )
                    for snapshot in ordered_snapshots
                ),
            ),
            evidence=ReportEvidenceV1(
                resolution_graphs=wire_graphs,
                attempts=wire_attempts,
                proposals=wire_proposals,
                static_evaluations=wire_static,
                evaluations=wire_evaluations,
                failures=wire_failures,
            ),
            cell_results=wire_results,
            projections=tuple(
                ProjectionEvidenceV1(
                    declaration_ref=projection.declaration_id,
                    floors=tuple(
                        FloorProjectionV1(
                            cell_ref=cell_id(floor.cell),
                            version=floor.version,
                        )
                        for floor in projection.floors
                    ),
                    projected_requirements=projection.projected_requirements,
                    representable=projection.representable,
                )
                for projection in sorted(
                    projections,
                    key=lambda item: item.declaration_id,
                )
            ),
            result=result_summary,
        )
        return ReportStore._validate_v1(wire.model_dump(mode="json", exclude_none=True))

    @staticmethod
    def _wire_observation(
        observation: ProbeObservation,
        *,
        region_ids: dict[StaticRegionSlice, str] | None = None,
    ) -> ProbeObservationV1:
        evidence = observation.evidence
        if isinstance(evidence, ProbePass):
            wire_evidence = DirectPassV1(
                kind="DIRECT",
                attempt_ref=evidence.attempt.attempt_id,
                status="PASS",
            )
        elif isinstance(evidence, ProbeRejection):
            wire_evidence = DirectRejectionV1(
                kind="DIRECT",
                attempt_ref=evidence.attempt.attempt_id,
                status="REJECTED",
                failure_ref=evidence.failure_id,
            )
        elif isinstance(evidence, ProbeIndeterminate):
            wire_evidence = DirectIndeterminateV1(
                kind="DIRECT",
                attempt_ref=evidence.attempt.attempt_id,
                status="INDETERMINATE",
                failure_ref=evidence.failure_id,
            )
        elif isinstance(evidence, StaticOnlyEvidence):
            reference = (region_ids or {}).get(evidence.region_slice)
            if reference is None:
                raise ConfigurationError(
                    "Static-only observation does not reference a local Region"
                )
            wire_evidence = StaticOnlyEvidenceV1(
                kind="STATIC_ONLY",
                attempt_ref=evidence.attempt.attempt_id,
                guidance=evidence.guidance,
                region_ref=reference,
                representative_proposal_ref=(evidence.representative_proposal_id),
            )
        else:
            raise ConfigurationError("Schema 1 observation evidence is not supported")
        return ProbeObservationV1(
            dependency=observation.dependency,
            candidate_version=observation.candidate_version,
            evidence=wire_evidence,
        )

    @classmethod
    def _wire_coordinate_success(
        cls,
        outcome: CoordinateSuccess,
        *,
        candidate_snapshots: tuple[CandidateSnapshot, ...],
    ) -> CoordinateSuccessV1:
        regions = tuple(
            cls._wire_region(
                region,
                candidate_snapshots=candidate_snapshots,
            )
            for region in outcome.regions
        )
        region_ids = {
            region.slice: wire.region_id
            for region, wire in zip(outcome.regions, regions)
        }
        return CoordinateSuccessV1(
            status="SUCCESS",
            observations=tuple(
                cls._wire_observation(
                    observation,
                    region_ids=region_ids,
                )
                for observation in outcome.observations
            ),
            boundaries=tuple(
                CoordinateBoundaryV1(
                    dependency=boundary.dependency,
                    floor=boundary.floor,
                    predecessor=boundary.predecessor,
                    predecessor_failure_ref=(boundary.predecessor_failure_id),
                )
                for boundary in outcome.boundaries
            ),
            regions=regions,
            sweeps=outcome.sweeps,
        )

    @staticmethod
    def _wire_region(
        region: StaticRegion,
        *,
        candidate_snapshots: tuple[CandidateSnapshot, ...],
    ) -> StaticRegionV1:
        snapshot = next(
            (
                item
                for item in candidate_snapshots
                if item.dependency == region.slice.active_dependency
            ),
            None,
        )
        if snapshot is None:
            raise ConfigurationError("Static Region has no matching CandidateSnapshot")
        return StaticRegionV1(
            region_id=static_region_id(region),
            candidate_snapshot_ref=snapshot.digest,
            baseline_digest=region.slice.baseline_digest,
            other_coordinates=region.slice.other_coordinates,
            static_fingerprint=region.static_fingerprint,
            observed_versions=region.observed_versions,
            runtime_references=tuple(
                StaticRegionRuntimeReferenceV1(proposal_ref=reference.proposal_id)
                for reference in sorted(
                    region.runtime_references,
                    key=lambda item: item.proposal_id,
                )
            ),
        )

    @classmethod
    def _wire_coordinate_failure(
        cls,
        outcome: CoordinateFailure,
        *,
        candidate_snapshots: tuple[CandidateSnapshot, ...],
    ) -> CoordinateFailureV1:
        regions = tuple(
            cls._wire_region(
                region,
                candidate_snapshots=candidate_snapshots,
            )
            for region in outcome.regions
        )
        region_ids = {
            region.slice: wire.region_id
            for region, wire in zip(outcome.regions, regions)
        }
        return CoordinateFailureV1(
            status=outcome.status,
            dependency=outcome.dependency,
            observations=tuple(
                cls._wire_observation(
                    observation,
                    region_ids=region_ids,
                )
                for observation in outcome.observations
            ),
            regions=regions,
            counterexample=outcome.counterexample,
            failure_ref=outcome.failure_id,
        )

    @staticmethod
    def _wire_proposal(
        proposal: Proposal,
        *,
        attempt_by_id: dict[str, Attempt],
        declarations: tuple[RequirementDeclaration, ...],
        source_snapshot: SourceSnapshotIdentity,
        policy_identity: str,
    ) -> ProposalV1:
        if proposal.attempt_id is None or proposal.attempt_id not in attempt_by_id:
            raise ConfigurationError(
                f"Proposal does not reference an interned Attempt: {proposal.proposal_id}"
            )
        attempt = attempt_by_id[proposal.attempt_id]
        if (
            proposal.snapshot_digest != source_snapshot.digest
            or proposal.policy_identity != policy_identity
            or proposal.cell != attempt.identity.cell
        ):
            raise ConfigurationError(
                f"Proposal scope does not match report generation: {proposal.proposal_id}"
            )
        if (
            not proposal.project_plan_digest
            or not proposal.environment_plan_digest
            or proposal.interpreter is None
        ):
            raise ConfigurationError(
                f"Proposal is missing plan or interpreter identity: {proposal.proposal_id}"
            )
        expected = environment_identity_digest(
            project_plan_digest=proposal.project_plan_digest,
            environment_plan_digest=proposal.environment_plan_digest,
            graph=proposal.resolved_graph,
        )
        if proposal.proposal_id != expected:
            raise ConfigurationError(
                f"Proposal identity mismatch: {proposal.proposal_id}"
            )
        declaration_ids = {item.declaration_id for item in declarations}
        fixed_ids = proposal.fixed_declaration_ids
        if fixed_ids != tuple(sorted(set(fixed_ids))):
            raise ConfigurationError(
                "Proposal fixed declaration refs must be sorted and unique: "
                f"{proposal.proposal_id}"
            )
        declaration_by_id = {item.declaration_id: item for item in declarations}
        if not set(fixed_ids) <= declaration_ids:
            raise ConfigurationError(
                f"Proposal references unknown declaration: {proposal.proposal_id}"
            )
        if not set(fixed_ids) <= set(attempt.identity.cell.active_declaration_ids):
            raise ConfigurationError(
                f"Proposal fixed declarations are not active: {proposal.proposal_id}"
            )
        if any(
            declaration_by_id[reference].kind != "fixed"
            or declaration_by_id[reference].managed
            for reference in fixed_ids
        ):
            raise ConfigurationError(
                f"Proposal declaration is not fixed: {proposal.proposal_id}"
            )
        return ProposalV1(
            proposal_id=proposal.proposal_id,
            attempt_ref=attempt.attempt_id,
            managed_vector=proposal.managed_vector,
            fixed_declaration_refs=proposal.fixed_declaration_ids,
            resolution_graph_ref=resolution_graph_id(proposal.resolved_graph),
            project_plan_digest=proposal.project_plan_digest,
            environment_plan_digest=proposal.environment_plan_digest,
            interpreter=proposal.interpreter,
        )

    @staticmethod
    def _wire_static(
        static: StaticUnchangedEvaluation | StaticRegressionEvaluation,
    ) -> StaticUnchangedEvaluationV1 | StaticRegressionEvaluationV1:
        if isinstance(static, StaticUnchangedEvaluation):
            return StaticUnchangedEvaluationV1(
                proposal_ref=static.proposal.proposal_id,
                status="STATIC_UNCHANGED",
                ty=static.ty,
                baseline_digest=static.baseline_digest,
                incremental=(),
                static_fingerprint=static.static_fingerprint,
            )
        return StaticRegressionEvaluationV1(
            proposal_ref=static.proposal.proposal_id,
            status="STATIC_REGRESSION",
            ty=static.ty,
            baseline_digest=static.baseline_digest,
            incremental=static.incremental,
            static_fingerprint=static.static_fingerprint,
            classifications=static.classifications,
        )

    @staticmethod
    def _wire_evaluation(
        evaluation: (
            PassEvaluation
            | VerifierRejectedEvaluation
            | RuntimeInterfaceMissingEvaluation
            | IndeterminateEvaluation
        ),
        *,
        failure_ref: str | None,
    ) -> (
        PassEvaluationV1
        | VerifierRejectedEvaluationV1
        | RuntimeInterfaceMissingEvaluationV1
        | IndeterminateEvaluationV1
    ):
        if isinstance(evaluation, PassEvaluation):
            if failure_ref is not None:
                raise ConfigurationError(
                    f"PASS Evaluation cannot reference FailureRecord: {evaluation.proposal.proposal_id}"
                )
            return PassEvaluationV1(
                proposal_ref=evaluation.proposal.proposal_id,
                status="PASS",
                static_evaluation_ref=evaluation.static.proposal.proposal_id,
                witnesses=PackageReportBuilder._wire_witnesses(
                    evaluation.witnesses,
                    failure_ref=None,
                ),
                terminal=evaluation.verifier.terminal,
            )
        if failure_ref is None:
            raise ConfigurationError(
                f"negative Evaluation requires FailureRecord: {evaluation.proposal.proposal_id}"
            )
        if isinstance(evaluation, VerifierRejectedEvaluation):
            return VerifierRejectedEvaluationV1(
                proposal_ref=evaluation.proposal.proposal_id,
                status="VERIFIER_REJECTED",
                static_evaluation_ref=evaluation.static.proposal.proposal_id,
                witnesses=PackageReportBuilder._wire_witnesses(
                    evaluation.witnesses,
                    failure_ref=failure_ref,
                ),
                failure_ref=failure_ref,
            )
        if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
            return RuntimeInterfaceMissingEvaluationV1(
                proposal_ref=evaluation.proposal.proposal_id,
                status="RUNTIME_INTERFACE_MISSING",
                static_evaluation_ref=evaluation.static.proposal.proposal_id,
                witnesses=PackageReportBuilder._wire_witnesses(
                    evaluation.witnesses,
                    failure_ref=failure_ref,
                ),
                failure_ref=failure_ref,
            )
        return IndeterminateEvaluationV1(
            proposal_ref=evaluation.proposal.proposal_id,
            status="INDETERMINATE",
            static_evaluation_ref=(
                evaluation.static.proposal.proposal_id
                if evaluation.static is not None
                else None
            ),
            witnesses=PackageReportBuilder._wire_witnesses(
                evaluation.witnesses,
                failure_ref=failure_ref,
            ),
            failure_ref=failure_ref,
        )

    @staticmethod
    def _wire_witnesses(
        witnesses: tuple[RuntimeWitnessAttempt, ...],
        *,
        failure_ref: str | None,
    ) -> tuple[RuntimeWitnessAttemptV1, ...]:
        result = []
        for witness in witnesses:
            outcome = witness.outcome
            if isinstance(outcome, RuntimeWitnessResult) and (
                outcome.status in {"PRESENT", "NOT_APPLICABLE"}
            ):
                wire_outcome = RuntimeWitnessPositiveV1(
                    status=outcome.status,
                    process=outcome.process,
                )
            else:
                if failure_ref is None:
                    raise ConfigurationError(
                        "terminal runtime witness requires FailureRecord"
                    )
                wire_outcome = RuntimeWitnessTerminalV1(
                    status=(
                        "CONFIRMED_MISSING"
                        if isinstance(outcome, RuntimeWitnessResult)
                        else "FAILURE"
                    ),
                    failure_ref=failure_ref,
                )
            result.append(
                RuntimeWitnessAttemptV1(
                    plan=witness.plan,
                    outcome=wire_outcome,
                )
            )
        return tuple(result)

    @staticmethod
    def _wire_attempt(
        attempt: Attempt,
        *,
        source_snapshot: SourceSnapshotIdentity,
        policy_identity: str,
    ) -> AttemptV1:
        identity = attempt.identity
        if identity.identity_version != "attempt-v2":
            raise ConfigurationError(
                f"Schema 1 requires attempt-v2: {attempt.attempt_id}"
            )
        if (
            identity.source_snapshot_digest != source_snapshot.digest
            or identity.evaluation_policy_identity != policy_identity
            or identity.active_declaration_ids != identity.cell.active_declaration_ids
            or not identity.resolution_context_digest
            or identity.harness_policy_identity is None
        ):
            raise ConfigurationError(
                f"Attempt scope does not match report generation: {attempt.attempt_id}"
            )
        return AttemptV1(
            attempt_id=attempt.attempt_id,
            cell_ref=cell_id(identity.cell),
            requested_resolution=identity.requested_resolution,
            requested_managed_vector=identity.requested_managed_vector,
            source_plan_identity=identity.source_plan_identity,
            resolution_context_digest=identity.resolution_context_digest,
            harness_policy_identity=identity.harness_policy_identity,
            harness_declaration_ids=identity.harness_declaration_ids,
            harness_baseline_digest=identity.harness_baseline_digest,
            selected_candidate_evidence_digest=(
                identity.selected_candidate_evidence_digest
            ),
        )

    @staticmethod
    def _wire_failure(
        failure: FailureRecord,
        *,
        source_snapshot: SourceSnapshotIdentity,
        policy_identity: str,
    ) -> FailureRecordV1:
        scope = failure.scope
        if isinstance(scope, AttemptFailureScope):
            attempt = scope.attempt
            PackageReportBuilder._wire_attempt(
                attempt,
                source_snapshot=source_snapshot,
                policy_identity=policy_identity,
            )
            wire_scope = AttemptFailureScopeV1(
                kind="attempt",
                attempt_ref=attempt.attempt_id,
            )
        elif (
            scope.source_snapshot_digest != source_snapshot.digest
            or scope.evaluation_policy_identity != policy_identity
        ):
            raise ConfigurationError(
                f"FailureRecord scope does not match report generation: {failure.failure_id}"
            )
        else:
            wire_scope = CellFailureScopeV1(
                kind="cell",
                cell_ref=cell_id(scope.cell),
            )
        return FailureRecordV1(
            failure_id=failure.failure_id,
            scope=wire_scope,
            disposition=failure.disposition,
            cause=failure.cause,
            stage=failure.stage,
            authority=failure.authority,
            project_plan_digest=failure.project_plan_digest,
            environment_plan_digest=failure.environment_plan_digest,
        )

    def _projection(
        self,
        *,
        declaration: RequirementDeclaration,
        package: PackagePlan,
        result_by_cell: dict[CellKey, CellResult],
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
                (
                    pin.version
                    for pin in result.final_vector
                    if pin.name == declaration.name
                ),
                None,
            )
            if floor is not None:
                floors.append(FloorProjection(cell=cell, version=floor))
        return self.project_declaration(
            declaration=declaration,
            target_cells=package.cells,
            active_cells=active_cells,
            floors=tuple(floors),
        )

    def project_declaration(
        self,
        *,
        declaration: RequirementDeclaration,
        target_cells: tuple[Cell, ...],
        active_cells: tuple[Cell, ...],
        floors: tuple[FloorProjection, ...],
    ) -> ProjectionEvidence:
        ordered_floors = tuple(
            sorted(floors, key=lambda floor: self._cell_key(floor.cell))
        )
        complete = {self._cell_key(floor.cell) for floor in ordered_floors} == {
            self._cell_key(cell) for cell in active_cells
        }
        versions = {floor.version for floor in ordered_floors}
        if complete and len(versions) == 1:
            version = next(iter(versions))
            projected = ((version, self._project_requirement(declaration, version)),)
        elif complete:
            projected = self._project_distinct_floors(
                declaration,
                ordered_floors,
            )
        else:
            projected = ()
        if projected and not self._projection_is_equivalent(
            declaration=declaration,
            target_cells=target_cells,
            floors=ordered_floors,
            projected=projected,
        ):
            projected = ()
        requirements = tuple(requirement for _, requirement in projected)
        return ProjectionEvidence(
            declaration_id=declaration.declaration_id,
            floors=ordered_floors,
            projected_requirements=requirements,
            representable=complete and bool(requirements),
        )

    def project(
        self,
        *,
        declarations: tuple[RequirementDeclaration, ...],
        target_cells: tuple[Cell, ...],
        floors: tuple[FloorProjection, ...],
        selected_selectors: tuple[ApplySelector, ...],
        platform_scoped: bool,
    ) -> DependencyGroupProjection:
        """Project one complete dependency group from Cell intent."""
        if not declarations:
            raise ConfigurationError("dependency group projection requires declarations")
        ordered_declarations = tuple(
            sorted(declarations, key=lambda declaration: declaration.declaration_id)
        )
        key = dependency_group_key(ordered_declarations[0])
        if any(
            dependency_group_key(declaration) != key
            for declaration in ordered_declarations[1:]
        ):
            raise ConfigurationError("dependency group projection mixes group keys")
        selector_keys = tuple(
            sorted(
                {
                    (selector.sys_platform, selector.platform_machine)
                    for selector in selected_selectors
                }
            )
        )
        ordered_floors = tuple(
            sorted(floors, key=lambda floor: self._cell_key(floor.cell))
        )
        result = DependencyGroupProjection(
            key=key,
            floors=ordered_floors,
            original_requirements=tuple(
                declaration.raw for declaration in ordered_declarations
            ),
            projected_requirements=(),
            representable=False,
        )
        if not selector_keys:
            return result
        target_keys = {self._cell_key(cell) for cell in target_cells}
        floor_by_cell: dict[CellKey, str] = {}
        floor_by_selector_coordinate: dict[
            tuple[str, tuple[str, ...], tuple[str, str]], str
        ] = {}
        for floor in ordered_floors:
            cell_key = self._cell_key(floor.cell)
            if cell_key not in target_keys:
                return result
            previous = floor_by_cell.get(cell_key)
            if previous is not None and previous != floor.version:
                return result
            floor_by_cell[cell_key] = floor.version
            selector_coordinate = (
                floor.cell.python_minor,
                floor.cell.extra_surface,
                self._selector_key(floor.cell),
            )
            selector_previous = floor_by_selector_coordinate.get(selector_coordinate)
            if selector_previous is not None and selector_previous != floor.version:
                return result
            floor_by_selector_coordinate[selector_coordinate] = floor.version

        projected: list[str] = []
        intended_floor_by_cell: dict[CellKey, str] = {}
        for declaration in ordered_declarations:
            if not declaration.managed:
                projected.append(declaration.raw)
                continue
            active_selected = tuple(
                cell
                for cell in target_cells
                if declaration.declaration_id in cell.active_declaration_ids
                and self._selector_key(cell) in selector_keys
            )
            coordinates: dict[tuple[str, tuple[str, str]], str] = {}
            for cell in active_selected:
                cell_key = self._cell_key(cell)
                version = floor_by_cell.get(cell_key) or floor_by_selector_coordinate.get(
                    (
                        cell.python_minor,
                        cell.extra_surface,
                        self._selector_key(cell),
                    )
                )
                if version is None:
                    return result
                intended_floor_by_cell[cell_key] = version
                coordinate = (cell.python_minor, self._selector_key(cell))
                previous = coordinates.get(coordinate)
                if previous is not None and previous != version:
                    return result
                coordinates[coordinate] = version
            if coordinates:
                versions = set(coordinates.values())
                if not platform_scoped and len(versions) == 1:
                    projected.append(
                        self._project_requirement(declaration, next(iter(versions)))
                    )
                else:
                    python_dimension = len(
                        {minor for minor, _ in coordinates}
                    ) > 1
                    selector_dimension = platform_scoped or len(
                        {selector for _, selector in coordinates}
                    ) > 1
                    by_version: dict[str, list[str]] = {}
                    for (minor, selector), version in sorted(coordinates.items()):
                        parts = []
                        if python_dimension:
                            parts.append(f'python_version == "{minor}"')
                        if selector_dimension:
                            parts.extend(
                                (
                                    f'sys_platform == "{selector[0]}"',
                                    f'platform_machine == "{selector[1]}"',
                                )
                            )
                        condition = " and ".join(parts)
                        if not condition:
                            return result
                        by_version.setdefault(version, []).append(condition)
                    for version in sorted(by_version, key=Version):
                        alternatives = tuple(sorted(set(by_version[version])))
                        selector = " or ".join(
                            f"({alternative})" if " and " in alternative else alternative
                            for alternative in alternatives
                        )
                        projected.append(
                            self._project_requirement(
                                declaration,
                                version,
                                selector=selector,
                            )
                        )
            else:
                projected.append(declaration.raw)
            if platform_scoped and coordinates:
                projected.append(
                    self._preserve_requirement(
                        declaration,
                        selector=self._selector_complement(selector_keys),
                    )
                )
        projected_requirements = tuple(projected)
        if not self._group_projection_is_equivalent(
            declarations=ordered_declarations,
            target_cells=target_cells,
            floor_by_cell=intended_floor_by_cell,
            selected_selectors=frozenset(selector_keys),
            projected_requirements=projected_requirements,
        ):
            return result
        return result.model_copy(
            update={
                "projected_requirements": projected_requirements,
                "representable": True,
            }
        )

    def _group_projection_is_equivalent(
        self,
        *,
        declarations: tuple[RequirementDeclaration, ...],
        target_cells: tuple[Cell, ...],
        floor_by_cell: dict[CellKey, str],
        selected_selectors: frozenset[tuple[str, str]],
        projected_requirements: tuple[str, ...],
    ) -> bool:
        for cell in target_cells:
            active = tuple(
                declaration
                for declaration in declarations
                if declaration.declaration_id in cell.active_declaration_ids
            )
            expected = []
            for declaration in active:
                if declaration.managed and self._selector_key(cell) in selected_selectors:
                    floor = floor_by_cell.get(self._cell_key(cell))
                    if floor is None:
                        return False
                    raw = self._project_requirement(declaration, floor)
                else:
                    raw = declaration.raw
                expected.append(self._effective_requirement(raw))
            observed = []
            for raw in projected_requirements:
                requirement = Requirement(raw)
                marker = (
                    str(requirement.marker)
                    if requirement.marker is not None
                    else None
                )
                if (
                    declarations[0].location == "optional"
                    and declarations[0].extra not in cell.extra_surface
                ):
                    continue
                if marker_applies(marker, cell):
                    observed.append(self._effective_requirement(raw))
            if sorted(expected) != sorted(observed):
                return False
        return True

    @staticmethod
    def _effective_requirement(raw: str) -> tuple[object, ...]:
        requirement = Requirement(raw)
        return (
            requirement.name.lower().replace("_", "-"),
            tuple(sorted(requirement.extras)),
            tuple(
                sorted(
                    (specifier.operator, specifier.version)
                    for specifier in requirement.specifier
                )
            ),
            requirement.url,
        )

    @staticmethod
    def _selector_key(cell: Cell) -> tuple[str, str]:
        values = marker_platform(cell.target)
        return values["sys_platform"], values["platform_machine"]

    @staticmethod
    def _selector_complement(
        selectors: tuple[tuple[str, str], ...],
    ) -> str:
        return " and ".join(
            f'(sys_platform != "{sys_platform}" or '
            f'platform_machine != "{platform_machine}")'
            for sys_platform, platform_machine in selectors
        )

    @staticmethod
    def _preserve_requirement(
        declaration: RequirementDeclaration,
        *,
        selector: str,
    ) -> str:
        original = Requirement(declaration.raw)
        original_marker = str(original.marker) if original.marker is not None else None
        marker_value = (
            f"({original_marker}) and ({selector})"
            if original_marker is not None
            else selector
        )
        name = original.name
        extras = f"[{','.join(sorted(original.extras))}]" if original.extras else ""
        return f"{name}{extras}{original.specifier}; {marker_value}"

    def _project_distinct_floors(
        self,
        declaration: RequirementDeclaration,
        floors: tuple[FloorProjection, ...],
    ) -> tuple[tuple[str, str], ...]:
        attributes: dict[CellKey, dict[str, str] | None] = {
            self._cell_key(floor.cell): self._marker_attributes(floor.cell)
            for floor in floors
        }
        if any(value is None for value in attributes.values()):
            return ()
        varying = tuple(
            name
            for name in ("python_version", "sys_platform", "platform_machine")
            if len({value[name] for value in attributes.values() if value is not None})
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
                (
                    version,
                    self._project_requirement(declaration, version, selector=marker),
                )
            )
        return tuple(requirements)

    def _projection_is_equivalent(
        self,
        *,
        declaration: RequirementDeclaration,
        target_cells: tuple[Cell, ...],
        floors: tuple[FloorProjection, ...],
        projected: tuple[tuple[str, str], ...],
    ) -> bool:
        intended = {self._cell_key(floor.cell): floor.version for floor in floors}
        observed: dict[CellKey, str] = {}
        for version, raw in projected:
            requirement = Requirement(raw)
            marker = str(requirement.marker) if requirement.marker is not None else None
            for cell in target_cells:
                if (
                    declaration.location == "optional"
                    and declaration.extra not in cell.extra_surface
                ):
                    continue
                if not marker_applies(marker, cell):
                    continue
                key = self._cell_key(cell)
                previous = observed.get(key)
                if previous is not None and previous != version:
                    return False
                observed[key] = version
        return observed == intended

    @staticmethod
    def _project_requirement(
        declaration: RequirementDeclaration,
        floor: str,
        *,
        selector: str | None = None,
    ) -> str:
        original = Requirement(declaration.raw)
        name = original.name
        extras = f"[{','.join(sorted(original.extras))}]" if original.extras else ""
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
        cell_results: tuple[CellResult, ...],
    ) -> str:
        for result in cell_results:
            if isinstance(result, CellSuccess):
                return result.final_evaluation.proposal.policy_identity
            if isinstance(result, (BaselineRejection, BaselineIndeterminate)):
                return result.attempt.identity.evaluation_policy_identity
            if isinstance(result, (CellIndeterminate, CellSearchFailure)) and (
                result.baseline is not None
            ):
                return result.baseline.proposal.policy_identity
        return evaluation_policy_identity(package.config)

    @staticmethod
    def _cell_key(cell: Cell) -> CellKey:
        return cell_identity(cell)


def incomplete_reason(result: CellResult) -> str | None:
    if isinstance(result, BaselineRejection):
        return "BASELINE_REJECTION"
    if isinstance(result, (BaselineIndeterminate, CellIndeterminate)):
        return "INDETERMINATE"
    if isinstance(result, CellSearchFailure):
        return result.reason
    return None


class ReportStore:
    """Own canonical, versioned, atomic package-floor report persistence."""

    _GENERATION_FIELDS = (
        ("generator", "generator"),
        ("package", "package"),
        ("source_snapshot", "source snapshot"),
        ("policy_identity", "policy"),
        ("source_plan", "source plan"),
        ("requirement_declarations", "declarations"),
        ("target_cells", "target cell coverage"),
    )

    def write(self, path: Path, report: ValidatedReport) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = report._wire.model_dump(mode="json", exclude_none=True)
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

    def read(self, path: Path) -> ValidatedReport:
        try:
            return self._validate_v1(self._read_document(path))
        except ConfigurationError as error:
            sanitized = _sanitize_report_error(str(error))
            if sanitized == str(error):
                raise
            raise ConfigurationError(sanitized) from error

    @staticmethod
    def _read_document(path: Path) -> dict[str, object]:
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ConfigurationError("report exceeds the 64 MiB read limit")
            content = path.read_bytes()
        except OSError as error:
            raise ConfigurationError(f"cannot read report: {path}") from error
        if len(content) > 64 * 1024 * 1024:
            raise ConfigurationError("report exceeds the 64 MiB read limit")
        try:
            document = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise ConfigurationError(f"invalid report JSON: {path}") from error
        schema_version = (
            document.get("schema_version") if isinstance(document, dict) else None
        )
        if schema_version != 1:
            raise ConfigurationError("unsupported report schema_version")
        return document

    @classmethod
    def _validate_v1(cls, document: dict[str, object]) -> ValidatedReport:
        def contains_null(value: object) -> bool:
            if value is None:
                return True
            if isinstance(value, dict):
                return any(contains_null(item) for item in value.values())
            if isinstance(value, list):
                return any(contains_null(item) for item in value)
            return False

        if contains_null(document):
            raise ConfigurationError(
                "invalid v1 report: optional fields must be omitted, not null"
            )
        try:
            wire = PackageFloorReportV1Wire.model_validate(document)
        except ValidationError as error:
            error_types = sorted(
                {
                    item["type"]
                    for item in error.errors(
                        include_url=False,
                        include_context=False,
                        include_input=False,
                    )
                }
            )
            raise ConfigurationError(
                "invalid v1 report structure: " + ", ".join(error_types[:8])
            ) from error
        if not _same_explicit_json(
            document,
            wire.model_dump(mode="json", exclude_none=True),
        ):
            raise ConfigurationError(
                "invalid v1 report: explicit wire facts are missing or not canonical"
            )
        source_plan = wire.inputs.source_plan
        if any(not _is_public_source(item) for item in source_plan.identities):
            raise ConfigurationError(
                "invalid v1 report: SourcePlan has a non-public locator"
            )
        declarations = wire.inputs.requirement_declarations
        declaration_ids = tuple(item.declaration_id for item in declarations)
        if declaration_ids != tuple(sorted(set(declaration_ids))):
            raise ConfigurationError(
                "invalid v1 report: requirement declarations must be sorted and unique"
            )
        source_snapshot = wire.identity.source_snapshot
        paths = tuple(entry.path for entry in source_snapshot.entries)
        pyproject_paths = tuple(
            identity.path for identity in source_snapshot.pyproject_identities
        )
        entry_by_path = {entry.path: entry for entry in source_snapshot.entries}
        if (
            paths != tuple(sorted(set(paths)))
            or pyproject_paths != tuple(sorted(set(pyproject_paths)))
            or any(
                path not in entry_by_path
                or entry_by_path[path].kind != "file"
                or entry_by_path[path].content_digest is not None
                for path in pyproject_paths
            )
            or source_snapshot.digest
            != source_snapshot_digest(
                source_snapshot.entries,
                source_snapshot.pyproject_identities,
            )
        ):
            raise ConfigurationError(
                "invalid v1 report: source snapshot identity mismatch"
            )
        declaration_id_set = set(declaration_ids)
        declaration_by_id = {
            declaration.declaration_id: declaration for declaration in declarations
        }
        if any(not _is_public_source(item.source) for item in declarations):
            raise ConfigurationError(
                "invalid v1 report: RequirementDeclaration has a non-public source locator"
            )
        cells: list[Cell] = []
        for record in wire.inputs.target_cells:
            if not set(record.active_declaration_refs) <= declaration_id_set:
                raise ConfigurationError(
                    "invalid v1 report: unknown declaration ref in Cell "
                    f"{_safe_report_id(record.cell_id)}"
                )
            cell = Cell(
                package=record.package,
                target=record.target,
                python_minor=record.python_minor,
                extra_surface=record.extra_surface,
                active_declaration_ids=record.active_declaration_refs,
            )
            if (
                record.package != wire.identity.package.name
                or record.cell_id != cell_id(cell)
            ):
                raise ConfigurationError(
                    "invalid v1 report: Cell identity mismatch: "
                    f"{_safe_report_id(record.cell_id)}"
                )
            cells.append(cell)
        target_cells = tuple(cells)
        keys = tuple(cell_identity(cell) for cell in target_cells)
        if keys != tuple(sorted(set(keys))):
            raise ConfigurationError(
                "invalid v1 report: target Cells must be sorted and unique"
            )
        cell_by_id = {cell_id(cell): cell for cell in target_cells}
        candidate_order = tuple(
            (item.cell_ref, item.dependency) for item in wire.inputs.candidate_snapshots
        )
        if candidate_order != tuple(sorted(set(candidate_order))):
            raise ConfigurationError(
                "invalid v1 report: CandidateSnapshots must be sorted and unique"
            )
        candidate_by_id: dict[str, CandidateSnapshot] = {}
        candidate_key_ids: set[str] = set()
        for record in wire.inputs.candidate_snapshots:
            cell = cell_by_id.get(record.cell_ref)
            if cell is None:
                raise ConfigurationError(
                    "invalid v1 report: unknown Cell ref in CandidateSnapshot "
                    f"{_safe_report_id(record.candidate_snapshot_id)}"
                )
            if not _is_public_source(record.source) or any(
                not _is_public_artifact(candidate.artifact)
                for candidate in record.candidates
            ):
                raise ConfigurationError(
                    "invalid v1 report: CandidateSnapshot has a non-public locator"
                )
            try:
                snapshot = CandidateSnapshot(
                    dependency=record.dependency,
                    cell=cell,
                    policy_identity=record.policy_identity,
                    source=record.source,
                    candidates=record.candidates,
                    series_representatives=record.series_representatives,
                    digest=record.candidate_snapshot_id,
                )
            except ValidationError as error:
                raise ConfigurationError(
                    "invalid v1 report: CandidateSnapshot identity mismatch: "
                    f"{_safe_report_id(record.candidate_snapshot_id)}"
                ) from error
            if record.candidate_snapshot_id in candidate_key_ids:
                raise ConfigurationError(
                    "invalid v1 report: duplicate CandidateSnapshot ID: "
                    f"{_safe_report_id(record.candidate_snapshot_id)}"
                )
            candidate_by_id[record.candidate_snapshot_id] = snapshot
            candidate_key_ids.add(record.candidate_snapshot_id)
        graph_ids = tuple(
            item.resolution_graph_id for item in wire.evidence.resolution_graphs
        )
        if graph_ids != tuple(sorted(set(graph_ids))):
            raise ConfigurationError(
                "invalid v1 report: ResolutionGraph IDs must be sorted and unique"
            )
        graph_by_id = {}
        for record in wire.evidence.resolution_graphs:
            try:
                expected_graph_id = resolution_graph_id(record.nodes)
            except ValueError as error:
                raise ConfigurationError(
                    "invalid v1 report: non-canonical ResolutionGraph: "
                    f"{_safe_report_id(record.resolution_graph_id)}"
                ) from error
            if record.resolution_graph_id != expected_graph_id:
                raise ConfigurationError(
                    "invalid v1 report: ResolutionGraph identity mismatch: "
                    f"{_safe_report_id(record.resolution_graph_id)}"
                )
            graph_by_id[record.resolution_graph_id] = record.nodes
        attempt_ids = tuple(item.attempt_id for item in wire.evidence.attempts)
        if attempt_ids != tuple(sorted(set(attempt_ids))):
            raise ConfigurationError(
                "invalid v1 report: Attempt IDs must be sorted and unique"
            )
        attempt_by_id: dict[str, Attempt] = {}
        for record in wire.evidence.attempts:
            cell = cell_by_id.get(record.cell_ref)
            if cell is None:
                raise ConfigurationError(
                    "invalid v1 report: unknown Cell ref in Attempt "
                    f"{_safe_report_id(record.attempt_id)}"
                )
            try:
                attempt_by_id[record.attempt_id] = Attempt(
                    attempt_id=record.attempt_id,
                    identity=AttemptIdentity(
                        identity_version="attempt-v2",
                        source_snapshot_digest=source_snapshot.digest,
                        cell=cell,
                        requested_resolution=record.requested_resolution,
                        requested_managed_vector=record.requested_managed_vector,
                        active_declaration_ids=cell.active_declaration_ids,
                        source_plan_identity=record.source_plan_identity,
                        evaluation_policy_identity=wire.identity.policy_identity,
                        resolution_context_digest=record.resolution_context_digest,
                        harness_policy_identity=record.harness_policy_identity,
                        harness_declaration_ids=record.harness_declaration_ids,
                        harness_baseline_digest=record.harness_baseline_digest,
                        selected_candidate_evidence_digest=(
                            record.selected_candidate_evidence_digest
                        ),
                    ),
                )
            except ValidationError as error:
                raise ConfigurationError(
                    "invalid v1 report: Attempt identity mismatch: "
                    f"{_safe_report_id(record.attempt_id)}"
                ) from error
        proposal_ids = tuple(item.proposal_id for item in wire.evidence.proposals)
        if proposal_ids != tuple(sorted(set(proposal_ids))):
            raise ConfigurationError(
                "invalid v1 report: Proposal IDs must be sorted and unique"
            )
        proposal_by_id: dict[str, Proposal] = {}
        proposal_attempt_ids: set[str] = set()
        for record in wire.evidence.proposals:
            attempt = attempt_by_id.get(record.attempt_ref)
            graph = graph_by_id.get(record.resolution_graph_ref)
            if attempt is None:
                raise ConfigurationError(
                    "invalid v1 report: unknown Attempt ref in Proposal "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            if graph is None:
                raise ConfigurationError(
                    "invalid v1 report: unknown ResolutionGraph ref in Proposal "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            if record.attempt_ref in proposal_attempt_ids:
                raise ConfigurationError(
                    "invalid v1 report: Attempt has multiple Proposals: "
                    f"{_safe_report_id(record.attempt_ref)}"
                )
            if not record.project_plan_digest or not record.environment_plan_digest:
                raise ConfigurationError(
                    "invalid v1 report: Proposal is missing plan identity: "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            try:
                interpreter_minor = Version(record.interpreter.version).release[:2]
                cell_minor = tuple(
                    int(part) for part in attempt.identity.cell.python_minor.split(".")
                )
            except ValueError as error:
                raise ConfigurationError(
                    "invalid v1 report: Proposal interpreter does not match its Cell: "
                    f"{_safe_report_id(record.proposal_id)}"
                ) from error
            interpreter_tag = "".join(str(part) for part in cell_minor)
            if (
                interpreter_minor != cell_minor
                or record.interpreter.abi
                not in {
                    f"cpython-{interpreter_tag}",
                    f"cp{interpreter_tag}",
                }
                and not (
                    record.interpreter.abi.startswith(f"cpython-{interpreter_tag}-")
                    or record.interpreter.abi.startswith(f"cp{interpreter_tag}-")
                )
            ):
                raise ConfigurationError(
                    "invalid v1 report: Proposal interpreter does not match its Cell: "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            fixed_refs = record.fixed_declaration_refs
            if fixed_refs != tuple(sorted(set(fixed_refs))):
                raise ConfigurationError(
                    "invalid v1 report: Proposal fixed declaration refs must be "
                    "sorted and unique: "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            if not set(fixed_refs) <= declaration_id_set:
                raise ConfigurationError(
                    "invalid v1 report: unknown declaration ref in Proposal "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            if not set(fixed_refs) <= set(attempt.identity.cell.active_declaration_ids):
                raise ConfigurationError(
                    "invalid v1 report: Proposal fixed declarations are not active: "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            if any(
                declaration_by_id[reference].kind != "fixed"
                or declaration_by_id[reference].managed
                for reference in fixed_refs
            ):
                raise ConfigurationError(
                    "invalid v1 report: Proposal declaration is not fixed: "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            expected_proposal_id = environment_identity_digest(
                project_plan_digest=record.project_plan_digest,
                environment_plan_digest=record.environment_plan_digest,
                graph=graph,
            )
            if record.proposal_id != expected_proposal_id:
                raise ConfigurationError(
                    "invalid v1 report: Proposal identity mismatch: "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            proposal = Proposal(
                proposal_id=record.proposal_id,
                attempt_id=attempt.attempt_id,
                snapshot_digest=source_snapshot.digest,
                cell=attempt.identity.cell,
                managed_vector=record.managed_vector,
                fixed_declaration_ids=record.fixed_declaration_refs,
                resolved_graph=graph,
                policy_identity=wire.identity.policy_identity,
                project_plan_digest=record.project_plan_digest,
                environment_plan_digest=record.environment_plan_digest,
                interpreter=record.interpreter,
            )
            if (
                attempt.identity.requested_resolution == "exact-vector"
                and attempt.identity.requested_managed_vector != proposal.managed_vector
            ):
                raise ConfigurationError(
                    "invalid v1 report: Proposal vector does not match Attempt: "
                    f"{_safe_report_id(record.proposal_id)}"
                )
            proposal_by_id[record.proposal_id] = proposal
            proposal_attempt_ids.add(record.attempt_ref)
        proposal_by_attempt = {
            proposal.attempt_id: proposal
            for proposal in proposal_by_id.values()
            if proposal.attempt_id is not None
        }
        static_refs = tuple(
            item.proposal_ref for item in wire.evidence.static_evaluations
        )
        if static_refs != tuple(sorted(set(static_refs))):
            raise ConfigurationError(
                "invalid v1 report: StaticEvaluations must be sorted and unique"
            )
        static_by_proposal: dict[
            str, StaticUnchangedEvaluation | StaticRegressionEvaluation
        ] = {}
        for record in wire.evidence.static_evaluations:
            proposal = proposal_by_id.get(record.proposal_ref)
            if proposal is None:
                raise ConfigurationError(
                    "invalid v1 report: unknown Proposal ref in StaticEvaluation "
                    f"{_safe_report_id(record.proposal_ref)}"
                )
            try:
                if isinstance(record, StaticUnchangedEvaluationV1):
                    static = StaticUnchangedEvaluation(
                        proposal=proposal,
                        ty=record.ty,
                        baseline_digest=record.baseline_digest,
                        incremental=(),
                        static_fingerprint=record.static_fingerprint,
                    )
                else:
                    static = StaticRegressionEvaluation(
                        proposal=proposal,
                        ty=record.ty,
                        baseline_digest=record.baseline_digest,
                        incremental=record.incremental,
                        static_fingerprint=record.static_fingerprint,
                        classifications=record.classifications,
                    )
                static_by_proposal[record.proposal_ref] = static
            except ValidationError as error:
                raise ConfigurationError(
                    "invalid v1 report: StaticEvaluation mismatch: "
                    f"{_safe_report_id(record.proposal_ref)}"
                ) from error
        evaluation_refs = tuple(item.proposal_ref for item in wire.evidence.evaluations)
        if evaluation_refs != tuple(sorted(set(evaluation_refs))):
            raise ConfigurationError(
                "invalid v1 report: Evaluations must be sorted and unique"
            )
        evaluation_by_proposal: dict[
            str,
            PassEvaluation
            | VerifierRejectedEvaluation
            | RuntimeInterfaceMissingEvaluation
            | IndeterminateEvaluation,
        ] = {}
        pending_evaluations = tuple(wire.evidence.evaluations)
        for record in wire.evidence.evaluations:
            proposal = proposal_by_id.get(record.proposal_ref)
            static_ref = record.static_evaluation_ref
            static = (
                static_by_proposal.get(static_ref) if static_ref is not None else None
            )
            if proposal is None or (
                not isinstance(record, IndeterminateEvaluationV1) and static is None
            ):
                raise ConfigurationError(
                    "invalid v1 report: unknown evidence ref in Evaluation "
                    f"{_safe_report_id(record.proposal_ref)}"
                )
            if static_ref is not None and static_ref != record.proposal_ref:
                raise ConfigurationError(
                    "invalid v1 report: cross-Proposal StaticEvaluation ref: "
                    f"{_safe_report_id(record.proposal_ref)}"
                )
        failure_ids = tuple(item.failure_id for item in wire.evidence.failures)
        if failure_ids != tuple(sorted(set(failure_ids))):
            raise ConfigurationError(
                "invalid v1 report: FailureRecord IDs must be sorted and unique"
            )
        failure_by_id: dict[str, FailureRecord] = {}
        for record in wire.evidence.failures:
            scope_record = record.scope
            if isinstance(scope_record, CellFailureScopeV1):
                cell = cell_by_id.get(scope_record.cell_ref)
                if cell is None:
                    raise ConfigurationError(
                        "invalid v1 report: unknown Cell ref in FailureRecord "
                        f"{_safe_report_id(record.failure_id)}"
                    )
                scope = CellFailureScope(
                    package=cell.package,
                    cell=cell,
                    source_snapshot_digest=source_snapshot.digest,
                    evaluation_policy_identity=wire.identity.policy_identity,
                )
            else:
                attempt = attempt_by_id.get(scope_record.attempt_ref)
                if attempt is None:
                    raise ConfigurationError(
                        "invalid v1 report: unknown Attempt ref in FailureRecord "
                        f"{_safe_report_id(record.failure_id)}"
                    )
                scope = AttemptFailureScope(attempt=attempt)
            try:
                failure_by_id[record.failure_id] = FailureRecord(
                    failure_id=record.failure_id,
                    scope=scope,
                    disposition=record.disposition,
                    cause=record.cause,
                    stage=record.stage,
                    authority=record.authority,
                    project_plan_digest=record.project_plan_digest,
                    environment_plan_digest=record.environment_plan_digest,
                )
            except ValidationError as error:
                raise ConfigurationError(
                    "invalid v1 report: FailureRecord identity mismatch: "
                    f"{_safe_report_id(record.failure_id)}"
                ) from error

        def resolve_witnesses(
            records: tuple[RuntimeWitnessAttemptV1, ...],
            *,
            terminal_failure_ref: str | None,
        ) -> tuple[RuntimeWitnessAttempt, ...]:
            witnesses = []
            for item in records:
                outcome_record = item.outcome
                if isinstance(outcome_record, RuntimeWitnessPositiveV1):
                    outcome = RuntimeWitnessResult(
                        status=outcome_record.status,
                        plan=item.plan,
                        process=outcome_record.process,
                    )
                else:
                    if outcome_record.failure_ref != terminal_failure_ref:
                        raise ConfigurationError(
                            "invalid v1 report: witness failure does not match terminal Evaluation"
                        )
                    failure = failure_by_id.get(outcome_record.failure_ref)
                    if (
                        failure is None
                        or failure.process is None
                        or failure.stage != "witness"
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: witness FailureRecord mismatch"
                        )
                    if outcome_record.status == "CONFIRMED_MISSING":
                        outcome = RuntimeWitnessResult(
                            status="CONFIRMED_MISSING",
                            plan=item.plan,
                            process=failure.process,
                        )
                    else:
                        outcome = ToolFailure(
                            cause=failure.cause,
                            stage=failure.stage,
                            process=failure.process,
                            summary_code=failure.summary_code,
                        )
                witnesses.append(RuntimeWitnessAttempt(plan=item.plan, outcome=outcome))
            return tuple(witnesses)

        for record in pending_evaluations:
            proposal = proposal_by_id[record.proposal_ref]
            static = (
                static_by_proposal.get(record.static_evaluation_ref)
                if record.static_evaluation_ref is not None
                else None
            )
            terminal_failure_ref = (
                None if isinstance(record, PassEvaluationV1) else record.failure_ref
            )
            witnesses = resolve_witnesses(
                record.witnesses,
                terminal_failure_ref=terminal_failure_ref,
            )
            try:
                if isinstance(record, PassEvaluationV1):
                    if static is None:
                        raise ConfigurationError(
                            "invalid v1 report: missing PASS static evidence: "
                            f"{_safe_report_id(record.proposal_ref)}"
                        )
                    evaluation = PassEvaluation(
                        proposal=proposal,
                        static=static,
                        witnesses=witnesses,
                        verifier=VerifierPass(terminal=record.terminal),
                    )
                elif isinstance(record, VerifierRejectedEvaluationV1):
                    failure = failure_by_id.get(record.failure_ref)
                    authority = None if failure is None else failure.authority
                    if (
                        failure is None
                        or failure.cause != "VERIFIER_EXITED_NONZERO"
                        or failure.stage != "test"
                        or not isinstance(
                            authority,
                            ConfiguredVerifierFailureAuthority,
                        )
                        or not isinstance(authority.terminal, NormalExit)
                        or authority.terminal.exit_code == 0
                        or static is None
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: VERIFIER_REJECTED failure mismatch: "
                            f"{_safe_report_id(record.proposal_ref)}"
                        )
                    assert isinstance(
                        authority,
                        ConfiguredVerifierFailureAuthority,
                    )
                    assert isinstance(authority.terminal, NormalExit)
                    evaluation = VerifierRejectedEvaluation(
                        proposal=proposal,
                        static=static,
                        witnesses=witnesses,
                        verifier=VerifierRejected(terminal=authority.terminal),
                    )
                elif isinstance(record, RuntimeInterfaceMissingEvaluationV1):
                    failure = failure_by_id.get(record.failure_ref)
                    if (
                        failure is None
                        or failure.cause != "RUNTIME_INTERFACE_MISSING"
                        or failure.stage != "witness"
                        or static is None
                        or not isinstance(static, StaticRegressionEvaluation)
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: runtime-missing failure mismatch: "
                            f"{_safe_report_id(record.proposal_ref)}"
                        )
                    evaluation = RuntimeInterfaceMissingEvaluation(
                        proposal=proposal,
                        static=static,
                        witnesses=witnesses,
                    )
                else:
                    failure = failure_by_id.get(record.failure_ref)
                    if failure is None:
                        raise ConfigurationError(
                            "invalid v1 report: INDETERMINATE failure mismatch: "
                            f"{_safe_report_id(record.proposal_ref)}"
                        )
                    authority = failure.authority
                    if isinstance(authority, ConfiguredVerifierFailureAuthority):
                        terminal = authority.terminal
                        if isinstance(terminal, NormalExit):
                            raise ConfigurationError(
                                "invalid v1 report: indeterminate verifier has normal exit"
                            )
                        reason = (
                            "process-start-failed"
                            if isinstance(terminal, StartFailed)
                            else (
                                "process-timed-out"
                                if isinstance(terminal, TimedOut)
                                else (
                                    "process-signaled"
                                    if isinstance(terminal, Signaled)
                                    else "terminal-unavailable"
                                )
                            )
                        )
                        evaluation = IndeterminateEvaluation(
                            proposal=proposal,
                            cause=failure.cause,
                            verifier=VerifierIndeterminate(
                                terminal=terminal,
                                reason=reason,
                            ),
                            static=static,
                            witnesses=witnesses,
                        )
                    else:
                        if failure.process is None:
                            raise ConfigurationError(
                                "invalid v1 report: process INDETERMINATE has no process"
                            )
                        evaluation = IndeterminateEvaluation(
                            proposal=proposal,
                            cause=failure.cause,
                            failure=ToolFailure(
                                cause=failure.cause,
                                stage=failure.stage,
                                process=failure.process,
                                summary_code=failure.summary_code,
                            ),
                            static=static,
                            witnesses=witnesses,
                        )
                evaluation_by_proposal[record.proposal_ref] = evaluation
            except ValidationError as error:
                raise ConfigurationError(
                    "invalid v1 report: Evaluation mismatch: "
                    f"{_safe_report_id(record.proposal_ref)}"
                ) from error
        resolved_results: list[CellResult] = []
        referenced_failure_ids: list[str] = []
        referenced_attempt_ids: set[str] = set()
        referenced_proposal_ids: set[str] = set()
        referenced_candidate_ids: set[str] = set()
        referenced_static_ids: set[str] = set()
        referenced_evaluation_ids: set[str] = set()

        def resolve_search_evidence(
            observation_records: tuple[ProbeObservationV1, ...],
            region_records: tuple[StaticRegionV1, ...],
            *,
            cell: Cell,
            snapshots: tuple[CandidateSnapshot, ...],
        ) -> tuple[tuple[ProbeObservation, ...], tuple[StaticRegion, ...]]:
            resolved_observations: list[ProbeObservation | None] = [
                None for _ in observation_records
            ]
            direct_status_by_proposal: dict[
                str, Literal["PASS", "REJECTED", "INDETERMINATE"]
            ] = {}
            for index, observation_record in enumerate(observation_records):
                evidence_record = observation_record.evidence
                if isinstance(evidence_record, StaticOnlyEvidenceV1):
                    continue
                attempt = attempt_by_id.get(evidence_record.attempt_ref)
                proposal = proposal_by_attempt.get(evidence_record.attempt_ref)
                if (
                    attempt is None
                    or attempt.identity.cell != cell
                    or attempt.identity.requested_managed_vector is None
                ):
                    raise ConfigurationError(
                        "invalid v1 report: incomplete direct evidence: "
                        f"{_safe_report_id(evidence_record.attempt_ref)}"
                    )
                if isinstance(evidence_record, DirectPassV1):
                    evaluation = (
                        evaluation_by_proposal.get(proposal.proposal_id)
                        if proposal is not None
                        else None
                    )
                    if proposal is None or not isinstance(evaluation, PassEvaluation):
                        raise ConfigurationError(
                            "invalid v1 report: incomplete direct PASS evidence: "
                            f"{_safe_report_id(evidence_record.attempt_ref)}"
                        )
                    evidence = ProbePass(
                        attempt=attempt,
                        proposal_id=proposal.proposal_id,
                        evaluation=evaluation,
                    )
                elif isinstance(evidence_record, DirectRejectionV1):
                    failure = failure_by_id.get(evidence_record.failure_ref)
                    evaluation = (
                        evaluation_by_proposal.get(proposal.proposal_id)
                        if proposal is not None
                        else None
                    )
                    if failure is None:
                        raise ConfigurationError(
                            "invalid v1 report: incomplete Rejection evidence: "
                            f"{_safe_report_id(evidence_record.attempt_ref)}"
                        )
                    rejection_evaluation: (
                        VerifierRejectedEvaluation
                        | RuntimeInterfaceMissingEvaluation
                        | None
                    )
                    if proposal is None:
                        rejection_evaluation = None
                    elif isinstance(
                        evaluation,
                        (
                            VerifierRejectedEvaluation,
                            RuntimeInterfaceMissingEvaluation,
                        ),
                    ):
                        rejection_evaluation = evaluation
                    else:
                        raise ConfigurationError(
                            "invalid v1 report: incomplete Rejection evidence: "
                            f"{_safe_report_id(evidence_record.attempt_ref)}"
                        )
                    evidence = ProbeRejection(
                        attempt=attempt,
                        proposal_id=(
                            proposal.proposal_id if proposal is not None else None
                        ),
                        failure_id=failure.failure_id,
                        cause=failure.cause,
                        evaluation=rejection_evaluation,
                    )
                else:
                    failure = failure_by_id.get(evidence_record.failure_ref)
                    evaluation = (
                        evaluation_by_proposal.get(proposal.proposal_id)
                        if proposal is not None
                        else None
                    )
                    if failure is None:
                        raise ConfigurationError(
                            "invalid v1 report: incomplete INDETERMINATE evidence: "
                            f"{_safe_report_id(evidence_record.attempt_ref)}"
                        )
                    indeterminate_evaluation: IndeterminateEvaluation | None
                    if proposal is None:
                        indeterminate_evaluation = None
                    elif isinstance(evaluation, IndeterminateEvaluation):
                        indeterminate_evaluation = evaluation
                    else:
                        raise ConfigurationError(
                            "invalid v1 report: incomplete INDETERMINATE evidence: "
                            f"{_safe_report_id(evidence_record.attempt_ref)}"
                        )
                    evidence = ProbeIndeterminate(
                        attempt=attempt,
                        proposal_id=(
                            proposal.proposal_id if proposal is not None else None
                        ),
                        failure_id=failure.failure_id,
                        cause=failure.cause,
                        evaluation=indeterminate_evaluation,
                    )
                resolved_observations[index] = ProbeObservation(
                    dependency=observation_record.dependency,
                    candidate_version=observation_record.candidate_version,
                    vector=attempt.identity.requested_managed_vector,
                    evidence=evidence,
                )
                referenced_attempt_ids.add(attempt.attempt_id)
                if proposal is not None:
                    referenced_proposal_ids.add(proposal.proposal_id)
                    direct_status_by_proposal[proposal.proposal_id] = evidence.status
                    if proposal.proposal_id in static_by_proposal:
                        referenced_static_ids.add(proposal.proposal_id)
                    if proposal.proposal_id in evaluation_by_proposal:
                        referenced_evaluation_ids.add(proposal.proposal_id)

            snapshot_by_id = {item.digest: item for item in snapshots}
            region_by_id: dict[str, StaticRegion] = {}
            for region_record in region_records:
                if region_record.region_id in region_by_id:
                    raise ConfigurationError(
                        "invalid v1 report: duplicate local Region: "
                        f"{_safe_report_id(region_record.region_id)}"
                    )
                snapshot = snapshot_by_id.get(region_record.candidate_snapshot_ref)
                if snapshot is None:
                    raise ConfigurationError(
                        "invalid v1 report: Region CandidateSnapshot is not owned by "
                        "CellResult: "
                        f"{_safe_report_id(region_record.region_id)}"
                    )
                proposal_refs = tuple(
                    item.proposal_ref for item in region_record.runtime_references
                )
                if proposal_refs != tuple(sorted(set(proposal_refs))):
                    raise ConfigurationError(
                        "invalid v1 report: Region runtime refs must be sorted and "
                        "unique: "
                        f"{_safe_report_id(region_record.region_id)}"
                    )
                runtime_references = []
                for proposal_ref in proposal_refs:
                    proposal = proposal_by_id.get(proposal_ref)
                    status = direct_status_by_proposal.get(proposal_ref)
                    if proposal is None or proposal.cell != cell or status is None:
                        raise ConfigurationError(
                            "invalid v1 report: Region representative is not local "
                            "direct evidence: "
                            f"{_safe_report_id(proposal_ref)}"
                        )
                    runtime_references.append(
                        StaticRegionRuntimeReference(
                            proposal_id=proposal_ref,
                            status=status,
                        )
                    )
                    referenced_proposal_ids.add(proposal_ref)
                region = StaticRegion(
                    slice=StaticRegionSlice(
                        cell=cell,
                        source_snapshot_digest=source_snapshot.digest,
                        policy_identity=wire.identity.policy_identity,
                        baseline_digest=region_record.baseline_digest,
                        active_dependency=snapshot.dependency,
                        other_coordinates=region_record.other_coordinates,
                        candidate_order=tuple(
                            candidate.version for candidate in snapshot.candidates
                        ),
                    ),
                    static_fingerprint=region_record.static_fingerprint,
                    observed_versions=region_record.observed_versions,
                    runtime_references=tuple(runtime_references),
                )
                if static_region_id(region) != region_record.region_id:
                    raise ConfigurationError(
                        "invalid v1 report: Region identity mismatch: "
                        f"{_safe_report_id(region_record.region_id)}"
                    )
                region_by_id[region_record.region_id] = region
                referenced_candidate_ids.add(snapshot.digest)

            for index, observation_record in enumerate(observation_records):
                evidence_record = observation_record.evidence
                if not isinstance(evidence_record, StaticOnlyEvidenceV1):
                    continue
                attempt = attempt_by_id.get(evidence_record.attempt_ref)
                proposal = proposal_by_attempt.get(evidence_record.attempt_ref)
                region = region_by_id.get(evidence_record.region_ref)
                representative = proposal_by_id.get(
                    evidence_record.representative_proposal_ref
                )
                static = (
                    static_by_proposal.get(proposal.proposal_id)
                    if proposal is not None
                    else None
                )
                if (
                    attempt is None
                    or attempt.identity.cell != cell
                    or attempt.identity.requested_managed_vector is None
                    or proposal is None
                    or static is None
                    or region is None
                    or representative is None
                    or representative.cell != cell
                ):
                    raise ConfigurationError(
                        "invalid v1 report: incomplete static-only evidence: "
                        f"{_safe_report_id(evidence_record.attempt_ref)}"
                    )
                evidence = StaticOnlyEvidence(
                    attempt=attempt,
                    proposal_id=proposal.proposal_id,
                    static_evaluation=static,
                    guidance=evidence_record.guidance,
                    region_slice=region.slice,
                    representative_proposal_id=representative.proposal_id,
                )
                resolved_observations[index] = ProbeObservation(
                    dependency=observation_record.dependency,
                    candidate_version=observation_record.candidate_version,
                    vector=attempt.identity.requested_managed_vector,
                    evidence=evidence,
                )
                referenced_attempt_ids.add(attempt.attempt_id)
                referenced_proposal_ids.update(
                    (proposal.proposal_id, representative.proposal_id)
                )
                referenced_static_ids.add(proposal.proposal_id)
            if any(item is None for item in resolved_observations):
                raise ConfigurationError("invalid v1 report: unresolved Observation")
            return (
                tuple(item for item in resolved_observations if item is not None),
                tuple(region_by_id[item.region_id] for item in region_records),
            )

        for record in wire.cell_results:
            cell = cell_by_id.get(record.cell_ref)
            if cell is None:
                raise ConfigurationError(
                    "invalid v1 report: unknown Cell ref in CellResult "
                    f"{_safe_report_id(record.cell_ref)}"
                )
            if record.failure_refs != tuple(dict.fromkeys(record.failure_refs)):
                raise ConfigurationError(
                    "invalid v1 report: duplicate failure ref in CellResult "
                    f"{_safe_report_id(record.cell_ref)}"
                )
            failures = tuple(
                failure_by_id.get(reference) for reference in record.failure_refs
            )
            if any(failure is None for failure in failures):
                missing = next(
                    reference
                    for reference, failure in zip(record.failure_refs, failures)
                    if failure is None
                )
                raise ConfigurationError(
                    "invalid v1 report: unknown FailureRecord ref: "
                    f"{_safe_report_id(missing)}"
                )
            try:
                owned_failures = tuple(
                    failure for failure in failures if failure is not None
                )
                if isinstance(record, CellSuccessV1):
                    baseline_attempt = attempt_by_id.get(record.baseline.attempt_ref)
                    baseline_proposal = proposal_by_id.get(record.baseline.proposal_ref)
                    final_proposal = proposal_by_id.get(record.final_proposal_ref)
                    baseline_evaluation = evaluation_by_proposal.get(
                        record.baseline.proposal_ref
                    )
                    final_evaluation = evaluation_by_proposal.get(
                        record.final_proposal_ref
                    )
                    baseline_static = static_by_proposal.get(
                        record.baseline.proposal_ref
                    )
                    if (
                        baseline_attempt is None
                        or baseline_proposal is None
                        or final_proposal is None
                        or not isinstance(baseline_evaluation, PassEvaluation)
                        or not isinstance(final_evaluation, PassEvaluation)
                        or baseline_static is None
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: unknown baseline/final ref in CellResult "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    assert baseline_attempt is not None
                    assert baseline_proposal is not None
                    assert final_proposal is not None
                    assert baseline_static is not None
                    if (
                        baseline_attempt.identity.cell != cell
                        or baseline_proposal.cell != cell
                        or final_proposal.cell != cell
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: cross-Cell baseline/final ref: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    snapshots = tuple(
                        candidate_by_id.get(reference)
                        for reference in record.candidate_snapshot_refs
                    )
                    if any(snapshot is None for snapshot in snapshots):
                        missing = next(
                            reference
                            for reference, snapshot in zip(
                                record.candidate_snapshot_refs, snapshots
                            )
                            if snapshot is None
                        )
                        raise ConfigurationError(
                            "invalid v1 report: unknown CandidateSnapshot ref: "
                            f"{_safe_report_id(missing)}"
                        )
                    owned_snapshots = tuple(
                        snapshot for snapshot in snapshots if snapshot is not None
                    )
                    if tuple(item.dependency for item in owned_snapshots) != tuple(
                        sorted({item.dependency for item in owned_snapshots})
                    ) or any(item.cell != cell for item in owned_snapshots):
                        raise ConfigurationError(
                            "invalid v1 report: invalid CandidateSnapshot refs for Cell "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    observations, regions = resolve_search_evidence(
                        record.search.observations,
                        record.search.regions,
                        cell=cell,
                        snapshots=owned_snapshots,
                    )
                    boundaries = tuple(
                        CoordinateBoundary(
                            dependency=item.dependency,
                            floor=item.floor,
                            predecessor=item.predecessor,
                            predecessor_failure_id=(item.predecessor_failure_ref),
                        )
                        for item in record.search.boundaries
                    )
                    resolved = CellSuccess(
                        cell=cell,
                        baseline_attempt=baseline_attempt,
                        static_baseline=StaticBaseline(
                            proposal=baseline_proposal,
                            ty=baseline_static.ty,
                            digest=record.baseline.static_baseline_digest,
                        ),
                        baseline=baseline_evaluation,
                        candidate_snapshots=owned_snapshots,
                        search=CoordinateSuccess(
                            vector=final_proposal.managed_vector,
                            observations=tuple(observations),
                            boundaries=boundaries,
                            regions=regions,
                            sweeps=record.search.sweeps,
                        ),
                        final_vector=final_proposal.managed_vector,
                        final_evaluation=final_evaluation,
                        failure_records=owned_failures,
                    )
                    referenced_attempt_ids.add(baseline_attempt.attempt_id)
                    referenced_proposal_ids.update(
                        (baseline_proposal.proposal_id, final_proposal.proposal_id)
                    )
                    referenced_static_ids.update(
                        (baseline_proposal.proposal_id, final_proposal.proposal_id)
                    )
                    referenced_evaluation_ids.update(
                        (baseline_proposal.proposal_id, final_proposal.proposal_id)
                    )
                    referenced_candidate_ids.update(record.candidate_snapshot_refs)
                elif isinstance(record, CellIndeterminateV1):
                    if record.failure_ref not in record.failure_refs:
                        raise ConfigurationError(
                            "invalid v1 report: terminal failure is not owned by "
                            "CellResult "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    baseline_attempt = None
                    static_baseline = None
                    baseline_evaluation = None
                    owned_snapshots: tuple[CandidateSnapshot, ...] = ()
                    coordinate_failure = None
                    if record.baseline is not None:
                        baseline_attempt = attempt_by_id.get(
                            record.baseline.attempt_ref
                        )
                        baseline_proposal = proposal_by_id.get(
                            record.baseline.proposal_ref
                        )
                        baseline_evaluation = evaluation_by_proposal.get(
                            record.baseline.proposal_ref
                        )
                        baseline_static = static_by_proposal.get(
                            record.baseline.proposal_ref
                        )
                        if (
                            baseline_attempt is None
                            or baseline_proposal is None
                            or not isinstance(baseline_evaluation, PassEvaluation)
                            or baseline_static is None
                            or baseline_attempt.identity.cell != cell
                            or baseline_proposal.cell != cell
                        ):
                            raise ConfigurationError(
                                "invalid v1 report: incomplete indeterminate baseline: "
                                f"{_safe_report_id(record.cell_ref)}"
                            )
                        static_baseline = StaticBaseline(
                            proposal=baseline_proposal,
                            ty=baseline_static.ty,
                            digest=record.baseline.static_baseline_digest,
                        )
                        referenced_attempt_ids.add(baseline_attempt.attempt_id)
                        referenced_proposal_ids.add(baseline_proposal.proposal_id)
                        referenced_static_ids.add(baseline_proposal.proposal_id)
                        referenced_evaluation_ids.add(baseline_proposal.proposal_id)
                    elif (
                        record.candidate_snapshot_refs
                        or record.coordinate_failure is not None
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: search evidence requires baseline refs: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    candidate_snapshot_refs = record.candidate_snapshot_refs or ()
                    snapshots = tuple(
                        candidate_by_id.get(reference)
                        for reference in candidate_snapshot_refs
                    )
                    if any(snapshot is None for snapshot in snapshots):
                        raise ConfigurationError(
                            "invalid v1 report: unknown indeterminate "
                            "CandidateSnapshot ref: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    owned_snapshots = tuple(
                        snapshot for snapshot in snapshots if snapshot is not None
                    )
                    if tuple(item.dependency for item in owned_snapshots) != tuple(
                        sorted({item.dependency for item in owned_snapshots})
                    ) or any(item.cell != cell for item in owned_snapshots):
                        raise ConfigurationError(
                            "invalid v1 report: invalid indeterminate "
                            "CandidateSnapshot refs: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    referenced_candidate_ids.update(candidate_snapshot_refs)
                    if record.coordinate_failure is not None:
                        outcome_record = record.coordinate_failure
                        observations, regions = resolve_search_evidence(
                            outcome_record.observations,
                            outcome_record.regions,
                            cell=cell,
                            snapshots=owned_snapshots,
                        )
                        coordinate_failure = CoordinateFailure(
                            status=outcome_record.status,
                            dependency=outcome_record.dependency,
                            observations=observations,
                            regions=regions,
                            counterexample=outcome_record.counterexample,
                            failure_id=outcome_record.failure_ref,
                        )
                    resolved = CellIndeterminate(
                        cell=cell,
                        phase=record.phase,
                        failure_id=record.failure_ref,
                        failure_records=owned_failures,
                        baseline_attempt=baseline_attempt,
                        static_baseline=static_baseline,
                        baseline=baseline_evaluation,
                        candidate_snapshots=owned_snapshots,
                        coordinate_failure=coordinate_failure,
                    )
                elif isinstance(record, CellSearchFailureV1):
                    baseline_attempt = attempt_by_id.get(record.baseline.attempt_ref)
                    baseline_proposal = proposal_by_id.get(record.baseline.proposal_ref)
                    baseline_evaluation = evaluation_by_proposal.get(
                        record.baseline.proposal_ref
                    )
                    baseline_static = static_by_proposal.get(
                        record.baseline.proposal_ref
                    )
                    if (
                        baseline_attempt is None
                        or baseline_proposal is None
                        or not isinstance(baseline_evaluation, PassEvaluation)
                        or baseline_static is None
                        or baseline_attempt.identity.cell != cell
                        or baseline_proposal.cell != cell
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: incomplete search-failure baseline: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    snapshots = tuple(
                        candidate_by_id.get(reference)
                        for reference in record.candidate_snapshot_refs
                    )
                    if any(snapshot is None for snapshot in snapshots):
                        raise ConfigurationError(
                            "invalid v1 report: unknown search-failure "
                            "CandidateSnapshot ref: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    owned_snapshots = tuple(
                        snapshot for snapshot in snapshots if snapshot is not None
                    )
                    if tuple(item.dependency for item in owned_snapshots) != tuple(
                        sorted({item.dependency for item in owned_snapshots})
                    ) or any(item.cell != cell for item in owned_snapshots):
                        raise ConfigurationError(
                            "invalid v1 report: invalid search-failure "
                            "CandidateSnapshot refs: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    coordinate_failure = None
                    if record.coordinate_failure is not None:
                        outcome_record = record.coordinate_failure
                        observations, regions = resolve_search_evidence(
                            outcome_record.observations,
                            outcome_record.regions,
                            cell=cell,
                            snapshots=owned_snapshots,
                        )
                        coordinate_failure = CoordinateFailure(
                            status=outcome_record.status,
                            dependency=outcome_record.dependency,
                            observations=observations,
                            regions=regions,
                            counterexample=outcome_record.counterexample,
                            failure_id=outcome_record.failure_ref,
                        )
                    resolved = CellSearchFailure(
                        reason=record.reason,
                        cell=cell,
                        phase=record.phase,
                        baseline_attempt=baseline_attempt,
                        static_baseline=StaticBaseline(
                            proposal=baseline_proposal,
                            ty=baseline_static.ty,
                            digest=record.baseline.static_baseline_digest,
                        ),
                        baseline=baseline_evaluation,
                        candidate_snapshots=owned_snapshots,
                        coordinate_failure=coordinate_failure,
                        failure_records=owned_failures,
                    )
                    referenced_attempt_ids.add(baseline_attempt.attempt_id)
                    referenced_proposal_ids.add(baseline_proposal.proposal_id)
                    referenced_static_ids.add(baseline_proposal.proposal_id)
                    referenced_evaluation_ids.add(baseline_proposal.proposal_id)
                    referenced_candidate_ids.update(record.candidate_snapshot_refs)
                else:
                    if len(record.failure_refs) != 1:
                        raise ConfigurationError(
                            "invalid v1 report: baseline result must own one failure: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    attempt = attempt_by_id.get(record.attempt_ref)
                    if attempt is None:
                        raise ConfigurationError(
                            "invalid v1 report: unknown Attempt ref in CellResult "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    if attempt.identity.cell != cell:
                        raise ConfigurationError(
                            "invalid v1 report: cross-Cell Attempt ref: "
                            f"{_safe_report_id(record.attempt_ref)}"
                        )
                    referenced_attempt_ids.add(attempt.attempt_id)
                    failure = owned_failures[0]
                    proposal = (
                        proposal_by_id.get(record.proposal_ref)
                        if record.proposal_ref is not None
                        else None
                    )
                    evaluation = (
                        evaluation_by_proposal.get(record.proposal_ref)
                        if record.proposal_ref is not None
                        else None
                    )
                    static = (
                        static_by_proposal.get(record.proposal_ref)
                        if record.proposal_ref is not None
                        else None
                    )
                    if record.proposal_ref is not None and (
                        proposal is None
                        or evaluation is None
                        or proposal.attempt_id != attempt.attempt_id
                    ):
                        raise ConfigurationError(
                            "invalid v1 report: incomplete baseline Evaluation refs: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    if record.static_baseline_digest is not None and static is None:
                        raise ConfigurationError(
                            "invalid v1 report: missing baseline StaticEvaluation: "
                            f"{_safe_report_id(record.cell_ref)}"
                        )
                    static_baseline = (
                        StaticBaseline(
                            proposal=proposal,
                            ty=static.ty,
                            digest=record.static_baseline_digest,
                        )
                        if proposal is not None
                        and static is not None
                        and record.static_baseline_digest is not None
                        else None
                    )
                    if isinstance(record, BaselineRejectionV1):
                        if evaluation is not None and not isinstance(
                            evaluation, VerifierRejectedEvaluation
                        ):
                            raise ConfigurationError(
                                "invalid v1 report: baseline rejection terminal "
                                "mismatch: "
                                f"{_safe_report_id(record.cell_ref)}"
                            )
                        resolved = BaselineRejection(
                            attempt=attempt,
                            failure=failure,
                            static_baseline=static_baseline,
                            evaluation=evaluation,
                        )
                    else:
                        if evaluation is not None and not isinstance(
                            evaluation, IndeterminateEvaluation
                        ):
                            raise ConfigurationError(
                                "invalid v1 report: baseline indeterminate terminal "
                                "mismatch: "
                                f"{_safe_report_id(record.cell_ref)}"
                            )
                        resolved = BaselineIndeterminate(
                            attempt=attempt,
                            failure=failure,
                            static_baseline=static_baseline,
                            evaluation=evaluation,
                        )
                    if proposal is not None:
                        referenced_proposal_ids.add(proposal.proposal_id)
                        referenced_evaluation_ids.add(proposal.proposal_id)
                        if static is not None:
                            referenced_static_ids.add(proposal.proposal_id)
            except ValidationError as error:
                raise ConfigurationError(
                    "invalid v1 report: CellResult evidence mismatch: "
                    f"{_safe_report_id(record.cell_ref)}"
                ) from error
            resolved_results.append(resolved)
            referenced_failure_ids.extend(record.failure_refs)
        referenced_attempt_ids.update(
            scope.attempt.attempt_id
            for failure in failure_by_id.values()
            if isinstance((scope := failure.scope), AttemptFailureScope)
        )
        referenced_attempt_ids.update(
            proposal.attempt_id
            for proposal in proposal_by_id.values()
            if proposal.attempt_id is not None
        )
        ordered_result_keys = tuple(
            cell_identity(result.cell) for result in resolved_results
        )
        if ordered_result_keys != tuple(sorted(set(ordered_result_keys))):
            raise ConfigurationError(
                "invalid v1 report: CellResults must be sorted and unique"
            )
        if set(referenced_failure_ids) != set(failure_by_id):
            raise ConfigurationError(
                "invalid v1 report: unreachable or unowned FailureRecord"
            )
        if referenced_attempt_ids != set(attempt_by_id):
            raise ConfigurationError("invalid v1 report: unreachable Attempt")
        if referenced_proposal_ids != set(proposal_by_id):
            raise ConfigurationError("invalid v1 report: unreachable Proposal")
        if referenced_candidate_ids != set(candidate_by_id):
            raise ConfigurationError("invalid v1 report: unreachable CandidateSnapshot")
        referenced_graph_ids = {
            resolution_graph_id(proposal.resolved_graph)
            for proposal in proposal_by_id.values()
        }
        if referenced_graph_ids != set(graph_by_id):
            raise ConfigurationError("invalid v1 report: unreachable ResolutionGraph")
        if referenced_static_ids != set(static_by_proposal):
            raise ConfigurationError("invalid v1 report: unreachable StaticEvaluation")
        if referenced_evaluation_ids != set(evaluation_by_proposal):
            raise ConfigurationError("invalid v1 report: unreachable Evaluation")
        cell_results = tuple(resolved_results)
        declaration_by_id = {
            declaration.declaration_id: declaration for declaration in declarations
        }
        projection_refs = tuple(item.declaration_ref for item in wire.projections)
        if projection_refs != tuple(sorted(set(projection_refs))):
            raise ConfigurationError(
                "invalid v1 report: projections must be sorted and unique"
            )
        projection_evidence: list[ProjectionEvidence] = []
        for record in wire.projections:
            declaration = declaration_by_id.get(record.declaration_ref)
            if declaration is None:
                raise ConfigurationError(
                    "invalid v1 report: unknown projection declaration ref: "
                    f"{_safe_report_id(record.declaration_ref)}"
                )
            floors: list[FloorProjection] = []
            for floor_record in record.floors:
                floor_cell = cell_by_id.get(floor_record.cell_ref)
                if floor_cell is None:
                    raise ConfigurationError(
                        "invalid v1 report: unknown projection Cell ref: "
                        f"{_safe_report_id(floor_record.cell_ref)}"
                    )
                floors.append(
                    FloorProjection(
                        cell=floor_cell,
                        version=floor_record.version,
                    )
                )
            projection = ProjectionEvidence(
                declaration_id=record.declaration_ref,
                floors=tuple(floors),
                projected_requirements=record.projected_requirements,
                representable=record.representable,
            )
            active_cells = tuple(
                cell
                for cell in target_cells
                if declaration.declaration_id in cell.active_declaration_ids
            )
            expected_projection = PackageReportBuilder().project_declaration(
                declaration=declaration,
                target_cells=target_cells,
                active_cells=active_cells,
                floors=projection.floors,
            )
            if projection != expected_projection:
                raise ConfigurationError(
                    "invalid v1 report: projection evidence mismatch: "
                    f"{_safe_report_id(record.declaration_ref)}"
                )
            projection_evidence.append(projection)
        expected_projection_refs = {
            declaration.declaration_id
            for declaration in declarations
            if declaration.managed
            and any(
                declaration.declaration_id in cell.active_declaration_ids
                for cell in target_cells
            )
        }
        if set(projection_refs) != expected_projection_refs:
            raise ConfigurationError("invalid v1 report: projection coverage mismatch")
        expected_generation = report_generation_id(
            generator=wire.identity.generator,
            package=wire.identity.package,
            source_snapshot=source_snapshot,
            policy_identity=wire.identity.policy_identity,
            verifier_outcome_policy=wire.identity.verifier_outcome_policy,
            source_plan=source_plan,
            requirement_declarations=declarations,
            target_cells=target_cells,
        )
        if wire.identity.report_generation_id != expected_generation:
            raise ConfigurationError(
                "invalid v1 report: report generation identity mismatch"
            )
        result_keys = {cell_identity(result.cell) for result in cell_results}
        target_keys = set(keys)
        reasons = {
            reason
            for result in cell_results
            if (reason := incomplete_reason(result)) is not None
        }
        if result_keys != target_keys:
            reasons.add("MISSING_CELL")
        all_success = (
            bool(target_cells)
            and result_keys == target_keys
            and all(isinstance(result, CellSuccess) for result in cell_results)
        )
        expected_result: CompleteReportResult | IncompleteReportResult
        all_representable = all(
            projection.representable for projection in projection_evidence
        )
        if not all_representable:
            reasons.add("UNREPRESENTABLE_PROJECTION")
        if all_success and all_representable:
            expected_result = CompleteReportResult(status="complete")
        else:
            expected_result = IncompleteReportResult(
                status="incomplete",
                reasons=tuple(sorted(reasons)),
            )
        if wire.result != expected_result:
            raise ConfigurationError(
                "invalid v1 report: result does not match target Cell coverage"
            )
        return ValidatedReport(
            report_generation_id=wire.identity.report_generation_id,
            generator=wire.identity.generator,
            package=wire.identity.package,
            source_snapshot=source_snapshot,
            policy_identity=wire.identity.policy_identity,
            verifier_outcome_policy=wire.identity.verifier_outcome_policy,
            source_plan=source_plan,
            requirement_declarations=declarations,
            target_cells=target_cells,
            cell_results=cell_results,
            projection_evidence=tuple(projection_evidence),
            result=wire.result,
            failure_records=tuple(
                failure_by_id[reference]
                for result in wire.cell_results
                for reference in result.failure_refs
            ),
            _wire=wire,
        )

    def merge(
        self,
        reports: tuple[ValidatedReport, ...],
    ) -> ValidatedReport:
        if not reports:
            raise ConfigurationError("merge requires at least one report")
        first = reports[0]
        for report in reports[1:]:
            self._validate_generation(first, report)

        cell_results: dict[CellKey, CellResult] = {}
        for report in reports:
            for result in report.cell_results:
                key = self._cell_key(result.cell)
                existing = cell_results.get(key)
                if existing is not None and existing != result:
                    raise ConfigurationError(f"conflicting result for cell: {key}")
                cell_results[key] = result
        if not cell_results:
            return first
        return self._reintern(
            first,
            tuple(cell_results[key] for key in sorted(cell_results)),
        )

    def update(
        self,
        existing: ValidatedReport,
        replacement: ValidatedReport,
    ) -> ValidatedReport:
        """Replace cells produced by one search while retaining other-host cells."""
        self._validate_generation(existing, replacement)
        replaced_keys = {
            self._cell_key(result.cell) for result in replacement.cell_results
        }
        if not replaced_keys:
            return existing
        final_by_cell = {
            self._cell_key(result.cell): result
            for result in existing.cell_results
            if self._cell_key(result.cell) not in replaced_keys
        }
        final_by_cell.update(
            {self._cell_key(result.cell): result for result in replacement.cell_results}
        )
        return self._reintern(
            existing,
            tuple(final_by_cell[key] for key in sorted(final_by_cell)),
        )

    def update_path(
        self,
        path: Path,
        replacement: ValidatedReport,
    ) -> ReportUpdate:
        if not path.exists():
            self.write(path, replacement)
            return ReportUpdate(
                report=replacement,
                replace_generation=True,
                removed_failure_ids=(),
            )
        existing = self.read(path)
        if existing.report_generation_id != replacement.report_generation_id:
            self.write(path, replacement)
            return ReportUpdate(
                report=replacement,
                replace_generation=True,
                removed_failure_ids=(),
            )
        replaced_keys = {
            self._cell_key(result.cell) for result in replacement.cell_results
        }
        removed_failure_ids = tuple(
            sorted(
                {
                    failure.failure_id
                    for result in existing.cell_results
                    if self._cell_key(result.cell) in replaced_keys
                    for failure in failure_records_for_result(result)
                }
            )
        )
        updated = self.update(existing, replacement)
        self.write(path, updated)
        return ReportUpdate(
            report=updated,
            replace_generation=False,
            removed_failure_ids=removed_failure_ids,
        )

    @staticmethod
    def _reintern(
        generation: ValidatedReport,
        cell_results: tuple[CellResult, ...],
    ) -> ValidatedReport:
        package = PackagePlan(
            name=generation.package.name,
            pyproject_path=generation.package.pyproject_path,
            requires_python=generation.package.requires_python,
            config=EffectiveConfig(),
            declarations=generation.requirement_declarations,
            cells=generation.target_cells,
            source_plan=generation.source_plan,
        )
        rebuilt = PackageReportBuilder().build(
            package=package,
            source_snapshot=generation.source_snapshot,
            cell_results=cell_results,
            _generator=generation.generator,
            _policy_identity=generation.policy_identity,
        )
        if rebuilt.report_generation_id != generation.report_generation_id:
            raise ConfigurationError(
                "report generation identity changed while reinterning roots"
            )
        return rebuilt

    @classmethod
    def _validate_generation(
        cls,
        left: ValidatedReport,
        right: ValidatedReport,
    ) -> None:
        if left.report_generation_id != right.report_generation_id:
            raise ConfigurationError("report generation identity mismatch")
        for field_name, label in cls._GENERATION_FIELDS:
            if getattr(left, field_name) != getattr(right, field_name):
                raise ConfigurationError(f"report {label} identity mismatch")

    @staticmethod
    def _cell_key(cell: Cell) -> CellKey:
        return cell_identity(cell)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

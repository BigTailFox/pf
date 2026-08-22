from __future__ import annotations

import re
from typing import Literal, Protocol

from pf.coordinate_search import CoordinateSearch
from pf.errors import ConfigurationError, InfrastructureError, NoApplicableFloorError
from pf.environment import PreparedEnvironment
from pf.evaluation import EvaluationCache, require_full_evaluation_contract
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    CacheConflict,
    Evaluation,
    FailureDetail,
    FailureCause,
    FailureRecord,
    CellFailureScope,
    HighestVersionOutcome,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    PrepareFailure,
    SearchFailureEvent,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFailEvaluation,
    ToolFailure,
)
from pf.schemas.project import (
    CandidateSnapshot,
    Cell,
    PackagePlan,
    SelectedCandidate,
    VersionPin,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellResult,
    CellSearchFailure,
    CellSuccess,
    CoordinateFailure,
    CoordinateSuccess,
    ProbeEvidence,
    ProbeIndeterminate,
    ProbeObservation,
    ProbePass,
    ProbeRejection,
)
from pf.snapshot import SourceSnapshot


def select_probe(
    vector: tuple[VersionPin, ...],
    snapshots: tuple[CandidateSnapshot, ...],
) -> tuple[SelectedCandidate, ...]:
    """Bind an exact vector to the frozen artifact selected for each version."""
    vector_by_name = {pin.name: pin.version for pin in vector}
    snapshot_by_name = {snapshot.dependency: snapshot for snapshot in snapshots}
    if len(vector_by_name) != len(vector) or len(snapshot_by_name) != len(snapshots):
        raise ConfigurationError("probe dependencies must be unique")
    if set(vector_by_name) != set(snapshot_by_name):
        raise ConfigurationError(
            "probe vector and candidate snapshots must cover the same dependencies"
        )
    selected: list[SelectedCandidate] = []
    for dependency in sorted(vector_by_name):
        matches = tuple(
            candidate
            for candidate in snapshot_by_name[dependency].candidates
            if candidate.version == vector_by_name[dependency]
        )
        if len(matches) != 1:
            raise ConfigurationError(
                f"probe version is not uniquely frozen for dependency: {dependency}"
            )
        candidate = matches[0]
        artifact = candidate.artifact
        if (
            not artifact.filename.strip()
            or artifact.locator is None
            or not artifact.locator.strip()
            or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", artifact.content_hash) is None
        ):
            raise ConfigurationError(
                f"probe artifact is incomplete for dependency: {dependency}"
            )
        selected.append(
            SelectedCandidate(
                dependency=dependency,
                version=candidate.version,
                artifact=artifact,
            )
        )
    return tuple(selected)


class SearchEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
        selection: tuple[SelectedCandidate, ...] | None = None,
    ) -> PreparedEnvironment | PrepareFailure: ...


class CandidateOperations(Protocol):
    def build(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        baseline: tuple[VersionPin, ...],
    ) -> tuple[CandidateSnapshot, ...]: ...


class StaticOperations(Protocol):
    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture | IndeterminateEvaluation: ...

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
    ) -> StaticEvaluation: ...


class FullOperations(Protocol):
    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> Evaluation: ...


class HighestOperations(Protocol):
    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> HighestVersionOutcome: ...


class SearchDiagnosticConsumer(Protocol):
    def consume(self, event: SearchFailureEvent) -> None: ...


class _StaticVectorEvaluator:
    def __init__(self, runner: "_ProposalRunner") -> None:
        self._runner = runner

    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        return self._runner.evaluate_static(vector)


class _FullVectorEvaluator:
    def __init__(self, runner: "_ProposalRunner") -> None:
        self._runner = runner

    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        return self._runner.evaluate_full(vector)


class _ProposalRunner:
    def __init__(
        self,
        *,
        environments: SearchEnvironmentOperations,
        static: StaticOperations,
        full: FullOperations,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        static_baseline: StaticBaseline,
        candidate_snapshots: tuple[CandidateSnapshot, ...],
        diagnostics: SearchDiagnosticConsumer | None = None,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._package = package
        self._cell = cell
        self._snapshot = snapshot
        self._static_baseline = static_baseline
        self._candidate_snapshots = candidate_snapshots
        self._diagnostics = diagnostics
        self._failures = failures or FailurePolicy()
        self._failure_records: dict[str, FailureRecord] = {}
        self._emitted_diagnostics: set[str] = set()
        self._cache = EvaluationCache()
        self._prepared: dict[tuple[tuple[str, str], ...], PreparedEnvironment] = {}
        self._prepare_failures: dict[tuple[tuple[str, str], ...], PrepareFailure] = {}
        self._attempts: dict[tuple[tuple[str, str], ...], Attempt] = {}
        self._evaluations: dict[tuple[tuple[str, str], ...], Evaluation] = {}
        self._full_evidence_by_key: dict[
            tuple[tuple[str, str], ...], ProbeEvidence
        ] = {}

    def evaluate_static(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        key = self._key(vector)
        full = self._evaluations.get(key)
        if isinstance(full, PassEvaluation):
            return self._pass_evidence(
                self._attempts[key],
                full,
            )
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            return self._prepare_evidence(prepared)
        assert prepared.attempt is not None
        try:
            cached = self._cache.get_static(
                prepared.proposal.proposal_id,
                baseline_digest=self._static_baseline.digest,
            )
            if cached is None:
                result = self._static.evaluate(
                    prepared,
                    package=self._package,
                    baseline=self._static_baseline,
                )
                stored = self._cache.record_static(
                    result,
                    baseline_digest=self._static_baseline.digest,
                )
                if isinstance(stored, CacheConflict):
                    return self._indeterminate_evidence(
                        attempt=prepared.attempt,
                        proposal_id=prepared.proposal.proposal_id,
                        stage="static-cache",
                        detail=FailureDetail(
                            code="conflicting-static-evaluation",
                            message=(
                                "the same proposal produced conflicting static "
                                "results"
                            ),
                        ),
                    )
            else:
                result = cached
            return self._static_evidence(prepared, result)
        finally:
            prepared.close()
            self._prepared.pop(key, None)

    def evaluate_full(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        key = self._key(vector)
        existing = self._full_evidence_by_key.get(key)
        if existing is not None:
            return existing
        prepared = self._prepare(vector)
        if isinstance(prepared, PrepareFailure):
            return self._prepare_evidence(prepared)
        assert prepared.attempt is not None
        try:
            static = self._cache.get_static(
                prepared.proposal.proposal_id,
                baseline_digest=self._static_baseline.digest,
            )
            result = self._full.evaluate(
                prepared,
                package=self._package,
                baseline=self._static_baseline,
                static_result=static,
            )
            stored = self._cache.record_full(
                result,
                baseline_digest=self._static_baseline.digest,
            )
            if isinstance(stored, CacheConflict):
                evidence = self._indeterminate_evidence(
                    attempt=prepared.attempt,
                    proposal_id=prepared.proposal.proposal_id,
                    stage="full-cache",
                    detail=FailureDetail(
                        code="conflicting-full-evaluation",
                        message="the same proposal produced conflicting full results",
                    ),
                )
            else:
                self._evaluations[key] = stored
                evidence = self._full_evidence(prepared, stored)
            self._full_evidence_by_key[key] = evidence
            return evidence
        finally:
            prepared.close()
            self._prepared.pop(key, None)

    def full_evaluation(self, vector: tuple[VersionPin, ...]) -> Evaluation | None:
        return self._evaluations.get(self._key(vector))

    @property
    def failure_records(self) -> tuple[FailureRecord, ...]:
        return tuple(self._failure_records.values())

    def failure_record(self, failure_id: str) -> FailureRecord:
        return self._failure_records[failure_id]

    def close(self) -> None:
        for prepared in self._prepared.values():
            prepared.close()
        self._prepared.clear()

    def _record(
        self,
        failure: FailureRecord,
        *,
        evaluation: StaticFailEvaluation
        | TestFailEvaluation
        | IndeterminateEvaluation
        | None,
    ) -> None:
        existing = self._failure_records.get(failure.failure_id)
        if existing is not None and existing != failure:
            raise ValueError("failure ID collision within one cell search")
        self._failure_records.setdefault(failure.failure_id, failure)
        if self._diagnostics is None or failure.failure_id in self._emitted_diagnostics:
            return
        self._emitted_diagnostics.add(failure.failure_id)
        self._diagnostics.consume(
            SearchFailureEvent(
                cell=self._cell,
                failure=failure,
                evaluation=evaluation,
            )
        )

    def _prepare(
        self,
        vector: tuple[VersionPin, ...],
    ) -> PreparedEnvironment | PrepareFailure:
        key = self._key(vector)
        existing = self._prepared.get(key)
        if existing is not None:
            return existing
        failed = self._prepare_failures.get(key)
        if failed is not None:
            return failed
        prepared = self._environments.prepare(
            package=self._package,
            cell=self._cell,
            snapshot=self._snapshot,
            resolution="highest",
            selection=select_probe(vector, self._candidate_snapshots),
        )
        if isinstance(prepared, PrepareFailure):
            self._prepare_failures[key] = prepared
            return prepared
        if isinstance(prepared, ToolFailure):
            raise ValueError("probe prepare must establish an Attempt")
        if prepared.attempt is None:
            prepared.close()
            raise ValueError("probe prepare must retain its attempt")
        if self._key(prepared.proposal.managed_vector) != key:
            prepared.close()
            return PrepareFailure(
                attempt=prepared.attempt,
                failure=ToolFailure(
                    cause="INTERNAL_INVARIANT",
                    stage="proposal-vector",
                    process=self._synthetic_process(),
                ),
            )
        self._prepared[key] = prepared
        self._attempts[key] = prepared.attempt
        return prepared

    def _prepare_evidence(self, prepared: PrepareFailure) -> ProbeEvidence:
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=None,
            cause=prepared.failure.cause,
            stage=prepared.failure.stage,
            process=prepared.failure.process,
            summary_code=prepared.failure.summary_code,
            evaluation=None,
        )

    def _static_evidence(
        self,
        prepared: PreparedEnvironment,
        result: StaticEvaluation,
    ) -> ProbeEvidence:
        assert prepared.attempt is not None
        if isinstance(result, StaticPassEvaluation):
            return self._pass_evidence(prepared.attempt, result)
        failure = self._failures.classify_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            result,
        )
        assert failure is not None
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=result.proposal.proposal_id,
            cause=failure.cause,
            stage=failure.stage,
            process=failure.process,
            summary_code=failure.summary_code,
            evaluation=result,
            record=failure,
        )

    def _full_evidence(
        self,
        prepared: PreparedEnvironment,
        result: Evaluation,
    ) -> ProbeEvidence:
        assert prepared.attempt is not None
        if isinstance(result, PassEvaluation):
            return self._pass_evidence(prepared.attempt, result)
        failure = self._failures.classify_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            result,
        )
        assert failure is not None
        return self._failure_evidence(
            attempt=prepared.attempt,
            proposal_id=result.proposal.proposal_id,
            cause=failure.cause,
            stage=failure.stage,
            process=failure.process,
            summary_code=failure.summary_code,
            evaluation=result,
            record=failure,
        )

    @staticmethod
    def _pass_evidence(
        attempt: Attempt,
        evaluation: StaticPassEvaluation | PassEvaluation,
    ) -> ProbeEvidence:
        proposal = evaluation.proposal
        if proposal.attempt_id is None:
            raise ValueError("probe proposal must reference its attempt")
        return ProbePass(
            attempt=attempt,
            proposal_id=proposal.proposal_id,
            evaluation=evaluation,
        )

    def _failure_evidence(
        self,
        *,
        attempt: Attempt,
        proposal_id: str | None,
        cause: FailureCause,
        stage: str,
        process: ProcessResult | None,
        evaluation: StaticFailEvaluation
        | TestFailEvaluation
        | IndeterminateEvaluation
        | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
        record: FailureRecord | None = None,
    ) -> ProbeEvidence:
        failure = record or self._failures.classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
        )
        self._record(failure, evaluation=evaluation)
        if failure.disposition == "REJECTED":
            assert not isinstance(evaluation, IndeterminateEvaluation)
            return ProbeRejection(
                attempt=attempt,
                proposal_id=proposal_id,
                failure_id=failure.failure_id,
                cause=failure.cause,
                evaluation=evaluation,
            )
        indeterminate_evaluation = (
            evaluation if isinstance(evaluation, IndeterminateEvaluation) else None
        )
        return ProbeIndeterminate(
            attempt=attempt,
            proposal_id=proposal_id,
            failure_id=failure.failure_id,
            cause=failure.cause,
            evaluation=indeterminate_evaluation,
        )

    def _indeterminate_evidence(
        self,
        *,
        attempt: Attempt,
        proposal_id: str,
        stage: str,
        detail: FailureDetail,
    ) -> ProbeEvidence:
        return self._failure_evidence(
            attempt=attempt,
            proposal_id=proposal_id,
            cause="NONDETERMINISTIC",
            stage=stage,
            process=None,
            detail=detail,
            evaluation=None,
        )

    @staticmethod
    def _key(vector: tuple[VersionPin, ...]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((pin.name, pin.version) for pin in vector))

    @staticmethod
    def _synthetic_process() -> ProcessResult:
        return ProcessResult(
            exit_code=None,
            signal=None,
            duration_seconds=0,
            stderr="proposal vector drift",
            start_error="proposal vector drift",
        )


class SearchCoordinator:
    """Own the complete baseline, static, fast-path, and dynamic cell state machine."""

    def __init__(
        self,
        *,
        environments: SearchEnvironmentOperations,
        candidates: CandidateOperations,
        static: StaticOperations,
        full: FullOperations,
        highest: HighestOperations,
        coordinate_search: CoordinateSearch,
        diagnostics: SearchDiagnosticConsumer | None = None,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._candidates = candidates
        self._static = static
        self._full = full
        self._failures = failures or FailurePolicy()
        self._highest = highest
        self._diagnostics = diagnostics
        self._coordinate_search = coordinate_search

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CellResult:
        require_full_evaluation_contract(package, "search")
        capture = self._highest.verify(
            package=package,
            cell=cell,
            snapshot=snapshot,
        )
        if isinstance(capture, (BaselineRejection, BaselineIndeterminate)):
            return capture
        baseline_evaluation = capture.evaluation
        try:
            candidate_snapshots = self._candidates.build(
                package=package,
                cell=cell,
                baseline=baseline_evaluation.proposal.managed_vector,
            )
        except InfrastructureError:
            failure = self._failures.classify(
                scope=CellFailureScope(
                    package=package.name,
                    cell=cell,
                    source_snapshot_digest=snapshot.identity.digest,
                    evaluation_policy_identity=baseline_evaluation.proposal.policy_identity,
                ),
                cause="SOURCE_FAILURE",
                stage="candidate-discovery",
                process=None,
                detail=FailureDetail(
                    code="candidate-discovery-failed",
                    message="candidate discovery failed",
                ),
            )
            return CellIndeterminate(
                cell=cell,
                phase="candidate-discovery",
                failure_id=failure.failure_id,
                failure_records=(failure,),
                baseline_attempt=capture.attempt,
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
            )
        except NoApplicableFloorError:
            return CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=cell,
                phase="candidate-discovery",
                baseline_attempt=capture.attempt,
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
            )
        runner = _ProposalRunner(
            environments=self._environments,
            static=self._static,
            full=self._full,
            package=package,
            cell=cell,
            snapshot=snapshot,
            static_baseline=capture.baseline,
            candidate_snapshots=candidate_snapshots,
            diagnostics=self._diagnostics,
            failures=self._failures,
        )
        try:
            static_search = self._coordinate_search.minimize(
                start=baseline_evaluation.proposal.managed_vector,
                candidates=candidate_snapshots,
                evaluator=_StaticVectorEvaluator(runner),
                start_is_known_pass=True,
            )
            if isinstance(static_search, CoordinateFailure):
                return self._coordinate_failure(
                    cell=cell,
                    phase="static-search",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidates=candidate_snapshots,
                    outcome=static_search,
                    runner=runner,
                    baseline_attempt=capture.attempt,
                )
            fast_evidence = runner.evaluate_full(static_search.vector)
            static_search = self._append_observation(
                static_search,
                vector=static_search.vector,
                evidence=fast_evidence,
            )
            fast_evaluation = runner.full_evaluation(static_search.vector)
            if fast_evidence.status == "PASS" and isinstance(
                fast_evaluation, PassEvaluation
            ):
                return CellSuccess(
                    cell=cell,
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    static_search=static_search,
                    final_vector=static_search.vector,
                    final_evaluation=fast_evaluation,
                    failure_records=runner.failure_records,
                )
            if isinstance(fast_evidence, ProbeIndeterminate):
                return CellIndeterminate(
                    cell=cell,
                    phase="static-fast-path",
                    failure_id=fast_evidence.failure_id,
                    failure_records=runner.failure_records,
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    coordinate_failure=CoordinateFailure(
                        status="INDETERMINATE",
                        observations=static_search.observations,
                        failure_id=fast_evidence.failure_id,
                    ),
                )
            if not (
                isinstance(fast_evidence, ProbeRejection)
                and fast_evidence.cause == "TEST_FAILURE"
            ):
                return CellSearchFailure(
                    reason="NONDETERMINISTIC",
                    cell=cell,
                    phase="static-fast-path",
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    coordinate_failure=CoordinateFailure(
                        status="NONDETERMINISTIC",
                        observations=static_search.observations,
                    ),
                    failure_records=runner.failure_records,
                )
            dynamic_search = self._coordinate_search.minimize(
                start=baseline_evaluation.proposal.managed_vector,
                candidates=candidate_snapshots,
                evaluator=_FullVectorEvaluator(runner),
                hints=static_search.vector,
                start_is_known_pass=True,
            )
            if isinstance(dynamic_search, CoordinateFailure):
                return self._coordinate_failure(
                    cell=cell,
                    phase="dynamic-search",
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidates=candidate_snapshots,
                    outcome=dynamic_search,
                    runner=runner,
                    baseline_attempt=capture.attempt,
                )
            final_evidence = runner.evaluate_full(dynamic_search.vector)
            dynamic_search = self._append_observation(
                dynamic_search,
                vector=dynamic_search.vector,
                evidence=final_evidence,
            )
            final_evaluation = runner.full_evaluation(dynamic_search.vector)
            if not isinstance(final_evidence, ProbePass) or not isinstance(
                final_evaluation, PassEvaluation
            ):
                return CellSearchFailure(
                    reason="NONDETERMINISTIC",
                    cell=cell,
                    phase="dynamic-final",
                    baseline_attempt=capture.attempt,
                    static_baseline=capture.baseline,
                    baseline=baseline_evaluation,
                    candidate_snapshots=candidate_snapshots,
                    failure_records=runner.failure_records,
                )
            return CellSuccess(
                cell=cell,
                baseline_attempt=capture.attempt,
                static_baseline=capture.baseline,
                baseline=baseline_evaluation,
                candidate_snapshots=candidate_snapshots,
                static_search=static_search,
                dynamic_search=dynamic_search,
                final_vector=dynamic_search.vector,
                final_evaluation=final_evaluation,
                failure_records=runner.failure_records,
            )
        finally:
            runner.close()

    @staticmethod
    def _append_observation(
        search: CoordinateSuccess,
        *,
        vector: tuple[VersionPin, ...],
        evidence: ProbeEvidence,
    ) -> CoordinateSuccess:
        if any(
            observation.vector == vector
            and observation.evidence.attempt == evidence.attempt
            and observation.evidence.status == evidence.status
            for observation in search.observations
        ):
            return search
        return search.model_copy(
            update={
                "observations": (
                    *search.observations,
                    ProbeObservation(
                        dependency=None,
                        candidate_version=None,
                        vector=vector,
                        evidence=evidence,
                    ),
                )
            }
        )

    @staticmethod
    def _coordinate_failure(
        *,
        cell: Cell,
        phase: str,
        static_baseline: StaticBaseline,
        baseline: PassEvaluation,
        candidates: tuple[CandidateSnapshot, ...],
        outcome: CoordinateFailure,
        runner: _ProposalRunner,
        baseline_attempt: Attempt,
    ) -> CellIndeterminate | CellSearchFailure:
        if outcome.status == "INDETERMINATE":
            assert outcome.failure_id is not None
            return CellIndeterminate(
                cell=cell,
                phase=phase,
                failure_id=outcome.failure_id,
                failure_records=runner.failure_records,
                baseline_attempt=baseline_attempt,
                static_baseline=static_baseline,
                baseline=baseline,
                candidate_snapshots=candidates,
                coordinate_failure=outcome,
            )
        return CellSearchFailure(
            reason=outcome.status,
            cell=cell,
            phase=phase,
            baseline_attempt=baseline_attempt,
            static_baseline=static_baseline,
            baseline=baseline,
            candidate_snapshots=candidates,
            coordinate_failure=outcome,
            failure_records=runner.failure_records,
        )

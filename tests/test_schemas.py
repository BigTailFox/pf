from __future__ import annotations

from typing import Literal

import pytest

from conftest import empty_harness_baseline
from pydantic import ValidationError

from pf.failure import FailurePolicy
from pf.schemas.base import FrozenSchema
from pf.schemas.config import (
    CheckRequest,
    EffectiveConfig,
    MergeRequest,
    SearchRequest,
    SmokeRequest,
)
from pf.schemas.evaluation import (
    ActivityEvent,
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellFailed,
    CellFailureScope,
    CellStageEvent,
    CellSucceeded,
    DiagnosticClassification,
    FailureDetail,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    ProcessSpec,
    PytestFailureCase,
    PytestFailureDetail,
    SearchFailureEvent,
    SmokeBaselineRejection,
    SmokeIndeterminate,
    StaticBaseline,
    StaticBaselineCapture,
    StaticRegressionEvaluation,
    StaticUnchangedEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    Proposal,
    SelectedCandidate,
    SourceIdentity,
    VersionPin,
    candidate_snapshot_digest,
    cell_identity,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateSuccess,
    ProbeObservation,
    ProbeIndeterminate,
    ProbePass,
    ProbeRejection,
    StaticOnlyEvidence,
    StaticRegion,
    StaticRegionRuntimeReference,
    StaticRegionSlice,
)
from pf.static_transition import static_fingerprint


class ExampleRecord(FrozenSchema):
    name: str


def _successful_process(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0,
        stdout="",
        stderr="",
    )


def _attempt(
    *,
    resolution: Literal["highest", "exact-vector"] = "highest",
    vector: tuple[VersionPin, ...] | None = None,
    cell: Cell | None = None,
) -> Attempt:
    attempt_cell = cell or Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=attempt_cell,
            requested_resolution=resolution,
            requested_managed_vector=vector,
            active_declaration_ids=attempt_cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )


def _proposal(
    proposal_id: str,
    *,
    attempt: Attempt | None = None,
    vector: tuple[VersionPin, ...] = (),
) -> Proposal:
    owned_attempt = attempt or _attempt()
    return Proposal(
        proposal_id=proposal_id,
        attempt_id=owned_attempt.attempt_id,
        snapshot_digest="snapshot",
        cell=owned_attempt.identity.cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
    )


def _diagnostic(*, line: int = 1, code: str = "invalid-type") -> TyDiagnostic:
    return TyDiagnostic(
        identity=f"snapshot|demo.py|{line}|2|{code}",
        origin="snapshot",
        path="demo.py",
        line=line,
        column=2,
        code=code,
        severity="major",
        message="message",
    )


def _general_classifications(
    *diagnostics: TyDiagnostic,
) -> tuple[DiagnosticClassification, ...]:
    return tuple(
        DiagnosticClassification(
            diagnostic_identity=diagnostic.identity,
            classification="general",
            reason_code="test-fixture",
        )
        for diagnostic in diagnostics
    )


def _baseline_evidence() -> tuple[Attempt, StaticBaseline, PassEvaluation]:
    attempt = _attempt()
    proposal = _proposal("baseline", attempt=attempt)
    check = TyCheck(process=_successful_process(), diagnostics=())
    baseline = StaticBaseline(
        proposal=proposal,
        ty=check,
        digest=ty_diagnostic_digest(check.diagnostics),
    )
    passed = PassEvaluation(
        proposal=proposal,
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=baseline.digest,
        ),
        test=TestPass(process=_successful_process()),
    )
    return attempt, baseline, passed


def _indeterminate_evaluation(attempt: Attempt) -> IndeterminateEvaluation:
    proposal = _proposal("candidate", attempt=attempt)
    static = StaticUnchangedEvaluation(
        proposal=proposal,
        ty=TyCheck(process=_successful_process(), diagnostics=()),
        baseline_digest=ty_diagnostic_digest(()),
    )
    failure = ToolFailure(
        cause="TOOL_FAILURE",
        stage="test",
        process=_successful_process(exit_code=2),
    )
    return IndeterminateEvaluation(
        proposal=proposal,
        cause=failure.cause,
        failure=failure,
        static=static,
    )


def _cell_success() -> CellSuccess:
    baseline_attempt, baseline, passed = _baseline_evidence()
    vector = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=vector)
    proposal = _proposal("floor", attempt=attempt, vector=vector)
    evaluation = PassEvaluation(
        proposal=proposal,
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=baseline.ty,
            baseline_digest=baseline.digest,
        ),
        test=TestPass(process=_successful_process()),
    )
    search = CoordinateSuccess(
        vector=vector,
        observations=(
            ProbeObservation(
                dependency="demo",
                candidate_version="1",
                vector=vector,
                evidence=ProbePass(
                    attempt=attempt,
                    proposal_id=proposal.proposal_id,
                    evaluation=evaluation,
                ),
            ),
        ),
        boundaries=(CoordinateBoundary(dependency="demo", floor="1"),),
        sweeps=1,
    )
    source = SourceIdentity(kind="registry")
    candidates = (
        Candidate(
            version="1",
            series_key="1",
            artifact=AvailableArtifact(
                filename="demo-1-py3-none-any.whl",
                kind="wheel",
                content_hash=f"sha256:{'a' * 64}",
                locator="https://files.example/demo-1-py3-none-any.whl",
            ),
        ),
    )
    representatives = (("1", "1"),)
    candidate_snapshot = CandidateSnapshot(
        dependency="demo",
        cell=baseline.proposal.cell,
        policy_identity="candidate-policy",
        source=source,
        candidates=candidates,
        series_representatives=representatives,
        digest=candidate_snapshot_digest(
            dependency="demo",
            cell=baseline.proposal.cell,
            policy_identity="candidate-policy",
            source=source,
            candidates=candidates,
            series_representatives=representatives,
        ),
    )
    return CellSuccess(
        cell=baseline.proposal.cell,
        baseline_attempt=baseline_attempt,
        static_baseline=baseline,
        baseline=passed,
        candidate_snapshots=(candidate_snapshot,),
        search=search,
        final_vector=vector,
        final_evaluation=evaluation,
    )


class TestPlanningSchemas:
    def test_cell_identity_is_the_compatibility_quadruple(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=("cuda",),
            active_declaration_ids=("decl",),
        )
        assert cell_identity(cell) == (
            "demo",
            "x86_64-unknown-linux-gnu",
            "3.10",
            ("cuda",),
        )
        assert "decl" not in cell_identity(cell)

    def test_selected_candidate_requires_a_complete_sha256_digest(self) -> None:
        with pytest.raises(ValidationError, match="complete SHA-256"):
            SelectedCandidate(
                dependency="idna",
                version="3.1",
                artifact=AvailableArtifact(
                    filename="idna-3.1-py3-none-any.whl",
                    kind="wheel",
                    content_hash="sha256:abc123",
                    locator="https://files.example/idna-3.1-py3-none-any.whl",
                ),
            )

    def test_cross_module_records_are_strict_and_immutable(self) -> None:
        with pytest.raises(ValidationError):
            ExampleRecord.model_validate({"name": "pf", "unexpected": True})

        record = ExampleRecord(name="pf")
        with pytest.raises(ValidationError):
            setattr(record, "name", "changed")

    @pytest.mark.parametrize(
        "config",
        (
            {"python": ("python3",)},
            {"python": ("3.11", "3.10")},
            {"jobs": True},
            {"jobs": 0},
            {"test_failure_exit_codes": (1, 1)},
            {"test_failure_exit_codes": (0,)},
        ),
    )
    def test_effective_config_rejects_ambiguous_runtime_policy(
        self,
        config: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            EffectiveConfig.model_validate(config)

    @pytest.mark.parametrize(
        "payload",
        (
            {"root": ".", "jobs": True},
            {"root": ".", "jobs": 0},
            {"root": ".", "max_duration_seconds": 0},
        ),
    )
    def test_search_request_rejects_invalid_scheduling(
        self,
        payload: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            SearchRequest.model_validate(payload)

    @pytest.mark.parametrize(
        "payload",
        (
            {"root": ".", "jobs": True},
            {"root": ".", "jobs": 0},
        ),
    )
    def test_check_request_rejects_invalid_scheduling(
        self,
        payload: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            CheckRequest.model_validate(payload)

    def test_merge_request_requires_an_input_report(self) -> None:
        with pytest.raises(ValidationError):
            MergeRequest(reports=(), output="merged.json")

    @pytest.mark.parametrize(
        "cell",
        (
            {
                "package": "demo",
                "target": "linux",
                "python_minor": "3.10",
                "extra_surface": (),
            },
            {
                "package": "demo",
                "target": "x86_64-unknown-linux-gnu",
                "python_minor": "3.10",
                "extra_surface": ("gpu", "gpu"),
            },
            {
                "package": "demo",
                "target": "x86_64-unknown-linux-gnu",
                "python_minor": "3.10",
                "extra_surface": (),
                "active_declaration_ids": ("b", "a"),
            },
        ),
    )
    def test_cell_requires_exact_normalized_coordinates(
        self,
        cell: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            Cell.model_validate(cell)

    def test_candidate_snapshot_requires_unique_nonempty_versions(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        artifact = AvailableArtifact(
            filename="demo.whl",
            kind="wheel",
            content_hash="sha256:abc",
        )
        base = {
            "dependency": "demo",
            "cell": cell,
            "policy_identity": "policy",
            "source": SourceIdentity(kind="registry"),
            "series_representatives": (),
            "digest": "digest",
        }

        with pytest.raises(ValidationError):
            CandidateSnapshot(candidates=(), **base)
        duplicate = Candidate(version="1.0", series_key="1", artifact=artifact)
        with pytest.raises(ValidationError):
            CandidateSnapshot(candidates=(duplicate, duplicate), **base)

    def test_candidate_snapshot_rejects_a_tampered_artifact(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        source = SourceIdentity(kind="registry")
        candidates = (
            Candidate(
                version="1.0",
                series_key="1",
                artifact=AvailableArtifact(
                    filename="demo.whl",
                    kind="wheel",
                    content_hash="sha256:abc",
                ),
            ),
        )
        representatives = (("1", "1.0"),)
        snapshot = CandidateSnapshot(
            dependency="demo",
            cell=cell,
            policy_identity="policy",
            source=source,
            candidates=candidates,
            series_representatives=representatives,
            digest=candidate_snapshot_digest(
                dependency="demo",
                cell=cell,
                policy_identity="policy",
                source=source,
                candidates=candidates,
                series_representatives=representatives,
            ),
        )
        dumped = snapshot.model_dump(mode="python")
        dumped["candidates"][0]["artifact"]["filename"] = "tampered.whl"

        with pytest.raises(ValidationError, match="digest"):
            CandidateSnapshot.model_validate(dumped)

    def test_structured_probe_rejections_require_their_evaluation(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        baseline_attempt, baseline, passed = _baseline_evidence()
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=_successful_process(exit_code=1),
        )
        with pytest.raises(ValidationError, match="structured evaluation"):
            CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=baseline.proposal.cell,
                phase="runtime-search",
                baseline_attempt=baseline_attempt,
                static_baseline=baseline,
                baseline=passed,
                failure_records=(failure,),
                coordinate_failure=CoordinateFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    observations=(
                        ProbeObservation(
                            dependency=None,
                            candidate_version=None,
                            vector=(),
                            evidence=ProbeRejection(
                                attempt=attempt,
                                proposal_id="candidate",
                                failure_id=failure.failure_id,
                                cause="TEST_FAILURE",
                            ),
                        ),
                    ),
                ),
            )


class TestSearchSchemas:
    def test_static_region_cannot_merge_an_a_b_a_transition(self) -> None:
        observations: list[ProbeObservation] = []
        baseline_digest = ty_diagnostic_digest(())
        for version in ("1", "2", "3"):
            vector = (VersionPin(name="demo", version=version),)
            attempt = _attempt(resolution="exact-vector", vector=vector)
            proposal = _proposal(
                f"demo={version}", attempt=attempt, vector=vector
            )
            if version == "2":
                diagnostic = _diagnostic()
                static = StaticRegressionEvaluation(
                    proposal=proposal,
                    ty=TyCheck(
                        process=_successful_process(exit_code=1),
                        diagnostics=(diagnostic,),
                    ),
                    baseline_digest=baseline_digest,
                    incremental=(diagnostic,),
                    static_fingerprint=static_fingerprint(
                        (diagnostic.identity,)
                    ),
                    classifications=_general_classifications(diagnostic),
                )
            else:
                static = StaticUnchangedEvaluation(
                    proposal=proposal,
                    ty=TyCheck(process=_successful_process(), diagnostics=()),
                    baseline_digest=baseline_digest,
                )
            evaluation = PassEvaluation(
                proposal=proposal,
                static=static,
                test=TestPass(process=_successful_process()),
            )
            observations.append(
                ProbeObservation(
                    dependency="demo",
                    candidate_version=version,
                    vector=vector,
                    evidence=ProbePass(
                        attempt=attempt,
                        proposal_id=proposal.proposal_id,
                        evaluation=evaluation,
                    ),
                )
            )
        region_slice = StaticRegionSlice(
            cell=observations[0].evidence.attempt.identity.cell,
            source_snapshot_digest="snapshot",
            policy_identity="policy",
            baseline_digest=baseline_digest,
            active_dependency="demo",
            other_coordinates=(),
            candidate_order=("1", "2", "3"),
        )

        with pytest.raises(ValidationError, match="backed by its observations"):
            CoordinateSuccess(
                vector=(VersionPin(name="demo", version="3"),),
                observations=tuple(observations),
                boundaries=(CoordinateBoundary(dependency="demo", floor="3"),),
                regions=(
                    StaticRegion(
                        slice=region_slice,
                        static_fingerprint=static_fingerprint(()),
                        observed_versions=("1", "2", "3"),
                        runtime_references=(
                            StaticRegionRuntimeReference(
                                proposal_id="demo=1", status="PASS"
                            ),
                            StaticRegionRuntimeReference(
                                proposal_id="demo=3", status="PASS"
                            ),
                        ),
                    ),
                ),
                sweeps=1,
            )

    def test_static_only_evidence_cannot_cross_slice_coordinates(self) -> None:
        vector = (
            VersionPin(name="a", version="1"),
            VersionPin(name="b", version="1"),
        )
        attempt = _attempt(resolution="exact-vector", vector=vector)
        proposal = _proposal("a=1;b=1", attempt=attempt, vector=vector)
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(process=_successful_process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
        )

        with pytest.raises(ValidationError, match="vector must match its Slice"):
            StaticOnlyEvidence(
                attempt=attempt,
                proposal_id=proposal.proposal_id,
                static_evaluation=static,
                guidance="PASS",
                region_slice=StaticRegionSlice(
                    cell=proposal.cell,
                    source_snapshot_digest="snapshot",
                    policy_identity="policy",
                    baseline_digest=static.baseline_digest,
                    active_dependency="a",
                    other_coordinates=(VersionPin(name="b", version="2"),),
                    candidate_order=("1", "2"),
                ),
                representative_proposal_id="a=2;b=2",
            )

    @pytest.mark.parametrize("jobs", (True, 0))
    def test_smoke_request_rejects_invalid_scheduling(self, jobs: bool | int) -> None:
        with pytest.raises(ValidationError):
            SmokeRequest(root=".", jobs=jobs)

    def test_highest_version_and_smoke_results_enforce_their_evidence(self) -> None:
        attempt, baseline, passed = _baseline_evidence()

        assert (
            HighestVersionPass(
                attempt=attempt,
                baseline=baseline,
                harness_baseline=empty_harness_baseline(attempt.identity.cell),
                evaluation=passed,
            ).evaluation
            is passed
        )

        increment = _diagnostic()
        static_failure = StaticRegressionEvaluation(
            proposal=baseline.proposal,
            ty=TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(increment,),
            ),
            baseline_digest=baseline.digest,
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=_general_classifications(increment),
        )
        with pytest.raises(ValidationError):
            HighestVersionPass.model_validate(
                {
                    "attempt": attempt,
                    "baseline": baseline,
                    "evaluation": static_failure,
                }
            )
        with pytest.raises(ValidationError, match="requires rejected evidence"):
            SmokeBaselineRejection(
                outcomes=(
                    HighestVersionPass(
                        attempt=attempt,
                        baseline=baseline,
                        harness_baseline=empty_harness_baseline(
                            attempt.identity.cell
                        ),
                        evaluation=passed,
                    ),
                )
            )

    def test_probe_rejection_requires_its_failure_record(self) -> None:
        baseline_attempt, baseline, passed = _baseline_evidence()
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        with pytest.raises(ValidationError, match="FailureRecord"):
            CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=baseline.proposal.cell,
                phase="runtime-search",
                baseline_attempt=baseline_attempt,
                static_baseline=baseline,
                baseline=passed,
                coordinate_failure=CoordinateFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    observations=(
                        ProbeObservation(
                            dependency="demo",
                            candidate_version="1",
                            vector=vector,
                            evidence=ProbeRejection(
                                attempt=attempt,
                                failure_id="failure-missing",
                                cause="RESOLUTION_CONFLICT",
                            ),
                        ),
                    ),
                ),
            )

    def test_probe_rejects_status_that_contradicts_structured_static_evidence(
        self,
    ) -> None:
        increment = _diagnostic()
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        proposal = _proposal("candidate", attempt=attempt, vector=vector)
        failed = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(increment,),
            ),
            baseline_digest="baseline",
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=_general_classifications(increment),
        )

        with pytest.raises(ValidationError):
            ProbePass.model_validate(
                {
                    "attempt": attempt,
                    "proposal_id": proposal.proposal_id,
                    "evaluation": failed,
                }
            )

    @pytest.mark.parametrize("evidence_kind", ("pass", "rejection", "indeterminate"))
    @pytest.mark.parametrize("mismatch", ("proposal", "attempt"))
    def test_probe_evidence_must_match_its_proposal_and_attempt(
        self,
        evidence_kind: str,
        mismatch: str,
    ) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        other_attempt = _attempt(
            resolution="exact-vector",
            vector=(VersionPin(name="demo", version="2"),),
        )
        indeterminate = _indeterminate_evaluation(attempt)
        proposal_id = (
            "other" if mismatch == "proposal" else indeterminate.proposal.proposal_id
        )
        evidence_attempt = other_attempt if mismatch == "attempt" else attempt

        if evidence_kind == "pass":
            static = StaticUnchangedEvaluation(
                proposal=indeterminate.proposal,
                ty=TyCheck(process=_successful_process(), diagnostics=()),
                baseline_digest=ty_diagnostic_digest(()),
            )
            evaluation = PassEvaluation(
                proposal=indeterminate.proposal,
                static=static,
                test=TestPass(process=_successful_process()),
            )
            constructor = ProbePass
            payload: dict[str, object] = {
                "attempt": evidence_attempt,
                "proposal_id": proposal_id,
                "evaluation": evaluation,
            }
        elif evidence_kind == "rejection":
            static = StaticUnchangedEvaluation(
                proposal=indeterminate.proposal,
                ty=TyCheck(process=_successful_process(), diagnostics=()),
                baseline_digest=ty_diagnostic_digest(()),
            )
            evaluation = TestFailEvaluation(
                proposal=indeterminate.proposal,
                static=static,
                test=TestFail(process=_successful_process(exit_code=1)),
            )
            constructor = ProbeRejection
            payload = {
                "attempt": evidence_attempt,
                "proposal_id": proposal_id,
                "failure_id": "failure",
                "cause": "TEST_FAILURE",
                "evaluation": evaluation,
            }
        else:
            constructor = ProbeIndeterminate
            payload = {
                "attempt": evidence_attempt,
                "proposal_id": proposal_id,
                "failure_id": "failure",
                "cause": "TOOL_FAILURE",
                "evaluation": indeterminate,
            }

        with pytest.raises(ValidationError):
            constructor.model_validate(payload)

    def test_probe_observation_requires_the_attempt_requested_vector(self) -> None:
        requested = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=requested)
        proposal = _proposal("candidate", attempt=attempt, vector=requested)
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(process=_successful_process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
        )

        with pytest.raises(ValidationError, match="exact attempt"):
            ProbeObservation(
                dependency="demo",
                candidate_version="2",
                vector=(VersionPin(name="demo", version="2"),),
                evidence=ProbePass(
                    attempt=attempt,
                    proposal_id="candidate",
                    evaluation=PassEvaluation(
                        proposal=proposal,
                        static=static,
                        test=TestPass(process=_successful_process()),
                    ),
                ),
            )

    def test_probe_evidence_requires_an_exact_vector_attempt_and_static_pass(
        self,
    ) -> None:
        baseline_attempt, baseline, passed = _baseline_evidence()

        with pytest.raises(ValidationError, match="exact-vector"):
            ProbePass(
                attempt=baseline_attempt,
                proposal_id=passed.proposal.proposal_id,
                evaluation=passed,
            )
        exact_attempt = _attempt(resolution="exact-vector", vector=())
        with pytest.raises(ValidationError, match="evaluation"):
            ProbePass.model_validate(
                {
                    "attempt": exact_attempt,
                    "proposal_id": "candidate",
                }
            )

    def test_probe_evaluation_must_match_the_attempt_requested_vector(self) -> None:
        requested = (VersionPin(name="demo", version="1"),)
        actual = (VersionPin(name="demo", version="2"),)
        attempt = _attempt(resolution="exact-vector", vector=requested)
        proposal = _proposal("candidate", attempt=attempt, vector=actual)
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(process=_successful_process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
        )

        with pytest.raises(ValidationError, match="requested exact vector"):
            ProbePass(
                attempt=attempt,
                proposal_id=proposal.proposal_id,
                evaluation=PassEvaluation(
                    proposal=proposal,
                    static=static,
                    test=TestPass(process=_successful_process()),
                ),
            )

    def test_probe_rejection_cause_must_match_its_evaluation_kind(self) -> None:
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        proposal = _proposal("candidate", attempt=attempt, vector=vector)
        diagnostic = _diagnostic()
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(diagnostic,),
            ),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(diagnostic,),
            static_fingerprint=static_fingerprint((diagnostic.identity,)),
            classifications=_general_classifications(diagnostic),
        )

        with pytest.raises(ValidationError, match="cause must match"):
            ProbeRejection(
                attempt=attempt,
                proposal_id=proposal.proposal_id,
                failure_id="failure",
                cause="RESOLUTION_CONFLICT",
                evaluation=TestFailEvaluation(
                    proposal=proposal,
                    static=static,
                    test=TestFail(process=_successful_process(exit_code=1)),
                ),
            )

    def test_probe_indeterminate_cause_must_match_its_evaluation(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        evaluation = _indeterminate_evaluation(attempt)

        with pytest.raises(ValidationError, match="cause must match"):
            ProbeIndeterminate(
                attempt=attempt,
                proposal_id=evaluation.proposal.proposal_id,
                failure_id="failure",
                cause="TIMEOUT",
                evaluation=evaluation,
            )

    def test_prepare_rejection_cannot_claim_a_proposal(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())

        with pytest.raises(ValidationError, match="cannot claim a Proposal"):
            ProbeRejection(
                attempt=attempt,
                proposal_id="invented",
                failure_id="failure",
                cause="RESOLUTION_CONFLICT",
            )

    @pytest.mark.parametrize(
        ("predecessor", "failure_id"),
        (("1", None), (None, "failure")),
    )
    def test_coordinate_boundary_requires_complete_predecessor_evidence(
        self,
        predecessor: str | None,
        failure_id: str | None,
    ) -> None:
        with pytest.raises(ValidationError, match="predecessor requires"):
            CoordinateBoundary(
                dependency="demo",
                floor="2",
                predecessor=predecessor,
                predecessor_failure_id=failure_id,
            )

    def test_indeterminate_coordinate_requires_its_failure_reference(self) -> None:
        with pytest.raises(ValidationError, match="requires a failure ID"):
            CoordinateFailure(status="INDETERMINATE", observations=())

    def test_indeterminate_coordinate_must_reference_its_observation(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())

        with pytest.raises(ValidationError, match="reference its observation"):
            CoordinateFailure(
                status="INDETERMINATE",
                failure_id="failure-terminal",
                observations=(
                    ProbeObservation(
                        dependency=None,
                        candidate_version=None,
                        vector=(),
                        evidence=ProbeIndeterminate(
                            attempt=attempt,
                            failure_id="failure-other",
                            cause="TIMEOUT",
                        ),
                    ),
                ),
            )

    def test_probe_observation_candidate_must_match_its_vector(self) -> None:
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)

        with pytest.raises(ValidationError, match="candidate must match"):
            ProbeObservation(
                dependency="demo",
                candidate_version="2",
                vector=vector,
                evidence=ProbeRejection(
                    attempt=attempt,
                    failure_id="failure",
                    cause="RESOLUTION_CONFLICT",
                ),
            )

    def test_non_monotonic_counterexample_requires_direct_same_slice_evidence(
        self,
    ) -> None:
        low_vector = (
            VersionPin(name="a", version="1"),
            VersionPin(name="b", version="1"),
        )
        high_vector = (
            VersionPin(name="a", version="2"),
            VersionPin(name="b", version="1"),
        )
        low_attempt = _attempt(resolution="exact-vector", vector=low_vector)
        low_proposal = _proposal("a=1;b=1", attempt=low_attempt, vector=low_vector)
        low_static = StaticUnchangedEvaluation(
            proposal=low_proposal,
            ty=TyCheck(process=_successful_process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
        )
        low = ProbeObservation(
            dependency="a",
            candidate_version="1",
            vector=low_vector,
            evidence=ProbePass(
                attempt=low_attempt,
                proposal_id=low_proposal.proposal_id,
                evaluation=PassEvaluation(
                    proposal=low_proposal,
                    static=low_static,
                    test=TestPass(process=_successful_process()),
                ),
            ),
        )
        high_attempt = _attempt(resolution="exact-vector", vector=high_vector)
        high = ProbeObservation(
            dependency="a",
            candidate_version="2",
            vector=high_vector,
            evidence=ProbeRejection(
                attempt=high_attempt,
                failure_id="failure-high",
                cause="RESOLUTION_CONFLICT",
            ),
        )

        result = CoordinateFailure(
            status="NON_MONOTONIC",
            dependency="a",
            counterexample=("1", "2"),
            observations=(low, high),
        )

        assert result.counterexample == ("1", "2")
        cross_slice_vector = (
            VersionPin(name="a", version="2"),
            VersionPin(name="b", version="2"),
        )
        cross_slice_attempt = _attempt(
            resolution="exact-vector",
            vector=cross_slice_vector,
        )
        with pytest.raises(ValidationError, match="direct evidence in one Slice"):
            CoordinateFailure(
                status="NON_MONOTONIC",
                dependency="a",
                counterexample=("1", "2"),
                observations=(
                    low,
                    high.model_copy(
                        update={
                            "vector": cross_slice_vector,
                            "evidence": ProbeRejection(
                                attempt=cross_slice_attempt,
                                failure_id="failure-cross-slice",
                                cause="RESOLUTION_CONFLICT",
                            ),
                        }
                    ),
                ),
            )

    def test_coordinate_success_cannot_contain_indeterminate_evidence(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())

        with pytest.raises(ValidationError, match="cannot contain indeterminate"):
            CoordinateSuccess(
                vector=(),
                observations=(
                    ProbeObservation(
                        dependency=None,
                        candidate_version=None,
                        vector=(),
                        evidence=ProbeIndeterminate(
                            attempt=attempt,
                            failure_id="failure",
                            cause="TIMEOUT",
                        ),
                    ),
                ),
                boundaries=(),
                sweeps=1,
            )

    def test_coordinate_boundary_must_reference_its_rejection_observation(self) -> None:
        rejected_vector = (VersionPin(name="demo", version="1"),)
        vector = (VersionPin(name="demo", version="2"),)
        attempt = _attempt(resolution="exact-vector", vector=rejected_vector)

        with pytest.raises(ValidationError, match="rejection observation"):
            CoordinateSuccess(
                vector=vector,
                observations=(
                    ProbeObservation(
                        dependency="demo",
                        candidate_version="1",
                        vector=rejected_vector,
                        evidence=ProbeRejection(
                            attempt=attempt,
                            failure_id="failure-observed",
                            cause="RESOLUTION_CONFLICT",
                        ),
                    ),
                ),
                boundaries=(
                    CoordinateBoundary(
                        dependency="demo",
                        floor="2",
                        predecessor="1",
                        predecessor_failure_id="failure-other",
                    ),
                ),
                sweeps=1,
            )

    def test_smoke_indeterminate_requires_indeterminate_evidence(self) -> None:
        attempt, baseline, passed = _baseline_evidence()

        with pytest.raises(ValidationError, match="requires indeterminate evidence"):
            SmokeIndeterminate(
                outcomes=(
                    HighestVersionPass(
                        attempt=attempt,
                        baseline=baseline,
                        harness_baseline=empty_harness_baseline(
                            attempt.identity.cell
                        ),
                        evaluation=passed,
                    ),
                )
            )

    def test_baseline_outcome_requires_a_highest_attempt(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=_successful_process(exit_code=1),
        )

        with pytest.raises(ValidationError, match="highest Attempt"):
            BaselineRejection(attempt=attempt, failure=failure)

    def test_baseline_test_rejection_requires_its_structured_evaluation(self) -> None:
        attempt, baseline, _ = _baseline_evidence()
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=_successful_process(exit_code=1),
        )

        with pytest.raises(ValidationError, match="structured evaluation"):
            BaselineRejection(
                attempt=attempt,
                failure=failure,
                static_baseline=baseline,
            )

    def test_baseline_indeterminate_evaluation_must_match_the_captured_baseline(
        self,
    ) -> None:
        attempt, baseline, _ = _baseline_evidence()
        other_proposal = _proposal("other-baseline", attempt=attempt)
        tool_failure = ToolFailure(
            cause="TOOL_FAILURE",
            stage="test",
            process=_successful_process(exit_code=2),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=tool_failure.cause,
            stage=tool_failure.stage,
            process=tool_failure.process,
        )

        with pytest.raises(ValidationError, match="captured V_hi"):
            BaselineIndeterminate(
                attempt=attempt,
                failure=failure,
                static_baseline=baseline,
                evaluation=IndeterminateEvaluation(
                    proposal=other_proposal,
                    cause=tool_failure.cause,
                    failure=tool_failure,
                    static=StaticUnchangedEvaluation(
                        proposal=other_proposal,
                        ty=baseline.ty,
                        baseline_digest=baseline.digest,
                    ),
                ),
            )

    def test_baseline_rejection_diagnosis_must_match_its_evaluation(self) -> None:
        attempt, baseline, passed = _baseline_evidence()
        evaluation = TestFailEvaluation(
            proposal=baseline.proposal,
            static=passed.static,
            test=TestFail(process=_successful_process(exit_code=1)),
        )
        other_process = _successful_process(exit_code=2)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=other_process,
        )

        with pytest.raises(ValidationError, match="diagnosis must match"):
            BaselineRejection(
                attempt=attempt,
                failure=failure,
                static_baseline=baseline,
                evaluation=evaluation,
            )

    def test_cell_indeterminate_requires_its_complete_pass_baseline(self) -> None:
        baseline_attempt, baseline, _ = _baseline_evidence()
        cell = baseline.proposal.cell
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package=cell.package,
                cell=cell,
                source_snapshot_digest="snapshot",
                evaluation_policy_identity="policy",
            ),
            cause="SOURCE_FAILURE",
            stage="candidate-discovery",
            process=None,
            detail=FailureDetail(code="source", message="source unavailable"),
        )

        with pytest.raises(ValidationError, match="complete PASS baseline"):
            CellIndeterminate(
                cell=cell,
                phase="candidate-discovery",
                failure_id=failure.failure_id,
                failure_records=(failure,),
                baseline_attempt=baseline_attempt,
                static_baseline=baseline,
            )

    def test_probe_attempt_must_share_the_baseline_evaluation_context(self) -> None:
        baseline_attempt, baseline, passed = _baseline_evidence()
        vector = (VersionPin(name="demo", version="1"),)
        candidate_attempt = _attempt(resolution="exact-vector", vector=vector)
        candidate_attempt = Attempt.from_identity(
            candidate_attempt.identity.model_copy(
                update={"source_plan_identity": "another-source-plan"}
            )
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=candidate_attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=_successful_process(exit_code=1),
        )
        candidate_proposal = _proposal(
            "candidate",
            attempt=candidate_attempt,
            vector=vector,
        )
        candidate_static = StaticUnchangedEvaluation(
            proposal=candidate_proposal,
            ty=TyCheck(process=_successful_process(), diagnostics=()),
            baseline_digest=baseline.digest,
        )
        candidate_failure = TestFailEvaluation(
            proposal=candidate_proposal,
            static=candidate_static,
            test=TestFail(process=_successful_process(exit_code=1)),
        )

        with pytest.raises(ValidationError, match="evaluation context"):
            CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=baseline.proposal.cell,
                phase="runtime-search",
                baseline_attempt=baseline_attempt,
                static_baseline=baseline,
                baseline=passed,
                failure_records=(failure,),
                coordinate_failure=CoordinateFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    observations=(
                        ProbeObservation(
                            dependency="demo",
                            candidate_version="1",
                            vector=vector,
                            evidence=ProbeRejection(
                                attempt=candidate_attempt,
                                proposal_id=candidate_proposal.proposal_id,
                                failure_id=failure.failure_id,
                                cause=failure.cause,
                                evaluation=candidate_failure,
                            ),
                        ),
                    ),
                ),
            )


class TestEvaluationSchemas:
    @pytest.mark.parametrize(
        "spec",
        (
            {"argv": (), "cwd": ".", "timeout_seconds": None},
            {"argv": ("python",), "cwd": ".", "timeout_seconds": 0},
            {
                "argv": ("python",),
                "cwd": ".",
                "timeout_seconds": None,
                "summary_limit": 0,
            },
        ),
    )
    def test_process_spec_rejects_an_unexecutable_contract(
        self,
        spec: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            ProcessSpec.model_validate(spec)

    @pytest.mark.parametrize(
        "facts",
        (
            {"exit_code": None, "signal": None, "start_error": None},
            {"exit_code": 0, "signal": 9, "start_error": None},
        ),
    )
    def test_process_result_requires_exactly_one_terminal_fact(
        self,
        facts: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            ProcessResult.model_validate({"duration_seconds": 0, **facts})

    def test_process_result_omits_captured_output_from_portable_facts(self) -> None:
        process = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="484 passed in 11.12s",
            stderr="tool noise",
        )

        dumped = process.model_dump(mode="json")
        restored = ProcessResult.model_validate(dumped)

        assert "stdout" not in dumped
        assert "stderr" not in dumped
        assert "stdout_tail" not in dumped
        assert "stderr_tail" not in dumped
        assert restored.exit_code == 1
        assert restored.stdout == ""
        assert restored.stderr == ""

    def test_cell_indeterminate_accepts_portable_process_facts_after_search_round_trip(
        self,
    ) -> None:
        baseline_attempt, baseline, passed = _baseline_evidence()
        attempt = _attempt(resolution="exact-vector", vector=())
        proposal = _proposal("candidate", attempt=attempt)
        process = ProcessResult(
            exit_code=2,
            signal=None,
            duration_seconds=0.1,
            stdout="captured test output",
            stderr="captured tool output",
        )
        tool_failure = ToolFailure(
            cause="TOOL_FAILURE",
            stage="test",
            process=process,
        )
        evaluation = IndeterminateEvaluation(
            proposal=proposal,
            cause=tool_failure.cause,
            failure=tool_failure,
            static=StaticUnchangedEvaluation(
                proposal=proposal,
                ty=baseline.ty,
                baseline_digest=baseline.digest,
            ),
        )
        failure = FailurePolicy().classify_evaluation(
            AttemptFailureScope(attempt=attempt),
            evaluation,
        )
        assert failure is not None
        search = CoordinateFailure(
            status="INDETERMINATE",
            failure_id=failure.failure_id,
            observations=(
                ProbeObservation(
                    dependency=None,
                    candidate_version=None,
                    vector=(),
                    evidence=ProbeIndeterminate(
                        attempt=attempt,
                        proposal_id=proposal.proposal_id,
                        failure_id=failure.failure_id,
                        cause=tool_failure.cause,
                        evaluation=evaluation,
                    ),
                ),
            ),
        )

        restored_search = CoordinateFailure.model_validate(
            search.model_dump(mode="json")
        )

        result = CellIndeterminate(
            cell=baseline.proposal.cell,
            phase="runtime-search",
            failure_id=failure.failure_id,
            failure_records=(failure,),
            baseline_attempt=baseline_attempt,
            static_baseline=baseline,
            baseline=passed,
            coordinate_failure=restored_search,
        )

        assert result.failure_id == failure.failure_id

    @pytest.mark.parametrize(
        ("outcome", "process"),
        (
            (TestPass, _successful_process(exit_code=1)),
            (TestFail, _successful_process(exit_code=0)),
            (
                TestFail,
                _successful_process(exit_code=1).model_copy(update={"timed_out": True}),
            ),
            (
                TestFail,
                _successful_process(exit_code=1).model_copy(
                    update={"stderr_complete": False}
                ),
            ),
        ),
    )
    def test_test_outcomes_require_complete_normal_terminal_facts(
        self,
        outcome: type[TestPass] | type[TestFail],
        process: ProcessResult,
    ) -> None:
        with pytest.raises(ValidationError, match="complete normal"):
            outcome(process=process)

    @pytest.mark.parametrize(
        "changes",
        (
            {"path": " "},
            {"severity": " "},
            {"line": None},
            {"column": 0},
            {
                "origin": "external",
                "identity": "external|demo.py|invalid-type",
            },
            {"identity": "unstable-identity"},
        ),
    )
    def test_ty_diagnostic_rejects_noncanonical_identity_fields(
        self,
        changes: dict[str, object],
    ) -> None:
        payload = _diagnostic().model_dump()
        payload.update(changes)

        with pytest.raises(ValidationError):
            TyDiagnostic.model_validate(payload)

    @pytest.mark.parametrize(
        "process_changes",
        (
            {"exit_code": 2},
            {"timed_out": True},
            {"stdout_complete": False},
        ),
    )
    def test_ty_check_rejects_noncomparable_process_results(
        self,
        process_changes: dict[str, object],
    ) -> None:
        process = _successful_process().model_dump()
        process.update(process_changes)

        with pytest.raises(ValidationError, match="complete output"):
            TyCheck(process=ProcessResult.model_validate(process), diagnostics=())

    def test_ty_check_requires_deterministically_sorted_diagnostics(self) -> None:
        with pytest.raises(ValidationError, match="sorted by stable identity"):
            TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(_diagnostic(line=2), _diagnostic(line=1)),
            )

    def test_static_models_reject_inconsistent_baseline_evidence(self) -> None:
        proposal = _proposal("baseline")
        other_proposal = _proposal("other")
        first = _diagnostic()
        second = _diagnostic(line=2)
        check = TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(first,),
        )
        other_check = TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(second,),
        )
        digest = ty_diagnostic_digest(check.diagnostics)
        baseline = StaticBaseline(proposal=proposal, ty=check, digest=digest)

        with pytest.raises(ValidationError, match="baseline digest"):
            StaticBaseline(proposal=proposal, ty=check, digest="wrong")
        with pytest.raises(ValidationError, match="digest cannot be empty"):
            StaticUnchangedEvaluation(
                proposal=proposal,
                ty=check,
                baseline_digest="",
            )
        with pytest.raises(ValidationError, match="empty diagnostic increment"):
            StaticUnchangedEvaluation(
                proposal=proposal,
                ty=check,
                baseline_digest=digest,
                incremental=(first,),
            )
        with pytest.raises(ValidationError, match="digest cannot be empty"):
            StaticRegressionEvaluation(
                proposal=proposal,
                ty=check,
                baseline_digest="",
                incremental=(first,),
                static_fingerprint=static_fingerprint((first.identity,)),
                classifications=_general_classifications(first),
            )
        with pytest.raises(ValidationError, match="non-empty diagnostic increment"):
            StaticRegressionEvaluation(
                proposal=proposal,
                ty=check,
                baseline_digest=digest,
                incremental=(),
                static_fingerprint=static_fingerprint(()),
                classifications=(),
            )
        with pytest.raises(ValidationError, match="sub-multiset"):
            StaticRegressionEvaluation(
                proposal=proposal,
                ty=check,
                baseline_digest=digest,
                incremental=(second,),
                static_fingerprint=static_fingerprint((second.identity,)),
                classifications=_general_classifications(second),
            )
        with pytest.raises(ValidationError, match="fingerprint"):
            StaticRegressionEvaluation(
                proposal=proposal,
                ty=check,
                baseline_digest=digest,
                incremental=(first,),
                static_fingerprint="wrong",
                classifications=_general_classifications(first),
            )

        mismatched_proposal = StaticUnchangedEvaluation(
            proposal=other_proposal,
            ty=check,
            baseline_digest=digest,
        )
        with pytest.raises(ValidationError, match="proposal must match"):
            StaticBaselineCapture(baseline=baseline, static=mismatched_proposal)

        mismatched_check = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=other_check,
            baseline_digest=digest,
        )
        with pytest.raises(ValidationError, match="reuse the baseline TyCheck"):
            StaticBaselineCapture(baseline=baseline, static=mismatched_check)

        mismatched_digest = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest="wrong",
        )
        with pytest.raises(ValidationError, match="digest must match"):
            StaticBaselineCapture(baseline=baseline, static=mismatched_digest)


class TestReportSchemas:
    def test_cell_failure_rejects_probe_evidence_from_another_static_baseline(
        self,
    ) -> None:
        baseline_attempt, baseline, passed = _baseline_evidence()
        increment = _diagnostic()
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        candidate = _proposal("candidate", attempt=attempt, vector=vector)
        wrong_static = StaticRegressionEvaluation(
            proposal=candidate,
            ty=TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(increment,),
            ),
            baseline_digest="another-baseline",
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=_general_classifications(increment),
        )
        evaluation = TestFailEvaluation(
            proposal=candidate,
            static=wrong_static,
            test=TestFail(process=_successful_process(exit_code=1)),
        )
        rejection = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=evaluation.test.process,
        )

        with pytest.raises(ValidationError, match="frozen static baseline"):
            CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=baseline.proposal.cell,
                phase="runtime-search",
                baseline_attempt=baseline_attempt,
                static_baseline=baseline,
                baseline=passed,
                failure_records=(rejection,),
                coordinate_failure=CoordinateFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    observations=(
                        ProbeObservation(
                            dependency="demo",
                            candidate_version="1",
                            vector=vector,
                            evidence=ProbeRejection(
                                attempt=attempt,
                                proposal_id=candidate.proposal_id,
                                failure_id=rejection.failure_id,
                                cause="TEST_FAILURE",
                                evaluation=evaluation,
                            ),
                        ),
                    ),
                ),
            )

    def test_search_failure_event_retains_structured_failure_and_evaluation(
        self,
    ) -> None:
        diagnostic = _diagnostic()
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        proposal = _proposal("candidate", attempt=attempt, vector=vector)
        static = StaticRegressionEvaluation(
            proposal=proposal,
            ty=TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(diagnostic,),
            ),
            baseline_digest="baseline",
            incremental=(diagnostic,),
            static_fingerprint=static_fingerprint((diagnostic.identity,)),
            classifications=_general_classifications(diagnostic),
        )
        evaluation = TestFailEvaluation(
            proposal=proposal,
            static=static,
            test=TestFail(process=_successful_process(exit_code=1)),
        )
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=evaluation.test.process,
        )
        event = SearchFailureEvent(
            cell=proposal.cell,
            failure=failure,
            evaluation=evaluation,
        )

        assert isinstance(event, ActivityEvent)
        assert event.failure == failure
        wrong_failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="HARNESS_CONFLICT",
            stage="install-harness",
            process=evaluation.test.process,
        )
        with pytest.raises(ValidationError, match="test evaluation"):
            SearchFailureEvent(
                cell=proposal.cell,
                failure=wrong_failure,
                evaluation=evaluation,
            )

    def test_search_failure_event_matches_indeterminate_failure_facts(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        evaluation = _indeterminate_evaluation(attempt)
        failure = FailurePolicy().classify_evaluation(
            AttemptFailureScope(attempt=attempt),
            evaluation,
        )
        assert failure is not None

        event = SearchFailureEvent(
            cell=evaluation.proposal.cell,
            failure=failure,
            evaluation=evaluation,
        )

        assert event.evaluation == evaluation
        mismatched = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=evaluation.failure.cause,
            stage="witness",
            process=evaluation.failure.process,
        )
        with pytest.raises(ValidationError, match="indeterminate evaluation"):
            SearchFailureEvent(
                cell=evaluation.proposal.cell,
                failure=mismatched,
                evaluation=evaluation,
            )

    def test_cell_activity_events_reject_progress_sentinel_fields(self) -> None:
        cell = _attempt().identity.cell
        stage = CellStageEvent(cell=cell, stage="install")
        completed = CellCompletedEvent(
            cell=cell,
            completed=1,
            total=1,
            outcome=CellSucceeded(status="PASS", phase="complete"),
        )

        assert isinstance(stage, ActivityEvent)
        assert isinstance(completed, ActivityEvent)
        assert "completed" not in stage.model_dump()
        assert "phase" not in completed.model_dump()
        with pytest.raises(ValidationError, match="Extra inputs"):
            CellStageEvent.model_validate(
                {"cell": cell, "stage": "install", "completed": 0}
            )
        with pytest.raises(ValidationError, match="completion counters"):
            CellCompletedEvent(
                cell=cell,
                completed=0,
                total=1,
                outcome=CellSucceeded(status="PASS", phase="complete"),
            )

    def test_cell_result_detail_is_excluded_from_runtime_event_serialization(
        self,
    ) -> None:
        attempt = _attempt()
        cell = attempt.identity.cell
        process = _successful_process(exit_code=1)
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=process,
        )
        event = CellCompletedEvent(
            cell=cell,
            completed=1,
            total=1,
            outcome=CellFailed(
                status="REJECTED",
                phase="test",
                detail=PytestFailureDetail(
                    first=PytestFailureCase(
                        nodeid="tests/test_cli.py::test_example",
                        phase="call",
                    ),
                    total=1,
                ),
                detail_failure_id=failure.failure_id,
                failures=(failure,),
            ),
        )

        outcome = event.model_dump(mode="json")["outcome"]
        assert "detail" not in outcome
        assert "detail_failure_id" not in outcome
        assert isinstance(event.outcome, CellFailed)
        with pytest.raises(ValidationError, match="explicit failure source"):
            CellFailed(
                status="REJECTED",
                phase="test",
                detail=event.outcome.detail,
                failures=(failure,),
            )

    @pytest.mark.parametrize("suffix", ("\u009b31mred", "\ud800bad"))
    def test_pytest_failure_case_rejects_unsafe_display_text(
        self,
        suffix: str,
    ) -> None:
        with pytest.raises(ValidationError, match="bounded display text"):
            PytestFailureCase(nodeid=f"case{suffix}", phase="call")

    def test_search_diagnostic_event_rejects_a_mismatched_cell(self) -> None:
        attempt = _attempt()
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="TEST_FAILURE",
            stage="test",
            process=_successful_process(exit_code=1),
        )
        wrong_cell = attempt.identity.cell.model_copy(update={"package": "other"})

        with pytest.raises(ValidationError, match="failure scope"):
            SearchFailureEvent(cell=wrong_cell, failure=failure)

    def test_cell_failure_record_ids_are_unique(self) -> None:
        cell = _attempt().identity.cell
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_attempt()),
            cause="TEST_FAILURE",
            stage="test",
            process=_successful_process(exit_code=1),
        )
        with pytest.raises(ValidationError, match="unique"):
            CellIndeterminate(
                cell=cell,
                phase="test",
                failure_id=failure.failure_id,
                failure_records=(failure, failure),
            )

    def test_failure_record_rejects_a_tampered_stable_id(self) -> None:
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_attempt()),
            cause="TEST_FAILURE",
            stage="test",
            process=_successful_process(exit_code=1),
        )
        document = failure.model_dump(mode="python")
        document["failure_id"] = "failure-0000000000000000"

        with pytest.raises(ValidationError, match="structured facts"):
            type(failure).model_validate(document)

    def test_cell_terminal_rejects_a_failure_scoped_to_another_cell(self) -> None:
        cell = _attempt().identity.cell
        other_cell = cell.model_copy(update={"python_minor": "3.11"})
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_attempt(cell=other_cell)),
            cause="TOOL_FAILURE",
            stage="test",
            process=_successful_process(exit_code=2),
        )

        with pytest.raises(ValidationError, match="result cell"):
            CellIndeterminate(
                cell=cell,
                phase="test",
                failure_id=failure.failure_id,
                failure_records=(failure,),
            )

    def test_cell_success_rejects_tampered_final_evidence(self) -> None:
        success = _cell_success()
        dumped = success.model_dump(mode="python")
        with pytest.raises(ValidationError, match="final vector"):
            CellSuccess.model_validate(
                dumped | {"final_vector": (VersionPin(name="demo", version="2"),)}
            )

        search = dict(dumped["search"])
        search["vector"] = (VersionPin(name="demo", version="2"),)
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped | {"search": search})

        dumped = success.model_dump(mode="python")
        dumped["search"]["observations"][0]["vector"] = (
            VersionPin(name="demo", version="2"),
        )
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped)

        dumped = success.model_dump(mode="python")
        dumped["candidate_snapshots"][0]["candidates"][0]["artifact"]["filename"] = (
            "tampered.whl"
        )
        with pytest.raises(ValidationError, match="digest"):
            CellSuccess.model_validate(dumped)

        dumped = success.model_dump(mode="python")
        dumped["final_evaluation"]["proposal"]["managed_vector"] = (
            VersionPin(name="demo", version="2"),
        )
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped)

        dumped = success.model_dump(mode="python")
        dumped["search"]["observations"][0]["evidence"]["attempt"]["identity"][
            "requested_managed_vector"
        ] = (VersionPin(name="demo", version="2"),)
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped)

    def test_package_floor_report_dump_excludes_process_output_bodies(self) -> None:
        success = _cell_success()
        dumped = success.final_evaluation.test.process.model_dump()
        assert "stdout" not in dumped
        assert "stderr" not in dumped

from __future__ import annotations

from typing import Literal

import pytest
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
    CellFailureScope,
    FailureDetail,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    ProcessSpec,
    SearchFailureEvent,
    SmokeBaselineRejection,
    SmokeIndeterminate,
    StaticBaseline,
    StaticBaselineCapture,
    StaticFailEvaluation,
    StaticPassEvaluation,
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
    SourceIdentity,
    SourceSnapshotIdentity,
    VersionPin,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateSuccess,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProbeObservation,
    ProbeIndeterminate,
    ProbePass,
    ProbeRejection,
    report_generation_id,
)


class ExampleRecord(FrozenSchema):
    name: str


def test_cross_module_records_are_strict_and_immutable() -> None:
    with pytest.raises(ValidationError):
        ExampleRecord.model_validate({"name": "pf", "unexpected": True})

    record = ExampleRecord(name="pf")
    with pytest.raises(ValidationError):
        record.name = "changed"


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
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CheckRequest.model_validate(payload)


@pytest.mark.parametrize("jobs", (True, 0))
def test_smoke_request_rejects_invalid_scheduling(jobs: bool | int) -> None:
    with pytest.raises(ValidationError):
        SmokeRequest(root=".", jobs=jobs)


def test_merge_request_requires_an_input_report() -> None:
    with pytest.raises(ValidationError):
        MergeRequest(reports=(), output="merged.json")


@pytest.mark.parametrize(
    "spec",
    (
        {"argv": (), "cwd": ".", "timeout_seconds": None},
        {"argv": ("python",), "cwd": ".", "timeout_seconds": 0},
        {"argv": ("python",), "cwd": ".", "timeout_seconds": None, "summary_limit": 0},
    ),
)
def test_process_spec_rejects_an_unexecutable_contract(
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
    facts: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProcessResult.model_validate({"duration_seconds": 0, **facts})


def test_process_result_omits_captured_output_from_portable_facts() -> None:
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
    cell: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Cell.model_validate(cell)


def test_candidate_snapshot_requires_unique_nonempty_versions() -> None:
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


def test_development_schema_one_without_generation_identity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="report_generation_id"):
        PackageFloorReportV1.model_validate(
            {
                "schema_version": 1,
                "generator": {"name": "pf", "version": "0.1.0", "algorithm": "v1"},
                "package": {"name": "demo", "pyproject_path": "pyproject.toml"},
                "source_snapshot": {"digest": "snapshot", "entries": []},
                "policy_identity": "policy",
                "requirement_declarations": [],
                "candidate_snapshots": [],
                "target_cells": [],
                "cell_results": [],
                "projection_evidence": [],
                "result": {"status": "incomplete", "reasons": ["MISSING_CELL"]},
            }
        )


def _successful_process(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0,
        stdout="",
        stderr="",
    )


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
    outcome: type[TestPass] | type[TestFail],
    process: ProcessResult,
) -> None:
    with pytest.raises(ValidationError, match="complete normal"):
        outcome(process=process)


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
    process_changes: dict[str, object],
) -> None:
    process = _successful_process().model_dump()
    process.update(process_changes)

    with pytest.raises(ValidationError, match="complete output"):
        TyCheck(process=ProcessResult.model_validate(process), diagnostics=())


def test_ty_check_requires_deterministically_sorted_diagnostics() -> None:
    with pytest.raises(ValidationError, match="sorted by stable identity"):
        TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(_diagnostic(line=2), _diagnostic(line=1)),
        )


def test_static_models_reject_inconsistent_baseline_evidence() -> None:
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
        StaticPassEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest="",
        )
    with pytest.raises(ValidationError, match="empty diagnostic increment"):
        StaticPassEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=digest,
            incremental=(first,),
        )
    with pytest.raises(ValidationError, match="digest cannot be empty"):
        StaticFailEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest="",
            incremental=(first,),
        )
    with pytest.raises(ValidationError, match="non-empty diagnostic increment"):
        StaticFailEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=digest,
            incremental=(),
        )
    with pytest.raises(ValidationError, match="sub-multiset"):
        StaticFailEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=digest,
            incremental=(second,),
        )

    mismatched_proposal = StaticPassEvaluation(
        proposal=other_proposal,
        ty=check,
        baseline_digest=digest,
    )
    with pytest.raises(ValidationError, match="proposal must match"):
        StaticBaselineCapture(baseline=baseline, static=mismatched_proposal)

    mismatched_check = StaticPassEvaluation(
        proposal=proposal,
        ty=other_check,
        baseline_digest=digest,
    )
    with pytest.raises(ValidationError, match="reuse the baseline TyCheck"):
        StaticBaselineCapture(baseline=baseline, static=mismatched_check)

    mismatched_digest = StaticPassEvaluation(
        proposal=proposal,
        ty=check,
        baseline_digest="wrong",
    )
    with pytest.raises(ValidationError, match="digest must match"):
        StaticBaselineCapture(baseline=baseline, static=mismatched_digest)


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
        static=StaticPassEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=baseline.digest,
        ),
        test=TestPass(process=_successful_process()),
    )
    return attempt, baseline, passed


def test_highest_version_and_smoke_results_enforce_their_evidence() -> None:
    attempt, baseline, passed = _baseline_evidence()

    assert (
        HighestVersionPass(
            attempt=attempt,
            baseline=baseline,
            evaluation=passed,
        ).evaluation
        is passed
    )

    increment = _diagnostic()
    static_failure = StaticFailEvaluation(
        proposal=baseline.proposal,
        ty=TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(increment,),
        ),
        baseline_digest=baseline.digest,
        incremental=(increment,),
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
                    attempt=attempt, baseline=baseline, evaluation=passed
                ),
            )
        )


def test_cell_failure_rejects_probe_evidence_from_another_static_baseline() -> None:
    baseline_attempt, baseline, passed = _baseline_evidence()
    increment = _diagnostic()
    vector = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=vector)
    candidate = _proposal("candidate", attempt=attempt, vector=vector)
    wrong_static = StaticFailEvaluation(
        proposal=candidate,
        ty=TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(increment,),
        ),
        baseline_digest="another-baseline",
        incremental=(increment,),
    )
    rejection = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="STATIC_REGRESSION",
        stage="ty",
        process=wrong_static.ty.process,
    )

    with pytest.raises(ValidationError, match="frozen static baseline"):
        CellSearchFailure(
            reason="NO_PASS_IN_SEARCH_SPACE",
            cell=baseline.proposal.cell,
            phase="static-search",
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
                            cause="STATIC_REGRESSION",
                            evaluation=wrong_static,
                        ),
                    ),
                ),
            ),
        )


def test_probe_rejection_requires_its_failure_record() -> None:
    baseline_attempt, baseline, passed = _baseline_evidence()
    vector = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=vector)
    with pytest.raises(ValidationError, match="FailureRecord"):
        CellSearchFailure(
            reason="NO_PASS_IN_SEARCH_SPACE",
            cell=baseline.proposal.cell,
            phase="static-search",
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


def test_probe_rejects_status_that_contradicts_structured_static_evidence() -> None:
    increment = _diagnostic()
    vector = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=vector)
    proposal = _proposal("candidate", attempt=attempt, vector=vector)
    failed = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(increment,),
        ),
        baseline_digest="baseline",
        incremental=(increment,),
    )

    with pytest.raises(ValidationError):
        ProbePass.model_validate(
            {
                "attempt": attempt,
                "proposal_id": proposal.proposal_id,
                "evaluation": failed,
            }
        )


def _indeterminate_evaluation(attempt: Attempt) -> IndeterminateEvaluation:
    proposal = _proposal("candidate", attempt=attempt)
    failure = ToolFailure(
        cause="TOOL_FAILURE",
        stage="test",
        process=_successful_process(exit_code=2),
    )
    return IndeterminateEvaluation(
        proposal=proposal,
        cause=failure.cause,
        failure=failure,
    )


@pytest.mark.parametrize("evidence_kind", ("pass", "rejection", "indeterminate"))
@pytest.mark.parametrize("mismatch", ("proposal", "attempt"))
def test_probe_evidence_must_match_its_proposal_and_attempt(
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
        static = StaticPassEvaluation(
            proposal=indeterminate.proposal,
            ty=TyCheck(process=_successful_process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
        )
        constructor = ProbePass
        payload: dict[str, object] = {
            "attempt": evidence_attempt,
            "proposal_id": proposal_id,
            "evaluation": static,
        }
    elif evidence_kind == "rejection":
        diagnostic = _diagnostic()
        evaluation = StaticFailEvaluation(
            proposal=indeterminate.proposal,
            ty=TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(diagnostic,),
            ),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(diagnostic,),
        )
        constructor = ProbeRejection
        payload = {
            "attempt": evidence_attempt,
            "proposal_id": proposal_id,
            "failure_id": "failure",
            "cause": "STATIC_REGRESSION",
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


def test_probe_observation_requires_the_attempt_requested_vector() -> None:
    requested = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=requested)
    proposal = _proposal("candidate", attempt=attempt, vector=requested)
    static = StaticPassEvaluation(
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
                evaluation=static,
            ),
        )


def test_probe_evidence_requires_an_exact_vector_attempt_and_static_pass() -> None:
    baseline_attempt, baseline, passed = _baseline_evidence()

    with pytest.raises(ValidationError, match="exact-vector"):
        ProbePass(
            attempt=baseline_attempt,
            proposal_id=passed.proposal.proposal_id,
            evaluation=passed.static,
        )
    exact_attempt = _attempt(resolution="exact-vector", vector=())
    with pytest.raises(ValidationError, match="evaluation"):
        ProbePass.model_validate(
            {
                "attempt": exact_attempt,
                "proposal_id": "candidate",
            }
        )


def test_probe_evaluation_must_match_the_attempt_requested_vector() -> None:
    requested = (VersionPin(name="demo", version="1"),)
    actual = (VersionPin(name="demo", version="2"),)
    attempt = _attempt(resolution="exact-vector", vector=requested)
    proposal = _proposal("candidate", attempt=attempt, vector=actual)
    static = StaticPassEvaluation(
        proposal=proposal,
        ty=TyCheck(process=_successful_process(), diagnostics=()),
        baseline_digest=ty_diagnostic_digest(()),
    )

    with pytest.raises(ValidationError, match="requested exact vector"):
        ProbePass(
            attempt=attempt,
            proposal_id=proposal.proposal_id,
            evaluation=static,
        )


def test_probe_rejection_cause_must_match_its_evaluation_kind() -> None:
    vector = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=vector)
    proposal = _proposal("candidate", attempt=attempt, vector=vector)
    diagnostic = _diagnostic()
    static = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(diagnostic,),
        ),
        baseline_digest=ty_diagnostic_digest(()),
        incremental=(diagnostic,),
    )

    with pytest.raises(ValidationError, match="cause must match"):
        ProbeRejection(
            attempt=attempt,
            proposal_id=proposal.proposal_id,
            failure_id="failure",
            cause="TEST_FAILURE",
            evaluation=static,
        )


def test_probe_indeterminate_cause_must_match_its_evaluation() -> None:
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


def test_prepare_rejection_cannot_claim_a_proposal() -> None:
    attempt = _attempt(resolution="exact-vector", vector=())

    with pytest.raises(ValidationError, match="cannot claim a Proposal"):
        ProbeRejection(
            attempt=attempt,
            proposal_id="invented",
            failure_id="failure",
            cause="RESOLUTION_CONFLICT",
        )


def test_structured_probe_rejections_require_their_evaluation() -> None:
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
            phase="dynamic-search",
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


@pytest.mark.parametrize(
    ("predecessor", "failure_id"),
    (("1", None), (None, "failure")),
)
def test_coordinate_boundary_requires_complete_predecessor_evidence(
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


def test_indeterminate_coordinate_requires_its_failure_reference() -> None:
    with pytest.raises(ValidationError, match="requires a failure ID"):
        CoordinateFailure(status="INDETERMINATE", observations=())


def test_indeterminate_coordinate_must_reference_its_observation() -> None:
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


def test_coordinate_success_cannot_contain_indeterminate_evidence() -> None:
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


def test_coordinate_boundary_must_reference_its_rejection_observation() -> None:
    vector = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=vector)

    with pytest.raises(ValidationError, match="rejection observation"):
        CoordinateSuccess(
            vector=vector,
            observations=(
                ProbeObservation(
                    dependency="demo",
                    candidate_version="1",
                    vector=vector,
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


def test_smoke_indeterminate_requires_indeterminate_evidence() -> None:
    attempt, baseline, passed = _baseline_evidence()

    with pytest.raises(ValidationError, match="requires indeterminate evidence"):
        SmokeIndeterminate(
            outcomes=(
                HighestVersionPass(
                    attempt=attempt,
                    baseline=baseline,
                    evaluation=passed,
                ),
            )
        )


def test_baseline_outcome_requires_a_highest_attempt() -> None:
    attempt = _attempt(resolution="exact-vector", vector=())
    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="TEST_FAILURE",
        stage="test",
        process=_successful_process(exit_code=1),
    )

    with pytest.raises(ValidationError, match="highest Attempt"):
        BaselineRejection(attempt=attempt, failure=failure)


def test_baseline_test_rejection_requires_its_structured_evaluation() -> None:
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


def test_baseline_indeterminate_evaluation_must_match_the_captured_baseline() -> None:
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
            ),
        )


def test_baseline_rejection_diagnosis_must_match_its_evaluation() -> None:
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


def test_cell_indeterminate_requires_its_complete_pass_baseline() -> None:
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


def test_probe_attempt_must_share_the_baseline_evaluation_context() -> None:
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
    candidate_static = StaticPassEvaluation(
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
            phase="dynamic-search",
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


def test_search_failure_event_retains_structured_failure_and_evaluation() -> None:
    diagnostic = _diagnostic()
    vector = (VersionPin(name="demo", version="1"),)
    attempt = _attempt(resolution="exact-vector", vector=vector)
    proposal = _proposal("candidate", attempt=attempt, vector=vector)
    static = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(diagnostic,),
        ),
        baseline_digest="baseline",
        incremental=(diagnostic,),
    )
    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="STATIC_REGRESSION",
        stage="ty",
        process=static.ty.process,
    )
    event = SearchFailureEvent(
        cell=proposal.cell,
        failure=failure,
        evaluation=static,
    )

    assert isinstance(event, ActivityEvent)
    assert event.failure == failure


def test_search_diagnostic_event_rejects_a_mismatched_cell() -> None:
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


def test_cell_failure_record_ids_are_unique() -> None:
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


def test_failure_record_rejects_a_tampered_stable_id() -> None:
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


def test_cell_terminal_rejects_a_failure_scoped_to_another_cell() -> None:
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


@pytest.mark.parametrize("mismatch", ("snapshot", "policy"))
def test_report_rejects_static_baseline_outside_its_source_or_policy(
    mismatch: str,
) -> None:
    baseline_attempt, baseline, passed = _baseline_evidence()
    source_digest = "other" if mismatch == "snapshot" else "snapshot"
    policy_identity = "other" if mismatch == "policy" else "policy"
    failure = CellSearchFailure(
        reason="NO_PASS_IN_SEARCH_SPACE",
        cell=baseline.proposal.cell,
        phase="candidate-discovery",
        baseline_attempt=baseline_attempt,
        static_baseline=baseline,
        baseline=passed,
    )

    with pytest.raises(ValidationError, match="report source and policy"):
        generator = GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1")
        package = PackageIdentity(name="demo", pyproject_path="pyproject.toml")
        source = SourceSnapshotIdentity(digest=source_digest, entries=())
        PackageFloorReportV1(
            report_generation_id=report_generation_id(
                generator=generator,
                package=package,
                source_snapshot=source,
                policy_identity=policy_identity,
                requirement_declarations=(),
                target_cells=(baseline.proposal.cell,),
            ),
            generator=generator,
            package=package,
            source_snapshot=source,
            policy_identity=policy_identity,
            requirement_declarations=(),
            candidate_snapshots=(),
            target_cells=(baseline.proposal.cell,),
            cell_results=(failure,),
            projection_evidence=(),
            result=IncompleteReportResult(reasons=("NO_PASS_IN_SEARCH_SPACE",)),
        )

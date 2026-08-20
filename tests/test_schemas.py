from __future__ import annotations

import pytest
from pydantic import ValidationError

from pf.schemas.base import FrozenSchema
from pf.schemas.config import (
    CheckRequest,
    EffectiveConfig,
    MergeRequest,
    SearchRequest,
    SmokeRequest,
)
from pf.schemas.evaluation import (
    HighestVersionVerification,
    PassEvaluation,
    ProcessResult,
    ProcessSpec,
    SmokeTestFailure,
    StaticBaseline,
    StaticBaselineCapture,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
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
)
from pf.schemas.report import (
    CellFailure,
    CoordinateFailure,
    GeneratorIdentity,
    IncompleteReportResult,
    PackageFloorReportV1,
    PackageIdentity,
    ProbeEvidence,
    ProbeObservation,
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
        ProcessResult.model_validate(
            {
                "duration_seconds": 0,
                "stdout_summary": "",
                "stderr_summary": "",
                "stdout_tail": "",
                "stderr_tail": "",
                **facts,
            }
        )


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


def test_static_fail_probe_requires_structured_incremental_evidence() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    with pytest.raises(ValidationError, match="STATIC_FAIL probe requires"):
        PackageFloorReportV1(
            generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
            package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
            source_snapshot=SourceSnapshotIdentity(digest="snapshot", entries=()),
            policy_identity="policy",
            requirement_declarations=(),
            candidate_snapshots=(),
            cell_results=(
                CellFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    cell=cell,
                    phase="static-search",
                    coordinate_failure=CoordinateFailure(
                        status="NO_PASS_IN_SEARCH_SPACE",
                        observations=(
                            ProbeObservation(
                                dependency="demo",
                                candidate_version="1",
                                vector=(),
                                evidence=ProbeEvidence(
                                    status="STATIC_FAIL",
                                    proposal_id="proposal",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            projection_evidence=(),
            result=IncompleteReportResult(reasons=("NO_PASS_IN_SEARCH_SPACE",)),
        )


def _successful_process(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0,
        stdout_summary="",
        stderr_summary="",
        stdout_tail="",
        stderr_tail="",
    )


def _proposal(proposal_id: str) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        snapshot_digest="snapshot",
        cell=Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        ),
        managed_vector=(),
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
        {"stdout_truncated": True},
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


def _baseline_evidence() -> tuple[StaticBaseline, PassEvaluation]:
    proposal = _proposal("baseline")
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
    return baseline, passed


def test_highest_version_and_smoke_results_enforce_their_evidence() -> None:
    baseline, passed = _baseline_evidence()

    assert HighestVersionVerification(
        baseline=baseline,
        evaluation=passed,
    ).evaluation is passed

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
    with pytest.raises(ValidationError, match="cannot produce STATIC_FAIL"):
        HighestVersionVerification(
            baseline=baseline,
            evaluation=static_failure,
        )
    with pytest.raises(ValidationError, match="requires a failed test"):
        SmokeTestFailure(evaluations=())


def test_cell_failure_rejects_probe_evidence_from_another_static_baseline() -> None:
    baseline, passed = _baseline_evidence()
    increment = _diagnostic()
    candidate = _proposal("candidate")
    wrong_static = StaticFailEvaluation(
        proposal=candidate,
        ty=TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(increment,),
        ),
        baseline_digest="another-baseline",
        incremental=(increment,),
    )

    with pytest.raises(ValidationError, match="frozen static baseline"):
        CellFailure(
            status="NO_PASS_IN_SEARCH_SPACE",
            cell=baseline.proposal.cell,
            phase="static-search",
            static_baseline=baseline,
            baseline=passed,
            coordinate_failure=CoordinateFailure(
                status="NO_PASS_IN_SEARCH_SPACE",
                observations=(
                    ProbeObservation(
                        dependency="demo",
                        candidate_version="1",
                        vector=(),
                        evidence=ProbeEvidence(
                            status="STATIC_FAIL",
                            proposal_id=candidate.proposal_id,
                            static=wrong_static,
                        ),
                    ),
                ),
            ),
        )


def test_cell_failure_requires_static_baseline_for_test_failure_evidence() -> None:
    _, passed = _baseline_evidence()
    failed = TestFailEvaluation(
        proposal=passed.proposal,
        static=passed.static,
        test=TestFail(process=_successful_process(exit_code=1)),
    )

    with pytest.raises(ValidationError, match="requires S_hi"):
        CellFailure(
            status="BASELINE_FAILED",
            cell=passed.proposal.cell,
            phase="baseline-evaluation",
            baseline=failed,
        )


def test_probe_rejects_status_that_contradicts_structured_static_evidence() -> None:
    increment = _diagnostic()
    proposal = _proposal("candidate")
    failed = StaticFailEvaluation(
        proposal=proposal,
        ty=TyCheck(
            process=_successful_process(exit_code=1),
            diagnostics=(increment,),
        ),
        baseline_digest="baseline",
        incremental=(increment,),
    )

    with pytest.raises(ValidationError, match="status must match"):
        ProbeEvidence(
            status="PASS",
            proposal_id=proposal.proposal_id,
            static=failed,
        )


@pytest.mark.parametrize("mismatch", ("proposal", "ty"))
def test_cell_failure_baseline_must_be_the_captured_v_hi_evidence(
    mismatch: str,
) -> None:
    baseline, passed = _baseline_evidence()
    proposal = (
        _proposal("other-v-hi") if mismatch == "proposal" else passed.proposal
    )
    ty = (
        passed.static.ty
        if mismatch == "proposal"
        else TyCheck(process=_successful_process(exit_code=1), diagnostics=())
    )
    failed = TestFailEvaluation(
        proposal=proposal,
        static=StaticPassEvaluation(
            proposal=proposal,
            ty=ty,
            baseline_digest=baseline.digest,
        ),
        test=TestFail(process=_successful_process(exit_code=1)),
    )

    with pytest.raises(ValidationError, match="captured V_hi"):
        CellFailure(
            status="BASELINE_FAILED",
            cell=baseline.proposal.cell,
            phase="baseline-evaluation",
            static_baseline=baseline,
            baseline=failed,
        )


@pytest.mark.parametrize("mismatch", ("snapshot", "policy"))
def test_report_rejects_static_baseline_outside_its_source_or_policy(
    mismatch: str,
) -> None:
    baseline, passed = _baseline_evidence()
    source_digest = "other" if mismatch == "snapshot" else "snapshot"
    policy_identity = "other" if mismatch == "policy" else "policy"
    failure = CellFailure(
        status="NO_PASS_IN_SEARCH_SPACE",
        cell=baseline.proposal.cell,
        phase="candidate-discovery",
        static_baseline=baseline,
        baseline=passed,
    )

    with pytest.raises(ValidationError, match="report source and policy"):
        PackageFloorReportV1(
            generator=GeneratorIdentity(name="pf", version="0.1.0", algorithm="v1"),
            package=PackageIdentity(name="demo", pyproject_path="pyproject.toml"),
            source_snapshot=SourceSnapshotIdentity(digest=source_digest, entries=()),
            policy_identity=policy_identity,
            requirement_declarations=(),
            candidate_snapshots=(),
            target_cells=(baseline.proposal.cell,),
            cell_results=(failure,),
            projection_evidence=(),
            result=IncompleteReportResult(reasons=("NO_PASS_IN_SEARCH_SPACE",)),
        )

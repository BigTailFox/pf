from __future__ import annotations

import pytest
from pydantic import ValidationError

from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    CellFailureScope,
    FailureCause,
    FailureDetail,
    FailureRecord,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticPassEvaluation,
    TestPass,
    ToolFailure,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, Proposal, VersionPin


def _cell() -> Cell:
    return Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
        active_declaration_ids=("demo:a",),
    )


def _process() -> ProcessResult:
    return ProcessResult(
        exit_code=1,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="No solution found",
    )


def _probe_attempt() -> Attempt:
    identity = AttemptIdentity(
        source_snapshot_digest="snapshot",
        cell=_cell(),
        requested_resolution="exact-vector",
        requested_managed_vector=(VersionPin(name="a", version="1"),),
        active_declaration_ids=("demo:a",),
        source_plan_identity="sources",
        evaluation_policy_identity="policy",
    )
    return Attempt.from_identity(identity)


def _highest_attempt() -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=_cell(),
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=("demo:a",),
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )


def test_failure_policy_requires_an_attempt_before_it_can_reject() -> None:
    policy = FailurePolicy()

    rejected = policy.classify(
        scope=AttemptFailureScope(attempt=_probe_attempt()),
        cause="RESOLUTION_CONFLICT",
        stage="install-project",
        process=_process(),
    )
    indeterminate = policy.classify(
        scope=CellFailureScope(
            package="demo",
            cell=_cell(),
            source_snapshot_digest="snapshot",
            evaluation_policy_identity="policy",
        ),
        cause="RESOLUTION_CONFLICT",
        stage="candidate-discovery",
        process=None,
        detail=FailureDetail(
            code="candidate-discovery-failed",
            message="candidate discovery stopped before an attempt was available",
        ),
    )

    assert rejected.disposition == "REJECTED"
    assert rejected.cause == "RESOLUTION_CONFLICT"
    assert isinstance(rejected.scope, AttemptFailureScope)
    assert rejected.scope.attempt.identity.requested_managed_vector == (
        VersionPin(name="a", version="1"),
    )
    assert rejected.failure_id.startswith("failure-")
    assert indeterminate.disposition == "INDETERMINATE"


def test_failure_record_identity_ignores_captured_process_output() -> None:
    policy = FailurePolicy()
    scope = AttemptFailureScope(attempt=_probe_attempt())
    first = policy.classify(
        scope=scope,
        cause="TEST_FAILURE",
        stage="test",
        process=_process().model_copy(update={"stdout": "first run"}),
    )
    second = policy.classify(
        scope=scope,
        cause="TEST_FAILURE",
        stage="test",
        process=_process().model_copy(update={"stdout": "second run"}),
    )

    assert first.failure_id == second.failure_id
    assert "first run" not in first.model_dump_json()
    assert "No solution found" not in first.model_dump_json()


@pytest.mark.parametrize(
    ("cause", "stage"),
    (
        ("RESOLUTION_CONFLICT", "install-project"),
        ("BUILD_FAILURE", "install"),
        ("HARNESS_CONFLICT", "install-harness"),
        ("STATIC_REGRESSION", "ty"),
        ("TEST_FAILURE", "test"),
    ),
)
def test_failure_policy_rejects_complete_probe_contract_failures(
    cause: FailureCause,
    stage: str,
) -> None:
    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=_probe_attempt()),
        cause=cause,
        stage=stage,
        process=_process(),
    )

    assert failure.disposition == "REJECTED"
    assert failure.cause == cause


def test_failure_policy_rejects_a_static_regression_when_ty_exits_zero() -> None:
    process = _process().model_copy(
        update={"exit_code": 0, "stderr": ""}
    )

    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=_probe_attempt()),
        cause="STATIC_REGRESSION",
        stage="ty",
        process=process,
    )

    assert failure.disposition == "REJECTED"


@pytest.mark.parametrize(
    ("attempt", "cause", "stage", "process"),
    (
        (_highest_attempt(), "STATIC_REGRESSION", "ty", _process()),
        (_probe_attempt(), "TEST_FAILURE", "install", _process()),
        (
            _probe_attempt(),
            "RESOLUTION_CONFLICT",
            "install-harness",
            _process(),
        ),
        (
            _probe_attempt(),
            "TEST_FAILURE",
            "test",
            _process().model_copy(update={"stderr_complete": False}),
        ),
    ),
)
def test_failure_policy_does_not_reject_an_invalid_role_stage_or_incomplete_fact(
    attempt: Attempt,
    cause: FailureCause,
    stage: str,
    process: ProcessResult,
) -> None:
    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause=cause,
        stage=stage,
        process=process,
    )

    assert failure.disposition == "INDETERMINATE"


@pytest.mark.parametrize(
    "process",
    (
        _process().model_copy(update={"timed_out": True}),
        _process().model_copy(update={"stdout_complete": False}),
    ),
)
def test_failure_record_rejects_forged_rejection_dispositions(
    process: ProcessResult,
) -> None:
    with pytest.raises(ValidationError, match="REJECTED disposition"):
        FailureRecord.from_facts(
            scope=AttemptFailureScope(attempt=_probe_attempt()),
            disposition="REJECTED",
            cause="TEST_FAILURE",
            stage="test",
            process=process,
        )


@pytest.mark.parametrize(
    "change",
    (
        {"source_snapshot_digest": ""},
        {"source_plan_identity": ""},
        {"evaluation_policy_identity": ""},
        {"active_declaration_ids": ()},
        {
            "requested_resolution": "highest",
            "requested_managed_vector": (VersionPin(name="a", version="1"),),
        },
        {
            "requested_resolution": "exact-vector",
            "requested_managed_vector": None,
        },
        {
            "requested_resolution": "exact-vector",
            "requested_managed_vector": (
                VersionPin(name="b", version="1"),
                VersionPin(name="a", version="1"),
            ),
        },
    ),
)
def test_attempt_identity_rejects_ambiguous_or_unstable_facts(
    change: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "source_snapshot_digest": "snapshot",
        "cell": _cell(),
        "requested_resolution": "highest",
        "requested_managed_vector": None,
        "active_declaration_ids": ("demo:a",),
        "source_plan_identity": "sources",
        "evaluation_policy_identity": "policy",
    }
    payload.update(change)

    with pytest.raises(ValidationError):
        AttemptIdentity.model_validate(payload)


def test_attempt_rejects_a_tampered_identity_digest() -> None:
    attempt = _probe_attempt()

    with pytest.raises(ValidationError, match="attempt ID"):
        Attempt(attempt_id="tampered", identity=attempt.identity)


@pytest.mark.parametrize(
    "change",
    (
        {"package": "other"},
        {"source_snapshot_digest": ""},
        {"evaluation_policy_identity": ""},
    ),
)
def test_cell_failure_scope_requires_complete_matching_identity(
    change: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "package": "demo",
        "cell": _cell(),
        "source_snapshot_digest": "snapshot",
        "evaluation_policy_identity": "policy",
    }
    payload.update(change)

    with pytest.raises(ValidationError):
        CellFailureScope.model_validate(payload)


@pytest.mark.parametrize(
    "detail",
    (
        {"code": "", "message": "message"},
        {"code": "code", "message": "  "},
    ),
)
def test_failure_detail_requires_machine_and_human_facts(
    detail: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        FailureDetail.model_validate(detail)


def test_failure_record_requires_a_stage_and_diagnostic_facts() -> None:
    scope = AttemptFailureScope(attempt=_probe_attempt())

    with pytest.raises(ValidationError, match="stage cannot be empty"):
        FailureRecord.from_facts(
            scope=scope,
            disposition="INDETERMINATE",
            cause="TOOL_FAILURE",
            stage=" ",
            process=_process(),
        )
    with pytest.raises(ValidationError, match="process facts or structured detail"):
        FailureRecord.from_facts(
            scope=scope,
            disposition="INDETERMINATE",
            cause="TOOL_FAILURE",
            stage="test",
            process=None,
        )
    with pytest.raises(ValidationError, match="cell-scoped"):
        FailureRecord.from_facts(
            scope=CellFailureScope(
                package="demo",
                cell=_cell(),
                source_snapshot_digest="snapshot",
                evaluation_policy_identity="policy",
            ),
            disposition="REJECTED",
            cause="TEST_FAILURE",
            stage="test",
            process=_process(),
        )


def _highest_evidence() -> tuple[Attempt, StaticBaseline, PassEvaluation]:
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=_cell(),
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=("demo:a",),
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )
    proposal = Proposal(
        proposal_id="highest",
        attempt_id=attempt.attempt_id,
        snapshot_digest="snapshot",
        cell=_cell(),
        managed_vector=(VersionPin(name="a", version="2"),),
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
    )
    check = TyCheck(
        process=_process().model_copy(update={"exit_code": 0}),
        diagnostics=(),
    )
    baseline = StaticBaseline(
        proposal=proposal,
        ty=check,
        digest=ty_diagnostic_digest(()),
    )
    passed = PassEvaluation(
        proposal=proposal,
        static=StaticPassEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=baseline.digest,
        ),
        test=TestPass(process=check.process),
    )
    return attempt, baseline, passed


@pytest.mark.parametrize("mismatch", ("proposal", "attempt", "ty", "digest"))
def test_highest_version_pass_rejects_mixed_evidence(mismatch: str) -> None:
    attempt, baseline, passed = _highest_evidence()
    other_attempt = _probe_attempt()
    other_proposal = passed.proposal.model_copy(
        update={
            "proposal_id": "other",
            "attempt_id": other_attempt.attempt_id,
        }
    )
    if mismatch == "proposal":
        passed = passed.model_copy(update={"proposal": other_proposal})
    elif mismatch == "attempt":
        baseline = baseline.model_copy(update={"proposal": other_proposal})
        passed = passed.model_copy(
            update={
                "proposal": other_proposal,
                "static": passed.static.model_copy(update={"proposal": other_proposal}),
            }
        )
    elif mismatch == "ty":
        other_check = baseline.ty.model_copy(
            update={
                "process": baseline.ty.process.model_copy(
                    update={"duration_seconds": 1.0}
                )
            }
        )
        passed = passed.model_copy(
            update={"static": passed.static.model_copy(update={"ty": other_check})}
        )
    else:
        passed = passed.model_copy(
            update={
                "static": passed.static.model_copy(update={"baseline_digest": "other"})
            }
        )

    with pytest.raises(ValidationError):
        HighestVersionPass(
            attempt=attempt,
            baseline=baseline,
            evaluation=passed,
        )


def test_indeterminate_evaluation_retains_the_adapter_cause() -> None:
    _, _, passed = _highest_evidence()
    failure = ToolFailure(
        cause="TIMEOUT",
        stage="test",
        process=_process(),
    )

    with pytest.raises(ValidationError, match="retain its tool cause"):
        IndeterminateEvaluation(
            proposal=passed.proposal,
            cause="TOOL_FAILURE",
            failure=failure,
        )

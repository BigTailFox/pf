from __future__ import annotations

import pytest

from conftest import empty_harness_baseline
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
    ProcessTerminalUnavailable,
    NormalExit,
    StaticBaseline,
    StaticUnchangedEvaluation,
    ToolFailure,
    TyCheck,
    VerifierPass,
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
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=check,
            baseline_digest=baseline.digest,
        ),
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )
    return attempt, baseline, passed


class TestFailurePolicy:
    def test_typed_terminal_unavailable_uses_portable_structured_authority(
        self,
    ) -> None:
        unavailable = ProcessTerminalUnavailable(
            duration_seconds=0.2,
            detail="runner returned no terminal status",
        )

        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_probe_attempt()),
            cause="TOOL_FAILURE",
            stage="resolve-project",
            process=unavailable,
        )

        assert failure.process is None
        assert failure.disposition == "INDETERMINATE"
        assert "runtime_process" not in failure.model_dump(mode="json")
        assert FailureRecord.model_validate(failure.model_dump()) == failure

    def test_failure_record_retains_only_acquired_resolution_plan_evidence(
        self,
    ) -> None:
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_probe_attempt()),
            cause="SOURCE_FAILURE",
            stage="install-environment",
            process=_process(),
            project_plan_digest="project-plan",
            environment_plan_digest="environment-plan",
        )

        assert failure.project_plan_digest == "project-plan"
        assert failure.environment_plan_digest == "environment-plan"
        with pytest.raises(ValidationError, match="requires a project plan"):
            FailureRecord.from_facts(
                scope=AttemptFailureScope(attempt=_probe_attempt()),
                disposition="INDETERMINATE",
                cause="TOOL_FAILURE",
                stage="resolve-environment",
                process=_process(),
                environment_plan_digest="invented-environment-plan",
            )

    def test_failure_policy_requires_an_attempt_before_it_can_reject(self) -> None:
        policy = FailurePolicy()

        rejected = policy.classify(
            scope=AttemptFailureScope(attempt=_probe_attempt()),
            cause="RESOLUTION_CONFLICT",
            stage="resolve-project",
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

    @pytest.mark.parametrize(
        ("cause", "stage"),
        (
            ("RESOLUTION_CONFLICT", "resolve-project"),
            ("HARNESS_CONFLICT", "resolve-environment"),
            ("RUNTIME_INTERFACE_MISSING", "witness"),
        ),
    )
    def test_failure_policy_rejects_complete_probe_contract_failures(
        self,
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

    def test_install_or_build_failure_does_not_prove_unsat(self) -> None:
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_probe_attempt()),
            cause="BUILD_FAILURE",
            stage="install-environment",
            process=_process(),
        )

        assert failure.disposition == "INDETERMINATE"

    def test_failure_policy_rejects_confirmed_missing_on_witness_exit_zero(
        self,
    ) -> None:
        process = _process().model_copy(update={"exit_code": 0, "stderr": ""})

        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_probe_attempt()),
            cause="RUNTIME_INTERFACE_MISSING",
            stage="witness",
            process=process,
        )

        assert failure.disposition == "REJECTED"

    @pytest.mark.parametrize(
        ("attempt", "cause", "stage", "process"),
        (
            (_probe_attempt(), "RESOLUTION_CONFLICT", "install", _process()),
            (
                _probe_attempt(),
                "RESOLUTION_CONFLICT",
                "install-harness",
                _process(),
            ),
            (
                _probe_attempt(),
                "RESOLUTION_CONFLICT",
                "resolve-project",
                _process().model_copy(update={"stderr_complete": False}),
            ),
        ),
    )
    def test_failure_policy_does_not_reject_an_invalid_role_stage_or_incomplete_fact(
        self,
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

    @pytest.mark.parametrize("mismatch", ("proposal", "attempt", "ty", "digest"))
    def test_highest_version_pass_rejects_mixed_evidence(self, mismatch: str) -> None:
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
                    "static": passed.static.model_copy(
                        update={"proposal": other_proposal}
                    ),
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
                    "static": passed.static.model_copy(
                        update={"baseline_digest": "other"}
                    )
                }
            )

        with pytest.raises(ValidationError):
            HighestVersionPass(
                attempt=attempt,
                baseline=baseline,
                harness_baseline=empty_harness_baseline(attempt.identity.cell),
                evaluation=passed,
            )

    def test_indeterminate_evaluation_retains_the_adapter_cause(self) -> None:
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

    def test_classify_evaluation_returns_none_for_pass(self) -> None:
        _, _, passed = _highest_evidence()
        assert (
            FailurePolicy().record_evaluation(
                AttemptFailureScope(attempt=_highest_attempt()),
                passed,
            )
            is None
        )

    def test_indeterminate_evaluation_projects_structured_failure_detail(self) -> None:
        _, _, passed = _highest_evidence()
        detail = FailureDetail(
            code="managed-source-mismatch",
            message="the selected source did not match the SourcePlan",
        )
        evaluation = IndeterminateEvaluation(
            proposal=passed.proposal,
            cause="INTERNAL_INVARIANT",
            failure=ToolFailure(
                cause="INTERNAL_INVARIANT",
                stage="resolve-project",
                process=None,
                detail=detail,
            ),
        )

        record = FailurePolicy().record_evaluation(
            AttemptFailureScope(attempt=_highest_attempt()),
            evaluation,
        )

        assert record is not None
        assert record.detail == detail


class TestFailureRecords:
    def test_configured_verifier_failure_identity_contains_only_terminal_facts(
        self,
    ) -> None:
        scope = AttemptFailureScope(attempt=_probe_attempt())

        first = FailureRecord.from_verifier(
            scope=scope,
            disposition="REJECTED",
            cause="VERIFIER_EXITED_NONZERO",
            stage="test",
            terminal=NormalExit(exit_code=4),
        )
        second = FailureRecord.from_verifier(
            scope=scope,
            disposition="REJECTED",
            cause="VERIFIER_EXITED_NONZERO",
            stage="test",
            terminal=NormalExit(exit_code=4),
        )

        assert first.failure_id == second.failure_id
        assert first.authority.kind == "configured-verifier"
        assert first.model_dump(mode="json")["authority"] == {
            "kind": "configured-verifier",
            "terminal": {"kind": "normal-exit", "exit_code": 4},
        }

    def test_failure_record_identity_ignores_captured_process_output(self) -> None:
        policy = FailurePolicy()
        scope = AttemptFailureScope(attempt=_probe_attempt())
        first = policy.classify(
            scope=scope,
            cause="SOURCE_FAILURE",
            stage="install-environment",
            process=_process().model_copy(update={"stdout": "first run"}),
        )
        second = policy.classify(
            scope=scope,
            cause="SOURCE_FAILURE",
            stage="install-environment",
            process=_process().model_copy(update={"stdout": "second run"}),
        )

        assert first.failure_id == second.failure_id
        assert "first run" not in first.model_dump_json()
        assert "No solution found" not in first.model_dump_json()

    @pytest.mark.parametrize(
        "process",
        (
            _process().model_copy(update={"timed_out": True}),
            _process().model_copy(update={"stdout_complete": False}),
        ),
    )
    def test_failure_record_rejects_forged_rejection_dispositions(
        self,
        process: ProcessResult,
    ) -> None:
        with pytest.raises(ValidationError, match="REJECTED disposition"):
            FailureRecord.from_facts(
                scope=AttemptFailureScope(attempt=_probe_attempt()),
                disposition="REJECTED",
                cause="RESOLUTION_CONFLICT",
                stage="resolve-project",
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
        self,
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

    def test_attempt_rejects_a_tampered_identity_digest(self) -> None:
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
        self,
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
        self,
        detail: dict[str, str],
    ) -> None:
        with pytest.raises(ValidationError, match="cannot be empty"):
            FailureDetail.model_validate(detail)

    def test_failure_record_requires_a_stage_and_diagnostic_facts(self) -> None:
        scope = AttemptFailureScope(attempt=_probe_attempt())

        with pytest.raises(ValidationError, match="stage cannot be empty"):
            FailureRecord.from_facts(
                scope=scope,
                disposition="INDETERMINATE",
                cause="TOOL_FAILURE",
                stage=" ",
                process=_process(),
            )
        with pytest.raises(ValueError, match="process or structured authority"):
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
                cause="RESOLUTION_CONFLICT",
                stage="resolve-project",
                process=_process(),
            )

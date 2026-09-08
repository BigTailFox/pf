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
    FailureDetail,
    FailureRecord,
    HighestVersionPass,
    IndeterminateEvaluation,
    VerifierIndeterminate,
    PassEvaluation,
    ProcessResult,
    ProcessTerminalUnavailable,
    NormalExit,
    VerifierPass,
    PrepareFailure,
    ExecutionFailure,
    ExecutionFailureAuthority,
    StructuredOperationFailure,
    SourceAccessFailedFact,
    Unattributed,
    TimedOut,
    execution_terminal,
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


def _probe_attempt(*, harness: bool = False) -> Attempt:
    identity = AttemptIdentity(
        source_snapshot_digest="snapshot",
        cell=_cell(),
        requested_resolution="exact-vector",
        requested_managed_vector=(VersionPin(name="a", version="1"),),
        active_declaration_ids=("demo:a",),
        source_plan_identity="sources",
        execution_policy_identity="policy",
        resolution_context_digest="context",
        harness_policy_identity="harness-relaxation-v1",
        harness_baseline_digest="baseline",
        selected_candidate_evidence_digest="selection",
        harness_declaration_ids=("test-harness",) if harness else (),
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
            execution_policy_identity="policy",
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
        )
    )


def _highest_evidence() -> tuple[Attempt, PassEvaluation]:
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=_cell(),
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=("demo:a",),
            source_plan_identity="sources",
            execution_policy_identity="policy",
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
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
    passed = PassEvaluation(
        proposal=proposal,

        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )
    return attempt, passed


class TestFailurePolicy:
    def test_typed_terminal_unavailable_uses_portable_structured_authority(
        self,
    ) -> None:
        unavailable = ProcessTerminalUnavailable(
            duration_seconds=0.2,
            detail="runner returned no terminal status",
        )

        failure = FailurePolicy().record_prepare(PrepareFailure(
            attempt=_probe_attempt(),
            stage="resolve-project",
            failure=ExecutionFailure(terminal=execution_terminal(unavailable), attribution=Unattributed()),
            process=unavailable,
            project_plan_digest=None, environment_plan_digest=None,
        ))

        assert failure.process is None
        assert failure.disposition == "INDETERMINATE"
        assert "runtime_process" not in failure.model_dump(mode="json")
        assert FailureRecord.model_validate(failure.model_dump()) == failure

    def test_failure_record_retains_only_acquired_resolution_plan_evidence(
        self,
    ) -> None:
        failure = FailurePolicy().record_prepare(PrepareFailure(
            attempt=_probe_attempt(harness=True),
            stage="install-environment",
            failure=StructuredOperationFailure(fact=SourceAccessFailedFact(), terminal=None),
            project_plan_digest="a" * 64,
            environment_plan_digest="b" * 64,
        ))

        assert failure.project_plan_digest == "a" * 64
        assert failure.environment_plan_digest == "b" * 64
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

        rejected = policy.record_prepare(PrepareFailure(
            attempt=_probe_attempt(),
            stage="resolve-project",
            failure=ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=Unattributed()),
            project_plan_digest=None, environment_plan_digest=None,
        ))
        indeterminate = policy.classify(
            scope=CellFailureScope(
                package="demo",
                cell=_cell(),
                source_snapshot_digest="snapshot",
                execution_policy_identity="policy",
            ),
            cause="TOOL_FAILURE",
            stage="candidate-discovery",
            process=None,
            detail=FailureDetail(
                code="candidate-discovery-failed",
                message="candidate discovery stopped before an attempt was available",
            ),
        )

        assert rejected.disposition == "REJECTED"
        assert rejected.cause == "RESOLUTION_FAILED"
        assert isinstance(rejected.scope, AttemptFailureScope)
        assert rejected.scope.attempt.identity.requested_managed_vector == (
            VersionPin(name="a", version="1"),
        )
        assert rejected.failure_id.startswith("failure-")
        assert indeterminate.disposition == "INDETERMINATE"


    def test_install_or_build_failure_does_not_prove_unsat(self) -> None:
        failure = FailurePolicy().record_prepare(PrepareFailure(
            attempt=_probe_attempt(),
            stage="install-project",
            failure=ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=Unattributed()),
            process=_process(),
            project_plan_digest="a" * 64, environment_plan_digest=None,
        ))

        assert failure.disposition == "REJECTED"
        assert failure.cause == "INSTALLATION_FAILED"
        assert isinstance(failure.authority, ExecutionFailureAuthority)
        assert failure.authority.attribution == Unattributed()


    @pytest.mark.parametrize("mismatch", ("cell", "attempt"))
    def test_highest_version_pass_rejects_mixed_dynamic_evidence(self, mismatch: str) -> None:
        attempt, passed = _highest_evidence()
        changes = (
            {"cell": passed.proposal.cell.model_copy(update={"python_minor": "3.12"})}
            if mismatch == "cell" else {"attempt_id": _probe_attempt().attempt_id}
        )
        passed = passed.model_copy(update={"proposal": passed.proposal.model_copy(update=changes)})

        with pytest.raises(ValidationError):
            HighestVersionPass(

                attempt=attempt,

                harness_baseline=empty_harness_baseline(attempt.identity.cell),
                evaluation=passed,
            )

    def test_indeterminate_evaluation_retains_the_verifier_terminal_cause(self) -> None:
        _, passed = _highest_evidence()
        with pytest.raises(ValidationError, match="cause must match its terminal"):
            IndeterminateEvaluation(
                proposal=passed.proposal,
                cause="TOOL_FAILURE",
                verifier=VerifierIndeterminate(terminal=TimedOut(), reason="process-timed-out"),

            )

    def test_classify_evaluation_returns_none_for_pass(self) -> None:
        _, passed = _highest_evidence()
        assert (
            FailurePolicy().record_evaluation(
                AttemptFailureScope(attempt=_highest_attempt()),
                passed,
            )
            is None
        )

    def test_static_collection_cannot_be_classified_as_a_dynamic_failure(self) -> None:
        with pytest.raises(ValidationError, match="static collection"):
            FailurePolicy().classify(
                scope=AttemptFailureScope(attempt=_highest_attempt()),
                cause="TIMEOUT", stage="ty", process=_process(),
            )



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
        records = []
        for process in (
            _process().model_copy(update={"stdout": "first run"}),
            _process().model_copy(update={"stdout": "second run", "duration_seconds": 9, "stderr_complete": False}),
        ):
            records.append(policy.record_prepare(PrepareFailure(
                attempt=_probe_attempt(), stage="install-project",
                failure=ExecutionFailure(terminal=execution_terminal(process), attribution=Unattributed()),
                process=process, project_plan_digest="a" * 64, environment_plan_digest=None,
            )))
        first, second = records

        assert first.failure_id == second.failure_id
        assert "first run" not in first.model_dump_json()
        assert "No solution found" not in first.model_dump_json()

    @pytest.mark.parametrize(
        "terminal",
        (
            TimedOut(),
            NormalExit(exit_code=1),
        ),
    )
    def test_failure_record_rejects_forged_rejection_dispositions(
        self,
        terminal,
    ) -> None:
        with pytest.raises(ValidationError, match="disposition and cause"):
            FailureRecord.from_authority(
                scope=AttemptFailureScope(attempt=_probe_attempt()),
                disposition="REJECTED",
                cause="RESOLUTION_CONFLICT",
                stage="resolve-project",
                authority=ExecutionFailureAuthority(terminal=terminal, attribution=Unattributed()),
            )

    @pytest.mark.parametrize(
        "change",
        (
            {"source_snapshot_digest": ""},
            {"source_plan_identity": ""},
            {"execution_policy_identity": ""},
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
            "execution_policy_identity": "policy",
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
            {"execution_policy_identity": ""},
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
            "execution_policy_identity": "policy",
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
                    execution_policy_identity="policy",
                ),
                disposition="REJECTED",
                cause="RESOLUTION_CONFLICT",
                stage="resolve-project",
                process=_process(),
            )

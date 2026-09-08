from __future__ import annotations

from pf.schemas.evaluation import (
    AttemptFailureScope,
    Evaluation,
    FailureCause,
    FailureDetail,
    FailureRecord,
    FailureScope,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessObservation,
    VerifierRejectedEvaluation,
    PrepareFailure,
    ExecutionFailure,
    ExecutionFailureAuthority,
    StructuredOperationFailureAuthority,
    classify_operation_failure,
)


class FailurePolicy:
    """Turn scoped operation facts into one conservative search disposition."""

    identity = "failure-execution-v4"

    def record_prepare(self, prepared: PrepareFailure) -> FailureRecord:
        disposition, cause = classify_operation_failure(prepared.stage, prepared.failure)
        authority = (
            ExecutionFailureAuthority.model_validate(prepared.failure.model_dump(mode="python"))
            if isinstance(prepared.failure, ExecutionFailure)
            else StructuredOperationFailureAuthority.model_validate(prepared.failure.model_dump(mode="python"))
        )
        return FailureRecord.from_authority(
            scope=AttemptFailureScope(attempt=prepared.attempt),
            stage=prepared.stage, disposition=disposition, cause=cause,
            authority=authority, project_plan_digest=prepared.project_plan_digest,
            environment_plan_digest=prepared.environment_plan_digest,
        )

    def classify(
        self,
        *,
        scope: FailureScope,
        cause: FailureCause,
        stage: str,
        process: ProcessObservation | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
    ) -> FailureRecord:
        return FailureRecord.from_facts(
            scope=scope,
            disposition="INDETERMINATE",
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )

    def record_evaluation(
        self,
        scope: FailureScope,
        evaluation: Evaluation,
        *,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
    ) -> FailureRecord | None:
        if isinstance(evaluation, PassEvaluation):
            return None
        if isinstance(evaluation, VerifierRejectedEvaluation):
            return FailureRecord.from_verifier(
                scope=scope,
                disposition="REJECTED",
                cause="VERIFIER_EXITED_NONZERO",
                stage="test",
                terminal=evaluation.verifier.terminal,
                project_plan_digest=project_plan_digest,
                environment_plan_digest=environment_plan_digest,
            )
        assert isinstance(evaluation, IndeterminateEvaluation)
        return FailureRecord.from_verifier(
            scope=scope,
            disposition="INDETERMINATE",
            cause=evaluation.cause,
            stage="test",
            terminal=evaluation.verifier.terminal,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )

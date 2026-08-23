from __future__ import annotations

from pf.schemas.evaluation import (
    AttemptFailureScope,
    Evaluation,
    FailureCause,
    FailureDetail,
    FailureRecord,
    FailureScope,
    PassEvaluation,
    ProcessResult,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessResult,
    TestFailEvaluation,
    rejection_is_supported,
)


class FailurePolicy:
    """Turn scoped operation facts into one conservative search disposition."""

    identity = "failure-runtime-v1"

    def classify(
        self,
        *,
        scope: FailureScope,
        cause: FailureCause,
        stage: str,
        process: ProcessResult | None,
        summary_code: str | None = None,
        detail: FailureDetail | None = None,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
    ) -> FailureRecord:
        requested_resolution = (
            scope.attempt.identity.requested_resolution
            if isinstance(scope, AttemptFailureScope)
            else None
        )
        supported = process is not None and rejection_is_supported(
            requested_resolution=requested_resolution,
            cause=cause,
            stage=stage,
            exit_code=process.exit_code,
            signal=process.signal,
            start_error=process.start_error,
            timed_out=process.timed_out,
            stdout_complete=process.stdout_complete,
            stderr_complete=process.stderr_complete,
        )
        return FailureRecord.from_facts(
            scope=scope,
            disposition="REJECTED" if supported else "INDETERMINATE",
            cause=cause,
            stage=stage,
            process=process,
            summary_code=summary_code,
            detail=detail,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )

    def classify_evaluation(
        self,
        scope: FailureScope,
        evaluation: Evaluation,
        *,
        project_plan_digest: str | None = None,
        environment_plan_digest: str | None = None,
    ) -> FailureRecord | None:
        if isinstance(evaluation, PassEvaluation):
            return None
        if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
            confirmed = next(
                attempt.outcome
                for attempt in evaluation.witnesses
                if isinstance(attempt.outcome, RuntimeWitnessResult)
                and attempt.outcome.status == "CONFIRMED_MISSING"
            )
            return self.classify(
                scope=scope,
                cause="RUNTIME_INTERFACE_MISSING",
                stage="witness",
                process=confirmed.process,
                project_plan_digest=project_plan_digest,
                environment_plan_digest=environment_plan_digest,
            )
        if isinstance(evaluation, TestFailEvaluation):
            return self.classify(
                scope=scope,
                cause="TEST_FAILURE",
                stage="test",
                process=evaluation.test.process,
                project_plan_digest=project_plan_digest,
                environment_plan_digest=environment_plan_digest,
            )
        return self.classify(
            scope=scope,
            cause=evaluation.cause,
            stage=evaluation.failure.stage,
            process=evaluation.failure.process,
            summary_code=evaluation.failure.summary_code,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )

from __future__ import annotations

from pf.environment import EnvironmentFactory, HighestResolution
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    HighestVersionOutcome,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    PrepareFailure,
    ProcessTerminalUnavailable,
    VerifierRejectedEvaluation,
)
from pf.schemas.project import Cell, PackagePlan, SourcePlan
from pf.snapshot import SourceSnapshot


class HighestVersionVerifier:
    """Fully verify one highest-resolution environment and close it."""

    def __init__(
        self,
        *,
        environments: EnvironmentFactory,
        static: StaticEvaluator,
        full: RuntimeEvaluator,
        failures: FailurePolicy | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._failures = failures or FailurePolicy()

    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> HighestVersionOutcome:
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=HighestResolution(),
            source_plan=source_plan,
        )
        if isinstance(prepared, PrepareFailure):
            failure = self._failures.classify(
                scope=AttemptFailureScope(attempt=prepared.attempt),
                cause=prepared.failure.cause,
                stage=prepared.failure.stage,
                process=prepared.failure.process,
                summary_code=prepared.failure.summary_code,
                detail=prepared.failure.detail,
                project_plan_digest=prepared.project_plan_digest,
                environment_plan_digest=prepared.environment_plan_digest,
            )
            if failure.disposition == "REJECTED":
                return BaselineRejection(
                    attempt=prepared.attempt,
                    failure=failure,
                )
            return BaselineIndeterminate(
                attempt=prepared.attempt,
                failure=failure,
                failure_process=(
                    prepared.failure.process
                    if isinstance(
                        prepared.failure.process,
                        ProcessTerminalUnavailable,
                    )
                    else None
                ),
            )
        try:
            capture = self._static.capture(prepared, package=package)
            if isinstance(capture, IndeterminateEvaluation):
                assert capture.failure is not None
                return BaselineIndeterminate(
                    attempt=prepared.attempt,
                    failure=self._failures.classify(
                        scope=AttemptFailureScope(attempt=prepared.attempt),
                        cause=capture.cause,
                        stage=capture.failure.stage,
                        process=capture.failure.process,
                        summary_code=capture.failure.summary_code,
                        project_plan_digest=prepared.project_plan.semantic_digest,
                        environment_plan_digest=prepared.environment_plan.semantic_digest,
                    ),
                    evaluation=capture,
                )
            run = self._full.evaluate(
                prepared,
                package=package,
                baseline=capture.baseline,
                static_result=capture.static,
            )
            evaluation = run.evaluation
            if isinstance(evaluation, PassEvaluation):
                return HighestVersionPass(
                    attempt=prepared.attempt,
                    baseline=capture.baseline,
                    harness_baseline=prepared.harness_baseline,
                    evaluation=evaluation,
                )
            failure = self._failures.record_evaluation(
                AttemptFailureScope(attempt=prepared.attempt),
                evaluation,
                project_plan_digest=prepared.project_plan.semantic_digest,
                environment_plan_digest=prepared.environment_plan.semantic_digest,
            )
            assert failure is not None
            if failure.disposition == "REJECTED":
                assert isinstance(evaluation, VerifierRejectedEvaluation)
                return BaselineRejection(
                    attempt=prepared.attempt,
                    failure=failure,
                    static_baseline=capture.baseline,
                    evaluation=evaluation,
                    runtime=run,
                )
            assert isinstance(evaluation, IndeterminateEvaluation)
            return BaselineIndeterminate(
                attempt=prepared.attempt,
                failure=failure,
                static_baseline=capture.baseline,
                evaluation=evaluation,
                runtime=run,
            )
        finally:
            prepared.close()

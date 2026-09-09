from __future__ import annotations

from pf.static import StaticEvaluator, TyCheckCache

from pf.environment import EnvironmentFactory, HighestResolution
from pf.evaluation import RuntimeEvaluator
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
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._failures = FailurePolicy()

    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
        run_cache: TyCheckCache,
    ) -> HighestVersionOutcome:
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=HighestResolution(),
            source_plan=source_plan,
        )
        if isinstance(prepared, PrepareFailure):
            failure = self._failures.record_prepare(prepared)
            if failure.disposition == "REJECTED":
                return BaselineRejection(
                    attempt=prepared.attempt,
                    failure=failure,
                    failure_process=prepared.process,
                )
            return BaselineIndeterminate(
                attempt=prepared.attempt,
                failure=failure,
                failure_process=prepared.process,
            )
        try:
            self._static.capture_highest(prepared, package=package, run_cache=run_cache)
            run = self._full.evaluate(prepared, package=package)
            self._static.record_runtime(prepared, run, run_cache=run_cache)
            evaluation = run.evaluation
            if isinstance(evaluation, PassEvaluation):
                return HighestVersionPass(
                    attempt=prepared.attempt,
                    harness_baseline=prepared.harness_baseline,
                    evaluation=evaluation,

                )
            failure = self._failures.record_evaluation(
                AttemptFailureScope(attempt=prepared.attempt),
                evaluation,
                project_plan_digest=prepared.project_plan.semantic_digest,
                environment_plan_digest=prepared.environment_identity.environment_plan_digest,
            )
            assert failure is not None
            if failure.disposition == "REJECTED":
                assert isinstance(evaluation, VerifierRejectedEvaluation)
                return BaselineRejection(
                    attempt=prepared.attempt,
                    failure=failure,

                    evaluation=evaluation,

                    runtime=run,
                )
            assert isinstance(evaluation, IndeterminateEvaluation)
            return BaselineIndeterminate(
                attempt=prepared.attempt,
                failure=failure,

                evaluation=evaluation,

                runtime=run,
            )
        finally:
            prepared.close()

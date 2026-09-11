from __future__ import annotations

from pf.static import StaticEvaluator, TyCheckCache

from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
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
    RuntimeEvaluationRun,
    SmokeCellOutcome,
    SmokeCellPass,
    VerifierRejectedEvaluation,
)
from pf.schemas.project import Cell, PackagePlan, SourcePlan
from pf.snapshot import SourceSnapshot


def _prepare_highest(
    environments: EnvironmentFactory,
    *,
    package: PackagePlan,
    cell: Cell,
    snapshot: SourceSnapshot,
    source_plan: SourcePlan,
) -> PreparedEnvironment | PrepareFailure:
    return environments.prepare(
        package=package,
        cell=cell,
        snapshot=snapshot,
        resolution=HighestResolution(),
        source_plan=source_plan,
    )


def _outcome_from_prepare_failure(
    failures: FailurePolicy,
    prepared: PrepareFailure,
) -> BaselineRejection | BaselineIndeterminate:
    failure = failures.record_prepare(prepared)
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


def _outcome_from_failed_evaluation(
    failures: FailurePolicy,
    *,
    prepared: PreparedEnvironment,
    run: RuntimeEvaluationRun,
) -> BaselineRejection | BaselineIndeterminate:
    evaluation = run.evaluation
    failure = failures.record_evaluation(
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


class SmokeVersionVerifier:
    """Fully verify one highest-resolution environment without static work."""

    def __init__(
        self,
        *,
        environments: EnvironmentFactory,
        full: RuntimeEvaluator,
    ) -> None:
        self._environments = environments
        self._full = full
        self._failures = FailurePolicy()

    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> SmokeCellOutcome:
        prepared = _prepare_highest(
            self._environments,
            package=package,
            cell=cell,
            snapshot=snapshot,
            source_plan=source_plan,
        )
        if isinstance(prepared, PrepareFailure):
            return _outcome_from_prepare_failure(self._failures, prepared)
        try:
            run = self._full.evaluate(prepared, package=package)
            evaluation = run.evaluation
            if isinstance(evaluation, PassEvaluation):
                return SmokeCellPass(
                    attempt=prepared.attempt,
                    evaluation=evaluation,
                )
            return _outcome_from_failed_evaluation(
                self._failures,
                prepared=prepared,
                run=run,
            )
        finally:
            prepared.close()


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
        prepared = _prepare_highest(
            self._environments,
            package=package,
            cell=cell,
            snapshot=snapshot,
            source_plan=source_plan,
        )
        if isinstance(prepared, PrepareFailure):
            return _outcome_from_prepare_failure(self._failures, prepared)
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
            return _outcome_from_failed_evaluation(
                self._failures,
                prepared=prepared,
                run=run,
            )
        finally:
            prepared.close()

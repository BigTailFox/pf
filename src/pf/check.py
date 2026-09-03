from __future__ import annotations

from typing import Literal

from pf.environment import EnvironmentFactory, HighestResolution, LowestDirectResolution
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    CellContextEvent,
    CheckCellOutcome,
    DeclarationDetailIdentity,
    Evaluation,
    IndeterminateEvaluation,
    PassEvaluation,
    PrepareFailure,
    ProcessTerminalUnavailable,
    RuntimeEvaluationRun,
    StaticBaseline,
)
from pf.schemas.project import Cell, PackagePlan, SourcePlan
from pf.snapshot import SourceSnapshot
from pf.verification import ActivityConsumer


class CompatibilityChecker:
    """Validate current declarations for one cell without searching."""

    def __init__(
        self,
        *,
        environments: EnvironmentFactory,
        static: StaticEvaluator,
        full: RuntimeEvaluator,
        failures: FailurePolicy | None = None,
        events: ActivityConsumer | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._failures = failures or FailurePolicy()
        self._events = events

    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CheckCellOutcome:
        highest = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=HighestResolution(),
            source_plan=source_plan,
        )
        if isinstance(highest, PrepareFailure):
            return self._prepare_outcome(highest, role="declaration-capture")
        try:
            capture = self._static.capture(highest, package=package)
        finally:
            highest.close()
        if isinstance(capture, IndeterminateEvaluation):
            return self._evaluation_outcome(
                attempt=highest.attempt,
                role="declaration-capture",
                evaluation=capture,
                static_baseline=None,
                project_plan_digest=highest.project_plan.semantic_digest,
                environment_plan_digest=highest.environment_plan.semantic_digest,
            )
        if self._events is not None:
            self._events.consume(
                CellContextEvent(cell=cell, detail=DeclarationDetailIdentity())
            )
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=LowestDirectResolution(highest.harness_baseline),
            source_plan=source_plan,
        )
        if isinstance(prepared, PrepareFailure):
            return self._prepare_outcome(prepared, role="declaration")
        try:
            runtime = self._full.evaluate(
                prepared,
                package=package,
                baseline=capture.baseline,
            )
        finally:
            prepared.close()
        return self._evaluation_outcome(
            attempt=prepared.attempt,
            role="declaration",
            evaluation=runtime.evaluation,
            runtime=runtime,
            static_baseline=capture.baseline,
            project_plan_digest=prepared.project_plan.semantic_digest,
            environment_plan_digest=prepared.environment_plan.semantic_digest,
        )

    def _prepare_outcome(
        self,
        prepared: PrepareFailure,
        *,
        role: Literal["declaration-capture", "declaration"],
    ) -> CheckCellOutcome:
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
        return CheckCellOutcome(
            status=failure.disposition,
            role=role,
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

    def _evaluation_outcome(
        self,
        *,
        attempt: Attempt,
        role: Literal["declaration-capture", "declaration"],
        evaluation: Evaluation,
        runtime: RuntimeEvaluationRun | None = None,
        static_baseline: StaticBaseline | None,
        project_plan_digest: str,
        environment_plan_digest: str,
    ) -> CheckCellOutcome:
        if isinstance(evaluation, PassEvaluation):
            return CheckCellOutcome(
                status="PASS",
                role=role,
                attempt=attempt,
                evaluation=evaluation,
                static_baseline=static_baseline,
                runtime=runtime,
            )
        failure = self._failures.record_evaluation(
            AttemptFailureScope(attempt=attempt),
            evaluation,
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
        )
        assert failure is not None
        return CheckCellOutcome(
            status=failure.disposition,
            role=role,
            attempt=attempt,
            failure=failure,
            evaluation=evaluation,
            static_baseline=static_baseline,
            runtime=runtime,
        )

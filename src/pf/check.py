from __future__ import annotations

from pf.static import CollectedStaticSubject, StaticEvaluator, TyCheckCache

from typing import Literal

from pf.environment import EnvironmentFactory, HighestResolution, LowestDirectResolution
from pf.evaluation import RuntimeEvaluator
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    CellContextEvent,
    CheckCellOutcome,
    DeclarationDetailIdentity,
    Evaluation,
    PassEvaluation,
    PrepareFailure,
    RuntimeEvaluationRun,
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
        events: ActivityConsumer | None = None,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full
        self._failures = FailurePolicy()
        self._events = events

    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
        run_cache: TyCheckCache,
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
            self._static.capture_highest(highest, package=package, run_cache=run_cache)
        finally:
            highest.close()
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
            return self._prepare_outcome(
                prepared, role="declaration"
            )
        try:
            collected = self._static.collect_prepared(prepared, package=package, run_cache=run_cache)
            if isinstance(collected, CollectedStaticSubject):
                self._static.compare_global(collected, run_cache=run_cache)
            runtime = self._full.evaluate(prepared, package=package)
            self._static.record_runtime(prepared, runtime, run_cache=run_cache)
        finally:
            prepared.close()
        return self._evaluation_outcome(
            attempt=prepared.attempt,
            role="declaration",
            evaluation=runtime.evaluation,
            runtime=runtime,
            project_plan_digest=prepared.project_plan.semantic_digest,
            environment_plan_digest=prepared.environment_identity.environment_plan_digest,
        )

    def _prepare_outcome(
        self,
        prepared: PrepareFailure,
        *,
        role: Literal["declaration-capture", "declaration"],
    ) -> CheckCellOutcome:
        failure = self._failures.record_prepare(prepared)
        return CheckCellOutcome(
            status=failure.disposition,
            role=role,
            attempt=prepared.attempt,
            failure=failure,
            failure_process=prepared.process,
        )

    def _evaluation_outcome(
        self,
        *,
        attempt: Attempt,
        role: Literal["declaration-capture", "declaration"],
        evaluation: Evaluation,
        runtime: RuntimeEvaluationRun | None = None,
        project_plan_digest: str,
        environment_plan_digest: str | None,
    ) -> CheckCellOutcome:
        if isinstance(evaluation, PassEvaluation):
            return CheckCellOutcome(
                status="PASS",
                role=role,
                attempt=attempt,
                evaluation=evaluation,
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
            runtime=runtime,
        )

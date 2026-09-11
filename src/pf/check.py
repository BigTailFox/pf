from __future__ import annotations

from typing import Literal

from pf.environment import EnvironmentFactory, HighestResolution, LowestDirectResolution
from pf.evaluation import RuntimeEvaluator
from pf.failure import FailurePolicy
from pf.harness import HarnessBaselineRequirement, degenerate_harness_baseline
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
from pf.schemas.project import Cell, HarnessBaseline, PackagePlan, SourcePlan
from pf.snapshot import SourceSnapshot
from pf.verification import ActivityConsumer


class CompatibilityChecker:
    """Validate current declarations for one cell without searching."""

    def __init__(
        self,
        *,
        environments: EnvironmentFactory,
        full: RuntimeEvaluator,
        events: ActivityConsumer | None = None,
    ) -> None:
        self._environments = environments
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
        baseline_requirement: HarnessBaselineRequirement,
    ) -> CheckCellOutcome:
        if baseline_requirement == "DEGENERATE":
            return self._declare(
                package=package,
                cell=cell,
                snapshot=snapshot,
                source_plan=source_plan,
                baseline=degenerate_harness_baseline(
                    package.harness_requirements,
                    cell,
                ),
            )
        highest = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=HighestResolution(),
            source_plan=source_plan,
        )
        if isinstance(highest, PrepareFailure):
            return self._prepare_outcome(highest, role="harness-prepare")
        try:
            baseline = highest.harness_baseline
        finally:
            highest.close()
        if self._events is not None:
            self._events.consume(
                CellContextEvent(cell=cell, detail=DeclarationDetailIdentity())
            )
        return self._declare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            source_plan=source_plan,
            baseline=baseline,
        )

    def _declare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
        baseline: HarnessBaseline,
    ) -> CheckCellOutcome:
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=LowestDirectResolution(baseline),
            source_plan=source_plan,
        )
        if isinstance(prepared, PrepareFailure):
            return self._prepare_outcome(prepared, role="declaration")
        try:
            runtime = self._full.evaluate(prepared, package=package)
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
        role: Literal["harness-prepare", "declaration"],
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
        role: Literal["harness-prepare", "declaration"],
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

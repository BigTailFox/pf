from __future__ import annotations

from typing import Protocol

from pf.environment import HighestResolution, PreparedEnvironment, ResolutionRequest
from pf.evaluation import require_full_evaluation_contract
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    AttemptFailureScope,
    BaselineIndeterminate,
    BaselineRejection,
    Evaluation,
    FailureDetail,
    HighestVersionOutcome,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    PrepareFailure,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    TestFailEvaluation,
    ToolFailure,
)
from pf.schemas.project import Cell, PackagePlan
from pf.snapshot import SourceSnapshot


class HighestEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: ResolutionRequest,
    ) -> PreparedEnvironment | PrepareFailure: ...


class HighestStaticOperations(Protocol):
    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture | IndeterminateEvaluation: ...


class HighestFullOperations(Protocol):
    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> Evaluation: ...


class HighestVersionVerifier:
    """Fully verify one highest-resolution environment and close it."""

    def __init__(
        self,
        *,
        environments: HighestEnvironmentOperations,
        static: HighestStaticOperations,
        full: HighestFullOperations,
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
    ) -> HighestVersionOutcome:
        require_full_evaluation_contract(package, "highest-version verification")
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution=HighestResolution(),
        )
        if isinstance(prepared, ToolFailure):
            raise ValueError("highest-version prepare must establish an Attempt")
        if isinstance(prepared, PrepareFailure):
            failure = self._failures.classify(
                scope=AttemptFailureScope(attempt=prepared.attempt),
                cause=prepared.failure.cause,
                stage=prepared.failure.stage,
                process=prepared.failure.process,
                summary_code=prepared.failure.summary_code,
            )
            if failure.disposition == "REJECTED":
                return BaselineRejection(
                    attempt=prepared.attempt,
                    failure=failure,
                )
            return BaselineIndeterminate(
                attempt=prepared.attempt,
                failure=failure,
            )
        try:
            capture = self._static.capture(prepared, package=package)
            if isinstance(capture, IndeterminateEvaluation):
                return BaselineIndeterminate(
                    attempt=prepared.attempt,
                    failure=self._failures.classify(
                        scope=AttemptFailureScope(attempt=prepared.attempt),
                        cause=capture.cause,
                        stage=capture.failure.stage,
                        process=capture.failure.process,
                        summary_code=capture.failure.summary_code,
                    ),
                    evaluation=capture,
                )
            evaluation = self._full.evaluate(
                prepared,
                package=package,
                baseline=capture.baseline,
                static_result=capture.static,
            )
            if isinstance(evaluation, PassEvaluation):
                return HighestVersionPass(
                    attempt=prepared.attempt,
                    baseline=capture.baseline,
                    evaluation=evaluation,
                )
            if isinstance(evaluation, RuntimeInterfaceMissingEvaluation):
                confirmed = next(
                    attempt.outcome
                    for attempt in evaluation.witnesses
                    if isinstance(attempt.outcome, RuntimeWitnessResult)
                    and attempt.outcome.status == "CONFIRMED_MISSING"
                )
                failure = self._failures.classify(
                    scope=AttemptFailureScope(attempt=prepared.attempt),
                    cause="INTERNAL_INVARIANT",
                    stage="baseline-witness",
                    process=confirmed.process,
                    detail=FailureDetail(
                        code="unexpected-baseline-runtime-witness",
                        message=(
                            "highest full evaluation cannot witness a static baseline "
                            "increment"
                        ),
                    ),
                )
                return BaselineIndeterminate(
                    attempt=prepared.attempt,
                    failure=failure,
                    static_baseline=capture.baseline,
                )
            if isinstance(evaluation, TestFailEvaluation):
                cause = "TEST_FAILURE"
                process = evaluation.test.process
            else:
                cause = evaluation.cause
                process = evaluation.failure.process
            failure = self._failures.classify(
                scope=AttemptFailureScope(attempt=prepared.attempt),
                cause=cause,
                stage=(
                    "test"
                    if isinstance(evaluation, TestFailEvaluation)
                    else evaluation.failure.stage
                ),
                process=process,
                summary_code=(
                    evaluation.failure.summary_code
                    if isinstance(evaluation, IndeterminateEvaluation)
                    else None
                ),
            )
            if failure.disposition == "REJECTED":
                assert isinstance(evaluation, TestFailEvaluation)
                return BaselineRejection(
                    attempt=prepared.attempt,
                    failure=failure,
                    static_baseline=capture.baseline,
                    evaluation=evaluation,
                )
            assert isinstance(evaluation, IndeterminateEvaluation)
            return BaselineIndeterminate(
                attempt=prepared.attempt,
                failure=failure,
                static_baseline=capture.baseline,
                evaluation=evaluation,
            )
        finally:
            prepared.close()

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import os
from pathlib import Path
from typing import Protocol

from pf.environment import PreparedEnvironment, StageConsumer, emit_cell_stage
from pf.errors import ConfigurationError
from pf.schemas.evaluation import (
    CacheConflict,
    EnvironmentVariable,
    Evaluation,
    IndeterminateEvaluation,
    PassEvaluation,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessAttempt,
    RuntimeWitnessOutcome,
    RuntimeWitnessPlan,
    RuntimeEvaluationRun,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticRegressionEvaluation,
    StaticUnchangedEvaluation,
    StageProgress,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
    VerifierRejected,
    VerifierRejectedEvaluation,
    VerifierRequest,
    VerifierRun,
    VerifierPass,
    VerifierIndeterminate,
)
from pf.schemas.project import PackagePlan
from pf.static_transition import StaticTransitionClassifier, static_fingerprint


def require_full_evaluation_contract(package: PackagePlan, command: str) -> None:
    if not package.config.test_command:
        raise ConfigurationError(f"test-command is required for {command}")
    if not package.test_group_present:
        raise ConfigurationError(
            f"test dependency group is required: {package.config.test_group}"
        )


class EvaluationCache:
    """Keep context-bound static and full evidence for one search."""

    def __init__(self) -> None:
        self._static: dict[tuple[str, str], StaticEvaluation] = {}
        self._full: dict[tuple[str, str], Evaluation] = {}

    def get_static(
        self,
        proposal_id: str,
        *,
        baseline_digest: str,
    ) -> StaticEvaluation | None:
        return self._static.get(self._key(proposal_id, baseline_digest))

    def get_full(
        self,
        proposal_id: str,
        *,
        baseline_digest: str,
    ) -> Evaluation | None:
        return self._full.get(self._key(proposal_id, baseline_digest))

    def record_static(
        self,
        evaluation: StaticEvaluation,
        *,
        baseline_digest: str,
    ) -> StaticEvaluation | CacheConflict:
        proposal_id = evaluation.proposal.proposal_id
        self._require_matching_baseline(evaluation, baseline_digest)
        key = self._key(proposal_id, baseline_digest)
        existing = self._static.get(key)
        if existing is not None and existing.status != evaluation.status:
            return CacheConflict(
                proposal_id=proposal_id,
                observed_statuses=(existing.status, evaluation.status),
            )
        if existing is None:
            self._static[key] = evaluation
            return evaluation
        return existing

    @staticmethod
    def _full_authority(evaluation: Evaluation) -> tuple[str, object | None]:
        if isinstance(evaluation, (PassEvaluation, VerifierRejectedEvaluation)):
            return evaluation.status, evaluation.verifier
        if (
            isinstance(evaluation, IndeterminateEvaluation)
            and evaluation.verifier is not None
        ):
            return evaluation.status, evaluation.verifier
        return evaluation.status, None

    def record_full(
        self,
        evaluation: Evaluation,
        *,
        baseline_digest: str,
    ) -> Evaluation | CacheConflict:
        proposal_id = evaluation.proposal.proposal_id
        self._require_matching_baseline(evaluation, baseline_digest)
        key = self._key(proposal_id, baseline_digest)
        existing = self._full.get(key)
        if existing is not None and self._full_authority(existing) != self._full_authority(
            evaluation
        ):
            return CacheConflict(
                proposal_id=proposal_id,
                observed_statuses=(existing.status, evaluation.status),
            )
        if existing is None:
            self._full[key] = evaluation
            return evaluation
        return existing

    @staticmethod
    def _key(proposal_id: str, baseline_digest: str) -> tuple[str, str]:
        if not baseline_digest:
            raise ValueError("evaluation cache baseline digest cannot be empty")
        return proposal_id, baseline_digest

    @staticmethod
    def _require_matching_baseline(
        evaluation: StaticEvaluation | Evaluation,
        baseline_digest: str,
    ) -> None:
        embedded: str | None
        if isinstance(
            evaluation,
            (StaticUnchangedEvaluation, StaticRegressionEvaluation),
        ):
            embedded = evaluation.baseline_digest
        elif isinstance(
            evaluation,
            (
                PassEvaluation,
                VerifierRejectedEvaluation,
                RuntimeInterfaceMissingEvaluation,
            ),
        ):
            embedded = evaluation.static.baseline_digest
        else:
            embedded = None
        if embedded is not None and embedded != baseline_digest:
            raise ValueError("evaluation does not match its cache baseline")


class TyOperations(Protocol):
    def check(
        self,
        *,
        interpreter: Path,
        package: Path,
        python_minor: str,
        target: str,
        args: tuple[str, ...],
        timeout_seconds: int | None,
        snapshot_root: Path | None = None,
    ) -> TyCheck | ToolFailure: ...


class VerifierOperations(Protocol):
    def run(
        self,
        request: VerifierRequest,
        progress: Callable[[StageProgress | None], None] | None = None,
    ) -> VerifierRun: ...


class RuntimeWitnessOperations(Protocol):
    def run(
        self,
        *,
        plan: RuntimeWitnessPlan,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> RuntimeWitnessOutcome: ...


class StaticEvaluator:
    """Freeze one cell baseline and compare Proposal diagnostics against it."""

    def __init__(
        self,
        ty: TyOperations,
        *,
        classifier: StaticTransitionClassifier | None = None,
        events: StageConsumer | None = None,
    ) -> None:
        self._ty = ty
        self._classifier = classifier or StaticTransitionClassifier()
        self._events = events

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
    ) -> StaticEvaluation:
        if (
            baseline.proposal.cell != prepared.proposal.cell
            or baseline.proposal.snapshot_digest != prepared.proposal.snapshot_digest
            or baseline.proposal.policy_identity != prepared.proposal.policy_identity
        ):
            raise ValueError(
                "static baseline must match proposal cell, snapshot, and policy"
            )
        emit_cell_stage(self._events, prepared.proposal.cell, "static check")
        outcome = self._check(prepared, package=package)
        if isinstance(outcome, ToolFailure):
            return IndeterminateEvaluation(
                proposal=prepared.proposal,
                cause=outcome.cause,
                failure=outcome,
            )
        incremental = self._increment(outcome, baseline)
        if not incremental:
            return StaticUnchangedEvaluation(
                proposal=prepared.proposal,
                ty=outcome,
                baseline_digest=baseline.digest,
                incremental=(),
                static_fingerprint=static_fingerprint(()),
            )
        return StaticRegressionEvaluation(
            proposal=prepared.proposal,
            ty=outcome,
            baseline_digest=baseline.digest,
            incremental=incremental,
            static_fingerprint=static_fingerprint(
                tuple(item.identity for item in incremental)
            ),
            classifications=self._classifier.classify(
                prepared,
                package=package,
                incremental=incremental,
            ),
        )

    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture | IndeterminateEvaluation:
        emit_cell_stage(
            self._events, prepared.proposal.cell, "capturing static baseline"
        )
        outcome = self._check(prepared, package=package)
        if isinstance(outcome, ToolFailure):
            return IndeterminateEvaluation(
                proposal=prepared.proposal,
                cause=outcome.cause,
                failure=outcome,
            )
        digest = ty_diagnostic_digest(outcome.diagnostics)
        baseline = StaticBaseline(
            proposal=prepared.proposal,
            ty=outcome,
            digest=digest,
        )
        static = StaticUnchangedEvaluation(
            proposal=prepared.proposal,
            ty=outcome,
            baseline_digest=digest,
            incremental=(),
            static_fingerprint=static_fingerprint(()),
        )
        return StaticBaselineCapture(baseline=baseline, static=static)

    def _check(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> TyCheck | ToolFailure:
        return self._ty.check(
            interpreter=prepared.interpreter,
            package=prepared.package_root,
            python_minor=prepared.proposal.cell.python_minor,
            target=prepared.proposal.cell.target,
            args=package.config.ty_args,
            timeout_seconds=package.config.ty_timeout,
            snapshot_root=prepared.proposal_root,
        )

    @staticmethod
    def _increment(
        check: TyCheck,
        baseline: StaticBaseline,
    ) -> tuple[TyDiagnostic, ...]:
        remaining = Counter(item.identity for item in baseline.diagnostics)
        incremental = []
        for diagnostic in check.diagnostics:
            if remaining[diagnostic.identity] > 0:
                remaining[diagnostic.identity] -= 1
            else:
                incremental.append(diagnostic)
        return tuple(incremental)


class RuntimeEvaluator:
    """Route static transitions through witness evidence and the full test command."""

    def __init__(
        self,
        *,
        static: StaticEvaluator,
        verifier: VerifierOperations,
        witnesses: RuntimeWitnessOperations | None = None,
        events: StageConsumer | None = None,
    ) -> None:
        self._static = static
        self._verifier = verifier
        self._witnesses = witnesses
        self._events = events

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> RuntimeEvaluationRun:
        static = static_result or self._static.evaluate(
            prepared,
            package=package,
            baseline=baseline,
        )
        if isinstance(static, IndeterminateEvaluation):
            return RuntimeEvaluationRun(evaluation=static)
        witness_attempts: list[RuntimeWitnessAttempt] = []
        if isinstance(static, StaticRegressionEvaluation) and self._witnesses:
            plans: list[RuntimeWitnessPlan] = []
            for classification in static.classifications:
                plan = classification.witness_plan
                if plan is not None and plan not in plans:
                    plans.append(plan)
            for plan in plans:
                emit_cell_stage(self._events, prepared.proposal.cell, "runtime witness")
                witness = self._witnesses.run(
                    plan=plan,
                    interpreter=prepared.interpreter,
                    cwd=prepared.package_root,
                    timeout_seconds=package.config.test_timeout,
                )
                attempt = RuntimeWitnessAttempt(plan=plan, outcome=witness)
                witness_attempts.append(attempt)
                if isinstance(witness, ToolFailure):
                    return RuntimeEvaluationRun(
                        evaluation=IndeterminateEvaluation(
                            proposal=prepared.proposal,
                            cause=witness.cause,
                            failure=witness,
                            static=static,
                            witnesses=tuple(witness_attempts),
                        )
                    )
                if witness.status == "CONFIRMED_MISSING":
                    return RuntimeEvaluationRun(
                        evaluation=RuntimeInterfaceMissingEvaluation(
                            proposal=prepared.proposal,
                            static=static,
                            witnesses=tuple(witness_attempts),
                        )
                    )
        emit_cell_stage(self._events, prepared.proposal.cell, "dynamic tests")
        cwd = (
            prepared.proposal_root
            if package.config.command_cwd == "root"
            else prepared.package_root
        )
        environment_bin = prepared.interpreter.parent.as_posix()
        path = os.pathsep.join(
            part for part in (environment_bin, os.environ.get("PATH", "")) if part
        )
        progress = (
            None
            if self._events is None
            else lambda progress: emit_cell_stage(
                self._events,
                prepared.proposal.cell,
                "dynamic tests",
                progress=progress,
            )
        )
        run = self._verifier.run(
            VerifierRequest(
                command=package.config.test_command,
                cwd=cwd,
                environment=(EnvironmentVariable(name="PATH", value=path),),
                timeout_seconds=package.config.test_timeout,
            ),
            progress=progress,
        )
        prepared.mark_tested()
        authoritative = run.authoritative
        if isinstance(authoritative, VerifierPass):
            evaluation: Evaluation = PassEvaluation(
                proposal=prepared.proposal,
                static=static,
                witnesses=tuple(witness_attempts),
                verifier=authoritative,
            )
        elif isinstance(authoritative, VerifierRejected):
            evaluation = VerifierRejectedEvaluation(
                proposal=prepared.proposal,
                static=static,
                witnesses=tuple(witness_attempts),
                verifier=authoritative,
            )
        else:
            assert isinstance(authoritative, VerifierIndeterminate)
            evaluation = IndeterminateEvaluation(
                proposal=prepared.proposal,
                cause=(
                    "TIMEOUT"
                    if authoritative.reason == "process-timed-out"
                    else "TOOL_FAILURE"
                ),
                verifier=authoritative,
                static=static,
                witnesses=tuple(witness_attempts),
            )
        return RuntimeEvaluationRun(
            evaluation=evaluation,
            diagnostics=run.diagnostics,
        )

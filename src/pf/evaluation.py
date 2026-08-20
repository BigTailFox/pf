from __future__ import annotations

from collections import Counter
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
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestOutcome,
    TestPass,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.schemas.project import PackagePlan


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
        if existing is not None and existing.status != evaluation.status:
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
        if isinstance(evaluation, (StaticPassEvaluation, StaticFailEvaluation)):
            embedded = evaluation.baseline_digest
        elif isinstance(evaluation, (PassEvaluation, TestFailEvaluation)):
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


class TestOperations(Protocol):
    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        failure_exit_codes: tuple[int, ...],
        timeout_seconds: int | None,
    ) -> TestOutcome: ...


class StaticEvaluator:
    """Freeze one cell baseline and compare Proposal diagnostics against it."""

    def __init__(
        self, ty: TyOperations, *, events: StageConsumer | None = None
    ) -> None:
        self._ty = ty
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
                status=outcome.status,
                proposal=prepared.proposal,
                failure=outcome,
            )
        incremental = self._increment(outcome, baseline)
        if not incremental:
            return StaticPassEvaluation(
                proposal=prepared.proposal,
                ty=outcome,
                baseline_digest=baseline.digest,
                incremental=(),
            )
        return StaticFailEvaluation(
            proposal=prepared.proposal,
            ty=outcome,
            baseline_digest=baseline.digest,
            incremental=incremental,
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
                status=outcome.status,
                proposal=prepared.proposal,
                failure=outcome,
            )
        digest = ty_diagnostic_digest(outcome.diagnostics)
        baseline = StaticBaseline(
            proposal=prepared.proposal,
            ty=outcome,
            digest=digest,
        )
        static = StaticPassEvaluation(
            proposal=prepared.proposal,
            ty=outcome,
            baseline_digest=digest,
            incremental=(),
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


class FullEvaluator:
    """Promote a static-clean Proposal through the complete test command once."""

    def __init__(
        self,
        *,
        static: StaticEvaluator,
        tests: TestOperations,
        events: StageConsumer | None = None,
    ) -> None:
        self._static = static
        self._tests = tests
        self._events = events

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> Evaluation:
        static = static_result or self._static.evaluate(
            prepared,
            package=package,
            baseline=baseline,
        )
        if not isinstance(static, StaticPassEvaluation):
            return static
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
        outcome = self._tests.run(
            command=package.config.test_command,
            cwd=cwd,
            environment=(EnvironmentVariable(name="PATH", value=path),),
            failure_exit_codes=package.config.test_failure_exit_codes,
            timeout_seconds=package.config.test_timeout,
        )
        prepared.mark_tested()
        if isinstance(outcome, TestPass):
            return PassEvaluation(
                proposal=prepared.proposal,
                static=static,
                test=outcome,
            )
        if isinstance(outcome, TestFail):
            return TestFailEvaluation(
                proposal=prepared.proposal,
                static=static,
                test=outcome,
            )
        assert isinstance(outcome, ToolFailure)
        return IndeterminateEvaluation(
            status=outcome.status,
            proposal=prepared.proposal,
            failure=outcome,
        )

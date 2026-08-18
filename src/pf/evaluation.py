from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from pf.environment import PreparedEnvironment
from pf.schemas.evaluation import (
    CacheConflict,
    EnvironmentVariable,
    Evaluation,
    IndeterminateEvaluation,
    PassEvaluation,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestOutcome,
    TestPass,
    ToolFailure,
    TyFail,
    TyOutcome,
    TyPass,
)
from pf.schemas.project import PackagePlan


class EvaluationCache:
    """Keep static and full evidence separate for the duration of one search."""

    def __init__(self) -> None:
        self._static: dict[str, StaticEvaluation] = {}
        self._full: dict[str, Evaluation] = {}

    def get_static(self, proposal_id: str) -> StaticEvaluation | None:
        return self._static.get(proposal_id)

    def get_full(self, proposal_id: str) -> Evaluation | None:
        return self._full.get(proposal_id)

    def record_static(
        self,
        evaluation: StaticEvaluation,
    ) -> StaticEvaluation | CacheConflict:
        proposal_id = evaluation.proposal.proposal_id
        existing = self._static.get(proposal_id)
        if existing is not None and existing.status != evaluation.status:
            return CacheConflict(
                proposal_id=proposal_id,
                observed_statuses=(existing.status, evaluation.status),
            )
        if existing is None:
            self._static[proposal_id] = evaluation
            return evaluation
        return existing

    def record_full(self, evaluation: Evaluation) -> Evaluation | CacheConflict:
        proposal_id = evaluation.proposal.proposal_id
        existing = self._full.get(proposal_id)
        if existing is not None and existing.status != evaluation.status:
            return CacheConflict(
                proposal_id=proposal_id,
                observed_statuses=(existing.status, evaluation.status),
            )
        if existing is None:
            self._full[proposal_id] = evaluation
            return evaluation
        return existing


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
    ) -> TyOutcome: ...


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
    """Evaluate resolve/install-complete Proposal source with ty."""

    def __init__(self, ty: TyOperations) -> None:
        self._ty = ty

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticEvaluation:
        outcome = self._ty.check(
            interpreter=prepared.interpreter,
            package=prepared.package_root,
            python_minor=prepared.proposal.cell.python_minor,
            target=prepared.proposal.cell.target,
            args=package.config.ty_args,
            timeout_seconds=package.config.ty_timeout,
        )
        if isinstance(outcome, TyPass):
            return StaticPassEvaluation(
                proposal=prepared.proposal,
                ty=outcome,
            )
        if isinstance(outcome, TyFail):
            return StaticFailEvaluation(
                proposal=prepared.proposal,
                ty=outcome,
            )
        assert isinstance(outcome, ToolFailure)
        return IndeterminateEvaluation(
            status=outcome.status,
            proposal=prepared.proposal,
            failure=outcome,
        )


class FullEvaluator:
    """Promote a static-clean Proposal through the complete test command once."""

    def __init__(self, *, static: StaticEvaluator, tests: TestOperations) -> None:
        self._static = static
        self._tests = tests

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        static_result: StaticEvaluation | None = None,
    ) -> Evaluation:
        static = static_result or self._static.evaluate(prepared, package=package)
        if not isinstance(static, StaticPassEvaluation):
            return static
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

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os
from threading import BoundedSemaphore
from typing import Protocol

from pf.cancellation import Cancellation
from pf.policy import execution_policy_identity
from pf.environment import PreparedEnvironment, StageConsumer, emit_cell_stage
from pf.schemas.evaluation import (
    CacheConflict,
    EnvironmentVariable,
    Evaluation,
    IndeterminateEvaluation,
    PassEvaluation,
    RuntimeEvaluationRun,
    StageProgress,
    VerifierRejected,
    VerifierRejectedEvaluation,
    VerifierRequest,
    VerifierRun,
    VerifierPass,
    VerifierIndeterminate,
)
from pf.schemas.project import PackagePlan, Proposal


class StagePermitPools:
    """Share invocation-wide permits at the ty and configured-test seams."""

    def __init__(
        self,
        *,
        ty_jobs: int | None = None,
        test_jobs: int | None = None,
    ) -> None:
        self._ty: BoundedSemaphore | None = None
        self._test: BoundedSemaphore | None = None
        if ty_jobs is not None or test_jobs is not None:
            if ty_jobs is None or test_jobs is None:
                raise ValueError("both stage pool limits are required")
            self.configure(ty_jobs=ty_jobs, test_jobs=test_jobs)

    def configure(self, *, ty_jobs: int, test_jobs: int) -> None:
        if ty_jobs <= 0 or test_jobs <= 0:
            raise ValueError("stage pool limits must be positive")
        self._ty = BoundedSemaphore(ty_jobs)
        self._test = BoundedSemaphore(test_jobs)

    @contextmanager
    def ty(self, *, cancellation: Cancellation | None = None) -> Iterator[None]:
        with self._permit(self._ty, cancellation=cancellation):
            yield

    @contextmanager
    def test(self) -> Iterator[None]:
        with self._permit(self._test):
            yield

    @staticmethod
    @contextmanager
    def _permit(semaphore: BoundedSemaphore | None, *, cancellation: Cancellation | None = None) -> Iterator[None]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if semaphore is None:
            yield
            return
        if cancellation is None:
            semaphore.acquire()
        else:
            while not semaphore.acquire(timeout=0.05):
                cancellation.raise_if_cancelled()
        try:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            yield
        finally:
            semaphore.release()


class EvaluationCache:
    """Direct verifier evidence for an exact Proposal and execution policy."""

    def __init__(self) -> None:
        self._full: dict[tuple[str, str], Evaluation] = {}

    def get_full(self, proposal: Proposal) -> Evaluation | None:
        existing = self._full.get(self._key(proposal))
        if existing is not None and existing.proposal != proposal:
            raise ValueError("dynamic cache identity has inconsistent Proposal facts")
        return existing

    def record_full(self, evaluation: Evaluation) -> Evaluation | CacheConflict:
        proposal = evaluation.proposal
        existing = self.get_full(proposal)
        if existing is not None and (existing.status, existing.verifier) != (
            evaluation.status, evaluation.verifier
        ):
            return CacheConflict(proposal_id=proposal.proposal_id,
                                 observed_statuses=(existing.status, evaluation.status))
        if existing is None:
            self._full[self._key(proposal)] = evaluation
            return evaluation
        return existing

    @staticmethod
    def _key(proposal: Proposal) -> tuple[str, str]:
        return proposal.proposal_id, proposal.policy_identity


class VerifierOperations(Protocol):
    def run(
        self,
        request: VerifierRequest,
        progress: Callable[[StageProgress | None], None] | None = None,
    ) -> VerifierRun: ...


class RuntimeEvaluator:
    """Run the configured verifier independently of static regression kinds."""

    def __init__(
        self,
        *,
        verifier: VerifierOperations,
        events: StageConsumer | None = None,
        permits: StagePermitPools | None = None,
    ) -> None:
        self._verifier = verifier
        self._events = events
        self._permits = permits or StagePermitPools()

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        failed_case_nodeids: tuple[str, ...] = (),
    ) -> RuntimeEvaluationRun:
        if execution_policy_identity(package.config) != prepared.proposal.policy_identity:
            raise ValueError("verifier configuration must match the prepared ExecutionPolicy")
        emit_cell_stage(self._events, prepared.proposal.cell, "dynamic tests")
        cwd = (
            prepared.proposal_root
            if package.config.test.cwd == "root"
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
        command = package.config.test.command
        with self._permits.test(), prepared.verifier_use():
            run = self._verifier.run(
                VerifierRequest(
                    command=command,
                    cwd=cwd,
                    environment=(EnvironmentVariable(name="PATH", value=path),),
                    timeout_seconds=package.config.test.timeout_seconds,
                    failed_case_nodeids=failed_case_nodeids,
                ),
                progress=progress,
            )
        authoritative = run.authoritative
        if isinstance(authoritative, VerifierPass):
            evaluation: Evaluation = PassEvaluation(
                proposal=prepared.proposal,

                verifier=authoritative,
            )
        elif isinstance(authoritative, VerifierRejected):
            evaluation = VerifierRejectedEvaluation(
                proposal=prepared.proposal,

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

            )
        return RuntimeEvaluationRun(
            evaluation=evaluation,
            diagnostics=run.diagnostics,
            failed_case_additions=run.failed_case_additions,
        )

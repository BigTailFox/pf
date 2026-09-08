from __future__ import annotations

from scripted_static import collect_highest
from pf.static_cache import RunTyFactRef

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
import time

import pytest

from evaluation_fixtures import (
    ScriptedVerifier,
    evaluation_assembly,
    evaluation_project,
    selected_candidate,
    successful_process,
)

from pf.static_cache import TyCheckCache
from scripted_static import ScriptedStaticRequests
from pf.environment import ExactSelection, HighestResolution, PreparedEnvironment
from pf.evaluation import RuntimeEvaluator, StagePermitPools, StaticEvaluator
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    AttemptFailureScope,
    CellStageEvent,
    NormalExit,
    StageProgress,
    TimedOut,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRequest,
    VerifierRun,
)
from pf.schemas.project import VersionPin


from pf.schemas.policy import GuidancePolicy


def collect_global(static, prepared, package, cache):
    static.collect_prepared(prepared, package=package, run_cache=cache)
    assert prepared.static_consumer is not None
    observation = prepared.static_consumer.fact.observation.observation_policy
    return cache.compare_global(
        prepared.static_consumer,
        guidance=GuidancePolicy(observation=observation, observation_identity=observation.identity),
    )


def diagnostic(
    identity: str,
    *,
    message: str = "wording is not evidence",
) -> TyDiagnostic:
    _, path, line, column, code = identity.split("|")
    return TyDiagnostic(
        identity=identity,
        origin="snapshot",
        path=path,
        line=int(line),
        column=int(column),
        code=code,
        severity="major",
        message=message,
    )


def empty_check() -> TyCheck:
    return TyCheck(process=successful_process(), diagnostics=())


def tool_failure(stage: str = "ty") -> ToolFailure:
    return ToolFailure(
        cause="TOOL_FAILURE",
        stage=stage,
        process=successful_process(exit_code=2),
    )


def candidate_resolution(
    prepared: PreparedEnvironment,
    dependency: str,
    version: str,
) -> ExactSelection:
    return ExactSelection(
        selection=(selected_candidate(dependency, version),),
        harness_baseline=prepared.harness_baseline,
    )


class RecordingStages:
    def __init__(self) -> None:
        self.events: list[CellStageEvent] = []

    def consume(self, event: object) -> None:
        if isinstance(event, CellStageEvent):
            self.events.append(event)




class TestStaticEvaluator:
    def test_static_evaluator_uses_multiset_subtraction_against_a_frozen_baseline(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        repeated = diagnostic("snapshot|demo.py|1|2|invalid-type", message="baseline")
        shifted = diagnostic("snapshot|demo.py|5|6|unresolved-reference")
        checks = (
            TyCheck(
                process=successful_process(exit_code=1),
                diagnostics=(repeated, repeated, shifted),
            ),
            TyCheck(
                process=successful_process(exit_code=1),
                diagnostics=(
                    repeated.model_copy(update={"message": "candidate wording"}),
                    shifted,
                    shifted.model_copy(update={"message": "extra occurrence"}),
                ),
            ),
        )
        project = evaluation_project(tmp_path / "project", dependency="demo-dep")
        assembly = evaluation_assembly(
            ty_handler=lambda vector, call: checks[call - 1],
        )
        highest = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(highest, PreparedEnvironment)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=candidate_resolution(highest, "demo-dep", "2"),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        capture = collect_highest(assembly.static, highest, run_cache=run_cache, package=project.package)
        assert isinstance(capture, RunTyFactRef)
        result = collect_global(assembly.static, candidate, project.package, run_cache)
        assert result.status == "COMPARED"
        assert result.state == "STATIC_REGRESSION"
        assert result.incremental_identities == (shifted.identity,)
        scope = run_cache.snapshot(candidate.proposal.cell)
        assert scope.comparisons[0].result == result
        highest.close()
        candidate.close()

    def test_static_evaluator_preserves_tool_failure(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        outcomes: tuple[TyCheck | ToolFailure, ...] = (tool_failure(),)
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            ty_handler=lambda vector, call: outcomes[call - 1],
        )
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)

        result = collect_highest(assembly.static, prepared, run_cache=run_cache, package=project.package)
        assert isinstance(result, RunTyFactRef)
        assert result.observation.fact.kind == "ty-check-unavailable"
        assert result.process == outcomes[-1].process
        prepared.close()


class TestRuntimeEvaluator:
    @pytest.mark.parametrize("outcome", (
        VerifierPass(terminal=NormalExit(exit_code=0)),
        VerifierRejected(terminal=NormalExit(exit_code=1)),
        VerifierIndeterminate(terminal=TimedOut(), reason="process-timed-out"),
    ))
    def test_configured_execution_needs_no_static_capture(self, run_cache, tmp_path, outcome):
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(), verifier_handler=lambda *_: VerifierRun(authoritative=outcome),
        )
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0], snapshot=project.snapshot,
            resolution=HighestResolution(), source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            run = assembly.runtime.evaluate(prepared, package=project.package, run_cache=run_cache)
            assert run.evaluation.proposal == prepared.proposal
            assert run.evaluation.verifier == outcome
            assert assembly.ty.vectors == []
            assert assembly.verifier.vectors == [()]
            assert prepared.tested
            scope = run_cache.snapshot(prepared.proposal.cell)
            assert scope.facts == scope.consumers == scope.passes == ()
        finally:
            prepared.close()
            project.snapshot.close()

    @pytest.mark.parametrize(
        ("outcome", "expected_type", "expected_cause"),
        (
            (VerifierPass(terminal=NormalExit(exit_code=0)), "PASS", None),
            (
                VerifierRejected(terminal=NormalExit(exit_code=4)),
                "VERIFIER_REJECTED",
                "VERIFIER_EXITED_NONZERO",
            ),
            (
                VerifierIndeterminate(
                    terminal=TimedOut(),
                    reason="process-timed-out",
                ),
                "INDETERMINATE",
                "TIMEOUT",
            ),
        ),
    )
    @pytest.mark.parametrize("static_available", (True, False))
    def test_runtime_evaluator_preserves_authoritative_verifier_outcome(
        self, run_cache,
        tmp_path: Path,
        outcome: VerifierPass | VerifierRejected | VerifierIndeterminate,
        expected_type: str,
        expected_cause: str | None,
        static_available: bool,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            verifier_handler=lambda vector, call: VerifierRun(authoritative=outcome),
            ty_handler=lambda vector, call: empty_check() if static_available else tool_failure(),
        )
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        capture = collect_highest(assembly.static, prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, RunTyFactRef)

        run = assembly.runtime.evaluate(
            prepared,
            run_cache=run_cache, package=project.package,

        )

        if not static_available:
            scope = run_cache.snapshot(prepared.proposal.cell)
            assert scope.facts[0].observation.fact.kind == "ty-check-unavailable"
        assert len(assembly.verifier.vectors) == 1
        assert run.evaluation.status == expected_type
        assert prepared.tested is True
        if expected_cause is not None:
            failure = FailurePolicy().record_evaluation(
                AttemptFailureScope(attempt=prepared.attempt),
                run.evaluation,
            )
            assert failure is not None
            assert failure.cause == expected_cause
            assert failure.authority.kind == "configured-verifier"
        prepared.close()

    def test_runtime_evaluator_runs_tests_for_a_general_static_regression(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        increment = diagnostic("snapshot|demo.py|1|2|invalid-type")
        project = evaluation_project(
            tmp_path / "project",
            dependency="demo-dep",
            source="value = 1\n",
        )
        assembly = evaluation_assembly(
            ty_handler=lambda vector, call: (
                empty_check()
                if call == 1
                else TyCheck(
                    process=successful_process(exit_code=1),
                    diagnostics=(increment,),
                )
            ),
        )
        highest = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(highest, PreparedEnvironment)
        capture = collect_highest(assembly.static, highest, run_cache=run_cache, package=project.package)
        assert isinstance(capture, RunTyFactRef)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=candidate_resolution(highest, "demo-dep", "2"),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        comparison = collect_global(assembly.static, candidate, project.package, run_cache)
        run = assembly.runtime.evaluate(
            candidate,
            run_cache=run_cache, package=project.package,
        )

        assert run.evaluation.status == "PASS"
        assert comparison.status == "COMPARED"
        assert comparison.state == "STATIC_REGRESSION"
        assert assembly.verifier.vectors[-1] == (
            VersionPin(name="demo-dep", version="2"),
        )
        highest.close()
        candidate.close()


class TestRuntimeStaticGuidance:
    @pytest.mark.parametrize("code,source", [
        ("unresolved-import", "import requests.missing\n"),
        ("unresolved-attribute", "import requests\nrequests.missing\n"),
        ("invalid-assignment", "value: int = 'text'\n"),
    ])
    @pytest.mark.parametrize("verifier_exit", [0, 4])
    def test_static_regression_runs_the_configured_verifier(
        self, run_cache, tmp_path: Path, code: str, source: str, verifier_exit: int,
    ) -> None:
        increment = diagnostic(f"snapshot|demo.py|1|8|{code}")
        project = evaluation_project(tmp_path / "project", dependency="requests", source=source)
        assembly = evaluation_assembly(
            highest=(VersionPin(name="requests", version="3"),),
            ty_handler=lambda vector, call: empty_check() if call == 1 else TyCheck(
                process=successful_process(exit_code=1), diagnostics=(increment,)),
            verifier_handler=lambda vector, call: VerifierRun(authoritative=(
                VerifierPass(terminal=NormalExit(exit_code=0)) if verifier_exit == 0
                else VerifierRejected(terminal=NormalExit(exit_code=verifier_exit)))),
        )
        highest = assembly.environments.prepare(package=project.package, cell=project.package.cells[0],
                                               snapshot=project.snapshot, resolution=HighestResolution(),
                                               source_plan=project.source_plan)
        assert isinstance(highest, PreparedEnvironment)
        candidate = None
        try:
            capture = collect_highest(assembly.static, highest, run_cache=run_cache, package=project.package)
            assert isinstance(capture, RunTyFactRef)
            candidate = assembly.environments.prepare(package=project.package, cell=project.package.cells[0],
                                                      snapshot=project.snapshot,
                                                      resolution=candidate_resolution(highest, "requests", "2"),
                                                      source_plan=project.source_plan)
            assert isinstance(candidate, PreparedEnvironment)
            comparison = collect_global(assembly.static, candidate, project.package, run_cache)
            run = assembly.runtime.evaluate(candidate, run_cache=run_cache, package=project.package)
            assert run.evaluation.status == ("PASS" if verifier_exit == 0 else "VERIFIER_REJECTED")
            assert len(assembly.verifier.vectors) == 1
            assert comparison.status == "COMPARED"
            assert comparison.state == "STATIC_REGRESSION"
            assert comparison.incremental_identities == (increment.identity,)
            assert candidate.tested
        finally:
            highest.close()
            if isinstance(candidate, PreparedEnvironment):
                candidate.close()


class TestEvaluationProgress:
    def test_evaluators_report_stage_and_verifier_progress(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        events = RecordingStages()
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=(), events=events)
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        capture = collect_highest(assembly.static, prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, RunTyFactRef)
        assert events.events[-1].stage == "static-probe"
        events.events.clear()

        class ProgressVerifier:
            def run(
                self,
                request: VerifierRequest,
                progress: Callable[[StageProgress | None], None] | None = None,
            ) -> VerifierRun:
                assert progress is not None
                progress(StageProgress(completed=3, total=5, unit="tests"))
                return VerifierRun(
                    authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
                )

        run = RuntimeEvaluator(
            verifier=ProgressVerifier(),
            events=events,
        ).evaluate(
            prepared,
            run_cache=run_cache, package=project.package,

        )

        assert run.evaluation.status == "PASS"
        assert [event.stage for event in events.events] == [
            "dynamic tests",
            "dynamic tests",
        ]
        assert events.events[-1].progress == StageProgress(
            completed=3,
            total=5,
            unit="tests",
        )
        prepared.close()

    def test_cache_hit_does_not_emit_a_ty_stage(
        self, run_cache, tmp_path: Path,
    ) -> None:
        events = RecordingStages()
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=(), events=events)
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        first = collect_highest(assembly.static, prepared, run_cache=run_cache, package=project.package)
        assert isinstance(first, RunTyFactRef)
        assert any(event.stage == "static-probe" for event in events.events)
        events.events.clear()
        second = collect_highest(assembly.static, prepared, run_cache=run_cache, package=project.package)
        assert second is first
        assert all(getattr(event, "stage", None) != "static-probe" for event in events.events)
        prepared.close()


class TestStagePermitPools:
    def test_ty_permits_bound_concurrent_public_static_evaluations(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=())
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        lock = Lock()
        active = 0
        maximum_active = 0
        calls: list[dict[str, object]] = []

        class BlockingTy:
            def observe(self, request, *, cancellation=None) -> TyCheck:
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    calls.append({"request": request})
                time.sleep(0.05)
                with lock:
                    active -= 1
                return empty_check()

        static = StaticEvaluator(
            BlockingTy(),
            requests=ScriptedStaticRequests(),
            permits=StagePermitPools(ty_jobs=1, test_jobs=2),
        )
        other = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0], snapshot=project.snapshot,
            resolution=HighestResolution(), source_plan=project.source_plan,
        )
        assert isinstance(other, PreparedEnvironment)
        try:
            with TyCheckCache() as other_cache, ThreadPoolExecutor(max_workers=2) as executor:
                captures = tuple(executor.map(
                    lambda pair: collect_highest(static, pair[0], run_cache=pair[1], package=project.package),
                    ((prepared, run_cache), (other, other_cache)),
                ))
            assert all(isinstance(item, RunTyFactRef) for item in captures)
            assert maximum_active == 1
            assert len(calls) == 2
        finally:
            prepared.close()
            other.close()

    def test_test_permits_bound_concurrent_public_runtime_evaluations(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=())
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        capture = collect_highest(assembly.static, prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, RunTyFactRef)
        lock = Lock()
        active = 0
        maximum_active = 0
        requests: list[VerifierRequest] = []

        class BlockingVerifier:
            def run(
                self,
                request: VerifierRequest,
                progress: Callable[[StageProgress | None], None] | None = None,
            ) -> VerifierRun:
                del progress
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    requests.append(request)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return VerifierRun(
                    authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
                )

        runtime = RuntimeEvaluator(
            verifier=BlockingVerifier(),
            permits=StagePermitPools(ty_jobs=2, test_jobs=1),
        )
        other = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(other, PreparedEnvironment)
        environments = (prepared, other)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                runs = tuple(
                    executor.map(
                        lambda environment: runtime.evaluate(
                            environment,
                            run_cache=run_cache, package=project.package,

                        ),
                        environments,
                    )
                )

            assert all(run.evaluation.status == "PASS" for run in runs)
            assert maximum_active == 1
            assert len(requests) == 2
        finally:
            for environment in environments:
                environment.close()

    def test_runtime_evaluator_uses_the_prepared_root_for_the_test_command(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=())
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        capture = collect_highest(assembly.static, prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, RunTyFactRef)

        class RequestRecorder(ScriptedVerifier):
            request: VerifierRequest | None = None

            def run(
                self,
                request: VerifierRequest,
                progress: Callable[[StageProgress | None], None] | None = None,
            ) -> VerifierRun:
                self.request = request
                return super().run(request, progress)

        verifier = RequestRecorder(assembly.uv)
        run = RuntimeEvaluator(
            verifier=verifier,
        ).evaluate(
            prepared,
            run_cache=run_cache, package=project.package,

        )

        assert run.evaluation.status == "PASS"
        assert verifier.request is not None
        assert verifier.request.cwd == prepared.proposal_root
        assert verifier.request.command == ("python", "-c", "pass")
        prepared.close()

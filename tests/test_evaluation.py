from __future__ import annotations

from pf.static import CollectedStaticSubject

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

from evaluation_fixtures import (
    ScriptedVerifier,
    evaluation_assembly,
    evaluation_project,
    selected_candidate,
    successful_process,
)

from pf.static import TyCheckCache
from evaluation_fixtures import ScriptedProcessRunner
from pf.environment import ExactSelection, HighestResolution, LowestDirectResolution, PreparedEnvironment
from pf.evaluation import RuntimeEvaluator, StagePermitPools
from pf.static import StaticEvaluator
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
from pf.schemas.static_comparison import StaticComparisonUnavailable


def collect_global(static, prepared, package, cache):
    collected = static.collect_prepared(prepared, package=package, run_cache=cache)
    assert isinstance(collected, CollectedStaticSubject)
    return static.compare_global(collected, run_cache=cache)


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

        capture = assembly.static.capture_highest(highest, run_cache=run_cache, package=project.package)
        assert isinstance(capture, CollectedStaticSubject)
        result = collect_global(assembly.static, candidate, project.package, run_cache)
        assert result.status == "COMPARED"
        assert result.state == "STATIC_REGRESSION"
        assert result.incremental_identities == (shifted.identity,)
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

        result = assembly.static.capture_highest(prepared, run_cache=run_cache, package=project.package)
        assert isinstance(result, CollectedStaticSubject)
        assert run_cache.documents()[0].fact.kind == "ty-check-unavailable"
        prepared.close()

    def test_compare_global_handle_survives_environment_close_and_rejects_foreign_or_closed_cache(
        self, tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency="demo-dep")
        assembly = evaluation_assembly()
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
        cache = TyCheckCache()
        try:
            capture = assembly.static.capture_highest(highest, run_cache=cache, package=project.package)
            collected = assembly.static.collect_prepared(candidate, package=project.package, run_cache=cache)
            assert isinstance(capture, CollectedStaticSubject)
            assert isinstance(collected, CollectedStaticSubject)
            highest.close()
            candidate.close()
            compared = assembly.static.compare_global(collected, run_cache=cache)
            assert compared.status == "COMPARED"
            foreign = TyCheckCache()
            try:
                with pytest.warns(RuntimeWarning, match="static observation failed"):
                    crossed = assembly.static.compare_global(collected, run_cache=foreign)
                assert isinstance(crossed, StaticComparisonUnavailable)
                assert crossed.status == "UNAVAILABLE"
            finally:
                foreign.close()
            cache.close()
            with pytest.warns(RuntimeWarning, match="static observation failed"):
                closed = assembly.static.compare_global(collected, run_cache=cache)
            assert isinstance(closed, StaticComparisonUnavailable)
            assert closed.status == "UNAVAILABLE"
        finally:
            if not highest.closed:
                highest.close()
            if not candidate.closed:
                candidate.close()
            project.snapshot.close()


class TestNonemptyPreparationAdmission:
    def test_nonempty_environment_plan_replays_highest_relaxed_and_rejects_tampered_request(
        self, tmp_path: Path,
    ) -> None:
        from pf.resolution import ResolutionPlanEvidence, environment_identity_digest, resolution_semantic_digest
        from pf.static.audit import _admit_saved_static_audit
        from pf.static_request import static_preparation_evidence
        from pf.schemas.static_preparation import StaticPreparationEvidence

        project = evaluation_project(tmp_path / "project", test_dependencies=("packaging",))
        assembly = evaluation_assembly(
            highest=(VersionPin(name="demo-dep", version="3"),),
            lowest=(VersionPin(name="demo-dep", version="1"),),
        )
        cell = project.package.cells[0]
        highest = assembly.environments.prepare(
            package=project.package, cell=cell, snapshot=project.snapshot,
            resolution=HighestResolution(), source_plan=project.source_plan,
        )
        assert isinstance(highest, PreparedEnvironment)
        assert highest.environment_plan is not None
        cache = TyCheckCache()
        try:
            captured = assembly.static.capture_highest(highest, package=project.package, run_cache=cache)
            assert isinstance(captured, CollectedStaticSubject)
            membership = cache.admitted_membership(cell)
            assert membership is not None

            lower = assembly.environments.prepare(
                package=project.package, cell=cell, snapshot=project.snapshot,
                resolution=LowestDirectResolution(highest.harness_baseline),
                source_plan=project.source_plan,
            )
            assert isinstance(lower, PreparedEnvironment)
            assert lower.environment_plan is not None
            collected_lower = assembly.static.collect_prepared(lower, package=project.package, run_cache=cache)
            assert isinstance(collected_lower, CollectedStaticSubject)
            assert cache.admitted_membership(cell) is not None

            exact = assembly.environments.prepare(
                package=project.package, cell=cell, snapshot=project.snapshot,
                resolution=candidate_resolution(highest, "demo-dep", "2"),
                source_plan=project.source_plan,
            )
            assert isinstance(exact, PreparedEnvironment)
            assert exact.environment_plan is not None
            collected_exact = assembly.static.collect_prepared(exact, package=project.package, run_cache=cache)
            assert isinstance(collected_exact, CollectedStaticSubject)
            assert cache.admitted_membership(cell) is not None

            evidence = static_preparation_evidence(highest, project.package)
            assert isinstance(evidence, StaticPreparationEvidence)
            assert evidence.environment_plan is not None
            forged_plan_payload = evidence.environment_plan.model_dump(mode="json")
            forged_plan_payload["request_digest"] = "f" * 64
            forged_plan_payload["semantic_digest"] = resolution_semantic_digest(
                kind="environment", request_digest="f" * 64,
                context=evidence.environment_plan.context,
                packages=evidence.environment_plan.packages,
                direct_harness=evidence.environment_plan.direct_harness,
            )
            forged_plan = ResolutionPlanEvidence.model_validate(forged_plan_payload)
            forged = evidence.model_dump(mode="json")
            forged["environment_plan"] = forged_plan.model_dump(mode="json")
            forged["proposal"]["environment_plan_digest"] = forged_plan.semantic_digest
            forged["proposal"]["proposal_id"] = environment_identity_digest(
                attempt_id=evidence.attempt.attempt_id,
                project_plan_digest=evidence.project_plan.semantic_digest,
                environment_plan_digest=forged_plan.semantic_digest,
                graph=evidence.proposal.resolved_graph,
            )
            forged_prep = StaticPreparationEvidence.model_validate(forged)
            scope = cache.snapshot(cell)
            payload = scope.model_dump(mode="json")
            payload["preparations"][0]["preparation"] = forged_prep.model_dump(mode="json")
            with pytest.raises(ValueError, match="resolution request mismatch"):
                _admit_saved_static_audit(type(scope).model_validate(payload))
        finally:
            highest.close()
            if "lower" in locals() and isinstance(lower, PreparedEnvironment):
                lower.close()
            if "exact" in locals() and isinstance(exact, PreparedEnvironment):
                exact.close()
            cache.close()
            project.snapshot.close()


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
            run = assembly.runtime.evaluate(prepared, package=project.package)
            assert run.evaluation.proposal == prepared.proposal
            assert run.evaluation.verifier == outcome
            assert assembly.ty.vectors == []
            assert assembly.verifier.vectors == [()]
            assert prepared.tested
            assert run_cache.documents() == ()
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
        capture = assembly.static.capture_highest(prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, CollectedStaticSubject)

        run = assembly.runtime.evaluate(prepared, package=project.package)
        assembly.static.record_runtime(prepared, run, run_cache=run_cache)

        if not static_available:
            assert run_cache.documents()[0].fact.kind == "ty-check-unavailable"
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
        capture = assembly.static.capture_highest(highest, run_cache=run_cache, package=project.package)
        assert isinstance(capture, CollectedStaticSubject)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=candidate_resolution(highest, "demo-dep", "2"),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        comparison = collect_global(assembly.static, candidate, project.package, run_cache)
        run = assembly.runtime.evaluate(candidate, package=project.package)
        assembly.static.record_runtime(candidate, run, run_cache=run_cache)

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
            capture = assembly.static.capture_highest(highest, run_cache=run_cache, package=project.package)
            assert isinstance(capture, CollectedStaticSubject)
            candidate = assembly.environments.prepare(package=project.package, cell=project.package.cells[0],
                                                      snapshot=project.snapshot,
                                                      resolution=candidate_resolution(highest, "requests", "2"),
                                                      source_plan=project.source_plan)
            assert isinstance(candidate, PreparedEnvironment)
            comparison = collect_global(assembly.static, candidate, project.package, run_cache)
            run = assembly.runtime.evaluate(candidate, package=project.package)
            assembly.static.record_runtime(candidate, run, run_cache=run_cache)
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
        capture = assembly.static.capture_highest(prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, CollectedStaticSubject)
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
        ).evaluate(prepared, package=project.package)

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
        first = assembly.static.capture_highest(prepared, run_cache=run_cache, package=project.package)
        assert isinstance(first, CollectedStaticSubject)
        assert any(event.stage == "static-probe" for event in events.events)
        events.events.clear()
        second = assembly.static.capture_highest(prepared, run_cache=run_cache, package=project.package)
        assert isinstance(second, CollectedStaticSubject)
        assert len(assembly.ty.vectors) == 1
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
        entered = Event()
        release = Event()

        class BlockingTy:
            def observe(self, request, *, cancellation=None) -> TyCheck:
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    calls.append({"request": request})
                entered.set()
                assert release.wait(timeout=1)
                with lock:
                    active -= 1
                return empty_check()

        static = StaticEvaluator(
            BlockingTy(),
            processes=ScriptedProcessRunner(assembly.uv),
            permits=StagePermitPools(ty_jobs=1, test_jobs=2),
        )
        other = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0], snapshot=project.snapshot,
            resolution=HighestResolution(), source_plan=project.source_plan,
        )
        assert isinstance(other, PreparedEnvironment)
        try:
            with TyCheckCache() as other_cache, ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    static.capture_highest,
                    prepared,
                    run_cache=run_cache,
                    package=project.package,
                )
                second = executor.submit(
                    static.capture_highest,
                    other,
                    run_cache=other_cache,
                    package=project.package,
                )
                assert entered.wait(timeout=1)
                release.set()
                captures = (first.result(timeout=2), second.result(timeout=2))
            assert all(isinstance(item, CollectedStaticSubject) for item in captures)
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
        capture = assembly.static.capture_highest(prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, CollectedStaticSubject)
        lock = Lock()
        active = 0
        maximum_active = 0
        requests: list[VerifierRequest] = []
        entered = Event()
        release = Event()

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
                entered.set()
                assert release.wait(timeout=1)
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
                first = executor.submit(
                    runtime.evaluate,
                    prepared,
                    package=project.package,
                )
                second = executor.submit(
                    runtime.evaluate,
                    other,
                    package=project.package,
                )
                assert entered.wait(timeout=1)
                release.set()
                runs = (first.result(timeout=2), second.result(timeout=2))

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
        capture = assembly.static.capture_highest(prepared, run_cache=run_cache, package=project.package)
        assert isinstance(capture, CollectedStaticSubject)

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
        ).evaluate(prepared, package=project.package)

        assert run.evaluation.status == "PASS"
        assert verifier.request is not None
        assert verifier.request.cwd == prepared.proposal_root
        assert verifier.request.command == ("python", "-c", "pass")
        prepared.close()

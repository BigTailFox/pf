from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
import time
from typing import Literal

import pytest

from evaluation_fixtures import (
    ScriptedVerifier,
    evaluation_assembly,
    evaluation_project,
    selected_candidate,
    successful_process,
)

from pf.environment import ExactSelection, HighestResolution, PreparedEnvironment
from pf.evaluation import RuntimeEvaluator, StagePermitPools, StaticEvaluator
from pf.failure import FailurePolicy
from pf.schemas.evaluation import (
    AttemptFailureScope,
    CellStageEvent,
    IndeterminateEvaluation,
    NormalExit,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessResult,
    StageProgress,
    StaticBaseline,
    StaticBaselineCapture,
    StaticRegressionEvaluation,
    TimedOut,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRequest,
    VerifierRun,
    ty_diagnostic_digest,
)
from pf.schemas.project import VersionPin
from pf.static_transition import static_fingerprint


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


class TestStaticFingerprint:
    def test_static_fingerprint_preserves_the_complete_ordered_multiset(self) -> None:
        states = {
            "empty": static_fingerprint(()),
            "a": static_fingerprint(("A",)),
            "a-twice": static_fingerprint(("A", "A")),
            "a-b": static_fingerprint(("A", "B")),
            "c": static_fingerprint(("C",)),
        }

        assert len(set(states.values())) == len(states)
        assert states == {
            name: static_fingerprint(identities)
            for name, identities in {
                "empty": (),
                "a": ("A",),
                "a-twice": ("A", "A"),
                "a-b": ("A", "B"),
                "c": ("C",),
            }.items()
        }


class TestStaticEvaluator:
    @pytest.mark.parametrize("scope", ("cell", "snapshot", "policy"))
    def test_static_evaluator_rejects_a_baseline_from_another_scope(
        self,
        tmp_path: Path,
        scope: str,
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
        changes: dict[str, object]
        if scope == "cell":
            changes = {
                "cell": prepared.proposal.cell.model_copy(
                    update={"python_minor": "3.11"}
                )
            }
        elif scope == "snapshot":
            changes = {"snapshot_digest": "other-snapshot"}
        else:
            changes = {"policy_identity": "other-policy"}
        baseline = StaticBaseline(
            proposal=prepared.proposal.model_copy(update=changes),
            ty=empty_check(),
            digest=ty_diagnostic_digest(()),
        )

        with pytest.raises(ValueError, match="cell, snapshot, and policy"):
            assembly.static.evaluate(
                prepared,
                package=project.package,
                baseline=baseline,
            )
        assert assembly.ty.vectors == []
        prepared.close()

    def test_static_evaluator_uses_multiset_subtraction_against_a_frozen_baseline(
        self,
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

        capture = assembly.static.capture(highest, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
        result = assembly.static.evaluate(
            candidate,
            package=project.package,
            baseline=capture.baseline,
        )

        assert capture.static.status == "STATIC_UNCHANGED"
        assert capture.static.ty is capture.baseline.ty
        assert isinstance(result, StaticRegressionEvaluation)
        assert [item.identity for item in result.incremental] == [shifted.identity]
        assert result.static_fingerprint == static_fingerprint((shifted.identity,))
        assert result.baseline_digest == capture.baseline.digest
        highest.close()
        candidate.close()

    @pytest.mark.parametrize("operation", ("capture", "evaluate"))
    def test_static_evaluator_preserves_tool_failure(
        self,
        tmp_path: Path,
        operation: str,
    ) -> None:
        outcomes: tuple[TyCheck | ToolFailure, ...] = (
            (empty_check(), tool_failure())
            if operation == "evaluate"
            else (tool_failure(),)
        )
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

        if operation == "capture":
            result = assembly.static.capture(prepared, package=project.package)
        else:
            capture = assembly.static.capture(prepared, package=project.package)
            assert isinstance(capture, StaticBaselineCapture)
            result = assembly.static.evaluate(
                prepared,
                package=project.package,
                baseline=capture.baseline,
            )

        assert isinstance(result, IndeterminateEvaluation)
        assert result.cause == "TOOL_FAILURE"
        prepared.close()


class TestRuntimeEvaluator:
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
    def test_runtime_evaluator_preserves_authoritative_verifier_outcome(
        self,
        tmp_path: Path,
        outcome: VerifierPass | VerifierRejected | VerifierIndeterminate,
        expected_type: str,
        expected_cause: str | None,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            verifier_handler=lambda vector, call: VerifierRun(authoritative=outcome),
        )
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        capture = assembly.static.capture(prepared, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)

        run = assembly.runtime.evaluate(
            prepared,
            package=project.package,
            baseline=capture.baseline,
            static_result=capture.static,
        )

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
        self,
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
        capture = assembly.static.capture(highest, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=candidate_resolution(highest, "demo-dep", "2"),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        run = assembly.runtime.evaluate(
            candidate,
            package=project.package,
            baseline=capture.baseline,
        )

        assert run.evaluation.status == "PASS"
        assert run.evaluation.static.status == "STATIC_REGRESSION"
        assert assembly.verifier.vectors[-1] == (
            VersionPin(name="demo-dep", version="2"),
        )
        highest.close()
        candidate.close()


class TestRuntimeWitnessEvaluator:
    @pytest.mark.parametrize(
        "witness_status",
        ("PRESENT", "NOT_APPLICABLE", "CONFIRMED_MISSING", "TOOL_FAILURE"),
    )
    def test_runtime_evaluator_routes_a_static_witness_outcome(
        self,
        tmp_path: Path,
        witness_status: Literal[
            "PRESENT",
            "NOT_APPLICABLE",
            "CONFIRMED_MISSING",
            "TOOL_FAILURE",
        ],
    ) -> None:
        increment = diagnostic("snapshot|demo.py|1|8|unresolved-import")
        project = evaluation_project(
            tmp_path / "project",
            dependency="requests",
            source="import requests.missing\n",
        )

        def witness(vector, plan, call):
            if witness_status == "TOOL_FAILURE":
                return tool_failure("witness")
            return RuntimeWitnessResult(
                status=witness_status,
                plan=plan,
                process=successful_process(),
            )

        assembly = evaluation_assembly(
            highest=(VersionPin(name="requests", version="3"),),
            ty_handler=lambda vector, call: (
                empty_check()
                if call == 1
                else TyCheck(
                    process=successful_process(exit_code=1),
                    diagnostics=(increment,),
                )
            ),
            witness_handler=witness,
        )
        highest = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(highest, PreparedEnvironment)
        capture = assembly.static.capture(highest, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=candidate_resolution(highest, "requests", "2"),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        run = assembly.runtime.evaluate(
            candidate,
            package=project.package,
            baseline=capture.baseline,
        )

        assert len(assembly.witnesses.calls) == 1
        if witness_status in {"PRESENT", "NOT_APPLICABLE"}:
            assert run.evaluation.status == "PASS"
            assert len(assembly.verifier.vectors) == 1
        elif witness_status == "CONFIRMED_MISSING":
            assert isinstance(run.evaluation, RuntimeInterfaceMissingEvaluation)
            assert assembly.verifier.vectors == []
            assert run.evaluation.witnesses[-1].plan.diagnostic_identities == (
                increment.identity,
            )
            failure = FailurePolicy().record_evaluation(
                AttemptFailureScope(attempt=candidate.attempt),
                run.evaluation,
            )
            assert failure is not None
            assert failure.cause == "RUNTIME_INTERFACE_MISSING"
            assert failure.disposition == "REJECTED"
        else:
            assert isinstance(run.evaluation, IndeterminateEvaluation)
            assert run.evaluation.cause == "TOOL_FAILURE"
            assert assembly.verifier.vectors == []
        highest.close()
        candidate.close()

    def test_runtime_evaluator_deduplicates_an_identical_witness_plan(
        self,
        tmp_path: Path,
    ) -> None:
        increment = diagnostic("snapshot|demo.py|1|8|unresolved-import")
        project = evaluation_project(
            tmp_path / "project",
            dependency="requests",
            source="import requests.missing\n",
        )
        assembly = evaluation_assembly(
            highest=(VersionPin(name="requests", version="3"),),
            ty_handler=lambda vector, call: (
                empty_check()
                if call == 1
                else TyCheck(
                    process=successful_process(exit_code=1),
                    diagnostics=(increment, increment),
                )
            ),
            witness_handler=lambda vector, plan, call: RuntimeWitnessResult(
                status="PRESENT",
                plan=plan,
                process=successful_process(),
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
        capture = assembly.static.capture(highest, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
        candidate = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            resolution=candidate_resolution(highest, "requests", "2"),
            source_plan=project.source_plan,
        )
        assert isinstance(candidate, PreparedEnvironment)

        run = assembly.runtime.evaluate(
            candidate,
            package=project.package,
            baseline=capture.baseline,
        )

        assert run.evaluation.status == "PASS"
        assert len(assembly.witnesses.calls) == 1
        assert len(run.evaluation.witnesses) == 1
        highest.close()
        candidate.close()


class TestEvaluationProgress:
    def test_evaluators_report_stage_and_verifier_progress(
        self,
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
        capture = assembly.static.capture(prepared, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
        assert events.events[-1].stage == "capturing static baseline"
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
            static=assembly.static,
            verifier=ProgressVerifier(),
            events=events,
        ).evaluate(
            prepared,
            package=project.package,
            baseline=capture.baseline,
            static_result=capture.static,
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


class TestStagePermitPools:
    def test_ty_permits_bound_concurrent_public_static_evaluations(
        self,
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
            def check(self, **kwargs: object) -> TyCheck:
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    calls.append(kwargs)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return empty_check()

        static = StaticEvaluator(
            BlockingTy(),
            permits=StagePermitPools(ty_jobs=1, test_jobs=2),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            captures = tuple(
                executor.map(
                    lambda _: static.capture(prepared, package=project.package),
                    range(2),
                )
            )

        assert all(isinstance(item, StaticBaselineCapture) for item in captures)
        assert maximum_active == 1
        assert len(calls) == 2
        assert all("jobs" not in call for call in calls)
        prepared.close()

    def test_test_permits_bound_concurrent_public_runtime_evaluations(
        self,
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
        capture = assembly.static.capture(prepared, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)
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
            static=assembly.static,
            verifier=BlockingVerifier(),
            permits=StagePermitPools(ty_jobs=2, test_jobs=1),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            runs = tuple(
                executor.map(
                    lambda _: runtime.evaluate(
                        prepared,
                        package=project.package,
                        baseline=capture.baseline,
                        static_result=capture.static,
                    ),
                    range(2),
                )
            )

        assert all(run.evaluation.status == "PASS" for run in runs)
        assert maximum_active == 1
        assert len(requests) == 2
        prepared.close()

    def test_runtime_evaluator_uses_the_prepared_root_for_the_test_command(
        self,
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
        capture = assembly.static.capture(prepared, package=project.package)
        assert isinstance(capture, StaticBaselineCapture)

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
            static=assembly.static,
            verifier=verifier,
        ).evaluate(
            prepared,
            package=project.package,
            baseline=capture.baseline,
            static_result=capture.static,
        )

        assert run.evaluation.status == "PASS"
        assert verifier.request is not None
        assert verifier.request.cwd == prepared.proposal_root
        assert verifier.request.command == ("python", "-c", "pass")
        prepared.close()

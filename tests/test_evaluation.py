from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import tempfile
from typing import Literal

import pytest

from conftest import prepared_resolution_evidence

from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.failure import FailurePolicy
from pf.project import ProjectLoader
from pf.resolution import (
    InstalledResolution,
    NativeResolutionPlan,
    ResolutionContext,
    ResolutionOutcome,
    ResolutionPlan,
    ResolutionRunContext,
)
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    CellStageEvent,
    DiagnosticClassification,
    Evaluation,
    GraphSuccess,
    IndeterminateEvaluation,
    InterpreterSuccess,
    NormalExit,
    ProcessResult,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessAttempt,
    RuntimeWitnessPlan,
    RuntimeWitnessResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticRegressionEvaluation,
    StageProgress,
    ToolFailure,
    ToolSuccess,
    TyCheck,
    TyDiagnostic,
    VerifierRejected,
    VerifierRejectedEvaluation,
    VerifierOutcome,
    VerifierRequest,
    VerifierRun,
    VerifierPass,
    VerifierIndeterminate,
    TimedOut,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    InterpreterIdentity,
    PackagePlan,
    Proposal,
    ResolvedNode,
    SourcePlan,
)
from pf.snapshot import SnapshotBuilder
from pf.static_transition import static_fingerprint


def process_result(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def diagnostic(identity: str, *, message: str = "message") -> TyDiagnostic:
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


def prepared_for_static(tmp_path: Path, proposal_id: str) -> PreparedEnvironment:
    temporary = tempfile.TemporaryDirectory(prefix=f"pf-{proposal_id}-", dir=tmp_path)
    root = Path(temporary.name)
    source = root / "source"
    environment = root / "environment"
    source.mkdir()
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
            evaluation_policy_identity="policy",
        )
    )
    return PreparedEnvironment(
        attempt=attempt,
        proposal=Proposal(
            proposal_id=proposal_id,
            attempt_id=attempt.attempt_id,
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=(),
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        ),
        proposal_root=source,
        package_root=source,
        environment_root=environment,
        interpreter=environment / "bin" / "python",
        **prepared_resolution_evidence(cell=cell),
        temporary_directory=temporary,
    )


def runtime_interface_missing_evaluation(
    tmp_path: Path,
) -> tuple[PreparedEnvironment, RuntimeInterfaceMissingEvaluation, TyDiagnostic]:
    prepared = prepared_for_static(tmp_path, "candidate")
    related_one = diagnostic("snapshot|demo.py|1|2|missing-one")
    related_two = diagnostic("snapshot|demo.py|2|2|missing-two")
    unrelated = diagnostic("snapshot|demo.py|3|2|general")
    plan = RuntimeWitnessPlan(
        diagnostic_identities=(related_one.identity, related_two.identity),
        managed_dependency="demo",
        operation="import-module",
        module="demo",
    )
    static = StaticRegressionEvaluation(
        proposal=prepared.proposal,
        ty=TyCheck(
            process=process_result(exit_code=1),
            diagnostics=(related_one, related_two, unrelated),
        ),
        baseline_digest=ty_diagnostic_digest(()),
        incremental=(related_one, related_two, unrelated),
        static_fingerprint=static_fingerprint(
            (related_one.identity, related_two.identity, unrelated.identity)
        ),
        classifications=(
            DiagnosticClassification(
                diagnostic_identity=related_one.identity,
                classification="strong",
                reason_code="witness-planned",
                witness_plan=plan,
            ),
            DiagnosticClassification(
                diagnostic_identity=related_two.identity,
                classification="strong",
                reason_code="witness-planned",
                witness_plan=plan,
            ),
            DiagnosticClassification(
                diagnostic_identity=unrelated.identity,
                classification="general",
                reason_code="not-runtime-checkable",
            ),
        ),
    )
    evaluation = RuntimeInterfaceMissingEvaluation(
        proposal=prepared.proposal,
        static=static,
        witnesses=(
            RuntimeWitnessAttempt(
                plan=plan,
                outcome=RuntimeWitnessResult(
                    status="CONFIRMED_MISSING",
                    plan=plan,
                    process=process_result(),
                ),
            ),
        ),
    )
    return prepared, evaluation, related_one


def empty_baseline(prepared: PreparedEnvironment) -> StaticBaseline:
    check = TyCheck(process=process_result(), diagnostics=())
    return StaticBaseline(
        proposal=prepared.proposal,
        ty=check,
        digest=ty_diagnostic_digest(check.diagnostics),
    )


class PreparedUv:
    def resolution_run_context(self, **kwargs: object) -> ResolutionRunContext:
        return ResolutionRunContext(
            uv_version="0.12.5",
            release_cutoff="2026-08-23T00:00:00+00:00",
        )

    def resolve_project(self, **kwargs: object) -> ResolutionOutcome:
        return self._plan("project", kwargs)

    def resolve_environment(self, **kwargs: object) -> ResolutionOutcome:
        return self._plan("environment", kwargs)

    @staticmethod
    def _plan(kind: str, kwargs: dict[str, object]) -> ResolutionPlan:
        context = kwargs["context"]
        request_digest = kwargs["request_digest"]
        assert isinstance(context, ResolutionContext)
        assert isinstance(request_digest, str)
        return ResolutionPlan.from_evidence(
            kind="project" if kind == "project" else "environment",
            request_digest=request_digest,
            context=context,
            packages=(),
            direct_harness=(),
            native=NativeResolutionPlan.from_content(
                'lock-version = "1.0"\ncreated-by = "uv"\npackages = []\n'
            ),
            process=process_result(),
        )

    def create_environment(self, **kwargs: object) -> ToolSuccess:
        return ToolSuccess(stage="create-environment", process=process_result())

    def install_resolution(self, **kwargs: object) -> InstalledResolution:
        plan = kwargs["plan"]
        assert isinstance(plan, ResolutionPlan)
        return InstalledResolution(plan_digest=plan.digest, process=process_result())

    def inspect_interpreter(self, **kwargs: object) -> InterpreterSuccess:
        return InterpreterSuccess(
            process=process_result(),
            interpreter=InterpreterIdentity(
                implementation="cpython",
                version="3.10.18",
                abi="cpython-310-x86_64-linux-gnu",
            ),
        )

    def inspect_environment(self, **kwargs: object) -> GraphSuccess:
        return GraphSuccess(
            process=process_result(),
            nodes=(ResolvedNode(name="demo", version="0.1.0"),),
        )


class FailingTy:
    def check(self, **kwargs: object) -> TyCheck:
        return TyCheck(
            process=process_result(exit_code=1),
            diagnostics=(diagnostic("snapshot|demo.py|1|2|invalid-type"),),
        )


class PassingTests:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *args: object, **kwargs: object) -> VerifierRun:
        self.calls += 1
        return VerifierRun(authoritative=VerifierPass(terminal=NormalExit(exit_code=0)))


class PassingTy:
    def check(self, **kwargs: object) -> TyCheck:
        return TyCheck(process=process_result(), diagnostics=())


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
        prepared = prepared_for_static(tmp_path, "candidate")
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
        check = TyCheck(process=process_result(), diagnostics=())
        baseline = StaticBaseline(
            proposal=prepared.proposal.model_copy(update=changes),
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )

        class Ty:
            def check(self, **kwargs: object) -> TyCheck:
                raise AssertionError("a mismatched baseline must fail before ty runs")

        with pytest.raises(ValueError, match="cell, snapshot, and policy"):
            StaticEvaluator(Ty()).evaluate(
                prepared,
                package=ProjectLoader().load(root=Path.cwd()).target,
                baseline=baseline,
            )

    def test_static_evaluator_uses_multiset_subtraction_against_a_frozen_baseline(
        self,
        tmp_path: Path,
    ) -> None:
        repeated = diagnostic("snapshot|demo.py|1|2|invalid-type", message="baseline")
        shifted = diagnostic("snapshot|demo.py|5|6|unresolved-reference")
        checks = iter(
            (
                TyCheck(
                    process=process_result(exit_code=1),
                    diagnostics=(repeated, repeated, shifted),
                ),
                TyCheck(
                    process=process_result(exit_code=0),
                    diagnostics=(
                        repeated.model_copy(update={"message": "candidate wording"}),
                        shifted,
                        shifted.model_copy(update={"message": "extra occurrence"}),
                    ),
                ),
            )
        )

        class Ty:
            def check(self, **kwargs: object) -> TyCheck:
                return next(checks)

        baseline_environment = prepared_for_static(tmp_path, "baseline")
        candidate_environment = prepared_for_static(tmp_path, "candidate")
        package = ProjectLoader().load(root=Path.cwd()).target
        evaluator = StaticEvaluator(Ty())

        capture = evaluator.capture(baseline_environment, package=package)
        assert isinstance(capture, StaticBaselineCapture)
        result = evaluator.evaluate(
            candidate_environment,
            package=package,
            baseline=capture.baseline,
        )

        assert capture.static.status == "STATIC_UNCHANGED"
        assert capture.static.incremental == ()
        assert capture.static.ty is capture.baseline.ty
        assert capture.static.baseline_digest == capture.baseline.digest
        assert isinstance(result, StaticRegressionEvaluation)
        assert [item.identity for item in result.incremental] == [shifted.identity]
        assert result.static_fingerprint == static_fingerprint((shifted.identity,))
        assert result.baseline_digest == capture.baseline.digest


class TestRuntimeEvaluator:
    def test_runtime_evaluator_preserves_authoritative_verifier_indeterminate(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["custom-verifier"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)

        class Verifier:
            def run(
                self,
                request: VerifierRequest,
                progress: Callable[[StageProgress | None], None] | None = None,
            ) -> VerifierRun:
                del request, progress
                return VerifierRun(
                    authoritative=VerifierIndeterminate(
                        terminal=TimedOut(),
                        reason="process-timed-out",
                    )
                )

        run = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            verifier=Verifier(),
        ).evaluate(
            prepared,
            package=package,
            baseline=empty_baseline(prepared),
        )

        assert isinstance(run.evaluation, IndeterminateEvaluation)
        assert run.evaluation.cause == "TIMEOUT"
        assert run.evaluation.failure is None
        assert run.evaluation.verifier is not None
        assert isinstance(run.evaluation.verifier.terminal, TimedOut)
        failure = FailurePolicy().record_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            run.evaluation,
        )
        assert failure is not None
        assert failure.disposition == "INDETERMINATE"
        assert failure.cause == "TIMEOUT"
        assert failure.authority.kind == "configured-verifier"

    def test_runtime_evaluator_preserves_authoritative_verifier_pass(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["custom-verifier"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)

        class Verifier:
            def run(
                self,
                request: VerifierRequest,
                progress: Callable[[StageProgress | None], None] | None = None,
            ) -> VerifierRun:
                del request, progress
                return VerifierRun(
                    authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
                )

        run = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            verifier=Verifier(),
        ).evaluate(
            prepared,
            package=package,
            baseline=empty_baseline(prepared),
        )

        assert run.evaluation.status == "PASS"
        assert run.evaluation.verifier.terminal == NormalExit(exit_code=0)
        assert prepared.tested is True

    def test_runtime_evaluator_preserves_authoritative_verifier_rejection(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["custom-verifier"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)

        class Verifier:
            request: VerifierRequest | None = None

            def run(
                self,
                request: VerifierRequest,
                progress: Callable[[StageProgress | None], None] | None = None,
            ) -> VerifierRun:
                del progress
                self.request = request
                return VerifierRun(
                    authoritative=VerifierRejected(terminal=NormalExit(exit_code=4))
                )

        verifier = Verifier()
        evaluator = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            verifier=verifier,
        )

        run = evaluator.evaluate(
            prepared,
            package=package,
            baseline=empty_baseline(prepared),
        )

        assert isinstance(run.evaluation, VerifierRejectedEvaluation)
        assert run.evaluation.verifier.terminal.exit_code == 4
        assert run.diagnostics is None
        assert verifier.request is not None
        assert verifier.request.command == ("custom-verifier",)
        failure = FailurePolicy().record_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            run.evaluation,
        )
        assert failure is not None
        assert failure.disposition == "REJECTED"
        assert failure.cause == "VERIFIER_EXITED_NONZERO"
        assert failure.authority.kind == "configured-verifier"
        assert failure.process is None

    def test_runtime_evaluator_runs_tests_for_a_general_static_regression(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-m", "unittest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        verifier = PassingTests()
        evaluator = RuntimeEvaluator(
            static=StaticEvaluator(FailingTy()),
            verifier=verifier,
        )
        baseline_check = TyCheck(process=process_result(exit_code=1), diagnostics=())
        baseline = StaticBaseline(
            proposal=prepared.proposal,
            ty=baseline_check,
            digest=ty_diagnostic_digest(baseline_check.diagnostics),
        )

        result = evaluator.evaluate(prepared, package=package, baseline=baseline)

        assert result.evaluation.status == "PASS"
        assert result.evaluation.static.status == "STATIC_REGRESSION"
        assert verifier.calls == 1
        assert prepared.tested is True


class TestRuntimeWitnessEvaluator:
    @staticmethod
    def _evaluate_witness_status(
        tmp_path: Path,
        witness_status: Literal[
            "PRESENT",
            "NOT_APPLICABLE",
            "CONFIRMED_MISSING",
            "TOOL_FAILURE",
        ],
    ) -> tuple[Evaluation, int, TyDiagnostic]:
        prepared = prepared_for_static(tmp_path, "candidate")
        source = prepared.proposal_root / "demo.py"
        source.write_text("import demo\n", encoding="utf-8")
        increment = diagnostic("snapshot|demo.py|1|2|unresolved-import")
        plan = RuntimeWitnessPlan(
            diagnostic_identities=(increment.identity,),
            managed_dependency="demo",
            operation="import-module",
            module="demo",
        )
        static = StaticRegressionEvaluation(
            proposal=prepared.proposal,
            ty=TyCheck(
                process=process_result(exit_code=1),
                diagnostics=(increment,),
            ),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(increment,),
            static_fingerprint=static_fingerprint((increment.identity,)),
            classifications=(
                DiagnosticClassification(
                    diagnostic_identity=increment.identity,
                    classification="strong",
                    reason_code="witness-planned",
                    witness_plan=plan,
                ),
            ),
        )

        class Witnesses:
            def run(self, **kwargs: object) -> RuntimeWitnessResult | ToolFailure:
                if witness_status == "TOOL_FAILURE":
                    return ToolFailure(
                        cause="TOOL_FAILURE",
                        stage="witness",
                        process=process_result(exit_code=2),
                    )
                return RuntimeWitnessResult.model_validate(
                    {
                        "status": witness_status,
                        "plan": plan,
                        "process": process_result(),
                    }
                )

        verifier = PassingTests()
        result = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            verifier=verifier,
            witnesses=Witnesses(),
        ).evaluate(
            prepared,
            package=ProjectLoader().load(root=Path.cwd()).target,
            baseline=empty_baseline(prepared),
            static_result=static,
        )

        return result.evaluation, verifier.calls, increment

    @pytest.mark.parametrize("witness_status", ("PRESENT", "NOT_APPLICABLE"))
    def test_runtime_evaluator_continues_after_nonterminal_witness(
        self,
        tmp_path: Path,
        witness_status: Literal["PRESENT", "NOT_APPLICABLE"],
    ) -> None:
        result, test_calls, _ = self._evaluate_witness_status(
            tmp_path,
            witness_status,
        )

        assert result.status == "PASS"
        assert test_calls == 1

    def test_runtime_evaluator_stops_after_confirmed_missing_witness(
        self,
        tmp_path: Path,
    ) -> None:
        result, test_calls, increment = self._evaluate_witness_status(
            tmp_path,
            "CONFIRMED_MISSING",
        )

        assert isinstance(result, RuntimeInterfaceMissingEvaluation)
        assert test_calls == 0
        assert result.witnesses[-1].plan.diagnostic_identities == (
            increment.identity,
        )

    def test_runtime_evaluator_stops_after_witness_tool_failure(
        self,
        tmp_path: Path,
    ) -> None:
        result, test_calls, _ = self._evaluate_witness_status(
            tmp_path,
            "TOOL_FAILURE",
        )

        assert isinstance(result, IndeterminateEvaluation)
        assert result.cause == "TOOL_FAILURE"
        assert test_calls == 0

    def test_runtime_evaluator_deduplicates_an_identical_multiset_witness_plan(
        self,
        tmp_path: Path,
    ) -> None:
        prepared = prepared_for_static(tmp_path, "candidate")
        increment = diagnostic("snapshot|demo.py|1|2|unresolved-import")
        plan = RuntimeWitnessPlan(
            diagnostic_identities=(increment.identity,),
            managed_dependency="demo",
            operation="import-module",
            module="demo",
        )
        classification = DiagnosticClassification(
            diagnostic_identity=increment.identity,
            classification="strong",
            reason_code="witness-planned",
            witness_plan=plan,
        )
        static = StaticRegressionEvaluation(
            proposal=prepared.proposal,
            ty=TyCheck(
                process=process_result(exit_code=1),
                diagnostics=(increment, increment),
            ),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(increment, increment),
            static_fingerprint=static_fingerprint(
                (increment.identity, increment.identity)
            ),
            classifications=(classification, classification),
        )

        class Witnesses:
            calls = 0

            def run(self, **kwargs: object) -> RuntimeWitnessResult:
                self.calls += 1
                return RuntimeWitnessResult(
                    status="PRESENT",
                    plan=plan,
                    process=process_result(),
                )

        witnesses = Witnesses()
        result = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            verifier=PassingTests(),
            witnesses=witnesses,
        ).evaluate(
            prepared,
            package=ProjectLoader().load(root=Path.cwd()).target,
            baseline=empty_baseline(prepared),
            static_result=static,
        )

        assert result.evaluation.status == "PASS"
        assert witnesses.calls == 1
        assert len(result.evaluation.witnesses) == 1

    def test_runtime_interface_missing_retains_only_confirmed_witness_issues(
        self,
        tmp_path: Path,
    ) -> None:
        prepared, evaluation, related_one = runtime_interface_missing_evaluation(
            tmp_path
        )

        identities = set(evaluation.witnesses[-1].plan.diagnostic_identities)
        relevant = tuple(
            item
            for item in evaluation.static.incremental
            if item.identity in identities
        )
        assert relevant[0] == related_one
        assert len(relevant) == 2
        prepared.close()


class TestFailurePolicy:
    def test_classify_evaluation_classifies_a_confirmed_missing_interface(
        self,
        tmp_path: Path,
    ) -> None:
        prepared, evaluation, _ = runtime_interface_missing_evaluation(tmp_path)

        failure = FailurePolicy().record_evaluation(
            AttemptFailureScope(attempt=prepared.attempt),
            evaluation,
        )

        assert failure is not None
        assert failure.cause == "RUNTIME_INTERFACE_MISSING"
        assert failure.disposition == "REJECTED"
        prepared.close()


class TestRuntimeEvaluatorOutcomes:
    @staticmethod
    def _evaluate_test_outcome(
        tmp_path: Path,
        outcome: VerifierOutcome,
    ) -> tuple[Evaluation, PreparedEnvironment, Path | None]:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    command-cwd = "root"
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)

        class Tests:
            def __init__(self) -> None:
                self.cwd: Path | None = None

            def run(
                self,
                request: VerifierRequest,
                progress: Callable[[StageProgress | None], None] | None = None,
            ) -> VerifierRun:
                del progress
                self.cwd = request.cwd
                return VerifierRun(authoritative=outcome)

        tests = Tests()
        result = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            verifier=tests,
        ).evaluate(prepared, package=package, baseline=empty_baseline(prepared))

        return result.evaluation, prepared, tests.cwd

    def test_runtime_evaluator_preserves_test_pass(self, tmp_path: Path) -> None:
        result, prepared, test_cwd = self._evaluate_test_outcome(
            tmp_path,
            VerifierPass(terminal=NormalExit(exit_code=0)),
        )

        assert result.status == "PASS"
        assert prepared.tested is True
        assert test_cwd == prepared.proposal_root

    def test_runtime_evaluator_preserves_test_failure(self, tmp_path: Path) -> None:
        result, prepared, test_cwd = self._evaluate_test_outcome(
            tmp_path,
            VerifierRejected(terminal=NormalExit(exit_code=1)),
        )

        assert result.status == "VERIFIER_REJECTED"
        assert prepared.tested is True
        assert test_cwd == prepared.proposal_root

    def test_runtime_evaluator_preserves_indeterminate_test_tool(
        self,
        tmp_path: Path,
    ) -> None:
        result, prepared, test_cwd = self._evaluate_test_outcome(
            tmp_path,
            VerifierIndeterminate(
                terminal=TimedOut(),
                reason="process-timed-out",
            ),
        )

        assert isinstance(result, IndeterminateEvaluation)
        assert result.cause == "TIMEOUT"
        assert prepared.tested is True
        assert test_cwd == prepared.proposal_root


class TestStaticEvaluatorFailures:
    @staticmethod
    def _tool_failure_case(
        tmp_path: Path,
    ) -> tuple[StaticEvaluator, PreparedEnvironment, PackagePlan]:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)

        class FailingTool:
            def check(self, **kwargs: object) -> ToolFailure:
                return ToolFailure(
                    cause="TOOL_FAILURE",
                    stage="ty",
                    process=process_result(exit_code=2),
                )

        evaluator = StaticEvaluator(FailingTool())

        return evaluator, prepared, package

    def test_static_evaluator_evaluate_preserves_tool_failure(
        self,
        tmp_path: Path,
    ) -> None:
        evaluator, prepared, package = self._tool_failure_case(tmp_path)

        result = evaluator.evaluate(
            prepared,
            package=package,
            baseline=empty_baseline(prepared),
        )

        assert isinstance(result, IndeterminateEvaluation)
        assert result.status == "INDETERMINATE"
        assert result.cause == "TOOL_FAILURE"

    def test_static_evaluator_capture_preserves_tool_failure(
        self,
        tmp_path: Path,
    ) -> None:
        evaluator, prepared, package = self._tool_failure_case(tmp_path)

        result = evaluator.capture(prepared, package=package)

        assert isinstance(result, IndeterminateEvaluation)
        assert result.status == "INDETERMINATE"
        assert result.cause == "TOOL_FAILURE"


class TestEvaluationProgress:
    class Events:
        def __init__(self) -> None:
            self.events: list[CellStageEvent] = []

        def consume(self, event: CellStageEvent) -> None:
            self.events.append(event)

    class Tests:
        def run(self, *args: object, **kwargs: object) -> VerifierRun:
            progress = kwargs["progress"]
            assert isinstance(progress, Callable)
            progress(StageProgress(completed=3, total=5, unit="tests"))
            return VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
            )

    @classmethod
    def _progress_case(
        cls,
        tmp_path: Path,
    ) -> tuple[PackagePlan, PreparedEnvironment, Events, StaticEvaluator]:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-m", "unittest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        events = cls.Events()
        static = StaticEvaluator(PassingTy(), events=events)

        return package, prepared, events, static

    def test_static_evaluator_capture_reports_baseline_stage(
        self,
        tmp_path: Path,
    ) -> None:
        package, prepared, events, static = self._progress_case(tmp_path)

        result = static.capture(prepared, package=package)

        assert isinstance(result, StaticBaselineCapture)
        assert [event.stage for event in events.events] == ["capturing static baseline"]
        prepared.close()

    def test_runtime_evaluator_reports_dynamic_stage_and_progress(
        self,
        tmp_path: Path,
    ) -> None:
        package, prepared, events, static = self._progress_case(tmp_path)
        baseline = static.capture(prepared, package=package)
        assert isinstance(baseline, StaticBaselineCapture)
        events.events.clear()

        result = RuntimeEvaluator(
            static=static,
            verifier=self.Tests(),
            events=events,
        ).evaluate(
            prepared,
            package=package,
            baseline=baseline.baseline,
            static_result=baseline.static,
        )

        assert result.evaluation.status == "PASS"
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

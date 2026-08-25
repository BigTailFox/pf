from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import tempfile

import pytest

from conftest import prepared_resolution_evidence

from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
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
    AttemptIdentity,
    CellStageEvent,
    DiagnosticClassification,
    GraphSuccess,
    IndeterminateEvaluation,
    InterpreterSuccess,
    ProcessResult,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessPlan,
    RuntimeWitnessResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticRegressionEvaluation,
    StageProgress,
    TestOutcome,
    TestFail,
    TestPass,
    ToolFailure,
    ToolSuccess,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, InterpreterIdentity, Proposal, ResolvedNode
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

    def run(self, **kwargs: object) -> TestOutcome:
        self.calls += 1
        return TestPass(process=process_result())


class PassingTy:
    def check(self, **kwargs: object) -> TyCheck:
        return TyCheck(process=process_result(), diagnostics=())


class TestEvaluators:
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
                package=ProjectLoader()
                .load(root=Path.cwd(), package_selection=None)
                .packages[0],
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
        package = (
            ProjectLoader().load(root=Path.cwd(), package_selection=None).packages[0]
        )
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
        package = ProjectLoader().load(root=root, package_selection=None).packages[0]
        snapshot = SnapshotBuilder.without_processes().build(root)
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        tests = PassingTests()
        evaluator = RuntimeEvaluator(
            static=StaticEvaluator(FailingTy()),
            tests=tests,
        )
        baseline_check = TyCheck(process=process_result(exit_code=1), diagnostics=())
        baseline = StaticBaseline(
            proposal=prepared.proposal,
            ty=baseline_check,
            digest=ty_diagnostic_digest(baseline_check.diagnostics),
        )

        result = evaluator.evaluate(prepared, package=package, baseline=baseline)

        assert result.status == "PASS"
        assert result.static.status == "STATIC_REGRESSION"
        assert tests.calls == 1
        assert prepared.tested is True

    @pytest.mark.parametrize(
        ("witness_status", "expected", "test_calls"),
        (
            ("PRESENT", "PASS", 1),
            ("NOT_APPLICABLE", "PASS", 1),
            ("CONFIRMED_MISSING", "RUNTIME_INTERFACE_MISSING", 0),
            ("TOOL_FAILURE", "INDETERMINATE", 0),
        ),
    )
    def test_runtime_evaluator_routes_structured_witness_results(
        self,
        tmp_path: Path,
        witness_status: str,
        expected: str,
        test_calls: int,
    ) -> None:
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

        tests = PassingTests()
        result = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            tests=tests,
            witnesses=Witnesses(),
        ).evaluate(
            prepared,
            package=ProjectLoader()
            .load(root=Path.cwd(), package_selection=None)
            .packages[0],
            baseline=empty_baseline(prepared),
            static_result=static,
        )

        assert result.status == expected
        assert tests.calls == test_calls
        if expected == "RUNTIME_INTERFACE_MISSING":
            assert isinstance(result, RuntimeInterfaceMissingEvaluation)

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
            tests=PassingTests(),
            witnesses=witnesses,
        ).evaluate(
            prepared,
            package=ProjectLoader()
            .load(root=Path.cwd(), package_selection=None)
            .packages[0],
            baseline=empty_baseline(prepared),
            static_result=static,
        )

        assert result.status == "PASS"
        assert witnesses.calls == 1
        assert len(result.witnesses) == 1

    @pytest.mark.parametrize(
        ("outcome", "expected"),
        (
            (TestPass(process=process_result()), "PASS"),
            (TestFail(process=process_result(exit_code=1)), "TEST_FAIL"),
            (
                ToolFailure(
                    cause="TIMEOUT",
                    stage="test",
                    process=ProcessResult(
                        exit_code=None,
                        signal=9,
                        duration_seconds=1,
                        stdout="",
                        stderr="",
                        timed_out=True,
                    ),
                ),
                "INDETERMINATE",
            ),
        ),
    )
    def test_full_evaluator_preserves_complete_test_outcomes(
        self,
        tmp_path: Path,
        outcome: TestOutcome,
        expected: str,
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
    command-cwd = "root"
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root, package_selection=None).packages[0]
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)

        class Tests:
            def __init__(self) -> None:
                self.cwd: Path | None = None

            def run(self, **kwargs: object) -> TestOutcome:
                cwd = kwargs["cwd"]
                assert isinstance(cwd, Path)
                self.cwd = cwd
                return outcome

        tests = Tests()
        result = RuntimeEvaluator(
            static=StaticEvaluator(PassingTy()),
            tests=tests,
        ).evaluate(prepared, package=package, baseline=empty_baseline(prepared))

        assert result.status == expected
        assert prepared.tested is True
        assert tests.cwd == prepared.proposal_root

    def test_static_evaluator_preserves_tool_failure(self, tmp_path: Path) -> None:
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
        package = ProjectLoader().load(root=root, package_selection=None).packages[0]
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
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
        evaluated = evaluator.evaluate(
            prepared,
            package=package,
            baseline=empty_baseline(prepared),
        )
        result = evaluator.capture(prepared, package=package)

        assert isinstance(evaluated, IndeterminateEvaluation)
        assert evaluated.status == "INDETERMINATE"
        assert evaluated.cause == "TOOL_FAILURE"
        assert isinstance(result, IndeterminateEvaluation)
        assert result.status == "INDETERMINATE"
        assert result.cause == "TOOL_FAILURE"

    def test_evaluators_report_static_and_dynamic_stages(self, tmp_path: Path) -> None:
        class Events:
            def __init__(self) -> None:
                self.events: list[CellStageEvent] = []

            def consume(self, event: CellStageEvent) -> None:
                self.events.append(event)

        class Tests:
            def run(self, **kwargs: object) -> TestOutcome:
                progress = kwargs["progress"]
                assert isinstance(progress, Callable)
                progress(StageProgress(completed=3, total=5, unit="tests"))
                return TestPass(process=process_result())

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
        package = ProjectLoader().load(root=root, package_selection=None).packages[0]
        prepared = EnvironmentFactory(PreparedUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=SnapshotBuilder.without_processes().build(root),
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        events = Events()

        static = StaticEvaluator(PassingTy(), events=events)
        capture = static.capture(prepared, package=package)
        assert isinstance(capture, StaticBaselineCapture)
        result = RuntimeEvaluator(
            static=static,
            tests=Tests(),
            events=events,
        ).evaluate(
            prepared,
            package=package,
            baseline=capture.baseline,
            static_result=capture.static,
        )

        assert result.status == "PASS"
        assert [event.stage for event in events.events] == [
            "capturing static baseline",
            "dynamic tests",
            "dynamic tests",
        ]
        assert events.events[-1].progress == StageProgress(
            completed=3,
            total=5,
            unit="tests",
        )
        prepared.close()

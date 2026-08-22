from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.evaluation import FullEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    CellStageEvent,
    GraphSuccess,
    IndeterminateEvaluation,
    InterpreterSuccess,
    ProcessResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticFailEvaluation,
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
    def create_environment(self, **kwargs: object) -> ToolSuccess:
        return ToolSuccess(stage="create-environment", process=process_result())

    def install_editable(self, **kwargs: object) -> ToolSuccess:
        return ToolSuccess(stage="install", process=process_result())

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

    def install_requirements(self, **kwargs: object) -> ToolSuccess:
        return ToolSuccess(stage="install-harness", process=process_result())


class FailingTy:
    def check(self, **kwargs: object) -> TyCheck:
        return TyCheck(
            process=process_result(exit_code=1),
            diagnostics=(diagnostic("snapshot|demo.py|1|2|invalid-type"),),
        )


class ExplodingTests:
    def run(self, **kwargs: object) -> TestOutcome:
        raise AssertionError("tests must not run after a static failure")


class PassingTy:
    def check(self, **kwargs: object) -> TyCheck:
        return TyCheck(process=process_result(), diagnostics=())


class TestEvaluators:
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

        assert capture.static.status == "STATIC_PASS"
        assert capture.static.incremental == ()
        assert capture.static.ty is capture.baseline.ty
        assert capture.static.baseline_digest == capture.baseline.digest
        assert isinstance(result, StaticFailEvaluation)
        assert [item.identity for item in result.incremental] == [shifted.identity]
        assert result.baseline_digest == capture.baseline.digest

    def test_full_evaluator_short_circuits_on_static_failure(
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
        evaluator = FullEvaluator(
            static=StaticEvaluator(FailingTy()),
            tests=ExplodingTests(),
        )
        baseline_check = TyCheck(process=process_result(exit_code=1), diagnostics=())
        baseline = StaticBaseline(
            proposal=prepared.proposal,
            ty=baseline_check,
            digest=ty_diagnostic_digest(baseline_check.diagnostics),
        )

        result = evaluator.evaluate(prepared, package=package, baseline=baseline)

        assert result.status == "STATIC_FAIL"
        assert prepared.tested is False

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
        result = FullEvaluator(
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
                self.phases: list[str] = []

            def consume(self, event: CellStageEvent) -> None:
                self.phases.append(event.stage)

        class Tests:
            def run(self, **kwargs: object) -> TestOutcome:
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
        result = FullEvaluator(
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
        assert events.phases == ["capturing static baseline", "dynamic tests"]
        prepared.close()

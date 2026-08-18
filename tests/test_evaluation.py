from __future__ import annotations

from pathlib import Path

import pytest

from pf.environment import EnvironmentFactory, PreparedEnvironment
from pf.evaluation import FullEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    GraphSuccess,
    InterpreterSuccess,
    ProcessResult,
    TestOutcome,
    TestFail,
    TestPass,
    ToolFailure,
    ToolSuccess,
    TyFail,
    TyPass,
)
from pf.schemas.project import InterpreterIdentity, ResolvedNode
from pf.snapshot import SnapshotBuilder


def process_result(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="",
        stderr_summary="",
        stdout_tail="",
        stderr_tail="",
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
    def check(self, **kwargs: object) -> TyFail:
        return TyFail(process=process_result(exit_code=1))


class ExplodingTests:
    def run(self, **kwargs: object) -> TestOutcome:
        raise AssertionError("tests must not run after a static failure")


def test_full_evaluator_short_circuits_on_static_failure(tmp_path: Path) -> None:
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
    snapshot = SnapshotBuilder().build(root)
    prepared = EnvironmentFactory(PreparedUv()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )
    assert isinstance(prepared, PreparedEnvironment)
    evaluator = FullEvaluator(
        static=StaticEvaluator(FailingTy()),
        tests=ExplodingTests(),
    )

    result = evaluator.evaluate(prepared, package=package)

    assert result.status == "STATIC_FAIL"
    assert prepared.tested is False


class PassingTy:
    def check(self, **kwargs: object) -> TyPass:
        return TyPass(process=process_result())


@pytest.mark.parametrize(
    ("outcome", "expected"),
    (
        (TestPass(process=process_result()), "PASS"),
        (TestFail(process=process_result(exit_code=1)), "TEST_FAIL"),
        (
            ToolFailure(
                status="TIMEOUT",
                stage="test",
                process=ProcessResult(
                    exit_code=None,
                    signal=9,
                    duration_seconds=1,
                    stdout_summary="",
                    stderr_summary="",
                    stdout_tail="",
                    stderr_tail="",
                    timed_out=True,
                ),
            ),
            "TIMEOUT",
        ),
    ),
)
def test_full_evaluator_preserves_complete_test_outcomes(
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
        snapshot=SnapshotBuilder().build(root),
        resolution="highest",
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
    ).evaluate(prepared, package=package)

    assert result.status == expected
    assert prepared.tested is True
    assert tests.cwd == prepared.proposal_root


def test_static_evaluator_preserves_tool_failure(tmp_path: Path) -> None:
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
        snapshot=SnapshotBuilder().build(root),
        resolution="highest",
    )
    assert isinstance(prepared, PreparedEnvironment)

    class FailingTool:
        def check(self, **kwargs: object) -> ToolFailure:
            return ToolFailure(
                status="TOOL_ERROR",
                stage="ty",
                process=process_result(exit_code=2),
            )

    result = StaticEvaluator(FailingTool()).evaluate(prepared, package=package)

    assert result.status == "TOOL_ERROR"

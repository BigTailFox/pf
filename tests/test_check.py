from __future__ import annotations

from pathlib import Path
from typing import Literal

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import TestAdapter
from pf.adapters.ty import TyAdapter
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, PreparedEnvironment
from pf.evaluation import FullEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.schemas.evaluation import Evaluation, ProcessResult, ToolFailure
from pf.schemas.project import Cell, PackagePlan
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot
from pf.workflow import CompatibilityChecker


def test_check_passes_a_minimal_local_package(tmp_path: Path) -> None:
    package_root = tmp_path / "demo"
    (package_root / "src" / "demo").mkdir(parents=True)
    (package_root / "src" / "demo" / "__init__.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (package_root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"

[dependency-groups]
test = []

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    runner = SubprocessRunner()
    uv = UvAdapter(runner)
    checker = CompatibilityChecker(
        environments=EnvironmentFactory(uv),
        evaluator=FullEvaluator(
            static=StaticEvaluator(TyAdapter(runner)),
            tests=TestAdapter(runner),
        ),
    )
    project = ProjectLoader().load(root=package_root, package_selection=None)
    snapshot = SnapshotBuilder(runner).build(package_root)

    result = checker.check(package=project.packages[0], snapshot=snapshot)

    assert result.status == "PASS", result
    assert len(result.evaluations) == 1


def test_check_only_evaluates_cells_for_the_exact_host_target(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = []

[tool.pf]
python = ["3.10"]
platform = ["aarch64-apple-darwin", "x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(tmp_path)

    class Environments:
        def __init__(self) -> None:
            self.targets: list[str] = []

        def prepare(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
            resolution: Literal["highest", "lowest-direct"],
        ) -> PreparedEnvironment | ToolFailure:
            self.targets.append(cell.target)
            return ToolFailure(
                status="TOOL_ERROR",
                stage="prepare",
                process=ProcessResult(
                    exit_code=None,
                    signal=None,
                    duration_seconds=0,
                    stdout_summary="",
                    stderr_summary="failure",
                    stdout_tail="",
                    stderr_tail="failure",
                    start_error="failure",
                ),
            )

    class Evaluator:
        def evaluate(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
        ) -> Evaluation:
            raise AssertionError("failed prepare must short-circuit evaluation")

    environments = Environments()
    checker = CompatibilityChecker(
        environments=environments,
        evaluator=Evaluator(),
        host_target="x86_64-unknown-linux-gnu",
    )

    checker.check(package=package, snapshot=snapshot)

    assert environments.targets == ["x86_64-unknown-linux-gnu"]

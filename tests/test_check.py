from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Literal, cast

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import TestAdapter
from pf.adapters.ty import TyAdapter
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, PreparedEnvironment
from pf.evaluation import FullEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.schemas.config import CheckRequest
from pf.schemas.evaluation import (
    CheckIndeterminate,
    CheckPass,
    CheckResult,
    Evaluation,
    ProcessResult,
    ToolFailure,
)
from pf.schemas.evaluation import (
    IndeterminateEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TyFail,
    TyPass,
)
from pf.schemas.project import Cell, PackagePlan, Proposal
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot
from pf.errors import ConfigurationError
from pf.workflow import CheckCommandWorkflow, CompatibilityChecker


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


def write_check_project(
    tmp_path: Path,
    *,
    test_command: bool = True,
    test_group: bool = True,
) -> tuple[PackagePlan, SourceSnapshot]:
    group = "[dependency-groups]\ntest = []\n" if test_group else ""
    command = 'test-command = ["pytest"]\n' if test_command else ""
    (tmp_path / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"

{group}
[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
{command}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
    return package, SnapshotBuilder().build(tmp_path)


@pytest.mark.parametrize(
    ("test_command", "test_group", "host", "message"),
    (
        (False, True, "x86_64-unknown-linux-gnu", "test-command is required"),
        (True, False, "x86_64-unknown-linux-gnu", "test dependency group is required"),
        (True, True, "aarch64-apple-darwin", "no configured cell matches"),
    ),
)
def test_check_rejects_an_incomplete_execution_contract(
    tmp_path: Path,
    test_command: bool,
    test_group: bool,
    host: str,
    message: str,
) -> None:
    package, snapshot = write_check_project(
        tmp_path,
        test_command=test_command,
        test_group=test_group,
    )

    class NeverEnvironments:
        def prepare(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
            resolution: Literal["highest", "lowest-direct"],
        ) -> PreparedEnvironment | ToolFailure:
            raise AssertionError("invalid configuration must fail before preparation")

    class NeverEvaluator:
        def evaluate(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
        ) -> Evaluation:
            raise AssertionError("invalid configuration must fail before evaluation")

    with pytest.raises(ConfigurationError, match=message):
        CompatibilityChecker(
            environments=NeverEnvironments(),
            evaluator=NeverEvaluator(),
            host_target=host,
        ).check(package=package, snapshot=snapshot)


@pytest.mark.parametrize(
    ("evaluation_status", "expected"),
    (
        ("STATIC_FAIL", "COMPATIBILITY_FAILED"),
        ("TEST_FAIL", "COMPATIBILITY_FAILED"),
        ("TOOL_ERROR", "INDETERMINATE"),
    ),
)
def test_check_preserves_compatibility_and_indeterminate_outcomes(
    tmp_path: Path,
    evaluation_status: str,
    expected: str,
) -> None:
    package, snapshot = write_check_project(tmp_path)

    class Environments:
        def prepare(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
            resolution: Literal["highest", "lowest-direct"],
        ) -> PreparedEnvironment:
            directory = tempfile.TemporaryDirectory(prefix="pf-check-test-")
            root = Path(directory.name)
            return PreparedEnvironment(
                proposal=Proposal(
                    proposal_id="proposal",
                    snapshot_digest=snapshot.identity.digest,
                    cell=cell,
                    managed_vector=(),
                    fixed_declaration_ids=(),
                    resolved_graph=(),
                    policy_identity="policy",
                ),
                proposal_root=root,
                package_root=root,
                environment_root=root,
                interpreter=root / "python",
                temporary_directory=directory,
            )

    class Evaluator:
        def evaluate(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
        ) -> Evaluation:
            process = ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0,
                stdout_summary="",
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
            )
            if evaluation_status == "STATIC_FAIL":
                return StaticFailEvaluation(
                    proposal=prepared.proposal,
                    ty=TyFail(process=process),
                )
            if evaluation_status == "TEST_FAIL":
                static = StaticPassEvaluation(
                    proposal=prepared.proposal,
                    ty=TyPass(process=process.model_copy(update={"exit_code": 0})),
                )
                return TestFailEvaluation(
                    proposal=prepared.proposal,
                    static=static,
                    test=TestFail(process=process),
                )
            failure = ToolFailure(status="TOOL_ERROR", stage="ty", process=process)
            return IndeterminateEvaluation(
                status="TOOL_ERROR",
                proposal=prepared.proposal,
                failure=failure,
            )

    result = CompatibilityChecker(
        environments=Environments(),
        evaluator=Evaluator(),
        host_target="x86_64-unknown-linux-gnu",
    ).check(package=package, snapshot=snapshot)

    assert result.status == expected


@pytest.mark.parametrize("indeterminate", (False, True))
def test_check_workflow_returns_the_aggregate_or_first_failure(
    tmp_path: Path,
    indeterminate: bool,
) -> None:
    write_check_project(tmp_path)
    failure = ToolFailure(
        status="TOOL_ERROR",
        stage="prepare",
        process=ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0,
            stdout_summary="",
            stderr_summary="failure",
            stdout_tail="",
            stderr_tail="failure",
        ),
    )

    class Checker:
        def check(
            self,
            *,
            package: PackagePlan,
            snapshot: SourceSnapshot,
        ) -> CheckResult:
            if indeterminate:
                return CheckIndeterminate(failure=failure)
            return CheckPass(evaluations=())

    result = CheckCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        checker=cast(CompatibilityChecker, Checker()),
    ).run(CheckRequest(root=tmp_path.as_posix()))

    assert result.status == ("INDETERMINATE" if indeterminate else "PASS")

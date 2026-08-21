from __future__ import annotations

from pathlib import Path
from threading import Lock
import tempfile
import time
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
    CellMatrixEvent,
    Evaluation,
    PassEvaluation,
    ProcessResult,
    ProgressEvent,
    StaticBaseline,
    StaticBaselineCapture,
    StatusEvent,
    TestPass,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.schemas.evaluation import (
    IndeterminateEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    StaticEvaluation,
    TestFail,
    TestFailEvaluation,
)
from pf.schemas.project import Cell, PackagePlan, Proposal
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot
from pf.errors import ConfigurationError
from pf.scheduling import Scheduler
from pf.workflow import CheckCommandWorkflow, CompatibilityChecker


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


def tool_failure() -> ToolFailure:
    return ToolFailure(
        cause="TOOL_FAILURE",
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


def passing_check(cell: Cell) -> PassEvaluation:
    process = ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0,
        stdout_summary="",
        stderr_summary="",
        stdout_tail="",
        stderr_tail="",
    )
    proposal = Proposal(
        proposal_id="proposal",
        snapshot_digest="snapshot",
        cell=cell,
        managed_vector=(),
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
    )
    return PassEvaluation(
        proposal=proposal,
        static=StaticPassEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(),
        ),
        test=TestPass(process=process),
    )


def test_compatibility_checker_captures_highest_before_testing_lowest_direct(
    tmp_path: Path,
) -> None:
    package, snapshot = write_check_project(tmp_path)
    resolutions: list[str] = []
    prepared: dict[str, PreparedEnvironment] = {}

    class Environments:
        def prepare(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
            resolution: Literal["highest", "lowest-direct"],
        ) -> PreparedEnvironment:
            resolutions.append(resolution)
            temporary = tempfile.TemporaryDirectory(prefix=f"pf-check-{resolution}-")
            root = Path(temporary.name)
            source = root / "source"
            environment = root / "environment"
            source.mkdir()
            value = PreparedEnvironment(
                proposal=Proposal(
                    proposal_id=resolution,
                    snapshot_digest=snapshot.identity.digest,
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
            prepared[resolution] = value
            return value

    process = ProcessResult(
        exit_code=1,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="[]",
        stderr_summary="",
        stdout_tail="[]",
        stderr_tail="",
    )

    class Static:
        def capture(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
        ) -> StaticBaselineCapture:
            check = TyCheck(process=process, diagnostics=())
            baseline = StaticBaseline(
                proposal=prepared.proposal,
                ty=check,
                digest=ty_diagnostic_digest(check.diagnostics),
            )
            return StaticBaselineCapture(
                baseline=baseline,
                static=StaticPassEvaluation(
                    proposal=prepared.proposal,
                    ty=check,
                    baseline_digest=baseline.digest,
                    incremental=(),
                ),
            )

    class Full:
        def evaluate(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
            baseline: StaticBaseline,
            static_result: StaticEvaluation | None = None,
        ) -> PassEvaluation:
            assert prepared.proposal.proposal_id == "lowest-direct"
            assert baseline.proposal.proposal_id == "highest"
            assert static_result is None
            prepared.mark_tested()
            static = StaticPassEvaluation(
                proposal=prepared.proposal,
                ty=TyCheck(process=process, diagnostics=()),
                baseline_digest=baseline.digest,
                incremental=(),
            )
            return PassEvaluation(
                proposal=prepared.proposal,
                static=static,
                test=TestPass(process=process.model_copy(update={"exit_code": 0})),
            )

    result = CompatibilityChecker(
        environments=Environments(),
        static=Static(),
        full=Full(),
    ).check(package=package, cell=package.cells[0], snapshot=snapshot)

    assert result.status == "PASS"
    assert resolutions == ["highest", "lowest-direct"]
    assert prepared["highest"].tested is False
    assert prepared["lowest-direct"].tested is True


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
    static = StaticEvaluator(TyAdapter(runner))
    checker = CompatibilityChecker(
        environments=EnvironmentFactory(uv),
        static=static,
        full=FullEvaluator(
            static=static,
            tests=TestAdapter(runner),
        ),
    )
    project = ProjectLoader().load(root=package_root, package_selection=None)
    snapshot = SnapshotBuilder(runner).build(package_root)

    result = checker.check(
        package=project.packages[0],
        cell=project.packages[0].cells[0],
        snapshot=snapshot,
    )

    assert result.status == "PASS", result


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
    seen: list[str] = []

    class Checker:
        def check(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
        ) -> ToolFailure:
            seen.append(cell.target)
            return tool_failure()

    CheckCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        checker=cast(CompatibilityChecker, Checker()),
        scheduler=Scheduler(),
        events=Events(),
        host_target="x86_64-unknown-linux-gnu",
    ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

    assert seen == ["x86_64-unknown-linux-gnu"]


def test_check_reports_progress_for_each_host_cell(tmp_path: Path) -> None:
    write_check_project(tmp_path)

    class Checker:
        def check(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
        ) -> ToolFailure:
            return tool_failure()

    events = Events()
    CheckCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        checker=cast(CompatibilityChecker, Checker()),
        scheduler=Scheduler(),
        events=events,
        host_target="x86_64-unknown-linux-gnu",
    ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

    progress = [event for event in events.items if isinstance(event, ProgressEvent)]
    assert [(event.message, event.completed, event.total) for event in progress] == [
        ("running", 0, 1),
        ("FAILURE", 1, 1),
    ]


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
    write_check_project(
        tmp_path,
        test_command=test_command,
        test_group=test_group,
    )

    class NeverChecker:
        def check(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
        ) -> Evaluation:
            raise AssertionError("invalid configuration must fail before evaluation")

    with pytest.raises(ConfigurationError, match=message):
        CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder(),
            checker=cast(CompatibilityChecker, NeverChecker()),
            scheduler=Scheduler(),
            events=Events(),
            host_target=host,
        ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))


@pytest.mark.parametrize(
    "evaluation_status",
    ("STATIC_FAIL", "TEST_FAIL", "INDETERMINATE"),
)
def test_check_preserves_compatibility_and_indeterminate_outcomes(
    tmp_path: Path,
    evaluation_status: str,
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

    class Static:
        def capture(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
        ) -> StaticBaselineCapture:
            process = ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0,
                stdout_summary="[]",
                stderr_summary="",
                stdout_tail="[]",
                stderr_tail="",
            )
            check = TyCheck(process=process, diagnostics=())
            baseline = StaticBaseline(
                proposal=prepared.proposal,
                ty=check,
                digest=ty_diagnostic_digest(check.diagnostics),
            )
            return StaticBaselineCapture(
                baseline=baseline,
                static=StaticPassEvaluation(
                    proposal=prepared.proposal,
                    ty=check,
                    baseline_digest=baseline.digest,
                    incremental=(),
                ),
            )

    class Full:
        def evaluate(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
            baseline: StaticBaseline,
            static_result: object | None = None,
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
                increment = TyDiagnostic(
                    identity="snapshot|demo.py|1|1|invalid-type",
                    origin="snapshot",
                    path="demo.py",
                    line=1,
                    column=1,
                    code="invalid-type",
                    severity="major",
                    message="invalid type",
                )
                check = TyCheck(process=process, diagnostics=(increment,))
                return StaticFailEvaluation(
                    proposal=prepared.proposal,
                    ty=check,
                    baseline_digest=baseline.digest,
                    incremental=(increment,),
                )
            if evaluation_status == "TEST_FAIL":
                static = StaticPassEvaluation(
                    proposal=prepared.proposal,
                    ty=TyCheck(
                        process=process.model_copy(update={"exit_code": 0}),
                        diagnostics=(),
                    ),
                    baseline_digest=baseline.digest,
                    incremental=(),
                )
                return TestFailEvaluation(
                    proposal=prepared.proposal,
                    static=static,
                    test=TestFail(process=process),
                )
            failure = ToolFailure(cause="TOOL_FAILURE", stage="ty", process=process)
            return IndeterminateEvaluation(
                proposal=prepared.proposal,
                cause="TOOL_FAILURE",
                failure=failure,
            )

    result = CompatibilityChecker(
        environments=Environments(),
        static=Static(),
        full=Full(),
    ).check(package=package, cell=package.cells[0], snapshot=snapshot)

    assert result.status == evaluation_status


@pytest.mark.parametrize("indeterminate", (False, True))
def test_check_workflow_returns_the_aggregate_or_first_failure(
    tmp_path: Path,
    indeterminate: bool,
) -> None:
    write_check_project(tmp_path)

    class Checker:
        def check(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
        ) -> PassEvaluation | ToolFailure:
            if indeterminate:
                return tool_failure()
            return passing_check(cell)

    events = Events()
    result = CheckCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        checker=cast(CompatibilityChecker, Checker()),
        scheduler=Scheduler(),
        events=events,
        host_target="x86_64-unknown-linux-gnu",
    ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

    assert result.status == ("INDETERMINATE" if indeterminate else "PASS")
    assert [
        event.message for event in events.items if isinstance(event, StatusEvent)
    ] == ["loading project", "building snapshot", "checking declarations"]
    matrix = next(event for event in events.items if isinstance(event, CellMatrixEvent))
    assert [cell.python_minor for cell in matrix.cells] == ["3.10"]
    assert [cell.target for cell in matrix.cells] == ["x86_64-unknown-linux-gnu"]


def test_check_workflow_emits_every_feasible_host_cell(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
optional-dependencies = {cuda = ["idna"]}

[dependency-groups]
test = []

[tool.pf]
python = ["3.10", "3.11"]
platform = ["x86_64-unknown-linux-gnu", "aarch64-apple-darwin"]
extras = "each"
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    class Checker:
        def check(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
        ) -> ToolFailure:
            return tool_failure()

    events = Events()
    CheckCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        checker=cast(CompatibilityChecker, Checker()),
        scheduler=Scheduler(),
        events=events,
        host_target="x86_64-unknown-linux-gnu",
    ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

    matrix = next(event for event in events.items if isinstance(event, CellMatrixEvent))
    assert [
        (cell.python_minor, cell.target, cell.extra_surface) for cell in matrix.cells
    ] == [
        ("3.10", "x86_64-unknown-linux-gnu", ()),
        ("3.10", "x86_64-unknown-linux-gnu", ("cuda",)),
        ("3.11", "x86_64-unknown-linux-gnu", ()),
        ("3.11", "x86_64-unknown-linux-gnu", ("cuda",)),
    ]


def test_check_workflow_runs_host_cells_in_parallel(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = []

[tool.pf]
python = ["3.10", "3.11"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    lock = Lock()
    active = 0
    maximum_active = 0
    seen: list[str] = []

    class Checker:
        def check(
            self,
            *,
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
        ) -> ToolFailure:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                seen.append(cell.python_minor)
            time.sleep(0.05)
            with lock:
                active -= 1
            return ToolFailure(
                cause="TOOL_FAILURE",
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

    class Events:
        def __init__(self) -> None:
            self.items: list[object] = []

        def consume(self, event: object) -> None:
            self.items.append(event)

    events = Events()
    result = CheckCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        checker=cast(CompatibilityChecker, Checker()),
        scheduler=Scheduler(),
        events=events,
        host_target="x86_64-unknown-linux-gnu",
    ).run(CheckRequest(root=tmp_path.as_posix(), jobs=2))

    assert maximum_active == 2
    assert sorted(seen) == ["3.10", "3.11"]
    assert result.status == "INDETERMINATE"
    progress = [event for event in events.items if isinstance(event, ProgressEvent)]
    assert [event.phase for event in progress[:2]] == ["start", "start"]
    assert sorted(event.cell.python_minor for event in progress[:2]) == ["3.10", "3.11"]

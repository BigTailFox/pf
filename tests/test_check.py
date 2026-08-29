from __future__ import annotations

from pathlib import Path
from threading import Lock
import tempfile
import time
from typing import Literal, cast

import pytest

from conftest import prepared_resolution_evidence

from pf.environment import (
    HighestResolution,
    LowestDirectResolution,
    PreparedEnvironment,
    ResolutionRequest,
)
from pf.failure import FailurePolicy
from pf.project import ProjectLoader
from pf.schemas.config import CheckRequest
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineDetailIdentity,
    CellCompletedEvent,
    CellContextEvent,
    CellMatrixEvent,
    CheckCellOutcome,
    DeclarationDetailIdentity,
    Evaluation,
    NormalExit,
    PassEvaluation,
    PrepareFailure,
    ProcessResult,
    RuntimeEvaluationRun,
    StaticBaseline,
    StaticBaselineCapture,
    StatusEvent,
    ToolFailure,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.evaluation import (
    IndeterminateEvaluation,
    StaticUnchangedEvaluation,
    StaticEvaluation,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
)
from pf.schemas.project import Cell, PackagePlan, Proposal
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot
from pf.errors import ConfigurationError
from pf.verification import VerificationRunner
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
            stdout="",
            stderr="failure",
        ),
    )


def passing_check(cell: Cell) -> PassEvaluation:
    process = ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0,
        stdout="",
        stderr="",
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
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(),
        ),
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )


def attempt_for(
    cell: Cell,
    *,
    resolution: Literal["highest", "lowest-direct"] = "lowest-direct",
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=None,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )


def resolution_kind(
    resolution: ResolutionRequest,
) -> Literal["highest", "lowest-direct"]:
    assert isinstance(resolution, (HighestResolution, LowestDirectResolution))
    return resolution.kind


def passing_outcome(cell: Cell) -> CheckCellOutcome:
    evaluation = passing_check(cell)
    return CheckCellOutcome(
        status="PASS",
        role="declaration",
        attempt=attempt_for(cell),
        evaluation=evaluation,
    )


def indeterminate_outcome(cell: Cell) -> CheckCellOutcome:
    attempt = attempt_for(cell)
    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="TOOL_FAILURE",
        stage="prepare",
        process=tool_failure().process,
    )
    return CheckCellOutcome(
        status=failure.disposition,
        role="declaration",
        attempt=attempt,
        failure=failure,
    )


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
    package = ProjectLoader().load(root=tmp_path).target
    return package, SnapshotBuilder.without_processes().build(tmp_path)


class TestCompatibilityChecker:
    def test_compatibility_checker_captures_highest_before_testing_lowest_direct(
        self,
        tmp_path: Path,
    ) -> None:
        package, snapshot = write_check_project(tmp_path)
        resolutions: list[str] = []
        prepared: dict[str, PreparedEnvironment] = {}
        events = Events()

        class Environments:
            def prepare(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                resolution: ResolutionRequest,
                source_mode: object,
            ) -> PreparedEnvironment:
                kind = resolution_kind(resolution)
                resolutions.append(kind)
                temporary = tempfile.TemporaryDirectory(prefix=f"pf-check-{kind}-")
                root = Path(temporary.name)
                source = root / "source"
                environment = root / "environment"
                source.mkdir()
                attempt = Attempt.from_identity(
                    AttemptIdentity(
                        source_snapshot_digest=snapshot.identity.digest,
                        cell=cell,
                        requested_resolution=kind,
                        requested_managed_vector=None,
                        active_declaration_ids=cell.active_declaration_ids,
                        source_plan_identity="sources",
                        evaluation_policy_identity="policy",
                    )
                )
                value = PreparedEnvironment(
                    attempt=attempt,
                    proposal=Proposal(
                        proposal_id=kind,
                        attempt_id=attempt.attempt_id,
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
                    **prepared_resolution_evidence(cell=cell),
                    temporary_directory=temporary,
                )
                prepared[kind] = value
                return value

        process = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="[]",
            stderr="",
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
                    static=StaticUnchangedEvaluation(
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
            ) -> RuntimeEvaluationRun:
                assert prepared.proposal.proposal_id == "lowest-direct"
                assert baseline.proposal.proposal_id == "highest"
                assert static_result is None
                prepared.mark_tested()
                static = StaticUnchangedEvaluation(
                    proposal=prepared.proposal,
                    ty=TyCheck(process=process, diagnostics=()),
                    baseline_digest=baseline.digest,
                    incremental=(),
                )
                return RuntimeEvaluationRun(
                    evaluation=PassEvaluation(
                        proposal=prepared.proposal,
                        static=static,
                        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
                    )
                )

        result = CompatibilityChecker(
            environments=Environments(),
            static=Static(),
            full=Full(),
            events=events,
        ).check(package=package, cell=package.cells[0], snapshot=snapshot, source_mode="SEARCH")

        assert result.status == "PASS"
        assert resolutions == ["highest", "lowest-direct"]
        assert [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ] == [BaselineDetailIdentity(), DeclarationDetailIdentity()]
        assert prepared["highest"].tested is False
        assert prepared["lowest-direct"].tested is True

    def test_check_highest_prepare_failure_does_not_start_lowest_direct(
        self,
        tmp_path: Path,
    ) -> None:
        package, snapshot = write_check_project(tmp_path)
        resolutions: list[str] = []
        cell = package.cells[0]
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution="highest",
                requested_managed_vector=None,
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity="sources",
                evaluation_policy_identity="policy",
            )
        )
        process = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="",
            stderr="Failed to build `numpy==1.24.0`",
        )
        events = Events()

        class Environments:
            def prepare(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                resolution: ResolutionRequest,
                source_mode: object,
            ) -> PrepareFailure:
                kind = resolution_kind(resolution)
                resolutions.append(kind)
                return PrepareFailure(
                    attempt=attempt
                    if kind == "highest"
                    else Attempt.from_identity(
                        attempt.identity.model_copy(
                            update={"requested_resolution": kind}
                        )
                    ),
                    failure=ToolFailure(
                        cause="BUILD_FAILURE",
                        stage="install-project",
                        process=process,
                    ),
                )

        class NeverStatic:
            def capture(
                self, *args: object, **kwargs: object
            ) -> StaticBaselineCapture | IndeterminateEvaluation:
                raise AssertionError(
                    "capture must not run after highest prepare failure"
                )

        class NeverFull:
            def evaluate(self, *args: object, **kwargs: object) -> RuntimeEvaluationRun:
                raise AssertionError(
                    "lowest-direct must not start after capture failure"
                )

        result = CompatibilityChecker(
            environments=Environments(),
            static=NeverStatic(),
            full=NeverFull(),
            events=events,
        ).check(package=package, cell=cell, snapshot=snapshot, source_mode="SEARCH")

        assert resolutions == ["highest"]
        assert result.status == "INDETERMINATE"
        assert result.role == "declaration-capture"
        assert result.attempt.identity.requested_resolution == "highest"
        assert result.failure is not None
        assert result.failure.cause == "BUILD_FAILURE"
        assert result.failure.stage == "install-project"
        assert [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ] == [BaselineDetailIdentity()]

    def test_check_only_evaluates_cells_for_the_exact_host_target(
        self, tmp_path: Path
    ) -> None:
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
                source_mode: object,
            ) -> CheckCellOutcome:
                seen.append(cell.target)
                return indeterminate_outcome(cell)

        CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CompatibilityChecker, Checker()),
            verification=VerificationRunner(events=Events(), logs=None),
            events=Events(),
            host_target="x86_64-unknown-linux-gnu",
        ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

        assert seen == ["x86_64-unknown-linux-gnu"]


class TestCheckWorkflow:
    def test_check_reports_progress_for_each_host_cell(self, tmp_path: Path) -> None:
        write_check_project(tmp_path)

        class Checker:
            def check(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                source_mode: object,
            ) -> CheckCellOutcome:
                return indeterminate_outcome(cell)

        events = Events()
        CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CompatibilityChecker, Checker()),
            verification=VerificationRunner(events=events, logs=None),
            events=events,
            host_target="x86_64-unknown-linux-gnu",
        ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

        progress = [
            event for event in events.items if isinstance(event, CellCompletedEvent)
        ]
        assert [
            (event.outcome.status, event.completed, event.total) for event in progress
        ] == [
            ("INDETERMINATE", 1, 1),
        ]

    @pytest.mark.parametrize(
        ("test_command", "test_group", "host", "message"),
        (
            (False, True, "x86_64-unknown-linux-gnu", "test-command is required"),
            (
                True,
                False,
                "x86_64-unknown-linux-gnu",
                "test dependency group is required",
            ),
            (True, True, "aarch64-apple-darwin", "no configured cell matches"),
        ),
    )
    def test_check_rejects_an_incomplete_execution_contract(
        self,
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
                source_mode: object,
            ) -> Evaluation:
                raise AssertionError(
                    "invalid configuration must fail before evaluation"
                )

        with pytest.raises(ConfigurationError, match=message):
            CheckCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder.without_processes(),
                checker=cast(CompatibilityChecker, NeverChecker()),
                verification=VerificationRunner(events=Events(), logs=None),
                events=Events(),
                host_target=host,
            ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

    @pytest.mark.parametrize(
        "evaluation_status",
        ("VERIFIER_REJECTED", "INDETERMINATE"),
    )
    def test_check_preserves_compatibility_and_indeterminate_outcomes(
        self,
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
                resolution: ResolutionRequest,
                source_mode: object,
            ) -> PreparedEnvironment:
                kind = resolution_kind(resolution)
                directory = tempfile.TemporaryDirectory(prefix="pf-check-test-")
                root = Path(directory.name)
                attempt = Attempt.from_identity(
                    AttemptIdentity(
                        source_snapshot_digest=snapshot.identity.digest,
                        cell=cell,
                        requested_resolution=kind,
                        requested_managed_vector=None,
                        active_declaration_ids=cell.active_declaration_ids,
                        source_plan_identity="sources",
                        evaluation_policy_identity="policy",
                    )
                )
                return PreparedEnvironment(
                    attempt=attempt,
                    proposal=Proposal(
                        proposal_id="proposal",
                        attempt_id=attempt.attempt_id,
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
                    **prepared_resolution_evidence(cell=cell),
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
                    stdout="[]",
                    stderr="",
                )
                check = TyCheck(process=process, diagnostics=())
                baseline = StaticBaseline(
                    proposal=prepared.proposal,
                    ty=check,
                    digest=ty_diagnostic_digest(check.diagnostics),
                )
                return StaticBaselineCapture(
                    baseline=baseline,
                    static=StaticUnchangedEvaluation(
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
            ) -> RuntimeEvaluationRun:
                process = ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0,
                    stdout="",
                    stderr="",
                )
                if evaluation_status == "VERIFIER_REJECTED":
                    static = StaticUnchangedEvaluation(
                        proposal=prepared.proposal,
                        ty=TyCheck(
                            process=process.model_copy(update={"exit_code": 0}),
                            diagnostics=(),
                        ),
                        baseline_digest=baseline.digest,
                        incremental=(),
                    )
                    return RuntimeEvaluationRun(
                        evaluation=VerifierRejectedEvaluation(
                            proposal=prepared.proposal,
                            static=static,
                            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
                        )
                    )
                failure = ToolFailure(cause="TOOL_FAILURE", stage="ty", process=process)
                return RuntimeEvaluationRun(
                    evaluation=IndeterminateEvaluation(
                        proposal=prepared.proposal,
                        cause="TOOL_FAILURE",
                        failure=failure,
                    )
                )

        result = CompatibilityChecker(
            environments=Environments(),
            static=Static(),
            full=Full(),
        ).check(package=package, cell=package.cells[0], snapshot=snapshot, source_mode="SEARCH")

        assert (
            result.status
            == {
                "VERIFIER_REJECTED": "REJECTED",
                "INDETERMINATE": "INDETERMINATE",
            }[evaluation_status]
        )
        assert result.role == "declaration"
        assert result.attempt.identity.requested_resolution == "lowest-direct"
        assert result.failure is not None
        assert (
            result.failure.cause
            == {
                "VERIFIER_REJECTED": "VERIFIER_EXITED_NONZERO",
                "INDETERMINATE": "TOOL_FAILURE",
            }[evaluation_status]
        )
        assert (
            result.failure.stage
            == {
                "VERIFIER_REJECTED": "test",
                "INDETERMINATE": "ty",
            }[evaluation_status]
        )

    @pytest.mark.parametrize("indeterminate", (False, True))
    def test_check_workflow_returns_the_aggregate_or_first_failure(
        self,
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
                source_mode: object,
            ) -> CheckCellOutcome:
                if indeterminate:
                    return indeterminate_outcome(cell)
                return passing_outcome(cell)

        events = Events()
        result = CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CompatibilityChecker, Checker()),
            verification=VerificationRunner(events=events, logs=None),
            events=events,
            host_target="x86_64-unknown-linux-gnu",
        ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

        assert result.status == ("INDETERMINATE" if indeterminate else "PASS")
        assert [
            event.message for event in events.items if isinstance(event, StatusEvent)
        ] == ["loading project", "building snapshot", "checking declarations"]
        matrix = next(
            event for event in events.items if isinstance(event, CellMatrixEvent)
        )
        assert [cell.python_minor for cell in matrix.cells] == ["3.10"]
        assert [cell.target for cell in matrix.cells] == ["x86_64-unknown-linux-gnu"]

    def test_check_workflow_emits_every_feasible_host_cell(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["packaging>=24", "rich==13.0"]
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
                source_mode: object,
            ) -> CheckCellOutcome:
                return indeterminate_outcome(cell)

        events = Events()
        CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CompatibilityChecker, Checker()),
            verification=VerificationRunner(events=events, logs=None),
            events=events,
            host_target="x86_64-unknown-linux-gnu",
        ).run(CheckRequest(root=tmp_path.as_posix(), jobs=1))

        matrix = next(
            event for event in events.items if isinstance(event, CellMatrixEvent)
        )
        assert [
            (cell.python_minor, cell.target, cell.extra_surface)
            for cell in matrix.cells
        ] == [
            ("3.10", "x86_64-unknown-linux-gnu", ()),
            ("3.10", "x86_64-unknown-linux-gnu", ("cuda",)),
            ("3.11", "x86_64-unknown-linux-gnu", ()),
            ("3.11", "x86_64-unknown-linux-gnu", ("cuda",)),
        ]
        assert matrix.active_packages == 3
        assert matrix.pinned_packages == 1

    def test_check_workflow_runs_host_cells_in_parallel(self, tmp_path: Path) -> None:
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
                source_mode: object,
            ) -> CheckCellOutcome:
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    seen.append(cell.python_minor)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return indeterminate_outcome(cell)

        class Events:
            def __init__(self) -> None:
                self.items: list[object] = []

            def consume(self, event: object) -> None:
                self.items.append(event)

        events = Events()
        result = CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CompatibilityChecker, Checker()),
            verification=VerificationRunner(events=events, logs=None),
            events=events,
            host_target="x86_64-unknown-linux-gnu",
        ).run(CheckRequest(root=tmp_path.as_posix(), jobs=2))

        assert maximum_active == 2
        assert sorted(seen) == ["3.10", "3.11"]
        assert result.status == "INDETERMINATE"
        progress = [
            event for event in events.items if isinstance(event, CellCompletedEvent)
        ]
        assert sorted(event.cell.python_minor for event in progress) == [
            "3.10",
            "3.11",
        ]

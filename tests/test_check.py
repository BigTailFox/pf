from __future__ import annotations

from pf.static_cache import TyCheckCache

from pathlib import Path
from threading import Lock
import time
from typing import Literal, cast

import pytest

from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process

from pf.schemas.ty_fact import TyCheckUnavailable
from pf.schemas.static import StaticContentUnavailable
from scripted_static import ScriptedStaticRequests
from pf.evaluation import StaticEvaluator
from pf.check import CompatibilityChecker
from pf.failure import FailurePolicy
from pf.project import ProjectLoader
from pf.schemas.config import CheckRequest
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    CellCompletedEvent,
    CellContextEvent,
    CellMatrixEvent,
    CheckCellOutcome,
    DeclarationDetailIdentity,
    Evaluation,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    StatusEvent,
    ToolFailure,
    OperationFailureResult,
    ExecutionFailure,
    Unattributed,
    TyCheck,
    TyDiagnostic,
)
from pf.schemas.evaluation import (
    TimedOut,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)
from pf.schemas.project import Cell, PackagePlan, Proposal, SourcePlan, VersionPin
from pf.snapshot import SnapshotBuilder
from pf.snapshot import SourceSnapshot
from pf.errors import ConfigurationError
from pf.verification import CheckCellOperations, VerificationRunner
from pf.workflow import CheckCommandWorkflow


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
            execution_policy_identity="policy",
            resolution_context_digest="context",
            harness_policy_identity=(
                "original-harness-v1"
                if resolution == "highest"
                else "harness-relaxation-v1"
            ),
            harness_baseline_digest=(
                None if resolution == "highest" else "baseline"
            ),
        )
    )


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
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
{command}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=tmp_path).target
    return package, SnapshotBuilder.without_processes().build(tmp_path)


class TestCompatibilityChecker:
    @pytest.mark.parametrize("test_command", (False, True))
    def test_compatibility_checker_captures_highest_before_testing_lowest_direct(
        self, run_cache,
        tmp_path: Path,
        test_command: bool,
    ) -> None:
        package, snapshot = write_check_project(tmp_path, test_command=test_command)
        assert package.config.test.command == ("pytest",)
        events = Events()
        assembly = evaluation_assembly(highest=(), lowest=(), events=events)

        result = CompatibilityChecker(
            environments=assembly.environments,
            static=assembly.static,
            full=assembly.runtime,
            events=events,
        ).check(run_cache=run_cache,
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert result.status == "PASS"
        assert assembly.uv.resolutions == ["highest", "lowest-direct"]
        assert assembly.uv.resolution_root_states == [
            ("highest", (True,)),
            ("lowest-direct", (False, True)),
        ]
        assert [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ] == [DeclarationDetailIdentity()]
        assert assembly.ty.vectors == [()]
        assert assembly.verifier.vectors == [()]
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    @pytest.mark.parametrize("uncollected", (None, "highest", "lowest-direct"))
    def test_check_retains_global_multiset_comparison_in_scope(
        self, run_cache, tmp_path: Path, uncollected: str | None,
    ) -> None:
        project = evaluation_project(tmp_path, dependency="demo-dep")
        diagnostic = TyDiagnostic(
            identity="snapshot|demo.py|1|1|invalid-type", origin="snapshot", path="demo.py",
            line=1, column=1, code="invalid-type", severity="major", message="invalid type",
        )
        assembly = evaluation_assembly(
            lowest=(VersionPin(name="demo-dep", version="1"),),
            ty_handler=lambda vector, call: TyCheck(
                process=successful_process(exit_code=1),
                diagnostics=(diagnostic,) * (3 if vector[0].version == "1" else 1),
            ),
        )

        class Requests(ScriptedStaticRequests):
            def capture(self, prepared, **kwargs):
                if prepared.attempt.identity.requested_resolution == uncollected:
                    return StaticContentUnavailable(detail="unreadable-content")
                return super().capture(prepared, **kwargs)

        result = CompatibilityChecker(
            environments=assembly.environments,
            static=StaticEvaluator(assembly.ty, requests=Requests()), full=assembly.runtime,
        ).check(
            package=project.package, cell=project.package.cells[0], snapshot=project.snapshot,
            source_plan=project.source_plan, run_cache=run_cache,
        )
        assert result.status == "PASS"
        assert result.evaluation is not None
        assert result.evaluation.proposal.managed_vector == (VersionPin(name="demo-dep", version="1"),)
        assert len(assembly.verifier.vectors) == 1
        scope = run_cache.snapshot(project.package.cells[0])
        assert len(scope.facts) == (2 if uncollected is None else 1)
        if uncollected == "lowest-direct":
            assert scope.comparisons == ()
            assert scope.passes == ()
        else:
            assert len(scope.comparisons) == 1
            comparison = scope.comparisons[0]
            assert comparison.context.kind == "GLOBAL"
            if uncollected == "highest":
                assert scope.highest_uncollected is not None
                assert scope.highest_uncollected.unavailable.detail == "unreadable-content"
                assert comparison.reference_ref is None
                assert comparison.result.status == "UNCOMPARED"
                assert comparison.result.reason == "reference-unavailable"
            else:
                assert comparison.reference_ref == scope.highest_reference_ref
                assert comparison.result.status == "COMPARED"
                assert comparison.result.state == "STATIC_REGRESSION"
                assert comparison.result.incremental_identities == (diagnostic.identity,) * 2
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_check_preserves_capture_when_lowest_preparation_fails(self, run_cache, tmp_path: Path) -> None:
        project = evaluation_project(tmp_path)
        lowest = (VersionPin(name="demo-dep", version="1"),)
        assembly = evaluation_assembly(
            lowest=lowest,
            ty_handler=lambda vector, call: ToolFailure(
                cause="TOOL_FAILURE", stage="ty", process=successful_process(exit_code=2)
            ),
        )
        assembly.uv.install_failures_by_vector[lowest] = OperationFailureResult(
            failure=ExecutionFailure(terminal=NormalExit(exit_code=2), attribution=Unattributed()),
            stage="install-project", process=successful_process(exit_code=2),
        )
        result = CompatibilityChecker(
            environments=assembly.environments, static=assembly.static, full=assembly.runtime
        ).check(run_cache=run_cache,
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
        )
        assert result.status == "REJECTED"
        assert result.role == "declaration"
        assert result.evaluation is None
        assert result.failure is not None
        assert result.failure.stage == "install-project"
        scope = run_cache.snapshot(project.package.cells[0])
        assert scope.highest_reference_ref is not None
        assert isinstance(scope.facts[0].observation.fact, TyCheckUnavailable)
        assert scope.facts[0].observation.fact.reason == "exit-code"
        assert assembly.uv.resolutions == ["highest", "lowest-direct"]
        assert len(assembly.ty.vectors) == 1
        assert assembly.verifier.vectors == []
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_check_highest_prepare_failure_does_not_start_lowest_direct(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        package, snapshot = write_check_project(tmp_path)
        cell = package.cells[0]
        events = Events()
        assembly = evaluation_assembly(
            highest=(),
            lowest=(),
            install_failure=OperationFailureResult(
                failure=ExecutionFailure(terminal=NormalExit(exit_code=2), attribution=Unattributed()),
                stage="install-project",
                process=successful_process(exit_code=2),
            ),
            events=events,
        )

        result = CompatibilityChecker(
            environments=assembly.environments,
            static=assembly.static,
            full=assembly.runtime,
            events=events,
        ).check(run_cache=run_cache,
            package=package,
            cell=cell,
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert assembly.uv.resolutions == ["highest"]
        assert result.status == "REJECTED"
        assert result.role == "declaration-capture"
        assert result.attempt.identity.requested_resolution == "highest"
        assert result.failure is not None
        assert result.failure.cause == "INSTALLATION_FAILED"
        assert result.failure.stage == "install-project"
        assert assembly.ty.vectors == []
        assert assembly.verifier.vectors == []
        assert all(not root.exists() for root in assembly.uv.environment_roots)
        assert [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ] == []

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
    pythons = ["3.10"]
    platforms = ["aarch64-apple-darwin", "x86_64-unknown-linux-gnu"]
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
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> CheckCellOutcome:
                seen.append(cell.target)
                return indeterminate_outcome(cell)

        CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CheckCellOperations, Checker()),
            verification=VerificationRunner(
                events=Events(),
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=Events(),
        ).run(CheckRequest(root=tmp_path.as_posix(), max_cells=1))

        assert seen == ["x86_64-unknown-linux-gnu"]


class TestCheckWorkflow:
    def test_check_reports_progress_and_closes_snapshot(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_check_project(tmp_path)
        closed: list[SourceSnapshot] = []
        close_snapshot = SourceSnapshot.close

        def close(snapshot: SourceSnapshot) -> None:
            closed.append(snapshot)
            close_snapshot(snapshot)

        monkeypatch.setattr(SourceSnapshot, "close", close)

        class Checker:
            def check(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> CheckCellOutcome:
                return indeterminate_outcome(cell)

        events = Events()
        CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CheckCellOperations, Checker()),
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=events,
        ).run(CheckRequest(root=tmp_path.as_posix(), max_cells=1))

        progress = [
            event for event in events.items if isinstance(event, CellCompletedEvent)
        ]
        assert [
            (event.outcome.status, event.completed, event.total) for event in progress
        ] == [
            ("INDETERMINATE", 1, 1),
        ]
        assert len(closed) == 1

    @pytest.mark.parametrize(
        ("test_command", "test_group", "host", "message"),
        (
            (True, False, "aarch64-apple-darwin", "no configured cell matches"),
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
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> Evaluation:
                raise AssertionError(
                    "invalid configuration must fail before evaluation"
                )

        with pytest.raises(ConfigurationError, match=message):
            CheckCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder.without_processes(),
                checker=cast(CheckCellOperations, NeverChecker()),
                verification=VerificationRunner(
                    events=Events(),
                    logs=None,
                    host_target=host,
                ),
                events=Events(),
            ).run(CheckRequest(root=tmp_path.as_posix(), max_cells=1))

    @pytest.mark.parametrize(
        "evaluation_status",
        ("PASS", "VERIFIER_REJECTED", "INDETERMINATE"),
    )
    @pytest.mark.parametrize("failed_collections", ((), (1,), (2,), (1, 2)))
    def test_check_preserves_configured_verifier_outcomes(
        self, run_cache,
        tmp_path: Path,
        evaluation_status: str,
        failed_collections: tuple[int, ...],
    ) -> None:
        project = evaluation_project(tmp_path, dependency="demo-dep")
        package, snapshot = project.package, project.snapshot
        assembly = evaluation_assembly(
            lowest=(VersionPin(name="demo-dep", version="1"),),
            ty_handler=lambda vector, call: (
                ToolFailure(
                    cause="TOOL_FAILURE",
                    stage="ty",
                    process=successful_process(exit_code=2),
                )
                if call in failed_collections
                else TyCheck(process=successful_process(), diagnostics=())
            ),
            verifier_handler=lambda vector, call: VerifierRun(
                authoritative=(
                    VerifierPass(terminal=NormalExit(exit_code=0))
                    if evaluation_status == "PASS"
                    else VerifierRejected(terminal=NormalExit(exit_code=1))
                    if evaluation_status == "VERIFIER_REJECTED"
                    else VerifierIndeterminate(
                        terminal=TimedOut(), reason="process-timed-out"
                    )
                )
            ),
        )

        result = CompatibilityChecker(
            environments=assembly.environments,
            static=assembly.static,
            full=assembly.runtime,
        ).check(run_cache=run_cache,
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert (
            result.status
            == {
                "PASS": "PASS",
                "VERIFIER_REJECTED": "REJECTED",
                "INDETERMINATE": "INDETERMINATE",
            }[evaluation_status]
        )
        assert result.role == "declaration"
        assert result.attempt.identity.requested_resolution == "lowest-direct"
        assert result.evaluation is not None
        scope = run_cache.snapshot(package.cells[0])
        assert len(scope.facts) == 2
        assert len(scope.comparisons) == 1
        comparison = scope.comparisons[0]
        assert comparison.context.kind == "GLOBAL"
        assert comparison.reference_ref == scope.highest_reference_ref
        if 2 in failed_collections:
            assert comparison.result.status == "UNAVAILABLE"
        elif 1 in failed_collections:
            assert comparison.result.status == "UNCOMPARED"
            assert comparison.result.reason == "reference-unavailable"
        else:
            assert comparison.result.status == "COMPARED"
            assert comparison.result.state == "STATIC_UNCHANGED"
        if evaluation_status == "PASS":
            assert result.failure is None
        else:
            assert result.failure is not None
            assert result.failure.cause == (
                "VERIFIER_EXITED_NONZERO"
                if evaluation_status == "VERIFIER_REJECTED"
                else "TIMEOUT"
            )
            assert result.failure.stage == "test"
            assert result.failure.authority.kind == "configured-verifier"
        assert assembly.verifier.vectors == [(VersionPin(name="demo-dep", version="1"),)]
        assert assembly.uv.resolutions == ["highest", "lowest-direct"]
        assert all(not root.exists() for root in assembly.uv.environment_roots)

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
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> CheckCellOutcome:
                if indeterminate:
                    return indeterminate_outcome(cell)
                return passing_outcome(cell)

        events = Events()
        result = CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CheckCellOperations, Checker()),
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=events,
        ).run(CheckRequest(root=tmp_path.as_posix(), max_cells=1))

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
    pythons = ["3.10", "3.11"]
    platforms = ["aarch64-apple-darwin", "x86_64-unknown-linux-gnu"]
    extra-policy = "each"
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
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> CheckCellOutcome:
                return indeterminate_outcome(cell)

        events = Events()
        CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=cast(CheckCellOperations, Checker()),
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=events,
        ).run(CheckRequest(root=tmp_path.as_posix(), max_cells=1))

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
    pythons = ["3.10", "3.11"]
    platforms = ["x86_64-unknown-linux-gnu"]
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
                source_plan: SourcePlan, run_cache: TyCheckCache,
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
            checker=cast(CheckCellOperations, Checker()),
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=events,
        ).run(CheckRequest(root=tmp_path.as_posix(), max_cells=2))

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

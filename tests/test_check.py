from __future__ import annotations

from inspect import signature
from pathlib import Path
from threading import Barrier, Lock
from typing import Literal, cast

import pytest

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    successful_process,
)

from pf.check import CompatibilityChecker
from pf.errors import ConfigurationError, InfrastructureError
from pf.failure import FailurePolicy
from pf.harness import (
    degenerate_harness_baseline,
    harness_baseline_requirement,
)
from pf.project import ProjectLoader
from pf.resolution import InstallFailure, ResolutionPlan
from pf.schemas.config import CheckRequest, RunLimits
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
    ProcessResult,
    StatusEvent,
    ToolFailure,
    OperationFailureResult,
    ExecutionFailure,
    Unattributed,
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
from pf.verification import (
    CheckCellOperations,
    CheckVerificationRun,
    VerificationRunner,
)
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


def run_checker(
    assembly,
    package: PackagePlan,
    snapshot: SourceSnapshot,
    *,
    events: Events | None = None,
    cell: Cell | None = None,
    requirement: Literal["REQUIRED", "DEGENERATE"] | None = None,
):
    source_plan = SourcePlan.for_package(package, "SEARCH")
    selected = package.cells[0] if cell is None else cell
    decided = (
        harness_baseline_requirement(
            package.harness_requirements,
            selected,
            source_plan=source_plan,
        )
        if requirement is None
        else requirement
    )
    return CompatibilityChecker(
        environments=assembly.environments,
        full=assembly.runtime,
        events=events,
    ).check(
        package=package,
        cell=selected,
        snapshot=snapshot,
        source_plan=source_plan,
        baseline_requirement=decided,
    )


class TestCompatibilityChecker:
    def test_checker_and_smoke_constructors_reject_static_dependencies(self) -> None:
        assert "static" not in signature(CompatibilityChecker.__init__).parameters
        assert "run_cache" not in signature(CompatibilityChecker.check).parameters

    @pytest.mark.parametrize("test_command", (False, True), ids=("default-command", "explicit-command"))
    @pytest.mark.parametrize("test_group", (False, True), ids=("missing-group", "empty-group"))
    def test_degenerate_without_active_harness_prepares_only_lowest_direct(
        self,
        tmp_path: Path,
        test_command: bool,
        test_group: bool,
    ) -> None:
        package, snapshot = write_check_project(
            tmp_path, test_command=test_command, test_group=test_group,
        )
        assert package.config.test.command == ("pytest",)
        events = Events()
        assembly = evaluation_assembly(highest=(), lowest=(), events=events)
        cell = package.cells[0]
        baseline = degenerate_harness_baseline(package.harness_requirements, cell)

        result = run_checker(assembly, package, snapshot, events=events)

        assert result.status == "PASS"
        assert result.role == "declaration"
        assert assembly.uv.resolutions == ["lowest-direct"]
        assert baseline.declaration_ids == ()
        assert baseline.observations == ()
        assert result.attempt.identity.harness_baseline_digest == baseline.digest
        assert result.evaluation is not None
        assert result.evaluation.proposal.environment_plan_digest is None
        assert [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ] == []
        assert assembly.ty.vectors == []
        assert assembly.verifier.vectors == [()]
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    @pytest.mark.parametrize("harness", ("tool==1.4.5", "tool===vendor"))
    def test_degenerate_fixed_harness_keeps_ids_and_rechecks_environment(
        self,
        tmp_path: Path,
        harness: str,
    ) -> None:
        project = evaluation_project(
            tmp_path,
            dependency=None,
            test_dependencies=(harness,),
        )
        assembly = evaluation_assembly(highest=(), lowest=())
        cell = project.package.cells[0]
        baseline = degenerate_harness_baseline(project.package.harness_requirements, cell)
        source_plan = SourcePlan.for_package(project.package, "SEARCH")

        assert (
            harness_baseline_requirement(
                project.package.harness_requirements,
                cell,
                source_plan=source_plan,
            )
            == "DEGENERATE"
        )
        result = run_checker(assembly, project.package, project.snapshot)

        assert result.status == "PASS"
        assert result.role == "declaration"
        assert assembly.uv.resolutions == ["lowest-direct"]
        assert baseline.declaration_ids == tuple(
            sorted(item.declaration_id for item in project.package.harness_requirements)
        )
        assert baseline.observations == ()
        assert result.attempt.identity.harness_declaration_ids == baseline.declaration_ids
        assert result.attempt.identity.harness_baseline_digest == baseline.digest
        assert result.evaluation is not None
        assert result.evaluation.proposal.environment_plan_digest is not None
        assert assembly.ty.vectors == []
        assert len(assembly.verifier.vectors) == 1
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    @pytest.mark.parametrize("harness", ("tool>=1", "tool~=1.0", "tool==1.*"))
    def test_required_prepares_highest_baseline_then_full_declaration(
        self,
        tmp_path: Path,
        harness: str,
    ) -> None:
        project = evaluation_project(
            tmp_path,
            dependency=None,
            test_dependencies=(harness,),
        )
        events = Events()
        assembly = evaluation_assembly(highest=(), lowest=(), events=events)
        cell = project.package.cells[0]
        source_plan = SourcePlan.for_package(project.package, "SEARCH")

        assert (
            harness_baseline_requirement(
                project.package.harness_requirements,
                cell,
                source_plan=source_plan,
            )
            == "REQUIRED"
        )
        result = run_checker(assembly, project.package, project.snapshot, events=events)

        assert result.status == "PASS"
        assert result.role == "declaration"
        assert assembly.uv.resolutions == ["highest", "lowest-direct"]
        assert result.attempt.identity.requested_resolution == "lowest-direct"
        assert result.attempt.identity.harness_declaration_ids
        assert result.evaluation is not None
        assert result.evaluation.proposal.environment_plan_digest is not None
        assert [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ] == [DeclarationDetailIdentity()]
        assert assembly.ty.vectors == []
        assert len(assembly.verifier.vectors) == 1
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_required_highest_prepare_failure_does_not_start_declaration(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(
            tmp_path,
            dependency=None,
            test_dependencies=("tool>=1",),
        )
        events = Events()
        assembly = evaluation_assembly(
            highest=(),
            lowest=(),
            install_failure=OperationFailureResult(
                failure=ExecutionFailure(
                    terminal=NormalExit(exit_code=2), attribution=Unattributed()
                ),
                stage="install-project",
                process=successful_process(exit_code=2),
            ),
            events=events,
        )

        result = run_checker(assembly, project.package, project.snapshot, events=events)

        assert assembly.uv.resolutions == ["highest"]
        assert result.status == "REJECTED"
        assert result.role == "harness-prepare"
        assert result.attempt.identity.requested_resolution == "highest"
        assert result.failure is not None
        assert result.failure.cause == "INSTALLATION_FAILED"
        assert result.failure.stage == "install-environment"
        assert assembly.ty.vectors == []
        assert assembly.verifier.vectors == []
        assert all(not root.exists() for root in assembly.uv.environment_roots)
        assert [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ] == []

    def test_required_interrupt_after_highest_does_not_forge_declaration(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(
            tmp_path,
            dependency=None,
            test_dependencies=("tool>=1",),
        )
        assembly = evaluation_assembly(highest=(), lowest=())
        closed: list[object] = []

        class InterruptAfterHighest:
            def prepare(self, **kwargs):
                if kwargs["resolution"].kind != "highest":
                    raise InfrastructureError("cancelled before declaration")
                prepared = assembly.environments.prepare(**kwargs)
                original_close = prepared.close

                def close() -> None:
                    closed.append(prepared)
                    original_close()

                prepared.close = close  # type: ignore[method-assign]
                return prepared

        with pytest.raises(InfrastructureError, match="cancelled before declaration"):
            CompatibilityChecker(
                environments=cast(object, InterruptAfterHighest()),
                full=assembly.runtime,
            ).check(
                package=project.package,
                cell=project.package.cells[0],
                snapshot=project.snapshot,
                source_plan=SourcePlan.for_package(project.package, "SEARCH"),
                baseline_requirement="REQUIRED",
            )

        assert assembly.uv.resolutions == ["highest"]
        assert closed
        assert assembly.verifier.vectors == []
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_declaration_prepare_failure_keeps_declaration_role(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path)
        lowest = (VersionPin(name="demo-dep", version="1"),)
        assembly = evaluation_assembly(lowest=lowest)
        assembly.uv.install_failures_by_vector[lowest] = OperationFailureResult(
            failure=ExecutionFailure(
                terminal=NormalExit(exit_code=2), attribution=Unattributed()
            ),
            stage="install-project",
            process=successful_process(exit_code=2),
        )

        result = run_checker(assembly, project.package, project.snapshot)

        assert result.status == "REJECTED"
        assert result.role == "declaration"
        assert result.evaluation is None
        assert result.failure is not None
        assert result.failure.stage == "install-project"
        assert assembly.uv.resolutions == ["lowest-direct"]
        assert assembly.ty.vectors == []
        assert assembly.verifier.vectors == []
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_runner_publishes_command_specific_initial_context(
        self,
        tmp_path: Path,
    ) -> None:
        degenerate_package, degenerate_snapshot = write_check_project(tmp_path)
        required = evaluation_project(
            tmp_path / "required",
            dependency=None,
            test_dependencies=("tool>=1",),
        )
        events = Events()
        assembly = evaluation_assembly(highest=(), lowest=(), events=events)
        checker = CompatibilityChecker(
            environments=assembly.environments,
            full=assembly.runtime,
            events=events,
        )
        runner = VerificationRunner(
            events=events,
            logs=None,
            host_target="x86_64-unknown-linux-gnu",
        )

        runner.run(
            CheckVerificationRun(
                package=degenerate_package,
                source_plan=SourcePlan.for_package(degenerate_package, "SEARCH"),
                snapshot=degenerate_snapshot,
                operation=checker,
                limits=RunLimits(max_cells=1, ty_jobs=1, test_jobs=1),
            )
        )
        degenerate_contexts = [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ]
        assert degenerate_contexts == [DeclarationDetailIdentity()]

        events.items.clear()
        runner.run(
            CheckVerificationRun(
                package=required.package,
                source_plan=SourcePlan.for_package(required.package, "SEARCH"),
                snapshot=required.snapshot,
                operation=checker,
                limits=RunLimits(max_cells=1, ty_jobs=1, test_jobs=1),
            )
        )
        required_contexts = [
            event.detail
            for event in events.items
            if isinstance(event, CellContextEvent)
        ]
        assert required_contexts == [
            BaselineDetailIdentity(),
            DeclarationDetailIdentity(),
        ]

    def test_mixed_cells_with_serial_scheduler_complete_both_branches(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
optional-dependencies = {cuda = ["idna"]}

[dependency-groups]
test = [
    "fixed==1.0; python_version == '3.10'",
    "tool>=8; python_version == '3.10' and extra == 'cuda'",
]

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
extra-policy = "each"
test-command = ["python", "-c", "pass"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=tmp_path).target
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        source_plan = SourcePlan.for_package(package, "SEARCH")
        host_cells = tuple(
            cell
            for cell in package.cells
            if cell.target == "x86_64-unknown-linux-gnu"
        )
        assert [cell.extra_surface for cell in host_cells] == [(), ("cuda",)]
        assert [
            harness_baseline_requirement(
                package.harness_requirements, cell, source_plan=source_plan
            )
            for cell in host_cells
        ] == ["DEGENERATE", "REQUIRED"]

        events = Events()
        assembly = evaluation_assembly(highest=(), lowest=(), events=events)
        install = assembly.uv.install_resolution

        def fail_required_highest(**kwargs: object):
            plan = cast(ResolutionPlan, kwargs["plan"])
            if assembly.uv.resolutions and assembly.uv.resolutions[-1] == "highest":
                return InstallFailure(
                    failure=ExecutionFailure(
                        terminal=NormalExit(exit_code=2), attribution=Unattributed()
                    ),
                    stage="install-environment",
                    process=successful_process(exit_code=2),
                    plan_digest=plan.digest,
                )
            return install(**kwargs)

        assembly.uv.install_resolution = fail_required_highest  # type: ignore[method-assign]
        result = CheckCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            checker=CompatibilityChecker(
                environments=assembly.environments,
                full=assembly.runtime,
                events=events,
            ),
            verification=VerificationRunner(
                events=events,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=events,
        ).run(CheckRequest(root=tmp_path.as_posix(), max_cells=1))

        assert assembly.uv.resolutions == ["lowest-direct", "highest"]
        assert result.status == "COMPATIBILITY_FAILED"
        assert [outcome.role for outcome in result.outcomes] == [
            "declaration",
            "harness-prepare",
        ]
        assert [outcome.status for outcome in result.outcomes] == [
            "PASS",
            "REJECTED",
        ]
        assert assembly.ty.vectors == []
        assert len(assembly.verifier.vectors) == 1
        contexts = [
            (event.cell.extra_surface, event.detail)
            for event in events.items
            if isinstance(event, CellContextEvent)
        ]
        assert contexts == [
            ((), DeclarationDetailIdentity()),
            (("cuda",), BaselineDetailIdentity()),
        ]
        snapshot.close()

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
                source_plan: SourcePlan,
                baseline_requirement: object,
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
                source_plan: SourcePlan,
                baseline_requirement: object,
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
        ids=("missing-group-foreign-host", "empty-group-foreign-host"),
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
                source_plan: SourcePlan,
                baseline_requirement: object,
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
    def test_check_preserves_configured_verifier_outcomes(
        self,
        tmp_path: Path,
        evaluation_status: str,
    ) -> None:
        project = evaluation_project(tmp_path, dependency="demo-dep")
        assembly = evaluation_assembly(
            lowest=(VersionPin(name="demo-dep", version="1"),),
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

        result = run_checker(assembly, project.package, project.snapshot)

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
        assert assembly.uv.resolutions == ["lowest-direct"]
        assert assembly.ty.vectors == []
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
                source_plan: SourcePlan,
                baseline_requirement: object,
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
                source_plan: SourcePlan,
                baseline_requirement: object,
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
        arrived = Barrier(2)

        class Checker:
            def check(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                source_plan: SourcePlan,
                baseline_requirement: object,
            ) -> CheckCellOutcome:
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    seen.append(cell.python_minor)
                arrived.wait(timeout=1)
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

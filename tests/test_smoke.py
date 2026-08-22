from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from pf.errors import InfrastructureError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader
from pf.schemas.config import SmokeRequest
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    HighestVersionOutcome,
    HighestVersionPass,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    TyCheck,
    VerificationJournal,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, PackagePlan, Proposal
from pf.scheduling import Scheduler
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.terminal import TerminalPresenter
from pf.verification import VerificationRunner
from pf.workflow import SmokeCommandWorkflow


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


def attempt_and_proposal(
    *,
    package: PackagePlan,
    cell: Cell,
    snapshot: SourceSnapshot,
) -> tuple[Attempt, Proposal]:
    policy_identity = evaluation_policy_identity(package.config)
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot.identity.digest,
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity=policy_identity,
        )
    )
    return attempt, Proposal(
        proposal_id="highest",
        attempt_id=attempt.attempt_id,
        snapshot_digest=snapshot.identity.digest,
        cell=cell,
        managed_vector=(),
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity=policy_identity,
    )


class FailingJournal:
    run_id = "fail-run"

    def write_journal(self, journal: VerificationJournal) -> Path:
        raise InfrastructureError("could not write PF verification journal")


class TestSmokeWorkflow:
    def test_smoke_workflow_verifies_each_host_cell_at_highest_resolution(
        self,
        tmp_path: Path,
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
    platform = ["x86_64-unknown-linux-gnu", "aarch64-apple-darwin"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        seen: list[Cell] = []

        class Verifier:
            def verify(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
            ) -> HighestVersionOutcome:
                seen.append(cell)
                process = ProcessResult(
                    exit_code=0,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="[]",
                    stderr="",
                )
                attempt, proposal = attempt_and_proposal(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                )
                check = TyCheck(process=process, diagnostics=())
                baseline = StaticBaseline(
                    proposal=proposal,
                    ty=check,
                    digest=ty_diagnostic_digest(check.diagnostics),
                )
                static = StaticPassEvaluation(
                    proposal=proposal,
                    ty=check,
                    baseline_digest=baseline.digest,
                )
                return HighestVersionPass(
                    attempt=attempt,
                    baseline=baseline,
                    evaluation=PassEvaluation(
                        proposal=proposal,
                        static=static,
                        test=TestPass(process=process),
                    ),
                )

        result = SmokeCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder(),
            verifier=Verifier(),
            verification=VerificationRunner(
                scheduler=Scheduler(), events=Events(), logs=None
            ),
            events=Events(),
            host_target="x86_64-unknown-linux-gnu",
        ).run(SmokeRequest(root=tmp_path.as_posix(), jobs=1))

        assert result.status == "PASS"
        assert len(result.outcomes) == 1
        assert [cell.target for cell in seen] == ["x86_64-unknown-linux-gnu"]

    def test_smoke_workflow_treats_a_normal_test_failure_as_compatibility_failure(
        self,
        tmp_path: Path,
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
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "raise SystemExit(1)"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )

        class Verifier:
            def verify(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
            ) -> HighestVersionOutcome:
                process = ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="1 failed",
                    stderr="",
                )
                attempt, proposal = attempt_and_proposal(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                )
                check = TyCheck(
                    process=process.model_copy(update={"stdout": "[]"}),
                    diagnostics=(),
                )
                baseline = StaticBaseline(
                    proposal=proposal,
                    ty=check,
                    digest=ty_diagnostic_digest(check.diagnostics),
                )
                static = StaticPassEvaluation(
                    proposal=proposal,
                    ty=check,
                    baseline_digest=baseline.digest,
                )
                evaluation = TestFailEvaluation(
                    proposal=proposal,
                    static=static,
                    test=TestFail(process=process),
                )
                failure = FailurePolicy().classify(
                    scope=AttemptFailureScope(attempt=attempt),
                    cause="TEST_FAILURE",
                    stage="test",
                    process=process,
                )
                return BaselineRejection(
                    attempt=attempt,
                    failure=failure,
                    static_baseline=baseline,
                    evaluation=evaluation,
                )

        result = SmokeCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder(),
            verifier=Verifier(),
            verification=VerificationRunner(
                scheduler=Scheduler(), events=Events(), logs=None
            ),
            events=Events(),
            host_target="x86_64-unknown-linux-gnu",
        ).run(SmokeRequest(root=tmp_path.as_posix(), jobs=1))

        assert result.status == "BASELINE_REJECTION"

    def test_smoke_workflow_preserves_an_indeterminate_tool_failure(
        self,
        tmp_path: Path,
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
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        process = ProcessResult(
            exit_code=2,
            signal=None,
            duration_seconds=0.1,
            stdout="",
            stderr="ty crashed",
        )

        class Verifier:
            def verify(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
            ) -> HighestVersionOutcome:
                attempt, _ = attempt_and_proposal(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                )
                failure = FailurePolicy().classify(
                    scope=AttemptFailureScope(attempt=attempt),
                    cause="TOOL_FAILURE",
                    stage="ty",
                    process=process,
                )
                return BaselineIndeterminate(attempt=attempt, failure=failure)

        result = SmokeCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder(),
            verifier=Verifier(),
            verification=VerificationRunner(
                scheduler=Scheduler(), events=Events(), logs=None
            ),
            events=Events(),
            host_target="x86_64-unknown-linux-gnu",
        ).run(SmokeRequest(root=tmp_path.as_posix(), jobs=1))

        assert result.status == "INDETERMINATE"
        assert isinstance(result.outcomes[0], BaselineIndeterminate)
        assert result.outcomes[0].failure.process is process

    def test_smoke_omits_diagnose_when_journal_write_fails(
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
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "raise SystemExit(1)"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        failure_id = ""
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
        )

        class Verifier:
            def verify(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
            ) -> HighestVersionOutcome:
                nonlocal failure_id
                process = ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stdout="1 failed",
                    stderr="",
                )
                attempt, proposal = attempt_and_proposal(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                )
                check = TyCheck(
                    process=process.model_copy(update={"stdout": "[]"}),
                    diagnostics=(),
                )
                baseline = StaticBaseline(
                    proposal=proposal,
                    ty=check,
                    digest=ty_diagnostic_digest(check.diagnostics),
                )
                evaluation = TestFailEvaluation(
                    proposal=proposal,
                    static=StaticPassEvaluation(
                        proposal=proposal,
                        ty=check,
                        baseline_digest=baseline.digest,
                    ),
                    test=TestFail(process=process),
                )
                failure = FailurePolicy().classify(
                    scope=AttemptFailureScope(attempt=attempt),
                    cause="TEST_FAILURE",
                    stage="test",
                    process=process,
                )
                failure_id = failure.failure_id
                return BaselineRejection(
                    attempt=attempt,
                    failure=failure,
                    static_baseline=baseline,
                    evaluation=evaluation,
                )

        with pytest.raises(InfrastructureError, match="verification journal"):
            journal = FailingJournal()
            SmokeCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder(),
                verifier=Verifier(),
                verification=VerificationRunner(
                    scheduler=Scheduler(), events=terminal, logs=journal
                ),
                events=terminal,
                host_target="x86_64-unknown-linux-gnu",
            ).run(SmokeRequest(root=tmp_path.as_posix(), jobs=1))

        output = stderr.getvalue()
        assert "failed at testing" in output
        assert "The full test command failed for this version combination." in output
        assert "pf diagnose" not in output
        assert failure_id not in output
        assert "1 failed" in output

from __future__ import annotations

from pf.static import TyCheckCache

from io import StringIO
from pathlib import Path
import re

import pytest

from conftest import empty_harness_baseline
from visible_text import visible_cli_text
from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process
from rich.console import Console

from pf.errors import InfrastructureError
from pf.policy import execution_policy_identity
from pf.project import ProjectLoader
from pf.schemas.config import SmokeRequest
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    FailureRecord,
    HighestVersionOutcome,
    HighestVersionPass,
    NormalExit,
    PassEvaluation,
    ProcessObservation,
    ProcessResult,
    ToolFailure,
    VerifierPass,
    VerifierIndeterminate,
    VerifierDiagnostics,
    VerifierRun,
    TimedOut,
    VerifierRejected,
    VerifierRejectedEvaluation,
)
from pf.schemas.journal import VerificationJournal
from pf.schemas.project import Cell, PackagePlan, Proposal, SourcePlan
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.terminal import TerminalPresenter
from pf.verification import VerificationRunner
from pf.workflow import SmokeCommandWorkflow


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


class TTYBuffer(StringIO):
    def isatty(self) -> bool:
        return True


def visible(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def attempt_and_proposal(
    *,
    package: PackagePlan,
    cell: Cell,
    snapshot: SourceSnapshot,
) -> tuple[Attempt, Proposal]:
    policy_identity = execution_policy_identity(package.config)
    attempt = Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot.identity.digest,
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            execution_policy_identity=policy_identity,
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
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

    def persist_run(self, journal: VerificationJournal, cache) -> None:
        self.write_journal(journal)

    def write_journal(self, journal: VerificationJournal) -> Path:
        raise InfrastructureError("could not write PF verification journal")

    def associate(
        self,
        report_generation_id: str,
        failure_id: str,
        result: ProcessObservation,
    ) -> None:
        raise AssertionError("journal failure must prevent process association")


class TestSmokeWorkflow:
    def test_smoke_runs_verifier_when_static_capture_is_unavailable(self, tmp_path: Path) -> None:
        evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            ty_handler=lambda vector, call: ToolFailure(
                cause="TOOL_FAILURE", stage="ty", process=successful_process(exit_code=2)
            ),
        )
        events = Events()
        result = SmokeCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            verifier=assembly.highest,
            verification=VerificationRunner(
                events=events, logs=None, host_target="x86_64-unknown-linux-gnu"
            ),
            events=events,
        ).run(SmokeRequest(root=tmp_path.as_posix(), max_cells=1))
        assert result.status == "PASS"
        assert len(result.outcomes) == 1
        outcome = result.outcomes[0]
        assert isinstance(outcome, HighestVersionPass)
        assert outcome.evaluation.verifier.terminal == NormalExit(exit_code=0)
        assert assembly.ty.vectors == [()]
        assert assembly.verifier.vectors == [()]
        assert all(not root.exists() for root in assembly.uv.environment_roots)


    def test_smoke_workflow_emits_live_baseline_identity_before_verification(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TERM", "xterm-256color")
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
        seen: list[Cell] = []
        live_frames: list[str] = []
        stderr = TTYBuffer()
        terminal = TerminalPresenter(
            stdout=Console(file=StringIO(), force_terminal=True, width=80),
            stderr=Console(file=stderr, force_terminal=True, width=80),
        )
        terminal.bind_command("smoke")

        class Verifier:
            def verify(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> HighestVersionOutcome:
                seen.append(cell)
                live_frames.append(visible(stderr.getvalue()))
                attempt, proposal = attempt_and_proposal(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                )
                return HighestVersionPass(
                    attempt=attempt,

                    harness_baseline=empty_harness_baseline(cell),
                    evaluation=PassEvaluation(
                        proposal=proposal,

                        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
                    ),
                )

        result = SmokeCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            verifier=Verifier(),
            verification=VerificationRunner(
                events=terminal,
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=terminal,
        ).run(SmokeRequest(root=tmp_path.as_posix(), max_cells=1))

        assert result.status == "PASS"
        assert len(result.outcomes) == 1
        assert [cell.target for cell in seen] == ["x86_64-unknown-linux-gnu"]
        live = live_frames[0]
        title = "[py3.10][x86_64-unknown-linux-gnu][no-extra]"
        title_line = next(line for line in reversed(live.splitlines()) if title in line)
        identity_line = next(
            line
            for line in reversed(live.splitlines())
            if "[baseline][highest]" in line
        )
        assert identity_line != title_line
        assert identity_line.index("[baseline]") == title_line.index(title)

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
    pythons = ["3.10"]
    platforms = ["x86_64-unknown-linux-gnu"]
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
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> HighestVersionOutcome:
                attempt, proposal = attempt_and_proposal(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                )
                evaluation = VerifierRejectedEvaluation(
                    proposal=proposal,

                    verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
                )
                failure = FailureRecord.from_verifier(
                    scope=AttemptFailureScope(attempt=attempt),
                    disposition="REJECTED",
                    cause="VERIFIER_EXITED_NONZERO",
                    stage="test",
                    terminal=NormalExit(exit_code=1),
                )
                return BaselineRejection(
                    attempt=attempt,
                    failure=failure,

                    evaluation=evaluation,
                )

        result = SmokeCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            verifier=Verifier(),
            verification=VerificationRunner(
                events=Events(),
                logs=None,
                host_target="x86_64-unknown-linux-gnu",
            ),
            events=Events(),
        ).run(SmokeRequest(root=tmp_path.as_posix(), max_cells=1))

        assert result.status == "BASELINE_REJECTION"

    def test_smoke_workflow_preserves_verifier_timeout_diagnostics(self, tmp_path: Path) -> None:
        evaluation_project(tmp_path, dependency=None)
        process = ProcessResult(exit_code=None, signal=9, timed_out=True, duration_seconds=1.0, stderr="test timed out")
        assembly = evaluation_assembly(
            highest=(),
            verifier_handler=lambda vector, call: VerifierRun(
                authoritative=VerifierIndeterminate(terminal=TimedOut(), reason="process-timed-out"),
                diagnostics=VerifierDiagnostics(process=process),
            ),
        )
        result = SmokeCommandWorkflow(
            projects=ProjectLoader(), snapshots=SnapshotBuilder.without_processes(),
            verifier=assembly.highest,
            verification=VerificationRunner(events=Events(), logs=None, host_target="x86_64-unknown-linux-gnu"),
            events=Events(),
        ).run(SmokeRequest(root=tmp_path.as_posix(), max_cells=1))
        assert result.status == "INDETERMINATE"
        outcome = result.outcomes[0]
        assert isinstance(outcome, BaselineIndeterminate)
        assert outcome.failure.cause == "TIMEOUT"
        assert outcome.runtime is not None and outcome.runtime.diagnostics is not None
        assert outcome.runtime.diagnostics.process is process

    def test_smoke_omits_diagnose_when_journal_write_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
    platforms = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "raise SystemExit(1)"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        failure_id = ""
        closed: list[SourceSnapshot] = []
        close_snapshot = SourceSnapshot.close

        def close(snapshot: SourceSnapshot) -> None:
            closed.append(snapshot)
            close_snapshot(snapshot)

        monkeypatch.setattr(SourceSnapshot, "close", close)
        stdout = StringIO()
        stderr = StringIO()
        terminal = TerminalPresenter(
            stdout=Console(file=stdout, force_terminal=False, color_system=None),
            stderr=Console(file=stderr, force_terminal=False, color_system=None),
        )
        terminal.bind_command("smoke")

        class Verifier:
            def verify(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                source_plan: SourcePlan, run_cache: TyCheckCache,
            ) -> HighestVersionOutcome:
                nonlocal failure_id
                attempt, proposal = attempt_and_proposal(
                    package=package,
                    cell=cell,
                    snapshot=snapshot,
                )
                evaluation = VerifierRejectedEvaluation(
                    proposal=proposal,

                    verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
                )
                failure = FailureRecord.from_verifier(
                    scope=AttemptFailureScope(attempt=attempt),
                    disposition="REJECTED",
                    cause="VERIFIER_EXITED_NONZERO",
                    stage="test",
                    terminal=NormalExit(exit_code=1),
                )
                failure_id = failure.failure_id
                return BaselineRejection(
                    attempt=attempt,
                    failure=failure,

                    evaluation=evaluation,
                )

        with pytest.raises(InfrastructureError, match="verification journal"):
            journal = FailingJournal()
            SmokeCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder.without_processes(),
                verifier=Verifier(),
                verification=VerificationRunner(
                    events=terminal,
                    logs=journal,
                    host_target="x86_64-unknown-linux-gnu",
                ),
                events=terminal,
            ).run(SmokeRequest(root=tmp_path.as_posix(), max_cells=1))

        output = visible_cli_text(stderr.getvalue())
        assert "smoke failed at [baseline][highest][testing]" in output
        assert "The configured verifier rejected this version combination." in output
        assert "pf diagnose" not in output
        assert failure_id not in output
        assert "1 failed" not in output
        assert "Detailed diagnosis unavailable." in output
        assert len(closed) == 1

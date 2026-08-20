from __future__ import annotations

from pathlib import Path

from pf.baseline import HighestVersionVerification
from pf.project import ProjectLoader
from pf.schemas.config import SmokeRequest
from pf.schemas.evaluation import (
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    ToolFailure,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, PackagePlan, Proposal
from pf.scheduling import Scheduler
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.workflow import SmokeCommandWorkflow


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


def test_smoke_workflow_verifies_each_host_cell_at_highest_resolution(
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
        ) -> HighestVersionVerification:
            seen.append(cell)
            process = ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="[]",
                stderr_summary="",
                stdout_tail="[]",
                stderr_tail="",
            )
            proposal = Proposal(
                proposal_id="highest",
                snapshot_digest=snapshot.identity.digest,
                cell=cell,
                managed_vector=(),
                fixed_declaration_ids=(),
                resolved_graph=(),
                policy_identity="policy",
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
            return HighestVersionVerification(
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
        scheduler=Scheduler(),
        events=Events(),
        host_target="x86_64-unknown-linux-gnu",
    ).run(SmokeRequest(root=tmp_path.as_posix(), jobs=1))

    assert result.status == "PASS"
    assert len(result.evaluations) == 1
    assert [cell.target for cell in seen] == ["x86_64-unknown-linux-gnu"]


def test_smoke_workflow_treats_a_normal_test_failure_as_compatibility_failure(
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
        ) -> HighestVersionVerification:
            process = ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="1 failed",
                stderr_summary="",
                stdout_tail="1 failed",
                stderr_tail="",
            )
            proposal = Proposal(
                proposal_id="highest",
                snapshot_digest=snapshot.identity.digest,
                cell=cell,
                managed_vector=(),
                fixed_declaration_ids=(),
                resolved_graph=(),
                policy_identity="policy",
            )
            check = TyCheck(
                process=process.model_copy(update={"stdout_summary": "[]"}),
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
            return HighestVersionVerification(
                baseline=baseline,
                evaluation=TestFailEvaluation(
                    proposal=proposal,
                    static=static,
                    test=TestFail(process=process),
                ),
            )

    result = SmokeCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        verifier=Verifier(),
        scheduler=Scheduler(),
        events=Events(),
        host_target="x86_64-unknown-linux-gnu",
    ).run(SmokeRequest(root=tmp_path.as_posix(), jobs=1))

    assert result.status == "TEST_FAILED"


def test_smoke_workflow_preserves_an_indeterminate_tool_failure(
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
    failure = ToolFailure(
        status="TOOL_ERROR",
        stage="ty",
        process=ProcessResult(
            exit_code=2,
            signal=None,
            duration_seconds=0.1,
            stdout_summary="",
            stderr_summary="ty crashed",
            stdout_tail="",
            stderr_tail="ty crashed",
        ),
    )

    class Verifier:
        def verify(self, **kwargs: object) -> ToolFailure:
            return failure

    result = SmokeCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        verifier=Verifier(),
        scheduler=Scheduler(),
        events=Events(),
        host_target="x86_64-unknown-linux-gnu",
    ).run(SmokeRequest(root=tmp_path.as_posix(), jobs=1))

    assert result.status == "INDETERMINATE"
    assert result.failure is failure

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Literal

from pf.baseline import HighestVersionVerification, HighestVersionVerifier
from pf.environment import PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticPassEvaluation,
    TestPass,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.project import Cell, PackagePlan, Proposal
from pf.snapshot import SnapshotBuilder, SourceSnapshot


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="[]",
        stderr_summary="",
        stdout_tail="[]",
        stderr_tail="",
    )


def test_highest_version_verifier_reuses_capture_for_full_test_and_closes(
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
    package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(tmp_path)
    resolutions: list[str] = []
    prepared_items: list[PreparedEnvironment] = []
    capture_calls = 0

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
            temporary = tempfile.TemporaryDirectory(prefix="pf-highest-test-")
            root = Path(temporary.name)
            prepared = PreparedEnvironment(
                proposal=Proposal(
                    proposal_id="highest",
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
                temporary_directory=temporary,
            )
            prepared_items.append(prepared)
            return prepared

    class Static:
        def capture(
            self,
            prepared: PreparedEnvironment,
            *,
            package: PackagePlan,
        ) -> StaticBaselineCapture:
            nonlocal capture_calls
            capture_calls += 1
            check = TyCheck(process=successful_process(), diagnostics=())
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
            assert isinstance(static_result, StaticPassEvaluation)
            assert static_result.ty is baseline.ty
            prepared.mark_tested()
            return PassEvaluation(
                proposal=prepared.proposal,
                static=static_result,
                test=TestPass(process=successful_process()),
            )

    result = HighestVersionVerifier(
        environments=Environments(),
        static=Static(),
        full=Full(),
    ).verify(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
    )

    assert isinstance(result, HighestVersionVerification)
    assert isinstance(result.evaluation, PassEvaluation)
    assert result.evaluation.status == "PASS"
    assert result.baseline.ty is result.evaluation.static.ty
    assert resolutions == ["highest"]
    assert capture_calls == 1
    assert prepared_items[0].tested is True
    assert not prepared_items[0].proposal_root.exists()

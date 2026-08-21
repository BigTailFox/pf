from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Literal

import pytest

from pf.baseline import HighestVersionVerifier
from pf.environment import PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    Evaluation,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    PrepareFailure,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
    ToolFailure,
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
            prepared = PreparedEnvironment(
                attempt=attempt,
                proposal=Proposal(
                    proposal_id="highest",
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

    assert isinstance(result, HighestVersionPass)
    assert isinstance(result.evaluation, PassEvaluation)
    assert result.evaluation.status == "PASS"
    assert result.baseline.ty is result.evaluation.static.ty
    assert resolutions == ["highest"]
    assert capture_calls == 1
    assert prepared_items[0].tested is True
    assert not prepared_items[0].proposal_root.exists()


def test_highest_version_verifier_distinguishes_baseline_rejection(
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
    identity = AttemptIdentity(
        source_snapshot_digest=snapshot.identity.digest,
        cell=package.cells[0],
        requested_resolution="highest",
        requested_managed_vector=None,
        active_declaration_ids=package.cells[0].active_declaration_ids,
        source_plan_identity="sources",
        evaluation_policy_identity="policy",
    )
    prepare_failure = PrepareFailure(
        attempt=Attempt.from_identity(identity),
        failure=ToolFailure(
            cause="BUILD_FAILURE",
            stage="install-project",
            process=ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout_summary="",
                stderr_summary="failed to build wheel",
                stdout_tail="",
                stderr_tail="failed to build wheel",
            ),
        ),
    )

    class Environments:
        def prepare(self, **kwargs: object) -> PrepareFailure:
            return prepare_failure

    class NeverStatic:
        def capture(self, *args: object, **kwargs: object) -> StaticBaselineCapture:
            raise AssertionError("baseline rejection must stop before ty")

    class NeverFull:
        def evaluate(self, *args: object, **kwargs: object) -> PassEvaluation:
            raise AssertionError("baseline rejection must stop before tests")

    result = HighestVersionVerifier(
        environments=Environments(),
        static=NeverStatic(),
        full=NeverFull(),
    ).verify(package=package, cell=package.cells[0], snapshot=snapshot)

    assert isinstance(result, BaselineRejection)
    assert result.failure.disposition == "REJECTED"
    assert result.failure.cause == "BUILD_FAILURE"
    assert result.evaluation is None


def _package_and_snapshot(tmp_path: Path) -> tuple[PackagePlan, SourceSnapshot]:
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
    return package, SnapshotBuilder().build(tmp_path)


def _prepared(
    package: PackagePlan,
    snapshot: SourceSnapshot,
) -> PreparedEnvironment:
    temporary = tempfile.TemporaryDirectory(prefix="pf-highest-outcome-test-")
    root = Path(temporary.name)
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
    return PreparedEnvironment(
        attempt=attempt,
        proposal=Proposal(
            proposal_id="highest",
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
        temporary_directory=temporary,
    )


def _capture(prepared: PreparedEnvironment) -> StaticBaselineCapture:
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


class _NeverStatic:
    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture:
        raise AssertionError("static evaluation must not run")


class _NeverFull:
    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> Evaluation:
        raise AssertionError("full evaluation must not run")


def test_highest_version_verifier_retains_indeterminate_prepare_failure(
    tmp_path: Path,
) -> None:
    package, snapshot = _package_and_snapshot(tmp_path)
    prepared = _prepared(package, snapshot)
    assert prepared.attempt is not None
    failure = PrepareFailure(
        attempt=prepared.attempt,
        failure=ToolFailure(
            cause="SOURCE_FAILURE",
            stage="install-project",
            process=successful_process().model_copy(update={"exit_code": 2}),
        ),
    )

    class Environments:
        def prepare(self, **kwargs: object) -> PrepareFailure:
            return failure

    result = HighestVersionVerifier(
        environments=Environments(),
        static=_NeverStatic(),
        full=_NeverFull(),
    ).verify(package=package, cell=package.cells[0], snapshot=snapshot)

    assert isinstance(result, BaselineIndeterminate)
    assert result.failure.cause == "SOURCE_FAILURE"
    assert result.evaluation is None
    prepared.close()


def test_highest_version_verifier_rejects_prepare_without_attempt(
    tmp_path: Path,
) -> None:
    package, snapshot = _package_and_snapshot(tmp_path)

    class Environments:
        def prepare(self, **kwargs: object) -> ToolFailure:
            return ToolFailure(
                cause="TOOL_FAILURE",
                stage="install-project",
                process=successful_process().model_copy(update={"exit_code": 2}),
            )

    with pytest.raises(ValueError, match="must establish an Attempt"):
        HighestVersionVerifier(
            environments=Environments(),
            static=_NeverStatic(),
            full=_NeverFull(),
        ).verify(package=package, cell=package.cells[0], snapshot=snapshot)


def test_highest_version_verifier_retains_indeterminate_static_capture(
    tmp_path: Path,
) -> None:
    package, snapshot = _package_and_snapshot(tmp_path)
    prepared = _prepared(package, snapshot)

    class Environments:
        def prepare(self, **kwargs: object) -> PreparedEnvironment:
            return prepared

    class Static:
        def capture(self, *args: object, **kwargs: object) -> IndeterminateEvaluation:
            failure = ToolFailure(
                cause="TOOL_FAILURE",
                stage="ty",
                process=successful_process().model_copy(update={"exit_code": 2}),
            )
            return IndeterminateEvaluation(
                proposal=prepared.proposal,
                cause=failure.cause,
                failure=failure,
            )

    result = HighestVersionVerifier(
        environments=Environments(),
        static=Static(),
        full=_NeverFull(),
    ).verify(package=package, cell=package.cells[0], snapshot=snapshot)

    assert isinstance(result, BaselineIndeterminate)
    assert result.evaluation is not None
    assert not prepared.proposal_root.exists()


@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_cause"),
    (
        ("static", "BASELINE_INDETERMINATE", "INTERNAL_INVARIANT"),
        ("test", "BASELINE_REJECTION", "TEST_FAILURE"),
        ("tool", "BASELINE_INDETERMINATE", "TOOL_FAILURE"),
    ),
)
def test_highest_version_verifier_classifies_complete_evaluations(
    tmp_path: Path,
    kind: str,
    expected_status: str,
    expected_cause: str,
) -> None:
    package, snapshot = _package_and_snapshot(tmp_path)
    prepared = _prepared(package, snapshot)
    capture = _capture(prepared)

    class Environments:
        def prepare(self, **kwargs: object) -> PreparedEnvironment:
            return prepared

    class Static:
        def capture(self, *args: object, **kwargs: object) -> StaticBaselineCapture:
            return capture

    class Full:
        def evaluate(self, *args: object, **kwargs: object) -> Evaluation:
            failed_process = successful_process().model_copy(update={"exit_code": 1})
            if kind == "static":
                diagnostic = TyDiagnostic(
                    identity="snapshot|demo.py|1|1|invalid-return-type",
                    origin="snapshot",
                    path="demo.py",
                    line=1,
                    column=1,
                    code="invalid-return-type",
                    severity="error",
                    message="incompatible return type",
                )
                return StaticFailEvaluation(
                    proposal=prepared.proposal,
                    ty=TyCheck(
                        process=failed_process,
                        diagnostics=(diagnostic,),
                    ),
                    baseline_digest=capture.baseline.digest,
                    incremental=(diagnostic,),
                )
            if kind == "test":
                return TestFailEvaluation(
                    proposal=prepared.proposal,
                    static=capture.static,
                    test=TestFail(process=failed_process),
                )
            failure = ToolFailure(
                cause="TOOL_FAILURE",
                stage="test",
                process=failed_process,
            )
            return IndeterminateEvaluation(
                proposal=prepared.proposal,
                cause=failure.cause,
                failure=failure,
            )

    result = HighestVersionVerifier(
        environments=Environments(),
        static=Static(),
        full=Full(),
    ).verify(package=package, cell=package.cells[0], snapshot=snapshot)

    assert result.status == expected_status
    assert isinstance(result, (BaselineRejection, BaselineIndeterminate))
    assert result.failure.cause == expected_cause
    assert (result.evaluation is not None) is (kind != "static")
    assert not prepared.proposal_root.exists()

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import cast

import pytest

from conftest import prepared_resolution_evidence

from pf.baseline import HighestEnvironmentOperations, HighestVersionVerifier
from pf.environment import HighestResolution, PreparedEnvironment, ResolutionRequest
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    PrepareFailure,
    RuntimeEvaluationRun,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    StaticUnchangedEvaluation,
    TyCheck,
    ty_diagnostic_digest,
    ToolFailure,
    NormalExit,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
)
from pf.schemas.project import Cell, PackagePlan, Proposal, SourcePlan
from pf.snapshot import SnapshotBuilder, SourceSnapshot


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="[]",
        stderr="",
    )


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
    package = ProjectLoader().load(root=tmp_path).target
    return package, SnapshotBuilder.without_processes().build(tmp_path)


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
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
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
        **prepared_resolution_evidence(cell=cell),
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
        static=StaticUnchangedEvaluation(
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
    ) -> RuntimeEvaluationRun:
        raise AssertionError("full evaluation must not run")


class TestHighestVersionVerifier:
    def test_highest_version_verifier_reuses_capture_for_full_test_and_closes(
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
        package = ProjectLoader().load(root=tmp_path).target
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        resolutions: list[ResolutionRequest] = []
        prepared_items: list[PreparedEnvironment] = []
        capture_calls = 0

        class Environments:
            def prepare(
                self,
                *,
                package: PackagePlan,
                cell: Cell,
                snapshot: SourceSnapshot,
                resolution: ResolutionRequest,
                source_plan: SourcePlan,
            ) -> PreparedEnvironment:
                assert source_plan is search_plan
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
                        resolution_context_digest="context",
                        harness_policy_identity="original-harness-v1",
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
                    **prepared_resolution_evidence(cell=cell),
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
                    static=StaticUnchangedEvaluation(
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
            ) -> RuntimeEvaluationRun:
                assert isinstance(static_result, StaticUnchangedEvaluation)
                assert static_result.ty is baseline.ty
                prepared.mark_tested()
                return RuntimeEvaluationRun(
                    evaluation=PassEvaluation(
                        proposal=prepared.proposal,
                        static=static_result,
                        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
                    )
                )

        search_plan = SourcePlan.for_package(package, "SEARCH")
        result = HighestVersionVerifier(
            environments=Environments(),
            static=Static(),
            full=Full(),
        ).verify(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=search_plan,
        )

        assert isinstance(result, HighestVersionPass)
        assert isinstance(result.evaluation, PassEvaluation)
        assert result.evaluation.status == "PASS"
        assert result.baseline.ty is result.evaluation.static.ty
        assert resolutions == [HighestResolution()]
        assert capture_calls == 1
        assert prepared_items[0].tested is True
        assert not prepared_items[0].proposal_root.exists()

    def test_highest_version_verifier_keeps_install_build_failure_indeterminate(
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
        package = ProjectLoader().load(root=tmp_path).target
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        identity = AttemptIdentity(
            source_snapshot_digest=snapshot.identity.digest,
            cell=package.cells[0],
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=package.cells[0].active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
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
                    stdout="",
                    stderr="failed to build wheel",
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
            def evaluate(self, *args: object, **kwargs: object) -> RuntimeEvaluationRun:
                raise AssertionError("baseline rejection must stop before tests")

        result = HighestVersionVerifier(
            environments=Environments(),
            static=NeverStatic(),
            full=NeverFull(),
        ).verify(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert isinstance(result, BaselineIndeterminate)
        assert result.failure.disposition == "INDETERMINATE"
        assert result.failure.cause == "BUILD_FAILURE"
        assert result.evaluation is None

    def test_highest_version_verifier_retains_indeterminate_prepare_failure(
        self,
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
        ).verify(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert isinstance(result, BaselineIndeterminate)
        assert result.failure.cause == "SOURCE_FAILURE"
        assert result.evaluation is None
        prepared.close()

    def test_highest_version_verifier_rejects_prepare_without_attempt(
        self,
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
                environments=cast(HighestEnvironmentOperations, Environments()),
                static=_NeverStatic(),
                full=_NeverFull(),
            ).verify(
                package=package,
                cell=package.cells[0],
                snapshot=snapshot,
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )

    def test_highest_version_verifier_retains_indeterminate_static_capture(
        self,
        tmp_path: Path,
    ) -> None:
        package, snapshot = _package_and_snapshot(tmp_path)
        prepared = _prepared(package, snapshot)

        class Environments:
            def prepare(self, **kwargs: object) -> PreparedEnvironment:
                return prepared

        class Static:
            def capture(
                self, *args: object, **kwargs: object
            ) -> IndeterminateEvaluation:
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
        ).verify(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert isinstance(result, BaselineIndeterminate)
        assert result.evaluation is not None
        assert not prepared.proposal_root.exists()

    @pytest.mark.parametrize(
        ("kind", "expected_status", "expected_cause"),
        (
            ("test", "BASELINE_REJECTION", "VERIFIER_EXITED_NONZERO"),
            ("tool", "BASELINE_INDETERMINATE", "TOOL_FAILURE"),
        ),
    )
    def test_highest_version_verifier_classifies_complete_evaluations(
        self,
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
            def evaluate(self, *args: object, **kwargs: object) -> RuntimeEvaluationRun:
                failed_process = successful_process().model_copy(
                    update={"exit_code": 1}
                )
                if kind == "test":
                    return RuntimeEvaluationRun(
                        evaluation=VerifierRejectedEvaluation(
                            proposal=prepared.proposal,
                            static=capture.static,
                            verifier=VerifierRejected(
                                terminal=NormalExit(
                                    exit_code=failed_process.exit_code or 1
                                )
                            ),
                        )
                    )
                failure = ToolFailure(
                    cause="TOOL_FAILURE",
                    stage="test",
                    process=failed_process,
                )
                return RuntimeEvaluationRun(
                    evaluation=IndeterminateEvaluation(
                        proposal=prepared.proposal,
                        cause=failure.cause,
                        failure=failure,
                        static=capture.static,
                    )
                )

        result = HighestVersionVerifier(
            environments=Environments(),
            static=Static(),
            full=Full(),
        ).verify(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert result.status == expected_status
        assert isinstance(result, (BaselineRejection, BaselineIndeterminate))
        assert result.failure.cause == expected_cause
        assert (result.evaluation is not None) is (kind != "static")
        assert not prepared.proposal_root.exists()

from __future__ import annotations

from pathlib import Path

import pytest

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    successful_process,
)

from pf.report import PackageReportBuilder, ReportStore
from pf.schemas.journal import JournalHighestCollected
from pf.schemas.evaluation import (
    BaselineIndeterminate,
    BaselineRejection,
    HighestVersionPass,
    TyCheck,
    NormalExit,
    TimedOut,
    ToolFailure,
    OperationFailureResult,
    ExecutionFailure,
    StructuredOperationFailure,
    SourceAccessFailedFact,
    Unattributed,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)


class TestHighestVersionVerifier:
    def test_highest_version_verifier_reuses_capture_for_full_test_and_closes(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=())

        result = assembly.highest.verify(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, HighestVersionPass)
        assert result.evaluation.status == "PASS"
        membership = run_cache.admitted_membership(result.evaluation.proposal.cell)
        assert membership is not None
        assert isinstance(membership.highest, JournalHighestCollected)
        assert run_cache.documents()[0].fact.kind == "ty-check"
        assert assembly.uv.resolutions == ["highest"]
        assert assembly.ty.vectors == [()]
        assert assembly.verifier.vectors == [()]
        assert assembly.uv.environment_roots
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    @pytest.mark.parametrize("cause", ("INSTALLATION_FAILED", "SOURCE_FAILURE"))
    def test_highest_version_verifier_retains_prepare_failure_and_closes(
        self, run_cache,
        tmp_path: Path,
        cause: str,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            install_failure=OperationFailureResult(
                failure=(ExecutionFailure(terminal=NormalExit(exit_code=2), attribution=Unattributed())
                         if cause == "INSTALLATION_FAILED" else StructuredOperationFailure(fact=SourceAccessFailedFact(), terminal=None)),
                stage="install-project",
                process=successful_process(exit_code=2) if cause == "INSTALLATION_FAILED" else None,
            ),
        )

        result = assembly.highest.verify(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, BaselineRejection if cause == "INSTALLATION_FAILED" else BaselineIndeterminate)
        assert result.failure.disposition == ("REJECTED" if cause == "INSTALLATION_FAILED" else "INDETERMINATE")
        assert result.failure.cause == cause
        assert result.evaluation is None
        assert assembly.ty.vectors == []
        assert assembly.verifier.vectors == []
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_highest_version_verifier_retains_static_capture_failure_and_closes(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            ty_handler=lambda vector, call: ToolFailure(
                cause="TOOL_FAILURE",
                stage="ty",
                process=successful_process(exit_code=2),
            ),
        )

        result = assembly.highest.verify(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, HighestVersionPass)
        membership = run_cache.admitted_membership(result.evaluation.proposal.cell)
        assert membership is not None
        assert isinstance(membership.highest, JournalHighestCollected)
        assert run_cache.documents()[0].fact.kind == "ty-check-unavailable"
        assert result.evaluation is not None
        assert assembly.verifier.vectors == [()]
        assert assembly.ty.vectors == [()]
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    @pytest.mark.parametrize(
        ("outcome", "expected_type", "expected_cause"),
        (
            (
                VerifierRejected(terminal=NormalExit(exit_code=1)),
                BaselineRejection,
                "VERIFIER_EXITED_NONZERO",
            ),
            (
                VerifierIndeterminate(
                    terminal=TimedOut(),
                    reason="process-timed-out",
                ),
                BaselineIndeterminate,
                "TIMEOUT",
            ),
        ),
    )
    @pytest.mark.parametrize("static_available", (False, True))
    def test_highest_version_verifier_classifies_complete_evaluations(
        self, run_cache,
        tmp_path: Path,
        outcome: VerifierRejected | VerifierIndeterminate,
        expected_type: type[BaselineRejection] | type[BaselineIndeterminate],
        expected_cause: str,
        static_available: bool,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            ty_handler=lambda vector, call: (
                TyCheck(process=successful_process(), diagnostics=()) if static_available
                else ToolFailure(cause="TOOL_FAILURE", stage="ty", process=successful_process(exit_code=2))
            ),
            verifier_handler=lambda vector, call: VerifierRun(authoritative=outcome),
        )

        result = assembly.highest.verify(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, expected_type)
        assert result.failure.cause == expected_cause
        assert result.evaluation is not None
        if not static_available:
            membership = run_cache.admitted_membership(result.cell)
            assert membership is not None
            assert isinstance(membership.highest, JournalHighestCollected)
            assert run_cache.documents()[0].fact.kind == "ty-check-unavailable"
        assert len(assembly.ty.vectors) == 1
        assert len(assembly.verifier.vectors) == 1
        assert all(not root.exists() for root in assembly.uv.environment_roots)

        report = PackageReportBuilder().build(
            package=project.package,
            source_plan=project.source_plan,
            source_snapshot=project.snapshot.identity,
            cell_results=(result,),
        )
        store = ReportStore()
        path = tmp_path / "package-floor.json"
        store.write(path, report)
        assert store.read(path) == report

    def test_highest_version_verifier_preserves_a_passing_verifier_authority(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            verifier_handler=lambda vector, call: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
            ),
        )

        result = assembly.highest.verify(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, HighestVersionPass)
        assert result.evaluation.verifier == VerifierPass(
            terminal=NormalExit(exit_code=0)
        )

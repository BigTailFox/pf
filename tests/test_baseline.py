from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    successful_process,
)

from pf.schemas.evaluation import (
    BaselineIndeterminate,
    BaselineRejection,
    HighestVersionPass,
    NormalExit,
    TimedOut,
    ToolFailure,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)


class TestHighestVersionVerifier:
    def test_highest_version_verifier_reuses_capture_for_full_test_and_closes(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=())

        result = assembly.highest.verify(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, HighestVersionPass)
        assert result.evaluation.status == "PASS"
        assert result.baseline.ty is result.evaluation.static.ty
        assert assembly.uv.resolutions == ["highest"]
        assert assembly.ty.vectors == [()]
        assert assembly.verifier.vectors == [()]
        assert assembly.uv.environment_roots
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    @pytest.mark.parametrize("cause", ("BUILD_FAILURE", "SOURCE_FAILURE"))
    def test_highest_version_verifier_retains_prepare_failure_and_closes(
        self,
        tmp_path: Path,
        cause: Literal["BUILD_FAILURE", "SOURCE_FAILURE"],
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            install_failure=ToolFailure(
                cause=cause,
                stage="install-environment",
                process=successful_process(exit_code=2),
            ),
        )

        result = assembly.highest.verify(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, BaselineIndeterminate)
        assert result.failure.disposition == "INDETERMINATE"
        assert result.failure.cause == cause
        assert result.evaluation is None
        assert assembly.ty.vectors == []
        assert assembly.verifier.vectors == []
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_highest_version_verifier_retains_static_capture_failure_and_closes(
        self,
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

        result = assembly.highest.verify(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, BaselineIndeterminate)
        assert result.failure.cause == "TOOL_FAILURE"
        assert result.evaluation is not None
        assert assembly.verifier.vectors == []
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
    def test_highest_version_verifier_classifies_complete_evaluations(
        self,
        tmp_path: Path,
        outcome: VerifierRejected | VerifierIndeterminate,
        expected_type: type[BaselineRejection] | type[BaselineIndeterminate],
        expected_cause: str,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            verifier_handler=lambda vector, call: VerifierRun(authoritative=outcome),
        )

        result = assembly.highest.verify(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, expected_type)
        assert result.failure.cause == expected_cause
        assert result.evaluation is not None
        assert len(assembly.ty.vectors) == 1
        assert len(assembly.verifier.vectors) == 1
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_highest_version_verifier_preserves_a_passing_verifier_authority(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            verifier_handler=lambda vector, call: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
            ),
        )

        result = assembly.highest.verify(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, HighestVersionPass)
        assert result.evaluation.verifier == VerifierPass(
            terminal=NormalExit(exit_code=0)
        )

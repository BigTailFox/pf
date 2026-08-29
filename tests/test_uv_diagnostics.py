from __future__ import annotations

import pytest

from pf.adapters.uv_diagnostics import classify_resolution_diagnostic
from pf.resolution import UV_DIAGNOSTIC_PROFILES
from pf.schemas.evaluation import ProcessResult


def _failed(
    *,
    stderr: str,
    stderr_complete: bool = True,
) -> ProcessResult:
    return ProcessResult(
        exit_code=1,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr=stderr,
        stderr_complete=stderr_complete,
    )


class TestUvDiagnosticProfile:
    @pytest.mark.parametrize("uv_version", tuple(UV_DIAGNOSTIC_PROFILES))
    def test_complete_direct_contradiction_is_certified(self, uv_version: str) -> None:
        stderr = """
  × No solution found when resolving dependencies:
  ╰─▶ Because you require demo==1 and demo==2, we can conclude that your
      requirements are unsatisfiable.
"""

        result = classify_resolution_diagnostic(
            uv_version=uv_version,
            process=_failed(stderr=stderr),
            stdout="",
            stderr=stderr,
        )

        assert result.kind == "unsat"
        assert result.proof_code == "direct-version-contradiction"

    @pytest.mark.parametrize("uv_version", tuple(UV_DIAGNOSTIC_PROFILES))
    def test_complete_transitive_contradiction_is_certified(
        self, uv_version: str
    ) -> None:
        stderr = """
  × No solution found when resolving dependencies:
  ╰─▶ Because package-a==1 depends on shared==1 and package-b==1 depends on
      shared==2, we can conclude that your requirements are unsatisfiable.
"""

        result = classify_resolution_diagnostic(
            uv_version=uv_version,
            process=_failed(stderr=stderr),
            stdout="",
            stderr=stderr,
        )

        assert result.kind == "unsat"
        assert result.proof_code == "transitive-version-contradiction"

    @pytest.mark.parametrize(
        ("stderr", "cause", "summary"),
        (
            (
                "No solution found. demo was not found in the provided package locations and your requirements are unsatisfiable.",
                "TOOL_FAILURE",
                "resolution-candidate-unavailable",
            ),
            (
                "No solution found. There is no version of demo==2 and your requirements are unsatisfiable.",
                "TOOL_FAILURE",
                "resolution-candidate-unavailable",
            ),
            (
                "Request failed: 401 Unauthorized",
                "SOURCE_FAILURE",
                "resolution-source-failure",
            ),
            ("Failed to read metadata", "SOURCE_FAILURE", "resolution-source-failure"),
            ("Archive hash mismatch", "SOURCE_FAILURE", "resolution-source-failure"),
            ("Failed to build wheel", "BUILD_FAILURE", "resolution-build-failure"),
            ("something new", "TOOL_FAILURE", "resolution-diagnostic-unknown"),
        ),
    )
    def test_ambiguous_and_abnormal_failures_are_indeterminate(
        self,
        stderr: str,
        cause: str,
        summary: str,
    ) -> None:
        if "was not found" in stderr:
            stderr = (
                "× No solution found when resolving dependencies: Because demo "
                "was not found in the provided package locations and you require "
                "demo==1, we can conclude that your requirements are unsatisfiable."
            )
        elif "no version of" in stderr.lower():
            stderr = (
                "× No solution found when resolving dependencies: Because there is "
                "no version of demo==2 and you require "
                "demo==1, we can conclude that your requirements are unsatisfiable."
            )

        result = classify_resolution_diagnostic(
            uv_version="0.12.5",
            process=_failed(stderr=stderr),
            stdout="",
            stderr=stderr,
        )

        assert result.kind == "indeterminate"
        assert result.cause == cause
        assert result.summary_code == summary

    def test_incomplete_known_diagnostic_is_not_certified(self) -> None:
        stderr = "Because you require demo==1 and demo==2"

        result = classify_resolution_diagnostic(
            uv_version="0.12.5",
            process=_failed(stderr=stderr, stderr_complete=False),
            stdout="",
            stderr=stderr,
        )

        assert result.kind == "indeterminate"
        assert result.summary_code == "resolution-output-incomplete"

    def test_unqualified_uv_version_is_not_classified(self) -> None:
        result = classify_resolution_diagnostic(
            uv_version="0.12.6",
            process=_failed(stderr="no solution found"),
            stdout="",
            stderr="no solution found",
        )

        assert result.kind == "indeterminate"
        assert result.summary_code == "unsupported-uv-version"

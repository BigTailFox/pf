from __future__ import annotations

from pathlib import Path

import pytest

from pf.adapters.test_command import TestAdapter
from pf.schemas.evaluation import (
    ProcessResult,
    ProcessSpec,
    TestFail,
    TestPass,
    ToolFailure,
)


class FailingTestRunner:
    def __init__(self) -> None:
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.spec = spec
        return ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="1 failed\n",
            stderr="",
        )


def test_test_adapter_uses_configured_argv_and_failure_codes(tmp_path: Path) -> None:
    runner = FailingTestRunner()
    adapter = TestAdapter(runner)

    result = adapter.run(
        command=("pytest", "tests"),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=1800,
    )

    assert result.status == "TEST_FAIL"
    assert runner.spec is not None
    assert runner.spec.argv == ("pytest", "tests")
    assert runner.spec.cwd == tmp_path.as_posix()
    assert runner.spec.timeout_seconds == 1800


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "expected"),
    (
        (0, False, "TEST_PASS"),
        (2, False, "TOOL_FAILURE"),
        (None, True, "TIMEOUT"),
    ),
)
def test_test_adapter_preserves_every_terminal_classification(
    tmp_path: Path,
    exit_code: int | None,
    timed_out: bool,
    expected: str,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return ProcessResult(
                exit_code=exit_code,
                signal=None if exit_code is not None else 9,
                duration_seconds=0.1,
                stdout="",
                stderr="",
                timed_out=timed_out,
            )

    result = TestAdapter(Runner()).run(
        command=("pytest",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=10,
    )

    observed = result.cause if isinstance(result, ToolFailure) else result.status
    assert observed == expected


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    (
        (0, TestPass),
        (1, TestFail),
    ),
)
def test_test_adapter_classifies_exit_code_without_treating_cache_as_failure(
    tmp_path: Path,
    exit_code: int,
    expected: type[TestPass] | type[TestFail],
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return ProcessResult(
                exit_code=exit_code,
                signal=None,
                duration_seconds=0.1,
                stdout="bounded",
                stderr="",
            )

    result = TestAdapter(Runner()).run(
        command=("pytest",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=10,
    )

    assert isinstance(result, expected)

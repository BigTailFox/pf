from __future__ import annotations

from pathlib import Path

from pf.adapters.process import ProcessRunner
from pf.schemas.evaluation import (
    EnvironmentVariable,
    ProcessSpec,
    TestFail,
    TestOutcome,
    TestPass,
    ToolFailure,
)


class TestAdapter:
    """Run the complete configured test command without interpreting test output."""

    __test__ = False

    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    def run(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        environment: tuple[EnvironmentVariable, ...],
        failure_exit_codes: tuple[int, ...],
        timeout_seconds: int | None,
    ) -> TestOutcome:
        result = self._runner.run(
            ProcessSpec(
                argv=command,
                cwd=cwd.as_posix(),
                environment=environment,
                timeout_seconds=timeout_seconds,
            )
        )
        if result.timed_out:
            return ToolFailure(cause="TIMEOUT", stage="test", process=result)
        if result.exit_code == 0:
            return TestPass(process=result)
        if result.exit_code in failure_exit_codes:
            return TestFail(process=result)
        return ToolFailure(cause="TOOL_FAILURE", stage="test", process=result)

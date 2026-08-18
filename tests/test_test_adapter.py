from __future__ import annotations

from pathlib import Path

from pf.adapters.test_command import TestAdapter
from pf.schemas.evaluation import ProcessResult, ProcessSpec


class FailingTestRunner:
    def __init__(self) -> None:
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.spec = spec
        return ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout_summary="1 failed\n",
            stderr_summary="",
            stdout_tail="1 failed\n",
            stderr_tail="",
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

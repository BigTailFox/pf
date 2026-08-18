from __future__ import annotations

from pathlib import Path

from pf.adapters.ty import TyAdapter
from pf.schemas.evaluation import ProcessResult, ProcessSpec


class DiagnosticRunner:
    def __init__(self) -> None:
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.spec = spec
        return ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout_summary="error: incompatible call\n",
            stderr_summary="",
            stdout_tail="error: incompatible call\n",
            stderr_tail="",
        )


def test_ty_adapter_classifies_diagnostics_and_owns_target_argv(tmp_path: Path) -> None:
    runner = DiagnosticRunner()
    adapter = TyAdapter(runner)
    interpreter = tmp_path / ".venv" / "bin" / "python"

    result = adapter.check(
        interpreter=interpreter,
        package=tmp_path,
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=("--error", "possibly-unresolved-reference"),
        timeout_seconds=600,
    )

    assert result.status == "STATIC_FAIL"
    assert runner.spec is not None
    assert runner.spec.argv == (
        "ty",
        "check",
        "--python",
        interpreter.as_posix(),
        "--python-version",
        "3.11",
        "--python-platform",
        "linux",
        "--no-progress",
        "--color",
        "never",
        "--error",
        "possibly-unresolved-reference",
        tmp_path.as_posix(),
    )

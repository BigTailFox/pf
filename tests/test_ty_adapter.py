from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("target", "platform"),
    (
        ("aarch64-apple-darwin", "darwin"),
        ("x86_64-pc-windows-msvc", "win32"),
        ("wasm32-unknown-unknown", "all"),
    ),
)
def test_ty_adapter_maps_supported_and_unknown_targets(
    tmp_path: Path,
    target: str,
    platform: str,
) -> None:
    runner = DiagnosticRunner()

    TyAdapter(runner).check(
        interpreter=tmp_path / "python",
        package=tmp_path,
        python_minor="3.10",
        target=target,
        args=(),
        timeout_seconds=None,
    )

    assert runner.spec is not None
    option = runner.spec.argv.index("--python-platform")
    assert runner.spec.argv[option + 1] == platform


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "expected"),
    (
        (0, False, "STATIC_PASS"),
        (2, False, "TOOL_ERROR"),
        (None, True, "TIMEOUT"),
    ),
)
def test_ty_adapter_preserves_non_diagnostic_terminal_states(
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
                stdout_summary="",
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
                timed_out=timed_out,
            )

    result = TyAdapter(Runner()).check(
        interpreter=tmp_path / "python",
        package=tmp_path,
        python_minor="3.10",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=None,
    )

    assert result.status == expected

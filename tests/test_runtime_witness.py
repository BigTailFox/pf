from __future__ import annotations

from pathlib import Path
import os
import sys
import venv

import pytest

from pf.adapters.process import SecretRedactor, SubprocessRunner, read_process_output
from pf.runlog import RunLogStore
from pf.adapters.runtime_witness import RuntimeWitnessAdapter
from pf.schemas.evaluation import (
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
    RuntimeWitnessPlan,
    RuntimeWitnessResult,
    ToolFailure,
)


def _plan(
    module: str,
    *,
    operation: str = "import-module",
    name: str | None = None,
) -> RuntimeWitnessPlan:
    return RuntimeWitnessPlan.model_validate(
        {
            "diagnostic_identities": ("snapshot|demo.py|1|1|unresolved-import",),
            "managed_dependency": "demo",
            "operation": operation,
            "module": module,
            "owner": module if operation == "has-member" else None,
            "symbol_or_member": name,
        }
    )


class ResultRunner:
    def __init__(self, result: ProcessResult | ProcessTerminalUnavailable) -> None:
        self.result = result
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult | ProcessTerminalUnavailable:
        self.spec = spec
        return self.result


class TestRuntimeWitnessAdapter:
    def test_runtime_witness_handles_unavailable_process_terminal(
        self,
        tmp_path: Path,
    ) -> None:
        outcome = RuntimeWitnessAdapter(ResultRunner(ProcessTerminalUnavailable())).run(
            plan=_plan("json"),
            interpreter=Path(sys.executable),
            cwd=tmp_path,
            timeout_seconds=10,
        )

        assert isinstance(outcome, ToolFailure)
        assert isinstance(outcome.process, ProcessTerminalUnavailable)

    @pytest.mark.parametrize(
        ("plan", "expected"),
        (
            (_plan("json"), "PRESENT"),
            (
                _plan("pf_definitely_missing_runtime_witness_target"),
                "CONFIRMED_MISSING",
            ),
            (_plan("json", operation="import-symbol", name="loads"), "PRESENT"),
            (_plan("email", operation="import-symbol", name="parser"), "PRESENT"),
            (
                _plan("json", operation="import-symbol", name="definitely_missing"),
                "CONFIRMED_MISSING",
            ),
        ),
    )
    def test_runtime_witness_adapter_returns_the_planned_target_status(
        self,
        tmp_path: Path,
        plan: RuntimeWitnessPlan,
        expected: str,
    ) -> None:
        result = RuntimeWitnessAdapter(SubprocessRunner()).run(
            plan=plan,
            interpreter=Path(sys.executable),
            cwd=tmp_path,
            timeout_seconds=10,
        )

        assert isinstance(result, RuntimeWitnessResult)
        assert result.status == expected
        assert result.plan == plan

    @pytest.mark.parametrize("status", ("PRESENT", "NOT_APPLICABLE", "CONFIRMED_MISSING"))
    @pytest.mark.parametrize("stderr", ("", "SyntaxWarning: invalid escape\n", "Traceback: diagnostic only\n"))
    def test_runtime_witness_preserves_diagnostics(self, tmp_path: Path, status: str, stderr: str) -> None:
        process = ProcessResult(exit_code=0, duration_seconds=0,
                                stdout='{"status":"' + status + '"}\n', stderr=stderr)
        outcome = RuntimeWitnessAdapter(ResultRunner(process)).run(
            plan=_plan("json"), interpreter=Path(sys.executable), cwd=tmp_path, timeout_seconds=10,
        )
        assert isinstance(outcome, RuntimeWitnessResult)
        assert outcome.status == status
        assert outcome.process is process
        assert outcome.process.stderr == stderr

    @pytest.mark.parametrize("stderr", ("", "warning\n"))
    @pytest.mark.parametrize("stdout", (
        pytest.param("[" * 2000 + "]" * 2000, id="nested-json"),
        pytest.param('{"status":' + "1" * 5000 + "}\n", id="oversized-integer"),
        "not-json\n", ' {"status":"PRESENT"}\n', '{"status":"PRESENT"}',
        '{"status":"PRESENT"}\nextra\n', '{"status":"PRESENT","extra":0}\n',
        '{"status":"UNKNOWN"}\n', '{"status":[]}\n', '{"status":{}}\n',
        '{"status":null}\n', '[]\n', '{"status":"PRESENT"}\n{"status":"PRESENT"}\n',
    ))
    def test_runtime_witness_adapter_rejects_invalid_protocol_output(self, tmp_path: Path, stdout: str, stderr: str) -> None:
        process = ProcessResult(exit_code=0, duration_seconds=0, stdout=stdout, stderr=stderr)
        runner = ResultRunner(process)
        outcome = RuntimeWitnessAdapter(runner).run(
            plan=_plan("json"), interpreter=Path(sys.executable), cwd=tmp_path, timeout_seconds=10,
        )
        assert isinstance(outcome, ToolFailure)
        assert outcome.cause == "TOOL_FAILURE"
        assert outcome.process is process
        assert runner.spec is not None
        assert runner.spec.argv[1:3] == ("-I", "-c")

    @pytest.mark.parametrize("terminal", (
        {"exit_code": 1}, {"signal": 9}, {"start_error": "cannot start"},
        {"exit_code": 0, "stdout_complete": False}, {"exit_code": 0, "stderr_complete": False},
    ))
    def test_runtime_witness_requires_complete_normal_success(self, tmp_path: Path, terminal: dict) -> None:
        process = ProcessResult(**terminal, duration_seconds=0,
                                stdout='{"status":"PRESENT"}\n', stderr="warning\n")
        outcome = RuntimeWitnessAdapter(ResultRunner(process)).run(
            plan=_plan("json"), interpreter=Path(sys.executable), cwd=tmp_path, timeout_seconds=10,
        )
        assert isinstance(outcome, ToolFailure)
        assert outcome.cause == "TOOL_FAILURE"
        assert outcome.process is process

    @pytest.mark.parametrize("side_effect", (False, True))
    def test_isolated_import_keeps_warning_in_redacted_log(self, tmp_path: Path, side_effect: bool) -> None:
        environment = tmp_path / "environment"
        venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
        interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        setup = SubprocessRunner().run(ProcessSpec(
            argv=(str(interpreter), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"),
            cwd=str(tmp_path), timeout_seconds=10,
        ))
        assert isinstance(setup, ProcessResult) and setup.exit_code == 0
        # Install the local fixture directly into this isolated interpreter's site-packages.
        module = Path(setup.stdout.strip()) / "witness_fixture.py"
        module.write_text("import warnings\nwarnings.warn('top-secret import warning', SyntaxWarning)\n"
                          + ("raise RuntimeError('import side effect')\n" if side_effect else ""))
        logs = RunLogStore(root=tmp_path, run_id="witness-import")
        runner = SubprocessRunner(logs=logs, redactor=SecretRedactor(("top-secret",)), cache_limit=16)
        outcome = RuntimeWitnessAdapter(runner).run(
            plan=_plan("witness_fixture"), interpreter=interpreter, cwd=tmp_path, timeout_seconds=10,
        )
        assert isinstance(outcome, RuntimeWitnessResult)
        assert outcome.status == ("NOT_APPLICABLE" if side_effect else "PRESENT")
        output = read_process_output(runner, outcome.process)
        assert "SyntaxWarning: *** import warning" in output.stderr
        assert "top-secret" not in output.stderr
        assert logs.reference_for(outcome.process) is not None

    def test_runtime_witness_adapter_maps_nonzero_exit_to_tool_failure(
        self, tmp_path: Path
    ) -> None:
        runner = ResultRunner(
            ProcessResult(
                exit_code=2,
                signal=None,
                duration_seconds=0,
                stdout="",
                stderr="harness failed",
            )
        )

        outcome = RuntimeWitnessAdapter(runner).run(
            plan=_plan("json"),
            interpreter=Path(sys.executable),
            cwd=tmp_path,
            timeout_seconds=10,
        )

        assert isinstance(outcome, ToolFailure)
        assert outcome.stage == "witness"
        assert outcome.cause == "TOOL_FAILURE"

    def test_runtime_witness_adapter_maps_timeout_to_timeout(
        self, tmp_path: Path
    ) -> None:
        runner = ResultRunner(
            ProcessResult(
                exit_code=None,
                signal=9,
                duration_seconds=1,
                stdout="",
                stderr="",
                timed_out=True,
            )
        )

        outcome = RuntimeWitnessAdapter(runner).run(
            plan=_plan("json"),
            interpreter=Path(sys.executable),
            cwd=tmp_path,
            timeout_seconds=10,
        )

        assert isinstance(outcome, ToolFailure)
        assert outcome.stage == "witness"
        assert outcome.cause == "TIMEOUT"

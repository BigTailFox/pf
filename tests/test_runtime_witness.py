from __future__ import annotations

from pathlib import Path
import sys

import pytest

from pf.adapters.process import SubprocessRunner
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
        outcome = RuntimeWitnessAdapter(
            ResultRunner(ProcessTerminalUnavailable())
        ).run(
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

    @pytest.mark.parametrize(
        "result",
        (
            ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0,
                stdout="not-json\n",
                stderr="",
            ),
            ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0,
                stdout=' {"status":"PRESENT"}\n',
                stderr="",
            ),
            ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0,
                stdout='{"status":"PRESENT"}\n',
                stderr="unexpected warning",
            ),
        ),
        ids=(
            "invalid-json",
            "noncanonical-json",
            "unexpected-stderr",
        ),
    )
    def test_runtime_witness_adapter_rejects_invalid_protocol_output(
        self,
        tmp_path: Path,
        result: ProcessResult,
    ) -> None:
        runner = ResultRunner(result)

        outcome = RuntimeWitnessAdapter(runner).run(
            plan=_plan("json"),
            interpreter=Path(sys.executable),
            cwd=tmp_path,
            timeout_seconds=10,
        )

        assert isinstance(outcome, ToolFailure)
        assert outcome.stage == "witness"
        assert outcome.cause == "TOOL_FAILURE"
        assert runner.spec is not None
        assert runner.spec.argv[1:3] == ("-I", "-c")

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

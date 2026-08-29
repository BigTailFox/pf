from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf.adapters.test_command import ConfiguredVerifier
from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
    Signaled,
    StartFailed,
    TimedOut,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRequest,
)


class _Runner:
    def __init__(self, result: ProcessResult | ProcessTerminalUnavailable) -> None:
        self.result = result
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult | ProcessTerminalUnavailable:
        self.spec = spec
        return self.result


@pytest.mark.parametrize("exit_code", (1, 2, 3, 4, 5, 137))
def test_configured_verifier_treats_every_normal_nonzero_exit_as_rejected(
    tmp_path: Path,
    exit_code: int,
) -> None:
    runner = _Runner(
        ProcessResult(
            exit_code=exit_code,
            duration_seconds=0.1,
            stdout_complete=False,
            stderr_complete=False,
        )
    )

    run = ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("custom-verifier",),
            cwd=tmp_path,
            environment=(),
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierRejected)
    assert run.authoritative.terminal.exit_code == exit_code
    assert run.authoritative.reason == "verifier-exited-nonzero"
    assert run.diagnostics is not None
    assert run.diagnostics.process == runner.result
    assert runner.spec is not None
    assert runner.spec.argv == ("custom-verifier",)


def test_configured_verifier_passes_exit_zero_with_incomplete_output(
    tmp_path: Path,
) -> None:
    runner = _Runner(
        ProcessResult(
            exit_code=0,
            duration_seconds=0.1,
            stdout_complete=False,
            stderr_complete=False,
        )
    )

    run = ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("custom-verifier",),
            cwd=tmp_path,
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierPass)


@pytest.mark.parametrize(
    "command",
    (
        ("custom-verifier",),
        ("wrapper", "pytest"),
        ("pytest",),
        ("py.test",),
        ("python", "-m", "pytest"),
        ("python3.12", "-m", "pytest"),
    ),
)
def test_configured_verifier_terminal_authority_is_command_shape_independent(
    tmp_path: Path,
    command: tuple[str, ...],
) -> None:
    class ShapeRunner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            environment = {item.name: item.value for item in spec.environment}
            observer_directory = environment.get("PF_PYTEST_OBSERVER_DIR")
            nonce = environment.get("PF_PYTEST_OBSERVER_NONCE")
            if observer_directory is not None and nonce is not None:
                document = {
                    "execution_mode": "unknown",
                    "facts": [],
                    "finalized": True,
                    "protocol": "pf-pytest-observer-v1",
                    "pytest_version": "unknown",
                    "python_implementation": "cpython",
                    "python_minor": "3.12",
                    "run_nonce": nonce,
                }
                payload = (
                    json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
                )
                Path(observer_directory, f"summary-{'a' * 32}.json").write_text(
                    payload,
                    encoding="utf-8",
                )
            return ProcessResult(exit_code=4, duration_seconds=0.1)

    run = ConfiguredVerifier(ShapeRunner()).run(
        VerifierRequest(command=command, cwd=tmp_path, timeout_seconds=30)
    )

    assert isinstance(run.authoritative, VerifierRejected)
    assert run.authoritative.terminal.exit_code == 4


def test_configured_verifier_timeout_wins_over_cleanup_exit(tmp_path: Path) -> None:
    runner = _Runner(
        ProcessResult(
            exit_code=143,
            duration_seconds=30.1,
            timed_out=True,
        )
    )

    run = ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("custom-verifier",),
            cwd=tmp_path,
            environment=(),
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierIndeterminate)
    assert isinstance(run.authoritative.terminal, TimedOut)
    assert run.authoritative.reason == "process-timed-out"


def test_direct_pytest_timeout_does_not_require_a_final_observer_artifact(
    tmp_path: Path,
) -> None:
    runner = _Runner(
        ProcessResult(
            signal=15,
            duration_seconds=30.1,
            timed_out=True,
        )
    )

    run = ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("pytest",),
            cwd=tmp_path,
            environment=(),
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierIndeterminate)
    assert isinstance(run.authoritative.terminal, TimedOut)


def test_configured_verifier_maps_typed_terminal_unavailable_to_indeterminate(
    tmp_path: Path,
) -> None:
    runner = _Runner(ProcessTerminalUnavailable())

    run = ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("custom-verifier",),
            cwd=tmp_path,
            environment=(),
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierIndeterminate)
    assert run.authoritative.terminal.kind == "unavailable"
    assert run.authoritative.reason == "terminal-unavailable"


def test_configured_verifier_maps_native_signal_to_indeterminate(
    tmp_path: Path,
) -> None:
    runner = _Runner(ProcessResult(signal=9, duration_seconds=0.1))

    run = ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("custom-verifier",),
            cwd=tmp_path,
            environment=(),
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierIndeterminate)
    assert run.authoritative.terminal == Signaled(signal=9)
    assert run.authoritative.reason == "process-signaled"


def test_configured_verifier_maps_start_failure_to_indeterminate(
    tmp_path: Path,
) -> None:
    runner = _Runner(
        ProcessResult(start_error="executable not found", duration_seconds=0.1)
    )

    run = ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("missing-verifier",),
            cwd=tmp_path,
            environment=(),
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierIndeterminate)
    assert isinstance(run.authoritative.terminal, StartFailed)
    assert run.authoritative.reason == "process-start-failed"


def test_configured_verifier_rejects_an_invalid_process_observation(
    tmp_path: Path,
) -> None:
    invalid = ProcessResult.model_construct(
        exit_code=None,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
        stdout_complete=True,
        stderr_complete=True,
        timed_out=True,
        start_error="start failed",
    )
    runner = _Runner(invalid)

    with pytest.raises(InfrastructureError, match="invalid verifier process terminal"):
        ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=("custom-verifier",),
                cwd=tmp_path,
                environment=(),
                timeout_seconds=30,
            )
        )


def test_configured_verifier_wraps_an_unexpected_runner_exception(
    tmp_path: Path,
) -> None:
    class BrokenRunner:
        def run(
            self,
            spec: ProcessSpec,
        ) -> ProcessResult | ProcessTerminalUnavailable:
            raise RuntimeError(f"runner exploded for {spec.argv[0]}")

    with pytest.raises(
        InfrastructureError,
        match="configured verifier process failed",
    ) as captured:
        ConfiguredVerifier(BrokenRunner()).run(
            VerifierRequest(
                command=("custom-verifier",),
                cwd=tmp_path,
                environment=(),
                timeout_seconds=30,
            )
        )

    assert captured.value.detail == "runner exploded for custom-verifier"

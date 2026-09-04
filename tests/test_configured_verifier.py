from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import ConfiguredVerifier
from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    EnvironmentVariable,
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
        if _is_direct_pytest_command(spec.argv):
            _write_observer_summary(spec)
        return self.result


def _is_direct_pytest_command(command: tuple[str, ...]) -> bool:
    executable = command[0].replace("\\", "/").rsplit("/", 1)[-1]
    if executable.casefold().endswith(".exe"):
        executable = executable[:-4]
    name = executable.casefold()
    if name in {"pytest", "py.test"}:
        return True
    return name.startswith("python") and command[1:3] == ("-m", "pytest")


def _write_observer_summary(spec: ProcessSpec) -> None:
    environment = {item.name: item.value for item in spec.environment}
    observer_directory = environment.get("PF_PYTEST_OBSERVER_DIR")
    nonce = environment.get("PF_PYTEST_OBSERVER_NONCE")
    if observer_directory is None or nonce is None:
        return
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
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    Path(observer_directory, f"summary-{'a' * 32}.json").write_text(
        payload,
        encoding="utf-8",
    )


def _overlay_tokens(argv: tuple[str, ...]) -> tuple[str, ...]:
    try:
        separator = argv.index("--")
    except ValueError:
        separator = len(argv)
    overlay = argv[separator - 3 : separator]
    assert overlay[0] == "--maxfail=1"
    assert overlay[1] == "-o"
    assert overlay[2].startswith("cache_dir=")
    return overlay


def _cache_dir(argv: tuple[str, ...]) -> Path:
    overlay = _overlay_tokens(argv)
    return Path(overlay[2].removeprefix("cache_dir="))


def _user_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    tokens = list(argv)
    overlay = _overlay_tokens(argv)
    overlay_at = (
        len(tokens) - len(overlay) if "--" not in tokens else tokens.index("--") - 3
    )
    del tokens[overlay_at : overlay_at + 3]
    index = 0
    while index < len(tokens) - 1:
        if tokens[index] == "-p" and tokens[index + 1].startswith("_pf_pytest_"):
            del tokens[index : index + 2]
            continue
        index += 1
    return tuple(tokens)


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
    runner = _Runner(
        ProcessResult(
            exit_code=4,
            duration_seconds=0.1,
        )
    )

    run = ConfiguredVerifier(runner).run(
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


def test_verifier_run_excludes_runtime_only_additions(tmp_path: Path) -> None:
    run = ConfiguredVerifier(
        _Runner(ProcessResult(exit_code=1, duration_seconds=0.1))
    ).run(
        VerifierRequest(
            command=("custom-verifier",),
            cwd=tmp_path,
            timeout_seconds=30,
        )
    )

    dumped = run.model_dump(mode="json")
    assert "failed_case_additions" not in dumped
    assert "diagnostics" not in dumped


@pytest.mark.parametrize(
    "command",
    (
        ("pytest",),
        ("py.test", "tests"),
        ("python", "-m", "pytest", "-q"),
        ("python3.12", "-m", "pytest"),
    ),
)
def test_direct_pytest_appends_maxfail_and_isolated_cache_dir(
    tmp_path: Path,
    command: tuple[str, ...],
) -> None:
    runner = _Runner(ProcessResult(exit_code=0, duration_seconds=0.1))

    ConfiguredVerifier(runner).run(
        VerifierRequest(command=command, cwd=tmp_path, timeout_seconds=30)
    )

    assert runner.spec is not None
    overlay = _overlay_tokens(runner.spec.argv)
    cache_dir = _cache_dir(runner.spec.argv)
    assert overlay[0] == "--maxfail=1"
    assert _user_argv(runner.spec.argv) == command
    assert not cache_dir.exists()


def test_direct_pytest_keeps_user_tokens_and_inserts_overlay_before_separator(
    tmp_path: Path,
) -> None:
    command = (
        "pytest",
        "-x",
        "--exitfirst",
        "--maxfail=5",
        "-o",
        "cache_dir=/tmp/user-cache",
        "--maxfail",
        "9",
        "tests",
        "--",
        "-k",
        "not overlay",
    )
    runner = _Runner(ProcessResult(exit_code=0, duration_seconds=0.1))

    ConfiguredVerifier(runner).run(
        VerifierRequest(command=command, cwd=tmp_path, timeout_seconds=30)
    )

    assert runner.spec is not None
    argv = runner.spec.argv
    overlay = _overlay_tokens(argv)
    assert _user_argv(argv) == command
    assert argv[argv.index("--") :] == ("--", "-k", "not overlay")
    assert overlay == argv[argv.index("--") - 3 : argv.index("--")]
    assert overlay[2] != "cache_dir=/tmp/user-cache"


def test_generic_command_argv_is_unchanged(tmp_path: Path) -> None:
    runner = _Runner(ProcessResult(exit_code=0, duration_seconds=0.1))
    command = ("wrapper", "pytest", "--maxfail=5")

    ConfiguredVerifier(runner).run(
        VerifierRequest(command=command, cwd=tmp_path, timeout_seconds=30)
    )

    assert runner.spec is not None
    assert runner.spec.argv == command


def test_direct_pytest_overlay_last_wins_for_maxfail_and_cache_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--maxfail=9 -o cache_dir=/tmp/addopts-cache")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --maxfail=8\ncache_dir = ini-cache\n",
        encoding="utf-8",
    )
    (tmp_path / "test_overlay.py").write_text(
        "from pathlib import Path\n"
        "def test_first():\n"
        "    Path('first').write_text('ran')\n"
        "    assert False\n"
        "def test_second():\n"
        "    Path('second').write_text('ran')\n",
        encoding="utf-8",
    )
    user_cache = tmp_path / "user-cache"
    user_cache.mkdir()

    run = ConfiguredVerifier(SubprocessRunner()).run(
        VerifierRequest(
            command=(
                sys.executable,
                "-m",
                "pytest",
                "--maxfail=7",
                "-o",
                f"cache_dir={user_cache.as_posix()}",
                "test_overlay.py",
            ),
            cwd=tmp_path,
            environment=(
                EnvironmentVariable(name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"),
            ),
            timeout_seconds=30,
        )
    )

    assert isinstance(run.authoritative, VerifierRejected)
    assert (tmp_path / "first").read_text(encoding="utf-8") == "ran"
    assert not (tmp_path / "second").exists()
    assert not any(user_cache.iterdir())
    assert not (tmp_path / "ini-cache").exists()

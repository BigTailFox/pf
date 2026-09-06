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
    Unavailable,
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
        _write_observer_summary(spec)
        return self.result


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


class TestConfiguredVerifierOutcome:
    @pytest.mark.parametrize(
        "command,exit_code",
        [
            *((("custom-verifier",), code) for code in (1, 2, 3, 4, 5, 137)),
            *(
                (command, 4)
                for command in (
                    ("wrapper", "pytest"),
                    ("pytest",),
                    ("py.test",),
                    ("python", "-m", "pytest"),
                    ("python3.12", "-m", "pytest"),
                )
            ),
        ],
    )
    def test_run_rejects_normal_nonzero_exit(
        self, tmp_path: Path, command: tuple[str, ...], exit_code: int
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
            VerifierRequest(command=command, cwd=tmp_path, timeout_seconds=30)
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert run.authoritative.terminal.exit_code == exit_code
        assert run.authoritative.reason == "verifier-exited-nonzero"
        assert run.diagnostics is not None
        assert run.diagnostics.process == runner.result

    def test_run_passes_normal_zero_exit(
        self,
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
        "command,process,terminal,reason",
        [
            pytest.param(
                ("custom-verifier",),
                ProcessResult(exit_code=143, duration_seconds=30.1, timed_out=True),
                TimedOut(),
                "process-timed-out",
                id="timeout-after-cleanup-exit",
            ),
            pytest.param(
                ("pytest",),
                ProcessResult(signal=15, duration_seconds=30.1, timed_out=True),
                TimedOut(),
                "process-timed-out",
                id="pytest-timeout-without-summary",
            ),
            pytest.param(
                ("custom-verifier",),
                ProcessTerminalUnavailable(),
                Unavailable(),
                "terminal-unavailable",
                id="unavailable",
            ),
            pytest.param(
                ("custom-verifier",),
                ProcessResult(signal=9, duration_seconds=0.1),
                Signaled(signal=9),
                "process-signaled",
                id="signal",
            ),
            pytest.param(
                ("missing-verifier",),
                ProcessResult(start_error="executable not found", duration_seconds=0.1),
                StartFailed(),
                "process-start-failed",
                id="start-failed",
            ),
        ],
    )
    def test_run_reports_indeterminate_terminal(
        self, tmp_path, command, process, terminal, reason
    ) -> None:
        class InterruptedRunner(_Runner):
            def run(self, spec: ProcessSpec):
                self.spec = spec
                return self.result

        runner = InterruptedRunner(process)
        run = ConfiguredVerifier(runner).run(
            VerifierRequest(command=command, cwd=tmp_path, timeout_seconds=30)
        )

        assert isinstance(run.authoritative, VerifierIndeterminate)
        assert run.authoritative.terminal == terminal
        assert run.authoritative.reason == reason

    def test_run_rejects_invalid_process_observation(
        self,
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

        with pytest.raises(
            InfrastructureError, match="invalid verifier process terminal"
        ):
            ConfiguredVerifier(runner).run(
                VerifierRequest(
                    command=("custom-verifier",),
                    cwd=tmp_path,
                    environment=(),
                    timeout_seconds=30,
                )
            )

    def test_run_wraps_unexpected_runner_exception(
        self,
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

    def test_model_dump_omits_runtime_only_evidence(self, tmp_path: Path) -> None:
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


class TestConfiguredVerifierCommand:
    @pytest.mark.parametrize(
        "command",
        (
            ("pytest",),
            ("py.test", "tests"),
            ("python", "-m", "pytest", "-q"),
            ("python3.12", "-m", "pytest"),
        ),
    )
    def test_run_isolates_direct_pytest_options(
        self,
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

    def test_run_preserves_user_tokens_around_separator(
        self,
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

    def test_run_preserves_generic_command(self, tmp_path: Path) -> None:
        runner = _Runner(ProcessResult(exit_code=0, duration_seconds=0.1))
        command = ("wrapper", "pytest", "--maxfail=5")

        ConfiguredVerifier(runner).run(
            VerifierRequest(command=command, cwd=tmp_path, timeout_seconds=30)
        )

        assert runner.spec is not None
        assert runner.spec.argv == command

    def test_run_enforces_fail_fast_and_isolates_cache(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "PYTEST_ADDOPTS", "--maxfail=9 -o cache_dir=/tmp/addopts-cache"
        )
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
                    EnvironmentVariable(
                        name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"
                    ),
                ),
                timeout_seconds=30,
            )
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert (tmp_path / "first").read_text(encoding="utf-8") == "ran"
        assert not (tmp_path / "second").exists()
        assert not any(user_cache.iterdir())
        assert not (tmp_path / "ini-cache").exists()

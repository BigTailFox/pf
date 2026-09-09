from __future__ import annotations

from pf.cancellation import Cancellation

import json
from collections.abc import Callable
from pathlib import Path
import tempfile
from threading import Event

import pytest

from pf.adapters.pytest_progress import PytestProgressMonitor
from pf.adapters.test_command import ConfiguredVerifier
from pf.schemas.evaluation import (
    ProcessResult,
    ProcessSpec,
    StageProgress,
    VerifierPass,
    VerifierRequest,
)


def _canonical(document: object) -> str:
    return (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    )


ProgressDocument = dict[str, object] | Callable[[str], dict[str, object]]


class ProgressRunner:
    def __init__(self, progress_document: ProgressDocument) -> None:
        self._progress_document = progress_document

    def run(self, spec: ProcessSpec, *, cancellation: Cancellation | None = None) -> ProcessResult:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        environment = {item.name: item.value for item in spec.environment}
        nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
        evidence = Path(environment["PF_PYTEST_OBSERVER_DIR"])
        summary = {
            "execution_mode": "serial",
            "facts": [],
            "finalized": True,
            "protocol": "pf-pytest-observer-v1",
            "pytest_version": "9.1.1",
            "python_implementation": "cpython",
            "python_minor": "3.10",
            "run_nonce": nonce,
        }
        (evidence / f"summary-{'a' * 32}.json").write_text(
            _canonical(summary),
            encoding="utf-8",
        )
        progress = Path(environment["PF_PYTEST_PROGRESS_DIR"])
        document = (
            self._progress_document(nonce)
            if isinstance(self._progress_document, Callable)
            else self._progress_document
        )
        (progress / "progress.json").write_text(
            _canonical(document),
            encoding="utf-8",
        )
        return ProcessResult(exit_code=0, signal=None, duration_seconds=0.1)


def _progress_document(nonce: str, **changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "completed": 3,
        "protocol": "pf-pytest-progress-v1",
        "run_nonce": nonce,
        "total": 8,
        "unit": "tests",
    }
    document.update(changes)
    return document


class TestConfiguredVerifierProgress:
    def test_run_reports_valid_direct_pytest_progress(self, tmp_path: Path) -> None:
        observed: list[StageProgress | None] = []

        result = ConfiguredVerifier(ProgressRunner(_progress_document)).run(
            VerifierRequest(command=("pytest",), cwd=tmp_path, timeout_seconds=30),
            progress=observed.append,
        )

        assert isinstance(result.authoritative, VerifierPass)
        assert observed == [StageProgress(completed=3, total=8, unit="tests")]

    @pytest.mark.parametrize(
        "document",
        (
            {"completed": 3},
            lambda nonce: _progress_document("0" * 32),
            lambda nonce: _progress_document(nonce, completed=9),
            lambda nonce: _progress_document(nonce, completed=True),
            lambda nonce: _progress_document(nonce, unexpected="field"),
        ),
        ids=(
            "missing-fields",
            "wrong-nonce",
            "over-total",
            "bool",
            "unknown-field",
        ),
    )
    def test_run_ignores_invalid_progress_without_changing_outcome(
        self,
        tmp_path: Path,
        document: ProgressDocument,
    ) -> None:
        observed: list[StageProgress | None] = []

        result = ConfiguredVerifier(ProgressRunner(document)).run(
            VerifierRequest(command=("pytest",), cwd=tmp_path, timeout_seconds=30),
            progress=observed.append,
        )

        assert isinstance(result.authoritative, VerifierPass)
        assert observed == []

    def test_run_keeps_generic_command_progress_indeterminate(
        self,
        tmp_path: Path,
    ) -> None:
        observed: list[StageProgress | None] = []

        class PassRunner:
            def run(self, spec: ProcessSpec, *, cancellation: Cancellation | None = None) -> ProcessResult:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                return ProcessResult(exit_code=0, signal=None, duration_seconds=0.1)

        result = ConfiguredVerifier(PassRunner()).run(
            VerifierRequest(
                command=("custom-test-runner",),
                cwd=tmp_path,
                timeout_seconds=30,
            ),
            progress=observed.append,
        )

        assert isinstance(result.authoritative, VerifierPass)
        assert observed == []

    def test_run_ignores_progress_cleanup_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original_temporary_directory = tempfile.TemporaryDirectory

        class ProgressCleanupFails:
            def __init__(self, *, prefix: str) -> None:
                self._temporary = original_temporary_directory(prefix=prefix)
                self.name = self._temporary.name
                self._is_progress = prefix == "pf-pytest-progress-"

            def cleanup(self) -> None:
                self._temporary.cleanup()
                if self._is_progress:
                    raise OSError("progress cleanup failed")

        monkeypatch.setattr(
            "pf.adapters.test_command.tempfile.TemporaryDirectory",
            ProgressCleanupFails,
        )
        observed: list[StageProgress | None] = []

        result = ConfiguredVerifier(ProgressRunner(_progress_document)).run(
            VerifierRequest(command=("pytest",), cwd=tmp_path, timeout_seconds=30),
            progress=observed.append,
        )

        assert isinstance(result.authoritative, VerifierPass)
        assert observed == [StageProgress(completed=3, total=8, unit="tests")]


def _write_progress(
    directory: Path, *, nonce: str, completed: int, total: int
) -> None:
    payload = _canonical(
        {
            "completed": completed,
            "protocol": "pf-pytest-progress-v1",
            "run_nonce": nonce,
            "total": total,
            "unit": "tests",
        }
    )
    temporary = directory / "progress.json.tmp"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(directory / "progress.json")


class TestPytestProgressMonitor:
    def test_stop_invalidates_a_monitor_with_a_stubborn_worker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        observed: list[StageProgress | None] = []
        monitor = PytestProgressMonitor(
            tmp_path,
            nonce="0" * 32,
            consume=observed.append,
        )
        monitor.start()
        monkeypatch.setattr(monitor._thread, "is_alive", lambda: True)

        monitor.stop()

        assert observed == []

    def test_start_stop_contains_progress_consumer_failure(
        self,
        tmp_path: Path,
    ) -> None:
        consumed = Event()
        calls = 0
        nonce = "0" * 32

        def consume(progress: StageProgress | None) -> None:
            nonlocal calls
            calls += 1
            consumed.set()
            raise KeyboardInterrupt

        monitor = PytestProgressMonitor(
            tmp_path,
            nonce=nonce,
            consume=consume,
        )
        monitor.start()
        _write_progress(tmp_path, nonce=nonce, completed=1, total=2)
        assert consumed.wait(timeout=1)
        monitor.stop()

        assert calls == 1

    def test_start_stop_freezes_last_value_after_regression(
        self,
        tmp_path: Path,
    ) -> None:
        nonce = "0" * 32
        first = StageProgress(completed=3, total=8, unit="tests")
        observed: list[StageProgress | None] = []
        first_seen = Event()

        def consume(progress: StageProgress | None) -> None:
            observed.append(progress)
            first_seen.set()

        monitor = PytestProgressMonitor(
            tmp_path,
            nonce=nonce,
            consume=consume,
        )
        monitor.start()
        _write_progress(tmp_path, nonce=nonce, completed=3, total=8)
        assert first_seen.wait(timeout=1)
        first_seen.clear()
        _write_progress(tmp_path, nonce=nonce, completed=2, total=8)
        assert not first_seen.wait(timeout=0.5)
        monitor.stop()

        assert observed == [first]

    @pytest.mark.parametrize(
        "fault",
        ("os-error", "temporarily-missing"),
    )
    def test_start_stop_freezes_last_value_after_read_failure(
        self,
        tmp_path: Path,
        fault: str,
    ) -> None:
        nonce = "0" * 32
        first = StageProgress(completed=320, total=842, unit="tests")
        observed: list[StageProgress | None] = []
        first_seen = Event()

        def consume(progress: StageProgress | None) -> None:
            observed.append(progress)
            first_seen.set()

        monitor = PytestProgressMonitor(
            tmp_path,
            nonce=nonce,
            consume=consume,
        )
        monitor.start()
        _write_progress(tmp_path, nonce=nonce, completed=320, total=842)
        assert first_seen.wait(timeout=1)
        first_seen.clear()
        progress_path = tmp_path / "progress.json"
        if fault == "temporarily-missing":
            progress_path.unlink()
        else:
            progress_path.unlink()
            progress_path.mkdir()
        assert not first_seen.wait(timeout=0.5)
        monitor.stop()

        assert observed == [first]

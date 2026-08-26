from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
import tempfile
import time

import pytest

from pf.adapters.pytest_progress import PytestProgressMonitor
from pf.adapters.test_command import TestAdapter
from pf.schemas.evaluation import ProcessResult, ProcessSpec, StageProgress, TestPass


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

    def run(self, spec: ProcessSpec) -> ProcessResult:
        environment = {item.name: item.value for item in spec.environment}
        nonce = environment["PF_PYTEST_WITNESS_NONCE"]
        evidence = Path(environment["PF_PYTEST_WITNESS_DIR"])
        summary = {
            "execution_mode": "serial",
            "facts": [],
            "finalized": True,
            "protocol": "pf-pytest-failure-witness-v1",
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


def test_direct_serial_pytest_reports_valid_determinate_progress(
    tmp_path: Path,
) -> None:
    observed: list[StageProgress | None] = []

    result = TestAdapter(ProgressRunner(_progress_document)).run(
        command=("pytest",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=30,
        progress=observed.append,
    )

    assert isinstance(result, TestPass)
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
    ids=("missing-fields", "wrong-nonce", "over-total", "bool", "unknown-field"),
)
def test_invalid_progress_is_ignored_without_changing_test_outcome(
    tmp_path: Path,
    document: ProgressDocument,
) -> None:
    observed: list[StageProgress | None] = []

    result = TestAdapter(ProgressRunner(document)).run(
        command=("pytest",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=30,
        progress=observed.append,
    )

    assert isinstance(result, TestPass)
    assert observed == []


def test_generic_test_command_never_claims_determinate_progress(tmp_path: Path) -> None:
    observed: list[StageProgress | None] = []

    class PassRunner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return ProcessResult(exit_code=0, signal=None, duration_seconds=0.1)

    result = TestAdapter(PassRunner()).run(
        command=("custom-test-runner",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=30,
        progress=observed.append,
    )

    assert isinstance(result, TestPass)
    assert observed == []


def test_progress_cleanup_failure_does_not_change_test_outcome(
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

    result = TestAdapter(ProgressRunner(_progress_document)).run(
        command=("pytest",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=30,
        progress=observed.append,
    )

    assert isinstance(result, TestPass)
    assert observed == [StageProgress(completed=3, total=8, unit="tests")]


def test_invalid_progress_after_a_valid_snapshot_freezes_the_last_value(
    tmp_path: Path,
) -> None:
    class RegressingProgressRunner(ProgressRunner):
        def run(self, spec: ProcessSpec) -> ProcessResult:
            environment = {item.name: item.value for item in spec.environment}
            progress = Path(environment["PF_PYTEST_PROGRESS_DIR"])
            nonce = environment["PF_PYTEST_WITNESS_NONCE"]
            (progress / "progress.json").write_text(
                _canonical(_progress_document(nonce, completed=3)),
                encoding="utf-8",
            )
            time.sleep(0.15)
            (progress / "progress.json").write_text(
                _canonical(_progress_document(nonce, completed=2)),
                encoding="utf-8",
            )
            time.sleep(0.15)
            return super().run(spec)

    observed: list[StageProgress | None] = []

    result = TestAdapter(RegressingProgressRunner(_progress_document)).run(
        command=("pytest",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=30,
        progress=observed.append,
    )

    assert isinstance(result, TestPass)
    assert observed == [StageProgress(completed=3, total=8, unit="tests")]


@pytest.mark.parametrize(
    "failed_read",
    (OSError("transient atomic snapshot read"), None),
    ids=("os-error", "temporarily-missing"),
)
def test_snapshot_read_failure_freezes_the_last_visible_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_read: OSError | None,
) -> None:
    first = StageProgress(completed=320, total=842, unit="tests")
    snapshots: list[StageProgress | OSError | None] = [
        first,
        failed_read,
        StageProgress(completed=321, total=842, unit="tests"),
    ]

    def read_snapshot(path: Path, *, nonce: str) -> StageProgress | None:
        del path, nonce
        snapshot = snapshots.pop(0)
        if isinstance(snapshot, OSError):
            raise snapshot
        return snapshot

    monkeypatch.setattr("pf.adapters.pytest_progress._read_progress", read_snapshot)
    observed: list[StageProgress | None] = []
    monitor = PytestProgressMonitor(
        tmp_path,
        nonce="0" * 32,
        consume=observed.append,
    )

    monitor._poll()
    monitor._poll()
    monitor._poll()

    assert observed == [first]

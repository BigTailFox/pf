from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path

import pytest

from pf.adapters.test_command import TestAdapter
from pf.schemas.evaluation import (
    ProcessResult,
    ProcessSpec,
    TestFail,
    TestPass,
    ToolFailure,
)


ArtifactWriter = Callable[[Path, str], None]


def _document(
    nonce: str,
    *,
    facts: tuple[tuple[str, str], ...] = (("TEST_FAILED", "call"),),
    **changes: object,
) -> dict[str, object]:
    document: dict[str, object] = {
        "execution_mode": "serial",
        "facts": [{"kind": kind, "phase": phase} for kind, phase in facts],
        "finalized": True,
        "protocol": "pf-pytest-failure-witness-v1",
        "pytest_version": "9.1.1",
        "python_implementation": "cpython",
        "python_minor": "3.10",
        "run_nonce": nonce,
    }
    document.update(changes)
    return document


def _canonical(document: object) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_summary(
    directory: Path,
    document: object,
    *,
    token: str = "a" * 32,
    payload: bytes | None = None,
) -> None:
    (directory / f"summary-{token}.json").write_bytes(
        _canonical(document) if payload is None else payload
    )


class EvidenceRunner:
    def __init__(self, writer: ArtifactWriter, *, exit_code: int = 1) -> None:
        self._writer = writer
        self._exit_code = exit_code

    def run(self, spec: ProcessSpec) -> ProcessResult:
        environment = {item.name: item.value for item in spec.environment}
        self._writer(
            Path(environment["PF_PYTEST_WITNESS_DIR"]),
            environment["PF_PYTEST_WITNESS_NONCE"],
        )
        return ProcessResult(
            exit_code=self._exit_code,
            signal=None,
            duration_seconds=0.1,
        )


def _run(tmp_path: Path, writer: ArtifactWriter):
    return TestAdapter(EvidenceRunner(writer)).run(
        command=("pytest",),
        cwd=tmp_path,
        environment=(),
        failure_exit_codes=(1,),
        timeout_seconds=30,
    )


class TestPytestWitnessArtifactProtocol:
    @pytest.mark.parametrize(
        "writer",
        (
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce),
                payload=b"{",
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce),
                payload=(json.dumps(_document(nonce), indent=2) + "\n").encode(),
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce, unexpected="field"),
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce, execution_mode=[]),
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce),
                payload=b"[" * 1100 + b"]" * 1100,
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document("0" * 32),
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce, finalized=False),
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce, facts=(("UNKNOWN", "call"),)),
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(
                    nonce,
                    facts=(
                        ("TEST_FAILED", "teardown"),
                        ("TEST_FAILED", "call"),
                    ),
                ),
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(
                    nonce,
                    facts=(
                        ("TEST_FAILED", "call"),
                        ("TEST_FAILED", "call"),
                    ),
                ),
            ),
            lambda directory, nonce: (directory / "leftover.tmp").write_bytes(
                _canonical(_document(nonce))
            ),
        ),
        ids=(
            "malformed-json",
            "non-canonical",
            "unknown-field",
            "execution-mode-container",
            "deeply-nested-json",
            "wrong-nonce",
            "not-finalized",
            "unknown-fact",
            "unsorted-facts",
            "duplicate-facts",
            "unknown-file",
        ),
    )
    def test_protocol_rejects_every_noncanonical_artifact(
        self,
        tmp_path: Path,
        writer: ArtifactWriter,
    ) -> None:
        result = _run(tmp_path, writer)

        assert isinstance(result, ToolFailure)
        assert result.summary_code == "pytest-evidence-invalid"

    @pytest.mark.parametrize(
        "writer",
        (
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce),
                payload=b"\xff",
            ),
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce),
                payload=b"x" * 4097,
            ),
            lambda directory, nonce: (
                (directory.parent / "outside.json").write_bytes(
                    _canonical(_document(nonce))
                ),
                (directory / f"summary-{'c' * 32}.json").symlink_to(
                    directory.parent / "outside.json"
                ),
            ),
        ),
        ids=("non-utf8", "oversize", "symlink"),
    )
    def test_protocol_reads_only_bounded_regular_utf8_files(
        self,
        tmp_path: Path,
        writer: ArtifactWriter,
    ) -> None:
        result = _run(tmp_path, writer)

        assert isinstance(result, ToolFailure)
        assert result.summary_code == "pytest-evidence-invalid"

    @pytest.mark.parametrize(
        ("count", "expected"),
        ((1024, TestFail), (1025, ToolFailure)),
        ids=("at-limit", "over-limit"),
    )
    def test_protocol_bounds_the_number_of_final_summaries(
        self,
        tmp_path: Path,
        count: int,
        expected: type[TestFail] | type[ToolFailure],
    ) -> None:
        def write_many(directory: Path, nonce: str) -> None:
            document = _document(nonce)
            for index in range(count):
                _write_summary(directory, document, token=f"{index:032x}")

        result = _run(tmp_path, write_many)

        assert isinstance(result, expected)
        if isinstance(result, ToolFailure):
            assert result.summary_code == "pytest-evidence-invalid"

    def test_protocol_stops_enumerating_at_the_summary_limit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        evidence_directory: Path | None = None
        original_iterdir = Path.iterdir

        def remember_directory(directory: Path, nonce: str) -> None:
            nonlocal evidence_directory
            evidence_directory = directory

        def bounded_artifacts(directory: Path):
            if directory != evidence_directory:
                yield from original_iterdir(directory)
                return
            for index in range(1025):
                yield directory / f"summary-{index:032x}.json"
            raise AssertionError("reader enumerated beyond its bounded sentinel")

        monkeypatch.setattr(Path, "iterdir", bounded_artifacts)

        result = _run(tmp_path, remember_directory)

        assert isinstance(result, ToolFailure)
        assert result.summary_code == "pytest-evidence-invalid"

    @pytest.mark.parametrize(
        ("field", "value"),
        (
            ("execution_mode", "unknown"),
            ("python_minor", "3.11"),
            ("pytest_version", "8.4.2"),
        ),
    )
    def test_protocol_rejects_identity_conflicts_between_summaries(
        self,
        tmp_path: Path,
        field: str,
        value: str,
    ) -> None:
        def write_conflict(directory: Path, nonce: str) -> None:
            _write_summary(directory, _document(nonce), token="d" * 32)
            conflicting = _document(nonce)
            conflicting[field] = value
            _write_summary(
                directory,
                conflicting,
                token="e" * 32,
            )

        result = _run(tmp_path, write_conflict)

        assert isinstance(result, ToolFailure)
        assert result.summary_code == "pytest-evidence-invalid"

    def test_protocol_unions_equivalent_multiprocess_summaries(
        self, tmp_path: Path
    ) -> None:
        def write_equivalent(directory: Path, nonce: str) -> None:
            _write_summary(
                directory,
                _document(nonce, facts=(("COLLECTION_FAILED", "collect"),)),
                token="f" * 32,
            )
            _write_summary(
                directory,
                _document(nonce, facts=(("TEST_FAILED", "call"),)),
                token="1" * 32,
            )
            _write_summary(
                directory,
                _document(nonce, facts=(("TEST_FAILED", "call"),)),
                token="2" * 32,
            )

        result = _run(tmp_path, write_equivalent)

        assert isinstance(result, TestFail)


class TestPytestWitnessProfileQualification:
    @pytest.mark.parametrize(
        "pytest_version",
        ("6.2.5", "6.9.9", "7.0.1", "7.4.4", "8.0.2", "8.4.2", "9.0.2", "9.1.1"),
    )
    @pytest.mark.parametrize("python_minor", ("3.10", "3.11", "3.12"))
    def test_profile_qualification_authorizes_test_failure(
        self,
        tmp_path: Path,
        pytest_version: str,
        python_minor: str,
    ) -> None:
        def write_qualified(directory: Path, nonce: str) -> None:
            _write_summary(
                directory,
                _document(
                    nonce,
                    pytest_version=pytest_version,
                    python_minor=python_minor,
                ),
            )

        result = _run(tmp_path, write_qualified)

        assert isinstance(result, TestFail)

    @pytest.mark.parametrize(
        ("pytest_version", "python_minor", "execution_mode"),
        (
            ("6.2.4", "3.10", "serial"),
            ("7.0.0", "3.10", "serial"),
            ("8.0.1", "3.10", "serial"),
            ("9.0.1", "3.10", "serial"),
            ("10.0.0", "3.10", "serial"),
            ("9.1.1rc1", "3.10", "serial"),
            ("9.1.1+local", "3.10", "serial"),
            ("vendor", "3.10", "serial"),
            ("9.1.1", "3.9", "serial"),
            ("9.1.1", "3.13", "serial"),
            ("9.1.1", "3.10", "xdist"),
            ("9.1.1", "3.10", "unknown"),
        ),
    )
    def test_profile_qualification_rejects_negative_evidence(
        self,
        tmp_path: Path,
        pytest_version: str,
        python_minor: str,
        execution_mode: str,
    ) -> None:
        def write_unqualified(directory: Path, nonce: str) -> None:
            _write_summary(
                directory,
                _document(
                    nonce,
                    pytest_version=pytest_version,
                    python_minor=python_minor,
                    execution_mode=execution_mode,
                ),
            )

        result = _run(tmp_path, write_unqualified)

        assert isinstance(result, ToolFailure)
        assert result.summary_code == "pytest-outcome-conflict"

    @pytest.mark.parametrize("execution_mode", ("xdist", "unknown"))
    def test_profile_qualification_retains_complete_witness_free_pass(
        self,
        tmp_path: Path,
        execution_mode: str,
    ) -> None:
        def write_pass(directory: Path, nonce: str) -> None:
            _write_summary(
                directory,
                _document(nonce, facts=(), execution_mode=execution_mode),
            )

        result = TestAdapter(EvidenceRunner(write_pass, exit_code=0)).run(
            command=("pytest",),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=30,
        )

        assert isinstance(result, TestPass)


class TestPytestWitnessProcessPrecedence:
    @pytest.mark.parametrize(
        ("process", "cause"),
        (
            (
                ProcessResult(
                    exit_code=None,
                    signal=9,
                    duration_seconds=0.1,
                    timed_out=True,
                ),
                "TIMEOUT",
            ),
            (
                ProcessResult(exit_code=None, signal=9, duration_seconds=0.1),
                "TOOL_FAILURE",
            ),
            (
                ProcessResult(
                    exit_code=None,
                    signal=None,
                    start_error="missing",
                    duration_seconds=0.1,
                ),
                "TOOL_FAILURE",
            ),
            (
                ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stdout_complete=False,
                ),
                "TOOL_FAILURE",
            ),
            (
                ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stderr_complete=False,
                ),
                "TOOL_FAILURE",
            ),
        ),
        ids=(
            "timeout",
            "signal",
            "start-error",
            "incomplete-stdout",
            "incomplete-stderr",
        ),
    )
    def test_test_adapter_process_completeness_precedes_witness_evidence(
        self,
        tmp_path: Path,
        process: ProcessResult,
        cause: str,
    ) -> None:
        class IncompleteRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                _write_summary(
                    Path(environment["PF_PYTEST_WITNESS_DIR"]),
                    _document(environment["PF_PYTEST_WITNESS_NONCE"]),
                )
                return process

        result = TestAdapter(IncompleteRunner()).run(
            command=("pytest",),
            cwd=tmp_path,
            environment=(),
            failure_exit_codes=(1,),
            timeout_seconds=30,
        )

        assert isinstance(result, ToolFailure)
        assert result.cause == cause

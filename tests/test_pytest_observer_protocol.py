from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path

import pytest

from pf.adapters import test_command as test_command_module
from pf.adapters import pytest_observer as pytest_observer_module
from pf.adapters.process import ProcessRunner
from pf.adapters.pytest_observer import read_pytest_observer_detail
from pf.adapters.test_command import ConfiguredVerifier
from pf.schemas.evaluation import (
    EnvironmentVariable,
    ProcessResult,
    ProcessSpec,
    PytestFailureCase,
    PytestFailureDetail,
    VerifierPass,
    VerifierIndeterminate,
    VerifierRejected,
    VerifierRequest,
    VerifierRun,
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
        "protocol": "pf-pytest-observer-v1",
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
            Path(environment["PF_PYTEST_OBSERVER_DIR"]),
            environment["PF_PYTEST_OBSERVER_NONCE"],
        )
        return ProcessResult(
            exit_code=self._exit_code,
            signal=None,
            duration_seconds=0.1,
        )


def _run_with(
    runner: ProcessRunner,
    tmp_path: Path,
    *,
    environment: tuple[EnvironmentVariable, ...] = (),
) -> VerifierRun:
    return ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=("pytest",),
            cwd=tmp_path,
            environment=environment,
            timeout_seconds=30,
        )
    )


def _run(tmp_path: Path, writer: ArtifactWriter) -> VerifierRun:
    return _run_with(EvidenceRunner(writer), tmp_path)


def _assert_summary_omitted(run: VerifierRun) -> None:
    assert isinstance(run.authoritative, VerifierRejected)
    assert run.diagnostics is not None
    assert isinstance(run.diagnostics.process, ProcessResult)
    assert run.diagnostics.process.exit_code == 1
    assert run.diagnostics.pytest_facts == ()
    assert run.diagnostics.pytest_version is None
    assert run.diagnostics.python_minor is None
    assert run.diagnostics.pytest_execution_mode is None
    assert run.diagnostics.summary_code is None


class TestPytestObserverArtifactProtocol:
    def test_configured_verifier_returns_runtime_pytest_failure_detail(
        self,
        tmp_path: Path,
    ) -> None:
        class DetailRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                _write_summary(
                    Path(environment["PF_PYTEST_OBSERVER_DIR"]),
                    _document(nonce),
                )
                detail = {
                    "first": {
                        "nodeid": "tests/test_cli.py::test_example",
                        "phase": "call",
                    },
                    "protocol": "pf-pytest-observer-details-v1",
                    "run_nonce": nonce,
                    "total": 3,
                }
                directory = Path(environment["PF_PYTEST_OBSERVER_DETAILS_DIR"])
                (directory / f"details-{'b' * 32}.json").write_bytes(_canonical(detail))
                return ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                )

        result = _run_with(DetailRunner(), tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail == PytestFailureDetail(
            first=PytestFailureCase(
                nodeid="tests/test_cli.py::test_example",
                phase="call",
            ),
            total=3,
        )
        assert "detail" not in result.model_dump(mode="json")

    def test_runtime_detail_ignores_valid_artifacts_from_nested_pytest_runs(
        self,
        tmp_path: Path,
    ) -> None:
        class NestedDetailRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                _write_summary(
                    Path(environment["PF_PYTEST_OBSERVER_DIR"]),
                    _document(nonce),
                )
                directory = Path(environment["PF_PYTEST_OBSERVER_DETAILS_DIR"])
                for index, (run_nonce, nodeid) in enumerate(
                    (
                        ("nested-nonce", "nested.py::test_failure"),
                        (nonce, "tests/test_cli.py::test_example"),
                    )
                ):
                    detail = {
                        "first": {"nodeid": nodeid, "phase": "call"},
                        "protocol": "pf-pytest-observer-details-v1",
                        "run_nonce": run_nonce,
                        "total": 1,
                    }
                    (directory / f"details-{index:032x}.json").write_bytes(
                        _canonical(detail)
                    )
                return ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                )

        result = _run_with(NestedDetailRunner(), tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail == PytestFailureDetail(
            first=PytestFailureCase(
                nodeid="tests/test_cli.py::test_example",
                phase="call",
            ),
            total=1,
        )

    def test_runtime_detail_rejects_duplicate_artifacts_for_its_nonce(
        self,
        tmp_path: Path,
    ) -> None:
        class DuplicateDetailRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                _write_summary(
                    Path(environment["PF_PYTEST_OBSERVER_DIR"]),
                    _document(nonce),
                )
                detail = {
                    "first": {"nodeid": "test_example.py::test_bad", "phase": "call"},
                    "protocol": "pf-pytest-observer-details-v1",
                    "run_nonce": nonce,
                    "total": 1,
                }
                directory = Path(environment["PF_PYTEST_OBSERVER_DETAILS_DIR"])
                for index in range(2):
                    (directory / f"details-{index:032x}.json").write_bytes(
                        _canonical(detail)
                    )
                return ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                )

        result = _run_with(DuplicateDetailRunner(), tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail is None

    def test_runtime_detail_artifact_enumeration_is_bounded(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(pytest_observer_module, "_MAX_DETAIL_ARTIFACTS", 1)
        for index in range(2):
            detail = {
                "first": {"nodeid": f"test_{index}.py::test_bad", "phase": "call"},
                "protocol": "pf-pytest-observer-details-v1",
                "run_nonce": "current-nonce" if index == 0 else "nested-nonce",
                "total": 1,
            }
            (tmp_path / f"details-{index:032x}.json").write_bytes(_canonical(detail))

        class LazyScandir:
            def __init__(self) -> None:
                self.consumed = 0

            def __enter__(self) -> "LazyScandir":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def __iter__(self) -> "LazyScandir":
                return self

            def __next__(self):
                self.consumed += 1
                if self.consumed > 2:
                    raise AssertionError("reader consumed beyond the bounded sentinel")
                path = tmp_path / f"details-{self.consumed - 1:032x}.json"
                return type("Entry", (), {"path": path.as_posix()})()

        scanner = LazyScandir()
        monkeypatch.setattr(pytest_observer_module.os, "scandir", lambda _: scanner)

        assert read_pytest_observer_detail(tmp_path, nonce="current-nonce") is None
        assert scanner.consumed == 2

    @pytest.mark.parametrize(
        "payload_for",
        (
            lambda nonce: b"{",
            lambda nonce: _canonical(
                {
                    "first": {"nodeid": "x" * 4_097, "phase": "call"},
                    "protocol": "pf-pytest-observer-details-v1",
                    "run_nonce": nonce,
                    "total": 1,
                }
            ),
            lambda nonce: _canonical(
                {
                    "first": {"nodeid": "case\u009b31mred", "phase": "call"},
                    "protocol": "pf-pytest-observer-details-v1",
                    "run_nonce": nonce,
                    "total": 1,
                }
            ),
            lambda nonce: _canonical(
                {
                    "first": {"nodeid": "case\ud800bad", "phase": "call"},
                    "protocol": "pf-pytest-observer-details-v1",
                    "run_nonce": nonce,
                    "total": 1,
                }
            ),
            lambda nonce: _canonical(
                {
                    "first": {"nodeid": "case", "phase": "call"},
                    "protocol": "unknown",
                    "run_nonce": nonce,
                    "total": 1,
                }
            ),
        ),
        ids=(
            "invalid-json",
            "overlong-nodeid",
            "c1-control-nodeid",
            "surrogate-nodeid",
            "wrong-protocol",
        ),
    )
    def test_invalid_runtime_detail_does_not_change_test_failure(
        self,
        tmp_path: Path,
        payload_for: Callable[[str], bytes],
    ) -> None:
        class InvalidDetailRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                _write_summary(
                    Path(environment["PF_PYTEST_OBSERVER_DIR"]),
                    _document(nonce),
                )
                directory = Path(environment["PF_PYTEST_OBSERVER_DETAILS_DIR"])
                (directory / f"details-{'b' * 32}.json").write_bytes(payload_for(nonce))
                return ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                )

        result = _run_with(InvalidDetailRunner(), tmp_path)

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail is None

    def test_detail_cleanup_failure_only_omits_optional_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real_temporary_directory = test_command_module.tempfile.TemporaryDirectory

        class CleanupFailureDirectory:
            def __init__(self, prefix: str) -> None:
                self._inner = real_temporary_directory(prefix=prefix)
                self.name = self._inner.name

            def cleanup(self) -> None:
                self._inner.cleanup()
                raise OSError("detail cleanup failed")

        def temporary_directory(*, prefix: str):
            if prefix == "pf-pytest-observer-details-":
                return CleanupFailureDirectory(prefix)
            return real_temporary_directory(prefix=prefix)

        monkeypatch.setattr(
            test_command_module.tempfile,
            "TemporaryDirectory",
            temporary_directory,
        )
        result = _run(
            tmp_path,
            lambda directory, nonce: _write_summary(
                directory,
                _document(nonce),
            ),
        )

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail is None

    def test_detail_setup_failure_does_not_reuse_an_inherited_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real_temporary_directory = test_command_module.tempfile.TemporaryDirectory

        def temporary_directory(*, prefix: str):
            if prefix == "pf-pytest-observer-details-":
                raise OSError("detail setup failed")
            return real_temporary_directory(prefix=prefix)

        class SetupFailureRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                assert "PF_PYTEST_OBSERVER_DETAILS_DIR" not in environment
                nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                _write_summary(
                    Path(environment["PF_PYTEST_OBSERVER_DIR"]),
                    _document(nonce),
                )
                return ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                )

        monkeypatch.setattr(
            test_command_module.tempfile,
            "TemporaryDirectory",
            temporary_directory,
        )
        result = _run_with(
            SetupFailureRunner(),
            tmp_path,
            environment=(
                EnvironmentVariable(
                    name="PF_PYTEST_OBSERVER_DETAILS_DIR",
                    value=(tmp_path / "untrusted").as_posix(),
                ),
            ),
        )

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.detail is None

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
    def test_protocol_rejects_noncanonical_document(
        self,
        tmp_path: Path,
        writer: ArtifactWriter,
    ) -> None:
        _assert_summary_omitted(_run(tmp_path, writer))

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
    def test_protocol_rejects_unsafe_artifact_file(
        self,
        tmp_path: Path,
        writer: ArtifactWriter,
    ) -> None:
        _assert_summary_omitted(_run(tmp_path, writer))

    @staticmethod
    def _run_with_final_summaries(
        tmp_path: Path,
        count: int,
    ) -> VerifierRun:
        def write_many(directory: Path, nonce: str) -> None:
            document = _document(nonce)
            for index in range(count):
                _write_summary(directory, document, token=f"{index:032x}")

        return _run(tmp_path, write_many)

    def test_protocol_accepts_the_maximum_number_of_final_summaries(
        self,
        tmp_path: Path,
    ) -> None:
        result = self._run_with_final_summaries(tmp_path, 1024)

        assert isinstance(result.authoritative, VerifierRejected)

    def test_protocol_rejects_too_many_final_summaries(
        self,
        tmp_path: Path,
    ) -> None:
        _assert_summary_omitted(self._run_with_final_summaries(tmp_path, 1025))

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

        _assert_summary_omitted(_run(tmp_path, remember_directory))

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

        _assert_summary_omitted(_run(tmp_path, write_conflict))

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

        assert isinstance(result.authoritative, VerifierRejected)


class TestPytestObserverMetadata:
    @pytest.mark.parametrize(
        "pytest_version",
        ("6.2.5", "6.9.9", "7.0.1", "7.4.4", "8.0.2", "8.4.2", "9.0.2", "9.1.1"),
    )
    @pytest.mark.parametrize("python_minor", ("3.10", "3.11", "3.12"))
    def test_supported_version_metadata_does_not_decide_rejection(
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.pytest_version == pytest_version
        assert result.diagnostics.python_minor == python_minor

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
    def test_arbitrary_version_and_mode_metadata_does_not_decide_rejection(
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

        assert isinstance(result.authoritative, VerifierRejected)
        assert result.diagnostics is not None
        assert result.diagnostics.pytest_version == pytest_version
        assert result.diagnostics.python_minor == python_minor
        assert result.diagnostics.pytest_execution_mode == execution_mode

    @pytest.mark.parametrize("execution_mode", ("xdist", "unknown"))
    def test_execution_mode_metadata_does_not_decide_pass(
        self,
        tmp_path: Path,
        execution_mode: str,
    ) -> None:
        def write_pass(directory: Path, nonce: str) -> None:
            _write_summary(
                directory,
                _document(nonce, facts=(), execution_mode=execution_mode),
            )

        result = _run_with(EvidenceRunner(write_pass, exit_code=0), tmp_path)

        assert isinstance(result.authoritative, VerifierPass)


class TestPytestObserverProcessPrecedence:
    @pytest.mark.parametrize(
        ("process", "status"),
        (
            (
                ProcessResult(
                    exit_code=None,
                    signal=9,
                    duration_seconds=0.1,
                    timed_out=True,
                ),
                "INDETERMINATE",
            ),
            (
                ProcessResult(exit_code=None, signal=9, duration_seconds=0.1),
                "INDETERMINATE",
            ),
            (
                ProcessResult(
                    exit_code=None,
                    signal=None,
                    start_error="missing",
                    duration_seconds=0.1,
                ),
                "INDETERMINATE",
            ),
            (
                ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stdout_complete=False,
                ),
                "REJECTED",
            ),
            (
                ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stderr_complete=False,
                ),
                "REJECTED",
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
    def test_terminal_facts_precede_observer_metadata(
        self,
        tmp_path: Path,
        process: ProcessResult,
        status: str,
    ) -> None:
        class IncompleteRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                _write_summary(
                    Path(environment["PF_PYTEST_OBSERVER_DIR"]),
                    _document(environment["PF_PYTEST_OBSERVER_NONCE"]),
                )
                return process

        result = _run_with(IncompleteRunner(), tmp_path)

        assert result.authoritative.status == status
        if status == "INDETERMINATE":
            assert isinstance(result.authoritative, VerifierIndeterminate)
        else:
            assert isinstance(result.authoritative, VerifierRejected)

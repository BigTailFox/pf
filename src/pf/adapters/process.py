from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO, Protocol

from pf.schemas.evaluation import (
    EnvironmentVariable,
    ProcessEvent,
    ProcessObservation,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
)

OUTPUT_CACHE_LIMIT = 16 * 1024 * 1024
_STREAM_CHUNK_SIZE = 65_536


@dataclass(frozen=True)
class ProcessOutput:
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    def run(self, spec: ProcessSpec) -> ProcessObservation: ...


class ProcessListener(Protocol):
    def consume(self, event: ProcessEvent) -> None: ...


class ProcessLogWriter(Protocol):
    def write_stdout(self, chunk: str) -> None: ...

    def write_stderr(self, chunk: str) -> None: ...

    def finish(self, result: ProcessObservation) -> Path: ...


class ProcessLogRecorder(Protocol):
    def begin_record(self, process_id: int, spec: ProcessSpec) -> ProcessLogWriter: ...

    def record(
        self,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessObservation,
        stdout: str = "",
        stderr: str = "",
    ) -> Path: ...

    def reference_for(self, result: ProcessObservation) -> Path | None: ...

    def read_output(self, result: ProcessResult) -> tuple[str, str] | None: ...


class SecretRedactor:
    """Remove configured secrets and URL userinfo before data crosses the seam."""

    _URL_SCHEME = re.compile(r"(?i)[a-z][a-z0-9+.-]*://")
    _INCOMPLETE_SCHEME = re.compile(
        r"(?i)(?:^|[^a-z0-9+.-])([a-z][a-z0-9+.-]{0,31}:?/?/?)\Z"
    )

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        self._secrets = tuple(
            sorted({secret for secret in secrets if secret}, key=len, reverse=True)
        )

    def redact(self, value: str) -> str:
        redacted = re.sub(
            r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@",
            r"\1***@",
            value,
        )
        for secret in self._secrets:
            redacted = redacted.replace(secret, "***")
        return redacted

    def with_secrets(self, secrets: tuple[str, ...]) -> "SecretRedactor":
        return SecretRedactor((*self._secrets, *secrets))

    def holdback_chars(self, text: str) -> int:
        """Return the suffix that must not yet be emitted to a consumer."""
        hold = self._secret_prefix_holdback(text)
        hold = max(hold, self._open_url_holdback(text))
        return max(hold, self._incomplete_scheme_holdback(text))

    def _secret_prefix_holdback(self, text: str) -> int:
        hold = 0
        for secret in self._secrets:
            limit = min(len(secret) - 1, len(text))
            for length in range(limit, 0, -1):
                if text.endswith(secret[:length]):
                    hold = max(hold, length)
                    break
        return hold

    def _open_url_holdback(self, text: str) -> int:
        match = None
        for candidate in self._URL_SCHEME.finditer(text):
            match = candidate
        if match is None:
            return 0
        rest = text[match.end() :]
        if re.search(r"[@/\s]", rest):
            return 0
        return len(text) - match.start()

    def _incomplete_scheme_holdback(self, text: str) -> int:
        match = self._INCOMPLETE_SCHEME.search(text)
        if match is not None:
            candidate = match.group(1)
            if "://" in candidate:
                return 0
            return len(candidate)
        if re.fullmatch(r"(?i)[a-z][a-z0-9+.-]{0,31}:?/?/?", text):
            return len(text)
        return 0

    def overlap_bytes(self) -> int:
        longest = max((len(secret.encode("utf-8")) for secret in self._secrets), default=0)
        return max(longest, 256)


def read_process_output(runner: object, result: ProcessResult) -> ProcessOutput:
    """Return stdout/stderr from a runner cache, Process Log, or result projection."""
    reader = getattr(runner, "output", None)
    if callable(reader):
        return reader(result)
    return ProcessOutput(stdout=result.stdout, stderr=result.stderr)


def project_output_cache(
    stdout: str,
    stderr: str,
    *,
    limit: int = OUTPUT_CACHE_LIMIT,
) -> tuple[str, str]:
    """Keep a tail-preferring projection whose UTF-8 size is at most *limit*."""
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    stdout_budget, stderr_budget = _cache_budgets(
        len(stdout_bytes),
        len(stderr_bytes),
        limit,
    )
    return (
        _decode_tail(stdout_bytes, stdout_budget),
        _decode_tail(stderr_bytes, stderr_budget),
    )


def _cache_budgets(stdout_len: int, stderr_len: int, limit: int) -> tuple[int, int]:
    if stdout_len + stderr_len <= limit:
        return stdout_len, stderr_len
    stderr_budget = min(stderr_len, limit if stdout_len == 0 else limit // 2)
    stdout_budget = min(stdout_len, limit - stderr_budget)
    stderr_budget = min(stderr_len, limit - stdout_budget)
    return stdout_budget, stderr_budget


class _OutputCacheBuilder:
    """Accumulate a ≤16 MiB tail projection from streamed chunks."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._stdout_chunks: deque[bytes] = deque()
        self._stderr_chunks: deque[bytes] = deque()
        self._stdout_size = 0
        self._stderr_size = 0
        self._stdout_total = 0
        self._stderr_total = 0

    def consume_stdout(self, text: str) -> None:
        self._consume(self._stdout_chunks, "_stdout_size", "_stdout_total", text)

    def consume_stderr(self, text: str) -> None:
        self._consume(self._stderr_chunks, "_stderr_size", "_stderr_total", text)

    def project(self) -> tuple[str, str]:
        return (
            _decode_tail(b"".join(self._stdout_chunks), self._stdout_size),
            _decode_tail(b"".join(self._stderr_chunks), self._stderr_size),
        )

    def covered_full(self) -> tuple[bool, bool]:
        return (
            self._stdout_size == self._stdout_total,
            self._stderr_size == self._stderr_total,
        )

    def _consume(
        self,
        chunks: deque[bytes],
        size_attr: str,
        total_attr: str,
        text: str,
    ) -> None:
        data = text.encode("utf-8")
        chunks.append(data)
        setattr(self, size_attr, getattr(self, size_attr) + len(data))
        setattr(self, total_attr, getattr(self, total_attr) + len(data))
        self._trim()

    def _trim(self) -> None:
        stdout_budget, stderr_budget = _cache_budgets(
            self._stdout_size,
            self._stderr_size,
            self._limit,
        )
        self._drop(self._stdout_chunks, "_stdout_size", stdout_budget)
        self._drop(self._stderr_chunks, "_stderr_size", stderr_budget)

    def _drop(self, chunks: deque[bytes], size_attr: str, budget: int) -> None:
        size = getattr(self, size_attr)
        while size > budget and chunks:
            extra = size - budget
            first = chunks[0]
            if len(first) <= extra:
                chunks.popleft()
                size -= len(first)
            else:
                chunks[0] = first[extra:]
                size -= extra
        setattr(self, size_attr, size)


def _decode_tail(payload: bytes, budget: int) -> str:
    if budget >= len(payload):
        return payload.decode("utf-8", errors="replace")
    return payload[-budget:].decode("utf-8", errors="replace")


class _StreamSink:
    """Fan a redacted chunk into the Output Cache and, when present, the log."""

    def __init__(
        self,
        cache: Callable[[str], None],
        write: Callable[[str], None] | None,
    ) -> None:
        self._cache = cache
        self._write = write

    def __call__(self, text: str) -> None:
        self._cache(text)
        if self._write is not None:
            self._write(text)


class SubprocessRunner:
    """Run argv without a shell and return portable facts plus an output cache."""

    def __init__(
        self,
        *,
        redactor: SecretRedactor | None = None,
        listener: ProcessListener | None = None,
        logs: ProcessLogRecorder | None = None,
        cache_limit: int = OUTPUT_CACHE_LIMIT,
        summary_limit: int | None = None,
        terminate_grace_seconds: float = 1.0,
    ) -> None:
        self._redactor = redactor or SecretRedactor()
        self._listener = listener
        self._logs = logs
        self._process_ids = count(1)
        self._cache_limit = summary_limit if summary_limit is not None else cache_limit
        self._terminate_grace_seconds = terminate_grace_seconds
        self._outputs: dict[int, ProcessOutput] = {}
        self._output_full: dict[int, tuple[bool, bool]] = {}

    def run(self, spec: ProcessSpec) -> ProcessObservation:
        started = time.monotonic()
        process_id = next(self._process_ids)
        redactor = self._redactor.with_secrets(
            tuple(item.value for item in spec.environment)
        )
        argv = tuple(redactor.redact(argument) for argument in spec.argv)
        self._emit(ProcessEvent(process_id=process_id, argv=argv, state="started"))
        environment = os.environ.copy()
        for name in spec.environment_removals:
            environment.pop(name, None)
        environment.update({item.name: item.value for item in spec.environment})
        size = self._terminal_size()
        environment["COLUMNS"] = str(size.columns)
        environment["LINES"] = str(size.lines)
        with (
            tempfile.TemporaryFile() as stdout_file,
            tempfile.TemporaryFile() as stderr_file,
        ):
            cache_limit = (
                spec.summary_limit
                if spec.summary_limit is not None
                else self._cache_limit
            )
            try:
                process = subprocess.Popen(
                    spec.argv,
                    cwd=spec.cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    start_new_session=spec.start_new_session,
                )
            except OSError as error:
                result = ProcessResult(
                    exit_code=None,
                    signal=None,
                    duration_seconds=time.monotonic() - started,
                    start_error=redactor.redact(str(error)),
                )
                self._store_output(result, (True, True))
                self._commit_log(process_id, spec, result, argv, redactor)
                self._emit(
                    ProcessEvent(
                        process_id=process_id,
                        argv=argv,
                        state="finished",
                        duration_seconds=result.duration_seconds,
                    )
                )
                return result

            timed_out = False
            try:
                process.communicate(timeout=spec.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate(process, spec.start_new_session)
                process.communicate()

            cache = _OutputCacheBuilder(cache_limit)
            writer = self._begin_log(process_id, spec, argv, redactor)
            self._redact_stream(
                stdout_file,
                _StreamSink(
                    cache.consume_stdout,
                    None if writer is None else writer.write_stdout,
                ),
                redactor,
            )
            self._redact_stream(
                stderr_file,
                _StreamSink(
                    cache.consume_stderr,
                    None if writer is None else writer.write_stderr,
                ),
                redactor,
            )
        cached_stdout, cached_stderr = cache.project()
        return_code = process.returncode
        duration_seconds = time.monotonic() - started
        if return_code is None:
            unavailable = ProcessTerminalUnavailable(
                duration_seconds=duration_seconds,
                detail="managed process lifecycle returned no terminal status",
            )
            self._outputs[id(unavailable)] = ProcessOutput(
                stdout=cached_stdout,
                stderr=cached_stderr,
            )
            self._output_full[id(unavailable)] = cache.covered_full()
            if writer is not None:
                writer.finish(unavailable)
            self._emit(
                ProcessEvent(
                    process_id=process_id,
                    argv=argv,
                    state="finished",
                    duration_seconds=duration_seconds,
                )
            )
            return unavailable
        exit_code = return_code if return_code >= 0 else None
        process_signal = -return_code if return_code < 0 else None
        result = ProcessResult(
            exit_code=exit_code,
            signal=process_signal,
            duration_seconds=duration_seconds,
            stdout=cached_stdout,
            stderr=cached_stderr,
            timed_out=timed_out,
        )
        self._store_output(result, cache.covered_full())
        if writer is not None:
            writer.finish(result)
        self._emit(
            ProcessEvent(
                process_id=process_id,
                argv=argv,
                state="finished",
                duration_seconds=result.duration_seconds,
            )
        )
        return result

    def output(self, result: ProcessResult) -> ProcessOutput:
        cached = self._outputs.get(id(result))
        full = self._output_full.get(id(result))
        if cached is not None and full == (True, True):
            return cached
        if self._logs is not None:
            logged = self._logs.read_output(result)
            if logged is not None:
                return ProcessOutput(stdout=logged[0], stderr=logged[1])
        if cached is not None:
            return cached
        return ProcessOutput(stdout=result.stdout, stderr=result.stderr)

    def _store_output(
        self, result: ProcessResult, covered_full: tuple[bool, bool]
    ) -> None:
        self._outputs[id(result)] = ProcessOutput(
            stdout=result.stdout,
            stderr=result.stderr,
        )
        self._output_full[id(result)] = covered_full

    def _redacted_spec(
        self,
        spec: ProcessSpec,
        argv: tuple[str, ...],
        redactor: SecretRedactor,
    ) -> ProcessSpec:
        return spec.model_copy(
            update={
                "argv": argv,
                "cwd": redactor.redact(spec.cwd),
                "environment": tuple(
                    EnvironmentVariable(name=item.name, value="***")
                    for item in spec.environment
                ),
            }
        )

    def _begin_log(
        self,
        process_id: int,
        spec: ProcessSpec,
        argv: tuple[str, ...],
        redactor: SecretRedactor,
    ) -> ProcessLogWriter | None:
        if self._logs is None:
            return None
        return self._logs.begin_record(
            process_id,
            self._redacted_spec(spec, argv, redactor),
        )

    def _commit_log(
        self,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessResult,
        argv: tuple[str, ...],
        redactor: SecretRedactor,
    ) -> None:
        writer = self._begin_log(process_id, spec, argv, redactor)
        if writer is not None:
            writer.finish(result)

    def _emit(self, event: ProcessEvent) -> None:
        if self._listener is not None:
            self._listener.consume(event)

    def _redact_stream(
        self,
        stream: BinaryIO,
        consume: Callable[[str], None],
        redactor: SecretRedactor,
    ) -> None:
        stream.flush()
        stream.seek(0)
        pending_bytes = b""
        pending_text = ""
        while True:
            chunk = stream.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            complete, pending_bytes = _split_utf8(pending_bytes + chunk)
            if not complete:
                continue
            pending_text += complete.decode("utf-8", errors="replace")
            hold = redactor.holdback_chars(pending_text)
            emit_at = len(pending_text) - hold
            if emit_at > 0:
                consume(redactor.redact(pending_text[:emit_at]))
                pending_text = pending_text[emit_at:]
        if pending_bytes:
            pending_text += pending_bytes.decode("utf-8", errors="replace")
        if pending_text:
            consume(redactor.redact(pending_text))

    @staticmethod
    def _terminal_size() -> os.terminal_size:
        for stream in (sys.stderr, sys.stdout):
            try:
                return os.get_terminal_size(stream.fileno())
            except (AttributeError, ValueError, OSError):
                continue
        return shutil.get_terminal_size()

    def _terminate(self, process: subprocess.Popen[bytes], process_group: bool) -> None:
        try:
            if process_group:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=self._terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            if process_group:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass


def _split_utf8(payload: bytes) -> tuple[bytes, bytes]:
    """Keep incomplete UTF-8 sequences with the next chunk."""
    if not payload:
        return payload, b""
    index = len(payload)
    while index > 0 and payload[index - 1] & 0xC0 == 0x80:
        index -= 1
    if index == 0:
        return b"", payload
    lead = payload[index - 1]
    if lead & 0x80 == 0:
        return payload, b""
    expected = 2 if lead & 0xE0 == 0xC0 else 3 if lead & 0xF0 == 0xE0 else 4
    if len(payload) - (index - 1) < expected:
        return payload[: index - 1], payload[index - 1 :]
    return payload, b""

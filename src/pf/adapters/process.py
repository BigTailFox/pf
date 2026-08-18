from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import time
from typing import BinaryIO
from typing import Protocol

from pf.schemas.evaluation import ProcessResult, ProcessSpec


class ProcessRunner(Protocol):
    def run(self, spec: ProcessSpec) -> ProcessResult: ...


class SecretRedactor:
    """Remove configured secrets and URL userinfo before data crosses the seam."""

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


class SubprocessRunner:
    """Run argv without a shell and return bounded, redacted mechanical facts."""

    def __init__(
        self,
        *,
        redactor: SecretRedactor | None = None,
        summary_limit: int = 4_096,
        tail_limit: int = 16_384,
        terminate_grace_seconds: float = 1.0,
    ) -> None:
        self._redactor = redactor or SecretRedactor()
        self._summary_limit = summary_limit
        self._tail_limit = tail_limit
        self._terminate_grace_seconds = terminate_grace_seconds

    def run(self, spec: ProcessSpec) -> ProcessResult:
        started = time.monotonic()
        environment = os.environ.copy()
        environment.update({item.name: item.value for item in spec.environment})
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
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
                return ProcessResult(
                    exit_code=None,
                    signal=None,
                    duration_seconds=time.monotonic() - started,
                    stdout_summary="",
                    stderr_summary="",
                    stdout_tail="",
                    stderr_tail="",
                    start_error=self._redactor.redact(str(error)),
                )

            timed_out = False
            try:
                process.communicate(timeout=spec.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate(process, spec.start_new_session)
                process.communicate()

            stdout_summary, stdout_tail, stdout_truncated = self._capture(stdout_file)
            stderr_summary, stderr_tail, stderr_truncated = self._capture(stderr_file)
        return_code = process.returncode
        exit_code = return_code if return_code >= 0 else None
        process_signal = -return_code if return_code < 0 else None
        return ProcessResult(
            exit_code=exit_code,
            signal=process_signal,
            duration_seconds=time.monotonic() - started,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            timed_out=timed_out,
        )

    def _capture(self, stream: BinaryIO) -> tuple[str, str, bool]:
        stream.flush()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
        summary_bytes = stream.read(self._summary_limit)
        stream.seek(max(0, size - self._tail_limit))
        tail_bytes = stream.read(self._tail_limit)
        summary = self._redactor.redact(
            summary_bytes.decode("utf-8", errors="replace")
        )
        tail = self._redactor.redact(tail_bytes.decode("utf-8", errors="replace"))
        return summary, tail, size > self._summary_limit

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

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
from threading import Lock

from pf.errors import InfrastructureError
from pf.schemas.evaluation import ProcessResult, ProcessSpec


class RunLogStore:
    """Persist bounded process facts and retain runtime-only result references."""

    def __init__(self, *, root: Path, run_id: str | None = None) -> None:
        self._root = root.resolve()
        self._run_id = run_id or self._new_run_id()
        if re.fullmatch(r"[A-Za-z0-9._-]+", self._run_id) is None:
            raise ValueError("run id must contain only safe filename characters")
        self._run_root = self._root / ".pf" / "logs" / self._run_id
        self._references: dict[int, Path] = {}
        self._lock = Lock()
        self._initialized = False

    def record(
        self,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessResult,
    ) -> Path:
        try:
            with self._lock:
                self._ensure_run()
                path = self._run_root / f"process-{process_id:04d}.log"
                self._write_private(path, self._render(process_id, spec, result))
                self._references[id(result)] = path
                return path
        except OSError as error:
            raise InfrastructureError(
                "could not write PF process log",
                detail=str(error),
            ) from error

    def reference_for(self, result: ProcessResult) -> Path | None:
        with self._lock:
            return self._references.get(id(result))

    def _ensure_run(self) -> None:
        if self._initialized:
            return
        pf_root = self._root / ".pf"
        logs_root = pf_root / "logs"
        for directory in (pf_root, logs_root):
            if directory.is_symlink():
                raise OSError(f"PF log directory cannot be a symlink: {directory}")
            directory.mkdir(exist_ok=True)
            if not directory.is_dir():
                raise OSError(f"PF log path is not a directory: {directory}")
        self._run_root.mkdir(mode=0o700, exist_ok=False)
        os.chmod(self._run_root, 0o700)
        manifest = (
            "format: pf-run-log-v1\n"
            f"run_id: {self._run_id}\n"
            f"root: {self._root.as_posix()}\n"
        )
        self._write_private(self._run_root / "run.log", manifest)
        self._initialized = True

    @staticmethod
    def _write_private(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _render(
        process_id: int,
        spec: ProcessSpec,
        result: ProcessResult,
    ) -> str:
        environment_names = sorted(variable.name for variable in spec.environment)
        terminal = (
            f"exit_code: {json.dumps(result.exit_code)}\n"
            f"signal: {json.dumps(result.signal)}\n"
            f"start_error: {json.dumps(result.start_error)}\n"
            f"timed_out: {json.dumps(result.timed_out)}\n"
            f"duration_seconds: {result.duration_seconds}\n"
            f"stdout_truncated: {json.dumps(result.stdout_truncated)}\n"
            f"stderr_truncated: {json.dumps(result.stderr_truncated)}\n"
        )
        sections = (
            ("stdout summary", result.stdout_summary),
            ("stdout tail", result.stdout_tail),
            ("stderr summary", result.stderr_summary),
            ("stderr tail", result.stderr_tail),
        )
        output = (
            "format: pf-process-log-v1\n"
            f"process_id: {process_id}\n"
            f"argv: {json.dumps(spec.argv, ensure_ascii=False)}\n"
            f"cwd: {json.dumps(spec.cwd, ensure_ascii=False)}\n"
            f"environment_names: {json.dumps(environment_names)}\n"
            f"timeout_seconds: {json.dumps(spec.timeout_seconds)}\n"
            f"start_new_session: {json.dumps(spec.start_new_session)}\n"
            f"summary_limit: {json.dumps(spec.summary_limit)}\n"
            f"redaction_policy_identity: {json.dumps(spec.redaction_policy_identity)}\n"
            f"{terminal}"
        )
        for heading, value in sections:
            output += f"\n--- {heading} ---\n{value}"
            if value and not value.endswith("\n"):
                output += "\n"
        return output

    @staticmethod
    def _new_run_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{timestamp}-{os.getpid()}-{secrets.token_hex(4)}"

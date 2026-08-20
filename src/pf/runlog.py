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
from pf.windows_runlog import WindowsRunDirectory


class RunLogStore:
    """Persist bounded process facts and retain runtime-only result references."""

    _METADATA_LIMIT = 4_096
    _OUTPUT_LIMIT = 16_384

    def __init__(self, *, root: Path, run_id: str | None = None) -> None:
        self._root = root.resolve()
        self._run_id = run_id or self._new_run_id()
        if re.fullmatch(r"[A-Za-z0-9._-]+", self._run_id) is None:
            raise ValueError("run id must contain only safe filename characters")
        self._run_root = self._root / ".pf" / "logs" / self._run_id
        self._references: dict[int, Path] = {}
        self._lock = Lock()
        self._initialized = False
        self._run_identity: tuple[int, int] | None = None
        self._windows_run: WindowsRunDirectory | None = None

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
                content = self._render(process_id, spec, result)
                if self._supports_secure_dir_fd():
                    run_fd = self._open_run()
                    try:
                        self._write_private_at(run_fd, path.name, content)
                    finally:
                        os.close(run_fd)
                elif self._windows_run is not None:
                    self._windows_run.assert_intact()
                    self._write_private_path(path, content)
                    self._windows_run.assert_intact()
                else:
                    raise OSError("secure PF run logs are unsupported on this platform")
                self._references[id(result)] = path
                return path
        except (OSError, NotImplementedError) as error:
            raise InfrastructureError(
                "could not write PF process log",
                detail=str(error),
            ) from error

    def reference_for(self, result: ProcessResult) -> Path | None:
        with self._lock:
            return self._references.get(id(result))

    def close(self) -> None:
        with self._lock:
            if self._windows_run is not None:
                self._windows_run.close()

    def _ensure_run(self) -> None:
        if self._initialized:
            return
        if not self._supports_secure_dir_fd():
            self._ensure_run_windows()
            self._initialized = True
            return
        root_fd = self._open_root()
        pf_fd: int | None = None
        logs_fd: int | None = None
        run_fd: int | None = None
        try:
            pf_fd = self._ensure_directory(root_fd, ".pf")
            logs_fd = self._ensure_directory(pf_fd, "logs")
            os.mkdir(self._run_id, mode=0o700, dir_fd=logs_fd)
            run_fd = self._open_directory(logs_fd, self._run_id)
            os.fchmod(run_fd, 0o700)
            run_stat = os.fstat(run_fd)
            self._run_identity = (run_stat.st_dev, run_stat.st_ino)
            self._write_private_at(run_fd, "run.log", self._manifest())
        finally:
            for descriptor in (run_fd, logs_fd, pf_fd, root_fd):
                if descriptor is not None:
                    os.close(descriptor)
        self._initialized = True

    def _ensure_run_windows(self) -> None:
        if not self._supports_windows_guard():
            raise OSError("secure PF run logs are unsupported on this platform")
        self._windows_run = WindowsRunDirectory.create(
            root=self._root,
            run_id=self._run_id,
        )
        self._windows_run.assert_intact()
        self._write_private_path(self._run_root / "run.log", self._manifest())
        self._windows_run.assert_intact()

    def _manifest(self) -> str:
        return (
            "format: pf-run-log-v1\n"
            f"run_id: {self._bounded(self._run_id, self._METADATA_LIMIT)}\n"
            f"root: {self._bounded(self._root.as_posix(), self._METADATA_LIMIT)}\n"
        )

    def _open_run(self) -> int:
        if self._run_identity is None:
            raise OSError("PF run log directory was not initialized")
        root_fd = self._open_root()
        pf_fd: int | None = None
        logs_fd: int | None = None
        run_fd: int | None = None
        try:
            pf_fd = self._open_directory(root_fd, ".pf")
            logs_fd = self._open_directory(pf_fd, "logs")
            run_fd = self._open_directory(logs_fd, self._run_id)
            run_stat = os.fstat(run_fd)
            if (run_stat.st_dev, run_stat.st_ino) != self._run_identity:
                raise OSError("PF run log directory identity changed")
            result = run_fd
            run_fd = None
            return result
        finally:
            for descriptor in (run_fd, logs_fd, pf_fd, root_fd):
                if descriptor is not None:
                    os.close(descriptor)

    def _open_root(self) -> int:
        descriptor = os.open(self._root, self._directory_flags())
        root_stat = os.fstat(descriptor)
        if not self._is_directory_mode(root_stat.st_mode):
            os.close(descriptor)
            raise OSError(f"PF project root is not a directory: {self._root}")
        return descriptor

    @staticmethod
    def _is_directory_mode(mode: int) -> bool:
        import stat

        return stat.S_ISDIR(mode)

    @classmethod
    def _ensure_directory(cls, parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return cls._open_directory(parent_fd, name)

    @classmethod
    def _open_directory(cls, parent_fd: int, name: str) -> int:
        descriptor = os.open(name, cls._directory_flags(), dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not cls._is_directory_mode(linked.st_mode)
            or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            os.close(descriptor)
            raise OSError(f"PF log directory cannot be a symlink: {name}")
        return descriptor

    @staticmethod
    def _directory_flags() -> int:
        return (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )

    @staticmethod
    def _write_private_at(directory_fd: int, name: str, content: str) -> None:
        temporary = f".{name}.{secrets.token_hex(4)}.tmp"
        descriptor: int | None = None
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

    def _write_private_path(self, path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if self._windows_run is None:
                raise OSError("Windows run directory guard is unavailable")
            self._windows_run.assert_intact()
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _supports_secure_dir_fd() -> bool:
        return os.name != "nt" and hasattr(os, "O_NOFOLLOW")

    @staticmethod
    def _supports_windows_guard() -> bool:
        return os.name == "nt"

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
            f"start_error: {RunLogStore._bounded_json(result.start_error)}\n"
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
            f"argv: {RunLogStore._bounded_json(spec.argv)}\n"
            f"cwd: {RunLogStore._bounded_json(spec.cwd)}\n"
            f"environment_names: {RunLogStore._bounded_json(environment_names)}\n"
            f"timeout_seconds: {json.dumps(spec.timeout_seconds)}\n"
            f"start_new_session: {json.dumps(spec.start_new_session)}\n"
            f"summary_limit: {json.dumps(spec.summary_limit)}\n"
            "redaction_policy_identity: "
            f"{RunLogStore._bounded_json(spec.redaction_policy_identity)}\n"
            f"{terminal}"
        )
        for heading, value in sections:
            bounded = RunLogStore._bounded(value, RunLogStore._OUTPUT_LIMIT)
            output += f"\n--- {heading} ---\n{bounded}"
            if bounded and not bounded.endswith("\n"):
                output += "\n"
        return output

    @staticmethod
    def _bounded(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        marker = "... [truncated by RunLogStore]"
        return f"{value[: limit - len(marker)]}{marker}"

    @classmethod
    def _bounded_json(cls, value: object) -> str:
        return cls._bounded(
            json.dumps(value, ensure_ascii=False),
            cls._METADATA_LIMIT,
        )

    @staticmethod
    def _new_run_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{timestamp}-{os.getpid()}-{secrets.token_hex(4)}"

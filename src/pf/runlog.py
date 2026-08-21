from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
from threading import Lock

from pf.errors import ConfigurationError, InfrastructureError
from pf.schemas.evaluation import ProcessResult, ProcessSpec
from pf.windows_runlog import WindowsRunDirectory


class RunLogStore:
    """Persist bounded process facts and retain runtime-only result references."""

    _METADATA_LIMIT = 4_096
    _OUTPUT_LIMIT = 32 * 1024 * 1024
    _INDEX_LIMIT = 8 * 1024 * 1024
    _INDEX_NAME = "diagnosis-index.json"

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

    def associate(
        self,
        report_generation_id: str,
        failure_id: str,
        result: ProcessResult,
    ) -> None:
        """Associate a portable failure identity with this run's local log."""
        if self.reference_for(result) is None:
            raise InfrastructureError(
                "could not write PF diagnosis index",
                detail=(
                    "the failure process was not recorded by this RunLogStore; "
                    "refusing to publish an unusable diagnosis locator"
                ),
            )
        self.replace_associations(
            report_generation_id,
            ((failure_id, result),),
            replace_generation=False,
        )

    def replace_associations(
        self,
        report_generation_id: str,
        failures: tuple[tuple[str, ProcessResult | None], ...],
        *,
        replace_generation: bool = True,
        remove_failure_ids: tuple[str, ...] = (),
    ) -> None:
        """Atomically update one report generation's local log locators."""
        try:
            with self._lock:
                located: dict[str, str] = {}
                for failure_id, result in failures:
                    if result is None:
                        continue
                    relative = self._relative_reference(result)
                    if relative is None:
                        raise ValueError(
                            "current failure process has no recorded diagnosis locator"
                        )
                    located[failure_id] = relative
                if not located and not replace_generation and not remove_failure_ids:
                    return
                if located:
                    self._ensure_run()
                if not self._supports_secure_dir_fd():
                    if not self._supports_windows_guard():
                        raise OSError(
                            "secure PF diagnosis indexes are unsupported on this platform"
                        )
                    self._replace_associations_windows(
                        report_generation_id,
                        located,
                        replace_generation=replace_generation,
                        remove_failure_ids=remove_failure_ids,
                    )
                    return
                logs_fd = self._open_existing_logs()
                if logs_fd is None:
                    return
                try:
                    entries = self._read_index_at(logs_fd)
                    self._update_entries(
                        entries,
                        report_generation_id,
                        located,
                        replace_generation=replace_generation,
                        remove_failure_ids=remove_failure_ids,
                    )
                    self._write_private_at(
                        logs_fd,
                        self._INDEX_NAME,
                        json.dumps(
                            {
                                "format": "pf-diagnosis-index-v1",
                                "entries": entries,
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n",
                    )
                finally:
                    os.close(logs_fd)
        except (OSError, NotImplementedError, ValueError) as error:
            raise InfrastructureError(
                "could not write PF diagnosis index",
                detail=str(error),
            ) from error

    def lookup(self, report_generation_id: str, failure_id: str) -> Path | None:
        """Resolve exactly one indexed local log without scanning run directories."""
        try:
            with self._lock:
                if not self._supports_secure_dir_fd():
                    if not self._supports_windows_guard():
                        raise OSError(
                            "secure PF diagnosis indexes are unsupported on this platform"
                        )
                    return self._lookup_windows(report_generation_id, failure_id)
                logs_fd = self._open_existing_logs()
                if logs_fd is None:
                    return None
                try:
                    entries = self._read_index_at(logs_fd)
                    generation = entries.get(report_generation_id)
                    if not isinstance(generation, dict):
                        return None
                    relative = generation.get(failure_id)
                    if not isinstance(relative, str):
                        return None
                    return self._validated_log_path(logs_fd, relative)
                finally:
                    os.close(logs_fd)
        except (OSError, NotImplementedError, ValueError) as error:
            raise ConfigurationError(
                "could not read PF diagnosis log",
                detail=str(error),
            ) from error

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

    def _open_existing_logs(self) -> int | None:
        root_fd = self._open_root()
        pf_fd: int | None = None
        logs_fd: int | None = None
        try:
            try:
                pf_fd = self._open_directory(root_fd, ".pf")
                logs_fd = self._open_directory(pf_fd, "logs")
            except FileNotFoundError:
                return None
            result = logs_fd
            logs_fd = None
            return result
        finally:
            for descriptor in (logs_fd, pf_fd, root_fd):
                if descriptor is not None:
                    os.close(descriptor)

    def _relative_reference(self, result: ProcessResult) -> str | None:
        path = self._references.get(id(result))
        if path is None:
            return None
        relative = path.relative_to(self._root / ".pf" / "logs")
        self._validate_relative_locator(relative.as_posix())
        return relative.as_posix()

    def _read_index_at(self, logs_fd: int) -> dict[str, dict[str, str]]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._INDEX_NAME, flags, dir_fd=logs_fd)
        except FileNotFoundError:
            return {}
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > self._INDEX_LIMIT:
                raise ValueError("PF diagnosis index is not a bounded regular file")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                content = stream.read(self._INDEX_LIMIT + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return self._parse_index(content)

    def _parse_index(self, content: str) -> dict[str, dict[str, str]]:
        if len(content.encode("utf-8")) > self._INDEX_LIMIT:
            raise ValueError("PF diagnosis index exceeds its size limit")
        document = json.loads(content)
        if not isinstance(document, dict) or document.get("format") != (
            "pf-diagnosis-index-v1"
        ):
            raise ValueError("PF diagnosis index has an unsupported format")
        entries = document.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("PF diagnosis index entries are invalid")
        validated: dict[str, dict[str, str]] = {}
        for generation, failures in entries.items():
            if not isinstance(generation, str) or not isinstance(failures, dict):
                raise ValueError("PF diagnosis index generation is invalid")
            validated_failures: dict[str, str] = {}
            for indexed_failure, relative in failures.items():
                if not isinstance(indexed_failure, str) or not isinstance(
                    relative, str
                ):
                    raise ValueError("PF diagnosis index locator is invalid")
                self._validate_relative_locator(relative)
                validated_failures[indexed_failure] = relative
            validated[generation] = validated_failures
        return validated

    def _replace_associations_windows(
        self,
        report_generation_id: str,
        located: dict[str, str],
        *,
        replace_generation: bool,
        remove_failure_ids: tuple[str, ...],
    ) -> None:
        guard = self._windows_run
        owns_guard = False
        if guard is None:
            try:
                guard = WindowsRunDirectory.open_existing(root=self._root)
            except FileNotFoundError:
                return
            owns_guard = True
        try:
            index_path = guard.logs_root / self._INDEX_NAME
            try:
                entries = self._parse_index(
                    guard.read_bounded_text(index_path, limit=self._INDEX_LIMIT)
                )
            except FileNotFoundError:
                entries = {}
            self._update_entries(
                entries,
                report_generation_id,
                located,
                replace_generation=replace_generation,
                remove_failure_ids=remove_failure_ids,
            )
            guard.write_private(index_path, self._index_content(entries))
        finally:
            if owns_guard:
                guard.close()

    def _lookup_windows(
        self,
        report_generation_id: str,
        failure_id: str,
    ) -> Path | None:
        try:
            guard = WindowsRunDirectory.open_existing(root=self._root)
        except FileNotFoundError:
            return None
        try:
            try:
                entries = self._parse_index(
                    guard.read_bounded_text(
                        guard.logs_root / self._INDEX_NAME,
                        limit=self._INDEX_LIMIT,
                    )
                )
            except FileNotFoundError:
                return None
            generation = entries.get(report_generation_id)
            if not isinstance(generation, dict):
                return None
            relative = generation.get(failure_id)
            if not isinstance(relative, str):
                return None
            self._validate_relative_locator(relative)
            run_name, file_name = Path(relative).parts
            run_guard = WindowsRunDirectory.open_existing(
                root=self._root,
                run_id=run_name,
            )
            try:
                run_guard.validate_regular_file(
                    run_guard.logs_root / run_name / file_name
                )
            finally:
                run_guard.close()
            return Path(".pf") / "logs" / relative
        finally:
            guard.close()

    @staticmethod
    def _update_entries(
        entries: dict[str, dict[str, str]],
        report_generation_id: str,
        located: dict[str, str],
        *,
        replace_generation: bool,
        remove_failure_ids: tuple[str, ...],
    ) -> None:
        generation: dict[str, str] = (
            {} if replace_generation else dict(entries.get(report_generation_id, {}))
        )
        for failure_id in remove_failure_ids:
            generation.pop(failure_id, None)
        generation.update(located)
        if generation:
            entries[report_generation_id] = generation
        else:
            entries.pop(report_generation_id, None)

    @staticmethod
    def _index_content(entries: dict[str, dict[str, str]]) -> str:
        return (
            json.dumps(
                {
                    "format": "pf-diagnosis-index-v1",
                    "entries": entries,
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

    @staticmethod
    def _validate_relative_locator(relative: str) -> None:
        parts = Path(relative).parts
        if (
            len(parts) != 2
            or any(part in {"", ".", ".."} for part in parts)
            or re.fullmatch(r"[A-Za-z0-9._-]+", parts[0]) is None
            or re.fullmatch(r"process-[0-9]{4}\.log", parts[1]) is None
        ):
            raise ValueError("PF diagnosis log locator is unsafe")

    def _validated_log_path(self, logs_fd: int, relative: str) -> Path:
        self._validate_relative_locator(relative)
        run_name, file_name = Path(relative).parts
        run_fd = self._open_directory(logs_fd, run_name)
        descriptor: int | None = None
        try:
            descriptor = os.open(
                file_name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=run_fd,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("PF diagnosis log is not a regular file")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(run_fd)
        return Path(".pf") / "logs" / relative

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
        if not cls._is_directory_mode(linked.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (linked.st_dev, linked.st_ino):
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
        if self._windows_run is None:
            raise OSError("Windows run directory guard is unavailable")
        self._windows_run.write_private(path, content)

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

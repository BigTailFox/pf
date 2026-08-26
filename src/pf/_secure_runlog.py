from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import secrets
import stat
from typing import Protocol, TextIO, TypeVar

from pf.windows_runlog import WindowsRunDirectory


_ReadT = TypeVar("_ReadT")


class SecureLogDirectory(Protocol):
    """Secure filesystem operations required by RunLogStore product logic."""

    def ensure_run(self, manifest: str) -> None: ...

    def write_run_text(self, name: str, content: str) -> None: ...

    def write_run_stream(
        self,
        name: str,
        write_body: Callable[[TextIO], None],
    ) -> None: ...

    def read_run_text(
        self,
        run_id: str,
        name: str,
        limit: int | None,
    ) -> str: ...

    def read_run_stream(
        self,
        run_id: str,
        name: str,
        read_body: Callable[[TextIO], _ReadT],
    ) -> _ReadT: ...

    def read_logs_text(self, name: str, limit: int) -> str: ...

    def write_logs_text(self, name: str, content: str) -> None: ...

    def resolve_regular_log(self, relative: Path) -> Path | None: ...

    def close(self) -> None: ...


def secure_log_directory(*, root: Path, run_id: str) -> SecureLogDirectory:
    if os.name == "nt":
        return WindowsDirectoryAdapter(root=root, run_id=run_id)
    if os.name == "posix" and hasattr(os, "O_NOFOLLOW"):
        return PosixDirectoryAdapter(root=root, run_id=run_id)
    raise OSError("secure PF run logs are unsupported on this platform")


class PosixDirectoryAdapter:
    """Guard PF log paths with dir_fd, no-follow opens, and inode identity."""

    _STREAM_CHUNK_SIZE = 65_536

    def __init__(self, *, root: Path, run_id: str) -> None:
        self._root = root.resolve()
        self._run_id = run_id
        self._run_identity: tuple[int, int] | None = None

    def ensure_run(self, manifest: str) -> None:
        if self._run_identity is not None:
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
            opened = os.fstat(run_fd)
            self._run_identity = (opened.st_dev, opened.st_ino)
            self._write_private_text(run_fd, "run.log", manifest)
        finally:
            self._close_descriptors(run_fd, logs_fd, pf_fd, root_fd)

    def write_run_text(self, name: str, content: str) -> None:
        run_fd = self._open_current_run()
        try:
            self._write_private_text(run_fd, name, content)
        finally:
            os.close(run_fd)

    def write_run_stream(
        self,
        name: str,
        write_body: Callable[[TextIO], None],
    ) -> None:
        run_fd = self._open_current_run()
        try:
            self._write_private_stream(run_fd, name, write_body)
        finally:
            os.close(run_fd)

    def read_run_text(
        self,
        run_id: str,
        name: str,
        limit: int | None,
    ) -> str:
        logs_fd = self._open_existing_logs()
        if logs_fd is None:
            raise FileNotFoundError(name)
        try:
            run_fd = self._open_directory(logs_fd, run_id)
            try:
                return self._read_regular_text(run_fd, name, limit)
            finally:
                os.close(run_fd)
        finally:
            os.close(logs_fd)

    def read_run_stream(
        self,
        run_id: str,
        name: str,
        read_body: Callable[[TextIO], _ReadT],
    ) -> _ReadT:
        logs_fd = self._open_existing_logs()
        if logs_fd is None:
            raise FileNotFoundError(name)
        try:
            run_fd = self._open_directory(logs_fd, run_id)
            try:
                return self._read_regular_stream(run_fd, name, read_body)
            finally:
                os.close(run_fd)
        finally:
            os.close(logs_fd)

    def read_logs_text(self, name: str, limit: int) -> str:
        logs_fd = self._open_existing_logs()
        if logs_fd is None:
            raise FileNotFoundError(name)
        try:
            return self._read_regular_text(logs_fd, name, limit)
        finally:
            os.close(logs_fd)

    def write_logs_text(self, name: str, content: str) -> None:
        logs_fd = self._open_existing_logs()
        if logs_fd is None:
            return
        try:
            self._write_private_text(logs_fd, name, content)
        finally:
            os.close(logs_fd)

    def resolve_regular_log(self, relative: Path) -> Path | None:
        run_name, file_name = relative.parts
        logs_fd = self._open_existing_logs()
        if logs_fd is None:
            return None
        try:
            run_fd = self._open_directory(logs_fd, run_name)
            try:
                descriptor = os.open(
                    file_name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=run_fd,
                )
                try:
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise OSError("PF diagnosis log is not a regular file")
                finally:
                    os.close(descriptor)
            finally:
                os.close(run_fd)
        finally:
            os.close(logs_fd)
        return Path(".pf") / "logs" / relative

    def close(self) -> None:
        return

    def _open_current_run(self) -> int:
        if self._run_identity is None:
            raise OSError("PF run log directory was not initialized")
        logs_fd = self._open_existing_logs()
        if logs_fd is None:
            raise OSError("PF run log directory disappeared")
        run_fd: int | None = None
        try:
            run_fd = self._open_directory(logs_fd, self._run_id)
            opened = os.fstat(run_fd)
            if (opened.st_dev, opened.st_ino) != self._run_identity:
                raise OSError("PF run log directory identity changed")
            result = run_fd
            run_fd = None
            return result
        finally:
            if run_fd is not None:
                os.close(run_fd)
            os.close(logs_fd)

    def _open_root(self) -> int:
        descriptor = os.open(self._root, self._directory_flags())
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
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
            self._close_descriptors(logs_fd, pf_fd, root_fd)

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
        if not stat.S_ISDIR(linked.st_mode) or (
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

    @classmethod
    def _read_regular_text(
        cls,
        directory_fd: int,
        name: str,
        limit: int | None,
    ) -> str:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("PF log entry is not a regular file")
            if limit is not None and opened.st_size > limit:
                raise ValueError("PF log entry exceeds its size limit")
            with os.fdopen(
                descriptor,
                "r",
                encoding="utf-8",
                newline="",
            ) as stream:
                descriptor = -1
                return stream.read() if limit is None else stream.read(limit + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @classmethod
    def _read_regular_stream(
        cls,
        directory_fd: int,
        name: str,
        read_body: Callable[[TextIO], _ReadT],
    ) -> _ReadT:
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("PF log entry is not a regular file")
            with os.fdopen(
                descriptor,
                "r",
                encoding="utf-8",
                newline="",
            ) as stream:
                descriptor = -1
                return read_body(stream)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @classmethod
    def _write_private_text(
        cls,
        directory_fd: int,
        name: str,
        content: str,
    ) -> None:
        def write_content(stream: TextIO) -> None:
            stream.write(content)

        cls._write_private_stream(directory_fd, name, write_content)

    @classmethod
    def _write_private_stream(
        cls,
        directory_fd: int,
        name: str,
        write_body: Callable[[TextIO], None],
    ) -> None:
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
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="",
                buffering=cls._STREAM_CHUNK_SIZE,
            ) as stream:
                descriptor = None
                write_body(stream)
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

    @staticmethod
    def _close_descriptors(*descriptors: int | None) -> None:
        for descriptor in descriptors:
            if descriptor is not None:
                os.close(descriptor)


class WindowsDirectoryAdapter:
    """Compose WindowsRunDirectory into the secure log-directory seam."""

    def __init__(self, *, root: Path, run_id: str) -> None:
        self._root = root.resolve()
        self._run_id = run_id
        self._run: WindowsRunDirectory | None = None

    def ensure_run(self, manifest: str) -> None:
        if self._run is not None:
            return
        self._run = WindowsRunDirectory.create(root=self._root, run_id=self._run_id)
        self.write_run_text("run.log", manifest)

    def write_run_text(self, name: str, content: str) -> None:
        def write_content(stream: TextIO) -> None:
            stream.write(content)

        self.write_run_stream(name, write_content)

    def write_run_stream(
        self,
        name: str,
        write_body: Callable[[TextIO], None],
    ) -> None:
        run = self._require_run()
        run.assert_intact()
        run.write_private_stream(run.logs_root / self._run_id / name, write_body)
        run.assert_intact()

    def read_run_text(
        self,
        run_id: str,
        name: str,
        limit: int | None,
    ) -> str:
        guard, owned = self._run_guard(run_id)
        try:
            return guard.read_bounded_text(
                guard.logs_root / run_id / name,
                limit=limit,
            )
        finally:
            if owned:
                guard.close()

    def read_run_stream(
        self,
        run_id: str,
        name: str,
        read_body: Callable[[TextIO], _ReadT],
    ) -> _ReadT:
        guard, owned = self._run_guard(run_id)
        try:
            return guard.read_text_stream(
                guard.logs_root / run_id / name,
                read_body,
            )
        finally:
            if owned:
                guard.close()

    def read_logs_text(self, name: str, limit: int) -> str:
        guard, owned = self._logs_guard()
        try:
            return guard.read_bounded_text(guard.logs_root / name, limit=limit)
        finally:
            if owned:
                guard.close()

    def write_logs_text(self, name: str, content: str) -> None:
        try:
            guard, owned = self._logs_guard()
        except FileNotFoundError:
            return
        try:
            guard.write_private(guard.logs_root / name, content)
        finally:
            if owned:
                guard.close()

    def resolve_regular_log(self, relative: Path) -> Path | None:
        run_name, file_name = relative.parts
        try:
            guard = WindowsRunDirectory.open_existing(
                root=self._root,
                run_id=run_name,
            )
        except FileNotFoundError:
            return None
        try:
            guard.validate_regular_file(guard.logs_root / run_name / file_name)
        finally:
            guard.close()
        return Path(".pf") / "logs" / relative

    def close(self) -> None:
        if self._run is not None:
            self._run.close()
            self._run = None

    def _require_run(self) -> WindowsRunDirectory:
        if self._run is None:
            raise OSError("Windows run directory guard is unavailable")
        return self._run

    def _run_guard(self, run_id: str) -> tuple[WindowsRunDirectory, bool]:
        if run_id == self._run_id and self._run is not None:
            return self._run, False
        return (
            WindowsRunDirectory.open_existing(root=self._root, run_id=run_id),
            True,
        )

    def _logs_guard(self) -> tuple[WindowsRunDirectory, bool]:
        if self._run is not None:
            return self._run, False
        return WindowsRunDirectory.open_existing(root=self._root), True

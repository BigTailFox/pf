from __future__ import annotations

import ctypes
import os
from pathlib import Path
import secrets
from typing import Any


_FILE_READ_ATTRIBUTES = 0x0080
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_PERSISTENT_ACLS = 0x00000008
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_SECURITY_DESCRIPTOR_REVISION = 1


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [
        ("file_attributes", ctypes.c_uint32),
        ("reparse_tag", ctypes.c_uint32),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("security_descriptor", ctypes.c_void_p),
        ("inherit_handle", ctypes.c_int),
    ]


class WindowsRunDirectory:
    """Lock a Windows run-log path and apply an owner-only inheritable DACL."""

    def __init__(self, *, handles: tuple[Any, ...], paths: tuple[Path, ...]) -> None:
        self._handles = handles
        self._paths = paths

    @classmethod
    def create(cls, *, root: Path, run_id: str) -> "WindowsRunDirectory":
        if os.name != "nt":
            raise OSError("Windows run-log guards require Windows")
        cls._require_persistent_acls(root)
        handles: list[Any] = []
        paths: list[Path] = []
        try:
            handles.append(cls._open_directory(root))
            paths.append(root)
            pf_root = root / ".pf"
            pf_root.mkdir(exist_ok=True)
            handles.append(cls._open_directory(pf_root))
            paths.append(pf_root)
            logs_root = pf_root / "logs"
            logs_root.mkdir(exist_ok=True)
            handles.append(cls._open_directory(logs_root))
            paths.append(logs_root)
            run_root = logs_root / run_id
            cls._create_private_directory(run_root)
            handles.append(cls._open_directory(run_root))
            paths.append(run_root)
            guard = cls(handles=tuple(handles), paths=tuple(paths))
            guard.assert_intact()
            return guard
        except Exception:
            cls._close_all(tuple(handles))
            raise

    @classmethod
    def open_existing(  # pragma: no cover
        cls,
        *,
        root: Path,
        run_id: str | None = None,
    ) -> "WindowsRunDirectory":
        """Lock an existing log hierarchy for an offline index or log read."""
        if os.name != "nt":
            raise OSError("Windows run-log guards require Windows")
        cls._require_persistent_acls(root)
        handles: list[Any] = []
        paths: list[Path] = []
        try:
            for path in (root, root / ".pf", root / ".pf" / "logs"):
                handles.append(cls._open_directory(path))
                paths.append(path)
            if run_id is not None:
                run_root = paths[-1] / run_id
                handles.append(cls._open_directory(run_root))
                paths.append(run_root)
            guard = cls(handles=tuple(handles), paths=tuple(paths))
            guard.assert_intact()
            return guard
        except Exception:
            cls._close_all(tuple(handles))
            raise

    @property
    def logs_root(self) -> Path:  # pragma: no cover
        if len(self._paths) < 3:
            raise OSError("Windows log directory guard is incomplete")
        return self._paths[2]

    def read_bounded_text(  # pragma: no cover
        self, path: Path, *, limit: int | None
    ) -> str:
        """Read a guarded regular file while its native handle prevents replacement."""
        self.assert_intact()
        self._require_guarded_parent(path)
        handle = self._open_file(path, access=_GENERIC_READ)
        descriptor: int | None = None
        try:
            import msvcrt  # pragma: no cover

            open_osfhandle = getattr(msvcrt, "open_osfhandle")
            descriptor = open_osfhandle(handle, os.O_RDONLY)
            handle = None
            opened = os.fstat(descriptor)
            if limit is not None and opened.st_size > limit:
                raise ValueError("PF diagnosis index exceeds its size limit")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = None
                return stream.read() if limit is None else stream.read(limit + 1)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if handle is not None:
                self._close_all((handle,))

    def validate_regular_file(self, path: Path) -> None:  # pragma: no cover
        """Validate a guarded file without following a reparse point."""
        self.assert_intact()
        self._require_guarded_parent(path)
        handle = self._open_file(path, access=_FILE_READ_ATTRIBUTES)
        self._close_all((handle,))

    def write_private(self, path: Path, content: str) -> None:  # pragma: no cover
        """Atomically replace a guarded file created with an owner-only DACL."""
        self.write_private_stream(path, lambda stream: stream.write(content))

    def write_private_stream(
        self, path: Path, write_body: Any
    ) -> None:  # pragma: no cover
        """Atomically replace a guarded file from a bounded streaming writer."""
        self.assert_intact()
        self._require_guarded_parent(path)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        descriptor: int | None = None
        handle: Any | None = None
        try:
            handle = self._create_private_file(temporary)
            import msvcrt  # pragma: no cover

            open_osfhandle = getattr(msvcrt, "open_osfhandle")
            descriptor = open_osfhandle(handle, os.O_WRONLY)
            handle = None
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                write_body(stream)
                stream.flush()
                os.fsync(stream.fileno())
            self.assert_intact()
            os.replace(temporary, path)
            self.assert_intact()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if handle is not None:
                self._close_all((handle,))
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def assert_intact(self) -> None:
        if not self._handles:
            raise OSError("Windows run-log directory guard is closed")
        for handle, path in zip(self._handles, self._paths):
            attributes = self._attributes(handle)
            if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise OSError(f"PF log path is not a directory: {path}")
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise OSError(f"PF log directory cannot be a reparse point: {path}")

    def close(self) -> None:
        handles, self._handles = self._handles, ()
        self._close_all(handles)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _require_guarded_parent(self, path: Path) -> None:  # pragma: no cover
        if path.parent not in self._paths:
            raise OSError(f"PF log file is outside the guarded hierarchy: {path}")

    @staticmethod
    def _api() -> tuple[Any, Any, Any]:  # pragma: no cover
        from ctypes import wintypes

        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise OSError("Windows security APIs are unavailable")
        kernel32 = loader("kernel32", use_last_error=True)
        advapi32 = loader("advapi32", use_last_error=True)
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandleEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel32.CreateDirectoryW.argtypes = (
            wintypes.LPCWSTR,
            ctypes.POINTER(_SecurityAttributes),
        )
        kernel32.CreateDirectoryW.restype = wintypes.BOOL
        kernel32.GetVolumeInformationW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        )
        kernel32.GetVolumeInformationW.restype = wintypes.BOOL
        kernel32.GetVolumePathNameW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
        )
        kernel32.GetVolumePathNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = (wintypes.LPVOID,)
        kernel32.LocalFree.restype = wintypes.LPVOID
        return kernel32, advapi32, wintypes

    @classmethod
    def _open_directory(cls, path: Path) -> Any:  # pragma: no cover
        kernel32, _, wintypes = cls._api()
        handle = kernel32.CreateFileW(
            str(path),
            _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise cls._last_windows_error()
        attributes = cls._attributes(handle)
        if (
            not attributes & _FILE_ATTRIBUTE_DIRECTORY
            or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            kernel32.CloseHandle(handle)
            raise OSError(f"PF log directory is unsafe: {path}")
        return handle

    @classmethod
    def _open_file(  # pragma: no cover
        cls, path: Path, *, access: int
    ) -> Any:
        kernel32, _, wintypes = cls._api()
        handle = kernel32.CreateFileW(
            str(path),
            access,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise cls._last_windows_error()
        attributes = cls._attributes(handle)
        if (
            attributes & _FILE_ATTRIBUTE_DIRECTORY
            or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            kernel32.CloseHandle(handle)
            raise OSError(f"PF log file is unsafe: {path}")
        return handle

    @classmethod
    def _require_persistent_acls(cls, root: Path) -> None:  # pragma: no cover
        kernel32, _, wintypes = cls._api()
        volume_root = ctypes.create_unicode_buffer(32_768)
        if not kernel32.GetVolumePathNameW(
            str(root),
            volume_root,
            len(volume_root),
        ):
            raise cls._last_windows_error()
        flags = wintypes.DWORD()
        if not kernel32.GetVolumeInformationW(
            volume_root.value,
            None,
            0,
            None,
            None,
            ctypes.byref(flags),
            None,
            0,
        ):
            raise cls._last_windows_error()
        if not flags.value & _FILE_PERSISTENT_ACLS:
            raise OSError("PF run logs require a Windows volume with persistent ACLs")

    @classmethod
    def _attributes(cls, handle: Any) -> int:  # pragma: no cover
        kernel32, _, _ = cls._api()
        info = _FileAttributeTagInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise cls._last_windows_error()
        return int(info.file_attributes)

    @classmethod
    def _create_private_directory(cls, path: Path) -> None:  # pragma: no cover
        kernel32, advapi32, wintypes = cls._api()
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )
        descriptor = wintypes.LPVOID()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;OW)",
            _SECURITY_DESCRIPTOR_REVISION,
            ctypes.byref(descriptor),
            None,
        ):
            raise cls._last_windows_error()
        try:
            security = _SecurityAttributes(
                length=ctypes.sizeof(_SecurityAttributes),
                security_descriptor=descriptor,
                inherit_handle=False,
            )
            if not kernel32.CreateDirectoryW(str(path), ctypes.byref(security)):
                raise cls._last_windows_error()
        finally:
            kernel32.LocalFree(descriptor)

    @classmethod
    def _create_private_file(cls, path: Path) -> Any:  # pragma: no cover
        kernel32, advapi32, wintypes = cls._api()
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )
        security_descriptor = wintypes.LPVOID()
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P(A;;FA;;;SY)(A;;FA;;;OW)",
            _SECURITY_DESCRIPTOR_REVISION,
            ctypes.byref(security_descriptor),
            None,
        ):
            raise cls._last_windows_error()
        try:
            security = _SecurityAttributes(
                length=ctypes.sizeof(_SecurityAttributes),
                security_descriptor=security_descriptor,
                inherit_handle=False,
            )
            handle = kernel32.CreateFileW(
                str(path),
                _GENERIC_WRITE,
                0,
                ctypes.byref(security),
                _CREATE_NEW,
                _FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            if handle == wintypes.HANDLE(-1).value:
                raise cls._last_windows_error()
            return handle
        finally:
            kernel32.LocalFree(security_descriptor)

    @classmethod
    def _close_all(cls, handles: tuple[Any, ...]) -> None:  # pragma: no cover
        if not handles or os.name != "nt":
            return
        kernel32, _, _ = cls._api()
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)

    @staticmethod
    def _last_windows_error() -> OSError:  # pragma: no cover
        get_last_error = getattr(ctypes, "get_last_error", None)
        code = int(get_last_error()) if callable(get_last_error) else 0
        return OSError(code, "Windows security API call failed")

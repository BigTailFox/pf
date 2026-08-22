from __future__ import annotations

from pathlib import Path
import ctypes
from typing import Any, cast

import pytest

import pf.windows_runlog as windows_runlog
from pf.windows_runlog import WindowsRunDirectory


class TestWindowsRunDirectory:
    def test_windows_run_directory_creates_private_run_before_opening_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        opened: list[Path] = []
        created: list[Path] = []
        closed: list[tuple[object, ...]] = []
        monkeypatch.setattr(windows_runlog.os, "name", "nt")

        def open_directory(cls: type[WindowsRunDirectory], path: Path) -> object:
            opened.append(path)
            return path.as_posix()

        def create_private(cls: type[WindowsRunDirectory], path: Path) -> None:
            created.append(path)
            path.mkdir()

        monkeypatch.setattr(
            WindowsRunDirectory,
            "_open_directory",
            classmethod(open_directory),
        )
        monkeypatch.setattr(
            WindowsRunDirectory,
            "_require_persistent_acls",
            classmethod(lambda cls, root: None),
        )
        monkeypatch.setattr(
            WindowsRunDirectory,
            "_create_private_directory",
            classmethod(create_private),
        )
        monkeypatch.setattr(
            WindowsRunDirectory,
            "_attributes",
            classmethod(lambda cls, handle: 0x10),
        )
        monkeypatch.setattr(
            WindowsRunDirectory,
            "_close_all",
            classmethod(lambda cls, handles: closed.append(handles)),
        )

        guard = WindowsRunDirectory.create(root=tmp_path, run_id="run")

        assert created == [tmp_path / ".pf/logs/run"]
        assert opened == [
            tmp_path,
            tmp_path / ".pf",
            tmp_path / ".pf/logs",
            tmp_path / ".pf/logs/run",
        ]
        guard.assert_intact()
        guard.close()
        assert len(closed[0]) == 4

    def test_windows_run_directory_rejects_a_reparse_handle(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        guard = WindowsRunDirectory(handles=(object(),), paths=(tmp_path,))
        monkeypatch.setattr(
            WindowsRunDirectory,
            "_attributes",
            classmethod(lambda cls, handle: 0x10 | 0x400),
        )
        monkeypatch.setattr(
            WindowsRunDirectory,
            "_close_all",
            classmethod(lambda cls, handles: None),
        )

        with pytest.raises(OSError, match="reparse point"):
            guard.assert_intact()

        guard.close()

    def test_windows_run_directory_rejects_non_windows(self, tmp_path: Path) -> None:
        if windows_runlog.os.name == "nt":
            pytest.skip("non-Windows guard behavior")

        with pytest.raises(OSError, match="require Windows"):
            WindowsRunDirectory.create(root=tmp_path, run_id="run")

    def test_windows_directory_guard_does_not_share_write_access(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[object, ...]] = []

        class CreateFile:
            def __call__(self, *args: object) -> int:
                calls.append(args)
                return 123

        class Kernel:
            CreateFileW = CreateFile()

            @staticmethod
            def CloseHandle(handle: object) -> None:
                return None

        class WinTypes:
            HANDLE = ctypes.c_void_p

        monkeypatch.setattr(
            WindowsRunDirectory,
            "_api",
            staticmethod(lambda: (Kernel(), object(), WinTypes())),
        )
        monkeypatch.setattr(
            WindowsRunDirectory,
            "_attributes",
            classmethod(lambda cls, handle: 0x10),
        )

        handle = WindowsRunDirectory._open_directory(tmp_path)

        assert handle == 123
        assert calls[0][2] == 0x00000001

    def test_windows_directory_guard_rejects_a_volume_without_persistent_acls(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        checked_volumes: list[str] = []

        class VolumePath:
            def __call__(self, *args: object) -> bool:
                buffer = cast(Any, args[1])
                buffer.value = "X:\\mounted-volume\\"
                return True

        class VolumeInformation:
            def __call__(self, *args: object) -> bool:
                checked_volumes.append(cast(str, args[0]))
                flags = ctypes.cast(
                    cast(Any, args[5]),
                    ctypes.POINTER(ctypes.c_uint32),
                )
                flags.contents.value = 0
                return True

        class Kernel:
            GetVolumePathNameW = VolumePath()
            GetVolumeInformationW = VolumeInformation()

        class WinTypes:
            DWORD = ctypes.c_uint32

        monkeypatch.setattr(
            WindowsRunDirectory,
            "_api",
            staticmethod(lambda: (Kernel(), object(), WinTypes())),
        )
        with pytest.raises(OSError, match="persistent ACLs"):
            WindowsRunDirectory._require_persistent_acls(tmp_path)

        assert checked_volumes == ["X:\\mounted-volume\\"]

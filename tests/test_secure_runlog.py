from __future__ import annotations

from collections.abc import Callable
from io import StringIO
import os
from pathlib import Path
import stat
from typing import TextIO

import pytest

from pf._secure_runlog import (
    PosixDirectoryAdapter,
    WindowsDirectoryAdapter,
    secure_log_directory,
)


class TestSecureLogDirectory:
    def test_secure_log_directory_selects_the_windows_adapter(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os, "name", "nt")

        adapter = secure_log_directory(root=tmp_path, run_id="run")

        assert isinstance(adapter, WindowsDirectoryAdapter)

    def test_secure_log_directory_rejects_an_unsupported_platform(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os, "name", "unsupported")

        with pytest.raises(OSError, match="unsupported"):
            secure_log_directory(root=tmp_path, run_id="run")


class TestPosixDirectoryAdapter:
    def test_secure_directory_initialization_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")

        adapter.ensure_run("first\n")
        adapter.ensure_run("second\n")

        assert adapter.read_run_text("run", "run.log", None) == "first\n"

    def test_read_run_text_reports_a_missing_log_directory(
        self, tmp_path: Path
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")

        with pytest.raises(FileNotFoundError):
            adapter.read_run_text("run", "missing", None)

    def test_read_run_stream_reports_a_missing_log_directory(
        self,
        tmp_path: Path,
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")

        with pytest.raises(FileNotFoundError):
            adapter.read_run_stream("run", "missing", lambda stream: stream.read())

    def test_read_logs_text_reports_a_missing_log_directory(
        self, tmp_path: Path
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")

        with pytest.raises(FileNotFoundError):
            adapter.read_logs_text("missing", 100)

    def test_resolve_regular_log_returns_none_without_a_log_directory(
        self,
        tmp_path: Path,
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")

        assert adapter.resolve_regular_log(Path("run/missing")) is None

    def test_write_logs_text_is_a_noop_without_a_log_directory(
        self,
        tmp_path: Path,
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")

        adapter.write_logs_text("index.json", "ignored")

        assert not (tmp_path / ".pf/logs/index.json").exists()

    def test_write_run_text_rejects_an_uninitialized_run(
        self,
        tmp_path: Path,
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")

        with pytest.raises(OSError, match="not initialized"):
            adapter.write_run_text("journal.json", "unsafe")

    def test_write_run_text_rejects_a_disappeared_run(
        self,
        tmp_path: Path,
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")
        adapter.ensure_run("manifest\n")
        (tmp_path / ".pf/logs").rename(tmp_path / "detached-logs")

        with pytest.raises(OSError, match="disappeared"):
            adapter.write_run_text("journal.json", "unsafe")

    def test_secure_directory_requires_a_directory_root(self, tmp_path: Path) -> None:
        root = tmp_path / "not-a-directory"
        root.write_text("file", encoding="utf-8")

        with pytest.raises(OSError):
            PosixDirectoryAdapter(root=root, run_id="run").ensure_run("manifest\n")

    @pytest.mark.parametrize("entry", ("oversized", "directory"))
    def test_run_text_requires_a_bounded_regular_file(
        self,
        tmp_path: Path,
        entry: str,
    ) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")
        adapter.ensure_run("manifest\n")
        run_root = tmp_path / ".pf/logs/run"
        if entry == "oversized":
            adapter.write_run_text("entry", "too long")
            limit = 2
        else:
            (run_root / "entry").mkdir()
            limit = None

        with pytest.raises(ValueError):
            adapter.read_run_text("run", "entry", limit)

    def test_secure_directory_contract(self, tmp_path: Path) -> None:
        def write_log(stream: TextIO) -> None:
            stream.write("log")

        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")
        adapter.ensure_run("manifest\n")
        adapter.write_run_text("journal.json", "journal\n")
        adapter.write_run_stream("process-0001.log", write_log)
        adapter.write_logs_text("diagnosis-index.json", "index\n")

        run_root = tmp_path / ".pf/logs/run"
        assert stat.S_IMODE(run_root.stat().st_mode) == 0o700
        assert stat.S_IMODE((run_root / "journal.json").stat().st_mode) == 0o600
        assert adapter.read_run_text("run", "journal.json", 100) == "journal\n"
        assert (
            adapter.read_run_stream(
                "run",
                "process-0001.log",
                lambda stream: stream.read(),
            )
            == "log"
        )
        assert adapter.read_logs_text("diagnosis-index.json", 100) == "index\n"
        assert adapter.resolve_regular_log(Path("run/process-0001.log")) == Path(
            ".pf/logs/run/process-0001.log"
        )

    def test_replaced_run_and_non_regular_log_fail_closed(self, tmp_path: Path) -> None:
        adapter = PosixDirectoryAdapter(root=tmp_path, run_id="run")
        adapter.ensure_run("manifest\n")
        run_root = tmp_path / ".pf/logs/run"
        run_root.rename(tmp_path / ".pf/logs/original")
        outside = tmp_path / "outside"
        outside.mkdir()
        run_root.symlink_to(outside, target_is_directory=True)

        with pytest.raises(OSError):
            adapter.write_run_text("journal.json", "unsafe")
        assert not (outside / "journal.json").exists()


class TestWindowsDirectoryAdapter:
    def test_secure_directory_contract(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        closed: list[str] = []

        class FakeWindowsRunDirectory:
            def __init__(self, root: Path, run_id: str | None) -> None:
                self.root = root
                self.run_id = run_id

            @classmethod
            def create(
                cls,
                *,
                root: Path,
                run_id: str,
            ) -> "FakeWindowsRunDirectory":
                (root / ".pf/logs" / run_id).mkdir(parents=True)
                return cls(root, run_id)

            @classmethod
            def open_existing(
                cls,
                *,
                root: Path,
                run_id: str | None = None,
            ) -> "FakeWindowsRunDirectory":
                if not (root / ".pf/logs").is_dir():
                    raise FileNotFoundError
                return cls(root, run_id)

            @property
            def logs_root(self) -> Path:
                return self.root / ".pf/logs"

            def assert_intact(self) -> None:
                return

            def write_private(self, path: Path, content: str) -> None:
                path.write_text(content, encoding="utf-8")

            def write_private_stream(
                self,
                path: Path,
                write_body: Callable[[TextIO], None],
            ) -> None:
                stream = StringIO()
                write_body(stream)
                self.write_private(path, stream.getvalue())

            def read_bounded_text(
                self,
                path: Path,
                *,
                limit: int | None,
            ) -> str:
                content = path.read_text(encoding="utf-8")
                return content if limit is None else content[: limit + 1]

            def read_text_stream(
                self,
                path: Path,
                read_body: Callable[[TextIO], object],
            ) -> object:
                with path.open(encoding="utf-8") as stream:
                    return read_body(stream)

            def validate_regular_file(self, path: Path) -> None:
                if not path.is_file():
                    raise OSError("unsafe PF log file")

            def close(self) -> None:
                closed.append(self.run_id or "logs")

        monkeypatch.setattr(
            "pf._secure_runlog.WindowsRunDirectory",
            FakeWindowsRunDirectory,
        )
        adapter = WindowsDirectoryAdapter(root=tmp_path, run_id="run")
        adapter.ensure_run("manifest\n")
        adapter.write_run_text("process-0001.log", "log")
        adapter.write_logs_text("diagnosis-index.json", "index\n")

        assert adapter.read_run_text("run", "process-0001.log", None) == "log"
        assert (
            adapter.read_run_stream(
                "run",
                "process-0001.log",
                lambda stream: stream.read(),
            )
            == "log"
        )
        assert adapter.read_logs_text("diagnosis-index.json", 100) == "index\n"
        assert adapter.resolve_regular_log(Path("run/process-0001.log")) == Path(
            ".pf/logs/run/process-0001.log"
        )
        adapter.close()
        assert "run" in closed

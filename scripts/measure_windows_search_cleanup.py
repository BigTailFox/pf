"""Instrument PreparedEnvironment close timing around a native Windows pf search.

This is an experiment wrapper, not a product switch. It monkeypatches close /
finish_coordinate, writes JSONL, then invokes the public pf CLI.

    .venv/Scripts/python.exe scripts/measure_windows_search_cleanup.py \\
        --output-dir docs/experiments/data/E010 -- \\
        search --max-cells 1 --ty-jobs 1 --test-jobs 1
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[1]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _tree_stats(root: Path) -> tuple[int, int]:
    files = 0
    bytes_ = 0
    if not root.exists():
        return 0, 0
    for path in root.rglob("*"):
        files += 1
        try:
            if path.is_file() and not path.is_symlink():
                bytes_ += path.stat().st_size
        except OSError:
            continue
    return files, bytes_


def _caller_reason() -> str:
    for frame in inspect.stack()[2:18]:
        name = Path(frame.filename).name
        function = frame.function
        if name == "search.py" and function == "finish_coordinate":
            return "finish_coordinate"
        if name == "search.py" and function == "_release_prepared":
            return "release_prepared"
        if name == "search.py" and function == "close":
            return "proposal_runner_close"
        if name == "baseline.py":
            return "baseline_finally"
        if name == "check.py":
            return "check"
        if name == "environment.py" and function == "prepare":
            return "prepare_failure"
    return "other"


def _host_facts() -> dict[str, Any]:
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "os_name": os.name,
        "sys_platform": sys.platform,
        "wsl_distro": os.environ.get("WSL_DISTRO_NAME"),
        "wsl_interop": os.environ.get("WSL_INTEROP"),
        "tempdir": __import__("tempfile").gettempdir(),
        "cpu_count": os.cpu_count(),
        "cwd": str(Path.cwd()),
        "has_killpg": hasattr(os, "killpg"),
        "git_head": _git_head(),
    }


def _git_head() -> str | None:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def install_probes(stream: TextIO) -> None:
    from pf.environment import PreparedEnvironment
    from pf.search import _ProposalRunner
    from pf.snapshot import SourceSnapshot

    lock = threading.Lock()
    original_env_close = PreparedEnvironment.close
    original_snapshot_close = SourceSnapshot.close
    original_finish = _ProposalRunner.finish_coordinate
    original_runner_close = _ProposalRunner.close

    def emit(record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=True, sort_keys=True)
        with lock:
            stream.write(line + "\n")
            stream.flush()

    def close_environment(self: PreparedEnvironment) -> None:
        root = Path(self._temporary_directory.name)
        files, bytes_ = _tree_stats(root)
        cell = self.proposal.cell
        record: dict[str, Any] = {
            "event": "prepared-environment-close",
            "reason": _caller_reason(),
            "utc": _utc_now(),
            "python_minor": cell.python_minor,
            "target": cell.target,
            "proposal_id": self.proposal.proposal_id,
            "path": str(root),
            "file_count": files,
            "byte_count": bytes_,
            "already_closed": self.closed,
        }
        started = time.monotonic()
        error: str | None = None
        try:
            original_env_close(self)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_s"] = round(time.monotonic() - started, 6)
            record["error"] = error
            record["exists_after"] = root.exists()
            emit(record)

    def close_snapshot(self: SourceSnapshot) -> None:
        root = Path(self._temporary_directory.name)
        files, bytes_ = _tree_stats(root)
        record = {
            "event": "snapshot-close",
            "reason": _caller_reason(),
            "utc": _utc_now(),
            "path": str(root),
            "file_count": files,
            "byte_count": bytes_,
        }
        started = time.monotonic()
        error: str | None = None
        try:
            original_snapshot_close(self)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_s"] = round(time.monotonic() - started, 6)
            record["error"] = error
            record["exists_after"] = root.exists()
            emit(record)

    def finish_coordinate(self: _ProposalRunner) -> None:
        record = {
            "event": "finish-coordinate",
            "utc": _utc_now(),
            "python_minor": self._cell.python_minor,
            "target": self._cell.target,
            "prepared_count": len(self._prepared),
        }
        started = time.monotonic()
        error: str | None = None
        try:
            original_finish(self)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_s"] = round(time.monotonic() - started, 6)
            record["error"] = error
            emit(record)

    def runner_close(self: _ProposalRunner) -> None:
        record = {
            "event": "proposal-runner-close",
            "utc": _utc_now(),
            "python_minor": self._cell.python_minor,
            "target": self._cell.target,
            "prepared_count": len(self._prepared),
        }
        started = time.monotonic()
        error: str | None = None
        try:
            original_runner_close(self)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_s"] = round(time.monotonic() - started, 6)
            record["error"] = error
            emit(record)

    PreparedEnvironment.close = close_environment  # type: ignore[method-assign]
    SourceSnapshot.close = close_snapshot  # type: ignore[method-assign]
    _ProposalRunner.finish_coordinate = finish_coordinate  # type: ignore[method-assign]
    _ProposalRunner.close = runner_close  # type: ignore[method-assign]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "docs" / "experiments" / "data" / "E010",
    )
    parser.add_argument("pf_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    pf_args = list(args.pf_args)
    if pf_args and pf_args[0] == "--":
        pf_args = pf_args[1:]
    if not pf_args:
        pf_args = ["search", "--max-cells", "1", "--ty-jobs", "1", "--test-jobs", "1"]

    host_path = output_dir / "host.json"
    host_path.write_text(
        json.dumps(_host_facts(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    cleanup_path = output_dir / "cleanup.jsonl"
    os.chdir(ROOT)
    command_path = output_dir / "command.json"
    command_path.write_text(
        json.dumps(
            {
                "argv": pf_args,
                "cwd": str(ROOT),
                "started_utc": _utc_now(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    from pf.cli import main as pf_main

    with cleanup_path.open("w", encoding="utf-8") as stream:
        install_probes(stream)
        sys.argv = ["pf", *pf_args]
        started = time.monotonic()
        code = 0
        error: str | None = None
        try:
            pf_main()
        except SystemExit as exc:
            code = int(exc.code or 0)
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            code = 1
            raise
        finally:
            summary = {
                "finished_utc": _utc_now(),
                "wall_s": round(time.monotonic() - started, 3),
                "exit_code": code,
                "error": error,
            }
            (output_dir / "summary.json").write_text(
                json.dumps(summary, indent=2) + "\n",
                encoding="utf-8",
            )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

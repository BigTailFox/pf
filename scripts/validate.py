"""Shared local/CI validation lanes. Run with `uv run python scripts/validate.py`."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
DOCS = (
    (PYTHON, "scripts/check_docs.py"),
    (PYTHON, "scripts/generate_report_schema.py", "--check"),
)
STATIC = (
    ("ruff", "check", "src", "tests", "scripts"),
    ("ty", "check", "src"),
)
PYTEST = (PYTHON, "-m", "pytest", "--no-testmon", "-q")
LANES = {
    "docs": DOCS,
    "daily": (*STATIC, *DOCS, (*PYTEST,)),
    "pr": (
        *STATIC, *DOCS, (*PYTEST, "-m", "not qualification"), ("uv", "build"),
    ),
    "coverage": (
        *STATIC, *DOCS,
        (*PYTEST, "--cov=pf", "--cov-report=term-missing", "--cov-fail-under=0", "-m", ""),
        ("uv", "build"),
    ),
    "process": ((*PYTEST, "-m", "process and not qualification"),),
    "qualification": ((*PYTEST, "-m", "qualification"),),
}


def source_state(log_dir: Path) -> dict[str, object]:
    """Record Git inputs without copying patches or untracked file contents into logs."""
    paths = ["--", "."]
    if log_dir.is_relative_to(ROOT):
        paths.append(f":(top,literal,exclude){log_dir.relative_to(ROOT).as_posix()}")

    def git(*args: str) -> bytes:
        return subprocess.run(
            ("git", *args), cwd=ROOT, capture_output=True, check=True, timeout=10,
        ).stdout

    try:
        untracked = {}
        for name in git("ls-files", "--others", "--exclude-standard", "-z", *paths).split(b"\0"):
            if not name:
                continue
            path = ROOT / os.fsdecode(name)
            mode = path.lstat().st_mode
            digest = hashlib.sha256()
            if stat.S_ISLNK(mode):
                digest.update(os.fsencode(os.readlink(path)))
            elif stat.S_ISREG(mode):
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            else:
                return {"error": f"unsupported untracked input: {os.fsdecode(name)}"}
            untracked[os.fsdecode(name)] = {
                "kind": "symlink" if stat.S_ISLNK(mode) else "file",
                "sha256": digest.hexdigest(),
            }
        return {
            "head": git("rev-parse", "HEAD").decode().strip(),
            "status": os.fsdecode(git("status", "--porcelain=v1", "--untracked-files=all", *paths)),
            "worktree_diff_sha256": hashlib.sha256(
                git("diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", *paths)
            ).hexdigest(),
            "index_diff_sha256": hashlib.sha256(
                git("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", *paths)
            ).hexdigest(),
            "untracked": untracked,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)}


def environment_state(commands: Sequence[Sequence[str]]) -> dict[str, object]:
    tools = {}
    for name in sorted({"git", *(command[0] for command in commands if command[0] != PYTHON)}):
        tool: dict[str, object] = {"executable": shutil.which(name)}
        try:
            result = subprocess.run(
                (name, "--version"), cwd=ROOT, capture_output=True, text=True, timeout=10,
            )
            tool.update(exit_code=result.returncode, version=(result.stdout + result.stderr).strip())
        except (OSError, subprocess.SubprocessError) as exc:
            tool["error"] = str(exc)
        tools[name] = tool
    return {
        "python": {"executable": PYTHON, "version": sys.version, "implementation": platform.python_implementation()},
        "platform": sys.platform,
        "machine": platform.machine(),
        "packages": sorted(
            ({"name": dist.metadata["Name"], "version": dist.version} for dist in metadata.distributions()),
            key=lambda item: (item["name"] or "", item["version"]),
        ),
        "tools": tools,
    }


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_manifest(path: Path, manifest: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def execute_steps(
    commands: Sequence[tuple[str, ...]], steps: list[dict[str, object]],
    manifest: dict[str, object], manifest_path: Path, base: str | None,
) -> int:
    if base is not None:
        try:
            result = subprocess.run(
                ("git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"),
                cwd=ROOT, capture_output=True, text=True, check=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            manifest["error"] = f"invalid --base {base!r}: {exc}"
            print(manifest["error"], file=sys.stderr)
            return 2
        base_commit = result.stdout.strip()
        manifest["base_commit"] = base_commit
        commands = tuple(
            (*DOCS[0], "--base", base_commit) if command == (*DOCS[0], "--base", base) else command
            for command in commands
        )
        for command, step in zip(commands, steps):
            step["command"] = list(command)
    save_manifest(manifest_path, manifest)
    manifest["environment"] = environment_state(commands)
    save_manifest(manifest_path, manifest)
    log_dir = manifest_path.parent
    for index, (command, step) in enumerate(zip(commands, steps), 1):
        log_path = log_dir / str(step["log"])
        print(f"[{index}/{len(commands)}] {shlex.join(command)}", flush=True)
        source_before = source_state(log_dir)
        step.update(
            status="running", started_at=timestamp(), source_before=source_before,
            source_after={"error": "not collected"}, source_unchanged=None,
        )
        save_manifest(manifest_path, manifest)
        started = time.monotonic()
        with log_path.open("w", encoding="utf-8") as output:
            try:
                result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
                code = result.returncode
                if code < 0:
                    code = 128 - code
            except OSError as exc:
                output.write(f"Unable to start command: {exc}\n")
                code = 127
            except KeyboardInterrupt:
                output.write("Validation interrupted\n")
                code = 130
        step.update(
            status="passed" if code == 0 else "failed", exit_code=code,
            finished_at=timestamp(), duration_seconds=time.monotonic() - started,
        )
        save_manifest(manifest_path, manifest)
        source_after = source_state(log_dir)
        step.update(
            source_after=source_after,
            source_unchanged=(
                source_before == source_after if "error" not in source_before and "error" not in source_after else None
            ),
        )
        save_manifest(manifest_path, manifest)
        print(f"exit={code} log={log_path}", flush=True)
        if code:
            with log_path.open(encoding="utf-8", errors="replace") as output:
                print("".join(deque(output, maxlen=40))[-8192:], end="", flush=True)
            return code
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=LANES)
    parser.add_argument("--dry-run", action="store_true", help="show commands without executing")
    parser.add_argument("--base", help="also check committed documentation changes from this commit to HEAD")
    parser.add_argument(
        "--log-dir", type=Path, default=ROOT / "tests/.cache/validation",
        help="parent for a fresh directory containing complete per-step logs",
    )
    args = parser.parse_args(argv)
    commands = LANES[args.lane]
    if args.base is not None:
        if DOCS[0] not in commands:
            parser.error("--base requires a lane with documentation checks")
        commands = tuple(
            (*command, "--base", args.base) if command == DOCS[0] else command
            for command in commands
        )
    if args.dry_run:
        print(f"cwd: {ROOT}")
        for command in commands:
            print(shlex.join(command))
        return 0

    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(tempfile.mkdtemp(prefix=f"{args.lane}-", dir=args.log_dir)).resolve()
    print(f"Logs: {log_dir}", flush=True)
    manifest_path = log_dir / "manifest.json"
    steps: list[dict[str, object]] = [
        {"command": list(command), "status": "pending", "exit_code": None, "log": f"{index:02d}.log"}
        for index, command in enumerate(commands, 1)
    ]
    manifest: dict[str, object] = {
        "lane": args.lane, "cwd": str(ROOT), "base": args.base, "base_commit": None,
        "started_at": timestamp(), "finished_at": None, "status": "running", "exit_code": None,
        "environment": {"error": "not collected"}, "steps": steps,
    }
    save_manifest(manifest_path, manifest)
    print(f"Manifest: {manifest_path}", flush=True)
    try:
        code = execute_steps(commands, steps, manifest, manifest_path, args.base)
    except KeyboardInterrupt:
        code = 130
        for step in steps:
            if step["status"] == "running":
                step.update(status="failed", exit_code=code, finished_at=timestamp())
        print("Validation interrupted", file=sys.stderr)
    manifest.update(status="passed" if code == 0 else "failed", exit_code=code, finished_at=timestamp())
    save_manifest(manifest_path, manifest)
    if code == 0:
        print(f"{args.lane}: PASS ({len(commands)} steps)", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

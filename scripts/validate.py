"""Shared local/CI validation lanes. Run with `uv run python scripts/validate.py`."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=LANES)
    parser.add_argument("--dry-run", action="store_true", help="show commands without executing")
    parser.add_argument(
        "--log-dir", type=Path, default=ROOT / "tests/.cache/validation",
        help="parent for a fresh directory containing complete per-step logs",
    )
    args = parser.parse_args(argv)
    commands = LANES[args.lane]
    if args.dry_run:
        print(f"cwd: {ROOT}")
        for command in commands:
            print(shlex.join(command))
        return 0

    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(tempfile.mkdtemp(prefix=f"{args.lane}-", dir=args.log_dir)).resolve()
    print(f"Logs: {log_dir}", flush=True)
    for index, command in enumerate(commands, 1):
        log_path = log_dir / f"{index:02d}.log"
        print(f"[{index}/{len(commands)}] {shlex.join(command)}", flush=True)
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
        print(f"exit={code} log={log_path}", flush=True)
        if code:
            with log_path.open(encoding="utf-8", errors="replace") as output:
                print("".join(deque(output, maxlen=40))[-8192:], end="", flush=True)
            return code
    print(f"{args.lane}: PASS ({len(commands)} steps)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

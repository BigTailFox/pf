"""E012 command runner. Records wall time, exit code, and terminal output."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path("/home/llh/pf")
TARGET = ROOT / "experiments" / "flask"
EVIDENCE = ROOT / "docs" / "experiments" / "data" / "E012"
PF = ROOT / ".venv" / "bin" / "pf"


def run(command: str, evidence_subdir: str | None = None) -> int:
    evidence = EVIDENCE / evidence_subdir if evidence_subdir else EVIDENCE
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}:{env.get('PATH', '')}"
    env["UV_CACHE_DIR"] = "/tmp/pf-uv-cache"
    started = datetime.now(timezone.utc)
    argv = [str(PF), command]
    log_path = evidence / f"{command}.txt"
    meta_path = evidence / f"{command}.meta.json"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            argv,
            cwd=TARGET,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finished = datetime.now(timezone.utc)
    meta = {
        "command": argv,
        "cwd": str(TARGET),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_s": (finished - started).total_seconds(),
        "exit_code": proc.returncode,
        "log": str(log_path.relative_to(ROOT)),
        "evidence": str(evidence.relative_to(ROOT)),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return proc.returncode


if __name__ == "__main__":
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: run_pf.py smoke|check|search|apply [evidence-subdir]")
    raise SystemExit(run(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None))

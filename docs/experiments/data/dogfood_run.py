"""Record one PF command for a dogfood experiment."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path("/home/llh/pf")
PF = ROOT / ".venv" / "bin" / "pf"


def run(
    *,
    command: str,
    target: Path,
    evidence: Path,
    log_name: str | None = None,
) -> int:
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{ROOT / '.venv' / 'bin'}:{env.get('PATH', '')}"
    env["UV_CACHE_DIR"] = "/tmp/pf-uv-cache"
    started = datetime.now(timezone.utc)
    argv = [str(PF), command]
    stem = log_name or command
    log_path = evidence / f"{stem}.txt"
    meta_path = evidence / f"{stem}.meta.json"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            argv,
            cwd=target,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finished = datetime.now(timezone.utc)
    meta = {
        "command": argv,
        "cwd": str(target),
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
    if len(sys.argv) not in {4, 5}:
        raise SystemExit(
            "usage: dogfood_run.py smoke|check|search|apply TARGET EVIDENCE [log-name]"
        )
    raise SystemExit(
        run(
            command=sys.argv[1],
            target=Path(sys.argv[2]),
            evidence=Path(sys.argv[3]),
            log_name=sys.argv[4] if len(sys.argv) == 5 else None,
        )
    )

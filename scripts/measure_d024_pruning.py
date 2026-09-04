from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


SUITE = """\
import time

def test_one():
    time.sleep({delay})
    assert False

def test_two():
    time.sleep({delay})
    assert False

def test_three():
    time.sleep({delay})
    assert False

def test_four():
    time.sleep({delay})
    assert False

def test_ok():
    pass
"""


def _write_suite(root: Path, delay: float) -> None:
    (root / "test_suite.py").write_text(
        SUITE.format(delay=delay),
        encoding="utf-8",
    )


def _time(command: tuple[str, ...], cwd: Path) -> float:
    started = time.perf_counter()
    subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return time.perf_counter() - started


def _configured(root: Path, nodeids: tuple[str, ...] = ()):
    from pf.adapters.process import SubprocessRunner
    from pf.adapters.test_command import ConfiguredVerifier
    from pf.schemas.evaluation import EnvironmentVariable, VerifierRequest

    started = time.perf_counter()
    run = ConfiguredVerifier(SubprocessRunner()).run(
        VerifierRequest(
            command=(sys.executable, "-m", "pytest", "--noconftest", "-q"),
            cwd=root,
            environment=(
                EnvironmentVariable(name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"),
            ),
            timeout_seconds=60,
            failed_case_nodeids=nodeids,
        )
    )
    return time.perf_counter() - started, type(run.authoritative).__name__


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    samples: dict[str, list[float]] = {
        "raw_pytest": [],
        "configured_original": [],
        "failed_set_hit": [],
        "failed_set_then_original": [],
    }
    dispositions: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="pf-d024-measure-") as raw:
        root = Path(raw)
        _write_suite(root, args.delay)
        raw_command = (
            sys.executable,
            "-m",
            "pytest",
            "--noconftest",
            "-q",
            "-p",
            "no:cacheprovider",
        )
        overlay_command = (
            sys.executable,
            "-m",
            "pytest",
            "--noconftest",
            "-q",
            "--maxfail=1",
            "-p",
            "no:cacheprovider",
        )
        for _ in range(args.repeat):
            samples["raw_pytest"].append(_time(raw_command, root))
            duration, disposition = _configured(root)
            samples["configured_original"].append(duration)
            dispositions["configured_original"] = disposition
            duration, disposition = _configured(
                root,
                nodeids=("test_suite.py::test_one",),
            )
            samples["failed_set_hit"].append(duration)
            dispositions["failed_set_hit"] = disposition
            duration, disposition = _configured(
                root,
                nodeids=("test_suite.py::test_ok",),
            )
            samples["failed_set_then_original"].append(duration)
            dispositions["failed_set_then_original"] = disposition
        overlay_samples = [_time(overlay_command, root) for _ in range(args.repeat)]
    document = {
        "delay_seconds": args.delay,
        "repeat": args.repeat,
        "dispositions": dispositions,
        "median_seconds": {
            name: statistics.median(values) for name, values in samples.items()
        },
        "median_raw_with_maxfail": statistics.median(overlay_samples),
        "samples": samples,
    }
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

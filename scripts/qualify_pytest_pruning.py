from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import TypedDict


PYTHON_MINORS = ("3.10", "3.11", "3.12")
PYTEST_VERSIONS = ("6.2.5", "7.0.1", "7.4.4", "8.0.2", "8.4.2", "9.0.2", "9.1.1")
CURRENT_PLUGIN_REQUIREMENTS = (
    "pytest==9.1.1",
    "pytest-cov==7.1.0",
    "pytest-env==1.7.0",
    "pytest-testmon==2.2.0",
    "pytest-benchmark==5.2.3",
    "pytest-xdist==3.8.0",
)


class CaseResult(TypedDict):
    case: str
    expected: bool
    detail: str


def _write(root: Path, name: str, source: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _run(root: Path, *args: str, nodeids: tuple[str, ...] = ()):
    from pf.adapters.process import SubprocessRunner
    from pf.adapters.test_command import ConfiguredVerifier
    from pf.schemas.evaluation import EnvironmentVariable, VerifierRequest

    return ConfiguredVerifier(SubprocessRunner()).run(
        VerifierRequest(
            command=(sys.executable, "-m", "pytest", "--no-header", "-q", *args),
            cwd=root,
            environment=(
                EnvironmentVariable(name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"),
            ),
            timeout_seconds=60,
            failed_case_nodeids=nodeids,
        )
    )


def _case_selection_applied(root: Path) -> CaseResult:
    from pf.schemas.evaluation import VerifierRejected

    _write(
        root,
        "test_a_bad.py",
        "from pathlib import Path\n"
        "def test_bad():\n"
        "    Path('bad-ran').write_text('yes')\n"
        "    assert False\n",
    )
    _write(
        root,
        "test_b_ok.py",
        "from pathlib import Path\n"
        "def test_ok():\n"
        "    Path('ok-ran').write_text('yes')\n",
    )
    run = _run(root, nodeids=("test_b_ok.py::test_ok",))
    expected = (
        isinstance(run.authoritative, VerifierRejected)
        and (root / "ok-ran").exists()
        and (root / "bad-ran").exists()
        and run.failed_case_additions == ("test_a_bad.py::test_bad",)
    )
    return {
        "case": "selection-applied-then-original",
        "expected": expected,
        "detail": type(run.authoritative).__name__,
    }


def _case_early_exit(root: Path) -> CaseResult:
    from pf.schemas.evaluation import VerifierRejected

    _write(
        root,
        "test_example.py",
        "from pathlib import Path\n"
        "def test_bad():\n    assert False\n"
        "def test_ok():\n    Path('original-ran').write_text('yes')\n",
    )
    run = _run(root, nodeids=("test_example.py::test_bad",))
    expected = (
        isinstance(run.authoritative, VerifierRejected)
        and not (root / "original-ran").exists()
        and run.failed_case_additions == ()
    )
    return {
        "case": "failed-set-early-exit",
        "expected": expected,
        "detail": type(run.authoritative).__name__,
    }


def _case_literal_dashdash(root: Path) -> CaseResult:
    from pf.schemas.evaluation import VerifierRejected

    _write(root, "test_example.py", "def test_bad():\n    assert False\n")
    run = _run(
        root,
        "--",
        "-k",
        "not overlay",
        nodeids=("test_example.py::test_bad",),
    )
    expected = (
        isinstance(run.authoritative, VerifierRejected)
        and run.failed_case_additions == ()
    )
    return {
        "case": "literal-double-dash",
        "expected": expected,
        "detail": type(run.authoritative).__name__,
    }


def _case_initial_conftest(root: Path) -> CaseResult:
    from pf.schemas.evaluation import VerifierRejected

    _write(root, "conftest.py", "raise ImportError('initial conftest')\n")
    _write(root, "test_example.py", "def test_ok():\n    pass\n")
    run = _run(root)
    expected = isinstance(run.authoritative, VerifierRejected)
    return {
        "case": "initial-conftest-original",
        "expected": expected,
        "detail": type(run.authoritative).__name__,
    }


def _case_dynamic_collection(root: Path) -> CaseResult:
    from pf.schemas.evaluation import VerifierRejected

    _write(
        root,
        "conftest.py",
        "def pytest_generate_tests(metafunc):\n"
        "    if metafunc.definition.name == 'test_bad':\n"
        "        metafunc.parametrize('value', (1, 2))\n",
    )
    _write(
        root,
        "test_example.py",
        "from pathlib import Path\n"
        "def test_bad(value):\n    assert False\n"
        "def test_ok():\n    Path('original-ran').write_text('yes')\n",
    )
    run = _run(root, nodeids=("test_example.py::test_bad",))
    expected = (
        isinstance(run.authoritative, VerifierRejected)
        and bool(run.failed_case_additions)
    )
    return {
        "case": "dynamic-parametrization-fallback",
        "expected": expected,
        "detail": type(run.authoritative).__name__,
    }


def _case_xdist_load(root: Path) -> CaseResult:
    from pf.schemas.evaluation import VerifierRejected

    try:
        import xdist
    except ImportError:
        return {
            "case": "xdist-dist-load",
            "expected": True,
            "detail": "skipped-no-xdist",
        }
    del xdist
    _write(
        root,
        "test_example.py",
        "from pathlib import Path\n"
        "def test_bad():\n    assert False\n"
        "def test_ok():\n    Path('original-ran').write_text('yes')\n",
    )
    from pf.adapters.process import SubprocessRunner
    from pf.adapters.test_command import ConfiguredVerifier
    from pf.schemas.evaluation import EnvironmentVariable, VerifierRequest

    run = ConfiguredVerifier(SubprocessRunner()).run(
        VerifierRequest(
            command=(
                sys.executable,
                "-m",
                "pytest",
                "--no-header",
                "-q",
                "-p",
                "xdist.plugin",
                "-n",
                "2",
                "--dist",
                "load",
            ),
            cwd=root,
            environment=(
                EnvironmentVariable(
                    name="PYTEST_DISABLE_PLUGIN_AUTOLOAD",
                    value="1",
                ),
            ),
            timeout_seconds=60,
            failed_case_nodeids=("test_example.py::test_bad",),
        )
    )
    expected = (
        isinstance(run.authoritative, VerifierRejected)
        and run.failed_case_additions == ("test_example.py::test_bad",)
    )
    return {
        "case": "xdist-dist-load",
        "expected": expected,
        "detail": type(run.authoritative).__name__,
    }


CASES = (
    _case_selection_applied,
    _case_early_exit,
    _case_literal_dashdash,
    _case_initial_conftest,
    _case_dynamic_collection,
    _case_xdist_load,
)


def _inner() -> dict[str, object]:
    import pytest

    results: list[CaseResult] = []
    for case in CASES:
        with tempfile.TemporaryDirectory(prefix="pf-pruning-qualify-") as raw:
            results.append(case(Path(raw)))
    return {
        "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "pytest_version": pytest.__version__,
        "cases": results,
        "expected": all(item["expected"] for item in results),
    }


def _run_profile(
    *,
    uv_binary: str,
    script: Path,
    python_minor: str,
    requirements: tuple[str, ...],
    project_root: Path,
) -> dict[str, object]:
    command = [
        uv_binary,
        "--no-config",
        "run",
        "--isolated",
        "--no-project",
        "--python",
        python_minor,
        "--with-editable",
        project_root.as_posix(),
    ]
    for requirement in requirements:
        command.extend(("--with", requirement))
    command.extend(("python", script.as_posix(), "--inner"))
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    process = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if process.returncode != 0:
        return {
            "python_minor": python_minor,
            "requirements": list(requirements),
            "expected": False,
            "runner_error": process.stderr[-2000:],
            "cases": [],
        }
    result = json.loads(process.stdout)
    return {
        "python_minor": result["python_minor"],
        "pytest_version": result["pytest_version"],
        "requirements": list(requirements),
        "expected": result["expected"] is True,
        "cases": result["cases"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inner", action="store_true")
    parser.add_argument("--python-minor", action="append")
    parser.add_argument("--pytest-version", action="append")
    parser.add_argument("--current-plugins", action="store_true")
    parser.add_argument("--uv-bin", default=shutil.which("uv"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.inner:
        print(json.dumps(_inner(), sort_keys=True))
        return 0
    if args.uv_bin is None:
        parser.error("uv is required")
    project_root = Path(__file__).resolve().parents[1]
    python_minors = tuple(args.python_minor or PYTHON_MINORS)
    versions = tuple(args.pytest_version or PYTEST_VERSIONS)
    script = Path(__file__).resolve()
    profiles = [
        _run_profile(
            uv_binary=args.uv_bin,
            script=script,
            python_minor=minor,
            requirements=(f"pytest=={version}",),
            project_root=project_root,
        )
        for minor in python_minors
        for version in versions
    ]
    if args.current_plugins:
        profiles.extend(
            _run_profile(
                uv_binary=args.uv_bin,
                script=script,
                python_minor=minor,
                requirements=CURRENT_PLUGIN_REQUIREMENTS,
                project_root=project_root,
            )
            for minor in python_minors
        )
    manifest = {
        "schema": "pf-pytest-pruning-qualification-v1",
        "as_of": date.today().isoformat(),
        "cases": [case.__name__.removeprefix("_case_") for case in CASES],
        "profiles": profiles,
        "all_profiles_expected": all(
            profile.get("expected") is True for profile in profiles
        ),
    }
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if manifest["all_profiles_expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

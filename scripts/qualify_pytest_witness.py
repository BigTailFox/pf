from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import platform
from typing import cast, Literal, TypedDict


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
PROTOCOL = "pf-pytest-failure-witness-v1"


@dataclass(frozen=True)
class QualificationCase:
    name: str
    files: tuple[tuple[str, str], ...]
    args: tuple[str, ...]
    expected_exit: int
    expected_facts: tuple[tuple[str, str], ...]
    summary: Literal["present", "absent", "either"] = "present"


CASES = (
    QualificationCase(
        "pass",
        (("test_example.py", "def test_ok():\n    pass\n"),),
        (),
        0,
        (),
    ),
    QualificationCase(
        "assertion-failure",
        (("test_example.py", "def test_bad():\n    assert False\n"),),
        (),
        1,
        (("TEST_FAILED", "call"),),
    ),
    QualificationCase(
        "fixture-setup-failure",
        (
            (
                "test_example.py",
                "import pytest\n"
                "@pytest.fixture\n"
                "def broken():\n"
                "    raise RuntimeError('setup')\n"
                "def test_bad(broken):\n"
                "    pass\n",
            ),
        ),
        (),
        1,
        (("TEST_FAILED", "setup"),),
    ),
    QualificationCase(
        "fixture-teardown-failure",
        (
            (
                "test_example.py",
                "import pytest\n"
                "@pytest.fixture\n"
                "def broken():\n"
                "    yield\n"
                "    raise RuntimeError('teardown')\n"
                "def test_bad(broken):\n"
                "    pass\n",
            ),
        ),
        (),
        1,
        (("TEST_FAILED", "teardown"),),
    ),
    QualificationCase(
        "module-collection-failure",
        (("test_example.py", "raise ImportError('collection')\n"),),
        (),
        2,
        (("COLLECTION_FAILED", "collect"),),
    ),
    QualificationCase(
        "nested-conftest-failure",
        (
            ("test_example.py", "def test_root():\n    pass\n"),
            ("nested/conftest.py", "raise ImportError('nested conftest')\n"),
            ("nested/test_nested.py", "def test_nested():\n    pass\n"),
        ),
        (),
        2,
        (("COLLECTION_FAILED", "collect"),),
    ),
    QualificationCase(
        "initial-conftest-failure",
        (
            ("conftest.py", "raise ImportError('initial conftest')\n"),
            ("test_example.py", "def test_ok():\n    pass\n"),
        ),
        (),
        4,
        (),
        "absent",
    ),
    QualificationCase(
        "internal-error",
        (
            (
                "conftest.py",
                "def pytest_collection_modifyitems(items):\n"
                "    raise RuntimeError('internal')\n",
            ),
            ("test_example.py", "def test_ok():\n    pass\n"),
        ),
        (),
        3,
        (("INTERNAL_ERROR", "pytest"),),
    ),
    QualificationCase(
        "keyboard-interrupt",
        (
            (
                "test_example.py",
                "def test_interrupt():\n    raise KeyboardInterrupt\n",
            ),
        ),
        (),
        2,
        (),
        "either",
    ),
    QualificationCase(
        "early-plugin-import-failure",
        (),
        ("-p", "pf_qualification_missing_plugin"),
        1,
        (),
        "absent",
    ),
    QualificationCase(
        "no-tests-collected",
        (),
        (),
        5,
        (),
    ),
    QualificationCase(
        "invalid-option",
        (),
        ("--pf-qualification-invalid-option",),
        4,
        (),
        "absent",
    ),
)


class CaseResult(TypedDict):
    case: str
    exit_code: int
    execution_modes: list[str]
    facts: list[list[str]]
    summary_count: int
    canonical: bool
    expected: bool


class InnerResult(TypedDict):
    python_minor: str
    pytest_version: str
    cases: list[CaseResult]


def _canonical(document: object) -> bytes:
    return (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")


def _run_case(
    case: QualificationCase,
    *,
    plugin_source: Path,
    autoload: bool,
    python_minor: str,
    pytest_version: str,
) -> CaseResult:
    with tempfile.TemporaryDirectory(prefix="pf-pytest-qualification-") as root_value:
        root = Path(root_value)
        project = root / "project"
        plugin_directory = root / "plugin"
        evidence = root / "evidence"
        project.mkdir()
        plugin_directory.mkdir()
        evidence.mkdir()
        for relative, content in case.files:
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        nonce = secrets.token_hex(16)
        module = f"_pf_pytest_witness_{nonce}"
        shutil.copyfile(plugin_source, plugin_directory / f"{module}.py")
        environment = os.environ.copy()
        pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            plugin_directory.as_posix()
            if not pythonpath
            else plugin_directory.as_posix() + os.pathsep + pythonpath
        )
        environment["PF_PYTEST_WITNESS_DIR"] = evidence.as_posix()
        environment["PF_PYTEST_WITNESS_NONCE"] = nonce
        if autoload:
            environment.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
        else:
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        process = subprocess.run(
            (
                sys.executable,
                "-m",
                "pytest",
                "-p",
                module,
                "--no-header",
                "-q",
                *case.args,
            ),
            cwd=project,
            env=environment,
            capture_output=True,
            timeout=30,
            check=False,
        )
        summaries: list[dict[str, object]] = []
        canonical = True
        for path in sorted(evidence.iterdir()):
            try:
                payload = path.read_bytes()
                document = json.loads(payload.decode("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                canonical = False
                continue
            if type(document) is not dict or payload != _canonical(document):
                canonical = False
                continue
            summaries.append(document)
        observed_facts: set[tuple[str, str]] = set()
        for summary in summaries:
            facts_document = summary.get("facts")
            if not isinstance(facts_document, list):
                canonical = False
                continue
            for fact in facts_document:
                if isinstance(fact, dict) and "kind" in fact and "phase" in fact:
                    fact_document = cast(dict[str, object], fact)
                    observed_facts.add(
                        (str(fact_document["kind"]), str(fact_document["phase"]))
                    )
        facts = sorted(observed_facts)
        modes = sorted({str(summary.get("execution_mode")) for summary in summaries})
        identities_valid = all(
            summary.get("protocol") == PROTOCOL
            and summary.get("run_nonce") == nonce
            and summary.get("finalized") is True
            and summary.get("python_implementation") == "cpython"
            and summary.get("python_minor") == python_minor
            and summary.get("pytest_version") == pytest_version
            for summary in summaries
        )
        summary_expected = (
            bool(summaries)
            if case.summary == "present"
            else not summaries
            if case.summary == "absent"
            else True
        )
        expected = (
            process.returncode == case.expected_exit
            and tuple(facts) == case.expected_facts
            and summary_expected
            and canonical
            and identities_valid
            and (not summaries or modes == ["serial"])
        )
        return {
            "case": case.name,
            "exit_code": process.returncode,
            "execution_modes": modes,
            "facts": [list(fact) for fact in facts],
            "summary_count": len(summaries),
            "canonical": canonical,
            "expected": expected,
        }


def _inner(
    plugin_source: Path,
    *,
    autoload: bool,
    cases: tuple[QualificationCase, ...] = CASES,
) -> InnerResult:
    import pytest

    python_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    results = [
        _run_case(
            case,
            plugin_source=plugin_source,
            autoload=autoload,
            python_minor=python_minor,
            pytest_version=pytest.__version__,
        )
        for case in cases
    ]
    return {
        "python_minor": python_minor,
        "pytest_version": pytest.__version__,
        "cases": results,
    }


def _run_profile(
    *,
    uv_binary: str,
    script: Path,
    plugin_source: Path,
    python_minor: str,
    requirements: tuple[str, ...],
    autoload: bool,
    cases: tuple[QualificationCase, ...],
) -> dict[str, object]:
    command = [
        uv_binary,
        "--no-config",
        "run",
        "--isolated",
        "--no-project",
        "--python",
        python_minor,
    ]
    for requirement in requirements:
        command.extend(("--with", requirement))
    command.extend(
        (
            "python",
            script.as_posix(),
            "--inner",
            "--plugin-source",
            plugin_source.as_posix(),
        )
    )
    if autoload:
        command.append("--autoload")
    for case in cases:
        command.extend(("--case", case.name))
    process = subprocess.run(
        command,
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        return {
            "python_minor": python_minor,
            "requirements": list(requirements),
            "qualified": False,
            "runner_error": process.stderr[-2000:],
            "cases": [],
        }
    result = json.loads(process.stdout)
    return {
        "python_minor": result["python_minor"],
        "pytest_version": result["pytest_version"],
        "requirements": list(requirements),
        "qualified": all(item["expected"] for item in result["cases"]),
        "cases": result["cases"],
    }


def _compact_manifest(manifest: dict[str, object]) -> dict[str, object]:
    profiles_document = manifest.get("profiles")
    if not isinstance(profiles_document, list):
        raise ValueError("qualification manifest profiles must be a list")
    profiles: list[dict[str, object]] = []
    for item in profiles_document:
        if not isinstance(item, dict):
            raise ValueError("qualification profile must be an object")
        profile = cast(dict[str, object], item)
        cases = profile.get("cases")
        requirements = profile.get("requirements")
        if not isinstance(cases, list) or not isinstance(requirements, list):
            raise ValueError("qualification profile cases/requirements are invalid")
        profiles.append(
            {
                "case_count": len(cases),
                "current_plugins": len(requirements) > 1,
                "pytest_version": profile.get("pytest_version"),
                "python_minor": profile.get("python_minor"),
                "qualified": profile.get("qualified") is True,
                "requirements": requirements,
                "results_sha256": hashlib.sha256(_canonical(cases)).hexdigest(),
            }
        )
    return {
        "schema": manifest.get("schema"),
        "as_of": date.today().isoformat(),
        "host_scope": f"{platform.system().lower()}-{platform.machine()}",
        "protocol": manifest.get("protocol"),
        "case_contracts": [
            {
                "name": case.name,
                "expected_exit": case.expected_exit,
                "expected_facts": [list(fact) for fact in case.expected_facts],
                "summary": case.summary,
            }
            for case in CASES
        ],
        "execution_count": manifest.get("execution_count"),
        "all_profiles_qualified": all(
            profile["qualified"] is True for profile in profiles
        ),
        "profiles": profiles,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inner", action="store_true")
    parser.add_argument("--autoload", action="store_true")
    parser.add_argument("--plugin-source", type=Path)
    parser.add_argument("--python-minor", action="append")
    parser.add_argument("--pytest-version", action="append")
    parser.add_argument("--current-plugins", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--uv-bin", default=shutil.which("uv"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    args = parser.parse_args()
    if args.list_cases:
        print(json.dumps([case.name for case in CASES]))
        return 0
    if args.summarize is not None:
        raw = json.loads(args.summarize.read_text(encoding="utf-8"))
        payload = json.dumps(_compact_manifest(raw), indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        return 0
    plugin_source = args.plugin_source or (
        Path(__file__).resolve().parents[1] / "src/pf/_pytest_failure_witness.py"
    )
    selected = frozenset(args.case)
    unknown = sorted(selected - {case.name for case in CASES})
    if unknown:
        parser.error(f"unknown qualification case: {unknown[0]}")
    cases = tuple(case for case in CASES if not selected or case.name in selected)
    if args.inner:
        print(
            json.dumps(
                _inner(plugin_source, autoload=args.autoload, cases=cases),
                sort_keys=True,
            )
        )
        return 0
    if args.uv_bin is None:
        parser.error("uv is required")
    python_minors = tuple(args.python_minor or PYTHON_MINORS)
    versions = tuple(args.pytest_version or PYTEST_VERSIONS)
    script = Path(__file__).resolve()
    profiles = [
        _run_profile(
            uv_binary=args.uv_bin,
            script=script,
            plugin_source=plugin_source.resolve(),
            python_minor=minor,
            requirements=(f"pytest=={version}",),
            autoload=False,
            cases=cases,
        )
        for minor in python_minors
        for version in versions
    ]
    if args.current_plugins:
        profiles.extend(
            _run_profile(
                uv_binary=args.uv_bin,
                script=script,
                plugin_source=plugin_source.resolve(),
                python_minor=minor,
                requirements=CURRENT_PLUGIN_REQUIREMENTS,
                autoload=True,
                cases=cases,
            )
            for minor in python_minors
        )
    manifest = {
        "schema": "pf-pytest-witness-qualification-v1",
        "protocol": PROTOCOL,
        "cases": [case.name for case in cases],
        "execution_count": sum(
            len(cases)
            for profile in profiles
            if isinstance((cases := profile.get("cases")), list)
        ),
        "profiles": profiles,
    }
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if all(profile.get("qualified") is True for profile in profiles) else 1


if __name__ == "__main__":
    raise SystemExit(main())

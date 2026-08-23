from __future__ import annotations

import argparse
from dataclasses import dataclass
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from threading import Thread
import time
from typing import Literal, TypedDict
from zipfile import ZIP_DEFLATED, ZipFile

from packaging.utils import canonicalize_name

from pf.adapters.uv_diagnostics import (
    classify_resolution_diagnostic,
    diagnostic_digest,
)
from pf.resolution import (
    UV_DIAGNOSTIC_PROFILES,
    UV_PROTOCOL_IDENTITY,
    UV_SUPPORTED_VERSIONS,
)
from pf.schemas.evaluation import ProcessResult


@dataclass(frozen=True)
class ExpectedClassification:
    kind: Literal["unsat", "indeterminate"]
    proof_code: str | None = None
    cause: str | None = None
    summary_code: str | None = None


@dataclass(frozen=True)
class QualificationCase:
    name: str
    requirement: str
    mode: Literal["no-index", "find-links", "index", "offline"]
    expected: ExpectedClassification
    index_route: str | None = None


class QualificationRecord(TypedDict):
    case: str
    command: list[str]
    exit_code: int
    stdout_complete: bool
    stderr_complete: bool
    structured_output_available: bool
    diagnostic_digest: str
    diagnostic_signature: str
    pf_classification: dict[str, str | None]
    classifier_confidence: str
    expected: bool
    stderr: str


class QualificationResult(TypedDict):
    uv_version: str
    protocol_identity: str
    diagnostic_profile: str
    matrix: list[QualificationRecord]


CASES = (
    QualificationCase(
        name="pure-version-contradiction",
        requirement="pf-qualification-demo==1\npf-qualification-demo==2\n",
        mode="no-index",
        expected=ExpectedClassification(
            kind="unsat",
            proof_code="direct-version-contradiction",
        ),
    ),
    QualificationCase(
        name="transitive-version-contradiction",
        requirement="pf-qualification-a==1\npf-qualification-b==1\n",
        mode="find-links",
        expected=ExpectedClassification(
            kind="unsat",
            proof_code="transitive-version-contradiction",
        ),
    ),
    QualificationCase(
        name="package-unavailable",
        requirement="pf-qualification-missing==1\n",
        mode="no-index",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="TOOL_FAILURE",
            summary_code="resolution-candidate-unavailable",
        ),
    ),
    QualificationCase(
        name="package-version-unavailable",
        requirement="pf-qualification-version==2\n",
        mode="find-links",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="TOOL_FAILURE",
            summary_code="resolution-candidate-unavailable",
        ),
    ),
    QualificationCase(
        name="platform-wheel-unavailable",
        requirement="pf-qualification-platform==1\n",
        mode="find-links",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="TOOL_FAILURE",
            summary_code="resolution-candidate-unavailable",
        ),
    ),
    QualificationCase(
        name="requires-python-mismatch",
        requirement="pf-qualification-python==1\n",
        mode="find-links",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="TOOL_FAILURE",
            summary_code="resolution-candidate-unavailable",
        ),
    ),
    QualificationCase(
        name="index-401",
        requirement="pf-qualification-index==1\n",
        mode="index",
        index_route="unauthorized",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="SOURCE_FAILURE",
            summary_code="resolution-source-failure",
        ),
    ),
    QualificationCase(
        name="index-403",
        requirement="pf-qualification-index==1\n",
        mode="index",
        index_route="forbidden",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="SOURCE_FAILURE",
            summary_code="resolution-source-failure",
        ),
    ),
    QualificationCase(
        name="index-timeout",
        requirement="pf-qualification-index==1\n",
        mode="index",
        index_route="timeout",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="SOURCE_FAILURE",
            summary_code="resolution-source-failure",
        ),
    ),
    QualificationCase(
        name="metadata-failure",
        requirement="pf-qualification-metadata==1\n",
        mode="find-links",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="SOURCE_FAILURE",
            summary_code="resolution-source-failure",
        ),
    ),
    QualificationCase(
        name="hash-mismatch",
        requirement="__HASH_REQUIREMENT__",
        mode="find-links",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="SOURCE_FAILURE",
            summary_code="resolution-source-failure",
        ),
    ),
    QualificationCase(
        name="sdist-build-failure",
        requirement="pf-qualification-build==1\n",
        mode="find-links",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="BUILD_FAILURE",
            summary_code="resolution-build-failure",
        ),
    ),
    QualificationCase(
        name="offline-cache-miss",
        requirement=(
            "pf-qualification-offline @ "
            "https://example.invalid/pf_qualification_offline-1-py3-none-any.whl\n"
        ),
        mode="offline",
        expected=ExpectedClassification(
            kind="indeterminate",
            cause="SOURCE_FAILURE",
            summary_code="resolution-source-failure",
        ),
    ),
)


class _QualificationIndex(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if "/unauthorized/" in self.path:
            self.send_error(401, "Unauthorized")
        elif "/forbidden/" in self.path:
            self.send_error(403, "Forbidden")
        elif "/timeout/" in self.path:
            time.sleep(3)
            self.send_error(404, "Not Found")
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format: str, *args: object) -> None:
        return


def _metadata(
    *,
    name: str,
    version: str,
    dependencies: tuple[str, ...],
    requires_python: str | None,
) -> bytes:
    message = Message()
    message["Metadata-Version"] = "2.4"
    message["Name"] = name
    message["Version"] = version
    if requires_python is not None:
        message["Requires-Python"] = requires_python
    for dependency in dependencies:
        message["Requires-Dist"] = dependency
    return message.as_bytes()


def _write_wheel(
    root: Path,
    *,
    name: str,
    version: str,
    dependencies: tuple[str, ...] = (),
    requires_python: str | None = None,
    tag: str = "py3-none-any",
    broken_metadata: bool = False,
) -> Path:
    normalized = canonicalize_name(name).replace("-", "_")
    filename = f"{normalized}-{version}-{tag}.whl"
    wheel = root / filename
    dist_info = f"{normalized}-{version}.dist-info"
    metadata = (
        b"Metadata-Version: 2.4\nVersion: 1\n"
        if broken_metadata
        else _metadata(
            name=name,
            version=version,
            dependencies=dependencies,
            requires_python=requires_python,
        )
    )
    wheel_text = (
        "Wheel-Version: 1.0\n"
        "Generator: pf-uv-qualification\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {tag}\n"
    ).encode()
    with ZipFile(wheel, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(f"{dist_info}/WHEEL", wheel_text)
        archive.writestr(f"{dist_info}/RECORD", b"")
    return wheel


def _write_failing_sdist(root: Path) -> Path:
    target = root / "pf_qualification_build-1.tar.gz"
    pyproject = (
        b"[build-system]\nrequires = []\n"
        b"build-backend = 'pf_qualification_missing_backend'\n"
    )
    with tarfile.open(target, "w:gz") as archive:
        info = tarfile.TarInfo("pf_qualification_build-1/pyproject.toml")
        info.size = len(pyproject)
        archive.addfile(info, BytesIO(pyproject))
    return target


def _prepare_fixtures(root: Path) -> dict[str, Path]:
    links = root / "links"
    links.mkdir()
    _write_wheel(links, name="pf-qualification-shared", version="1")
    _write_wheel(links, name="pf-qualification-shared", version="2")
    _write_wheel(links, name="pf-qualification-version", version="1")
    _write_wheel(
        links,
        name="pf-qualification-a",
        version="1",
        dependencies=("pf-qualification-shared==1",),
    )
    _write_wheel(
        links,
        name="pf-qualification-b",
        version="1",
        dependencies=("pf-qualification-shared==2",),
    )
    _write_wheel(
        links,
        name="pf-qualification-platform",
        version="1",
        tag="py3-none-win_amd64",
    )
    _write_wheel(
        links,
        name="pf-qualification-python",
        version="1",
        requires_python=">=99",
    )
    _write_wheel(
        links,
        name="pf-qualification-metadata",
        version="1",
        broken_metadata=True,
    )
    hash_wheel = _write_wheel(
        links,
        name="pf-qualification-hash",
        version="1",
    )
    _write_failing_sdist(links)
    return {"links": links, "hash_wheel": hash_wheel}


def _uv_version(uv_binary: Path) -> str:
    result = subprocess.run(
        (uv_binary.as_posix(), "--version"),
        check=True,
        capture_output=True,
        text=True,
    )
    fields = result.stdout.strip().split()
    if len(fields) < 2:
        raise RuntimeError("uv returned an invalid version string")
    return fields[1]


def _run_case(
    case: QualificationCase,
    *,
    root: Path,
    fixtures: dict[str, Path],
    index_url: str,
    uv_version: str,
    uv_binary: Path,
) -> QualificationRecord:
    case_root = root / case.name
    case_root.mkdir()
    requirement = case.requirement
    if requirement == "__HASH_REQUIREMENT__":
        locator = fixtures["hash_wheel"].resolve().as_uri()
        requirement = (
            f"pf-qualification-hash @ {locator}#sha256={'0' * 64}\n"
        )
    input_path = case_root / "requirements.in"
    input_path.write_text(requirement, encoding="utf-8")
    output_path = case_root / f"pylock.{case.name}.toml"
    cache = root / "cache"
    command = [
        uv_binary.as_posix(),
        "pip",
        "compile",
        input_path.as_posix(),
        "--format",
        "pylock.toml",
        "--output-file",
        output_path.as_posix(),
        "--python-version",
        "3.11",
        "--python-platform",
        "x86_64-unknown-linux-gnu",
        "--exclude-newer",
        "2026-08-23T00:00:00Z",
        "--cache-dir",
        cache.as_posix(),
        "--no-progress",
        "--color",
        "never",
    ]
    if case.mode == "no-index":
        command.append("--no-index")
    elif case.mode == "find-links":
        command.extend(("--no-index", "--find-links", fixtures["links"].as_posix()))
    elif case.mode == "index":
        assert case.index_route is not None
        command.extend(
            (
                "--default-index",
                f"{index_url}/{case.index_route}/simple",
            )
        )
    else:
        command.append("--offline")
    environment = os.environ.copy()
    if case.name == "index-timeout":
        environment.update({"UV_HTTP_TIMEOUT": "1", "UV_HTTP_RETRIES": "0"})
    completed = subprocess.run(
        command,
        cwd=case_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=environment,
    )
    if case.name == "hash-mismatch" and completed.returncode == 0:
        content = output_path.read_text(encoding="utf-8")
        tampered, replacements = re.subn(
            r'sha256 = "[0-9a-f]{64}"',
            f'sha256 = "{"0" * 64}"',
            content,
        )
        if replacements != 1:
            raise RuntimeError("hash qualification lock did not contain one SHA-256")
        output_path.write_text(tampered, encoding="utf-8")
        environment = case_root / "environment"
        subprocess.run(
            (
                uv_binary.as_posix(),
                "venv",
                "--python",
                "3.11",
                "--no-project",
                "--cache-dir",
                cache.as_posix(),
                environment.as_posix(),
            ),
            cwd=case_root,
            capture_output=True,
            text=True,
            check=True,
        )
        interpreter = environment / "bin" / "python"
        command = [
            uv_binary.as_posix(),
            "pip",
            "sync",
            output_path.as_posix(),
            "--python",
            interpreter.as_posix(),
            "--cache-dir",
            cache.as_posix(),
            "--no-progress",
            "--color",
            "never",
        ]
        completed = subprocess.run(
            command,
            cwd=case_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    process = ProcessResult(
        exit_code=completed.returncode,
        signal=None,
        duration_seconds=0.0,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    classification = classify_resolution_diagnostic(
        uv_version=uv_version,
        process=process,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    actual = ExpectedClassification(
        kind=classification.kind,
        proof_code=classification.proof_code,
        cause=classification.cause,
        summary_code=classification.summary_code,
    )
    return {
        "case": case.name,
        "command": command,
        "exit_code": completed.returncode,
        "stdout_complete": True,
        "stderr_complete": True,
        "structured_output_available": output_path.exists(),
        "diagnostic_digest": diagnostic_digest(
            completed.stdout,
            completed.stderr,
        ),
        "diagnostic_signature": classification.signature,
        "pf_classification": {
            "kind": classification.kind,
            "proof_code": classification.proof_code,
            "cause": classification.cause,
            "summary_code": classification.summary_code,
        },
        "classifier_confidence": (
            "certified" if classification.kind == "unsat" else "conservative"
        ),
        "expected": case.expected == actual,
        "stderr": completed.stderr,
    }


def qualify(
    selected: frozenset[str],
    *,
    uv_binary: Path,
    expected_version: str | None,
) -> QualificationResult:
    resolved_binary = _resolve_uv_binary(uv_binary)
    uv_version = _uv_version(resolved_binary)
    if uv_version not in UV_SUPPORTED_VERSIONS:
        raise RuntimeError(f"qualification does not support uv {uv_version}")
    if expected_version is not None and uv_version != expected_version:
        raise RuntimeError(
            f"expected uv {expected_version}, found {uv_version}"
        )
    with tempfile.TemporaryDirectory(prefix="pf-uv-qualification-") as directory:
        root = Path(directory)
        fixtures = _prepare_fixtures(root)
        chosen = tuple(
            case for case in CASES if not selected or case.name in selected
        )
        if any(case.mode == "index" for case in chosen):
            server = ThreadingHTTPServer(("127.0.0.1", 0), _QualificationIndex)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                index_url = f"http://127.0.0.1:{server.server_port}"
                results = [
                    _run_case(
                        case,
                        root=root,
                        fixtures=fixtures,
                        index_url=index_url,
                        uv_version=uv_version,
                        uv_binary=resolved_binary,
                    )
                    for case in chosen
                ]
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        else:
            results = [
                _run_case(
                    case,
                    root=root,
                    fixtures=fixtures,
                    index_url="",
                    uv_version=uv_version,
                    uv_binary=resolved_binary,
                )
                for case in chosen
            ]
    unknown = sorted(selected - {case.name for case in CASES})
    if unknown:
        raise ValueError(f"unknown qualification case: {unknown[0]}")
    return {
        "uv_version": uv_version,
        "protocol_identity": UV_PROTOCOL_IDENTITY,
        "diagnostic_profile": UV_DIAGNOSTIC_PROFILES[uv_version],
        "matrix": results,
    }


def _resolve_uv_binary(value: Path) -> Path:
    if value.is_absolute() or value.parent != Path("."):
        resolved = value.resolve()
        if not resolved.is_file():
            raise RuntimeError(f"uv binary does not exist: {resolved}")
        return resolved
    discovered = shutil.which(value.as_posix())
    if discovered is None:
        raise RuntimeError(f"uv binary was not found: {value}")
    return Path(discovered).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qualify one exact uv version against PF resolution diagnostics."
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--uv-bin", type=Path, default=Path("uv"))
    parser.add_argument("--expected-version")
    parser.add_argument("--list-cases", action="store_true")
    arguments = parser.parse_args()
    if arguments.list_cases:
        print(json.dumps([case.name for case in CASES]))
        return
    result = qualify(
        frozenset(arguments.case),
        uv_binary=arguments.uv_bin,
        expected_version=arguments.expected_version,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.write_text(rendered, encoding="utf-8")
    failed = [item["case"] for item in result["matrix"] if not item["expected"]]
    if failed:
        raise SystemExit("qualification mismatch: " + ", ".join(failed))


if __name__ == "__main__":
    main()

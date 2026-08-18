from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json

import pytest

from pf.adapters.uv import UvAdapter
from pf.schemas.evaluation import GraphSuccess, ProcessResult, ProcessSpec
from pf.schemas.project import Cell, SourceIdentity


class RecordingRunner:
    def __init__(self) -> None:
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        return ProcessResult(
            exit_code=0,
            signal=None,
            duration_seconds=0.1,
            stdout_summary="installed\n",
            stderr_summary="",
            stdout_tail="installed\n",
            stderr_tail="",
        )


def test_uv_adapter_owns_lowest_direct_editable_install_argv(tmp_path: Path) -> None:
    runner = RecordingRunner()
    adapter = UvAdapter(runner)
    interpreter = tmp_path / ".venv" / "bin" / "python"
    package = tmp_path / "packages" / "demo"

    result = adapter.install_editable(
        interpreter=interpreter,
        package=package,
        extra_surface=("cuda", "arrow"),
        resolution="lowest-direct",
        timeout_seconds=600,
    )

    assert result.status == "SUCCESS"
    assert runner.specs[0].argv == (
        "uv",
        "pip",
        "install",
        "--python",
        interpreter.as_posix(),
        "--resolution",
        "lowest-direct",
        "--editable",
        f"{package.as_posix()}[arrow,cuda]",
    )
    assert runner.specs[0].cwd == package.as_posix()


def test_uv_adapter_inspects_a_canonical_installed_graph(tmp_path: Path) -> None:
    class GraphRunner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0.1,
                stdout_summary=(
                    '[{"name":"Requests","version":"2.32.5",'
                    '"requires":["urllib3>=1.21", "Certifi"]},'
                    '{"name":"certifi","version":"2026.1.1","requires":[]}]\n'
                ),
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
            )

    graph = UvAdapter(GraphRunner()).inspect_environment(
        interpreter=tmp_path / ".venv" / "bin" / "python",
        cwd=tmp_path,
        timeout_seconds=30,
    )

    assert isinstance(graph, GraphSuccess)
    assert graph.status == "SUCCESS"
    assert [(node.name, node.version, node.dependencies) for node in graph.nodes] == [
        ("certifi", "2026.1.1", ()),
        ("requests", "2.32.5", ("certifi", "urllib3")),
    ]


def test_candidate_query_accepts_arm64_wheel_for_aarch64_darwin_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = json.dumps(
        {
            "files": [
                {
                    "filename": "demo-1.0-cp310-cp310-macosx_11_0_arm64.whl",
                    "url": "files/demo.whl",
                    "hashes": {"sha256": "abc"},
                    "yanked": False,
                }
            ]
        }
    ).encode()

    class Response(BytesIO):
        headers = {"Content-Length": str(len(document))}

    monkeypatch.setattr(
        "pf.adapters.uv.urlopen",
        lambda request, timeout: Response(document),
    )
    candidates = UvAdapter(RecordingRunner()).query(
        dependency="demo",
        source=SourceIdentity(
            kind="registry",
            locator="https://index.example/simple",
        ),
        cell=Cell(
            package="demo",
            target="aarch64-apple-darwin",
            python_minor="3.10",
            extra_surface=(),
        ),
    )

    assert [candidate.version for candidate in candidates] == ["1.0"]


def test_uv_adapter_lists_only_default_stable_cpython_minors(tmp_path: Path) -> None:
    class PythonRunner:
        def __init__(self) -> None:
            self.spec: ProcessSpec | None = None

        def run(self, spec: ProcessSpec) -> ProcessResult:
            self.spec = spec
            return ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0.1,
                stdout_summary=json.dumps(
                    [
                        {"version": "3.12.4", "implementation": "cpython", "variant": "default"},
                        {"version": "3.11.9", "implementation": "cpython", "variant": "default"},
                        {"version": "3.13.0rc1", "implementation": "cpython", "variant": "default"},
                        {"version": "3.12.4", "implementation": "cpython", "variant": "freethreaded"},
                        {"version": "3.10.14", "implementation": "pypy", "variant": "default"},
                    ]
                ),
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
            )

    runner = PythonRunner()
    minors = UvAdapter(runner).available_cpython_minors(root=tmp_path)

    assert minors == ("3.11", "3.12")
    assert runner.spec is not None
    assert runner.spec.argv == (
        "uv",
        "python",
        "list",
        "--output-format",
        "json",
    )

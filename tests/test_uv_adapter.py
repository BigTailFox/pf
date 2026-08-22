from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
from urllib.error import URLError

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.uv import UvAdapter
from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    GraphSuccess,
    InterpreterSuccess,
    ProcessResult,
    ProcessSpec,
    ToolFailure,
)
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
            stdout="installed\n",
            stderr="",
        )


def process_result(
    *,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    truncated: bool = False,
    timed_out: bool = False,
) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None if exit_code is not None else 9,
        duration_seconds=0.1,
        stdout=stdout,
        stderr=stderr,
        stdout_complete=not truncated,
        timed_out=timed_out,
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
                stdout=(
                    '[{"name":"Requests","version":"2.32.5",'
                    '"requires":["urllib3>=1.21", "Certifi"]},'
                    '{"name":"certifi","version":"2026.1.1","requires":[]}]\n'
                ),
                stderr="",
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
                stdout=json.dumps(
                    [
                        {
                            "version": "3.12.4",
                            "implementation": "cpython",
                            "variant": "default",
                        },
                        {
                            "version": "3.11.9",
                            "implementation": "cpython",
                            "variant": "default",
                        },
                        {
                            "version": "3.13.0rc1",
                            "implementation": "cpython",
                            "variant": "default",
                        },
                        {
                            "version": "3.12.4",
                            "implementation": "cpython",
                            "variant": "freethreaded",
                        },
                        {
                            "version": "3.10.14",
                            "implementation": "pypy",
                            "variant": "default",
                        },
                    ]
                ),
                stderr="",
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
    assert runner.spec.summary_limit is None


def test_uv_adapter_lists_real_cpython_inventory_beyond_default_summary(
    tmp_path: Path,
) -> None:
    minors = UvAdapter(SubprocessRunner()).available_cpython_minors(root=tmp_path)

    assert minors
    assert all(minor.startswith("3.") for minor in minors)


def test_uv_adapter_owns_environment_and_harness_install_argv(tmp_path: Path) -> None:
    runner = RecordingRunner()
    adapter = UvAdapter(runner)
    interpreter = tmp_path / ".venv" / "bin" / "python"
    constraints = tmp_path / "constraints.txt"

    created = adapter.create_environment(
        environment=tmp_path / ".venv",
        python_minor="3.11",
        cwd=tmp_path,
        timeout_seconds=30,
    )
    installed = adapter.install_requirements(
        interpreter=interpreter,
        requirements=("pytest", "coverage"),
        constraints=constraints,
        cwd=tmp_path,
        timeout_seconds=60,
    )

    assert created.status == installed.status == "SUCCESS"
    assert runner.specs[0].argv[:5] == (
        "uv",
        "venv",
        "--python",
        "3.11",
        "--no-project",
    )
    assert runner.specs[1].argv == (
        "uv",
        "pip",
        "install",
        "--python",
        interpreter.as_posix(),
        "--constraint",
        constraints.as_posix(),
        "pytest",
        "coverage",
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (process_result(exit_code=None, timed_out=True), "TIMEOUT"),
        (process_result(exit_code=1, stderr="failed to build wheel"), "BUILD_FAILURE"),
        (
            process_result(exit_code=1, stderr="No solution found"),
            "RESOLUTION_CONFLICT",
        ),
        (
            process_result(exit_code=1, stderr="failed to download: DNS error"),
            "SOURCE_FAILURE",
        ),
        (process_result(exit_code=1, stderr="unexpected"), "TOOL_FAILURE"),
    ),
)
def test_uv_adapter_classifies_operation_causes(
    tmp_path: Path,
    result: ProcessResult,
    expected: str,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return result

    outcome = UvAdapter(Runner()).create_environment(
        environment=tmp_path / ".venv",
        python_minor="3.10",
        cwd=tmp_path,
        timeout_seconds=None,
    )

    assert isinstance(outcome, ToolFailure)
    assert outcome.cause == expected


@pytest.mark.parametrize(
    "result",
    (
        process_result(exit_code=1),
        process_result(stdout="[]", truncated=True),
        process_result(stdout="not-json"),
        process_result(stdout="[]"),
        process_result(
            stdout=json.dumps(
                [
                    {
                        "version": "not-a-version",
                        "implementation": "cpython",
                        "variant": "default",
                    }
                ]
            )
        ),
    ),
)
def test_uv_python_inventory_rejects_unusable_evidence(
    tmp_path: Path,
    result: ProcessResult,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return result

    with pytest.raises(InfrastructureError):
        UvAdapter(Runner()).available_cpython_minors(root=tmp_path)


def test_uv_python_inventory_failure_includes_process_diagnostic(
    tmp_path: Path,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return process_result(exit_code=1, stderr="uv: python list failed")

    with pytest.raises(InfrastructureError) as caught:
        UvAdapter(Runner()).available_cpython_minors(root=tmp_path)

    assert str(caught.value) == "uv could not list available Python versions"
    assert caught.value.detail == "uv: python list failed"


def test_uv_adapter_inspects_interpreter_identity(tmp_path: Path) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return process_result(
                stdout=json.dumps(
                    {
                        "implementation": "cpython",
                        "version": "3.10.18",
                        "abi": "cpython-310",
                    }
                )
            )

    outcome = UvAdapter(Runner()).inspect_interpreter(
        interpreter=tmp_path / "python",
        cwd=tmp_path,
        timeout_seconds=10,
    )

    assert isinstance(outcome, InterpreterSuccess)
    assert outcome.status == "SUCCESS"
    assert outcome.interpreter.version == "3.10.18"


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        (process_result(exit_code=None, timed_out=True), "TIMEOUT"),
        (process_result(stdout="{}", truncated=True), "TOOL_FAILURE"),
        (process_result(stdout="not-json"), "TOOL_FAILURE"),
    ),
)
def test_uv_adapter_rejects_unusable_interpreter_evidence(
    tmp_path: Path,
    result: ProcessResult,
    expected: str,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return result

    outcome = UvAdapter(Runner()).inspect_interpreter(
        interpreter=tmp_path / "python",
        cwd=tmp_path,
        timeout_seconds=10,
    )

    assert isinstance(outcome, ToolFailure)
    assert outcome.cause == expected


@pytest.mark.parametrize(
    "result",
    (
        process_result(exit_code=1),
        process_result(stdout="[]", truncated=True),
        process_result(stdout="not-json"),
        process_result(stdout='[{"name":"demo","version":"bad","requires":[]}]'),
    ),
)
def test_uv_graph_inspection_rejects_unusable_evidence(
    tmp_path: Path,
    result: ProcessResult,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return result

    outcome = UvAdapter(Runner()).inspect_environment(
        interpreter=tmp_path / "python",
        cwd=tmp_path,
        timeout_seconds=10,
    )

    assert isinstance(outcome, ToolFailure)
    assert outcome.cause == "TOOL_FAILURE"


def test_uv_graph_inspection_ignores_invalid_dependency_metadata(
    tmp_path: Path,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return process_result(
                stdout='[{"name":"demo","version":"1.0","requires":["not [valid"]}]'
            )

    outcome = UvAdapter(Runner()).inspect_environment(
        interpreter=tmp_path / "python",
        cwd=tmp_path,
        timeout_seconds=10,
    )

    assert isinstance(outcome, GraphSuccess)
    assert outcome.nodes[0].dependencies == ()


def test_candidate_query_filters_files_and_preserves_yanked_sdists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = json.dumps(
        {
            "files": [
                {"filename": 42, "url": "bad", "hashes": {"sha256": "x"}},
                {"filename": "demo-0.1.tar.gz", "url": "no-hash", "hashes": {}},
                {
                    "filename": "demo-0.2.tar.gz",
                    "url": "bad-python",
                    "hashes": {"sha256": "a"},
                    "requires-python": "not valid",
                },
                {
                    "filename": "demo-0.3.tar.gz",
                    "url": "new-python",
                    "hashes": {"sha256": "b"},
                    "requires-python": ">=3.12",
                },
                {
                    "filename": "not-an-artifact.txt",
                    "url": "invalid",
                    "hashes": {"sha256": "c"},
                },
                {
                    "filename": "demo-1.0.tar.gz",
                    "url": "demo-1.0.tar.gz",
                    "hashes": {"sha256": "d"},
                    "yanked": True,
                },
                {
                    "filename": "demo-2.0-py3-none-any.whl",
                    "url": "demo-2.0.whl",
                    "hashes": {"sha256": "e"},
                    "yanked": False,
                },
            ]
        }
    ).encode()

    class Response(BytesIO):
        headers = {}

    monkeypatch.setattr(
        "pf.adapters.uv.urlopen", lambda request, timeout: Response(document)
    )
    candidates = UvAdapter(RecordingRunner()).query(
        dependency="demo",
        source=SourceIdentity(kind="registry", locator="https://index.example/simple"),
        cell=Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        ),
    )

    assert [(candidate.version, candidate.yanked) for candidate in candidates] == [
        ("1.0", True),
        ("2.0", False),
    ]


def test_candidate_query_rejects_non_registry_and_invalid_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = UvAdapter(RecordingRunner())
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )

    with pytest.raises(InfrastructureError):
        adapter.query(
            dependency="demo",
            source=SourceIdentity(kind="workspace", locator="packages/demo"),
            cell=cell,
        )

    monkeypatch.setattr(
        "pf.adapters.uv.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(InfrastructureError) as caught:
        adapter.query(
            dependency="demo",
            source=SourceIdentity(kind="registry", locator=None),
            cell=cell,
        )
    assert str(caught.value) == "registry candidate query failed for: demo"
    assert caught.value.detail == "<urlopen error offline>"

    class InvalidResponse(BytesIO):
        headers = {}

    monkeypatch.setattr(
        "pf.adapters.uv.urlopen",
        lambda request, timeout: InvalidResponse(b"not-json"),
    )
    with pytest.raises(InfrastructureError):
        adapter.query(
            dependency="demo",
            source=SourceIdentity(kind="registry", locator=None),
            cell=cell,
        )


def test_candidate_query_rejects_a_declared_oversized_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response(BytesIO):
        headers = {"Content-Length": str(16 * 1024 * 1024 + 1)}

    monkeypatch.setattr(
        "pf.adapters.uv.urlopen", lambda request, timeout: Response(b"{}")
    )

    with pytest.raises(InfrastructureError):
        UvAdapter(RecordingRunner()).query(
            dependency="demo",
            source=SourceIdentity(kind="registry", locator=None),
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
        )

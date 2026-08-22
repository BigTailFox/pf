from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
from urllib.error import URLError

import pytest

from pf.adapters.process import SecretRedactor, SubprocessRunner
from pf.adapters.uv import RegistryAccess, UvAdapter
from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    GraphSuccess,
    InterpreterSuccess,
    ProcessResult,
    ProcessSpec,
    ToolFailure,
)
from pf.schemas.project import (
    AvailableArtifact,
    Cell,
    SelectedCandidate,
    SourceIdentity,
)


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


class TestUvAdapter:
    def test_uv_adapter_owns_lowest_direct_editable_install_argv(
        self, tmp_path: Path
    ) -> None:
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

    def test_uv_adapter_installs_the_selected_artifact_with_its_sha256(
        self,
        tmp_path: Path,
    ) -> None:
        runner = RecordingRunner()
        adapter = UvAdapter(runner)
        interpreter = tmp_path / ".venv" / "bin" / "python"
        package = tmp_path / "demo"
        digest = "a" * 64

        result = adapter.install_editable(
            interpreter=interpreter,
            package=package,
            extra_surface=(),
            resolution="highest",
            timeout_seconds=600,
            selection=(
                SelectedCandidate(
                    dependency="idna",
                    version="3.1",
                    artifact=AvailableArtifact(
                        filename="idna-3.1-py3-none-any.whl",
                        kind="wheel",
                        content_hash=f"sha256:{digest}",
                        locator="https://files.example/idna-3.1-py3-none-any.whl",
                    ),
                ),
            ),
        )

        assert result.status == "SUCCESS"
        assert runner.specs[0].argv[-1] == (
            f"idna @ https://files.example/idna-3.1-py3-none-any.whl#sha256={digest}"
        )

    def test_uv_adapter_inspects_a_canonical_installed_graph(
        self, tmp_path: Path
    ) -> None:
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
        assert [
            (node.name, node.version, node.dependencies) for node in graph.nodes
        ] == [
            ("certifi", "2026.1.1", ()),
            ("requests", "2.32.5", ("certifi", "urllib3")),
        ]

    def test_uv_adapter_owns_environment_and_harness_install_argv(
        self, tmp_path: Path
    ) -> None:
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
            (
                process_result(exit_code=1, stderr="failed to build wheel"),
                "BUILD_FAILURE",
            ),
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
        self,
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

    def test_uv_adapter_inspects_interpreter_identity(self, tmp_path: Path) -> None:
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
        self,
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
        self,
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
        self,
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


class TestCandidateQuery:
    def test_candidate_query_accepts_arm64_wheel_for_aarch64_darwin_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": "demo-1.0-cp310-cp310-macosx_11_0_arm64.whl",
                        "url": "files/demo.whl",
                        "hashes": {"sha256": "a" * 64},
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

    def test_candidate_query_uses_process_local_registry_authentication(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "demo-1.0.tar.gz",
                        "hashes": {"sha256": "a" * 64},
                    }
                ]
            }
        ).encode()
        observed_authorization: list[str | None] = []

        class Response(BytesIO):
            headers = {"Content-Length": str(len(document))}

        def open_request(request: object, timeout: int) -> Response:
            assert timeout == 30
            observed_authorization.append(request.get_header("Authorization"))
            return Response(document)

        monkeypatch.setattr("pf.adapters.uv.urlopen", open_request)
        access = RegistryAccess.basic(
            index="private", username="alice", password="s3cret"
        )
        redactor = SecretRedactor(access.secret_literals)

        candidates = UvAdapter(
            RecordingRunner(),
            registry_access=access,
            redactor=redactor,
        ).query(
            dependency="demo",
            source=SourceIdentity(
                kind="registry",
                index="private",
                locator="https://index.example/simple",
            ),
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
        )

        assert [candidate.version for candidate in candidates] == ["1.0"]
        assert observed_authorization == ["Basic YWxpY2U6czNjcmV0"]
        assert redactor.redact("s3cret") == "***"
        assert not hasattr(access, "model_dump")

    def test_candidate_query_filters_incompatible_files_and_preserves_yanked_sdists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": "demo-0.3.tar.gz",
                        "url": "new-python",
                        "hashes": {"sha256": "b" * 64},
                        "requires-python": ">=3.12",
                    },
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "demo-1.0.tar.gz",
                        "hashes": {"sha256": "d" * 64},
                        "yanked": True,
                    },
                    {
                        "filename": "demo-2.0-py3-none-any.whl",
                        "url": "demo-2.0.whl",
                        "hashes": {"sha256": "e" * 64},
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
            source=SourceIdentity(
                kind="registry", locator="https://index.example/simple"
            ),
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

    @pytest.mark.parametrize(
        "document",
        [
            [],
            {},
            {"files": {}},
            {"files": [None]},
            {
                "files": [
                    {"filename": 42, "url": "demo.whl", "hashes": {"sha256": "a" * 64}}
                ]
            },
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": 42,
                        "hashes": {"sha256": "a" * 64},
                    }
                ]
            },
            {
                "files": [
                    {"filename": "demo-1.0.tar.gz", "url": "demo.tar.gz", "hashes": []}
                ]
            },
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "demo.tar.gz",
                        "hashes": {"sha256": 42},
                    }
                ]
            },
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "demo.tar.gz",
                        "hashes": {"sha256": "not-a-sha256"},
                    }
                ]
            },
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "demo.tar.gz",
                        "hashes": {"sha256": "a" * 64},
                        "requires-python": 42,
                    }
                ]
            },
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "demo.tar.gz",
                        "hashes": {"sha256": "a" * 64},
                        "requires-python": "not valid",
                    }
                ]
            },
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "demo.tar.gz",
                        "hashes": {"sha256": "a" * 64},
                        "yanked": 42,
                    }
                ]
            },
            {
                "files": [
                    {
                        "filename": "not-an-artifact.txt",
                        "url": "artifact",
                        "hashes": {"sha256": "a" * 64},
                    }
                ]
            },
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "https://[invalid",
                        "hashes": {"sha256": "a" * 64},
                    }
                ]
            },
        ],
    )
    def test_candidate_query_rejects_malformed_simple_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        document: object,
    ) -> None:
        payload = json.dumps(document).encode()

        class Response(BytesIO):
            headers = {"Content-Length": str(len(payload))}

        monkeypatch.setattr(
            "pf.adapters.uv.urlopen", lambda request, timeout: Response(payload)
        )

        with pytest.raises(InfrastructureError, match="invalid Simple JSON"):
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

    @pytest.mark.parametrize("length", ["-1", "+1", "1.5", "not-a-number"])
    def test_candidate_query_rejects_invalid_content_length(
        self,
        monkeypatch: pytest.MonkeyPatch,
        length: str,
    ) -> None:
        class Response(BytesIO):
            headers = {"Content-Length": length}

        monkeypatch.setattr(
            "pf.adapters.uv.urlopen",
            lambda request, timeout: Response(b'{"files": []}'),
        )

        with pytest.raises(InfrastructureError, match="invalid Simple JSON"):
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

    def test_query_rejects_non_registry_source(self) -> None:
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

    def test_query_maps_transport_error_to_infrastructure_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "pf.adapters.uv.urlopen",
            lambda request, timeout: (_ for _ in ()).throw(URLError("offline")),
        )
        with pytest.raises(InfrastructureError) as caught:
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

        assert caught.value.category == "infrastructure"
        assert caught.value.detail is not None
        assert "offline" in caught.value.detail

    def test_query_rejects_invalid_json_document(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class InvalidResponse(BytesIO):
            headers = {}

        monkeypatch.setattr(
            "pf.adapters.uv.urlopen",
            lambda request, timeout: InvalidResponse(b"not-json"),
        )
        with pytest.raises(InfrastructureError, match="invalid Simple JSON"):
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

    def test_candidate_query_rejects_a_declared_oversized_response(
        self,
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

    def test_candidate_query_stores_public_locators_and_redacts_error_detail(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "https://user:p4ssw0rd@files.example/demo-1.0.tar.gz?token=abc",
                        "hashes": {"sha256": "d" * 64},
                    }
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
            source=SourceIdentity(
                kind="registry",
                locator="https://user:p4ssw0rd@index.example/simple?token=abc",
            ),
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
        )

        locator = candidates[0].artifacts[0].locator
        assert locator == "https://files.example/demo-1.0.tar.gz"
        assert "p4ssw0rd" not in locator
        assert "token=" not in locator

        monkeypatch.setattr(
            "pf.adapters.uv.urlopen",
            lambda request, timeout: (_ for _ in ()).throw(
                URLError("https://user:p4ssw0rd@index.example/simple failed")
            ),
        )
        with pytest.raises(InfrastructureError) as caught:
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
        assert caught.value.detail is not None
        assert "p4ssw0rd" not in caught.value.detail
        assert "***" in caught.value.detail


class TestPythonInventory:
    def test_uv_adapter_lists_only_default_stable_cpython_minors(
        self, tmp_path: Path
    ) -> None:
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
        self,
        tmp_path: Path,
    ) -> None:
        minors = UvAdapter(SubprocessRunner()).available_cpython_minors(root=tmp_path)

        assert minors
        assert all(minor.startswith("3.") for minor in minors)

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
        self,
        tmp_path: Path,
        result: ProcessResult,
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return result

        with pytest.raises(InfrastructureError):
            UvAdapter(Runner()).available_cpython_minors(root=tmp_path)

    def test_uv_python_inventory_failure_includes_process_diagnostic(
        self,
        tmp_path: Path,
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return process_result(exit_code=1, stderr="uv: python list failed")

        with pytest.raises(InfrastructureError) as caught:
            UvAdapter(Runner()).available_cpython_minors(root=tmp_path)

        assert str(caught.value) == "uv could not list available Python versions"
        assert caught.value.detail == "uv: python list failed"

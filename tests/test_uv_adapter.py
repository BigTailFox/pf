from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
from typing import Literal
from urllib.error import URLError
from urllib.request import Request

import pytest

from pf.adapters.process import SecretRedactor, SubprocessRunner
from pf.adapters.uv import RegistryAccess, UvAdapter
from pf.environment import HighestResolution, LowestDirectResolution
from pf.errors import InfrastructureError
from pf.harness import original_harness, relax_harness
from pf.project import ProjectLoader
from pf.resolution import (
    ResolutionContext,
    ResolutionIndeterminate,
    ResolutionPlan,
    ResolutionRunContext,
    ResolutionUnsat,
)
from pf.schemas.evaluation import (
    GraphSuccess,
    InterpreterSuccess,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
    ToolFailure,
)
from pf.schemas.project import (
    Cell,
    DependencySourceRoute,
    HarnessBaseline,
    HarnessSelection,
    SourceIdentity,
    SourcePlan,
    StaticWorkspaceMemberVersion,
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


class TestRegistryAccess:
    def test_environment_credentials_support_default_and_named_indexes(self) -> None:
        access = RegistryAccess.from_environment(
            {
                "UV_INDEX_USERNAME": "default-user",
                "UV_INDEX_PASSWORD": "default-password",
                "UV_INDEX_PRIVATE_USERNAME": "private-user",
                "UV_INDEX_PRIVATE_PASSWORD": "private-password",
                "UNRELATED": "ignored",
            }
        )

        assert access.authorization(SourceIdentity(kind="registry")) is not None
        assert (
            access.authorization(SourceIdentity(kind="registry", index="private"))
            is not None
        )
        assert set(access.secret_literals) == {
            "default-password",
            "private-password",
            "default-user",
            "private-user",
        }

    def test_authorization_rejects_incomplete_credentials(self) -> None:
        access = RegistryAccess.from_environment({"UV_INDEX_USERNAME": "user"})

        with pytest.raises(InfrastructureError, match="credentials are incomplete"):
            access.authorization(SourceIdentity(kind="registry"))


class TestUvAdapter:
    @pytest.mark.parametrize(
        ("policy", "flag"),
        (("wheel", "--only-binary"), ("sdist", "--no-binary"), ("any", None)),
    )
    def test_resolution_maps_shared_artifact_policy_to_uv_admission(
        self,
        tmp_path: Path,
        policy: Literal["wheel", "sdist", "any"],
        flag: str | None,
    ) -> None:
        class Runner:
            def __init__(self) -> None:
                self.specs: list[ProcessSpec] = []

            def run(self, spec: ProcessSpec) -> ProcessResult:
                self.specs.append(spec)
                return process_result(exit_code=1, stderr="resolution failed")

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\n',
            encoding="utf-8",
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        context = ResolutionContext.from_inputs(
            run=ResolutionRunContext(
                uv_version="0.12.5",
                release_cutoff="2026-08-28T00:00:00+00:00",
            ),
            cell=cell,
            source_plan_identity="source-plan",
            uv_project_configuration_identity="uv-config",
        )
        runner = Runner()

        UvAdapter(runner).resolve_project(
            package=tmp_path,
            package_name="demo",
            cell=cell,
            resolution=HighestResolution(),
            context=context,
            request_digest="request",
            work_directory=tmp_path,
            artifact_policy=policy,
            timeout_seconds=30,
            source_plan=SourcePlan(source_mode="DEVELOPMENT", routes=()),
        )

        argv = runner.specs[0].argv
        if flag is None:
            assert "--only-binary" not in argv
            assert "--no-binary" not in argv
        else:
            assert argv[argv.index(flag) + 1] == ":all:"

    def test_default_executable_comes_from_the_uv_runtime_dependency(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "pf.adapters.uv.find_uv_bin",
            lambda: "/runtime-dependency/bin/uv",
            raising=False,
        )
        monkeypatch.setattr(
            "pf.adapters.uv.shutil.which",
            lambda _executable: "/global/bin/uv",
        )

        runner = RecordingRunner()
        UvAdapter(runner).resolution_run_context(root=tmp_path, timeout_seconds=30)

        assert runner.specs[0].argv == (
            "/runtime-dependency/bin/uv",
            "--version",
        )

    def test_resolution_ignores_user_level_uv_configuration(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        user_config = tmp_path / "user-config"
        uv_config = user_config / "uv" / "uv.toml"
        uv_config.parent.mkdir(parents=True)
        uv_config.write_text("this is not valid toml = [\n", encoding="utf-8")
        monkeypatch.setenv("XDG_CONFIG_HOME", user_config.as_posix())
        monkeypatch.setenv("UV_CONFIG_FILE", uv_config.as_posix())

        work_directory = tmp_path / "work"
        package = work_directory / "source"
        package.mkdir(parents=True)
        dependency = package / "vendor" / "tool"
        dependency.mkdir(parents=True)
        (dependency / "pyproject.toml").write_text(
            '[project]\nname = "tool"\nversion = "1.0"\n',
            encoding="utf-8",
        )
        (package / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["tool"]

[tool.uv.sources]
tool = { path = "vendor/tool" }
""".strip()
            + "\n",
            encoding="utf-8",
        )
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        )
        context = ResolutionContext.from_inputs(
            run=ResolutionRunContext(
                uv_version="0.12.5",
                release_cutoff="2026-08-28T00:00:00+00:00",
            ),
            cell=cell,
            source_plan_identity="source-plan",
            uv_project_configuration_identity="uv-config",
        )

        outcome = UvAdapter(SubprocessRunner()).resolve_project(
            package=package,
            package_name="demo",
            cell=cell,
            resolution=HighestResolution(),
            context=context,
            request_digest="request",
            work_directory=work_directory,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=SourcePlan(
                source_mode="DEVELOPMENT",
                routes=(
                    DependencySourceRoute(
                        dependency="tool",
                        development_source=SourceIdentity(
                            kind="path", locator="vendor/tool"
                        ),
                        search_source=SourceIdentity(
                            kind="path", locator="vendor/tool"
                        ),
                    ),
                ),
            ),
        )

        assert isinstance(outcome, ResolutionPlan)
        assert [item.name for item in outcome.packages] == ["tool"]
        assert outcome.packages[0].source.kind == "path"

    def test_uv_adapter_resolves_two_pylocks_and_syncs_only_the_final_plan(
        self, tmp_path: Path
    ) -> None:
        project_lock = f"""
lock-version = "1.0"
created-by = "uv"
requires-python = ">=3.11"

[[packages]]
name = "demo"
directory = {{ path = "demo", editable = true }}

[[packages]]
name = "idna"
version = "3.10"
index = "https://pypi.org/simple"
wheels = [{{ name = "idna-3.10-py3-none-any.whl", url = "https://files.example/idna.whl", hashes = {{ sha256 = "{"a" * 64}" }} }}]
"""
        environment_lock = (
            project_lock
            + f"""

[[packages]]
name = "pytest"
version = "8.4.2"
index = "https://pypi.org/simple"
wheels = [{{ name = "pytest-8.4.2-py3-none-any.whl", url = "https://files.example/pytest.whl", hashes = {{ sha256 = "{"b" * 64}" }} }}]
"""
        )

        class ResolutionRunner:
            def __init__(self) -> None:
                self.specs: list[ProcessSpec] = []
                self.compiles = 0

            def run(self, spec: ProcessSpec) -> ProcessResult:
                self.specs.append(spec)
                if spec.argv[1:] == ("--version",):
                    return process_result(stdout="uv 0.12.5 (test)\n")
                if spec.argv[1:3] == ("pip", "compile"):
                    output = Path(spec.argv[spec.argv.index("--output-file") + 1])
                    output.write_text(
                        project_lock if self.compiles == 0 else environment_lock,
                        encoding="utf-8",
                    )
                    self.compiles += 1
                return process_result()

        package_root = tmp_path / "demo"
        package_root.mkdir()
        (package_root / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]

[dependency-groups]
test = ["pytest>=8.0a1"]

[tool.pf]
pythons = ["3.11"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=package_root).target
        runner = ResolutionRunner()
        adapter = UvAdapter(runner)
        run = adapter.resolution_run_context(root=package_root, timeout_seconds=30)
        assert isinstance(run, ResolutionRunContext)
        assert (
            adapter.resolution_run_context(root=package_root, timeout_seconds=30) is run
        )
        context = ResolutionContext.from_inputs(
            run=run,
            cell=package.cells[0],
            source_plan_identity="source-plan",
            uv_project_configuration_identity="uv-config",
        )
        baseline = HarnessBaseline.from_evidence(
            cell=package.cells[0],
            declaration_ids=tuple(
                item.declaration_id for item in package.harness_requirements
            ),
            selections=(
                HarnessSelection(
                    name="pytest",
                    version="8.4.2",
                    source=SourceIdentity(kind="registry"),
                    ceiling_bound=True,
                ),
            ),
        )
        resolution = LowestDirectResolution(baseline)
        registry = SourceIdentity(kind="registry", locator="https://pypi.org/simple")
        search_source_plan = SourcePlan(
            source_mode="SEARCH",
            routes=(
                DependencySourceRoute(
                    dependency="idna",
                    development_source=SourceIdentity(
                        kind="workspace", locator="packages/idna"
                    ),
                    search_source=registry,
                    workspace_member_version=StaticWorkspaceMemberVersion(value="3.10"),
                ),
                DependencySourceRoute(
                    dependency="local-tool",
                    development_source=SourceIdentity(
                        kind="workspace", locator="packages/local-tool"
                    ),
                    search_source=SourceIdentity(
                        kind="workspace", locator="packages/local-tool"
                    ),
                    workspace_member_version=StaticWorkspaceMemberVersion(value="1.0"),
                ),
                next(
                    route
                    for route in package.source_routes
                    if route.dependency == "pytest"
                ),
                DependencySourceRoute(
                    dependency="urllib3",
                    development_source=SourceIdentity(
                        kind="workspace", locator="packages/urllib3"
                    ),
                    search_source=registry,
                    workspace_member_version=StaticWorkspaceMemberVersion(value="2.0"),
                ),
            ),
        )
        project = adapter.resolve_project(
            package=package_root,
            package_name=package.name,
            cell=package.cells[0],
            resolution=resolution,
            context=context,
            request_digest="project-request",
            work_directory=tmp_path,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=search_source_plan,
        )
        assert isinstance(project, ResolutionPlan)
        environment = adapter.resolve_environment(
            package=package_root,
            package_name=package.name,
            cell=package.cells[0],
            resolution=resolution,
            context=context,
            request_digest="environment-request",
            project_plan=project,
            harness=relax_harness(
                package,
                baseline,
                source_plan=search_source_plan,
            ).requirements,
            work_directory=tmp_path,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=search_source_plan,
        )
        assert isinstance(environment, ResolutionPlan)
        installed = adapter.install_resolution(
            plan=environment,
            interpreter=tmp_path / "venv" / "bin" / "python",
            cwd=package_root,
            work_directory=tmp_path,
            timeout_seconds=30,
        )

        assert installed.status == "INSTALLED"
        assert installed.plan_digest == environment.digest
        assert [item.name for item in project.packages] == ["idna"]
        assert [item.name for item in environment.direct_harness] == ["pytest"]
        assert (tmp_path / "project-constraints.in").read_text() == "idna==3.10\n"
        assert (
            (tmp_path / "environment-requirements.in")
            .read_text()
            .endswith("pytest<=8.4.2\n")
        )
        project_compile_argv = runner.specs[1].argv
        environment_compile_argv = runner.specs[2].argv
        assert Path(project_compile_argv[0]).is_absolute()
        assert project_compile_argv[1:3] == ("pip", "compile")
        assert project_compile_argv[
            project_compile_argv.index("--python-platform") + 1
        ] == ("x86_64-unknown-linux-gnu")
        assert (
            project_compile_argv[project_compile_argv.index("--resolution") + 1]
            == "lowest-direct"
        )
        assert (
            environment_compile_argv[environment_compile_argv.index("--resolution") + 1]
            == "highest"
        )
        assert runner.specs[-1].argv[1:3] == ("pip", "sync")
        assert "--prerelease" not in runner.specs[2].argv
        assert "--prerelease-package" not in runner.specs[2].argv
        assert not any(spec.argv[1:3] == ("pip", "install") for spec in runner.specs)
        for spec in runner.specs[1:3]:
            assert "--no-sources" not in spec.argv
            assert tuple(
                spec.argv[index + 1]
                for index, value in enumerate(spec.argv)
                if value == "--no-sources-package"
            ) == ("idna", "urllib3")
            assert "UV_NO_SOURCES" in spec.environment_removals
            assert "UV_NO_SOURCES_PACKAGE" in spec.environment_removals
            assert "UV_PRERELEASE" in spec.environment_removals
            assert "--only-binary" in spec.argv
            assert spec.argv[spec.argv.index("--only-binary") + 1] == ":all:"

        development = adapter.resolve_project(
            package=package_root,
            package_name=package.name,
            cell=package.cells[0],
            resolution=resolution,
            context=context,
            request_digest="development-project-request",
            work_directory=tmp_path,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=SourcePlan(
                source_mode="DEVELOPMENT",
                routes=search_source_plan.routes,
            ),
        )

        assert isinstance(development, ResolutionPlan)
        assert "--no-sources-package" not in runner.specs[4].argv

    def test_resolution_failure_uses_the_qualified_diagnostic_profile(
        self, tmp_path: Path
    ) -> None:
        stderr = (
            "× No solution found when resolving dependencies: "
            "Because you require demo==1 and demo==2, we can conclude that your "
            "requirements are unsatisfiable."
        )

        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return process_result(exit_code=1, stderr=stderr)

        context = ResolutionContext.from_inputs(
            run=ResolutionRunContext(
                uv_version="0.12.5",
                release_cutoff="2026-08-23T00:00:00+00:00",
            ),
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.11",
                extra_surface=(),
            ),
            source_plan_identity="source-plan",
            uv_project_configuration_identity="uv-config",
        )
        outcome = UvAdapter(Runner()).resolve_project(
            package=tmp_path,
            package_name="demo",
            cell=context.cell,
            resolution=HighestResolution(),
            context=context,
            request_digest="request",
            work_directory=tmp_path,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=SourcePlan(source_mode="SEARCH", routes=()),
        )

        assert isinstance(outcome, ResolutionUnsat)
        assert outcome.proof_code == "direct-version-contradiction"

    def test_environment_resolution_materializes_a_fixed_path_harness_source(
        self,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "source"
        dependency = source / "vendor" / "tool"
        dependency.mkdir(parents=True)
        (dependency / "pyproject.toml").write_text(
            '[project]\nname = "tool"\nversion = "1.0"\n',
            encoding="utf-8",
        )
        source.mkdir(exist_ok=True)
        (source / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = ["tool>=1"]

[tool.uv.sources]
tool = { path = "vendor/tool" }

[tool.pf]
pythons = ["3.11"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        project_lock = """\
lock-version = "1.0"
created-by = "uv"
packages = [{ name = "demo", directory = { path = "source", editable = true } }]
"""
        environment_lock = f'''\
lock-version = "1.0"
created-by = "uv"
packages = [
  {{ name = "demo", directory = {{ path = "source", editable = true }} }},
  {{ name = "tool", directory = {{ path = "{dependency.as_posix()}" }} }},
]
'''

        class Runner:
            def __init__(self) -> None:
                self.compiles = 0

            def run(self, spec: ProcessSpec) -> ProcessResult:
                if spec.argv[1:3] == ("pip", "compile"):
                    output = Path(spec.argv[spec.argv.index("--output-file") + 1])
                    output.write_text(
                        project_lock if self.compiles == 0 else environment_lock,
                        encoding="utf-8",
                    )
                    self.compiles += 1
                return process_result()

        package = ProjectLoader().load(root=source).target
        context = ResolutionContext.from_inputs(
            run=ResolutionRunContext(
                uv_version="0.12.5",
                release_cutoff="2026-08-23T00:00:00+00:00",
            ),
            cell=package.cells[0],
            source_plan_identity="source-plan",
            uv_project_configuration_identity="uv-config",
        )
        adapter = UvAdapter(Runner())
        project = adapter.resolve_project(
            package=source,
            package_name=package.name,
            cell=package.cells[0],
            resolution=HighestResolution(),
            context=context,
            request_digest="project-request",
            work_directory=tmp_path,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )
        assert isinstance(project, ResolutionPlan)
        environment = adapter.resolve_environment(
            package=source,
            package_name=package.name,
            cell=package.cells[0],
            resolution=HighestResolution(),
            context=context,
            request_digest="environment-request",
            project_plan=project,
            harness=original_harness(
                package,
                package.cells[0],
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            ),
            work_directory=tmp_path,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert isinstance(environment, ResolutionPlan)
        assert environment.direct_harness[0].source == SourceIdentity(
            kind="path",
            locator="vendor/tool",
        )
        assert dependency.as_posix() not in environment.native.content
        assert "source/vendor/tool" in environment.native.content
        assert (tmp_path / "environment-requirements.in").read_text() == (
            f"-e .\ntool>=1\ntool @ {dependency.as_uri()}\n"
        )

    def test_success_with_an_invalid_native_plan_is_indeterminate(
        self, tmp_path: Path
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                output = Path(spec.argv[spec.argv.index("--output-file") + 1])
                output.write_text("not a pylock", encoding="utf-8")
                return process_result()

        context = ResolutionContext.from_inputs(
            run=ResolutionRunContext(
                uv_version="0.12.5",
                release_cutoff="2026-08-23T00:00:00+00:00",
            ),
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.11",
                extra_surface=(),
            ),
            source_plan_identity="source-plan",
            uv_project_configuration_identity="uv-config",
        )
        outcome = UvAdapter(Runner()).resolve_project(
            package=tmp_path,
            package_name="demo",
            cell=context.cell,
            resolution=HighestResolution(),
            context=context,
            request_digest="request",
            work_directory=tmp_path,
            artifact_policy="wheel",
            timeout_seconds=30,
            source_plan=SourcePlan(source_mode="SEARCH", routes=()),
        )

        assert isinstance(outcome, ResolutionIndeterminate)
        assert outcome.summary_code == "resolution-plan-invalid"

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
                "TOOL_FAILURE",
            ),
            (
                process_result(exit_code=1, stderr="failed to download: DNS error"),
                "SOURCE_FAILURE",
            ),
            (
                process_result(
                    exit_code=1, stderr="Failed to read archive: Hash mismatch"
                ),
                "SOURCE_FAILURE",
            ),
            (
                process_result(
                    exit_code=1, stderr="Failed to read archive: file is empty"
                ),
                "SOURCE_FAILURE",
            ),
            (
                process_result(
                    exit_code=1,
                    stderr="Failed to read artifact: No such file or directory",
                ),
                "SOURCE_FAILURE",
            ),
            (
                process_result(
                    exit_code=1,
                    stderr="Failed to read archive: invalid package format",
                ),
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

    def test_uv_adapter_handles_unavailable_process_terminal(
        self,
        tmp_path: Path,
    ) -> None:
        class Runner:
            def run(self, spec: ProcessSpec) -> ProcessTerminalUnavailable:
                return ProcessTerminalUnavailable()

        outcome = UvAdapter(Runner()).create_environment(
            environment=tmp_path / ".venv",
            python_minor="3.10",
            cwd=tmp_path,
            timeout_seconds=None,
        )

        assert isinstance(outcome, ToolFailure)
        assert isinstance(outcome.process, ProcessTerminalUnavailable)

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
    @pytest.mark.parametrize(
        ("filename", "python_minor", "target"),
        (
            (
                "demo-1.0-cp39-abi3-manylinux_2_17_x86_64.whl",
                "3.10",
                "x86_64-unknown-linux-gnu",
            ),
            (
                "demo-1.0-py3-none-musllinux_1_2_x86_64.whl",
                "3.10",
                "x86_64-unknown-linux-musl",
            ),
            (
                "demo-1.0-py3-none-win_amd64.whl",
                "3.10",
                "x86_64-pc-windows-msvc",
            ),
        ),
        ids=("abi3", "musl", "windows"),
    )
    def test_candidate_query_accepts_a_compatible_wheel(
        self,
        monkeypatch: pytest.MonkeyPatch,
        filename: str,
        python_minor: str,
        target: str,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": filename,
                        "url": filename,
                        "hashes": {"sha256": "a" * 64},
                    }
                ]
            }
        ).encode()

        class Response(BytesIO):
            headers = {"Content-Length": str(len(document))}

        monkeypatch.setattr(
            "pf.adapters.uv.urlopen", lambda request, timeout: Response(document)
        )

        candidates = UvAdapter(RecordingRunner()).query(
            dependency="demo",
            source=SourceIdentity(kind="registry", locator=None),
            cell=Cell(
                package="demo",
                target=target,
                python_minor=python_minor,
                extra_surface=(),
            ),
        )

        assert candidates

    @pytest.mark.parametrize(
        "filename",
        (
            "demo-1.0-cp3x-abi3-manylinux_2_17_x86_64.whl",
            "demo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",
        ),
        ids=("invalid-abi3", "newer-cpython"),
    )
    def test_candidate_query_rejects_an_incompatible_wheel(
        self,
        monkeypatch: pytest.MonkeyPatch,
        filename: str,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": filename,
                        "url": filename,
                        "hashes": {"sha256": "a" * 64},
                    }
                ]
            }
        ).encode()

        class Response(BytesIO):
            headers = {"Content-Length": str(len(document))}

        monkeypatch.setattr(
            "pf.adapters.uv.urlopen", lambda request, timeout: Response(document)
        )

        candidates = UvAdapter(RecordingRunner()).query(
            dependency="demo",
            source=SourceIdentity(kind="registry", locator=None),
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
        )

        assert candidates == ()

    def test_candidate_query_rejects_an_invalid_artifact_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": "demo-1.0.tar.gz",
                        "url": "file:///tmp/demo.tar.gz",
                        "hashes": {"sha256": "a" * 64},
                    }
                ]
            }
        ).encode()

        class Response(BytesIO):
            headers = {}

        monkeypatch.setattr(
            "pf.adapters.uv.urlopen", lambda request, timeout: Response(document)
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

    def test_candidate_query_rejects_an_undeclared_oversized_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class Response(BytesIO):
            headers = {}

        payload = b"x" * (16 * 1024 * 1024 + 1)
        monkeypatch.setattr(
            "pf.adapters.uv.urlopen", lambda request, timeout: Response(payload)
        )

        with pytest.raises(InfrastructureError, match="too large"):
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

    def test_candidate_query_memoizes_raw_source_response_across_cells(
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
        opens = 0

        class Response(BytesIO):
            headers = {"Content-Length": str(len(document))}

        def open_request(request: Request, timeout: int) -> Response:
            nonlocal opens
            opens += 1
            return Response(document)

        monkeypatch.setattr("pf.adapters.uv.urlopen", open_request)
        adapter = UvAdapter(RecordingRunner())
        source = SourceIdentity(
            kind="registry",
            locator="https://index.example/simple",
        )

        for python_minor in ("3.10", "3.11"):
            candidates = adapter.query(
                dependency="demo",
                source=source,
                cell=Cell(
                    package="demo",
                    target="x86_64-unknown-linux-gnu",
                    python_minor=python_minor,
                    extra_surface=(),
                ),
            )
            assert [candidate.version for candidate in candidates] == ["1.0"]

        assert opens == 1

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

        def open_request(request: Request, timeout: int) -> Response:
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

    def test_candidate_query_skips_unparseable_legacy_distribution_filename(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        document = json.dumps(
            {
                "files": [
                    {
                        "filename": "pydantic-0.18-py36+-none-any.whl",
                        "url": "pydantic-0.18-py36+-none-any.whl",
                        "hashes": {"sha256": "a" * 64},
                    },
                    {
                        "filename": "pydantic-1.0-py3-none-any.whl",
                        "url": "pydantic-1.0-py3-none-any.whl",
                        "hashes": {"sha256": "b" * 64},
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
            dependency="pydantic",
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

        assert [candidate.version for candidate in candidates] == ["1.0"]

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
        assert runner.spec.argv[1:] == (
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

        assert caught.value.detail == "uv: python list failed"

from __future__ import annotations

import hashlib
from importlib.metadata import version as distribution_version
import json
from pathlib import Path

from packaging.requirements import Requirement
import pytest
import tomli

from pf.environment import EnvironmentFactory, PreparedEnvironment
from pf.errors import ConfigurationError
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    FailureCause,
    GraphOutcome,
    GraphSuccess,
    InterpreterOutcome,
    InterpreterSuccess,
    PrepareFailure,
    ProcessResult,
    ProgressEvent,
    ToolFailure,
    ToolOutcome,
    ToolSuccess,
)
from pf.schemas.project import InterpreterIdentity, ResolvedNode, VersionPin
from pf.snapshot import SnapshotBuilder


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="",
        stderr_summary="",
        stdout_tail="",
        stderr_tail="",
    )


class SuccessfulUv:
    def create_environment(self, **kwargs: object) -> ToolOutcome:
        return ToolSuccess(stage="create-environment", process=successful_process())

    def install_editable(self, **kwargs: object) -> ToolOutcome:
        return ToolSuccess(stage="install", process=successful_process())

    def inspect_interpreter(self, **kwargs: object) -> InterpreterOutcome:
        return InterpreterSuccess(
            process=successful_process(),
            interpreter=InterpreterIdentity(
                implementation="cpython",
                version="3.10.18",
                abi="cpython-310-x86_64-linux-gnu",
            ),
        )

    def inspect_environment(self, **kwargs: object) -> GraphOutcome:
        return GraphSuccess(
            process=successful_process(),
            nodes=(
                ResolvedNode(name="demo", version="0.1.0"),
                ResolvedNode(name="idna", version="3.10"),
            ),
        )

    def install_requirements(self, **kwargs: object) -> ToolOutcome:
        return ToolSuccess(stage="install-harness", process=successful_process())


def test_evaluation_policy_identity_ignores_scheduler_concurrency() -> None:
    automatic = EffectiveConfig(jobs="auto")
    serial = EffectiveConfig(jobs=1)

    assert evaluation_policy_identity(automatic) == evaluation_policy_identity(serial)


def test_environment_factory_materializes_an_isolated_proposal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-m", "unittest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    prepared = EnvironmentFactory(SuccessfulUv()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(prepared, PreparedEnvironment)
    assert prepared.proposal.managed_vector[0].name == "idna"
    assert prepared.proposal.managed_vector[0].version == "3.10"
    assert prepared.proposal.interpreter == InterpreterIdentity(
        implementation="cpython",
        version="3.10.18",
        abi="cpython-310-x86_64-linux-gnu",
    )
    policy_document = {
        "config": package.config.model_dump(mode="json", exclude={"jobs"}),
        "tool_versions": {"ty": distribution_version("ty")},
        "ty_diagnostic_policy": {
            "comparison": "multiset-subtraction",
            "identity_rule": (
                "snapshot-path-line-column-code+external-namespace-path-code"
            ),
            "output_format": "gitlab",
            "policy": "increment-v2",
        },
        "failure_policy": "failure-v1",
    }
    expected_policy = hashlib.sha256(
        (
            "pf:policy:v1\0"
            + json.dumps(policy_document, sort_keys=True, separators=(",", ":"))
        ).encode()
    ).hexdigest()
    assert prepared.proposal.policy_identity == expected_policy
    source = prepared.proposal_root / "pyproject.toml"
    assert source.is_file()
    source.write_text("changed\n", encoding="utf-8")
    assert (root / "pyproject.toml").read_text(encoding="utf-8").startswith("[project]")


def test_environment_materializes_requested_vector_in_replica_metadata(
    tmp_path: Path,
) -> None:
    class ReplicaInspectingUv(SuccessfulUv):
        def __init__(self) -> None:
            self.requirement: Requirement | None = None

        def install_editable(self, **kwargs: object) -> ToolOutcome:
            package = kwargs["package"]
            assert isinstance(package, Path)
            replica = ProjectLoader().load(root=package, package_selection=None)
            self.requirement = Requirement(replica.packages[0].declarations[0].raw)
            return super().install_editable(**kwargs)

        def inspect_environment(self, **kwargs: object) -> GraphSuccess:
            return GraphSuccess(
                process=successful_process(),
                nodes=(
                    ResolvedNode(name="demo", version="0.1.0"),
                    ResolvedNode(name="idna", version="3.1"),
                ),
            )

    root = tmp_path / "project"
    root.mkdir()
    original = (
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna>=3,<4"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n"
    )
    (root / "pyproject.toml").write_text(original, encoding="utf-8")
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)
    uv = ReplicaInspectingUv()

    prepared = EnvironmentFactory(uv).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
        managed_vector=(VersionPin(name="idna", version="3.1"),),
    )

    assert isinstance(prepared, PreparedEnvironment)
    assert uv.requirement is not None
    assert str(uv.requirement.specifier) == "<4,==3.1"
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == original
    prepared.close()
    snapshot.close()


def test_environment_prepare_failure_retains_attempt_without_a_proposal(
    tmp_path: Path,
) -> None:
    class ResolutionConflictUv(SuccessfulUv):
        def install_editable(self, **kwargs: object) -> ToolFailure:
            return ToolFailure(
                cause="RESOLUTION_CONFLICT",
                stage="install-project",
                process=ProcessResult(
                    exit_code=1,
                    signal=None,
                    duration_seconds=0.1,
                    stdout_summary="",
                    stderr_summary="No solution found",
                    stdout_tail="",
                    stderr_tail="No solution found",
                ),
            )

    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna>=3"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)
    requested = (VersionPin(name="idna", version="3.1"),)

    result = EnvironmentFactory(ResolutionConflictUv()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
        managed_vector=requested,
    )

    assert isinstance(result, PrepareFailure)
    assert result.attempt.identity.requested_resolution == "exact-vector"
    assert result.attempt.identity.requested_managed_vector == requested
    assert result.failure.cause == "RESOLUTION_CONFLICT"
    assert not hasattr(result, "proposal")


def test_environment_only_materializes_declarations_active_for_cell(
    tmp_path: Path,
) -> None:
    class ReplicaInspectingUv(SuccessfulUv):
        def __init__(self) -> None:
            self.dependencies: tuple[str, ...] = ()
            self.optional: tuple[str, ...] = ()
            self.sibling: str = ""

        def install_editable(self, **kwargs: object) -> ToolOutcome:
            package = kwargs["package"]
            assert isinstance(package, Path)
            with (package / "pyproject.toml").open("rb") as stream:
                document = tomli.load(stream)
            self.dependencies = tuple(document["project"]["dependencies"])
            self.optional = tuple(document["project"]["optional-dependencies"]["http"])
            self.sibling = (package.parent / "sibling" / "pyproject.toml").read_text(
                encoding="utf-8"
            )
            return super().install_editable(**kwargs)

        def inspect_environment(self, **kwargs: object) -> GraphSuccess:
            return GraphSuccess(
                process=successful_process(),
                nodes=(
                    ResolvedNode(name="demo", version="0.1.0"),
                    ResolvedNode(name="certifi", version="2024.2"),
                    ResolvedNode(name="idna", version="2.1"),
                ),
            )

    root = tmp_path / "workspace"
    demo = root / "packages" / "demo"
    sibling = root / "packages" / "sibling"
    demo.mkdir(parents=True)
    sibling.mkdir(parents=True)
    root_pyproject = (
        """
[tool.uv.workspace]
members = ["packages/*"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
extras = "each"
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n"
    )
    demo_pyproject = (
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = [
    "idna>=3; python_version >= '3.11'",
    "idna>=2; python_version < '3.11'",
]

[project.optional-dependencies]
http = [
    "urllib3>=2; python_version >= '3.11'",
    "certifi>=2024; python_version < '3.11'",
]
""".strip()
        + "\n"
    )
    sibling_pyproject = '[project]\nname = "sibling"\nversion = "0.1.0"\n'
    (root / "pyproject.toml").write_text(root_pyproject, encoding="utf-8")
    (demo / "pyproject.toml").write_text(demo_pyproject, encoding="utf-8")
    (sibling / "pyproject.toml").write_text(sibling_pyproject, encoding="utf-8")
    package = (
        ProjectLoader()
        .load(
            root=root,
            package_selection="demo",
        )
        .packages[0]
    )
    cell = next(cell for cell in package.cells if cell.extra_surface == ("http",))
    snapshot = SnapshotBuilder().build(root)
    uv = ReplicaInspectingUv()

    prepared = EnvironmentFactory(uv).prepare(
        package=package,
        cell=cell,
        snapshot=snapshot,
        resolution="highest",
        managed_vector=(
            VersionPin(name="certifi", version="2024.2"),
            VersionPin(name="idna", version="2.1"),
        ),
    )

    assert isinstance(prepared, PreparedEnvironment)
    assert [str(Requirement(raw).specifier) for raw in uv.dependencies] == [
        ">=3",
        "==2.1",
    ]
    assert [str(Requirement(raw).specifier) for raw in uv.optional] == [
        ">=2",
        "==2024.2",
    ]
    assert uv.sibling == sibling_pyproject
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == root_pyproject
    assert (demo / "pyproject.toml").read_text(encoding="utf-8") == demo_pyproject
    assert (sibling / "pyproject.toml").read_text(encoding="utf-8") == sibling_pyproject
    prepared.close()
    snapshot.close()


def test_environment_rejects_test_harness_that_changes_target_graph(
    tmp_path: Path,
) -> None:
    class ChangingGraphUv(SuccessfulUv):
        def __init__(self) -> None:
            self.inspections = 0

        def inspect_environment(self, **kwargs: object) -> GraphSuccess:
            self.inspections += 1
            version = "3.10" if self.inspections == 1 else "3.11"
            return GraphSuccess(
                process=successful_process(),
                nodes=(
                    ResolvedNode(name="demo", version="0.1.0"),
                    ResolvedNode(name="idna", version=version),
                ),
            )

    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]

[dependency-groups]
test = ["pytest"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(ChangingGraphUv()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(result, PrepareFailure)
    assert result.failure.cause == "HARNESS_CONFLICT"
    assert result.failure.stage == "install-harness"


def test_environment_reports_prepare_stages(tmp_path: Path) -> None:
    class Events:
        def __init__(self) -> None:
            self.phases: list[str] = []

        def consume(self, event: ProgressEvent) -> None:
            self.phases.append(event.phase)

    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]

[dependency-groups]
test = ["pytest"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)
    events = Events()

    prepared = EnvironmentFactory(SuccessfulUv(), events=events).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(prepared, PreparedEnvironment)
    assert events.phases == [
        "preparing environment",
        "installing dependencies",
        "installing harness",
    ]
    prepared.close()
    snapshot.close()


def _write_demo(root: Path, *, harness: bool = False) -> Path:
    project = root / "project"
    project.mkdir()
    harness_toml = (
        """
[dependency-groups]
test = ["pytest"]
"""
        if harness
        else ""
    )
    command = '["pytest"]' if harness else '["python", "-c", "pass"]'
    (project / "pyproject.toml").write_text(
        f"""
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]
{harness_toml}
[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = {command}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return project


def _failed_tool(cause: FailureCause, stage: str) -> ToolFailure:
    return ToolFailure(
        cause=cause,
        stage=stage,
        process=ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout_summary="",
            stderr_summary=f"{stage} failed",
            stdout_tail="",
            stderr_tail=f"{stage} failed",
        ),
    )


def test_environment_establishes_attempt_before_any_external_operation(
    tmp_path: Path,
) -> None:
    class CreateFails(SuccessfulUv):
        def create_environment(self, **kwargs: object) -> ToolFailure:
            return _failed_tool("ENVIRONMENT_FAILURE", "create-environment")

    root = _write_demo(tmp_path)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(CreateFails()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(result, PrepareFailure)
    assert result.attempt.identity.requested_resolution == "highest"
    assert result.attempt.identity.requested_managed_vector is None
    assert result.failure.cause == "ENVIRONMENT_FAILURE"
    assert result.failure.stage == "create-environment"
    snapshot.close()


def test_environment_check_prepare_failure_has_no_attempt(tmp_path: Path) -> None:
    class CreateFails(SuccessfulUv):
        def create_environment(self, **kwargs: object) -> ToolFailure:
            return _failed_tool("ENVIRONMENT_FAILURE", "create-environment")

    root = _write_demo(tmp_path)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(CreateFails()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="lowest-direct",
    )

    assert isinstance(result, ToolFailure)
    assert result.cause == "ENVIRONMENT_FAILURE"
    snapshot.close()


@pytest.mark.parametrize(
    ("method", "cause", "stage"),
    (
        ("inspect_interpreter", "ENVIRONMENT_FAILURE", "inspect-interpreter"),
        ("install_editable", "BUILD_FAILURE", "install-project"),
        ("inspect_environment", "TOOL_FAILURE", "inspect"),
    ),
)
def test_environment_prepare_keeps_attempt_when_a_stage_fails(
    tmp_path: Path,
    method: str,
    cause: FailureCause,
    stage: str,
) -> None:
    class StageFails(SuccessfulUv):
        def __init__(self) -> None:
            setattr(self, method, lambda **kwargs: _failed_tool(cause, stage))

    root = _write_demo(tmp_path)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(StageFails()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(result, PrepareFailure)
    assert result.attempt.identity.requested_resolution == "highest"
    assert result.failure.cause == cause
    assert result.failure.stage == stage
    snapshot.close()


def test_environment_rejects_an_interpreter_that_does_not_match_the_cell(
    tmp_path: Path,
) -> None:
    class WrongInterpreter(SuccessfulUv):
        def inspect_interpreter(self, **kwargs: object) -> InterpreterSuccess:
            return InterpreterSuccess(
                process=successful_process(),
                interpreter=InterpreterIdentity(
                    implementation="cpython",
                    version="3.11.13",
                    abi="cpython-311-x86_64-linux-gnu",
                ),
            )

    root = _write_demo(tmp_path)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(WrongInterpreter()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(result, PrepareFailure)
    assert result.failure.cause == "ENVIRONMENT_FAILURE"
    assert result.failure.stage == "inspect-interpreter"
    snapshot.close()


def test_environment_rejects_a_graph_that_omits_managed_dependencies(
    tmp_path: Path,
) -> None:
    class MissingManaged(SuccessfulUv):
        def inspect_environment(self, **kwargs: object) -> GraphSuccess:
            return GraphSuccess(
                process=successful_process(),
                nodes=(ResolvedNode(name="demo", version="0.1.0"),),
            )

    root = _write_demo(tmp_path)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(MissingManaged()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(result, PrepareFailure)
    assert result.failure.cause == "INTERNAL_INVARIANT"
    assert result.failure.stage == "inspect"
    snapshot.close()


def test_environment_prepare_keeps_attempt_when_harness_install_fails(
    tmp_path: Path,
) -> None:
    class HarnessFails(SuccessfulUv):
        def install_requirements(self, **kwargs: object) -> ToolFailure:
            return _failed_tool("HARNESS_CONFLICT", "install-harness")

    root = _write_demo(tmp_path, harness=True)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(HarnessFails()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(result, PrepareFailure)
    assert result.failure.cause == "HARNESS_CONFLICT"
    assert result.failure.stage == "install-harness"
    snapshot.close()


def test_environment_prepare_keeps_attempt_when_harness_graph_inspection_fails(
    tmp_path: Path,
) -> None:
    class HarnessInspectFails(SuccessfulUv):
        def __init__(self) -> None:
            self.inspections = 0

        def inspect_environment(self, **kwargs: object) -> GraphOutcome:
            self.inspections += 1
            if self.inspections == 2:
                return _failed_tool("TOOL_FAILURE", "inspect")
            return super().inspect_environment(**kwargs)

    root = _write_demo(tmp_path, harness=True)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    result = EnvironmentFactory(HarnessInspectFails()).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution="highest",
    )

    assert isinstance(result, PrepareFailure)
    assert result.failure.cause == "TOOL_FAILURE"
    assert result.failure.stage == "inspect"
    snapshot.close()


def test_environment_rejects_a_managed_vector_that_does_not_cover_declarations(
    tmp_path: Path,
) -> None:
    root = _write_demo(tmp_path)
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder().build(root)

    with pytest.raises(ConfigurationError, match="exactly cover"):
        EnvironmentFactory(SuccessfulUv()).prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            resolution="highest",
            managed_vector=(),
        )
    snapshot.close()

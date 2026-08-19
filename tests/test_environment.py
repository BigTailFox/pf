from __future__ import annotations

from pathlib import Path

from pf.environment import EnvironmentFactory, PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    GraphSuccess,
    InterpreterSuccess,
    ProcessResult,
    ProgressEvent,
    ToolFailure,
    ToolSuccess,
)
from pf.schemas.project import InterpreterIdentity, ResolvedNode
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
    def create_environment(self, **kwargs: object) -> ToolSuccess:
        return ToolSuccess(stage="create-environment", process=successful_process())

    def install_editable(self, **kwargs: object) -> ToolSuccess:
        return ToolSuccess(stage="install", process=successful_process())

    def inspect_interpreter(self, **kwargs: object) -> InterpreterSuccess:
        return InterpreterSuccess(
            process=successful_process(),
            interpreter=InterpreterIdentity(
                implementation="cpython",
                version="3.10.18",
                abi="cpython-310-x86_64-linux-gnu",
            ),
        )

    def inspect_environment(self, **kwargs: object) -> GraphSuccess:
        return GraphSuccess(
            process=successful_process(),
            nodes=(
                ResolvedNode(name="demo", version="0.1.0"),
                ResolvedNode(name="idna", version="3.10"),
            ),
        )

    def install_requirements(self, **kwargs: object) -> ToolSuccess:
        return ToolSuccess(stage="install-harness", process=successful_process())


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
    source = prepared.proposal_root / "pyproject.toml"
    assert source.is_file()
    source.write_text("changed\n", encoding="utf-8")
    assert (root / "pyproject.toml").read_text(encoding="utf-8").startswith("[project]")


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

    assert isinstance(result, ToolFailure)
    assert result.status == "HARNESS_ERROR"
    assert result.stage == "install-harness"


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

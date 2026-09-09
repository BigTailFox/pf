from __future__ import annotations

from pathlib import Path

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.static_inputs import (
    PreparedStaticInputs, PreparedStaticInputsUnavailable, StaticInputsAdapter,
)
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.static import StaticInstalledWorld, StaticSourceInput, StaticTargetInput
from pf.schemas.project import SourcePlan
from pf.snapshot import SnapshotBuilder

pytestmark = pytest.mark.process


class TestPreparedStaticInputs:
    def test_real_prepare_captures_installed_source_and_interpreter_closures(self, tmp_path: Path) -> None:
        with_dependency = False
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "__init__.py").write_text("VALUE = 1\n")
        (tmp_path / "pyproject.toml").write_text('''
[project]
name = "demo"
version = "1"
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
''')
        if with_dependency:
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(pyproject.read_text().replace('version = "1"', 'version = "1"\ndependencies = ["idna==3.10"]'))
        package = ProjectLoader().load(root=tmp_path).target
        source_plan = SourcePlan.for_package(package, "SEARCH")
        runner = SubprocessRunner()
        snapshot = SnapshotBuilder(runner).build(tmp_path)
        prepared = None
        try:
            prepared = EnvironmentFactory(UvAdapter(runner)).prepare(
                package=package, cell=package.cells[0], snapshot=snapshot,
                resolution=HighestResolution(), source_plan=source_plan,
            )
            assert isinstance(prepared, PreparedEnvironment)
            captured = StaticInputsAdapter(runner).capture(
                prepared, package=package, source_plan=source_plan,
            )
            assert isinstance(captured, PreparedStaticInputs), captured
            assert captured.process.exit_code == 0
            assert captured.target.interpreter == prepared.proposal.interpreter
            assert captured.target.content.entry_at(captured.target.executable).kind == "file"
            assert captured.source.snapshot_identity == snapshot.identity.digest
            assert captured.source.packages[0].package == "demo"
            demo = next(node for node in captured.installed_world.nodes if node.name == "demo")
            assert demo.version == "1"
            assert demo.install_mode == "editable"
            assert demo.source_mapping is not None
            assert demo.source_mapping.root == "snapshot"
            assert any("METADATA" in path.path for path in demo.files)
            assert {node.name for node in captured.installed_world.nodes} == {node.name for node in prepared.proposal.resolved_graph}
            if with_dependency:
                idna = next(node for node in captured.installed_world.nodes if node.name == "idna")
                assert idna.version == "3.10"
                assert idna.install_mode == "wheel"
                assert idna.source.kind == "registry"
            assert all(root.root == "environment" for root in captured.import_roots)
            wrong_source = StaticInputsAdapter(runner).capture(
                prepared, package=package, source_plan=SourcePlan.for_package(package, "DEVELOPMENT"),
            )
            assert isinstance(wrong_source, PreparedStaticInputsUnavailable)
            assert wrong_source.process is None
            saved = (
                captured.source.model_dump_json(), captured.target.model_dump_json(),
                captured.installed_world.model_dump_json(),
            )
            metadata = next(path for path in demo.files if path.path.endswith("/METADATA"))
            physical = dict(captured.roots)[metadata.root] / metadata.path
            physical.write_text(physical.read_text() + "Summary: changed installation bytes\n")
            changed = StaticInputsAdapter(runner).capture(
                prepared, package=package, source_plan=source_plan,
            )
            assert isinstance(changed, PreparedStaticInputs)
            assert changed.source == captured.source
            assert changed.target == captured.target
            assert changed.installed_world.nodes == captured.installed_world.nodes
            assert changed.installed_world.content.identity != captured.installed_world.content.identity
            prepared.mark_tested()
            unavailable = StaticInputsAdapter(runner).capture(
                prepared, package=package, source_plan=source_plan,
            )
            assert isinstance(unavailable, PreparedStaticInputsUnavailable)
            assert unavailable.process is None
            assert unavailable.failure.detail == "installed-input-mismatch"
        finally:
            if isinstance(prepared, PreparedEnvironment):
                prepared.close()
            snapshot.close()
        assert StaticSourceInput.model_validate_json(saved[0]) == captured.source
        assert StaticTargetInput.model_validate_json(saved[1]) == captured.target
        assert StaticInstalledWorld.model_validate_json(saved[2]) == captured.installed_world

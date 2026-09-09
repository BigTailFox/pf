from __future__ import annotations

from pathlib import Path

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.static_inputs import (
    PreparedStaticInputs, PreparedStaticInputsUnavailable, StaticInputsAdapter,
    _INSPECT,
)
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.project import SourcePlan
from pf.snapshot import SnapshotBuilder


def test_inspect_script_does_not_access_distribution_files() -> None:
    assert "distribution.files" not in _INSPECT
    assert ".files" not in _INSPECT
    source = Path("src/pf/adapters/static_inputs.py").read_text(encoding="utf-8")
    assert "distribution.files" not in source


@pytest.mark.process
class TestPreparedStaticInputs:
    def test_real_prepare_inspects_interpreter_without_file_inventory(self, tmp_path: Path) -> None:
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
            assert captured.interpreter == prepared.proposal.interpreter
            assert captured.diagnostic_prefix
            wrong_source = StaticInputsAdapter(runner).capture(
                prepared, package=package, source_plan=SourcePlan.for_package(package, "DEVELOPMENT"),
            )
            assert isinstance(wrong_source, PreparedStaticInputsUnavailable)
            assert wrong_source.process is None
            prepared.mark_tested()
            unavailable = StaticInputsAdapter(runner).capture(
                prepared, package=package, source_plan=source_plan,
            )
            assert isinstance(unavailable, PreparedStaticInputsUnavailable)
            assert unavailable.failure.detail == "installed-input-mismatch"
        finally:
            if isinstance(prepared, PreparedEnvironment):
                prepared.close()
            snapshot.close()

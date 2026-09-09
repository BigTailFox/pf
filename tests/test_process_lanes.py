from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import tomli

from pf.adapters.process import SubprocessRunner
from pf.schemas.evaluation import ProcessSpec
from process_lane import ProcessLaneViolation, assert_e2e_implies_process
from visible_text import run_installed_pf, run_pf_cli, run_ty_executable

pytestmark = pytest.mark.infra

ROOT = Path(__file__).resolve().parents[1]
DAILY_ADDOPTS = ["--testmon", "-m", "not process and not e2e and not qualification"]
BOOTSTRAP_COMMAND = [
    "pytest",
    "--no-testmon",
    "--no-cov",
    "--maxfail=1",
    "-m",
    "not process and not e2e and not qualification and not infra",
]
REGISTERED_MARKERS = {"process", "e2e", "qualification", "infra"}


def _pytest_config() -> dict[str, object]:
    return tomli.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]


def _marker_names(markers: list[str]) -> set[str]:
    names = set()
    for marker in markers:
        names.add(marker.split(":", 1)[0].strip())
    return names


class TestRepositoryTestLanes:
    def test_pytest_registers_the_four_lane_markers(self) -> None:
        markers = _pytest_config()["markers"]
        assert isinstance(markers, list)
        names = _marker_names(markers)
        assert names == REGISTERED_MARKERS

    def test_daily_addopts_match_the_lane_contract(self) -> None:
        assert _pytest_config()["addopts"] == DAILY_ADDOPTS

    def test_bootstrap_test_command_matches_the_lane_contract(self) -> None:
        document = tomli.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert document["tool"]["pf"]["test-command"] == BOOTSTRAP_COMMAND

    def test_ci_jobs_pass_explicit_marker_expressions(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "uv run pytest --no-testmon -m \"not qualification\"" in workflow
        assert (
            "uv run pytest --no-testmon --cov --cov-report=term-missing "
            "--cov-fail-under=0 -m \"\""
        ) in workflow
        assert "coverage combine" in workflow
        assert "fail_under = 90" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    def test_e2e_without_process_fails_collection_check(self) -> None:
        item = SimpleNamespace(
            nodeid="tests/test_example.py::test_e2e_only",
            get_closest_marker=lambda name: object() if name == "e2e" else None,
        )
        with pytest.raises(ProcessLaneViolation, match="e2e tests must also be marked process"):
            assert_e2e_implies_process([item])

    def test_e2e_with_process_passes_collection_check(self) -> None:
        item = SimpleNamespace(
            nodeid="tests/test_example.py::test_e2e",
            get_closest_marker=lambda name: object() if name in {"e2e", "process"} else None,
        )
        assert_e2e_implies_process([item])

    def test_unmarked_subprocess_runner_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ProcessLaneViolation, match="SubprocessRunner.run"):
            SubprocessRunner().run(
                ProcessSpec(
                    argv=(sys.executable, "-c", "pass"),
                    cwd=str(tmp_path),
                    timeout_seconds=1,
                )
            )

    def test_unmarked_run_pf_cli_is_rejected(self) -> None:
        with pytest.raises(ProcessLaneViolation, match="run_pf_cli"):
            run_pf_cli("--help")

    def test_unmarked_run_installed_pf_is_rejected(self) -> None:
        with pytest.raises(ProcessLaneViolation, match="run_installed_pf"):
            run_installed_pf("--help")

    def test_unmarked_run_ty_executable_is_rejected(self) -> None:
        with pytest.raises(ProcessLaneViolation, match="run_ty_executable"):
            run_ty_executable(("ty", "--version"))

from __future__ import annotations

import json
from pathlib import Path
from runpy import run_path
from typing import Callable, cast


_script = run_path("scripts/qualify_uv_workspace_sources.py")
SCENARIOS = cast(tuple[str, ...], _script["SCENARIOS"])
_qualify_unmanaged_workspace_fail_closed = cast(
    Callable[[Path], object],
    _script["qualify_unmanaged_workspace_fail_closed"],
)
MANIFEST = Path("tests/uv_workspace_qualification/matrix-manifest.json")


class TestUvWorkspaceSourceQualification:
    def test_committed_manifest_covers_the_fixed_uv_profile(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        assert manifest["schema"] == "pf-uv-workspace-sources-qualification-v1"
        assert manifest["uv_version"] == "0.12.5"
        assert manifest["all_passed"] is True
        assert {item["scenario"] for item in manifest["scenarios"]} == set(SCENARIOS)
        assert all(item["compile_count"] == 4 for item in manifest["scenarios"])
        assert all(item["install_count"] == 2 for item in manifest["scenarios"])
        assert all(
            item["exact_artifact_dependencies"] == ["certifi", "idna"]
            for item in manifest["scenarios"]
        )

    def test_runner_replays_unmanaged_workspace_fail_closed_locally(
        self,
        tmp_path: Path,
    ) -> None:
        record = _qualify_unmanaged_workspace_fail_closed(tmp_path / "workspace")

        assert getattr(record, "disposition") == "INDETERMINATE"
        assert getattr(record, "cause") == "TOOL_FAILURE"
        assert getattr(record, "stage") == "resolve-project"
        assert getattr(record, "compile_suppressions") == ("certifi", "idna")
        assert getattr(record, "install_count") == 0
        assert getattr(record, "source_tables_preserved") is True

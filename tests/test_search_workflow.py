from __future__ import annotations

from pathlib import Path

from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.scheduling import Scheduler
from pf.schemas.config import SearchRequest
from pf.schemas.project import Cell, PackagePlan
from pf.schemas.report import CellFailure
from pf.snapshot import SnapshotBuilder
from pf.workflow import SearchCommandWorkflow


class FailedSearch:
    def __init__(self) -> None:
        self.cells: list[Cell] = []

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: object,
    ) -> CellFailure:
        self.cells.append(cell)
        return CellFailure(
            status="TIMEOUT",
            cell=cell,
            phase=f"evaluation-{len(self.cells)}",
        )


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


def test_search_workflow_schedules_cells_and_writes_incomplete_report(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["idna"]

[dependency-groups]
test = ["pytest"]

[tool.pf]
python = ["3.10", "3.11"]
platform = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    events = Events()
    store = ReportStore()
    workflow = SearchCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        coordinator=FailedSearch(),
        scheduler=Scheduler(),
        reports=store,
        report_builder=PackageReportBuilder(),
        events=events,
    )

    output = workflow.run(
        SearchRequest(
            root=tmp_path.as_posix(),
            package=None,
            jobs=2,
            max_duration_seconds=None,
        )
    )

    assert output[0].result.status == "incomplete"
    assert output[0].result.reasons == ("TIMEOUT", "UNREPRESENTABLE_PROJECTION")
    assert len(events.items) == 2
    assert store.read(tmp_path / "package-floor.json") == output[0]

    repeated = workflow.run(
        SearchRequest(
            root=tmp_path.as_posix(),
            package=None,
            jobs=2,
            max_duration_seconds=None,
        )
    )
    assert repeated[0].source_snapshot == output[0].source_snapshot
    assert repeated[0].cell_results != output[0].cell_results

    (tmp_path / "new-source.py").write_text("VALUE = 1\n", encoding="utf-8")
    refreshed = workflow.run(
        SearchRequest(
            root=tmp_path.as_posix(),
            package=None,
            jobs=2,
            max_duration_seconds=None,
        )
    )
    assert refreshed[0].source_snapshot != output[0].source_snapshot
    assert store.read(tmp_path / "package-floor.json") == refreshed[0]


def test_search_workflow_never_executes_a_non_host_target(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = []

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu", "aarch64-apple-darwin"]
managed-deps = []
test-command = ["python", "-c", "pass"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    coordinator = FailedSearch()
    workflow = SearchCommandWorkflow(
        projects=ProjectLoader(),
        snapshots=SnapshotBuilder(),
        coordinator=coordinator,
        scheduler=Scheduler(),
        reports=ReportStore(),
        report_builder=PackageReportBuilder(),
        events=Events(),
        host_target="x86_64-unknown-linux-gnu",
    )

    reports = workflow.run(SearchRequest(root=tmp_path.as_posix()))

    assert [cell.target for cell in coordinator.cells] == [
        "x86_64-unknown-linux-gnu"
    ]
    assert reports[0].result.status == "incomplete"
    assert "MISSING_CELL" in reports[0].result.reasons

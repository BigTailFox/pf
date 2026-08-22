from __future__ import annotations

import json
from pathlib import Path
import sys

from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.runlog import RunLogStore
from pf.scheduling import Scheduler
from pf.schemas.config import SearchRequest
from pf.schemas.evaluation import (
    CellFailureScope,
    CellMatrixEvent,
    FailureDetail,
    ProcessResult,
    ProcessSpec,
    ProgressEvent,
)
from pf.schemas.project import Cell, PackagePlan
from pf.schemas.report import CellIndeterminate
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.verification import VerificationRunner
from pf.workflow import SearchCommandWorkflow


class FailedSearch:
    def __init__(self, process: ProcessResult | None = None) -> None:
        self.cells: list[Cell] = []
        self.process = process

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> CellIndeterminate:
        self.cells.append(cell)
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package=package.name,
                cell=cell,
                source_snapshot_digest=snapshot.identity.digest,
                evaluation_policy_identity=evaluation_policy_identity(package.config),
            ),
            cause="TIMEOUT",
            stage=f"evaluation-{len(self.cells)}",
            process=self.process,
            detail=(
                None
                if self.process is not None
                else FailureDetail(code="deadline", message="deadline expired")
            ),
        )
        return CellIndeterminate(
            cell=cell,
            phase=f"evaluation-{len(self.cells)}",
            failure_id=failure.failure_id,
            failure_records=(failure,),
        )


class Events:
    def __init__(self) -> None:
        self.items: list[object] = []

    def consume(self, event: object) -> None:
        self.items.append(event)


class TestSearchWorkflow:
    def test_search_workflow_schedules_cells_and_writes_incomplete_report(
        self,
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
            verification=VerificationRunner(
                scheduler=Scheduler(), events=events, logs=None
            ),
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
        assert output[0].result.reasons == (
            "INDETERMINATE",
            "UNREPRESENTABLE_PROJECTION",
        )
        completions = [
            event
            for event in events.items
            if isinstance(event, ProgressEvent) and event.phase != "start"
        ]
        assert len(completions) == 2
        matrix = next(
            event for event in events.items if isinstance(event, CellMatrixEvent)
        )
        assert [(cell.python_minor, cell.target) for cell in matrix.cells] == [
            ("3.10", "x86_64-unknown-linux-gnu"),
            ("3.11", "x86_64-unknown-linux-gnu"),
        ]
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

    def test_search_workflow_indexes_failure_logs_after_writing_the_report(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [dependency-groups]
    test = []

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        logs = RunLogStore(root=tmp_path, run_id="search-run")
        process = ProcessResult(
            exit_code=124,
            signal=None,
            duration_seconds=1,
            stdout="",
            stderr="",
            timed_out=True,
        )
        logs.record(
            1,
            ProcessSpec(
                argv=(sys.executable, "-c", "pass"),
                cwd=tmp_path.as_posix(),
                timeout_seconds=1,
            ),
            process,
        )
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder(),
            coordinator=FailedSearch(process),
            verification=VerificationRunner(
                scheduler=Scheduler(), events=Events(), logs=logs
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=Events(),
            associations=logs,
        )

        report = workflow.run(SearchRequest(root=tmp_path.as_posix()))[0]
        failure = report.failure_records[0]

        assert logs.lookup(report.report_generation_id, failure.failure_id) == Path(
            ".pf/logs/search-run/process-0001.log"
        )

        replacement = workflow.run(SearchRequest(root=tmp_path.as_posix()))[0]
        replacement_failure = replacement.failure_records[0]

        assert replacement_failure.failure_id != failure.failure_id
        assert logs.lookup(report.report_generation_id, failure.failure_id) is None
        assert logs.lookup(
            replacement.report_generation_id,
            replacement_failure.failure_id,
        ) == Path(".pf/logs/search-run/process-0001.log")
        journal = logs.read_latest_journal("demo")
        assert journal is not None
        assert journal.command == "search"
        assert journal.run_id == "search-run"
        assert {entry.failure.failure_id for entry in journal.entries} == {
            replacement_failure.failure_id
        }

    def test_search_replaces_a_report_from_an_incompatible_policy_generation(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"

    [dependency-groups]
    test = []

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    managed-deps = []
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        store = ReportStore()
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder(),
            coordinator=FailedSearch(),
            verification=VerificationRunner(
                scheduler=Scheduler(), events=Events(), logs=None
            ),
            reports=store,
            report_builder=PackageReportBuilder(),
            events=Events(),
        )
        request = SearchRequest(root=tmp_path.as_posix())
        current = workflow.run(request)[0]
        report_path = tmp_path / "package-floor.json"
        document = json.loads(report_path.read_text(encoding="utf-8"))
        legacy_policy = "pre-diagnostic-baseline-policy"
        cell_result = document["cell_results"][0]
        cell_result.update(
            {
                "status": "BASELINE_FAILED",
                "phase": "baseline-evaluation",
                "baseline": {
                    "status": "STATIC_FAIL",
                    "proposal": {
                        "proposal_id": "legacy-baseline",
                        "snapshot_digest": document["source_snapshot"]["digest"],
                        "cell": cell_result["cell"],
                        "managed_vector": [],
                        "fixed_declaration_ids": [],
                        "resolved_graph": [],
                        "policy_identity": legacy_policy,
                        "interpreter": None,
                    },
                    "ty": {
                        "status": "STATIC_FAIL",
                        "process": {
                            "exit_code": 1,
                            "signal": None,
                            "duration_seconds": 0.1,
                            "stdout": "",
                            "stderr": "",
                            "stdout_complete": True,
                            "stderr_complete": True,
                            "timed_out": False,
                            "start_error": None,
                        },
                    },
                },
            }
        )
        document["policy_identity"] = legacy_policy
        report_path.write_text(json.dumps(document), encoding="utf-8")

        refreshed = workflow.run(request)[0]

        assert refreshed.policy_identity == current.policy_identity
        assert store.read(report_path) == refreshed

    def test_search_workflow_never_executes_a_non_host_target(
        self, tmp_path: Path
    ) -> None:
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
        events = Events()
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder(),
            coordinator=coordinator,
            verification=VerificationRunner(
                scheduler=Scheduler(), events=events, logs=None
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=events,
            host_target="x86_64-unknown-linux-gnu",
        )

        reports = workflow.run(SearchRequest(root=tmp_path.as_posix()))

        assert [cell.target for cell in coordinator.cells] == [
            "x86_64-unknown-linux-gnu"
        ]
        matrix = next(
            event for event in events.items if isinstance(event, CellMatrixEvent)
        )
        assert [cell.target for cell in matrix.cells] == ["x86_64-unknown-linux-gnu"]
        assert reports[0].result.status == "incomplete"
        assert "MISSING_CELL" in reports[0].result.reasons

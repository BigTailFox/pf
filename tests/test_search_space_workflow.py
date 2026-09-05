from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from evaluation_fixtures import evaluation_assembly, available_artifact
from pf.candidates import CandidateBuilder
from pf.errors import ConfigurationError, SearchSpaceResolutionError
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.runlog import RunLogStore
from pf.schemas.config import CheckRequest, SearchRequest, SmokeRequest
from pf.schemas.project import AvailableCandidate, Cell, RegistryCandidates
from pf.schemas.evaluation import CellCompletedEvent, ProcessResult, ProcessSpec
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder
from pf.verification import VerificationRunner
from pf.workflow import (
    CheckCommandWorkflow,
    SearchCommandWorkflow,
    SmokeCommandWorkflow,
)


class Events:
    def __init__(self):
        self.items = []

    def consume(self, event):
        self.items.append(event)


def write_project(
    root: Path, space: str, *, platform="x86_64-unknown-linux-gnu"
) -> None:
    (root / "pyproject.toml").write_text(f'''[project]
name = "demo"
version = "0.1"
dependencies = ["demo-dep<4"]
[project.optional-dependencies]
extra = ["demo-dep<4"]
[dependency-groups]
test = []
[tool.pf]
pythons = ["3.10"]
platforms = ["{platform}"]
search-space = "{space}"
max-cells = 2
test-command = ["python", "-c", "pass"]
''')


class TestSearchSpaceWorkflow:
    def test_declaration_admission_checks_non_host_before_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_project(tmp_path, "majors[declaration:]", platform="aarch64-apple-darwin")
        events = Events()
        assembly = evaluation_assembly()

        def forbidden(*args, **kwargs):
            raise AssertionError("invalid declaration premise must precede snapshot")

        monkeypatch.setattr(SnapshotBuilder, "build", forbidden)
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=assembly.coordinator,
            verification=VerificationRunner(
                events=events, logs=None, host_target="x86_64-unknown-linux-gnu"
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=events,
        )
        with pytest.raises(
            ConfigurationError, match="declaration lower bound"
        ) as caught:
            workflow.run(SearchRequest(root=str(tmp_path)))
        assert caught.value.exit_code == 3
        assert assembly.uv.resolutions == []

    @pytest.mark.parametrize("command", ["smoke", "check"])
    def test_non_search_workflows_do_not_consume_anchor(
        self, tmp_path: Path, command: str
    ) -> None:
        write_project(tmp_path, "majors[declaration:]")
        events = Events()

        class ReachedOperation(Exception):
            pass

        class Operation:
            def check(self, **kwargs) -> NoReturn:
                raise ReachedOperation()

            def verify(self, **kwargs) -> NoReturn:
                raise ReachedOperation()

        verification = VerificationRunner(
            events=events, logs=None, host_target="x86_64-unknown-linux-gnu"
        )
        if command == "check":
            workflow = CheckCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder.without_processes(),
                checker=Operation(),
                verification=verification,
                events=events,
            )
            with pytest.raises(ReachedOperation):
                workflow.run(CheckRequest(root=str(tmp_path)))
        else:
            workflow = SmokeCommandWorkflow(
                projects=ProjectLoader(),
                snapshots=SnapshotBuilder.without_processes(),
                verifier=Operation(),
                verification=verification,
                events=events,
            )
            with pytest.raises(ReachedOperation):
                workflow.run(SmokeRequest(root=str(tmp_path)))

    @pytest.mark.parametrize("existing_report", [False, True])
    def test_resolution_error_drains_cells_keeps_journal_and_never_writes_report(
        self, tmp_path: Path, existing_report: bool
    ) -> None:
        write_project(tmp_path, "majors[baseline]")
        report_path = tmp_path / "package-floor.json"
        previous = b'{"previous": "report bytes must be retained"}\n'
        if existing_report:
            report_path.write_bytes(previous)
        events = Events()
        logs = RunLogStore(root=tmp_path, run_id="space-failure")
        process = ProcessResult(
            exit_code=0, signal=None, duration_seconds=0.0, stdout="", stderr=""
        )
        spec = ProcessSpec(
            argv=("fixture-registry",), cwd=str(tmp_path), timeout_seconds=None
        )
        old_log = logs.record(99, spec, process, stdout="previous run evidence")
        logs.associate("previous-generation", "failure-0123456789abcdef", process)
        retained_logs = []
        assembly = evaluation_assembly()

        class Registry:
            def query(self, *, cell, **kwargs) -> RegistryCandidates:
                retained_logs.append(
                    logs.record(
                        2 if cell.extra_surface else 1,
                        spec,
                        process.model_copy(),
                        stdout=f"registry observation: {cell.extra_surface}",
                    )
                )
                if cell.extra_surface:
                    return RegistryCandidates(release_versions=(), candidates=())
                return RegistryCandidates(
                    release_versions=("3",),
                    candidates=(
                        AvailableCandidate(
                            version="3",
                            artifacts=(available_artifact("demo-dep", "3"),),
                        ),
                    ),
                )

        coordinator = SearchCoordinator(
            environments=assembly.environments,
            candidates=CandidateBuilder(Registry()),
            static=assembly.static,
            full=assembly.runtime,
            highest=assembly.highest,
            coordinate_search=assembly.coordinate_search,
        )
        workflow = SearchCommandWorkflow(
            projects=ProjectLoader(),
            snapshots=SnapshotBuilder.without_processes(),
            coordinator=coordinator,
            verification=VerificationRunner(
                events=events, logs=logs, host_target="x86_64-unknown-linux-gnu"
            ),
            reports=ReportStore(),
            report_builder=PackageReportBuilder(),
            events=events,
            logs=logs,
        )
        with pytest.raises(SearchSpaceResolutionError) as caught:
            workflow.run(SearchRequest(root=str(tmp_path)))
        assert caught.value.reason == "missing-anchor-series"
        assert isinstance(caught.value.cell, Cell)
        assert caught.value.cell.extra_surface == ("extra",)
        assert caught.value.anchors == (("baseline", "3"),)
        assert caught.value.exit_code == 2
        assert any(isinstance(event, CellCompletedEvent) for event in events.items)
        assert logs.read_latest_journal("demo") is not None
        assert len(retained_logs) == 2
        assert all(
            "registry observation:" in path.read_text() for path in retained_logs
        )
        assert logs.lookup("previous-generation", "failure-0123456789abcdef") == old_log.relative_to(tmp_path)
        assert all(not path.exists() for path in assembly.uv.environment_roots)
        if existing_report:
            assert report_path.read_bytes() == previous
        else:
            assert not report_path.exists()

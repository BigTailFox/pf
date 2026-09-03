from __future__ import annotations

from pathlib import Path

import pytest

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    successful_process,
)

from pf.errors import InfrastructureError
from pf.schemas.evaluation import (
    BaselineIndeterminate,
    BaselineRejection,
    CellContextEvent,
    CellSearchProgressEvent,
    CellStageEvent,
    NormalExit,
    ProcessResult,
    SearchFailureEvent,
    TimedOut,
    ToolFailure,
    VerifierDiagnostics,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)
from pf.schemas.project import VersionPin
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
    ProbeIndeterminate,
    ProbePass,
    ProbeRejection,
    StaticOnlyEvidence,
)


class RecordingDiagnostics:
    def __init__(self) -> None:
        self.events: list[SearchFailureEvent] = []

    def consume(self, event: SearchFailureEvent) -> None:
        self.events.append(event)


class RecordingActivity:
    def __init__(self) -> None:
        self.events: list[object] = []

    def consume(self, event: object) -> None:
        self.events.append(event)


def threshold_verifier(
    vector: tuple[VersionPin, ...],
    call: int,
    *,
    diagnostics: bool = True,
) -> VerifierRun:
    del call
    version = int(vector[0].version)
    if version >= 2:
        return VerifierRun(
            authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
        )
    return VerifierRun(
        authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
        diagnostics=(
            VerifierDiagnostics(process=successful_process(exit_code=1))
            if diagnostics
            else None
        ),
    )


class TestSearchCoordinator:
    @pytest.mark.parametrize("indeterminate", (False, True))
    def test_search_stops_on_the_real_highest_verification_outcome(
        self,
        tmp_path: Path,
        indeterminate: bool,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        outcome = (
            VerifierIndeterminate(
                terminal=TimedOut(),
                reason="process-timed-out",
            )
            if indeterminate
            else VerifierRejected(terminal=NormalExit(exit_code=1))
        )
        assembly = evaluation_assembly(
            verifier_handler=lambda vector, call: VerifierRun(authoritative=outcome),
        )

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(
            result,
            BaselineIndeterminate if indeterminate else BaselineRejection,
        )
        assert assembly.candidates.queries == []
        assert assembly.uv.resolutions == ["highest"]
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_search_reports_an_empty_candidate_space_after_a_closed_baseline(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(candidate_versions=())

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSearchFailure)
        assert result.reason == "NO_PASS_IN_SEARCH_SPACE"
        assert result.phase == "candidate-discovery"
        assert len(assembly.candidates.queries) == 1
        assert assembly.uv.resolutions == ["highest"]
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_search_retains_candidate_source_failure_as_cell_evidence(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(
            candidate_error=InfrastructureError("registry unavailable")
        )

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellIndeterminate)
        assert result.phase == "candidate-discovery"
        assert result.failure_records[0].failure_id == result.failure_id
        assert result.failure_records[0].cause == "SOURCE_FAILURE"
        assert result.failure_records[0].authority.kind == "structured"
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    @pytest.mark.parametrize("runtime_diagnostics", (False, True))
    def test_search_returns_a_runtime_backed_floor_with_closed_public_evidence(
        self,
        tmp_path: Path,
        runtime_diagnostics: bool,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        diagnostics = RecordingDiagnostics()
        activity = RecordingActivity()
        assembly = evaluation_assembly(
            verifier_handler=lambda vector, call: threshold_verifier(
                vector,
                call,
                diagnostics=runtime_diagnostics,
            ),
            diagnostics=diagnostics,
            events=activity,
        )

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        assert result.final_vector == (VersionPin(name="demo-dep", version="2"),)
        assert result.search.vector == result.final_vector
        assert result.final_evaluation.proposal.managed_vector == result.final_vector
        observations = result.search.observations
        direct = tuple(
            observation.evidence
            for observation in observations
            if isinstance(
                observation.evidence,
                (ProbePass, ProbeRejection, ProbeIndeterminate),
            )
        )
        assert any(
            isinstance(evidence, ProbePass)
            and evidence.evaluation == result.final_evaluation
            for evidence in direct
        )
        rejection = next(
            evidence for evidence in direct if isinstance(evidence, ProbeRejection)
        )
        failure = next(
            item
            for item in result.failure_records
            if item.failure_id == rejection.failure_id
        )
        assert failure.cause == "VERIFIER_EXITED_NONZERO"
        assert result.search.boundaries[0].predecessor_failure_id == failure.failure_id
        assert all(
            region.slice.cell == result.cell for region in result.search.regions
        )
        assert any(
            isinstance(observation.evidence, StaticOnlyEvidence)
            for observation in observations
        )
        assert len(diagnostics.events) == 1
        assert diagnostics.events[0].failure == failure
        assert bool(result.failure_runtime_runs) is runtime_diagnostics
        runtime = diagnostics.events[0].runtime
        assert runtime is not None
        assert bool(runtime.diagnostics) is runtime_diagnostics
        assert any(isinstance(event, CellContextEvent) for event in activity.events)
        assert any(isinstance(event, CellStageEvent) for event in activity.events)
        assert any(
            isinstance(event, CellSearchProgressEvent) for event in activity.events
        )
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_search_reuses_a_full_probe_for_final_evaluation(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(verifier_handler=threshold_verifier)

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        assert assembly.uv.install_vectors == [
            (VersionPin(name="demo-dep", version="3"),),
            (VersionPin(name="demo-dep", version="1"),),
            (VersionPin(name="demo-dep", version="2"),),
        ]
        assert assembly.verifier.vectors == [
            (VersionPin(name="demo-dep", version="3"),),
            (VersionPin(name="demo-dep", version="1"),),
            (VersionPin(name="demo-dep", version="2"),),
        ]
        assert len(assembly.candidates.queries) == 1
        assert "lowest-direct" not in assembly.uv.resolutions
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_search_maps_every_exact_probe_to_the_frozen_candidate_artifact(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(verifier_handler=threshold_verifier)

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        frozen = {
            (snapshot.dependency, candidate.version): candidate.artifact
            for snapshot in result.candidate_snapshots
            for candidate in snapshot.candidates
        }
        assert assembly.uv.exact_selections
        assert all(
            frozen[(candidate.dependency, candidate.version)] == candidate.artifact
            for selection in assembly.uv.exact_selections
            for candidate in selection
        )

    def test_search_preserves_exact_prepare_failure_and_emits_one_diagnostic(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        diagnostics = RecordingDiagnostics()
        assembly = evaluation_assembly(
            verifier_handler=threshold_verifier,
            diagnostics=diagnostics,
        )
        failed_vector = (VersionPin(name="demo-dep", version="1"),)
        assembly.uv.install_failures_by_vector[failed_vector] = ToolFailure(
            cause="BUILD_FAILURE",
            stage="install-environment",
            process=successful_process(exit_code=2),
        )

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellIndeterminate)
        assert result.coordinate_failure is not None
        evidence = next(
            observation.evidence
            for observation in result.coordinate_failure.observations
            if isinstance(observation.evidence, ProbeIndeterminate)
        )
        assert evidence.attempt.identity.requested_managed_vector == failed_vector
        assert evidence.proposal_id is None
        assert evidence.cause == "BUILD_FAILURE"
        assert result.failure_id == evidence.failure_id
        assert len(diagnostics.events) == 1
        assert diagnostics.events[0].failure.failure_id == evidence.failure_id
        assert assembly.uv.install_vectors.count(failed_vector) == 1
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_search_retains_terminal_runtime_indeterminate_diagnostics(
        self,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        diagnostics = RecordingDiagnostics()

        def indeterminate_below_two(
            vector: tuple[VersionPin, ...],
            call: int,
        ) -> VerifierRun:
            del call
            if int(vector[0].version) >= 2:
                return VerifierRun(
                    authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
                )
            return VerifierRun(
                authoritative=VerifierIndeterminate(
                    terminal=TimedOut(),
                    reason="process-timed-out",
                ),
                diagnostics=VerifierDiagnostics(
                    process=ProcessResult(
                        signal=9,
                        duration_seconds=30,
                        timed_out=True,
                    )
                ),
            )

        assembly = evaluation_assembly(
            verifier_handler=indeterminate_below_two,
            diagnostics=diagnostics,
        )

        result = assembly.coordinator.search(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellIndeterminate)
        assert result.failure_records[0].cause == "TIMEOUT"
        assert result.failure_runtime_runs
        assert len(diagnostics.events) == 1
        assert diagnostics.events[0].runtime is not None
        assert diagnostics.events[0].runtime.diagnostics is not None
        assert all(not root.exists() for root in assembly.uv.environment_roots)

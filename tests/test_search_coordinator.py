from __future__ import annotations

import json
from pathlib import Path

import pytest

from visible_text import visible_cli_text

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    successful_process,
)

from pf.coordinate_search import CoordinateSearch
from pf.errors import ConfigurationError, DiagnoseNotFoundError, InfrastructureError
from pf.project_discovery import ProjectDiscovery
from pf.schemas.config import DiagnoseRequest
from pf.workflow import DiagnoseCommandWorkflow
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.schemas.evaluation import (
    BaselineIndeterminate,
    BaselineRejection,
    CellContextEvent,
    CellSearchProgressEvent,
    CellStageEvent,
    NormalExit,
    ProcessResult,
    SearchFailureEvent,
    SearchProbeDetailIdentity,
    TimedOut,
    ToolFailure,
    TyCheck,
    OperationFailureResult,
    ExecutionFailure,
    Unattributed,
    StructuredOperationFailure,
    SourceAccessFailedFact,
    VerifierDiagnostics,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)
from pf.schemas.project import SourcePlan, VersionPin
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
    ProbeIndeterminate,
    ProbePass,
    ProbeRejection,
)
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder
from pf.static import TyCheckCache


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


def _pass_run(*, exit_code: int = 0) -> VerifierRun:
    return VerifierRun(
        authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
        diagnostics=VerifierDiagnostics(process=successful_process(exit_code=exit_code)),
    )


def threshold_verifier(
    vector: tuple[VersionPin, ...],
    call: int,
    *,
    diagnostics: bool = True,
) -> VerifierRun:
    del call
    version = int(vector[0].version)
    if version >= 2:
        return _pass_run()
    return VerifierRun(
        authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
        diagnostics=(
            VerifierDiagnostics(process=successful_process(exit_code=1))
            if diagnostics
            else None
        ),
    )


def assert_public_direct_bound(result, *, floor, predecessor, predecessor_required=True):
    boundary = result.search.boundaries[0]
    assert boundary.floor == floor
    assert boundary.predecessor == predecessor
    if predecessor_required:
        assert boundary.predecessor_failure_id
    else:
        assert boundary.predecessor_failure_id is None


def assert_guidance_journal_roundtrip(tmp_path, project, result):
    from pf.runlog import RunLogStore
    from pf.schemas.journal import (
        VerificationJournal,
        VerificationJournalEntry,
        VerificationPackagePolicy,
        cell_canonical_key,
    )
    from pf.schemas.evaluation import AttemptFailureScope

    def journal_role(failure):
        if not isinstance(failure.scope, AttemptFailureScope):
            return "probe"
        requested = failure.scope.attempt.identity.requested_resolution
        return "baseline" if requested == "highest" else "probe"

    logs = RunLogStore(root=tmp_path, run_id="guided")
    entries = tuple(
        sorted(
            (
                VerificationJournalEntry(
                    package=project.package.name,
                    cell=project.package.cells[0],
                    role=journal_role(failure),
                    failure=failure,
                    attempt=(
                        failure.scope.attempt
                        if isinstance(failure.scope, AttemptFailureScope)
                        else None
                    ),
                )
                for failure in result.failure_records
            ),
            key=lambda entry: (*cell_canonical_key(entry.cell), entry.failure.failure_id),
        )
    )
    journal = VerificationJournal(
        run_id=logs.run_id,
        command="search",
        source_snapshot_digest=project.snapshot.identity.digest,
        package_policies=(
            VerificationPackagePolicy(
                package=project.package.name,
                execution_policy_identity=result.baseline.proposal.policy_identity,
            ),
        ),
        entries=entries,
        static_membership=(),
    )
    logs.write_journal(journal)
    assert logs.read_journal(logs.run_id) == journal


def assert_public_selection_reasons(report) -> None:
    from pf.schemas.report import CellSearchFailure, CellSuccess

    intern_fields = {
        "static_contents",
        "static_subjects",
        "static_facts",
        "static_comparisons",
        "static_scopes",
    }
    evidence = report._wire.model_dump(mode="json")["evidence"]
    assert intern_fields.isdisjoint(evidence)
    for result in report.cell_results:
        searches = []
        if isinstance(result, CellSuccess):
            searches.append(result.search)
        elif isinstance(result, (CellSearchFailure, CellIndeterminate)) and result.coordinate_failure is not None:
            searches.append(result.coordinate_failure)
        for search in searches:
            for observation in search.observations:
                if observation.dependency is None:
                    assert observation.selection_reason is None
                else:
                    assert observation.selection_reason in {
                        "mechanical-lowest",
                        "mechanical-midpoint",
                        "history",
                        "static-suspect",
                        "static-clean-neighbor",
                        "direct-existing",
                        "external-hint",
                        "current-upper",
                    }


class TestSearchCoordinator:
    def test_missing_highest_static_anchor_is_saved_without_observation_or_extra_process(self, tmp_path, run_cache):
        def verifier(vector, call):
            passed = int(vector[0].version) >= 2
            return VerifierRun(
                authoritative=(VerifierPass(terminal=NormalExit(exit_code=0)) if passed
                               else VerifierRejected(terminal=NormalExit(exit_code=1))),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if passed else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(fail_inspect_at={1}, verifier_handler=verifier)
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            assert result.final_vector == (VersionPin(name="demo-dep", version="2"),)
            assert [vector[0].version for vector in assembly.ty.vectors] == ["3", "1", "2"]
            assert [vector[0].version for vector in assembly.verifier.vectors] == ["3", "1", "2"]
            assert_public_direct_bound(result, floor="2", predecessor="1")
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,),
            )
            store = ReportStore()
            path = tmp_path / "omitted-guidance.json"
            store.write(path, report)
            original = path.read_bytes()
            restored = store.read(path)
            assert "static_scopes" not in restored._wire.model_dump(mode="json")
            assert "static_contents" not in restored._wire.model_dump(mode="json")
            assert_public_selection_reasons(restored)
            assert_guidance_journal_roundtrip(tmp_path, project, result)
            store.write(path, restored)
            assert path.read_bytes() == original
            assert all(not root.exists() for root in assembly.uv.environment_roots)
        finally:
            project.snapshot.close()

    def test_unmodeled_static_exception_does_not_interrupt_search(self, tmp_path, run_cache):
        def ty_handler(vector, call):
            raise RuntimeError("unmodeled ty observe")

        def verifier(vector, call):
            passed = int(vector[0].version) >= 2
            return VerifierRun(
                authoritative=(VerifierPass(terminal=NormalExit(exit_code=0)) if passed
                               else VerifierRejected(terminal=NormalExit(exit_code=1))),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if passed else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(ty_handler=ty_handler, verifier_handler=verifier)
        try:
            with pytest.warns(RuntimeWarning, match="static observation failed"):
                result = assembly.coordinator.search(
                    run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                    snapshot=project.snapshot, source_plan=project.source_plan,
                )
            assert isinstance(result, CellSuccess)
            assert result.final_vector == (VersionPin(name="demo-dep", version="2"),)
            verified = [vector[0].version for vector in assembly.verifier.vectors]
            assert verified[0] == "3"
            assert set(verified[1:]) == {"1", "2"}
            assert_public_direct_bound(result, floor="2", predecessor="1")
            reasons = {
                observation.selection_reason
                for observation in result.search.observations
                if observation.selection_reason is not None
            }
            assert not any(reason.startswith("static-") for reason in reasons)
            assert all(not root.exists() for root in assembly.uv.environment_roots)
        finally:
            project.snapshot.close()

    @pytest.mark.parametrize("mode", ["guided", "unavailable", "lower-unchanged", "capture-unavailable", "prepare-unavailable"])
    def test_local_static_phase_guides_real_oracle_and_reuses_prepared_inputs(self, tmp_path, run_cache, mode):
        from pf.schemas.evaluation import TyDiagnostic

        events = []
        diagnostic = TyDiagnostic(identity="snapshot|src/demo/__init__.py|1|1|example", origin="snapshot",
                                  path="src/demo/__init__.py", line=1, column=1, code="example",
                                  severity="error", message="static suspicion")

        def ty(vector, call):
            version = int(vector[0].version)
            events.append(("ty", version))
            if mode == "unavailable" and version == 2:
                return ToolFailure(cause="TOOL_FAILURE", stage="ty", process=successful_process(exit_code=2))
            regression = version < 3 and mode != "lower-unchanged"
            return TyCheck(process=successful_process(exit_code=1 if regression else 0),
                           diagnostics=(diagnostic,) if regression else ())

        def verifier(vector, call):
            version = int(vector[0].version)
            events.append(("verifier", version))
            return VerifierRun(
                authoritative=(VerifierPass(terminal=NormalExit(exit_code=0)) if version >= 2
                               else VerifierRejected(terminal=NormalExit(exit_code=1))),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if version >= 2 else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(
            ty_handler=ty, verifier_handler=verifier,
            fail_inspect_version_once="2" if mode == "capture-unavailable" else None,
        )
        if mode == "prepare-unavailable":
            assembly.uv.install_failures_by_vector[(VersionPin(name="demo-dep", version="1"),)] = OperationFailureResult(
                failure=ExecutionFailure(terminal=NormalExit(exit_code=2), attribution=Unattributed()),
                stage="install-project", process=successful_process(exit_code=2),
            )
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            expected = {
                "guided": [("ty", 1), ("ty", 2), ("verifier", 2), ("verifier", 1)],
                "unavailable": [("ty", 1), ("ty", 2), ("verifier", 1), ("verifier", 2)],
                "lower-unchanged": [("ty", 1), ("verifier", 1), ("ty", 2), ("verifier", 2)],
                "capture-unavailable": [("ty", 1), ("verifier", 1), ("ty", 2), ("verifier", 2)],
                "prepare-unavailable": [("ty", 2), ("verifier", 2)],
            }
            assert events == [("ty", 3), ("verifier", 3), *expected[mode]]
            assert assembly.uv.resolutions == ["highest", "exact-selection", "exact-selection"]
            assert all(not root.exists() for root in assembly.uv.environment_roots)
            assert_public_direct_bound(result, floor="2", predecessor="1")
            reasons = {
                observation.selection_reason
                for observation in result.search.observations
                if observation.selection_reason is not None
            }
            if mode == "guided":
                assert "static-suspect" in reasons
            else:
                assert not any(reason.startswith("static-") for reason in reasons)
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,),
            )
            path = tmp_path / "guided-report.json"
            store = ReportStore()
            store.write(path, report)
            original = path.read_bytes()
            restored = store.read(path)
            assert_public_selection_reasons(restored)
            assert_guidance_journal_roundtrip(tmp_path, project, result)
            store.write(path, restored)
            assert path.read_bytes() == original
            import json
            from jsonschema import Draft202012Validator
            schema = json.loads(Path("docs/schemas/package-floor-v1.schema.json").read_text())
            assert [error.message for error in Draft202012Validator(schema).iter_errors(json.loads(original))] == []
        finally:
            project.snapshot.close()

    def test_static_slice_does_not_retain_every_inspected_environment(
        self, tmp_path, run_cache
    ):
        from pf.schemas.evaluation import TyDiagnostic

        diagnostic = TyDiagnostic(
            identity="snapshot|src/demo/__init__.py|1|1|example",
            origin="snapshot",
            path="src/demo/__init__.py",
            line=1,
            column=1,
            code="example",
            severity="error",
            message="static suspicion",
        )
        versions = tuple(str(index) for index in range(1, 17))

        def ty(vector, call):
            version = int(vector[0].version)
            return TyCheck(
                process=successful_process(exit_code=1 if version < 12 else 0),
                diagnostics=(diagnostic,) if version < 12 else (),
            )

        def verifier(vector, call):
            version = int(vector[0].version)
            return VerifierRun(
                authoritative=(
                    VerifierPass(terminal=NormalExit(exit_code=0))
                    if version >= 10
                    else VerifierRejected(terminal=NormalExit(exit_code=1))
                ),
                diagnostics=VerifierDiagnostics(
                    process=successful_process(exit_code=0 if version >= 10 else 1)
                ),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(
            highest=(VersionPin(name="demo-dep", version="16"),),
            candidate_versions=versions,
            ty_handler=ty,
            verifier_handler=verifier,
        )
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache,
                package=project.package,
                cell=project.package.cells[0],
                snapshot=project.snapshot,
                source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            peak = max(
                (sum(states) for _, states in assembly.uv.resolution_root_states),
                default=0,
            )
            assert peak <= 4
            assert result.final_vector[0].name == "demo-dep"
            assert all(not root.exists() for root in assembly.uv.environment_roots)
        finally:
            project.snapshot.close()

    def test_oracle_selection_records_clean_neighbor_after_suspect_rejection(self, tmp_path, run_cache):
        from pf.schemas.evaluation import TyDiagnostic

        events = []
        diagnostic = TyDiagnostic(identity="snapshot|src/demo/__init__.py|1|1|example", origin="snapshot",
                                  path="src/demo/__init__.py", line=1, column=1, code="example",
                                  severity="error", message="static suspicion")

        def ty(vector, call):
            version = int(vector[0].version)
            events.append(("ty", version))
            return TyCheck(process=successful_process(exit_code=1 if version < 2 else 0),
                           diagnostics=(diagnostic,) if version < 2 else ())

        def verifier(vector, call):
            version = int(vector[0].version)
            events.append(("verifier", version))
            return VerifierRun(
                authoritative=(VerifierPass(terminal=NormalExit(exit_code=0)) if version >= 2
                               else VerifierRejected(terminal=NormalExit(exit_code=1))),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if version >= 2 else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(ty_handler=ty, verifier_handler=verifier)
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            assert result.final_vector == (VersionPin(name="demo-dep", version="2"),)
            assert events == [("ty", 3), ("verifier", 3), ("ty", 1), ("ty", 2), ("verifier", 1), ("verifier", 2)]
            assert_public_direct_bound(result, floor="2", predecessor="1")
            suspects = [
                observation for observation in result.search.observations
                if observation.selection_reason == "static-suspect"
            ]
            cleans = [
                observation for observation in result.search.observations
                if observation.selection_reason == "static-clean-neighbor"
            ]
            assert len(suspects) == 1 and len(cleans) == 1
            assert suspects[0].candidate_version == "1"
            assert cleans[0].candidate_version == "2"
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,),
            )
            store = ReportStore()
            path = tmp_path / "clean-neighbor.json"
            store.write(path, report)
            restored = store.read(path)
            assert_public_selection_reasons(restored)
            assert_guidance_journal_roundtrip(tmp_path, project, result)
            failure_id = next(
                item.failure_id for item in result.failure_records
                if item.cause == "VERIFIER_EXITED_NONZERO"
            )
            store.write(tmp_path / "package-floor.json", restored)
            from io import StringIO
            from rich.console import Console
            from pf.terminal import TerminalPresenter

            class Logs:
                def __init__(self):
                    self.journal_reads = []

                def lookup(self, report_generation_id, failure_id):
                    return None

                def lookup_run(self, run_id, failure_id):
                    return None

                def lookup_static(self, run_id, scope_ref, producer_ref):
                    return None

                def lookup_report_static(self, report_generation_id, scope_ref, producer_ref):
                    return None

                def read_latest_journal(self, package):
                    self.journal_reads.append(package)
                    return None

                def read_tail(self, path):
                    return ()

            logs = Logs()
            diagnosis = DiagnoseCommandWorkflow(
                discovery=ProjectDiscovery(), reports=ReportStore(), logs=logs,
            ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))
            assert logs.journal_reads == []
            with pytest.raises(DiagnoseNotFoundError):
                DiagnoseCommandWorkflow(
                    discovery=ProjectDiscovery(), reports=ReportStore(), logs=logs,
                ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id="failure-aaaaaaaaaaaaaaaa"))
            stdout = StringIO()
            assert TerminalPresenter(
                stdout=Console(file=stdout, force_terminal=False, color_system=None),
                stderr=Console(file=StringIO(), force_terminal=False, color_system=None),
                root=tmp_path,
            ).render_diagnose(diagnosis) == 0
            rendered = visible_cli_text(stdout.getvalue())
            assert "Related static evidence" not in rendered
            (tmp_path / "package-floor.json").unlink()
            from pf.runlog import RunLogStore
            journaled = DiagnoseCommandWorkflow(
                discovery=ProjectDiscovery(), reports=ReportStore(),
                logs=RunLogStore(root=tmp_path, run_id="guided"),
            ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))
            assert journaled.source == "journal"
        finally:
            project.snapshot.close()

    def test_same_ty_key_is_collected_once_and_global_local_deltas_differ(self, tmp_path, run_cache):
        from pf.schemas.evaluation import TyDiagnostic

        diagnostic = TyDiagnostic(identity="snapshot|src/demo/__init__.py|1|1|example", origin="snapshot",
                                  path="src/demo/__init__.py", line=1, column=1, code="example",
                                  severity="error", message="static suspicion")
        events = []

        def ty(vector, call):
            events.append(("ty", int(vector[0].version)))
            version = int(vector[0].version)
            return TyCheck(process=successful_process(exit_code=1 if version < 3 else 0),
                           diagnostics=(diagnostic,) if version < 3 else ())

        def verifier(vector, call):
            version = int(vector[0].version)
            return VerifierRun(
                authoritative=(VerifierPass(terminal=NormalExit(exit_code=0)) if version >= 2
                               else VerifierRejected(terminal=NormalExit(exit_code=1))),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if version >= 2 else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(ty_handler=ty, verifier_handler=verifier)
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            ty_versions = [version for kind, version in events if kind == "ty"]
            assert ty_versions == sorted(set(ty_versions), key=ty_versions.index)
            peak = max((sum(states) for _, states in assembly.uv.resolution_root_states), default=0)
            assert peak <= 3
        finally:
            project.snapshot.close()

    def test_prepare_unavailable_diagnose_does_not_fabricate_ty_check(self, tmp_path, run_cache):
        def verifier(vector, call):
            version = int(vector[0].version)
            return VerifierRun(
                authoritative=(VerifierPass(terminal=NormalExit(exit_code=0)) if version >= 2
                               else VerifierRejected(terminal=NormalExit(exit_code=1))),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if version >= 2 else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(verifier_handler=verifier)
        assembly.uv.install_failures_by_vector[(VersionPin(name="demo-dep", version="1"),)] = OperationFailureResult(
            failure=ExecutionFailure(terminal=NormalExit(exit_code=2), attribution=Unattributed()),
            stage="install-project", process=successful_process(exit_code=2),
        )
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            assert_public_direct_bound(result, floor="2", predecessor="1")
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,),
            )
            path = tmp_path / "prepare-unavailable.json"
            store = ReportStore()
            store.write(path, report)
            forged = json.loads(path.read_text())
            if forged["projections"]:
                forged["projections"][0]["floors"][0]["version"] = "1"
                path.write_text(json.dumps(forged))
                with pytest.raises(ConfigurationError):
                    store.read(path)
        finally:
            project.snapshot.close()

    @pytest.mark.parametrize("indeterminate", (False, True))
    def test_search_stops_on_the_real_highest_verification_outcome(
        self, run_cache,
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

        result = assembly.coordinator.search(run_cache=run_cache,
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
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", search_space="all")
        assembly = evaluation_assembly(candidate_versions=())

        result = assembly.coordinator.search(run_cache=run_cache,
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
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(
            candidate_error=InfrastructureError("registry unavailable")
        )

        result = assembly.coordinator.search(run_cache=run_cache,
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

    def test_search_classifies_an_unclosed_baseline_artifact_as_source_failure(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", search_space="all")
        assembly = evaluation_assembly(candidate_versions=("1", "2"))
        assembly.candidates.baseline.clear()

        result = assembly.coordinator.search(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellIndeterminate)
        assert result.phase == "candidate-discovery"
        assert result.failure_records[0].cause == "SOURCE_FAILURE"

    def test_search_reuses_an_in_candidate_baseline_as_the_final_pass(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project", search_space="all")
        assembly = evaluation_assembly(candidate_versions=("3",))

        result = assembly.coordinator.search(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        assert result.final_vector == result.baseline.proposal.managed_vector
        assert result.final_evaluation == result.baseline
        assert {
            observation.dependency for observation in result.search.observations
        } == {None, "demo-dep"}
        assert all(
            isinstance(observation.evidence, ProbePass)
            and observation.evidence.attempt == result.baseline_attempt
            for observation in result.search.observations
        )
        assert not assembly.uv.exact_selections
        assert_public_direct_bound(result, floor="3", predecessor=None, predecessor_required=False)

    @pytest.mark.parametrize("runtime_diagnostics", (False, True))
    @pytest.mark.parametrize("failed_collections", ("none", "capture", "candidates", "all"))
    def test_search_returns_a_runtime_backed_floor_with_closed_public_evidence(
        self, run_cache,
        tmp_path: Path,
        runtime_diagnostics: bool,
        failed_collections: str,
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
            ty_handler=lambda vector, call: (
                TyCheck(process=successful_process(), diagnostics=())
                if failed_collections == "none" or (call == 1 and failed_collections == "candidates") or (call != 1 and failed_collections == "capture")
                else ToolFailure(
                    cause="TOOL_FAILURE",
                    stage="ty",
                    process=successful_process(exit_code=2),
                )
            ),
            diagnostics=diagnostics,
            events=activity,
        )

        result = assembly.coordinator.search(run_cache=run_cache,
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
        assert len(direct) == len(observations)
        baseline_observations = tuple(
            observation
            for observation in observations
            if isinstance(observation.evidence, ProbePass)
            and observation.evidence.attempt == result.baseline_attempt
        )
        assert baseline_observations
        assert baseline_observations[0].vector == result.baseline.proposal.managed_vector
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

        report = PackageReportBuilder().build(
            package=project.package,
            source_plan=project.source_plan,
            source_snapshot=project.snapshot.identity,
            cell_results=(result,),
        )
        path = tmp_path / "package-floor.json"
        store = ReportStore()
        store.write(path, report)
        restored = store.read(path)
        assert restored == report
        original_bytes = path.read_bytes()
        store.write(path, restored)
        assert path.read_bytes() == original_bytes
        assert_public_selection_reasons(restored)
        if failed_collections in {"candidates", "all"}:
            assert failure.authority.kind == "configured-verifier"
            assert any(pin.version == "1" for vector in assembly.verifier.vectors for pin in vector)
            assert any(pin.version == "2" for vector in assembly.verifier.vectors for pin in vector)

    def test_search_uses_baseline_selection_for_a_narrow_inactive_coordinate(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(
            tmp_path / "project",
            dependencies=("alpha>=1", "charset-normalizer>=1.3.9"),
            search_space="all",
            search_configuration="""
search-resolution = "patch"
[[tool.pf.dep]]
name = "charset-normalizer"
search-space = "minors[declaration]"
search-resolution = "patch"
""",
        )
        highest = (
            VersionPin(name="alpha", version="3"),
            VersionPin(name="charset-normalizer", version="3.5.1"),
        )

        def verifier(
            vector: tuple[VersionPin, ...],
            call: int,
        ) -> VerifierRun:
            del call
            versions = {pin.name: pin.version for pin in vector}
            if int(versions["alpha"]) >= 2:
                return _pass_run()
            return VerifierRun(
                authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=1)),
            )

        assembly = evaluation_assembly(
            highest=highest,
            candidate_versions_by_dependency={
                "alpha": ("1", "2", "3"),
                "charset-normalizer": ("1.3.9", "1.3.10", "3.5.1"),
            },
            verifier_handler=verifier,
        )

        result = assembly.coordinator.search(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        charset = next(
            snapshot
            for snapshot in result.candidate_snapshots
            if snapshot.dependency == "charset-normalizer"
        )
        assert charset.baseline_selection.version == "3.5.1"
        assert "3.5.1" not in {
            candidate.version for candidate in charset.candidates
        }
        assert any(
            {
                candidate.dependency: candidate.version for candidate in selection
            }
            == {"alpha": "2", "charset-normalizer": "3.5.1"}
            for selection in assembly.uv.exact_selections
        )

    def test_full_result_cache_hit_is_registered_in_another_slice_without_activity(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(
            tmp_path / "project",
            dependencies=("alpha", "beta"),
            search_space="all",
        )
        highest = (
            VersionPin(name="alpha", version="3"),
            VersionPin(name="beta", version="3"),
        )
        activity = RecordingActivity()

        def verifier(
            vector: tuple[VersionPin, ...],
            call: int,
        ) -> VerifierRun:
            del call
            versions = {pin.name: int(pin.version) for pin in vector}
            if versions["alpha"] >= 2 and versions["beta"] >= 2:
                return _pass_run()
            return VerifierRun(
                authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=1)),
            )

        assembly = evaluation_assembly(
            highest=highest,
            candidate_versions_by_dependency={
                "alpha": ("1", "2", "3"),
                "beta": ("1", "2", "3"),
            },
            verifier_handler=verifier,
            events=activity,
        )

        result = assembly.coordinator.search(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        mixed = {"alpha": "2", "beta": "3"}
        assert sum(
            {
                candidate.dependency: candidate.version for candidate in selection
            }
            == mixed
            for selection in assembly.uv.exact_selections
        ) == 1
        assert not any(
            isinstance(event, CellContextEvent)
            and isinstance(event.detail, SearchProbeDetailIdentity)
            and event.detail.dependency == "beta"
            and event.detail.version == "3"
            for event in activity.events
        )

    def test_search_maps_a_vector_outside_frozen_selection_to_cell_invariant(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(verifier_handler=threshold_verifier)

        class OutsideSelection(CoordinateSearch):
            def minimize(self, **kwargs):
                evaluator = kwargs["evaluator"]
                evaluator.evaluate(
                    (VersionPin(name="demo-dep", version="999"),)
                )
                raise AssertionError("selection must fail before evaluation")

        coordinator = SearchCoordinator(
            environments=assembly.environments,
            candidates=assembly.candidate_builder,
            static=assembly.static,
            full=assembly.runtime,
            highest=assembly.highest,
            coordinate_search=OutsideSelection(),
        )

        result = coordinator.search(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellIndeterminate)
        assert result.failure_records[-1].scope.kind == "cell"
        assert result.failure_records[-1].cause == "INTERNAL_INVARIANT"
        assert result.failure_records[-1].stage == "candidate-selection"

    def test_search_reuses_a_full_probe_for_final_evaluation(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(verifier_handler=threshold_verifier)

        result = assembly.coordinator.search(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        assert [vector[0].version for vector in assembly.uv.install_vectors] == ["3", "1", "1", "2"]
        assert assembly.verifier.vectors == [
            (VersionPin(name="demo-dep", version="3"),),
            (VersionPin(name="demo-dep", version="1"),),
            (VersionPin(name="demo-dep", version="2"),),
        ]
        assert len(assembly.candidates.queries) == 1
        assert "lowest-direct" not in assembly.uv.resolutions
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_search_maps_every_exact_probe_to_the_frozen_candidate_artifact(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        assembly = evaluation_assembly(verifier_handler=threshold_verifier)

        result = assembly.coordinator.search(run_cache=run_cache,
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
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")
        diagnostics = RecordingDiagnostics()
        assembly = evaluation_assembly(
            verifier_handler=threshold_verifier,
            diagnostics=diagnostics,
        )
        failed_vector = (VersionPin(name="demo-dep", version="1"),)
        assembly.uv.install_failures_by_vector[failed_vector] = OperationFailureResult(
            failure=StructuredOperationFailure(fact=SourceAccessFailedFact(), terminal=None),
            stage="install-project",
        )

        result = assembly.coordinator.search(run_cache=run_cache,
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
        assert evidence.cause == "SOURCE_FAILURE"
        assert result.failure_id == evidence.failure_id
        assert len(diagnostics.events) == 1
        assert diagnostics.events[0].failure.failure_id == evidence.failure_id
        assert assembly.uv.install_vectors.count(failed_vector) == 1
        assert all(not root.exists() for root in assembly.uv.environment_roots)

    def test_search_retains_terminal_runtime_indeterminate_diagnostics(
        self, run_cache,
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
                return _pass_run()
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

        result = assembly.coordinator.search(run_cache=run_cache,
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

    def test_search_reuses_failed_cases_on_the_same_coordinate(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        project = evaluation_project(tmp_path / "project")

        def handler(vector: tuple[VersionPin, ...], call: int) -> VerifierRun:
            del call
            version = int(vector[0].version)
            if version >= 2:
                return _pass_run()
            return VerifierRun(
                authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
                failed_case_additions=("test_example.py::test_bad",),
            )

        assembly = evaluation_assembly(verifier_handler=handler)

        result = assembly.coordinator.search(run_cache=run_cache,
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
        )

        assert isinstance(result, CellSuccess)
        nodeids = tuple(
            request.failed_case_nodeids for request in assembly.verifier.requests
        )
        assert () in nodeids
        assert ("test_example.py::test_bad",) in nodeids
        passing = (VersionPin(name="demo-dep", version="2"),)
        assert assembly.verifier.vectors.count(passing) == 1

    def test_search_isolates_failed_cases_across_coordinates_and_runs(
        self, run_cache,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["alpha-dep", "beta-dep"]

[dependency-groups]
test = []

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["python", "-c", "pass"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=root).target
        snapshot = SnapshotBuilder.without_processes().build(root)
        project_source = SourcePlan.for_package(package, "SEARCH")

        def handler(vector: tuple[VersionPin, ...], call: int) -> VerifierRun:
            del call
            versions = {pin.name: int(pin.version) for pin in vector}
            if versions.get("alpha-dep", 3) < 2:
                return VerifierRun(
                    authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
                    failed_case_additions=("test_alpha.py::test_bad",),
                )
            if versions.get("beta-dep", 3) < 2:
                return VerifierRun(
                    authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
                    failed_case_additions=("test_beta.py::test_bad",),
                )
            return _pass_run()

        pins = (
            VersionPin(name="alpha-dep", version="3"),
            VersionPin(name="beta-dep", version="3"),
        )
        assembly = evaluation_assembly(
            highest=pins,
            lowest=pins,
            verifier_handler=handler,
        )

        first = assembly.coordinator.search(run_cache=run_cache,
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=project_source,
        )
        first_count = len(assembly.verifier.requests)
        with TyCheckCache() as other_cache:
            second = assembly.coordinator.search(run_cache=other_cache,
                package=package,
                cell=package.cells[0],
                snapshot=snapshot,
                source_plan=project_source,
            )

        assert isinstance(first, CellSuccess)
        assert isinstance(second, CellSuccess)
        paired = list(
            zip(assembly.verifier.vectors[:first_count], assembly.verifier.requests[:first_count])
        )
        first_beta = [
            request.failed_case_nodeids
            for vector, request in paired
            if any(pin.name == "beta-dep" and pin.version == "1" for pin in vector)
            and all(pin.version != "1" or pin.name == "beta-dep" for pin in vector)
        ]
        assert first_beta
        assert first_beta[0] == ()
        assert any(
            request.failed_case_nodeids == ("test_alpha.py::test_bad",)
            for request in assembly.verifier.requests[:first_count]
        )
        assert assembly.verifier.requests[first_count].failed_case_nodeids == ()


class TestSearchSpaceBuildDisposition:
    @pytest.mark.parametrize("space", ["all", "majors[baseline-1:]"])
    def test_prepare_rejection_continues_within_configured_space(self, run_cache, tmp_path: Path, space: str) -> None:
        project = evaluation_project(tmp_path / "project", search_space=space)
        assembly = evaluation_assembly()
        failing = (VersionPin(name="demo-dep", version="1"),)
        assembly.uv.install_failures_by_vector[failing] = OperationFailureResult(
            failure=ExecutionFailure(terminal=NormalExit(exit_code=2), attribution=Unattributed()),
            stage="install-project", process=successful_process(exit_code=2),
        )
        try:
            result = assembly.coordinator.search(run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                                                snapshot=project.snapshot, source_plan=project.source_plan)
            if space == "all":
                assert isinstance(result, CellSuccess)
                assert result.failure_records[0].cause == "INSTALLATION_FAILED"
                assert result.final_vector == (VersionPin(name="demo-dep", version="2"),)
                assert result.search.boundaries[0].predecessor == "1"
                assert failing in assembly.uv.install_vectors
            else:
                assert isinstance(result, CellSuccess)
                assert result.final_vector == (VersionPin(name="demo-dep", version="2"),)
                assert failing not in assembly.uv.install_vectors
                assert result.final_evaluation.proposal.managed_vector == result.final_vector
                assert result.search.boundaries[0].predecessor is None
        finally:
            project.snapshot.close()

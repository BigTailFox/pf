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


def assert_direct_bound_skip(scope, *, floor, predecessor, predecessor_required=True):
    assert scope.skips
    skip = scope.skips[-1]
    assert skip.reason == "direct-bound"
    actual = next(pin.version for pin in skip.proposal.managed_vector if pin.name == skip.candidates.dependency)
    assert actual == floor
    assert skip.predecessor == predecessor
    if predecessor_required:
        assert skip.predecessor_failure_id
    else:
        assert skip.predecessor_failure_id is None
    assert floor in skip.window
    return skip


def assert_selection_identity_is_dynamic_only(scope):
    by_attempt = {}
    for selection in scope.selections:
        by_attempt.setdefault(selection.attempt.attempt_id, []).append(selection)
        assert selection.attempt.identity.execution_policy_identity
        assert "static-" not in selection.attempt.identity.execution_policy_identity
    reused = [group for group in by_attempt.values() if len(group) > 1]
    assert reused
    assert any(len({item.request.selection_reason for item in group}) > 1 for group in reused)


def assert_guidance_journal_roundtrip(tmp_path, project, result, scope):
    from pf.runlog import RunLogStore
    from pf.schemas.journal import (VerificationJournal, VerificationJournalEntry,
                                    VerificationPackagePolicy, JournalStaticScope)
    from pf.schemas.evaluation import AttemptFailureScope
    logs = RunLogStore(root=tmp_path, run_id="guided")
    journal = VerificationJournal(
        run_id=logs.run_id, command="search", source_snapshot_digest=project.snapshot.identity.digest,
        package_policies=(VerificationPackagePolicy(package=project.package.name,
                          execution_policy_identity=result.baseline.proposal.policy_identity),),
        entries=tuple(VerificationJournalEntry(
            package=project.package.name, cell=project.package.cells[0], role="probe", failure=failure,
            attempt=failure.scope.attempt if isinstance(failure.scope, AttemptFailureScope) else None,
        ) for failure in result.failure_records),
        static_scopes=(JournalStaticScope(run_id=logs.run_id, scope=scope),),
    )
    logs.write_journal(journal)
    assert logs.read_journal(logs.run_id) == journal


class TestSearchCoordinator:
    def test_missing_highest_static_anchor_is_saved_without_observation_or_extra_process(self, tmp_path, run_cache):
        from scripted_static import ScriptedStaticRequests
        from pf.schemas.static import StaticContentUnavailable

        class Requests(ScriptedStaticRequests):
            def capture(self, prepared, **kwargs):
                if prepared.attempt.identity.requested_resolution == "highest":
                    return StaticContentUnavailable(detail="unreadable-content")
                return super().capture(prepared, **kwargs)

        def verifier(vector, call):
            passed = int(vector[0].version) >= 2
            return VerifierRun(
                authoritative=(VerifierPass(terminal=NormalExit(exit_code=0)) if passed
                               else VerifierRejected(terminal=NormalExit(exit_code=1))),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if passed else 1)),
            )

        project = evaluation_project(tmp_path)
        assembly = evaluation_assembly(static_requests=Requests(), verifier_handler=verifier)
        try:
            result = assembly.coordinator.search(
                run_cache=run_cache, package=project.package, cell=project.package.cells[0],
                snapshot=project.snapshot, source_plan=project.source_plan,
            )
            assert isinstance(result, CellSuccess)
            assert result.final_vector == (VersionPin(name="demo-dep", version="2"),)
            assert [vector[0].version for vector in assembly.ty.vectors] == ["1", "2"]
            assert [vector[0].version for vector in assembly.verifier.vectors] == ["3", "1", "2"]
            scope = run_cache.snapshot(project.package.cells[0])
            assert scope.searches == ()
            assert len(scope.omissions) == 1
            omitted = scope.omissions[0]
            assert omitted.reason == "anchor-unavailable"
            assert omitted.proposal == result.baseline.proposal
            assert omitted.window == ("1", "2", "3")
            assert omitted.observed_pass_refs == ()
            assert len(scope.facts) == 2 and len(scope.passes) == 1
            assert all(item.request.selection_reason in {"mechanical", "history"} for item in scope.selections)
            assert all(item.request.static_search_ref is None for item in scope.selections)
            skip = assert_direct_bound_skip(scope, floor="2", predecessor="1")
            assert skip.observed_search_refs == ()
            assert_selection_identity_is_dynamic_only(scope)
            assert type(scope).model_validate_json(scope.model_dump_json()) == scope
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,), static_scopes=(scope,),
            )
            store = ReportStore()
            path = tmp_path / "omitted-guidance.json"
            store.write(path, report)
            original = path.read_bytes()
            restored = store.read(path)
            assert restored.static_scopes[0].omissions == scope.omissions
            assert restored.static_scopes[0].selections == scope.selections
            assert restored.static_scopes[0].skips == scope.skips
            assert_guidance_journal_roundtrip(tmp_path, project, result, scope)
            store.write(path, restored)
            assert path.read_bytes() == original
            assert all(not root.exists() for root in assembly.uv.environment_roots)
        finally:
            project.snapshot.close()

    @pytest.mark.parametrize("mode", ["guided", "unavailable", "lower-unchanged", "capture-unavailable", "prepare-unavailable"])
    def test_local_static_phase_guides_real_oracle_and_reuses_prepared_inputs(self, tmp_path, run_cache, mode):
        from pf.schemas.evaluation import TyDiagnostic
        from pf.schemas.static_comparison import SliceComparisonContext

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
        from scripted_static import ScriptedStaticRequests
        from pf.schemas.static import StaticContentUnavailable

        class Requests(ScriptedStaticRequests):
            def __init__(self):
                self.failed = False

            def capture(self, prepared, **kwargs):
                if mode == "capture-unavailable" and prepared.proposal.managed_vector[0].version == "2" and not self.failed:
                    self.failed = True
                    return StaticContentUnavailable(detail="unreadable-content")
                return super().capture(prepared, **kwargs)

        assembly = evaluation_assembly(ty_handler=ty, verifier_handler=verifier, static_requests=Requests())
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
            scope = run_cache.snapshot(project.package.cells[0])
            local = [item for item in scope.comparisons if isinstance(item.context, SliceComparisonContext)]
            assert len(local) == (1 if mode == "prepare-unavailable" else
                                  2 if mode in {"lower-unchanged", "capture-unavailable"} else 3)
            if mode == "guided":
                assert {item.result.state for item in local} == {"STATIC_UNCHANGED", "STATIC_REGRESSION"}
            elif mode == "unavailable":
                assert any(item.result.status == "UNAVAILABLE" for item in local)
            elif mode == "lower-unchanged":
                assert all(item.result.state == "STATIC_UNCHANGED" for item in local)
            assert len(scope.passes) == 2
            assert len(scope.searches) == 1
            audit = scope.searches[0]
            assert audit.reason == {"guided": None, "unavailable": "static-unavailable",
                                    "lower-unchanged": "lower-unchanged", "capture-unavailable": "static-unavailable", "prepare-unavailable": "static-unavailable"}[mode]
            assert (audit.hint is not None) == (mode == "guided")
            if mode == "capture-unavailable":
                assert audit.points[-1].comparison_identity is None
                assert audit.points[-1].unavailable.proposal is not None
                assert audit.points[-1].unavailable.unavailable.detail == "unreadable-content"
            if mode == "prepare-unavailable":
                unavailable = audit.points[-1].unavailable
                assert unavailable.proposal is None
                assert unavailable.failure.stage == "install-project"
                assert unavailable.process is not None
                assert audit.points[-1].comparison_identity is None
            saved = scope.model_dump_json()
            assert type(scope).model_validate_json(saved).model_dump_json() == saved
            skip = assert_direct_bound_skip(scope, floor="2", predecessor="1")
            assert skip.observed_search_refs == (audit.ref,)
            assert_selection_identity_is_dynamic_only(scope)
            if mode == "guided":
                suspects = [item for item in scope.selections if item.request.selection_reason == "static-suspect"]
                assert len(suspects) == 1
                assert suspects[0].request.candidate_version == "2"
                assert suspects[0].request.static_search_ref == audit.ref
                assert suspects[0].status == "PASS" and suspects[0].reused is False
                assert audit.ref in suspects[0].observed_search_refs
                assert any(
                    item.reused and item.attempt.attempt_id == suspects[0].attempt.attempt_id
                    and item.request.selection_reason != "static-suspect"
                    for item in scope.selections
                )
                assert any(item.request.selection_reason == "history" and item.reused for item in scope.selections)
            else:
                assert all(not item.request.selection_reason.startswith("static-") for item in scope.selections)
                assert all(item.request.static_search_ref is None for item in scope.selections)
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,), static_scopes=(scope,),
            )
            path = tmp_path / "guided-report.json"
            store = ReportStore()
            store.write(path, report)
            original = path.read_bytes()
            restored = store.read(path)
            assert restored.static_scopes[0].searches == scope.searches
            assert restored.static_scopes[0].selections == scope.selections
            assert restored.static_scopes[0].skips == scope.skips
            assert_guidance_journal_roundtrip(tmp_path, project, result, scope)
            store.write(path, restored)
            assert path.read_bytes() == original
            import json
            from jsonschema import Draft202012Validator
            schema = json.loads(Path("docs/schemas/package-floor-v1.schema.json").read_text())
            assert [error.message for error in Draft202012Validator(schema).iter_errors(json.loads(original))] == []
            extra_selection = scope.model_dump(mode="json")
            extra_selection["searches"][0]["candidates"]["selection"]["unrecognized_rule"] = True
            with pytest.raises(ValueError):
                type(scope).model_validate(extra_selection)
            if mode == "prepare-unavailable":
                wrong_terminal = scope.model_dump(mode="json")
                wrong_terminal["searches"][0]["points"][-1]["unavailable"]["process"]["exit_code"] = 0
                with pytest.raises(ValueError, match="static prepare process"):
                    type(scope).model_validate(wrong_terminal)
            forged = scope.model_dump(mode="json")
            if mode == "guided":
                forged["searches"][0]["hint"]["suspect_index"] = 1
            else:
                forged["searches"][0]["reason"] = "anchor-unavailable"
            with pytest.raises(ValueError, match="static (hint|search)"):
                type(scope).model_validate(forged)
            future = scope.model_dump(mode="json")
            future["skips"][0]["observed_search_refs"] = [*skip.observed_search_refs, "static-search-99"]
            with pytest.raises(ValueError, match="completed static search prefix"):
                type(scope).model_validate(future)
            if mode == "guided":
                detached = scope.model_dump(mode="json")
                index = next(
                    offset for offset, item in enumerate(detached["selections"])
                    if item["request"]["selection_reason"] == "static-suspect"
                )
                detached["selections"][index]["observed_search_refs"] = []
                with pytest.raises(ValueError, match="future static search"):
                    type(scope).model_validate(detached)
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
            scope = run_cache.snapshot(project.package.cells[0])
            search = scope.searches[0]
            assert search.hint is not None and search.hint.clean_is_anchor is False
            suspects = [item for item in scope.selections if item.request.selection_reason == "static-suspect"]
            cleans = [item for item in scope.selections if item.request.selection_reason == "static-clean-neighbor"]
            assert len(suspects) == 1 and len(cleans) == 1
            assert suspects[0].request.candidate_version == "1"
            assert suspects[0].status == "REJECTED" and suspects[0].reused is False
            assert cleans[0].request.candidate_version == "2"
            assert cleans[0].status == "PASS" and cleans[0].reused is False
            assert suspects[0].request.static_search_ref == cleans[0].request.static_search_ref == search.ref
            assert search.ref in suspects[0].observed_search_refs
            assert_direct_bound_skip(scope, floor="2", predecessor="1")
            assert_selection_identity_is_dynamic_only(scope)
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,), static_scopes=(scope,),
            )
            store = ReportStore()
            path = tmp_path / "clean-neighbor.json"
            store.write(path, report)
            restored = store.read(path)
            assert restored.static_scopes[0].selections == scope.selections
            assert_guidance_journal_roundtrip(tmp_path, project, result, scope)
            failure_id = next(
                item.failure_id for item in scope.selections
                if item.request.selection_reason == "static-suspect"
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
            assert [item.path for item in diagnosis.static_associations] == ["execution", "selection"]
            execution, selection = diagnosis.static_associations
            assert execution.fact_kind == "ty-check" and execution.diagnostic_count == 1
            assert any(item.kind == "SLICE" and item.state == "STATIC_REGRESSION" for item in execution.comparisons)
            assert selection.selection_reason == "static-suspect"
            assert selection.static_search_ref == search.ref
            assert any(item.state == "STATIC_REGRESSION" for item in selection.comparisons)
            highest_self = {
                item.identity for item in scope.comparisons
                if item.subject_ref == item.reference_ref == scope.highest_reference_ref
            }
            assert highest_self.isdisjoint({item.identity for item in execution.comparisons})
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
            assert "Related static evidence" in rendered
            assert "probed as static-suspect" in rendered
            assert "not the compatibility result" in rendered
            assert "SLICE vs S_slice:" in rendered
            assert "static process log is unavailable" in rendered
            (tmp_path / "package-floor.json").unlink()
            from pf.runlog import RunLogStore
            journaled = DiagnoseCommandWorkflow(
                discovery=ProjectDiscovery(), reports=ReportStore(),
                logs=RunLogStore(root=tmp_path, run_id="guided"),
            ).run(DiagnoseRequest(root=tmp_path.as_posix(), failure_id=failure_id))
            assert journaled.source == "journal"
            assert [item.path for item in journaled.static_associations] == ["execution", "selection"]
        finally:
            project.snapshot.close()

    def test_same_ty_key_is_collected_once_and_global_local_deltas_differ(self, tmp_path, run_cache):
        from pf.schemas.evaluation import TyDiagnostic
        from pf.schemas.static_comparison import SliceComparisonContext

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
            scope = run_cache.snapshot(project.package.cells[0])
            global_ids = {item.identity for item in scope.comparisons if not isinstance(item.context, SliceComparisonContext)}
            local_ids = {item.identity for item in scope.comparisons if isinstance(item.context, SliceComparisonContext)}
            assert global_ids and local_ids and global_ids.isdisjoint(local_ids)
            peak = max((sum(states) for _, states in assembly.uv.resolution_root_states), default=0)
            assert peak <= 3
        finally:
            project.snapshot.close()

    def test_prepare_unavailable_diagnose_does_not_fabricate_ty_check(self, tmp_path, run_cache):
        from pf.static_association import diagnose_static_associations

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
            scope = run_cache.snapshot(project.package.cells[0])
            prepare = next(point.unavailable for search in scope.searches for point in search.points
                           if point.unavailable is not None and point.unavailable.proposal is None)
            assert prepare.proposal is None
            skip = assert_direct_bound_skip(scope, floor="2", predecessor="1")
            failure = next(item for item in result.failure_records if item.failure_id == skip.predecessor_failure_id)
            associations = diagnose_static_associations(failure, (scope,))
            assert all(item.fact_kind != "ty-check" for item in associations)
            report = PackageReportBuilder().build(
                package=project.package, source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity, cell_results=(result,), static_scopes=(scope,),
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
        scope = run_cache.snapshot(project.package.cells[0])
        assert_direct_bound_skip(scope, floor="3", predecessor=None, predecessor_required=False)
        assert scope.skips[-1].window == ("3",)

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

        scope = run_cache.snapshot(project.package.cells[0])
        report = PackageReportBuilder().build(
            package=project.package,
            source_plan=project.source_plan,
            source_snapshot=project.snapshot.identity,
            cell_results=(result,),
            static_scopes=(scope,),
        )
        path = tmp_path / "package-floor.json"
        store = ReportStore()
        store.write(path, report)
        restored = store.read(path)
        assert restored == report
        original_bytes = path.read_bytes()
        store.write(path, restored)
        assert path.read_bytes() == original_bytes
        assert restored.static_scopes == (scope,)
        comparisons = {
            scope.consumer(item.subject_ref).preparation.proposal.proposal_id: item
            for item in scope.comparisons
        }
        final_static = comparisons[result.final_evaluation.proposal.proposal_id]
        assert final_static.context.kind == "GLOBAL"
        assert final_static.reference_ref == scope.highest_reference_ref
        if failed_collections in {"candidates", "all"}:
            assert final_static.result.status == "UNAVAILABLE"
            assert rejection.evaluation is not None
            assert comparisons[rejection.evaluation.proposal.proposal_id].result.status == "UNAVAILABLE"
            assert final_static.result.reason == "exit-code"
            assert failure.authority.kind == "configured-verifier"
            assert any(pin.version == "1" for vector in assembly.verifier.vectors for pin in vector)
            assert any(pin.version == "2" for vector in assembly.verifier.vectors for pin in vector)
        elif failed_collections == "capture":
            assert final_static.result.status == "UNCOMPARED"
            assert final_static.result.reason == "reference-unavailable"
        else:
            assert final_static.result.status == "COMPARED"
            assert final_static.result.state == "STATIC_UNCHANGED"

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
            return VerifierRun(
                authoritative=(
                    VerifierPass(terminal=NormalExit(exit_code=0))
                    if int(versions["alpha"]) >= 2
                    else VerifierRejected(terminal=NormalExit(exit_code=1))
                )
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
            return VerifierRun(
                authoritative=(
                    VerifierPass(terminal=NormalExit(exit_code=0))
                    if versions["alpha"] >= 2 and versions["beta"] >= 2
                    else VerifierRejected(terminal=NormalExit(exit_code=1))
                )
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
                return VerifierRun(
                    authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
                )
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
            return VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0))
            )

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
        second = assembly.coordinator.search(run_cache=run_cache,
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

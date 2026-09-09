from __future__ import annotations

from scripted_static import collect_highest
from pf.static_cache import RunTyFactRef

import pytest

from static_fixtures import static_request as static_request
from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process
from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import ConfiguredVerifier
from pf.adapters.ty import TyAdapter
from pf.environment import HighestResolution, PreparedEnvironment
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    NormalExit, ToolFailure, TyCheck,
    VerifierDiagnostics, VerifierPass, VerifierRun,
)
from pf.schemas.static_scope import StaticScopeEvidence
from pf.schemas.ty_fact import TyCheckUnavailable
from pf.static_cache import TyCheckCache
from pf.static_request import StaticRequestFactory


class TestRuntimeStaticPassRegistration:
    @pytest.mark.process
    def test_real_verifier_pass_is_saved_with_its_collected_consumer(self, static_request):
        prepared = static_request.prepared
        package = ProjectLoader().load(root=prepared.package_root).target
        runner = SubprocessRunner()
        static = StaticEvaluator(TyAdapter(runner), requests=StaticRequestFactory(runner))
        runtime = RuntimeEvaluator( verifier=ConfiguredVerifier(runner))
        with TyCheckCache() as cache:
            capture = collect_highest(static, prepared, package=package, run_cache=cache)
            assert isinstance(capture, RunTyFactRef)
            result = runtime.evaluate(prepared, package=package,
                                      run_cache=cache)
            assert result.evaluation.status == "PASS"
            assert result.diagnostics is not None
            prepared.close()
            assert not prepared.environment_root.exists()
            scope = cache.snapshot(prepared.proposal.cell)
            assert len(scope.facts) == len(scope.consumers) == len(scope.passes) == 1
            assert len(scope.processes) == 2
            passed = scope.passes[0]
            assert passed.consumer_ref == scope.highest_reference_ref
            assert passed.evidence.proposal_id == result.evaluation.proposal.proposal_id
            assert passed.evidence.verifier == result.evaluation.verifier
            process = next(item.process for item in scope.processes if item.ref == passed.process_ref)
            assert process == result.diagnostics.process
            assert passed.process_ref != scope.facts[0].process_ref
            saved = scope.model_dump_json()
        restored = StaticScopeEvidence.model_validate_json(saved)
        assert restored.model_dump_json() == saved
        assert restored.passes[0].evidence == passed.evidence

    @pytest.mark.parametrize("unavailable", [False, True])
    @pytest.mark.parametrize("foreign", [False, True])
    def test_static_availability_or_foreign_ref_does_not_change_pass(self, tmp_path, unavailable, foreign):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            ty_handler=lambda *_: (ToolFailure(cause="TOOL_FAILURE", stage="ty", process=successful_process(exit_code=2))
                                  if unavailable else TyCheck(process=successful_process(), diagnostics=())),
            verifier_handler=lambda *_: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
                diagnostics=VerifierDiagnostics(process=successful_process()),
            ),
        )
        prepared = assembly.environments.prepare(package=project.package, cell=project.package.cells[0],
                                                 snapshot=project.snapshot, source_plan=project.source_plan,
                                                 resolution=HighestResolution())
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as source, TyCheckCache() as other:
                collect_highest(assembly.static, prepared, package=project.package, run_cache=source)
                target = other if foreign else source
                result = assembly.runtime.evaluate(prepared, package=project.package,
                                                   run_cache=target)
                assert result.evaluation.status == "PASS"
                assert len(assembly.verifier.vectors) == 1
                source_scope = source.snapshot(prepared.proposal.cell)
                assert len(source_scope.facts) == 1
                assert isinstance(source_scope.facts[0].observation.fact, TyCheckUnavailable) == unavailable
                assert len(source_scope.passes) == (0 if foreign else 1)
                assert other.snapshot(prepared.proposal.cell).passes == ()
                assert other.snapshot(prepared.proposal.cell).facts == ()
        finally:
            prepared.close()
            project.snapshot.close()

    def test_uncollected_capture_preserves_highest_proposal_and_allows_verifier_pass(self, tmp_path):
        from pf.schemas.static import StaticContentUnavailable

        class UnavailableRequests:
            def capture(self, prepared, **kwargs):
                return StaticContentUnavailable(detail="unreadable-content")

        project = evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(highest=(), verifier_handler=lambda *_: VerifierRun(
            authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
            diagnostics=VerifierDiagnostics(process=successful_process()),
        ))
        static = StaticEvaluator(assembly.ty, requests=UnavailableRequests())
        runtime = RuntimeEvaluator( verifier=assembly.verifier)
        prepared = assembly.environments.prepare(package=project.package, cell=project.package.cells[0],
                                                 snapshot=project.snapshot, source_plan=project.source_plan,
                                                 resolution=HighestResolution())
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                capture = collect_highest(static, prepared, package=project.package, run_cache=cache)
                assert isinstance(capture, StaticContentUnavailable)
                assert capture.detail == "unreadable-content"
                result = runtime.evaluate(prepared, package=project.package,
                                          run_cache=cache)
                assert result.evaluation.status == "PASS"
                scope = cache.snapshot(prepared.proposal.cell)
                assert scope.highest_uncollected is not None
                assert scope.highest_uncollected.proposal == prepared.proposal
                assert scope.highest_uncollected.attempt == prepared.attempt
                assert scope.highest_uncollected.unavailable.detail == "unreadable-content"
                assert scope.highest_reference_ref is None
                assert scope.facts == scope.consumers == scope.processes == scope.passes == ()
                assert assembly.ty.vectors == []
                assert len(assembly.verifier.vectors) == 1
                saved = scope.model_dump_json()
            assert StaticScopeEvidence.model_validate_json(saved) == scope
        finally:
            prepared.close()
            project.snapshot.close()

    def test_failed_request_recapture_cannot_register_a_pass_against_stale_consumer(self, tmp_path):
        from scripted_static import ScriptedStaticRequests
        from pf.schemas.static import StaticContentUnavailable

        class Requests(ScriptedStaticRequests):
            def __init__(self):
                self.calls = 0

            def capture(self, prepared, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    return StaticContentUnavailable(detail="unreadable-content")
                return super().capture(prepared, **kwargs)

        project = evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(highest=(), verifier_handler=lambda *_: VerifierRun(
            authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
            diagnostics=VerifierDiagnostics(process=successful_process()),
        ))
        static = StaticEvaluator(assembly.ty, requests=Requests())
        runtime = RuntimeEvaluator( verifier=assembly.verifier)
        prepared = assembly.environments.prepare(package=project.package, cell=project.package.cells[0],
                                                 snapshot=project.snapshot, source_plan=project.source_plan,
                                                 resolution=HighestResolution())
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                capture = collect_highest(static, prepared, package=project.package, run_cache=cache)
                assert isinstance(capture, RunTyFactRef)
                observation = static.collect_prepared(prepared, package=project.package, run_cache=cache)
                assert isinstance(observation, StaticContentUnavailable)
                result = runtime.evaluate(prepared, package=project.package, run_cache=cache)
                assert result.evaluation.status == "PASS"
                scope = cache.snapshot(prepared.proposal.cell)
                assert len(scope.facts) == 1
                assert scope.passes == ()
                assert len(assembly.ty.vectors) == len(assembly.verifier.vectors) == 1
        finally:
            prepared.close()
            project.snapshot.close()

    @pytest.mark.parametrize("field,value", [("command", ("python", "-c", "print('other')")), ("timeout_seconds", 1)])
    def test_pass_cannot_be_attributed_to_a_different_execution_policy(self, tmp_path, field, value):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(highest=())
        prepared = assembly.environments.prepare(package=project.package, cell=project.package.cells[0],
                                                 snapshot=project.snapshot, source_plan=project.source_plan,
                                                 resolution=HighestResolution())
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                collect_highest(assembly.static, prepared, package=project.package, run_cache=cache)
                config = project.package.config.model_dump()
                config["test"][field] = value
                changed = project.package.model_copy(update={"config": type(project.package.config).model_validate(config)})
                with pytest.raises(ValueError, match="prepared ExecutionPolicy"):
                    assembly.runtime.evaluate(prepared, package=changed,
                                              run_cache=cache)
                assert not prepared.tested
                assert assembly.verifier.vectors == []
                assert cache.snapshot(prepared.proposal.cell).passes == ()
        finally:
            prepared.close()
            project.snapshot.close()

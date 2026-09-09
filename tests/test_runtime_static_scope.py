from __future__ import annotations

import pytest

from evaluation_fixtures import (
    FailingInspectRunner,
    evaluation_assembly,
    evaluation_project,
    successful_process,
)
from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import ConfiguredVerifier
from pf.adapters.ty import TyAdapter
from pf.environment import HighestResolution, PreparedEnvironment
from pf.evaluation import RuntimeEvaluator
from pf.schemas.evaluation import (
    NormalExit,
    TyCheck,
    VerifierDiagnostics,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)
from pf.schemas.static import StaticContentUnavailable
from pf.schemas.static_scope import StaticScopeEvidence
from pf.static import CollectedStaticSubject, StaticEvaluator, TyCheckCache
from static_fixtures import static_request as static_request


def _passing_runtime():
    return evaluation_assembly(
        highest=(),
        verifier_handler=lambda *_: VerifierRun(
            authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
            diagnostics=VerifierDiagnostics(process=successful_process()),
        ),
    )


class TestRuntimeStaticPassRegistration:
    @pytest.mark.process
    def test_real_verifier_pass_is_saved_with_its_collected_consumer(self, static_request):
        prepared = static_request.prepared
        package = static_request.package
        runner = SubprocessRunner()
        static = StaticEvaluator(TyAdapter(runner), processes=runner)
        runtime = RuntimeEvaluator(verifier=ConfiguredVerifier(runner))
        with TyCheckCache() as cache:
            capture = static.capture_highest(prepared, package=package, run_cache=cache)
            assert isinstance(capture, CollectedStaticSubject)
            result = runtime.evaluate(prepared, package=package)
            static.record_runtime(prepared, result, run_cache=cache)
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

    def test_open_slice_requires_direct_pass_ledger(self, tmp_path):
        from test_search import snapshot

        project = evaluation_project(tmp_path, dependency=None)
        assembly = _passing_runtime()
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)

        class Collector:
            def inspect(self, version):
                raise AssertionError("collector must not inspect without a ledger row")

            def finish(self, keep_versions):
                raise AssertionError("collector must not finish without a ledger row")

        try:
            with TyCheckCache() as cache:
                assembly.static.capture_highest(
                    prepared, package=project.package, run_cache=cache,
                )
                with pytest.raises(ValueError, match="Direct-PASS ledger"):
                    assembly.static.open_slice(
                        run_cache=cache,
                        upper_proposal=prepared.proposal,
                        dependency="idna",
                        versions=("1", "2", "3"),
                        candidates=snapshot("idna"),
                        collector=Collector(),
                    )
        finally:
            prepared.close()
            project.snapshot.close()

    def test_record_runtime_registers_pass_without_consumer(self, tmp_path):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = _passing_runtime()
        static = StaticEvaluator(assembly.ty, processes=FailingInspectRunner(assembly.uv, fail_all=True))
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                capture = static.capture_highest(prepared, package=project.package, run_cache=cache)
                assert isinstance(capture, StaticContentUnavailable)
                run = assembly.runtime.evaluate(prepared, package=project.package)
                static.record_runtime(prepared, run, run_cache=cache)
                scope = cache.snapshot(prepared.proposal.cell)
                assert len(scope.passes) == 1
                assert scope.passes[0].consumer_ref is None
                assert scope.facts == scope.consumers == ()
                assert len(scope.processes) == 1
        finally:
            prepared.close()
            project.snapshot.close()

    def test_record_runtime_rejects_foreign_or_unregistered_prepared(self, tmp_path):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = _passing_runtime()
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache, TyCheckCache() as other:
                run = assembly.runtime.evaluate(prepared, package=project.package)
                with pytest.raises(ValueError, match="registered"):
                    assembly.static.record_runtime(prepared, run, run_cache=other)
                assembly.static.capture_highest(prepared, package=project.package, run_cache=cache)
                with pytest.raises(ValueError, match="another cache|registered"):
                    assembly.static.record_runtime(prepared, run, run_cache=other)
        finally:
            prepared.close()
            project.snapshot.close()

    def test_record_runtime_rejects_process_reuse(self, tmp_path):
        project = evaluation_project(tmp_path, dependency=None)
        shared = successful_process()
        assembly = evaluation_assembly(
            highest=(),
            ty_handler=lambda *_: TyCheck(process=shared, diagnostics=()),
            verifier_handler=lambda *_: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
                diagnostics=VerifierDiagnostics(process=shared),
            ),
        )
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                assert isinstance(
                    assembly.static.capture_highest(prepared, package=project.package, run_cache=cache),
                    CollectedStaticSubject,
                )
                run = assembly.runtime.evaluate(prepared, package=project.package)
                with pytest.raises(ValueError, match="reuse a ty process"):
                    assembly.static.record_runtime(prepared, run, run_cache=cache)
        finally:
            prepared.close()
            project.snapshot.close()

    def test_record_runtime_is_idempotent_for_same_process(self, tmp_path):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = _passing_runtime()
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                assembly.static.capture_highest(prepared, package=project.package, run_cache=cache)
                run = assembly.runtime.evaluate(prepared, package=project.package)
                assembly.static.record_runtime(prepared, run, run_cache=cache)
                assembly.static.record_runtime(prepared, run, run_cache=cache)
                assert len(cache.snapshot(prepared.proposal.cell).passes) == 1
        finally:
            prepared.close()
            project.snapshot.close()

    def test_later_collect_binds_consumer_without_replacing_runtime_owner(self, tmp_path):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = _passing_runtime()
        static = StaticEvaluator(assembly.ty, processes=FailingInspectRunner(assembly.uv, fail_at={1}))
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                capture = static.capture_highest(prepared, package=project.package, run_cache=cache)
                assert isinstance(capture, StaticContentUnavailable)
                run = assembly.runtime.evaluate(prepared, package=project.package)
                static.record_runtime(prepared, run, run_cache=cache)
                assert cache.snapshot(prepared.proposal.cell).passes[0].consumer_ref is None
                later = assembly.environments.reprepare(
                    prepared.proposal, project.snapshot, project.source_plan,
                )
                assert isinstance(later, PreparedEnvironment)
                try:
                    collected = static.collect_prepared(later, package=project.package, run_cache=cache)
                    assert isinstance(collected, CollectedStaticSubject)
                    scope = cache.snapshot(prepared.proposal.cell)
                    assert len(scope.passes) == 1
                    assert scope.passes[0].consumer_ref is not None
                    assert scope.passes[0].preparation_ref is not None
                    assert scope.preparation(scope.passes[0].preparation_ref).proposal == prepared.proposal
                finally:
                    later.close()
        finally:
            prepared.close()
            project.snapshot.close()

    def test_record_runtime_rejects_different_prepared_for_owned_proposal(self, tmp_path):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = _passing_runtime()
        first = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(first, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                assembly.static.capture_highest(first, package=project.package, run_cache=cache)
                run = assembly.runtime.evaluate(first, package=project.package)
                assembly.static.record_runtime(first, run, run_cache=cache)
                second = assembly.environments.reprepare(
                    first.proposal, project.snapshot, project.source_plan,
                )
                assert isinstance(second, PreparedEnvironment)
                try:
                    assembly.static.collect_prepared(second, package=project.package, run_cache=cache)
                    with pytest.raises(ValueError, match="runtime owner"):
                        assembly.static.record_runtime(second, run, run_cache=cache)
                finally:
                    second.close()
        finally:
            first.close()
            project.snapshot.close()

    def test_record_runtime_validates_registration_before_outcome(self, tmp_path):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            verifier_handler=lambda *_: VerifierRun(
                authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
                diagnostics=VerifierDiagnostics(process=successful_process(exit_code=1)),
            ),
        )
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                run = assembly.runtime.evaluate(prepared, package=project.package)
                assert run.evaluation.status == "VERIFIER_REJECTED"
                with pytest.raises(ValueError, match="registered"):
                    assembly.static.record_runtime(prepared, run, run_cache=cache)
        finally:
            prepared.close()
            project.snapshot.close()

    @pytest.mark.parametrize("field,value", [("command", ("python", "-c", "print('other')")), ("timeout_seconds", 1)])
    def test_pass_cannot_be_attributed_to_a_different_execution_policy(self, tmp_path, field, value):
        project = evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(highest=())
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        try:
            with TyCheckCache() as cache:
                assembly.static.capture_highest(prepared, package=project.package, run_cache=cache)
                config = project.package.config.model_dump()
                config["test"][field] = value
                changed = project.package.model_copy(
                    update={"config": type(project.package.config).model_validate(config)},
                )
                with pytest.raises(ValueError, match="prepared ExecutionPolicy"):
                    assembly.runtime.evaluate(prepared, package=changed)
                assert not prepared.tested
                assert assembly.verifier.vectors == []
                assert cache.snapshot(prepared.proposal.cell).passes == ()
        finally:
            prepared.close()
            project.snapshot.close()

    def test_record_runtime_rejects_process_from_another_run(self, tmp_path):
        process = successful_process()
        project_a = evaluation_project(tmp_path / "a", dependency=None)
        project_b = evaluation_project(tmp_path / "b", dependency=None)
        assembly_a = evaluation_assembly(
            highest=(),
            verifier_handler=lambda *_: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
                diagnostics=VerifierDiagnostics(process=process),
            ),
        )
        assembly_b = evaluation_assembly(
            highest=(),
            verifier_handler=lambda *_: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
                diagnostics=VerifierDiagnostics(process=process),
            ),
        )
        prepared_a = assembly_a.environments.prepare(
            package=project_a.package, cell=project_a.package.cells[0],
            snapshot=project_a.snapshot, source_plan=project_a.source_plan,
            resolution=HighestResolution(),
        )
        prepared_b = assembly_b.environments.prepare(
            package=project_b.package, cell=project_b.package.cells[0],
            snapshot=project_b.snapshot, source_plan=project_b.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared_a, PreparedEnvironment)
        assert isinstance(prepared_b, PreparedEnvironment)
        try:
            with TyCheckCache() as cache_a, TyCheckCache() as cache_b:
                assembly_a.static.capture_highest(
                    prepared_a, package=project_a.package, run_cache=cache_a,
                )
                run_a = assembly_a.runtime.evaluate(prepared_a, package=project_a.package)
                assembly_a.static.record_runtime(prepared_a, run_a, run_cache=cache_a)
                assembly_b.static.capture_highest(
                    prepared_b, package=project_b.package, run_cache=cache_b,
                )
                run_b = assembly_b.runtime.evaluate(prepared_b, package=project_b.package)
                with pytest.raises(ValueError, match="another static Run"):
                    assembly_b.static.record_runtime(prepared_b, run_b, run_cache=cache_b)
        finally:
            prepared_a.close()
            prepared_b.close()
            project_a.snapshot.close()
            project_b.snapshot.close()

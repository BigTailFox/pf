from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest

from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process
from scripted_static import ScriptedStaticRequests
from pf.environment import HighestResolution, LowestDirectResolution, PreparedEnvironment
from pf.evaluation import StaticEvaluator, StagePermitPools
from pf.schemas.evaluation import ToolFailure, TyCheck, VerifierRun, VerifierPass, NormalExit, VerifierDiagnostics
from pf.static_cache import RunTyFactRef, TyCheckCache


@pytest.fixture
def prepared_pair(tmp_path):
    project = evaluation_project(tmp_path, dependency=None)
    assembly = evaluation_assembly(highest=(), lowest=(), verifier_handler=lambda *_: VerifierRun(
        authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
        diagnostics=VerifierDiagnostics(process=successful_process()),
    ))
    owner = assembly.environments.prepare(
        package=project.package, cell=project.package.cells[0], snapshot=project.snapshot,
        source_plan=project.source_plan, resolution=HighestResolution(),
    )
    assert isinstance(owner, PreparedEnvironment)
    waiter = assembly.environments.prepare(
        package=project.package, cell=project.package.cells[0], snapshot=project.snapshot,
        source_plan=project.source_plan, resolution=LowestDirectResolution(owner.harness_baseline),
    )
    assert isinstance(waiter, PreparedEnvironment)
    requests = ScriptedStaticRequests()
    owner_request = requests.capture(owner, package=project.package, environment={})
    waiter_request = requests.capture(waiter, package=project.package, environment={})
    assert owner_request.subject == waiter_request.subject
    assert owner.proposal != waiter.proposal
    try:
        yield project, assembly, owner, waiter, requests, owner_request, waiter_request
    finally:
        owner.close()
        waiter.close()
        project.snapshot.close()


class TestStaticConsumerOwnership:
    @pytest.mark.parametrize("failed", [False, True])
    @pytest.mark.parametrize("target", ["owner", "waiter"])
    @pytest.mark.parametrize("action", ["close", "verifier"])
    def test_join_holds_each_proposal_inputs_until_static_completion(self, prepared_pair, failed, target, action):
        project, assembly, owner, waiter, requests, owner_request, waiter_request = prepared_pair
        entered, joined, release, action_entered = Event(), Event(), Event(), Event()
        calls = []

        class Cache(TyCheckCache):
            def collect(self, preparation, policy, operation, *, revalidate):
                if preparation.proposal == waiter.proposal:
                    joined.set()
                return super().collect(preparation, policy, operation, revalidate=revalidate)

        class Ty:
            def observe(self, request, *, cancellation=None):
                calls.append(request)
                entered.set()
                assert release.wait(5)
                assert owner.environment_root.exists()
                process = successful_process(exit_code=2 if failed else 0)
                return (ToolFailure(cause="TOOL_FAILURE", stage="ty", process=process) if failed
                        else TyCheck(process=process, diagnostics=()))

        static = StaticEvaluator(Ty(), requests=requests, permits=StagePermitPools(ty_jobs=1, test_jobs=1))
        environment = owner if target == "owner" else waiter

        def consume():
            action_entered.set()
            if action == "close":
                environment.close()
            else:
                run = assembly.runtime.evaluate(environment, package=project.package, run_cache=cache)
                assert run.evaluation.proposal == environment.proposal

        try:
            with Cache() as cache, ThreadPoolExecutor(max_workers=3) as pool:
                first = pool.submit(static.collect, owner, owner_request, run_cache=cache)
                try:
                    assert entered.wait(5)
                    second = pool.submit(static.collect, waiter, waiter_request, run_cache=cache)
                    assert joined.wait(5)
                    consumed = pool.submit(consume)
                    assert action_entered.wait(5)
                    with pytest.raises(TimeoutError):
                        consumed.result(0.05)
                    assert owner.environment_root.exists() and waiter.environment_root.exists()
                    assert len(calls) == 1
                finally:
                    release.set()
                a, b = first.result(5), second.result(5)
                consumed.result(5)
                assert isinstance(a, RunTyFactRef) and a is b
                assert a.observation.fact.kind == ("ty-check-unavailable" if failed else "ty-check")
                scope = cache.snapshot(owner.proposal.cell)
                assert len(scope.facts) == 1 and len(scope.consumers) == 2
                assert {item.preparation.proposal.proposal_id for item in scope.consumers} == {
                    owner.proposal.proposal_id, waiter.proposal.proposal_id,
                }
                assert len(calls) == 1
                assert len(scope.passes) == (1 if action == "verifier" else 0)
                if scope.passes:
                    passed = scope.passes[0]
                    assert scope.consumer(passed.consumer_ref).preparation.proposal == environment.proposal
        finally:
            release.set()

    def test_changed_host_bytes_outside_subject_do_not_block_verifier(self, prepared_pair):
        project, assembly, owner, _, requests, request, _ = prepared_pair

        class Ty:
            def observe(self, request, *, cancellation=None):
                return TyCheck(process=successful_process(), diagnostics=())

        assert "ty-executable" not in dict(request.roots)
        (owner.environment_root / "changed.txt").write_text("outside v2 subject")
        with TyCheckCache() as cache:
            result = StaticEvaluator(Ty(), requests=requests).collect(owner, request, run_cache=cache)
            assert isinstance(result, RunTyFactRef)
            run = assembly.runtime.evaluate(owner, package=project.package, run_cache=cache)
            assert run.evaluation.status == "PASS"
            assert len(assembly.verifier.vectors) == 1

    def test_unavailable_collection_still_runs_verifier(self, prepared_pair):
        from pf.schemas.static import StaticContentUnavailable

        project, assembly, owner, _, requests, request, _ = prepared_pair

        class Ty:
            def observe(self, request, *, cancellation=None):
                (request.environment_root / "changed.txt").write_text("changed during collection")
                return StaticContentUnavailable(detail="unreadable-content")

        with TyCheckCache() as cache:
            result = StaticEvaluator(Ty(), requests=requests).collect(owner, request, run_cache=cache)
            assert isinstance(result, StaticContentUnavailable)
            run = assembly.runtime.evaluate(owner, package=project.package, run_cache=cache)
            assert run.evaluation.status == "PASS"
            assert len(assembly.verifier.vectors) == 1
            assert cache.snapshot(owner.proposal.cell).facts == ()

    @pytest.mark.parametrize("changed_projection", [False, True])
    def test_changed_waiter_is_not_admitted_and_clean_rebuild_uses_its_own_projection(
        self, prepared_pair, changed_projection,
    ):
        project, assembly, owner, waiter, requests, owner_request, waiter_request = prepared_pair
        entered, joined, release = Event(), Event(), Event()
        calls = []

        class Cache(TyCheckCache):
            def collect(self, preparation, policy, operation, *, revalidate):
                if preparation.proposal == waiter.proposal:
                    joined.set()
                return super().collect(preparation, policy, operation, revalidate=revalidate)

        class Ty:
            def observe(self, request, *, cancellation=None):
                calls.append(request)
                entered.set()
                assert release.wait(5)
                return TyCheck(process=successful_process(), diagnostics=())

        static = StaticEvaluator(Ty(), requests=requests)
        with Cache() as cache, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(static.collect, owner, owner_request, run_cache=cache)
            try:
                assert entered.wait(5)
                second = pool.submit(static.collect, waiter, waiter_request, run_cache=cache)
                assert joined.wait(5)
                (waiter.proposal_root / "changed.txt").write_text("external change while joining")
            finally:
                release.set()
            fact, joined_fact = first.result(5), second.result(5)
            assert isinstance(fact, RunTyFactRef)
            assert isinstance(joined_fact, RunTyFactRef)
            assert joined_fact.observation.subject.identity == fact.observation.subject.identity
            scope = cache.snapshot(owner.proposal.cell)
            assert len(scope.facts) == 1
            assert {item.preparation.proposal.proposal_id for item in scope.consumers} == {
                owner.proposal.proposal_id, waiter.proposal.proposal_id,
            }
            run = assembly.runtime.evaluate(waiter, package=project.package, run_cache=cache)
            assert run.evaluation.status == "PASS"
            waiter.close()
            rebuilt = assembly.environments.prepare(
                package=project.package, cell=project.package.cells[0], snapshot=project.snapshot,
                source_plan=project.source_plan, resolution=LowestDirectResolution(owner.harness_baseline),
            )
            assert isinstance(rebuilt, PreparedEnvironment)
            try:
                if changed_projection:
                    (rebuilt.environment_root / "support.txt").write_text("different installed support bytes")
                current = static.collect_prepared(rebuilt, package=project.package, run_cache=cache)
                assert isinstance(current, RunTyFactRef)
                assert current.observation.subject.identity == fact.observation.subject.identity
                assert len(calls) == 1
                run = assembly.runtime.evaluate(rebuilt, package=project.package, run_cache=cache)
                assert run.evaluation.status == "PASS"
                scope = cache.snapshot(owner.proposal.cell)
                assert len(scope.facts) == 1
                assert len(scope.consumers) == 2 and len(scope.passes) == 2
            finally:
                rebuilt.close()

    @pytest.mark.parametrize("mode", ["cancel", "exception"])
    def test_terminal_cleanup_releases_both_consumers_and_wakes_waiters(self, prepared_pair, mode):
        from pf.cancellation import OperationCancelled
        from pf.static_cache import CacheMiss
        project, _assembly, owner, waiter, requests, owner_request, waiter_request = prepared_pair
        entered, joined, trigger, cleanup_started, finish_cleanup, cleaned = (Event() for _ in range(6))
        calls = []

        class Cache(TyCheckCache):
            def collect(self, preparation, policy, operation, *, revalidate):
                if preparation.proposal == waiter.proposal:
                    joined.set()
                return super().collect(preparation, policy, operation, revalidate=revalidate)

        class Ty:
            def observe(self, request, *, cancellation=None):
                assert cancellation is not None
                unregister = cancellation.register(trigger.set)
                calls.append(request)
                try:
                    entered.set()
                    assert trigger.wait(5)
                    if mode == "exception":
                        raise ValueError("unmodeled lower ty error")
                    cancellation.raise_if_cancelled()
                    raise AssertionError("cancelled operation returned")
                finally:
                    cleanup_started.set()
                    assert finish_cleanup.wait(5)
                    assert owner.environment_root.exists()
                    unregister()
                    cleaned.set()

        static = StaticEvaluator(Ty(), requests=requests, permits=StagePermitPools(ty_jobs=1, test_jobs=1))
        with Cache() as cache, ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(static.collect, owner, owner_request, run_cache=cache)
            try:
                assert entered.wait(5)
                second = pool.submit(static.collect, waiter, waiter_request, run_cache=cache)
                assert joined.wait(5)
                closing = pool.submit(owner.close)
                with pytest.raises(TimeoutError):
                    closing.result(0.05)
                stopping = pool.submit(cache.stop) if mode == "cancel" else None
                if mode == "exception":
                    trigger.set()
                assert cleanup_started.wait(5)
                assert not first.done() and not closing.done()
                if stopping is not None:
                    assert not stopping.done()
                assert owner.environment_root.exists() and waiter.environment_root.exists()
            finally:
                trigger.set()
                finish_cleanup.set()
            expected = OperationCancelled if mode == "cancel" else ValueError
            with pytest.raises(expected):
                first.result(5)
            with pytest.raises((expected, OperationCancelled)):
                second.result(5)
            if stopping is not None:
                assert stopping.result(5) == ()
            closing.result(5)
            assert cleaned.is_set() and owner.closed
            waiter.close()
            assert waiter.closed
            assert len(calls) == 1
            assert isinstance(cache.lookup(owner_request.subject, owner_request.observation_policy), CacheMiss)
            scope = cache.snapshot(owner.proposal.cell)
            assert scope.facts == scope.consumers == scope.processes == scope.passes == ()
            with pytest.raises(OperationCancelled):
                static.collect(owner, owner_request, run_cache=cache)

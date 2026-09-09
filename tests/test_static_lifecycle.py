from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event

import pytest

from evaluation_fixtures import evaluation_assembly, evaluation_project
from pf.cancellation import Cancellation, OperationCancelled
from pf.evaluation import StagePermitPools
from pf.environment import HighestResolution, PreparedEnvironment


@pytest.fixture
def prepared(tmp_path: Path) -> Iterator[PreparedEnvironment]:
    project = evaluation_project(tmp_path, dependency=None)
    assembly = evaluation_assembly(highest=())
    value = assembly.environments.prepare(
        package=project.package, cell=project.package.cells[0],
        snapshot=project.snapshot, resolution=HighestResolution(),
        source_plan=project.source_plan,
    )
    assert isinstance(value, PreparedEnvironment)
    try:
        yield value
    finally:
        value.close()
        project.snapshot.close()


class TestPreparedStaticLifetime:
    def test_static_owner_cannot_close_or_mark_its_inputs_tested(self, prepared: PreparedEnvironment) -> None:
        with prepared.static_use() as available:
            assert available
            with prepared.static_use() as nested:
                assert nested
            with pytest.raises(RuntimeError):
                prepared.close()
            with pytest.raises(RuntimeError):
                prepared.mark_tested()
            assert prepared.environment_root.exists()
        assert not prepared.tested
        assert not prepared.closed

    @pytest.mark.parametrize("action", ("close", "verifier"))
    def test_other_owner_waits_for_static_collection_to_release(self, prepared: PreparedEnvironment, action: str) -> None:
        requested = Event()
        entered = Event()

        def consume() -> None:
            requested.set()
            if action == "close":
                prepared.close()
                entered.set()
            else:
                with prepared.verifier_use():
                    entered.set()
                    assert prepared.environment_root.exists()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with prepared.static_use() as available:
                assert available
                future = pool.submit(consume)
                assert requested.wait(5)
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.05)
                assert not entered.is_set()
                assert prepared.environment_root.exists()
            future.result(timeout=5)
        assert entered.is_set()
        assert prepared.closed if action == "close" else prepared.tested

    @pytest.mark.parametrize("failure", (RuntimeError, OperationCancelled))
    def test_static_failure_releases_inputs_for_verifier(self, prepared: PreparedEnvironment, failure: type[BaseException]) -> None:
        with pytest.raises(failure):
            with prepared.static_use() as available:
                assert available
                raise failure()
        with prepared.verifier_use():
            assert prepared.tested
            with prepared.static_use() as available:
                assert not available
        with pytest.raises(RuntimeError):
            with prepared.verifier_use():
                pass

    def test_closed_environment_cannot_be_collected_or_verified(self, prepared: PreparedEnvironment) -> None:
        prepared.close()
        with prepared.static_use() as available:
            assert not available
        with pytest.raises(RuntimeError):
            with prepared.verifier_use():
                pass
        assert not prepared.environment_root.exists()

    def test_reprepare_collects_after_original_environment_close(self, tmp_path: Path) -> None:
        from evaluation_fixtures import evaluation_assembly, evaluation_project
        from pf.static_cache import RunTyFactRef, TyCheckCache
        from scripted_static import ScriptedStaticRequests

        project = evaluation_project(tmp_path, dependency=None)
        assembly = evaluation_assembly(highest=())
        prepared = assembly.environments.prepare(
            package=project.package, cell=project.package.cells[0],
            snapshot=project.snapshot, resolution=HighestResolution(),
            source_plan=project.source_plan,
        )
        assert isinstance(prepared, PreparedEnvironment)
        proposal = prepared.proposal
        request = ScriptedStaticRequests().capture(
            prepared, package=project.package, environment={},
        )
        prepared.close()
        assert not prepared.environment_root.exists()
        rebuilt = assembly.environments.reprepare(
            proposal, project.snapshot, project.source_plan,
        )
        assert isinstance(rebuilt, PreparedEnvironment)
        try:
            cache = TyCheckCache()
            collected = assembly.static.collect_prepared(
                rebuilt, package=project.package, run_cache=cache,
            )
            assert isinstance(collected, RunTyFactRef)
            assert collected.observation.subject.identity == request.subject.identity
            cache.close()
        finally:
            rebuilt.close()
            project.snapshot.close()


class TestStaticCancellationWhileWaiting:
    @pytest.mark.parametrize("cached", [False, True])
    def test_run_stop_cancels_collection_waiting_for_input_admission(self, prepared, cached):
        from evaluation_fixtures import successful_process
        from scripted_static import ScriptedStaticRequests
        from pf.evaluation import StaticEvaluator
        from pf.project import ProjectLoader
        from pf.schemas.evaluation import TyCheck
        from pf.static_cache import CacheMiss, RunTyFactRef, TyCheckCache

        package = ProjectLoader().load(root=prepared.package_root).target
        requests = ScriptedStaticRequests()
        request = requests.capture(prepared, package=package, environment={})
        entered = Event()
        calls = []

        class Ty:
            def observe(self, request, *, cancellation=None):
                calls.append(request)
                return TyCheck(process=successful_process(), diagnostics=())

        static = StaticEvaluator(Ty(), requests=requests)
        with TyCheckCache() as cache:
            if cached:
                assert isinstance(static.collect(prepared, request, run_cache=cache), RunTyFactRef)

            def collect():
                entered.set()
                return static.collect(prepared, request, run_cache=cache)

            with ThreadPoolExecutor(max_workers=1) as pool:
                with prepared.static_use() as available:
                    assert available
                    pending = pool.submit(collect)
                    assert entered.wait(5)
                    cache.stop()
                    with pytest.raises(OperationCancelled):
                        pending.result(timeout=1)
                    # The input owner still holds its resource; cancellation did
                    # not need that owner to finish or close the environment.
                    assert prepared.environment_root.exists()
            assert len(calls) == int(cached)
            assert len(cache.snapshot(prepared.proposal.cell).facts) == int(cached)
            saved = cache.lookup(request.subject, request.observation_policy)
            assert isinstance(saved, RunTyFactRef if cached else CacheMiss)
        assert not prepared.closed

    def test_cancelled_input_borrow_finishes_while_other_owner_still_holds_it(self, prepared):
        cancellation, requested = Cancellation(), Event()

        def collect():
            requested.set()
            with prepared.static_use(cancellation=cancellation):
                raise AssertionError("cancelled borrower acquired inputs")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with prepared.static_use() as available:
                assert available
                pending = pool.submit(collect)
                assert requested.wait(5)
                cancellation.cancel()
                with pytest.raises(OperationCancelled):
                    pending.result(5)
                assert prepared.environment_root.exists()
        assert not prepared.closed

    def test_cancelled_permit_waiter_finishes_without_releasing_other_owner(self):
        permits = StagePermitPools(ty_jobs=1, test_jobs=1)
        cancellation, requested = Cancellation(), Event()

        def collect():
            requested.set()
            with permits.ty(cancellation=cancellation):
                raise AssertionError("cancelled owner started a lower operation")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with permits.ty():
                pending = pool.submit(collect)
                assert requested.wait(5)
                cancellation.cancel()
                with pytest.raises(OperationCancelled):
                    pending.result(5)
        with permits.ty():
            pass

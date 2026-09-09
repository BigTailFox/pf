from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import ConfiguredVerifier
from pf.adapters.ty import TyAdapter
from pf.adapters.uv import UvAdapter
from pf.baseline import HighestVersionVerifier
from pf.environment import EnvironmentFactory
from pf.errors import JournalReadError
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.runlog import RunLogStore
from pf.schemas.config import RunLimits
from pf.schemas.evaluation import CellCompletedEvent, HighestVersionPass
from pf.schemas.policy import GuidancePolicy
from pf.schemas.project import SourcePlan
from pf.schemas.static_comparison import GlobalComparisonContext
from pf.snapshot import SnapshotBuilder
from pf.static_cache import CacheMiss, RunTyFactRef
from pf.static_request import StaticRequestFactory
from pf.verification import SmokeVerificationRun, VerificationRunner
from pf.errors import InfrastructureError
from pf.schemas.journal import (
    VerificationJournal,
    VerificationPackagePolicy,
    admit_static_membership,
)

INTERN_FIELDS = (
    "static_contents",
    "static_subjects",
    "static_facts",
    "static_comparisons",
    "static_scopes",
)


def _write_current_journal(tmp_path: Path):
    store = RunLogStore(root=tmp_path, run_id="current-journal")
    journal = VerificationJournal(
        run_id=store.run_id,
        command="smoke",
        source_snapshot_digest="e91f3a54af54b8970f89b69b74a98cccf621f0cd7e6027d3996f49d3e8bacd81",
        package_policies=(
            VerificationPackagePolicy(
                package="demo",
                execution_policy_identity="d02a58ef756e371f8feec239e6570eb88b7f3938a72423a6b0949409a2e01fa6",
            ),
        ),
        entries=(),
        static_membership=(),
    )
    path = store.write_journal(journal)
    return store, journal, path


@pytest.fixture(scope="module")
def actual_static_journal(tmp_path_factory):
    root = tmp_path_factory.mktemp("static-journal")
    project = root / "project"
    (project / "src/demo").mkdir(parents=True)
    (project / "src/demo/__init__.py").write_text("VALUE = 1\n")
    (project / "pyproject.toml").write_text("""
[project]
name = "demo"
version = "1"
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1; print('verified demo')"]
""")
    package = ProjectLoader().load(root=project).target
    source_plan = SourcePlan.for_package(package, "DEVELOPMENT")
    logs = RunLogStore(root=root, run_id="actual-static-journal")
    runner = SubprocessRunner(logs=logs)
    snapshot = SnapshotBuilder(runner).build(project)
    static = StaticEvaluator(TyAdapter(runner), requests=StaticRequestFactory(runner))
    highest = HighestVersionVerifier(
        environments=EnvironmentFactory(UvAdapter(runner)),
        static=static,
        full=RuntimeEvaluator( verifier=ConfiguredVerifier(runner)),
    )
    caches = []
    events = []

    class Events:
        def consume(self, event):
            events.append(event)

    class Operation:
        def verify(self, *, run_cache, **kwargs):
            caches.append(run_cache)
            result = highest.verify(run_cache=run_cache, **kwargs)
            assert isinstance(result, HighestVersionPass)
            scope = run_cache.snapshot(result.attempt.identity.cell)
            member = scope.consumers[0]
            policy = scope.facts[0].observation.observation_policy
            fact = run_cache.lookup(member.preparation.subject, policy)
            assert isinstance(fact, RunTyFactRef)
            consumer = run_cache.consumer(fact, member.preparation)
            compared = static.compare(
                consumer,
                consumer,
                run_cache=run_cache,
                context=GlobalComparisonContext(
                    highest_proposal_id=result.evaluation.proposal.proposal_id
                ),
                guidance_policy=GuidancePolicy(
                    observation=policy, observation_identity=policy.identity
                ),
            )
            assert compared.status == "COMPARED"
            return result

    try:
        outcomes = VerificationRunner(
            events=Events(), logs=logs, host_target=package.cells[0].target
        ).run(
            SmokeVerificationRun(
                package=package,
                source_plan=source_plan,
                snapshot=snapshot,
                operation=Operation(),
                limits=RunLimits(max_cells=1, ty_jobs=1, test_jobs=1),
            ),
        )
        journal = logs.read_latest_journal(package.name)
        assert journal is not None
        assert journal.static_membership[0].highest.kind == "collected"
        cache = logs.read_ty_cache(journal.run_id)
        admit_static_membership(journal, cache)
        yield logs, journal, outcomes, events, caches
    finally:
        snapshot.close()


class TestStaticJournal:
    @pytest.mark.process
    def test_real_pass_persists_membership_and_ty_cache(
        self, actual_static_journal, tmp_path: Path, record_property
    ):
        logs, journal, outcomes, events, caches = actual_static_journal
        assert all(outcome.status == "PASS" for outcome in outcomes)
        assert journal.schema_version == "verification-journal-v3"
        assert journal.entries == ()
        assert len(journal.static_membership) == 1
        member = journal.static_membership[0]
        assert member.highest.kind == "collected"
        cache = logs.read_ty_cache(journal.run_id)
        assert cache.schema_version == "pf-ty-cache-v1"
        assert cache.run_id == journal.run_id
        admit_static_membership(journal, cache)
        document = json.loads((logs._run_root / "journal.json").read_text())
        assert all(name not in document for name in INTERN_FIELDS)
        assert (
            next(
                event for event in events if isinstance(event, CellCompletedEvent)
            ).outcome.status
            == "PASS"
        )
        logs.write_journal(journal)
        copy = RunLogStore(root=tmp_path, run_id=journal.run_id)
        copy.write_ty_cache(cache)
        path = copy.write_journal(journal)
        first = path.read_bytes()
        restored = copy.read_latest_journal(journal.packages[0])
        assert restored is not None
        assert restored == journal
        copy.write_journal(restored)
        assert path.read_bytes() == first
        record_property("journal_bytes", len(first))
        assert isinstance(caches[0].lookup(cache.entries[0].document.subject, cache.entries[0].document.observation_policy), CacheMiss)

    @pytest.mark.parametrize("field", INTERN_FIELDS)
    def test_reader_rejects_old_intern_tables(self, tmp_path: Path, field):
        store, journal, path = _write_current_journal(tmp_path)
        document = json.loads(path.read_text())
        document[field] = []
        path.write_text(json.dumps(document))
        with pytest.raises(JournalReadError) as caught:
            store.read_journal(journal.run_id)
        assert caught.value.reason == "invalid-static-evidence"

    @pytest.mark.parametrize("content", ["[]", "{invalid-json"])
    def test_reader_rejects_an_undecodable_contract(
        self, tmp_path: Path, content
    ):
        store, journal, path = _write_current_journal(tmp_path)
        path.write_text(content)
        with pytest.raises(JournalReadError) as caught:
            store.read_journal(journal.run_id)
        assert caught.value.reason == "unsupported-journal-contract"

    def test_reader_rejects_an_unsupported_contract(
        self, tmp_path: Path
    ):
        store, journal, path = _write_current_journal(tmp_path)
        document = json.loads(path.read_text())
        document["schema"] = "unsupported-journal-contract"
        path.write_text(json.dumps(document))
        with pytest.raises(JournalReadError) as caught:
            store.read_latest_journal(journal.packages[0])
        assert caught.value.reason == "unsupported-journal-contract"

    def test_ordinary_journal_read_does_not_open_ty_cache(self, tmp_path: Path):
        store, journal, _path = _write_current_journal(tmp_path)

        def boom(run_id):
            raise AssertionError("diagnose reader must not open ty-cache")

        store.read_ty_cache = boom  # type: ignore[method-assign]
        restored = store.read_journal(journal.run_id)
        assert restored == journal
        assert store.latest_journal_id("demo") == journal.run_id

    def test_many_observations_round_trip_in_one_scope(
        self, tmp_path: Path, record_property
    ):
        from evaluation_fixtures import evaluation_assembly, evaluation_project
        from pf.environment import HighestResolution, PreparedEnvironment
        from pf.schemas.journal import (
            VerificationJournal,
            VerificationPackagePolicy,
            static_membership_from_scope,
        )
        from pf.schemas.ty_cache import ty_cache_from_documents
        from pf.schemas.ty_fact import TyCheckFact
        from pf.static_cache import TyCheckCache
        from scripted_static import ScriptedStaticRequests
        from pf.static_request import StaticTyRequest

        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(highest=())
        prepared = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(prepared, PreparedEnvironment)
        factory = ScriptedStaticRequests()
        store = RunLogStore(root=tmp_path, run_id="multiple-static-observations")
        try:
            with TyCheckCache() as cache:
                for timeout in range(30, 42):
                    config = project.package.config.model_copy(
                        update={
                            "ty": project.package.config.ty.model_copy(
                                update={"timeout_seconds": timeout}
                            )
                        }
                    )
                    selected = project.package.model_copy(update={"config": config})
                    request = factory.capture(
                        prepared, package=selected, environment={},
                    )
                    assert isinstance(request, StaticTyRequest)
                    fact = assembly.static.collect(prepared, request, run_cache=cache)
                    assert isinstance(fact, RunTyFactRef)
                    assert isinstance(fact.observation.fact, TyCheckFact)
                scope = cache.snapshot(prepared.proposal.cell)
                assert len(scope.facts) == 1
                member = static_membership_from_scope(scope)
                journal = VerificationJournal(
                    run_id=store.run_id,
                    command="check",
                    entries=(),
                    source_snapshot_digest=prepared.proposal.snapshot_digest,
                    package_policies=(
                        VerificationPackagePolicy(
                            package=project.package.name,
                            execution_policy_identity=prepared.proposal.policy_identity,
                        ),
                    ),
                    static_membership=() if member is None else (member,),
                )
                ty_cache = ty_cache_from_documents(run_id=store.run_id, documents=cache.documents())
                store.persist_run(journal, ty_cache)
                path = store._run_root / "journal.json"
            restored = store.read_latest_journal(project.package.name)
            assert restored is not None
            assert restored.model_dump(mode="json") == journal.model_dump(mode="json")
            admit_static_membership(restored, store.read_ty_cache(store.run_id))
            record_property("multiple_observation_journal_bytes", path.stat().st_size)
        finally:
            prepared.close()
            project.snapshot.close()

    @pytest.mark.parametrize(
        "scenario", ["input-unavailable", "ty-unavailable", "raise-after-pass"]
    )
    def test_unavailable_and_interrupted_runs_preserve_static_audit_without_failures(
        self, tmp_path: Path, scenario
    ):
        from evaluation_fixtures import (
            evaluation_assembly,
            evaluation_project,
            successful_process,
        )
        from pf.schemas.evaluation import (
            NormalExit,
            ToolFailure,
            VerifierDiagnostics,
            VerifierPass,
            VerifierRun,
        )
        from pf.schemas.static import StaticContentUnavailable
        from pf.static_cache import CacheMiss

        project = evaluation_project(tmp_path / "project", dependency=None)
        assembly = evaluation_assembly(
            highest=(),
            ty_handler=(
                lambda *_: ToolFailure(
                    cause="TOOL_FAILURE",
                    stage="ty",
                    process=successful_process(exit_code=2),
                )
            )
            if scenario == "ty-unavailable"
            else None,
            verifier_handler=lambda *_: VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
                diagnostics=VerifierDiagnostics(process=successful_process()),
            ),
        )

        class UnavailableRequests:
            def capture(self, prepared, **kwargs):
                return StaticContentUnavailable(detail="unreadable-content")

        static = (
            StaticEvaluator(assembly.ty, requests=UnavailableRequests())
            if scenario == "input-unavailable"
            else assembly.static
        )
        highest = HighestVersionVerifier(
            environments=assembly.environments,
            static=static,
            full=RuntimeEvaluator( verifier=assembly.verifier),
        )
        caches = []

        class Operation:
            def verify(self, *, run_cache, **kwargs):
                caches.append(run_cache)
                result = highest.verify(run_cache=run_cache, **kwargs)
                assert result.status == "PASS"
                if scenario == "raise-after-pass":
                    raise RuntimeError("completed observation before interruption")
                return result

        class Events:
            def consume(self, event):
                pass

        logs = RunLogStore(root=tmp_path, run_id=scenario)
        request = SmokeVerificationRun(
            package=project.package,
            source_plan=SourcePlan.for_package(project.package, "DEVELOPMENT"),
            snapshot=project.snapshot,
            operation=Operation(),
            limits=RunLimits(max_cells=1, ty_jobs=1, test_jobs=1),
        )
        try:
            verification = VerificationRunner(
                events=Events(), logs=logs, host_target=project.package.cells[0].target
            )
            if scenario == "raise-after-pass":
                with pytest.raises(RuntimeError, match="completed observation"):
                    verification.run(request)
            else:
                assert verification.run(request)[0].status == "PASS"
            journal = logs.read_latest_journal(project.package.name)
            assert journal is not None
            assert journal.entries == ()
            assert len(journal.static_membership) == 1
            member = journal.static_membership[0]
            if scenario == "input-unavailable":
                assert member.highest.kind == "uncollected"
                assert member.highest.detail == "unreadable-content"
                cache = logs.read_ty_cache(journal.run_id)
                assert cache.entries == ()
            else:
                assert member.highest.kind == "collected"
                cache = logs.read_ty_cache(journal.run_id)
                admit_static_membership(journal, cache)
                assert cache.entries[0].document.fact.kind == (
                    "ty-check-unavailable"
                    if scenario == "ty-unavailable"
                    else "ty-check"
                )
                assert isinstance(
                    caches[0].lookup(
                        cache.entries[0].document.subject,
                        cache.entries[0].document.observation_policy,
                    ),
                    CacheMiss,
                )
        finally:
            project.snapshot.close()

    def _empty_artifacts(self, tmp_path: Path, run_id: str):
        from pf.schemas.ty_cache import TyCacheDocument

        journal = VerificationJournal(
            run_id=run_id,
            command="smoke",
            source_snapshot_digest="e91f3a54af54b8970f89b69b74a98cccf621f0cd7e6027d3996f49d3e8bacd81",
            package_policies=(
                VerificationPackagePolicy(
                    package="demo",
                    execution_policy_identity="d02a58ef756e371f8feec239e6570eb88b7f3938a72423a6b0949409a2e01fa6",
                ),
            ),
            entries=(),
            static_membership=(),
        )
        return journal, TyCacheDocument(run_id=run_id, entries=())

    def test_cache_write_failure_does_not_commit_journal_or_latest(self, tmp_path: Path):
        class Broken(RunLogStore):
            def write_ty_cache(self, cache):
                raise InfrastructureError("could not write PF ty-cache", detail="injected")

        broken = Broken(root=tmp_path, run_id="cache-fail")
        journal, cache = self._empty_artifacts(tmp_path, broken.run_id)
        with pytest.raises(InfrastructureError):
            broken.persist_run(journal, cache)
        assert not (broken._run_root / "journal.json").exists()
        assert broken.latest_journal_id("demo") is None

    def test_journal_write_failure_leaves_cache_without_latest(self, tmp_path: Path):
        class Broken(RunLogStore):
            def write_journal(self, journal, **kwargs):
                raise InfrastructureError("could not write PF verification journal", detail="injected")

        broken = Broken(root=tmp_path, run_id="journal-fail")
        journal, cache = self._empty_artifacts(tmp_path, broken.run_id)
        with pytest.raises(InfrastructureError):
            broken.persist_run(journal, cache)
        assert (broken._run_root / "ty-cache.json").exists()
        assert not (broken._run_root / "journal.json").exists()
        assert broken.latest_journal_id("demo") is None

    def test_latest_failure_keeps_journal_readable_by_run_id(self, tmp_path: Path):
        class Broken(RunLogStore):
            def publish_latest(self, journal):
                raise InfrastructureError("could not write PF diagnosis index", detail="injected")

        broken = Broken(root=tmp_path, run_id="latest-fail")
        journal, cache = self._empty_artifacts(tmp_path, broken.run_id)
        with pytest.raises(InfrastructureError):
            broken.persist_run(journal, cache)
        restored = broken.read_journal(broken.run_id)
        assert restored is not None
        assert restored.run_id == broken.run_id
        assert broken.latest_journal_id("demo") is None

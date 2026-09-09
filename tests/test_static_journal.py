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
from test_static_report import assert_interned_static_audit

FROZEN_ADMITTED_JOURNAL = (
    Path(__file__).resolve().parent / "fixtures" / "admitted-static-journal.json"
)


def _write_frozen_journal(tmp_path: Path):
    from pf.schemas.journal import VerificationJournal

    payload = json.loads(FROZEN_ADMITTED_JOURNAL.read_text(encoding="utf-8"))
    document = dict(payload)
    document["schema_version"] = document.pop("schema")
    journal = VerificationJournal.model_validate(document)
    store = RunLogStore(root=tmp_path, run_id=journal.run_id)
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
        scope = journal.static_scopes[0].scope
        policy = scope.facts[0].observation.observation_policy
        assert isinstance(
            caches[0].lookup(scope.consumers[0].preparation.subject, policy), CacheMiss
        )
        yield logs, journal, outcomes, events
    finally:
        snapshot.close()


class TestStaticJournal:
    @pytest.mark.process
    def test_real_pass_persists_raw_comparison_and_pass_after_run_close(
        self, actual_static_journal, tmp_path: Path, record_property
    ):
        logs, journal, outcomes, events = actual_static_journal
        assert all(outcome.status == "PASS" for outcome in outcomes)
        assert journal.schema_version == "verification-journal-v3"
        assert journal.entries == ()
        assert len(journal.static_scopes) == 1
        membership = journal.static_scopes[0]
        assert membership.run_id == journal.run_id
        scope = membership.scope
        assert (
            len(scope.facts)
            == len(scope.consumers)
            == len(scope.passes)
            == len(scope.comparisons)
            == 1
        )
        assert len(scope.processes) == 2
        comparison = scope.comparisons[0]
        replay = scope.compare(
            scope_ref=scope.scope_ref,
            subject_ref=comparison.subject_ref,
            reference_ref=comparison.reference_ref,
            context=comparison.context,
            guidance=comparison.guidance,
            anchor_pass_ref=comparison.anchor_pass_ref,
        )
        assert replay.identity == comparison.identity
        assert replay.result == comparison.result
        assert (
            next(
                event for event in events if isinstance(event, CellCompletedEvent)
            ).outcome.status
            == "PASS"
        )
        # Rewriting admitted portable evidence preserves this Run's typed producer logs.
        logs.write_journal(journal)
        from pf.static_association import static_producer_log_associations
        associations = static_producer_log_associations(scope)
        assert associations
        for producer_ref, _process in associations:
            path = logs.lookup_static(journal.run_id, scope.scope_ref, producer_ref)
            assert path is not None
            assert producer_ref.startswith(("ty-check:", "ty-check-unavailable:", "static-prepare:"))
            assert logs.read_tail(path) == (
                ("[]",) if producer_ref.startswith("ty-check:") else ("verified demo",)
            )
        assert all(
            logs.lookup_static(journal.run_id, scope.scope_ref, record.ref) is None
            for record in scope.processes
        )
        copy = RunLogStore(root=tmp_path, run_id=journal.run_id)
        path = copy.write_journal(journal)
        first = path.read_bytes()
        restored = copy.read_latest_journal(journal.packages[0])
        assert restored == journal
        assert restored is not None
        copy.write_journal(restored)
        assert path.read_bytes() == first
        assert_interned_static_audit(json.loads(first))
        record_property("journal_bytes", len(first))

    @pytest.mark.process
    def test_report_side_index_resolves_typed_producer_logs(
        self, actual_static_journal, tmp_path: Path,
    ):
        from pf.static_association import static_producer_log_associations

        logs, journal, _, _ = actual_static_journal
        logs.write_journal(journal)
        scope = journal.static_scopes[0].scope
        associations = static_producer_log_associations(scope)
        assert associations
        assert all(logs.reference_for(process) is None for _ref, process in associations)
        logs.index_report_static("generation", scope, replace_generation=True)
        for producer_ref, _process in associations:
            path = logs.lookup_report_static("generation", scope.scope_ref, producer_ref)
            journal_path = logs.lookup_static(journal.run_id, scope.scope_ref, producer_ref)
            assert path is not None
            assert path == journal_path
            assert logs.read_tail(path) is not None
        assert logs.lookup_report_static("generation", scope.scope_ref, "ty-check:missing") is None

    @pytest.mark.parametrize(
        "mutation",
        [
            "scope-run", "snapshot", "policy", "consumer", "comparison", "duplicate-cell",
            "dangling-fact", "unused-fact", "embedded-fact",
        ],
    )
    def test_reader_rejects_invalid_static_scope_evidence(
        self, tmp_path: Path, mutation
    ):
        store, journal, path = _write_frozen_journal(tmp_path)
        document = json.loads(path.read_text())
        if mutation == "scope-run":
            document["static_scopes"][0]["run_id"] = "another-run"
        elif mutation == "snapshot":
            document["source_snapshot_digest"] = "another-snapshot"
        elif mutation == "policy":
            document["package_policies"][0]["execution_policy_identity"] = (
                "another-policy"
            )
        elif mutation == "consumer":
            document["static_scopes"][0]["scope"]["comparisons"][0]["subject_ref"] = (
                "another-consumer"
            )
        elif mutation == "comparison":
            document["static_scopes"][0]["scope"]["comparisons"][0]["identity"] = (
                "0" * 64
            )
        elif mutation == "dangling-fact":
            document["static_scopes"][0]["scope"]["facts"][0]["observation_identity"] = (
                "0" * 64
            )
        elif mutation == "unused-fact":
            document["static_facts"].append(document["static_facts"][0])
        elif mutation == "embedded-fact":
            document["static_scopes"][0]["scope"]["facts"][0]["observation"] = {
                "subject_identity": document["static_facts"][0]["subject_identity"],
                "fact": document["static_facts"][0]["fact"],
            }
            del document["static_scopes"][0]["scope"]["facts"][0]["observation_identity"]
        else:
            document["static_scopes"].append(document["static_scopes"][0])
        path.write_text(json.dumps(document))
        with pytest.raises(JournalReadError) as caught:
            store.read_journal(journal.run_id)
        assert caught.value.reason == "invalid-static-evidence"

    @pytest.mark.parametrize("content", ["[]", "{invalid-json"])
    def test_reader_rejects_an_undecodable_contract(
        self, tmp_path: Path, content
    ):
        store, journal, path = _write_frozen_journal(tmp_path)
        path.write_text(content)
        with pytest.raises(JournalReadError) as caught:
            store.read_journal(journal.run_id)
        assert caught.value.reason == "unsupported-journal-contract"

    def test_reader_rejects_an_unsupported_contract(
        self, tmp_path: Path
    ):
        store, journal, path = _write_frozen_journal(tmp_path)
        document = json.loads(path.read_text())
        document["schema"] = "unsupported-journal-contract"
        path.write_text(json.dumps(document))
        with pytest.raises(JournalReadError) as caught:
            store.read_latest_journal(journal.packages[0])
        assert caught.value.reason == "unsupported-journal-contract"

    def test_many_observations_round_trip_in_one_scope(
        self, tmp_path: Path, record_property
    ):
        from evaluation_fixtures import evaluation_assembly, evaluation_project
        from pf.environment import HighestResolution, PreparedEnvironment
        from pf.schemas.journal import (
            JournalStaticScope,
            VerificationJournal,
            VerificationPackagePolicy,
        )
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
                assert len(scope.facts) == 12
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
                    static_scopes=(JournalStaticScope(run_id=store.run_id, scope=scope),),
                )
                path = store.write_journal(journal)
            restored = store.read_latest_journal(project.package.name)
            assert restored is not None
            assert restored.model_dump(mode="json") == journal.model_dump(mode="json")
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
            assert len(journal.static_scopes) == 1
            scope = journal.static_scopes[0].scope
            if scenario == "input-unavailable":
                assert scope.highest_uncollected is not None
                assert (
                    scope.highest_uncollected.unavailable.detail == "unreadable-content"
                )
                assert scope.facts == scope.processes == scope.passes == ()
            else:
                assert len(scope.facts) == len(scope.passes) == 1
                assert scope.facts[0].observation.fact.kind == (
                    "ty-check-unavailable"
                    if scenario == "ty-unavailable"
                    else "ty-check"
                )
                assert isinstance(
                    caches[0].lookup(
                        scope.facts[0].observation.subject,
                        scope.facts[0].observation.observation_policy,
                    ),
                    CacheMiss,
                )
        finally:
            project.snapshot.close()

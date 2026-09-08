"""Search Run scope persistence through the public report boundary."""
from __future__ import annotations

import json
from pathlib import Path
from jsonschema import Draft202012Validator

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.ty import TyAdapter
from pf.adapters.uv import UvAdapter
from pf.baseline import HighestVersionVerifier
from pf.candidates import CandidateBuilder
from pf.coordinate_search import CoordinateSearch
from pf.environment import EnvironmentFactory
from pf.errors import ConfigurationError
from pf.evaluation import RuntimeEvaluator, StaticEvaluator
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ReportStore
from pf.schemas.policy import GuidancePolicy
from pf.schemas.project import SourcePlan
from pf.schemas.config import RunLimits, SearchRequest
from pf.schemas.static import StaticContentUnavailable
from pf.workflow import SearchCommandWorkflow
from pf.schemas.report import CellSuccess
from pf.schemas.static_comparison import GlobalComparisonContext
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder
from pf.static_cache import RunTyFactRef
from pf.static_request import StaticRequestFactory
from pf.adapters.test_command import ConfiguredVerifier
from pf.verification import SearchVerificationRun, VerificationRunner


def assert_interned_static_audit(container) -> None:
    facts = container["static_facts"]
    comparisons = container["static_comparisons"]
    contents = container.get("static_contents", [])
    subjects = container.get("static_subjects", [])
    identities = [item["identity"] for item in facts]
    assert identities == sorted(set(identities))
    comparison_ids = [item["identity"] for item in comparisons]
    assert comparison_ids == sorted(set(comparison_ids))
    assert [item["identity"] for item in contents] == sorted({item["identity"] for item in contents})
    assert [item["identity"] for item in subjects] == sorted({item["identity"] for item in subjects})
    referenced_facts = set()
    referenced_comparisons = set()
    for scope in container["static_scopes"]:
        body = scope["scope"] if "scope" in scope and "facts" not in scope else scope
        for member in body["facts"]:
            assert "observation" not in member
            referenced_facts.add(member["observation_identity"])
        for member in body["comparisons"]:
            assert "context" not in member
            referenced_comparisons.add(member["identity"])
        for member in body["consumers"]:
            assert "subject" not in member["preparation"]
            assert member["subject_identity"]
    assert referenced_facts == set(identities)
    assert referenced_comparisons == set(comparison_ids)
    for item in facts:
        assert "observation" not in item
        assert "subject_identity" in item


def _scripted_search_report(root, *, unavailable=False, source="VALUE = 1\n", project_only=False):
    from evaluation_fixtures import evaluation_assembly, evaluation_project, successful_process
    from pf.schemas.evaluation import (
        NormalExit, VerifierDiagnostics, VerifierPass, VerifierRejected, VerifierRun,
    )
    from pf.static_cache import TyCheckCache

    class UnavailableRequests:
        def capture(self, prepared, **kwargs):
            return StaticContentUnavailable(detail="unreadable-content")

    def collected_verifier(vector, _call):
        passed = int(vector[0].version) >= 2
        return VerifierRun(
            authoritative=(
                VerifierPass(terminal=NormalExit(exit_code=0)) if passed
                else VerifierRejected(terminal=NormalExit(exit_code=1))
            ),
            diagnostics=VerifierDiagnostics(process=successful_process(exit_code=0 if passed else 1)),
        )

    def project_only_verifier(_vector, _call):
        return VerifierRun(
            authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
            diagnostics=VerifierDiagnostics(process=successful_process()),
        )

    if unavailable or project_only:
        project = evaluation_project(root, dependency=None, source=source)
        assembly = evaluation_assembly(
            highest=(),
            static_requests=UnavailableRequests() if unavailable else None,
            verifier_handler=project_only_verifier,
        )
    else:
        project = evaluation_project(root, source=source)
        assembly = evaluation_assembly(verifier_handler=collected_verifier)
    try:
        with TyCheckCache() as cache:
            result = assembly.coordinator.search(
                run_cache=cache,
                package=project.package,
                cell=project.package.cells[0],
                snapshot=project.snapshot,
                source_plan=project.source_plan,
            )
            assert result.status == "SUCCESS", result
            scope = cache.snapshot(project.package.cells[0])
            if scope.consumers and scope.facts:
                member = scope.consumers[0]
                policy = scope.facts[0].observation.observation_policy
                fact = cache.lookup(member.preparation.subject, policy)
                if isinstance(fact, RunTyFactRef):
                    consumer = cache.consumer(fact, member.preparation)
                    assembly.static.compare(
                        consumer, consumer, run_cache=cache,
                        context=GlobalComparisonContext(
                            highest_proposal_id=member.preparation.proposal.proposal_id,
                        ),
                        guidance_policy=GuidancePolicy(
                            observation=policy, observation_identity=policy.identity,
                        ),
                    )
                    scope = cache.snapshot(project.package.cells[0])
            report = PackageReportBuilder().build(
                package=project.package,
                source_plan=project.source_plan,
                source_snapshot=project.snapshot.identity,
                cell_results=(result,),
                static_scopes=(scope,),
            )
        return project.package, report
    finally:
        project.snapshot.close()


@pytest.fixture(scope="module")
def actual_report(tmp_path_factory):
    root = tmp_path_factory.mktemp("static-report")
    (root / "src/demo").mkdir(parents=True)
    (root / "src/demo/__init__.py").write_text("VALUE = 1\n")
    (root / "pyproject.toml").write_text('''
[project]
name = "demo"
version = "1"
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
''')
    package = ProjectLoader().load(root=root).target
    plan = SourcePlan.for_package(package, "SEARCH")
    process = SubprocessRunner()
    uv = UvAdapter(process)
    environments = EnvironmentFactory(uv)
    static = StaticEvaluator(TyAdapter(process), requests=StaticRequestFactory(process))
    full = RuntimeEvaluator(verifier=ConfiguredVerifier(process))
    highest = HighestVersionVerifier(environments=environments, static=static, full=full)
    coordinator = SearchCoordinator(environments=environments, candidates=CandidateBuilder(uv),
                                    static=static, full=full, highest=highest,
                                    coordinate_search=CoordinateSearch())
    caches = []

    class Events:
        def consume(self, event):
            pass

    class Operation:
        def search(self, *, run_cache, **kwargs):
            caches.append(run_cache)
            result = coordinator.search(run_cache=run_cache, **kwargs)
            assert result.status == "SUCCESS", result
            scope = run_cache.snapshot(result.cell)
            if scope.consumers:
                member = scope.consumers[0]
                policy = scope.facts[0].observation.observation_policy
                fact = run_cache.lookup(member.preparation.subject, policy)
                assert isinstance(fact, RunTyFactRef)
                consumer = run_cache.consumer(fact, member.preparation)
                static.compare(consumer, consumer, run_cache=run_cache,
                               context=GlobalComparisonContext(highest_proposal_id=member.preparation.proposal.proposal_id),
                               guidance_policy=GuidancePolicy(observation=policy, observation_identity=policy.identity))
            return result

    snapshot = SnapshotBuilder(process).build(root)
    try:
        run = VerificationRunner(events=Events(), logs=None, host_target=package.cells[0].target).run(
            SearchVerificationRun(package=package, source_plan=plan, snapshot=snapshot,
                                  operation=Operation(), limits=RunLimits(max_cells=1, ty_jobs=1, test_jobs=1)),
        )
        identity = snapshot.identity
    finally:
        snapshot.close()
    with pytest.raises(ValueError, match="closed"):
        caches[0].snapshot(package.cells[0])
    report = PackageReportBuilder().build(package=package, source_plan=plan, source_snapshot=identity,
                                        cell_results=run.cell_results, static_scopes=run.static_scopes)
    return package, report, root, coordinator


@pytest.fixture(scope="module")
def scripted_report(tmp_path_factory):
    return _scripted_search_report(tmp_path_factory.mktemp("scripted-report"))


@pytest.fixture(scope="module")
def scripted_uncollected_report(tmp_path_factory):
    return _scripted_search_report(
        tmp_path_factory.mktemp("scripted-uncollected"), unavailable=True,
    )


class TestStaticReport:
    def test_search_workflow_persists_its_fresh_run_scope(self, actual_report):
        package, previous, root, coordinator = actual_report
        class Events:
            def consume(self, event):
                pass
        events = Events()
        store = ReportStore()
        result = SearchCommandWorkflow(
            projects=ProjectLoader(), snapshots=SnapshotBuilder(SubprocessRunner()),
            coordinator=coordinator,
            verification=VerificationRunner(events=events, logs=None, host_target=package.cells[0].target),
            reports=store, report_builder=PackageReportBuilder(), events=events,
        ).run(SearchRequest(root=str(root)))
        restored = store.read(root / result.report_path)
        scope, = restored.static_scopes
        assert scope.scope_ref != previous.static_scopes[0].scope_ref
        assert len(scope.facts) == len(scope.consumers) == len(scope.passes) == 1
        assert restored.result.status == "complete"

    def test_uncollected_highest_keeps_full_dynamic_pass(self, scripted_uncollected_report, tmp_path):
        _, report = scripted_uncollected_report
        scope, = report.static_scopes
        assert scope.highest_uncollected is not None
        assert scope.highest_uncollected.unavailable.detail == "unreadable-content"
        assert scope.highest_reference_ref is None
        assert scope.facts == scope.consumers == scope.passes == scope.processes == ()
        result = report.cell_results[0]
        assert isinstance(result, CellSuccess)
        assert result.final_evaluation.verifier.status == "PASS"
        assert report.result.status == "complete"
        path = tmp_path / "uncollected.json"
        ReportStore().write(path, report)
        schema = json.loads((Path(__file__).parents[1] / "docs/schemas/package-floor-v1.schema.json").read_text())
        Draft202012Validator(schema).validate(json.loads(path.read_text()))

    def test_real_scope_survives_run_close_and_byte_stable_report_roundtrip(self, actual_report, tmp_path):
        _, report, _root, _coordinator = actual_report
        path = tmp_path / "report.json"
        store = ReportStore()
        store.write(path, report)
        original = path.read_bytes()
        schema = json.loads((Path(__file__).parents[1] / "docs/schemas/package-floor-v1.schema.json").read_text())
        Draft202012Validator(schema).validate(json.loads(original))
        restored = store.read(path)
        store.write(path, restored)
        assert path.read_bytes() == original
        assert_interned_static_audit(json.loads(original)["evidence"])
        scope, = restored.static_scopes
        assert len(scope.facts) == len(scope.consumers) == len(scope.passes) == len(scope.comparisons) == 1
        comparison = scope.comparisons[0]
        replay = scope.compare(scope_ref=scope.scope_ref, subject_ref=comparison.subject_ref,
                               reference_ref=comparison.reference_ref, context=comparison.context,
                               guidance=comparison.guidance, anchor_pass_ref=comparison.anchor_pass_ref)
        assert replay.result == comparison.result
        assert replay.identity == comparison.identity
        result = restored.cell_results[0]
        assert isinstance(result, CellSuccess)
        assert scope.passes[0].evidence.proposal_id == result.baseline.proposal.proposal_id

    @pytest.mark.parametrize("mutation", ("root", "anchor", "comparison", "cell-null", "missing-nullable"))
    def test_reader_rejects_broken_scope_authority(self, scripted_report, tmp_path, mutation):
        _, report = scripted_report
        path = tmp_path / "report.json"
        ReportStore().write(path, report)
        document = json.loads(path.read_text())
        scope = document["evidence"]["static_scopes"][0]
        if mutation == "root":
            scope["highest_reference_ref"] = "missing"
        elif mutation == "anchor":
            scope["passes"][0]["evidence"]["proposal_id"] = "foreign"
        elif mutation == "comparison":
            scope["comparisons"][0]["reference_ref"] = "missing"
        elif mutation == "cell-null":
            scope["cell"] = None
        else:
            del scope["highest_uncollected"]
        path.write_text(json.dumps(document))
        with pytest.raises(ConfigurationError):
            ReportStore().read(path)

    def test_builder_rejects_valid_scope_from_another_source_context(self, tmp_path_factory):
        package, report = _scripted_search_report(
            tmp_path_factory.mktemp("scripted-scope"), project_only=True, source="VALUE = 1\n",
        )
        _, foreign = _scripted_search_report(
            tmp_path_factory.mktemp("scripted-foreign"), project_only=True, source="VALUE = 2\n",
        )
        with pytest.raises(ConfigurationError, match="scope input context"):
            PackageReportBuilder().build(package=package, source_plan=report.source_plan,
                                        source_snapshot=report.source_snapshot, cell_results=report.cell_results,
                                        static_scopes=foreign.static_scopes)

    def test_merge_renames_colliding_local_scope_without_joining_membership(
        self, scripted_report, tmp_path,
    ):
        _, report = scripted_report
        merged = ReportStore().merge((report, report))
        first, second = merged.static_scopes
        assert first.scope_ref != second.scope_ref
        assert first.comparisons[0].identity == second.comparisons[0].identity
        assert first.facts == second.facts
        comparison = second.comparisons[0]
        with pytest.raises(ValueError):
            second.compare(scope_ref=first.scope_ref, subject_ref=comparison.subject_ref,
                           reference_ref=comparison.reference_ref, context=comparison.context,
                           guidance=comparison.guidance)
        path = tmp_path / "merged.json"
        ReportStore().write(path, merged)
        document = json.loads(path.read_text())
        assert_interned_static_audit(document["evidence"])
        assert len(document["evidence"]["static_facts"]) == len({
            member.observation.fact_identity for member in report.static_scopes[0].facts
        })
        assert len(document["evidence"]["static_scopes"]) == 2
        assert (
            [member["observation_identity"] for member in document["evidence"]["static_scopes"][0]["facts"]]
            == [member["observation_identity"] for member in document["evidence"]["static_scopes"][1]["facts"]]
        )

    @pytest.mark.parametrize("mutation", ("regions", "witnesses", "runtime-interface-missing"))
    def test_reader_rejects_retired_static_authority_fields(self, scripted_report, tmp_path, mutation):
        _, report = scripted_report
        path = tmp_path / "retired.json"
        ReportStore().write(path, report)
        document = json.loads(path.read_text())
        if mutation == "regions":
            document["cell_results"][0]["regions"] = []
        elif mutation == "witnesses":
            document["evidence"]["evaluations"][0]["witnesses"] = []
        else:
            document["evidence"]["evaluations"][0]["status"] = "RUNTIME_INTERFACE_MISSING"
        path.write_text(json.dumps(document))
        with pytest.raises(ConfigurationError) as caught:
            ReportStore().read(path)
        assert caught.value.reason in {None, "unsupported-report-contract", "invalid-static-evidence"}

    @pytest.mark.parametrize(
        "mutation",
        (
            "dangling-fact",
            "unused-fact",
            "fact-identity",
            "unused-content",
            "dangling-subject",
            "unused-subject",
            "dangling-content",
            "consumer-subject-mismatch",
            "unused-comparison",
        ),
    )
    def test_reader_rejects_broken_static_intern(self, scripted_report, tmp_path, mutation):
        _, report = scripted_report
        path = tmp_path / "report.json"
        ReportStore().write(path, report)
        document = json.loads(path.read_text())
        evidence = document["evidence"]
        if mutation == "dangling-fact":
            evidence["static_scopes"][0]["facts"][0]["observation_identity"] = "0" * 64
        elif mutation == "unused-fact":
            evidence["static_facts"].append(evidence["static_facts"][0])
        elif mutation == "fact-identity":
            evidence["static_facts"][0]["identity"] = "0" * 64
        elif mutation == "unused-content":
            evidence["static_contents"].append(evidence["static_contents"][0])
        elif mutation == "unused-subject":
            evidence["static_subjects"].append(evidence["static_subjects"][0])
        elif mutation == "dangling-content":
            evidence["static_subjects"][0]["source"]["content_identity"] = "0" * 64
        elif mutation == "consumer-subject-mismatch":
            evidence["static_scopes"][0]["consumers"][0]["subject_identity"] = "0" * 64
        elif mutation == "unused-comparison":
            evidence["static_comparisons"].append(evidence["static_comparisons"][0])
        else:
            evidence["static_facts"][0]["subject_identity"] = "0" * 64
        path.write_text(json.dumps(document))
        with pytest.raises(ConfigurationError):
            ReportStore().read(path)

    def test_update_retains_replacement_scope(self, scripted_report):
        package, report = scripted_report
        scope = report.static_scopes[0].model_copy(update={"scope_ref": "replacement"})
        replacement = PackageReportBuilder().build(package=package, source_plan=report.source_plan,
                                                  source_snapshot=report.source_snapshot,
                                                  cell_results=report.cell_results, static_scopes=(scope,))
        updated = ReportStore().update(report, replacement)
        assert tuple(item.scope_ref for item in updated.static_scopes) == ("replacement",)
        assert updated.cell_results == report.cell_results

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
    """Journal intern shape until S4; public reports no longer carry these tables."""
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


INTERN_FIELDS = (
    "static_contents",
    "static_subjects",
    "static_facts",
    "static_comparisons",
    "static_scopes",
)


def assert_report_has_no_static_intern(document: dict) -> None:
    evidence = document["evidence"]
    assert all(name not in evidence for name in INTERN_FIELDS)
    assert all(name not in document for name in INTERN_FIELDS)


def public_selection_reasons(report) -> list:
    reasons = []
    for result in report.cell_results:
        search = getattr(result, "search", None) or getattr(result, "coordinate_failure", None)
        if search is None:
            continue
        for observation in search.observations:
            if observation.dependency is None:
                assert observation.selection_reason is None
            else:
                assert observation.selection_reason is not None
            reasons.append(observation.selection_reason)
    return reasons


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
                                        cell_results=run.cell_results)
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
    @pytest.mark.process
    def test_search_workflow_writes_a_report_without_static_intern(self, actual_report, tmp_path):
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
        assert restored.result.status == "complete"
        assert isinstance(restored.cell_results[0], CellSuccess)
        public_selection_reasons(restored)
        path = tmp_path / "report.json"
        store.write(path, restored)
        original = path.read_bytes()
        schema = json.loads((Path(__file__).parents[1] / "docs/schemas/package-floor-v1.schema.json").read_text())
        Draft202012Validator(schema).validate(json.loads(original))
        assert_report_has_no_static_intern(json.loads(original))
        reread = store.read(path)
        store.write(path, reread)
        assert path.read_bytes() == original
        assert previous.result.status == "complete"

    def test_uncollected_highest_keeps_full_dynamic_pass(self, scripted_uncollected_report, tmp_path):
        _, report = scripted_uncollected_report
        result = report.cell_results[0]
        assert isinstance(result, CellSuccess)
        assert result.final_evaluation.verifier.status == "PASS"
        assert report.result.status == "complete"
        public_selection_reasons(report)
        path = tmp_path / "uncollected.json"
        ReportStore().write(path, report)
        document = json.loads(path.read_text())
        schema = json.loads((Path(__file__).parents[1] / "docs/schemas/package-floor-v1.schema.json").read_text())
        Draft202012Validator(schema).validate(document)
        assert_report_has_no_static_intern(document)

    @pytest.mark.parametrize("field", INTERN_FIELDS)
    def test_reader_rejects_old_intern_tables(self, scripted_report, tmp_path, field):
        _, report = scripted_report
        path = tmp_path / "report.json"
        ReportStore().write(path, report)
        document = json.loads(path.read_text())
        document["evidence"][field] = []
        path.write_text(json.dumps(document))
        with pytest.raises(ConfigurationError):
            ReportStore().read(path)

    def test_update_path_treats_intern_existing_as_absent(self, scripted_report, tmp_path):
        package, report = scripted_report
        path = tmp_path / "stale-intern.json"
        store = ReportStore()
        store.write(path, report)
        document = json.loads(path.read_text())
        document["evidence"]["static_scopes"] = []
        path.write_text(json.dumps(document))
        replacement = PackageReportBuilder().build(
            package=package,
            source_plan=report.source_plan,
            source_snapshot=report.source_snapshot,
            cell_results=report.cell_results,
        )
        update = store.update_path(path, replacement)
        assert update.replace_generation is True
        assert_report_has_no_static_intern(json.loads(path.read_text()))

    def test_merge_keeps_cell_results_without_static_tables(self, scripted_report, tmp_path):
        _, report = scripted_report
        merged = ReportStore().merge((report, report))
        assert merged.cell_results == report.cell_results
        path = tmp_path / "merged.json"
        ReportStore().write(path, merged)
        document = json.loads(path.read_text())
        assert_report_has_no_static_intern(document)

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

    def test_update_retains_replacement_cells(self, scripted_report):
        package, report = scripted_report
        replacement = PackageReportBuilder().build(
            package=package,
            source_plan=report.source_plan,
            source_snapshot=report.source_snapshot,
            cell_results=report.cell_results,
        )
        updated = ReportStore().update(report, replacement)
        assert updated.cell_results == report.cell_results

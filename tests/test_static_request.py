from __future__ import annotations

from pf.cancellation import Cancellation

from pathlib import Path
import shutil
import sys
import copy

import pytest

from pf.adapters.process import SubprocessRunner
from pf.adapters.ty import TyAdapter
from pf.errors import MaterializationIntegrityError
from pf.evaluation import StaticEvaluator
from pf.static_cache import RunTyFactRef, TyCheckCache
from pf.schemas.ty_fact import TyCheckFact
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, HighestResolution, LowestDirectResolution, ExactSelection, PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.evaluation import TyCheck
from pf.schemas.project import SourcePlan
from pf.schemas.static import StaticContentUnavailable
from pf.schemas.ty_fact import TyFactDocument
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.snapshot import SnapshotBuilder
from pf.static_request import StaticRequestFactory, StaticTyRequest
from pf.ty_fact import ty_fact_document
from pf.static_admission import admit_harness_relation, admit_common_static_context


class RecordingRunner(SubprocessRunner):
    def __init__(self):
        super().__init__()
        self.ty_checks = 0


class CountingTy(TyAdapter):
    def __init__(self, runner: RecordingRunner) -> None:
        super().__init__(runner)
        self._recording = runner

    def observe(self, request, *, cancellation: Cancellation | None = None):
        observed = super().observe(request, cancellation=cancellation)
        if not isinstance(observed, StaticContentUnavailable):
            self._recording.ty_checks += 1
        return observed


def run_complete_prepared_request(
    tmp_path: Path, *, external_stub: bool, relocate: bool, resolution_kind: str,
) -> None:
        project, home = tmp_path / "project", tmp_path / "home"
        (project / "src/demo").mkdir(parents=True)
        home.mkdir()
        (project / "src/demo/__init__.py").write_text("VALUE: str = 1\n")
        (project / "ignored.py").write_text("VALUE: int = 'ignored'\n")
        (project / ".ignore").write_text("ignored.py\n")
        environment = {"HOME": str(home), "GIT_CONFIG_SYSTEM": str(tmp_path / "no-system-config")}
        if external_stub:
            stubs = project / "stubs"
            stubs.mkdir()
            (stubs / "helper.pyi").write_text("VALUE: int\n")
            (project / "ty.toml").write_text('[environment]\nextra-paths=["stubs"]\n')
            (project / "src/demo/__init__.py").write_text("from helper import VALUE\nvalid: int = VALUE\nwrong: str = 1\n")
        (project / "pyproject.toml").write_text('''
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
        package = ProjectLoader().load(root=project).target
        source_plan = SourcePlan.for_package(package, "SEARCH")
        runner = RecordingRunner()
        snapshot = SnapshotBuilder(runner).build(project)
        prepared = None
        cache = TyCheckCache()
        static = StaticEvaluator(CountingTy(runner), requests=StaticRequestFactory(runner))
        try:
            prepared = EnvironmentFactory(UvAdapter(runner)).prepare(package=package, cell=package.cells[0], snapshot=snapshot,
                                                                     resolution=HighestResolution(), source_plan=source_plan)
            assert isinstance(prepared, PreparedEnvironment)
            if resolution_kind != "highest":
                baseline = prepared.harness_baseline
                prepared.close()
                prepared = EnvironmentFactory(UvAdapter(runner)).prepare(
                    package=package, cell=package.cells[0], snapshot=snapshot,
                    resolution=(LowestDirectResolution(baseline) if resolution_kind == "lowest-direct"
                                else ExactSelection(selection=(), harness_baseline=baseline)),
                    source_plan=source_plan,
                )
                assert isinstance(prepared, PreparedEnvironment)
            executable = shutil.which("ty")
            assert executable is not None
            request = StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                prepared, package=package, 
                environment=environment,
            )
            assert isinstance(request, StaticTyRequest), request
            assert StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                prepared, package=package, environment=environment,
            ) is request
            assert request.spec.environment_mode == "explicit"
            argv = request.spec.argv
            for option, value in (
                ("--python", str(prepared.interpreter)),
                ("--python-version", prepared.proposal.cell.python_minor),
                ("--python-platform", sys.platform),
                ("--output-format", "gitlab"),
                ("--color", "never"),
            ):
                assert argv.count(option) == 1
                assert argv[argv.index(option) + 1] == value
            assert "--no-progress" in argv
            assert argv[-1] == str(prepared.package_root)
            assert request.spec.cwd == str(prepared.package_root)
            assert request.subject.source_snapshot_digest == snapshot.identity.digest
            assert request.subject.cell.package == prepared.proposal.cell.package
            assert prepared.proposal.interpreter is not None
            assert request.subject.interpreter.implementation == prepared.proposal.interpreter.implementation
            assert request.subject.interpreter.abi == prepared.proposal.interpreter.abi
            assert request.revalidate()
            saved_preparation = request.preparation.model_dump_json()
            assert StaticPreparationEvidence.model_validate_json(saved_preparation) == request.preparation
            if not external_stub and not relocate:
                original = request.preparation.model_dump(mode="json")
                for field, value in (
                    ("attempt_id", "f" * 64),
                    ("snapshot_digest", "f" * 64),
                    ("policy_identity", "different-execution-policy"),
                    ("project_plan_digest", "f" * 64),
                    ("proposal_id", "f" * 64),
                    ("interpreter", None),
                ):
                    forged = copy.deepcopy(original)
                    forged["proposal"][field] = value
                    with pytest.raises(ValueError, match="static preparation"):
                        StaticPreparationEvidence.model_validate(forged)
            if external_stub:
                shutil.rmtree(stubs)
                shutil.rmtree(home)
            ref = static.collect(prepared, request, run_cache=cache)
            assert isinstance(ref, RunTyFactRef), ref
            observed = ref.observation.fact
            assert isinstance(observed, TyCheckFact)
            assert static.collect(prepared, request, run_cache=cache) is ref
            assert [(item.path, item.code) for item in observed.diagnostics] == [("src/demo/__init__.py", "invalid-assignment")]
            document = ref.observation
            encoded = document.model_dump_json()
            assert runner.ty_checks == 1
            if relocate:
                moved = prepared.relocate_to(request)
                prepared.close()
                prepared = moved
                restored_request = StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                    prepared, package=package,  environment=environment,
                )
                assert isinstance(restored_request, StaticTyRequest), restored_request
                assert restored_request.subject == request.subject
                assert restored_request.observation_policy == request.observation_policy
                assert static.collect(prepared, restored_request, run_cache=cache) is ref
                assert runner.ty_checks == 1
                request = restored_request
            elif not external_stub:
                rebuilt = prepared.relocate_to(request)
                (prepared.environment_root / "changed.txt").write_text("external mutation")
                assert static.collect(prepared, request, run_cache=cache) is ref
                assert prepared.inputs_valid
                assert runner.ty_checks == 1
                prepared.invalidate_inputs()
                assert isinstance(static.collect(prepared, request, run_cache=cache), StaticContentUnavailable)
                assert isinstance(StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                    prepared, package=package, environment=environment,
                ), StaticContentUnavailable)
                with pytest.raises(MaterializationIntegrityError), prepared.verifier_use():
                    pytest.fail("invalid inputs reached verifier")
                prepared.close()
                prepared = rebuilt
                metadata_candidates = list(prepared.environment_root.rglob("*.dist-info/METADATA"))
                assert metadata_candidates
                metadata_candidates[0].write_text(
                    metadata_candidates[0].read_text() + "Summary: changed installed bytes\n",
                )
                changed = StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                    prepared, package=package,  environment=environment,
                )
                assert isinstance(changed, StaticTyRequest), changed
                assert changed.subject.identity == request.subject.identity
                assert changed.subject.resolution_projection == request.subject.resolution_projection
                assert changed.observation_policy == request.observation_policy
                changed_ref = static.collect(prepared, changed, run_cache=cache)
                assert changed_ref is ref
                assert runner.ty_checks == 1
                request = changed
            prepared.mark_tested()
            assert isinstance(static.collect(prepared, request, run_cache=cache), StaticContentUnavailable)
            assert runner.ty_checks == 1
        finally:
            if isinstance(prepared, PreparedEnvironment):
                prepared.close()
            snapshot.close()
            cache.stop()
        assert static.lookup(document.subject, observation_policy=document.observation_policy, run_cache=cache) is ref
        cache.close()
        assert StaticPreparationEvidence.model_validate_json(saved_preparation).proposal == request.preparation.proposal
        restored = TyFactDocument.model_validate_json(encoded)
        assert restored.fact_identity == document.fact_identity
        assert restored.model_dump_json() == encoded


@pytest.mark.process
class TestRealStaticRequest:
    def test_complete_prepared_request_observes_and_survives_environment_close(self, tmp_path: Path) -> None:
        run_complete_prepared_request(
            tmp_path, external_stub=False, relocate=False, resolution_kind="highest",
        )

    def test_relocation_reuses_the_run_cache(self, tmp_path: Path) -> None:
        run_complete_prepared_request(
            tmp_path, external_stub=False, relocate=True, resolution_kind="highest",
        )


def run_nonempty_static_preparation(
    tmp_path: Path, *, resolution_kind: str, secondary_managed: bool,
) -> None:
        from pf.resolution import ResolutionPlanEvidence, resolution_semantic_digest, environment_identity_digest

        project = tmp_path / "project"
        (project / "src/demo").mkdir(parents=True)
        (project / "src/demo/__init__.py").write_text("VALUE: int = 1\n")
        (project / "pyproject.toml").write_text('''
[project]
name = "demo"
version = "1"
dependencies = ["idna>=3.10"]
[dependency-groups]
test = ["idna>=3.10", "packaging>=24.0"]
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-command = ["python", "-c", "import demo"]
[tool.ty.rules]
invalid-assignment = "error"
''')
        if secondary_managed:
            pyproject = project / "pyproject.toml"
            pyproject.write_text(pyproject.read_text().replace('dependencies = ["idna>=3.10"]',
                                                              'dependencies = ["idna>=3.10", "packaging>=24.0"]'))
        package = ProjectLoader().load(root=project).target
        source_plan = SourcePlan.for_package(package, "SEARCH")
        runner = RecordingRunner()
        snapshot = SnapshotBuilder(runner).build(project)
        prepared = None
        try:
            factory = EnvironmentFactory(UvAdapter(runner))
            prepared = factory.prepare(package=package, cell=package.cells[0], snapshot=snapshot,
                                       resolution=HighestResolution(), source_plan=source_plan)
            assert isinstance(prepared, PreparedEnvironment), prepared
            executable = shutil.which("ty")
            assert executable is not None
            baseline_request = StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                prepared, package=package, 
                environment={"HOME": str(tmp_path / "home"), "GIT_CONFIG_SYSTEM": str(tmp_path / "no-git-config")},
            )
            assert isinstance(baseline_request, StaticTyRequest), baseline_request
            baseline_observed = TyAdapter(runner).observe(baseline_request)
            assert isinstance(baseline_observed, TyCheck), baseline_observed
            baseline_consumer = StaticConsumerEvidence(
                preparation=baseline_request.preparation,
                observation=ty_fact_document(baseline_request.subject, baseline_request.observation_policy, baseline_observed),
            )
            baseline = prepared.harness_baseline
            assert {item.name for item in baseline.observations} == {"idna", "packaging"}
            selection = selected_candidates_for_prepared(prepared)
            anchor_pass = None
            if resolution_kind == "exact-vector":
                from pf.adapters.test_command import ConfiguredVerifier
                from pf.schemas.evaluation import VerifierRequest, EnvironmentVariable
                from pf.schemas.static_comparison import SliceAnchorPass
                import os

                verifier_run = ConfiguredVerifier(runner).run(VerifierRequest(
                    command=package.config.test.command, cwd=prepared.package_root,
                    environment=(EnvironmentVariable(name="PATH", value=os.pathsep.join(
                        (str(prepared.interpreter.parent), os.environ.get("PATH", "")))),),
                    timeout_seconds=package.config.test.timeout_seconds,
                ))
                prepared.mark_tested()
                anchor_pass = SliceAnchorPass.from_run(proposal=prepared.proposal, run=verifier_run)
            if resolution_kind != "highest":
                prepared.close()
                prepared = factory.prepare(package=package, cell=package.cells[0], snapshot=snapshot,
                                           resolution=(LowestDirectResolution(baseline) if resolution_kind == "lowest-direct"
                                                       else ExactSelection(selection=selection, harness_baseline=baseline)),
                                           source_plan=source_plan)
                assert isinstance(prepared, PreparedEnvironment), prepared
            executable = shutil.which("ty")
            assert executable is not None
            request = baseline_request if resolution_kind == "highest" else StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                prepared, package=package, 
                environment={"HOME": str(tmp_path / "home"), "GIT_CONFIG_SYSTEM": str(tmp_path / "no-git-config")},
            )
            assert isinstance(request, StaticTyRequest), request
            evidence = request.preparation
            assert admit_common_static_context(baseline_request.preparation, evidence)
            assert admit_harness_relation(baseline_request.preparation, evidence)
            assert admit_harness_relation(evidence, evidence)
            assert admit_harness_relation(evidence, baseline_request.preparation) == (resolution_kind == "highest")
            changed_group = evidence.model_dump(mode="json")
            changed_group["selected_test_group"] = "another-group"
            assert not admit_harness_relation(baseline_request.preparation,
                                              StaticPreparationEvidence.model_validate(changed_group))
            assert evidence.environment_plan is not None
            assert {item.name for item in evidence.subject.resolution_projection} == {"idna", "packaging"}
            assert {item.artifact.kind for item in evidence.subject.resolution_projection} == {"available-set"}
            assert evidence.harness_baseline == baseline
            assert evidence.selected_candidates == (selection if resolution_kind == "exact-vector" else None)
            assert evidence.attempt.identity.requested_resolution == resolution_kind
            observed = TyAdapter(runner).observe(request)
            assert isinstance(observed, TyCheck), observed
            assert observed.diagnostics == ()
            consumer = StaticConsumerEvidence(
                preparation=evidence,
                observation=ty_fact_document(request.subject, request.observation_policy, observed),
            )
            assert StaticConsumerEvidence.model_validate_json(consumer.model_dump_json()) == consumer
            assert_global_comparison_contract(baseline_consumer, consumer)
            if anchor_pass is not None:
                assert_slice_comparison_contract(baseline_consumer, consumer, anchor_pass, selection)
                assert verifier_run.diagnostics is not None
                assert_static_scope_contract(baseline_consumer, consumer, anchor_pass, selection,
                                             baseline_observed.process, observed.process, verifier_run.diagnostics.process)
            other_subject = evidence.subject.model_copy(
                update={"source_snapshot_digest": "e" * 64},
            )
            with pytest.raises(ValueError, match="consumer projection"):
                StaticConsumerEvidence(
                    preparation=evidence,
                    observation=ty_fact_document(
                        other_subject, request.observation_policy, observed,
                    ),
                )
            # Recompute enclosing identities too: matching copied digests alone
            # must not admit a request that was never derived from these inputs.
            forged_plan_payload = evidence.project_plan.model_dump(mode="json")
            forged_plan_payload["request_digest"] = "f" * 64
            forged_plan_payload["semantic_digest"] = resolution_semantic_digest(
                kind="project", request_digest="f" * 64, context=evidence.project_plan.context,
                packages=evidence.project_plan.packages, direct_harness=(),
            )
            forged_plan = ResolutionPlanEvidence.model_validate(forged_plan_payload)
            forged = evidence.model_dump(mode="json")
            forged["project_plan"] = forged_plan.model_dump(mode="json")
            forged["proposal"]["project_plan_digest"] = forged_plan.semantic_digest
            forged["proposal"]["proposal_id"] = environment_identity_digest(
                attempt_id=evidence.attempt.attempt_id, project_plan_digest=forged_plan.semantic_digest,
                environment_plan_digest=evidence.proposal.environment_plan_digest,
                graph=evidence.proposal.resolved_graph,
            )
            with pytest.raises(ValueError, match="resolution request mismatch"):
                StaticPreparationEvidence.model_validate(forged)
            encoded = evidence.model_dump_json()
            if secondary_managed:
                assert anchor_pass is not None
                other = factory.prepare(package=package, cell=package.cells[0], snapshot=snapshot,
                                        resolution=LowestDirectResolution(baseline), source_plan=source_plan)
                assert isinstance(other, PreparedEnvironment), other
                try:
                    lower_selection = selected_candidates_for_prepared(other)
                finally:
                    other.close()
                for changed_name in ("idna", "packaging"):
                    lower_candidate = next(item for item in lower_selection if item.dependency == changed_name)
                    original_candidate = next(item for item in selection if item.dependency == changed_name)
                    assert lower_candidate.version != original_candidate.version
                    changed_selection = tuple(lower_candidate if item.dependency == changed_name else item
                                              for item in selection)
                    other = factory.prepare(package=package, cell=package.cells[0], snapshot=snapshot,
                                            resolution=ExactSelection(selection=changed_selection, harness_baseline=baseline),
                                            source_plan=source_plan)
                    assert isinstance(other, PreparedEnvironment), other
                    try:
                        changed_request = StaticRequestFactory(runner, ty_executable=Path(executable)).capture(
                            other, package=package, 
                            environment={"HOME": str(tmp_path / "home"), "GIT_CONFIG_SYSTEM": str(tmp_path / "no-git-config")},
                        )
                        assert isinstance(changed_request, StaticTyRequest), changed_request
                        changed_observed = TyAdapter(runner).observe(changed_request)
                        assert isinstance(changed_observed, TyCheck), changed_observed
                        changed_consumer = StaticConsumerEvidence(preparation=changed_request.preparation,
                            observation=ty_fact_document(changed_request.subject, changed_request.observation_policy, changed_observed))
                        if changed_name == "idna":
                            assert_slice_comparison_contract(baseline_consumer, changed_consumer, anchor_pass, changed_selection)
                        else:
                            assert_changed_other_coordinate(baseline_consumer, changed_consumer, anchor_pass, selection)
                    finally:
                        other.close()
        finally:
            if isinstance(prepared, PreparedEnvironment):
                prepared.close()
            snapshot.close()
        assert StaticPreparationEvidence.model_validate_json(encoded) == evidence


@pytest.mark.process
class TestNonemptyStaticPreparation:
    def test_registry_selection_and_external_harness_round_trip(self, tmp_path: Path) -> None:
        run_nonempty_static_preparation(
            tmp_path, resolution_kind="highest", secondary_managed=False,
        )


def assert_global_comparison_contract(reference: StaticConsumerEvidence, subject: StaticConsumerEvidence) -> None:
    from pf.schemas.policy import GuidancePolicy
    from pf.schemas.static_comparison import GlobalComparisonContext, StaticComparisonDocument, StaticCompared, StaticUncompared
    from pf.schemas.ty_fact import TyCheckFact, TyCheckUnavailable
    from pf.schemas.evaluation import TimedOut, NormalExit, TyDiagnostic

    guidance = GuidancePolicy(observation=subject.observation.observation_policy,
                              observation_identity=subject.observation.observation_policy.identity)
    context = GlobalComparisonContext(highest_proposal_id=reference.preparation.proposal.proposal_id)
    compared = StaticComparisonDocument.compare(context=context, subject=subject, reference=reference, guidance=guidance)
    assert isinstance(compared.result, StaticCompared)
    assert compared.result.state == "STATIC_UNCHANGED"
    assert compared.result.incremental_identities == ()
    assert StaticComparisonDocument.model_validate_json(compared.model_dump_json()) == compared
    missing = StaticComparisonDocument.compare(context=context, subject=subject, reference=None, guidance=guidance)
    assert missing.result == StaticUncompared(reason="reference-missing")
    wrong = StaticComparisonDocument.compare(context=GlobalComparisonContext(highest_proposal_id="f" * 64),
                                              subject=subject, reference=reference, guidance=guidance)
    assert wrong.result == StaticUncompared(reason="context-mismatch")
    forged = compared.model_dump(mode="json")
    forged["context"]["highest_proposal_id"] = "f" * 64
    with pytest.raises(ValueError, match="saved static comparison"):
        StaticComparisonDocument.model_validate(forged)

    def with_fact(consumer, fact):
        return StaticConsumerEvidence(preparation=consumer.preparation,
                                      observation=TyFactDocument(subject=consumer.observation.subject,
                                                                 observation_policy=consumer.observation.observation_policy,
                                                                 fact=fact, fact_identity=fact.identity))

    failed = with_fact(subject, TyCheckUnavailable(subject_identity=subject.preparation.subject.identity,
                                                   observation_policy_identity=guidance.observation_identity,
                                                   reason="timeout", terminal=TimedOut()))
    unavailable = StaticComparisonDocument.compare(context=context, subject=failed, reference=reference, guidance=guidance)
    assert unavailable.result.status == "UNAVAILABLE"
    no_baseline = StaticComparisonDocument.compare(context=context, subject=subject,
                                                    reference=with_fact(reference, TyCheckUnavailable(
                                                        subject_identity=reference.preparation.subject.identity,
                                                        observation_policy_identity=guidance.observation_identity,
                                                        reason="timeout", terminal=TimedOut())), guidance=guidance)
    assert no_baseline.result == StaticUncompared(reason="reference-unavailable")
    diagnostic = TyDiagnostic(identity="snapshot|src/demo/__init__.py|1|invalid-assignment", origin="snapshot",
                              path="src/demo/__init__.py", line=1, column=None, code="invalid-assignment",
                              severity="error", message="example")
    duplicate_subject = with_fact(subject, TyCheckFact(subject_identity=subject.preparation.subject.identity,
                                                       observation_policy_identity=guidance.observation_identity,
                                                       terminal=NormalExit(exit_code=1), diagnostics=(diagnostic, diagnostic)))
    single_reference = with_fact(reference, TyCheckFact(subject_identity=reference.preparation.subject.identity,
                                                        observation_policy_identity=guidance.observation_identity,
                                                        terminal=NormalExit(exit_code=1), diagnostics=(diagnostic,)))
    delta = StaticComparisonDocument.compare(context=context, subject=duplicate_subject,
                                             reference=single_reference, guidance=guidance)
    assert isinstance(delta.result, StaticCompared)
    assert delta.result.state == "STATIC_REGRESSION"
    assert delta.result.incremental_identities == (diagnostic.identity,)
    assert delta.result.fingerprint != compared.result.fingerprint
    changed_display = diagnostic.model_dump(mode="json")
    changed_display.update(severity="warning", message="different explanation")
    display = TyDiagnostic.model_validate(changed_display)
    displayed_subject = with_fact(subject, TyCheckFact(subject_identity=subject.preparation.subject.identity,
                                                       observation_policy_identity=guidance.observation_identity,
                                                       terminal=NormalExit(exit_code=1), diagnostics=(display, display)))
    displayed = StaticComparisonDocument.compare(context=context, subject=displayed_subject,
                                                 reference=single_reference, guidance=guidance)
    assert displayed.result == delta.result


def assert_slice_comparison_contract(reference, subject, anchor_pass, selection) -> None:
    from pf.schemas.policy import GuidancePolicy
    from pf.schemas.project import VersionPin
    from pf.schemas.static_comparison import (
        StaticComparisonDocument, GlobalComparisonContext, SliceComparisonContext,
        SliceAnchorPass, StaticCompared, StaticUncompared,
    )
    from pf.schemas.evaluation import VerifierRun, VerifierRejected, NormalExit

    guidance = GuidancePolicy(observation=subject.observation.observation_policy,
                              observation_identity=subject.observation.observation_policy.identity)
    selection = tuple(item for item in selection if item.dependency == "idna")
    fixed = tuple(pin for pin in reference.preparation.proposal.managed_vector if pin.name != "idna")
    context = SliceComparisonContext(dependency="idna", fixed_other_coordinates=fixed,
                                     window=selection, anchor_pass=anchor_pass)
    local = StaticComparisonDocument.compare(context=context, reference=reference, subject=subject, guidance=guidance)
    assert isinstance(local.result, StaticCompared)
    assert local.result.state == "STATIC_UNCHANGED"
    assert StaticComparisonDocument.model_validate_json(local.model_dump_json()) == local
    global_result = StaticComparisonDocument.compare(
        context=GlobalComparisonContext(highest_proposal_id=reference.preparation.proposal.proposal_id),
        reference=reference, subject=subject, guidance=guidance,
    )
    assert isinstance(global_result.result, StaticCompared)
    assert global_result.result.fingerprint != local.result.fingerprint
    missing = StaticComparisonDocument.compare(context=SliceComparisonContext(dependency="idna",
                                               fixed_other_coordinates=fixed, window=selection, anchor_pass=None),
                                               reference=reference, subject=subject, guidance=guidance)
    assert missing.result == StaticUncompared(reason="anchor-missing")
    with pytest.raises(ValueError, match="configured verifier PASS"):
        SliceAnchorPass.from_run(proposal=reference.preparation.proposal,
                                 run=VerifierRun(authoritative=VerifierRejected(terminal=NormalExit(exit_code=1))))
    altered = context.model_dump(mode="json")
    altered["anchor_pass"]["proposal_id"] = "f" * 64
    wrong_anchor = SliceComparisonContext.model_validate(altered)
    wrong_fixed = SliceComparisonContext(dependency="idna", window=selection, anchor_pass=anchor_pass,
                                         fixed_other_coordinates=(VersionPin(name="packaging", version="24.0"),))
    altered_window = context.model_dump(mode="json")
    altered_window["window"][0]["artifact"]["content_hash"] = "sha256:" + "f" * 64
    wrong_window = SliceComparisonContext.model_validate(altered_window)
    for wrong in (wrong_anchor, wrong_fixed, wrong_window):
        result = StaticComparisonDocument.compare(context=wrong, reference=reference, subject=subject, guidance=guidance)
        assert result.result == StaticUncompared(reason="context-mismatch")
        forged = local.model_dump(mode="json")
        forged["context"] = wrong.model_dump(mode="json")
        with pytest.raises(ValueError, match="saved static comparison"):
            StaticComparisonDocument.model_validate(forged)


def selected_candidates_for_prepared(prepared: PreparedEnvironment):
    from pf.schemas.project import AvailableArtifact, SelectedCandidate

    names = {pin.name for pin in prepared.proposal.managed_vector}
    selected = []
    for node in prepared.project_plan.packages:
        if node.name not in names:
            continue
        artifact = next(item for item in node.available_artifacts if item.kind == "wheel"
                        and item.filename.endswith("py3-none-any.whl"))
        assert node.version is not None
        selected.append(SelectedCandidate(dependency=node.name, version=node.version,
                                          artifact=AvailableArtifact.model_validate(artifact.model_dump(mode="json"))))
    return tuple(selected)


def assert_changed_other_coordinate(reference, subject, anchor_pass, selection):
    from pf.schemas.policy import GuidancePolicy
    from pf.schemas.static_comparison import StaticComparisonDocument, SliceComparisonContext, StaticUncompared

    assert admit_common_static_context(reference.preparation, subject.preparation)
    context = SliceComparisonContext(
        dependency="idna", anchor_pass=anchor_pass,
        fixed_other_coordinates=tuple(pin for pin in reference.preparation.proposal.managed_vector if pin.name != "idna"),
        window=tuple(item for item in selection if item.dependency == "idna"),
    )
    guidance = GuidancePolicy(observation=subject.observation.observation_policy,
                              observation_identity=subject.observation.observation_policy.identity)
    compared = StaticComparisonDocument.compare(context=context, reference=reference, subject=subject, guidance=guidance)
    assert compared.result == StaticUncompared(reason="context-mismatch")
    assert StaticComparisonDocument.model_validate_json(compared.model_dump_json()) == compared


def assert_static_scope_contract(reference, subject, anchor_pass, selection, reference_process, subject_process, pass_process):
    from pf.schemas.policy import GuidancePolicy
    from pf.schemas.static_comparison import SliceComparisonContext, GlobalComparisonContext
    from pf.schemas.static_scope import (
        StaticScopeEvidence, StaticProcessRecord, StaticFactMembership,
        StaticConsumerMembership, StaticPassMembership, StaticComparisonMembership,
    )

    shared_key = (
        reference.observation.subject.identity == subject.observation.subject.identity
        and reference.observation.observation_policy.identity
        == subject.observation.observation_policy.identity
    )
    if shared_key:
        processes = (
            StaticProcessRecord(ref="reference-ty", process=reference_process),
            StaticProcessRecord(ref="verifier", process=pass_process),
        )
        facts = (
            StaticFactMembership(
                ref="shared-fact", observation=reference.observation,
                producer_ref="reference", process_ref="reference-ty",
            ),
        )
        consumers = (
            StaticConsumerMembership(
                ref="reference", preparation=reference.preparation, fact_ref="shared-fact",
            ),
            StaticConsumerMembership(
                ref="subject", preparation=subject.preparation, fact_ref="shared-fact",
            ),
        )
    else:
        processes = (
            StaticProcessRecord(ref="reference-ty", process=reference_process),
            StaticProcessRecord(ref="subject-ty", process=subject_process),
            StaticProcessRecord(ref="verifier", process=pass_process),
        )
        facts = (
            StaticFactMembership(
                ref="reference-fact", observation=reference.observation,
                producer_ref="reference", process_ref="reference-ty",
            ),
            StaticFactMembership(
                ref="subject-fact", observation=subject.observation,
                producer_ref="subject", process_ref="subject-ty",
            ),
        )
        consumers = (
            StaticConsumerMembership(
                ref="reference", preparation=reference.preparation, fact_ref="reference-fact",
            ),
            StaticConsumerMembership(
                ref="subject", preparation=subject.preparation, fact_ref="subject-fact",
            ),
        )
    scope = StaticScopeEvidence(
        scope_ref="scope-a", cell=reference.preparation.proposal.cell,
        processes=processes, facts=facts, consumers=consumers,
        passes=(StaticPassMembership(ref="anchor-pass", evidence=anchor_pass, consumer_ref="reference", process_ref="verifier"),),
        highest_reference_ref="reference", highest_uncollected=None, comparisons=(),
    )
    restored_scope = StaticScopeEvidence.model_validate_json(scope.model_dump_json())
    assert restored_scope.model_dump_json() == scope.model_dump_json()
    guidance = GuidancePolicy(observation=subject.observation.observation_policy,
                              observation_identity=subject.observation.observation_policy.identity)
    context = SliceComparisonContext(dependency="idna", anchor_pass=anchor_pass,
                                     fixed_other_coordinates=tuple(pin for pin in reference.preparation.proposal.managed_vector if pin.name != "idna"),
                                     window=tuple(item for item in selection if item.dependency == "idna"))
    compared = scope.compare(scope_ref="scope-a", subject_ref="subject", reference_ref="reference",
                             context=context, guidance=guidance, anchor_pass_ref="anchor-pass")
    assert compared.result.status == "COMPARED"
    anchor_self = scope.compare(
        scope_ref="scope-a", subject_ref="reference", reference_ref="reference",
        context=context, guidance=guidance, anchor_pass_ref="anchor-pass",
    )
    assert anchor_self.result.status == "COMPARED"
    assert anchor_self.result.state == "STATIC_UNCHANGED"
    assert type(anchor_self).model_validate_json(anchor_self.model_dump_json()) == anchor_self
    scope = StaticScopeEvidence.model_validate({
        **scope.model_dump(mode="json"),
        "comparisons": [StaticComparisonMembership(
            ref="local-comparison", identity=compared.identity,
            subject_ref="subject", reference_ref="reference", anchor_pass_ref="anchor-pass",
            context=context, guidance=guidance, result=compared.result,
        ).model_dump(mode="json")],
    })
    from pf.schemas.static_baseline import StaticUncollectedBaseline
    from pf.schemas.static_comparison import StaticUncompared

    unavailable_global = scope.model_dump(mode="json")
    unavailable_global["highest_reference_ref"] = None
    unavailable_global["highest_uncollected"] = StaticUncollectedBaseline(
        attempt=reference.preparation.attempt, proposal=reference.preparation.proposal,
        unavailable=StaticContentUnavailable(detail="unreadable-content"),
    ).model_dump(mode="json")
    unavailable_scope = StaticScopeEvidence.model_validate(unavailable_global)
    global_comparison = unavailable_scope.compare(
        scope_ref="scope-a", subject_ref="subject", reference_ref=None,
        context=GlobalComparisonContext(highest_proposal_id=reference.preparation.proposal.proposal_id),
        guidance=guidance,
    )
    assert global_comparison.result == StaticUncompared(reason="reference-unavailable")
    assert unavailable_scope.compare(scope_ref="scope-a", subject_ref="subject", reference_ref="reference",
                                     context=context, guidance=guidance, anchor_pass_ref="anchor-pass").result == compared.result
    assert restored_scope.compare(scope_ref="scope-a", subject_ref="subject", reference_ref="reference",
                                  context=context, guidance=guidance, anchor_pass_ref="anchor-pass").result == compared.result
    for kwargs in (
        {"scope_ref": "scope-b", "subject_ref": "subject", "anchor_pass_ref": "anchor-pass"},
        {"scope_ref": "scope-a", "subject_ref": "foreign-subject", "anchor_pass_ref": "anchor-pass"},
        {"scope_ref": "scope-a", "subject_ref": "subject", "anchor_pass_ref": "foreign-pass"},
    ):
        with pytest.raises(ValueError):
            scope.compare(**kwargs, reference_ref="reference", context=context, guidance=guidance)
    with pytest.raises(ValueError, match="fixed highest"):
        scope.compare(scope_ref="scope-a", subject_ref="subject", reference_ref="subject",
                      context=GlobalComparisonContext(highest_proposal_id=subject.preparation.proposal.proposal_id), guidance=guidance)
    incomplete = scope.model_dump(mode="json")
    incomplete["processes"][0]["process"]["stdout_complete"] = False
    with pytest.raises(ValueError, match="output completeness"):
        StaticScopeEvidence.model_validate(incomplete)
    dangling = scope.model_dump(mode="json")
    dangling["facts"][0]["producer_ref"] = "missing"
    with pytest.raises(ValueError, match="producer references"):
        StaticScopeEvidence.model_validate(dangling)
    borrowed_process = scope.model_dump(mode="json")
    borrowed_process["passes"][0]["process_ref"] = "reference-ty"
    with pytest.raises(ValueError, match="own execution"):
        StaticScopeEvidence.model_validate(borrowed_process)
    duplicate = scope.model_dump(mode="json")
    duplicate["facts"].append({**duplicate["facts"][0], "ref": "copied-fact"})
    with pytest.raises(ValueError, match="one observation"):
        StaticScopeEvidence.model_validate(duplicate)
    renamed = scope.model_dump(mode="json")
    renamed["scope_ref"] = "renamed-scope"
    renamed["highest_reference_ref"] = "renamed/reference"
    for table in ("processes", "facts", "consumers", "passes", "comparisons"):
        for row in renamed[table]:
            for key in ("ref", "producer_ref", "process_ref", "fact_ref", "consumer_ref", "subject_ref", "reference_ref", "anchor_pass_ref"):
                if key in row and row[key] is not None:
                    row[key] = "renamed/" + row[key]
    relocated = StaticScopeEvidence.model_validate(renamed)
    replay = relocated.compare(scope_ref="renamed-scope", subject_ref="renamed/subject", reference_ref="renamed/reference",
                               context=context, guidance=guidance, anchor_pass_ref="renamed/anchor-pass")
    assert replay.result == compared.result
    assert replay.identity == compared.identity == relocated.comparisons[0].identity

    from pf.schemas.static_scope import intern_static_scopes
    copy = scope.model_copy(update={"scope_ref": "scope-b"})
    with pytest.raises(ValueError, match="static-subject-v2 is not interned"):
        intern_static_scopes((scope, copy))

    # Register the same actual preparations/processes through the runtime owner;
    # runtime and offline admission must derive the same Slice comparison.
    from dataclasses import replace
    from pf.schemas.evaluation import VerifierRun, VerifierDiagnostics
    from pf.schemas.static_comparison import StaticUncompared

    cache = TyCheckCache()
    reference_fact = cache.collect(reference.preparation, reference.observation.observation_policy,
                                   lambda _: (reference.observation, reference_process), revalidate=lambda: True)
    subject_fact = cache.collect(subject.preparation, subject.observation.observation_policy,
                                 lambda _: (subject.observation, subject_process), revalidate=lambda: True)
    assert isinstance(reference_fact, RunTyFactRef)
    assert isinstance(subject_fact, RunTyFactRef)
    reference_ref = cache.consumer(reference_fact, reference.preparation)
    subject_ref = cache.consumer(subject_fact, subject.preparation)
    cache.set_highest(reference_ref)
    actual_pass = VerifierRun(authoritative=anchor_pass.verifier,
                              diagnostics=VerifierDiagnostics(process=pass_process))
    pass_ref = cache.record_pass(reference_ref, actual_pass)
    assert cache.record_pass(reference_ref, actual_pass) is pass_ref
    assert cache.compare(subject_ref, reference_ref, context=context, guidance=guidance,
                         anchor_pass=pass_ref) == compared.result
    assert cache.compare(subject_ref, reference_ref, context=context, guidance=guidance,
                         anchor_pass=replace(pass_ref)) == StaticUncompared(reason="context-mismatch")
    assert cache.compare(subject_ref, reference_ref, context=context, guidance=guidance) == StaticUncompared(reason="context-mismatch")
    cache.stop()
    emitted = cache.snapshot(reference.preparation.proposal.cell)
    assert len(emitted.facts) == (1 if shared_key else 2)
    assert len(emitted.passes) == 1
    assert len(emitted.comparisons) == 1
    assert emitted.comparisons[0].identity == compared.identity
    assert emitted.comparisons[0].anchor_pass_ref == emitted.passes[0].ref
    assert len(emitted.processes) == (2 if shared_key else 3)
    saved = emitted.model_dump_json()
    cache.close()
    emitted = StaticScopeEvidence.model_validate_json(saved)
    subject_member = next(item for item in emitted.consumers if item.preparation == subject.preparation)
    reference_member = next(item for item in emitted.consumers if item.preparation == reference.preparation)
    assert emitted.compare(scope_ref=emitted.scope_ref, subject_ref=subject_member.ref,
                           reference_ref=reference_member.ref, context=context, guidance=guidance,
                           anchor_pass_ref=emitted.passes[0].ref).result == compared.result

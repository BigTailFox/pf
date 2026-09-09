"""Slice/global comparison and scope admission on the scripted public seam."""
from __future__ import annotations

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    successful_process,
)
import pytest
from pf.environment import (
    ExactSelection,
    HighestResolution,
    LowestDirectResolution,
    PreparedEnvironment,
)
from pf.schemas.evaluation import (
    NormalExit,
    TyCheck,
    VerifierDiagnostics,
    VerifierPass,
    VerifierRejected,
    VerifierRun,
)
from pf.schemas.project import VersionPin
from pf.schemas.static_comparison import SliceAnchorPass
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.ty_fact import ty_fact_document
from scripted_static import ScriptedStaticRequests
from pf.static_admission import admit_common_static_context, admit_harness_relation
from test_static_request import (
    assert_changed_other_coordinate,
    assert_global_comparison_contract,
    assert_slice_comparison_contract,
    assert_static_scope_contract,
    selected_candidates_for_prepared,
)


def _consumer(request, observed):
    return StaticConsumerEvidence(
        preparation=request.preparation,
        observation=ty_fact_document(
            request.subject, request.observation_policy, observed,
        ),
    )


class TestScriptedStaticComparison:
    def test_scripted_pair_covers_global_slice_and_scope_contracts(self, tmp_path):
        project = evaluation_project(tmp_path, dependencies=("idna", "packaging"))
        assembly = evaluation_assembly(
            highest=(
                VersionPin(name="idna", version="3.10"),
                VersionPin(name="packaging", version="24.2"),
            ),
            lowest=(
                VersionPin(name="idna", version="3.10"),
                VersionPin(name="packaging", version="24.0"),
            ),
        )
        factory = ScriptedStaticRequests()
        reference_process = successful_process()
        subject_process = successful_process()
        pass_process = successful_process()
        highest = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(highest, PreparedEnvironment)
        try:
            reference_request = factory.capture(
                highest, package=project.package, environment={},
            )
            reference = _consumer(
                reference_request, TyCheck(process=reference_process, diagnostics=()),
            )
            run = VerifierRun(
                authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
                diagnostics=VerifierDiagnostics(process=pass_process),
            )
            with pytest.raises(ValueError, match="slice anchor requires"):
                SliceAnchorPass.from_run(
                    proposal=highest.proposal,
                    run=VerifierRun(
                        authoritative=VerifierRejected(terminal=NormalExit(exit_code=1)),
                        diagnostics=VerifierDiagnostics(process=pass_process),
                    ),
                )
            anchor = SliceAnchorPass.from_run(proposal=highest.proposal, run=run)
            selection = selected_candidates_for_prepared(highest)
            assert_global_comparison_contract(reference, reference)
            from pf.policy import guidance_policy
            from pf.schemas.config import EffectiveConfig
            from pf.schemas.policy import (
                GuidancePolicy, SnapshotTyConfigMaterialized, TyToolVersionDistribution,
            )
            from pf.schemas.static_comparison import (
                GlobalComparisonContext, SliceComparisonContext, StaticCompared,
                StaticComparisonDocument, StaticUncompared,
            )
            from pf.ty_fact import ty_fact_document

            other_policy = guidance_policy(
                EffectiveConfig(),
                tool_version=TyToolVersionDistribution(version="2.0.0"),
                snapshot_ty_config=SnapshotTyConfigMaterialized(digest="d" * 64),
            ).observation
            other = StaticConsumerEvidence(
                preparation=reference.preparation,
                observation=ty_fact_document(
                    reference.preparation.subject, other_policy,
                    TyCheck(process=reference_process, diagnostics=()),
                ),
            )
            mismatched = StaticComparisonDocument.compare(
                context=GlobalComparisonContext(
                    highest_proposal_id=reference.preparation.proposal.proposal_id,
                ),
                subject=other, reference=reference, guidance=GuidancePolicy(
                    observation=other_policy, observation_identity=other_policy.identity,
                ),
            )
            assert mismatched.result == StaticUncompared(reason="context-mismatch")

            guidance = GuidancePolicy(
                observation=reference.observation.observation_policy,
                observation_identity=reference.observation.observation_policy.identity,
            )
            local = StaticComparisonDocument.compare(
                context=SliceComparisonContext(
                    dependency="idna",
                    fixed_other_coordinates=tuple(
                        pin for pin in reference.preparation.proposal.managed_vector
                        if pin.name != "idna"
                    ),
                    window=tuple(item for item in selection if item.dependency == "idna"),
                    anchor_pass=anchor,
                ),
                reference=reference,
                subject=reference,
                guidance=guidance,
            )
            assert isinstance(local.result, StaticCompared)
            assert local.result.state == "STATIC_UNCHANGED"
            lower = assembly.environments.prepare(
                package=project.package,
                cell=project.package.cells[0],
                snapshot=project.snapshot,
                source_plan=project.source_plan,
                resolution=LowestDirectResolution(highest.harness_baseline),
            )
            assert isinstance(lower, PreparedEnvironment)
            try:
                changed = _consumer(
                    factory.capture(lower, package=project.package, environment={}),
                    TyCheck(process=subject_process, diagnostics=()),
                )
                assert admit_common_static_context(
                    reference.preparation, changed.preparation,
                )
                assert admit_harness_relation(reference.preparation, changed.preparation)
                assert admit_harness_relation(changed.preparation, changed.preparation)
                assert_changed_other_coordinate(reference, changed, anchor, selection)
            finally:
                lower.close()
            exact = assembly.environments.prepare(
                package=project.package,
                cell=project.package.cells[0],
                snapshot=project.snapshot,
                source_plan=project.source_plan,
                resolution=ExactSelection(
                    selection=selection,
                    harness_baseline=highest.harness_baseline,
                ),
            )
            assert isinstance(exact, PreparedEnvironment)
            try:
                exact_process = successful_process()
                exact_consumer = _consumer(
                    factory.capture(exact, package=project.package, environment={}),
                    TyCheck(process=exact_process, diagnostics=()),
                )
                assert_slice_comparison_contract(
                    reference, exact_consumer, anchor, selection,
                )
                assert_static_scope_contract(
                    reference, exact_consumer, anchor, selection,
                    reference_process, exact_process, pass_process,
                )
            finally:
                exact.close()
        finally:
            highest.close()
            project.snapshot.close()

    def test_nonempty_harness_rejects_highest_subjects_and_admits_shared_baselines(
        self, tmp_path,
    ) -> None:
        project = evaluation_project(
            tmp_path,
            dependencies=("idna",),
            test_dependencies=("packaging>=24",),
        )
        assembly = evaluation_assembly(
            highest=(VersionPin(name="idna", version="3.10"),),
        )
        factory = ScriptedStaticRequests()
        highest = assembly.environments.prepare(
            package=project.package,
            cell=project.package.cells[0],
            snapshot=project.snapshot,
            source_plan=project.source_plan,
            resolution=HighestResolution(),
        )
        assert isinstance(highest, PreparedEnvironment)
        try:
            assert highest.attempt.identity.harness_declaration_ids
            reference = factory.capture(
                highest, package=project.package, environment={},
            )
            lower = assembly.environments.prepare(
                package=project.package,
                cell=project.package.cells[0],
                snapshot=project.snapshot,
                source_plan=project.source_plan,
                resolution=LowestDirectResolution(highest.harness_baseline),
            )
            assert isinstance(lower, PreparedEnvironment)
            try:
                subject = factory.capture(
                    lower, package=project.package, environment={},
                )
                assert admit_harness_relation(
                    reference.preparation, subject.preparation,
                )
                assert not admit_harness_relation(
                    subject.preparation, reference.preparation,
                )
            finally:
                lower.close()
        finally:
            highest.close()
            project.snapshot.close()

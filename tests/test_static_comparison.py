"""Slice/global comparison and scope admission on the scripted public seam."""
from __future__ import annotations

from evaluation_fixtures import (
    evaluation_assembly,
    evaluation_project,
    successful_process,
)
from pf.environment import (
    HighestResolution,
    LowestDirectResolution,
    PreparedEnvironment,
)
from pf.schemas.evaluation import (
    NormalExit,
    TyCheck,
    VerifierDiagnostics,
    VerifierPass,
    VerifierRun,
)
from pf.schemas.project import VersionPin
from pf.schemas.static_comparison import SliceAnchorPass
from pf.schemas.static_consumer import StaticConsumerEvidence
from pf.ty_fact import ty_fact_document
from scripted_static import ScriptedStaticRequests
from test_static_request import (
    assert_changed_other_coordinate,
    assert_global_comparison_contract,
    selected_candidates_for_prepared,
)


def _consumer(request, observed):
    return StaticConsumerEvidence(
        preparation=request.preparation,
        observation=ty_fact_document(
            request.subject, request.observation_policy, observed,
        ),
    )


def test_scripted_pair_covers_global_slice_and_scope_contracts(tmp_path):
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
    observed = TyCheck(process=successful_process(), diagnostics=())
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
        reference = _consumer(reference_request, observed)
        run = VerifierRun(
            authoritative=VerifierPass(terminal=NormalExit(exit_code=0)),
            diagnostics=VerifierDiagnostics(process=successful_process()),
        )
        anchor = SliceAnchorPass.from_run(proposal=highest.proposal, run=run)
        selection = selected_candidates_for_prepared(highest)
        assert_global_comparison_contract(reference, reference)
        from pf.schemas.policy import GuidancePolicy
        from pf.schemas.static_comparison import (
            SliceComparisonContext,
            StaticCompared,
            StaticComparisonDocument,
        )

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
                observed,
            )
            assert_changed_other_coordinate(reference, changed, anchor, selection)
        finally:
            lower.close()
    finally:
        highest.close()
        project.snapshot.close()

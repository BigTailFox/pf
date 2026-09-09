"""Complete controlled input evidence for the scripted lower ty operation.

These fixtures materialize the declared byte inputs but never claim to be a real
Python installation. Real request/adapter integration uses StaticRequestFactory.
"""

from pf.policy import execution_policy, guidance_policy
from pf.resolution import ResolutionPlanEvidence
from pf.schemas.evaluation import ProcessSpec
from pf.schemas.policy import SnapshotTyConfigMaterialized, TyToolVersionDistribution
from pf.schemas.static import StaticSubject
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.static_projection import static_subject
from pf.static_request import StaticTyRequest


class ScriptedStaticRequests:
    def capture(self, prepared, *, package, environment, cancellation=None):
        del environment
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        directory = prepared.proposal_root.parent / "static"
        directory.mkdir(parents=True, exist_ok=True)
        tool = directory / "ty"
        tool.write_bytes(b"scripted ty")
        plan = prepared.environment_plan or prepared.project_plan
        interpreter = prepared.proposal.interpreter
        assert interpreter is not None
        subject = static_subject(
            source_snapshot_digest=prepared.proposal.snapshot_digest,
            cell=prepared.proposal.cell,
            interpreter=interpreter,
            packages=plan.packages,
            snapshot_root=prepared.proposal_root,
        )
        assert isinstance(subject, StaticSubject), subject
        policy = guidance_policy(
            package.config,
            tool_version=TyToolVersionDistribution(version="1.0.0"),
            snapshot_ty_config=SnapshotTyConfigMaterialized(digest="a" * 64),
        ).observation
        preparation = StaticPreparationEvidence(
            attempt=prepared.attempt, proposal=prepared.proposal, subject=subject,
            project_plan=ResolutionPlanEvidence.from_plan(prepared.project_plan),
            environment_plan=ResolutionPlanEvidence.from_plan(prepared.environment_plan) if prepared.environment_plan else None,
            execution_policy=execution_policy(package.config), declarations=package.declarations,
            selected_test_group=package.selected_test_group, harness_requirements=package.harness_requirements,
            harness_baseline=prepared.harness_baseline, selected_candidates=prepared.selected_candidates,
            source_plan=prepared.source_plan,
        )
        return StaticTyRequest(
            subject, policy,
            ProcessSpec(argv=(str(tool), "check"), cwd=str(prepared.package_root), timeout_seconds=None),
            prepared.proposal_root, prepared.environment_root, prepared,
            (("snapshot", prepared.proposal_root), ("environment", prepared.environment_root)),
            str(prepared.environment_root), preparation,
        )


def collect_highest(static, prepared, *, package, run_cache):
    """Collect actual fixture inputs and register the fixed Run reference."""
    from pf.schemas.static import StaticContentUnavailable
    from pf.schemas.static_baseline import StaticUncollectedBaseline
    observation = static.collect_prepared(prepared, package=package, run_cache=run_cache)
    if isinstance(observation, StaticContentUnavailable):
        run_cache.set_highest_uncollected(StaticUncollectedBaseline(
            attempt=prepared.attempt, proposal=prepared.proposal, unavailable=observation,
        ))
    else:
        assert prepared.static_consumer is not None
        run_cache.set_highest(prepared.static_consumer)
    return observation

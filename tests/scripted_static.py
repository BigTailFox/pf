"""Complete controlled input evidence for the scripted lower ty operation.

These fixtures materialize the declared byte inputs but never claim to be a real
Python installation. Real request/adapter integration uses StaticRequestFactory.
"""

from pf.policy import execution_policy, guidance_policy
from pf.resolution import ResolutionPlanEvidence
from pf.schemas.evaluation import ProcessSpec
from pf.schemas.static import StaticSubject, StaticContentManifest, StaticContentPath
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.static_request import StaticTyRequest
from pf.static_subject import StaticContentCollector


class ScriptedStaticRequests:
    def capture(self, prepared, *, package, environment, cancellation=None):
        del environment
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        directory = prepared.proposal_root.parent / "static"
        runtime = directory / "runtime"
        (runtime / "lib").mkdir(parents=True, exist_ok=True)
        (runtime / "python").write_bytes(b"scripted interpreter")
        tool = directory / "ty"
        tool.write_bytes(b"scripted ty")
        plan = prepared.environment_plan or prepared.project_plan
        by_name = {node.name: node for node in plan.packages}
        nodes = []
        for node in prepared.proposal.resolved_graph:
            filename = f"{node.name}.METADATA"
            (prepared.environment_root / filename).write_text(f"Name: {node.name}\nVersion: {node.version}\n")
            planned = by_name.get(node.name)
            nodes.append({
                "name": node.name, "version": node.version,
                "source": planned.source.model_dump() if planned else {"kind": "path", "locator": "."},
                "artifact": planned.selected_artifact.model_dump() if planned and planned.selected_artifact else None,
                "dependencies": planned.dependencies if planned else (),
                "install_mode": "wheel" if planned else "source",
                "source_mapping": None if planned else {"root": "snapshot", "path": "."},
                "files": [{"root": "environment", "path": filename}],
            })
        # Model the separate effective configuration used by native capture.
        # The projected pyproject remains an original input, not the effective
        # ty settings: exact-vector preparation legitimately rewrites its pins.
        effective_config = ".pf-scripted-ty.toml"
        (prepared.proposal_root / effective_config).write_text("# Scripted effective ty configuration\n")
        roots = {"snapshot": prepared.proposal_root, "environment": prepared.environment_root,
                 "runtime": runtime, "ty-executable": tool}
        content = StaticContentCollector().collect(roots)
        assert isinstance(content, StaticContentManifest), content
        source = content.for_roots(frozenset({"snapshot"}))
        installed = content.for_roots(frozenset({"snapshot", "environment"}))

        def ref(root, path="."):
            return {"root": root, "path": path}

        target = ref("snapshot", prepared.package_root.relative_to(prepared.proposal_root).as_posix())
        subject = StaticSubject.model_validate({
            "projection": "static-subject-v1",
            "source": {"snapshot_identity": prepared.proposal.snapshot_digest, "content": source,
                       "packages": [{"package": package.name, "source": target}], "source_plan": prepared.source_plan},
            "target": {"cell": prepared.proposal.cell, "interpreter": prepared.proposal.interpreter,
                       "content": content.for_roots(frozenset({"runtime"})),
                       "executable": ref("runtime", "python"), "stdlib_roots": [ref("runtime", "lib")]},
            "installed_world": {"content": installed, "nodes": nodes,
                                "support_files": [entry.location for entry in installed.entries if entry.kind != "directory"]},
            "analysis_layout": {"project_root": target, "targets": [target], "cwd": target,
                                "import_roots": [target, ref("environment")], "type_roots": [ref("runtime", "lib")],
                                "root_placements": [{"root": name, "location": ref("prepared", path.relative_to(directory.parent).as_posix())}
                                                    for name, path in sorted(roots.items()) if name != "ty-executable"]},
            "configuration": {"content": source, "effective_file": ref("snapshot", effective_config),
                              "files_in_precedence_order": [ref("snapshot", "pyproject.toml"), ref("snapshot", effective_config)],
                              "discovery_boundaries": [ref("snapshot")], "external_roots": []},
            "process_context": {"environment": [], "filesystem_case": "sensitive", "environment_case": "sensitive"},
        })
        policy = guidance_policy(package.config, tool_version="scripted-ty-1",
                                 tool_content=content.for_roots(frozenset({"ty-executable"})),
                                 executable=StaticContentPath(root="ty-executable", path=".")).observation
        preparation = StaticPreparationEvidence(
            attempt=prepared.attempt, proposal=prepared.proposal, subject=subject,
            project_plan=ResolutionPlanEvidence.from_plan(prepared.project_plan),
            environment_plan=ResolutionPlanEvidence.from_plan(prepared.environment_plan) if prepared.environment_plan else None,
            execution_policy=execution_policy(package.config), declarations=package.declarations,
            selected_test_group=package.selected_test_group, harness_requirements=package.harness_requirements,
            harness_baseline=prepared.harness_baseline, selected_candidates=prepared.selected_candidates,
        )
        return StaticTyRequest(subject, policy, ProcessSpec(argv=(str(tool), "check"), cwd=str(prepared.package_root), timeout_seconds=None),
                               prepared.proposal_root, prepared.environment_root, prepared, tuple(sorted(roots.items())),
                               content, None, preparation)


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

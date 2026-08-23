from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

from pf.resolution import (
    EnvironmentIdentity,
    NativeResolutionPlan,
    ResolutionContext,
    ResolutionPlan,
    ResolutionRunContext,
)
from pf.schemas.evaluation import ProcessResult
from pf.schemas.project import Cell, HarnessBaseline, ResolvedNode


testmon_datafile = os.environ.get("TESTMON_DATAFILE")
if testmon_datafile:
    Path(testmon_datafile).parent.mkdir(parents=True, exist_ok=True)


class PreparedResolutionEvidence(TypedDict):
    project_plan: ResolutionPlan
    environment_plan: ResolutionPlan
    environment_identity: EnvironmentIdentity
    harness_baseline: HarnessBaseline


def empty_harness_baseline(cell: Cell) -> HarnessBaseline:
    return HarnessBaseline.from_evidence(
        cell=cell,
        declaration_ids=(),
        selections=(),
    )


def prepared_resolution_evidence(
    *,
    cell: Cell,
    graph: tuple[ResolvedNode, ...] = (),
) -> PreparedResolutionEvidence:
    context = ResolutionContext.from_inputs(
        run=ResolutionRunContext(
            uv_version="0.12.5",
            release_cutoff="2026-08-23T00:00:00+00:00",
        ),
        cell=cell,
        source_policy_identity="test-source-policy",
        allow_prereleases=False,
    )
    native = NativeResolutionPlan.from_content(
        'lock-version = "1.0"\ncreated-by = "uv"\npackages = []\n'
    )
    process = ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0,
        stdout="",
        stderr="",
    )
    project = ResolutionPlan.from_evidence(
        kind="project",
        request_digest="test-project-request",
        context=context,
        packages=(),
        direct_harness=(),
        native=native,
        process=process,
    )
    environment = ResolutionPlan.from_evidence(
        kind="environment",
        request_digest="test-environment-request",
        context=context,
        packages=(),
        direct_harness=(),
        native=native,
        process=process,
    )
    return {
        "project_plan": project,
        "environment_plan": environment,
        "environment_identity": EnvironmentIdentity.from_plans(
            project_plan=project,
            environment_plan=environment,
            graph=graph,
        ),
        "harness_baseline": empty_harness_baseline(cell),
    }

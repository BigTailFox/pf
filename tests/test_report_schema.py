from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest

from pf.errors import ConfigurationError
from pf.resolution import environment_identity_digest, resolution_graph_id
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellFailureScope,
    FailureDetail,
    DiagnosticClassification,
    PassEvaluation,
    IndeterminateEvaluation,
    ProcessResult,
    RuntimeInterfaceMissingEvaluation,
    RuntimeWitnessAttempt,
    RuntimeWitnessPlan,
    RuntimeWitnessResult,
    StaticBaseline,
    StaticRegressionEvaluation,
    StaticUnchangedEvaluation,
    TestPass,
    TestFail,
    TestFailEvaluation,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.static_transition import static_fingerprint
from pf.schemas.project import (
    Cell,
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    PackagePlan,
    InterpreterIdentity,
    Proposal,
    ResolvedNode,
    RequirementDeclaration,
    SnapshotEntry,
    SourcePlan,
    SourceSnapshotIdentity,
    SourceIdentity,
    VersionPin,
    candidate_snapshot_digest,
    cell_id,
    source_snapshot_digest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateSuccess,
    PackageIdentity,
    ProbeObservation,
    ProbePass,
    ProbeIndeterminate,
    ProbeRejection,
    StaticOnlyEvidence,
    StaticRegion,
    StaticRegionRuntimeReference,
    StaticRegionSlice,
    static_region_id,
)


@pytest.mark.parametrize(
    "path",
    ("/tmp/secret.py", "C:\\secret.py", "../secret.py", "src/../secret.py"),
)
def test_report_domain_rejects_non_public_persisted_paths(path: str) -> None:
    with pytest.raises(ValueError, match="public and relative"):
        SnapshotEntry(path=path, kind="file", mode=0o644)
    with pytest.raises(ValueError, match="public and relative"):
        RequirementDeclaration(
            declaration_id="declaration",
            package="demo",
            location="base",
            name="foo",
            source=SourceIdentity(kind="registry"),
            pyproject_path=path,
            raw="foo",
            kind="searchable",
            managed=True,
        )
    with pytest.raises(ValueError, match="public and relative"):
        PackageIdentity(name="demo", pyproject_path=path)


def test_report_domain_rejects_noncanonical_package_names() -> None:
    malicious = "[link=https://evil.example]demo[/link]"
    with pytest.raises(ValueError, match="canonical distribution name"):
        Cell(
            package=malicious,
            target="x86_64-unknown-linux-gnu",
            python_minor="3.12",
            extra_surface=(),
        )
    with pytest.raises(ValueError, match="canonical distribution name"):
        PackageIdentity(name=malicious, pyproject_path="pyproject.toml")


def test_report_domain_rejects_absolute_symlink_targets() -> None:
    with pytest.raises(ValueError, match="public and relative"):
        SnapshotEntry(
            path="link",
            kind="symlink",
            mode=0o777,
            link_target="/tmp/secret.py",
        )


def test_cell_id_is_stable_across_active_declaration_changes() -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=("docs", "test"),
        active_declaration_ids=("demo",),
    )
    payload = {
        "package": "demo",
        "target": "x86_64-unknown-linux-gnu",
        "python_minor": "3.12",
        "extra_surface": ["docs", "test"],
    }
    expected = (
        "cell-"
        + hashlib.sha256(
            b"pf:cell:v1\0"
            + json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
    )

    assert cell_id(cell) == expected
    assert (
        cell_id(cell.model_copy(update={"active_declaration_ids": ("other",)}))
        == expected
    )


def test_source_snapshot_digest_uses_entries_in_path_order() -> None:
    entries = (
        SnapshotEntry(
            path="src/demo.py",
            kind="file",
            mode=0o644,
            content_digest="b" * 64,
        ),
        SnapshotEntry(path="src", kind="directory", mode=0o755),
    )
    canonical = tuple(sorted(entries, key=lambda entry: entry.path))
    expected = hashlib.sha256(
        b"pf:snapshot:v1\0"
        + json.dumps(
            [entry.model_dump(mode="json") for entry in canonical],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()

    assert source_snapshot_digest(entries) == expected
    assert source_snapshot_digest(tuple(reversed(entries))) == expected


def test_resolution_graph_id_requires_canonical_nodes_and_dependencies() -> None:
    graph = (
        ResolvedNode(name="demo", version="1.0", dependencies=("idna",)),
        ResolvedNode(name="idna", version="3.10", dependencies=()),
    )
    expected = (
        "resolution-"
        + hashlib.sha256(
            b"pf:resolution-graph:v1\0"
            + json.dumps(
                [node.model_dump(mode="json") for node in graph],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
    )

    assert resolution_graph_id(graph) == expected
    with pytest.raises(ValueError, match="sorted and unique"):
        resolution_graph_id(tuple(reversed(graph)))


def test_minimal_incomplete_report_round_trips_schema_2(
    tmp_path: Path,
) -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
    )
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(cell,),
        source_plan=SourcePlan(identities=()),
    )
    entries: tuple[SnapshotEntry, ...] = ()
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(entries),
        entries=entries,
    )
    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(),
    )
    path = tmp_path / "package-floor.json"

    ReportStore().write(path, report)

    document = json.loads(path.read_text(encoding="utf-8"))
    loaded = ReportStore().read(path)
    assert isinstance(report, ValidatedReport)
    assert loaded == report
    assert set(document) == {
        "schema_version",
        "identity",
        "inputs",
        "evidence",
        "cell_results",
        "projections",
        "result",
    }
    assert document["schema_version"] == 2
    assert document["inputs"]["target_cells"] == [
        {
            "cell_id": cell_id(cell),
            "package": "demo",
            "target": "x86_64-unknown-linux-gnu",
            "python_minor": "3.12",
            "extra_surface": [],
            "active_declaration_refs": [],
        }
    ]
    assert document["result"] == {
        "status": "incomplete",
        "reasons": ["MISSING_CELL"],
    }
    assert "null" not in path.read_text(encoding="utf-8")
    assert path.read_bytes().endswith(b"\n")


def test_cell_indeterminate_interns_one_failure_and_resolves_its_context(
    tmp_path: Path,
) -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
    )
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(cell,),
        source_plan=SourcePlan(identities=()),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(()),
        entries=(),
    )
    policy = evaluation_policy_identity(package.config)
    failure = FailurePolicy().classify(
        scope=CellFailureScope(
            package="demo",
            cell=cell,
            source_snapshot_digest=snapshot.digest,
            evaluation_policy_identity=policy,
        ),
        cause="TIMEOUT",
        stage="scheduler-deadline",
        process=None,
        detail=FailureDetail(code="deadline", message="cell deadline expired"),
    )
    result = CellIndeterminate(
        cell=cell,
        phase="scheduler-deadline",
        failure_id=failure.failure_id,
        failure_records=(failure,),
    )
    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(result,),
    )
    path = tmp_path / "package-floor.json"

    ReportStore().write(path, report)

    document = json.loads(path.read_text(encoding="utf-8"))
    loaded = ReportStore().read(path)
    assert document["evidence"]["failures"] == [
        {
            "failure_id": failure.failure_id,
            "scope": {"kind": "cell", "cell_ref": cell_id(cell)},
            "disposition": "INDETERMINATE",
            "cause": "TIMEOUT",
            "stage": "scheduler-deadline",
            "detail": {
                "code": "deadline",
                "message": "cell deadline expired",
            },
        }
    ]
    assert document["cell_results"] == [
        {
            "status": "CELL_INDETERMINATE",
            "cell_ref": cell_id(cell),
            "phase": "scheduler-deadline",
            "failure_ref": failure.failure_id,
            "failure_refs": [failure.failure_id],
        }
    ]
    assert loaded.cell_results == (result,)
    assert loaded.failure_records == (failure,)

    legacy_attempt = Attempt.from_identity(
        AttemptIdentity(
            identity_version="attempt-v1",
            source_snapshot_digest=snapshot.digest,
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=(),
            source_plan_identity="sources",
            evaluation_policy_identity=policy,
        )
    )
    legacy_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=legacy_attempt),
        cause="TIMEOUT",
        stage="resolve-project",
        process=None,
        detail=FailureDetail(code="timeout", message="resolver timed out"),
    )
    with pytest.raises(ConfigurationError, match="Schema 2 requires attempt-v2"):
        PackageReportBuilder().build(
            package=package,
            source_snapshot=snapshot,
            cell_results=(
                BaselineIndeterminate(
                    attempt=legacy_attempt,
                    failure=legacy_failure,
                ),
            ),
        )
    assert loaded.failure(failure.failure_id) == failure
    context = loaded.failure_context(failure.failure_id)
    assert context is not None
    assert context.cell == cell
    assert context.proposal_id is None
    assert context.boundary_role is None


def test_baseline_prepare_indeterminate_interns_attempt_without_proposal(
    tmp_path: Path,
) -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
    )
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(cell,),
        source_plan=SourcePlan(identities=()),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(()),
        entries=(),
    )
    policy = evaluation_policy_identity(package.config)
    attempt = Attempt.from_identity(
        AttemptIdentity(
            identity_version="attempt-v2",
            source_snapshot_digest=snapshot.digest,
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=(),
            source_plan_identity="sources",
            evaluation_policy_identity=policy,
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
        )
    )
    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="TIMEOUT",
        stage="resolve-project",
        process=None,
        detail=FailureDetail(code="timeout", message="resolver timed out"),
    )
    result = BaselineIndeterminate(attempt=attempt, failure=failure)
    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(result,),
    )
    path = tmp_path / "package-floor.json"

    ReportStore().write(path, report)

    document = json.loads(path.read_text(encoding="utf-8"))
    loaded = ReportStore().read(path)
    assert document["evidence"]["attempts"] == [
        {
            "attempt_id": attempt.attempt_id,
            "cell_ref": cell_id(cell),
            "requested_resolution": "highest",
            "source_plan_identity": "sources",
            "resolution_context_digest": "context",
            "harness_policy_identity": "original-harness-v1",
            "harness_declaration_ids": [],
        }
    ]
    assert document["evidence"]["proposals"] == []
    assert document["evidence"]["failures"][0]["scope"] == {
        "kind": "attempt",
        "attempt_ref": attempt.attempt_id,
    }
    assert document["cell_results"] == [
        {
            "status": "BASELINE_INDETERMINATE",
            "cell_ref": cell_id(cell),
            "attempt_ref": attempt.attempt_id,
            "failure_refs": [failure.failure_id],
        }
    ]
    assert loaded.cell_results == (result,)
    assert loaded.failure_records == (failure,)


def test_success_interns_shared_evaluation_entities_and_resolves_complete_cell(
    tmp_path: Path,
) -> None:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
    )
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(),
        cells=(cell,),
        source_plan=SourcePlan(identities=()),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(()),
        entries=(),
    )
    policy = evaluation_policy_identity(package.config)
    attempt = Attempt.from_identity(
        AttemptIdentity(
            identity_version="attempt-v2",
            source_snapshot_digest=snapshot.digest,
            cell=cell,
            requested_resolution="highest",
            requested_managed_vector=None,
            active_declaration_ids=(),
            source_plan_identity="sources",
            evaluation_policy_identity=policy,
            resolution_context_digest="context",
            harness_policy_identity="original-harness-v1",
        )
    )
    graph = ()
    project_plan_digest = "project-plan"
    environment_plan_digest = "environment-plan"
    proposal = Proposal(
        proposal_id=environment_identity_digest(
            project_plan_digest=project_plan_digest,
            environment_plan_digest=environment_plan_digest,
            graph=graph,
        ),
        attempt_id=attempt.attempt_id,
        snapshot_digest=snapshot.digest,
        cell=cell,
        managed_vector=(),
        fixed_declaration_ids=(),
        resolved_graph=graph,
        policy_identity=policy,
        project_plan_digest=project_plan_digest,
        environment_plan_digest=environment_plan_digest,
        interpreter=InterpreterIdentity(
            implementation="cpython",
            version="3.12.11",
            abi="cpython-312-x86_64-linux-gnu",
        ),
    )
    process = ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )
    ty = TyCheck(process=process, diagnostics=())
    baseline_digest = ty_diagnostic_digest(())
    static = StaticUnchangedEvaluation(
        proposal=proposal,
        ty=ty,
        baseline_digest=baseline_digest,
    )
    evaluation = PassEvaluation(
        proposal=proposal,
        static=static,
        test=TestPass(process=process),
    )
    result = CellSuccess(
        cell=cell,
        baseline_attempt=attempt,
        static_baseline=StaticBaseline(
            proposal=proposal,
            ty=ty,
            digest=baseline_digest,
        ),
        baseline=evaluation,
        candidate_snapshots=(),
        search=CoordinateSuccess(
            vector=(),
            observations=(),
            boundaries=(),
            sweeps=0,
        ),
        final_vector=(),
        final_evaluation=evaluation,
    )
    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(result,),
    )
    path = tmp_path / "package-floor.json"

    ReportStore().write(path, report)

    document = json.loads(path.read_text(encoding="utf-8"))
    loaded = ReportStore().read(path)
    proposal_ref = proposal.proposal_id
    assert len(document["evidence"]["resolution_graphs"]) == 1
    assert len(document["evidence"]["proposals"]) == 1
    assert len(document["evidence"]["static_evaluations"]) == 1
    assert len(document["evidence"]["evaluations"]) == 1
    assert document["evidence"]["proposals"][0] == {
        "proposal_id": proposal_ref,
        "attempt_ref": attempt.attempt_id,
        "managed_vector": [],
        "fixed_declaration_refs": [],
        "resolution_graph_ref": resolution_graph_id(graph),
        "project_plan_digest": project_plan_digest,
        "environment_plan_digest": environment_plan_digest,
        "interpreter": {
            "implementation": "cpython",
            "version": "3.12.11",
            "abi": "cpython-312-x86_64-linux-gnu",
        },
    }
    assert document["cell_results"] == [
        {
            "status": "SUCCESS",
            "cell_ref": cell_id(cell),
            "baseline": {
                "attempt_ref": attempt.attempt_id,
                "proposal_ref": proposal_ref,
                "static_baseline_digest": baseline_digest,
            },
            "candidate_snapshot_refs": [],
            "search": {
                "status": "SUCCESS",
                "observations": [],
                "boundaries": [],
                "regions": [],
                "sweeps": 0,
            },
            "final_proposal_ref": proposal_ref,
            "failure_refs": [],
        }
    ]
    assert document["result"] == {"status": "complete"}
    assert loaded.cell_results == (result,)
    assert loaded.result.status == "complete"


def test_direct_observations_use_attempt_proposal_and_failure_refs(
    tmp_path: Path,
) -> None:
    dependency = "demo-dep"
    declaration = RequirementDeclaration(
        declaration_id="demo-declaration",
        package="demo",
        location="base",
        name=dependency,
        source=SourceIdentity(kind="registry", index="https://pypi.org/simple"),
        pyproject_path="pyproject.toml",
        raw=dependency,
        kind="searchable",
        managed=True,
    )
    inactive_fixed_declaration = RequirementDeclaration(
        declaration_id="inactive-fixed-declaration",
        package="demo",
        location="base",
        name="fixed-dep",
        specifier="==1.0",
        source=SourceIdentity(kind="registry", index="https://pypi.org/simple"),
        pyproject_path="pyproject.toml",
        raw="fixed-dep==1.0",
        kind="fixed",
        managed=False,
    )
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.12",
        extra_surface=(),
        active_declaration_ids=(declaration.declaration_id,),
    )
    package = PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(),
        declarations=(declaration, inactive_fixed_declaration),
        cells=(cell,),
        source_plan=SourcePlan(identities=()),
    )
    snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(()),
        entries=(),
    )
    policy = evaluation_policy_identity(package.config)
    candidate_policy = "candidate-policy-v1"
    artifact = AvailableArtifact(
        filename="demo_dep-1.0-py3-none-any.whl",
        kind="wheel",
        content_hash=f"sha256:{'a' * 64}",
        locator="https://files.example/demo_dep-1.0-py3-none-any.whl",
    )
    candidates = (
        Candidate(
            version="1.0",
            series_key="1.0",
            artifact=artifact,
        ),
    )
    source = SourceIdentity(kind="registry", index="https://pypi.org/simple")
    candidate_snapshot = CandidateSnapshot(
        dependency=dependency,
        cell=cell,
        policy_identity=candidate_policy,
        source=source,
        candidates=candidates,
        series_representatives=(("1.0", "1.0"),),
        digest=candidate_snapshot_digest(
            dependency=dependency,
            cell=cell,
            policy_identity=candidate_policy,
            source=source,
            candidates=candidates,
            series_representatives=(("1.0", "1.0"),),
        ),
    )
    process = ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )
    baseline_digest = ty_diagnostic_digest(())

    def passed(
        *,
        resolution: Literal["highest", "lowest-direct", "exact-vector"],
        version: str,
        suffix: str,
    ) -> tuple[Attempt, Proposal, PassEvaluation]:
        vector = (VersionPin(name=dependency, version=version),)
        attempt = Attempt.from_identity(
            AttemptIdentity(
                identity_version="attempt-v2",
                source_snapshot_digest=snapshot.digest,
                cell=cell,
                requested_resolution=resolution,
                requested_managed_vector=(
                    vector if resolution == "exact-vector" else None
                ),
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity="sources",
                evaluation_policy_identity=policy,
                resolution_context_digest="context",
                harness_policy_identity=(
                    "harness-relaxation-v1"
                    if resolution == "exact-vector"
                    else "original-harness-v1"
                ),
                harness_declaration_ids=(
                    ("opaque-harness-declaration",)
                    if resolution == "exact-vector"
                    else ()
                ),
                harness_baseline_digest=(
                    "harness-baseline" if resolution == "exact-vector" else None
                ),
                selected_candidate_evidence_digest=(
                    "selected-candidate" if resolution == "exact-vector" else None
                ),
            )
        )
        graph = (ResolvedNode(name=dependency, version=version),)
        project_digest = f"project-{suffix}"
        environment_digest = f"environment-{suffix}"
        proposal = Proposal(
            proposal_id=environment_identity_digest(
                project_plan_digest=project_digest,
                environment_plan_digest=environment_digest,
                graph=graph,
            ),
            attempt_id=attempt.attempt_id,
            snapshot_digest=snapshot.digest,
            cell=cell,
            managed_vector=vector,
            fixed_declaration_ids=(),
            resolved_graph=graph,
            policy_identity=policy,
            project_plan_digest=project_digest,
            environment_plan_digest=environment_digest,
            interpreter=InterpreterIdentity(
                implementation="cpython",
                version="3.12.11",
                abi="cpython-312-x86_64-linux-gnu",
            ),
        )
        static = StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=()),
            baseline_digest=baseline_digest,
        )
        return (
            attempt,
            proposal,
            PassEvaluation(
                proposal=proposal,
                static=static,
                test=TestPass(process=process),
            ),
        )

    baseline_attempt, baseline_proposal, baseline = passed(
        resolution="highest",
        version="2.0",
        suffix="baseline",
    )
    final_attempt, final_proposal, final = passed(
        resolution="exact-vector",
        version="1.0",
        suffix="final",
    )
    final_vector = final_proposal.managed_vector
    result = CellSuccess(
        cell=cell,
        baseline_attempt=baseline_attempt,
        static_baseline=StaticBaseline(
            proposal=baseline_proposal,
            ty=baseline.static.ty,
            digest=baseline_digest,
        ),
        baseline=baseline,
        candidate_snapshots=(candidate_snapshot,),
        search=CoordinateSuccess(
            vector=final_vector,
            observations=(
                ProbeObservation(
                    dependency=dependency,
                    candidate_version="1.0",
                    vector=final_vector,
                    evidence=ProbePass(
                        attempt=final_attempt,
                        proposal_id=final_proposal.proposal_id,
                        evaluation=final,
                    ),
                ),
            ),
            boundaries=(CoordinateBoundary(dependency=dependency, floor="1.0"),),
            sweeps=1,
        ),
        final_vector=final_vector,
        final_evaluation=final,
    )
    report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(result,),
    )
    path = tmp_path / "package-floor.json"

    ReportStore().write(path, report)

    document = json.loads(path.read_text(encoding="utf-8"))
    loaded = ReportStore().read(path)
    assert document["inputs"]["candidate_snapshots"] == [
        {
            "candidate_snapshot_id": candidate_snapshot.digest,
            "dependency": dependency,
            "cell_ref": cell_id(cell),
            "policy_identity": candidate_policy,
            "source": source.model_dump(mode="json", exclude_none=True),
            "candidates": [candidates[0].model_dump(mode="json", exclude_none=True)],
            "series_representatives": [["1.0", "1.0"]],
        }
    ]
    observation = document["cell_results"][0]["search"]["observations"][0]
    assert observation == {
        "dependency": dependency,
        "candidate_version": "1.0",
        "evidence": {
            "kind": "DIRECT",
            "attempt_ref": final_attempt.attempt_id,
            "status": "PASS",
        },
    }
    assert "vector" not in observation
    assert document["cell_results"][0]["final_proposal_ref"] == (
        final_proposal.proposal_id
    )
    assert document["projections"] == [
        {
            "declaration_ref": declaration.declaration_id,
            "floors": [{"cell_ref": cell_id(cell), "version": "1.0"}],
            "projected_requirements": ["demo-dep>=1.0"],
            "representable": True,
        }
    ]
    assert loaded.cell_results == (result,)

    predecessor = Candidate(
        version="0.9",
        series_key="0.9",
        artifact=artifact.model_copy(
            update={
                "filename": "demo_dep-0.9-py3-none-any.whl",
                "locator": "https://files.example/demo_dep-0.9-py3-none-any.whl",
            }
        ),
    )
    expanded_candidates = (predecessor, *candidates)
    expanded_representatives = (("0.9", "0.9"), ("1.0", "1.0"))
    expanded_snapshot = CandidateSnapshot(
        dependency=dependency,
        cell=cell,
        policy_identity=candidate_policy,
        source=source,
        candidates=expanded_candidates,
        series_representatives=expanded_representatives,
        digest=candidate_snapshot_digest(
            dependency=dependency,
            cell=cell,
            policy_identity=candidate_policy,
            source=source,
            candidates=expanded_candidates,
            series_representatives=expanded_representatives,
        ),
    )
    rejected_attempt, rejected_proposal, rejected_pass = passed(
        resolution="exact-vector",
        version="0.9",
        suffix="rejected",
    )
    rejection_process = ProcessResult(
        exit_code=1,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="resolution failed",
    )
    failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=rejected_attempt),
        cause="RESOLUTION_CONFLICT",
        stage="resolve-project",
        process=rejection_process,
    )
    rejected_result = CellSuccess(
        cell=cell,
        baseline_attempt=baseline_attempt,
        static_baseline=result.static_baseline,
        baseline=baseline,
        candidate_snapshots=(expanded_snapshot,),
        search=CoordinateSuccess(
            vector=final_vector,
            observations=(
                ProbeObservation(
                    dependency=dependency,
                    candidate_version="0.9",
                    vector=rejected_attempt.identity.requested_managed_vector or (),
                    evidence=ProbeRejection(
                        attempt=rejected_attempt,
                        failure_id=failure.failure_id,
                        cause=failure.cause,
                    ),
                ),
                result.search.observations[0],
            ),
            boundaries=(
                CoordinateBoundary(
                    dependency=dependency,
                    floor="1.0",
                    predecessor="0.9",
                    predecessor_failure_id=failure.failure_id,
                ),
            ),
            sweeps=1,
        ),
        final_vector=final_vector,
        final_evaluation=final,
        failure_records=(failure,),
    )
    rejected_report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(rejected_result,),
    )
    rejected_path = tmp_path / "rejected.json"

    ReportStore().write(rejected_path, rejected_report)

    rejected_document = json.loads(rejected_path.read_text(encoding="utf-8"))
    rejected_loaded = ReportStore().read(rejected_path)
    rejection = rejected_document["cell_results"][0]["search"]["observations"][0]
    assert rejection["evidence"] == {
        "kind": "DIRECT",
        "attempt_ref": rejected_attempt.attempt_id,
        "status": "REJECTED",
        "failure_ref": failure.failure_id,
    }
    assert (
        rejected_document["cell_results"][0]["search"]["boundaries"][0][
            "predecessor_failure_ref"
        ]
        == failure.failure_id
    )
    assert rejected_document["cell_results"][0]["failure_refs"] == [failure.failure_id]
    assert rejected_loaded == rejected_report
    rejected_context = rejected_loaded.failure_context(failure.failure_id)
    assert rejected_context is not None
    assert rejected_context.proposal_id is None
    assert rejected_context.boundary_role == "predecessor"
    portable_failure = rejected_loaded.failure(failure.failure_id)
    assert portable_failure is not None
    assert portable_failure.process is not None
    assert portable_failure.process.stderr == ""

    test_process = ProcessResult(
        exit_code=1,
        signal=None,
        duration_seconds=0.2,
        stdout="",
        stderr="failed test",
    )
    diagnostic = TyDiagnostic(
        identity="snapshot|demo.py|1|1|invalid-argument-type",
        origin="snapshot",
        path="demo.py",
        line=1,
        column=1,
        code="invalid-argument-type",
        severity="major",
        message="incompatible call",
    )
    regression = StaticRegressionEvaluation(
        proposal=rejected_proposal,
        ty=TyCheck(
            process=process.model_copy(update={"exit_code": 1}),
            diagnostics=(diagnostic,),
        ),
        baseline_digest=baseline_digest,
        incremental=(diagnostic,),
        static_fingerprint=static_fingerprint((diagnostic.identity,)),
        classifications=(
            DiagnosticClassification(
                diagnostic_identity=diagnostic.identity,
                classification="general",
                reason_code="not-runtime-witnessable",
            ),
        ),
    )
    test_evaluation = TestFailEvaluation(
        proposal=rejected_proposal,
        static=regression,
        test=TestFail(process=test_process),
    )
    test_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=rejected_attempt),
        cause="TEST_FAILURE",
        stage="test",
        process=test_process,
    )
    test_failed_result = CellSuccess(
        cell=cell,
        baseline_attempt=baseline_attempt,
        static_baseline=result.static_baseline,
        baseline=baseline,
        candidate_snapshots=(expanded_snapshot,),
        search=CoordinateSuccess(
            vector=final_vector,
            observations=(
                ProbeObservation(
                    dependency=dependency,
                    candidate_version="0.9",
                    vector=rejected_attempt.identity.requested_managed_vector or (),
                    evidence=ProbeRejection(
                        attempt=rejected_attempt,
                        proposal_id=rejected_proposal.proposal_id,
                        failure_id=test_failure.failure_id,
                        cause=test_failure.cause,
                        evaluation=test_evaluation,
                    ),
                ),
                result.search.observations[0],
            ),
            boundaries=(
                CoordinateBoundary(
                    dependency=dependency,
                    floor="1.0",
                    predecessor="0.9",
                    predecessor_failure_id=test_failure.failure_id,
                ),
            ),
            sweeps=1,
        ),
        final_vector=final_vector,
        final_evaluation=final,
        failure_records=(test_failure,),
    )
    test_failed_report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(test_failed_result,),
    )
    test_failed_path = tmp_path / "test-failed.json"

    ReportStore().write(test_failed_path, test_failed_report)

    test_failed_document = json.loads(test_failed_path.read_text(encoding="utf-8"))
    test_failed_loaded = ReportStore().read(test_failed_path)
    terminal = next(
        item
        for item in test_failed_document["evidence"]["evaluations"]
        if item["proposal_ref"] == rejected_proposal.proposal_id
    )
    assert terminal["status"] == "TEST_FAIL"
    assert terminal["failure_ref"] == test_failure.failure_id
    assert "test" not in terminal
    static_record = next(
        item
        for item in test_failed_document["evidence"]["static_evaluations"]
        if item["proposal_ref"] == rejected_proposal.proposal_id
    )
    assert static_record["status"] == "STATIC_REGRESSION"
    assert static_record["classifications"][0]["reason_code"] == (
        "not-runtime-witnessable"
    )
    assert test_failed_loaded == test_failed_report
    test_failure_context = test_failed_loaded.failure_context(test_failure.failure_id)
    assert test_failure_context is not None
    assert test_failure_context.proposal_id == rejected_proposal.proposal_id
    assert test_failure_context.boundary_role == "predecessor"

    witness_plan = RuntimeWitnessPlan(
        diagnostic_identities=(diagnostic.identity,),
        managed_dependency=dependency,
        operation="import-module",
        module="demo_dep",
    )
    witness_regression = regression.model_copy(
        update={
            "classifications": (
                DiagnosticClassification(
                    diagnostic_identity=diagnostic.identity,
                    classification="strong",
                    reason_code="runtime-witnessable",
                    witness_plan=witness_plan,
                ),
            )
        }
    )
    missing_evaluation = RuntimeInterfaceMissingEvaluation(
        proposal=rejected_proposal,
        static=witness_regression,
        witnesses=(
            RuntimeWitnessAttempt(
                plan=witness_plan,
                outcome=RuntimeWitnessResult(
                    status="CONFIRMED_MISSING",
                    plan=witness_plan,
                    process=process,
                ),
            ),
        ),
    )
    missing_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=rejected_attempt),
        cause="RUNTIME_INTERFACE_MISSING",
        stage="witness",
        process=process,
    )
    missing_result = test_failed_result.model_copy(
        update={
            "search": test_failed_result.search.model_copy(
                update={
                    "observations": (
                        ProbeObservation(
                            dependency=dependency,
                            candidate_version="0.9",
                            vector=(
                                rejected_attempt.identity.requested_managed_vector or ()
                            ),
                            evidence=ProbeRejection(
                                attempt=rejected_attempt,
                                proposal_id=rejected_proposal.proposal_id,
                                failure_id=missing_failure.failure_id,
                                cause=missing_failure.cause,
                                evaluation=missing_evaluation,
                            ),
                        ),
                        result.search.observations[0],
                    ),
                    "boundaries": (
                        CoordinateBoundary(
                            dependency=dependency,
                            floor="1.0",
                            predecessor="0.9",
                            predecessor_failure_id=missing_failure.failure_id,
                        ),
                    ),
                }
            ),
            "failure_records": (missing_failure,),
        }
    )
    missing_report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(missing_result,),
    )
    missing_path = tmp_path / "runtime-missing.json"

    ReportStore().write(missing_path, missing_report)

    missing_document = json.loads(missing_path.read_text(encoding="utf-8"))
    missing_loaded = ReportStore().read(missing_path)
    terminal = next(
        item
        for item in missing_document["evidence"]["evaluations"]
        if item["proposal_ref"] == rejected_proposal.proposal_id
    )
    assert terminal["status"] == "RUNTIME_INTERFACE_MISSING"
    assert terminal["failure_ref"] == missing_failure.failure_id
    assert terminal["witnesses"][-1]["outcome"] == {
        "status": "CONFIRMED_MISSING",
        "failure_ref": missing_failure.failure_id,
    }
    assert missing_loaded == missing_report

    tool_process = ProcessResult(
        exit_code=2,
        signal=None,
        duration_seconds=0.2,
        stdout="",
        stderr="pytest usage failure",
    )
    tool_failure = ToolFailure(
        cause="TOOL_FAILURE",
        stage="test",
        process=tool_process,
    )
    indeterminate_evaluation = IndeterminateEvaluation(
        proposal=rejected_proposal,
        cause=tool_failure.cause,
        failure=tool_failure,
        static=regression,
    )
    indeterminate_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=rejected_attempt),
        cause=tool_failure.cause,
        stage=tool_failure.stage,
        process=tool_process,
    )
    coordinate_failure = CoordinateFailure(
        status="INDETERMINATE",
        dependency=dependency,
        observations=(
            ProbeObservation(
                dependency=dependency,
                candidate_version="0.9",
                vector=rejected_attempt.identity.requested_managed_vector or (),
                evidence=ProbeIndeterminate(
                    attempt=rejected_attempt,
                    proposal_id=rejected_proposal.proposal_id,
                    failure_id=indeterminate_failure.failure_id,
                    cause=indeterminate_failure.cause,
                    evaluation=indeterminate_evaluation,
                ),
            ),
        ),
        failure_id=indeterminate_failure.failure_id,
    )
    indeterminate_result = CellIndeterminate(
        cell=cell,
        phase="test",
        failure_id=indeterminate_failure.failure_id,
        failure_records=(indeterminate_failure,),
        baseline_attempt=baseline_attempt,
        static_baseline=result.static_baseline,
        baseline=baseline,
        candidate_snapshots=(expanded_snapshot,),
        coordinate_failure=coordinate_failure,
    )
    indeterminate_report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(indeterminate_result,),
    )
    indeterminate_path = tmp_path / "indeterminate.json"

    ReportStore().write(indeterminate_path, indeterminate_report)

    indeterminate_document = json.loads(indeterminate_path.read_text(encoding="utf-8"))
    indeterminate_loaded = ReportStore().read(indeterminate_path)
    terminal = next(
        item
        for item in indeterminate_document["evidence"]["evaluations"]
        if item["proposal_ref"] == rejected_proposal.proposal_id
    )
    assert terminal["status"] == "INDETERMINATE"
    assert terminal["failure_ref"] == indeterminate_failure.failure_id
    assert (
        indeterminate_document["cell_results"][0]["coordinate_failure"]["failure_ref"]
        == indeterminate_failure.failure_id
    )
    assert indeterminate_loaded == indeterminate_report

    cheap_candidate = Candidate(
        version="0.8",
        series_key="0.8",
        artifact=artifact.model_copy(
            update={
                "filename": "demo_dep-0.8-py3-none-any.whl",
                "locator": "https://files.example/demo_dep-0.8-py3-none-any.whl",
            }
        ),
    )
    region_candidates = (cheap_candidate, *expanded_candidates)
    region_snapshot = CandidateSnapshot(
        dependency=dependency,
        cell=cell,
        policy_identity=policy,
        source=source,
        candidates=region_candidates,
        series_representatives=(
            ("0.8", "0.8"),
            ("0.9", "0.9"),
            ("1.0", "1.0"),
        ),
        digest=candidate_snapshot_digest(
            dependency=dependency,
            cell=cell,
            policy_identity=policy,
            source=source,
            candidates=region_candidates,
            series_representatives=(
                ("0.8", "0.8"),
                ("0.9", "0.9"),
                ("1.0", "1.0"),
            ),
        ),
    )
    cheap_attempt, cheap_proposal, cheap_pass = passed(
        resolution="exact-vector",
        version="0.8",
        suffix="cheap",
    )

    def regional_static(proposal: Proposal) -> StaticRegressionEvaluation:
        return StaticRegressionEvaluation(
            proposal=proposal,
            ty=regression.ty,
            baseline_digest=regression.baseline_digest,
            incremental=regression.incremental,
            static_fingerprint=regression.static_fingerprint,
            classifications=regression.classifications,
        )

    regional_final = PassEvaluation(
        proposal=final_proposal,
        static=regional_static(final_proposal),
        test=TestPass(process=process),
    )
    regional_rejection = TestFailEvaluation(
        proposal=rejected_proposal,
        static=regression,
        test=TestFail(process=test_process),
    )
    baseline_test_failure = FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=baseline_attempt),
        cause="TEST_FAILURE",
        stage="test",
        process=test_process,
    )
    baseline_test_evaluation = TestFailEvaluation(
        proposal=baseline_proposal,
        static=baseline.static,
        test=TestFail(process=test_process),
    )
    baseline_rejection_report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(
            BaselineRejection(
                attempt=baseline_attempt,
                failure=baseline_test_failure,
                static_baseline=StaticBaseline(
                    proposal=baseline_proposal,
                    ty=baseline.static.ty,
                    digest=baseline_digest,
                ),
                evaluation=baseline_test_evaluation,
            ),
        ),
    )
    baseline_rejection_path = tmp_path / "baseline-test-failed.json"
    ReportStore().write(
        baseline_rejection_path,
        baseline_rejection_report,
    )
    baseline_rejection_document = json.loads(
        baseline_rejection_path.read_text(encoding="utf-8")
    )
    assert (
        baseline_rejection_document["cell_results"][0]["proposal_ref"]
        == baseline_proposal.proposal_id
    )
    assert ReportStore().read(baseline_rejection_path) == baseline_rejection_report
    region_slice = StaticRegionSlice(
        cell=cell,
        source_snapshot_digest=snapshot.digest,
        policy_identity=policy,
        baseline_digest=baseline_digest,
        active_dependency=dependency,
        other_coordinates=(),
        candidate_order=("0.8", "0.9", "1.0"),
    )
    region = StaticRegion(
        slice=region_slice,
        static_fingerprint=regression.static_fingerprint,
        observed_versions=("0.8", "0.9", "1.0"),
        runtime_references=(
            StaticRegionRuntimeReference(
                proposal_id=rejected_proposal.proposal_id,
                status="REJECTED",
            ),
            StaticRegionRuntimeReference(
                proposal_id=final_proposal.proposal_id,
                status="PASS",
            ),
        ),
    )
    regional_result = CellSuccess(
        cell=cell,
        baseline_attempt=baseline_attempt,
        static_baseline=result.static_baseline,
        baseline=baseline,
        candidate_snapshots=(region_snapshot,),
        search=CoordinateSuccess(
            vector=final_vector,
            observations=(
                ProbeObservation(
                    dependency=dependency,
                    candidate_version="0.8",
                    vector=cheap_proposal.managed_vector,
                    evidence=StaticOnlyEvidence(
                        attempt=cheap_attempt,
                        proposal_id=cheap_proposal.proposal_id,
                        static_evaluation=regional_static(cheap_proposal),
                        guidance="REJECTED",
                        region_slice=region_slice,
                        representative_proposal_id=(rejected_proposal.proposal_id),
                    ),
                ),
                ProbeObservation(
                    dependency=dependency,
                    candidate_version="0.9",
                    vector=rejected_proposal.managed_vector,
                    evidence=ProbeRejection(
                        attempt=rejected_attempt,
                        proposal_id=rejected_proposal.proposal_id,
                        failure_id=test_failure.failure_id,
                        cause=test_failure.cause,
                        evaluation=regional_rejection,
                    ),
                ),
                ProbeObservation(
                    dependency=dependency,
                    candidate_version="1.0",
                    vector=final_vector,
                    evidence=ProbePass(
                        attempt=final_attempt,
                        proposal_id=final_proposal.proposal_id,
                        evaluation=regional_final,
                    ),
                ),
            ),
            boundaries=(
                CoordinateBoundary(
                    dependency=dependency,
                    floor="1.0",
                    predecessor="0.9",
                    predecessor_failure_id=test_failure.failure_id,
                ),
            ),
            regions=(region,),
            sweeps=1,
        ),
        final_vector=final_vector,
        final_evaluation=regional_final,
        failure_records=(test_failure,),
    )
    regional_report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(regional_result,),
    )
    regional_path = tmp_path / "static-region.json"

    ReportStore().write(regional_path, regional_report)

    regional_document = json.loads(regional_path.read_text(encoding="utf-8"))
    regional_loaded = ReportStore().read(regional_path)
    wire_region = regional_document["cell_results"][0]["search"]["regions"][0]
    assert wire_region["region_id"] == static_region_id(region)
    assert wire_region["candidate_snapshot_ref"] == region_snapshot.digest
    assert wire_region["runtime_references"] == [
        {"proposal_ref": reference}
        for reference in sorted(
            (
                rejected_proposal.proposal_id,
                final_proposal.proposal_id,
            )
        )
    ]
    static_only = regional_document["cell_results"][0]["search"]["observations"][0][
        "evidence"
    ]
    assert static_only == {
        "kind": "STATIC_ONLY",
        "attempt_ref": cheap_attempt.attempt_id,
        "guidance": "REJECTED",
        "region_ref": static_region_id(region),
        "representative_proposal_ref": rejected_proposal.proposal_id,
    }
    assert regional_loaded == regional_report

    non_monotonic = CoordinateFailure(
        status="NON_MONOTONIC",
        dependency=dependency,
        observations=(
            ProbeObservation(
                dependency=dependency,
                candidate_version="0.8",
                vector=cheap_proposal.managed_vector,
                evidence=ProbePass(
                    attempt=cheap_attempt,
                    proposal_id=cheap_proposal.proposal_id,
                    evaluation=cheap_pass,
                ),
            ),
            ProbeObservation(
                dependency=dependency,
                candidate_version="0.9",
                vector=rejected_proposal.managed_vector,
                evidence=ProbeRejection(
                    attempt=rejected_attempt,
                    failure_id=failure.failure_id,
                    cause=failure.cause,
                ),
            ),
        ),
        counterexample=("0.8", "0.9"),
    )
    search_failure_result = CellSearchFailure(
        reason="NON_MONOTONIC",
        cell=cell,
        phase="coordinate-search",
        baseline_attempt=baseline_attempt,
        static_baseline=result.static_baseline,
        baseline=baseline,
        candidate_snapshots=(region_snapshot,),
        coordinate_failure=non_monotonic,
        failure_records=(failure,),
    )
    search_failure_report = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(search_failure_result,),
    )
    search_failure_path = tmp_path / "search-failed.json"

    ReportStore().write(search_failure_path, search_failure_report)

    search_failure_document = json.loads(
        search_failure_path.read_text(encoding="utf-8")
    )
    search_failure_loaded = ReportStore().read(search_failure_path)
    assert search_failure_document["cell_results"][0]["status"] == ("SEARCH_FAILED")
    assert search_failure_document["cell_results"][0]["coordinate_failure"][
        "counterexample"
    ] == ["0.8", "0.9"]
    assert search_failure_loaded == search_failure_report

    store = ReportStore()
    assert store.merge((report, report)) == report
    with pytest.raises(ConfigurationError, match="conflicting result for cell"):
        store.merge((report, rejected_report))
    updated = store.update(report, rejected_report)
    assert updated.cell_results == rejected_report.cell_results
    assert updated.failure_records == rejected_report.failure_records
    empty_replacement = PackageReportBuilder().build(
        package=package,
        source_snapshot=snapshot,
        cell_results=(),
    )
    assert store.update(updated, empty_replacement) is updated

    update_path = tmp_path / "updated.json"
    created = store.update_path(update_path, rejected_report)
    assert created.replace_generation is True
    assert created.removed_failure_ids == ()
    same_generation = store.update_path(update_path, report)
    assert same_generation.replace_generation is False
    assert same_generation.removed_failure_ids == (failure.failure_id,)
    assert store.read(update_path) == report

    new_snapshot = SourceSnapshotIdentity(
        digest=source_snapshot_digest(
            (
                SnapshotEntry(
                    path="README.md",
                    kind="file",
                    mode=0o644,
                    content_digest="f" * 64,
                ),
            )
        ),
        entries=(
            SnapshotEntry(
                path="README.md",
                kind="file",
                mode=0o644,
                content_digest="f" * 64,
            ),
        ),
    )
    new_generation = PackageReportBuilder().build(
        package=package,
        source_snapshot=new_snapshot,
        cell_results=(),
    )
    replaced_generation = store.update_path(update_path, new_generation)
    assert replaced_generation.replace_generation is True
    assert replaced_generation.removed_failure_ids == ()
    assert store.read(update_path) == new_generation

    tampered_path = tmp_path / "tampered.json"

    def rejects(mutator, message: str) -> None:
        tampered = copy.deepcopy(regional_document)
        mutator(tampered)
        tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ConfigurationError, match=message):
            store.read(tampered_path)

    rejects(
        lambda value: value["evidence"]["attempts"].append(
            copy.deepcopy(value["evidence"]["attempts"][0])
        ),
        "Attempt IDs must be sorted and unique",
    )

    def duplicate_record(section: str, table: str):
        def mutate(value) -> None:
            value[section][table].append(copy.deepcopy(value[section][table][0]))

        return mutate

    for section, table, message in (
        (
            "inputs",
            "requirement_declarations",
            "requirement declarations must be sorted and unique",
        ),
        ("inputs", "target_cells", "target Cells must be sorted and unique"),
        (
            "inputs",
            "candidate_snapshots",
            "CandidateSnapshots must be sorted and unique",
        ),
        (
            "evidence",
            "resolution_graphs",
            "ResolutionGraph IDs must be sorted and unique",
        ),
        ("evidence", "proposals", "Proposal IDs must be sorted and unique"),
        (
            "evidence",
            "static_evaluations",
            "StaticEvaluations must be sorted and unique",
        ),
        ("evidence", "evaluations", "Evaluations must be sorted and unique"),
        (
            "evidence",
            "failures",
            "FailureRecord IDs must be sorted and unique",
        ),
    ):
        rejects(duplicate_record(section, table), message)

    rejects(
        lambda value: value["cell_results"].append(
            copy.deepcopy(value["cell_results"][0])
        ),
        "CellResults must be sorted and unique",
    )
    rejects(
        lambda value: value["projections"].append(
            copy.deepcopy(value["projections"][0])
        ),
        "projections must be sorted and unique",
    )
    rejects(
        lambda value: value["cell_results"][0]["search"]["observations"][0][
            "evidence"
        ].update({"attempt_ref": "missing-attempt"}),
        "incomplete static-only evidence",
    )
    rejects(
        lambda value: value["cell_results"][0]["search"]["observations"][0][
            "evidence"
        ].update({"attempt_ref": cheap_proposal.proposal_id}),
        "incomplete static-only evidence",
    )

    orphan_graph = (ResolvedNode(name="orphan", version="1.0"),)
    orphan_graph_id = resolution_graph_id(orphan_graph)

    def add_orphan_graph(value) -> None:
        value["evidence"]["resolution_graphs"].append(
            {
                "resolution_graph_id": orphan_graph_id,
                "nodes": [orphan_graph[0].model_dump(mode="json")],
            }
        )
        value["evidence"]["resolution_graphs"].sort(
            key=lambda item: item["resolution_graph_id"]
        )

    rejects(add_orphan_graph, "unreachable ResolutionGraph")
    rejects(
        lambda value: value["evidence"]["proposals"][0].update(
            {"project_plan_digest": "tampered"}
        ),
        "Proposal identity mismatch",
    )

    def empty_project_plan_digest(value) -> None:
        record = next(
            item
            for item in value["evidence"]["proposals"]
            if item["proposal_id"] == cheap_proposal.proposal_id
        )
        record.update(
            {
                "proposal_id": environment_identity_digest(
                    project_plan_digest="",
                    environment_plan_digest=record["environment_plan_digest"],
                    graph=cheap_proposal.resolved_graph,
                ),
                "project_plan_digest": "",
            }
        )
        value["evidence"]["proposals"].sort(key=lambda item: item["proposal_id"])

    rejects(empty_project_plan_digest, "Proposal is missing plan identity")

    def empty_environment_plan_digest(value) -> None:
        record = next(
            item
            for item in value["evidence"]["proposals"]
            if item["proposal_id"] == cheap_proposal.proposal_id
        )
        record.update(
            {
                "proposal_id": environment_identity_digest(
                    project_plan_digest=record["project_plan_digest"],
                    environment_plan_digest="",
                    graph=cheap_proposal.resolved_graph,
                ),
                "environment_plan_digest": "",
            }
        )
        value["evidence"]["proposals"].sort(key=lambda item: item["proposal_id"])

    rejects(empty_environment_plan_digest, "Proposal is missing plan identity")
    rejects(
        lambda value: value["evidence"]["proposals"][0]["interpreter"].update(
            {"version": "3.11.9"}
        ),
        "Proposal interpreter does not match its Cell",
    )
    rejects(
        lambda value: value["evidence"]["proposals"][0]["interpreter"].update(
            {"abi": "tampered-abi"}
        ),
        "Proposal interpreter does not match its Cell",
    )

    def duplicate_fixed_declarations(value) -> None:
        value["evidence"]["proposals"][0]["fixed_declaration_refs"] = [
            inactive_fixed_declaration.declaration_id,
            inactive_fixed_declaration.declaration_id,
        ]

    rejects(
        duplicate_fixed_declarations,
        "Proposal fixed declaration refs must be sorted and unique",
    )
    rejects(
        lambda value: value["evidence"]["proposals"][0].update(
            {"fixed_declaration_refs": [inactive_fixed_declaration.declaration_id]}
        ),
        "Proposal fixed declarations are not active",
    )
    rejects(
        lambda value: value["evidence"]["proposals"][0].update(
            {"fixed_declaration_refs": [declaration.declaration_id]}
        ),
        "Proposal declaration is not fixed",
    )
    rejects(
        lambda value: value["inputs"]["candidate_snapshots"][0]["candidates"][0].update(
            {"version": "tampered"}
        ),
        "CandidateSnapshot identity mismatch",
    )

    def reverse_candidate_order_with_matching_digest(value) -> None:
        record = value["inputs"]["candidate_snapshots"][0]
        record["candidates"].reverse()
        record["series_representatives"].reverse()
        record["candidate_snapshot_id"] = candidate_snapshot_digest(
            dependency=record["dependency"],
            cell=cell,
            policy_identity=record["policy_identity"],
            source=SourceIdentity.model_validate(record["source"]),
            candidates=tuple(
                Candidate.model_validate(item) for item in record["candidates"]
            ),
            series_representatives=tuple(
                tuple(item) for item in record["series_representatives"]
            ),
        )

    rejects(
        reverse_candidate_order_with_matching_digest,
        "CandidateSnapshot identity mismatch",
    )
    rejects(
        lambda value: value["identity"].update(
            {"report_generation_id": "tampered-generation"}
        ),
        "report generation identity mismatch",
    )
    rejects(
        lambda value: value["cell_results"][0]["search"]["regions"][0].update(
            {"candidate_snapshot_ref": "missing-candidate"}
        ),
        "Region CandidateSnapshot is not owned",
    )
    rejects(
        lambda value: value["cell_results"][0]["search"]["regions"][0][
            "runtime_references"
        ][0].update({"proposal_ref": cheap_proposal.proposal_id}),
        "Region representative is not local direct evidence",
    )
    rejects(
        lambda value: value["cell_results"][0]["search"]["boundaries"][0].update(
            {"predecessor_failure_ref": "missing-failure"}
        ),
        "CellResult evidence mismatch",
    )
    rejects(
        lambda value: value["cell_results"][0].update(
            {"final_proposal_ref": rejected_proposal.proposal_id}
        ),
        "unknown baseline/final ref",
    )
    rejects(
        lambda value: value["projections"][0]["floors"][0].update({"version": "9.9"}),
        "projection evidence mismatch",
    )
    rejects(
        lambda value: value["identity"].update({"unexpected": True}),
        "extra_forbidden",
    )
    rejects(
        lambda value: value["cell_results"][0]["search"].update({"sweeps": True}),
        "explicit wire facts are missing or not canonical",
    )
    rejects(
        lambda value: value["evidence"]["static_evaluations"][0]["ty"][
            "process"
        ].update({"duration_seconds": False}),
        "explicit wire facts are missing or not canonical",
    )
    rejects(
        lambda value: value["cell_results"][0]["search"]["boundaries"][0].update(
            {"predecessor": None}
        ),
        "optional fields must be omitted, not null",
    )

    def reverse_proposals(value) -> None:
        value["evidence"]["proposals"].reverse()

    rejects(reverse_proposals, "Proposal IDs must be sorted and unique")

    secret = "SECRET-PROCESS-OUTPUT\x1b[31m"
    leaked_input = copy.deepcopy(regional_document)
    leaked_input["identity"]["package"]["name"] = {"secret": secret}
    tampered_path.write_text(json.dumps(leaked_input), encoding="utf-8")
    with pytest.raises(ConfigurationError) as structure_error:
        store.read(tampered_path)
    assert secret not in str(structure_error.value)
    assert "\x1b" not in str(structure_error.value)

    leaked_id = copy.deepcopy(regional_document)
    leaked_id["evidence"]["proposals"][0]["proposal_id"] = secret
    leaked_id["evidence"]["proposals"].sort(key=lambda item: item["proposal_id"])
    tampered_path.write_text(json.dumps(leaked_id), encoding="utf-8")
    with pytest.raises(ConfigurationError) as identity_error:
        store.read(tampered_path)
    assert secret not in str(identity_error.value)
    assert "\x1b" not in str(identity_error.value)

    printable_secret = "SECRET-PROCESS-OUTPUT[red]"
    leaked_cell = copy.deepcopy(regional_document)
    leaked_cell["inputs"]["target_cells"][0]["cell_id"] = printable_secret
    leaked_cell["inputs"]["target_cells"][0]["active_declaration_refs"] = [
        "missing-declaration"
    ]
    tampered_path.write_text(json.dumps(leaked_cell), encoding="utf-8")
    with pytest.raises(ConfigurationError) as cell_error:
        store.read(tampered_path)
    assert printable_secret not in str(cell_error.value)

    leaked_cell_result = copy.deepcopy(regional_document)
    leaked_cell_result["cell_results"][0]["cell_ref"] = printable_secret
    tampered_path.write_text(json.dumps(leaked_cell_result), encoding="utf-8")
    with pytest.raises(ConfigurationError) as cell_result_error:
        store.read(tampered_path)
    assert printable_secret not in str(cell_result_error.value)

    rejects(
        lambda value: value["identity"]["package"].update(
            {"pyproject_path": "/tmp/secret/pyproject.toml"}
        ),
        "invalid v2 report structure",
    )
    rejects(
        lambda value: value["inputs"]["requirement_declarations"][0]["source"].update(
            {"locator": ("https://alice:secret@example.com/pkg.whl?token=abc")}
        ),
        "RequirementDeclaration has a non-public source locator",
    )
    rejects(
        lambda value: value["inputs"]["candidate_snapshots"][0]["candidates"][0][
            "artifact"
        ].update({"locator": "https://alice:secret@example.com/pkg.whl?token=abc"}),
        "CandidateSnapshot has a non-public locator",
    )
    rejects(
        lambda value: value["inputs"]["candidate_snapshots"][0]["candidates"][0][
            "artifact"
        ].update({"locator": "/tmp/secret.whl"}),
        "CandidateSnapshot has a non-public locator",
    )
    for windows_locator in ("c:/secret.whl", "c:\\secret.whl"):
        rejects(
            lambda value, locator=windows_locator: value["inputs"][
                "candidate_snapshots"
            ][0]["candidates"][0]["artifact"].update({"locator": locator}),
            "CandidateSnapshot has a non-public locator",
        )
    rejects(
        lambda value: value["inputs"]["candidate_snapshots"][0]["candidates"][0][
            "artifact"
        ].update({"locator": "https://example.com:invalid/secret.whl"}),
        "CandidateSnapshot has a non-public locator",
    )
    rejects(
        lambda value: value["evidence"]["proposals"][0]["interpreter"].update(
            {"abi": "cpython-312evil"}
        ),
        "Proposal interpreter does not match its Cell",
    )

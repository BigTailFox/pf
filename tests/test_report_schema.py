from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Literal

import pytest

from pf.errors import ConfigurationError
from pf.resolution import environment_identity_digest, resolution_graph_id
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.base import canonical_identity_json
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellFailureScope,
    FailureDetail,
    FailureRecord,
    DiagnosticClassification,
    NormalExit,
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
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
    VerificationJournal,
    VerificationJournalEntry,
    VerificationPackagePolicy,
    ty_diagnostic_digest,
)
from pf.static_transition import static_fingerprint
from pf.schemas.project import (
    Cell,
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    DependencySourceRoute,
    PackagePlan,
    InterpreterIdentity,
    NamedSearchPolicy,
    Proposal,
    ResolvedNode,
    RequirementDeclaration,
    SelectedCandidate,
    SnapshotEntry,
    SourceSnapshotIdentity,
    SourceIdentity,
    SourcePlan,
    VersionPin,
    candidate_snapshot_digest,
    cell_id,
    selected_candidate_evidence_digest,
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


NON_PUBLIC_PATHS = (
    "/tmp/secret.py",
    "C:\\secret.py",
    "../secret.py",
    "src/../secret.py",
)


class TestReportDomain:
    @pytest.mark.parametrize(
        "path",
        NON_PUBLIC_PATHS,
    )
    def test_snapshot_entry_rejects_non_public_path(
        self,
        path: str,
    ) -> None:
        with pytest.raises(ValueError):
            SnapshotEntry(path=path, kind="file", mode=0o644)

    @pytest.mark.parametrize("path", NON_PUBLIC_PATHS)
    def test_requirement_declaration_rejects_non_public_pyproject_path(
        self,
        path: str,
    ) -> None:
        with pytest.raises(ValueError):
            RequirementDeclaration(
                declaration_id="declaration",
                package="demo",
                location="base",
                name="foo",
                pyproject_path=path,
                raw="foo",
                kind="searchable",
                managed=True,
            )

    @pytest.mark.parametrize("path", NON_PUBLIC_PATHS)
    def test_package_identity_rejects_non_public_pyproject_path(
        self,
        path: str,
    ) -> None:
        with pytest.raises(ValueError):
            PackageIdentity(name="demo", pyproject_path=path)

    def test_cell_rejects_noncanonical_package_name(self) -> None:
        malicious = "[link=https://evil.example]demo[/link]"
        with pytest.raises(ValueError):
            Cell(
                package=malicious,
                target="x86_64-unknown-linux-gnu",
                python_minor="3.12",
                extra_surface=(),
            )

    def test_package_identity_rejects_noncanonical_name(self) -> None:
        malicious = "[link=https://evil.example]demo[/link]"
        with pytest.raises(ValueError):
            PackageIdentity(name=malicious, pyproject_path="pyproject.toml")

    def test_snapshot_entry_rejects_absolute_symlink_target(self) -> None:
        with pytest.raises(ValueError):
            SnapshotEntry(
                path="link",
                kind="symlink",
                mode=0o777,
                link_target="/tmp/secret.py",
            )


class TestReportIdentity:
    def test_cell_id_is_stable_across_active_declaration_changes(self) -> None:
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

    def test_source_snapshot_digest_uses_entries_in_path_order(self) -> None:
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
            b"pf:source-snapshot:v1\0"
            + json.dumps(
                {
                    "entries": [entry.model_dump(mode="json") for entry in canonical],
                    "pyproject_identities": [],
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

        assert source_snapshot_digest(entries, ()) == expected
        assert source_snapshot_digest(tuple(reversed(entries)), ()) == expected

    def test_resolution_graph_id_hashes_canonical_graph(self) -> None:
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

    def test_resolution_graph_id_rejects_noncanonical_graph(self) -> None:
        graph = (
            ResolvedNode(name="demo", version="1.0", dependencies=("idna",)),
            ResolvedNode(name="idna", version="3.10", dependencies=()),
        )

        with pytest.raises(ValueError):
            resolution_graph_id(tuple(reversed(graph)))


class TestPackageReportBuilder:
    def test_build_round_trips_minimal_incomplete_schema_1(
        self,
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
            source_routes=(),
        )
        entries: tuple[SnapshotEntry, ...] = ()
        snapshot = SourceSnapshotIdentity(
            digest=source_snapshot_digest(entries, ()),
            entries=entries,
            pyproject_identities=(),
        )
        report = PackageReportBuilder().build(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
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
        assert document["schema_version"] == 1
        expected_identity = {
            "generator": report.generator.model_dump(mode="json"),
            "package": report.package.model_dump(mode="json"),
            "source_snapshot": report.source_snapshot.model_dump(mode="json"),
            "policy_identity": report.policy_identity,
            "verifier_outcome_policy": report.verifier_outcome_policy,
            "source_plan": SourcePlan.for_package(package, "SEARCH").model_dump(
                mode="json"
            ),
            "requirement_declarations": [],
            "target_cells": [cell.model_dump(mode="json")],
        }
        assert (
            report.report_generation_id
            == hashlib.sha256(
                b"pf:report-generation:v1\0"
                + canonical_identity_json(expected_identity)
            ).hexdigest()
        )
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

    def test_build_interns_cell_failure_and_resolves_its_context(
        self,
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
            source_routes=(),
        )
        snapshot = SourceSnapshotIdentity(
            digest=source_snapshot_digest((), ()),
            entries=(),
            pyproject_identities=(),
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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
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
                "authority": {
                    "kind": "structured",
                    "detail": {
                        "code": "deadline",
                        "message": "cell deadline expired",
                    },
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

        assert loaded.failure(failure.failure_id) == failure
        context = loaded.failure_context(failure.failure_id)
        assert context is not None
        assert context.cell == cell
        assert context.proposal_id is None
        assert context.boundary_role is None

    def test_build_interns_prepare_attempt_without_proposal(
        self,
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
            source_routes=(),
        )
        snapshot = SourceSnapshotIdentity(
            digest=source_snapshot_digest((), ()),
            entries=(),
            pyproject_identities=(),
        )
        policy = evaluation_policy_identity(package.config)
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.digest,
                cell=cell,
                requested_resolution="highest",
                requested_managed_vector=None,
                active_declaration_ids=(),
                source_plan_identity=SourcePlan.for_package(
                    package, "SEARCH"
                ).identity,
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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
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
                "source_plan_identity": SourcePlan.for_package(
                    package, "SEARCH"
                ).identity,
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

    def test_build_interns_shared_evidence_for_complete_cell(
        self,
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
            source_routes=(),
        )
        snapshot = SourceSnapshotIdentity(
            digest=source_snapshot_digest((), ()),
            entries=(),
            pyproject_identities=(),
        )
        policy = evaluation_policy_identity(package.config)
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.digest,
                cell=cell,
                requested_resolution="highest",
                requested_managed_vector=None,
                active_declaration_ids=(),
                source_plan_identity=SourcePlan.for_package(
                    package, "SEARCH"
                ).identity,
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
            verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
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


class _CompleteReportCase:
    case: ClassVar[SimpleNamespace]
    _cached_case: ClassVar[SimpleNamespace | None] = None

    _DUPLICATE_TABLES = (
        ("evidence", "attempts"),
        ("inputs", "requirement_declarations"),
        ("inputs", "target_cells"),
        ("inputs", "candidate_snapshots"),
        ("evidence", "resolution_graphs"),
        ("evidence", "proposals"),
        ("evidence", "static_evaluations"),
        ("evidence", "evaluations"),
        ("evidence", "failures"),
    )

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def _complete_report_case(
        cls,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        if _CompleteReportCase._cached_case is None:
            _CompleteReportCase._cached_case = cls._complete_report_evidence(
                tmp_path_factory.mktemp("complete-report-evidence")
            )
        cls.case = _CompleteReportCase._cached_case

    @staticmethod
    def _complete_report_evidence(
        tmp_path: Path,
    ) -> SimpleNamespace:
        dependency = "demo-dep"
        declaration = RequirementDeclaration(
            declaration_id="demo-declaration",
            package="demo",
            location="base",
            name=dependency,
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
        source = SourceIdentity(kind="registry", index="https://pypi.org/simple")
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(),
            declarations=(declaration, inactive_fixed_declaration),
            cells=(cell,),
            source_routes=tuple(
                DependencySourceRoute(
                    dependency=name,
                    development_source=source,
                    search_source=source,
                )
                for name in (dependency, "fixed-dep")
            ),
            dependency_search_policies=(
                NamedSearchPolicy(
                    name=dependency,
                    space="all",
                    step="minor",
                    prereleases=False,
                ),
            ),
        )
        snapshot = SourceSnapshotIdentity(
            digest=source_snapshot_digest((), ()),
            entries=(),
            pyproject_identities=(),
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
        plan_identity = SourcePlan.for_package(package, "SEARCH").identity
        candidate_snapshot = CandidateSnapshot(
            dependency=dependency,
            cell=cell,
            policy_identity=candidate_policy,
            source_plan_identity=plan_identity,
            source=source,
            candidates=candidates,
            series_representatives=(("1.0", "1.0"),),
            digest=candidate_snapshot_digest(
                dependency=dependency,
                cell=cell,
                policy_identity=candidate_policy,
                source_plan_identity=plan_identity,
                source=source,
                candidates=candidates,
                series_representatives=(("1.0", "1.0"),),
            ),
        )
        process = ProcessResult(
            exit_code=0,
            signal=None,
            duration_seconds=0.1,
            stdout="excluded ty output",
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
                    source_snapshot_digest=snapshot.digest,
                    cell=cell,
                    requested_resolution=resolution,
                    requested_managed_vector=(
                        vector if resolution == "exact-vector" else None
                    ),
                    active_declaration_ids=cell.active_declaration_ids,
                    source_plan_identity=plan_identity,
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
                        selected_candidate_evidence_digest(
                            (
                                SelectedCandidate(
                                    dependency=dependency,
                                    version=version,
                                    artifact=(
                                        artifact
                                        if version == "1.0"
                                        else artifact.model_copy(
                                            update={
                                                "filename": (
                                                    f"demo_dep-{version}"
                                                    "-py3-none-any.whl"
                                                ),
                                                "locator": (
                                                    "https://files.example/"
                                                    f"demo_dep-{version}"
                                                    "-py3-none-any.whl"
                                                ),
                                            }
                                        )
                                    ),
                                ),
                            )
                        )
                        if resolution == "exact-vector"
                        else None
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
                    verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
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
        search = CoordinateSuccess(
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
        )
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
            search=CoordinateSuccess.model_validate(search.model_dump()),
            final_vector=final_vector,
            final_evaluation=final,
        )
        report = PackageReportBuilder().build(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot,
            cell_results=(result,),
        )
        path = tmp_path / "package-floor.json"

        ReportStore().write(path, report)

        document = json.loads(path.read_text(encoding="utf-8"))
        loaded = ReportStore().read(path)

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
            source_plan_identity=plan_identity,
            source=source,
            candidates=expanded_candidates,
            series_representatives=expanded_representatives,
            digest=candidate_snapshot_digest(
                dependency=dependency,
                cell=cell,
                policy_identity=candidate_policy,
                source_plan_identity=plan_identity,
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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot,
            cell_results=(rejected_result,),
        )
        rejected_path = tmp_path / "rejected.json"

        ReportStore().write(rejected_path, rejected_report)

        rejected_document = json.loads(rejected_path.read_text(encoding="utf-8"))
        rejected_loaded = ReportStore().read(rejected_path)

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
        test_evaluation = VerifierRejectedEvaluation(
            proposal=rejected_proposal,
            static=regression,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        test_failure = FailureRecord.from_verifier(
            scope=AttemptFailureScope(attempt=rejected_attempt),
            disposition="REJECTED",
            cause="VERIFIER_EXITED_NONZERO",
            stage="test",
            terminal=NormalExit(exit_code=1),
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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot,
            cell_results=(test_failed_result,),
        )
        test_failed_path = tmp_path / "test-failed.json"

        ReportStore().write(test_failed_path, test_failed_report)

        test_failed_document = json.loads(test_failed_path.read_text(encoding="utf-8"))
        test_failed_loaded = ReportStore().read(test_failed_path)

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
                                    rejected_attempt.identity.requested_managed_vector
                                    or ()
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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot,
            cell_results=(missing_result,),
        )
        missing_path = tmp_path / "runtime-missing.json"

        ReportStore().write(missing_path, missing_report)

        missing_document = json.loads(missing_path.read_text(encoding="utf-8"))
        missing_loaded = ReportStore().read(missing_path)

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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot,
            cell_results=(indeterminate_result,),
        )
        indeterminate_path = tmp_path / "indeterminate.json"

        ReportStore().write(indeterminate_path, indeterminate_report)

        indeterminate_document = json.loads(
            indeterminate_path.read_text(encoding="utf-8")
        )
        indeterminate_loaded = ReportStore().read(indeterminate_path)

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
            source_plan_identity=plan_identity,
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
                source_plan_identity=plan_identity,
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
            verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
        )
        regional_rejection = VerifierRejectedEvaluation(
            proposal=rejected_proposal,
            static=regression,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        baseline_test_failure = FailureRecord.from_verifier(
            scope=AttemptFailureScope(attempt=baseline_attempt),
            disposition="REJECTED",
            cause="VERIFIER_EXITED_NONZERO",
            stage="test",
            terminal=NormalExit(exit_code=1),
        )
        baseline_test_evaluation = VerifierRejectedEvaluation(
            proposal=baseline_proposal,
            static=baseline.static,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        baseline_rejection_report = PackageReportBuilder().build(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
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
        baseline_rejection_loaded = ReportStore().read(baseline_rejection_path)
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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot,
            cell_results=(regional_result,),
        )
        regional_path = tmp_path / "static-region.json"

        ReportStore().write(regional_path, regional_report)

        regional_document = json.loads(regional_path.read_text(encoding="utf-8"))
        regional_loaded = ReportStore().read(regional_path)

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
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot,
            cell_results=(search_failure_result,),
        )
        search_failure_path = tmp_path / "search-failed.json"

        ReportStore().write(search_failure_path, search_failure_report)

        search_failure_document = json.loads(
            search_failure_path.read_text(encoding="utf-8")
        )
        search_failure_loaded = ReportStore().read(search_failure_path)

        return SimpleNamespace(
            baseline_proposal=baseline_proposal,
            baseline_rejection_document=baseline_rejection_document,
            baseline_rejection_loaded=baseline_rejection_loaded,
            baseline_rejection_report=baseline_rejection_report,
            candidate_policy=candidate_policy,
            candidate_snapshot=candidate_snapshot,
            candidates=candidates,
            cell=cell,
            cheap_attempt=cheap_attempt,
            cheap_proposal=cheap_proposal,
            declaration=declaration,
            dependency=dependency,
            document=document,
            failure=failure,
            final_attempt=final_attempt,
            final_proposal=final_proposal,
            inactive_fixed_declaration=inactive_fixed_declaration,
            indeterminate_document=indeterminate_document,
            indeterminate_failure=indeterminate_failure,
            indeterminate_loaded=indeterminate_loaded,
            indeterminate_report=indeterminate_report,
            loaded=loaded,
            missing_document=missing_document,
            missing_failure=missing_failure,
            missing_loaded=missing_loaded,
            missing_report=missing_report,
            package=package,
            region=region,
            region_snapshot=region_snapshot,
            regional_document=regional_document,
            regional_loaded=regional_loaded,
            regional_report=regional_report,
            rejected_attempt=rejected_attempt,
            rejected_document=rejected_document,
            rejected_loaded=rejected_loaded,
            rejected_proposal=rejected_proposal,
            rejected_report=rejected_report,
            report=report,
            result=result,
            search_failure_document=search_failure_document,
            search_failure_loaded=search_failure_loaded,
            search_failure_report=search_failure_report,
            source=source,
            test_failed_document=test_failed_document,
            test_failed_loaded=test_failed_loaded,
            test_failed_report=test_failed_report,
            test_failure=test_failure,
        )


class TestCompleteReportEvidence(_CompleteReportCase):
    def test_build_round_trips_direct_pass_observation(self) -> None:
        case = self.case

        observation = case.document["cell_results"][0]["search"]["observations"][0]
        assert observation == {
            "dependency": case.dependency,
            "candidate_version": "1.0",
            "evidence": {
                "kind": "DIRECT",
                "attempt_ref": case.final_attempt.attempt_id,
                "status": "PASS",
            },
        }
        assert "vector" not in observation
        assert case.document["cell_results"][0]["final_proposal_ref"] == (
            case.final_proposal.proposal_id
        )
        assert case.document["inputs"]["candidate_snapshots"] == [
            {
                "candidate_snapshot_id": case.candidate_snapshot.digest,
                "dependency": case.dependency,
                "cell_ref": cell_id(case.cell),
                "policy_identity": case.candidate_policy,
                "source_plan_identity": case.candidate_snapshot.source_plan_identity,
                "source": case.source.model_dump(mode="json", exclude_none=True),
                "candidates": [
                    case.candidates[0].model_dump(mode="json", exclude_none=True)
                ],
                "series_representatives": [["1.0", "1.0"]],
            }
        ]
        assert case.document["projections"] == [
            {
                "declaration_ref": case.declaration.declaration_id,
                "floors": [{"cell_ref": cell_id(case.cell), "version": "1.0"}],
                "projected_requirements": ["demo-dep>=1.0"],
                "representable": True,
            }
        ]
        assert case.loaded.cell_results[0].model_dump(mode="json") == (
            case.result.model_dump(mode="json")
        )

    def test_build_round_trips_prepare_rejection_without_proposal(
        self,
    ) -> None:
        case = self.case

        rejection = case.rejected_document["cell_results"][0]["search"]["observations"][
            0
        ]
        context = case.rejected_loaded.failure_context(case.failure.failure_id)
        portable = case.rejected_loaded.failure(case.failure.failure_id)

        assert rejection["evidence"] == {
            "kind": "DIRECT",
            "attempt_ref": case.rejected_attempt.attempt_id,
            "status": "REJECTED",
            "failure_ref": case.failure.failure_id,
        }
        assert (
            case.rejected_document["cell_results"][0]["search"]["boundaries"][0][
                "predecessor_failure_ref"
            ]
            == case.failure.failure_id
        )
        assert case.rejected_document["cell_results"][0]["failure_refs"] == [
            case.failure.failure_id
        ]
        assert case.rejected_loaded == case.rejected_report
        assert context is not None
        assert context.proposal_id is None
        assert context.boundary_role == "predecessor"
        assert portable is not None
        assert portable.process is not None
        assert portable.process.stderr == ""

    def test_build_round_trips_test_failure_evaluation(
        self,
    ) -> None:
        case = self.case

        terminal = next(
            item
            for item in case.test_failed_document["evidence"]["evaluations"]
            if item["proposal_ref"] == case.rejected_proposal.proposal_id
        )
        static = next(
            item
            for item in case.test_failed_document["evidence"]["static_evaluations"]
            if item["proposal_ref"] == case.rejected_proposal.proposal_id
        )
        context = case.test_failed_loaded.failure_context(case.test_failure.failure_id)

        assert terminal["status"] == "VERIFIER_REJECTED"
        assert terminal["failure_ref"] == case.test_failure.failure_id
        assert "test" not in terminal
        assert static["status"] == "STATIC_REGRESSION"
        assert static["classifications"][0]["reason_code"] == (
            "not-runtime-witnessable"
        )
        journal = VerificationJournal(
            run_id="same-authority",
            command="search",
            source_snapshot_digest=case.test_failure.scope.attempt.identity.source_snapshot_digest,
            package_policies=(
                VerificationPackagePolicy(
                    package=case.cell.package,
                    evaluation_policy_identity=(
                        case.test_failure.scope.attempt.identity.evaluation_policy_identity
                    ),
                ),
            ),
            entries=(
                VerificationJournalEntry(
                    package=case.cell.package,
                    cell=case.cell,
                    role="probe",
                    attempt=case.test_failure.scope.attempt,
                    failure=case.test_failure,
                ),
            ),
        )
        journal_authority = journal.model_dump(mode="json")["entries"][0]["failure"][
            "authority"
        ]
        report_failure = next(
            item
            for item in case.test_failed_document["evidence"]["failures"]
            if item["failure_id"] == case.test_failure.failure_id
        )
        assert journal_authority == report_failure["authority"]
        assert case.test_failed_loaded == case.test_failed_report
        assert context is not None
        assert context.proposal_id == case.rejected_proposal.proposal_id
        assert context.boundary_role == "predecessor"

    def test_build_round_trips_runtime_interface_missing_evaluation(
        self,
    ) -> None:
        case = self.case

        terminal = next(
            item
            for item in case.missing_document["evidence"]["evaluations"]
            if item["proposal_ref"] == case.rejected_proposal.proposal_id
        )

        assert terminal["status"] == "RUNTIME_INTERFACE_MISSING"
        assert terminal["failure_ref"] == case.missing_failure.failure_id
        assert terminal["witnesses"][-1]["outcome"] == {
            "status": "CONFIRMED_MISSING",
            "failure_ref": case.missing_failure.failure_id,
        }
        assert case.missing_loaded == case.missing_report

    def test_build_round_trips_indeterminate_evaluation(
        self,
    ) -> None:
        case = self.case

        terminal = next(
            item
            for item in case.indeterminate_document["evidence"]["evaluations"]
            if item["proposal_ref"] == case.rejected_proposal.proposal_id
        )

        assert terminal["status"] == "INDETERMINATE"
        assert terminal["failure_ref"] == case.indeterminate_failure.failure_id
        assert (
            case.indeterminate_document["cell_results"][0]["coordinate_failure"][
                "failure_ref"
            ]
            == case.indeterminate_failure.failure_id
        )
        assert case.indeterminate_loaded == case.indeterminate_report

    def test_build_round_trips_baseline_rejection(self) -> None:
        case = self.case

        assert (
            case.baseline_rejection_document["cell_results"][0]["proposal_ref"]
            == case.baseline_proposal.proposal_id
        )
        assert case.baseline_rejection_loaded == case.baseline_rejection_report

    def test_build_round_trips_static_region_evidence(self) -> None:
        case = self.case

        search = case.regional_document["cell_results"][0]["search"]
        wire_region = search["regions"][0]
        static_only = search["observations"][0]["evidence"]

        assert wire_region["region_id"] == static_region_id(case.region)
        assert wire_region["candidate_snapshot_ref"] == case.region_snapshot.digest
        assert wire_region["runtime_references"] == [
            {"proposal_ref": reference}
            for reference in sorted(
                (
                    case.rejected_proposal.proposal_id,
                    case.final_proposal.proposal_id,
                )
            )
        ]
        assert static_only == {
            "kind": "STATIC_ONLY",
            "attempt_ref": case.cheap_attempt.attempt_id,
            "guidance": "REJECTED",
            "region_ref": static_region_id(case.region),
            "representative_proposal_ref": case.rejected_proposal.proposal_id,
        }
        assert case.regional_loaded == case.regional_report

    def test_build_round_trips_non_monotonic_search_failure(
        self,
    ) -> None:
        case = self.case

        cell_result = case.search_failure_document["cell_results"][0]

        assert cell_result["status"] == "SEARCH_FAILED"
        assert cell_result["coordinate_failure"]["counterexample"] == ["0.8", "0.9"]
        assert case.search_failure_loaded == case.search_failure_report


class TestValidatedReport(_CompleteReportCase):
    def test_cell_result_returns_none_for_unknown_evidence(self) -> None:
        assert self.case.regional_loaded.cell_result("cell-" + "f" * 64) is None

    def test_failure_returns_none_for_unknown_evidence(self) -> None:
        assert self.case.regional_loaded.failure("failure-ffffffffffffffff") is None

    def test_failure_context_returns_none_for_unknown_evidence(self) -> None:
        assert (
            self.case.regional_loaded.failure_context("failure-ffffffffffffffff")
            is None
        )


class TestCompleteReportStore(_CompleteReportCase):
    @pytest.mark.parametrize(
        "field_path",
        (
            ("identity", "source_snapshot", "pyproject_identities"),
            ("inputs", "source_plan"),
        ),
        ids=("pyproject-identity", "source-plan"),
    )
    def test_read_rejects_a_legacy_report_missing_apply_identity(
        self,
        tmp_path: Path,
        field_path: tuple[str, ...],
    ) -> None:
        document = copy.deepcopy(self.case.document)
        owner: dict[str, Any] = document
        for field in field_path[:-1]:
            owner = owner[field]
        del owner[field_path[-1]]
        path = tmp_path / "legacy-report.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(ConfigurationError, match="invalid v1 report structure"):
            ReportStore().read(path)

    def test_merge_requires_at_least_one_report(self) -> None:
        with pytest.raises(ConfigurationError, match="at least one"):
            ReportStore().merge(())

    def test_merge_rejects_conflicting_results_for_one_cell(self) -> None:
        case = self.case

        with pytest.raises(ConfigurationError, match="conflicting result"):
            ReportStore().merge((case.report, case.rejected_report))

    def test_merge_preserves_a_report_without_cell_results(self) -> None:
        case = self.case
        report = PackageReportBuilder().build(
            package=case.package,
            source_plan=SourcePlan.for_package(case.package, "SEARCH"),
            source_snapshot=case.report.source_snapshot,
            cell_results=(),
        )

        assert ReportStore().merge((report,)) is report

    def test_update_preserves_existing_when_replacement_has_no_cells(self) -> None:
        case = self.case
        replacement = PackageReportBuilder().build(
            package=case.package,
            source_plan=SourcePlan.for_package(case.package, "SEARCH"),
            source_snapshot=case.report.source_snapshot,
            cell_results=(),
        )

        assert ReportStore().update(case.report, replacement) is case.report

    def test_merge_rejects_a_different_generation(self) -> None:
        report = self.case.report

        with pytest.raises(ConfigurationError, match="generation identity"):
            ReportStore().merge(
                (report, replace(report, report_generation_id="other-generation"))
            )

    def test_merge_rejects_generation_metadata_drift(self) -> None:
        report = self.case.report
        changed = replace(
            report,
            package=report.package.model_copy(update={"pyproject_path": "other.toml"}),
        )

        with pytest.raises(ConfigurationError, match="package identity"):
            ReportStore().merge((report, changed))

    def test_read_rejects_source_snapshot_identity_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["identity"]["source_snapshot"]["digest"] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_unknown_cell_declaration(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["inputs"]["target_cells"][0]["active_declaration_refs"] = [
            "missing-declaration"
        ]

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_cell_identity_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["inputs"]["target_cells"][0]["cell_id"] = "cell-" + "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_candidate_snapshot_for_an_unknown_cell(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["inputs"]["candidate_snapshots"][0]["cell_ref"] = "cell-" + "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_noncanonical_resolution_graph(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        graph = document["evidence"]["resolution_graphs"][0]
        graph["nodes"].append(copy.deepcopy(graph["nodes"][0]))

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_resolution_graph_identity_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["resolution_graphs"][0]["resolution_graph_id"] = (
            "resolution-" + "f" * 64
        )
        document["evidence"]["resolution_graphs"].sort(
            key=lambda item: item["resolution_graph_id"]
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_attempt_for_an_unknown_cell(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["attempts"][0]["cell_ref"] = "cell-" + "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_attempt_identity_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["attempts"][0]["resolution_context_digest"] = ""

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_proposal_for_an_unknown_attempt(
        self, tmp_path: Path
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["proposals"][0]["attempt_ref"] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_proposal_for_an_unknown_graph(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["proposals"][0]["resolution_graph_ref"] = (
            "resolution-" + "f" * 64
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_invalid_interpreter_version(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["proposals"][0]["interpreter"]["version"] = "invalid"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_unknown_fixed_declaration(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["proposals"][0]["fixed_declaration_refs"] = [
            "missing-declaration"
        ]

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_proposal_vector_drift(self, tmp_path: Path) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        proposal = next(
            item
            for item in document["evidence"]["proposals"]
            if item["proposal_id"] == case.cheap_proposal.proposal_id
        )
        proposal["managed_vector"][0]["version"] = "9.9"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_static_evidence_for_an_unknown_proposal(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["static_evaluations"][0]["proposal_ref"] = "f" * 64
        document["evidence"]["static_evaluations"].sort(
            key=lambda item: item["proposal_ref"]
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_static_evaluation_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["static_evaluations"][0]["static_fingerprint"] = "tampered"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_evaluation_for_an_unknown_proposal(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["evaluations"][0]["proposal_ref"] = "f" * 64
        document["evidence"]["evaluations"].sort(key=lambda item: item["proposal_ref"])

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_cross_proposal_static_reference(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        evaluations = document["evidence"]["evaluations"]
        evaluations[0]["static_evaluation_ref"] = evaluations[1]["proposal_ref"]

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_failure_for_an_unknown_attempt(
        self, tmp_path: Path
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["failures"][0]["scope"]["attempt_ref"] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_failure_identity_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["failures"][0]["failure_id"] = "failure-ffffffffffffffff"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_failure_without_authority(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.test_failed_document)
        document["evidence"]["failures"][0].pop("authority")

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_failure_with_mixed_authority(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.test_failed_document)
        authority = document["evidence"]["failures"][0]["authority"]
        authority["process"] = {
            "exit_code": 1,
            "signal": None,
            "duration_seconds": 0.1,
            "stdout_complete": True,
            "stderr_complete": True,
            "timed_out": False,
            "start_error": None,
        }

        self._assert_read_rejects(tmp_path, document)

    @pytest.mark.parametrize(
        ("field", "value"),
        (("cause", "TIMEOUT"), ("stage", "resolve-project")),
    )
    def test_read_rejects_verifier_authority_mismatch(
        self,
        tmp_path: Path,
        field: str,
        value: str,
    ) -> None:
        document = copy.deepcopy(self.case.test_failed_document)
        document["evidence"]["failures"][0][field] = value

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_test_failure_without_its_record(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        failed = next(
            item
            for item in document["evidence"]["evaluations"]
            if item["status"] == "VERIFIER_REJECTED"
        )
        failed["failure_ref"] = "failure-ffffffffffffffff"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_indeterminate_evaluation_without_its_record(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.indeterminate_document)
        document["evidence"]["evaluations"][0]["failure_ref"] = (
            "failure-ffffffffffffffff"
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_direct_evidence_for_an_unknown_attempt(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        observation = next(
            item
            for item in document["cell_results"][0]["search"]["observations"]
            if item["evidence"].get("status") == "PASS"
        )
        observation["evidence"]["attempt_ref"] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_direct_pass_without_a_pass_evaluation(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        observation = next(
            item
            for item in document["cell_results"][0]["search"]["observations"]
            if item["evidence"].get("status") == "PASS"
        )
        observation["evidence"]["attempt_ref"] = case.rejected_attempt.attempt_id

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_direct_rejection_without_its_failure(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        observation = next(
            item
            for item in document["cell_results"][0]["search"]["observations"]
            if item["evidence"].get("status") == "REJECTED"
        )
        observation["evidence"]["failure_ref"] = "failure-ffffffffffffffff"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_direct_rejection_with_a_pass_evaluation(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        observation = next(
            item
            for item in document["cell_results"][0]["search"]["observations"]
            if item["evidence"].get("status") == "REJECTED"
        )
        observation["evidence"]["attempt_ref"] = case.final_attempt.attempt_id

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_direct_indeterminate_without_its_failure(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.indeterminate_document)
        observation = document["cell_results"][0]["coordinate_failure"]["observations"][
            0
        ]
        observation["evidence"]["failure_ref"] = "failure-ffffffffffffffff"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_direct_indeterminate_with_a_pass_evaluation(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.indeterminate_document)
        observation = document["cell_results"][0]["coordinate_failure"]["observations"][
            0
        ]
        observation["evidence"]["attempt_ref"] = case.final_attempt.attempt_id

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_duplicate_region_runtime_references(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        references = document["cell_results"][0]["search"]["regions"][0][
            "runtime_references"
        ]
        references.append(copy.deepcopy(references[0]))

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_static_region_identity_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["cell_results"][0]["search"]["regions"][0]["region_id"] = (
            "region-" + "f" * 64
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_duplicate_cell_failure_references(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        references = document["cell_results"][0]["failure_refs"]
        references.append(references[0])

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_unknown_cell_failure_reference(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["cell_results"][0]["failure_refs"][0] = "failure-ffffffffffffffff"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_unknown_cell_candidate_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["cell_results"][0]["candidate_snapshot_refs"][0] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_duplicate_cell_candidate_snapshots(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        references = document["cell_results"][0]["candidate_snapshot_refs"]
        references.append(references[0])

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_incomplete_search_failure_baseline(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.search_failure_document)
        document["cell_results"][0]["baseline"]["attempt_ref"] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_unknown_search_failure_candidate_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.search_failure_document)
        document["cell_results"][0]["candidate_snapshot_refs"][0] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_baseline_result_without_one_failure(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.baseline_rejection_document)
        document["cell_results"][0]["failure_refs"] = []

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_baseline_result_for_an_unknown_attempt(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.baseline_rejection_document)
        document["cell_results"][0]["attempt_ref"] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_baseline_result_with_unknown_evaluation_refs(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.baseline_rejection_document)
        document["cell_results"][0]["proposal_ref"] = "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_unknown_projection_declaration(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["projections"][0]["declaration_ref"] = "missing-declaration"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_an_unknown_projection_cell(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["projections"][0]["floors"][0]["cell_ref"] = "cell-" + "f" * 64

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_incomplete_projection_coverage(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["projections"] = []

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_result_that_contradicts_cell_coverage(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["result"] = {
            "status": "incomplete",
            "reasons": ["MISSING_CELL"],
        }

        self._assert_read_rejects(tmp_path, document)

    def test_update_path_reports_removed_failures_for_same_generation(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        path = tmp_path / "updated.json"
        store = ReportStore()

        created = store.update_path(path, case.rejected_report)
        updated = store.update_path(path, case.report)

        assert created.replace_generation is True
        assert created.removed_failure_ids == ()
        assert updated.replace_generation is False
        assert updated.removed_failure_ids == (case.failure.failure_id,)
        assert store.read(path) == case.report

    def test_update_path_replaces_a_different_generation(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        entries = (
            SnapshotEntry(
                path="README.md",
                kind="file",
                mode=0o644,
                content_digest="f" * 64,
            ),
        )
        replacement = PackageReportBuilder().build(
            package=case.package,
            source_plan=SourcePlan.for_package(case.package, "SEARCH"),
            source_snapshot=SourceSnapshotIdentity(
                digest=source_snapshot_digest(entries, ()),
                entries=entries,
                pyproject_identities=(),
            ),
            cell_results=(),
        )
        path = tmp_path / "updated.json"
        store = ReportStore()
        store.update_path(path, case.report)

        result = store.update_path(path, replacement)

        assert result.replace_generation is True
        assert result.removed_failure_ids == ()
        assert store.read(path) == replacement

    @pytest.mark.parametrize(
        ("section", "table"),
        _CompleteReportCase._DUPLICATE_TABLES,
        ids=(
            "attempts",
            "declarations",
            "cells",
            "candidate-snapshots",
            "resolution-graphs",
            "proposals",
            "static-evaluations",
            "evaluations",
            "failures",
        ),
    )
    def test_read_rejects_duplicate_evidence_records(
        self,
        tmp_path: Path,
        section: str,
        table: str,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document[section][table].append(copy.deepcopy(document[section][table][0]))

        self._assert_read_rejects(tmp_path, document)

    @pytest.mark.parametrize(
        "table",
        ("cell_results", "projections"),
    )
    def test_read_rejects_duplicate_result_records(
        self,
        tmp_path: Path,
        table: str,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document[table].append(copy.deepcopy(document[table][0]))

        self._assert_read_rejects(tmp_path, document)

    @pytest.mark.parametrize(
        "attempt_ref",
        ("missing-attempt", "proposal-id"),
        ids=("missing", "proposal-instead-of-attempt"),
    )
    def test_read_rejects_invalid_static_only_attempt_reference(
        self,
        tmp_path: Path,
        attempt_ref: str,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        evidence = document["cell_results"][0]["search"]["observations"][0]["evidence"]
        evidence["attempt_ref"] = (
            case.cheap_proposal.proposal_id
            if attempt_ref == "proposal-id"
            else attempt_ref
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_unreachable_resolution_graph(self, tmp_path: Path) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        graph = (ResolvedNode(name="orphan", version="1.0"),)
        document["evidence"]["resolution_graphs"].append(
            {
                "resolution_graph_id": resolution_graph_id(graph),
                "nodes": [graph[0].model_dump(mode="json")],
            }
        )
        document["evidence"]["resolution_graphs"].sort(
            key=lambda item: item["resolution_graph_id"]
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_tampered_proposal_plan_identity(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["evidence"]["proposals"][0]["project_plan_digest"] = "tampered"

        self._assert_read_rejects(tmp_path, document)

    @pytest.mark.parametrize(
        ("field", "value"),
        (
            ("version", "3.11.9"),
            ("abi", "tampered-abi"),
            ("abi", "cpython-312evil"),
        ),
        ids=("python-version", "abi", "noncanonical-abi"),
    )
    def test_read_rejects_mismatched_proposal_interpreter(
        self,
        tmp_path: Path,
        field: str,
        value: str,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["evidence"]["proposals"][0]["interpreter"][field] = value

        self._assert_read_rejects(tmp_path, document)

    @pytest.mark.parametrize(
        "digest_field",
        ("project_plan_digest", "environment_plan_digest"),
    )
    def test_read_rejects_missing_proposal_plan_identity(
        self,
        tmp_path: Path,
        digest_field: str,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        proposal = next(
            item
            for item in document["evidence"]["proposals"]
            if item["proposal_id"] == case.cheap_proposal.proposal_id
        )
        proposal[digest_field] = ""
        proposal["proposal_id"] = environment_identity_digest(
            project_plan_digest=proposal["project_plan_digest"],
            environment_plan_digest=proposal["environment_plan_digest"],
            graph=case.cheap_proposal.resolved_graph,
        )
        document["evidence"]["proposals"].sort(key=lambda item: item["proposal_id"])

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_duplicate_fixed_declaration_references(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        reference = case.inactive_fixed_declaration.declaration_id
        document["evidence"]["proposals"][0]["fixed_declaration_refs"] = [
            reference,
            reference,
        ]

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_inactive_fixed_declaration_reference(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["evidence"]["proposals"][0]["fixed_declaration_refs"] = [
            case.inactive_fixed_declaration.declaration_id
        ]

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_searchable_fixed_declaration_reference(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["evidence"]["proposals"][0]["fixed_declaration_refs"] = [
            case.declaration.declaration_id
        ]

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_tampered_candidate_snapshot_identity(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["inputs"]["candidate_snapshots"][0]["candidates"][0]["version"] = (
            "tampered"
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_candidate_artifact_drift_from_exact_attempt(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        snapshot = document["inputs"]["candidate_snapshots"][0]
        old_snapshot_id = snapshot["candidate_snapshot_id"]
        snapshot["candidates"][-1]["artifact"]["content_hash"] = (
            f"sha256:{'b' * 64}"
        )
        new_snapshot_id = candidate_snapshot_digest(
            dependency=snapshot["dependency"],
            cell=case.cell,
            policy_identity=snapshot["policy_identity"],
            source_plan_identity=snapshot["source_plan_identity"],
            source=SourceIdentity.model_validate(snapshot["source"]),
            candidates=tuple(
                Candidate.model_validate(item) for item in snapshot["candidates"]
            ),
            series_representatives=tuple(
                tuple(item) for item in snapshot["series_representatives"]
            ),
        )
        document = json.loads(
            json.dumps(document).replace(old_snapshot_id, new_snapshot_id)
        )
        path = tmp_path / "candidate-artifact-drift.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(
            ConfigurationError,
            match="Attempt selected candidate mismatch",
        ):
            ReportStore().read(path)

    def test_read_rejects_noncanonical_candidate_snapshot_order(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        snapshot = document["inputs"]["candidate_snapshots"][0]
        snapshot["candidates"].reverse()
        snapshot["series_representatives"].reverse()
        snapshot["candidate_snapshot_id"] = candidate_snapshot_digest(
            dependency=snapshot["dependency"],
            cell=case.cell,
            policy_identity=snapshot["policy_identity"],
            source_plan_identity=snapshot["source_plan_identity"],
            source=SourceIdentity.model_validate(snapshot["source"]),
            candidates=tuple(
                Candidate.model_validate(item) for item in snapshot["candidates"]
            ),
            series_representatives=tuple(
                tuple(item) for item in snapshot["series_representatives"]
            ),
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_report_generation_identity_drift(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["identity"]["report_generation_id"] = "tampered-generation"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_unowned_region_candidate_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["cell_results"][0]["search"]["regions"][0][
            "candidate_snapshot_ref"
        ] = "missing-candidate"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_nonlocal_region_runtime_reference(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["cell_results"][0]["search"]["regions"][0]["runtime_references"][0][
            "proposal_ref"
        ] = case.cheap_proposal.proposal_id

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_missing_predecessor_failure_reference(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["cell_results"][0]["search"]["boundaries"][0][
            "predecessor_failure_ref"
        ] = "missing-failure"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_wrong_final_proposal_reference(
        self,
        tmp_path: Path,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["cell_results"][0]["final_proposal_ref"] = (
            case.rejected_proposal.proposal_id
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_projection_floor_drift(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["projections"][0]["floors"][0]["version"] = "9.9"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_extra_wire_field(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["identity"]["unexpected"] = True

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_boolean_search_sweeps(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["cell_results"][0]["search"]["sweeps"] = True

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_boolean_process_duration(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["evidence"]["static_evaluations"][0]["ty"]["process"][
            "duration_seconds"
        ] = False

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_null_optional_wire_field(self, tmp_path: Path) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["cell_results"][0]["search"]["boundaries"][0]["predecessor"] = None

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_unsorted_proposals(self, tmp_path: Path) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["evidence"]["proposals"].reverse()

        self._assert_read_rejects(tmp_path, document)

    @pytest.mark.parametrize(
        "locator",
        (
            "https://alice:secret@example.com/pkg.whl?token=abc",
            "/tmp/secret.whl",
            "c:/secret.whl",
            "c:\\secret.whl",
            "https://example.com:invalid/secret.whl",
        ),
    )
    def test_read_rejects_non_public_candidate_locator(
        self,
        tmp_path: Path,
        locator: str,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["inputs"]["candidate_snapshots"][0]["candidates"][0]["artifact"][
            "locator"
        ] = locator

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_non_public_persisted_input(self, tmp_path: Path) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["identity"]["package"]["pyproject_path"] = "/tmp/secret/pyproject.toml"

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_non_public_source_route_locator(self, tmp_path: Path) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        document["inputs"]["source_plan"]["routes"][0]["search_source"]["locator"] = (
            "https://alice:secret@example.com/pkg.whl?token=abc"
        )

        self._assert_read_rejects(tmp_path, document)

    def test_read_rejects_a_non_search_report_source_plan(
        self,
        tmp_path: Path,
    ) -> None:
        document = copy.deepcopy(self.case.regional_document)
        document["inputs"]["source_plan"]["source_mode"] = "DEVELOPMENT"
        path = tmp_path / "development-report.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(
            ConfigurationError,
            match="SourcePlan must use SEARCH mode",
        ):
            ReportStore().read(path)

    @pytest.mark.parametrize(
        "surface",
        ("structure", "proposal-id", "cell-id", "cell-result"),
    )
    def test_read_error_does_not_leak_untrusted_values(
        self,
        tmp_path: Path,
        surface: str,
    ) -> None:
        case = self.case
        document = copy.deepcopy(case.regional_document)
        secret = "SECRET-PROCESS-OUTPUT\x1b[31m"
        if surface == "structure":
            document["identity"]["package"]["name"] = {"secret": secret}
        elif surface == "proposal-id":
            document["evidence"]["proposals"][0]["proposal_id"] = secret
            document["evidence"]["proposals"].sort(key=lambda item: item["proposal_id"])
        elif surface == "cell-id":
            document["inputs"]["target_cells"][0]["cell_id"] = secret
            document["inputs"]["target_cells"][0]["active_declaration_refs"] = [
                "missing-declaration"
            ]
        else:
            document["cell_results"][0]["cell_ref"] = secret
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(ConfigurationError) as caught:
            ReportStore().read(path)

        assert secret not in str(caught.value)
        assert "\x1b" not in str(caught.value)

    @staticmethod
    def _assert_read_rejects(tmp_path: Path, document: dict[str, Any]) -> None:
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(ConfigurationError):
            ReportStore().read(path)

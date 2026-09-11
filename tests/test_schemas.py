from __future__ import annotations


from pf.search_space import SpaceSelection

from typing import Any, Literal

import pytest

from conftest import empty_harness_baseline
from pydantic import ValidationError
from verifier_fixtures import verifier_pass, verifier_rejected

from pf.failure import FailurePolicy
from pf.schemas.base import FrozenSchema
from pf.schemas.config import (
    CheckRequest,
    EffectiveConfig,
    MergeRequest,
    SearchRequest,
    SmokeRequest,
    WorkspacePackage,
)
from pf.schemas.evaluation import (
    ExecutionFailure,
    Unattributed,
    ActivityEvent,
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineIndeterminate,
    BaselineRejection,
    CellCompletedEvent,
    CellFailed,
    CellFailureScope,
    CellMatrixEvent,
    CellSearchProgressEvent,
    CellStageEvent,
    CellSucceeded,
    FailureDetail,
    FailureRecord,
    HighestVersionPass,
    IndeterminateEvaluation,
    PassEvaluation,
    ProcessResult,
    ProcessSpec,
    PrepareFailure,
    PytestFailureCase,
    PytestFailureDetail,
    SearchFailureEvent,
    SmokeBaselineRejection,
    SmokeCellPass,
    SmokeIndeterminate,
    TyCheck,
    TyDiagnostic,
    NormalExit,
    VerifierPass,
    VerifierIndeterminate,
    Unavailable,
    VerifierRejected,
    VerifierRejectedEvaluation,
    process_facts_match,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    DependencySourceRoute,
    StaticWorkspaceMemberVersion,
    HarnessGroupProvenance,
    Proposal,
    RequirementDeclaration,
    SelectedCandidate,
    SourceIdentity,
    VersionPin,
    candidate_snapshot_digest,
    cell_identity,
    public_locator,
    selected_candidate_evidence_digest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
    CoordinateBoundary,
    CoordinateFailure,
    CoordinateSuccess,
    ProbeObservation,
    ProbeIndeterminate,
    ProbePass,
    ProbeRejection,
)


class ExampleRecord(FrozenSchema):
    name: str


def _successful_process(*, exit_code: int = 0) -> ProcessResult:
    return ProcessResult(
        exit_code=exit_code,
        signal=None,
        duration_seconds=0,
        stdout="",
        stderr="",
    )


def _verifier_failure(attempt: Attempt, *, exit_code: int = 1) -> FailureRecord:
    return FailureRecord.from_verifier(
        scope=AttemptFailureScope(attempt=attempt),
        disposition="REJECTED",
        cause="VERIFIER_EXITED_NONZERO",
        stage="test",
        terminal=NormalExit(exit_code=exit_code),
    )


def _attempt(
    *,
    resolution: Literal["highest", "exact-vector"] = "highest",
    vector: tuple[VersionPin, ...] | None = None,
    cell: Cell | None = None,
    selected_digest: str | None = None,
) -> Attempt:
    attempt_cell = cell or Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    if resolution == "exact-vector" and selected_digest is None:
        selected_digest = selected_candidate_evidence_digest(
            tuple(
                SelectedCandidate(
                    dependency=pin.name,
                    version=pin.version,
                    artifact=AvailableArtifact(
                        filename=f"{pin.name}-{pin.version}-py3-none-any.whl",
                        kind="wheel",
                        content_hash=f"sha256:{'a' * 64}",
                        locator=(
                            f"https://files.example/"
                            f"{pin.name}-{pin.version}-py3-none-any.whl"
                        ),
                    ),
                )
                for pin in (vector or ())
            )
        )
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=attempt_cell,
            requested_resolution=resolution,
            requested_managed_vector=vector,
            active_declaration_ids=attempt_cell.active_declaration_ids,
            source_plan_identity="sources",
            execution_policy_identity="policy",
            resolution_context_digest="context",
            harness_policy_identity=(
                "original-harness-v1"
                if resolution == "highest"
                else "harness-relaxation-v1"
            ),
            harness_baseline_digest=(
                None if resolution == "highest" else "baseline"
            ),
            selected_candidate_evidence_digest=(
                selected_digest if resolution == "exact-vector" else None
            ),
        )
    )


def _proposal(
    proposal_id: str,
    *,
    attempt: Attempt | None = None,
    vector: tuple[VersionPin, ...] = (),
) -> Proposal:
    owned_attempt = attempt or _attempt()
    return Proposal(
        proposal_id=proposal_id,
        attempt_id=owned_attempt.attempt_id,
        snapshot_digest="snapshot",
        cell=owned_attempt.identity.cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
    )


def _diagnostic(*, line: int = 1, code: str = "invalid-type") -> TyDiagnostic:
    return TyDiagnostic(
        identity=f"snapshot|demo.py|{line}|2|{code}",
        origin="snapshot",
        path="demo.py",
        line=line,
        column=2,
        code=code,
        severity="major",
        message="message",
    )


def _baseline_evidence(
    *,
    version: str = "1",
    cell: Cell | None = None,
) -> tuple[Attempt, PassEvaluation]:
    attempt = _attempt(cell=cell)
    proposal = _proposal(
        "baseline",
        attempt=attempt,
        vector=(VersionPin(name="demo", version=version),),
    )
    passed = PassEvaluation(
        proposal=proposal,

        verifier=verifier_pass(_successful_process()),
    )
    return attempt, passed


def _indeterminate_evaluation(attempt: Attempt) -> IndeterminateEvaluation:
    proposal = _proposal("candidate", attempt=attempt)
    return IndeterminateEvaluation(
        proposal=proposal,
        cause="TOOL_FAILURE",
        verifier=VerifierIndeterminate(terminal=Unavailable(), reason="terminal-unavailable"),

    )


def _cell_success() -> CellSuccess:
    baseline_attempt, passed = _baseline_evidence(version="2")
    vector = (VersionPin(name="demo", version="1"),)
    artifact = AvailableArtifact(
        filename="demo-1-py3-none-any.whl",
        kind="wheel",
        content_hash=f"sha256:{'a' * 64}",
        locator="https://files.example/demo-1-py3-none-any.whl",
    )
    baseline_selection = SelectedCandidate(
        dependency="demo",
        version="2",
        artifact=artifact.model_copy(
            update={
                "filename": "demo-2-py3-none-any.whl",
                "content_hash": f"sha256:{'b' * 64}",
                "locator": "https://files.example/demo-2-py3-none-any.whl",
            }
        ),
    )
    attempt = _attempt(
        resolution="exact-vector",
        vector=vector,
        selected_digest=selected_candidate_evidence_digest(
            (
                SelectedCandidate(
                    dependency="demo",
                    version="1",
                    artifact=artifact,
                ),
            )
        ),
    )
    proposal = _proposal("floor", attempt=attempt, vector=vector)
    evaluation = PassEvaluation(
        proposal=proposal,

        verifier=verifier_pass(_successful_process()),
    )
    search = CoordinateSuccess(
        vector=vector,
        observations=(
            ProbeObservation(
                dependency="demo",
                candidate_version="1",
                vector=vector,
                evidence=ProbePass(
                    attempt=attempt,
                    proposal_id=proposal.proposal_id,
                    evaluation=evaluation,
                ),
                selection_reason="mechanical-lowest",
            ),
            ProbeObservation(
                dependency=None,
                candidate_version=None,
                vector=passed.proposal.managed_vector,
                evidence=ProbePass(
                    attempt=baseline_attempt,
                    proposal_id=passed.proposal.proposal_id,
                    evaluation=passed,
                ),
                selection_reason=None,
            ),
        ),
        boundaries=(CoordinateBoundary(dependency="demo", floor="1"),),
        sweeps=1,
    )
    source = SourceIdentity(kind="registry")
    candidates = (
        Candidate(
            version="1",
            series_key="1",
            artifact=artifact,
        ),
    )
    representatives = (("1", "1"),)
    candidate_snapshot = CandidateSnapshot(
            selection=SpaceSelection("all", "explicit", ()), series_inventory=None,
        dependency="demo",
        cell=passed.proposal.cell,
        policy_identity="candidate-policy",
        source_plan_identity="sources",
        source=source,
        baseline_selection=baseline_selection,
        candidates=candidates,
        series_representatives=representatives,
        digest=candidate_snapshot_digest(
            selection=SpaceSelection("all", "explicit", ()), series_inventory=None,
            dependency="demo",
            cell=passed.proposal.cell,
            policy_identity="candidate-policy",
            source_plan_identity="sources",
            source=source,
            baseline_selection=baseline_selection,
            candidates=candidates,
            series_representatives=representatives,
        ),
    )
    return CellSuccess(

        cell=passed.proposal.cell,
        baseline_attempt=baseline_attempt,

        baseline=passed,
        candidate_snapshots=(candidate_snapshot,),
        search=search,
        final_vector=vector,
        final_evaluation=evaluation,
    )


def _model_values(model: FrozenSchema) -> dict[str, Any]:
    return {field: getattr(model, field) for field in type(model).model_fields}


class TestPlanningSchemas:
    @pytest.mark.parametrize(
        ("locator", "expected"),
        (
            (
                "https://user:secret@example.test:8443/demo.whl?token=secret#fragment",
                "https://example.test:8443/demo.whl",
            ),
            ("file:///tmp/demo.whl?token=secret", "file:///tmp/demo.whl"),
        ),
        ids=("network-port", "absolute-file"),
    )
    def test_public_locator_removes_private_url_parts(
        self,
        locator: str,
        expected: str,
    ) -> None:
        assert public_locator(locator) == expected

    def test_requirement_declaration_rejects_noncanonical_names(self) -> None:
        with pytest.raises(ValidationError, match="canonical distribution"):
            RequirementDeclaration(
                declaration_id="declaration",
                package="Demo",
                location="base",
                name="idna",
                pyproject_path="pyproject.toml",
                raw="idna",
                kind="searchable",
                managed=True,
            )

    def test_harness_provenance_requires_a_group_path(self) -> None:
        with pytest.raises(ValidationError, match="requires a pyproject and group"):
            HarnessGroupProvenance(
                owner="root",
                pyproject_path="pyproject.toml",
                group_path=(),
                item_path=(),
            )

    def test_harness_provenance_requires_matching_path_depth(self) -> None:
        with pytest.raises(ValidationError, match="equal depth"):
            HarnessGroupProvenance(
                owner="root",
                pyproject_path="pyproject.toml",
                group_path=("test",),
                item_path=(0, 1),
            )

    def test_cell_identity_is_the_compatibility_quadruple(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=("cuda",),
            active_declaration_ids=("decl",),
        )
        assert cell_identity(cell) == (
            "demo",
            "x86_64-unknown-linux-gnu",
            "3.10",
            ("cuda",),
        )
        assert "decl" not in cell_identity(cell)

    def test_selected_candidate_requires_a_complete_sha256_digest(self) -> None:
        with pytest.raises(ValidationError, match="complete SHA-256"):
            SelectedCandidate(
                dependency="idna",
                version="3.1",
                artifact=AvailableArtifact(
                    filename="idna-3.1-py3-none-any.whl",
                    kind="wheel",
                    content_hash="sha256:abc123",
                    locator="https://files.example/idna-3.1-py3-none-any.whl",
                ),
            )

    def test_cross_module_records_are_strict_and_immutable(self) -> None:
        with pytest.raises(ValidationError):
            ExampleRecord.model_validate({"name": "pf", "unexpected": True})

        record = ExampleRecord(name="pf")
        with pytest.raises(ValidationError):
            setattr(record, "name", "changed")

    @pytest.mark.parametrize(
        "config",
        (
            {"resolution": {"timeout_seconds": 0}},
            {"ty": {"timeout_seconds": True}},
            {"test": {"timeout_seconds": 0}},
            {"scheduling": {"max_cells": True}},
            {"scheduling": {"ty_jobs": 0}},
        ),
    )
    def test_effective_config_rejects_ambiguous_runtime_policy(
        self,
        config: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            EffectiveConfig.model_validate(config)

    @pytest.mark.parametrize(
        "request_type,payload",
        (
            (SearchRequest, {"root": ".", "max_cells": True}),
            (SearchRequest, {"root": ".", "ty_jobs": 0}),
            (SearchRequest, {"root": ".", "test_jobs": False}),
            (SearchRequest, {"root": ".", "max_duration_seconds": 0}),
            (CheckRequest, {"root": ".", "max_cells": True}),
            (CheckRequest, {"root": ".", "test_jobs": False}),
            (SmokeRequest, {"root": ".", "max_cells": True}),
            (SmokeRequest, {"root": ".", "max_cells": 0}),
            (SmokeRequest, {"root": ".", "test_jobs": 0}),
        ),
        ids=(
            "search-max-cells",
            "search-ty-jobs",
            "search-test-jobs",
            "search-max-duration",
            "check-max-cells",
            "check-test-jobs",
            "smoke-max-cells-bool",
            "smoke-max-cells-zero",
            "smoke-test-jobs",
        ),
    )
    def test_command_request_rejects_invalid_scheduling(
        self,
        request_type: type[SearchRequest] | type[CheckRequest] | type[SmokeRequest],
        payload: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            request_type.model_validate(payload)

    def test_merge_request_requires_an_input_report(self) -> None:
        with pytest.raises(ValidationError):
            MergeRequest(reports=(), output="merged.json")

    @pytest.mark.parametrize("name", ("", "/tmp/demo", "Demo_Package"))
    def test_workspace_package_selector_requires_a_canonical_distribution_name(
        self,
        name: str,
    ) -> None:
        with pytest.raises(ValidationError):
            WorkspacePackage(canonical_name=name)

    def test_workspace_route_cannot_switch_between_local_members(self) -> None:
        with pytest.raises(ValidationError, match="one source identity"):
            DependencySourceRoute(
                dependency="demo-core",
                development_source=SourceIdentity(
                    kind="workspace",
                    locator="packages/core",
                ),
                search_source=SourceIdentity(
                    kind="workspace",
                    locator="packages/other",
                ),
                workspace_member_version=StaticWorkspaceMemberVersion(value="1.0"),
            )

    @pytest.mark.parametrize(
        "cell",
        (
            {
                "package": "demo",
                "target": "linux",
                "python_minor": "3.10",
                "extra_surface": (),
            },
            {
                "package": "demo",
                "target": "x86_64-unknown-linux-gnu",
                "python_minor": "3.10",
                "extra_surface": ("gpu", "gpu"),
            },
            {
                "package": "demo",
                "target": "x86_64-unknown-linux-gnu",
                "python_minor": "3.10",
                "extra_surface": (),
                "active_declaration_ids": ("b", "a"),
            },
        ),
    )
    def test_cell_requires_exact_normalized_coordinates(
        self,
        cell: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            Cell.model_validate(cell)

    def test_candidate_snapshot_requires_unique_nonempty_versions(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        artifact = AvailableArtifact(
            filename="demo.whl",
            kind="wheel",
            content_hash=f"sha256:{'a' * 64}",
            locator="https://files.example/demo.whl",
        )
        base = {
            "dependency": "demo",
            "cell": cell,
            "policy_identity": "policy",
            "source_plan_identity": "sources",
            "source": SourceIdentity(kind="registry"),
            "baseline_selection": SelectedCandidate(
                dependency="demo",
                version="1.0",
                artifact=artifact,
            ),
            "series_representatives": (),
            "digest": "digest",
        }

        with pytest.raises(ValidationError):
            CandidateSnapshot(
            selection=SpaceSelection("all", "explicit", ()), series_inventory=None,candidates=(), **base)
        duplicate = Candidate(version="1.0", series_key="1", artifact=artifact)
        with pytest.raises(ValidationError):
            CandidateSnapshot(
            selection=SpaceSelection("all", "explicit", ()), series_inventory=None,candidates=(duplicate, duplicate), **base)

    def test_candidate_snapshot_rejects_a_tampered_artifact(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        source = SourceIdentity(kind="registry")
        candidates = (
            Candidate(
                version="1.0",
                series_key="1",
                artifact=AvailableArtifact(
                    filename="demo.whl",
                    kind="wheel",
                    content_hash=f"sha256:{'a' * 64}",
                    locator="https://files.example/demo.whl",
                ),
            ),
        )
        representatives = (("1", "1.0"),)
        baseline_selection = SelectedCandidate(
            dependency="demo",
            version="1.0",
            artifact=candidates[0].artifact,
        )
        snapshot = CandidateSnapshot(
            selection=SpaceSelection("all", "explicit", ()), series_inventory=None,
            dependency="demo",
            cell=cell,
            policy_identity="policy",
            source_plan_identity="sources",
            source=source,
            baseline_selection=baseline_selection,
            candidates=candidates,
            series_representatives=representatives,
            digest=candidate_snapshot_digest(
            selection=SpaceSelection("all", "explicit", ()), series_inventory=None,
                dependency="demo",
                cell=cell,
                policy_identity="policy",
                source_plan_identity="sources",
                source=source,
                baseline_selection=baseline_selection,
                candidates=candidates,
                series_representatives=representatives,
            ),
        )
        dumped = snapshot.model_dump(mode="python")
        dumped["candidates"][0]["artifact"]["filename"] = "tampered.whl"

        with pytest.raises(ValidationError, match="artifact|digest"):
            CandidateSnapshot.model_validate(dumped)

    def test_structured_probe_rejections_require_their_evaluation(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        baseline_attempt, passed = _baseline_evidence()
        failure = _verifier_failure(attempt)
        with pytest.raises(ValidationError, match="structured evaluation"):
            CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=passed.proposal.cell,
                phase="runtime-search",
                baseline_attempt=baseline_attempt,

                baseline=passed,
                failure_records=(failure,),
                coordinate_failure=CoordinateFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    observations=(
                        ProbeObservation(
                            dependency=None,
                            candidate_version=None,
                            vector=(),
                            evidence=ProbeRejection(
                                attempt=attempt,
                                proposal_id="candidate",
                                failure_id=failure.failure_id,
                                cause="VERIFIER_EXITED_NONZERO",
                            ),
                            selection_reason=None,
                        ),
                    ),
                ),
            )


class TestSearchSchemas:
    def test_highest_version_and_smoke_results_enforce_their_evidence(self) -> None:
        attempt, passed = _baseline_evidence()

        assert (
            HighestVersionPass(
                attempt=attempt,

                harness_baseline=empty_harness_baseline(attempt.identity.cell),
                evaluation=passed,
            ).evaluation
            is passed
        )

        with pytest.raises(ValidationError, match="requires rejected evidence"):
            SmokeBaselineRejection(
                outcomes=(
                    SmokeCellPass(
                        attempt=attempt,
                        evaluation=passed,
                    ),
                )
            )

    def test_probe_rejection_requires_its_failure_record(self) -> None:
        success = _cell_success()
        baseline_attempt = success.baseline_attempt
        baseline = success.baseline
        passed = success.baseline
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        with pytest.raises(ValidationError, match="FailureRecord"):
            CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=baseline.proposal.cell,
                phase="runtime-search",
                baseline_attempt=baseline_attempt,

                baseline=passed,
                candidate_snapshots=success.candidate_snapshots,
                coordinate_failure=CoordinateFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    observations=(
                        ProbeObservation(
                            dependency="demo",
                            candidate_version="1",
                            vector=vector,
                            evidence=ProbeRejection(
                                attempt=attempt,
                                failure_id="failure-missing",
                                cause="RESOLUTION_CONFLICT",
                            ),
                            selection_reason="mechanical-lowest",
                        ),
                    ),
                ),
            )


    @pytest.mark.parametrize("evidence_kind", ("pass", "rejection", "indeterminate"))
    @pytest.mark.parametrize("mismatch", ("proposal", "attempt"))
    def test_probe_evidence_must_match_its_proposal_and_attempt(
        self,
        evidence_kind: str,
        mismatch: str,
    ) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        other_attempt = _attempt(
            resolution="exact-vector",
            vector=(VersionPin(name="demo", version="2"),),
        )
        indeterminate = _indeterminate_evaluation(attempt)
        proposal_id = (
            "other" if mismatch == "proposal" else indeterminate.proposal.proposal_id
        )
        evidence_attempt = other_attempt if mismatch == "attempt" else attempt

        if evidence_kind == "pass":
            evaluation = PassEvaluation(
                proposal=indeterminate.proposal,

                verifier=verifier_pass(_successful_process()),
            )
            constructor = ProbePass
            payload: dict[str, object] = {
                "attempt": evidence_attempt,
                "proposal_id": proposal_id,
                "evaluation": evaluation,
            }
        elif evidence_kind == "rejection":
            evaluation = VerifierRejectedEvaluation(
                proposal=indeterminate.proposal,

                verifier=verifier_rejected(_successful_process(exit_code=1)),
            )
            constructor = ProbeRejection
            payload = {
                "attempt": evidence_attempt,
                "proposal_id": proposal_id,
                "failure_id": "failure",
                "cause": "VERIFIER_EXITED_NONZERO",
                "evaluation": evaluation,
            }
        else:
            constructor = ProbeIndeterminate
            payload = {
                "attempt": evidence_attempt,
                "proposal_id": proposal_id,
                "failure_id": "failure",
                "cause": "TOOL_FAILURE",
                "evaluation": indeterminate,
            }

        with pytest.raises(ValidationError):
            constructor.model_validate(payload)

    def test_probe_observation_requires_the_attempt_requested_vector(self) -> None:
        requested = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=requested)
        proposal = _proposal("candidate", attempt=attempt, vector=requested)

        with pytest.raises(ValidationError, match="exact attempt"):
            ProbeObservation(
                dependency="demo",
                candidate_version="2",
                vector=(VersionPin(name="demo", version="2"),),
                evidence=ProbePass(
                    attempt=attempt,
                    proposal_id="candidate",
                    evaluation=PassEvaluation(
                        proposal=proposal,

                        verifier=verifier_pass(_successful_process()),
                    ),
                ),
                selection_reason="mechanical-lowest",
            )

    def test_probe_pass_accepts_exact_vector_or_the_real_highest_attempt(
        self,
    ) -> None:
        baseline_attempt, passed = _baseline_evidence()

        highest = ProbePass(
            attempt=baseline_attempt,
            proposal_id=passed.proposal.proposal_id,
            evaluation=passed,
        )
        assert highest.evaluation is passed
        exact_attempt = _attempt(resolution="exact-vector", vector=())
        with pytest.raises(ValidationError, match="evaluation"):
            ProbePass.model_validate(
                {
                    "attempt": exact_attempt,
                    "proposal_id": "candidate",
                }
            )

    def test_probe_rejection_rejects_a_highest_attempt(self) -> None:
        baseline_attempt, passed = _baseline_evidence()
        rejected = VerifierRejectedEvaluation(
            proposal=passed.proposal,

            verifier=verifier_rejected(_successful_process(exit_code=1)),
        )

        with pytest.raises(ValidationError, match="exact-vector Attempt"):
            ProbeRejection(
                attempt=baseline_attempt,
                proposal_id=passed.proposal.proposal_id,
                failure_id="failure",
                cause="VERIFIER_EXITED_NONZERO",
                evaluation=rejected,
            )

    def test_probe_evaluation_must_match_the_attempt_requested_vector(self) -> None:
        requested = (VersionPin(name="demo", version="1"),)
        actual = (VersionPin(name="demo", version="2"),)
        attempt = _attempt(resolution="exact-vector", vector=requested)
        proposal = _proposal("candidate", attempt=attempt, vector=actual)

        with pytest.raises(ValidationError, match="requested exact vector"):
            ProbePass(
                attempt=attempt,
                proposal_id=proposal.proposal_id,
                evaluation=PassEvaluation(
                    proposal=proposal,

                    verifier=verifier_pass(_successful_process()),
                ),
            )

    def test_probe_rejection_cause_must_match_its_evaluation_kind(self) -> None:
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        proposal = _proposal("candidate", attempt=attempt, vector=vector)

        with pytest.raises(ValidationError, match="cause must match"):
            ProbeRejection(
                attempt=attempt,
                proposal_id=proposal.proposal_id,
                failure_id="failure",
                cause="RESOLUTION_CONFLICT",
                evaluation=VerifierRejectedEvaluation(
                    proposal=proposal,

                    verifier=verifier_rejected(_successful_process(exit_code=1)),
                ),
            )

    def test_probe_indeterminate_cause_must_match_its_evaluation(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        evaluation = _indeterminate_evaluation(attempt)

        with pytest.raises(ValidationError, match="cause must match"):
            ProbeIndeterminate(
                attempt=attempt,
                proposal_id=evaluation.proposal.proposal_id,
                failure_id="failure",
                cause="TIMEOUT",
                evaluation=evaluation,
            )

    def test_prepare_rejection_cannot_claim_a_proposal(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())

        with pytest.raises(ValidationError, match="cannot claim a Proposal"):
            ProbeRejection(
                attempt=attempt,
                proposal_id="invented",
                failure_id="failure",
                cause="RESOLUTION_CONFLICT",
            )

    @pytest.mark.parametrize(
        ("predecessor", "failure_id"),
        (("1", None), (None, "failure")),
    )
    def test_coordinate_boundary_requires_complete_predecessor_evidence(
        self,
        predecessor: str | None,
        failure_id: str | None,
    ) -> None:
        with pytest.raises(ValidationError, match="predecessor requires"):
            CoordinateBoundary(
                dependency="demo",
                floor="2",
                predecessor=predecessor,
                predecessor_failure_id=failure_id,
            )

    def test_indeterminate_coordinate_requires_its_failure_reference(self) -> None:
        with pytest.raises(ValidationError, match="requires a failure ID"):
            CoordinateFailure(status="INDETERMINATE", observations=())

    def test_indeterminate_coordinate_must_reference_its_observation(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())

        with pytest.raises(ValidationError, match="reference its observation"):
            CoordinateFailure(
                status="INDETERMINATE",
                failure_id="failure-terminal",
                observations=(
                    ProbeObservation(
                        dependency=None,
                        candidate_version=None,
                        vector=(),
                        evidence=ProbeIndeterminate(
                            attempt=attempt,
                            failure_id="failure-other",
                            cause="TIMEOUT",
                        ),
                        selection_reason=None,
                    ),
                ),
            )

    def test_probe_observation_candidate_must_match_its_vector(self) -> None:
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)

        with pytest.raises(ValidationError, match="candidate must match"):
            ProbeObservation(
                dependency="demo",
                candidate_version="2",
                vector=vector,
                evidence=ProbeRejection(
                    attempt=attempt,
                    failure_id="failure",
                    cause="RESOLUTION_CONFLICT",
                ),
                selection_reason="mechanical-lowest",
            )

    def test_non_monotonic_counterexample_requires_direct_same_slice_evidence(
        self,
    ) -> None:
        low_vector = (
            VersionPin(name="a", version="1"),
            VersionPin(name="b", version="1"),
        )
        high_vector = (
            VersionPin(name="a", version="2"),
            VersionPin(name="b", version="1"),
        )
        low_attempt = _attempt(resolution="exact-vector", vector=low_vector)
        low_proposal = _proposal("a=1;b=1", attempt=low_attempt, vector=low_vector)
        low = ProbeObservation(
            dependency="a",
            candidate_version="1",
            vector=low_vector,
            evidence=ProbePass(
                attempt=low_attempt,
                proposal_id=low_proposal.proposal_id,
                evaluation=PassEvaluation(
                    proposal=low_proposal,

                    verifier=verifier_pass(_successful_process()),
                ),
            ),
            selection_reason="mechanical-lowest",
        )
        high_attempt = _attempt(resolution="exact-vector", vector=high_vector)
        high = ProbeObservation(
            dependency="a",
            candidate_version="2",
            vector=high_vector,
            evidence=ProbeRejection(
                attempt=high_attempt,
                failure_id="failure-high",
                cause="RESOLUTION_CONFLICT",
            ),
            selection_reason="mechanical-lowest",
        )

        result = CoordinateFailure(
            status="NON_MONOTONIC",
            dependency="a",
            counterexample=("1", "2"),
            observations=(low, high),
        )

        assert result.counterexample == ("1", "2")
        cross_slice_vector = (
            VersionPin(name="a", version="2"),
            VersionPin(name="b", version="2"),
        )
        cross_slice_attempt = _attempt(
            resolution="exact-vector",
            vector=cross_slice_vector,
        )
        with pytest.raises(ValidationError, match="direct evidence in one Slice"):
            CoordinateFailure(
                status="NON_MONOTONIC",
                dependency="a",
                counterexample=("1", "2"),
                observations=(
                    low,
                    high.model_copy(
                        update={
                            "vector": cross_slice_vector,
                            "evidence": ProbeRejection(
                                attempt=cross_slice_attempt,
                                failure_id="failure-cross-slice",
                                cause="RESOLUTION_CONFLICT",
                            ),
                        }
                    ),
                ),
            )

    def test_coordinate_success_cannot_contain_indeterminate_evidence(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())

        with pytest.raises(ValidationError, match="cannot contain indeterminate"):
            CoordinateSuccess(
                vector=(),
                observations=(
                    ProbeObservation(
                        dependency=None,
                        candidate_version=None,
                        vector=(),
                        evidence=ProbeIndeterminate(
                            attempt=attempt,
                            failure_id="failure",
                            cause="TIMEOUT",
                        ),
                        selection_reason=None,
                    ),
                ),
                boundaries=(),
                sweeps=1,
            )

    def test_coordinate_boundary_must_reference_its_rejection_observation(self) -> None:
        rejected_vector = (VersionPin(name="demo", version="1"),)
        vector = (VersionPin(name="demo", version="2"),)
        attempt = _attempt(resolution="exact-vector", vector=rejected_vector)

        with pytest.raises(ValidationError, match="rejection observation"):
            CoordinateSuccess(
                vector=vector,
                observations=(
                    ProbeObservation(
                        dependency="demo",
                        candidate_version="1",
                        vector=rejected_vector,
                        evidence=ProbeRejection(
                            attempt=attempt,
                            failure_id="failure-observed",
                            cause="RESOLUTION_CONFLICT",
                        ),
                        selection_reason="mechanical-lowest",
                    ),
                ),
                boundaries=(
                    CoordinateBoundary(
                        dependency="demo",
                        floor="2",
                        predecessor="1",
                        predecessor_failure_id="failure-other",
                    ),
                ),
                sweeps=1,
            )

    def test_smoke_indeterminate_requires_indeterminate_evidence(self) -> None:
        attempt, passed = _baseline_evidence()

        with pytest.raises(ValidationError, match="requires indeterminate evidence"):
            SmokeIndeterminate(
                outcomes=(
                    SmokeCellPass(
                        attempt=attempt,
                        evaluation=passed,
                    ),
                )
            )

    def test_baseline_outcome_requires_a_highest_attempt(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        failure = _verifier_failure(attempt)

        with pytest.raises(ValidationError, match="highest Attempt"):
            BaselineRejection(attempt=attempt, failure=failure)

    def test_baseline_test_rejection_requires_its_structured_evaluation(self) -> None:
        attempt, baseline = _baseline_evidence()
        failure = _verifier_failure(attempt)

        with pytest.raises(ValidationError, match="requires its evaluation"):
            BaselineRejection(
                attempt=attempt,
                failure=failure,

            )

    def test_baseline_indeterminate_evaluation_must_match_its_attempt(
        self,
    ) -> None:
        attempt, baseline = _baseline_evidence()
        other_proposal = baseline.proposal.model_copy(update={"attempt_id": "other-attempt"})
        failure = FailureRecord.from_verifier(
            scope=AttemptFailureScope(attempt=attempt), disposition="INDETERMINATE",
            cause="TOOL_FAILURE", stage="test", terminal=Unavailable(),
        )

        with pytest.raises(ValidationError, match="match its attempt"):
            BaselineIndeterminate(
                attempt=attempt,
                failure=failure,

                evaluation=IndeterminateEvaluation(
                    proposal=other_proposal,
                    cause="TOOL_FAILURE",
                    verifier=VerifierIndeterminate(terminal=Unavailable(), reason="terminal-unavailable"),

                ),
            )

    def test_baseline_rejection_diagnosis_must_match_its_evaluation(self) -> None:
        attempt, passed = _baseline_evidence()
        evaluation = VerifierRejectedEvaluation(
            proposal=passed.proposal,

            verifier=verifier_rejected(_successful_process(exit_code=1)),
        )
        failure = _verifier_failure(attempt, exit_code=2)

        with pytest.raises(ValidationError, match="diagnosis must match"):
            BaselineRejection(
                attempt=attempt,
                failure=failure,

                evaluation=evaluation,
            )

    def test_cell_indeterminate_requires_its_complete_pass_baseline(self) -> None:
        baseline_attempt, baseline = _baseline_evidence()
        cell = baseline.proposal.cell
        failure = FailurePolicy().classify(
            scope=CellFailureScope(
                package=cell.package,
                cell=cell,
                source_snapshot_digest="snapshot",
                execution_policy_identity="policy",
            ),
            cause="SOURCE_FAILURE",
            stage="candidate-discovery",
            process=None,
            detail=FailureDetail(code="source", message="source unavailable"),
        )

        with pytest.raises(ValidationError, match="complete PASS baseline"):
            CellIndeterminate(
                cell=cell,
                phase="candidate-discovery",
                failure_id=failure.failure_id,
                failure_records=(failure,),
                baseline_attempt=baseline_attempt,

            )

    def test_probe_attempt_must_share_the_baseline_evaluation_context(self) -> None:
        baseline_attempt, passed = _baseline_evidence()
        vector = (VersionPin(name="demo", version="1"),)
        candidate_attempt = _attempt(resolution="exact-vector", vector=vector)
        candidate_attempt = Attempt.from_identity(
            candidate_attempt.identity.model_copy(
                update={"source_plan_identity": "another-source-plan"}
            )
        )
        failure = _verifier_failure(candidate_attempt)
        candidate_proposal = _proposal(
            "candidate",
            attempt=candidate_attempt,
            vector=vector,
        )
        candidate_failure = VerifierRejectedEvaluation(
            proposal=candidate_proposal,

            verifier=verifier_rejected(_successful_process(exit_code=1)),
        )

        with pytest.raises(ValidationError, match="evaluation context"):
            CellSearchFailure(
                reason="NO_PASS_IN_SEARCH_SPACE",
                cell=passed.proposal.cell,
                phase="runtime-search",
                baseline_attempt=baseline_attempt,

                baseline=passed,
                failure_records=(failure,),
                coordinate_failure=CoordinateFailure(
                    status="NO_PASS_IN_SEARCH_SPACE",
                    observations=(
                        ProbeObservation(
                            dependency="demo",
                            candidate_version="1",
                            vector=vector,
                            evidence=ProbeRejection(
                                attempt=candidate_attempt,
                                proposal_id=candidate_proposal.proposal_id,
                                failure_id=failure.failure_id,
                                cause=failure.cause,
                                evaluation=candidate_failure,
                            ),
                            selection_reason="mechanical-lowest",
                        ),
                    ),
                ),
            )


class TestEvaluationSchemas:
    def test_exact_attempt_v2_requires_selected_candidate_evidence(self) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )

        with pytest.raises(ValidationError, match="selected candidate evidence"):
            AttemptIdentity(
                identity_version="attempt-v1",
                source_snapshot_digest="snapshot",
                cell=cell,
                requested_resolution="exact-vector",
                requested_managed_vector=(VersionPin(name="demo", version="1"),),
                active_declaration_ids=(),
                source_plan_identity="sources",
                execution_policy_identity="policy",
                resolution_context_digest="context",
                harness_policy_identity="harness-relaxation-v1",
                harness_baseline_digest="baseline",
            )


    def test_process_facts_match_requires_matching_presence(self) -> None:
        assert process_facts_match(None, None) is True
        assert process_facts_match(_successful_process(), None) is False





    @pytest.mark.parametrize(
        "facts,diagnostic",
        [
            pytest.param(
                {"start_error": "could not start"}, "could not start", id="start-error"
            ),
            pytest.param({"exit_code": 1, "stderr": "failed"}, "failed", id="stderr"),
            pytest.param(
                {"signal": 9, "timed_out": True}, "process timed out", id="timeout"
            ),
            pytest.param({"signal": 9}, "terminated by signal 9", id="signal"),
            pytest.param({"exit_code": 2}, "exit code 2", id="exit-code"),
        ],
    )
    def test_diagnostic_describes_process_failure(
        self, facts: dict[str, object], diagnostic: str
    ) -> None:
        process = ProcessResult.model_validate({"duration_seconds": 0, **facts})

        assert process.diagnostic() == diagnostic

    @pytest.mark.parametrize(
        "facts",
        (
            {},
            {"exit_code": None, "signal": None, "start_error": None},
            {"exit_code": 0, "signal": 9, "start_error": None},
            {"exit_code": 1, "signal": 9},
            {"exit_code": 1, "start_error": "failed"},
            {"signal": 9, "start_error": "failed"},
            {"start_error": "failed", "timed_out": True},
        ),
        ids=(
            "none",
            "explicit-null",
            "zero-exit-signal",
            "exit-signal",
            "exit-start",
            "signal-start",
            "start-timeout",
        ),
    )
    def test_process_result_requires_one_valid_terminal_observation(
        self,
        facts: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            ProcessResult.model_validate({"duration_seconds": 0, **facts})

    @pytest.mark.parametrize(
        "changes",
        (
            {
                "requested_resolution": "highest",
                "requested_managed_vector": (),
            },
            {
                "requested_resolution": "exact-vector",
                "requested_managed_vector": None,
            },
            {
                "requested_resolution": "exact-vector",
                "requested_managed_vector": (
                    VersionPin(name="b", version="1"),
                    VersionPin(name="a", version="1"),
                ),
            },
        ),
        ids=("highest-vector", "missing-vector", "vector-order"),
    )
    def test_attempt_identity_rejects_an_invalid_resolution_vector(
        self,
        changes: dict[str, object],
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        values: dict[str, object] = {
            "source_snapshot_digest": "snapshot",
            "cell": cell,
            "requested_resolution": "highest",
            "requested_managed_vector": None,
            "active_declaration_ids": cell.active_declaration_ids,
            "source_plan_identity": "sources",
            "execution_policy_identity": "policy",
            "resolution_context_digest": "context",
            "harness_policy_identity": "original-harness-v1",
        }
        values.update(changes)

        with pytest.raises(ValidationError):
            AttemptIdentity.model_validate(values)

    @pytest.mark.parametrize(
        "changes",
        (
            {"resolution_context_digest": ""},
            {"harness_declaration_ids": ("b", "a")},
            {"harness_policy_identity": "harness-relaxation-v1"},
            {"harness_baseline_digest": "unexpected"},
            {"selected_candidate_evidence_digest": "unexpected"},
        ),
        ids=("context", "harness-order", "policy", "baseline", "selection"),
    )
    def test_attempt_identity_rejects_incoherent_harness_evidence(
        self,
        changes: dict[str, object],
    ) -> None:
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        values: dict[str, object] = {
            "identity_version": "attempt-v1",
            "source_snapshot_digest": "snapshot",
            "cell": cell,
            "requested_resolution": "highest",
            "requested_managed_vector": None,
            "active_declaration_ids": cell.active_declaration_ids,
            "source_plan_identity": "sources",
            "execution_policy_identity": "policy",
            "resolution_context_digest": "context",
            "harness_policy_identity": "original-harness-v1",
        }
        values.update(changes)

        with pytest.raises(ValidationError):
            AttemptIdentity.model_validate(values)

    def test_prepare_failure_requires_project_evidence_before_environment(self) -> None:
        attempt = Attempt.from_identity(_attempt().identity.model_copy(update={"harness_declaration_ids": ("harness",)}))
        with pytest.raises(ValidationError, match="project plan timing"):
            PrepareFailure(
                attempt=attempt, stage="install-environment",
                failure=ExecutionFailure(terminal=NormalExit(exit_code=1), attribution=Unattributed()),
                project_plan_digest=None, environment_plan_digest="a" * 64,
            )


    def test_process_spec_rejects_empty_argv(self) -> None:
        with pytest.raises(ValidationError):
            ProcessSpec(argv=(), cwd=".", timeout_seconds=None)

    @pytest.mark.parametrize(
        "timeout_seconds",
        (0, -0.1, float("nan"), float("inf")),
        ids=("zero", "negative", "nan", "infinite"),
    )
    def test_process_spec_rejects_invalid_timeout(
        self,
        timeout_seconds: int | float,
    ) -> None:
        with pytest.raises(ValidationError):
            ProcessSpec(
                argv=("python",),
                cwd=".",
                timeout_seconds=timeout_seconds,
            )

    def test_process_spec_rejects_invalid_summary_limit(self) -> None:
        with pytest.raises(ValidationError):
            ProcessSpec(
                argv=("python",),
                cwd=".",
                timeout_seconds=None,
                summary_limit=0,
            )

    def test_process_result_omits_captured_output_from_portable_facts(self) -> None:
        process = ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout="484 passed in 11.12s",
            stderr="tool noise",
        )

        dumped = process.model_dump(mode="json")
        restored = ProcessResult.model_validate(dumped)

        assert "stdout" not in dumped
        assert "stderr" not in dumped
        assert "stdout_tail" not in dumped
        assert "stderr_tail" not in dumped
        assert restored.exit_code == 1
        assert restored.stdout == ""
        assert restored.stderr == ""

    def test_cell_indeterminate_accepts_verifier_terminal_facts_after_search_round_trip(
        self,
    ) -> None:
        baseline_attempt, passed = _baseline_evidence()
        attempt = _attempt(resolution="exact-vector", vector=())
        proposal = _proposal("candidate", attempt=attempt)
        evaluation = IndeterminateEvaluation(
            proposal=proposal,
            cause="TOOL_FAILURE",
            verifier=VerifierIndeterminate(terminal=Unavailable(), reason="terminal-unavailable"),

        )
        failure = FailurePolicy().record_evaluation(
            AttemptFailureScope(attempt=attempt),
            evaluation,
        )
        assert failure is not None
        search = CoordinateFailure(
            status="INDETERMINATE",
            failure_id=failure.failure_id,
            observations=(
                ProbeObservation(
                    dependency=None,
                    candidate_version=None,
                    vector=(),
                    evidence=ProbeIndeterminate(
                        attempt=attempt,
                        proposal_id=proposal.proposal_id,
                        failure_id=failure.failure_id,
                        cause="TOOL_FAILURE",
                        evaluation=evaluation,
                    ),
                    selection_reason=None,
                ),
            ),
        )

        restored_search = CoordinateFailure.model_validate(
            search.model_dump(mode="json")
        )

        result = CellIndeterminate(
            cell=passed.proposal.cell,
            phase="runtime-search",
            failure_id=failure.failure_id,
            failure_records=(failure,),
            baseline_attempt=baseline_attempt,

            baseline=passed,
            coordinate_failure=restored_search,
        )

        assert result.failure_id == failure.failure_id

    @pytest.mark.parametrize(
        ("outcome", "terminal"),
        (
            (VerifierPass, NormalExit(exit_code=1)),
            (VerifierRejected, NormalExit(exit_code=0)),
        ),
    )
    def test_verifier_outcomes_require_matching_normal_terminal_facts(
        self,
        outcome: type[VerifierPass] | type[VerifierRejected],
        terminal: NormalExit,
    ) -> None:
        with pytest.raises(ValidationError, match="requires"):
            outcome(terminal=terminal)

    @pytest.mark.parametrize(
        "changes",
        (
            {"path": " "},
            {"severity": " "},
            {"line": None},
            {"column": 0},
            {
                "origin": "external",
                "identity": "external|demo.py|invalid-type",
            },
            {"identity": "unstable-identity"},
        ),
    )
    def test_ty_diagnostic_rejects_noncanonical_identity_fields(
        self,
        changes: dict[str, object],
    ) -> None:
        payload = _diagnostic().model_dump()
        payload.update(changes)

        with pytest.raises(ValidationError):
            TyDiagnostic.model_validate(payload)

    @pytest.mark.parametrize(
        "process_changes",
        (
            {"exit_code": 2},
            {"timed_out": True},
            {"stdout_complete": False},
        ),
    )
    def test_ty_check_rejects_noncomparable_process_results(
        self,
        process_changes: dict[str, object],
    ) -> None:
        process = _successful_process().model_dump()
        process.update(process_changes)

        with pytest.raises(ValidationError, match="complete output"):
            TyCheck(process=ProcessResult.model_validate(process), diagnostics=())

    def test_ty_check_requires_deterministically_sorted_diagnostics(self) -> None:
        with pytest.raises(ValidationError, match="sorted by stable identity"):
            TyCheck(
                process=_successful_process(exit_code=1),
                diagnostics=(_diagnostic(line=2), _diagnostic(line=1)),
            )



class TestReportSchemas:
    def test_cell_success_requires_unique_candidate_snapshots(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        values["candidate_snapshots"] = (
            *success.candidate_snapshots,
            *success.candidate_snapshots,
        )

        with pytest.raises(ValidationError, match="dependencies must be unique"):
            CellSuccess(**values)

    def test_cell_success_requires_a_highest_baseline_attempt(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        values["baseline_attempt"] = _attempt(
            resolution="exact-vector",
            vector=success.final_vector,
            cell=success.cell,
        )

        with pytest.raises(ValidationError, match="highest Attempt"):
            CellSuccess(**values)

    def test_cell_success_requires_the_baseline_attempt_cell(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        other = success.cell.model_copy(update={"python_minor": "3.11"})
        values["baseline_attempt"] = _attempt(cell=other)

        with pytest.raises(ValidationError, match="Attempt must match"):
            CellSuccess(**values)

    def test_cell_success_requires_the_baseline_proposal_attempt(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        values["baseline"] = success.baseline.model_copy(
            update={
                "proposal": success.baseline.proposal.model_copy(
                    update={"attempt_id": "other-attempt"}
                )
            }
        )

        with pytest.raises(ValidationError, match="reference its Attempt"):
            CellSuccess(**values)

    def test_cell_success_requires_the_baseline_proposal_cell(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        other = success.cell.model_copy(update={"python_minor": "3.11"})
        values["baseline"] = success.baseline.model_copy(
            update={
                "proposal": success.baseline.proposal.model_copy(
                    update={"cell": other}
                )
            }
        )

        with pytest.raises(ValidationError, match="baseline must match"):
            CellSuccess(**values)


    def test_cell_success_rejects_another_highest_pass_observation(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        baseline_observation = success.search.observations[1]
        other_proposal = success.baseline.proposal.model_copy(
            update={"proposal_id": "other-baseline"}
        )
        other_evaluation = success.baseline.model_copy(
            update={
                "proposal": other_proposal,
            }
        )
        values["search"] = success.search.model_copy(
            update={
                "observations": (
                    success.search.observations[0],
                    baseline_observation.model_copy(
                        update={
                            "evidence": ProbePass(
                                attempt=success.baseline_attempt,
                                proposal_id=other_proposal.proposal_id,
                                evaluation=other_evaluation,
                            )
                        }
                    ),
                )
            }
        )

        with pytest.raises(ValidationError, match="verified baseline"):
            CellSuccess(**values)

    def test_cell_success_requires_a_baseline_final_to_reuse_highest_pass(
        self,
    ) -> None:
        success = _cell_success()
        values = _model_values(success)
        baseline_proposal = success.baseline.proposal.model_copy(
            update={"managed_vector": success.final_vector}
        )
        baseline_evaluation = success.baseline.model_copy(
            update={
                "proposal": baseline_proposal,
            }
        )
        candidate_snapshot = success.candidate_snapshots[0]
        baseline_selection = SelectedCandidate(
            dependency="demo",
            version="1",
            artifact=candidate_snapshot.candidates[0].artifact,
        )
        baseline_observation = ProbeObservation(
            dependency=None,
            candidate_version=None,
            vector=success.final_vector,
            evidence=ProbePass(
                attempt=success.baseline_attempt,
                proposal_id=baseline_proposal.proposal_id,
                evaluation=baseline_evaluation,
            ),
            selection_reason=None,
        )
        values.update(
            {
                "baseline": baseline_evaluation,
                "candidate_snapshots": (
                    candidate_snapshot.model_copy(
                        update={
                            "baseline_selection": baseline_selection,
                            "digest": candidate_snapshot_digest(
                                dependency=candidate_snapshot.dependency,
                                cell=candidate_snapshot.cell,
                                policy_identity=candidate_snapshot.policy_identity,
                                source_plan_identity=(
                                    candidate_snapshot.source_plan_identity
                                ),
                                source=candidate_snapshot.source,
                                baseline_selection=baseline_selection,
                                candidates=candidate_snapshot.candidates,
                                series_representatives=(
                                    candidate_snapshot.series_representatives
                                ),
                                selection=candidate_snapshot.selection,
                                series_inventory=(
                                    candidate_snapshot.series_inventory
                                ),
                            ),
                        }
                    ),
                ),
                "search": success.search.model_copy(
                    update={
                        "observations": (
                            success.search.observations[0],
                            baseline_observation,
                        )
                    }
                ),
            }
        )

        with pytest.raises(ValidationError, match="reuse its highest PASS"):
            CellSuccess(**values)



    def test_cell_success_requires_unique_final_dependencies(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        values["final_vector"] = (*success.final_vector, *success.final_vector)

        with pytest.raises(ValidationError, match="unique and sorted"):
            CellSuccess(**values)

    def test_cell_success_requires_the_final_proposal_vector(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        values["final_vector"] = ()
        values["search"] = success.search.model_copy(
            update={"vector": (), "boundaries": ()}
        )

        with pytest.raises(ValidationError, match="PASS Proposal"):
            CellSuccess(**values)

    def test_cell_success_requires_the_final_probe_observation(self) -> None:
        success = _cell_success()
        values = _model_values(success)
        observation = success.search.observations[0]
        rejected = VerifierRejectedEvaluation(
            proposal=success.final_evaluation.proposal,

            verifier=verifier_rejected(_successful_process(exit_code=1)),
        )
        values["search"] = success.search.model_copy(
            update={
                "observations": (
                    observation.model_copy(
                        update={
                            "evidence": ProbeRejection(
                                attempt=observation.evidence.attempt,
                                proposal_id=success.final_evaluation.proposal.proposal_id,
                                failure_id="missing-failure",
                                cause="VERIFIER_EXITED_NONZERO",
                                evaluation=rejected,
                            )
                        }
                    ),
                )
            }
        )

        with pytest.raises(ValidationError, match="final ProbePass"):
            CellSuccess(**values)


    @pytest.mark.parametrize(
        "values",
        (
            {
                "status": "NON_MONOTONIC",
                "dependency": None,
                "observations": (),
            },
            {
                "status": "NON_MONOTONIC",
                "dependency": "demo",
                "counterexample": ("2", "1"),
                "observations": (),
            },
            {
                "status": "NO_PASS_IN_SEARCH_SPACE",
                "counterexample": ("1", "2"),
                "observations": (),
            },
            {
                "status": "NONDETERMINISTIC",
                "failure_id": "failure",
                "observations": (),
            },
        ),
        ids=(
            "counterexample",
            "order",
            "unexpected-counterexample",
            "unexpected-failure",
        ),
    )
    def test_coordinate_failure_rejects_incoherent_terminal_evidence(
        self,
        values: dict[str, object],
    ) -> None:
        with pytest.raises(ValidationError):
            CoordinateFailure.model_validate(values)

    @pytest.mark.parametrize("evidence_type", (ProbeRejection, ProbeIndeterminate))
    def test_probe_evidence_requires_an_exact_vector_attempt(
        self,
        evidence_type: type[ProbeRejection] | type[ProbeIndeterminate],
    ) -> None:
        with pytest.raises(ValidationError):
            evidence_type(
                attempt=_attempt(),
                failure_id="failure",
                cause="RESOLUTION_CONFLICT",
            )

    def test_probe_observation_pairs_the_active_coordinate(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        evidence = ProbeRejection(
            attempt=attempt,
            failure_id="failure",
            cause="RESOLUTION_CONFLICT",
        )

        with pytest.raises(ValidationError, match="must be paired"):
            ProbeObservation(
                dependency="demo",
                candidate_version=None,
                vector=(),
                evidence=evidence,
                selection_reason="mechanical-lowest",
            )

    def test_probe_observation_requires_selection_reason(self) -> None:
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        proposal = _proposal("candidate", attempt=attempt, vector=vector)
        observation = ProbeObservation(
            dependency="demo",
            candidate_version="1",
            vector=vector,
            evidence=ProbePass(
                attempt=attempt,
                proposal_id=proposal.proposal_id,
                evaluation=PassEvaluation(
                    proposal=proposal,
                    verifier=verifier_pass(_successful_process()),
                ),
            ),
            selection_reason="mechanical-lowest",
        )
        payload = observation.model_dump(mode="json")
        payload.pop("selection_reason")
        with pytest.raises(ValidationError, match="selection_reason"):
            ProbeObservation.model_validate(payload)

    def test_search_failure_event_retains_structured_failure_and_evaluation(
        self,
    ) -> None:
        vector = (VersionPin(name="demo", version="1"),)
        attempt = _attempt(resolution="exact-vector", vector=vector)
        proposal = _proposal("candidate", attempt=attempt, vector=vector)
        evaluation = VerifierRejectedEvaluation(
            proposal=proposal,

            verifier=verifier_rejected(_successful_process(exit_code=1)),
        )
        failure = _verifier_failure(attempt)
        event = SearchFailureEvent(
            cell=proposal.cell,
            failure=failure,
            evaluation=evaluation,
        )

        assert isinstance(event, ActivityEvent)
        assert event.failure == failure
        wrong_failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause="HARNESS_CONFLICT",
            stage="install-harness",
            process=_successful_process(exit_code=1),
        )
        with pytest.raises(ValidationError, match="test evaluation"):
            SearchFailureEvent(
                cell=proposal.cell,
                failure=wrong_failure,
                evaluation=evaluation,
            )

    def test_search_failure_event_matches_indeterminate_failure_facts(self) -> None:
        attempt = _attempt(resolution="exact-vector", vector=())
        evaluation = _indeterminate_evaluation(attempt)
        failure = FailurePolicy().record_evaluation(
            AttemptFailureScope(attempt=attempt),
            evaluation,
        )
        assert failure is not None

        event = SearchFailureEvent(
            cell=evaluation.proposal.cell,
            failure=failure,
            evaluation=evaluation,
        )

        assert event.evaluation == evaluation
        mismatched = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=attempt),
            cause=evaluation.cause,
            stage="planning",
            process=_successful_process(exit_code=2),
        )
        with pytest.raises(ValidationError, match="verifier indeterminate"):
            SearchFailureEvent(
                cell=evaluation.proposal.cell,
                failure=mismatched,
                evaluation=evaluation,
            )

    def test_cell_activity_events_reject_progress_sentinel_fields(self) -> None:
        cell = _attempt().identity.cell
        stage = CellStageEvent(cell=cell, stage="install")
        completed = CellCompletedEvent(
            cell=cell,
            completed=1,
            total=1,
            outcome=CellSucceeded(status="PASS", phase="complete"),
        )

        assert isinstance(stage, ActivityEvent)
        assert isinstance(completed, ActivityEvent)
        assert "completed" not in stage.model_dump()
        assert "phase" not in completed.model_dump()
        with pytest.raises(ValidationError) as caught:
            CellStageEvent.model_validate(
                {"cell": cell, "stage": "install", "completed": 0}
            )
        assert any(
            error["type"] == "extra_forbidden" and error["loc"] == ("completed",)
            for error in caught.value.errors(include_url=False)
        )
        with pytest.raises(ValidationError, match="completion counters"):
            CellCompletedEvent(
                cell=cell,
                completed=0,
                total=1,
                outcome=CellSucceeded(status="PASS", phase="complete"),
            )

    def test_cell_matrix_event_defaults_and_validates_package_counts(self) -> None:
        matrix = CellMatrixEvent(cells=())

        assert matrix.active_packages == 0
        assert matrix.pinned_packages == 0
        with pytest.raises(ValidationError, match="0 <= pinned <= active"):
            CellMatrixEvent(cells=(), active_packages=1, pinned_packages=2)

    def test_cell_search_progress_requires_a_unique_vector_prefix(self) -> None:
        cell = _attempt().identity.cell
        first = VersionPin(name="a", version="1")
        second = VersionPin(name="b", version="2")

        with pytest.raises(ValidationError, match="vector packages must be unique"):
            CellSearchProgressEvent(
                cell=cell,
                packages=(first, first),
                completed_packages=(),
            )
        with pytest.raises(ValidationError, match="current vector prefix"):
            CellSearchProgressEvent(
                cell=cell,
                packages=(first, second),
                completed_packages=(second,),
            )

    def test_cell_result_detail_is_excluded_from_runtime_event_serialization(
        self,
    ) -> None:
        attempt = _attempt()
        cell = attempt.identity.cell
        failure = _verifier_failure(attempt)
        event = CellCompletedEvent(
            cell=cell,
            completed=1,
            total=1,
            outcome=CellFailed(
                status="REJECTED",
                phase="test",
                detail=PytestFailureDetail(
                    first=PytestFailureCase(
                        nodeid="tests/test_cli.py::test_example",
                        phase="call",
                    ),
                    total=1,
                ),
                detail_failure_id=failure.failure_id,
                failures=(failure,),
            ),
        )

        outcome = event.model_dump(mode="json")["outcome"]
        assert "detail" not in outcome
        assert "detail_failure_id" not in outcome
        assert isinstance(event.outcome, CellFailed)
        with pytest.raises(ValidationError, match="explicit failure source"):
            CellFailed(
                status="REJECTED",
                phase="test",
                detail=event.outcome.detail,
                failures=(failure,),
            )

    @pytest.mark.parametrize("suffix", ("\u009b31mred", "\ud800bad"))
    def test_pytest_failure_case_rejects_unsafe_display_text(
        self,
        suffix: str,
    ) -> None:
        with pytest.raises(ValidationError, match="bounded display text"):
            PytestFailureCase(nodeid=f"case{suffix}", phase="call")

    def test_search_diagnostic_event_rejects_a_mismatched_cell(self) -> None:
        attempt = _attempt()
        failure = _verifier_failure(attempt)
        wrong_cell = attempt.identity.cell.model_copy(update={"package": "other"})

        with pytest.raises(ValidationError, match="failure scope"):
            SearchFailureEvent(cell=wrong_cell, failure=failure)

    def test_cell_failure_record_ids_are_unique(self) -> None:
        cell = _attempt().identity.cell
        failure = _verifier_failure(_attempt())
        with pytest.raises(ValidationError, match="unique"):
            CellIndeterminate(
                cell=cell,
                phase="test",
                failure_id=failure.failure_id,
                failure_records=(failure, failure),
            )

    def test_failure_record_rejects_a_tampered_stable_id(self) -> None:
        failure = _verifier_failure(_attempt())
        document = failure.model_dump(mode="python")
        document["failure_id"] = "failure-0000000000000000"

        with pytest.raises(ValidationError, match="structured facts"):
            type(failure).model_validate(document)

    def test_cell_terminal_rejects_a_failure_scoped_to_another_cell(self) -> None:
        cell = _attempt().identity.cell
        other_cell = cell.model_copy(update={"python_minor": "3.11"})
        failure = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=_attempt(cell=other_cell)),
            cause="TOOL_FAILURE",
            stage="candidate-discovery",
            process=_successful_process(exit_code=2),
        )

        with pytest.raises(ValidationError, match="result cell"):
            CellIndeterminate(
                cell=cell,
                phase="test",
                failure_id=failure.failure_id,
                failure_records=(failure,),
            )

    def test_cell_success_rejects_tampered_final_evidence(self) -> None:
        success = _cell_success()
        dumped = success.model_dump(mode="python")
        with pytest.raises(ValidationError, match="final vector"):
            CellSuccess.model_validate(
                dumped | {"final_vector": (VersionPin(name="demo", version="2"),)}
            )

        search = dict(dumped["search"])
        search["vector"] = (VersionPin(name="demo", version="2"),)
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped | {"search": search})

        dumped = success.model_dump(mode="python")
        dumped["search"]["observations"][0]["vector"] = (
            VersionPin(name="demo", version="2"),
        )
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped)

        dumped = success.model_dump(mode="python")
        dumped["candidate_snapshots"][0]["candidates"][0]["artifact"]["filename"] = (
            "tampered.whl"
        )
        with pytest.raises(ValidationError, match="artifact|digest"):
            CellSuccess.model_validate(dumped)

        dumped = success.model_dump(mode="python")
        dumped["final_evaluation"]["proposal"]["managed_vector"] = (
            VersionPin(name="demo", version="2"),
        )
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped)

        dumped = success.model_dump(mode="python")
        dumped["search"]["observations"][0]["evidence"]["attempt"]["identity"][
            "requested_managed_vector"
        ] = (VersionPin(name="demo", version="2"),)
        with pytest.raises(ValidationError):
            CellSuccess.model_validate(dumped)

    def test_package_floor_report_dump_excludes_process_output_bodies(self) -> None:
        success = _cell_success()
        dumped = success.final_evaluation.verifier.model_dump()
        assert "stdout" not in dumped
        assert "stderr" not in dumped

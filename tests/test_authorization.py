from __future__ import annotations

from candidate_fixtures import frozen_candidate_snapshot

from dataclasses import replace
import subprocess
import sys
from pathlib import Path
from typing import Literal

from packaging.requirements import Requirement
from packaging.version import Version
import pytest
import tomli

from pf.authorization import ApplyAuthorizer
from pf.errors import (
    ApplyAuthorizationError,
    ConfigurationError,
    NoApplicableFloorError,
)
from pf import policy as policy_module
from pf.policy import evaluation_policy_identity
from pf.project import ProjectLoader, marker_applies
from pf.report import PackageReportBuilder, ReportStore, ValidatedReport
from pf.resolution import environment_identity_digest
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    FailureRecord,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TyCheck,
    VerifierPass,
    VerifierRejected,
    VerifierRejectedEvaluation,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    Cell,
    InterpreterIdentity,
    PackagePlan,
    ProjectPlan,
    Proposal,
    SelectedCandidate,
    SourcePlan,
    VersionPin,
    cell_id,
    selected_candidate_evidence_digest,
)
from pf.schemas.config import WorkspacePackage
from pf.schemas.report import (
    CellIndeterminate,
    CellSearchFailure,
    CellSuccess,
    CoordinateBoundary,
    CoordinateSuccess,
    ProbeObservation,
    ProbePass,
    ProbeRejection,
)
from pf.snapshot import SnapshotBuilder, SourceSnapshot


def _process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def _attempt(
    *,
    cell: Cell,
    snapshot_digest: str,
    policy: str,
    resolution: Literal["highest", "exact-vector"],
    vector: tuple[VersionPin, ...],
    source_plan_identity_value: str,
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=(vector if resolution == "exact-vector" else None),
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity=source_plan_identity_value,
            evaluation_policy_identity=policy,
            resolution_context_digest="context",
            harness_policy_identity=(
                "harness-relaxation-v1"
                if resolution == "exact-vector"
                else "original-harness-v1"
            ),
            harness_baseline_digest=(
                "harness-baseline" if resolution == "exact-vector" else None
            ),
            selected_candidate_evidence_digest=(
                selected_candidate_evidence_digest(
                    tuple(
                        SelectedCandidate(
                            dependency=pin.name,
                            version=pin.version,
                            artifact=AvailableArtifact(
                                filename=f"{pin.name}-{pin.version}.whl",
                                kind="wheel",
                python_minors=(cell.python_minor,), targets=(cell.target,),
                                content_hash=f"sha256:{'a' * 64}",
                                locator=(
                                    f"https://files.example/"
                                    f"{pin.name}-{pin.version}.whl"
                                ),
                            ),
                        )
                        for pin in vector
                    )
                )
                if resolution == "exact-vector"
                else None
            ),
        )
    )


def _evaluation(
    *,
    cell: Cell,
    version: str,
    snapshot_digest: str,
    policy: str,
    resolution: Literal["highest", "exact-vector"],
    attempt: Attempt,
) -> PassEvaluation:
    vector = (VersionPin(name="idna", version=version),)
    project_digest = f"project-{cell_id(cell)}-{version}"
    environment_digest = f"environment-{cell_id(cell)}-{version}"
    proposal = Proposal(
        proposal_id=environment_identity_digest(
            project_plan_digest=project_digest,
            environment_plan_digest=environment_digest,
            graph=(),
        ),
        attempt_id=attempt.attempt_id,
        snapshot_digest=snapshot_digest,
        cell=cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity=policy,
        project_plan_digest=project_digest,
        environment_plan_digest=environment_digest,
        interpreter=InterpreterIdentity(
            implementation="cpython",
            version=f"{cell.python_minor}.11",
            abi=f"cpython-{cell.python_minor.replace('.', '')}-{cell.target}",
        ),
    )
    return PassEvaluation(
        proposal=proposal,
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(process=_process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(),
        ),
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )


def _successful_cell(
    package: PackagePlan,
    cell: Cell,
    floor: str,
    *,
    snapshot_digest: str,
    historical_rejection: bool = False,
) -> CellSuccess:
    policy = evaluation_policy_identity(package.config)
    plan_identity = SourcePlan.for_package(package, "SEARCH").identity
    vector = (VersionPin(name="idna", version=floor),)
    final_attempt = _attempt(
        cell=cell,
        snapshot_digest=snapshot_digest,
        policy=policy,
        resolution="exact-vector",
        vector=vector,
        source_plan_identity_value=plan_identity,
    )
    final = _evaluation(
        cell=cell,
        version=floor,
        snapshot_digest=snapshot_digest,
        policy=policy,
        resolution="exact-vector",
        attempt=final_attempt,
    )
    candidate_versions = ("1.0", floor) if historical_rejection else (floor,)
    candidates = tuple(
        Candidate(
            version=version,
            series_key=version,
            artifact=AvailableArtifact(
                filename=f"idna-{version}.whl",
                kind="wheel",
                python_minors=(cell.python_minor,), targets=(cell.target,),
                content_hash=f"sha256:{'a' * 64}",
                locator=f"https://files.example/idna-{version}.whl",
            ),
        )
        for version in candidate_versions
    )
    snapshot = frozen_candidate_snapshot(package, cell, candidates)
    baseline_vector = (VersionPin(name="idna", version="3.11"),)
    baseline_attempt = _attempt(
        cell=cell,
        snapshot_digest=snapshot_digest,
        policy=policy,
        resolution="highest",
        vector=baseline_vector,
        source_plan_identity_value=plan_identity,
    )
    baseline = _evaluation(
        cell=cell,
        version="3.11",
        snapshot_digest=snapshot_digest,
        policy=policy,
        resolution="highest",
        attempt=baseline_attempt,
    )
    observations: tuple[ProbeObservation, ...] = (
        ProbeObservation(
            dependency="idna",
            candidate_version=floor,
            vector=vector,
            evidence=ProbePass(
                attempt=final_attempt,
                proposal_id=final.proposal.proposal_id,
                evaluation=final,
            ),
        ),
    )
    failure_records: tuple[FailureRecord, ...] = ()
    predecessor: str | None = None
    predecessor_failure_id: str | None = None
    if historical_rejection:
        predecessor = "1.0"
        rejected_vector = (VersionPin(name="idna", version=predecessor),)
        rejected_attempt = _attempt(
            cell=cell,
            snapshot_digest=snapshot_digest,
            policy=policy,
            resolution="exact-vector",
            vector=rejected_vector,
            source_plan_identity_value=plan_identity,
        )
        rejected_pass = _evaluation(
            cell=cell,
            version=predecessor,
            snapshot_digest=snapshot_digest,
            policy=policy,
            resolution="exact-vector",
            attempt=rejected_attempt,
        )
        rejected = VerifierRejectedEvaluation(
            proposal=rejected_pass.proposal,
            static=rejected_pass.static,
            verifier=VerifierRejected(terminal=NormalExit(exit_code=1)),
        )
        failure = FailureRecord.from_verifier(
            scope=AttemptFailureScope(attempt=rejected_attempt),
            disposition="REJECTED",
            cause="VERIFIER_EXITED_NONZERO",
            stage="test",
            terminal=rejected.verifier.terminal,
        )
        observations = (
            ProbeObservation(
                dependency="idna",
                candidate_version=predecessor,
                vector=rejected_vector,
                evidence=ProbeRejection(
                    attempt=rejected_attempt,
                    proposal_id=rejected.proposal.proposal_id,
                    failure_id=failure.failure_id,
                    cause="VERIFIER_EXITED_NONZERO",
                    evaluation=rejected,
                ),
            ),
            *observations,
        )
        failure_records = (failure,)
        predecessor_failure_id = failure.failure_id

    return CellSuccess(
        cell=cell,
        baseline_attempt=baseline_attempt,
        static_baseline=StaticBaseline(
            proposal=baseline.proposal,
            ty=baseline.static.ty,
            digest=ty_diagnostic_digest(()),
        ),
        baseline=baseline,
        candidate_snapshots=(snapshot,),
        search=CoordinateSuccess(
            vector=vector,
            observations=observations,
            boundaries=(
                CoordinateBoundary(
                    dependency="idna",
                    floor=floor,
                    predecessor=predecessor,
                    predecessor_failure_id=predecessor_failure_id,
                ),
            ),
            sweeps=len(observations),
        ),
        final_vector=vector,
        final_evaluation=final,
        failure_records=failure_records,
    )


def _write_project(
    root: Path,
    *,
    platforms: tuple[str, ...] | None,
    pythons: tuple[str, ...] = ("3.10",),
    dependency: str | tuple[str, ...] = "idna<4",
    extra: str = "",
) -> None:
    platform_line = (
        "platforms = ["
        + ", ".join(f'"{item}"' for item in sorted(platforms))
        + "]\n"
        if platforms is not None
        else ""
    )
    dependencies = (dependency,) if isinstance(dependency, str) else dependency
    dependency_array = ", ".join(f'"{item}"' for item in dependencies)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.10"\n'
        f"dependencies = [{dependency_array}]\n"
        "[tool.pf]\npythons = ["
        + ", ".join(f'"{item}"' for item in pythons)
        + "]\n"
        + platform_line
        + 'test-command = ["pytest"]\n'
        + extra,
        encoding="utf-8",
    )


def _snapshot(project: ProjectPlan, root: Path) -> SourceSnapshot:
    return SnapshotBuilder.without_processes().build(
        root,
        owned_pyproject_paths=project.owned_pyproject_paths,
    )


def _report(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    floors: dict[str, str],
) -> ValidatedReport:
    return PackageReportBuilder().build(
        package=package,
        source_plan=SourcePlan.for_package(package, "SEARCH"),
        source_snapshot=snapshot.identity,
        cell_results=tuple(
            _successful_cell(
                package,
                cell,
                floors[cell.target],
                snapshot_digest=snapshot.identity.digest,
            )
            for cell in package.cells
            if cell.target in floors
        ),
    )


class TestApplyAuthorizer:
    def test_normalization_policy_isolates_reports_with_explicit_defaults(
        self, tmp_path, monkeypatch
    ):
        linux = "x86_64-unknown-linux-gnu"
        macos = "aarch64-apple-darwin"
        _write_project(
            tmp_path,
            platforms=(linux, macos),
            extra='resolve-artifact = "any"\n[dependency-groups]\ntest = ["idna>=1"]\n',
        )
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        try:
            with monkeypatch.context() as other_policy:
                other_policy.setitem(
                    policy_module.VALIDATION_CONTRACT_POLICY,
                    "project_overlap",
                    "different-normalization-contract",
                )
                other = _report(project.target, snapshot, {linux: "2.0"})
            current = _report(project.target, snapshot, {macos: "2.0"})
            assert other.source_snapshot == current.source_snapshot
            assert other.target_cells == current.target_cells
            assert other.generator == current.generator
            assert other.policy_identity != current.policy_identity
            assert other.report_generation_id != current.report_generation_id
            store = ReportStore()
            for operation in (
                lambda: store.merge((other, current)),
                lambda: store.update(other, current),
            ):
                with pytest.raises(
                    ConfigurationError, match="generation identity mismatch"
                ):
                    operation()
            for force in (False, True):
                with pytest.raises(
                    ApplyAuthorizationError, match="evaluation policy mismatch"
                ):
                    ApplyAuthorizer().authorize(
                        report=other,
                        project=project,
                        current_snapshot=snapshot,
                        force=force,
                    )
            report_path = tmp_path / "package-floor.json"
            store.write(report_path, other)
            updated = store.update_path(report_path, current)
            assert updated.replace_generation
            assert {result.cell.target for result in updated.report.cell_results} == {
                macos
            }
            assert store.read(report_path).policy_identity == current.policy_identity
        finally:
            snapshot.close()

    def test_required_surface_round_trips_and_authorizes_current_contract(
        self, tmp_path
    ):
        linux = "x86_64-unknown-linux-gnu"
        _write_project(
            tmp_path,
            platforms=(linux,),
            extra='resolve-artifact = "any"\n[project.optional-dependencies]\nsocks = []\n[dependency-groups]\ntest = ["demo[socks]", "idna>=1"]\n',
        )
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        try:
            report = _report(project.target, snapshot, {linux: "2.0"})
            path = tmp_path / "package-floor.json"
            store = ReportStore()
            store.write(path, report)
            restored = store.read(path)
            assert {cell.extra_surface for cell in restored.target_cells} == {
                ("socks",)
            }
            authority = ApplyAuthorizer().authorize(
                report=restored, project=project, current_snapshot=snapshot, force=False
            )
            assert authority.package_apply.dependency_state == "WRITABLE"
        finally:
            snapshot.close()

    def test_omitted_platform_uses_declared_matrix_without_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "pf.project.host_target",
            lambda: "x86_64-unknown-linux-gnu",
        )
        _write_project(tmp_path, platforms=None)
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )

        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=project,
            current_snapshot=snapshot,
            force=False,
        )

        package_apply = authorization.package_apply
        assert package_apply.scope == "DECLARED_MATRIX"
        assert package_apply.declared_platforms == ()
        replacement = package_apply.authorized_edits[0].group_edits[0]
        assert Requirement(replacement.replacement_requirements[0]).marker is None
        snapshot.close()

    def test_multi_platform_missing_selector_is_default_platform_scoped(
        self,
        tmp_path: Path,
    ) -> None:
        platforms = (
            "x86_64-pc-windows-msvc",
            "x86_64-unknown-linux-gnu",
        )
        _write_project(tmp_path, platforms=platforms)
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )

        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=project,
            current_snapshot=snapshot,
            force=False,
        )

        package_apply = authorization.package_apply
        assert package_apply.scope == "PLATFORM_SCOPED"
        assert [item.sys_platform for item in package_apply.selected_selectors] == [
            "linux"
        ]
        assert [item.sys_platform for item in package_apply.preserved_selectors] == [
            "win32"
        ]
        replacement = package_apply.authorized_edits[0].group_edits[0]
        assert any(
            "!=" in str(Requirement(raw).marker)
            for raw in replacement.replacement_requirements
        )
        snapshot.close()

    def test_explicit_single_platform_uses_declared_matrix_without_marker(
        self,
        tmp_path: Path,
    ) -> None:
        platform = "x86_64-unknown-linux-gnu"
        _write_project(tmp_path, platforms=(platform,))
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(project.target, snapshot, {platform: "2.0"})

        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=project,
            current_snapshot=snapshot,
            force=False,
        )

        package_apply = authorization.package_apply
        assert package_apply.scope == "DECLARED_MATRIX"
        replacement = package_apply.authorized_edits[0].group_edits[0]
        assert Requirement(replacement.replacement_requirements[0]).marker is None
        snapshot.close()

    def test_complete_multi_selector_report_uses_declared_matrix(
        self,
        tmp_path: Path,
    ) -> None:
        platforms = (
            "x86_64-unknown-linux-gnu",
            "x86_64-pc-windows-msvc",
        )
        _write_project(tmp_path, platforms=platforms)
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {
                "x86_64-unknown-linux-gnu": "2.0",
                "x86_64-pc-windows-msvc": "3.0",
            },
        )

        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=project,
            current_snapshot=snapshot,
            force=False,
        )

        package_apply = authorization.package_apply
        assert package_apply.scope == "DECLARED_MATRIX"
        assert package_apply.preserved_selectors == ()
        snapshot.close()

    def test_missing_libc_variant_inherits_selector_floor_without_scoping(
        self,
        tmp_path: Path,
    ) -> None:
        platforms = (
            "x86_64-unknown-linux-gnu",
            "x86_64-unknown-linux-musl",
        )
        _write_project(tmp_path, platforms=platforms)
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )

        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=project,
            current_snapshot=snapshot,
            force=False,
        )

        package_apply = authorization.package_apply
        assert package_apply.scope == "DECLARED_MATRIX"
        assert package_apply.preserved_selectors == ()
        raw = (
            package_apply.authorized_edits[0].group_edits[0].replacement_requirements[0]
        )
        assert Requirement(raw).marker is None
        snapshot.close()

    def test_conflicting_observed_libc_floors_are_not_representable(
        self,
        tmp_path: Path,
    ) -> None:
        platforms = (
            "x86_64-unknown-linux-gnu",
            "x86_64-unknown-linux-musl",
        )
        _write_project(tmp_path, platforms=platforms)
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {
                "x86_64-unknown-linux-gnu": "2.0",
                "x86_64-unknown-linux-musl": "3.0",
            },
        )

        with pytest.raises(ApplyAuthorizationError, match="representable"):
            ApplyAuthorizer().authorize(
                report=report,
                project=project,
                current_snapshot=snapshot,
                force=True,
            )
        snapshot.close()

    def test_partial_platform_cells_are_not_waived_by_force(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
            pythons=("3.10", "3.11"),
        )
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        package = project.target
        report = PackageReportBuilder().build(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot.identity,
            cell_results=(
                _successful_cell(
                    package,
                    package.cells[0],
                    "2.0",
                    snapshot_digest=snapshot.identity.digest,
                ),
            ),
        )

        with pytest.raises(ApplyAuthorizationError, match="partially observed"):
            ApplyAuthorizer().authorize(
                report=report,
                project=project,
                current_snapshot=snapshot,
                force=True,
            )
        snapshot.close()

    @pytest.mark.parametrize("force", (False, True))
    def test_current_platform_mismatch_is_never_waived(
        self,
        tmp_path: Path,
        force: bool,
    ) -> None:
        linux = "x86_64-unknown-linux-gnu"
        _write_project(tmp_path, platforms=(linux,))
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(project.target, report_snapshot, {linux: "2.0"})
        _write_project(
            tmp_path,
            platforms=("x86_64-pc-windows-msvc",),
        )
        current_project = ProjectLoader().load(
            root=tmp_path,
        )
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="platform declaration"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=force,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_report_without_a_final_success_has_no_applicable_floor(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = PackageReportBuilder().build(
            package=project.target,
            source_plan=SourcePlan.for_package(project.target, "SEARCH"),
            source_snapshot=snapshot.identity,
            cell_results=(),
        )

        with pytest.raises(NoApplicableFloorError, match="successful final cell"):
            ApplyAuthorizer().authorize(
                report=report,
                project=project,
                current_snapshot=snapshot,
                force=True,
            )
        snapshot.close()

    def test_historical_candidate_rejection_does_not_block_successful_final_root(
        self,
        tmp_path: Path,
    ) -> None:
        platform = "x86_64-unknown-linux-gnu"
        _write_project(tmp_path, platforms=(platform,))
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        package = project.target
        report = PackageReportBuilder().build(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            source_snapshot=snapshot.identity,
            cell_results=(
                _successful_cell(
                    package,
                    package.cells[0],
                    "2.0",
                    snapshot_digest=snapshot.identity.digest,
                    historical_rejection=True,
                ),
            ),
        )
        path = tmp_path / "package-floor.json"
        ReportStore().write(path, report)
        report = ReportStore().read(path)

        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=project,
            current_snapshot=snapshot,
            force=False,
        )

        assert authorization.package_apply.observed_cells == 1
        assert authorization.package_apply.authorized_edits
        snapshot.close()

    @pytest.mark.parametrize("root_kind", ("indeterminate", "non-monotonic"))
    def test_non_success_root_beside_a_success_is_not_waived_by_force(
        self,
        tmp_path: Path,
        root_kind: str,
    ) -> None:
        platforms = (
            "x86_64-unknown-linux-gnu",
            "x86_64-pc-windows-msvc",
        )
        _write_project(tmp_path, platforms=platforms)
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        windows = next(
            cell
            for cell in project.target.cells
            if cell.target == "x86_64-pc-windows-msvc"
        )
        failed_root = (
            CellIndeterminate.model_construct(cell=windows)
            if root_kind == "indeterminate"
            else CellSearchFailure.model_construct(
                cell=windows,
                reason="NON_MONOTONIC",
            )
        )
        report = replace(
            report,
            cell_results=(*report.cell_results, failed_root),
        )

        with pytest.raises(ApplyAuthorizationError, match="failed or partially"):
            ApplyAuthorizer().authorize(
                report=report,
                project=project,
                current_snapshot=snapshot,
                force=True,
            )
        snapshot.close()

    def test_source_drift_requires_force_and_records_bounded_facts(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        for index in range(10):
            (tmp_path / f"source-{index:02}.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        for index in range(10):
            (tmp_path / f"source-{index:02}.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="source snapshot"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=False,
            )
        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=current_project,
            current_snapshot=current_snapshot,
            force=True,
        )

        assert authorization.waivers_used == ("SOURCE_SNAPSHOT_DRIFT",)
        assert authorization.presentation_facts.source_drift_path_count == 10
        assert authorization.presentation_facts.source_drift_paths == tuple(
            f"source-{index:02}.py" for index in range(8)
        )
        report_snapshot.close()
        current_snapshot.close()


class TestApplyAuthorizationDriftAndCliRoundTrip:
    @pytest.mark.parametrize(
        ("member_version", "expected_code"),
        (("2.5", 0), ("1.5", 3)),
    )
    def test_static_workspace_member_version_controls_cli_apply_without_metadata_edits(
        self,
        tmp_path: Path,
        member_version: str,
        expected_code: int,
    ) -> None:
        member = tmp_path / "packages" / "idna"
        member.mkdir(parents=True)
        root_pyproject = tmp_path / "pyproject.toml"
        root_pyproject.write_text(
            """
[project]
name = "demo"
version = "1"
requires-python = ">=3.10"
dependencies = ["idna>=1"]

[tool.uv.sources]
idna = { workspace = true }

[tool.uv.workspace]
members = ["packages/*"]

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        member_pyproject = member / "pyproject.toml"
        member_pyproject.write_text(
            f'[project]\nname = "idna"\nversion = "{member_version}"\n',
            encoding="utf-8",
        )
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        ReportStore().write(tmp_path / "package-floor.json", report)
        snapshot.close()
        member_before = member_pyproject.read_bytes()

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pf",
                "apply",
                "--package",
                "demo",
            ],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == expected_code
        assert member_pyproject.read_bytes() == member_before
        with root_pyproject.open("rb") as stream:
            document = tomli.load(stream)
        assert document["tool"]["uv"]["sources"] == {"idna": {"workspace": True}}
        if expected_code == 0:
            requirement = Requirement(document["project"]["dependencies"][0])
            assert requirement.name == "idna"
            assert Version("2.0") in requirement.specifier
        else:
            assert document["project"]["dependencies"] == ["idna>=1"]
            assert "version 1.5 does not satisfy the intended requirement" in (
                " ".join(result.stderr.split())
            )

    @pytest.mark.parametrize("force", (False, True))
    def test_dynamic_workspace_member_blocks_cli_apply_before_edit(
        self,
        tmp_path: Path,
        force: bool,
    ) -> None:
        member = tmp_path / "packages" / "idna"
        member.mkdir(parents=True)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            """
[project]
name = "demo"
version = "1"
requires-python = ">=3.10"
dependencies = ["idna>=1"]

[tool.uv.sources]
idna = { workspace = true }

[tool.uv.workspace]
members = ["packages/*"]

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (member / "pyproject.toml").write_text(
            """
[project]
name = "idna"
dynamic = ["version"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        ReportStore().write(tmp_path / "package-floor.json", report)
        snapshot.close()
        before = pyproject.read_bytes()
        argv = [
            sys.executable,
            "-m",
            "pf",
            "apply",
            "--package",
            "demo",
        ]
        if force:
            argv.append("--force")

        result = subprocess.run(
            argv,
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 3
        assert result.stdout == ""
        assert "Usage:" not in result.stderr
        assert "Cannot apply idna>=" in result.stderr
        assert "workspace member idna declares its version dynamically" in (
            " ".join(result.stderr.split())
        )
        assert "PF cannot verify offline" in result.stderr
        assert "apply the requirement manually and run pf smoke" in (
            " ".join(result.stderr.split())
        )
        assert "--force" not in result.stderr
        assert pyproject.read_bytes() == before

    def test_sequential_scoped_apply_starts_a_new_generation_and_reprojects_group(
        self,
        tmp_path: Path,
    ) -> None:
        platforms = (
            "x86_64-unknown-linux-gnu",
            "x86_64-pc-windows-msvc",
        )
        _write_project(tmp_path, platforms=platforms)
        first_project = ProjectLoader().load(root=tmp_path)
        first_snapshot = _snapshot(first_project, tmp_path)
        first_report = _report(
            first_project.target,
            first_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        ReportStore().write(tmp_path / "package-floor.json", first_report)
        first_snapshot.close()

        explained = subprocess.run(
            [sys.executable, "-m", "pf", "explain"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert explained.returncode == 0, explained.stderr
        assert "platform-scoped apply evidence is available" in (
            " ".join(explained.stdout.split())
        )

        linux_apply = subprocess.run(
            [sys.executable, "-m", "pf", "apply"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert linux_apply.returncode == 0, linux_apply.stderr
        assert "Scope linux/x86_64 verified" in " ".join(linux_apply.stdout.split())
        second_project = ProjectLoader().load(root=tmp_path)
        second_snapshot = _snapshot(second_project, tmp_path)
        assert second_snapshot.identity.digest != first_report.source_snapshot.digest
        second_report = _report(
            second_project.target,
            second_snapshot,
            {"x86_64-pc-windows-msvc": "3.0"},
        )
        assert second_report.report_generation_id != first_report.report_generation_id
        ReportStore().write(tmp_path / "package-floor.json", second_report)
        second_snapshot.close()

        windows_apply = subprocess.run(
            [sys.executable, "-m", "pf", "apply"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert windows_apply.returncode == 0, windows_apply.stderr
        rendered = " ".join(windows_apply.stdout.split())
        assert "Scope windows/x86_64 verified" in rendered
        assert "Preserved linux/x86_64" in rendered
        final_project = ProjectLoader().load(root=tmp_path)
        declarations = final_project.target.declarations
        expected_floors = {
            "x86_64-unknown-linux-gnu": "2.0",
            "x86_64-pc-windows-msvc": "3.0",
        }
        for cell in final_project.target.cells:
            active = tuple(
                item
                for item in declarations
                if item.name == "idna" and marker_applies(item.marker, cell)
            )
            assert len(active) == 1
            assert (">=", expected_floors[cell.target]) in {
                (specifier.operator, specifier.version)
                for specifier in Requirement(active[0].raw).specifier
            }

        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        assert raw.count('sys_platform != "linux"') == 0

    def test_force_source_drift_is_a_successful_stderr_warning(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        project = ProjectLoader().load(root=tmp_path)
        snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        ReportStore().write(tmp_path / "package-floor.json", report)
        snapshot.close()
        (tmp_path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-m", "pf", "apply", "--force"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert "source-drift override" in result.stderr
        assert "source drift accepted · 1 path" in result.stderr
        assert "app.py" in result.stderr
        assert result.stderr.count("Applied floors") == 1
        assert "Applied floors with source-drift override" in result.stderr

    @pytest.mark.parametrize("force", (False, True))
    def test_dependency_drift_is_never_waived(
        self,
        tmp_path: Path,
        force: bool,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
            dependency="idna<3",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="dependency declarations"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=force,
            )
        report_snapshot.close()
        current_snapshot.close()

    @pytest.mark.parametrize(
        "replacement",
        (
            "idna[security]<4",
            "idna<4 ; python_version >= '3.10'",
            "idna==2.0",
            "idna<4,!=2.5",
        ),
    )
    def test_dependency_ownership_and_semantics_drift_is_not_forceable(
        self,
        tmp_path: Path,
        replacement: str,
    ) -> None:
        platform = "x86_64-unknown-linux-gnu"
        _write_project(tmp_path, platforms=(platform,))
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(project.target, report_snapshot, {platform: "2.0"})
        _write_project(
            tmp_path,
            platforms=(platform,),
            dependency=replacement,
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="dependency declarations"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=True,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_unmanaged_dependency_group_drift_is_not_forceable(
        self,
        tmp_path: Path,
    ) -> None:
        platform = "x86_64-unknown-linux-gnu"
        _write_project(
            tmp_path,
            platforms=(platform,),
            dependency=("idna<4", "urllib3<3"),
            extra='managed-deps = ["idna"]\n',
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(project.target, report_snapshot, {platform: "2.0"})
        _write_project(
            tmp_path,
            platforms=(platform,),
            dependency=("idna<4", "urllib3<2"),
            extra='managed-deps = ["idna"]\n',
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="dependency declarations"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=True,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_semantically_reordered_dependency_array_remains_writable(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
            dependency=("idna<4", "urllib3<3"),
            extra='managed-deps = ["idna"]\n',
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            raw.replace(
                'dependencies = ["idna<4", "urllib3<3"]',
                'dependencies = ["urllib3<3", "idna<4"]',
            ),
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        authorization = ApplyAuthorizer().authorize(
            report=report,
            project=current_project,
            current_snapshot=current_snapshot,
            force=False,
        )

        assert authorization.package_apply.dependency_state == "WRITABLE"
        report_snapshot.close()
        current_snapshot.close()

    def test_policy_and_requires_python_drift_are_not_forceable(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            raw.replace('requires-python = ">=3.10"', 'requires-python = ">=3.9"'),
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="Python semantics"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=True,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_exact_projected_dependency_state_is_an_unforced_noop(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        first = ApplyAuthorizer().authorize(
            report=report,
            project=project,
            current_snapshot=report_snapshot,
            force=False,
        )
        replacement = (
            first.package_apply.authorized_edits[0]
            .group_edits[0]
            .replacement_requirements[0]
        )
        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            raw.replace("idna<4", replacement),
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        repeated = ApplyAuthorizer().authorize(
            report=report,
            project=current_project,
            current_snapshot=current_snapshot,
            force=False,
        )

        package_apply = repeated.package_apply
        assert package_apply.dependency_state == "NOOP"
        assert package_apply.authorized_edits == ()
        assert repeated.waivers_used == ()
        report_snapshot.close()
        current_snapshot.close()

    @pytest.mark.parametrize("force", (False, True))
    def test_uv_project_configuration_drift_is_not_forceable(
        self,
        tmp_path: Path,
        force: bool,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            raw.replace(
                "[tool.pf]",
                '[tool.uv]\nprerelease = "allow"\n\n[tool.pf]',
            ),
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(
            ApplyAuthorizationError,
            match="uv project configuration",
        ):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=force,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_harness_source_plan_drift_is_not_forceable(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(tmp_path, platforms=("x86_64-unknown-linux-gnu",))
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            raw.replace(
                "[tool.pf]",
                '[dependency-groups]\ntest = ["pytest"]\n[tool.pf]',
            ),
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="source plan"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=True,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_test_command_drift_is_not_forceable(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            raw.replace(
                'test-command = ["pytest"]',
                'test-command = ["pytest", "-q"]',
            ),
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="policy"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=True,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_dependency_source_plan_drift_is_not_forceable(
        self,
        tmp_path: Path,
    ) -> None:
        _write_project(
            tmp_path,
            platforms=("x86_64-unknown-linux-gnu",),
        )
        project = ProjectLoader().load(root=tmp_path)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        (tmp_path / "vendor" / "idna").mkdir(parents=True)
        raw = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            raw + '[tool.uv.sources]\nidna = { path = "vendor/idna" }\n',
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(root=tmp_path)
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="source plan"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=True,
            )
        report_snapshot.close()
        current_snapshot.close()

    def test_unselected_workspace_dependency_drift_is_not_forceable(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
            '[tool.pf]\npythons = ["3.10"]\n'
            'platforms = ["x86_64-unknown-linux-gnu"]\n'
            'test-command = ["pytest"]\n',
            encoding="utf-8",
        )
        for name in ("alpha", "beta"):
            package_root = tmp_path / "packages" / name
            package_root.mkdir(parents=True)
            (package_root / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\nversion = "1"\n'
                'dependencies = ["idna<4"]\n',
                encoding="utf-8",
            )
        selector = WorkspacePackage(canonical_name="alpha")
        project = ProjectLoader().load(root=tmp_path, selector=selector)
        report_snapshot = _snapshot(project, tmp_path)
        report = _report(
            project.target,
            report_snapshot,
            {"x86_64-unknown-linux-gnu": "2.0"},
        )
        beta = tmp_path / "packages" / "beta" / "pyproject.toml"
        beta.write_text(
            beta.read_text(encoding="utf-8").replace("idna<4", "idna<3"),
            encoding="utf-8",
        )
        current_project = ProjectLoader().load(
            root=tmp_path,
            selector=selector,
        )
        current_snapshot = _snapshot(current_project, tmp_path)

        with pytest.raises(ApplyAuthorizationError, match="unselected package"):
            ApplyAuthorizer().authorize(
                report=report,
                project=current_project,
                current_snapshot=current_snapshot,
                force=True,
            )
        report_snapshot.close()
        current_snapshot.close()

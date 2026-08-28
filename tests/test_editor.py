from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Literal

import tomli
import pytest

from pf.editor import ProjectEditor
from pf.errors import ConfigurationError, NoApplicableFloorError
from pf.project import ProjectLoader
from pf.report import PackageReportBuilder, ValidatedReport
from pf.resolution import environment_identity_digest
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    NormalExit,
    PassEvaluation,
    ProcessResult,
    StaticBaseline,
    StaticUnchangedEvaluation,
    TyCheck,
    VerifierPass,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    InterpreterIdentity,
    Proposal,
    SourceIdentity,
    VersionPin,
    candidate_snapshot_digest,
)
from pf.schemas.report import (
    CellSuccess,
    CoordinateBoundary,
    CoordinateSuccess,
    ProbeObservation,
    ProbePass,
)
from pf.snapshot import SnapshotBuilder


def process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def candidate_snapshot(
    cell: Cell,
    vector: tuple[VersionPin, ...],
) -> tuple[CandidateSnapshot, ...]:
    pin = vector[0]
    source = SourceIdentity(kind="registry")
    candidates = (
        Candidate(
            version=pin.version,
            series_key=pin.version,
            artifact=AvailableArtifact(
                filename=f"{pin.name}-{pin.version}.whl",
                kind="wheel",
                content_hash=f"sha256:{'a' * 64}",
                locator=f"https://files.example/{pin.name}-{pin.version}.whl",
            ),
        ),
    )
    representatives = ((pin.version, pin.version),)
    return (
        CandidateSnapshot(
            dependency=pin.name,
            cell=cell,
            policy_identity="policy",
            source=source,
            candidates=candidates,
            series_representatives=representatives,
            digest=candidate_snapshot_digest(
                dependency=pin.name,
                cell=cell,
                policy_identity="policy",
                source=source,
                candidates=candidates,
                series_representatives=representatives,
            ),
        ),
    )


def report_attempt(
    *,
    cell: Cell,
    snapshot_digest: str,
    resolution: Literal["highest", "exact-vector"],
    vector: tuple[VersionPin, ...],
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            identity_version="attempt-v2",
            source_snapshot_digest=snapshot_digest,
            cell=cell,
            requested_resolution=resolution,
            requested_managed_vector=(
                vector if resolution == "exact-vector" else None
            ),
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
            resolution_context_digest="context",
            harness_policy_identity=(
                "harness-relaxation-v1"
                if resolution == "exact-vector"
                else "original-harness-v1"
            ),
            harness_baseline_digest=(
                "harness-baseline"
                if resolution == "exact-vector"
                else None
            ),
            selected_candidate_evidence_digest=(
                "selected-candidate"
                if resolution == "exact-vector"
                else None
            ),
        )
    )


def evaluation(
    cell: Cell,
    version: str,
    *,
    snapshot_digest: str,
    resolution: Literal["highest", "exact-vector"] = "exact-vector",
    attempt: Attempt | None = None,
) -> PassEvaluation:
    vector = (VersionPin(name="idna", version=version),)
    owned_attempt = attempt or report_attempt(
        cell=cell,
        snapshot_digest=snapshot_digest,
        resolution=resolution,
        vector=vector,
    )
    project_digest = f"project-{version}"
    environment_digest = f"environment-{version}"
    proposal = Proposal(
        proposal_id=environment_identity_digest(
            project_plan_digest=project_digest,
            environment_plan_digest=environment_digest,
            graph=(),
        ),
        attempt_id=owned_attempt.attempt_id,
        snapshot_digest=snapshot_digest,
        cell=cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
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
            ty=TyCheck(process=process(), diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
            incremental=(),
        ),
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )


def _report_for_package(package, source_snapshot) -> ValidatedReport:
    cell = package.cells[0]
    vector = (VersionPin(name="idna", version="3.0"),)
    final_attempt = report_attempt(
        cell=cell,
        snapshot_digest=source_snapshot.digest,
        resolution="exact-vector",
        vector=vector,
    )
    final_evaluation = evaluation(
        cell,
        "3.0",
        snapshot_digest=source_snapshot.digest,
        attempt=final_attempt,
    )
    coordinate = CoordinateSuccess(
        vector=vector,
        observations=(
            ProbeObservation(
                dependency="idna",
                candidate_version="3.0",
                vector=vector,
                evidence=ProbePass(
                    attempt=final_attempt,
                    proposal_id=final_evaluation.proposal.proposal_id,
                    evaluation=final_evaluation,
                ),
            ),
        ),
        boundaries=(CoordinateBoundary(dependency="idna", floor="3.0"),),
        sweeps=1,
    )
    baseline_attempt = report_attempt(
        cell=cell,
        snapshot_digest=source_snapshot.digest,
        resolution="highest",
        vector=vector,
    )
    baseline_evaluation = evaluation(
        cell,
        "3.11",
        snapshot_digest=source_snapshot.digest,
        resolution="highest",
        attempt=baseline_attempt,
    )
    return PackageReportBuilder().build(
        package=package,
        source_snapshot=source_snapshot,
        cell_results=(
            CellSuccess(
                cell=cell,
                baseline_attempt=baseline_attempt,
                static_baseline=StaticBaseline(
                    proposal=baseline_evaluation.proposal,
                    ty=baseline_evaluation.static.ty,
                    digest=ty_diagnostic_digest(
                        baseline_evaluation.static.ty.diagnostics
                    ),
                ),
                baseline=baseline_evaluation,
                candidate_snapshots=candidate_snapshot(cell, vector),
                search=coordinate,
                final_vector=vector,
                final_evaluation=final_evaluation,
            ),
        ),
    )


def _single_package_report(root: Path) -> ValidatedReport:
    package = ProjectLoader().load(root=root, package_selection=None).packages[0]
    snapshot = SnapshotBuilder.without_processes().build(root)
    try:
        return _report_for_package(package, snapshot.identity)
    finally:
        snapshot.close()


class TestProjectEditor:
    def test_apply_many_accepts_an_empty_workspace(self, tmp_path: Path) -> None:
        assert (
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply_many(
                reports=(),
                root=tmp_path,
            )
            == ()
        )

    def test_apply_many_rejects_reports_from_different_snapshots(
        self,
        tmp_path: Path,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "demo"\nversion = "1"\ndependencies = ["idna"]\n',
            encoding="utf-8",
        )
        first = _single_package_report(tmp_path)
        pyproject.write_text(pyproject.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        second = _single_package_report(tmp_path)

        with pytest.raises(ConfigurationError, match="different source snapshots"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply_many(
                reports=(first, second),
                root=tmp_path,
            )

    def test_apply_rejects_an_incomplete_report(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "demo"\nversion = "1"\ndependencies = ["idna"]\n',
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        try:
            report = PackageReportBuilder().build(
                package=package,
                source_snapshot=snapshot.identity,
                cell_results=(),
            )
        finally:
            snapshot.close()

        with pytest.raises(NoApplicableFloorError):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                report=report,
                root=tmp_path,
            )

    def test_apply_rejects_a_missing_report_project(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "demo"\nversion = "1"\ndependencies = ["idna"]\n',
            encoding="utf-8",
        )
        report = _single_package_report(tmp_path)
        missing = replace(
            report,
            package=report.package.model_copy(
                update={"pyproject_path": "missing/pyproject.toml"}
            ),
        )

        with pytest.raises(ConfigurationError, match="pyproject does not exist"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                report=missing,
                root=tmp_path,
            )

    def test_apply_rejects_a_report_project_outside_the_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\ndependencies = ["idna"]\n',
            encoding="utf-8",
        )
        report = _single_package_report(tmp_path)
        escaped = replace(
            report,
            package=report.package.model_copy(update={"pyproject_path": "../pyproject.toml"}),
        )

        with pytest.raises(ConfigurationError, match="escapes project root"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                report=escaped,
                root=tmp_path,
            )

    def test_apply_rejects_an_unrepresentable_projection(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\ndependencies = ["idna"]\n',
            encoding="utf-8",
        )
        report = _single_package_report(tmp_path)
        projection = report.projection_evidence[0].model_copy(
            update={"representable": False}
        )

        with pytest.raises(NoApplicableFloorError, match="not applicable"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                report=replace(report, projection_evidence=(projection,)),
                root=tmp_path,
            )

    def test_apply_rejects_an_unknown_projection_declaration(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1"\ndependencies = ["idna"]\n',
            encoding="utf-8",
        )
        report = _single_package_report(tmp_path)
        projection = report.projection_evidence[0].model_copy(
            update={"declaration_id": "missing-declaration"}
        )

        with pytest.raises(ConfigurationError, match="unknown declaration"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                report=replace(report, projection_evidence=(projection,)),
                root=tmp_path,
            )

    def test_apply_many_rejects_invalid_recovery_state(
        self,
        tmp_path: Path,
    ) -> None:
        journal = tmp_path / ".pf" / "apply-recovery.json"
        journal.parent.mkdir()
        journal.write_text("not JSON\n", encoding="utf-8")
        editor = ProjectEditor(snapshots=SnapshotBuilder.without_processes())

        with pytest.raises(ConfigurationError, match="invalid apply recovery log"):
            editor.apply_many(reports=(), root=tmp_path)

    def test_apply_many_discards_committed_recovery_state(
        self,
        tmp_path: Path,
    ) -> None:
        journal = tmp_path / ".pf" / "apply-recovery.json"
        journal.parent.mkdir()
        journal.write_text(
            '{"schema_version":2,"state":"COMMITTED","files":[]}\n',
            encoding="utf-8",
        )
        editor = ProjectEditor(snapshots=SnapshotBuilder.without_processes())

        assert editor.apply_many(reports=(), root=tmp_path) == ()

    def test_project_editor_preserves_toml_comments_and_is_idempotent(
        self,
        tmp_path: Path,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0" # keep version comment
    dependencies = [
        "idna>2,!=2.5,<4", # verified by integration tests
        "click==8.1.8",
    ]

    [dependency-groups]
    test = ["pytest"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        package = (
            ProjectLoader().load(root=tmp_path, package_selection=None).packages[0]
        )
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        cell = package.cells[0]
        vector = (VersionPin(name="idna", version="3.0"),)
        final_attempt = report_attempt(
            cell=cell,
            snapshot_digest=snapshot.identity.digest,
            resolution="exact-vector",
            vector=vector,
        )
        final_evaluation = evaluation(
            cell,
            "3.0",
            snapshot_digest=snapshot.identity.digest,
            attempt=final_attempt,
        )
        search = CoordinateSuccess(
            vector=vector,
            observations=(
                ProbeObservation(
                    dependency="idna",
                    candidate_version="3.0",
                    vector=vector,
                    evidence=ProbePass(
                        attempt=final_attempt,
                        proposal_id=final_evaluation.proposal.proposal_id,
                        evaluation=final_evaluation,
                    ),
                ),
            ),
            boundaries=(CoordinateBoundary(dependency="idna", floor="3.0"),),
            sweeps=1,
        )
        baseline_attempt = report_attempt(
            cell=cell,
            snapshot_digest=snapshot.identity.digest,
            resolution="highest",
            vector=vector,
        )
        baseline_evaluation = evaluation(
            cell,
            "3.11",
            snapshot_digest=snapshot.identity.digest,
            resolution="highest",
            attempt=baseline_attempt,
        )
        result = CellSuccess(
            cell=cell,
            baseline_attempt=baseline_attempt,
            static_baseline=StaticBaseline(
                proposal=baseline_evaluation.proposal,
                ty=baseline_evaluation.static.ty,
                digest=ty_diagnostic_digest(baseline_evaluation.static.ty.diagnostics),
            ),
            baseline=baseline_evaluation,
            candidate_snapshots=candidate_snapshot(cell, vector),
            search=search,
            final_vector=vector,
            final_evaluation=final_evaluation,
        )
        report = PackageReportBuilder().build(
            package=package,
            source_snapshot=snapshot.identity,
            cell_results=(result,),
        )
        editor = ProjectEditor(snapshots=SnapshotBuilder.without_processes())

        projection = report.projection_evidence[0]
        malicious_report = replace(
            report,
            projection_evidence=(
                projection.model_copy(
                    update={"projected_requirements": ("idna>=3.0",)}
                ),
            ),
        )
        before = pyproject.read_bytes()
        with pytest.raises(
            ConfigurationError, match="unauthorized projected requirement"
        ):
            editor.apply(report=malicious_report, root=tmp_path)
        assert pyproject.read_bytes() == before

        first = editor.apply(report=report, root=tmp_path)
        after_first = pyproject.read_bytes()
        second = editor.apply(report=report, root=tmp_path)

        with pyproject.open("rb") as stream:
            document = tomli.load(stream)
        assert first.changed is True
        assert second.changed is False
        assert pyproject.read_bytes() == after_first
        assert document["project"]["dependencies"] == [
            "idna!=2.5,<4,>=3.0",
            "click==8.1.8",
        ]
        content = after_first.decode()
        assert "# verified by integration tests" in content
        assert "# keep version comment" in content
        assert '"click==8.1.8"' in content

    def test_project_editor_applies_all_workspace_reports_against_one_snapshot(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [tool.uv.workspace]
    members = ["packages/*"]

    [dependency-groups]
    test = []

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        for name in ("alpha", "beta"):
            package_root = tmp_path / "packages" / name
            package_root.mkdir(parents=True)
            (package_root / "pyproject.toml").write_text(
                f"""
    [project]
    name = "{name}"
    version = "0.1.0"
    dependencies = ["idna<4"]
    """.strip()
                + "\n",
                encoding="utf-8",
            )
        project = ProjectLoader().load(root=tmp_path, package_selection=None)
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        reports = []
        for package in project.packages:
            cell = package.cells[0]
            vector = (VersionPin(name="idna", version="3.0"),)
            final_attempt = report_attempt(
                cell=cell,
                snapshot_digest=snapshot.identity.digest,
                resolution="exact-vector",
                vector=vector,
            )
            final_evaluation = evaluation(
                cell,
                "3.0",
                snapshot_digest=snapshot.identity.digest,
                attempt=final_attempt,
            )
            coordinate = CoordinateSuccess(
                vector=vector,
                observations=(
                    ProbeObservation(
                        dependency="idna",
                        candidate_version="3.0",
                        vector=vector,
                        evidence=ProbePass(
                            attempt=final_attempt,
                            proposal_id=final_evaluation.proposal.proposal_id,
                            evaluation=final_evaluation,
                        ),
                    ),
                ),
                boundaries=(CoordinateBoundary(dependency="idna", floor="3.0"),),
                sweeps=1,
            )
            baseline_attempt = report_attempt(
                cell=cell,
                snapshot_digest=snapshot.identity.digest,
                resolution="highest",
                vector=vector,
            )
            baseline_evaluation = evaluation(
                cell,
                "3.11",
                snapshot_digest=snapshot.identity.digest,
                resolution="highest",
                attempt=baseline_attempt,
            )
            reports.append(
                PackageReportBuilder().build(
                    package=package,
                    source_snapshot=snapshot.identity,
                    cell_results=(
                        CellSuccess(
                            cell=cell,
                            baseline_attempt=baseline_attempt,
                            static_baseline=StaticBaseline(
                                proposal=baseline_evaluation.proposal,
                                ty=baseline_evaluation.static.ty,
                                digest=ty_diagnostic_digest(
                                    baseline_evaluation.static.ty.diagnostics
                                ),
                            ),
                            baseline=baseline_evaluation,
                            candidate_snapshots=candidate_snapshot(cell, vector),
                            search=coordinate,
                            final_vector=vector,
                            final_evaluation=final_evaluation,
                        ),
                    ),
                )
            )

        edits = ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply_many(
            reports=tuple(reports),
            root=tmp_path,
        )

        assert [edit.changed for edit in edits] == [True, True]
        for name in ("alpha", "beta"):
            with (tmp_path / "packages" / name / "pyproject.toml").open("rb") as stream:
                assert tomli.load(stream)["project"]["dependencies"] == ["idna<4,>=3.0"]

    def test_apply_unknown_recovery_schema_does_not_overwrite_user_files(
        self,
        tmp_path: Path,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        original = (
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna<4"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n"
        )
        pyproject.write_text(original, encoding="utf-8")
        journal = tmp_path / ".pf" / "apply-recovery.json"
        journal.parent.mkdir(parents=True)
        journal.write_text(
            '{"schema_version": 1, "state": "PROJECT_REPLACED"}\n', encoding="utf-8"
        )
        report = _single_package_report(tmp_path)
        before = pyproject.read_bytes()

        with pytest.raises(ConfigurationError, match="recovery"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(
                report=report, root=tmp_path
            )
        assert pyproject.read_bytes() == before

    def test_apply_rolls_back_a_target_digest_instead_of_committing(
        self,
        tmp_path: Path,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        original = (
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna<4"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n"
        )
        pyproject.write_text(original, encoding="utf-8")
        report = _single_package_report(tmp_path)
        editor = ProjectEditor(snapshots=SnapshotBuilder.without_processes())
        editor.apply(report=report, root=tmp_path)
        target = pyproject.read_bytes()
        backup = tmp_path / ".pf" / "apply-target.toml.backup"
        backup.write_bytes(original.encode())
        journal = tmp_path / ".pf" / "apply-recovery.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "state": "PROJECTS_REPLACED",
                    "files": [
                        {
                            "pyproject_path": "pyproject.toml",
                            "original_digest": hashlib.sha256(
                                original.encode()
                            ).hexdigest(),
                            "target_digest": hashlib.sha256(target).hexdigest(),
                            "backup_path": backup.relative_to(tmp_path).as_posix(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        class StopAfterRecover(SnapshotBuilder):
            def build(self, root: Path):  # type: ignore[override]
                raise ConfigurationError("stop after recover")

        with pytest.raises(ConfigurationError, match="stop after recover"):
            ProjectEditor(snapshots=StopAfterRecover.without_processes()).apply(
                report=report, root=tmp_path
            )
        assert pyproject.read_bytes() == original.encode()

    def test_apply_many_rolls_back_all_members_when_a_later_write_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
    [tool.uv.workspace]
    members = ["packages/*"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["python", "-c", "pass"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        originals: dict[str, bytes] = {}
        for name in ("alpha", "beta"):
            package_root = tmp_path / "packages" / name
            package_root.mkdir(parents=True)
            content = (
                f"""
    [project]
    name = "{name}"
    version = "0.1.0"
    dependencies = ["idna<4"]
    """.strip()
                + "\n"
            ).encode()
            (package_root / "pyproject.toml").write_bytes(content)
            originals[name] = content
        project = ProjectLoader().load(root=tmp_path, package_selection=None)
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        reports = tuple(
            _report_for_package(package, snapshot.identity)
            for package in project.packages
        )
        real_write = ProjectEditor._atomic_write

        def fail_second_project(path: Path, content: bytes) -> None:
            if path.name == "pyproject.toml" and path.parent.name == "beta":
                raise OSError("disk full")
            real_write(path, content)

        monkeypatch.setattr(
            ProjectEditor, "_atomic_write", staticmethod(fail_second_project)
        )
        with pytest.raises(OSError, match="disk full"):
            ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply_many(
                reports=reports,
                root=tmp_path,
            )
        for name, original in originals.items():
            assert (
                tmp_path / "packages" / name / "pyproject.toml"
            ).read_bytes() == original

    def test_apply_post_write_does_not_start_project_loader(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            """
    [project]
    name = "demo"
    version = "0.1.0"
    dependencies = ["idna<4"]

    [tool.pf]
    python = ["3.10"]
    platform = ["x86_64-unknown-linux-gnu"]
    test-command = ["pytest"]
    """.strip()
            + "\n",
            encoding="utf-8",
        )
        report = _single_package_report(tmp_path)

        def forbidden_load(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("apply must not call ProjectLoader.load")

        monkeypatch.setattr(ProjectLoader, "load", forbidden_load)
        ProjectEditor(snapshots=SnapshotBuilder.without_processes()).apply(report=report, root=tmp_path)
        assert "idna<4,>=3.0" in pyproject.read_text(encoding="utf-8")

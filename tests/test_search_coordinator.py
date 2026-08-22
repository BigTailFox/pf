from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, NoReturn, cast

import pytest

from pf.baseline import HighestVersionVerifier
from pf.coordinate_search import CoordinateSearch
from pf.environment import PreparedEnvironment
from pf.errors import InfrastructureError
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    BaselineRejection,
    HighestVersionPass,
    PassEvaluation,
    ProcessResult,
    PrepareFailure,
    SearchFailureEvent,
    StaticBaseline,
    StaticBaselineCapture,
    StaticFailEvaluation,
    StaticPassEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    ToolFailure,
    TyCheck,
    TyDiagnostic,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    PackagePlan,
    Proposal,
    SelectedCandidate,
    SourceIdentity,
    SourcePlan,
    VersionPin,
    candidate_snapshot_digest,
)
from pf.schemas.report import (
    CellIndeterminate,
    CellSuccess,
    ProbeRejection,
)
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder, SourceSnapshot


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def search_coordinator(
    *,
    environments: Any,
    candidates: Any,
    static: Any,
    full: Any,
    highest: Any | None = None,
    **kwargs: Any,
) -> SearchCoordinator:
    return SearchCoordinator(
        environments=environments,
        candidates=candidates,
        static=static,
        full=full,
        highest=highest
        or HighestVersionVerifier(
            environments=environments,
            static=static,
            full=full,
        ),
        coordinate_search=CoordinateSearch(),
        **kwargs,
    )


class RecordingDiagnostics:
    def __init__(self) -> None:
        self.events: list[SearchFailureEvent] = []

    def consume(self, event: SearchFailureEvent) -> None:
        self.events.append(event)


class ProposalFactory:
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: str,
        managed_vector: tuple[VersionPin, ...] | None = None,
        selection: tuple[SelectedCandidate, ...] | None = None,
    ) -> PreparedEnvironment:
        selected_vector = (
            tuple(
                VersionPin(name=item.dependency, version=item.version)
                for item in selection
            )
            if selection is not None
            else None
        )
        vector = (
            selected_vector or managed_vector or (VersionPin(name="a", version="3"),)
        )
        exact = selection is not None or managed_vector is not None
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution=("exact-vector" if exact else "highest"),
                requested_managed_vector=(vector if exact else None),
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity="sources",
                evaluation_policy_identity="policy",
            )
        )
        proposal = Proposal(
            proposal_id=";".join(f"{pin.name}={pin.version}" for pin in vector),
            attempt_id=attempt.attempt_id,
            snapshot_digest=snapshot.identity.digest,
            cell=cell,
            managed_vector=vector,
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        temporary = tempfile.TemporaryDirectory(prefix="pf-test-proposal-")
        root = Path(temporary.name)
        return PreparedEnvironment(
            attempt=attempt,
            proposal=proposal,
            proposal_root=root,
            package_root=root,
            environment_root=root / "environment",
            interpreter=root / "environment" / "bin" / "python",
            temporary_directory=temporary,
        )


class CountingProposalFactory(ProposalFactory):
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.prepare_vectors: list[tuple[VersionPin, ...]] = []

    def prepare(self, **kwargs: Any) -> PreparedEnvironment:
        prepared = super().prepare(**kwargs)
        self.prepare_vectors.append(prepared.proposal.managed_vector)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        original_close = prepared.close
        closed = False

        def close() -> None:
            nonlocal closed
            if not closed:
                closed = True
                self.active -= 1
            original_close()

        cast(Any, prepared).close = close
        return prepared


class StaticPasses:
    def __init__(self) -> None:
        self.captures = 0
        self.baseline_digests: list[str] = []

    @staticmethod
    def diagnostic() -> TyDiagnostic:
        return TyDiagnostic(
            identity="snapshot|source.py|1|1|existing-error",
            origin="snapshot",
            path="source.py",
            line=1,
            column=1,
            code="existing-error",
            severity="major",
            message="existing project error",
        )

    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture:
        self.captures += 1
        check = TyCheck(
            process=successful_process().model_copy(update={"exit_code": 1}),
            diagnostics=(self.diagnostic(),),
        )
        baseline = StaticBaseline(
            proposal=prepared.proposal,
            ty=check,
            digest=ty_diagnostic_digest(check.diagnostics),
        )
        return StaticBaselineCapture(
            baseline=baseline,
            static=StaticPassEvaluation(
                proposal=prepared.proposal,
                ty=check,
                baseline_digest=baseline.digest,
                incremental=(),
            ),
        )

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
    ) -> StaticPassEvaluation | StaticFailEvaluation:
        self.baseline_digests.append(baseline.digest)
        return StaticPassEvaluation(
            proposal=prepared.proposal,
            ty=TyCheck(
                process=successful_process().model_copy(update={"exit_code": 1}),
                diagnostics=(self.diagnostic(),),
            ),
            baseline_digest=baseline.digest,
            incremental=(),
        )


class FullPasses:
    def __init__(self, static: StaticPasses) -> None:
        self.static = static

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: object | None = None,
    ) -> PassEvaluation | StaticFailEvaluation | TestFailEvaluation:
        static = (
            static_result
            if isinstance(static_result, StaticPassEvaluation)
            else self.static.evaluate(prepared, package=package, baseline=baseline)
        )
        if isinstance(static, StaticFailEvaluation):
            return static
        prepared.mark_tested()
        return PassEvaluation(
            proposal=prepared.proposal,
            static=static,
            test=TestPass(process=successful_process()),
        )


class FullThreshold:
    def __init__(self, static: StaticPasses) -> None:
        self.static = static

    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: object | None = None,
    ) -> PassEvaluation | StaticFailEvaluation | TestFailEvaluation:
        static = (
            static_result
            if isinstance(static_result, StaticPassEvaluation)
            else self.static.evaluate(prepared, package=package, baseline=baseline)
        )
        if isinstance(static, StaticFailEvaluation):
            return static
        prepared.mark_tested()
        if int(prepared.proposal.managed_vector[0].version) >= 2:
            return PassEvaluation(
                proposal=prepared.proposal,
                static=static,
                test=TestPass(process=successful_process()),
            )
        return TestFailEvaluation(
            proposal=prepared.proposal,
            static=static,
            test=TestFail(
                process=successful_process().model_copy(update={"exit_code": 1})
            ),
        )


class FrozenCandidates:
    def build(self, **kwargs: Any) -> tuple[CandidateSnapshot, ...]:
        cell = cast(Cell, kwargs["cell"])
        candidates = tuple(
            Candidate(
                version=version,
                series_key=version,
                artifact=AvailableArtifact(
                    filename=f"a-{version}-py3-none-any.whl",
                    kind="wheel",
                    content_hash=f"sha256:{version * 64}",
                    locator=f"https://files.example/a-{version}-py3-none-any.whl",
                    python_minors=("3.10",),
                    targets=("x86_64-unknown-linux-gnu",),
                ),
            )
            for version in ("1", "2", "3")
        )
        source = SourceIdentity(kind="registry")
        representatives = tuple(
            (candidate.series_key, candidate.version) for candidate in candidates
        )
        return (
            CandidateSnapshot(
                dependency="a",
                cell=cell,
                policy_identity="policy",
                source=source,
                candidates=candidates,
                series_representatives=representatives,
                digest=candidate_snapshot_digest(
                    dependency="a",
                    cell=cell,
                    policy_identity="policy",
                    source=source,
                    candidates=candidates,
                    series_representatives=representatives,
                ),
            ),
        )


class TestSearchCoordinator:
    def test_search_coordinator_requires_both_search_strategies(self) -> None:
        environments = ProposalFactory()
        candidates = FrozenCandidates()
        static = StaticPasses()
        full = FullPasses(static)
        highest = HighestVersionVerifier(
            environments=environments,
            static=static,
            full=full,
        )
        with pytest.raises(TypeError, match="highest"):
            SearchCoordinator(  # ty: ignore[missing-argument]
                environments=environments,
                candidates=candidates,
                static=static,
                full=full,
                coordinate_search=CoordinateSearch(),
            )
        with pytest.raises(TypeError, match="coordinate_search"):
            SearchCoordinator(  # ty: ignore[missing-argument]
                environments=environments,
                candidates=candidates,
                static=static,
                full=full,
                highest=highest,
            )

    def test_search_coordinator_returns_static_fast_path_with_full_evidence(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        static = StaticPasses()
        coordinator = search_coordinator(
            environments=ProposalFactory(),
            candidates=FrozenCandidates(),
            static=static,
            full=FullPasses(static),
        )

        result = coordinator.search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)
        assert result.status == "SUCCESS"
        assert result.final_vector == (VersionPin(name="a", version="1"),)
        assert result.dynamic_search is None
        assert result.final_evaluation.status == "PASS"
        assert result.static_baseline.proposal == result.baseline.proposal
        assert len(result.static_baseline.diagnostics) == 1
        assert result.static_baseline.digest == ty_diagnostic_digest(
            result.static_baseline.diagnostics
        )
        assert static.captures == 1

    def test_search_closes_static_pass_before_repreparing_for_full_evaluation(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        environments = CountingProposalFactory()
        static = StaticPasses()

        result = search_coordinator(
            environments=environments,
            candidates=FrozenCandidates(),
            static=static,
            full=FullPasses(static),
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)
        final_prepares = [
            vector
            for vector in environments.prepare_vectors
            if vector == result.final_vector
        ]
        assert len(final_prepares) == 2
        assert environments.maximum_active == 1
        assert environments.active == 0
        assert set(static.baseline_digests) == {
            ty_diagnostic_digest(result.static_baseline.diagnostics)
        }

    def test_search_coordinator_maps_every_probe_to_its_frozen_artifact(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )

        class SelectionFactory(ProposalFactory):
            def __init__(self) -> None:
                self.selections: list[tuple[SelectedCandidate, ...]] = []

            def prepare(self, **kwargs: Any) -> PreparedEnvironment:
                selection = kwargs.get("selection")
                if selection is not None:
                    self.selections.append(
                        cast(tuple[SelectedCandidate, ...], selection)
                    )
                return super().prepare(**kwargs)

        environments = SelectionFactory()
        static = StaticPasses()

        result = search_coordinator(
            environments=environments,
            candidates=FrozenCandidates(),
            static=static,
            full=FullPasses(static),
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)
        assert environments.selections
        for selection in environments.selections:
            assert len(selection) == 1
            selected = selection[0]
            assert selected.artifact.locator == (
                f"https://files.example/a-{selected.version}-py3-none-any.whl"
            )
            assert selected.artifact.content_hash == f"sha256:{selected.version * 64}"

    def test_search_coordinator_never_requests_lowest_direct(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        resolutions: list[str] = []

        class SpyFactory(ProposalFactory):
            def prepare(self, **kwargs: Any) -> PreparedEnvironment:
                resolutions.append(str(kwargs["resolution"]))
                return super().prepare(**kwargs)

        static = StaticPasses()
        result = search_coordinator(
            environments=SpyFactory(),
            candidates=FrozenCandidates(),
            static=static,
            full=FullPasses(static),
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)
        assert "lowest-direct" not in resolutions
        assert result.baseline_attempt.identity.requested_resolution != "lowest-direct"
        for observation in result.static_search.observations:
            assert (
                observation.evidence.attempt.identity.requested_resolution
                != "lowest-direct"
            )

    def test_search_coordinator_consumes_shared_highest_version_verification(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        static = StaticPasses()
        prepared = ProposalFactory().prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="highest",
        )
        capture = static.capture(prepared, package=package)
        baseline_evaluation = FullPasses(static).evaluate(
            prepared,
            package=package,
            baseline=capture.baseline,
            static_result=capture.static,
        )
        assert isinstance(baseline_evaluation, PassEvaluation)
        baseline_attempt = prepared.attempt
        assert baseline_attempt is not None

        class Highest:
            def verify(self, **kwargs: object) -> HighestVersionPass:
                return HighestVersionPass(
                    attempt=baseline_attempt,
                    baseline=capture.baseline,
                    evaluation=baseline_evaluation,
                )

        class CandidateOnlyEnvironments(ProposalFactory):
            def prepare(self, **kwargs: Any) -> PreparedEnvironment:
                assert kwargs.get("selection") is not None
                return super().prepare(**kwargs)

        result = search_coordinator(
            environments=CandidateOnlyEnvironments(),
            candidates=FrozenCandidates(),
            static=static,
            full=FullPasses(static),
            highest=Highest(),
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)

    def test_search_report_evidence_keeps_static_fail_incremental_diagnostics(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )

        class StaticThreshold(StaticPasses):
            def evaluate(
                self,
                prepared: PreparedEnvironment,
                *,
                package: PackagePlan,
                baseline: StaticBaseline,
            ) -> StaticPassEvaluation | StaticFailEvaluation:
                if prepared.proposal.managed_vector[0].version != "1":
                    return super().evaluate(
                        prepared,
                        package=package,
                        baseline=baseline,
                    )
                increment = TyDiagnostic(
                    identity="snapshot|source.py|2|1|dependency-regression",
                    origin="snapshot",
                    path="source.py",
                    line=2,
                    column=1,
                    code="dependency-regression",
                    severity="major",
                    message="dependency API is unavailable",
                )
                return StaticFailEvaluation(
                    proposal=prepared.proposal,
                    ty=TyCheck(
                        process=successful_process().model_copy(
                            update={"exit_code": 1}
                        ),
                        diagnostics=(*baseline.diagnostics, increment),
                    ),
                    baseline_digest=baseline.digest,
                    incremental=(increment,),
                )

        static = StaticThreshold()
        diagnostics = RecordingDiagnostics()
        result = search_coordinator(
            environments=ProposalFactory(),
            candidates=FrozenCandidates(),
            static=static,
            full=FullPasses(static),
            diagnostics=diagnostics,
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)
        failure = next(
            observation.evidence
            for observation in result.static_search.observations
            if isinstance(observation.evidence, ProbeRejection)
            and observation.evidence.cause == "STATIC_REGRESSION"
        )
        assert isinstance(failure.evaluation, StaticFailEvaluation)
        assert failure.evaluation.incremental[0].code == "dependency-regression"
        assert diagnostics.events[0].failure.failure_id == failure.failure_id

    def test_search_coordinator_falls_back_to_dynamic_search_after_joint_test_failure(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        static = StaticPasses()
        diagnostics = RecordingDiagnostics()
        coordinator = search_coordinator(
            environments=ProposalFactory(),
            candidates=FrozenCandidates(),
            static=static,
            full=FullThreshold(static),
            diagnostics=diagnostics,
        )

        result = coordinator.search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)
        assert result.status == "SUCCESS"
        assert result.final_vector == (VersionPin(name="a", version="2"),)
        assert result.dynamic_search is not None
        assert result.dynamic_search.boundaries[0].predecessor == "1"
        assert any(
            event.failure.cause == "TEST_FAILURE" for event in diagnostics.events
        )

    def test_search_coordinator_records_candidate_source_failure_as_non_evidence(
        self,
        tmp_path: Path,
    ) -> None:
        class UnavailableCandidates:
            def build(self, **kwargs: Any) -> tuple[CandidateSnapshot, ...]:
                raise InfrastructureError("index unavailable")

        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        static = StaticPasses()
        coordinator = search_coordinator(
            environments=ProposalFactory(),
            candidates=UnavailableCandidates(),
            static=static,
            full=FullPasses(static),
        )

        result = coordinator.search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellIndeterminate)
        assert result.status == "CELL_INDETERMINATE"
        assert result.phase == "candidate-discovery"
        assert result.failure_records[0].cause == "SOURCE_FAILURE"
        assert result.failure_records[0].scope.kind == "cell"

    def test_search_coordinator_retains_candidate_source_failure_detail(
        self,
        tmp_path: Path,
    ) -> None:
        class UnavailableCandidates:
            def build(self, **kwargs: Any) -> tuple[CandidateSnapshot, ...]:
                raise InfrastructureError(
                    "index unavailable",
                    detail="DNS lookup for packages.example failed",
                )

        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        static = StaticPasses()

        result = search_coordinator(
            environments=ProposalFactory(),
            candidates=UnavailableCandidates(),
            static=static,
            full=FullPasses(static),
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellIndeterminate)
        detail = result.failure_records[0].detail
        assert detail is not None
        assert detail.code == "candidate-discovery-failed"
        assert detail.message == "candidate discovery failed"
        assert "packages.example" not in detail.message

    def test_search_coordinator_keeps_prepare_failure_for_cli_diagnostics(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        failure = ToolFailure(
            cause="HARNESS_CONFLICT",
            stage="install-harness",
            process=ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout="",
                stderr="No solution found",
            ),
        )
        attempt = Attempt.from_identity(
            AttemptIdentity(
                source_snapshot_digest=snapshot.identity.digest,
                cell=cell,
                requested_resolution="highest",
                requested_managed_vector=None,
                active_declaration_ids=cell.active_declaration_ids,
                source_plan_identity="sources",
                evaluation_policy_identity="policy",
            )
        )

        class UnresolvableEnvironments:
            def prepare(self, **kwargs: Any) -> PrepareFailure:
                return PrepareFailure(attempt=attempt, failure=failure)

        class NeverEvaluate:
            def capture(self, *args: object, **kwargs: object) -> NoReturn:
                raise AssertionError("prepare failure must not evaluate")

            def evaluate(self, *args: object, **kwargs: object) -> NoReturn:
                raise AssertionError("prepare failure must not evaluate")

        result = search_coordinator(
            environments=UnresolvableEnvironments(),
            candidates=FrozenCandidates(),
            static=NeverEvaluate(),
            full=NeverEvaluate(),
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, BaselineRejection)
        assert result.status == "BASELINE_REJECTION"
        assert result.failure.cause == "HARNESS_CONFLICT"
        assert result.failure.process == failure.process

    def test_search_coordinator_emits_candidate_prepare_failure_diagnostic(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        static = StaticPasses()
        prepared = ProposalFactory().prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="highest",
        )
        capture = static.capture(prepared, package=package)
        baseline = FullPasses(static).evaluate(
            prepared,
            package=package,
            baseline=capture.baseline,
            static_result=capture.static,
        )
        assert isinstance(baseline, PassEvaluation)
        baseline_attempt = prepared.attempt
        assert baseline_attempt is not None
        failure = ToolFailure(
            cause="RESOLUTION_CONFLICT",
            stage="install-project",
            process=successful_process().model_copy(
                update={"exit_code": 1, "stderr": "No solution found"}
            ),
        )

        class Highest:
            def verify(self, **kwargs: object) -> HighestVersionPass:
                return HighestVersionPass(
                    attempt=baseline_attempt,
                    baseline=capture.baseline,
                    evaluation=baseline,
                )

        class CandidateFailure:
            def prepare(self, **kwargs: Any) -> PreparedEnvironment | PrepareFailure:
                selection = cast(tuple[SelectedCandidate, ...], kwargs["selection"])
                vector = tuple(
                    VersionPin(name=item.dependency, version=item.version)
                    for item in selection
                )
                if vector[0].version != "1":
                    return ProposalFactory().prepare(**kwargs)
                attempt = Attempt.from_identity(
                    AttemptIdentity(
                        source_snapshot_digest=snapshot.identity.digest,
                        cell=cell,
                        requested_resolution="exact-vector",
                        requested_managed_vector=vector,
                        active_declaration_ids=cell.active_declaration_ids,
                        source_plan_identity="sources",
                        evaluation_policy_identity="policy",
                    )
                )
                return PrepareFailure(attempt=attempt, failure=failure)

        recorder = RecordingDiagnostics()
        result = search_coordinator(
            environments=CandidateFailure(),
            candidates=FrozenCandidates(),
            static=static,
            full=FullPasses(static),
            highest=Highest(),
            diagnostics=recorder,
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert isinstance(result, CellSuccess)
        assert result.final_vector == (VersionPin(name="a", version="2"),)
        rejection = result.failure_records[0]
        assert rejection.cause == "RESOLUTION_CONFLICT"
        assert rejection.disposition == "REJECTED"
        assert result.static_search.boundaries[0].predecessor_failure_id == (
            rejection.failure_id
        )
        assert recorder.events == [SearchFailureEvent(cell=cell, failure=rejection)]

    def test_search_coordinator_reuses_a_prepare_failure_for_the_same_attempt(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        snapshot = SnapshotBuilder().build(tmp_path)
        cell = Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.10",
            extra_surface=(),
        )
        package = PackagePlan(
            name="demo",
            pyproject_path="pyproject.toml",
            config=EffectiveConfig(test_command=("python", "-m", "unittest")),
            declarations=(),
            cells=(cell,),
            source_plan=SourcePlan(identities=(SourceIdentity(kind="registry"),)),
            test_group_present=True,
        )
        static = StaticPasses()
        highest_prepared = ProposalFactory().prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="highest",
        )
        capture = static.capture(highest_prepared, package=package)
        baseline = FullPasses(static).evaluate(
            highest_prepared,
            package=package,
            baseline=capture.baseline,
            static_result=capture.static,
        )
        assert isinstance(baseline, PassEvaluation)
        baseline_attempt = highest_prepared.attempt
        assert baseline_attempt is not None

        class Highest:
            def verify(self, **kwargs: object) -> HighestVersionPass:
                return HighestVersionPass(
                    attempt=baseline_attempt,
                    baseline=capture.baseline,
                    evaluation=baseline,
                )

        class FailsFirstPrepare:
            def __init__(self) -> None:
                self.version_one_calls = 0

            def prepare(self, **kwargs: Any) -> PreparedEnvironment | PrepareFailure:
                selection = cast(tuple[SelectedCandidate, ...], kwargs["selection"])
                vector = tuple(
                    VersionPin(name=item.dependency, version=item.version)
                    for item in selection
                )
                if vector[0].version != "1":
                    return ProposalFactory().prepare(**kwargs)
                self.version_one_calls += 1
                if self.version_one_calls > 1:
                    return ProposalFactory().prepare(**kwargs)
                attempt = Attempt.from_identity(
                    AttemptIdentity(
                        source_snapshot_digest=snapshot.identity.digest,
                        cell=cell,
                        requested_resolution="exact-vector",
                        requested_managed_vector=vector,
                        active_declaration_ids=cell.active_declaration_ids,
                        source_plan_identity="sources",
                        evaluation_policy_identity="policy",
                    )
                )
                return PrepareFailure(
                    attempt=attempt,
                    failure=ToolFailure(
                        cause="RESOLUTION_CONFLICT",
                        stage="install-project",
                        process=successful_process().model_copy(
                            update={"exit_code": 1}
                        ),
                    ),
                )

        environments = FailsFirstPrepare()
        result = search_coordinator(
            environments=environments,
            candidates=FrozenCandidates(),
            static=static,
            full=FullThreshold(static),
            highest=Highest(),
        ).search(package=package, cell=cell, snapshot=snapshot)

        assert environments.version_one_calls == 1
        assert isinstance(result, CellSuccess)
        assert all(
            observation.evidence.status != "PASS"
            for search in (result.static_search, result.dynamic_search)
            if search is not None
            for observation in search.observations
            if observation.vector == (VersionPin(name="a", version="1"),)
        )

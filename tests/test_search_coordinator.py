from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, NoReturn, cast

from pf.environment import PreparedEnvironment
from pf.errors import InfrastructureError
from pf.schemas.config import EffectiveConfig
from pf.schemas.evaluation import (
    HighestVersionVerification,
    PassEvaluation,
    ProcessResult,
    SearchDiagnosticEvent,
    SearchDynamicDiagnosticEvent,
    SearchStaticDiagnosticEvent,
    SearchToolDiagnosticEvent,
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
    SourceIdentity,
    SourcePlan,
    VersionPin,
)
from pf.schemas.report import CellFailure, CellSuccess
from pf.search import SearchCoordinator
from pf.snapshot import SnapshotBuilder


def successful_process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout_summary="",
        stderr_summary="",
        stdout_tail="",
        stderr_tail="",
    )


class RecordingDiagnostics:
    def __init__(self) -> None:
        self.events: list[SearchDiagnosticEvent] = []

    def consume(self, event: SearchDiagnosticEvent) -> None:
        self.events.append(event)


class ProposalFactory:
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: object,
        resolution: str,
        managed_vector: tuple[VersionPin, ...] | None = None,
    ) -> PreparedEnvironment:
        vector = managed_vector or (VersionPin(name="a", version="3"),)
        proposal = Proposal(
            proposal_id=";".join(f"{pin.name}={pin.version}" for pin in vector),
            snapshot_digest="snapshot",
            cell=cell,
            managed_vector=vector,
            fixed_declaration_ids=(),
            resolved_graph=(),
            policy_identity="policy",
        )
        temporary = tempfile.TemporaryDirectory(prefix="pf-test-proposal-")
        root = Path(temporary.name)
        return PreparedEnvironment(
            proposal=proposal,
            proposal_root=root,
            package_root=root,
            environment_root=root / "environment",
            interpreter=root / "environment" / "bin" / "python",
            temporary_directory=temporary,
        )


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
            test=TestFail(process=successful_process()),
        )


class FrozenCandidates:
    def build(self, **kwargs: Any) -> tuple[CandidateSnapshot, ...]:
        cell = cast(Cell, kwargs["cell"])
        artifact = AvailableArtifact(
            filename="a.whl",
            kind="wheel",
            content_hash="sha256:a",
            python_minors=("3.10",),
            targets=("x86_64-unknown-linux-gnu",),
        )
        candidates = tuple(
            Candidate(version=version, series_key=version, artifact=artifact)
            for version in ("1", "2", "3")
        )
        return (
            CandidateSnapshot(
                dependency="a",
                cell=cell,
                policy_identity="policy",
                source=SourceIdentity(kind="registry"),
                candidates=candidates,
                series_representatives=tuple(
                    (candidate.series_key, candidate.version)
                    for candidate in candidates
                ),
                digest="digest",
            ),
        )


def test_search_coordinator_returns_static_fast_path_with_full_evidence(
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
    coordinator = SearchCoordinator(
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
    assert set(static.baseline_digests) == {
        ty_diagnostic_digest(result.static_baseline.diagnostics)
    }


def test_search_coordinator_consumes_shared_highest_version_verification(
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

    class Highest:
        def verify(self, **kwargs: object) -> HighestVersionVerification:
            return HighestVersionVerification(
                baseline=capture.baseline,
                evaluation=baseline_evaluation,
            )

    class CandidateOnlyEnvironments(ProposalFactory):
        def prepare(self, **kwargs: Any) -> PreparedEnvironment:
            assert kwargs.get("managed_vector") is not None
            return super().prepare(**kwargs)

    result = SearchCoordinator(
        environments=CandidateOnlyEnvironments(),
        candidates=FrozenCandidates(),
        static=static,
        full=FullPasses(static),
        highest=Highest(),
    ).search(package=package, cell=cell, snapshot=snapshot)

    assert isinstance(result, CellSuccess)


def test_search_report_evidence_keeps_static_fail_incremental_diagnostics(
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
                    process=successful_process().model_copy(update={"exit_code": 1}),
                    diagnostics=(*baseline.diagnostics, increment),
                ),
                baseline_digest=baseline.digest,
                incremental=(increment,),
            )

    static = StaticThreshold()
    diagnostics = RecordingDiagnostics()
    result = SearchCoordinator(
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
        if observation.evidence.status == "STATIC_FAIL"
    )
    assert isinstance(failure.static, StaticFailEvaluation)
    assert failure.static.incremental[0].code == "dependency-regression"
    assert any(
        isinstance(event, SearchStaticDiagnosticEvent)
        for event in diagnostics.events
    )


def test_search_coordinator_falls_back_to_dynamic_search_after_joint_test_failure(
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
    coordinator = SearchCoordinator(
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
        isinstance(event, SearchDynamicDiagnosticEvent)
        for event in diagnostics.events
    )


def test_search_coordinator_records_candidate_source_failure_as_non_evidence(
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
    coordinator = SearchCoordinator(
        environments=ProposalFactory(),
        candidates=UnavailableCandidates(),
        static=static,
        full=FullPasses(static),
    )

    result = coordinator.search(package=package, cell=cell, snapshot=snapshot)

    assert isinstance(result, CellFailure)
    assert result.status == "SOURCE_ERROR"
    assert result.phase == "candidate-discovery"
    assert result.detail == "index unavailable"


def test_search_coordinator_keeps_prepare_failure_for_cli_diagnostics(
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
        status="UNRESOLVABLE",
        stage="install-harness",
        process=ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout_summary="",
            stderr_summary="No solution found",
            stdout_tail="",
            stderr_tail="No solution found",
        ),
    )

    class UnresolvableEnvironments:
        def prepare(self, **kwargs: Any) -> ToolFailure:
            return failure

    class NeverEvaluate:
        def capture(self, *args: object, **kwargs: object) -> NoReturn:
            raise AssertionError("prepare failure must not evaluate")

        def evaluate(self, *args: object, **kwargs: object) -> NoReturn:
            raise AssertionError("prepare failure must not evaluate")

    result = SearchCoordinator(
        environments=UnresolvableEnvironments(),
        candidates=FrozenCandidates(),
        static=NeverEvaluate(),
        full=NeverEvaluate(),
    ).search(package=package, cell=cell, snapshot=snapshot)

    assert isinstance(result, CellFailure)
    assert result.status == "UNRESOLVABLE"
    assert result.phase == "baseline-prepare"
    assert result.failure == failure


def test_search_coordinator_emits_candidate_prepare_failure_diagnostic(
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
    failure = ToolFailure(
        status="UNRESOLVABLE",
        stage="install",
        process=successful_process().model_copy(
            update={"exit_code": 1, "stderr_summary": "No solution found"}
        ),
    )

    class Highest:
        def verify(self, **kwargs: object) -> HighestVersionVerification:
            return HighestVersionVerification(
                baseline=capture.baseline,
                evaluation=baseline,
            )

    class CandidateFailure:
        def prepare(self, **kwargs: Any) -> ToolFailure:
            assert kwargs.get("managed_vector") is not None
            return failure

    recorder = RecordingDiagnostics()
    result = SearchCoordinator(
        environments=CandidateFailure(),
        candidates=FrozenCandidates(),
        static=static,
        full=FullPasses(static),
        highest=Highest(),
        diagnostics=recorder,
    ).search(package=package, cell=cell, snapshot=snapshot)

    assert isinstance(result, CellFailure)
    assert result.status == "UNRESOLVABLE"
    assert recorder.events == [
        SearchToolDiagnosticEvent(cell=cell, outcome=failure)
    ]

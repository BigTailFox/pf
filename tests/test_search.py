from __future__ import annotations

from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    Proposal,
    SourceIdentity,
    VersionPin,
)
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    ProcessResult,
    StaticPassEvaluation,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.report import (
    CoordinateFailure,
    CoordinateSuccess,
    ProbeEvidence,
    ProbeIndeterminate,
    ProbePass,
    ProbeRejection,
)
from pf.search import CoordinateSearch


def snapshot(name: str) -> CandidateSnapshot:
    cell = Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    )
    artifact = AvailableArtifact(
        filename=f"{name}.whl",
        kind="wheel",
        content_hash=f"sha256:{name}",
        python_minors=("3.10",),
        targets=("x86_64-unknown-linux-gnu",),
    )
    candidates = tuple(
        Candidate(version=version, series_key=version, artifact=artifact)
        for version in ("1", "2", "3")
    )
    return CandidateSnapshot(
        dependency=name,
        cell=cell,
        policy_identity="policy",
        source=SourceIdentity(kind="registry"),
        candidates=candidates,
        series_representatives=tuple(
            (candidate.series_key, candidate.version) for candidate in candidates
        ),
        digest=f"digest-{name}",
    )


def wide_snapshot(name: str) -> CandidateSnapshot:
    base = snapshot(name)
    artifact = base.candidates[0].artifact
    candidates = tuple(
        Candidate(version=str(version), series_key=str(version), artifact=artifact)
        for version in range(1, 10)
    )
    return CandidateSnapshot(
        dependency=name,
        cell=base.cell,
        policy_identity=base.policy_identity,
        source=base.source,
        candidates=candidates,
        series_representatives=tuple(
            (candidate.series_key, candidate.version) for candidate in candidates
        ),
        digest="wide-digest",
    )


def probe_attempt(vector: tuple[VersionPin, ...]) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest="snapshot",
            cell=snapshot(vector[0].name).cell,
            requested_resolution="exact-vector",
            requested_managed_vector=vector,
            active_declaration_ids=(),
            source_plan_identity="sources",
            evaluation_policy_identity="policy",
        )
    )


def probe_pass(vector: tuple[VersionPin, ...], proposal_id: str) -> ProbePass:
    attempt = probe_attempt(vector)
    proposal = Proposal(
        proposal_id=proposal_id,
        attempt_id=attempt.attempt_id,
        snapshot_digest="snapshot",
        cell=attempt.identity.cell,
        managed_vector=vector,
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity="policy",
    )
    static = StaticPassEvaluation(
        proposal=proposal,
        ty=TyCheck(
            process=ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0,
                stdout_summary="",
                stderr_summary="",
                stdout_tail="",
                stderr_tail="",
            ),
            diagnostics=(),
        ),
        baseline_digest=ty_diagnostic_digest(()),
    )
    return ProbePass(attempt=attempt, proposal_id=proposal_id, evaluation=static)


class InteractionEvaluator:
    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        versions = {pin.name: int(pin.version) for pin in vector}
        passes = (
            versions["a"] >= 2 if versions["b"] >= 2 else versions["a"] >= 1
        ) and versions["b"] >= 1
        identity = ";".join(f"{pin.name}={pin.version}" for pin in vector)
        if passes:
            return probe_pass(vector, identity)
        return ProbeRejection(
            attempt=probe_attempt(vector),
            failure_id=f"failure-{identity}",
            cause="RESOLUTION_CONFLICT",
        )


def test_coordinate_search_repeats_sweeps_until_the_final_context_is_minimal() -> None:
    result = CoordinateSearch(small_threshold=4).minimize(
        start=(VersionPin(name="a", version="3"), VersionPin(name="b", version="3")),
        candidates=(snapshot("a"), snapshot("b")),
        evaluator=InteractionEvaluator(),
    )

    assert isinstance(result, CoordinateSuccess)
    assert result.status == "SUCCESS"
    assert [(pin.name, pin.version) for pin in result.vector] == [
        ("a", "1"),
        ("b", "1"),
    ]
    assert [
        (boundary.dependency, boundary.floor) for boundary in result.boundaries
    ] == [
        ("a", "1"),
        ("b", "1"),
    ]
    assert result.sweeps == 3


def test_coordinate_search_uses_hint_then_lower_bound_binary_search() -> None:
    class ThresholdEvaluator:
        def __init__(self) -> None:
            self.probed: list[str] = []

        def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
            version = vector[0].version
            self.probed.append(version)
            if int(version) >= 4:
                return probe_pass(vector, version)
            return ProbeRejection(
                attempt=probe_attempt(vector),
                failure_id=f"failure-{version}",
                cause="RESOLUTION_CONFLICT",
            )

    evaluator = ThresholdEvaluator()
    result = CoordinateSearch(small_threshold=2).minimize(
        start=(VersionPin(name="a", version="9"),),
        candidates=(wide_snapshot("a"),),
        evaluator=evaluator,
        hints=(VersionPin(name="a", version="5"),),
    )

    assert isinstance(result, CoordinateSuccess)
    assert result.status == "SUCCESS"
    assert result.vector[0].version == "4"
    assert result.boundaries[0].predecessor == "3"
    assert result.boundaries[0].predecessor_failure_id == "failure-3"
    assert evaluator.probed[:5] == ["9", "5", "1", "3", "4"]


def test_coordinate_search_does_not_use_an_out_of_space_baseline_as_floor() -> None:
    class BaselineOnlyPasses:
        def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
            version = vector[0].version
            if version == "4":
                return probe_pass(vector, version)
            return ProbeRejection(
                attempt=probe_attempt(vector),
                failure_id=f"failure-{version}",
                cause="RESOLUTION_CONFLICT",
            )

    result = CoordinateSearch(small_threshold=4).minimize(
        start=(VersionPin(name="a", version="4"),),
        candidates=(snapshot("a"),),
        evaluator=BaselineOnlyPasses(),
    )

    assert isinstance(result, CoordinateFailure)
    assert result.status == "NO_PASS_IN_SEARCH_SPACE"


def test_coordinate_search_binary_searches_below_a_virtual_pass_sentinel() -> None:
    class ThresholdEvaluator:
        def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
            version = int(vector[0].version)
            identity = str(version)
            if version >= 8:
                return probe_pass(vector, identity)
            return ProbeRejection(
                attempt=probe_attempt(vector),
                failure_id=f"failure-{identity}",
                cause="RESOLUTION_CONFLICT",
            )

    result = CoordinateSearch(small_threshold=2).minimize(
        start=(VersionPin(name="a", version="10"),),
        candidates=(wide_snapshot("a"),),
        evaluator=ThresholdEvaluator(),
    )

    assert isinstance(result, CoordinateSuccess)
    assert result.vector[0].version == "8"
    assert result.boundaries[0].predecessor == "7"


def test_coordinate_search_never_returns_a_virtual_pass_sentinel() -> None:
    class BaselineOnlyPasses:
        def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
            version = vector[0].version
            if version == "10":
                return probe_pass(vector, version)
            return ProbeRejection(
                attempt=probe_attempt(vector),
                failure_id=f"failure-{version}",
                cause="RESOLUTION_CONFLICT",
            )

    result = CoordinateSearch(small_threshold=2).minimize(
        start=(VersionPin(name="a", version="10"),),
        candidates=(wide_snapshot("a"),),
        evaluator=BaselineOnlyPasses(),
    )

    assert isinstance(result, CoordinateFailure)
    assert result.status == "NO_PASS_IN_SEARCH_SPACE"


def test_coordinate_search_stops_at_an_indeterminate_probe() -> None:
    class UnknownAtLowest:
        def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
            version = vector[0].version
            if version == "1":
                return ProbeIndeterminate(
                    attempt=probe_attempt(vector),
                    failure_id="failure-timeout",
                    cause="TIMEOUT",
                )
            return probe_pass(vector, version)

    result = CoordinateSearch().minimize(
        start=(VersionPin(name="a", version="3"),),
        candidates=(snapshot("a"),),
        evaluator=UnknownAtLowest(),
    )

    assert isinstance(result, CoordinateFailure)
    assert result.status == "INDETERMINATE"
    assert result.failure_id == "failure-timeout"

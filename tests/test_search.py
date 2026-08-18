from __future__ import annotations

from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    SourceIdentity,
    VersionPin,
)
from pf.schemas.report import CoordinateFailure, CoordinateSuccess, ProbeEvidence
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


class InteractionEvaluator:
    def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
        versions = {pin.name: int(pin.version) for pin in vector}
        passes = (
            versions["a"] >= 2 if versions["b"] >= 2 else versions["a"] >= 1
        ) and versions["b"] >= 1
        return ProbeEvidence(
            status="PASS" if passes else "TEST_FAIL",
            proposal_id=";".join(f"{pin.name}={pin.version}" for pin in vector),
        )


def test_coordinate_search_repeats_sweeps_until_the_final_context_is_minimal() -> None:
    result = CoordinateSearch(small_threshold=4).minimize(
        start=(VersionPin(name="a", version="3"), VersionPin(name="b", version="3")),
        candidates=(snapshot("a"), snapshot("b")),
        evaluator=InteractionEvaluator(),
    )

    assert isinstance(result, CoordinateSuccess)
    assert result.status == "SUCCESS"
    assert [(pin.name, pin.version) for pin in result.vector] == [("a", "1"), ("b", "1")]
    assert [(boundary.dependency, boundary.floor) for boundary in result.boundaries] == [
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
            return ProbeEvidence(
                status="PASS" if int(version) >= 4 else "STATIC_FAIL",
                proposal_id=version,
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
    assert evaluator.probed[:5] == ["9", "5", "1", "3", "4"]


def test_coordinate_search_does_not_use_an_out_of_space_baseline_as_floor() -> None:
    class BaselineOnlyPasses:
        def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
            version = vector[0].version
            return ProbeEvidence(
                status="PASS" if version == "4" else "TEST_FAIL",
                proposal_id=version,
            )

    result = CoordinateSearch(small_threshold=4).minimize(
        start=(VersionPin(name="a", version="4"),),
        candidates=(snapshot("a"),),
        evaluator=BaselineOnlyPasses(),
    )

    assert isinstance(result, CoordinateFailure)
    assert result.status == "NO_PASS_IN_SEARCH_SPACE"

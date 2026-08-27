from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from pf.schemas.project import (
    AvailableArtifact,
    Candidate,
    CandidateSnapshot,
    Cell,
    Proposal,
    SourceIdentity,
    VersionPin,
    candidate_snapshot_digest,
)
from pf.schemas.evaluation import (
    Attempt,
    AttemptIdentity,
    PassEvaluation,
    ProcessResult,
    StaticUnchangedEvaluation,
    TestFail,
    TestFailEvaluation,
    TestPass,
    TyCheck,
    ty_diagnostic_digest,
)
from pf.schemas.report import (
    CoordinateFailure,
    CoordinateOutcome,
    CoordinateSuccess,
    ProbeEvidence,
    ProbeIndeterminate,
    ProbePass,
    ProbeRejection,
    StaticOnlyEvidence,
    StaticRegion,
    StaticRegionRuntimeReference,
    StaticRegionSlice,
)
from pf.coordinate_search import CoordinateSearch
from pf.errors import ConfigurationError
from pf.schemas.evaluation import SearchProbeRequest
from pf.search import select_probe


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
    source = SourceIdentity(kind="registry")
    representatives = tuple(
        (candidate.series_key, candidate.version) for candidate in candidates
    )
    return CandidateSnapshot(
        dependency=name,
        cell=cell,
        policy_identity="policy",
        source=source,
        candidates=candidates,
        series_representatives=representatives,
        digest=candidate_snapshot_digest(
            dependency=name,
            cell=cell,
            policy_identity="policy",
            source=source,
            candidates=candidates,
            series_representatives=representatives,
        ),
    )


def wide_snapshot(name: str) -> CandidateSnapshot:
    return snapshot_versions(name, tuple(str(version) for version in range(1, 10)))


def snapshot_versions(name: str, versions: tuple[str, ...]) -> CandidateSnapshot:
    base = snapshot(name)
    artifact = base.candidates[0].artifact
    candidates = tuple(
        Candidate(version=version, series_key=version, artifact=artifact)
        for version in versions
    )
    representatives = tuple(
        (candidate.series_key, candidate.version) for candidate in candidates
    )
    return CandidateSnapshot(
        dependency=name,
        cell=base.cell,
        policy_identity=base.policy_identity,
        source=base.source,
        candidates=candidates,
        series_representatives=representatives,
        digest=candidate_snapshot_digest(
            dependency=name,
            cell=base.cell,
            policy_identity=base.policy_identity,
            source=base.source,
            candidates=candidates,
            series_representatives=representatives,
        ),
    )


def selectable_snapshot(name: str = "a") -> CandidateSnapshot:
    base = snapshot(name)
    artifact = base.candidates[0].artifact.model_copy(
        update={
            "filename": f"{name}-1-py3-none-any.whl",
            "locator": f"https://files.example/{name}-1-py3-none-any.whl",
            "content_hash": f"sha256:{'a' * 64}",
        }
    )
    candidate = Candidate(version="1", series_key="1", artifact=artifact)
    representatives = (("1", "1"),)
    return CandidateSnapshot(
        dependency=name,
        cell=base.cell,
        policy_identity=base.policy_identity,
        source=base.source,
        candidates=(candidate,),
        series_representatives=representatives,
        digest=candidate_snapshot_digest(
            dependency=name,
            cell=base.cell,
            policy_identity=base.policy_identity,
            source=base.source,
            candidates=(candidate,),
            series_representatives=representatives,
        ),
    )


class TestProbeSelection:
    def test_select_probe_returns_the_frozen_artifact(self) -> None:
        selected = select_probe(
            (VersionPin(name="a", version="1"),),
            (selectable_snapshot(),),
        )

        assert selected[0].artifact.filename == "a-1-py3-none-any.whl"

    def test_select_probe_rejects_duplicate_dependencies(self) -> None:
        pin = VersionPin(name="a", version="1")

        with pytest.raises(ConfigurationError, match="unique"):
            select_probe((pin, pin), (selectable_snapshot(),))

    def test_select_probe_rejects_mismatched_dependencies(self) -> None:
        with pytest.raises(ConfigurationError, match="same dependencies"):
            select_probe(
                (VersionPin(name="b", version="1"),),
                (selectable_snapshot(),),
            )

    def test_select_probe_rejects_an_unfrozen_version(self) -> None:
        with pytest.raises(ConfigurationError, match="not uniquely frozen"):
            select_probe(
                (VersionPin(name="a", version="2"),),
                (selectable_snapshot(),),
            )

    @pytest.mark.parametrize(
        "artifact_update",
        (
            {"filename": ""},
            {"locator": None},
            {"locator": ""},
            {"content_hash": "sha256:short"},
        ),
        ids=("filename", "missing-locator", "empty-locator", "hash"),
    )
    def test_select_probe_rejects_incomplete_artifact_evidence(
        self,
        artifact_update: dict[str, object],
    ) -> None:
        snapshot = selectable_snapshot()
        candidate = snapshot.candidates[0].model_copy(
            update={
                "artifact": snapshot.candidates[0].artifact.model_copy(
                    update=artifact_update
                )
            }
        )
        tampered = snapshot.model_copy(update={"candidates": (candidate,)})

        with pytest.raises(ConfigurationError, match="artifact is incomplete"):
            select_probe((VersionPin(name="a", version="1"),), (tampered,))


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
    static = StaticUnchangedEvaluation(
        proposal=proposal,
        ty=TyCheck(
            process=ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0,
                stdout="",
                stderr="",
            ),
            diagnostics=(),
        ),
        baseline_digest=ty_diagnostic_digest(()),
    )
    return ProbePass(
        attempt=attempt,
        proposal_id=proposal_id,
        evaluation=PassEvaluation(
            proposal=proposal,
            static=static,
            test=TestPass(
                process=ProcessResult(
                    exit_code=0,
                    signal=None,
                    duration_seconds=0,
                    stdout="",
                    stderr="",
                )
            ),
        ),
    )


def probe_rejection(
    vector: tuple[VersionPin, ...], proposal_id: str
) -> ProbeRejection:
    passed = probe_pass(vector, proposal_id)
    evaluation = TestFailEvaluation(
        proposal=passed.evaluation.proposal,
        static=passed.evaluation.static,
        test=TestFail(
            process=ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0,
                stdout="",
                stderr="failed",
            )
        ),
    )
    return ProbeRejection(
        attempt=passed.attempt,
        proposal_id=proposal_id,
        failure_id=f"failure-{proposal_id}",
        cause="TEST_FAILURE",
        evaluation=evaluation,
    )


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


class TestCoordinateSearch:
    def test_runtime_backed_probe_receives_the_current_discrete_search_window(
        self,
    ) -> None:
        requests: list[SearchProbeRequest] = []

        class RuntimeBacked:
            @property
            def regions(self) -> tuple[StaticRegion, ...]:
                return ()

            def evaluate(
                self, vector: tuple[VersionPin, ...]
            ) -> ProbeEvidence:
                raise AssertionError("known baseline must not be evaluated")

            def evaluate_in_slice(
                self, request: SearchProbeRequest
            ) -> ProbeEvidence:
                requests.append(request)
                return self._outcome(request)

            def promote(self, request: SearchProbeRequest) -> ProbeEvidence:
                requests.append(request)
                return self._outcome(request)

            @staticmethod
            def _outcome(request: SearchProbeRequest) -> ProbeEvidence:
                if int(request.candidate_version) >= 3:
                    return probe_pass(request.vector, request.candidate_version)
                return probe_rejection(
                    request.vector,
                    request.candidate_version,
                )

        result = CoordinateSearch().minimize(
            start=(VersionPin(name="a", version="5"),),
            candidates=(snapshot_versions("a", ("1", "2", "3", "4", "5")),),
            evaluator=RuntimeBacked(),
            hints=(VersionPin(name="a", version="3"),),
            start_is_known_pass=True,
        )

        assert isinstance(result, CoordinateSuccess)
        assert requests[0] == SearchProbeRequest(
            vector=(VersionPin(name="a", version="3"),),
            active_dependency="a",
            candidate_version="3",
            lower_version="1",
            upper_version="5",
            candidate_count=5,
        )

    def test_runtime_backed_window_excludes_a_virtual_baseline_sentinel(
        self,
    ) -> None:
        requests: list[SearchProbeRequest] = []

        class RuntimeBacked:
            @property
            def regions(self) -> tuple[StaticRegion, ...]:
                return ()

            def evaluate(
                self, vector: tuple[VersionPin, ...]
            ) -> ProbeEvidence:
                raise AssertionError("known baseline must not be evaluated")

            def evaluate_in_slice(
                self, request: SearchProbeRequest
            ) -> ProbeEvidence:
                requests.append(request)
                if int(request.candidate_version) >= 8:
                    return probe_pass(request.vector, request.candidate_version)
                return probe_rejection(request.vector, request.candidate_version)

            promote = evaluate_in_slice

        result = CoordinateSearch(small_threshold=2).minimize(
            start=(VersionPin(name="a", version="10"),),
            candidates=(wide_snapshot("a"),),
            evaluator=RuntimeBacked(),
            start_is_known_pass=True,
        )

        assert isinstance(result, CoordinateSuccess)
        assert result.vector[0].version == "8"
        assert all(request.candidate_version != "10" for request in requests)
        assert all(request.upper_version != "10" for request in requests)
        assert all(request.candidate_count <= 9 for request in requests)

    def test_static_frontier_is_promoted_and_rebounded_on_runtime_rejection(
        self,
    ) -> None:
        candidates = snapshot_versions("a", ("1", "2", "3", "4", "5"))
        vector_one = (VersionPin(name="a", version="1"),)
        vector_two = (VersionPin(name="a", version="2"),)
        passed_two = probe_pass(vector_two, "a=2")
        rejected_one = probe_rejection(vector_one, "a=1")
        assert isinstance(rejected_one.evaluation, TestFailEvaluation)
        region_slice = StaticRegionSlice(
            cell=candidates.cell,
            source_snapshot_digest="snapshot",
            policy_identity="policy",
            baseline_digest=ty_diagnostic_digest(()),
            active_dependency="a",
            other_coordinates=(),
            candidate_order=("1", "2", "3", "4", "5"),
        )
        cheap_one = StaticOnlyEvidence(
            attempt=rejected_one.attempt,
            proposal_id="a=1",
            static_evaluation=rejected_one.evaluation.static,
            guidance="PASS",
            region_slice=region_slice,
            representative_proposal_id="a=2",
        )

        class RuntimeBacked:
            evaluated: list[str] = []
            promoted: list[str] = []

            @property
            def regions(self) -> tuple[StaticRegion, ...]:
                return (
                    StaticRegion(
                        slice=region_slice,
                        static_fingerprint=cheap_one.static_evaluation.static_fingerprint,
                        observed_versions=("1", "2"),
                        runtime_references=(
                            StaticRegionRuntimeReference(
                                proposal_id="a=1", status="REJECTED"
                            ),
                            StaticRegionRuntimeReference(
                                proposal_id="a=2", status="PASS"
                            ),
                        ),
                    ),
                )

            def evaluate(
                self, vector: tuple[VersionPin, ...]
            ) -> ProbeEvidence:
                raise AssertionError("known highest must not be evaluated")

            def evaluate_in_slice(
                self,
                request: SearchProbeRequest,
            ) -> ProbeEvidence | StaticOnlyEvidence:
                assert request.active_dependency == "a"
                version = request.candidate_version
                self.evaluated.append(version)
                return passed_two if version == "2" else cheap_one

            def promote(
                self,
                request: SearchProbeRequest,
            ) -> ProbeEvidence:
                assert request.active_dependency == "a"
                self.promoted.append(request.candidate_version)
                return rejected_one

        evaluator = RuntimeBacked()
        result = CoordinateSearch(small_threshold=2).minimize(
            start=(VersionPin(name="a", version="5"),),
            candidates=(candidates,),
            evaluator=evaluator,
            hints=(VersionPin(name="a", version="2"),),
            start_is_known_pass=True,
        )

        assert isinstance(result, CoordinateSuccess)
        assert result.vector == vector_two
        assert evaluator.evaluated == ["2", "1"]
        assert evaluator.promoted == ["1"]
        assert any(
            isinstance(observation.evidence, StaticOnlyEvidence)
            for observation in result.observations
        )
        assert isinstance(
            next(
                observation.evidence
                for observation in result.observations
                if observation.candidate_version == "1"
                and not isinstance(observation.evidence, StaticOnlyEvidence)
            ),
            ProbeRejection,
        )

    def test_coordinate_search_repeats_sweeps_until_the_final_context_is_minimal(
        self,
    ) -> None:
        progress: list[tuple[VersionPin, ...]] = []
        result = CoordinateSearch(small_threshold=4).minimize(
            start=(
                VersionPin(name="a", version="3"),
                VersionPin(name="b", version="3"),
            ),
            candidates=(snapshot("a"), snapshot("b")),
            evaluator=InteractionEvaluator(),
            progress=progress.append,
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
        assert progress == [
            (),
            (VersionPin(name="a", version="2"),),
            (
                VersionPin(name="a", version="2"),
                VersionPin(name="b", version="1"),
            ),
            (),
            (VersionPin(name="a", version="1"),),
            (
                VersionPin(name="a", version="1"),
                VersionPin(name="b", version="1"),
            ),
            (),
            (VersionPin(name="a", version="1"),),
            (
                VersionPin(name="a", version="1"),
                VersionPin(name="b", version="1"),
            ),
        ]

    def test_coordinate_search_uses_hint_then_lower_bound_binary_search(self) -> None:
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

    def test_coordinate_search_does_not_use_an_out_of_space_baseline_as_floor(
        self,
    ) -> None:
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

    def test_coordinate_search_binary_searches_below_a_virtual_pass_sentinel(
        self,
    ) -> None:
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

    def test_coordinate_search_never_returns_a_virtual_pass_sentinel(self) -> None:
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

    def test_coordinate_search_stops_at_an_indeterminate_probe(self) -> None:
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

    def test_coordinate_progress_excludes_an_active_indeterminate_package(
        self,
    ) -> None:
        class UnknownOnSecondPackage:
            def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
                versions = {pin.name: pin.version for pin in vector}
                if versions == {"a": "1", "b": "1"}:
                    return ProbeIndeterminate(
                        attempt=probe_attempt(vector),
                        failure_id="failure-timeout",
                        cause="TIMEOUT",
                    )
                return probe_pass(vector, "known-pass")

        progress: list[tuple[VersionPin, ...]] = []
        result = CoordinateSearch().minimize(
            start=(
                VersionPin(name="a", version="3"),
                VersionPin(name="b", version="3"),
            ),
            candidates=(snapshot("a"), snapshot("b")),
            evaluator=UnknownOnSecondPackage(),
            progress=progress.append,
        )

        assert isinstance(result, CoordinateFailure)
        assert result.status == "INDETERMINATE"
        assert progress == [
            (),
            (VersionPin(name="a", version="1"),),
        ]

    @pytest.mark.parametrize(
        ("small_threshold", "versions", "floor"),
        [
            (4, ("1", "2", "3"), "2"),
            (2, ("1", "2", "3"), "2"),
            (2, ("1", "2", "3", "4", "5"), "2"),
        ],
    )
    def test_coordinate_search_finds_the_same_floor_across_slice_strategies(
        self,
        small_threshold: int,
        versions: tuple[str, ...],
        floor: str,
    ) -> None:
        class Threshold:
            def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
                version = vector[0].version
                if int(version) >= int(floor):
                    return probe_pass(vector, version)
                return ProbeRejection(
                    attempt=probe_attempt(vector),
                    failure_id=f"failure-{version}",
                    cause="RESOLUTION_CONFLICT",
                )

        result = CoordinateSearch(small_threshold=small_threshold).minimize(
            start=(VersionPin(name="a", version=versions[-1]),),
            candidates=(snapshot_versions("a", versions),),
            evaluator=Threshold(),
            hints=(VersionPin(name="missing", version="99"),),
        )

        assert isinstance(result, CoordinateSuccess)
        assert result.vector == (VersionPin(name="a", version=floor),)

    def test_coordinate_search_known_start_is_not_evaluated(self) -> None:
        calls: list[str] = []

        class AlwaysPasses:
            def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
                calls.append(vector[0].version)
                return probe_pass(vector, vector[0].version)

        result = CoordinateSearch().minimize(
            start=(VersionPin(name="a", version="3"),),
            candidates=(snapshot("a"),),
            evaluator=AlwaysPasses(),
            start_is_known_pass=True,
        )

        assert isinstance(result, CoordinateSuccess)
        assert "3" not in calls

    def test_coordinate_search_same_instance_supports_nested_minimize(self) -> None:
        search = CoordinateSearch()
        inner_results: list[CoordinateOutcome] = []

        class Inner:
            def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
                return probe_pass(vector, f"inner-{vector[0].version}")

        class Outer:
            nested = False

            def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
                if not self.nested:
                    self.nested = True
                    inner_results.append(
                        search.minimize(
                            start=(VersionPin(name="inner", version="3"),),
                            candidates=(snapshot("inner"),),
                            evaluator=Inner(),
                        )
                    )
                version = vector[0].version
                if int(version) >= 2:
                    return probe_pass(vector, f"outer-{version}")
                return ProbeRejection(
                    attempt=probe_attempt(vector),
                    failure_id=f"outer-failure-{version}",
                    cause="RESOLUTION_CONFLICT",
                )

        outer = search.minimize(
            start=(VersionPin(name="outer", version="3"),),
            candidates=(snapshot("outer"),),
            evaluator=Outer(),
        )

        assert isinstance(outer, CoordinateSuccess)
        assert outer.vector == (VersionPin(name="outer", version="2"),)
        assert all(
            observation.vector[0].name == "outer" for observation in outer.observations
        )
        assert isinstance(inner_results[0], CoordinateSuccess)
        assert inner_results[0].vector == (VersionPin(name="inner", version="1"),)

    def test_coordinate_search_same_instance_supports_barrier_interleaving(
        self,
    ) -> None:
        search = CoordinateSearch()
        barrier = Barrier(2)

        class Threshold:
            def __init__(self, floor: int) -> None:
                self.floor = floor
                self.first = True

            def evaluate(self, vector: tuple[VersionPin, ...]) -> ProbeEvidence:
                if self.first:
                    self.first = False
                    barrier.wait(timeout=2)
                version = vector[0].version
                if int(version) >= self.floor:
                    return probe_pass(vector, f"{vector[0].name}-{version}")
                return ProbeRejection(
                    attempt=probe_attempt(vector),
                    failure_id=f"{vector[0].name}-failure-{version}",
                    cause="RESOLUTION_CONFLICT",
                )

        def run(name: str, floor: int) -> CoordinateOutcome:
            return search.minimize(
                start=(VersionPin(name=name, version="3"),),
                candidates=(snapshot(name),),
                evaluator=Threshold(floor),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, "a", 1)
            second = pool.submit(run, "b", 2)
            results = (first.result(), second.result())

        assert all(isinstance(result, CoordinateSuccess) for result in results)
        assert [
            result.vector for result in results if isinstance(result, CoordinateSuccess)
        ] == [
            (VersionPin(name="a", version="1"),),
            (VersionPin(name="b", version="2"),),
        ]

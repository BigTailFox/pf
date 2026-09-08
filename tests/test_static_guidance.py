from math import ceil, log2

import pytest

from pf.coordinate_search import CoordinateSearch
from pf.schemas.project import VersionPin
from pf.schemas.report import CoordinateSuccess, CoordinateFailure
from pf.schemas.static_comparison import StaticCompared, StaticComparisonUnavailable, StaticUncompared
from pf.static_guidance import StaticPoint, locate_static_hint
from test_search import snapshot_versions, probe_pass, probe_rejection


def point(version, regression):
    return StaticPoint(str(version), StaticCompared(
        state="STATIC_REGRESSION" if regression else "STATIC_UNCHANGED",
        incremental_identities=("diagnostic",) if regression else (), fingerprint="a" * 64,
    ), "b" * 64)


class Slice:
    def __init__(self, upper, boundary, events, *, unavailable=None, known=()):
        self.anchor = point(upper, False)
        self.known_points = known
        self.boundary = boundary
        self.events = events
        self.unavailable = unavailable
        self.result = None

    def inspect(self, version):
        self.events.append(("static", int(version)))
        return (StaticPoint(version, StaticComparisonUnavailable(reason="timeout"), None)
                if int(version) == self.unavailable else point(version, int(version) < self.boundary))

    def finish(self, result):
        self.result = result
        return "algorithm-search"


class Oracle:
    def __init__(self, floor, boundary):
        self.floor, self.boundary = floor, boundary
        self.events = []
        self.cache = {}
        self.slices = []

    def evaluate(self, vector):
        version = int(vector[0].version)
        if version not in self.cache:
            self.events.append(("oracle", version))
            self.cache[version] = (probe_pass(vector, str(version)) if version >= self.floor
                                   else probe_rejection(vector, str(version)))
        return self.cache[version]

    def evaluate_in_slice(self, request):
        return self.evaluate(request.vector)

    def lookup_direct_in_slice(self, request):
        return self.cache.get(int(request.candidate_version))

    def open_static_slice(self, vector, *, dependency, versions):
        assert dependency == "a"
        slice = Slice(int(vector[0].version), self.boundary, self.events)
        self.slices.append(slice)
        return slice

    def finish_coordinate(self):
        self.events.append(("finish", 0))


class TestStaticGuidance:
    @pytest.mark.parametrize("floor", range(1, 11))
    @pytest.mark.parametrize("static_boundary", range(1, 12))
    def test_guidance_preserves_monotone_dynamic_floor(self, floor, static_boundary):
        oracle = Oracle(floor, static_boundary)
        result = CoordinateSearch(small_threshold=2).minimize(
            start=(VersionPin(name="a", version="10"),),
            candidates=(snapshot_versions("a", tuple(map(str, range(1, 10)))),), evaluator=oracle,
        )
        if floor <= 9:
            assert isinstance(result, CoordinateSuccess)
            assert result.vector == (VersionPin(name="a", version=str(floor)),)
        else:
            assert isinstance(result, CoordinateFailure)
            assert result.status == "NO_PASS_IN_SEARCH_SPACE"
        assert len(oracle.slices) == 1
        static = [event for event in oracle.events if event[0] == "static"]
        assert len(static) <= 2 + ceil(log2(10))
        first_oracle = next(i for i, event in enumerate(oracle.events) if event[0] == "oracle" and event[1] != 10)
        assert all(i < first_oracle for i, event in enumerate(oracle.events) if event[0] == "static")

    @pytest.mark.parametrize("floor,expected", [(4, [4, 1]), (5, [4, 5]), (7, [4, 5])])
    def test_oracle_consumes_suspect_then_only_needed_clean_neighbor(self, floor, expected):
        oracle = Oracle(floor, 5)
        CoordinateSearch(small_threshold=2).minimize(
            start=(VersionPin(name="a", version="10"),),
            candidates=(snapshot_versions("a", tuple(map(str, range(1, 10)))),), evaluator=oracle,
        )
        probes = [version for kind, version in oracle.events if kind == "oracle" and version != 10]
        assert probes[:2] == expected

    def test_unavailable_aborts_without_retry_or_false_hint(self):
        slice = Slice(10, 5, [], unavailable=5)
        result = locate_static_hint(slice, tuple(map(str, range(1, 10))))
        assert result.hint is None and result.reason == "static-unavailable"
        assert slice.events == [("static", 1), ("static", 5)]

    def test_prior_local_contradiction_aborts_without_scanning(self):
        slice = Slice(10, 5, [], known=(point(2, False), point(6, True)))
        result = locate_static_hint(slice, tuple(map(str, range(1, 10))))
        assert result.hint is None and result.reason == "static-inconsistent"
        assert slice.events == []

    def test_lower_unchanged_and_context_mismatch_return_stable_no_hint(self):
        clean = Slice(10, 0, [])
        result = locate_static_hint(clean, tuple(map(str, range(1, 10))))
        assert result.hint is None and result.reason == "lower-unchanged"
        assert clean.events == [("static", 1)]

        class Mismatch(Slice):
            def inspect(self, version):
                self.events.append(("static", int(version)))
                return StaticPoint(version, StaticUncompared(reason="context-mismatch"), None)

        mismatch = Mismatch(10, 5, [])
        result = locate_static_hint(mismatch, tuple(map(str, range(1, 10))))
        assert result.hint is None and result.reason == "context-mismatch"
        assert mismatch.events == [("static", 1)]

    @pytest.mark.parametrize("floor", (1, 5, 9, 10))
    @pytest.mark.parametrize("unavailable", (None, 1, 5, 9))
    def test_unavailable_and_mechanical_paths_keep_the_same_dynamic_floor(self, floor, unavailable):
        class Mechanical:
            def __init__(self):
                self.cache = {}

            def evaluate(self, vector):
                version = int(vector[0].version)
                if version not in self.cache:
                    self.cache[version] = (
                        probe_pass(vector, str(version)) if version >= floor
                        else probe_rejection(vector, str(version))
                    )
                return self.cache[version]

            def evaluate_in_slice(self, request):
                return self.evaluate(request.vector)

            def lookup_direct_in_slice(self, request):
                return self.cache.get(int(request.candidate_version))

            def open_static_slice(self, vector, *, dependency, versions):
                return None

            def finish_coordinate(self):
                return None

        class Guided(Oracle):
            def open_static_slice(self, vector, *, dependency, versions):
                slice = super().open_static_slice(vector, dependency=dependency, versions=versions)
                slice.unavailable = unavailable
                return slice

        guided = Guided(floor, 5) if unavailable is not None else Oracle(floor, 5)
        candidates = (snapshot_versions("a", tuple(map(str, range(1, 10)))),)
        start = (VersionPin(name="a", version="10"),)
        guided_result = CoordinateSearch(small_threshold=2).minimize(
            start=start, candidates=candidates, evaluator=guided,
        )
        mechanical_result = CoordinateSearch(small_threshold=2).minimize(
            start=start, candidates=candidates, evaluator=Mechanical(),
        )
        assert type(guided_result) is type(mechanical_result)
        if isinstance(guided_result, CoordinateSuccess):
            assert isinstance(mechanical_result, CoordinateSuccess)
            assert guided_result.vector == mechanical_result.vector == (VersionPin(name="a", version=str(floor)),)
        else:
            assert isinstance(guided_result, CoordinateFailure)
            assert isinstance(mechanical_result, CoordinateFailure)
            assert guided_result.status == mechanical_result.status == "NO_PASS_IN_SEARCH_SPACE"

"""E005: disposable, deterministic D031 algorithm experiment, not product code.

Run from the repository root: .venv/bin/python scripts/simulate_d031_search.py
Only synthetic oracle evaluations occur. No registry, resolver, verifier or CLI runs.
The production bridge exercises CoordinateSearch.minimize with valid test fixtures.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import platform
import runpy
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]
PASS, REJECTED, INDETERMINATE = "PASS", "REJECTED", "INDETERMINATE"
STRATEGIES = {
    "A": (False, False),
    "B": (False, True),
    "C": (True, False),
    "D": (True, True),
}
Vector = tuple[int, ...]
Oracle = Callable[[Vector], str]


def require(condition: bool, detail: object) -> None:
    if not condition:
        raise AssertionError(detail)


@dataclass(frozen=True)
class Domain:
    versions: tuple[Version, ...]
    resolution: int
    targets: tuple[int, ...]

    @classmethod
    def build(cls, raw: tuple[str, ...], resolution: int) -> Domain:
        versions = tuple(sorted(set(map(Version, raw))))
        # Independent reference grouping: collect maxima by key, without tree traversal.
        maxima = {}
        for index, version in enumerate(versions):
            release = (*version.release, 0, 0, 0)
            maxima[(version.epoch, *release[:resolution])] = index
        return cls(versions, resolution, tuple(sorted(maxima.values())))

    def key(self, index: int, depth: int) -> tuple[int, ...]:
        version = self.versions[index]
        release = (*version.release, 0, 0, 0)
        return (version.epoch, *release[:depth])

    def groups(
        self, points: tuple[int, ...], depth: int
    ) -> tuple[tuple[int, ...], ...]:
        return tuple(
            tuple(group)
            for _, group in itertools.groupby(points, lambda i: self.key(i, depth))
        )

    def text(self, index: int) -> str:
        return (
            str(self.versions[index]) if index < len(self.versions) else "999!999.0.0"
        )

    def audit_partition(self) -> None:
        def visit(points, depth):
            groups = self.groups(points, depth)
            require(
                tuple(i for group in groups for i in group) == points,
                "partition lost candidates",
            )
            require(
                len({self.key(group[0], depth) for group in groups}) == len(groups),
                "non-contiguous bucket",
            )
            if depth == self.resolution:
                return tuple(group[-1] for group in groups)
            return tuple(i for group in groups for i in visit(group, depth + 1))

        require(
            visit(tuple(range(len(self.versions))), 1) == self.targets,
            "raw-tree terminal representatives differ from independent grouped maxima",
        )


class Stop(Exception):
    def __init__(self, status: str, counterexample: tuple[int, int] | None = None):
        self.status = status
        self.counterexample = counterexample


@dataclass(frozen=True)
class Evidence:
    status: str
    direct: bool = True


class Evaluator:
    """One fixed invocation, one authoritative vector result table, separate guidance."""

    def __init__(self, oracle: Oracle, baseline: Vector, guidance=None):
        require(oracle(baseline) == PASS, ("invalid baseline", baseline))
        self.oracle = oracle
        self.full = {baseline: Evidence(PASS)}
        self.guidance = guidance or {}
        self.cheap = {}
        self.requests = 0
        self.hits = 0
        self.static_misses = 0
        self.misses: list[tuple[Vector, str]] = []
        self.promotions = 0
        self.calls: list[dict] = []

    def get(self, vector: Vector, dependency: int, direct: bool) -> Evidence:
        self.requests += 1
        self.promotions += int(direct)
        key = (dependency, vector)
        if vector in self.full:
            self.hits += 1
            evidence, source = self.full[vector], "direct-cache"
        elif not direct and key in self.cheap:
            self.hits += 1
            evidence, source = self.cheap[key], "guidance-cache"
        elif not direct and key in self.guidance:
            evidence = Evidence(self.guidance[key], direct=False)
            self.cheap[key] = evidence
            self.static_misses += 1
            source = "synthetic-guidance"
        else:
            evidence = Evidence(self.oracle(vector))
            self.full[vector] = evidence
            self.misses.append((vector, evidence.status))
            source = "direct-miss"
        self.calls.append(
            {
                "vector": vector,
                "dependency": dependency,
                "promotion": direct,
                "status": evidence.status,
                "direct": evidence.direct,
                "source": source,
            }
        )
        return evidence


@dataclass
class Outcome:
    status: str
    vector: Vector
    boundaries: tuple[tuple[int, int | None], ...]
    sweeps: int
    counterexample: tuple[int, int] | None = None


class Simulation:
    def __init__(
        self,
        domains: tuple[Domain, ...],
        baseline: Vector,
        oracle: Oracle,
        strategy: str,
        guidance=None,
    ):
        self.domains = domains
        self.current = baseline
        self.tree, self.reconfirm = STRATEGIES[strategy]
        self.evaluator = Evaluator(oracle, baseline, guidance)
        self.observations = defaultdict(dict)
        self.history = {}
        self.reconfirm_events = []
        self.visited_slices = []
        self.sweeps = 0
        self.boundaries = ()

    def probe(self, d: int, candidate: int, direct=False) -> str:
        require(
            candidate in self.domains[d].targets,
            ("sentinel/non-target probe", d, candidate),
        )
        vector = (*self.current[:d], candidate, *self.current[d + 1 :])
        evidence = self.evaluator.get(vector, d, direct)
        self.evaluator.calls[-1]["sweep"] = self.sweeps
        if evidence.direct:
            if evidence.status == INDETERMINATE:
                raise Stop(INDETERMINATE)
            key = (d, (*vector[:d], *vector[d + 1 :]))
            points = self.observations[key]
            points[candidate] = evidence.status
            passes = [i for i, s in points.items() if s == PASS]
            rejects = [i for i, s in points.items() if s == REJECTED]
            if passes and rejects and min(passes) < max(rejects):
                raise Stop("NON_MONOTONIC", (min(passes), max(rejects)))
        return evidence.status

    def locate(self, d: int, points: tuple[int, ...], upper: int) -> int | None:
        """Current default flat locator: threshold 8 checked once, then binary to adjacency."""
        low = 0
        virtual = upper not in points
        high = len(points) if virtual else points.index(upper)
        if high - low <= 8:
            for index in range(low + 1, min(high, len(points) - 1) + 1):
                if self.probe(d, points[index]) == PASS:
                    return points[index]
            return None if virtual else points[high]
        while high - low > 1:
            middle = (low + high) // 2
            if self.probe(d, points[middle]) == PASS:
                high = middle
            else:
                low = middle
        return points[high] if high < len(points) else None

    def refine(
        self, d: int, points: tuple[int, ...], upper: int, depth: int
    ) -> int | None:
        groups = self.domains[d].groups(points, depth)
        reps = tuple(group[-1] for group in groups)
        # The entry contract carries upper direction; singleton can only skip when it IS upper.
        if len(groups) == 1 and reps[0] == upper:
            selected = reps[0]
        elif self.probe(d, reps[0]) == PASS:
            selected = reps[0]
        else:
            selected = self.locate(d, reps, upper)
        if selected is None or depth == self.domains[d].resolution:
            return selected
        group = groups[reps.index(selected)]
        return self.refine(d, group, selected, depth + 1)

    def find_floor(self, d: int) -> tuple[int, int | None]:
        upper = self.current[d]
        targets = tuple(i for i in self.domains[d].targets if i <= upper)
        if not targets:
            raise Stop("NO_PASS_IN_SEARCH_SPACE")
        self.visited_slices.append((d, self.current, targets))
        if targets[0] == upper:
            return upper, None
        old = self.history.get(d)
        if self.reconfirm and old and old[0] == upper and old[1] is not None:
            predecessor = old[1]
            require(targets[-2:] == (predecessor, upper), "history is not adjacent")
            status = self.probe(d, predecessor, direct=True)
            self.reconfirm_events.append(
                {
                    "dependency": d,
                    "context": self.current,
                    "predecessor": predecessor,
                    "status": status,
                }
            )
            if status == REJECTED:
                return upper, predecessor
            upper = predecessor
            targets = tuple(i for i in targets if i <= upper)
        for _ in range(2 * len(targets) + 1):
            if self.probe(d, targets[0]) == PASS:
                floor = targets[0]
            elif self.tree:
                # Every coarse representative is also a terminal representative. Retaining
                # terminal indices is an interval view over the frozen U, not a second oracle.
                floor = self.refine(d, targets, upper, 1)
            else:
                if upper in targets:
                    require(self.probe(d, upper) == PASS, "lost known upper PASS")
                floor = self.locate(d, targets, upper)
            if floor is None:
                raise Stop("NO_PASS_IN_SEARCH_SPACE")
            if self.probe(d, floor, direct=True) != PASS:
                continue
            index = targets.index(floor)
            if index == 0:
                return floor, None
            predecessor = targets[index - 1]
            if self.probe(d, predecessor, direct=True) == REJECTED:
                return floor, predecessor
        raise Stop("NONDETERMINISTIC")

    def run(self) -> Outcome:
        try:
            for _ in range(sum(len(d.targets) for d in self.domains) + 2):
                self.sweeps += 1
                before = self.current
                boundaries = []
                for d in range(len(self.domains)):
                    boundary = self.find_floor(d)
                    self.history[d] = boundary
                    boundaries.append(boundary)
                    require(boundary[0] <= self.current[d], "coordinate increased")
                    self.current = (
                        *self.current[:d],
                        boundary[0],
                        *self.current[d + 1 :],
                    )
                    require(
                        self.evaluator.full[self.current] == Evidence(PASS),
                        "non-direct current",
                    )
                self.boundaries = tuple(boundaries)
                if before == self.current:
                    return Outcome(
                        "SUCCESS", self.current, self.boundaries, self.sweeps
                    )
            raise AssertionError("finite descent bound exceeded")
        except Stop as stop:
            return Outcome(
                stop.status, self.current, (), self.sweeps, stop.counterexample
            )


def exhaustive(
    domains: tuple[Domain, ...], baseline: Vector, oracle: Oracle
) -> Outcome:
    """Independent brute-force coordinate descent, no bracket/tree/cache/promotion."""
    current = baseline
    for sweep in range(1, sum(len(d.targets) for d in domains) + 3):
        before = current
        boundaries = []
        for d, domain in enumerate(domains):
            active = [i for i in domain.targets if i <= current[d]]
            floor = None
            predecessor = None
            for candidate in active:
                trial = (*current[:d], candidate, *current[d + 1 :])
                status = oracle(trial)
                if status == INDETERMINATE:
                    return Outcome(INDETERMINATE, current, (), sweep)
                if status == PASS:
                    floor = candidate
                    break
                predecessor = candidate
            if floor is None:
                return Outcome("NO_PASS_IN_SEARCH_SPACE", current, (), sweep)
            current = (*current[:d], floor, *current[d + 1 :])
            boundaries.append((floor, predecessor))
        if before == current:
            return Outcome("SUCCESS", current, tuple(boundaries), sweep)
    raise AssertionError("reference descent did not terminate")


class ProductionBridge:
    """Invoke the unmodified public algorithm with existing valid public-seam fixtures."""

    def __init__(self):
        self.fixtures = runpy.run_path(str(ROOT / "tests/test_search.py"))
        self.checked = 0

    @lru_cache(maxsize=128)
    def snapshots(self, domains):
        return tuple(
            self.fixtures["snapshot_versions"](
                chr(97 + d), tuple(domain.text(i) for i in domain.targets)
            )
            for d, domain in enumerate(domains)
        )

    def check(self, domains, baseline, oracle, expected, misses):
        from pf.coordinate_search import CoordinateSearch
        from pf.schemas.project import VersionPin
        from pf.schemas.report import CoordinateSuccess, ProbeIndeterminate

        fixtures = self.fixtures
        reverse = [
            {domain.text(i): i for i in (*domain.targets, len(domain.versions))}
            for domain in domains
        ]
        direct = {}
        observed = []

        class Adapter:
            def evaluate(self, pins):
                vector = tuple(reverse[d][pin.version] for d, pin in enumerate(pins))
                if vector not in direct:
                    status = oracle(vector)
                    observed.append((vector, status))
                    identity = ";".join(f"{pin.name}={pin.version}" for pin in pins)
                    if status == PASS:
                        result = fixtures["probe_pass"](pins, identity)
                    elif status == REJECTED:
                        result = fixtures["probe_rejection"](pins, identity)
                    else:
                        result = ProbeIndeterminate(
                            attempt=fixtures["probe_attempt"](pins),
                            failure_id="failure-synthetic",
                            cause="TOOL_FAILURE",
                        )
                    direct[vector] = result
                return direct[vector]

        actual = CoordinateSearch().minimize(
            start=tuple(
                VersionPin(name=chr(97 + d), version=domain.text(baseline[d]))
                for d, domain in enumerate(domains)
            ),
            candidates=self.snapshots(domains),
            evaluator=Adapter(),
            start_is_known_pass=True,
        )
        status = "SUCCESS" if isinstance(actual, CoordinateSuccess) else actual.status
        require(
            status == expected.status,
            ("production terminal mismatch", status, expected),
        )
        if isinstance(actual, CoordinateSuccess):
            vector = tuple(
                reverse[d][pin.version] for d, pin in enumerate(actual.vector)
            )
            boundaries = tuple(
                (
                    reverse[d][b.floor],
                    reverse[d][b.predecessor] if b.predecessor else None,
                )
                for d, b in enumerate(actual.boundaries)
            )
            require(
                (vector, boundaries, actual.sweeps)
                == (expected.vector, expected.boundaries, expected.sweeps),
                "production result mismatch",
            )
        require(
            observed == misses,
            ("production direct-miss trace mismatch", observed, misses),
        )
        self.checked += 1


def validate_result(sim: Simulation, result: Outcome, reference: Outcome | None):
    ev = sim.evaluator
    require(
        ev.requests == ev.hits + len(ev.misses) + ev.static_misses, "cache accounting"
    )
    require(
        len({v for v, _ in ev.misses}) == len(ev.misses), "duplicate direct execution"
    )
    # Replay the evidence independently of Simulation.observations. A fatal or an observed
    # contradiction must be the last logical call, including when that call hits the cache.
    slices = defaultdict(list)
    for index, call in enumerate(ev.calls):
        if not call["direct"]:
            continue
        d, vector = call["dependency"], call["vector"]
        points = slices[(d, (*vector[:d], *vector[d + 1 :]))]
        points.append((vector[d], call["status"]))
        contradiction = any(
            a < b and sa == PASS and sb == REJECTED
            for a, sa in points
            for b, sb in points
        )
        fatal = call["status"] == INDETERMINATE
        if contradiction or fatal:
            require(
                index == len(ev.calls) - 1,
                "requests continued after a terminal observation",
            )
            require(
                result.status == (INDETERMINATE if fatal else "NON_MONOTONIC"),
                "wrong observed terminal",
            )
    if reference is not None:
        require(
            (result.status, result.vector, result.boundaries, result.sweeps)
            == (
                reference.status,
                reference.vector,
                reference.boundaries,
                reference.sweeps,
            ),
            ("brute-force mismatch", result, reference),
        )
    if result.status == "SUCCESS":
        require(ev.oracle(result.vector) == PASS, "final oracle rejected")
        for d, (floor, predecessor) in enumerate(result.boundaries):
            require(floor in sim.domains[d].targets, "non-target floor")
            if predecessor is not None:
                vector = (*result.vector[:d], predecessor, *result.vector[d + 1 :])
                require(
                    ev.full[vector] == Evidence(REJECTED),
                    "boundary lacks direct rejection",
                )
    if reference is not None and result.status == "SUCCESS":
        for d, current, targets in sim.visited_slices:
            statuses = [
                ev.oracle((*current[:d], i, *current[d + 1 :])) for i in targets
            ]
            require(
                statuses == sorted(statuses, key=lambda s: s == PASS),
                "non-monotone reference slice",
            )


def threshold_oracle(thresholds: Vector) -> Oracle:
    return lambda vector: (
        PASS if all(v >= t for v, t in zip(vector, thresholds)) else REJECTED
    )


def shapes() -> dict[str, tuple[str, ...]]:
    def grid(majors, minors, patches):
        return tuple(f"{a}.{b}.{c}" for a in majors for b in minors for c in patches)

    return {
        "uniform_10x10x10": grid(range(10), range(10), range(10)),
        "dense_old_major": (
            *grid([0], range(40), range(10)),
            *grid(range(1, 10), [0], [0]),
        ),
        "dense_new_major": (
            *grid(range(9), [0], [0]),
            *grid([9], range(40), range(10)),
        ),
        "single_major": grid([0], range(30), range(10)),
        "single_minor": grid([0], [0], range(300)),
        "sparse_series": grid(range(0, 60, 5), range(0, 20, 5), [0, 3, 9]),
    }


class Experiment:
    def __init__(self):
        self.bridge = ProductionBridge()
        self.rows = []
        self.validation = defaultdict(int)
        self.special = {}
        self.trace_digest = hashlib.sha256()

    def case(
        self,
        name,
        domains,
        baseline,
        oracle,
        category,
        compare=True,
        guidance=None,
        production=True,
        keep=False,
    ):
        reference = exhaustive(domains, baseline, oracle) if compare else None
        simulations = {}
        for strategy in STRATEGIES:
            sim = Simulation(domains, baseline, oracle, strategy, guidance)
            result = sim.run()
            try:
                validate_result(sim, result, reference)
                if strategy == "A" and production and not guidance:
                    self.bridge.check(
                        domains, baseline, oracle, result, sim.evaluator.misses
                    )
            except AssertionError as error:
                raise AssertionError((name, strategy, str(error))) from error
            ev = sim.evaluator
            record = {
                "case": name,
                "category": category,
                "strategy": strategy,
                "status": result.status,
                "sweeps": result.sweeps,
                "requests": ev.requests,
                "hits": ev.hits,
                "direct_misses": len(ev.misses),
                "static_misses": ev.static_misses,
                "promotion_requests": ev.promotions,
                "unique_vectors_including_baseline": len(ev.full),
                "reconfirm_rejected": sum(
                    e["status"] == REJECTED for e in sim.reconfirm_events
                ),
                "reconfirm_passed": sum(
                    e["status"] == PASS for e in sim.reconfirm_events
                ),
                "vector": json.dumps(result.vector),
                "boundaries": json.dumps(result.boundaries),
            }
            self.rows.append(record)
            self.trace_digest.update(
                json.dumps([name, strategy, ev.calls], sort_keys=True).encode()
            )
            self.validation[category + "_strategy_runs"] += 1
            simulations[strategy] = (sim, result)
        if keep:
            self.special[name] = {
                "baseline": baseline,
                "domains": [
                    {
                        "versions": [str(v) for v in d.versions],
                        "resolution": d.resolution,
                        "targets": d.targets,
                    }
                    for d in domains
                ],
                "strategies": {
                    k: {
                        "status": r.status,
                        "vector": r.vector,
                        "boundaries": r.boundaries,
                        "calls": s.evaluator.calls,
                        "reconfirm": s.reconfirm_events,
                    }
                    for k, (s, r) in simulations.items()
                },
            }
        return simulations

    def correctness(self):
        # All boundary positions, every target resolution, in-space and virtual high.
        raw_sets = {
            "tiny": ("0.0.0", "0.0.3", "0.1.0", "1.0.0", "1.1.2"),
            "singleton": ("2.3.4",),
            "pep440": (
                "1",
                "1.0.0",
                "1.0.1rc1",
                "1.0.1",
                "1.0.1+abc",
                "1.0.1.post1",
                "1.0.1.4",
                "2.0",
                "1!0.1",
                "1!1.0",
                "2!0.0.1",
            ),
        }
        for shape, raw in raw_sets.items():
            for resolution in (1, 2, 3):
                domain = Domain.build(raw, resolution)
                domain.audit_partition()
                for virtual in (False, True):
                    baseline = (
                        len(domain.versions) if virtual else domain.targets[-1],
                    )
                    thresholds = (
                        (*domain.targets, len(domain.versions))
                        if virtual
                        else domain.targets
                    )
                    for threshold in thresholds:
                        self.case(
                            f"{shape}/r{resolution}/virtual{virtual}/t{threshold}",
                            (domain,),
                            baseline,
                            threshold_oracle((threshold,)),
                            "single_correctness",
                        )

        empty = Domain.build((), 3)
        self.case(
            "empty_space",
            (empty,),
            (0,),
            lambda v: PASS,
            "single_correctness",
            production=False,
        )

        # Independently enumerate every upward-closed Boolean table for these small products.
        for sizes in ((3, 3), (4, 4), (2, 2, 2)):
            domains = tuple(
                Domain.build(tuple(f"0.0.{i}" for i in range(n)), 3) for n in sizes
            )
            vectors = list(itertools.product(*(range(n) for n in sizes)))
            baseline = vectors[-1]
            covers = [
                (v, (*v[:d], v[d] + 1, *v[d + 1 :]))
                for v in vectors
                for d, n in enumerate(sizes)
                if v[d] + 1 < n
            ]
            count = 0
            for mask in range(1 << (len(vectors) - 1)):
                passing = {
                    baseline,
                    *(v for i, v in enumerate(vectors[:-1]) if mask & (1 << i)),
                }
                if any(v in passing and w not in passing for v, w in covers):
                    continue
                self.case(
                    f"matrix/{sizes}/{mask}",
                    domains,
                    baseline,
                    lambda v, p=passing: PASS if v in p else REJECTED,
                    "monotone_matrix",
                )
                count += 1
            self.validation[f"upward_closed_tables_{sizes}"] = count

        # Every Boolean history of 9 candidates with a verified in-space upper.
        # These are deliberately non-monotone too: certify direct evidence/terminal only.
        domain = Domain.build(tuple(f"{i // 3}.{i % 3}.0" for i in range(9)), 2)
        for mask in range(1 << 8):
            oracle = lambda v, m=mask: (
                PASS if v[0] == 8 or m & (1 << v[0]) else REJECTED
            )
            self.case(
                f"holes/{mask}",
                (domain,),
                (8,),
                oracle,
                "arbitrary_holes",
                compare=False,
            )

        # Fatal outcomes are placed at EVERY possible non-baseline candidate.
        domain = Domain.build(tuple(f"0.0.{i}" for i in range(12)), 3)
        for fatal in range(11):
            oracle = lambda v, f=fatal: (
                INDETERMINATE if v[0] == f else (PASS if v[0] >= 7 else REJECTED)
            )
            sims = self.case(
                f"fatal/{fatal}", (domain,), (11,), oracle, "fatal", compare=False
            )
            for sim, result in sims.values():
                seen = any(
                    status == INDETERMINATE for _, status in sim.evaluator.misses
                )
                require(
                    (result.status == INDETERMINATE) == seen, "fatal was suppressed"
                )
                if seen:
                    require(
                        sim.evaluator.calls[-1]["status"] == INDETERMINATE,
                        "continued after fatal",
                    )

        # Injected scheduling evidence, not a simulation of PF static-region eligibility.
        for name, threshold, guidance in (
            ("false_pass", 5, {(0, (0,)): PASS}),
            ("false_reject", 4, {(0, (4,)): REJECTED}),
        ):
            sims = self.case(
                name,
                (domain,),
                (11,),
                threshold_oracle((threshold,)),
                "promotion",
                guidance=guidance,
                keep=True,
            )
            for sim, _ in sims.values():
                candidate = next(iter(guidance))[1]
                require(sim.evaluator.full[candidate].direct, "guidance not promoted")
                require(sim.evaluator.static_misses > 0, "guidance case not exercised")

        # Valid prior direct observations in a Slice: run() must not ignore their contradiction
        # with later probes. No assumption that an ordinary sparse trace discovers every hole.
        for strategy in STRATEGIES:
            sim = Simulation(
                (domain,),
                (11,),
                lambda v: PASS if v[0] in (1, 11) else REJECTED,
                strategy,
            )
            sim.probe(0, 1, direct=True)
            result = sim.run()
            require(
                result.status == "NON_MONOTONIC", "direct contradiction not terminal"
            )
            validate_result(sim, result, None)
            self.special[f"direct_contradiction/{strategy}"] = sim.evaluator.calls
            self.validation["direct_observed_contradiction"] += 1
        # Directly exercise cache-hit observation registration under a new active coordinate.
        domains = (domain, domain)
        sim = Simulation(
            domains,
            (11, 11),
            lambda v: PASS if v[0] <= 2 or v[0] == 11 else REJECTED,
            "D",
        )
        sim.evaluator.get((1, 11), 1, True)
        sim.probe(0, 5, direct=True)
        try:
            sim.probe(0, 1, direct=True)
        except Stop as stop:
            require(stop.status == "NON_MONOTONIC", "wrong contradiction terminal")
        else:
            raise AssertionError("cache hit bypassed direct contradiction detection")
        require(len(sim.evaluator.misses) == 2, "cache hit re-executed")
        self.validation["cache_hit_observation_and_nonmonotonic"] = 1
        self.special["cache_hit_contradiction"] = sim.evaluator.calls

        # Full-vector lookup is separate from Slice-local guidance and invocation lifetime.
        ev = Evaluator(threshold_oracle((5, 5)), (11, 11), {(0, (6, 6)): REJECTED})
        require(not ev.get((6, 6), 0, False).direct, "static not injected")
        require(
            ev.get((6, 6), 1, False) == Evidence(PASS), "cross-slice guidance leaked"
        )
        require(
            ev.get((6, 6), 0, True) == Evidence(PASS), "direct result not preferred"
        )
        other = Evaluator(threshold_oracle((5, 5)), (11, 11))
        other.get((6, 6), 0, True)
        require(len(ev.misses) == len(other.misses) == 1, "invocation cache leaked")
        self.validation["slice_guidance_and_invocation_isolation"] = 1

    def performance(self):
        for name, raw in shapes().items():
            for resolution in (1, 2, 3):
                domain = Domain.build(raw, resolution)
                domain.audit_partition()
                for rank, threshold in enumerate(domain.targets):
                    case = f"{name}/r{resolution}/rank{rank}"
                    self.case(
                        case,
                        (domain,),
                        (domain.targets[-1],),
                        threshold_oracle((threshold,)),
                        "performance_single",
                        keep=case
                        in {
                            "uniform_10x10x10/r3/rank500",
                            "uniform_10x10x10/r3/rank900",
                            "dense_new_major/r3/rank408",
                        },
                    )
            print(f"completed shape: {name}", flush=True)

        raw = tuple(
            f"{a}.{b}.{c}" for a in range(4) for b in range(5) for c in range(5)
        )
        for resolution in (1, 2, 3):
            domain = Domain.build(raw, resolution)
            for coordinates in (2, 3, 5):
                domains = (domain,) * coordinates
                baseline = (domain.targets[-1],) * coordinates
                for fraction in (0.2, 0.5, 0.8):
                    rank = int((len(domain.targets) - 1) * fraction)
                    t = domain.targets[rank]
                    self.case(
                        f"independent/r{resolution}/d{coordinates}/q{fraction}",
                        domains,
                        baseline,
                        threshold_oracle((t,) * coordinates),
                        "performance_multi",
                        keep=resolution == 3 and coordinates == 3 and fraction == 0.5,
                    )
                # Lowering later coordinates relaxes earlier thresholds. Every visited DOWNWARD
                # slice is monotone, although inaccessible higher slices need not be.
                positions = {v: i for i, v in enumerate(domain.targets)}
                size = len(domain.targets)

                def contextual(vector, pos=positions, n=size):
                    ranks = tuple(pos[v] for v in vector)
                    floors = tuple(
                        min(
                            n - 1,
                            max(1, n // 10)
                            + sum(ranks[d + 1 :]) // (2 * max(1, len(ranks) - d - 1)),
                        )
                        for d in range(len(ranks))
                    )
                    return (
                        PASS if all(v >= f for v, f in zip(ranks, floors)) else REJECTED
                    )

                sims = self.case(
                    f"context_relax/r{resolution}/d{coordinates}",
                    domains,
                    baseline,
                    contextual,
                    "performance_multi",
                    keep=resolution == 3 and coordinates == 3,
                )
                if len(domain.targets) > 4:
                    require(
                        any(e["status"] == PASS for e in sims["D"][0].reconfirm_events),
                        "no predecessor became PASS",
                    )

    def summaries(self):
        grouped = defaultdict(list)
        case_rows = defaultdict(dict)
        for row in self.rows:
            case_rows[row["case"]][row["strategy"]] = row
            if row["category"] == "performance_single":
                family = row["case"].rsplit("/", 1)[0]
                grouped[(family, row["strategy"])].append(row)
        summary = []
        for (family, strategy), rows in grouped.items():
            misses = [r["direct_misses"] for r in rows]
            diffs = [
                r["direct_misses"] - case_rows[r["case"]]["A"]["direct_misses"]
                for r in rows
            ]
            summary.append(
                {
                    "family": family,
                    "strategy": strategy,
                    "cases": len(rows),
                    "mean_misses": statistics.mean(misses),
                    "min_misses": min(misses),
                    "max_misses": max(misses),
                    "mean_delta_vs_A": statistics.mean(diffs),
                    "wins": sum(d < 0 for d in diffs),
                    "ties": diffs.count(0),
                    "losses": sum(d > 0 for d in diffs),
                }
            )
        totals = {}
        for category in ("performance_single", "performance_multi"):
            totals[category] = {}
            cases = [
                rows for rows in case_rows.values() if rows["A"]["category"] == category
            ]
            for strategy in STRATEGIES:
                diffs = [
                    r[strategy]["direct_misses"] - r["A"]["direct_misses"]
                    for r in cases
                ]
                totals[category][strategy] = {
                    "cases": len(cases),
                    "total_direct_misses": sum(
                        r[strategy]["direct_misses"] for r in cases
                    ),
                    "wins": sum(d < 0 for d in diffs),
                    "ties": diffs.count(0),
                    "losses": sum(d > 0 for d in diffs),
                    "best_delta": min(diffs),
                    "worst_delta": max(diffs),
                }
        return summary, totals

    def save(self, output: Path):
        output.mkdir(parents=True, exist_ok=True)
        rows_file = output / "cases.csv"
        with rows_file.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        summaries, totals = self.summaries()
        source_paths = (
            "scripts/simulate_d031_search.py",
            "src/pf/coordinate_search.py",
            "tests/test_search.py",
        )
        payload = {
            "experiment": "E005",
            "scope": "synthetic algorithm only; no runtime cost claim",
            "head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "python": platform.python_version(),
            "small_threshold": 8,
            "source_sha256": {
                p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                for p in source_paths
            },
            "manifest": {k: list(v) for k, v in shapes().items()},
            "baseline_counted": False,
            "main_benchmark_static_guidance": False,
            "validation": dict(self.validation),
            "production_baseline_differential_cases": self.bridge.checked,
            "rows": len(self.rows),
            "case_csv_sha256": hashlib.sha256(rows_file.read_bytes()).hexdigest(),
            "all_logical_traces_sha256": self.trace_digest.hexdigest(),
            "summaries": summaries,
            "totals": totals,
        }
        (output / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")
        (output / "traces.json").write_text(json.dumps(self.special, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "validation": dict(self.validation),
                    "production_cases": self.bridge.checked,
                    "totals": totals,
                    "output": str(output),
                },
                indent=2,
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/pf-d031-simulation"))
    args = parser.parse_args()
    experiment = Experiment()
    experiment.correctness()
    print("correctness matrix passed", flush=True)
    experiment.performance()
    experiment.save(args.output)


if __name__ == "__main__":
    main()

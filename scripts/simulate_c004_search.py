"""E011: deterministic synthetic oracle experiment; never runs a real verifier.

This is experimental code, not a proposed production implementation. It compares
mechanical search, prune-time midpoint exploration, and global uniform sampling.
The optional production bridge only calls CoordinateSearch with synthetic facts.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import gzip
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import platform
import random
import runpy
import statistics
import subprocess


ROOT = Path(__file__).resolve().parents[1]
P, R = "P", "R"
STRATEGIES = ("plain", "prune", "uniform_cap", "uniform_matched")


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


@dataclass(frozen=True)
class Case:
    name: str
    family: str
    truth: str
    virtual: bool = False
    hint: int | None = None
    width: int = 0
    position: int = -1


class Stop(Exception):
    pass


class Counterexample(Exception):
    pass


class Search:
    """One fixed Slice; each candidate is evaluated at most once.

    Prune discovery stops at its first contradiction and enters shared closure.
    Uniform samples are a preselected batch after mechanical search, then enter
    the same closure. This scheduling difference is part of the comparison.
    """

    def __init__(
        self, case, strategy, threshold=8, budget=None, fatal=None, limit=None
    ):
        self.case = case
        self.n = len(case.truth)
        self.upper = self.n if case.virtual else self.n - 1
        require(case.virtual or case.truth[-1] == P, "baseline must pass")
        self.strategy = strategy
        self.threshold = threshold
        self.budget = math.ceil(math.log2(self.n + 1)) if budget is None else budget
        self.seen = {self.upper: P}
        self.trace = []
        self.counts = Counter()
        self.pivots = []
        self.discards = []
        self.detected = False
        self.fatal = fatal
        self.limit = limit
        self.status = "SUCCESS"

    def observe(self, index, reason):
        require(0 <= index <= self.upper, ("out of bounds", index))
        if index in self.seen:
            return self.seen[index]
        require(index < self.n, "virtual sentinel was executed")
        if self.limit is not None and len(self.trace) >= self.limit:
            self.status = "INCOMPLETE"
            raise Stop
        value = "I" if index == self.fatal else self.case.truth[index]
        self.trace.append({"index": index, "status": value, "reason": reason})
        category = "refine" if reason.startswith("refine") else reason
        self.counts[category] += 1
        if value == "I":
            self.status = "INDETERMINATE"
            raise Stop
        self.admit(index, value)
        return value

    def admit(self, index, value):
        """Admit a direct fact; conflicting arrivals are not ordinary holes."""
        if index in self.seen:
            if self.seen[index] != value:
                self.status = "NONDETERMINISTIC"
                raise Stop
            return
        before_left = max((i for i in self.seen if i < index), default=None)
        before_right = min((i for i in self.seen if i > index), default=None)
        self.seen[index] = value
        if before_left is not None and before_right is not None:
            if self.seen[before_left] == self.seen[before_right] != value:
                self.pivots.append(index)
        if value == P:
            self.detected |= any(i > index and s == R for i, s in self.seen.items())
        else:
            self.detected |= any(i < index and s == P for i, s in self.seen.items())

    def audit(self, left, right, expected):
        unknown = [i for i in range(left + 1, min(right, self.n)) if i not in self.seen]
        item = {
            "left": left,
            "right": right,
            "expected": expected,
            "unknown": len(unknown),
        }
        self.discards.append(item)
        if any(
            left < i < right and value != expected for i, value in self.seen.items()
        ):
            item["decision"] = "known-counterexample"
            raise Counterexample
        if self.strategy != "prune" or len(unknown) <= self.threshold:
            item["decision"] = "no-exploration"
            return
        if self.counts["explore"] >= self.budget:
            item["decision"] = "budget-exhausted"
            return
        probe = unknown[(len(unknown) - 1) // 2]
        item.update(decision="explore", index=probe)
        if self.observe(probe, "explore") != expected:
            # An existing exact PASS remains valid. The discarded model does not.
            require(
                self.detected, "audit mismatch did not create a direct counterexample"
            )
            raise Counterexample
        # Do not recursively audit the two same-colour subintervals.

    def mechanical(self):
        high = self.upper
        if self.observe(0, "main") == P:
            self.audit(0, high, P)
            return 0
        low = 0
        if self.case.hint is not None and low < self.case.hint < high:
            middle = self.case.hint
            if self.observe(middle, "main") == P:
                self.audit(middle, high, P)
                high = middle
            else:
                self.audit(low, middle, R)
                low = middle
        # Mirror the existing mechanical small-window decision: only at entry.
        if high - low <= 8:
            for index in range(low + 1, min(high, self.n - 1) + 1):
                if self.observe(index, "main") == P:
                    self.audit(index, high, P)
                    return index
            return None if high == self.n else high
        while high - low > 1:
            middle = (low + high) // 2
            if self.observe(middle, "main") == P:
                self.audit(middle, high, P)
                high = middle
            else:
                self.audit(low, middle, R)
                low = middle
        return None if high == self.n else high

    def uniform(self):
        unknown = [i for i in range(self.n) if i not in self.seen]
        count = min(self.budget, len(unknown))
        # Equally spaced quantiles of the unknown candidates; no truth-dependent picks.
        selected = [
            unknown[((2 * j + 1) * len(unknown) - 1) // (2 * count)]
            for j in range(count)
        ]
        require(len(set(selected)) == count, "duplicate uniform point")
        for index in selected:
            self.observe(index, "explore")

    def refine(self):
        # Neighbours of newly discovered islands/holes have first priority.
        for pivot in self.pivots:
            for index in (pivot - 1, pivot + 1):
                if 0 <= index < self.n:
                    self.observe(index, "refine-neighbor")
        while True:
            ordered = sorted(self.seen)
            gap = next(
                (
                    (a, b)
                    for a, b in zip(ordered, ordered[1:])
                    if b - a > 1 and self.seen[a] != self.seen[b]
                ),
                None,
            )
            if gap is None:
                return
            left, right = gap
            if self.pivots:
                origin = min(
                    gap, key=lambda i: (min(abs(i - p) for p in self.pivots), i)
                )
            else:
                origin = left if self.seen[left] == R else right
            anchor = right if origin == left else left
            direction = 1 if anchor > origin else -1
            same = origin
            distance = 1
            while True:
                target = origin + direction * min(distance, abs(anchor - origin))
                if self.observe(target, "refine-expand") != self.seen[origin]:
                    left, right = sorted((same, target))
                    break
                same = target
                distance *= 2
            while right - left > 1:
                middle = (left + right) // 2
                if self.observe(middle, "refine-midpoint") == self.seen[left]:
                    left = middle
                else:
                    right = middle

    def run(self):
        try:
            try:
                self.mechanical()
            except Counterexample:
                pass
            if self.strategy.startswith("uniform"):
                self.uniform()
            self.refine()
        except Stop:
            return self
        self.status = (
            "SUCCESS"
            if any(i < self.n and s == P for i, s in self.seen.items())
            else "NO_PASS"
        )
        self.validate()
        return self

    def inferred(self):
        values = [None] * self.n
        for index, status in self.seen.items():
            if index < self.n:
                values[index] = status
        ordered = sorted(self.seen)
        for left, right in zip(ordered, ordered[1:]):
            if self.seen[left] == self.seen[right]:
                for index in range(left + 1, min(right, self.n)):
                    values[index] = self.seen[left]
            else:
                require(right == left + 1, ("unclosed evidence boundary", left, right))
        require(all(v is not None for v in values), "unanchored inference")
        return "".join(values)

    @property
    def floor(self):
        return min(
            (i for i, s in self.seen.items() if i < self.n and s == P), default=None
        )

    def validate(self):
        require(
            len(self.trace) == len({p["index"] for p in self.trace}),
            "duplicate oracle call",
        )
        require(
            len(self.trace) <= self.n - int(not self.case.virtual),
            "exceeded finite domain",
        )
        require(self.counts["explore"] <= self.budget, "exploration budget exceeded")
        inferred = self.inferred()
        require(
            all(inferred[i] == s for i, s in self.seen.items() if i < self.n),
            "observation overwritten",
        )
        if self.floor is not None:
            require(self.case.truth[self.floor] == P, "floor lacks direct PASS")
            require(
                self.floor == 0 or self.seen.get(self.floor - 1) == R,
                "floor lacks direct predecessor",
            )
        truth_monotone = not any(
            self.case.truth[i] == P and R in self.case.truth[i + 1 :]
            for i in range(self.n)
        )
        if truth_monotone:
            require(inferred == self.case.truth, "monotone oracle result differs")
        if self.strategy == "plain":
            require(
                not self.detected, "ordinary self-generated trace found a contradiction"
            )


def primary_cases():
    for n in (16, 64, 256, 1024):
        for virtual in (False, True):
            for floor in range(n + int(virtual)):
                yield Case(
                    f"mono/{n}/{int(virtual)}/{floor}",
                    "monotone",
                    R * floor + P * (n - floor),
                    virtual,
                )
    for n in (16, 64, 256):
        widths = sorted({1, 2, 4, n // 16, n // 4})
        for virtual in (False, True):
            for floor in (0, n // 4, n // 2):
                for width in widths:
                    for start in range(floor + 1, n - width + int(virtual)):
                        truth = list(R * floor + P * (n - floor))
                        truth[start : start + width] = R * width
                        yield Case(
                            f"hole/{n}/{int(virtual)}/{floor}/{width}/{start}",
                            "hole",
                            "".join(truth),
                            virtual,
                            width=width,
                            position=start,
                        )
            floors = (n // 4, n // 2, 3 * n // 4) + ((n,) if virtual else ())
            for floor in floors:
                for width in widths:
                    for start in range(floor - width):
                        truth = list(R * floor + P * (n - floor))
                        truth[start : start + width] = P * width
                        yield Case(
                            f"island/{n}/{int(virtual)}/{floor}/{width}/{start}",
                            "island",
                            "".join(truth),
                            virtual,
                            width=width,
                            position=start,
                        )
    rng = random.Random(57361)
    for n in (16, 64, 256):
        for virtual in (False, True):
            for probability in (0.1, 0.5, 0.9):
                for serial in range(32):
                    truth = "".join(
                        P if rng.random() < probability else R for _ in range(n)
                    )
                    if not virtual:
                        truth = truth[:-1] + P
                    yield Case(
                        f"random/{n}/{int(virtual)}/{probability}/{serial}",
                        "random",
                        truth,
                        virtual,
                    )
            for offset in (0, 1):
                truth = "".join(P if (i + offset) % 2 else R for i in range(n))
                if not virtual:
                    truth = truth[:-1] + P
                yield Case(
                    f"alternating/{n}/{int(virtual)}/{offset}",
                    "alternating",
                    truth,
                    virtual,
                )
            for serial in range(64):
                truth = list(R * (n // 4) + P * (n - n // 4))
                for _ in range(4):
                    start = rng.randrange(n - 1)
                    width = rng.choice((1, 2, max(1, n // 16)))
                    for index in range(start, min(start + width, n - int(not virtual))):
                        truth[index] = R if truth[index] == P else P
                yield Case(
                    f"multi/{n}/{int(virtual)}/{serial}",
                    "multi",
                    "".join(truth),
                    virtual,
                )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serialise(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def row_for(case, config, sim, plain):
    truth_floor = case.truth.find(P)
    truth_floor = None if truth_floor < 0 else truth_floor
    inferred = sim.inferred()
    nonmonotone = P in case.truth and R in case.truth[case.truth.index(P) :]
    misses = sum(a == P and b == R for a, b in zip(inferred, case.truth))
    missed_pass = sum(a == R and b == P for a, b in zip(inferred, case.truth))
    return {
        "config": config,
        "case": case.name,
        "family": case.family,
        "n": sim.n,
        "virtual": int(case.virtual),
        "left_pass": int(case.truth[0] == P),
        "width": case.width,
        "position": case.position,
        "hint": case.hint,
        "truth": case.truth,
        "strategy": sim.strategy,
        "threshold": sim.threshold,
        "budget": sim.budget,
        "main": sim.counts["main"],
        "explore": sim.counts["explore"],
        "refine": sim.counts["refine"],
        "calls": len(sim.trace),
        "nonmonotone": int(nonmonotone),
        "detected": int(sim.detected),
        "floor": sim.floor,
        "true_floor": truth_floor,
        "exact_floor": int(sim.floor == truth_floor),
        "improved_floor": int(
            sim.floor is not None and (plain.floor is None or sim.floor < plain.floor)
        ),
        "worsened_floor": int(
            plain.floor is not None and (sim.floor is None or sim.floor > plain.floor)
        ),
        "same_floor": int(sim.floor == plain.floor),
        "false_pass": misses,
        "false_pass_delta": misses
        - sum(a == P and b == R for a, b in zip(plain.inferred(), case.truth)),
        "missed_pass": missed_pass,
        "floor_gap": None
        if sim.floor is None or truth_floor is None
        else sim.floor - truth_floor,
        "oracle_delta": len(sim.trace) - len(plain.trace),
    }


def bundle(case, threshold=8, budget=None):
    plain = Search(case, "plain", threshold, budget).run()
    prune = Search(case, "prune", threshold, budget).run()
    uniform = Search(case, "uniform_cap", threshold, budget).run()
    matched = Search(case, "uniform_matched", threshold, prune.counts["explore"]).run()
    require(
        matched.counts["explore"] == prune.counts["explore"],
        "matched exploration spend differs",
    )
    return dict(zip(STRATEGIES, (plain, prune, uniform, matched)))


def production_bridge(cases):
    from pf.coordinate_search import CoordinateSearch
    from pf.schemas import VersionPin

    fixtures = runpy.run_path(str(ROOT / "tests/test_search.py"))
    count = 0
    for case in cases:
        if case.family != "monotone" or len(case.truth) > 64:
            continue
        sim = Search(case, "plain").run()
        observed = []
        cache = {}

        class Evaluator:
            def evaluate(self, vector):
                index = int(vector[0].version) - 1
                if index not in cache:
                    status = P if index == sim.upper else case.truth[index]
                    if index != sim.upper:
                        observed.append(index)
                    identity = f"e011-{index}"
                    cache[index] = fixtures[
                        "probe_pass" if status == P else "probe_rejection"
                    ](vector, identity)
                return cache[index]

        snapshot = fixtures["snapshot_versions"](
            "a", tuple(str(i + 1) for i in range(sim.n))
        )
        outcome = CoordinateSearch().minimize(
            start=(VersionPin(name="a", version=str(sim.upper + 1)),),
            candidates=(snapshot,),
            evaluator=Evaluator(),
        )
        expected_status = (
            "SUCCESS" if sim.floor is not None else "NO_PASS_IN_SEARCH_SPACE"
        )
        require(outcome.status == expected_status, ("production status", case.name))
        if sim.floor is not None:
            require(
                int(outcome.vector[0].version) - 1 == sim.floor,
                ("production floor", case.name),
            )
        require(
            observed == [p["index"] for p in sim.trace],
            ("production trace", case.name, observed, sim.trace),
        )
        count += 1
    return count


def mechanism_checks(maximum):
    checked = 0
    digest = hashlib.sha256()
    for n in range(1, maximum + 1):
        for virtual in (False, True):
            for prefix in itertools.product((R, P), repeat=n if virtual else n - 1):
                truth = "".join(prefix) + ("" if virtual else P)
                case = Case(
                    f"exhaustive/{n}/{int(virtual)}/{truth}",
                    "exhaustive",
                    truth,
                    virtual,
                )
                for strategy, sim in bundle(case, threshold=0, budget=n).items():
                    digest.update(
                        serialise(
                            (case.name, strategy, sim.trace, sim.inferred())
                        ).encode()
                    )
                    checked += 1
    special = {}
    fatal_case = Case("fatal", "mechanism", P * 32, True)
    for strategy in ("prune", "uniform_cap"):
        reference = Search(fatal_case, strategy, threshold=0).run()
        fatal = next(p["index"] for p in reference.trace if p["reason"] == "explore")
        sim = Search(fatal_case, strategy, threshold=0, fatal=fatal).run()
        require(
            sim.status == "INDETERMINATE" and sim.trace[-1]["status"] == "I",
            "fatal suppressed",
        )
        special[f"{strategy}_fatal"] = sim.trace
    case = Case("budget", "mechanism", P * 16 + R + P * 15, True)
    reference = Search(case, "prune", threshold=0, budget=1).run()
    require(
        reference.detected and reference.counts["refine"] > 0,
        "counterexample fixture not reached",
    )
    sim = Search(case, "prune", threshold=0, budget=1, limit=2).run()
    require(sim.status == "INCOMPLETE", "incomplete refinement marked complete")
    spent, before = reference.counts["explore"], len(reference.trace)
    try:
        reference.audit(0, reference.upper, P)
    except Counterexample:
        pass
    else:
        raise AssertionError("known contradiction hidden by exhausted budget")
    reference.refine()
    require(
        reference.counts["explore"] == spent and len(reference.trace) == before,
        "same Slice reset/repeated execution",
    )
    special["budget_refinement"] = {"complete": reference.trace, "limited": sim.trace}
    before = dict(reference.seen)
    try:
        reference.admit(0, R)
    except Stop:
        require(
            reference.status == "NONDETERMINISTIC" and reference.seen == before,
            "conflict overwrote fact",
        )
    else:
        raise AssertionError("same-point conflict not stopped")
    special["same_point_conflict"] = {
        "existing": P,
        "incoming": R,
        "index": 0,
        "status": reference.status,
    }
    # Separate instances stand for distinct exact contexts; neither shares observations.
    second = Search(
        Case("new-context", "mechanism", R * 16 + P * 16, True),
        "prune",
        threshold=0,
        budget=1,
    ).run()
    require(
        second.trace[0]["status"] == R and second.counts["explore"] == 1,
        "new context reused observations/budget",
    )
    return {
        "exhaustive_runs": checked,
        "exhaustive_trace_sha256": digest.hexdigest(),
        "special": special,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exhaustive-max", type=int, default=10)
    parser.add_argument("--skip-production-bridge", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = list(primary_cases())
    groups = defaultdict(list)
    pairs = defaultdict(Counter)
    traces = {}
    digest = hashlib.sha256()
    csv_buffer = io.StringIO(newline="")
    writer = None
    total = 0

    def record(case, config, threshold=8, budget=None):
        nonlocal writer, total
        runs = bundle(case, threshold, budget)
        plain, prune, cap, matched = (runs[s] for s in STRATEGIES)
        for strategy, sim in runs.items():
            row = row_for(case, config, sim, plain)
            if writer is None:
                writer = csv.DictWriter(csv_buffer, fieldnames=tuple(row))
                writer.writeheader()
            writer.writerow(row)
            total += 1
            group_names = [
                f"{config}/{case.family}/{strategy}",
                f"{config}/sentinel-{int(case.virtual)}/{strategy}",
                f"{config}/left-pass-{int(case.truth[0] == P)}/{strategy}",
            ]
            if config == "primary":
                group_names += [
                    f"{config}/{case.family}/N{sim.n}/{strategy}",
                    f"{config}/{case.family}/width-{case.width}/{strategy}",
                    f"{config}/{case.family}/left-pass-{int(case.truth[0] == P)}/{strategy}",
                ]
            for group in group_names:
                groups[group].append(row)
            digest.update(
                serialise(
                    (config, case.name, strategy, sim.trace, sim.inferred())
                ).encode()
            )
        for other in (cap, matched):
            pair = pairs[f"{config}/{case.family}/prune-vs-{other.strategy}"]
            pair["cases"] += 1
            pair["prune_only_detected"] += int(prune.detected and not other.detected)
            pair["other_only_detected"] += int(other.detected and not prune.detected)
            pair["both_detected"] += int(prune.detected and other.detected)
            pair["neither_detected"] += int(not prune.detected and not other.detected)
            pair["prune_calls"] += len(prune.trace)
            pair["other_calls"] += len(other.trace)
        labels = []
        if config == "primary":
            labels.append(f"first/{case.family}/virtual-{int(case.virtual)}")
            if prune.detected and not matched.detected:
                labels.append("prune-only-vs-matched")
            if matched.detected and not prune.detected:
                labels.append("matched-only-vs-prune")
            if prune.floor is not None and (
                plain.floor is None or prune.floor < plain.floor
            ):
                labels.append("prune-recovers-lower-pass")
            if plain.floor is not None and (
                prune.floor is None or prune.floor > plain.floor
            ):
                labels.append("prune-worsens-floor")
            if case.family == "hole" and not any(s.detected for s in runs.values()):
                labels.append("all-strategies-miss-hole")
            if case.virtual and P not in case.truth:
                labels.append("virtual-no-pass")
        for label in labels:
            if label not in traces:
                traces[label] = {
                    "case": case.__dict__,
                    "config": config,
                    "runs": {
                        strategy: {
                            "trace": sim.trace,
                            "discards": sim.discards,
                            "floor": sim.floor,
                            "inferred": sim.inferred(),
                            "counts": dict(sim.counts),
                            "detected": sim.detected,
                        }
                        for strategy, sim in runs.items()
                    },
                }

    for case in cases:
        record(case, "primary")
    print(f"primary: {len(cases)} cases, {total} strategy runs", flush=True)
    sensitivity = [
        case
        for case in cases
        if len(case.truth) == 64 and case.family in ("hole", "island", "monotone")
    ]
    for threshold, budget in itertools.product((4, 16, 64), (0, 1, 7)):
        for case in sensitivity:
            record(case, f"T{threshold}-B{budget}", threshold, budget)
    # A synthetic deterministic hint is only a probe-order control, not a ty model.
    for fraction in (0.25, 0.75):
        for case in sensitivity:
            hinted = Case(**{**case.__dict__, "hint": int(len(case.truth) * fraction)})
            record(hinted, f"hint-{fraction}")
    print(f"all performance rows: {total}; checking exhaustive mechanisms", flush=True)
    mechanisms = mechanism_checks(args.exhaustive_max)
    bridge = 0 if args.skip_production_bridge else production_bridge(cases)
    aggregates = {}
    for key, rows in sorted(groups.items()):
        calls = [r["calls"] for r in rows]
        nonlinear = sum(r["nonmonotone"] for r in rows)
        aggregates[key] = {
            "cases": len(rows),
            "nonmonotone_cases": nonlinear,
            "detected": sum(r["detected"] for r in rows),
            "detection_rate": sum(r["detected"] for r in rows) / nonlinear
            if nonlinear
            else None,
            "calls": sum(calls),
            "calls_mean": statistics.mean(calls),
            "calls_p95": percentile(calls, 0.95),
            "calls_max": max(calls),
            "main": sum(r["main"] for r in rows),
            "explore": sum(r["explore"] for r in rows),
            "refine": sum(r["refine"] for r in rows),
            "exact_floor": sum(r["exact_floor"] for r in rows),
            "improved_floor": sum(r["improved_floor"] for r in rows),
            "worsened_floor": sum(r["worsened_floor"] for r in rows),
            "same_floor": sum(r["same_floor"] for r in rows),
            "false_pass": sum(r["false_pass"] for r in rows),
            "false_pass_worsened_cases": sum(r["false_pass_delta"] > 0 for r in rows),
            "missed_pass": sum(r["missed_pass"] for r in rows),
            "oracle_delta": sum(r["oracle_delta"] for r in rows),
        }
    raw_csv = csv_buffer.getvalue().encode()
    (output / "cases.csv.gz").write_bytes(gzip.compress(raw_csv, mtime=0))
    (output / "traces.json").write_text(
        json.dumps(traces, indent=2, sort_keys=True) + "\n"
    )
    (output / "mechanisms.json").write_text(
        json.dumps(mechanisms, indent=2, sort_keys=True) + "\n"
    )
    sources = (
        "scripts/simulate_c004_search.py",
        "src/pf/coordinate_search.py",
        "tests/test_search.py",
    )
    summary = {
        "experiment": "E011",
        "schema": "e011-synthetic-v1",
        "head": subprocess.check_output(
            ("git", "rev-parse", "HEAD"), cwd=ROOT, text=True
        ).strip(),
        "python": platform.python_version(),
        "source_sha256": {name: sha(ROOT / name) for name in sources},
        "protocol": {
            "threshold": 8,
            "budget": "ceil(log2(N+1))",
            "small_threshold": 8,
            "seed": 57361,
            "exhaustive_max": args.exhaustive_max,
            "uniform": "whole-domain unknown-candidate quantiles, one batch after mechanical search",
            "matched": "offline diagnostic: uniform uses prune's actual exploration count",
            "prune": "one midpoint per eligible ordinary discard; first contradiction enters common closure",
            "closure": "neighbors of new counterexample pivots, then gallop/binary all adjacent opposite observations",
            "scope": "fixed one-dimensional Slice; no real resolver/verifier, no full multi-coordinate scheduler, no projection",
        },
        "primary_cases": len(cases),
        "sensitivity_cases_per_config": len(sensitivity),
        "performance_rows": total,
        "mechanisms": {k: v for k, v in mechanisms.items() if k != "special"},
        "production_baseline_differential_cases": bridge,
        "csv_sha256": hashlib.sha256(raw_csv).hexdigest(),
        "all_performance_trace_sha256": digest.hexdigest(),
        "groups": aggregates,
        "pairs": {k: dict(v) for k, v in sorted(pairs.items())},
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(
        serialise(
            {
                k: summary[k]
                for k in (
                    "primary_cases",
                    "performance_rows",
                    "production_baseline_differential_cases",
                    "mechanisms",
                )
            }
        )
    )


if __name__ == "__main__":
    main()

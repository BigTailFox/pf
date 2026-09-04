from __future__ import annotations

from collections.abc import Callable
import inspect
from pathlib import Path
from threading import Barrier, Event
from typing import Literal, cast

import pytest

from pf.errors import ConfigurationError, InfrastructureError
from pf.failure import FailurePolicy
from pf.policy import evaluation_policy_identity
from pf.schemas.config import EffectiveConfig, RunLimits, TestConfig as PfTestConfig
from pf.schemas.evaluation import (
    ActivityEvent,
    Attempt,
    AttemptFailureScope,
    AttemptIdentity,
    BaselineDetailIdentity,
    BaselineIndeterminate,
    CellCompletedEvent,
    CellContextEvent,
    CellFailureScope,
    CellMatrixEvent,
    CheckCellOutcome,
    FailureDetail,
    NormalExit,
    PassEvaluation,
    ProcessObservation,
    ProcessResult,
    ProcessTerminalUnavailable,
    StaticUnchangedEvaluation,
    TyCheck,
    VerifierPass,
    VerificationJournal,
    ty_diagnostic_digest,
)
from pf.schemas.project import (
    Cell,
    DependencySourceRoute,
    PackagePlan,
    Proposal,
    SourceIdentity,
    SourcePlan,
)
from pf.schemas.report import CellIndeterminate, CellResult
from pf.snapshot import SnapshotBuilder, SourceSnapshot
from pf.verification import (
    CellSearchOperations,
    CheckCellOperations,
    CheckVerificationRun,
    SearchVerificationRun,
    SmokeVerificationRun,
    VerificationRunner,
)


HOST = "x86_64-unknown-linux-gnu"


class _Events:
    def __init__(self) -> None:
        self.items: list[ActivityEvent] = []

    def consume(self, event: ActivityEvent) -> None:
        self.items.append(event)


class _SearchOperation:
    def __init__(
        self,
        run: Callable[[PackagePlan, Cell, SourceSnapshot, SourcePlan], CellResult],
    ) -> None:
        self._run = run

    def search(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CellResult:
        return self._run(package, cell, snapshot, source_plan)


class _CheckOperation:
    def __init__(
        self,
        run: Callable[
            [PackagePlan, Cell, SourceSnapshot, SourcePlan], CheckCellOutcome
        ],
    ) -> None:
        self._run = run

    def check(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> CheckCellOutcome:
        return self._run(package, cell, snapshot, source_plan)


class _SmokeOperation:
    def __init__(
        self,
        run: Callable[
            [PackagePlan, Cell, SourceSnapshot, SourcePlan], BaselineIndeterminate
        ],
    ) -> None:
        self._run = run

    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        source_plan: SourcePlan,
    ) -> BaselineIndeterminate:
        return self._run(package, cell, snapshot, source_plan)


def _cell(
    python_minor: str = "3.10",
    *,
    target: str = HOST,
) -> Cell:
    return Cell(
        package="demo",
        target=target,
        python_minor=python_minor,
        extra_surface=(),
    )


def _package(
    cells: tuple[Cell, ...],
    *,
    full_contract: bool = True,
) -> PackagePlan:
    return PackagePlan(
        name="demo",
        pyproject_path="pyproject.toml",
        config=EffectiveConfig(
            test=PfTestConfig(
                command=("pytest",) if full_contract else None,
            ),
        ),
        declarations=(),
        cells=cells,
        source_routes=(),
        test_group_present=full_contract,
    )


def _case(
    tmp_path: Path,
    *,
    cells: tuple[Cell, ...] | None = None,
    full_contract: bool = True,
) -> tuple[SourceSnapshot, PackagePlan]:
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = SnapshotBuilder.without_processes().build(tmp_path)
    package = _package(cells or (_cell(),), full_contract=full_contract)
    return snapshot, package


def _attempt(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    cell: Cell,
    requested_resolution: Literal["highest", "lowest-direct", "exact-vector"],
) -> Attempt:
    return Attempt.from_identity(
        AttemptIdentity(
            source_snapshot_digest=snapshot.identity.digest,
            cell=cell,
            requested_resolution=requested_resolution,
            requested_managed_vector=(
                () if requested_resolution == "exact-vector" else None
            ),
            active_declaration_ids=cell.active_declaration_ids,
            source_plan_identity=SourcePlan.for_package(package, "SEARCH").identity,
            evaluation_policy_identity=evaluation_policy_identity(package.config),
            resolution_context_digest="context",
            harness_policy_identity=(
                "original-harness-v1"
                if requested_resolution == "highest"
                else "harness-relaxation-v1"
            ),
            harness_baseline_digest=(
                None if requested_resolution == "highest" else "baseline"
            ),
            selected_candidate_evidence_digest=(
                "candidate" if requested_resolution == "exact-vector" else None
            ),
        )
    )


def _attempt_failure(
    attempt: Attempt,
    *,
    process: ProcessObservation | None = None,
):
    return FailurePolicy().classify(
        scope=AttemptFailureScope(attempt=attempt),
        cause="SOURCE_FAILURE" if process is None else "TOOL_FAILURE",
        stage="prepare",
        process=process,
        detail=(
            FailureDetail(code="offline", message="registry unavailable")
            if process is None
            else None
        ),
    )


def _cell_failure(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    cell: Cell,
):
    return FailurePolicy().classify(
        scope=CellFailureScope(
            package=package.name,
            cell=cell,
            source_snapshot_digest=snapshot.identity.digest,
            evaluation_policy_identity=evaluation_policy_identity(package.config),
        ),
        cause="SOURCE_FAILURE",
        stage="candidate-discovery",
        process=None,
        detail=FailureDetail(code="offline", message="registry unavailable"),
    )


def _search_indeterminate(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    cell: Cell,
) -> CellIndeterminate:
    failure = _cell_failure(package, snapshot, cell)
    return CellIndeterminate(
        cell=cell,
        phase=failure.stage,
        failure_id=failure.failure_id,
        failure_records=(failure,),
    )


def _check_indeterminate(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    cell: Cell,
    *,
    role: Literal["declaration-capture", "declaration"] = "declaration",
    process: ProcessObservation | None = None,
) -> CheckCellOutcome:
    attempt = _attempt(package, snapshot, cell, "lowest-direct")
    failure = _attempt_failure(attempt, process=process)
    return CheckCellOutcome(
        status="INDETERMINATE",
        role=role,
        attempt=attempt,
        failure=failure,
        failure_process=(
            process if isinstance(process, ProcessTerminalUnavailable) else None
        ),
    )


def _check_pass(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    cell: Cell,
) -> CheckCellOutcome:
    attempt = _attempt(package, snapshot, cell, "lowest-direct")
    proposal = Proposal(
        proposal_id="passing-check",
        attempt_id=attempt.attempt_id,
        snapshot_digest=snapshot.identity.digest,
        cell=cell,
        managed_vector=(),
        fixed_declaration_ids=(),
        resolved_graph=(),
        policy_identity=evaluation_policy_identity(package.config),
    )
    process = ProcessResult(
        exit_code=0,
        duration_seconds=0.1,
        stdout="[]",
        stderr="",
    )
    evaluation = PassEvaluation(
        proposal=proposal,
        static=StaticUnchangedEvaluation(
            proposal=proposal,
            ty=TyCheck(process=process, diagnostics=()),
            baseline_digest=ty_diagnostic_digest(()),
        ),
        verifier=VerifierPass(terminal=NormalExit(exit_code=0)),
    )
    return CheckCellOutcome(
        status="PASS",
        role="declaration",
        attempt=attempt,
        evaluation=evaluation,
    )


def _smoke_indeterminate(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    cell: Cell,
) -> BaselineIndeterminate:
    attempt = _attempt(package, snapshot, cell, "highest")
    return BaselineIndeterminate(
        attempt=attempt,
        failure=_attempt_failure(attempt),
    )


def _search_request(
    package: PackagePlan,
    snapshot: SourceSnapshot,
    operation: CellSearchOperations,
    *,
    jobs: int = 1,
    duration: float | None = None,
) -> SearchVerificationRun:
    return SearchVerificationRun(
        package=package,
        source_plan=SourcePlan.for_package(package, "SEARCH"),
        snapshot=snapshot,
        operation=operation,
        limits=_limits(max_cells=jobs, duration=duration),
    )


def _limits(
    *,
    max_cells: int = 1,
    duration: float | None = None,
) -> RunLimits:
    return RunLimits(
        max_cells=max_cells,
        ty_jobs=max_cells,
        test_jobs=max_cells,
        max_duration_seconds=duration,
    )


class TestVerificationRunnerRequest:
    def test_unknown_request_fails_closed(self) -> None:
        with pytest.raises(TypeError, match="verification request"):
            VerificationRunner(
                events=_Events(),
                logs=None,
                host_target=HOST,
            ).run(cast(SearchVerificationRun, object()))

    def test_search_runs_the_complete_host_cell_set(self, tmp_path: Path) -> None:
        host = _cell()
        other = _cell(target="aarch64-apple-darwin")
        snapshot, package = _case(tmp_path, cells=(other, host))
        source_plan = SourcePlan.for_package(package, "SEARCH")
        received: list[tuple[PackagePlan, Cell, SourceSnapshot, SourcePlan]] = []
        events = _Events()
        outcome = _search_indeterminate(package, snapshot, host)
        operation = _SearchOperation(
            lambda package, cell, snapshot, plan: received.append(
                (package, cell, snapshot, plan)
            )
            or outcome
        )

        results = VerificationRunner(
            events=events,
            logs=None,
            host_target=HOST,
        ).run(
            SearchVerificationRun(
                package=package,
                source_plan=source_plan,
                snapshot=snapshot,
                operation=operation,
                limits=_limits(),
            )
        )

        assert results == (outcome,)
        assert received == [(package, host, snapshot, source_plan)]
        assert received[0][0] is package
        assert received[0][2] is snapshot
        assert received[0][3] is source_plan
        matrix = next(item for item in events.items if isinstance(item, CellMatrixEvent))
        assert matrix.cells == (host,)
        completion = next(
            item for item in events.items if isinstance(item, CellCompletedEvent)
        )
        assert completion.total == 1
        snapshot.close()

    @pytest.mark.parametrize(
        ("request_type", "command"),
        [
            (CheckVerificationRun, "search"),
            (SmokeVerificationRun, "check"),
            (SearchVerificationRun, "smoke"),
        ],
    )
    def test_command_discriminator_is_not_a_constructor_argument(
        self,
        request_type: type,
        command: str,
    ) -> None:
        assert "command" not in inspect.signature(request_type).parameters
        with pytest.raises(TypeError, match="command"):
            request_type(command=command)

    def test_check_and_smoke_return_their_exact_outcome_family(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, package = _case(tmp_path)
        cell = package.cells[0]
        check = _check_indeterminate(package, snapshot, cell)
        smoke = _smoke_indeterminate(package, snapshot, cell)
        runner = VerificationRunner(events=_Events(), logs=None, host_target=HOST)

        check_results = runner.run(
            CheckVerificationRun(
                package=package,
                source_plan=SourcePlan.for_package(package, "SEARCH"),
                snapshot=snapshot,
                operation=_CheckOperation(lambda *_: check),
                limits=_limits(),
            )
        )
        smoke_results = runner.run(
            SmokeVerificationRun(
                package=package,
                source_plan=SourcePlan.for_package(package, "DEVELOPMENT"),
                snapshot=snapshot,
                operation=_SmokeOperation(lambda *_: smoke),
                limits=_limits(),
            )
        )

        assert check_results == (check,)
        assert smoke_results == (smoke,)
        snapshot.close()


class TestVerificationRunnerAdmission:
    def test_duplicate_host_cell_identity_stops_before_matrix(
        self,
        tmp_path: Path,
    ) -> None:
        cell = _cell()
        snapshot, package = _case(tmp_path, cells=(cell, cell))
        events = _Events()

        with pytest.raises(ValueError, match="unique identities"):
            VerificationRunner(events=events, logs=None, host_target=HOST).run(
                _search_request(
                    package,
                    snapshot,
                    _SearchOperation(
                        lambda *_: pytest.fail(
                            "duplicate cells must not start operation"
                        )
                    ),
                )
            )
        assert events.items == []
        snapshot.close()

    @pytest.mark.parametrize("jobs", [0, -1, True, "2"])
    def test_invalid_jobs_stop_before_operation(
        self,
        tmp_path: Path,
        jobs: object,
    ) -> None:
        snapshot, package = _case(tmp_path)
        with pytest.raises(ValueError, match="max_cells|integer"):
            RunLimits(
                max_cells=cast(int, jobs),
                ty_jobs=1,
                test_jobs=1,
            )
        snapshot.close()

    @pytest.mark.parametrize("duration", [0.0, -1.0, float("inf"), float("nan")])
    def test_invalid_search_duration_stops_before_operation(
        self,
        tmp_path: Path,
        duration: float,
    ) -> None:
        snapshot, package = _case(tmp_path)
        with pytest.raises(ValueError, match="duration"):
            _limits(duration=duration)
        snapshot.close()

    def test_source_mode_mismatch_stops_before_matrix(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        events = _Events()
        request = SearchVerificationRun(
            package=package,
            source_plan=SourcePlan.for_package(package, "DEVELOPMENT"),
            snapshot=snapshot,
            operation=_SearchOperation(
                lambda *_: pytest.fail("source mismatch must not start operation")
            ),
            limits=_limits(),
        )

        with pytest.raises(ValueError, match="source mode does not match"):
            VerificationRunner(events=events, logs=None, host_target=HOST).run(
                request
            )
        assert events.items == []
        snapshot.close()

    def test_source_routes_mismatch_stops_before_matrix(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        registry = SourceIdentity(kind="registry")
        request = SearchVerificationRun(
            package=package,
            source_plan=SourcePlan(
                source_mode="SEARCH",
                routes=(
                    DependencySourceRoute(
                        dependency="other",
                        development_source=registry,
                        search_source=registry,
                    ),
                ),
            ),
            snapshot=snapshot,
            operation=_SearchOperation(
                lambda *_: pytest.fail("route mismatch must not start operation")
            ),
            limits=_limits(),
        )

        with pytest.raises(ValueError, match="source plan does not match"):
            VerificationRunner(
                events=_Events(), logs=None, host_target=HOST
            ).run(request)
        snapshot.close()

    def test_check_contract_error_precedes_empty_host_error(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, package = _case(
            tmp_path,
            cells=(_cell(target="aarch64-apple-darwin"),),
            full_contract=False,
        )
        request = CheckVerificationRun(
            package=package,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
            snapshot=snapshot,
            operation=cast(
                CheckCellOperations,
                object(),
            ),
            limits=_limits(),
        )

        with pytest.raises(ConfigurationError, match="test-command"):
            VerificationRunner(
                events=_Events(), logs=None, host_target=HOST
            ).run(request)
        snapshot.close()

    def test_search_empty_host_set_still_requires_full_contract(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, package = _case(
            tmp_path,
            cells=(_cell(target="aarch64-apple-darwin"),),
            full_contract=False,
        )
        events = _Events()
        with pytest.raises(ConfigurationError, match="test-command"):
            VerificationRunner(
                events=events,
                logs=None,
                host_target=HOST,
            ).run(
                _search_request(
                    package,
                    snapshot,
                    _SearchOperation(
                        lambda *_: pytest.fail("empty Run must not start operation")
                    ),
                )
            )
        matrix = next(item for item in events.items if isinstance(item, CellMatrixEvent))
        assert matrix.cells == ()
        assert not any(isinstance(item, CellCompletedEvent) for item in events.items)
        snapshot.close()


class TestVerificationRunnerLifecycle:
    def test_initial_context_happens_before_operation(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        events = _Events()
        cell = package.cells[0]

        def run(*_: object) -> CellResult:
            contexts = [
                item for item in events.items if isinstance(item, CellContextEvent)
            ]
            assert contexts == [
                CellContextEvent(cell=cell, detail=BaselineDetailIdentity())
            ]
            return _search_indeterminate(package, snapshot, cell)

        VerificationRunner(events=events, logs=None, host_target=HOST).run(
            _search_request(package, snapshot, _SearchOperation(run))
        )

        assert sum(isinstance(item, CellContextEvent) for item in events.items) == 1
        snapshot.close()

    def test_completion_order_is_real_and_return_order_is_canonical(
        self,
        tmp_path: Path,
    ) -> None:
        slow = _cell("3.12")
        fast = _cell("3.10")
        snapshot, package = _case(tmp_path, cells=(slow, fast))
        barrier = Barrier(2)
        release_slow = Event()
        events = _Events()

        def run(
            package: PackagePlan,
            cell: Cell,
            snapshot: SourceSnapshot,
            _source_plan: SourcePlan,
        ) -> CellResult:
            barrier.wait()
            if cell == slow:
                assert release_slow.wait(timeout=5)
            return _search_indeterminate(package, snapshot, cell)

        class ReleasingEvents(_Events):
            def consume(self, event: ActivityEvent) -> None:
                super().consume(event)
                if isinstance(event, CellCompletedEvent) and event.cell == fast:
                    release_slow.set()

        releasing = ReleasingEvents()
        results = VerificationRunner(
            events=releasing,
            logs=None,
            host_target=HOST,
        ).run(
            _search_request(
                package,
                snapshot,
                _SearchOperation(run),
                jobs=2,
            )
        )

        completions = [
            item.cell
            for item in releasing.items
            if isinstance(item, CellCompletedEvent)
        ]
        assert completions == [fast, slow]
        assert [result.cell for result in results] == [fast, slow]
        assert events.items == []
        snapshot.close()

    def test_deadline_cell_is_not_started(self, tmp_path: Path) -> None:
        first = _cell("3.10")
        pending = _cell("3.11")
        snapshot, package = _case(tmp_path, cells=(first, pending))
        events = _Events()
        outcomes = VerificationRunner(
            events=events,
            logs=None,
            host_target=HOST,
            monotonic=iter((0.0, 0.0, 1.0)).__next__,
        ).run(
            _search_request(
                package,
                snapshot,
                _SearchOperation(
                    lambda package, cell, snapshot, _plan: _search_indeterminate(
                        package, snapshot, cell
                    )
                ),
                duration=0.5,
            )
        )

        contexts = [
            item.cell for item in events.items if isinstance(item, CellContextEvent)
        ]
        assert contexts == [first]
        assert outcomes[1].cell == pending
        assert isinstance(outcomes[1], CellIndeterminate)
        assert outcomes[1].phase == "scheduler-deadline"
        assert outcomes[1].baseline_attempt is None
        snapshot.close()


class TestVerificationRunnerProjection:
    def test_conflicting_portable_failure_id_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        first = _cell("3.10")
        second = _cell("3.11")
        snapshot, package = _case(tmp_path, cells=(first, second))
        first_failure = _cell_failure(package, snapshot, first)
        second_failure = _cell_failure(package, snapshot, second).model_copy(
            update={"failure_id": first_failure.failure_id}
        )
        outcomes = {
            first: CellIndeterminate(
                cell=first,
                phase=first_failure.stage,
                failure_id=first_failure.failure_id,
                failure_records=(first_failure,),
            ),
            second: CellIndeterminate.model_construct(
                cell=second,
                status="CELL_INDETERMINATE",
                phase=second_failure.stage,
                failure_id=second_failure.failure_id,
                failure_records=(second_failure,),
                baseline_attempt=None,
                static_baseline=None,
                baseline=None,
                candidate_snapshots=(),
                coordinate_failure=None,
                failure_runtime_runs=(),
            ),
        }

        with pytest.raises(ValueError, match="failure ID"):
            VerificationRunner(
                events=_Events(),
                logs=None,
                host_target=HOST,
            ).run(
                _search_request(
                    package,
                    snapshot,
                    _SearchOperation(
                        lambda _package, cell, _snapshot, _plan: outcomes[cell]
                    ),
                )
            )
        snapshot.close()

    def test_wrong_outcome_family_fails_before_completion(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        cell = package.cells[0]
        events = _Events()

        class WrongOperation:
            def check(self, **_: object) -> CellIndeterminate:
                return _search_indeterminate(package, snapshot, cell)

        with pytest.raises(TypeError, match="check operation"):
            VerificationRunner(events=events, logs=None, host_target=HOST).run(
                CheckVerificationRun(
                    package=package,
                    source_plan=SourcePlan.for_package(package, "SEARCH"),
                    snapshot=snapshot,
                    operation=cast(CheckCellOperations, WrongOperation()),
                    limits=_limits(),
                )
            )
        assert not any(isinstance(item, CellCompletedEvent) for item in events.items)
        snapshot.close()

    def test_outcome_cell_mismatch_fails_before_completion(self, tmp_path: Path) -> None:
        cell = _cell()
        other = _cell("3.11")
        snapshot, package = _case(tmp_path, cells=(cell,))
        events = _Events()

        with pytest.raises(ValueError, match="outcome cell"):
            VerificationRunner(events=events, logs=None, host_target=HOST).run(
                _search_request(
                    package,
                    snapshot,
                    _SearchOperation(
                        lambda *_: _search_indeterminate(package, snapshot, other)
                    ),
                )
            )
        assert not any(isinstance(item, CellCompletedEvent) for item in events.items)
        snapshot.close()

    def test_check_role_and_process_enter_the_journal(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        cell = package.cells[0]
        unavailable = ProcessTerminalUnavailable(
            duration_seconds=0.2,
            detail="runner returned no terminal status",
        )
        outcome = _check_indeterminate(
            package,
            snapshot,
            cell,
            role="declaration-capture",
            process=unavailable,
        )
        journals: list[VerificationJournal] = []
        associations: list[tuple[str, str, ProcessObservation]] = []

        class Logs:
            run_id = "check-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                journals.append(journal)
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                associations.append((report_generation_id, failure_id, result))

        VerificationRunner(events=_Events(), logs=Logs(), host_target=HOST).run(
            CheckVerificationRun(
                package=package,
                source_plan=SourcePlan.for_package(package, "SEARCH"),
                snapshot=snapshot,
                operation=_CheckOperation(lambda *_: outcome),
                limits=_limits(),
            )
        )

        assert outcome.failure is not None
        assert journals[-1].entries[0].role == "declaration-capture"
        assert journals[-1].entries[0].attempt == outcome.attempt
        assert associations == [
            ("journal:check-run", outcome.failure.failure_id, unavailable),
            ("journal:check-run", outcome.failure.failure_id, unavailable),
        ]
        snapshot.close()

    def test_smoke_failure_uses_baseline_role(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        cell = package.cells[0]
        outcome = _smoke_indeterminate(package, snapshot, cell)
        journals: list[VerificationJournal] = []

        class Logs:
            run_id = "smoke-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                journals.append(journal)
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                return

        VerificationRunner(events=_Events(), logs=Logs(), host_target=HOST).run(
            SmokeVerificationRun(
                package=package,
                source_plan=SourcePlan.for_package(package, "DEVELOPMENT"),
                snapshot=snapshot,
                operation=_SmokeOperation(lambda *_: outcome),
                limits=_limits(),
            )
        )

        assert journals[-1].entries[0].role == "baseline"
        assert journals[-1].entries[0].attempt == outcome.attempt
        snapshot.close()

    def test_search_attempt_and_cell_scopes_use_closed_roles(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, package = _case(tmp_path)
        cell = package.cells[0]
        highest = _attempt_failure(_attempt(package, snapshot, cell, "highest"))
        probe = _attempt_failure(_attempt(package, snapshot, cell, "exact-vector"))
        terminal = _cell_failure(package, snapshot, cell)
        outcome = CellIndeterminate(
            cell=cell,
            phase=terminal.stage,
            failure_id=terminal.failure_id,
            failure_records=(highest, probe, terminal),
        )
        journals: list[VerificationJournal] = []

        class Logs:
            run_id = "search-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                journals.append(journal)
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                return

        VerificationRunner(events=_Events(), logs=Logs(), host_target=HOST).run(
            _search_request(package, snapshot, _SearchOperation(lambda *_: outcome))
        )

        assert {entry.role for entry in journals[-1].entries} == {
            "baseline",
            "probe",
        }
        assert sum(entry.role == "probe" for entry in journals[-1].entries) == 2
        snapshot.close()

    def test_search_rejects_lowest_direct_attempt(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        cell = package.cells[0]
        lowest = _attempt_failure(_attempt(package, snapshot, cell, "lowest-direct"))
        outcome = CellIndeterminate(
            cell=cell,
            phase=lowest.stage,
            failure_id=lowest.failure_id,
            failure_records=(lowest,),
        )

        with pytest.raises(ValueError, match="lowest-direct"):
            VerificationRunner(
                events=_Events(), logs=None, host_target=HOST
            ).run(
                _search_request(
                    package,
                    snapshot,
                    _SearchOperation(lambda *_: outcome),
                )
            )
        snapshot.close()


class TestVerificationRunnerDurability:
    def test_passing_cell_finalizes_an_empty_journal(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        cell = package.cells[0]
        outcome = _check_pass(package, snapshot, cell)
        events = _Events()
        journals: list[VerificationJournal] = []

        class Logs:
            run_id = "passing-check"

            def write_journal(self, journal: VerificationJournal) -> Path:
                journals.append(journal)
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                raise AssertionError("PASS must not create a process association")

        results = VerificationRunner(
            events=events,
            logs=Logs(),
            host_target=HOST,
        ).run(
            CheckVerificationRun(
                package=package,
                source_plan=SourcePlan.for_package(package, "SEARCH"),
                snapshot=snapshot,
                operation=_CheckOperation(lambda *_: outcome),
                limits=_limits(),
            )
        )

        assert results == (outcome,)
        assert len(journals) == 1
        assert journals[0].entries == ()
        completion = next(
            item for item in events.items if isinstance(item, CellCompletedEvent)
        )
        assert completion.diagnose_available is False
        snapshot.close()

    def test_empty_search_persists_final_journal(self, tmp_path: Path) -> None:
        snapshot, package = _case(
            tmp_path,
            cells=(_cell(target="aarch64-apple-darwin"),),
            full_contract=True,
        )
        journals: list[VerificationJournal] = []

        class Logs:
            run_id = "empty-search"

            def write_journal(self, journal: VerificationJournal) -> Path:
                journals.append(journal)
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                return

        results = VerificationRunner(
            events=_Events(),
            logs=Logs(),
            host_target=HOST,
        ).run(
            _search_request(
                package,
                snapshot,
                _SearchOperation(
                    lambda *_: pytest.fail("empty Run must not start operation")
                ),
            )
        )

        assert results == ()
        assert len(journals) == 1
        assert journals[0].entries == ()
        snapshot.close()

    def test_empty_search_final_persist_failure_raises(self, tmp_path: Path) -> None:
        snapshot, package = _case(
            tmp_path,
            cells=(_cell(target="aarch64-apple-darwin"),),
            full_contract=True,
        )

        class FailingLogs:
            run_id = "empty-search"

            def write_journal(self, journal: VerificationJournal) -> Path:
                raise InfrastructureError("could not finalize verification journal")

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                return

        with pytest.raises(InfrastructureError, match="finalize"):
            VerificationRunner(
                events=_Events(),
                logs=FailingLogs(),
                host_target=HOST,
            ).run(
                _search_request(
                    package,
                    snapshot,
                    _SearchOperation(
                        lambda *_: pytest.fail("empty Run must not start operation")
                    ),
                )
            )
        snapshot.close()

    def test_journal_is_durable_before_diagnose_and_finalized(
        self,
        tmp_path: Path,
    ) -> None:
        snapshot, package = _case(tmp_path)
        outcome = _search_indeterminate(package, snapshot, package.cells[0])
        timeline: list[tuple[str, object]] = []

        class Events:
            def consume(self, event: ActivityEvent) -> None:
                if isinstance(event, CellCompletedEvent):
                    timeline.append(("completion", event.diagnose_available))

        class Logs:
            run_id = "verification-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                timeline.append(("journal", len(journal.entries)))
                return tmp_path / "journal.json"

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                return

        VerificationRunner(events=Events(), logs=Logs(), host_target=HOST).run(
            _search_request(package, snapshot, _SearchOperation(lambda *_: outcome))
        )

        assert timeline == [
            ("journal", 1),
            ("completion", True),
            ("journal", 1),
        ]
        snapshot.close()

    def test_without_logs_never_advertises_diagnose(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        outcome = _search_indeterminate(package, snapshot, package.cells[0])
        events = _Events()

        VerificationRunner(events=events, logs=None, host_target=HOST).run(
            _search_request(package, snapshot, _SearchOperation(lambda *_: outcome))
        )

        completion = next(
            item for item in events.items if isinstance(item, CellCompletedEvent)
        )
        assert completion.diagnose_available is False
        snapshot.close()

    def test_persist_failure_emits_false_then_raises(self, tmp_path: Path) -> None:
        snapshot, package = _case(tmp_path)
        outcome = _search_indeterminate(package, snapshot, package.cells[0])
        events = _Events()

        class FailingLogs:
            run_id = "verification-run"

            def write_journal(self, journal: VerificationJournal) -> Path:
                raise InfrastructureError("could not write verification journal")

            def associate(
                self,
                report_generation_id: str,
                failure_id: str,
                result: ProcessObservation,
            ) -> None:
                raise AssertionError("failed journal must prevent association")

        with pytest.raises(InfrastructureError, match="verification journal"):
            VerificationRunner(
                events=events,
                logs=FailingLogs(),
                host_target=HOST,
            ).run(
                _search_request(
                    package,
                    snapshot,
                    _SearchOperation(lambda *_: outcome),
                )
            )

        completion = next(
            item for item in events.items if isinstance(item, CellCompletedEvent)
        )
        assert completion.diagnose_available is False
        snapshot.close()

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import sys

import pytest

from evaluation_fixtures import evaluation_assembly

from pf.adapters.process import SubprocessRunner
from pf.adapters.test_command import ConfiguredVerifier
from pf.baseline import HighestVersionVerifier
from pf.check import CompatibilityChecker
from pf.errors import InfrastructureError
from pf.evaluation import RuntimeEvaluator
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    EnvironmentVariable,
    HighestVersionPass,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
    VerifierIndeterminate,
    VerifierPass,
    VerifierRejected,
    VerifierRequest,
)
from pf.schemas.project import SourcePlan
from pf.snapshot import SnapshotBuilder


def _run(
    root: Path,
    *args: str,
    nodeids: tuple[str, ...] = (),
):
    return ConfiguredVerifier(SubprocessRunner()).run(
        VerifierRequest(
            command=(sys.executable, "-m", "pytest", "--no-header", "-q", *args),
            cwd=root,
            environment=(
                EnvironmentVariable(name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"),
            ),
            timeout_seconds=30,
            failed_case_nodeids=nodeids,
        )
    )


class _CountingRunner:
    def __init__(self) -> None:
        self.count = 0
        self._inner = SubprocessRunner()

    def run(self, spec: ProcessSpec) -> ProcessResult | ProcessTerminalUnavailable:
        self.count += 1
        return self._inner.run(spec)


def _run_counted(
    root: Path,
    runner: _CountingRunner,
    *args: str,
    nodeids: tuple[str, ...] = (),
):
    return ConfiguredVerifier(runner).run(
        VerifierRequest(
            command=(sys.executable, "-m", "pytest", "--no-header", "-q", *args),
            cwd=root,
            environment=(
                EnvironmentVariable(name="PYTEST_DISABLE_PLUGIN_AUTOLOAD", value="1"),
            ),
            timeout_seconds=30,
            failed_case_nodeids=nodeids,
        )
    )


def _write(root: Path, name: str, source: str) -> None:
    (root / name).write_text(source, encoding="utf-8")


class _RecordingRunner:
    def __init__(self) -> None:
        self.specs: list[ProcessSpec] = []
        self.prune_request: object = None

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        environment = {item.name: item.value for item in spec.environment}
        nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
        evidence = Path(environment["PF_PYTEST_OBSERVER_DIR"])
        request_path = environment.get("PF_PYTEST_PRUNE_REQUEST")
        if request_path is not None:
            self.prune_request = json.loads(
                Path(request_path).read_text(encoding="utf-8")
            )
        summary = {
            "execution_mode": "serial",
            "facts": [{"kind": "TEST_FAILED", "phase": "call"}],
            "finalized": True,
            "protocol": "pf-pytest-observer-v1",
            "pytest_version": "9.1.1",
            "python_implementation": "cpython",
            "python_minor": "3.10",
            "run_nonce": nonce,
        }
        (evidence / f"summary-{'a' * 32}.json").write_bytes(
            (json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        cases_directory = environment.get("PF_PYTEST_OBSERVER_CASES_DIR")
        projection = environment.get("PF_PYTEST_OBSERVER_CASES_PROJECTION")
        if cases_directory is not None and projection is not None:
            document = {
                "collection_completed": True,
                "collection_failed": False,
                "nodeids": ["test_example.py::test_bad"],
                "projection": projection,
                "protocol": "pf-pytest-observer-cases-v1",
                "role": "serial",
                "run_nonce": nonce,
            }
            Path(cases_directory, f"cases-{'b' * 32}.json").write_bytes(
                (
                    json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode()
            )
        return ProcessResult(exit_code=1, duration_seconds=0.1)


class TestFailedCasePruning:
    def test_original_command_adds_failed_nodeids(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "test_example.py",
            "def test_bad():\n    assert False\n"
            "def test_ok():\n    pass\n",
        )

        run = _run(tmp_path)

        assert isinstance(run.authoritative, VerifierRejected)
        assert run.failed_case_additions == ("test_example.py::test_bad",)

    def test_failed_set_rejects_without_running_the_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        _write(
            tmp_path,
            "test_example.py",
            "from pathlib import Path\n"
            "def test_bad():\n    assert False\n"
            "def test_ok():\n"
            "    Path('original-ran').write_text('yes')\n",
        )

        run = _run(tmp_path, nodeids=("test_example.py::test_bad",))

        assert isinstance(run.authoritative, VerifierRejected)
        assert run.failed_case_additions == ()
        assert not (tmp_path / "original-ran").exists()

    def test_failed_set_requested_nodeids_do_not_enter_argv(
        self,
        tmp_path: Path,
    ) -> None:
        runner = _RecordingRunner()
        ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=("pytest", "tests"),
                cwd=tmp_path,
                timeout_seconds=30,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )

        assert len(runner.specs) == 1
        assert "test_example.py::test_bad" not in runner.specs[0].argv
        environment = {item.name: item.value for item in runner.specs[0].environment}
        assert environment["PF_PYTEST_PRUNE_REQUEST"]
        assert environment["PF_PYTEST_PRUNE_NONCE"] == environment[
            "PF_PYTEST_OBSERVER_NONCE"
        ]
        assert runner.prune_request == ["test_example.py::test_bad"]

    def test_failed_set_pass_requires_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        _write(
            tmp_path,
            "test_example.py",
            "def test_once_failed():\n    pass\n"
            "def test_other():\n    assert False\n",
        )

        run = _run(tmp_path, nodeids=("test_example.py::test_once_failed",))

        assert isinstance(run.authoritative, VerifierRejected)
        assert run.failed_case_additions == ("test_example.py::test_other",)

    def test_failed_set_selection_only_collects_requested_nodeids(
        self,
        tmp_path: Path,
    ) -> None:
        _write(
            tmp_path,
            "test_a_bad.py",
            "from pathlib import Path\n"
            "def test_bad():\n"
            "    Path('bad-ran').write_text('yes')\n"
            "    assert False\n",
        )
        _write(
            tmp_path,
            "test_b_ok.py",
            "from pathlib import Path\n"
            "def test_ok():\n"
            "    Path('ok-ran').write_text('yes')\n",
        )

        run = _run(tmp_path, nodeids=("test_b_ok.py::test_ok",))

        assert isinstance(run.authoritative, VerifierRejected)
        assert (tmp_path / "ok-ran").exists()
        assert (tmp_path / "bad-ran").exists()
        assert run.failed_case_additions == ("test_a_bad.py::test_bad",)

    def test_missing_nodeid_falls_back_to_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        _write(tmp_path, "test_example.py", "def test_ok():\n    pass\n")

        run = _run(tmp_path, nodeids=("test_example.py::test_missing",))

        assert isinstance(run.authoritative, VerifierPass)
        assert run.failed_case_additions == ()

    @pytest.mark.parametrize("exit_code", (1, 2, 3, 4, 5))
    def test_normal_nonzero_is_rejected_for_failed_set_and_original(
        self,
        tmp_path: Path,
        exit_code: int,
    ) -> None:
        class ExitRunner(_RecordingRunner):
            def run(self, spec: ProcessSpec) -> ProcessResult:
                super().run(spec)
                return ProcessResult(exit_code=exit_code, duration_seconds=0.1)

        verifier = ConfiguredVerifier(ExitRunner())
        failed_set = verifier.run(
            VerifierRequest(
                command=("pytest",),
                cwd=tmp_path,
                timeout_seconds=30,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )
        original = verifier.run(
            VerifierRequest(
                command=("pytest",),
                cwd=tmp_path,
                timeout_seconds=30,
            )
        )

        assert isinstance(failed_set.authoritative, VerifierRejected)
        assert failed_set.authoritative.terminal.exit_code == exit_code
        assert failed_set.failed_case_additions == ()
        assert isinstance(original.authoritative, VerifierRejected)
        assert original.failed_case_additions == ("test_example.py::test_bad",)

    def test_failed_set_timeout_does_not_fall_back(self, tmp_path: Path) -> None:
        class TimeoutRunner:
            def __init__(self) -> None:
                self.specs: list[ProcessSpec] = []

            def run(self, spec: ProcessSpec) -> ProcessResult:
                self.specs.append(spec)
                return ProcessResult(
                    exit_code=143,
                    timed_out=True,
                    duration_seconds=30.1,
                )

        runner = TimeoutRunner()
        run = ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=("pytest",),
                cwd=tmp_path,
                timeout_seconds=30,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )

        assert isinstance(run.authoritative, VerifierIndeterminate)
        assert len(runner.specs) == 1
        assert run.failed_case_additions == ()

    def test_unexpected_collected_item_falls_back_to_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        class UnexpectedRunner(_RecordingRunner):
            def run(self, spec: ProcessSpec) -> ProcessResult:
                environment = {item.name: item.value for item in spec.environment}
                projection = environment.get("PF_PYTEST_OBSERVER_CASES_PROJECTION")
                result = super().run(spec)
                if projection == "collected":
                    nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                    cases_directory = Path(environment["PF_PYTEST_OBSERVER_CASES_DIR"])
                    document = {
                        "collection_completed": True,
                        "collection_failed": False,
                        "nodeids": [
                            "test_example.py::test_bad",
                            "test_example.py::test_extra",
                        ],
                        "projection": "collected",
                        "protocol": "pf-pytest-observer-cases-v1",
                        "role": "serial",
                        "run_nonce": nonce,
                    }
                    Path(cases_directory, f"cases-{'b' * 32}.json").write_bytes(
                        (
                            json.dumps(document, sort_keys=True, separators=(",", ":"))
                            + "\n"
                        ).encode()
                    )
                return result

        runner = UnexpectedRunner()
        run = ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=("pytest",),
                cwd=tmp_path,
                timeout_seconds=30,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert len(runner.specs) == 2
        assert run.failed_case_additions == ("test_example.py::test_bad",)

    def test_generic_command_rejects_failed_case_nodeids(
        self,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(InfrastructureError, match="direct pytest"):
            ConfiguredVerifier(_RecordingRunner()).run(
                VerifierRequest(
                    command=("custom-verifier",),
                    cwd=tmp_path,
                    timeout_seconds=30,
                    failed_case_nodeids=("test_example.py::test_bad",),
                )
            )

    def test_generic_command_does_not_collect_additions(
        self,
        tmp_path: Path,
    ) -> None:
        class GenericRunner:
            def run(self, spec: ProcessSpec) -> ProcessResult:
                return ProcessResult(exit_code=1, duration_seconds=0.1)

        run = ConfiguredVerifier(GenericRunner()).run(
            VerifierRequest(
                command=("custom-verifier",),
                cwd=tmp_path,
                timeout_seconds=30,
            )
        )

        assert run.failed_case_additions == ()
        assert isinstance(run.authoritative, VerifierRejected)

    def test_empty_collection_falls_back_to_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        _write(
            tmp_path,
            "test_example.py",
            "def test_bad():\n    assert False\n"
            "def test_ok():\n    pass\n",
        )

        run = _run(
            tmp_path,
            "-k",
            "test_ok",
            nodeids=("test_example.py::test_bad",),
        )

        assert isinstance(run.authoritative, VerifierPass)
        assert run.failed_case_additions == ()

    def test_collection_error_falls_back_to_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        _write(tmp_path, "test_example.py", "raise ImportError('collection')\n")
        _write(tmp_path, "test_other.py", "def test_ok():\n    pass\n")

        runner = _CountingRunner()
        run = _run_counted(
            tmp_path,
            runner,
            nodeids=("test_example.py::test_bad",),
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert runner.count == 2
        assert run.failed_case_additions == ()

    def test_dynamic_parametrization_falls_back_to_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        _write(
            tmp_path,
            "conftest.py",
            "def pytest_generate_tests(metafunc):\n"
            "    if metafunc.definition.name == 'test_bad':\n"
            "        metafunc.parametrize('value', (1, 2))\n",
        )
        _write(
            tmp_path,
            "test_example.py",
            "from pathlib import Path\n"
            "def test_bad(value):\n    assert False\n"
            "def test_ok():\n    Path('original-ran').write_text('yes')\n",
        )

        runner = _CountingRunner()
        run = _run_counted(
            tmp_path,
            runner,
            nodeids=("test_example.py::test_bad",),
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert runner.count == 2
        assert run.failed_case_additions
        assert all(
            item.startswith("test_example.py::test_bad")
            for item in run.failed_case_additions
        )

    def test_duplicate_collected_item_falls_back_to_original_command(
        self,
        tmp_path: Path,
    ) -> None:
        _write(
            tmp_path,
            "conftest.py",
            "def pytest_collection_modifyitems(items):\n"
            "    if items:\n"
            "        items.append(items[0])\n",
        )
        _write(
            tmp_path,
            "test_example.py",
            "from pathlib import Path\n"
            "def test_bad():\n    assert False\n"
            "def test_ok():\n    Path('original-ran').write_text('yes')\n",
        )

        runner = _CountingRunner()
        run = _run_counted(
            tmp_path,
            runner,
            nodeids=("test_example.py::test_bad",),
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert runner.count == 2
        assert run.failed_case_additions == ("test_example.py::test_bad",)

    @pytest.mark.parametrize(
        "mutate",
        (
            "missing",
            "invalid",
            "empty",
            "collection-failed",
            "duplicate",
        ),
    )
    def test_collection_artifact_failures_fall_back_to_original_command(
        self,
        tmp_path: Path,
        mutate: str,
    ) -> None:
        class MutatingRunner(_RecordingRunner):
            def run(self, spec: ProcessSpec) -> ProcessResult:
                result = super().run(spec)
                environment = {item.name: item.value for item in spec.environment}
                if environment.get("PF_PYTEST_OBSERVER_CASES_PROJECTION") != "collected":
                    return result
                path = Path(
                    environment["PF_PYTEST_OBSERVER_CASES_DIR"],
                    f"cases-{'b' * 32}.json",
                )
                if mutate == "missing":
                    path.unlink()
                    return result
                if mutate == "invalid":
                    path.write_text("{", encoding="utf-8")
                    return result
                nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                nodeids = ["test_example.py::test_bad"]
                collection_completed = True
                collection_failed = False
                if mutate == "empty":
                    nodeids = []
                elif mutate == "collection-failed":
                    collection_completed = False
                    collection_failed = True
                elif mutate == "duplicate":
                    nodeids = [
                        "test_example.py::test_bad",
                        "test_example.py::test_bad",
                    ]
                document = {
                    "collection_completed": collection_completed,
                    "collection_failed": collection_failed,
                    "nodeids": nodeids,
                    "projection": "collected",
                    "protocol": "pf-pytest-observer-cases-v1",
                    "role": "serial",
                    "run_nonce": nonce,
                }
                path.write_bytes(
                    (
                        json.dumps(document, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    ).encode()
                )
                return result

        runner = MutatingRunner()
        run = ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=("pytest",),
                cwd=tmp_path,
                timeout_seconds=30,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert len(runner.specs) == 2
        assert run.failed_case_additions == ("test_example.py::test_bad",)

    def test_worker_unexpected_item_falls_back_without_rejecting(
        self,
        tmp_path: Path,
    ) -> None:
        class WorkerRunner(_RecordingRunner):
            def run(self, spec: ProcessSpec) -> ProcessResult:
                result = super().run(spec)
                environment = {item.name: item.value for item in spec.environment}
                if environment.get("PF_PYTEST_OBSERVER_CASES_PROJECTION") != "collected":
                    return result
                nonce = environment["PF_PYTEST_OBSERVER_NONCE"]
                directory = Path(environment["PF_PYTEST_OBSERVER_CASES_DIR"])
                worker = {
                    "collection_completed": True,
                    "collection_failed": False,
                    "nodeids": ["test_example.py::test_extra"],
                    "projection": "collected",
                    "protocol": "pf-pytest-observer-cases-v1",
                    "role": "worker",
                    "run_nonce": nonce,
                }
                Path(directory, f"cases-{'c' * 32}.json").write_bytes(
                    (
                        json.dumps(worker, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    ).encode()
                )
                return result

        runner = WorkerRunner()
        run = ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=("pytest",),
                cwd=tmp_path,
                timeout_seconds=30,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert len(runner.specs) == 2
        assert run.failed_case_additions == ("test_example.py::test_bad",)

    @pytest.mark.parametrize(
        ("factory", "reason"),
        (
            (
                lambda: ProcessResult(signal=9, duration_seconds=0.1),
                "process-signaled",
            ),
            (
                lambda: ProcessResult(
                    start_error="executable not found",
                    duration_seconds=0.1,
                ),
                "process-start-failed",
            ),
            (lambda: ProcessTerminalUnavailable(), "terminal-unavailable"),
        ),
    )
    def test_incomplete_failed_set_does_not_fall_back(
        self,
        tmp_path: Path,
        factory: Callable[[], ProcessResult | ProcessTerminalUnavailable],
        reason: str,
    ) -> None:
        class IncompleteRunner:
            def __init__(self) -> None:
                self.specs: list[ProcessSpec] = []

            def run(
                self, spec: ProcessSpec
            ) -> ProcessResult | ProcessTerminalUnavailable:
                self.specs.append(spec)
                return factory()

        runner = IncompleteRunner()
        run = ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=("pytest",),
                cwd=tmp_path,
                timeout_seconds=30,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )

        assert isinstance(run.authoritative, VerifierIndeterminate)
        assert run.authoritative.reason == reason
        assert len(runner.specs) == 1
        assert run.failed_case_additions == ()

    @pytest.mark.parametrize("flag", ("--lf", "--ff", "--sw"))
    def test_lastfailed_flags_match_a_single_original_command(
        self,
        tmp_path: Path,
        flag: str,
    ) -> None:
        _write(
            tmp_path,
            "test_example.py",
            "def test_bad():\n    assert False\n"
            "def test_ok():\n    pass\n",
        )

        original = _run(tmp_path, flag)
        two_phase = _run(tmp_path, flag, nodeids=("test_example.py::test_bad",))

        assert isinstance(original.authoritative, VerifierRejected)
        assert isinstance(two_phase.authoritative, VerifierRejected)
        assert type(original.authoritative) is type(two_phase.authoritative)

    def test_xdist_without_controller_collection_falls_back(
        self,
        tmp_path: Path,
    ) -> None:
        pytest.importorskip("xdist")
        _write(
            tmp_path,
            "test_example.py",
            "from pathlib import Path\n"
            "def test_bad():\n    assert False\n"
            "def test_ok():\n"
            "    Path('original-ran').write_text('yes')\n",
        )

        runner = _CountingRunner()
        run = ConfiguredVerifier(runner).run(
            VerifierRequest(
                command=(
                    sys.executable,
                    "-m",
                    "pytest",
                    "--no-header",
                    "-q",
                    "-p",
                    "xdist.plugin",
                    "-n",
                    "2",
                    "--dist",
                    "load",
                ),
                cwd=tmp_path,
                environment=(
                    EnvironmentVariable(
                        name="PYTEST_DISABLE_PLUGIN_AUTOLOAD",
                        value="1",
                    ),
                ),
                timeout_seconds=60,
                failed_case_nodeids=("test_example.py::test_bad",),
            )
        )

        assert isinstance(run.authoritative, VerifierRejected)
        assert runner.count == 2
        assert run.failed_case_additions == ("test_example.py::test_bad",)

    def test_portable_schemas_omit_pruning_context(self) -> None:
        from pf.schemas.evaluation import (
            FailureRecord,
            VerificationJournal,
            VerificationJournalV1,
        )
        from pf.schemas.report import (
            FailureRecordV1,
            PackageFloorReportV1Wire,
            ProbeRejection,
        )

        forbidden = {
            "failed_case_nodeids",
            "failed_case_additions",
            "fallback_reason",
            "collected_nodeids",
            "requested_nodeids",
            "selection_applied",
        }
        for model in (
            FailureRecord,
            FailureRecordV1,
            VerificationJournal,
            VerificationJournalV1,
            PackageFloorReportV1Wire,
            ProbeRejection,
        ):
            names = set(model.model_fields)
            assert names.isdisjoint(forbidden)
            assert "PruningObservation" not in model.__name__


class _OverlayRunner:
    def __init__(self) -> None:
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.specs.append(spec)
        environment = {item.name: item.value for item in spec.environment}
        observer_directory = environment.get("PF_PYTEST_OBSERVER_DIR")
        nonce = environment.get("PF_PYTEST_OBSERVER_NONCE")
        if observer_directory is not None and nonce is not None:
            summary = {
                "execution_mode": "unknown",
                "facts": [],
                "finalized": True,
                "protocol": "pf-pytest-observer-v1",
                "pytest_version": "unknown",
                "python_implementation": "cpython",
                "python_minor": "3.12",
                "run_nonce": nonce,
            }
            Path(observer_directory, f"summary-{'a' * 32}.json").write_bytes(
                (
                    json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode()
            )
        return ProcessResult(exit_code=0, duration_seconds=0.1)


class TestPublicOperations:
    def test_smoke_cell_runs_one_original_pytest_with_overlay(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = []

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=tmp_path).target
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        runner = _OverlayRunner()
        assembly = evaluation_assembly(highest=())
        runtime = RuntimeEvaluator(
            static=assembly.static,
            verifier=ConfiguredVerifier(runner),
        )
        result = HighestVersionVerifier(
            environments=assembly.environments,
            static=assembly.static,
            full=runtime,
        ).verify(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "DEVELOPMENT"),
        )

        assert isinstance(result, HighestVersionPass)
        assert len(runner.specs) == 1
        assert "--maxfail=1" in runner.specs[0].argv
        assert any(
            token.startswith("cache_dir=") for token in runner.specs[0].argv
        )
        environment = {item.name: item.value for item in runner.specs[0].environment}
        assert "PF_PYTEST_PRUNE_REQUEST" not in environment

    def test_check_runs_one_original_pytest_with_overlay(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "0.1.0"

[dependency-groups]
test = []

[tool.pf]
pythons = ["3.10"]
platforms = ["x86_64-unknown-linux-gnu"]
test-command = ["pytest"]
""".strip()
            + "\n",
            encoding="utf-8",
        )
        package = ProjectLoader().load(root=tmp_path).target
        snapshot = SnapshotBuilder.without_processes().build(tmp_path)
        runner = _OverlayRunner()
        assembly = evaluation_assembly(highest=(), lowest=())
        runtime = RuntimeEvaluator(
            static=assembly.static,
            verifier=ConfiguredVerifier(runner),
        )
        result = CompatibilityChecker(
            environments=assembly.environments,
            static=assembly.static,
            full=runtime,
        ).check(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            source_plan=SourcePlan.for_package(package, "SEARCH"),
        )

        assert result.status == "PASS"
        assert len(runner.specs) == 1
        assert "--maxfail=1" in runner.specs[0].argv
        environment = {item.name: item.value for item in runner.specs[0].environment}
        assert "PF_PYTEST_PRUNE_REQUEST" not in environment


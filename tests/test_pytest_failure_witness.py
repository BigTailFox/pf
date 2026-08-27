from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pf import _pytest_failure_witness as witness


@pytest.fixture(autouse=True)
def _reset_witness(monkeypatch: pytest.MonkeyPatch):
    importlib.reload(witness)
    for variable in (
        "PF_PYTEST_WITNESS_DIR",
        "PF_PYTEST_WITNESS_NONCE",
        "PF_PYTEST_PROGRESS_DIR",
        "PF_PYTEST_PROGRESS_NONCE",
        "PF_PYTEST_FAILURE_DETAILS_DIR",
    ):
        monkeypatch.delenv(variable, raising=False)
    yield
    importlib.reload(witness)


def _session(
    *,
    items: object = None,
    collectonly: bool = False,
    pluginmanager: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            option=SimpleNamespace(collectonly=collectonly),
            pluginmanager=pluginmanager,
        ),
        items=items,
    )


def _finish_command(excinfo: object = None) -> None:
    hook = witness.pytest_cmdline_main(SimpleNamespace())
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(excinfo=excinfo))


def _enable_progress(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    *,
    nonce: str = "nonce",
) -> None:
    monkeypatch.setenv("PF_PYTEST_PROGRESS_DIR", str(directory))
    monkeypatch.setenv("PF_PYTEST_PROGRESS_NONCE", nonce)
    monkeypatch.setenv("PF_PYTEST_WITNESS_NONCE", nonce)


class TestPytestFailureWitnessEvents:
    def test_pytest_collectreport_records_a_collection_failure(self) -> None:
        witness.pytest_collectreport(
            SimpleNamespace(failed=True, nodeid="tests/test_bad.py")
        )

        assert witness._facts == {("COLLECTION_FAILED", "collect")}
        assert witness._failure_details == {"tests/test_bad.py": "collect"}

    def test_pytest_collectreport_ignores_a_success(self) -> None:
        witness.pytest_collectreport(SimpleNamespace(failed=False))

        assert witness._facts == set()

    @pytest.mark.parametrize("phase", ("setup", "call", "teardown"))
    def test_pytest_runtest_logreport_records_a_test_failure(
        self,
        phase: str,
    ) -> None:
        witness.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when=phase, nodeid="tests/test_bad.py::test_bad")
        )

        assert witness._facts == {("TEST_FAILED", phase)}
        assert witness._failure_details == {"tests/test_bad.py::test_bad": phase}

    def test_pytest_runtest_logreport_ignores_a_non_test_phase(self) -> None:
        witness.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when="collect", nodeid="test_bad.py")
        )

        assert witness._facts == set()

    def test_pytest_runtest_logreport_ignores_a_success(self) -> None:
        witness.pytest_runtest_logreport(
            SimpleNamespace(failed=False, when="call", nodeid="test_ok.py")
        )

        assert witness._facts == set()

    def test_pytest_internalerror_records_an_internal_error(self) -> None:
        witness.pytest_internalerror()

        assert witness._facts == {("INTERNAL_ERROR", "pytest")}

    @pytest.mark.parametrize(
        "nodeid",
        (
            None,
            "",
            "x" * 4_097,
            "test_bad.py::test_bad\nvalue",
            "test_bad.py::test_bad\x7fvalue",
            "test_bad.py::test_bad\ud800value",
        ),
        ids=(
            "non-string",
            "empty",
            "oversized",
            "control",
            "c1-control",
            "surrogate",
        ),
    )
    def test_pytest_runtest_logreport_invalidates_an_unsafe_nodeid(
        self,
        nodeid: object,
    ) -> None:
        witness.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when="call", nodeid=nodeid)
        )

        assert witness._failure_details_valid is False

    def test_pytest_runtest_logreport_accepts_a_safe_nodeid(self) -> None:
        witness.pytest_runtest_logreport(
            SimpleNamespace(
                failed=True,
                when="call",
                nodeid="test_ok.py::test_ok",
            )
        )

        assert witness._failure_details_valid is True

    def test_pytest_runtest_logreport_preserves_the_first_failure_phase(self) -> None:
        report = SimpleNamespace(
            failed=True,
            when="setup",
            nodeid="tests/test_bad.py::test_bad",
        )
        witness.pytest_runtest_logreport(report)
        report.when = "teardown"

        witness.pytest_runtest_logreport(report)

        assert witness._failure_details == {report.nodeid: "setup"}

    def test_pytest_runtest_logreport_invalidates_excess_failure_details(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(witness, "_MAX_FAILURE_DETAILS", 1)
        for nodeid in ("test_one.py::test_bad", "test_two.py::test_bad"):
            witness.pytest_runtest_logreport(
                SimpleNamespace(failed=True, when="call", nodeid=nodeid)
            )

        assert witness._failure_details_valid is False
        assert witness._failure_details == {}

    def test_pytest_runtest_logreport_ignores_details_after_invalidation(
        self,
    ) -> None:
        witness._failure_details_valid = False

        witness.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when="call", nodeid="test_bad.py::test_bad")
        )

        assert witness._failure_details == {}

    def test_pytest_runtest_logreport_invalidates_unreadable_nodeid(self) -> None:
        class Report:
            failed = True
            when = "call"

            @property
            def nodeid(self) -> str:
                raise RuntimeError("unreadable")

        witness.pytest_runtest_logreport(Report())

        assert witness._failure_details_valid is False


class TestPytestFailureWitnessExecutionMode:
    def test_pytest_sessionstart_selects_serial_without_xdist(self) -> None:
        witness.pytest_sessionstart(_session())

        assert witness._execution_mode == "serial"

    @staticmethod
    def _patch_xdist(
        monkeypatch: pytest.MonkeyPatch,
        *,
        controller: bool,
        worker: bool,
    ) -> object:
        import xdist

        class PluginManager:
            @staticmethod
            def hasplugin(name: str) -> bool:
                return name == "xdist"

        monkeypatch.setattr(xdist, "is_xdist_controller", lambda session: controller)
        monkeypatch.setattr(xdist, "is_xdist_worker", lambda session: worker)
        return _session(pluginmanager=PluginManager())

    @pytest.mark.parametrize(
        ("controller", "worker"),
        ((True, False), (False, True)),
        ids=("controller", "worker"),
    )
    def test_pytest_sessionstart_selects_xdist_for_one_role(
        self,
        monkeypatch: pytest.MonkeyPatch,
        controller: bool,
        worker: bool,
    ) -> None:
        session = self._patch_xdist(
            monkeypatch,
            controller=controller,
            worker=worker,
        )

        witness.pytest_sessionstart(session)

        assert witness._execution_mode == "xdist"

    def test_pytest_sessionstart_fails_closed_for_both_xdist_roles(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = self._patch_xdist(monkeypatch, controller=True, worker=True)

        witness.pytest_sessionstart(session)

        assert witness._execution_mode == "unknown"

    def test_pytest_sessionstart_selects_serial_without_an_xdist_role(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = self._patch_xdist(monkeypatch, controller=False, worker=False)

        witness.pytest_sessionstart(session)

        assert witness._execution_mode == "serial"

    def test_pytest_sessionstart_fails_closed_when_xdist_probe_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import xdist

        class PluginManager:
            @staticmethod
            def hasplugin(name: str) -> bool:
                return name == "xdist"

        def fail(session: object) -> bool:
            raise RuntimeError("unavailable")

        monkeypatch.setattr(xdist, "is_xdist_controller", fail)

        witness.pytest_sessionstart(_session(pluginmanager=PluginManager()))

        assert witness._execution_mode == "unknown"


class TestPytestFailureWitnessProgress:
    def test_pytest_collection_finish_initializes_progress(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "serial"
        items = [SimpleNamespace(nodeid="test_one.py"), SimpleNamespace(nodeid="test_two.py")]

        witness.pytest_collection_finish(_session(items=items))

        assert json.loads((tmp_path / "progress.json").read_text()) == {
            "completed": 0,
            "protocol": "pf-pytest-progress-v1",
            "run_nonce": "nonce",
            "total": 2,
            "unit": "tests",
        }

    def test_pytest_collection_finish_ignores_xdist_progress(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "xdist"

        witness.pytest_collection_finish(_session(items=[]))

        assert not (tmp_path / "progress.json").exists()

    def test_pytest_collection_finish_ignores_failed_collection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "serial"
        witness._facts.add(("COLLECTION_FAILED", "collect"))

        witness.pytest_collection_finish(_session(items=[]))

        assert not (tmp_path / "progress.json").exists()

    def test_pytest_collection_finish_ignores_collect_only_session(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "serial"

        witness.pytest_collection_finish(_session(items=[], collectonly=True))

        assert not (tmp_path / "progress.json").exists()

    def test_pytest_collection_finish_ignores_non_list_items(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "serial"

        witness.pytest_collection_finish(_session(items=()))

        assert not (tmp_path / "progress.json").exists()

    def test_pytest_collection_finish_ignores_an_invalid_nodeid(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "serial"

        witness.pytest_collection_finish(
            _session(items=[SimpleNamespace(nodeid=None)])
        )

        assert not (tmp_path / "progress.json").exists()

    def test_pytest_collection_finish_ignores_duplicate_nodeids(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "serial"
        items = [
            SimpleNamespace(nodeid="duplicate"),
            SimpleNamespace(nodeid="duplicate"),
        ]

        witness.pytest_collection_finish(_session(items=items))

        assert not (tmp_path / "progress.json").exists()

    def test_pytest_collection_finish_requires_a_progress_directory(
        self,
    ) -> None:
        witness._execution_mode = "serial"

        witness.pytest_collection_finish(_session(items=[]))

        assert witness._progress_remaining is None

    def test_pytest_collection_finish_suppresses_progress_commit_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        _enable_progress(monkeypatch, blocked)
        witness._execution_mode = "serial"

        witness.pytest_collection_finish(_session(items=[]))

        assert not (blocked / "progress.json").exists()

    def test_pytest_runtest_logfinish_advances_known_test(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        witness._execution_mode = "serial"
        witness.pytest_collection_finish(
            _session(items=[SimpleNamespace(nodeid="test_one.py")])
        )

        witness.pytest_runtest_logfinish("test_one.py", None)

        assert json.loads((tmp_path / "progress.json").read_text())["completed"] == 1

    def test_pytest_runtest_logfinish_ignores_unknown_test(self) -> None:
        witness._progress_remaining = {"test_one.py"}

        witness.pytest_runtest_logfinish("test_two.py", None)

        assert witness._progress_completed == 0


class TestPytestFailureWitnessFinalization:
    def test_pytest_cmdline_main_commits_protocol_artifacts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for variable in (
            "PF_PYTEST_WITNESS_DIR",
            "PF_PYTEST_PROGRESS_DIR",
            "PF_PYTEST_FAILURE_DETAILS_DIR",
        ):
            monkeypatch.setenv(variable, str(tmp_path))
        monkeypatch.setenv("PF_PYTEST_WITNESS_NONCE", "nonce")
        monkeypatch.setenv("PF_PYTEST_PROGRESS_NONCE", "nonce")
        witness._execution_mode = "serial"
        witness._facts.add(("TEST_FAILED", "call"))
        witness._failure_details["tests/test_bad.py::test_bad"] = "call"
        witness._progress_remaining = set()
        witness._progress_completed = 1
        witness._progress_total = 1

        _finish_command()

        summary = json.loads(next(tmp_path.glob("summary-*.json")).read_text())
        details = json.loads(next(tmp_path.glob("details-*.json")).read_text())
        progress = json.loads((tmp_path / "progress.json").read_text())
        assert summary["facts"] == [{"kind": "TEST_FAILED", "phase": "call"}]
        assert summary["run_nonce"] == "nonce"
        assert details["first"] == {
            "nodeid": "tests/test_bad.py::test_bad",
            "phase": "call",
        }
        assert details["total"] == 1
        assert progress["completed"] == progress["total"] == 1

    def test_pytest_cmdline_main_records_a_hookwrapper_error(self) -> None:
        _finish_command(excinfo=RuntimeError("failed"))

        assert witness._facts == {("INTERNAL_ERROR", "pytest")}

    def test_pytest_cmdline_main_suppresses_missing_output_configuration(self) -> None:
        _finish_command()

        assert witness._facts == set()

    def test_pytest_cmdline_main_omits_invalid_failure_details(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PF_PYTEST_WITNESS_DIR", str(tmp_path))
        monkeypatch.setenv("PF_PYTEST_FAILURE_DETAILS_DIR", str(tmp_path))
        monkeypatch.setenv("PF_PYTEST_WITNESS_NONCE", "nonce")
        witness._failure_details_valid = False
        witness._failure_details = {"test_bad.py": "call"}

        _finish_command()

        assert not tuple(tmp_path.glob("details-*.json"))

    def test_pytest_cmdline_main_omits_empty_failure_details(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PF_PYTEST_WITNESS_DIR", str(tmp_path))
        monkeypatch.setenv("PF_PYTEST_FAILURE_DETAILS_DIR", str(tmp_path))
        monkeypatch.setenv("PF_PYTEST_WITNESS_NONCE", "nonce")
        witness._failure_details_valid = True
        witness._failure_details = {}

        _finish_command()

        assert not tuple(tmp_path.glob("details-*.json"))

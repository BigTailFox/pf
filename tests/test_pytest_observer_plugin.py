from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pf import _pytest_observer as observer

pytestmark = pytest.mark.infra


@pytest.fixture(autouse=True)
def _reset_observer(monkeypatch: pytest.MonkeyPatch):
    importlib.reload(observer)
    for variable in (
        "PF_PYTEST_OBSERVER_DIR",
        "PF_PYTEST_OBSERVER_NONCE",
        "PF_PYTEST_PROGRESS_DIR",
        "PF_PYTEST_PROGRESS_NONCE",
        "PF_PYTEST_OBSERVER_DETAILS_DIR",
        "PF_PYTEST_OBSERVER_CASES_DIR",
        "PF_PYTEST_OBSERVER_CASES_PROJECTION",
        "PF_PYTEST_PRUNE_REQUEST",
        "PF_PYTEST_PRUNE_NONCE",
    ):
        monkeypatch.delenv(variable, raising=False)
    yield
    importlib.reload(observer)


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
    hook = observer.pytest_cmdline_main(SimpleNamespace())
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
    monkeypatch.setenv("PF_PYTEST_OBSERVER_NONCE", nonce)


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for variable in (
        "PF_PYTEST_OBSERVER_DIR",
        "PF_PYTEST_OBSERVER_DETAILS_DIR",
    ):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setenv("PF_PYTEST_OBSERVER_NONCE", "nonce")

    def finish(excinfo: object = None):
        _finish_command(excinfo)
        summary = json.loads(next(tmp_path.glob("summary-*.json")).read_text())
        details = [
            json.loads(path.read_text()) for path in tmp_path.glob("details-*.json")
        ]
        assert summary["protocol"] == "pf-pytest-observer-v1"
        assert summary["run_nonce"] == "nonce"
        assert summary["finalized"] is True
        return summary, details

    return finish


def _xdist_session(monkeypatch: pytest.MonkeyPatch, controller: bool, worker: bool):
    import xdist

    class PluginManager:
        @staticmethod
        def hasplugin(name: str) -> bool:
            return name == "xdist"

    monkeypatch.setattr(xdist, "is_xdist_controller", lambda session: controller)
    monkeypatch.setattr(xdist, "is_xdist_worker", lambda session: worker)
    return _session(pluginmanager=PluginManager())


class TestPytestFailureWitnessEvents:
    def test_pytest_collectreport_records_collection_failure(self, artifacts) -> None:
        observer.pytest_collectreport(
            SimpleNamespace(failed=True, nodeid="tests/test_bad.py")
        )

        summary, details = artifacts()
        assert summary["facts"] == [{"kind": "COLLECTION_FAILED", "phase": "collect"}]
        assert details[0]["first"] == {
            "nodeid": "tests/test_bad.py",
            "phase": "collect",
        }
        assert details[0]["total"] == 1

    def test_pytest_collectreport_omits_success(self, artifacts) -> None:
        observer.pytest_collectreport(SimpleNamespace(failed=False))

        summary, details = artifacts()
        assert summary["facts"] == []
        assert details == []

    @pytest.mark.parametrize("phase", ("setup", "call", "teardown"))
    def test_pytest_runtest_logreport_records_test_failure(
        self, artifacts, phase: str
    ) -> None:
        nodeid = "tests/test_bad.py::test_bad"
        observer.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when=phase, nodeid=nodeid)
        )

        summary, details = artifacts()
        assert summary["facts"] == [{"kind": "TEST_FAILED", "phase": phase}]
        assert details[0]["first"] == {"nodeid": nodeid, "phase": phase}
        assert details[0]["total"] == 1

    @pytest.mark.parametrize(
        "failed,phase",
        [(True, "collect"), (False, "call")],
        ids=["non-test-phase", "success"],
    )
    def test_pytest_runtest_logreport_omits_nonfailure(
        self, artifacts, failed, phase
    ) -> None:
        observer.pytest_runtest_logreport(
            SimpleNamespace(failed=failed, when=phase, nodeid="test_ok.py")
        )

        summary, details = artifacts()
        assert summary["facts"] == []
        assert details == []

    def test_pytest_internalerror_records_internal_error(self, artifacts) -> None:
        observer.pytest_internalerror()

        summary, _ = artifacts()
        assert summary["facts"] == [{"kind": "INTERNAL_ERROR", "phase": "pytest"}]

    @pytest.mark.parametrize(
        "nodeid",
        [
            None,
            "",
            "x" * 4_097,
            "test_bad.py::test_bad\nvalue",
            "test_bad.py::test_bad\x7fvalue",
            "test_bad.py::test_bad\ud800value",
        ],
        ids=[
            "non-string",
            "empty",
            "oversized",
            "control",
            "delete-control",
            "surrogate",
        ],
    )
    def test_pytest_runtest_logreport_omits_unsafe_details(
        self, artifacts, nodeid
    ) -> None:
        observer.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when="call", nodeid=nodeid)
        )
        # Later valid reports cannot turn a partial detail set into complete evidence.
        observer.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when="call", nodeid="test_ok.py::test_ok")
        )

        summary, details = artifacts()
        assert summary["facts"] == [{"kind": "TEST_FAILED", "phase": "call"}]
        assert details == []

    def test_pytest_runtest_logreport_preserves_first_failure_phase(
        self, artifacts
    ) -> None:
        nodeid = "tests/test_bad.py::test_bad"
        for phase in ("setup", "teardown"):
            observer.pytest_runtest_logreport(
                SimpleNamespace(failed=True, when=phase, nodeid=nodeid)
            )

        _, details = artifacts()
        assert details[0]["first"] == {"nodeid": nodeid, "phase": "setup"}
        assert details[0]["total"] == 1

    def test_pytest_runtest_logreport_omits_excess_details(self, artifacts) -> None:
        # Exercise the bounded protocol through reports, without replacing its storage.
        for index in range(10_001):
            observer.pytest_runtest_logreport(
                SimpleNamespace(
                    failed=True, when="call", nodeid=f"test_bad.py::test_bad[{index}]"
                )
            )

        summary, details = artifacts()
        assert summary["facts"] == [{"kind": "TEST_FAILED", "phase": "call"}]
        assert details == []

    def test_pytest_runtest_logreport_omits_unreadable_details(self, artifacts) -> None:
        class Report:
            failed = True
            when = "call"

            @property
            def nodeid(self) -> str:
                raise RuntimeError("unreadable")

        observer.pytest_runtest_logreport(Report())

        summary, details = artifacts()
        assert summary["facts"] == [{"kind": "TEST_FAILED", "phase": "call"}]
        assert details == []


class TestPytestFailureWitnessExecutionMode:
    def test_pytest_sessionstart_selects_serial_without_xdist(self, artifacts) -> None:
        observer.pytest_sessionstart(_session())

        summary, _ = artifacts()
        assert summary["execution_mode"] == "serial"

    @pytest.mark.parametrize(
        "controller,worker,expected",
        [
            (True, False, "xdist"),
            (False, True, "xdist"),
            (True, True, "unknown"),
            (False, False, "serial"),
        ],
        ids=["controller", "worker", "conflicting-roles", "no-role"],
    )
    def test_pytest_sessionstart_classifies_xdist_roles(
        self, artifacts, monkeypatch, controller, worker, expected
    ) -> None:
        observer.pytest_sessionstart(_xdist_session(monkeypatch, controller, worker))

        summary, _ = artifacts()
        assert summary["execution_mode"] == expected

    def test_pytest_sessionstart_reports_unknown_when_probe_fails(
        self, artifacts, monkeypatch
    ) -> None:
        import xdist

        session = _xdist_session(monkeypatch, False, False)

        def fail(session: object) -> bool:
            raise RuntimeError("unavailable")

        monkeypatch.setattr(xdist, "is_xdist_controller", fail)
        observer.pytest_sessionstart(session)

        summary, _ = artifacts()
        assert summary["execution_mode"] == "unknown"


class TestPytestFailureWitnessProgress:
    def test_pytest_collection_finish_initializes_progress(
        self, tmp_path, monkeypatch
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        observer.pytest_sessionstart(_session())
        observer.pytest_collection_finish(
            _session(
                items=[
                    SimpleNamespace(nodeid="test_one.py"),
                    SimpleNamespace(nodeid="test_two.py"),
                ]
            )
        )

        assert json.loads((tmp_path / "progress.json").read_text()) == {
            "completed": 0,
            "protocol": "pf-pytest-progress-v1",
            "run_nonce": "nonce",
            "total": 2,
            "unit": "tests",
        }

    @pytest.mark.parametrize(
        "case",
        [
            "xdist",
            "failed-collection",
            "collect-only",
            "non-list",
            "invalid-nodeid",
            "duplicate-nodeids",
            "missing-directory",
            "blocked-directory",
        ],
    )
    def test_pytest_collection_finish_omits_unavailable_progress(
        self, tmp_path, monkeypatch, case
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        session = _session(items=[])
        if case == "xdist":
            session = _xdist_session(monkeypatch, True, False)
        elif case == "failed-collection":
            observer.pytest_collectreport(
                SimpleNamespace(failed=True, nodeid="test_bad.py")
            )
        elif case == "collect-only":
            session = _session(items=[], collectonly=True)
        elif case == "non-list":
            session = _session(items=())
        elif case == "invalid-nodeid":
            session = _session(items=[SimpleNamespace(nodeid=None)])
        elif case == "duplicate-nodeids":
            session = _session(items=[SimpleNamespace(nodeid="duplicate")] * 2)
        elif case == "missing-directory":
            monkeypatch.delenv("PF_PYTEST_PROGRESS_DIR")
        elif case == "blocked-directory":
            blocked = tmp_path / "blocked"
            blocked.write_text("not a directory")
            _enable_progress(monkeypatch, blocked)
        observer.pytest_sessionstart(session)

        observer.pytest_collection_finish(session)
        observer.pytest_runtest_logfinish("test_one.py", None)

        assert not list(tmp_path.rglob("progress.json"))

    @pytest.mark.parametrize(
        "finished,completed",
        [
            (["test_one.py"], 1),
            (["test_two.py"], 0),
            (["test_one.py", "test_one.py"], 1),
        ],
        ids=["known", "unknown", "repeated"],
    )
    def test_pytest_runtest_logfinish_counts_unique_collected_tests(
        self, tmp_path, monkeypatch, finished, completed
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        observer.pytest_sessionstart(_session())
        observer.pytest_collection_finish(
            _session(items=[SimpleNamespace(nodeid="test_one.py")])
        )

        for nodeid in finished:
            observer.pytest_runtest_logfinish(nodeid, None)

        progress = json.loads((tmp_path / "progress.json").read_text())
        assert progress["completed"] == completed
        assert progress["total"] == 1


class TestPytestFailureWitnessFinalization:
    def test_pytest_cmdline_main_commits_protocol_artifacts(
        self, artifacts, tmp_path, monkeypatch
    ) -> None:
        _enable_progress(monkeypatch, tmp_path)
        nodeid = "tests/test_bad.py::test_bad"
        observer.pytest_sessionstart(_session())
        observer.pytest_collection_finish(
            _session(items=[SimpleNamespace(nodeid=nodeid)])
        )
        observer.pytest_runtest_logreport(
            SimpleNamespace(failed=True, when="call", nodeid=nodeid)
        )
        observer.pytest_runtest_logfinish(nodeid, None)

        summary, details = artifacts()
        progress = json.loads((tmp_path / "progress.json").read_text())
        assert summary["facts"] == [{"kind": "TEST_FAILED", "phase": "call"}]
        assert details[0]["first"] == {"nodeid": nodeid, "phase": "call"}
        assert details[0]["total"] == 1
        assert progress["completed"] == progress["total"] == 1

    def test_pytest_cmdline_main_records_hookwrapper_error(self, artifacts) -> None:
        summary, _ = artifacts(excinfo=RuntimeError("failed"))

        assert summary["facts"] == [{"kind": "INTERNAL_ERROR", "phase": "pytest"}]

    def test_pytest_cmdline_main_completes_without_output_configuration(
        self, tmp_path
    ) -> None:
        _finish_command()

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("invalid", [False, True], ids=["empty", "invalid"])
    def test_pytest_cmdline_main_omits_unavailable_details(
        self, artifacts, invalid
    ) -> None:
        if invalid:
            observer.pytest_runtest_logreport(
                SimpleNamespace(failed=True, when="call", nodeid=None)
            )

        _, details = artifacts()
        assert details == []

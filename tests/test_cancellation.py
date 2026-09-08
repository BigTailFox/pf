from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from pf.adapters.process import SubprocessRunner
from pf.cancellation import Cancellation, OperationCancelled
from pf.schemas.evaluation import ProcessResult, ProcessSpec


class TestCancellation:
    def test_registration_and_release_follow_the_cancelled_state(self) -> None:
        cancellation = Cancellation()
        stopped: list[str] = []
        release = cancellation.register(lambda: stopped.append("released"))
        release()
        cancellation.register(lambda: stopped.append("active"))
        cancellation.cancel()
        cancellation.cancel()
        cancellation.register(lambda: stopped.append("late"))
        assert stopped == ["active", "late"]
        with pytest.raises(OperationCancelled):
            cancellation.raise_if_cancelled()

    def test_cleanup_failure_does_not_skip_other_registered_operations(self) -> None:
        cancellation = Cancellation()
        stopped: list[str] = []

        def failed_cleanup() -> None:
            raise RuntimeError("cleanup failed")

        cancellation.register(failed_cleanup)
        cancellation.register(lambda: stopped.append("other"))
        with pytest.raises(RuntimeError, match="cleanup failed"):
            cancellation.cancel()
        assert stopped == ["other"]
        assert cancellation.cancelled


class TestProcessCancellation:
    def test_cancel_before_spawn_does_not_start_a_process(self, tmp_path: Path) -> None:
        cancellation = Cancellation()
        cancellation.cancel()
        marker = tmp_path / "spawned"
        with pytest.raises(OperationCancelled):
            SubprocessRunner().run(
                ProcessSpec(
                    argv=(sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"),
                    cwd=str(tmp_path), timeout_seconds=None,
                ), cancellation=cancellation,
            )
        assert not marker.exists()

    def test_cancel_during_spawn_reaps_the_child(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cancellation = Cancellation()
        original = subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            cancellation.cancel()
            return process

        monkeypatch.setattr(subprocess, "Popen", spawn)
        with pytest.raises(OperationCancelled):
            SubprocessRunner(terminate_grace_seconds=0.05).run(
                ProcessSpec(argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                            cwd=str(tmp_path), timeout_seconds=None),
                cancellation=cancellation,
            )
        assert len(children) == 1
        assert children[0].poll() is not None

    def test_cancel_one_inflight_process_keeps_other_calls_usable(self, tmp_path: Path) -> None:
        runner = SubprocessRunner(terminate_grace_seconds=0.05)
        cancelled, independent = Cancellation(), Cancellation()
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(2)
            server.settimeout(5)
            address = server.getsockname()

            def spec(label: str) -> ProcessSpec:
                return ProcessSpec(
                    argv=(sys.executable, "-c", (
                        f"import socket; s=socket.create_connection({address!r});"
                        f"s.sendall({label.encode()!r}); s.recv(1); print('done')"
                    )), cwd=str(tmp_path), timeout_seconds=None,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(runner.run, spec("a"), cancellation=cancelled)
                second = pool.submit(runner.run, spec("b"), cancellation=independent)
                connections: dict[str, socket.socket] = {}
                try:
                    for _ in range(2):
                        connection, _ = server.accept()
                        connection.settimeout(5)
                        connections[connection.recv(1).decode()] = connection
                    cancelled.cancel()
                    with pytest.raises(OperationCancelled):
                        first.result(timeout=5)
                    assert connections["a"].recv(1) == b""
                    connections["b"].sendall(b"x")
                    result = second.result(timeout=5)
                    assert isinstance(result, ProcessResult)
                    assert result.exit_code == 0
                    assert result.stdout.strip() == "done"
                finally:
                    cancelled.cancel()
                    independent.cancel()
                    for connection in connections.values():
                        connection.close()
        result = runner.run(ProcessSpec(argv=(sys.executable, "-c", "print('next')"), cwd=str(tmp_path), timeout_seconds=5))
        assert isinstance(result, ProcessResult)
        assert result.exit_code == 0

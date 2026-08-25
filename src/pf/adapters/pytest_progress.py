from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import stat
from threading import Event, Thread

from pf.schemas.evaluation import StageProgress


PROGRESS_DIRECTORY_VARIABLE = "PF_PYTEST_PROGRESS_DIR"
PROGRESS_PROTOCOL = "pf-pytest-progress-v1"

_MAX_PROGRESS_BYTES = 1024
_PROGRESS_FIELDS = frozenset({"completed", "protocol", "run_nonce", "total", "unit"})


class InvalidPytestProgress(ValueError):
    pass


class PytestProgressMonitor:
    """Poll one bounded, atomic pytest progress snapshot without affecting outcome."""

    def __init__(
        self,
        directory: Path,
        *,
        nonce: str,
        consume: Callable[[StageProgress | None], None],
    ) -> None:
        self._path = directory / "progress.json"
        self._nonce = nonce
        self._consume = consume
        self._stopped = Event()
        self._thread = Thread(target=self._run, daemon=True)
        self._last: StageProgress | None = None
        self._invalid = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            self._invalidate()
            return
        self._poll()

    def _run(self) -> None:
        while not self._stopped.wait(0.1):
            self._poll()

    def _poll(self) -> None:
        if self._invalid:
            return
        try:
            progress = _read_progress(self._path, nonce=self._nonce)
        except (InvalidPytestProgress, OSError):
            self._invalidate()
            return
        if progress is None:
            if self._last is not None:
                self._invalidate()
            return
        if progress == self._last:
            return
        if self._last is not None and (
            progress.total != self._last.total
            or progress.completed < self._last.completed
        ):
            self._invalidate()
            return
        try:
            self._consume(progress)
        except BaseException:
            self._invalid = True
            return
        self._last = progress

    def _invalidate(self) -> None:
        self._invalid = True
        if self._last is None:
            return
        try:
            self._consume(None)
        except BaseException:
            pass
        self._last = None


def _read_progress(path: Path, *, nonce: str) -> StageProgress | None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_PROGRESS_BYTES:
        raise InvalidPytestProgress("progress is not a bounded regular file")
    with path.open("rb") as stream:
        payload = stream.read(_MAX_PROGRESS_BYTES + 1)
    if len(payload) > _MAX_PROGRESS_BYTES:
        raise InvalidPytestProgress("progress exceeds the byte limit")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InvalidPytestProgress("progress is not UTF-8 JSON") from error
    if type(document) is not dict or frozenset(document) != _PROGRESS_FIELDS:
        raise InvalidPytestProgress("progress fields do not match the protocol")
    canonical = (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")
    if payload != canonical:
        raise InvalidPytestProgress("progress bytes are not canonical")
    if document["protocol"] != PROGRESS_PROTOCOL or document["run_nonce"] != nonce:
        raise InvalidPytestProgress("progress protocol or nonce does not match")
    if (
        type(document["completed"]) is not int
        or type(document["total"]) is not int
        or document["unit"] != "tests"
    ):
        raise InvalidPytestProgress("progress values are invalid")
    try:
        return StageProgress(
            completed=document["completed"],
            total=document["total"],
            unit=document["unit"],
        )
    except ValueError as error:
        raise InvalidPytestProgress("progress values are invalid") from error

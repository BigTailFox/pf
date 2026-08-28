from __future__ import annotations

from collections.abc import Callable
from collections import deque
from datetime import datetime, timezone
from itertools import chain
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from threading import RLock
from typing import TextIO

from pf._secure_runlog import SecureLogDirectory, secure_log_directory
from pf.errors import ConfigurationError, InfrastructureError
from pf.schemas.evaluation import (
    ProcessObservation,
    ProcessSpec,
    ProcessTerminalUnavailable,
    VerificationJournal,
    VerificationJournalRecord,
    VerificationJournalV1,
)


class RunLogStore:
    """Own PF log formats and associations over one secure directory adapter."""

    _METADATA_LIMIT = 4_096
    _INDEX_LIMIT = 8 * 1024 * 1024
    _JOURNAL_LIMIT = 8 * 1024 * 1024
    _INDEX_NAME = "diagnosis-index.json"
    _JOURNAL_NAME = "journal.json"
    _LATEST_JOURNAL_KEY = "__latest_journal__"
    _STDOUT_SECTION = "--- stdout ---"
    _STDERR_SECTION = "--- stderr ---"
    _STREAM_CHUNK_SIZE = 65_536
    _TERMINAL_SEQUENCE = re.compile(
        r"\x1b\][^\x07]*(?:\x07|\x1b\\|$)"
        r"|\x1b[P^_].*?(?:\x1b\\|$)"
        r"|\x1b\[[0-?]*[ -/]*[@-~]"
        r"|\x1b[@-_]"
        r"|\x9d[^\x9c\x07]*(?:\x9c|\x07|$)"
        r"|[\x90\x98\x9e\x9f].*?(?:\x9c|$)"
        r"|\x9b[0-?]*[ -/]*[@-~]"
    )

    def __init__(self, *, root: Path, run_id: str | None = None) -> None:
        self._root = root.resolve()
        self._run_id = run_id or self._new_run_id()
        if re.fullmatch(r"[A-Za-z0-9._-]+", self._run_id) is None:
            raise ValueError("run id must contain only safe filename characters")
        self._run_root = self._root / ".pf" / "logs" / self._run_id
        self._directory: SecureLogDirectory = secure_log_directory(
            root=self._root,
            run_id=self._run_id,
        )
        self._references: dict[int, Path] = {}
        self._lock = RLock()
        self._initialized = False

    def begin_record(self, process_id: int, spec: ProcessSpec) -> "_ProcessLogWriter":
        """Open a streaming Process Log body; call finish() to patch terminal facts."""
        return _ProcessLogWriter(self, process_id, spec)

    def record(
        self,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessObservation,
        stdout: str = "",
        stderr: str = "",
    ) -> Path:
        writer = self.begin_record(process_id, spec)
        if stdout:
            writer.write_stdout(stdout)
        if stderr:
            writer.write_stderr(stderr)
        return writer.finish(result)

    def _commit_process_log(
        self,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessObservation,
        stdout_file: TextIO,
        stderr_file: TextIO,
    ) -> Path:
        try:
            with self._lock:
                self._ensure_run()
                path = self._run_root / f"process-{process_id:04d}.log"
                stdout_characters = self._text_length(stdout_file)
                stderr_characters = self._text_length(stderr_file)

                def write_body(stream: TextIO) -> None:
                    stream.write(
                        self._render_header(
                            process_id,
                            spec,
                            result,
                            stdout_characters=stdout_characters,
                            stderr_characters=stderr_characters,
                        )
                    )
                    stream.write(f"\n{self._STDOUT_SECTION}\n")
                    self._copy_text(stdout_file, stream)
                    stream.write(f"\n{self._STDERR_SECTION}\n")
                    self._copy_text(stderr_file, stream)

                self._directory.write_run_stream(path.name, write_body)
                self._references[id(result)] = path
                return path
        except (OSError, NotImplementedError, ValueError) as error:
            raise InfrastructureError(
                "could not write PF process log",
                detail=str(error),
            ) from error

    def reference_for(self, result: ProcessObservation) -> Path | None:
        with self._lock:
            return self._references.get(id(result))

    def read_output(self, result: ProcessObservation) -> tuple[str, str] | None:
        path = self.reference_for(result)
        if path is None:
            return None
        try:
            text = self._directory.read_run_text(
                self._run_id,
                path.name,
                None,
            )
            return self._parse_output(text)
        except (OSError, UnicodeError, ValueError):
            return None

    @property
    def run_id(self) -> str:
        return self._run_id

    def write_journal(self, journal: VerificationJournal) -> Path:
        """Write this run's Verification Journal and index its failure locators."""
        try:
            if not isinstance(journal, VerificationJournal):
                raise ValueError("verification journal writer only accepts v2")
            with self._lock:
                self._ensure_run()
                payload = journal.model_dump(mode="json")
                payload["schema"] = payload.pop("schema_version")
                content = (
                    json.dumps(
                        payload,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                self._directory.write_run_text(self._JOURNAL_NAME, content)
            located = tuple(
                (entry.failure.failure_id, entry.failure.process)
                for entry in journal.entries
                if entry.failure.process is None
                or self.reference_for(entry.failure.process) is not None
            )
            self.replace_associations(
                f"journal:{journal.run_id}",
                located,
                replace_generation=True,
            )
            self._write_latest_journal(
                {package: journal.run_id for package in journal.packages}
            )
            return self._run_root / self._JOURNAL_NAME
        except (OSError, NotImplementedError, ValueError) as error:
            raise InfrastructureError(
                "could not write PF verification journal",
                detail=str(error),
            ) from error

    def read_latest_journal(self, package: str) -> VerificationJournalRecord | None:
        run_id = self.latest_journal_id(package)
        if run_id is None:
            return None
        return self.read_journal(run_id)

    def latest_journal_id(self, package: str) -> str | None:
        try:
            with self._lock:
                entries = self._read_index_entries()
                latest = entries.get(self._LATEST_JOURNAL_KEY, {})
                run_id = latest.get(package)
                return run_id if isinstance(run_id, str) else None
        except (OSError, NotImplementedError, ValueError, ConfigurationError):
            return None

    def read_journal(self, run_id: str) -> VerificationJournalRecord | None:
        if re.fullmatch(r"[A-Za-z0-9._-]+", run_id) is None:
            return None
        try:
            content = self._directory.read_run_text(
                run_id,
                self._JOURNAL_NAME,
                self._JOURNAL_LIMIT,
            )
            document = json.loads(content)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(document, dict):
            return None
        schema = document.pop("schema", None)
        document["schema_version"] = schema
        try:
            if schema == "verification-journal-v2":
                return VerificationJournal.model_validate(document)
            if schema == "verification-journal-v1":
                return VerificationJournalV1.model_validate(document)
            return None
        except Exception:
            return None

    def lookup_run(self, run_id: str, failure_id: str) -> Path | None:
        return self.lookup(f"journal:{run_id}", failure_id)

    def _write_latest_journal(self, latest: dict[str, str]) -> None:
        with self._lock:
            entries = self._read_index_entries()
            current = dict(entries.get(self._LATEST_JOURNAL_KEY, {}))
            current.update(latest)
            entries[self._LATEST_JOURNAL_KEY] = current
            self._directory.write_logs_text(
                self._INDEX_NAME,
                self._index_content(entries),
            )

    def _read_index_entries(self) -> dict[str, dict[str, str]]:
        try:
            content = self._directory.read_logs_text(
                self._INDEX_NAME,
                self._INDEX_LIMIT,
            )
        except FileNotFoundError:
            return {}
        return self._parse_index(content)

    def associate(
        self,
        report_generation_id: str,
        failure_id: str,
        result: ProcessObservation,
    ) -> None:
        """Associate a portable failure identity with this run's local log."""
        if self.reference_for(result) is None:
            raise InfrastructureError(
                "could not write PF diagnosis index",
                detail=(
                    "the failure process was not recorded by this RunLogStore; "
                    "refusing to publish an unusable diagnosis locator"
                ),
            )
        self.replace_associations(
            report_generation_id,
            ((failure_id, result),),
            replace_generation=False,
        )

    def replace_associations(
        self,
        report_generation_id: str,
        failures: tuple[tuple[str, ProcessObservation | None], ...],
        *,
        replace_generation: bool = True,
        remove_failure_ids: tuple[str, ...] = (),
    ) -> None:
        """Atomically update one report generation's local log locators."""
        try:
            with self._lock:
                located: dict[str, str] = {}
                for failure_id, result in failures:
                    if result is None:
                        continue
                    relative = self._relative_reference(result)
                    if relative is None:
                        raise ValueError(
                            "current failure process has no recorded diagnosis locator"
                        )
                    located[failure_id] = relative
                if not located and not replace_generation and not remove_failure_ids:
                    return
                if located:
                    self._ensure_run()
                entries = self._read_index_entries()
                self._update_entries(
                    entries,
                    report_generation_id,
                    located,
                    replace_generation=replace_generation,
                    remove_failure_ids=remove_failure_ids,
                )
                self._directory.write_logs_text(
                    self._INDEX_NAME,
                    self._index_content(entries),
                )
        except (OSError, NotImplementedError, ValueError) as error:
            raise InfrastructureError(
                "could not write PF diagnosis index",
                detail=str(error),
            ) from error

    def lookup(self, report_generation_id: str, failure_id: str) -> Path | None:
        """Resolve exactly one indexed local log without scanning run directories."""
        try:
            with self._lock:
                entries = self._read_index_entries()
                generation = entries.get(report_generation_id)
                if not isinstance(generation, dict):
                    return None
                relative = generation.get(failure_id)
                if not isinstance(relative, str):
                    return None
                self._validate_relative_locator(relative)
                return self._directory.resolve_regular_log(Path(relative))
        except (OSError, NotImplementedError, ValueError) as error:
            raise ConfigurationError(
                "could not read PF diagnosis log",
                detail=str(error),
            ) from error

    def read_tail(self, path: Path) -> tuple[str, ...]:
        """Read the diagnose preview through the secure log-directory adapter."""
        try:
            logs_root = (self._root / ".pf" / "logs").resolve()
            candidate = path if path.is_absolute() else self._root / path
            relative = candidate.resolve().relative_to(logs_root)
            self._validate_relative_locator(relative.as_posix())
            run_id, name = relative.parts
            return self._directory.read_run_stream(
                run_id,
                name,
                self._read_process_tail,
            )
        except (NotImplementedError, OSError, UnicodeError, ValueError) as error:
            raise ConfigurationError(
                "could not read PF diagnosis log",
                detail=str(error),
            ) from error

    @classmethod
    def _read_process_tail(cls, stream: TextIO) -> tuple[str, ...]:
        first = stream.readline()
        if first.rstrip("\r\n") == "format: pf-process-log-v2":
            return cls._read_v2_process_tail(stream, first)
        return cls._read_v1_process_tail(stream, first)

    @classmethod
    def _read_v1_process_tail(
        cls,
        stream: TextIO,
        first: str,
    ) -> tuple[str, ...]:
        stdout: deque[str] = deque(maxlen=3)
        stderr: deque[str] = deque(maxlen=3)
        section: str | None = None
        stdout_markers = 0
        stderr_markers = 0
        for raw_line in chain((first,), stream):
            line = raw_line.rstrip("\r\n")
            if line == cls._STDOUT_SECTION:
                stdout_markers += 1
                if stdout_markers > 1 or section is not None:
                    raise ValueError("PF v1 process log framing is ambiguous")
                section = "stdout"
                continue
            if line == cls._STDERR_SECTION and section in {"stdout", "stderr"}:
                stderr_markers += 1
                if stderr_markers > 1:
                    raise ValueError("PF v1 process log framing is ambiguous")
                section = "stderr"
                continue
            if section is None:
                continue
            display = cls._safe_terminal_line(line).rstrip()
            if display.strip():
                (stderr if section == "stderr" else stdout).append(display)
        if stdout_markers != 1 or stderr_markers != 1 or section != "stderr":
            raise ValueError("PF process log sections are invalid")
        return tuple(stderr or stdout)

    @classmethod
    def _read_v2_process_tail(
        cls,
        stream: TextIO,
        first: str,
    ) -> tuple[str, ...]:
        header = [first.rstrip("\r\n")]
        while True:
            raw_line = stream.readline()
            if not raw_line:
                raise ValueError("PF v2 process log has no stdout section")
            line = raw_line.rstrip("\r\n")
            if line == cls._STDOUT_SECTION:
                break
            header.append(line)
        stdout_length, stderr_length = cls._framed_lengths(header)
        stdout = cls._read_framed_tail(stream, stdout_length)
        delimiter = stream.read(len(f"\n{cls._STDERR_SECTION}\n"))
        if delimiter != f"\n{cls._STDERR_SECTION}\n":
            raise ValueError("PF v2 process log stderr framing is invalid")
        stderr = cls._read_framed_tail(stream, stderr_length)
        if stream.read(1):
            raise ValueError("PF v2 process log has trailing bytes")
        return stderr or stdout

    @classmethod
    def _read_framed_tail(
        cls,
        stream: TextIO,
        length: int,
    ) -> tuple[str, ...]:
        collector = _LineTail(cls._safe_terminal_line)
        remaining = length
        while remaining:
            chunk = stream.read(min(cls._STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                raise ValueError("PF v2 process log body is incomplete")
            collector.feed(chunk)
            remaining -= len(chunk)
        return collector.finish()

    @classmethod
    def _safe_terminal_line(cls, line: str) -> str:
        without_sequences = cls._TERMINAL_SEQUENCE.sub("", line)
        return "".join(
            character
            for character in without_sequences
            if character == "\t"
            or (ord(character) >= 32 and not 127 <= ord(character) <= 159)
        )

    def close(self) -> None:
        with self._lock:
            self._directory.close()

    def _ensure_run(self) -> None:
        if self._initialized:
            return
        self._directory.ensure_run(self._manifest())
        self._initialized = True

    def _manifest(self) -> str:
        return (
            "format: pf-run-log-v1\n"
            f"run_id: {self._bounded(self._run_id, self._METADATA_LIMIT)}\n"
            f"root: {self._bounded(self._root.as_posix(), self._METADATA_LIMIT)}\n"
        )

    def _relative_reference(self, result: ProcessObservation) -> str | None:
        path = self._references.get(id(result))
        if path is None:
            return None
        relative = path.relative_to(self._root / ".pf" / "logs")
        self._validate_relative_locator(relative.as_posix())
        return relative.as_posix()

    def _parse_index(self, content: str) -> dict[str, dict[str, str]]:
        if len(content.encode("utf-8")) > self._INDEX_LIMIT:
            raise ValueError("PF diagnosis index exceeds its size limit")
        document = json.loads(content)
        if not isinstance(document, dict) or document.get("format") != (
            "pf-diagnosis-index-v1"
        ):
            raise ValueError("PF diagnosis index has an unsupported format")
        entries = document.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("PF diagnosis index entries are invalid")
        validated: dict[str, dict[str, str]] = {}
        for generation, failures in entries.items():
            if not isinstance(generation, str) or not isinstance(failures, dict):
                raise ValueError("PF diagnosis index generation is invalid")
            if generation == self._LATEST_JOURNAL_KEY:
                validated_latest: dict[str, str] = {}
                for package, run_id in failures.items():
                    if not isinstance(package, str) or not isinstance(run_id, str):
                        raise ValueError("PF diagnosis index latest journal is invalid")
                    if re.fullmatch(r"[A-Za-z0-9._-]+", run_id) is None:
                        raise ValueError("PF diagnosis index latest journal is invalid")
                    validated_latest[package] = run_id
                validated[generation] = validated_latest
                continue
            validated_failures: dict[str, str] = {}
            for indexed_failure, relative in failures.items():
                if not isinstance(indexed_failure, str) or not isinstance(
                    relative, str
                ):
                    raise ValueError("PF diagnosis index locator is invalid")
                self._validate_relative_locator(relative)
                validated_failures[indexed_failure] = relative
            validated[generation] = validated_failures
        return validated

    @staticmethod
    def _update_entries(
        entries: dict[str, dict[str, str]],
        report_generation_id: str,
        located: dict[str, str],
        *,
        replace_generation: bool,
        remove_failure_ids: tuple[str, ...],
    ) -> None:
        generation: dict[str, str] = (
            {} if replace_generation else dict(entries.get(report_generation_id, {}))
        )
        for failure_id in remove_failure_ids:
            generation.pop(failure_id, None)
        generation.update(located)
        if generation:
            entries[report_generation_id] = generation
        else:
            entries.pop(report_generation_id, None)

    @staticmethod
    def _index_content(entries: dict[str, dict[str, str]]) -> str:
        return (
            json.dumps(
                {
                    "format": "pf-diagnosis-index-v1",
                    "entries": entries,
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

    @staticmethod
    def _validate_relative_locator(relative: str) -> None:
        parts = Path(relative).parts
        if (
            len(parts) != 2
            or any(part in {"", ".", ".."} for part in parts)
            or re.fullmatch(r"[A-Za-z0-9._-]+", parts[0]) is None
            or re.fullmatch(r"process-[0-9]{4}\.log", parts[1]) is None
        ):
            raise ValueError("PF diagnosis log locator is unsafe")

    @classmethod
    def _copy_text(cls, source: TextIO, dest: TextIO) -> None:
        source.seek(0)
        while True:
            piece = source.read(cls._STREAM_CHUNK_SIZE)
            if not piece:
                break
            dest.write(piece)

    @classmethod
    def _text_length(cls, source: TextIO) -> int:
        source.seek(0)
        length = 0
        while True:
            piece = source.read(cls._STREAM_CHUNK_SIZE)
            if not piece:
                return length
            length += len(piece)

    @classmethod
    def _render_header(
        cls,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessObservation,
        *,
        stdout_characters: int,
        stderr_characters: int,
    ) -> str:
        environment_names = sorted(variable.name for variable in spec.environment)
        if isinstance(result, ProcessTerminalUnavailable):
            terminal = (
                "terminal_kind: terminal-unavailable\n"
                "exit_code: null\n"
                "signal: null\n"
                "start_error: null\n"
                "timed_out: false\n"
                f"duration_seconds: {result.duration_seconds}\n"
                "stdout_complete: false\n"
                "stderr_complete: false\n"
            )
        else:
            terminal = (
                "terminal_kind: process-result\n"
                f"exit_code: {json.dumps(result.exit_code)}\n"
                f"signal: {json.dumps(result.signal)}\n"
                f"start_error: {cls._bounded_json(result.start_error)}\n"
                f"timed_out: {json.dumps(result.timed_out)}\n"
                f"duration_seconds: {result.duration_seconds}\n"
                f"stdout_complete: {json.dumps(result.stdout_complete)}\n"
                f"stderr_complete: {json.dumps(result.stderr_complete)}\n"
            )
        return (
            "format: pf-process-log-v2\n"
            f"process_id: {process_id}\n"
            f"argv: {cls._bounded_json(spec.argv)}\n"
            f"cwd: {cls._bounded_json(spec.cwd)}\n"
            f"environment_names: {cls._bounded_json(environment_names)}\n"
            f"timeout_seconds: {json.dumps(spec.timeout_seconds)}\n"
            f"start_new_session: {json.dumps(spec.start_new_session)}\n"
            "redaction_policy_identity: "
            f"{cls._bounded_json(spec.redaction_policy_identity)}\n"
            f"{terminal}"
            f"stdout_characters: {stdout_characters}\n"
            f"stderr_characters: {stderr_characters}\n"
        )

    @classmethod
    def _render(
        cls,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessObservation,
        *,
        stdout: str,
        stderr: str,
    ) -> str:
        output = cls._render_header(
            process_id,
            spec,
            result,
            stdout_characters=len(stdout),
            stderr_characters=len(stderr),
        )
        for heading, value in (
            (cls._STDOUT_SECTION, stdout),
            (cls._STDERR_SECTION, stderr),
        ):
            output += f"\n{heading}\n{value}"
        return output

    @classmethod
    def _parse_output(cls, text: str) -> tuple[str, str]:
        if text.startswith("format: pf-process-log-v2\n"):
            return cls._parse_v2_output(text)
        return cls._parse_v1_output(text)

    @classmethod
    def _parse_v2_output(cls, text: str) -> tuple[str, str]:
        stdout_mark = f"\n{cls._STDOUT_SECTION}\n"
        stdout_at = text.find(stdout_mark)
        if stdout_at < 0:
            raise ValueError("PF v2 process log has no stdout section")
        stdout_length, stderr_length = cls._framed_lengths(
            text[:stdout_at].splitlines()
        )
        stdout_start = stdout_at + len(stdout_mark)
        stdout_end = stdout_start + stdout_length
        stderr_mark = f"\n{cls._STDERR_SECTION}\n"
        if text[stdout_end : stdout_end + len(stderr_mark)] != stderr_mark:
            raise ValueError("PF v2 process log stderr framing is invalid")
        stderr_start = stdout_end + len(stderr_mark)
        stderr_end = stderr_start + stderr_length
        if stderr_end != len(text):
            raise ValueError("PF v2 process log body length is invalid")
        return text[stdout_start:stdout_end], text[stderr_start:stderr_end]

    @classmethod
    def _parse_v1_output(cls, text: str) -> tuple[str, str]:
        candidates: list[tuple[str, str]] = []
        for newline in ("\n", "\r\n"):
            stdout_mark = f"{newline}{cls._STDOUT_SECTION}{newline}"
            stderr_mark = f"{newline}{cls._STDERR_SECTION}{newline}"
            if text.count(stdout_mark) != 1 or text.count(stderr_mark) != 1:
                continue
            stdout_at = text.find(stdout_mark)
            stderr_at = text.find(stderr_mark)
            if stderr_at < stdout_at:
                continue
            candidates.append(
                (
                    text[stdout_at + len(stdout_mark) : stderr_at],
                    text[stderr_at + len(stderr_mark) :],
                )
            )
        if len(candidates) != 1:
            raise ValueError("PF v1 process log framing is ambiguous")
        return candidates[0]

    @staticmethod
    def _framed_lengths(header: list[str]) -> tuple[int, int]:
        values: dict[str, int] = {}
        for line in header:
            name, separator, value = line.partition(": ")
            if name not in {"stdout_characters", "stderr_characters"}:
                continue
            if not separator or not value.isascii() or not value.isdecimal():
                raise ValueError("PF v2 process log length is invalid")
            if name in values:
                raise ValueError("PF v2 process log length is duplicated")
            values[name] = int(value)
        if set(values) != {"stdout_characters", "stderr_characters"}:
            raise ValueError("PF v2 process log lengths are missing")
        return values["stdout_characters"], values["stderr_characters"]

    @staticmethod
    def _bounded(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        marker = "... [truncated by RunLogStore]"
        return f"{value[: limit - len(marker)]}{marker}"

    @classmethod
    def _bounded_json(cls, value: object) -> str:
        return cls._bounded(
            json.dumps(value, ensure_ascii=False),
            cls._METADATA_LIMIT,
        )

    @staticmethod
    def _new_run_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{timestamp}-{os.getpid()}-{secrets.token_hex(4)}"


class _LineTail:
    def __init__(self, sanitize: Callable[[str], str]) -> None:
        self._sanitize = sanitize
        self._lines: deque[str] = deque(maxlen=3)
        self._pending: list[str] = []
        self._previous_was_cr = False

    def feed(self, chunk: str) -> None:
        start = 0
        for index, character in enumerate(chunk):
            if character not in {"\r", "\n"}:
                self._previous_was_cr = False
                continue
            if index > start:
                self._pending.append(chunk[start:index])
            if character != "\n" or not self._previous_was_cr:
                self._flush_pending()
            self._previous_was_cr = character == "\r"
            start = index + 1
        if start < len(chunk):
            self._pending.append(chunk[start:])

    def finish(self) -> tuple[str, ...]:
        if self._pending:
            self._flush_pending()
        return tuple(self._lines)

    def _flush_pending(self) -> None:
        line = "".join(self._pending)
        self._pending.clear()
        self._append(line)

    def _append(self, line: str) -> None:
        display = self._sanitize(line).rstrip()
        if display.strip():
            self._lines.append(display)


class _ProcessLogWriter:
    """Stream redacted stdout/stderr to anonymous temps, then patch terminal facts."""

    def __init__(
        self, store: RunLogStore, process_id: int, spec: ProcessSpec
    ) -> None:
        self._store = store
        self._process_id = process_id
        self._spec = spec
        self._stdout = tempfile.TemporaryFile(
            mode="w+",
            encoding="utf-8",
            newline="",
        )
        self._stderr = tempfile.TemporaryFile(
            mode="w+",
            encoding="utf-8",
            newline="",
        )
        self._closed = False

    def write_stdout(self, chunk: str) -> None:
        self._stdout.write(chunk)

    def write_stderr(self, chunk: str) -> None:
        self._stderr.write(chunk)

    def finish(self, result: ProcessObservation) -> Path:
        try:
            return self._store._commit_process_log(
                self._process_id,
                self._spec,
                result,
                self._stdout,
                self._stderr,
            )
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stdout.close()
        self._stderr.close()

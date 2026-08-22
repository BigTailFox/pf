from __future__ import annotations

from datetime import datetime, timezone
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
    ProcessResult,
    ProcessSpec,
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
        result: ProcessResult,
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
        result: ProcessResult,
        stdout_file: TextIO,
        stderr_file: TextIO,
    ) -> Path:
        try:
            with self._lock:
                self._ensure_run()
                path = self._run_root / f"process-{process_id:04d}.log"

                def write_body(stream: TextIO) -> None:
                    stream.write(self._render_header(process_id, spec, result))
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

    def reference_for(self, result: ProcessResult) -> Path | None:
        with self._lock:
            return self._references.get(id(result))

    def read_output(self, result: ProcessResult) -> tuple[str, str] | None:
        path = self.reference_for(result)
        if path is None:
            return None
        try:
            text = self._directory.read_run_text(
                self._run_id,
                path.name,
                None,
            )
        except (OSError, UnicodeError, ValueError):
            return None
        return self._parse_output(text)

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
        result: ProcessResult,
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
        failures: tuple[tuple[str, ProcessResult | None], ...],
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

    def _relative_reference(self, result: ProcessResult) -> str | None:
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
    def _render_header(
        cls,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessResult,
    ) -> str:
        environment_names = sorted(variable.name for variable in spec.environment)
        return (
            "format: pf-process-log-v1\n"
            f"process_id: {process_id}\n"
            f"argv: {cls._bounded_json(spec.argv)}\n"
            f"cwd: {cls._bounded_json(spec.cwd)}\n"
            f"environment_names: {cls._bounded_json(environment_names)}\n"
            f"timeout_seconds: {json.dumps(spec.timeout_seconds)}\n"
            f"start_new_session: {json.dumps(spec.start_new_session)}\n"
            "redaction_policy_identity: "
            f"{cls._bounded_json(spec.redaction_policy_identity)}\n"
            f"exit_code: {json.dumps(result.exit_code)}\n"
            f"signal: {json.dumps(result.signal)}\n"
            f"start_error: {cls._bounded_json(result.start_error)}\n"
            f"timed_out: {json.dumps(result.timed_out)}\n"
            f"duration_seconds: {result.duration_seconds}\n"
            f"stdout_complete: {json.dumps(result.stdout_complete)}\n"
            f"stderr_complete: {json.dumps(result.stderr_complete)}\n"
        )

    @classmethod
    def _render(
        cls,
        process_id: int,
        spec: ProcessSpec,
        result: ProcessResult,
        *,
        stdout: str,
        stderr: str,
    ) -> str:
        output = cls._render_header(process_id, spec, result)
        for heading, value in (
            (cls._STDOUT_SECTION, stdout),
            (cls._STDERR_SECTION, stderr),
        ):
            output += f"\n{heading}\n{value}"
        return output

    @classmethod
    def _parse_output(cls, text: str) -> tuple[str, str]:
        stdout_mark = f"\n{cls._STDOUT_SECTION}\n"
        stderr_mark = f"\n{cls._STDERR_SECTION}\n"
        stdout_at = text.find(stdout_mark)
        stderr_at = text.find(stderr_mark)
        if stdout_at < 0 or stderr_at < 0 or stderr_at < stdout_at:
            return "", ""
        stdout = text[stdout_at + len(stdout_mark) : stderr_at]
        stderr = text[stderr_at + len(stderr_mark) :]
        return stdout, stderr

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


class _ProcessLogWriter:
    """Stream redacted stdout/stderr to anonymous temps, then patch terminal facts."""

    def __init__(
        self, store: RunLogStore, process_id: int, spec: ProcessSpec
    ) -> None:
        self._store = store
        self._process_id = process_id
        self._spec = spec
        self._stdout = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self._stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self._closed = False

    def write_stdout(self, chunk: str) -> None:
        self._stdout.write(chunk)

    def write_stderr(self, chunk: str) -> None:
        self._stderr.write(chunk)

    def finish(self, result: ProcessResult) -> Path:
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

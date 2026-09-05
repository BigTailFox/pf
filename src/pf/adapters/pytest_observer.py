from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal

from pf.schemas.evaluation import (
    PytestFailureCase,
    PytestFailureDetail,
)

OBSERVATION_DIRECTORY_VARIABLE = "PF_PYTEST_OBSERVER_DIR"
DETAILS_DIRECTORY_VARIABLE = "PF_PYTEST_OBSERVER_DETAILS_DIR"
CASES_DIRECTORY_VARIABLE = "PF_PYTEST_OBSERVER_CASES_DIR"
CASES_PROJECTION_VARIABLE = "PF_PYTEST_OBSERVER_CASES_PROJECTION"
RUN_NONCE_VARIABLE = "PF_PYTEST_OBSERVER_NONCE"
PROTOCOL = "pf-pytest-observer-v1"
DETAILS_PROTOCOL = "pf-pytest-observer-details-v1"
CASES_PROTOCOL = "pf-pytest-observer-cases-v1"
PRUNE_REQUEST_VARIABLE = "PF_PYTEST_PRUNE_REQUEST"
PRUNE_NONCE_VARIABLE = "PF_PYTEST_PRUNE_NONCE"

_MAX_SUMMARIES = 1024
_MAX_SUMMARY_BYTES = 4 * 1024
_SUMMARY_NAME = re.compile(r"summary-[0-9a-f]{32}\.json")
_DETAIL_NAME = re.compile(r"details-[0-9a-f]{32}\.json")
_CASES_NAME = re.compile(r"cases-[0-9a-f]{32}\.json")
_MAX_DETAIL_BYTES = 8 * 1024
_MAX_DETAIL_ARTIFACTS = 1024
_MAX_CASES_BYTES = 8 * 1024 * 1024
_MAX_CASES_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_CASES_ARTIFACTS = 1024
_SUMMARY_FIELDS = frozenset(
    {
        "execution_mode",
        "facts",
        "finalized",
        "protocol",
        "pytest_version",
        "python_implementation",
        "python_minor",
        "run_nonce",
    }
)
_FACT_FIELDS = frozenset({"kind", "phase"})
_VALID_FACTS = frozenset(
    {
        ("COLLECTION_FAILED", "collect"),
        ("TEST_FAILED", "setup"),
        ("TEST_FAILED", "call"),
        ("TEST_FAILED", "teardown"),
        ("INTERNAL_ERROR", "pytest"),
    }
)
_CASES_FIELDS = frozenset(
    {
        "collection_completed",
        "collection_failed",
        "nodeids",
        "projection",
        "protocol",
        "role",
        "run_nonce",
    }
)
_VALID_ROLES = frozenset({"serial", "controller", "worker"})
_VALID_PROJECTIONS = frozenset({"failed", "collected"})
_FAILURE_FACTS = _VALID_FACTS - {("INTERNAL_ERROR", "pytest")}

ExecutionMode = Literal["serial", "xdist", "unknown"]
Fact = tuple[str, str]


class InvalidPytestObservation(ValueError):
    pass


@dataclass(frozen=True)
class PytestObservation:
    execution_mode: ExecutionMode
    facts: frozenset[Fact]
    python_minor: str
    pytest_version: str

    @property
    def has_failure(self) -> bool:
        return bool(self.facts & _FAILURE_FACTS)

@dataclass(frozen=True)
class _Summary:
    execution_mode: ExecutionMode
    facts: tuple[Fact, ...]
    pytest_version: str
    python_minor: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.execution_mode, self.python_minor, self.pytest_version


CasesRole = Literal["serial", "controller", "worker"]
CasesProjection = Literal["failed", "collected"]
CasesStatus = Literal["missing", "invalid", "present"]


@dataclass(frozen=True)
class PytestCasesRecord:
    role: CasesRole
    projection: CasesProjection
    collection_completed: bool
    collection_failed: bool
    nodeids: tuple[str, ...]


@dataclass(frozen=True)
class PytestCasesObservation:
    status: CasesStatus
    projection: CasesProjection | None = None
    records: tuple[PytestCasesRecord, ...] = ()


def read_pytest_observer_cases(
    directory: Path,
    *,
    nonce: str,
    projection: CasesProjection,
) -> PytestCasesObservation:
    """Read optional collected/failed projection without affecting disposition."""
    try:
        with os.scandir(directory) as entries:
            paths = tuple(
                Path(entry.path)
                for entry in islice(entries, _MAX_CASES_ARTIFACTS + 1)
            )
        if not paths:
            return PytestCasesObservation(status="missing", projection=projection)
        if len(paths) > _MAX_CASES_ARTIFACTS:
            return PytestCasesObservation(status="invalid", projection=projection)
        total_bytes = 0
        records: list[PytestCasesRecord] = []
        for path in paths:
            metadata = path.stat(follow_symlinks=False)
            total_bytes += metadata.st_size
            if total_bytes > _MAX_CASES_TOTAL_BYTES:
                return PytestCasesObservation(status="invalid", projection=projection)
            artifact_nonce, record = _read_cases_artifact(path)
            if artifact_nonce != nonce:
                continue
            if record.projection != projection:
                return PytestCasesObservation(status="invalid", projection=projection)
            records.append(record)
        if not records:
            return PytestCasesObservation(status="missing", projection=projection)
        return PytestCasesObservation(
            status="present",
            projection=projection,
            records=tuple(records),
        )
    except (
        InvalidPytestObservation,
        OSError,
        RecursionError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return PytestCasesObservation(status="invalid", projection=projection)


def _read_cases_artifact(path: Path) -> tuple[str, PytestCasesRecord]:
    if _CASES_NAME.fullmatch(path.name) is None:
        raise InvalidPytestObservation("observer cases artifact name is invalid")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_CASES_BYTES:
        raise InvalidPytestObservation("observer cases is not a bounded regular file")
    with path.open("rb") as stream:
        payload = stream.read(_MAX_CASES_BYTES + 1)
    if len(payload) > _MAX_CASES_BYTES:
        raise InvalidPytestObservation("observer cases exceeds the byte limit")
    document = json.loads(payload.decode("utf-8"))
    if type(document) is not dict or frozenset(document) != _CASES_FIELDS:
        raise InvalidPytestObservation("observer cases fields do not match")
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
        raise InvalidPytestObservation("observer cases bytes are not canonical")
    if document["protocol"] != CASES_PROTOCOL:
        raise InvalidPytestObservation("observer cases protocol is invalid")
    run_nonce = document["run_nonce"]
    role = document["role"]
    projection = document["projection"]
    nodeids = document["nodeids"]
    if (
        type(run_nonce) is not str
        or role not in _VALID_ROLES
        or projection not in _VALID_PROJECTIONS
        or type(document["collection_completed"]) is not bool
        or type(document["collection_failed"]) is not bool
        or type(nodeids) is not list
        or any(type(item) is not str for item in nodeids)
    ):
        raise InvalidPytestObservation("observer cases values are invalid")
    return run_nonce, PytestCasesRecord(
        role=role,
        projection=projection,
        collection_completed=document["collection_completed"],
        collection_failed=document["collection_failed"],
        nodeids=tuple(nodeids),
    )


def read_pytest_observer(
    directory: Path,
    *,
    nonce: str,
) -> PytestObservation | None:
    """Read an optional summary, discarding the whole projection if invalid."""
    try:
        return _read_evidence(directory, nonce=nonce)
    except (InvalidPytestObservation, OSError):
        return None


def read_pytest_observer_detail(
    directory: Path,
    *,
    nonce: str,
) -> PytestFailureDetail | None:
    """Read optional UI metadata without affecting pytest outcome authority."""
    try:
        with os.scandir(directory) as entries:
            paths = tuple(
                Path(entry.path)
                for entry in islice(entries, _MAX_DETAIL_ARTIFACTS + 1)
            )
        if not paths:
            return None
        if len(paths) > _MAX_DETAIL_ARTIFACTS:
            raise InvalidPytestObservation("too many observer detail artifacts")
        matches = tuple(
            detail
            for artifact_nonce, detail in (
                _read_pytest_failure_detail_artifact(path) for path in paths
            )
            if artifact_nonce == nonce
        )
        if len(matches) != 1:
            raise InvalidPytestObservation("observer detail artifact count is invalid")
        return matches[0]
    except (
        InvalidPytestObservation,
        OSError,
        RecursionError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None


def _read_pytest_failure_detail_artifact(
    path: Path,
) -> tuple[str, PytestFailureDetail]:
    if _DETAIL_NAME.fullmatch(path.name) is None:
        raise InvalidPytestObservation("observer detail artifact name is invalid")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_DETAIL_BYTES:
        raise InvalidPytestObservation("observer detail is not a bounded regular file")
    with path.open("rb") as stream:
        payload = stream.read(_MAX_DETAIL_BYTES + 1)
    if len(payload) > _MAX_DETAIL_BYTES:
        raise InvalidPytestObservation("observer detail exceeds the byte limit")
    document = json.loads(payload.decode("utf-8"))
    if type(document) is not dict or frozenset(document) != {
        "first",
        "protocol",
        "run_nonce",
        "total",
    }:
        raise InvalidPytestObservation("observer detail fields do not match")
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
        raise InvalidPytestObservation("observer detail bytes are not canonical")
    if document["protocol"] != DETAILS_PROTOCOL:
        raise InvalidPytestObservation("observer detail protocol is invalid")
    run_nonce = document["run_nonce"]
    if type(run_nonce) is not str:
        raise InvalidPytestObservation("observer detail nonce is invalid")
    first = document["first"]
    if type(first) is not dict or frozenset(first) != {"nodeid", "phase"}:
        raise InvalidPytestObservation("observer detail first case is invalid")
    return run_nonce, PytestFailureDetail(
        first=PytestFailureCase(
            nodeid=first["nodeid"],
            phase=first["phase"],
        ),
        total=document["total"],
    )


def _read_evidence(directory: Path, *, nonce: str) -> PytestObservation | None:
    paths = tuple(islice(directory.iterdir(), _MAX_SUMMARIES + 1))
    if not paths:
        return None
    if len(paths) > _MAX_SUMMARIES:
        raise InvalidPytestObservation("too many summary artifacts")
    summaries = tuple(_read_summary(path, nonce=nonce) for path in paths)
    identity = summaries[0].identity
    if any(summary.identity != identity for summary in summaries[1:]):
        raise InvalidPytestObservation("summary identities conflict")
    return PytestObservation(
        execution_mode=summaries[0].execution_mode,
        facts=frozenset(fact for summary in summaries for fact in summary.facts),
        python_minor=summaries[0].python_minor,
        pytest_version=summaries[0].pytest_version,
    )


def _read_summary(path: Path, *, nonce: str) -> _Summary:
    if _SUMMARY_NAME.fullmatch(path.name) is None:
        raise InvalidPytestObservation("unknown observer artifact")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_SUMMARY_BYTES:
        raise InvalidPytestObservation("summary is not a bounded regular file")
    with path.open("rb") as stream:
        payload = stream.read(_MAX_SUMMARY_BYTES + 1)
    if len(payload) > _MAX_SUMMARY_BYTES:
        raise InvalidPytestObservation("summary exceeds the byte limit")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InvalidPytestObservation("summary is not UTF-8 JSON") from error
    if type(document) is not dict or frozenset(document) != _SUMMARY_FIELDS:
        raise InvalidPytestObservation("summary fields do not match the protocol")
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
        raise InvalidPytestObservation("summary bytes are not canonical")
    if document["protocol"] != PROTOCOL or document["run_nonce"] != nonce:
        raise InvalidPytestObservation("summary protocol or nonce does not match")
    if document["finalized"] is not True:
        raise InvalidPytestObservation("summary is not finalized")
    execution_mode = document["execution_mode"]
    if type(execution_mode) is not str or execution_mode not in {
        "serial",
        "xdist",
        "unknown",
    }:
        raise InvalidPytestObservation("execution mode is invalid")
    if document["python_implementation"] != "cpython":
        raise InvalidPytestObservation("runtime implementation is invalid")
    python_minor = document["python_minor"]
    pytest_version = document["pytest_version"]
    if (
        type(python_minor) is not str
        or re.fullmatch(r"3\.\d+", python_minor) is None
        or type(pytest_version) is not str
        or not pytest_version
        or len(pytest_version) > 128
    ):
        raise InvalidPytestObservation("runtime identity is invalid")
    facts_document = document["facts"]
    if type(facts_document) is not list:
        raise InvalidPytestObservation("facts must be a list")
    facts: list[Fact] = []
    for fact_document in facts_document:
        if type(fact_document) is not dict or frozenset(fact_document) != _FACT_FIELDS:
            raise InvalidPytestObservation("fact fields do not match the protocol")
        fact = fact_document["kind"], fact_document["phase"]
        if any(type(item) is not str for item in fact) or fact not in _VALID_FACTS:
            raise InvalidPytestObservation("fact is not recognized")
        facts.append(fact)
    if facts != sorted(set(facts)):
        raise InvalidPytestObservation("facts must be sorted and unique")
    return _Summary(
        execution_mode=execution_mode,
        facts=tuple(facts),
        pytest_version=pytest_version,
        python_minor=python_minor,
    )

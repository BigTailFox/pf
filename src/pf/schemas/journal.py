"""Verification Journal v3: dynamic failures and lightweight static membership."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    FailureRecord,
    VerificationRole,
)
from pf.schemas.project import Cell
from pf.schemas.static_scope import StaticScopeEvidence
from pf.schemas.ty_cache import TyCacheDocument


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

_COMMAND_REQUEST_ROLE: dict[tuple[str, str], VerificationRole] = {
    ("smoke", "highest"): "baseline",
    ("search", "highest"): "baseline",
    ("check", "highest"): "declaration-capture",
    ("check", "lowest-direct"): "declaration",
    ("search", "exact-vector"): "probe",
}


def journal_role(*, command: str, requested_resolution: str | None) -> VerificationRole:
    """Return the Journal Role that D008 §2 admits for this command and request."""
    if requested_resolution is None:
        if command != "search":
            raise ValueError("journal cell-scoped entry must be a search probe")
        return "probe"
    role = _COMMAND_REQUEST_ROLE.get((command, requested_resolution))
    if role is None:
        raise ValueError("journal entry role does not match its command and request")
    return role


def journal_role_for_failure(*, command: str, failure: FailureRecord) -> VerificationRole:
    if isinstance(failure.scope, AttemptFailureScope):
        return journal_role(
            command=command,
            requested_resolution=failure.scope.attempt.identity.requested_resolution,
        )
    return journal_role(command=command, requested_resolution=None)


def _admit_entry_role(command: str, entry: "VerificationJournalEntry") -> None:
    requested = (
        None
        if entry.attempt is None
        else entry.attempt.identity.requested_resolution
    )
    if journal_role(command=command, requested_resolution=requested) != entry.role:
        raise ValueError("journal entry role does not match its command and request")


_INTERN_FIELDS = (
    "static_contents",
    "static_subjects",
    "static_facts",
    "static_comparisons",
    "static_scopes",
)


class JournalHighestCollected(FrozenSchema):
    kind: Literal["collected"] = "collected"
    subject_identity: Digest
    cache_identity: Digest
    fact_identity: Digest


class JournalHighestUncollected(FrozenSchema):
    kind: Literal["uncollected"] = "uncollected"
    detail: str = Field(min_length=1)


JournalHighest = Annotated[
    Union[JournalHighestCollected, JournalHighestUncollected],
    Field(discriminator="kind"),
]


class JournalStaticMembership(FrozenSchema):
    cell: Cell
    highest: JournalHighest


def cell_canonical_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (cell.package, cell.python_minor, cell.target, cell.extra_surface)


def journal_entry_sort_key(
    entry: "VerificationJournalEntry",
) -> tuple[str, str, str, tuple[str, ...], str]:
    return (*cell_canonical_key(entry.cell), entry.failure.failure_id)


def static_membership_from_scope(scope: StaticScopeEvidence) -> JournalStaticMembership | None:
    if scope.highest_uncollected is not None:
        return JournalStaticMembership(
            cell=scope.cell,
            highest=JournalHighestUncollected(detail=scope.highest_uncollected.unavailable.detail),
        )
    if scope.highest_reference_ref is None:
        return None
    document = scope.consumer(scope.highest_reference_ref).observation
    return JournalStaticMembership(
        cell=scope.cell,
        highest=JournalHighestCollected(
            subject_identity=document.subject.identity,
            cache_identity=document.observation_policy.cache_identity,
            fact_identity=document.fact.identity,
        ),
    )


def admit_static_membership(
    journal: "VerificationJournal",
    cache: TyCacheDocument,
) -> None:
    if cache.run_id != journal.run_id:
        raise ValueError("ty-cache run_id must match its journal")
    by_key = {
        (item.subject_identity, item.cache_identity): item
        for item in cache.entries
    }
    for member in journal.static_membership:
        highest = member.highest
        if highest.kind != "collected":
            continue
        entry = by_key.get((highest.subject_identity, highest.cache_identity))
        if (
            entry is None
            or entry.document.fact.identity != highest.fact_identity
            or entry.document.subject.identity != highest.subject_identity
            or entry.document.observation_policy.cache_identity != highest.cache_identity
        ):
            raise ValueError("static membership is not closed against ty-cache")


class VerificationJournalEntry(FrozenSchema):
    package: str
    cell: Cell
    role: VerificationRole
    attempt: Attempt | None = None
    failure: FailureRecord

    @model_validator(mode="after")
    def validate_entry_identity(self) -> "VerificationJournalEntry":
        if self.package != self.cell.package:
            raise ValueError("journal entry package must match its cell")
        scope = self.failure.scope
        if isinstance(scope, AttemptFailureScope):
            if self.attempt != scope.attempt:
                raise ValueError("journal entry attempt must match its failure scope")
            if scope.attempt.identity.cell != self.cell:
                raise ValueError("journal entry cell must match its attempt")
        else:
            if self.attempt is not None:
                raise ValueError("cell-scoped journal entry cannot contain an attempt")
            if scope.cell != self.cell or scope.package != self.package:
                raise ValueError("journal entry cell must match its failure scope")
        return self


class VerificationPackagePolicy(FrozenSchema):
    package: str
    execution_policy_identity: str


class VerificationJournal(FrozenSchema):
    schema_version: Literal["verification-journal-v3"] = "verification-journal-v3"
    run_id: str
    command: Literal["smoke", "check", "search"]
    source_snapshot_digest: str
    package_policies: tuple[VerificationPackagePolicy, ...]
    entries: tuple[VerificationJournalEntry, ...]
    static_membership: tuple[JournalStaticMembership, ...]

    @model_validator(mode="wrap")
    @classmethod
    def reject_interned_static_audit(cls, value, handler):
        if isinstance(value, dict) and any(name in value for name in _INTERN_FIELDS):
            raise PydanticCustomError(
                "invalid-static-evidence",
                "journal intern tables are not supported",
            )
        return handler(value)

    @property
    def packages(self) -> tuple[str, ...]:
        return tuple(item.package for item in self.package_policies)

    @model_validator(mode="after")
    def validate_package_policies(self) -> "VerificationJournal":
        packages = self.packages
        if not packages or packages != tuple(sorted(set(packages))):
            raise ValueError("journal package policies must be sorted and unique")
        policies = {
            item.package: item.execution_policy_identity
            for item in self.package_policies
        }
        for entry in self.entries:
            policy = policies.get(entry.package)
            if policy is None:
                raise ValueError("journal entry package has no policy identity")
            scope = entry.failure.scope
            if isinstance(scope, AttemptFailureScope):
                identity = scope.attempt.identity
                entry_policy = identity.execution_policy_identity
                snapshot_digest = identity.source_snapshot_digest
            else:
                entry_policy = scope.execution_policy_identity
                snapshot_digest = scope.source_snapshot_digest
            if entry_policy != policy:
                raise ValueError("journal entry policy identity does not match package")
            if snapshot_digest != self.source_snapshot_digest:
                raise ValueError("journal entry snapshot does not match its run")
            _admit_entry_role(self.command, entry)
        seen_ids: set[str] = set()
        for entry in self.entries:
            failure_id = entry.failure.failure_id
            if failure_id in seen_ids:
                raise ValueError("journal failure ID maps to conflicting entries")
            seen_ids.add(failure_id)
        entry_keys = tuple(journal_entry_sort_key(entry) for entry in self.entries)
        if entry_keys != tuple(sorted(entry_keys)):
            raise ValueError("journal entries must be sorted by cell and failure ID")
        keys = tuple(cell_canonical_key(member.cell) for member in self.static_membership)
        if keys != tuple(sorted(set(keys))):
            raise PydanticCustomError(
                "invalid-static-evidence",
                "journal static membership must be unique and sorted by cell",
            )
        for member in self.static_membership:
            if member.cell.package not in policies:
                raise PydanticCustomError(
                    "invalid-static-evidence",
                    "journal static membership package has no policy identity",
                )
        return self

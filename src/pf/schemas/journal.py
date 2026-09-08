"""Verification Journal v3: dynamic failures and independent static audit scopes."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError, model_serializer, model_validator
from pydantic_core import PydanticCustomError

from pf.schemas.base import FrozenSchema
from pf.schemas.evaluation import (
    Attempt,
    AttemptFailureScope,
    FailureRecord,
    VerificationRole,
)
from pf.schemas.project import Cell
from pf.schemas.static_scope import (
    InternedStaticComparison,
    InternedStaticContent,
    InternedStaticFact,
    InternedStaticSubject,
    StaticScopeEvidence,
    StaticScopeWire,
    intern_static_scopes,
    resolve_static_scopes,
)


class JournalStaticScope(FrozenSchema):
    run_id: str
    scope: StaticScopeEvidence


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


def _journal_has_embedded_static_facts(value: dict) -> bool:
    for member in value.get("static_scopes") or ():
        if not isinstance(member, dict):
            return False
        scope = member.get("scope")
        if not isinstance(scope, dict):
            return False
        for table, embedded in (("facts", "observation"), ("comparisons", "context")):
            records = scope.get(table) or ()
            if records and isinstance(records[0], dict) and embedded in records[0]:
                return True
    return False


def _journal_has_interned_static_audit(value: dict) -> bool:
    if (
        "static_facts" in value
        or "static_comparisons" in value
        or "static_contents" in value
        or "static_subjects" in value
    ):
        return True
    for member in value.get("static_scopes") or ():
        if not isinstance(member, dict):
            return False
        scope = member.get("scope")
        if not isinstance(scope, dict):
            return False
        facts = scope.get("facts") or ()
        if facts and isinstance(facts[0], dict) and "observation_identity" in facts[0]:
            return True
        comparisons = scope.get("comparisons") or ()
        if comparisons and isinstance(comparisons[0], dict) and "context" not in comparisons[0]:
            return True
    return False


class VerificationJournal(FrozenSchema):
    schema_version: Literal["verification-journal-v3"] = "verification-journal-v3"
    run_id: str
    command: Literal["smoke", "check", "search"]
    source_snapshot_digest: str
    package_policies: tuple[VerificationPackagePolicy, ...]
    entries: tuple[VerificationJournalEntry, ...]
    static_scopes: tuple[JournalStaticScope, ...]

    @model_validator(mode="wrap")
    @classmethod
    def resolve_interned_static_audit(cls, value, handler):
        if isinstance(value, dict) and _journal_has_embedded_static_facts(value):
            raise PydanticCustomError(
                "invalid-static-evidence",
                "journal static facts must be interned",
            )
        if isinstance(value, dict) and _journal_has_interned_static_audit(value):
            try:
                contents = tuple(
                    InternedStaticContent.model_validate(item)
                    for item in value.get("static_contents") or ()
                )
                subjects = tuple(
                    InternedStaticSubject.model_validate(item)
                    for item in value.get("static_subjects") or ()
                )
                facts = tuple(
                    InternedStaticFact.model_validate(item)
                    for item in value.get("static_facts") or ()
                )
                comparisons = tuple(
                    InternedStaticComparison.model_validate(item)
                    for item in value.get("static_comparisons") or ()
                )
                members = value.get("static_scopes") or ()
                wires = tuple(
                    StaticScopeWire.model_validate(member["scope"]) for member in members
                )
                scopes = resolve_static_scopes(
                    facts, comparisons, wires, contents=contents, subjects=subjects,
                )
            except (TypeError, KeyError, ValueError, ValidationError) as error:
                raise PydanticCustomError(
                    "invalid-static-evidence",
                    "journal static audit intern is invalid",
                ) from error
            value = {
                **value,
                "static_scopes": [
                    {"run_id": member["run_id"], "scope": scope}
                    for member, scope in zip(members, scopes, strict=True)
                ],
            }
            value.pop("static_contents", None)
            value.pop("static_subjects", None)
            value.pop("static_facts", None)
            value.pop("static_comparisons", None)
        return handler(value)

    @model_serializer(mode="wrap")
    def serialize_interned_static_audit(self, handler):
        result = handler(self)
        audit = intern_static_scopes(
            tuple(member.scope for member in self.static_scopes)
        )
        result["static_contents"] = [item.model_dump(mode="json") for item in audit.contents]
        result["static_subjects"] = [item.model_dump(mode="json") for item in audit.subjects]
        result["static_facts"] = [item.model_dump(mode="json") for item in audit.facts]
        result["static_comparisons"] = [item.model_dump(mode="json") for item in audit.comparisons]
        result["static_scopes"] = [
            {"run_id": member.run_id, "scope": wire.model_dump(mode="json")}
            for member, wire in zip(self.static_scopes, audit.scopes, strict=True)
        ]
        return result

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
        scope_refs = set()
        cells = set()
        for member in self.static_scopes:
            scope = member.scope
            cell_key = scope.cell.model_dump_json()
            if (
                member.run_id != self.run_id
                or scope.scope_ref in scope_refs
                or cell_key in cells
                or scope.cell.package not in policies
            ):
                raise PydanticCustomError(
                    "invalid-static-evidence",
                    "journal static scope must bind one Run/Cell",
                )
            scope_refs.add(scope.scope_ref)
            cells.add(cell_key)
            attempts = [consumer.preparation.attempt for consumer in scope.consumers]
            attempts.extend(item.attempt for item in scope.omissions)
            attempts.extend(item.attempt for item in scope.skips)
            attempts.extend(item.attempt for item in scope.selections)
            if scope.highest_uncollected is not None:
                attempts.append(scope.highest_uncollected.attempt)
            if any(
                attempt.identity.source_snapshot_digest != self.source_snapshot_digest
                or attempt.identity.execution_policy_identity
                != policies[scope.cell.package]
                for attempt in attempts
            ):
                raise PydanticCustomError(
                    "invalid-static-evidence",
                    "journal static scope inputs must match its Run",
                )
        return self

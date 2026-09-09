"""Run-local ty-cache envelope. Not a Process Log and not a diagnose input."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.ty_fact import TyFactDocument


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TyCacheEntry(FrozenSchema):
    subject_identity: Digest
    cache_identity: Digest
    document: TyFactDocument

    @model_validator(mode="after")
    def validate_entry(self) -> TyCacheEntry:
        document = self.document
        if document.subject.identity != self.subject_identity:
            raise ValueError("ty-cache entry subject identity must match its document")
        if document.observation_policy.cache_identity != self.cache_identity:
            raise ValueError("ty-cache entry cache identity must match its document")
        if document.fact.observation_policy_identity != document.observation_policy.identity:
            raise ValueError("ty-cache fact must bind the generation observation identity")
        return self


class TyCacheDocument(FrozenSchema):
    schema_version: Literal["pf-ty-cache-v1"] = "pf-ty-cache-v1"
    run_id: str
    entries: tuple[TyCacheEntry, ...]

    @model_validator(mode="after")
    def validate_entries(self) -> TyCacheDocument:
        keys = tuple((item.subject_identity, item.cache_identity) for item in self.entries)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("ty-cache entries must be unique and sorted by key")
        return self


def ty_cache_from_documents(
    *,
    run_id: str,
    documents: tuple[TyFactDocument, ...],
) -> TyCacheDocument:
    entries = tuple(
        sorted(
            (
                TyCacheEntry(
                    subject_identity=document.subject.identity,
                    cache_identity=document.observation_policy.cache_identity,
                    document=document,
                )
                for document in documents
            ),
            key=lambda item: (item.subject_identity, item.cache_identity),
        )
    )
    return TyCacheDocument(run_id=run_id, entries=entries)

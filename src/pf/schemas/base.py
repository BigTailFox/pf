from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict


class FrozenSchema(BaseModel):
    """Base for validated, immutable records crossing module interfaces."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


def canonical_identity_json(value: object) -> bytes:
    """Encode a domain identity preimage using PF's canonical JSON contract."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

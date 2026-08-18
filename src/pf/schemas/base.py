from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FrozenSchema(BaseModel):
    """Base for validated, immutable records crossing module interfaces."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

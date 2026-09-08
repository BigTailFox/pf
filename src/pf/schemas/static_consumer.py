"""A saved static fact associated with one actual consuming preparation."""

from __future__ import annotations

from pydantic import model_validator

from pf.schemas.base import FrozenSchema
from pf.schemas.static_preparation import StaticPreparationEvidence
from pf.schemas.ty_fact import TyFactDocument


class StaticConsumerEvidence(FrozenSchema):
    """Semantic association; Run membership and producer process refs are separate."""

    preparation: StaticPreparationEvidence
    observation: TyFactDocument

    @model_validator(mode="after")
    def validate_subject_projection(self) -> StaticConsumerEvidence:
        if self.preparation.subject != self.observation.subject:
            raise ValueError("static consumer projection must match the raw observation")
        return self

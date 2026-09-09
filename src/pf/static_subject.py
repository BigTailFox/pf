"""Project a validated static request key, independent of any dynamic Proposal."""

from __future__ import annotations

from dataclasses import dataclass

from pf.schemas.policy import TyObservationPolicy
from pf.schemas.static import StaticSubject


@dataclass(frozen=True)
class TyCheckKey:
    subject_identity: str
    cache_identity: str


def ty_check_key(subject: StaticSubject, observation: TyObservationPolicy) -> TyCheckKey:
    """Project a validated static request, independent of any dynamic Proposal."""
    return TyCheckKey(subject.identity, observation.cache_identity)

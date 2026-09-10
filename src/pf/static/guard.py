"""Turn unexpected static failures into typed unavailability without stopping a Run."""

from __future__ import annotations

import warnings

from pf.schemas.static import StaticContentUnavailable
from pf.schemas.static_comparison import StaticComparisonUnavailable


def warn_static_failure(exc: BaseException) -> None:
    warnings.warn(
        f"static observation failed; continuing without static guidance "
        f"({type(exc).__name__}: {exc})",
        RuntimeWarning,
        stacklevel=3,
    )


def unexpected_unavailable(exc: BaseException) -> StaticContentUnavailable:
    warn_static_failure(exc)
    return StaticContentUnavailable(detail="invalid-layout")


def unexpected_comparison(exc: BaseException) -> StaticComparisonUnavailable:
    warn_static_failure(exc)
    return StaticComparisonUnavailable(reason="invalid-layout")

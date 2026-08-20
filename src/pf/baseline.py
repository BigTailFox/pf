from __future__ import annotations

from typing import Literal, Protocol

from pf.environment import PreparedEnvironment
from pf.evaluation import require_full_evaluation_contract
from pf.schemas.evaluation import (
    Evaluation,
    HighestVersionVerification,
    IndeterminateEvaluation,
    StaticBaseline,
    StaticBaselineCapture,
    StaticEvaluation,
    ToolFailure,
)
from pf.schemas.project import Cell, PackagePlan
from pf.snapshot import SourceSnapshot


class HighestEnvironmentOperations(Protocol):
    def prepare(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
        resolution: Literal["highest", "lowest-direct"],
    ) -> PreparedEnvironment | ToolFailure: ...


class HighestStaticOperations(Protocol):
    def capture(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
    ) -> StaticBaselineCapture | IndeterminateEvaluation: ...


class HighestFullOperations(Protocol):
    def evaluate(
        self,
        prepared: PreparedEnvironment,
        *,
        package: PackagePlan,
        baseline: StaticBaseline,
        static_result: StaticEvaluation | None = None,
    ) -> Evaluation: ...


class HighestVersionVerifier:
    """Fully verify one highest-resolution environment and close it."""

    def __init__(
        self,
        *,
        environments: HighestEnvironmentOperations,
        static: HighestStaticOperations,
        full: HighestFullOperations,
    ) -> None:
        self._environments = environments
        self._static = static
        self._full = full

    def verify(
        self,
        *,
        package: PackagePlan,
        cell: Cell,
        snapshot: SourceSnapshot,
    ) -> HighestVersionVerification | ToolFailure | IndeterminateEvaluation:
        require_full_evaluation_contract(package, "highest-version verification")
        prepared = self._environments.prepare(
            package=package,
            cell=cell,
            snapshot=snapshot,
            resolution="highest",
        )
        if not isinstance(prepared, PreparedEnvironment):
            return prepared
        try:
            capture = self._static.capture(prepared, package=package)
            if isinstance(capture, IndeterminateEvaluation):
                return capture
            evaluation = self._full.evaluate(
                prepared,
                package=package,
                baseline=capture.baseline,
                static_result=capture.static,
            )
            return HighestVersionVerification(
                baseline=capture.baseline,
                evaluation=evaluation,
            )
        finally:
            prepared.close()

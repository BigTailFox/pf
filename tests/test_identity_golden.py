from __future__ import annotations

from pf.schemas.resolution import (
    environment_identity_digest,
    resolution_graph_id,
    resolution_request_digest,
)
from pf.schemas.project import Cell, ResolvedNode


graph = (
    ResolvedNode(name="demo", version="1.0", dependencies=("idna",)),
    ResolvedNode(name="idna", version="3.10", dependencies=()),
)


class TestIdentityGolden:
    def test_resolution_graph_id(self) -> None:
        assert resolution_graph_id(graph=graph) == (
            "resolution-f544a6e8d7807d357b5ce869b80e44f94f880dd76093a09302bbbde395cc8b2d"
        )

    def test_environment_identity_digest(self) -> None:
        assert environment_identity_digest(
            attempt_id="a" * 64,
            project_plan_digest="b" * 64,
            environment_plan_digest="c" * 64,
            graph=graph,
        ) == "ce0ead5a082509dd2dff56d9f66bd9084c92628270c4a1b360dddde1844558c5"

    def test_resolution_request_digest(self) -> None:
        assert resolution_request_digest(
            kind="project",
            package_name="demo",
            snapshot_digest="d" * 64,
            cell=Cell(
                package="demo",
                target="x86_64-unknown-linux-gnu",
                python_minor="3.10",
                extra_surface=(),
            ),
            resolution_kind="highest",
            selection=None,
            baseline_digest=None,
            context_digest="e" * 64,
            project_plan_digest=None,
            harness=(),
            source_plan_identity="f" * 64,
        ) == "3d1fb2ee2675c345c778311efdc04230af28846699626e6e78d9e3ad2487e09e"

from __future__ import annotations

from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.version import Version
import pytest
import tomli

from pf.resolution import (
    ResolutionArtifact,
    InstalledResolution,
    InstallFailure,
    UV_DIAGNOSTIC_PROFILES,
    NativeResolutionPlan,
    ResolutionContext,
    ResolutionPackage,
    ResolutionPlan,
    ResolutionRunContext,
    resolution_graph_id,
)
from pf.schemas.evaluation import ProcessResult
from pf.schemas.project import Cell, ResolvedNode, SourceIdentity


def _process() -> ProcessResult:
    return ProcessResult(
        exit_code=0,
        signal=None,
        duration_seconds=0.1,
        stdout="",
        stderr="",
    )


def _context() -> ResolutionContext:
    run = ResolutionRunContext(
        uv_version="0.12.5",
        release_cutoff="2026-08-23T01:02:03+00:00",
    )
    return ResolutionContext.from_inputs(
        run=run,
        cell=Cell(
            package="demo",
            target="x86_64-unknown-linux-gnu",
            python_minor="3.11",
            extra_surface=(),
        ),
        source_policy_identity="source-policy",
        allow_prereleases=False,
    )


def _plan() -> ResolutionPlan:
    return ResolutionPlan.from_evidence(
        kind="project",
        request_digest="request",
        context=_context(),
        packages=(
            ResolutionPackage(
                name="demo-dependency",
                version="1.0",
                source=SourceIdentity(kind="registry"),
            ),
        ),
        direct_harness=(),
        native=NativeResolutionPlan.from_content('lock-version = "1.0"\n'),
        process=_process(),
    )


class TestResolutionIdentity:
    def test_resolution_plan_rejects_an_empty_request_identity(self) -> None:
        plan = _plan()
        values: dict[str, Any] = {
            field: getattr(plan, field)
            for field in ResolutionPlan.model_fields
        }
        values["request_digest"] = ""

        with pytest.raises(ValueError, match="request digest"):
            ResolutionPlan(**values)

    def test_resolution_plan_rejects_duplicate_packages(self) -> None:
        plan = _plan()
        values: dict[str, Any] = {
            field: getattr(plan, field)
            for field in ResolutionPlan.model_fields
        }
        values["packages"] = (*values["packages"], *values["packages"])

        with pytest.raises(ValueError, match="sorted and unique"):
            ResolutionPlan(**values)

    @pytest.mark.parametrize(
        "cutoff",
        ("not-a-timestamp", "2026-08-23T01:02:03"),
        ids=("invalid", "naive"),
    )
    def test_run_context_rejects_an_invalid_release_cutoff(self, cutoff: str) -> None:
        with pytest.raises(ValueError):
            ResolutionRunContext(uv_version="0.12.5", release_cutoff=cutoff)

    @pytest.mark.parametrize(
        ("source_policy_identity", "digest"),
        (("", "digest"), ("source-policy", "tampered")),
        ids=("empty-source-policy", "digest-drift"),
    )
    def test_resolution_context_rejects_incomplete_identity(
        self,
        source_policy_identity: str,
        digest: str,
    ) -> None:
        context = _context()

        with pytest.raises(ValueError):
            ResolutionContext(
                run=context.run,
                cell=context.cell,
                source_policy_identity=source_policy_identity,
                prerelease_policy=context.prerelease_policy,
                digest=digest,
            )

    @pytest.mark.parametrize(
        ("filename", "locator", "content_hash"),
        (
            ("", "artifact.whl", f"sha256:{'a' * 64}"),
            ("artifact.whl", "", f"sha256:{'a' * 64}"),
            ("artifact.whl", "artifact.whl", "sha256:short"),
        ),
        ids=("filename", "locator", "hash"),
    )
    def test_resolution_artifact_rejects_incomplete_evidence(
        self,
        filename: str,
        locator: str,
        content_hash: str,
    ) -> None:
        with pytest.raises(ValueError):
            ResolutionArtifact(
                filename=filename,
                kind="wheel",
                locator=locator,
                content_hash=content_hash,
            )

    @pytest.mark.parametrize(
        "changes",
        (
            {"name": "Demo_Dependency"},
            {"version": "not a version"},
            {"version": "01.0"},
            {"version": None},
            {"dependencies": ("b", "a")},
        ),
        ids=(
            "canonical-name",
            "valid-version",
            "normalized-version",
            "versionless-registry",
            "dependency-order",
        ),
    )
    def test_resolution_package_rejects_noncanonical_evidence(
        self,
        changes: dict[str, object],
    ) -> None:
        values: dict[str, Any] = {
            "name": "demo-dependency",
            "version": "1.0",
            "source": SourceIdentity(kind="registry"),
        }
        values.update(changes)

        with pytest.raises(ValueError):
            ResolutionPackage(**values)

    def test_resolution_package_rejects_duplicate_artifacts(self) -> None:
        first = ResolutionArtifact(
            filename="first.whl",
            kind="wheel",
            locator="first.whl",
            content_hash=f"sha256:{'a' * 64}",
        )

        with pytest.raises(ValueError, match="sorted and unique"):
            ResolutionPackage(
                name="demo",
                version="1",
                source=SourceIdentity(kind="registry"),
                available_artifacts=(first, first),
            )

    def test_resolution_package_rejects_unavailable_selected_artifact(self) -> None:
        first = ResolutionArtifact(
            filename="first.whl",
            kind="wheel",
            locator="first.whl",
            content_hash=f"sha256:{'a' * 64}",
        )
        second = first.model_copy(update={"filename": "second.whl"})

        with pytest.raises(ValueError, match="belong"):
            ResolutionPackage(
                name="demo",
                version="1",
                source=SourceIdentity(kind="registry"),
                available_artifacts=(first,),
                selected_artifact=second,
            )

    def test_native_plan_rejects_digest_drift(self) -> None:
        with pytest.raises(ValueError, match="digest"):
            NativeResolutionPlan(content="lock", digest="tampered")

    @pytest.mark.parametrize(
        "graph",
        (
            (ResolvedNode(name="Demo_Package", version="1"),),
            (
                ResolvedNode(
                    name="demo",
                    version="1",
                    dependencies=("z", "a"),
                ),
            ),
            (
                ResolvedNode(
                    name="demo",
                    version="1",
                    dependencies=("Demo_Package",),
                ),
            ),
        ),
        ids=("node-name", "dependency-order", "dependency-name"),
    )
    def test_resolution_graph_id_rejects_noncanonical_graph(
        self,
        graph: tuple[ResolvedNode, ...],
    ) -> None:
        with pytest.raises(ValueError):
            resolution_graph_id(graph)

    def test_distribution_pins_qualified_runtime_tools(self) -> None:
        with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as stream:
            dependencies = tomli.load(stream)["project"]["dependencies"]
        requirements = {
            requirement.name: requirement
            for raw_requirement in dependencies
            if (requirement := Requirement(raw_requirement)).name in {"ty", "uv"}
        }

        assert str(requirements["uv"].specifier) == "==0.12.5"
        assert str(requirements["ty"].specifier) == "==0.0.74"
        assert tuple(UV_DIAGNOSTIC_PROFILES) == ("0.12.5",)
        assert Version("0.12.5") in requirements["uv"].specifier

    def test_install_outcomes_are_bound_to_the_validated_plan(self) -> None:
        installed = InstalledResolution(
            plan_digest="plan",
            process=_process(),
        )

        assert installed.plan_digest == "plan"
        with pytest.raises(ValueError, match="cannot prove"):
            InstallFailure(
                plan_digest="plan",
                cause="HARNESS_CONFLICT",
                process=_process().model_copy(update={"exit_code": 1}),
            )

    def test_run_context_supports_exactly_the_pinned_uv_version(self) -> None:
        contexts = tuple(
            ResolutionRunContext(
                uv_version=version,
                release_cutoff="2026-08-23T01:02:03+00:00",
            )
            for version in UV_DIAGNOSTIC_PROFILES
        )

        assert tuple(item.uv_version for item in contexts) == tuple(
            UV_DIAGNOSTIC_PROFILES
        )
        assert tuple(item.qualification_profile for item in contexts) == tuple(
            UV_DIAGNOSTIC_PROFILES.values()
        )

    def test_run_context_rejects_unqualified_or_mismatched_profiles(self) -> None:
        with pytest.raises(ValueError, match="unsupported uv version"):
            ResolutionRunContext(
                uv_version="0.11.29",
                qualification_profile="uv-diagnostics-0.11.29-v1",
                release_cutoff="2026-08-23T01:02:03+00:00",
            )
        with pytest.raises(ValueError, match="does not match"):
            ResolutionRunContext(
                uv_version="0.12.5",
                qualification_profile="uv-diagnostics-0.12.4-v1",
                release_cutoff="2026-08-23T01:02:03+00:00",
            )

    def test_context_covers_run_cell_source_and_candidate_policy(self) -> None:
        context = _context()

        changed_cutoff = ResolutionContext.from_inputs(
            run=context.run.model_copy(
                update={"release_cutoff": "2026-08-23T01:02:04+00:00"}
            ),
            cell=context.cell,
            source_policy_identity=context.source_policy_identity,
            allow_prereleases=False,
        )
        prereleases = ResolutionContext.from_inputs(
            run=context.run,
            cell=context.cell,
            source_policy_identity=context.source_policy_identity,
            allow_prereleases=True,
        )

        assert context.digest != changed_cutoff.digest
        assert context.digest != prereleases.digest

    def test_plan_identity_uses_normalized_and_native_evidence(self) -> None:
        native = NativeResolutionPlan.from_content(
            'lock-version = "1.0"\ncreated-by = "uv"\npackages = []\n'
        )
        package = ResolutionPackage(
            name="demo-dependency",
            version="1.0",
            source=SourceIdentity(kind="registry"),
        )

        plan = ResolutionPlan.from_evidence(
            kind="project",
            request_digest="request",
            context=_context(),
            packages=(package,),
            direct_harness=(),
            native=native,
            process=_process(),
        )

        assert plan.digest
        assert plan.native.content.startswith("lock-version")
        assert plan.model_dump(mode="json")["native"] == {
            "format": "pylock.toml",
            "digest": native.digest,
        }

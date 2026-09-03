from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import tempfile
from typing import Literal

from pf.adapters.process import SubprocessRunner
from pf.adapters.uv import UvAdapter
from pf.candidates import CandidateBuilder
from pf.environment import (
    EnvironmentFactory,
    ExactSelection,
    HighestResolution,
    PreparedEnvironment,
)
from pf.failure import FailurePolicy
from pf.project import ProjectLoader
from pf.schemas.config import RootPackage, WorkspacePackage
from pf.schemas.evaluation import (
    AttemptFailureScope,
    PrepareFailure,
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
)
from pf.schemas.project import SelectedCandidate, SourcePlan
from pf.snapshot import SnapshotBuilder


Scenario = Literal["root", "member", "root-and-member"]
SCENARIOS: tuple[Scenario, ...] = ("root", "member", "root-and-member")


@dataclass(frozen=True)
class QualificationRecord:
    scenario: Scenario
    uv_version: str
    candidate_dependencies: tuple[str, ...]
    managed_registry_dependencies: tuple[str, ...]
    exact_artifact_dependencies: tuple[str, ...]
    retained_local_dependencies: tuple[str, ...]
    compile_suppressions: tuple[tuple[str, ...], ...]
    compile_count: int
    install_count: int
    installed_graph_contains_expected: bool
    source_tables_preserved: bool


@dataclass(frozen=True)
class UnmanagedWorkspaceFailClosedRecord:
    disposition: str
    cause: str
    stage: str
    summary_code: str | None
    compile_suppressions: tuple[str, ...]
    install_count: int
    source_tables_preserved: bool


class RecordingRunner(SubprocessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.specs: list[ProcessSpec] = []

    def run(self, spec: ProcessSpec) -> ProcessResult | ProcessTerminalUnavailable:
        self.specs.append(spec)
        return super().run(spec)


def _package_document(*, include_sources: bool, local_path: str) -> str:
    sources = (
        """
[tool.uv.sources]
idna = { workspace = true }
certifi = { workspace = true }
pf-qualification-local = { workspace = true }
"""
        if include_sources
        else ""
    ).replace(
        "pf-qualification-local = { workspace = true }",
        f'pf-qualification-local = {{ path = "{local_path}" }}',
    )
    return (
        """
[project]
name = "pf-qualification-app"
version = "1.0"
requires-python = ">=3.10"
dependencies = ["idna>=3", "certifi>=2024", "pf-qualification-local>=1"]

[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"

[dependency-groups]
test = []
"""
        + sources
    ).strip() + "\n"


def _root_document(*, scenario: Scenario) -> str:
    project = (
        _package_document(
            include_sources=scenario == "root",
            local_path="packages/local",
        )
        if scenario == "root"
        else ""
    )
    sources = (
        """
[tool.uv.sources]
idna = { workspace = true }
certifi = { workspace = true }
pf-qualification-local = { path = "packages/local" }
"""
        if scenario == "root-and-member"
        else ""
    )
    return (
        project
        + sources
        + """
[tool.uv.workspace]
members = ["packages/*"]

[tool.pf]
python = ["3.10"]
platform = ["x86_64-unknown-linux-gnu"]
unmanaged-deps = ["pf-qualification-local"]
test-command = ["python", "-c", "import pf_qualification_app"]
"""
    ).strip() + "\n"


def _write_workspace(root: Path, scenario: Scenario) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        _root_document(scenario=scenario),
        encoding="utf-8",
    )
    if scenario != "root":
        app = root / "packages" / "app"
        (app / "src" / "pf_qualification_app").mkdir(parents=True)
        (app / "src" / "pf_qualification_app" / "__init__.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
        (app / "pyproject.toml").write_text(
            _package_document(include_sources=True, local_path="../local"),
            encoding="utf-8",
        )
    else:
        (root / "src" / "pf_qualification_app").mkdir(parents=True)
        (root / "src" / "pf_qualification_app" / "__init__.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
    for name in ("idna", "certifi"):
        member = root / "packages" / name
        member.mkdir(parents=True)
        (member / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "999"\n',
            encoding="utf-8",
        )
    local = root / "packages" / "local"
    (local / "src" / "pf_qualification_local").mkdir(parents=True)
    (local / "src" / "pf_qualification_local" / "__init__.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (local / "pyproject.toml").write_text(
        """
[project]
name = "pf-qualification-local"
version = "1.0"

[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _suppression_names(spec: ProcessSpec) -> tuple[str, ...]:
    return tuple(
        spec.argv[index + 1]
        for index, value in enumerate(spec.argv)
        if value == "--no-sources-package"
    )


def qualify_scenario(root: Path, scenario: Scenario) -> QualificationRecord:
    _write_workspace(root, scenario)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("pyproject.toml")
    }
    selector = (
        RootPackage()
        if scenario == "root"
        else WorkspacePackage(canonical_name="pf-qualification-app")
    )
    project = ProjectLoader().load(root=root, selector=selector)
    package = project.target
    source_plan = SourcePlan.for_package(package, "SEARCH")
    snapshot = SnapshotBuilder.without_processes().build(
        root,
        owned_pyproject_paths=project.owned_pyproject_paths,
    )
    runner = RecordingRunner()
    adapter = UvAdapter(runner)
    prepared: PreparedEnvironment | None = None
    try:
        factory = EnvironmentFactory(adapter)
        prepared_result = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            resolution=HighestResolution(),
            source_plan=source_plan,
        )
        if not isinstance(prepared_result, PreparedEnvironment):
            failure = prepared_result.failure
            diagnostic = (
                failure.process.diagnostic()
                if isinstance(failure.process, ProcessResult)
                else "no process diagnostic"
            )
            raise RuntimeError(
                "workspace source qualification failed: "
                f"{failure.cause}@{failure.stage}:{failure.summary_code}: "
                f"{diagnostic}; detail={failure.detail}"
            )
        prepared = prepared_result
        candidates = CandidateBuilder(adapter).build(
            package=package,
            cell=package.cells[0],
            baseline=prepared.proposal.managed_vector,
            source_plan=source_plan,
        )
        project_sources = {
            item.name: item.source for item in prepared.project_plan.packages
        }
        managed = tuple(
            sorted(
                name
                for name in ("certifi", "idna")
                if project_sources[name].kind == "registry"
            )
        )
        retained = tuple(
            sorted(
                name
                for name in ("pf-qualification-local",)
                if project_sources[name].kind == "path"
            )
        )
        installed_names = {node.name for node in prepared.proposal.resolved_graph}
        harness_baseline = prepared.harness_baseline
        prepared.close()
        prepared = None

        selected = tuple(
            SelectedCandidate(
                dependency=item.dependency,
                version=item.candidates[-1].version,
                artifact=item.candidates[-1].artifact,
            )
            for item in candidates
        )
        exact_result = factory.prepare(
            package=package,
            cell=package.cells[0],
            snapshot=snapshot,
            resolution=ExactSelection(
                selection=selected,
                harness_baseline=harness_baseline,
            ),
            source_plan=source_plan,
        )
        if not isinstance(exact_result, PreparedEnvironment):
            failure = exact_result.failure
            diagnostic = (
                failure.process.diagnostic()
                if isinstance(failure.process, ProcessResult)
                else "no process diagnostic"
            )
            raise RuntimeError(
                "exact workspace source qualification failed: "
                f"{failure.cause}@{failure.stage}:{failure.summary_code}: "
                f"{diagnostic}; detail={failure.detail}"
            )
        prepared = exact_result
        exact_artifacts = tuple(
            sorted(
                item.name
                for item in prepared.project_plan.packages
                if item.name in {"certifi", "idna"}
                and item.source.kind == "url"
                and item.selected_artifact is not None
            )
        )
        installed_names &= {node.name for node in prepared.proposal.resolved_graph}
        compiles = tuple(
            spec for spec in runner.specs if spec.argv[1:3] == ("pip", "compile")
        )
        if len(compiles) != 4:
            raise RuntimeError(
                "qualification requires two uv compile calls per Attempt"
            )
        suppressions = tuple(_suppression_names(spec) for spec in compiles)
        if suppressions != (("certifi", "idna"),) * 4:
            raise RuntimeError("uv source suppression did not match the SourcePlan")
        if any("--no-sources" in spec.argv for spec in compiles):
            raise RuntimeError("global uv source suppression is forbidden")
        if any(
            name not in spec.environment_removals
            for spec in compiles
            for name in ("UV_NO_SOURCES", "UV_NO_SOURCES_PACKAGE")
        ):
            raise RuntimeError("uv source-selection environment was not isolated")
        expected_names = {
            "certifi",
            "idna",
            "pf-qualification-app",
            "pf-qualification-local",
        }
        after = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("pyproject.toml")
        }
        run = prepared.project_plan.context.run
        return QualificationRecord(
            scenario=scenario,
            uv_version=run.uv_version,
            candidate_dependencies=tuple(item.dependency for item in candidates),
            managed_registry_dependencies=managed,
            exact_artifact_dependencies=exact_artifacts,
            retained_local_dependencies=retained,
            compile_suppressions=suppressions,
            compile_count=len(compiles),
            install_count=sum(
                spec.argv[1:3] == ("pip", "sync") for spec in runner.specs
            ),
            installed_graph_contains_expected=expected_names <= installed_names,
            source_tables_preserved=before == after,
        )
    finally:
        if prepared is not None:
            prepared.close()
        snapshot.close()


def qualify_unmanaged_workspace_fail_closed(
    root: Path,
) -> UnmanagedWorkspaceFailClosedRecord:
    _write_workspace(root, "root")
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'pf-qualification-local = { path = "packages/local" }',
            "pf-qualification-local = { workspace = true }",
        ),
        encoding="utf-8",
    )
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("pyproject.toml")
    }
    project = ProjectLoader().load(root=root, selector=RootPackage())
    package = project.target
    source_plan = SourcePlan.for_package(package, "SEARCH")
    snapshot = SnapshotBuilder.without_processes().build(
        root,
        owned_pyproject_paths=project.owned_pyproject_paths,
    )
    runner = RecordingRunner()
    result = EnvironmentFactory(UvAdapter(runner)).prepare(
        package=package,
        cell=package.cells[0],
        snapshot=snapshot,
        resolution=HighestResolution(),
        source_plan=source_plan,
    )
    try:
        if not isinstance(result, PrepareFailure):
            result.close()
            raise RuntimeError(
                "mixed managed and unmanaged workspace sources must fail closed"
            )
        failure = result.failure
        record = FailurePolicy().classify(
            scope=AttemptFailureScope(attempt=result.attempt),
            cause=failure.cause,
            stage=failure.stage,
            process=failure.process,
            summary_code=failure.summary_code,
            detail=failure.detail,
            project_plan_digest=result.project_plan_digest,
            environment_plan_digest=result.environment_plan_digest,
        )
        compiles = tuple(
            spec for spec in runner.specs if spec.argv[1:3] == ("pip", "compile")
        )
        if len(compiles) != 1:
            raise RuntimeError("fail-closed qualification requires one failed compile")
        return UnmanagedWorkspaceFailClosedRecord(
            disposition=record.disposition,
            cause=failure.cause,
            stage=failure.stage,
            summary_code=failure.summary_code,
            compile_suppressions=_suppression_names(compiles[0]),
            install_count=sum(
                spec.argv[1:3] == ("pip", "sync") for spec in runner.specs
            ),
            source_tables_preserved=(
                before
                == {
                    path.relative_to(root).as_posix(): path.read_bytes()
                    for path in root.rglob("pyproject.toml")
                }
            ),
        )
    finally:
        snapshot.close()


def qualify(selected: frozenset[Scenario]) -> dict[str, object]:
    chosen = tuple(item for item in SCENARIOS if not selected or item in selected)
    with tempfile.TemporaryDirectory(
        prefix="pf-uv-workspace-qualification-"
    ) as directory:
        root = Path(directory)
        records = tuple(
            qualify_scenario(root / scenario, scenario) for scenario in chosen
        )
        fail_closed = qualify_unmanaged_workspace_fail_closed(
            root / "unmanaged-workspace"
        )
    return {
        "schema": "pf-uv-workspace-sources-qualification-v1",
        "as_of": "2026-08-29",
        "host_scope": "linux-x86_64",
        "resolution_target": "x86_64-unknown-linux-gnu",
        "python_minor": "3.10",
        "uv_version": "0.12.5",
        "scenarios": [asdict(record) for record in records],
        "unmanaged_workspace_fail_closed": asdict(fail_closed),
        "all_passed": all(
            record.uv_version == "0.12.5"
            and record.candidate_dependencies == ("certifi", "idna")
            and record.managed_registry_dependencies == ("certifi", "idna")
            and record.exact_artifact_dependencies == ("certifi", "idna")
            and record.retained_local_dependencies == ("pf-qualification-local",)
            and record.compile_count == 4
            and record.install_count == 2
            and record.installed_graph_contains_expected
            and record.source_tables_preserved
            for record in records
        )
        and fail_closed.disposition == "INDETERMINATE"
        and fail_closed.cause == "TOOL_FAILURE"
        and fail_closed.stage == "resolve-project"
        and fail_closed.summary_code == "resolution-diagnostic-unknown"
        and fail_closed.compile_suppressions == ("certifi", "idna")
        and fail_closed.install_count == 0
        and fail_closed.source_tables_preserved,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    arguments = parser.parse_args()
    result = qualify(frozenset(arguments.scenario or ()))
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

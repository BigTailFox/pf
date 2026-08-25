from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import threading
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import Tag
from packaging.utils import canonicalize_name
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError
from uv import find_uv_bin

from pf.adapters.process import ProcessRunner, SecretRedactor, read_process_output
from pf.adapters.uv_diagnostics import (
    classify_resolution_diagnostic,
    diagnostic_digest,
)
from pf.adapters.uv_lock import (
    UvLockError,
    normalize_uv_pylock_paths,
    parse_uv_pylock,
)
from pf.errors import InfrastructureError
from pf.environment import (
    ExactSelection,
    LowestDirectResolution,
    ResolutionRequest,
)
from pf.schemas.evaluation import (
    GraphOutcome,
    GraphSuccess,
    InterpreterOutcome,
    InterpreterSuccess,
    ProcessResult,
    ProcessSpec,
    ToolFailure,
    ToolOutcome,
    ToolSuccess,
)
from pf.harness import harness_requirement_policy, render_harness_requirement
from pf.resolution import (
    InstalledResolution,
    InstallFailure,
    InstallOutcome,
    NativeResolutionPlan,
    ResolutionContext,
    ResolutionIndeterminate,
    ResolutionOutcome,
    ResolutionPackage,
    ResolutionPlan,
    ResolutionRunContext,
    ResolutionUnsat,
)
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    Cell,
    HarnessResolutionRequirement,
    HarnessSelection,
    InterpreterIdentity,
    ResolvedNode,
    SelectedCandidate,
    public_locator,
    SourceIdentity,
)

_JSON_SUMMARY_LIMIT = 16 * 1024 * 1024


class RegistryAccess:
    """Process-local registry credentials; never part of portable PF schemas."""

    __slots__ = ("_credentials",)

    def __init__(
        self,
        credentials: Mapping[str | None, tuple[str | None, str | None]] | None = None,
    ) -> None:
        self._credentials = dict(credentials or {})

    @classmethod
    def basic(
        cls,
        *,
        index: str | None,
        username: str,
        password: str,
    ) -> "RegistryAccess":
        key = cls._environment_key(index) if index is not None else None
        return cls({key: (username, password)})

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RegistryAccess":
        credentials: dict[str | None, tuple[str | None, str | None]] = {}
        default_username = environment.get("UV_INDEX_USERNAME")
        default_password = environment.get("UV_INDEX_PASSWORD")
        if default_username is not None or default_password is not None:
            credentials[None] = (default_username, default_password)

        suffixes: dict[str, dict[str, str]] = {}
        for name, value in environment.items():
            if not name.startswith("UV_INDEX_"):
                continue
            if name in {"UV_INDEX_USERNAME", "UV_INDEX_PASSWORD"}:
                continue
            for suffix in ("_USERNAME", "_PASSWORD"):
                if name.endswith(suffix):
                    key = name[len("UV_INDEX_") : -len(suffix)]
                    suffixes.setdefault(key, {})[suffix] = value
                    break
        for key, values in suffixes.items():
            credentials[key] = (
                values.get("_USERNAME"),
                values.get("_PASSWORD"),
            )
        return cls(credentials)

    @property
    def secret_literals(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    value
                    for credentials in self._credentials.values()
                    for value in credentials
                    if value
                },
                key=len,
                reverse=True,
            )
        )

    def authorization(self, source: SourceIdentity) -> str | None:
        key = self._environment_key(source.index) if source.index is not None else None
        credentials = self._credentials.get(key)
        if credentials is None:
            return None
        username, password = credentials
        if not username or not password:
            label = source.index or "default"
            raise InfrastructureError(f"registry credentials are incomplete: {label}")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        return f"Basic {encoded}"

    @staticmethod
    def _environment_key(index: str) -> str:
        return "".join(character if character.isalnum() else "_" for character in index).upper()


class UvAdapter:
    """Own every uv argv and classify uv process facts into PF outcomes."""

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        registry_access: RegistryAccess | None = None,
        redactor: SecretRedactor | None = None,
        uv_executable: str | Path | None = None,
    ) -> None:
        self._runner = runner
        self._registry_access = registry_access or RegistryAccess()
        self._redactor = redactor or SecretRedactor(
            self._registry_access.secret_literals
        )
        requested_executable = (
            find_uv_bin() if uv_executable is None else str(uv_executable)
        )
        discovered = (
            requested_executable
            if uv_executable is None
            else shutil.which(requested_executable)
        )
        self._uv_executable = (
            Path(discovered).resolve().as_posix()
            if discovered is not None
            else requested_executable
        )
        self._resolution_lock = threading.Lock()
        self._resolution_run: ResolutionRunContext | ToolFailure | None = None
        self._candidate_lock = threading.Lock()
        self._candidate_responses: dict[tuple[str, SourceIdentity], bytes] = {}

    def resolution_run_context(
        self,
        *,
        root: Path,
        timeout_seconds: int | None,
    ) -> ResolutionRunContext | ToolFailure:
        """Establish the exact uv protocol once for this verification run."""
        with self._resolution_lock:
            if self._resolution_run is not None:
                return self._resolution_run
            cutoff = datetime.now(timezone.utc).isoformat()
            process = self._runner.run(
                ProcessSpec(
                    argv=(self._uv_executable, "--version"),
                    cwd=root.as_posix(),
                    timeout_seconds=timeout_seconds,
                )
            )
            output = read_process_output(self._runner, process)
            match = re.fullmatch(
                r"uv (?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?: \([^\n]+\))?\s*",
                output.stdout,
            )
            if (
                process.exit_code != 0
                or not process.stdout_complete
                or not process.stderr_complete
                or match is None
            ):
                self._resolution_run = ToolFailure(
                    cause="TOOL_FAILURE",
                    stage="resolver-context",
                    process=process,
                )
                return self._resolution_run
            try:
                self._resolution_run = ResolutionRunContext(
                    uv_version=match.group("version"),
                    release_cutoff=cutoff,
                )
            except ValidationError:
                self._resolution_run = ToolFailure(
                    cause="TOOL_FAILURE",
                    stage="resolver-context",
                    process=process,
                )
            return self._resolution_run

    def resolve_project(
        self,
        *,
        package: Path,
        package_name: str,
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        request_digest: str,
        work_directory: Path,
        allow_prereleases: bool,
        timeout_seconds: int | None,
    ) -> ResolutionOutcome:
        return self._resolve(
            kind="project",
            package=package,
            package_name=package_name,
            cell=cell,
            resolution=resolution,
            context=context,
            request_digest=request_digest,
            project_plan=None,
            harness=(),
            work_directory=work_directory,
            allow_prereleases=allow_prereleases,
            timeout_seconds=timeout_seconds,
        )

    def resolve_environment(
        self,
        *,
        package: Path,
        package_name: str,
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        request_digest: str,
        project_plan: ResolutionPlan,
        harness: tuple[HarnessResolutionRequirement, ...],
        work_directory: Path,
        allow_prereleases: bool,
        timeout_seconds: int | None,
    ) -> ResolutionOutcome:
        return self._resolve(
            kind="environment",
            package=package,
            package_name=package_name,
            cell=cell,
            resolution=resolution,
            context=context,
            request_digest=request_digest,
            project_plan=project_plan,
            harness=harness,
            work_directory=work_directory,
            allow_prereleases=allow_prereleases,
            timeout_seconds=timeout_seconds,
        )

    def _resolve(
        self,
        *,
        kind: Literal["project", "environment"],
        package: Path,
        package_name: str,
        cell: Cell,
        resolution: ResolutionRequest,
        context: ResolutionContext,
        request_digest: str,
        project_plan: ResolutionPlan | None,
        harness: tuple[HarnessResolutionRequirement, ...],
        work_directory: Path,
        allow_prereleases: bool,
        timeout_seconds: int | None,
    ) -> ResolutionOutcome:
        stage: Literal["resolve-project", "resolve-environment"] = (
            "resolve-project" if kind == "project" else "resolve-environment"
        )
        request_file = work_directory / f"{kind}-requirements.in"
        output_file = work_directory / f"pylock.pf-{kind}.toml"
        source_root = work_directory / "source"
        if not source_root.is_dir():
            source_root = package
        output_file.unlink(missing_ok=True)
        request_file.write_text(
            self._resolution_requirements(
                package=package,
                cell=cell,
                resolution=resolution,
                harness=harness,
                source_root=source_root,
            ),
            encoding="utf-8",
        )
        argv: list[str] = [
            self._uv_executable,
            "pip",
            "compile",
            "pyproject.toml",
            request_file.as_posix(),
            "--format",
            "pylock.toml",
            "--output-file",
            output_file.as_posix(),
            "--python-version",
            cell.python_minor,
            "--python-platform",
            cell.target,
            "--resolution",
            (
                "lowest-direct"
                if kind == "project"
                and isinstance(resolution, LowestDirectResolution)
                else "highest"
            ),
            "--exclude-newer",
            context.run.release_cutoff,
            "--no-header",
            "--no-progress",
            "--color",
            "never",
        ]
        for extra in cell.extra_surface:
            argv.extend(("--extra", extra))
        if allow_prereleases or any(
            item.declaration.prerelease_allowed for item in harness
        ):
            argv.extend(("--prerelease", "allow"))
        if project_plan is not None:
            constraints = work_directory / "project-constraints.in"
            constraints.write_text(
                self._project_constraints(project_plan.packages),
                encoding="utf-8",
            )
            argv.extend(("--constraints", constraints.as_posix()))
        process = self._runner.run(
            ProcessSpec(
                argv=tuple(argv),
                cwd=package.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        output = read_process_output(self._runner, process)
        if process.exit_code != 0:
            classification = classify_resolution_diagnostic(
                uv_version=context.run.uv_version,
                process=process,
                stdout=output.stdout,
                stderr=output.stderr,
            )
            if classification.kind == "unsat":
                assert classification.proof_code is not None
                return ResolutionUnsat(
                    stage=stage,
                    request_digest=request_digest,
                    context=context,
                    proof_code=classification.proof_code,
                    diagnostic_digest=diagnostic_digest(
                        output.stdout, output.stderr
                    ),
                    process=process,
                )
            assert classification.cause is not None
            assert classification.summary_code is not None
            return ResolutionIndeterminate(
                stage=stage,
                request_digest=request_digest,
                context=context,
                cause=classification.cause,
                summary_code=classification.summary_code,
                process=process,
            )
        if not process.stdout_complete or not process.stderr_complete:
            return ResolutionIndeterminate(
                stage=stage,
                request_digest=request_digest,
                context=context,
                cause="TOOL_FAILURE",
                summary_code="resolution-output-incomplete",
                process=process,
            )
        try:
            native_content = output_file.read_text(encoding="utf-8")
            source_root = source_root.resolve()
            try:
                package_locator = package.resolve().relative_to(source_root).as_posix()
            except ValueError:
                package_locator = "."
            packages = tuple(
                item
                for item in parse_uv_pylock(
                    native_content,
                    python_minor=cell.python_minor,
                    source_root=source_root,
                    lock_root=output_file.parent,
                )
                if not (
                    item.name == package_name
                    and item.source.kind == "path"
                    and item.source.locator in {".", package_locator}
                )
            )
            if any(item.version is None for item in packages):
                raise ValueError("resolution plan omitted a package version")
            direct_harness = (
                self._direct_harness(packages=packages, requirements=harness)
                if kind == "environment"
                else ()
            )
            native_content = normalize_uv_pylock_paths(
                native_content,
                source_root=source_root,
                lock_root=output_file.parent,
            )
        except (OSError, UnicodeError, UvLockError, ValueError):
            return ResolutionIndeterminate(
                stage=stage,
                request_digest=request_digest,
                context=context,
                cause="TOOL_FAILURE",
                summary_code="resolution-plan-invalid",
                process=process,
            )
        native = NativeResolutionPlan.from_content(native_content)
        return ResolutionPlan.from_evidence(
            kind=kind,
            request_digest=request_digest,
            context=context,
            packages=packages,
            direct_harness=direct_harness,
            native=native,
            process=process,
        )

    def install_resolution(
        self,
        *,
        plan: ResolutionPlan,
        interpreter: Path,
        cwd: Path,
        work_directory: Path,
        timeout_seconds: int | None,
    ) -> InstallOutcome:
        lock_file = work_directory / "pylock.pf-install.toml"
        lock_file.write_text(plan.native.content, encoding="utf-8")
        process = self._runner.run(
            ProcessSpec(
                argv=(
                    self._uv_executable,
                    "pip",
                    "sync",
                    "--python",
                    interpreter.as_posix(),
                    "--no-progress",
                    "--color",
                    "never",
                    lock_file.as_posix(),
                ),
                cwd=cwd.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        outcome = self._classify(process, stage="install-environment")
        if isinstance(outcome, ToolFailure):
            return InstallFailure(
                plan_digest=plan.digest,
                cause=outcome.cause,
                process=outcome.process,
                summary_code=outcome.summary_code,
            )
        return InstalledResolution(plan_digest=plan.digest, process=outcome.process)

    @staticmethod
    def _resolution_requirements(
        *,
        package: Path,
        cell: Cell,
        resolution: ResolutionRequest,
        harness: tuple[HarnessResolutionRequirement, ...],
        source_root: Path,
    ) -> str:
        del package
        extras = f"[{','.join(cell.extra_surface)}]" if cell.extra_surface else ""
        requirements = [f"-e .{extras}"]
        if isinstance(resolution, ExactSelection):
            requirements.extend(
                UvAdapter._selected_requirement(item)
                for item in resolution.selection
            )
        for item in harness:
            requirements.extend(
                UvAdapter._render_harness_requirements(
                    item,
                    source_root=source_root,
                )
            )
        return "".join(f"{item}\n" for item in requirements)

    @staticmethod
    def _render_harness_requirements(
        requirement: HarnessResolutionRequirement,
        *,
        source_root: Path,
    ) -> tuple[str, ...]:
        rendered = render_harness_requirement(requirement)
        declaration = requirement.declaration
        source = declaration.source
        if source.kind == "registry":
            return (rendered,)
        extras = (
            f"[{','.join(declaration.requested_extras)}]"
            if declaration.requested_extras
            else ""
        )
        marker = f"; {declaration.marker}" if declaration.marker else ""
        if source.kind == "url":
            assert source.locator is not None and source.content_hash is not None
            digest = source.content_hash.removeprefix("sha256:")
            direct = (
                f"{declaration.name}{extras} @ "
                f"{source.locator}#sha256={digest}{marker}"
            )
        elif source.kind == "git":
            assert source.locator is not None and source.commit is not None
            direct = (
                f"{declaration.name}{extras} @ "
                f"git+{source.locator}@{source.commit}{marker}"
            )
        else:
            assert source.locator is not None
            target = (source_root / source.locator).resolve()
            root = source_root.resolve()
            if target != root and root not in target.parents:
                raise ValueError("harness source path escapes the source snapshot")
            direct = f"{declaration.name}{extras} @ {target.as_uri()}{marker}"
        return (rendered, direct)

    @staticmethod
    def _project_constraints(packages: tuple[ResolutionPackage, ...]) -> str:
        constraints: list[str] = []
        for package in packages:
            if package.source.kind == "registry" and package.version is not None:
                constraints.append(f"{package.name}=={package.version}")
            elif package.source.kind == "url" and package.selected_artifact is not None:
                digest = package.selected_artifact.content_hash.removeprefix("sha256:")
                constraints.append(
                    f"{package.name} @ {package.selected_artifact.locator}#sha256={digest}"
                )
            elif (
                package.source.kind == "git"
                and package.source.locator is not None
                and package.source.commit is not None
            ):
                constraints.append(
                    f"{package.name} @ git+{package.source.locator}@{package.source.commit}"
                )
        return "".join(f"{item}\n" for item in sorted(constraints))

    @staticmethod
    def _direct_harness(
        *,
        packages: tuple[ResolutionPackage, ...],
        requirements: tuple[HarnessResolutionRequirement, ...],
    ) -> tuple[HarnessSelection, ...]:
        by_name = {item.name: item for item in packages}
        selections: list[HarnessSelection] = []
        for name in sorted({item.declaration.name for item in requirements}):
            package = by_name.get(name)
            if package is None or package.version is None:
                raise ValueError(f"resolved harness package is missing: {name}")
            declarations = tuple(
                item.declaration
                for item in requirements
                if item.declaration.name == name
            )
            selections.append(
                HarnessSelection(
                    name=name,
                    version=package.version,
                    source=package.source,
                    selected_artifact=(
                        AvailableArtifact(
                            filename=package.selected_artifact.filename,
                            kind=package.selected_artifact.kind,
                            content_hash=package.selected_artifact.content_hash,
                            locator=package.selected_artifact.locator,
                        )
                        if package.selected_artifact is not None
                        else None
                    ),
                    ceiling_bound=any(
                        harness_requirement_policy(item).ceiling_bound
                        for item in declarations
                    ),
                )
            )
        return tuple(selections)

    def available_cpython_minors(self, *, root: Path) -> tuple[str, ...]:
        process = self._runner.run(
            ProcessSpec(
                argv=(
                    self._uv_executable,
                    "python",
                    "list",
                    "--output-format",
                    "json",
                ),
                cwd=root.as_posix(),
                timeout_seconds=30,
            )
        )
        outcome = self._classify(process, stage="python-list")
        if isinstance(outcome, ToolFailure) or not process.stdout_complete:
            raise InfrastructureError(
                "uv could not list available Python versions",
                detail=process.diagnostic() or None,
            )
        try:
            records = json.loads(read_process_output(self._runner, process).stdout)
            minors = {
                f"{version.major}.{version.minor}"
                for record in records
                if record["implementation"] == "cpython"
                and record["variant"] == "default"
                and not (version := Version(record["version"])).is_prerelease
                and not version.is_devrelease
            }
        except (KeyError, TypeError, InvalidVersion, json.JSONDecodeError) as error:
            raise InfrastructureError(
                "uv returned invalid Python inventory JSON",
                detail=str(error),
            ) from error
        if not minors:
            raise InfrastructureError(
                "uv reported no stable CPython versions",
                detail=process.diagnostic() or None,
            )
        return tuple(sorted(minors, key=Version))

    def inspect_interpreter(
        self,
        *,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> InterpreterOutcome:
        script = (
            "import json,platform,sys,sysconfig;"
            "print(json.dumps({'implementation':sys.implementation.name,"
            "'version':platform.python_version(),"
            "'abi':sysconfig.get_config_var('SOABI') or ''}))"
        )
        process = self._runner.run(
            ProcessSpec(
                argv=(interpreter.as_posix(), "-c", script),
                cwd=cwd.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        outcome = self._classify(process, stage="inspect-interpreter")
        if isinstance(outcome, ToolFailure) or not process.stdout_complete:
            return ToolFailure(
                cause=(
                    outcome.cause
                    if isinstance(outcome, ToolFailure)
                    else "TOOL_FAILURE"
                ),
                stage="inspect-interpreter",
                process=process,
            )
        try:
            document = json.loads(read_process_output(self._runner, process).stdout)
            identity = InterpreterIdentity.model_validate(document)
            Version(identity.version)
        except (ValidationError, InvalidVersion, json.JSONDecodeError):
            return ToolFailure(
                cause="TOOL_FAILURE",
                stage="inspect-interpreter",
                process=process,
            )
        return InterpreterSuccess(process=process, interpreter=identity)

    @staticmethod
    def _selected_requirement(selected: SelectedCandidate) -> str:
        assert selected.artifact.locator is not None
        digest = selected.artifact.content_hash.removeprefix("sha256:")
        return f"{selected.dependency} @ {selected.artifact.locator}#sha256={digest}"

    def create_environment(
        self,
        *,
        environment: Path,
        python_minor: str,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> ToolOutcome:
        result = self._runner.run(
            ProcessSpec(
                argv=(
                    self._uv_executable,
                    "venv",
                    "--python",
                    python_minor,
                    "--no-project",
                    "--no-progress",
                    "--color",
                    "never",
                    environment.as_posix(),
                ),
                cwd=cwd.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        return self._classify(result, stage="create-environment")

    def inspect_environment(
        self,
        *,
        interpreter: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> GraphOutcome:
        script = (
            "import importlib.metadata as m,json;"
            "print(json.dumps([{'name':d.metadata['Name'],'version':d.version,"
            "'requires':d.requires or []} for d in m.distributions()]))"
        )
        process = self._runner.run(
            ProcessSpec(
                argv=(interpreter.as_posix(), "-c", script),
                cwd=cwd.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        outcome = self._classify(process, stage="inspect")
        if isinstance(outcome, ToolFailure):
            return outcome
        if not process.stdout_complete:
            return ToolFailure(cause="TOOL_FAILURE", stage="inspect", process=process)
        try:
            raw_nodes = json.loads(read_process_output(self._runner, process).stdout)
            nodes: list[ResolvedNode] = []
            for raw_node in raw_nodes:
                dependencies: set[str] = set()
                for raw_requirement in raw_node["requires"]:
                    try:
                        dependencies.add(
                            canonicalize_name(Requirement(raw_requirement).name)
                        )
                    except InvalidRequirement:
                        continue
                nodes.append(
                    ResolvedNode(
                        name=canonicalize_name(raw_node["name"]),
                        version=str(Version(raw_node["version"])),
                        dependencies=tuple(sorted(dependencies)),
                    )
                )
        except (KeyError, TypeError, ValueError, InvalidVersion, json.JSONDecodeError):
            return ToolFailure(cause="TOOL_FAILURE", stage="inspect", process=process)
        sorted_nodes: tuple[ResolvedNode, ...] = tuple(
            sorted(nodes, key=lambda node: node.name)
        )
        return GraphSuccess(
            process=process,
            nodes=sorted_nodes,
        )

    def query(
        self,
        *,
        dependency: str,
        source: SourceIdentity,
        cell: Cell,
    ) -> tuple[AvailableCandidate, ...]:
        """Read one frozen view from the source's PEP Simple JSON endpoint."""
        if source.kind != "registry":
            raise InfrastructureError(
                f"candidate query requires a registry source: {dependency}"
            )
        base_url = source.locator or "https://pypi.org/simple/"
        project_url = f"{base_url.rstrip('/')}/{canonicalize_name(dependency)}/"
        headers = {
            "Accept": "application/vnd.pypi.simple.latest+json",
            "User-Agent": "pf/0.1",
        }
        authorization = self._registry_access.authorization(source)
        if authorization is not None:
            headers["Authorization"] = authorization
        request = Request(
            project_url,
            headers=headers,
        )
        query_key = (canonicalize_name(dependency), source)
        with self._candidate_lock:
            payload = self._candidate_responses.get(query_key)
            if payload is None:
                try:
                    with urlopen(request, timeout=30) as response:
                        length = response.headers.get("Content-Length")
                        if length is not None:
                            if not isinstance(length, str) or not length.isdecimal():
                                raise ValueError(
                                    "Content-Length is not a non-negative decimal"
                                )
                            if int(length) > _JSON_SUMMARY_LIMIT:
                                raise InfrastructureError(
                                    "registry candidate response is too large"
                                )
                        payload = response.read(_JSON_SUMMARY_LIMIT + 1)
                except (HTTPError, URLError, TimeoutError, OSError) as error:
                    raise InfrastructureError(
                        f"registry candidate query failed for: {dependency}",
                        detail=self._redactor.redact(str(error)),
                    ) from error
                except (ValueError, TypeError, AttributeError) as error:
                    raise InfrastructureError(
                        "registry returned invalid Simple JSON",
                        detail=self._redactor.redact(str(error)),
                    ) from error
                if len(payload) > _JSON_SUMMARY_LIMIT:
                    raise InfrastructureError("registry candidate response is too large")
                self._candidate_responses[query_key] = payload
        try:
            return self._available_candidates(
                payload=payload,
                project_url=project_url,
                cell=cell,
            )
        except (
            KeyError,
            ValueError,
            TypeError,
            AttributeError,
            InvalidVersion,
            InvalidSpecifier,
            InvalidWheelFilename,
            InvalidSdistFilename,
            ValidationError,
            json.JSONDecodeError,
        ) as error:
            raise InfrastructureError(
                "registry returned invalid Simple JSON",
                detail=self._redactor.redact(str(error)),
            ) from error

    def _available_candidates(
        self,
        *,
        payload: bytes,
        project_url: str,
        cell: Cell,
    ) -> tuple[AvailableCandidate, ...]:
        document = json.loads(payload)
        if not isinstance(document, Mapping):
            raise TypeError("Simple JSON root must be an object")
        files = document.get("files")
        if not isinstance(files, list):
            raise TypeError("Simple JSON files must be an array")
        grouped: dict[Version, list[tuple[AvailableArtifact, bool]]] = {}
        target_python = Version(f"{cell.python_minor}.0")
        for file in files:
            if not isinstance(file, Mapping):
                raise TypeError("Simple JSON file must be an object")
            filename = file.get("filename")
            locator = file.get("url")
            hashes = file.get("hashes")
            if not isinstance(filename, str) or not filename:
                raise TypeError("Simple JSON filename must be a non-empty string")
            if not isinstance(locator, str) or not locator:
                raise TypeError("Simple JSON URL must be a non-empty string")
            if not isinstance(hashes, Mapping):
                raise TypeError("Simple JSON hashes must be an object")
            sha256 = hashes.get("sha256")
            if not isinstance(sha256, str) or re.fullmatch(
                r"[0-9a-fA-F]{64}", sha256
            ) is None:
                raise ValueError("Simple JSON SHA-256 hash is invalid")
            requires_python = file.get("requires-python")
            if requires_python is not None:
                if not isinstance(requires_python, str):
                    raise TypeError("Simple JSON requires-python must be a string")
                if target_python not in SpecifierSet(requires_python):
                    continue
            yanked = file.get("yanked", False)
            if not isinstance(yanked, (bool, str)):
                raise TypeError("Simple JSON yanked must be a boolean or string")
            resolved_locator = urljoin(project_url, locator)
            parsed_locator = urlsplit(resolved_locator)
            if parsed_locator.scheme not in {"http", "https"} or not parsed_locator.hostname:
                raise ValueError("Simple JSON artifact URL is invalid")
            artifact: AvailableArtifact
            try:
                _, version, _, tags = parse_wheel_filename(filename)
                if not self._wheel_compatible(tags, cell):
                    continue
                artifact = AvailableArtifact(
                    filename=filename,
                    kind="wheel",
                    content_hash=f"sha256:{sha256}",
                    locator=public_locator(resolved_locator),
                    python_minors=(cell.python_minor,),
                    targets=(cell.target,),
                )
            except InvalidWheelFilename:
                try:
                    _, version = parse_sdist_filename(filename)
                except InvalidSdistFilename:
                    continue
                artifact = AvailableArtifact(
                    filename=filename,
                    kind="sdist",
                    content_hash=f"sha256:{sha256}",
                    locator=public_locator(resolved_locator),
                )
            grouped.setdefault(version, []).append((artifact, bool(yanked)))

        result = []
        for version in sorted(grouped):
            records = sorted(grouped[version], key=lambda item: item[0].filename)
            active = tuple(artifact for artifact, yanked in records if not yanked)
            result.append(
                AvailableCandidate(
                    version=str(version),
                    yanked=not active,
                    artifacts=active or tuple(artifact for artifact, _ in records),
                )
            )
        return tuple(result)

    @staticmethod
    def _wheel_compatible(tags: frozenset[Tag], cell: Cell) -> bool:
        major, minor = (int(part) for part in cell.python_minor.split("."))
        return any(
            UvAdapter._python_tag_compatible(tag.interpreter, tag.abi, major, minor)
            and UvAdapter._platform_tag_compatible(tag.platform, cell.target)
            for tag in tags
        )

    @staticmethod
    def _python_tag_compatible(
        interpreter: str,
        abi: str,
        major: int,
        minor: int,
    ) -> bool:
        if interpreter == f"py{major}" or interpreter == f"py{major}{minor}":
            return abi == "none"
        if interpreter == f"cp{major}{minor}":
            return abi in {"none", "abi3", f"cp{major}{minor}"}
        if interpreter.startswith(f"cp{major}") and abi == "abi3":
            try:
                required_minor = int(interpreter[len(f"cp{major}") :])
            except ValueError:
                return False
            return required_minor <= minor
        return False

    @staticmethod
    def _platform_tag_compatible(platform_tag: str, target: str) -> bool:
        if platform_tag == "any":
            return True
        architecture = target.split("-", 1)[0]
        if "-linux-gnu" in target:
            return platform_tag == f"linux_{architecture}" or (
                platform_tag.startswith("manylinux")
                and platform_tag.endswith(f"_{architecture}")
            )
        if "-linux-musl" in target:
            return platform_tag.startswith("musllinux") and platform_tag.endswith(
                f"_{architecture}"
            )
        if "-apple-darwin" in target:
            mac_architecture = {
                "aarch64": "arm64",
                "arm64": "arm64",
            }.get(architecture, architecture)
            return platform_tag.startswith("macosx_") and (
                platform_tag.endswith(f"_{mac_architecture}")
                or platform_tag.endswith("_universal2")
            )
        if "-windows-" in target:
            windows_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(
                architecture,
                architecture,
            )
            return platform_tag == f"win_{windows_arch}"
        return False

    def _classify(self, result: ProcessResult, *, stage: str) -> ToolOutcome:
        if result.timed_out:
            return ToolFailure(cause="TIMEOUT", stage=stage, process=result)
        if result.exit_code == 0:
            return ToolSuccess(stage=stage, process=result)
        if result.signal is not None or result.start_error is not None:
            return ToolFailure(cause="TOOL_FAILURE", stage=stage, process=result)
        output = read_process_output(self._runner, result)
        text = f"{output.stdout}\n{output.stderr}".lower()
        if not result.stdout_complete or not result.stderr_complete:
            return ToolFailure(cause="TOOL_FAILURE", stage=stage, process=result)
        source_phrases = (
            "failed to fetch",
            "failed to download",
            "failed to read",
            "request failed",
            "dns error",
            "name or service not known",
            "temporary failure in name resolution",
            "connection refused",
            "connection timed out",
            "401 unauthorized",
            "403 forbidden",
            "hash mismatch",
            "does not match the expected hash",
            "invalid package format",
            "metadata is invalid",
            "network connectivity is disabled",
            "wasn't found in the cache",
        )
        if any(phrase in text for phrase in source_phrases):
            cause = "SOURCE_FAILURE"
        elif "failed to build" in text or "build backend" in text:
            cause = "BUILD_FAILURE"
        else:
            cause = "TOOL_FAILURE"
        return ToolFailure(cause=cause, stage=stage, process=result)

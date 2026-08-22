from __future__ import annotations

import base64
from collections.abc import Mapping
import json
from pathlib import Path
import re
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

from pf.adapters.process import ProcessRunner, SecretRedactor, read_process_output
from pf.errors import InfrastructureError
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
from pf.schemas.project import (
    AvailableArtifact,
    AvailableCandidate,
    Cell,
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
    ) -> None:
        self._runner = runner
        self._registry_access = registry_access or RegistryAccess()
        self._redactor = redactor or SecretRedactor(
            self._registry_access.secret_literals
        )

    def available_cpython_minors(self, *, root: Path) -> tuple[str, ...]:
        process = self._runner.run(
            ProcessSpec(
                argv=("uv", "python", "list", "--output-format", "json"),
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

    def install_editable(
        self,
        *,
        interpreter: Path,
        package: Path,
        extra_surface: tuple[str, ...],
        resolution: Literal["highest", "lowest-direct"],
        timeout_seconds: int | None,
        selection: tuple[SelectedCandidate, ...] | None = None,
    ) -> ToolOutcome:
        extras = ",".join(sorted(set(extra_surface)))
        editable = package.as_posix()
        if extras:
            editable = f"{editable}[{extras}]"
        selected_requirements = tuple(
            self._selected_requirement(item) for item in selection or ()
        )
        result = self._runner.run(
            ProcessSpec(
                argv=(
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    interpreter.as_posix(),
                    "--resolution",
                    resolution,
                    "--editable",
                    editable,
                    *selected_requirements,
                ),
                cwd=package.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        return self._classify(result, stage="install")

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
                    "uv",
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

    def install_requirements(
        self,
        *,
        interpreter: Path,
        requirements: tuple[str, ...],
        constraints: Path,
        cwd: Path,
        timeout_seconds: int | None,
    ) -> ToolOutcome:
        result = self._runner.run(
            ProcessSpec(
                argv=(
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    interpreter.as_posix(),
                    "--constraint",
                    constraints.as_posix(),
                    *requirements,
                ),
                cwd=cwd.as_posix(),
                timeout_seconds=timeout_seconds,
            )
        )
        return self._classify(result, stage="install-harness")

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
        return GraphSuccess(
            process=process,
            nodes=tuple(sorted(nodes, key=lambda node: node.name)),
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
        try:
            with urlopen(request, timeout=30) as response:
                length = response.headers.get("Content-Length")
                if length is not None:
                    if not isinstance(length, str) or not length.isdecimal():
                        raise ValueError("Content-Length is not a non-negative decimal")
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
                _, version = parse_sdist_filename(filename)
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
        if not result.stderr_complete and any(
            phrase in text
            for phrase in (
                "failed to download",
                "dns error",
                "name or service not known",
                "temporary failure in name resolution",
                "connection refused",
                "connection timed out",
                "401 unauthorized",
                "403 forbidden",
                "no solution found",
                "no matching distribution",
                "failed to resolve",
                "unsatisfiable",
                "failed to build",
                "build backend",
            )
        ):
            return ToolFailure(cause="TOOL_FAILURE", stage=stage, process=result)
        source_phrases = (
            "failed to download",
            "dns error",
            "name or service not known",
            "temporary failure in name resolution",
            "connection refused",
            "connection timed out",
            "401 unauthorized",
            "403 forbidden",
        )
        resolution_phrases = (
            "no solution found",
            "no matching distribution",
            "failed to resolve",
            "unsatisfiable",
        )
        if any(phrase in text for phrase in source_phrases):
            cause = "SOURCE_FAILURE"
        elif any(phrase in text for phrase in resolution_phrases):
            cause = (
                "HARNESS_CONFLICT"
                if stage == "install-harness"
                else "RESOLUTION_CONFLICT"
            )
        elif "failed to build" in text or "build backend" in text:
            cause = "BUILD_FAILURE"
        else:
            cause = "TOOL_FAILURE"
        return ToolFailure(cause=cause, stage=stage, process=result)

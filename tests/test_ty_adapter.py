from __future__ import annotations

from pf.cancellation import Cancellation

import json
import os
import sys
from pathlib import Path

import pytest

from pf.adapters.ty import TyAdapter, TyOutputDecoder
from pf.adapters.process import SubprocessRunner
from pf.adapters.uv import UvAdapter
from pf.environment import EnvironmentFactory, HighestResolution, PreparedEnvironment
from pf.project import ProjectLoader
from pf.schemas.evaluation import (
    ProcessResult,
    ProcessSpec,
    ProcessTerminalUnavailable,
    ToolFailure,
    TyCheck,
)
from pf.schemas.static import StaticContentUnavailable
from pf.schemas.project import SourcePlan
from pf.snapshot import SnapshotBuilder
from pf.static import StaticEvaluator, TyCheckCache


class DiagnosticRunner:
    def __init__(self) -> None:
        self.spec: ProcessSpec | None = None

    def run(
        self, spec: ProcessSpec, *, cancellation: Cancellation | None = None
    ) -> ProcessResult:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        self.spec = spec
        output = json.dumps(
            [
                {
                    "check_name": "invalid-argument-type",
                    "description": "Expected str, found int",
                    "severity": "major",
                    "fingerprint": "ignored",
                    "location": {
                        "path": (Path(spec.cwd) / "src" / "demo.py").as_posix(),
                        "positions": {
                            "begin": {"line": 42, "column": 7},
                            "end": {"line": 42, "column": 8},
                        },
                    },
                }
            ]
        )
        return ProcessResult(
            exit_code=1,
            signal=None,
            duration_seconds=0.1,
            stdout=output,
            stderr="",
        )


class ResultRunner:
    def __init__(self, result: ProcessResult | ProcessTerminalUnavailable) -> None:
        self.result = result
        self.spec: ProcessSpec | None = None

    def run(
        self, spec: ProcessSpec, *, cancellation: Cancellation | None = None
    ) -> ProcessResult | ProcessTerminalUnavailable:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        self.spec = spec
        return self.result


def decode_process(runner, *, spec, snapshot_root, environment_root):
    result = runner.run(spec)
    return TyOutputDecoder(runner).decode(
        result,
        diagnostic_root=Path(spec.cwd),
        snapshot_root=snapshot_root,
        environment_root=environment_root,
    )


class TestTyOutputDecoder:
    def test_decoder_handles_unavailable_process_terminal(
        self, tmp_path: Path
    ) -> None:
        result = decode_process(
            ResultRunner(ProcessTerminalUnavailable()),
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(tmp_path / ".venv/bin/python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(tmp_path),
                ),
                cwd=str(tmp_path),
                timeout_seconds=600,
            ),
            snapshot_root=tmp_path,
            environment_root=(tmp_path / ".venv/bin/python").parent.parent,
        )

        assert isinstance(result, ToolFailure)
        assert isinstance(result.process, ProcessTerminalUnavailable)

    def test_decoder_collects_snapshot_diagnostics(
        self,
        tmp_path: Path,
    ) -> None:
        runner = DiagnosticRunner()
        interpreter = tmp_path / ".venv" / "bin" / "python"

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(interpreter),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *("--error", "possibly-unresolved-reference"),
                    str(tmp_path),
                ),
                cwd=str(tmp_path),
                timeout_seconds=600,
            ),
            snapshot_root=tmp_path,
            environment_root=(interpreter).parent.parent,
        )

        assert isinstance(result, TyCheck)
        assert result.status == "SUCCESS"
        assert len(result.diagnostics) == 1
        diagnostic = result.diagnostics[0]
        assert diagnostic.identity == "snapshot|src/demo.py|42|7|invalid-argument-type"
        assert diagnostic.origin == "snapshot"
        assert diagnostic.path == "src/demo.py"
        assert diagnostic.line == 42
        assert diagnostic.column == 7
        assert diagnostic.code == "invalid-argument-type"
        assert diagnostic.severity == "major"
        assert diagnostic.message == "Expected str, found int"
        assert runner.spec is not None

    def test_decoder_preserves_external_diagnostic_multiplicity_on_exit_zero(
        self,
        tmp_path: Path,
    ) -> None:
        package = tmp_path / "source"
        package.mkdir()
        environment = tmp_path / "environment"
        external_path = (
            environment / "lib" / "python3.11" / "site-packages" / "demo.pyi"
        )
        records = [
            {
                "check_name": "invalid-return-type",
                "description": message,
                "severity": severity,
                "fingerprint": fingerprint,
                "location": {
                    "path": external_path.as_posix(),
                    "positions": {
                        "begin": {"line": line, "column": column},
                        "end": {"line": line, "column": column + 1},
                    },
                },
            }
            for line, column, severity, message, fingerprint in (
                (10, 2, "major", "first wording", "first"),
                (99, 20, "minor", "second wording", "second"),
            )
        ]
        output = json.dumps(records)
        runner = ResultRunner(
            ProcessResult(
                exit_code=0,
                signal=None,
                duration_seconds=0.1,
                stdout=output,
                stderr="",
            )
        )

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(environment / "bin" / "python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(package),
                ),
                cwd=str(package),
                timeout_seconds=600,
            ),
            snapshot_root=package,
            environment_root=(environment / "bin" / "python").parent.parent,
        )

        assert isinstance(result, TyCheck)
        assert [item.identity for item in result.diagnostics] == [
            "external|site-packages/demo.pyi|invalid-return-type",
            "external|site-packages/demo.pyi|invalid-return-type",
        ]
        assert all(
            item.line is None and item.column is None for item in result.diagnostics
        )

    def test_decoder_namespaces_environment_paths_as_interpreter_files(
        self,
        tmp_path: Path,
    ) -> None:
        environment = tmp_path / "environment"
        path = environment / "lib" / "python3.11" / "os.pyi"
        document = json.dumps(
            [
                {
                    "check_name": "invalid-type",
                    "description": "message",
                    "severity": "major",
                    "location": {
                        "path": path.as_posix(),
                        "lines": {"begin": 1},
                    },
                }
            ]
        )
        runner = ResultRunner(
            ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout=document,
                stderr="",
            )
        )

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(environment / "bin" / "python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(tmp_path / "source"),
                ),
                cwd=str(tmp_path / "source"),
                timeout_seconds=600,
            ),
            snapshot_root=tmp_path / "source",
            environment_root=(environment / "bin" / "python").parent.parent,
        )

        assert isinstance(result, TyCheck)
        assert result.diagnostics[0].path == "interpreter/lib/python3.11/os.pyi"

    def test_decoder_accepts_gitlab_lines_begin_without_a_column(
        self, tmp_path: Path
    ) -> None:
        package = tmp_path / "source"
        document = json.dumps(
            [
                {
                    "check_name": "unresolved-reference",
                    "description": "name is unresolved",
                    "severity": "major",
                    "location": {
                        "path": "demo.py",
                        "lines": {"begin": 3},
                    },
                }
            ]
        )
        runner = ResultRunner(
            ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout=document,
                stderr="",
            )
        )

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(tmp_path / "environment" / "bin" / "python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(package),
                ),
                cwd=str(package),
                timeout_seconds=600,
            ),
            snapshot_root=package,
            environment_root=(
                tmp_path / "environment" / "bin" / "python"
            ).parent.parent,
        )

        assert isinstance(result, TyCheck)
        assert result.diagnostics[0].identity == (
            "snapshot|demo.py|3|unresolved-reference"
        )
        assert result.diagnostics[0].column is None

    def test_decoder_resolves_relative_diagnostics_from_nested_package_cwd(
        self,
        tmp_path: Path,
    ) -> None:
        package = tmp_path / "packages" / "demo"
        package.mkdir(parents=True)
        document = json.dumps(
            [
                {
                    "check_name": "invalid-type",
                    "description": "message",
                    "severity": "major",
                    "location": {
                        "path": "src/demo.py",
                        "lines": {"begin": 4},
                    },
                }
            ]
        )
        runner = ResultRunner(
            ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout=document,
                stderr="",
            )
        )

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(tmp_path / "environment" / "bin" / "python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(package),
                ),
                cwd=str(package),
                timeout_seconds=600,
            ),
            snapshot_root=tmp_path,
            environment_root=(
                tmp_path / "environment" / "bin" / "python"
            ).parent.parent,
        )

        assert isinstance(result, TyCheck)
        assert result.diagnostics[0].path == "packages/demo/src/demo.py"

    @pytest.mark.process
    def test_real_ty_validates_invalid_project_configuration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "PATH",
            str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        )
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "src" / "demo" / "__init__.py").write_text("VALUE = 1\n")
        (tmp_path / "pyproject.toml").write_text(
            """
[project]
name = "demo"
version = "1"
[build-system]
requires = ["uv_build>=0.8.22,<0.9.0"]
build-backend = "uv_build"
[tool.pf]
pythons = ["3.10"]
test-command = ["python", "-c", "import demo; assert demo.VALUE == 1"]
[tool.ty]
terminal = "invalid"
""".strip()
            + "\n"
        )
        package = ProjectLoader().load(root=tmp_path).target
        runner = SubprocessRunner()
        snapshot = SnapshotBuilder(runner).build(tmp_path)
        prepared = None
        try:
            prepared = EnvironmentFactory(UvAdapter(runner)).prepare(
                package=package,
                cell=package.cells[0],
                snapshot=snapshot,
                resolution=HighestResolution(),
                source_plan=SourcePlan.for_package(package, "SEARCH"),
            )
            assert isinstance(prepared, PreparedEnvironment)
            monkeypatch.delenv("HOME", raising=False)
            monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
            static = StaticEvaluator(TyAdapter(runner), processes=runner)
            with TyCheckCache() as cache:
                captured = static.collect_prepared(prepared, package=package, run_cache=cache)
            assert isinstance(captured, StaticContentUnavailable)
            assert captured.detail == "configuration-context-unavailable"
        finally:
            if isinstance(prepared, PreparedEnvironment):
                prepared.close()
            snapshot.close()

    @pytest.mark.parametrize(
        ("exit_code", "timed_out", "expected"),
        (
            (0, False, "SUCCESS"),
            (2, False, "TOOL_FAILURE"),
            (101, False, "TOOL_FAILURE"),
            (None, True, "TIMEOUT"),
        ),
    )
    def test_decoder_preserves_non_diagnostic_terminal_states(
        self,
        tmp_path: Path,
        exit_code: int | None,
        timed_out: bool,
        expected: str,
    ) -> None:
        class Runner:
            def run(
                self, spec: ProcessSpec, *, cancellation: Cancellation | None = None
            ) -> ProcessResult:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                return ProcessResult(
                    exit_code=exit_code,
                    signal=None if exit_code is not None else 9,
                    duration_seconds=0.1,
                    stdout="[]",
                    stderr="",
                    timed_out=timed_out,
                )

        result = decode_process(
            Runner(),
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(tmp_path / "python"),
                    "--python-version",
                    "3.10",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(tmp_path),
                ),
                cwd=str(tmp_path),
                timeout_seconds=None,
            ),
            snapshot_root=tmp_path,
            environment_root=(tmp_path / "python").parent.parent,
        )

        observed = result.cause if isinstance(result, ToolFailure) else result.status
        assert observed == expected

    @pytest.mark.parametrize(
        ("document", "truncated"),
        (
            ("not JSON", False),
            (json.dumps({"diagnostics": []}), False),
            (
                json.dumps(
                    [
                        {
                            "check_name": " ",
                            "description": "message",
                            "severity": "major",
                            "location": {
                                "path": "demo.py",
                                "lines": {"begin": 1},
                            },
                        }
                    ]
                ),
                False,
            ),
            (
                json.dumps(
                    [
                        {
                            "check_name": "invalid-type",
                            "description": "message",
                            "severity": "major",
                            "location": {"lines": {"begin": 1}},
                        }
                    ]
                ),
                False,
            ),
            (json.dumps(["not an object"]), False),
            (
                json.dumps(
                    [
                        {
                            "check_name": "invalid-type",
                            "description": "message",
                            "severity": "major",
                            "location": "not an object",
                        }
                    ]
                ),
                False,
            ),
            (
                json.dumps(
                    [
                        {
                            "check_name": "invalid-type",
                            "description": "message",
                            "severity": "major",
                            "location": {"path": "demo.py"},
                        }
                    ]
                ),
                False,
            ),
            (
                json.dumps(
                    [
                        {
                            "check_name": "invalid-type",
                            "description": "message",
                            "severity": "major",
                            "location": {
                                "path": "demo.py",
                                "lines": {"begin": 0},
                            },
                        }
                    ]
                ),
                False,
            ),
            ("[]", True),
        ),
    )
    def test_decoder_rejects_incomplete_or_malformed_gitlab_output(
        self,
        tmp_path: Path,
        document: str,
        truncated: bool,
    ) -> None:
        runner = ResultRunner(
            ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout=document,
                stderr="",
                stdout_complete=not truncated,
            )
        )

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(tmp_path / "environment" / "bin" / "python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(tmp_path / "source"),
                ),
                cwd=str(tmp_path / "source"),
                timeout_seconds=600,
            ),
            snapshot_root=tmp_path / "source",
            environment_root=(
                tmp_path / "environment" / "bin" / "python"
            ).parent.parent,
        )

        assert isinstance(result, ToolFailure)
        assert result.cause == "TOOL_FAILURE"

    def test_decoder_namespaces_external_paths_outside_the_environment(
        self,
        tmp_path: Path,
    ) -> None:
        records = [
            {
                "check_name": "invalid-type",
                "description": "message",
                "severity": "major",
                "location": {
                    "path": path,
                    "lines": {"begin": 1},
                },
            }
            for path in (
                "/opt/python/site-packages/vendor/demo.pyi",
                "/opt/python/typeshed/stdlib/demo.pyi",
                "/opt/python/site-packages/typeshed/demo.pyi",
            )
        ]
        document = json.dumps(records)
        runner = ResultRunner(
            ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout=document,
                stderr="",
            )
        )

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(tmp_path / "environment" / "bin" / "python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(tmp_path / "source"),
                ),
                cwd=str(tmp_path / "source"),
                timeout_seconds=600,
            ),
            snapshot_root=tmp_path / "source",
            environment_root=(
                tmp_path / "environment" / "bin" / "python"
            ).parent.parent,
        )

        assert isinstance(result, TyCheck)
        assert [item.path for item in result.diagnostics] == [
            "site-packages/typeshed/demo.pyi",
            "site-packages/vendor/demo.pyi",
            "typeshed/stdlib/demo.pyi",
        ]

    def test_decoder_rejects_an_external_path_without_a_stable_namespace(
        self,
        tmp_path: Path,
    ) -> None:
        document = json.dumps(
            [
                {
                    "check_name": "invalid-type",
                    "description": "message",
                    "severity": "major",
                    "location": {
                        "path": "/opt/vendor/opaque.pyi",
                        "lines": {"begin": 1},
                    },
                }
            ]
        )
        runner = ResultRunner(
            ProcessResult(
                exit_code=1,
                signal=None,
                duration_seconds=0.1,
                stdout=document,
                stderr="",
            )
        )

        result = decode_process(
            runner,
            spec=ProcessSpec(
                argv=(
                    "ty",
                    "check",
                    "--output-format",
                    "gitlab",
                    "--python",
                    str(tmp_path / "environment" / "bin" / "python"),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "linux",
                    "--no-progress",
                    "--color",
                    "never",
                    *(),
                    str(tmp_path / "source"),
                ),
                cwd=str(tmp_path / "source"),
                timeout_seconds=600,
            ),
            snapshot_root=tmp_path / "source",
            environment_root=(
                tmp_path / "environment" / "bin" / "python"
            ).parent.parent,
        )

        assert isinstance(result, ToolFailure)
        assert result.cause == "TOOL_FAILURE"

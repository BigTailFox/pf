from __future__ import annotations

import json
from pathlib import Path

import pytest

from pf.adapters.ty import TyAdapter
from pf.errors import ConfigurationError
from pf.schemas.evaluation import ProcessResult, ProcessSpec, ToolFailure, TyCheck


class DiagnosticRunner:
    def __init__(self) -> None:
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult:
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
    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        self.spec: ProcessSpec | None = None

    def run(self, spec: ProcessSpec) -> ProcessResult:
        self.spec = spec
        return self.result


def test_ty_adapter_collects_snapshot_diagnostics_and_owns_target_argv(
    tmp_path: Path,
) -> None:
    runner = DiagnosticRunner()
    adapter = TyAdapter(runner)
    interpreter = tmp_path / ".venv" / "bin" / "python"

    result = adapter.check(
        interpreter=interpreter,
        package=tmp_path,
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=("--error", "possibly-unresolved-reference"),
        timeout_seconds=600,
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
    assert runner.spec.argv == (
        "ty",
        "check",
        "--output-format",
        "gitlab",
        "--python",
        interpreter.as_posix(),
        "--python-version",
        "3.11",
        "--python-platform",
        "linux",
        "--no-progress",
        "--color",
        "never",
        "--error",
        "possibly-unresolved-reference",
        tmp_path.as_posix(),
    )


def test_ty_adapter_preserves_external_diagnostic_multiplicity_on_exit_zero(
    tmp_path: Path,
) -> None:
    package = tmp_path / "source"
    package.mkdir()
    environment = tmp_path / "environment"
    external_path = environment / "lib" / "python3.11" / "site-packages" / "demo.pyi"
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

    result = TyAdapter(runner).check(
        interpreter=environment / "bin" / "python",
        package=package,
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=600,
    )

    assert isinstance(result, TyCheck)
    assert [item.identity for item in result.diagnostics] == [
        "external|site-packages/demo.pyi|invalid-return-type",
        "external|site-packages/demo.pyi|invalid-return-type",
    ]
    assert all(item.line is None and item.column is None for item in result.diagnostics)


def test_ty_adapter_namespaces_environment_paths_as_interpreter_files(
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

    result = TyAdapter(runner).check(
        interpreter=environment / "bin" / "python",
        package=tmp_path / "source",
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=600,
    )

    assert isinstance(result, TyCheck)
    assert result.diagnostics[0].path == "interpreter/lib/python3.11/os.pyi"


def test_ty_adapter_accepts_gitlab_lines_begin_without_a_column(tmp_path: Path) -> None:
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

    result = TyAdapter(runner).check(
        interpreter=tmp_path / "environment" / "bin" / "python",
        package=package,
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=600,
    )

    assert isinstance(result, TyCheck)
    assert result.diagnostics[0].identity == ("snapshot|demo.py|3|unresolved-reference")
    assert result.diagnostics[0].column is None


def test_ty_adapter_resolves_relative_diagnostics_from_nested_package_cwd(
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

    result = TyAdapter(runner).check(
        interpreter=tmp_path / "environment" / "bin" / "python",
        package=package,
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=600,
        snapshot_root=tmp_path,
    )

    assert isinstance(result, TyCheck)
    assert result.diagnostics[0].path == "packages/demo/src/demo.py"


@pytest.mark.parametrize(
    "args",
    (
        ("--output-format=concise",),
        ("--python", "/usr/bin/python"),
        ("--platform=darwin",),
        ("--config-file", "ty.toml"),
        ('--config=output_format="concise"',),
        ("-c", 'output-format="concise"'),
        ("--config", 'terminal.output_format="concise"'),
        ("-c", 'environment.python-version="3.12"'),
    ),
)
def test_ty_adapter_rejects_user_arguments_owned_by_the_adapter(
    tmp_path: Path,
    args: tuple[str, ...],
) -> None:
    runner = DiagnosticRunner()

    with pytest.raises(ConfigurationError, match="adapter-owned ty option"):
        TyAdapter(runner).check(
            interpreter=tmp_path / "python",
            package=tmp_path,
            python_minor="3.11",
            target="x86_64-unknown-linux-gnu",
            args=args,
            timeout_seconds=600,
        )

    assert runner.spec is None


@pytest.mark.parametrize("key", ("output-format", "output_format"))
def test_ty_adapter_rejects_owned_terminal_configuration(
    tmp_path: Path,
    key: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.ty.terminal]\n{key} = "concise"\n',
        encoding="utf-8",
    )
    runner = DiagnosticRunner()

    with pytest.raises(ConfigurationError, match="adapter-owned ty configuration"):
        TyAdapter(runner).check(
            interpreter=tmp_path / "python",
            package=tmp_path,
            python_minor="3.11",
            target="x86_64-unknown-linux-gnu",
            args=(),
            timeout_seconds=600,
        )

    assert runner.spec is None


def test_ty_adapter_rejects_owned_terminal_configuration_from_snapshot_root(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ty.terminal]\noutput-format = "concise"\n',
        encoding="utf-8",
    )
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    runner = DiagnosticRunner()

    with pytest.raises(ConfigurationError, match="adapter-owned ty configuration"):
        TyAdapter(runner).check(
            interpreter=tmp_path / "environment" / "bin" / "python",
            package=package,
            python_minor="3.11",
            target="x86_64-unknown-linux-gnu",
            args=(),
            timeout_seconds=600,
            snapshot_root=tmp_path,
        )

    assert runner.spec is None


def test_ty_adapter_leaves_unowned_config_and_non_table_terminal_to_ty(
    tmp_path: Path,
) -> None:
    package = tmp_path / "source"
    package.mkdir()
    (package / "pyproject.toml").write_text(
        '[tool.ty]\nterminal = "invalid-but-not-an-owned-table"\n',
        encoding="utf-8",
    )
    runner = DiagnosticRunner()

    result = TyAdapter(runner).check(
        interpreter=tmp_path / "environment" / "bin" / "python",
        package=package,
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=("-c",),
        timeout_seconds=600,
        snapshot_root=tmp_path,
    )

    assert isinstance(result, TyCheck)
    assert runner.spec is not None


@pytest.mark.parametrize(
    ("target", "platform"),
    (
        ("aarch64-apple-darwin", "darwin"),
        ("x86_64-pc-windows-msvc", "win32"),
        ("wasm32-unknown-unknown", "all"),
    ),
)
def test_ty_adapter_maps_supported_and_unknown_targets(
    tmp_path: Path,
    target: str,
    platform: str,
) -> None:
    runner = DiagnosticRunner()

    TyAdapter(runner).check(
        interpreter=tmp_path / "python",
        package=tmp_path,
        python_minor="3.10",
        target=target,
        args=(),
        timeout_seconds=None,
    )

    assert runner.spec is not None
    option = runner.spec.argv.index("--python-platform")
    assert runner.spec.argv[option + 1] == platform


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "expected"),
    (
        (0, False, "SUCCESS"),
        (2, False, "TOOL_FAILURE"),
        (101, False, "TOOL_FAILURE"),
        (None, True, "TIMEOUT"),
    ),
)
def test_ty_adapter_preserves_non_diagnostic_terminal_states(
    tmp_path: Path,
    exit_code: int | None,
    timed_out: bool,
    expected: str,
) -> None:
    class Runner:
        def run(self, spec: ProcessSpec) -> ProcessResult:
            return ProcessResult(
                exit_code=exit_code,
                signal=None if exit_code is not None else 9,
                duration_seconds=0.1,
                stdout="[]",
                stderr="",
                timed_out=timed_out,
            )

    result = TyAdapter(Runner()).check(
        interpreter=tmp_path / "python",
        package=tmp_path,
        python_minor="3.10",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=None,
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
def test_ty_adapter_rejects_incomplete_or_malformed_gitlab_output(
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

    result = TyAdapter(runner).check(
        interpreter=tmp_path / "environment" / "bin" / "python",
        package=tmp_path / "source",
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=600,
    )

    assert isinstance(result, ToolFailure)
    assert result.cause == "TOOL_FAILURE"


def test_ty_adapter_namespaces_external_paths_outside_the_environment(
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

    result = TyAdapter(runner).check(
        interpreter=tmp_path / "environment" / "bin" / "python",
        package=tmp_path / "source",
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=600,
    )

    assert isinstance(result, TyCheck)
    assert [item.path for item in result.diagnostics] == [
        "site-packages/typeshed/demo.pyi",
        "site-packages/vendor/demo.pyi",
        "typeshed/stdlib/demo.pyi",
    ]


def test_ty_adapter_rejects_an_external_path_without_a_stable_namespace(
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

    result = TyAdapter(runner).check(
        interpreter=tmp_path / "environment" / "bin" / "python",
        package=tmp_path / "source",
        python_minor="3.11",
        target="x86_64-unknown-linux-gnu",
        args=(),
        timeout_seconds=600,
    )

    assert isinstance(result, ToolFailure)
    assert result.cause == "TOOL_FAILURE"

from dataclasses import FrozenInstanceError

import packaging.markers
import pytest

from pf.markers import (
    MarkerError,
    PortableMarker,
    evaluate_contextual_marker,
    platform_marker_facts,
)
from pf.schemas.project import Cell


def cell(target="x86_64-unknown-linux-gnu", *, extras=()):
    return Cell(
        package="demo", target=target, python_minor="3.11", extra_surface=extras
    )


class TestPortableMarkers:
    @pytest.mark.parametrize("architecture", ["x86_64", "aarch64"])
    @pytest.mark.parametrize(
        "family,sys_platform,system,os_name",
        [
            ("unknown-linux-gnu", "linux", "Linux", "posix"),
            ("unknown-linux-musl", "linux", "Linux", "posix"),
            ("apple-darwin", "darwin", "Darwin", "posix"),
            ("pc-windows-msvc", "win32", "Windows", "nt"),
        ],
    )
    def test_complete_cell_facts_are_host_independent(
        self, monkeypatch, architecture, family, sys_platform, system, os_name
    ):
        default = packaging.markers.default_environment()
        # Deliberately inconsistent host aliases and patch cannot affect five-field input.
        monkeypatch.setattr(
            packaging.markers,
            "default_environment",
            lambda: {
                **default,
                "sys_platform": "other",
                "platform_machine": "other",
                "platform_system": "Other",
                "os_name": "other",
                "python_version": "2.7",
                "python_full_version": "2.7.18",
            },
        )
        target = f"{architecture}-{family}"
        machine = architecture
        if family == "apple-darwin" and architecture == "aarch64":
            machine = "arm64"
        if family == "pc-windows-msvc":
            machine = {"x86_64": "AMD64", "aarch64": "ARM64"}[architecture]
        facts = platform_marker_facts(target)
        assert (
            facts.sys_platform,
            facts.platform_machine,
            facts.platform_system,
            facts.os_name,
        ) == (sys_platform, machine, system, os_name)
        marker = " and ".join(
            f'{key} == "{value}"'
            for key, value in {
                "python_version": "3.11",
                "sys_platform": sys_platform,
                "platform_machine": machine,
                "platform_system": system,
                "os_name": os_name,
            }.items()
        )
        assert PortableMarker.parse(marker).evaluate(cell(target))
        assert evaluate_contextual_marker(marker, cell(target))
        with pytest.raises(FrozenInstanceError):
            setattr(facts, "os_name", "changed")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, True),
            (
                'os_name == "posix" and (platform_system == "Linux" or platform_system == "Darwin")',
                True,
            ),
            ('platform_system == "linux"', False),
            ('os_name == "nt"', False),
            ('"python_full_version" == platform_system', False),
            ('platform_system == "implementation_name"', False),
        ],
    )
    def test_whole_expression_semantics(self, raw, expected):
        marker = PortableMarker.parse(raw)
        assert marker.evaluate(cell()) is expected
        assert evaluate_contextual_marker(raw, cell()) is expected
        with pytest.raises(FrozenInstanceError):
            setattr(marker, "raw", None)

    @pytest.mark.parametrize(
        "variable",
        [
            "python_full_version",
            "implementation_version",
            "implementation_name",
            "platform_python_implementation",
            "platform_release",
            "platform_version",
            "extra",
            "extras",
            "dependency_groups",
        ],
    )
    def test_qualification_checks_all_variables_without_a_cell(self, variable):
        with pytest.raises(MarkerError) as caught:
            PortableMarker.parse(f'os_name == "posix" or {variable} == "unused"')
        assert caught.value.reason == "unsupported-variable"
        assert caught.value.variable == variable

    def test_first_unsupported_variable_is_sorted(self):
        with pytest.raises(MarkerError) as caught:
            PortableMarker.parse(
                'python_full_version == "3.11.5" and implementation_name == "cpython"'
            )
        assert caught.value.variable == "implementation_name"

    def test_invalid_syntax_is_structured(self):
        with pytest.raises(MarkerError) as caught:
            PortableMarker.parse('made_up == "secret"')
        assert caught.value.reason == "syntax"
        assert "secret" not in str(caught.value)

    @pytest.mark.parametrize(
        "raw", ['platform_system ~= "Windows"', 'os_name ~= "posix"']
    )
    def test_undefined_comparison_is_not_false(self, raw):
        marker = PortableMarker.parse(raw)
        for evaluate in (
            marker.evaluate,
            lambda target: evaluate_contextual_marker(raw, target),
        ):
            with pytest.raises(MarkerError) as caught:
                evaluate(cell())
            assert caught.value.reason == "comparison"

    def test_unknown_target_fails_closed(self):
        for target in ("wasm32-unknown-unknown", "x86_64-other-linux-gnu"):
            with pytest.raises(MarkerError) as caught:
                PortableMarker.parse('os_name == "posix"').evaluate(cell(target))
            assert caught.value.reason == "target"

    def test_contextual_full_expression_retains_host_patch_and_extra(self, monkeypatch):
        default = packaging.markers.default_environment()
        raw = 'os_name == "posix" and python_full_version >= "3.11.5"'
        for patch, expected in [("3.11.0", False), ("3.11.15", True)]:
            monkeypatch.setattr(
                packaging.markers,
                "default_environment",
                lambda: {**default, "os_name": "nt", "python_full_version": patch},
            )
            assert evaluate_contextual_marker(raw, cell()) is expected
            assert PortableMarker.parse('os_name == "posix"').evaluate(cell())
        assert evaluate_contextual_marker('extra == ""', cell(extras=("tests",)))
        assert evaluate_contextual_marker('extra == "tests"', cell(extras=("tests",)))
        assert not evaluate_contextual_marker(
            'extra == "other"', cell(extras=("tests",))
        )
        with pytest.raises(MarkerError) as caught:
            evaluate_contextual_marker('"tests" in dependency_groups', cell())
        assert caught.value.reason == "missing-fact"

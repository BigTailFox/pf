from __future__ import annotations

import pytest
from packaging.version import Version
from pathlib import Path

from pf.project import ProjectLoader

from pf.errors import ConfigurationError, SearchSpaceResolutionError
from pf.search_space import (
    AllSpace,
    SeriesSpace,
    bind,
    bind_policy,
    declaration_anchor,
    defaults,
    evaluate,
    parse,
)


class TestSearchSpace:
    @pytest.mark.parametrize("custom", [False, True])
    def test_active_base_extra_and_markers_choose_each_cells_default(
        self, tmp_path: Path, custom: bool
    ) -> None:
        text = """[project]
name = "demo"
version = "0.1"
dependencies = ["dep<10; python_version < '3.11'", "dep>=2,<10; python_version >= '3.11'", "fixed~=2.0"]
[project.optional-dependencies]
extra = ["dep>3.5.4,>=3.2"]
[tool.pf]
pythons = ["3.10", "3.11"]
platforms = ["x86_64-unknown-linux-gnu"]
"""
        if custom:
            text += """[tool.pf.search-space-defaults]
with-lower-bound = "minors[declaration:]"
without-lower-bound = "all"
"""
        (tmp_path / "pyproject.toml").write_text(text)
        package = ProjectLoader().load(root=tmp_path).target
        assert tuple(p.name for p in package.dependency_search_policies) == ("dep",)
        assert all(not d.managed for d in package.declarations if d.name == "fixed")
        policy = package.search_policy_for("dep")
        for cell in package.cells:
            result = bind_policy(policy, declarations=package.declarations, cell=cell)
            expected = (
                "3.5.4"
                if cell.extra_surface
                else "2"
                if cell.python_minor == "3.11"
                else None
            )
            assert result.declaration == (Version(expected) if expected else None)
            assert result.reason == (
                "default-declaration" if expected else "default-unbounded"
            )
            assert result.space.canonical == (
                ("minors[declaration:]" if expected else "all")
                if custom
                else ("majors[declaration-1:]" if expected else "majors[baseline-2:]")
            )

    @pytest.mark.parametrize(
        ("raw", "canonical"),
        [
            (
                " majors [ declaration - 001 : baseline + 00 ] ",
                "majors[declaration-1:baseline]",
            ),
            ("minors[baseline-0]", "minors[baseline]"),
            ("majors[:]", "all"),
            ("minors[ : ]", "all"),
            ("all", "all"),
            ("majors[:baseline+1]", "majors[:baseline+1]"),
        ],
    )
    def test_parser_canonicalizes_tokens(self, raw: str, canonical: str) -> None:
        assert parse(raw).canonical == canonical

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "ALL",
            "majors[]",
            "majors[0]",
            "majors[baseline,declaration]",
            "majors[baseline::]",
            "majors[baseline*2]",
            "majors[base line]",
            "majors[baseline+1 2]",
            "majors[baseline()]",
            "majors[baseline\n]",
        ],
    )
    def test_parser_rejects_invalid_expressions(self, raw: str) -> None:
        with pytest.raises(ConfigurationError, match="invalid search-space"):
            parse(raw)

    def test_specifier_is_an_explicit_opt_in(self) -> None:
        assert parse(">=1,<=2", allow_specifier=True).canonical == "<=2,>=1"
        with pytest.raises(ConfigurationError):
            parse(">=1")
        with pytest.raises(ConfigurationError):
            parse("numpy>=1", allow_specifier=True)

    @pytest.mark.parametrize(
        ("expression", "selected"),
        [
            ("majors[baseline]", ((0, 7),)),
            ("majors[baseline-1]", ((0, 3),)),
            ("majors[baseline-2:baseline+1]", ((0, 1), (0, 3), (0, 7))),
            ("majors[baseline-99:]", ((0, 1), (0, 3), (0, 7), (0, 9))),
            ("majors[:baseline-99]", ()),
            ("majors[baseline-99]", ()),
            ("majors[baseline+99]", ()),
            ("majors[baseline:baseline-1]", ()),
        ],
    )
    def test_sparse_series_use_positions(
        self, expression: str, selected: tuple
    ) -> None:
        selection = evaluate(
            bind(parse(expression), declaration=None, dependency="dep", cell="cell"),
            baseline=Version("7.4.2"),
            release_versions=("1", "3.1", "7.4", "9", "1!1"),
            dependency="dep",
            cell="cell",
            source="registry",
        )
        assert selection.selected_keys == selected
        assert selection.series_keys == ((0, 1), (0, 3), (0, 7), (0, 9))
        assert selection.anchors == (("baseline", "7.4.2"),)

    def test_default_branch_and_unpublished_endpoint(self) -> None:
        request = defaults("minors[declaration-1:]", "majors[baseline-2:]")
        lower = declaration_anchor((">=2.4,>2.5.3", ">=2.5.3,<4", "!=3"))
        bound = bind(request, declaration=lower, dependency="dep", cell="cell")
        selection = evaluate(
            bound,
            baseline=Version("2.6"),
            release_versions=("2.4", "2.5.4", "2.6", "3.0"),
            dependency="dep",
            cell="cell",
            source="registry",
        )
        assert selection.reason == "default-declaration"
        assert selection.selected_keys == ((0, 2, 4), (0, 2, 5), (0, 2, 6))
        assert selection.contains(Version("2.5.4"))
        assert not selection.contains(Version("3"))
        assert (
            bind(request, declaration=None, dependency="dep", cell="cell").reason
            == "default-unbounded"
        )
        assert declaration_anchor(("<4", "!=2")) is None

    def test_sparse_minors_stay_in_the_anchor_epoch_and_major(self) -> None:
        selected = evaluate(
            bind(
                parse("minors[baseline-1:baseline+1]"),
                declaration=None,
                dependency="dep",
                cell="cell",
            ),
            baseline=Version("1!2.7"),
            release_versions=("2.3", "1!2.1", "1!2.3", "1!2.7", "1!3.1"),
            dependency="dep",
            cell="cell",
            source="registry",
        )
        assert selected.series_keys == ((1, 2, 1), (1, 2, 3), (1, 2, 7))
        assert selected.selected_keys == ((1, 2, 3), (1, 2, 7))

    def test_declaration_prerequisite_is_distinct_from_registry_failure(self) -> None:
        with pytest.raises(ConfigurationError, match="lower bound"):
            bind(
                parse("minors[declaration:]"),
                declaration=None,
                dependency="dep",
                cell="cell",
            )
        with pytest.raises(ConfigurationError, match="without-lower-bound"):
            defaults("all", "majors[declaration]")

    @pytest.mark.parametrize(
        ("expression", "lower", "baseline", "releases", "reason"),
        [
            (
                "minors[declaration:]",
                "2.5",
                "2.6",
                ("2.4", "2.6"),
                "missing-anchor-series",
            ),
            ("majors[baseline]", None, "2", (), "missing-anchor-series"),
            (
                "majors[declaration:baseline]",
                "1",
                "1!2",
                ("1", "1!2"),
                "anchor-scope-mismatch",
            ),
            (
                "minors[declaration:baseline]",
                "1.2",
                "2.1",
                ("1.2", "2.1"),
                "anchor-scope-mismatch",
            ),
        ],
    )
    def test_resolution_errors_keep_source_facts(
        self, expression, lower, baseline, releases, reason
    ) -> None:
        with pytest.raises(SearchSpaceResolutionError) as raised:
            evaluate(
                bind(
                    parse(expression),
                    declaration=Version(lower) if lower else None,
                    dependency="dep",
                    cell="cell",
                ),
                baseline=Version(baseline),
                release_versions=releases,
                dependency="dep",
                cell="cell",
                source="mirror",
            )
        assert raised.value.reason == reason
        assert raised.value.source == "mirror"
        assert raised.value.dependency == "dep"
        assert raised.value.exit_code == 2

    def test_anchor_free_spaces_need_no_series(self) -> None:
        assert isinstance(parse("minors[:]"), AllSpace)
        assert isinstance(parse("minors[baseline]"), SeriesSpace)
        selected = evaluate(
            bind(
                parse("<=2", allow_specifier=True),
                declaration=None,
                dependency="dep",
                cell="cell",
            ),
            baseline=Version("3"),
            release_versions=(),
            dependency="dep",
            cell="cell",
            source="registry",
        )
        assert selected.contains(Version("2"))
        assert not selected.contains(Version("2.1"))
        assert selected.anchors == ()
        assert selected.series_keys == ()

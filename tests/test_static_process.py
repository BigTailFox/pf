from __future__ import annotations

import os
from pathlib import Path

import pytest

from pf.schemas.evaluation import EnvironmentVariable
from pf.schemas.static import StaticContentManifest, StaticContentUnavailable
from pf.static_process import StaticProcessEnvironment, bind_static_process_environment
from pf.static_subject import StaticContentCollector


class TestStaticProcessEnvironment:
    def test_path_values_relocate_while_preserving_order_and_exact_execution_values(self, tmp_path: Path) -> None:
        contexts = []
        for label in ("first", "second-longer"):
            root = tmp_path / label
            (root / "home").mkdir(parents=True)
            (root / "lib").mkdir()
            (root / "extra").mkdir()
            roots = {"process-inputs": root}
            content = StaticContentCollector().collect(roots)
            assert isinstance(content, StaticContentManifest)
            variables = (EnvironmentVariable(name="HOME", value=str(root / "home"), sensitive=False),
                         EnvironmentVariable(name="PYTHONPATH", value=os.pathsep.join(map(str, (root / "lib", root / "extra"))), sensitive=False),
                         EnvironmentVariable(name="TOKEN", value="private-value"))
            bound = bind_static_process_environment(variables, roots=roots, content=content,
                                                   path_variables=("HOME",), path_list_variables=("PYTHONPATH",),
                                                   filesystem_case="sensitive", environment_case="sensitive")
            assert isinstance(bound, StaticProcessEnvironment)
            assert bound.variables == variables
            assert [path.path for path in bound.context.environment[1].logical_paths] == ["lib", "extra"]
            assert "private-value" not in bound.context.model_dump_json()
            assert str(root) not in bound.context.model_dump_json()
            contexts.append(bound.context)
        assert contexts[0] == contexts[1]

    @pytest.mark.parametrize("change", ["literal", "path-order", "output-treatment"])
    def test_actual_context_changes_are_bound(self, tmp_path: Path, change: str) -> None:
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
        content = StaticContentCollector().collect({"inputs": tmp_path})
        assert isinstance(content, StaticContentManifest)
        original = (EnvironmentVariable(name="LANG", value="C", sensitive=False),
                    EnvironmentVariable(name="PYTHONPATH", value=os.pathsep.join(map(str, (tmp_path / "a", tmp_path / "b"))), sensitive=False))
        changed = list(original)
        if change == "literal":
            changed[0] = EnvironmentVariable(name="LANG", value="C.UTF-8", sensitive=False)
        elif change == "path-order":
            changed[1] = EnvironmentVariable(name="PYTHONPATH", value=os.pathsep.join(map(str, (tmp_path / "b", tmp_path / "a"))), sensitive=False)
        else:
            changed[0] = EnvironmentVariable(name="LANG", value="C", sensitive=True)
        contexts = []
        for variables in (original, tuple(changed)):
            bound = bind_static_process_environment(variables, roots={"inputs": tmp_path}, content=content,
                                                   path_variables=(), path_list_variables=("PYTHONPATH",),
                                                   filesystem_case="sensitive", environment_case="sensitive")
            assert isinstance(bound, StaticProcessEnvironment)
            contexts.append(bound.context)
        assert contexts[0] != contexts[1]

    @pytest.mark.parametrize("value", ["relative", "", "/unregistered-static-root", "a\0b"])
    def test_unclosed_path_values_do_not_form_a_context(self, tmp_path: Path, value: str) -> None:
        content = StaticContentCollector().collect({"inputs": tmp_path})
        assert isinstance(content, StaticContentManifest)
        bound = bind_static_process_environment((EnvironmentVariable(name="HOME", value=value),),
                                               roots={"inputs": tmp_path}, content=content,
                                               path_variables=("HOME",), path_list_variables=(),
                                               filesystem_case="sensitive", environment_case="sensitive")
        assert isinstance(bound, StaticContentUnavailable)

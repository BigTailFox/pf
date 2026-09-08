"""Bind the exact explicit process environment to closed logical content."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Literal

from pf.schemas.base import canonical_identity_json
from pf.schemas.evaluation import EnvironmentVariable
from pf.schemas.static import (
    StaticContentManifest, StaticContentPath, StaticContentUnavailable,
    StaticEnvironmentValue, StaticProcessContext,
)


@dataclass(frozen=True)
class StaticProcessEnvironment:
    variables: tuple[EnvironmentVariable, ...]
    context: StaticProcessContext


def bind_static_process_environment(
    variables: tuple[EnvironmentVariable, ...], *, roots: Mapping[str, Path],
    content: StaticContentManifest,
    path_variables: tuple[str, ...], path_list_variables: tuple[str, ...],
    filesystem_case: Literal["sensitive", "insensitive"],
    environment_case: Literal["sensitive", "insensitive"],
) -> StaticProcessEnvironment | StaticContentUnavailable:
    """Project explicit path semantics; never infer paths from arbitrary values.

    The request builder resolves relative/expanded paths before this boundary.
    Returned variables are precisely the values to pass to ProcessSpec. Their
    report projection contains only digests and ordered logical path references.
    """
    try:
        names = {item.name for item in variables}
        if set(path_variables) & set(path_list_variables) or not set((*path_variables, *path_list_variables)) <= names:
            raise ValueError("ambiguous or missing environment path declaration")
        if any(not path.is_absolute() for path in roots.values()):
            raise ValueError("environment roots must be absolute")
        ordered = tuple(sorted(variables, key=lambda item: item.name))
        values = []
        for variable in ordered:
            if "\0" in variable.value:
                raise ValueError("environment value contains a null byte")
            paths: list[StaticContentPath] = []
            payload: dict[str, object]
            separator = os.pathsep if variable.name in path_list_variables else None
            if variable.name in (*path_variables, *path_list_variables):
                for raw in variable.value.split(separator) if separator else (variable.value,):
                    path = Path(raw)
                    if not raw or not path.is_absolute() or path != Path(os.path.normpath(raw)):
                        raise ValueError("environment paths must be resolved before binding")
                    matches = [(len(root.parts), name, path.relative_to(root).as_posix())
                               for name, root in roots.items() if path.is_relative_to(root)]
                    if not matches:
                        raise ValueError("environment path is outside registered roots")
                    _, name, relative = max(matches)
                    location = StaticContentPath(root=name, path=relative)
                    content.entry_at(location)
                    paths.append(location)
                payload = {"kind": "paths", "separator": separator,
                           "paths": [path.model_dump(mode="json") for path in paths]}
            else:
                payload = {"kind": "literal", "value": variable.value}
            # Output treatment is explicit too: making a value public cannot
            # accidentally alias an observation with different redaction.
            payload["sensitive"] = variable.sensitive
            digest = hashlib.sha256(b"pf:static-environment-value:v1\0" + canonical_identity_json(payload)).hexdigest()
            values.append(StaticEnvironmentValue(name=variable.name, value_digest=digest, logical_paths=tuple(paths)))
        context = StaticProcessContext(environment=tuple(values), filesystem_case=filesystem_case, environment_case=environment_case)
        return StaticProcessEnvironment(ordered, context)
    except (KeyError, ValueError):
        return StaticContentUnavailable(detail="invalid-layout")

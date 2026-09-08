"""Shared admission for PF-owned ty options."""

from __future__ import annotations

import tomli

from pf.errors import ConfigurationError


_OWNED_OPTIONS = frozenset(
    {
        "--color",
        "--no-progress",
        "--output-format",
        "--platform",
        "--progress",
        "--python",
        "--python-platform",
        "--python-version",
        "--target-version",
        "--venv",
        "--project",
        "--fix",
        "--add-ignore",
        "--watch",
        "-W",
    }
)
_OWNED_CONFIGURATION_KEYS = frozenset(
    {
        "color",
        "no-progress",
        "output-format",
        "platform",
        "progress",
        "python",
        "python-platform",
        "python-version",
        "target-version",
        "venv",
    }
)



def validate_ty_args(args: tuple[str, ...]) -> None:
    for index, argument in enumerate(args):
        option = argument.partition("=")[0]
        if option in _OWNED_OPTIONS:
            raise ConfigurationError(
                f"adapter-owned ty option is not allowed: {option}"
            )
        if option == "--config-file":
            raise ConfigurationError(
                "adapter-owned ty option may be changed by --config-file"
            )
        if option not in {"-c", "--config"}:
            continue
        if "=" in argument:
            override = argument.partition("=")[2]
        elif index + 1 < len(args):
            override = args[index + 1]
        else:
            continue
        key = override.partition("=")[0].strip().replace("_", "-")
        leaf_key = key.rpartition(".")[2]
        if leaf_key in _OWNED_CONFIGURATION_KEYS:
            raise ConfigurationError(
                f"adapter-owned ty option is not allowed in config override: {key}"
            )
        try:
            settings = tomli.loads(override)
        except tomli.TOMLDecodeError:
            # Invalid TOML stays an ordinary tool/configuration failure.
            continue
        pending = [("", settings)]
        while pending:
            prefix, table = pending.pop()
            for name, value in table.items():
                path = f"{prefix}.{name}" if prefix else name
                if name.replace("_", "-") in _OWNED_CONFIGURATION_KEYS:
                    raise ConfigurationError(
                        f"adapter-owned ty option is not allowed in config override: {path}"
                    )
                if isinstance(value, dict):
                    pending.append((path, value))

# PF — Package Floor

English | [简体中文](README.zh.md)

> Find verified lower bounds for a Python package's direct dependencies.

## What it does

PF discovers candidate versions in isolated environments, captures a `ty` static baseline from the highest versions your declarations allow, then runs the project's full test command. It returns an explainable, reproducible floor for each managed direct dependency.

The search unit is one installable package and one compatibility cell: exact uv target triple, CPython minor, and extra surface. On a frozen candidate snapshot, PF returns a coordinate-minimal vector that passed full tests. It does not claim a global minimum over the Cartesian product of dependencies, and it does not prove that unprobed versions or other combinations work. The product contract is [D001](docs/designs/D001-pf.md).

## Installation

```bash
uv tool install package-floor
```

`pip install package-floor` also works. The CLI name is `pf`. From a clone, `uv run pf` uses the local tree.

## Quick Start

The target project needs static `project.dependencies` (and optional-dependencies, if used), a `test` dependency group (may be empty), and a `[tool.pf]` test command:

```toml
[tool.pf]
test-command = ["pytest"]
```

Then:

```bash
pf smoke
pf search
pf apply
```

`smoke` checks a fresh install at the newest allowed versions. `search` writes `package-floor.json`. `apply` updates the project's requirement floors from that report when authorization succeeds.

## Commands

| Command | What it does |
| --- | --- |
| `pf smoke` | Fresh-install at newest allowed versions, capture a `ty` baseline, run the full tests. Does not search or write a report. |
| `pf check` | Verify the lower bounds the project already declares. Does not search or write a report. |
| `pf search` | Find verified floors and write `package-floor.json`. Never edits project metadata. |
| `pf explain` | Read the report and show floors, coverage, and apply blockers. |
| `pf apply` | Edit project metadata from an authorized report. `--force` only waives source-layer drift. |
| `pf minimize` | Run `search`, then the default `apply`. |
| `pf diagnose FAILURE_ID` | Explain one recorded rejection or indeterminate result. Offline; does not replay. |
| `pf merge REPORT ... --output PATH` | Combine compatible reports produced on different hosts. |

Typical workflow: `pf smoke` → `pf search` → `pf explain` → `pf apply`. Use `pf minimize` to search and apply in one step.

## Requirements

- Omit `--package` to select the installable workspace root. An explicit value is a canonical distribution name of one workspace member, not a path.
- Each process only runs the target that matches the current host. Merge other hosts with `pf merge`. When this host succeeds and the only gaps are other hosts, `pf search` exits 0 with an incomplete report so CI can collect artifacts.
- `search` writes `package-floor.json`. `apply` does not re-resolve dependencies or rerun `ty` or tests.

## Configuration

Persistent settings merge two layers: workspace-root `[tool.pf]`, then the selected member's own `[tool.pf]`. CLI flags override that run only.

`max-cells`, `ty-jobs`, and `test-jobs` limit cell, `ty`, and verifier concurrency. Full fields, defaults, and exit codes are in [D001](docs/designs/D001-pf.md).

## Pinned tools

Released PF pins uv `0.12.5` and ty `0.0.74`. The resolver protocol accepts only that uv version; other versions fail closed. Upgrading either tool requires re-qualification before the pin changes.

## Documentation

- [D001 — product and command contract](docs/designs/D001-pf.md): floors, commands, configuration, reports, and exit codes
- [Engineering docs index](docs/README.md): contract ownership and layout

## License

Apache License 2.0. See [LICENSE](LICENSE).

# PF — Package Floor

English | [简体中文](README.zh.md)

> Find verified lower bounds for a Python package's direct dependencies.

## What it does

PF discovers candidate versions in isolated environments, optionally captures a `ty` static baseline from the highest versions your declarations allow, then runs the project's full test command. Compatibility conclusions come only from that dynamic evidence; `ty` may choose a later probe but cannot reject a candidate. It records an explainable, verified exact dependency vector.

The search unit is one installable package and one compatibility cell: exact uv target triple, CPython minor, and extra surface. On a frozen candidate snapshot, PF returns a coordinate-minimal vector that passed full tests. It does not claim a global minimum over the Cartesian product of dependencies, and it does not prove that unprobed versions or other combinations work. The product contract is [D001](docs/designs/D001-pf.md).

For admitted resolution and installation requests, an unattributed normal nonzero exit rejects that attempt, including failures inside a build backend. It does not prove a dependency conflict or a repeatable failure. A backend can report network or permission problems this way, so false rejections may raise the reported floor, miss feasible vectors, or leave no result. Timeouts, abnormal terminals and directly observed external or consistency failures remain indeterminate. Final results still require actual resolution, installation, graph checks and a full verifier PASS; PF does not add retries or promise reproducibility from one observation.

## Installation

```bash
uv tool install package-floor
```

`pip install package-floor` also works. The CLI name is `pf`. From a clone, `uv run pf` uses the local tree.

## Quick Start

The target project needs static `project.dependencies` (and optional-dependencies, if used). Test dependency groups are optional: omitting `test-group` selects `dev`, then `test`, from the workspace root or selected member. If neither exists, the group is empty. An explicit name selects only that group; a missing name also means an empty group. The default test command is `pytest`; PF does not install it automatically. For example, provide the test tools with:

```toml
[dependency-groups]
test = ["pytest"]
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
| `pf smoke` | Fresh-install at newest allowed versions, try to capture a `ty` baseline, run the full tests. A missing `ty` baseline still enters the verifier. Does not search or write a report. |
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

Settings merge from the workspace root's `[tool.pf]` into the selected member's own `[tool.pf]`;
explicit CLI flags override that run. For example:

```toml
[tool.pf]
test-command = ["pytest"]
search-resolution = "patch"
max-cells = 4

[tool.pf.search-space-defaults]
with-lower-bound = "majors[declaration-1:]"
without-lower-bound = "majors[baseline-2:]"
```

This is a configuration example, not a complete defaults table. See
[D001 configuration](docs/designs/D001-pf.md#7-配置) for groups, Cells, concurrency, timeouts and layer merging;
see [D037 candidate and search policy](docs/designs/D037-pf-candidate-search-policy.md) for spaces,
conditional defaults, per-dependency overrides and exact baseline artifacts.
`search-resolution` controls sampling within the chosen space; a verified floor remains an exact version.

A self-reference such as `requests[socks]` in the test group makes `socks` required in every Cell.
Extra exploration is added to that required surface. A Cell without active external test dependencies
installs the project plan directly and still runs the configured verifier. Changing the test command or
harness changes the validation contract and can change the resulting floor; see
[D001 validation](docs/designs/D001-pf.md#4-候选与验证边界).

## Pinned tools

Released PF pins uv `0.12.5` and ty `0.0.74`. The resolver protocol accepts only that uv version; other versions fail closed. Upgrading either tool requires re-qualification before the pin changes.

## Documentation

- [D001 — product and command contract](docs/designs/D001-pf.md): floors, commands, configuration, reports, and exit codes
- [Engineering docs index](docs/README.md): contract ownership and layout

## License

Apache License 2.0. See [LICENSE](LICENSE).

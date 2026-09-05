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

The target project needs static `project.dependencies` (and optional-dependencies, if used) and a dependency group named `test`. Omit `test-group` to use that name; the group itself may be empty. The omitted test command is `pytest`. For example, provide the test tools with:

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

Persistent settings merge two layers: workspace-root `[tool.pf]`, then the selected member's own `[tool.pf]`. CLI flags override that run only. Unknown keys fail. The values below are the omitted defaults except `pythons` and `platforms`, which are inferred from the project and host. Omit `test-group` to use the dependency group named `test`.

```toml
[tool.pf]
test-command = ["pytest"]          # default argv; explicit value replaces it; must not start with "uv run"
# pythons = ["3.10", "3.11", "3.12"]  # CPython minors; omit to infer from requires-python
# platforms = ["x86_64-unknown-linux-gnu"]  # uv target triples; omit to use the host
extra-policy = "each"              # none | each | all
extra-surfaces = []                # extra extra-combinations, e.g. [["docs", "check"]]
# search-space = "all"             # explicit override; omitted selects conditional defaults below
search-resolution = "minor"        # major | minor | patch
search-prereleases = false
resolve-artifact = "any"         # wheel | sdist | any
# managed-deps = ["rich"]          # mutually exclusive with unmanaged-deps
# unmanaged-deps = ["build"]       # omit both to manage every searchable direct dependency
test-group = "test"                # omit to use the group named "test"; that group may be empty
test-cwd = "package"               # package | root
ty-args = []
max-cells = "auto"                 # auto or a positive integer; cell concurrency
ty-jobs = "auto"                   # ty process concurrency
test-jobs = "auto"                 # verifier concurrency
resolve-timeout = "10m"
ty-timeout = "10m"
test-timeout = "30m"               # each timeout may be "none"

# [[tool.pf.dep]]
# name = "rich"                    # canonical distribution name
# search-space = "majors[baseline]" # or minors[...] / a PEP 440 specifier
# search-resolution = "minor"
# search-prereleases = false

[tool.pf.search-space-defaults]
with-lower-bound = "majors[declaration-1:]"
without-lower-bound = "majors[baseline-2:]"
```

All spaces accept `major`, `minor`, or `patch` resolution. `baseline` anchors the verified highest version; `declaration` anchors the strongest active direct lower bound in each Cell. Offsets move through existing registry series; slices are half-open. For example, `majors[baseline-2:]` includes the baseline major and the two preceding existing majors, subject to the baseline cap and candidate filters. A filtered-out series still occupies its position.

A narrow space may exclude the verified baseline version. PF freezes that baseline's exact artifact separately so multi-dependency probes remain reproducible, but it never adds the version to the search candidates, windows, boundaries, or floors.

Explicit space wins over conditional defaults; per-dependency space wins over global space. A defaults table requires both entries and replaces the inherited table as a whole; `without-lower-bound` cannot use `declaration`. Per-dependency `[[tool.pf.dep]]` rows also replace as a whole table; omit `dep` on a member to inherit the root table, or set `dep = []` to clear it. A missing declaration prerequisite exits 3 before search; an unresolvable registry anchor/scope exits 2 and leaves the report untouched. Full rules are in [D001](docs/designs/D001-pf.md).

A self-reference in the test group selects required project extras. For example, `requests[socks]` includes `socks` in every Cell; extra-policy explores only the remaining extras with nonempty dependency lists, and `none` retains required extras. Empty groups are skipped automatically; explicit `extra-surfaces` and required extras can still include them. Floors are relative to the configured validation contract, so changing the test command or harness can change the result.

## Pinned tools

Released PF pins uv `0.12.5` and ty `0.0.74`. The resolver protocol accepts only that uv version; other versions fail closed. Upgrading either tool requires re-qualification before the pin changes.

## Documentation

- [D001 — product and command contract](docs/designs/D001-pf.md): floors, commands, configuration, reports, and exit codes
- [Engineering docs index](docs/README.md): contract ownership and layout

## License

Apache License 2.0. See [LICENSE](LICENSE).

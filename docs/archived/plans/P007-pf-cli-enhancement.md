# P007 — CLI 交互与展示增强实施记录

- **状态：** 已归档（已完成）
- **开始日期：** 2026-08-21
- **完成日期：** 2026-08-21
- **性质：** 非规范性实施记录
- **设计来源：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **依赖：** [P005](P005-pf-process-output.md) 的 Process Log / Output Cache；[P006](P006-pf-verification-run.md) 的 FailureRecord、Journal、Role→impact

本文记录 D006 的落地过程与可核验证据，不复制契约。check 的 Diagnose 入口与 Role impact 必须以 P006 的结构化事实为数据源。

## 1. 范围

本轮实现：

- 顶层 help 按 Verify / Find and apply / Inspect and combine 分组；`package` 仅位置参数；
- 可预期调用错误在 CLI 边界转为 `Error:` / `Usage:` / `Try ... --help`，退出码 3，无 traceback；
- 命令摘要按 §8.4 区分 complete/incomplete/stopped；`render_minimize` 为 minimize 唯一最终摘要；
- cell 完成块消费 D005 title/impact 与 Diagnose 入口；末 3 行来自 D007；
- `explain` 默认展示声明、coverage、Apply 授权与 blocker，不转储 digest。

## 2. 落地顺序

| 切片 | 可观察行为 | 主要测试 | 状态 |
| --- | --- | --- | --- |
| 001 | Cell/failure 放置：无 Schema status，有 Diagnose | `tests/test_terminal.py` | 已完成 |
| 002 | Help 与调用错误 | `tests/test_cli.py` | 已完成 |
| 003 | 命令摘要、通道、`render_minimize` | `tests/test_cli.py`、`tests/test_terminal.py` | 已完成 |
| 004 | explain 层级、FailurePresentation、诊断折叠 | `tests/test_terminal.py` | 已完成 |
| 005 | 相关门禁与入口 | pytest / ty / e2e | 已完成 |

## 3. 过程与证据

### 关键产物

- `src/pf/cli.py`：Cyclopts `Group` 固定顺序；`package` positional-only；`help_epilogue` Typical workflow；`InvocationError`；`bind_command`；`main()` 将 `CycloptsError` 映射为退出码 3
- `src/pf/terminal.py`：§8 摘要动词；smoke/check/search 通道；`render_minimize`；explain 层级；诊断 `×N` 与 10 条上限；Role impact 区分 smoke/search
- `src/pf/errors.py`：`InvocationError`

### 可核验测试

```text
uv run --no-sync pytest --no-testmon -q --tb=line
  tests/test_cli.py
  tests/test_terminal.py
  tests/test_end_to_end.py
```

代表断言：

- `test_module_help_lists_every_v1_command`：Verify → Find and apply → Inspect；Typical workflow
- `test_package_is_positional_only_on_command_help`：无 `--package`
- `test_merge_without_reports_is_a_usage_error`：退出 3，`Error:` / `Usage:` / `Try`，无 traceback
- `test_unknown_option_is_an_invocation_error` / `test_illegal_jobs_is_an_invocation_error` / `test_illegal_duration_restates_accepted_format` / `test_unknown_package_is_an_invocation_error`
- `test_console_script_help_matches_module_help`：`pf` 与 `python -m pf` 顶层 help 一致
- `test_smoke_test_failure_prints_dynamic_summary_and_log_link`：smoke impact 为 “highest-version resolution did not pass”，不是 search baseline 句子
- `test_explain_folds_repeated_diagnostics_and_caps_unique_lines`：`×3`、10 条上限、省略计数
- `test_explain_keeps_required_fields_readable_at_common_widths`：56 / 80 / 120 列
- `test_installed_module_cli_completes_smoke_check_search_explain_apply`：`Status: complete`、`Apply: authorized by this report`
- `test_successful_cell_does_not_print_probe_diagnose`：成功 cell 不打印 probe Diagnose
- `test_explain_does_not_silently_use_declaration_digest`：关联缺失时 `ConfigurationError`，不把 `declaration_id` 当依赖名
- `test_command_help_usage_names_package_instead_of_args`：`Usage: pf … [OPTIONS] [PACKAGE]`，无 `[ARGS]`
- `test_merge_help_usage_names_reports_and_hides_report_option`：`Usage: pf merge REPORT [REPORT ...] --output PATH`，无 `--report`

### 决策

- 调用错误不用结果图标；未知 package 带最多 10 个候选和 `... and N more`。
- `minimize` handler 只调用 `render_minimize`；search 不完整时 `edits=None`。
- live 冻结路径通过 `TerminalPresenter.bind_command` 选择 smoke vs search 的 baseline impact。
- explain 按展示字段折叠诊断；每个 blocker group 最多 10 条唯一行。
- 成功 cell 即使带有 probe `SearchFailureEvent` / `failure_records`，完成块也只保留图标、标题和耗时；Diagnose 留给非成功完成块与 `pf diagnose`。

### 审计补完：D006 §10.3 关联缺失不得静默退回裸 digest

D006 §10.3 原文要点：

> Presenter 必须用 `declaration_id` 关联 `requirement_declarations`，默认展示 `raw` / `name` 和 `projected_requirements`：
>
> - 不能把 declaration digest 当作依赖名称；
> - …
> - 关联缺失是 Schema/内部一致性错误，不得静默退回裸 digest。

生产 `declaration_id` 是声明 identity 的 SHA-256 digest（`ProjectLoader`）。改前 `explain` 在 `requirement_declarations` 找不到对应项时把 `projection.declaration_id` 当作 Requirements 行的名称。结案核验时该回退仍在，且 `test_explain_renders_incomplete_reasons_and_projection_requirements` 曾用空声明列表走到这条路径。

**改后：** 任一 projection 缺少声明则 `ConfigurationError`（命令错误、退出 3），Requirements 行只使用 `raw` / `name`。

### 审计补完：D006 §5.1 Usage 不得退化为 `[ARGS]`

D006 §5.1 原文要点：

> Usage 必须显示具体参数名，例如 `pf search [OPTIONS] [PACKAGE]`，不能退化为不透明的 `[ARGS]`。

§5.3 同时规定 `Usage: pf merge REPORT [REPORT ...] --output PATH`，且 `package` / `REPORT` 为位置参数表面。

**改前：** Cyclopts 4.23 对可选位置参数把 Usage 写成 `[ARGS]` / `[ARGS...]`；`merge` 的第一个 `report` 不是 positional-only，帮助里出现 `REPORT --report`。调用错误路径已有正确 Usage，`--help` 没有。

**改后：** 各子命令 `App.usage` 使用 D006 Usage 行；`merge` 的第一个 REPORT 改为 positional-only。`command_usage` 同时供给 `--help`、Cyclopts 调用错误和 Presenter 调用错误。

## 4. 完成门禁

结案核验（含 §5.1 Usage 与 §10.3 digest 补完）重跑：

```text
uv run --no-sync pytest --no-testmon -q
  -> 522 passed in 115.33s
uv run --no-sync ty check src tests
  -> All checks passed
```

未 git commit。P001 `fail_under=90` 覆盖率不是 D006 产品条款，本轮未作为结案门禁重跑。

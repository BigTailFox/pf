# P032 — D026 按命令装配与 Ctrl+C 终态实施计划

- **状态：** 已完成、已归档
- **开始日期：** 2026-09-04
- **完成日期：** 2026-09-04
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D026](../designs/D026-pf-command-composition-and-interrupt.md)
- **评审来源：** [R006](../../reviews/R006-pf-cli-system-review.md) §4、§6，
  [R007](../../reviews/R007-pf-current-improvement-priorities.md) §4、§5
- **实施基线：** 当前工作树对照 D001/D002/D006/D007 与 `src/pf/cli.py`
- **实现提交：** 工作树未提交；本 Plan 与 D026 在同一完成变更中归档

本文把 D026 验收标准映射到有序切片、测试和证据槽。Composition 与 Interrupt 分列验收，可共享
bootstrap 实现。完成标准只来自 D026 §4，不以局部绿色或单一 Python 版本替代验收。

## 1. 目标与边界

- `build_context()` 只建立 logs + presenter；同一 `cli.py` root 在命令 handler 内按 §2.2 装配；
- composition-time `PfError` 走 `render_error()`；资源关闭一次；
- 用户中断退出 `130`，stderr 唯一 `Interrupted` final，停止 in-flight process groups，不合成
  report；
- 删除 `FailureLogAssociations`；不引入第二个 composition root 或 DI framework。

## 2. 基线事实与目标差距

| 切面 | 当前事实 | D026 目标 |
| --- | --- | --- |
| bootstrap | 解析前装配完整验证图与七个 workflow | 只装配 presenter + logs |
| `CliContext` | 构造要求七个 workflow | 生产构造不要求 workflow；按命令装配 |
| composition 错误 | `PfError` 在 `with build_context()` 外 | 与运行期错误同一 `render_error` |
| 中断 | 未捕获；可能 traceback；无退出码 | `130` + stderr final + 停 child |
| Search association | `FailureLogAssociations` Protocol | 直接 `RunLogStore` |
| owner | D001 仅 `0..4`；D002 七槽 context | 归并后归档 D026/P032 |

## 3. Interface 与 ownership

1. `cli.py` 独占生产 composition 与 `main()` 错误/中断边界。
2. `TerminalPresenter` 独占 interrupt final 与 exactly-one-final 记账。
3. `SubprocessRunner` 独占 in-flight process-group 停止；复用 timeout 的 SIGTERM/SIGKILL。
4. D001 拥有退出 `130` 与“中断不是搜索结果、不合成报告”；D006 拥有文案与通道；D007 拥有
   child 停止；D002 拥有 command-scoped assembly。
5. Search workflow 直接依赖 `RunLogStore.replace_associations`；`DiagnosisLogLocator` 不动。

## 4. 实施顺序

### 切片 001 — 最小 bootstrap 与按命令装配

`CliContext` 持有 presenter/logs 与 private 子图缓存。`check_workflow()` 等入口在首次调用时
装配 §2.2 graph。help/version 不调用这些入口。CLI 测试 helper 可注入已构造 workflow。
`host_target()` 随 VerificationRunner 缓存一次。

验收：D026 §4.1–4.5。

### 切片 002 — composition 错误边界

`main()` 先 bootstrap，再在 handler/`create_app()` 内捕获 `PfError`。composition-time
`ConfigurationError` 进入 `render_error()`。`close()` 幂等，装配失败与正常退出都只关闭一次。

验收：D026 §4.6。

### 切片 003 — 删除 FailureLogAssociations

`SearchCommandWorkflow` 接收可选 `RunLogStore`；删除 Protocol。public search workflow tests
继续覆盖 generation association 替换/删除。

验收：D026 §4.7。

### 切片 004 — in-flight 中断与退出 130

`SubprocessRunner` 跟踪 in-flight Popen；`interrupt()` 终止并置位，后续 `run()` 再抛
`KeyboardInterrupt`。`main()` 捕获中断、停止 children、`render_interrupt()`、`SystemExit(130)`。
Presenter 在尚无 final 时于 stderr 打印 `⚠ Interrupted`。live `close()` 幂等。嵌套中断仍关闭。

验收：D026 §4.8–4.11。

### 切片 005 — owner 归并与归档

写入 D001/D002/D006/D007；关闭 R006/R007 对应项；更新文档索引；归档 D026/P032。

验收：D026 §4.13。

## 5. 测试矩阵

| 场景 | 公开 seam |
| --- | --- |
| help/version 不构造 UvAdapter/host_target/SearchCoordinator | `main` / `python -m pf` |
| explain/diagnose/merge 同上 | `main` 或 `build_context` + 装配入口 |
| apply 不构造 evaluator/SearchCoordinator、不探测 host | 装配入口 |
| search 装配后 host_target 一次；smoke 复用 | `build_context` + 入口 |
| composition `ConfigurationError` → 退出 3、无 traceback | `main` |
| Search association 仍随 generation 替换 | `SearchCommandWorkflow.run` |
| 运行中 interrupt 停止 process group 并抛 KeyboardInterrupt | `SubprocessRunner` |
| handler 中 KeyboardInterrupt → 130、stderr Interrupted、无 traceback | `main` |
| 已有命令 final 时不打印第二个 Interrupted | `render_interrupt` |

## 6. 验证命令

```text
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon \
  tests/test_cli.py tests/test_process.py tests/test_search_workflow.py tests/test_terminal.py -q

UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon --cov -q

.venv/bin/ruff check src tests
.venv/bin/ty check src
git diff --check
```

## 7. 验收对照

| D026 §4 | 切片 | 状态 |
| --- | --- | --- |
| 1–5 composition graphs | 001 | 通过 |
| 6 composition errors | 002 | 通过 |
| 7 FailureLogAssociations | 003 | 通过 |
| 8–11 interrupt | 004 | 通过 |
| 12 公共 tests | 001–004 | 通过 |
| 13 owner 归并 | 005 | 通过 |

## 8. 行动记录

- 建立 D026/P032，选择唯一 root 内最小 bootstrap + 按命令装配；拒绝七槽惰性 provider。
- 实现 `CliContext` 按命令装配、composition-time `PfError` 进入 `render_error()`、删除
  `FailureLogAssociations`、`SubprocessRunner.interrupt()`、退出 `130` 与 `⚠ Interrupted`。
- 稳定规则归并 D001/D002/D006/D007；关闭 R006/R007 对应项；归档本文与 D026。

**验证：**

```text
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon \
  tests/test_cli.py::TestDefaultContext tests/test_process.py tests/test_search_workflow.py \
  tests/test_terminal.py::TestErrorRendering -q
27+ passed

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/ty check src
All checks passed!

UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon --cov -q
1513 passed；coverage 90.25%（required 90.0%）
本环境另有 34 个既有 TTY/width/installed-CLI 失败，与对照未改代码复现，不计入本变更回归。
```

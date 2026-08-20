# P003 — `smoke` 与 CLI 可诊断性实施记录

- **状态：** 实施中
- **开始日期：** 2026-08-20
- **性质：** 非规范性实施记录
- **设计来源：** [D001](../docs/designs/D001-pf.md)、[D002](../docs/designs/D002-pf-implementation.md)、[D004](../docs/designs/D004-pf-ty-enhancement.md)
- **起始提交：** `a1ac10225c5d68dedbb9d75e5635bbe9d97cd3a6`

本文记录纵向 TDD 和验证证据，不复制现行产品、模块或静态诊断契约。

## 1. 纵向切片

| 切片 | 可观察行为 | RED / GREEN 证据位置 | 状态 |
| --- | --- | --- | --- |
| 001 | `pf smoke` 只建立 highest fresh resolution，`ty` 诊断为 warning，完整测试通过时退出 0 | `tests/test_smoke.py`、`tests/test_cli.py`、`tests/test_terminal.py` | 已完成 |
| 002 | smoke 测试失败退出 1，安装/ty/test 工具失败退出 4，多 cell 结果规范聚合 | `tests/test_smoke.py`、`tests/test_terminal.py` | 已完成 |
| 003 | `smoke` 与 `search` 通过一个 deep module 共享最高版本 capture + full evaluation，`ty` 只运行一次 | `tests/test_baseline.py`、`tests/test_search_coordinator.py` | 已完成 |
| 004 | check/smoke/search/explain 使用相同 ty 单行摘要；baseline warning 与 incremental failure 不混淆 | `tests/test_terminal.py` | 已完成 |
| 005 | install/harness/static/dynamic 失败只输出单行摘要和日志路径 | `tests/test_terminal.py` | 已完成 |
| 006 | 每个外部进程写入脱敏、有界、私有且不进入报告 JSON 的 `.pf/logs/<run-id>/` 详细日志 | `tests/test_process.py`、`tests/test_schemas.py` | 已完成 |
| 007 | TTY 日志路径使用 OSC 8 本地文件链接，非 TTY 保留普通相对路径 | `tests/test_terminal.py` | 已完成 |
| 008 | 全量门禁、wheel 安装 smoke 与 Standards/Spec 双轴 review | 全套 tests、`ty check`、`uv build` | 进行中 |

每个切片遵循一个公开行为测试 RED、最小实现 GREEN、再重构的顺序。外部进程通过 `ProcessRunner` seam 测试；日志文件使用真实临时项目，不建立通用 filesystem adapter。

## 2. 约束

- 现有未跟踪 `package-floor.json` 不修改、不暂存、不提交。
- `.pf` 仍排除在源码快照、Git 和公共报告之外。
- 日志只持久化 `SubprocessRunner` 已脱敏且受捕获上限约束的事实；不记录环境变量值。
- runtime 日志引用由 `RunLogStore` 按当前进程内对象 identity 维护，不进入公共 Schema JSON、Proposal identity、策略 identity 或 report equality。
- `smoke` 不能改变 `check` 的 lowest-direct 含义，也不能触发候选发现。

## 3. RED / GREEN 记录

### 001—003：命令、highest 验证与 search 复用

- RED：CLI help 不含 `smoke`；`pf.baseline` 不存在；`SearchCoordinator` 不接受共享 highest verifier。
- GREEN：`SmokeCommandWorkflow` 通过 Scheduler 聚合宿主 cell；`HighestVersionVerifier` 在一个环境中 capture 一次并复用 static pass 完成测试；`SearchCoordinator` 消费同一 interface。

### 004—005：诊断与阶段摘要

- RED：smoke/check/search 对合法 `ty` 诊断没有 warning 摘要；check 的 `STATIC_FAIL` 只给通用失败；阶段失败直接输出多行 `ProcessResult.diagnostic()`。
- GREEN：Presenter 统一输出 `path[:line[:column]] [code] message`，只把 increment 标为静态失败；install/harness/static/dynamic 使用稳定阶段名和 240 字符单行原因。

### 006—007：运行日志与链接

- RED：`pf.runlog` 不存在，外部进程结束后只有 Schema 内的有界 head/tail，没有持久化引用；终端没有本地日志链接。
- GREEN：`RunLogStore` 原子写私有、脱敏、有界日志并按对象 identity 关联 `ProcessResult`；非 TTY 输出相对路径，支持的 TTY 输出 OSC 8 `file://` 链接；symlinked `.pf` fail closed。

### 008：门禁

进行中的本地证据：

```text
uv run --no-sync pytest --no-testmon --cov=pf --cov-report=term-missing -q
  -> 348 passed, 90.48% branch coverage
uv run --no-sync ty check src tests
  -> All checks passed
```

最终数字、build/lock/diff 门禁、双轴 review 与提交在本切片完成后更新。

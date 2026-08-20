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
| 001 | `pf smoke` 只建立 highest fresh resolution，`ty` 诊断为 warning，完整测试通过时退出 0 | `tests/test_smoke.py`、`tests/test_cli.py`、`tests/test_terminal.py` | 待开始 |
| 002 | smoke 测试失败退出 1，安装/ty/test 工具失败退出 4，多 cell 结果规范聚合 | `tests/test_smoke.py`、`tests/test_terminal.py` | 待开始 |
| 003 | `smoke` 与 `search` 通过一个 deep module 共享最高版本 capture + full evaluation，`ty` 只运行一次 | `tests/test_baseline.py`、`tests/test_search_coordinator.py` | 待开始 |
| 004 | check/smoke/search/explain 使用相同 ty 单行摘要；baseline warning 与 incremental failure 不混淆 | `tests/test_terminal.py` | 待开始 |
| 005 | install/harness/static/dynamic 失败只输出单行摘要和日志路径 | `tests/test_terminal.py` | 待开始 |
| 006 | 每个外部进程写入脱敏、有界、私有且不进入报告 JSON 的 `.pf/logs/<run-id>/` 详细日志 | `tests/test_process.py`、`tests/test_schemas.py` | 待开始 |
| 007 | TTY 日志路径使用 OSC 8 本地文件链接，非 TTY 保留普通相对路径 | `tests/test_terminal.py` | 待开始 |
| 008 | 全量门禁、wheel 安装 smoke 与 Standards/Spec 双轴 review | 全套 tests、`ty check`、`uv build` | 待开始 |

每个切片遵循一个公开行为测试 RED、最小实现 GREEN、再重构的顺序。外部进程通过 `ProcessRunner` seam 测试；日志文件使用真实临时项目，不建立通用 filesystem adapter。

## 2. 约束

- 现有未跟踪 `package-floor.json` 不修改、不暂存、不提交。
- `.pf` 仍排除在源码快照、Git 和公共报告之外。
- 日志只持久化 `SubprocessRunner` 已脱敏且受捕获上限约束的事实；不记录环境变量值。
- runtime 日志引用由 `RunLogStore` 按当前进程内对象 identity 维护，不进入公共 Schema JSON、Proposal identity、策略 identity 或 report equality。
- `smoke` 不能改变 `check` 的 lowest-direct 含义，也不能触发候选发现。

## 3. RED / GREEN 记录

实施过程中逐切片追加失败命令、最小 GREEN 命令和必要的重构说明。

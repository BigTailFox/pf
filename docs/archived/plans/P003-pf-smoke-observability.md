# P003 — `smoke` 与 CLI 可诊断性实施记录

- **状态：** 已归档（已完成）
- **开始日期：** 2026-08-20
- **完成日期：** 2026-08-20
- **性质：** 非规范性历史记录
- **设计来源：** [D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)、[D004](../../designs/D004-pf-ty-enhancement.md)
- **后续失败契约：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **起始提交：** `a1ac10225c5d68dedbb9d75e5635bbe9d97cd3a6`

本文记录纵向 TDD 和验证证据，不复制现行产品、模块或静态诊断契约。“已完成”只表示当时的 P003 范围已经实现；D005 的实施证据见 [P004](P004-pf-failure-and-diagnose.md)。

## 0. 后续契约变更

D005 已取代 P003 当时的 runtime-only candidate failure 结构：Rejection/Indeterminate 现在必须以 `AttemptFailureScope | CellFailureScope` 和 `FailureRecord` 进入公共报告，详细日志仍留在本机，并通过 `(report_generation_id, failure_id)` 的 diagnosis index 关联。D005、重塑后的 Schema 1 与 `pf diagnose` 的实施证据见 [P004](P004-pf-failure-and-diagnose.md)；下面关于 `SearchDiagnosticEvent`、对象 identity 日志引用和“不扩展报告 Schema”的内容只记录 P003 当时的实现证据。

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
| 008 | 全量门禁、wheel 安装 smoke 与 Standards/Spec 双轴 review | 全套 tests、`ty check`、`uv build` | 已完成 |

每个切片遵循一个公开行为测试 RED、最小实现 GREEN、再重构的顺序。外部进程通过 `ProcessRunner` seam 测试；日志文件使用真实临时项目，不建立通用 filesystem adapter。

## 2. 约束

- 现有未跟踪 `package-floor.json` 不修改、不暂存、不提交。
- `.pf` 仍排除在源码快照、Git 和公共报告之外。
- 日志只持久化 `SubprocessRunner` 已脱敏且受捕获上限约束的事实；不记录环境变量值。
- P003 实现中的 runtime 日志引用由 `RunLogStore` 按当前进程内对象 identity 维护；D005 实施后，公共 Schema 仍不保存日志路径，但本地 diagnosis index 会把 FailureRecord 与详细日志关联。
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

最终本地证据：

```text
uv run --no-sync pytest --no-testmon --cov=pf --cov-report=term-missing -q
  -> 362 passed, 90.42% branch coverage
uv run --no-sync ty check src tests
  -> All checks passed
uv build
  -> sdist 与 wheel 构建成功
uv lock --check --no-config --default-index https://pypi.org/simple
  -> lock 检查通过
installed wheel: pf smoke
  -> highest fresh install；1 条 ty 诊断为 warning；完整测试通过；exit 0
```

`git diff --check` 通过。以设计提交 `8bda99b` 为固定点的 Standards/Spec 双轴 review 最终均无剩余 finding。实现与复审修正提交为 `4b43599`、`ed29cbd`。

第一次 Standards/Spec 双轴 review 发现并以回归测试修正：

- search candidate 的 static/dynamic/install/harness 失败通过运行期强类型事件进入统一摘要，不扩展报告 Schema（P003 历史实现；D005 已改变目标契约）；
- 进程日志的 argv、cwd、环境变量名和输出片段全部增加独立硬上限；
- run directory 写入改用逐级 `dir_fd`、禁止跟随 symlink 和 inode identity，初始化后路径替换也 fail closed；
- `HighestVersionVerification` 的跨模块导入统一回到 `pf.schemas.evaluation` 所有者。

复审继续发现并修正两个边界问题：`SearchDiagnosticEvent` 拆为 `kind` 判别 variants，并拒绝 event cell 与 Proposal cell 不一致；Windows 日志写入先解析项目实际承载卷并要求其支持 persistent ACLs，再使用拒绝 reparse point 且不共享 write/delete 的原生 directory handles，并在创建 run directory 的同一个 `CreateDirectoryW` 调用中设置 owner/SYSTEM protected inheritable DACL，不再使用有 TOCTOU 窗口的 portable path fallback。

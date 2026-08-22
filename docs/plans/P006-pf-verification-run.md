# P006 — 统一验证运行语义实施记录

- **状态：** 已完成
- **开始日期：** 2026-08-21
- **完成日期：** 2026-08-21
- **性质：** 非规范性实施记录
- **设计来源：** [D008](../designs/D008-pf-verification-run.md)
- **依赖：** [P005](P005-pf-process-output.md) 的 `*_complete` 已替换截断字段；本计划不实施 D006 的完整展示架构
- **后继：** [P007](P007-pf-cli-enhancement.md) 的 Diagnose 入口与 Role→impact 渲染以本计划为前提

本文记录 D008 的落地过程与可核验证据，不复制契约。

## 1. 范围

本轮实现：

- `requested_resolution` 增加 `lowest-direct`；`EnvironmentFactory` 在三种解析方式下都于外部操作前建立 Attempt；
- `rejection_is_supported` 接受 `lowest-direct`（与 Probe 相同的 Rejection 资格）；
- `CompatibilityChecker` 停止 unwrap `PrepareFailure`，两轮失败都经 `FailurePolicy.classify`；
- 非成功 Cell Completion 必带 `stage` 与 `FailureRecord`；
- smoke/check/search 写入 Verification Journal；diagnosis index 增加 `latest_journal` 与 `(run_id, failure_id)`；
- `diagnose` 读取报告 ∪ `latest_journal`；impact 由 Verification Role 选择。

## 2. 落地顺序

| 切片 | 可观察行为 | 主要测试 | 状态 |
| --- | --- | --- | --- |
| 001 | Attempt identity 与分类接受 `lowest-direct` | `tests/test_schemas.py`、`tests/test_failure.py`、`tests/test_environment.py` | 已完成 |
| 002 | check 错误链：保留 Attempt、stage、FailureRecord；捕获失败不启动 declaration | `tests/test_check.py` | 已完成 |
| 003 | Journal 与 diagnosis index；check/smoke 无 floor 报告仍可 diagnose | `tests/test_diagnose.py`、`tests/test_process.py` | 已完成 |
| 004 | 调度投影带 stage；`CoordinateSearch` 不看见 declaration Attempt | `tests/test_scheduling.py`、`tests/test_search.py`、`tests/test_search_coordinator.py` | 已完成 |
| 005 | 相关门禁 | pytest / ty | 已完成 |

## 3. 过程与证据

### 关键产物

- `src/pf/schemas/evaluation.py`：`requested_resolution` 含 `lowest-direct`；`CheckCellOutcome`；`VerificationJournal`
- `src/pf/environment.py`：三种 resolution 均返回 `PreparedEnvironment | PrepareFailure`，`PreparedEnvironment.attempt` 必填
- `src/pf/workflow.py`：`CompatibilityChecker` 两轮 classify；`persist_verification_journal`；check 聚合按 D008 §7.1
- `src/pf/runlog.py`：`write_journal` / `read_journal` / `read_latest_journal` / `lookup_run`；index 键 `__latest_journal__` 与 `journal:{run_id}`
- `src/pf/scheduling.py`：完成投影带 `failure.stage` 与 `verification_role`，不用 `STATIC_FAIL` 猜 stage
- `src/pf/terminal.py`：`_impact_for(..., role=, command=)` 实现 D008 Role 表

### 可核验测试

```text
uv run --no-sync pytest --no-testmon -q --tb=line
  tests/test_check.py::test_check_highest_prepare_failure_does_not_start_lowest_direct
  tests/test_environment.py::test_environment_check_prepare_failure_keeps_a_lowest_direct_attempt
  tests/test_diagnose.py
  tests/test_report_workflows.py::test_explain_does_not_treat_a_verification_journal_as_a_report
  tests/test_search_coordinator.py::test_search_coordinator_never_requests_lowest_direct
  tests/test_search_workflow.py
  tests/test_terminal.py::test_completed_cell_with_failure_record_prints_diagnose_and_role_impact
  tests/test_terminal.py::test_completed_cell_omits_diagnose_when_journal_is_unavailable
  tests/test_smoke.py::test_smoke_omits_diagnose_when_journal_write_fails
```

代表断言：

- highest 捕获失败：`role == "declaration-capture"`，不启动 `lowest-direct`
- 无 `package-floor.json` 时仍能 `pf diagnose --failure ID`；impact 不含 “did not start the floor search”
- Journal 不能当 `explain` / `apply` 报告；即使复制成 `package-floor.json` 也因 schema 失败
- search 的 `failure_id` 在报告与 journal 中一致
- `CoordinateSearch` 测试不得读到 `lowest-direct`
- Journal 写入失败：live 卡片有 title 与进程末行，无 `pf diagnose` / `failure_id`（`test_smoke_omits_diagnose_when_journal_write_fails`）

### 决策

- `_parse_index` 必须跳过 `__latest_journal__` 的 locator 校验（值是 run_id，不是 `run/process-NNNN.log`）。
- `write_journal` 只索引已 `record` 的 process；无 locator 的 process 跳过，不让 Journal 写入失败。
- `Evaluation` 是 `Annotated[Union[...]]`，不能 `isinstance(..., Evaluation)`；check 聚合改用 `outcome.evaluation is not None`。
- smoke 与 search 的 baseline Role 相同，impact 由 `command` 区分。

### 审计补完：D008 §9 Journal 写入失败不得打印 Diagnose ID

独立审计发现：live 卡片在 `persist_verification_journal` 之前冻结，且 `write_journal` 失败时卡片仍打印 `pf diagnose ... --failure`。D008 §9 明确要求此时 Diagnose 入口不可用，不得留下事后 404 的 `failure_id`。

补完方式：cell 完成事件转发到 Presenter 之前先写入 Journal；写入失败则 `ProgressEvent.diagnose_available=False`，卡片仍保留 title、末 3 行和 Process Log 链接。

## 4. 完成门禁

```text
uv run --no-sync pytest --no-testmon -q
  -> 510 passed
uv run --no-sync ty check src tests
  -> All checks passed
```

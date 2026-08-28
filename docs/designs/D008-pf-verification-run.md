# PF 统一验证运行语义

- **状态：** 现行
- **Journal：** `verification-journal-v2`
- **最后核对：** 2026-08-26
- **命令语义：** [D001](D001-pf.md)
- **Failure 分类：** [D005](D005-pf-failure-and-diagnose.md)
- **展示：** [D006](D006-pf-cli-enhancement.md)
- **日志：** [D007](D007-pf-process-output.md)

本文是 Verification Run、命令的 Attempt 序列、Verification Role、跨 Cell scheduling、completion projection、Journal 时序、Diagnosis Index association 与 `diagnose` 读取面的唯一所有者。

## 1. Verification Run

```text
VerificationRun
  command: smoke | check | search
  packages
  one immutable SourceSnapshot
  unique Cell tasks
  jobs
  optional max-duration
```

每个 Cell task 在外部 operation 前必须建立 Attempt；Attempt 前的 candidate discovery 或 scheduler deadline 只能形成 `CellFailureScope`。Prepare failure 保留完整 `PrepareFailure(attempt, failure, acquired plan digests)`，调用方不得 unwrap 成裸 ToolFailure。

一个 Cell 的链路是：

```text
request/Cell
  -> Attempt
  -> prepare -> Proposal 或 PrepareFailure
  -> evaluate -> Evaluation
  -> FailurePolicy -> FailureRecord
  -> Journal durability
  -> CellCompletedEvent
  -> Presenter
```

PASS 不经过 FailurePolicy。合法 baseline `TyCheck` diagnostics 不是 FailureRecord。

## 2. Attempt request 与 Role

`requested_resolution` 是 Attempt identity：

```text
highest        managed vector 在解析前未知
lowest-direct  声明下界 request，managed vector 在解析前未知
exact-vector   search probe，必须带 requested managed vector
```

不能在 prepare 成功后把 highest/lowest-direct 改写成 exact-vector。Highest identity 不包含“是否运行测试”；同一 request 可以在不同 Verification Run 中承担不同 Role。

| Role | 命令 | Request | Evaluation contract |
| --- | --- | --- | --- |
| `baseline` | smoke/search | highest | full |
| `declaration-capture` | check | highest | static capture only |
| `declaration` | check | lowest-direct | full，相对本次捕获的 `S_hi` |
| `probe` | search | exact-vector | D003 runtime-backed route |

Role 进入 Journal 并决定 offline impact；它不进入 Attempt ID、Proposal ID 或 Failure ID，也不改变 D005 classification。

## 3. 命令序列

### 3.1 Smoke

每个宿主 Cell 一次 baseline：

```text
prepare(highest, original harness)
-> capture S_hi
-> 在同一未污染 environment 上 full evaluate
-> close
```

使用 `HighestVersionVerifier`，写 Journal，不读写 floor report。

### 3.2 Check

每个宿主 Cell 最多两次串行 Attempt：

```text
1. declaration-capture
   prepare(highest, original harness) -> capture S_hi/HarnessBaseline -> close

2. declaration
   仅当步骤 1 得到合法 S_hi
   prepare(lowest-direct, relaxed harness, captured HarnessBaseline)
   -> full evaluate relative to S_hi -> close
```

步骤 1 的 static diagnostics 构成 baseline，不是 failure。步骤 1 未成功时不得启动步骤 2，也不能把结果描述为“declared lower bounds failed”；它只说明未能捕获 baseline。步骤 2 不进入 CoordinateSearch。

### 3.3 Search

每个宿主 Cell 先运行一次 full highest baseline；只有 `HighestVersionPass` 才冻结 candidates 并进入 D003。每个真实 probe 是 exact-vector Attempt。Candidate discovery/scheduler deadline 可形成 Cell-scoped Indeterminate。

Search 同时把 FailureRecord 放入 Journal 与 Schema 1 report。Report 是 apply/explain/merge 的唯一公共接口；Journal 只用于本机 diagnose。

## 4. VerificationRunner 与 Scheduler

```text
VerificationRunner.run(VerificationRun) -> ordered outcomes
```

Runner 独占：

- 验证 package/task/Cell identity；
- 构造 generic Scheduler；
- max-duration 未启动 task 的 `TIMEOUT @ scheduler-deadline` CellResult；
- 领域 result → Cell completion 投影；
- per-Cell Journal merge、持久化与 diagnose availability；
- final Journal 错误上抛。

Scheduler 只理解 `ScheduledCellTask`, worker, deadline callback, `jobs`, monotonic clock 与 `cell_schedule_key`。它不导入 Evaluation、Failure、Journal、CellResult 或 terminal facts。结果按 package/target/Python/extra 规范排序；单 Cell 内 probe 串行。

当 Cell 完成时，Runner 先合并 buffered search failures 与 final task failures，再写当前完整 Journal；只有写入成功，`CellCompletedEvent.diagnose_available` 才为 true。写入失败仍发布 `diagnose_available=false` 的 completion，随后以 InfrastructureError 结束 run，不能静默宣称可诊断。

## 5. Activity 与 completion

Activity 使用判别 records：

```text
CellContextEvent(detail = baseline | declaration | search-probe | None)
CellStageEvent(stage, progress?)
CellCompletedEvent(completed, total, outcome, diagnose_available)
StatusEvent / CellMatrixEvent / ProcessEvent / SearchFailureEvent
```

Context、stage 与 completion 不能用 optional field 组合或 `completed == 0` 隐式编码。`0 < completed <= total`。

统一 completion outcome 是：

```text
CellSucceeded(status, phase)
CellFailed(status, phase, failures, process?, role?, runtime detail?)
```

`RuntimeEvaluator.evaluate` 返回 `RuntimeEvaluationRun(evaluation, diagnostics?)`。Evaluation 是
cache/report/Journal authority；diagnostics 是 invocation-local、excluded 数据。唯一投影路径是：

```text
VerifierRun.diagnostics
-> RuntimeEvaluationRun.diagnostics
-> completion projector
-> CellResultDetail(detail_failure_id)
```

Non-success `phase` 来自 FailureRecord stage；Presenter 不从 Schema status 猜 stage。
`CellResultDetail` 是 excluded runtime-only union：pytest failure detail 或 confirmed-missing
static issue。它必须绑定 retained failure ID，不进入 Journal、report、FailureRecord、cache 或
identity。搜索结果只在内存中按 Failure ID 保留相应 `RuntimeEvaluationRun`，用于 terminal
completion 与本地 Process Log association；序列化必须排除该映射。

## 6. 命令聚合

`check` 对每个 Cell 使用 declaration 结果；若未启动，则使用 declaration-capture 结果。任一 Rejected → compatibility failure/exit 1；否则任一 Indeterminate → exit 4；否则全部 declaration PASS → exit 0。

`smoke` 任一 BaselineRejection → exit 1；否则任一 BaselineIndeterminate → exit 4；否则 PASS。Search 的 baseline 聚合相同；Probe Rejections 只是搜索证据，其他 no-floor 原因按 D001 exit 2。

## 7. Verification Journal

V2 位置：

```text
.pf/logs/<run-id>/journal.json
```

结构：

```text
schema_version = verification-journal-v2
run_id
command = smoke | check | search
source_snapshot_digest
package_policies[]
  package
  evaluation_policy_identity
entries[]
  package
  Cell
  Role
  Attempt?       CellFailureScope 时省略
  FailureRecord v2 authority
```

Packages 与 policies 必须 sorted/unique。每个 entry 的 package、Cell、scope、Attempt、source digest 与该 package policy 必须闭合；同 failure ID 的不同 payload 冲突。Entries 按 package/Cell/failure ID 规范排序。

Journal 不保存 stdout/stderr、完整 Evaluation、`RuntimeEvaluationRun` diagnostics、absolute path
或 report refs。对于同一 Failure ID，其展开后的 `FailureAuthority` 必须与 D014 report 完全
一致。Process 原文在 D007 Process Log；search 的完整 portable evidence 在 D014 report。
Writer 只写 V2；`verification-journal-v1` 仅作历史本机日志的严格 reader compatibility，不是
第二个写 contract。

## 8. Diagnosis Index 与 report association

`.pf/logs/diagnosis-index.json` 保存：

```text
latest_journal[package] = run_id
(run_id, failure_id) -> relative Process Log
(report_generation_id, failure_id) -> relative Process Log
```

不得扫描 run directories 或按 output text 猜 locator。新 Verification Run 替换对应 package 的 `latest_journal`。

Search 必须先成功 `ReportStore.update_path`，再用 `ReportUpdate` 更新 report-side associations：

- generation replacement 时整体替换；
- 同 generation update 移除旧 Failure IDs、添加本次可关联 records；
- merge 自其他 host 的 Failure 可以没有本机 locator。

Association/locator 不进入 report，缺失不改变 Failure evidence。

## 9. Diagnose 读取面

`pf diagnose` 只读取：

```text
选中 package 的 package-floor.json（若存在）
union
该 package 的 latest Verification Journal（若存在）
```

指定 failure ID 时先查 report，再查 latest Journal；不存在则 exit 3，不遍历历史 runs。省略时合并 report records 与 latest Journal 中尚未出现的 failure ID，并标注来源；最终稳定排序和诊断语义由 D005 定义。两边都无记录时展示 0 failures、exit 0。

`explain`、`apply` 与 `merge` 只使用 report；Journal 缺失不削弱 report authority，report 缺失不阻止诊断最近一次 run。读取必须离线，不规划 environment、不启动 process、不修改项目。

## 10. Role-aware impact

| Role | Rejected | Indeterminate |
| --- | --- | --- |
| probe | candidate 未通过；search 可继续 | candidate compatibility unknown；停止 Cell |
| baseline/search | highest baseline 未通过，未开始 floor search | highest baseline unknown；停止 Cell |
| baseline/smoke | highest resolution 未通过 | highest resolution unknown |
| declaration-capture | 未能捕获 current declarations 的 static baseline，未验证下界 | baseline capture unknown，未验证下界 |
| declaration | declared lower bounds 未通过 | declared lower bounds unknown |
| Cell scope | 不允许 | 未取得启动/继续 Cell 所需信息 |

D005 拥有 title/next step 与 disposition；D008 唯一选择上述 impact；D006 只渲染。

## 11. 不变量

- Role 不改变 classification 或 identity。
- Journal 必须在可用 diagnose command 展示前 durable。
- Search report 与 Journal 的同一 failure ID 必须映射同一 portable facts。
- Cell completion 必须保留 stage 与 FailureRecord，不能退化为裸 status/message。
- Journal/Index/Process Log 是本机诊断材料，不是 apply authority。
- Scheduler 不拥有领域 failure；workflow 不复制 Runner 的 Journal timing。

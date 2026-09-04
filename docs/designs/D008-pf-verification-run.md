# PF 统一验证运行语义

- **状态：** 现行
- **Journal：** `verification-journal-v2`
- **最后核对：** 2026-09-04
- **命令语义：** [D001](D001-pf.md)
- **Failure 分类：** [D005](D005-pf-failure-and-diagnose.md)
- **展示：** [D006](D006-pf-cli-enhancement.md)
- **日志：** [D007](D007-pf-process-output.md)

本文是 Verification Run、命令的 Attempt 序列、Verification Role、跨 Cell scheduling、completion projection、Journal 时序、Diagnosis Index association 与 `diagnose` 读取面的唯一所有者。

## 1. Verification Run

```text
VerificationRun = CheckVerificationRun | SmokeVerificationRun | SearchVerificationRun
  command: ClassVar[smoke | check | search]
  package: one PackagePlan target
  source_plan: one SourcePlan
  snapshot: one borrowed immutable SourceSnapshot
  operation: command-specific Check | Smoke | Search Cell operation
  limits: RunLimits(max_cells, ty_jobs, test_jobs, max_duration_seconds)
```

三个 request 都是 invocation-local frozen dataclass；command 不进入实例字段或构造器。Runner只有一个
`run(VerificationRun)`行为入口，以typed overload返回各命令的精确outcome tuple。Request不进入Schema、
report、Journal、identity、cache或CLI context persistence。每个Cell operation在外部执行前必须建立
Attempt；Attempt前的candidate discovery或scheduler deadline只能形成`CellFailureScope`。Prepare failure
保留完整`PrepareFailure(attempt, failure, acquired plan digests)`，调用方不得unwrap成裸ToolFailure。

`source_plan.source_mode`必须与命令闭合：`smoke=DEVELOPMENT`，`check/search=SEARCH`；plan routes必须精确
等于package已分类routes。RunLimits 已由 workflow 在 project load 后一次解析并验证；Runner在operation前验证mode/routes，再从
`package.cells`选择全部且仅有`cell.target == host_target`的规范identity唯一Cell。Runner构造时接收
composition root一次探测的host target；它不隐式探测，request与workflow都不携带override。所选集合
必须等于一次`CellMatrixEvent`、Scheduler tasks、completion total、Search deadline scope与规范返回集合。
Runner把同一个PackagePlan、SourcePlan与borrowed snapshot对象传给全部operation；operation不从workflow
closure取得第二份plan。Workflow 在 snapshot/process 前验证 smoke/check/search 的 full-evaluation contract；Runner 保留同一 defense-in-depth admission。三条命令即使 host Cell 为空也要求有效 `test-command` 与存在的 effective `test-group`。合法完整 contract 下的 Search空集仍finalize空Journal并返回`()`，workflow继续形成`MISSING_CELL` incomplete report。

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
prepare(highest, original harness, DEVELOPMENT)
-> capture S_hi
-> 在同一未污染 environment 上 full evaluate
-> close
```

使用 `HighestVersionVerifier`，写 Journal，不读写 floor report。

### 3.2 Check

每个宿主 Cell 最多两次串行 Attempt：

```text
1. declaration-capture
   prepare(highest, original harness, SEARCH) -> capture S_hi/HarnessBaseline -> close

2. declaration
   仅当步骤 1 得到合法 S_hi
   prepare(lowest-direct, relaxed harness, captured HarnessBaseline, SEARCH)
   -> full evaluate relative to S_hi -> close
```

步骤 1 的 static diagnostics 构成 baseline，不是 failure。步骤 1 未成功时不得启动步骤 2，也不能把结果描述为“declared lower bounds failed”；它只说明未能捕获 baseline。步骤 2 不进入 CoordinateSearch。

### 3.3 Search

每个宿主Cell先以SEARCH source运行一次full highest registry baseline；只有`HighestVersionPass`才从相同registry routes冻结candidates并进入D003。每个真实probe是exact-vector Attempt且继续使用同一SourcePlan。Candidate discovery/scheduler deadline可形成Cell-scoped Indeterminate；registry失败或managed coordinate local leakage不得回退到DEVELOPMENT。没有宿主Cell时仍先完成命令级 contract admission；contract 完整才是合法空Run，不启动full-evaluation或operation，report coverage仍记录`MISSING_CELL`。

Search 同时把 FailureRecord 放入 Journal 与 Schema 1 report。Report 是 apply/explain/merge 的唯一公共接口；Journal 只用于本机 diagnose。

## 4. VerificationRunner 与 Scheduler

```text
VerificationRunner.run(VerificationRun) -> ordered outcomes
```

Runner 独占：

- 验证command/package/SourcePlan与host Cell聚合不变量，消费已经解析的 RunLimits；
- 发布唯一`CellMatrixEvent`，私有装配command-specific operation task并注入同一Run facts；
- 经Scheduler `on_started`为每个真正启动的Cell发布唯一initial
  `CellContextEvent(BaselineDetailIdentity)`；
- 用 `limits.max_cells` 构造 generic Scheduler，并在 scheduler 前用 `limits.ty_jobs/test_jobs` 配置 composition root 与 evaluators 共享的阶段 permit pools；
- `limits.max_duration_seconds` 对未启动task形成`TIMEOUT @ scheduler-deadline` CellResult；`None`不安装deadline callback；
- 三个implementation-private typed projector同时形成Run-live completion、Journal entries与journal-side
  Process Log facts，并拒绝outcome family/Cell mismatch或Search lowest-direct Attempt；
- per-Cell Journal/association merge、持久化与diagnose availability；
- final Journal 错误上抛。

Scheduler只理解`ScheduledCellTask`, worker, deadline callback, resolved positive integer `jobs`, monotonic clock与
`cell_schedule_key`。它在取得worker slot后先完成`on_started`再提交operation；deadline到达时留在pending
的Cell不调用started。它不导入Evaluation、Failure、Role、Journal、CellResult或terminal facts。结果按
target/Python/extra规范排序，completion保留真实完成顺序；单Cell内probe串行。

当Cell完成时，Runner先做typed projection，再合并buffered与terminal failures，写当前完整Journal并写
`journal:<run-id>` Process Log associations；两者都成功才让`CellCompletedEvent.diagnose_available`为true。
`logs=None`永远false。写入失败仍发布false completion，Scheduler收尾后以InfrastructureError结束Run；
final persist failure也不能被typed command result吞掉。同failure ID对应不同portable entry时fail closed。

Workflow继续拥有project load、snapshot build/close、SourcePlan构造、status与typed聚合；Search还拥有
post-run source drift、report build/update与report-generation association replacement。Runner不关闭、
materialize或重建snapshot，也不拥有report ID。

`max_cells` 只限制跨 Cell task；`ty_jobs` 与 `test_jobs` 分别限制所有 Cell 共享的真实 ty process 和 configured verifier process。Runtime witness、uv resolution/install 与其他进程不占这两个 pool，stage limits 也不进入 tool argv、Journal、report 或 policy identity。

## 5. Activity 与 completion

Activity 使用判别 records：

```text
CellContextEvent(detail = baseline | declaration | search-probe | None)
CellStageEvent(stage, progress?)
CellCompletedEvent(completed, total, outcome, diagnose_available)
StatusEvent / CellMatrixEvent / ProcessEvent / SearchFailureEvent
```

Context、stage 与 completion 不能用 optional field 组合或 `completed == 0` 隐式编码。`0 < completed <= total`。

Initial context只由Runner为已启动Cell发布。Smoke workflow、`CompatibilityChecker`与
`SearchCoordinator`不再发布baseline initial；Check的`DeclarationDetailIdentity`、Search的`detail=None`
与probe context继续由对应单Celloperation拥有。该initial event happens-before operation的任何context或
stage；未启动deadline Cell没有context。`HighestVersionVerifier`不发布context。

统一 completion outcome 是：

```text
CellSucceeded(status, phase)
CellFailed(status, phase, failures, process?, role?, runtime detail?)
```

`RuntimeEvaluator.evaluate` 返回 `RuntimeEvaluationRun(evaluation, diagnostics?)`。Evaluation 是
cache/report/Journal authority；diagnostics 是 invocation-local、excluded 数据。Completion展示有三条
closed且不共享public projector的投影路径：

```text
Run live: command outcome + RuntimeEvaluationRun diagnostics
  -> Runner private typed projector -> CellCompletedEvent
Run final: Check/Smoke typed outcomes | Search CellResult
  -> terminal-private command projector -> CellPresentation
Explain / remaining Search failure: Evaluation | Failure facts
  -> terminal-private evaluation projector -> CellPresentation
```

三者对共同适用的kind/status/phase、detail及其Failure source、process及其Failure source、Failure集与Role
保持语义相等，但不要求共享实现或object identity。Terminal不导入Runner private projector；不存在接受
任意`object`的shared projector。Run live只从`CellCompletedEvent`读取completion，final不缓存或重放live。

Non-success `phase` 来自 FailureRecord stage；Presenter 不从 Schema status 猜 stage。
`CellResultDetail` 是 excluded runtime-only union：pytest failure detail 或 confirmed-missing
static issue。它必须绑定 retained failure ID，不进入 Journal、report、FailureRecord、cache 或
identity。搜索结果只在内存中按 Failure ID 保留相应 `RuntimeEvaluationRun`，用于 terminal
completion 与本地 Process Log association；序列化必须排除该映射。

## 6. 命令聚合

`check` 对每个 Cell 使用 declaration 结果；若未启动，则使用 declaration-capture 结果。任一
Rejected 聚合为 `COMPATIBILITY_FAILED`；否则任一 Indeterminate 聚合为 `INDETERMINATE`；其余
为 `PASS`。

`smoke` 按 `BaselineRejection > BaselineIndeterminate > PASS` 聚合。`search` 按
`BASELINE_REJECTION > INDETERMINATE > 其他 no-floor reason > complete` 聚合；Probe Rejection
只作为搜索证据。D001 独占这些结果的数值退出码。

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

每个现行Verification Run只写一个package policy；数组形状仅服务Journal wire。每个entry的package、Cell、scope、Attempt、source digest与该policy必须闭合；同failure ID的不同payload冲突。Entries按package/Cell/failure ID规范排序。

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

Search 必须先成功更新单target report path，再用该`ReportUpdate`更新report-side associations：

- generation replacement 时整体替换；
- 同 generation update 移除旧 Failure IDs、添加本次可关联 records；
- merge 自其他 host 的 Failure 可以没有本机 locator。

Association/locator 不进入 report，缺失不改变 Failure evidence。

## 9. Diagnose 读取面

`pf diagnose FAILURE_ID [--package PACKAGE]`只查一个canonical Failure ID，并按顺序读取：

```text
1. 选中 package 的可读 Schema 1 `package-floor.json`（若存在）；
2. 仅当报告不存在、不可读/非法、或没有该 ID 时，该 package 的 latest Verification Journal（若存在）。
```

可读报告命中后不读取Journal。两处都没有该ID时形成D001的typed配置错误，不遍历历史runs，也不枚举、合并或排序多个Failure。Journal/Index缺失只使本地log link不可用，不削弱报告中的portable authority。不可读报告不是一次命中，不能阻止 Journal 诊断最近一次 run。

`explain`、`apply` 与 `merge` 只使用 report；Journal 缺失不削弱 report authority，report 缺失或不合法不阻止诊断最近一次 run。读取必须离线，不规划 environment、不启动 process、不修改项目。

## 10. Role-aware impact

| Role | Rejected | Indeterminate |
| --- | --- | --- |
| probe | `This candidate was excluded from the search.` | `Compatibility for this candidate is unknown, so this cell stopped.` |
| baseline/search | `The highest-version baseline did not pass, so the floor search did not start for this cell.` | `Compatibility of the highest-version baseline is unknown, so this cell stopped.` |
| baseline/smoke | `The highest-version resolution did not pass the required checks.` | `Compatibility of the highest-version resolution is unknown.` |
| declaration-capture | `A static baseline could not be captured from the current declarations, so declared lower bounds were not verified for this cell.` | `Whether a static baseline can be captured is unknown, so declared lower bounds were not verified for this cell.` |
| declaration | `The declared lower bounds did not pass the required checks.` | `Compatibility of the declared lower bounds is unknown.` |
| Cell scope | 不允许 | `PF could not obtain the information needed to start or continue this cell.` |

D005 拥有 title/next step 与 disposition；D008 唯一选择上述 impact；D006 只渲染。

## 11. 不变量

- Role 不改变 classification 或 identity。
- Journal 必须在可用 diagnose command 展示前 durable。
- Search report 与 Journal 的同一 failure ID 必须映射同一 portable facts。
- Cell completion 必须保留 stage 与 FailureRecord，不能退化为裸 status/message。
- Journal/Index/Process Log 是本机诊断材料，不是 apply authority。
- Scheduler 不拥有领域 failure；workflow 不复制 Runner 的 Journal timing。
- Matrix、task、completion total、deadline scope与返回集合必须来自同一个host Cell集。
- Borrowed snapshot总由workflow `finally`关闭；Search在Runner返回后继续拥有drift/report/association。

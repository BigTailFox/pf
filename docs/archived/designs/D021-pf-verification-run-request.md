# D021 — PF Verification Run request 模块深化（归档）

- **状态：** 已完成，已归档
- **日期：** 2026-09-03
- **接受日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 临时性架构优化设计；完成后归并到现行 owner 并与实施 Plan 一同归档
- **设计核对基线：** `e570cea`（`refactor: deepen workspace inventory module`）
- **实现提交：** `7bc21fe`（`refactor: deepen verification run request`）
- **评审来源：** [R005](../reviews/R005-pf-module-depth-review.md) §5、§11.3
- **现行产品契约：** [D001](../../designs/D001-pf.md)
- **现行实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **现行 Failure 契约：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **现行展示契约：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **现行进程日志：** [D007](../../designs/D007-pf-process-output.md)
- **现行运行语义：** [D008](../../designs/D008-pf-verification-run.md)
- **现行 Harness 契约：** [D012](../../designs/D012-pf-harness-relaxation.md)
- **现行报告 wire：** [D014](../../designs/D014-pf-report-schema.md)
- **前序迁移：** [D019](D019-pf-source-plan-depth.md)、[D020](D020-pf-workspace-inventory.md)
- **实施计划：** [P027](../plans/P027-pf-verification-run-request.md)

> **归档声明：** 本文只保留 Verification Run request 与跨 Cell lifecycle interface 迁移的设计理由和
> 验收历史；稳定规则已经由 D002/D006/D008 接管，本文不再承担规范性 ownership。

[R005](../reviews/R005-pf-module-depth-review.md) 的评价 seam / `SearchCoordinator` 测试整改与
terminal-private result card 属于独立轨道，不进入本设计。SourcePlan 与 WorkspaceInventory 已由
D019/P025、D020/P026 完成；本设计消费它们的现行 interface，不重新打开其 ownership。

## 1. 问题与目标

当前 `VerificationRunner` 已独占 generic scheduling、scheduler deadline、completion projection、
Verification Journal 时序与 Journal-side Process Log association，但调用 interface 仍要求三个 workflow
为每个 Cell 组装：

```text
VerificationTask
  cell
  execute(source_plan)
  journal_entries(outcome)
  runtime_associations(outcome)?
  deadline_scope?
```

Check、Smoke 与 Search 因而分别知道 task closure、Role → Journal entry、runtime sidecar 与 deadline
FailureRecord 的形成方式。`completion_outcome(result: object)` 又让 Runner 对开放 object family 做类型猜测，
并被 Runner tests、评价 tests 与 terminal presentation 共同调用。Runner 的 implementation 已经是领域
module，interface 却仍把 lifecycle mechanics 交回 caller；新增 outcome 或调整 durable-before-diagnose
时需要跨 workflow、Runner 和 presentation 同步修改。

三个在线 workflow 还各自执行 host Cell 过滤、`CellMatrixEvent` 投影与 full-evaluation contract 检查。
这些事实直接决定 task 集、completion total 与 deadline scope，却不由跨 Cell Run owner 闭合。Search
把 full contract 检查留到每个 `SearchCoordinator.search`，Check/Smoke 又在 workflow 与单 Cell operation
重复检查；空 host Cell 的产品语义也没有在一个 owner 中明确表达。

现行 initial context 也不是三份等价代码：Smoke 在 workflow `_cell_task` 发布
`CellContextEvent(BaselineDetailIdentity)`；Check 在 `CompatibilityChecker.check()` 发布同一 initial context，
随后还发布 `DeclarationDetailIdentity`；Search 在 `SearchCoordinator.search()` 发布 initial context，随后还
发布 `detail=None` 的 context 与 `SearchProbeDetailIdentity`。`HighestVersionVerifier` 不发布 context，Runner
传给 Scheduler 的 `on_started` 当前为空。若只在 Runner 增加 initial context 而不删除前三个现行发布点，
同一 Cell 的 live activity 会出现重复 started；若把后续产品 context 一并搬走，又会夺走单 Cell operation
对 declaration/probe 阶段的 ownership。

Terminal 的现行 live 与 final 也不是同一消费路径。Live 已消费 `CellCompletedEvent.outcome`；Check/Smoke
final 分别迭代 command result 的 `outcomes`，Search final 迭代 report 的 `cell_results`。Explain 与剩余的
`SearchFailureEvent` 还需要从 Evaluation/Failure facts 形成展示。因此 Runner completion 可以成为 Run live
的唯一投影，却不能被描述为所有 final/explain presentation 唯一可消费的 event cache；否则实现只能缓存并
重放 live event，或重新引入对开放 object family 的共享 projector。

本设计目标是：

1. 以一个 command-discriminated request 替换 `VerificationRun + VerificationTask[]`；
2. 让 workflow 只提供 package、唯一 SourcePlan、借用的 SourceSnapshot、命令 Cell operation 与调度输入；
3. Runner 独占 host Cell 集、matrix、task assembly、initial context、deadline、typed completion、Journal 与
   Journal-side association；
4. 保留 Check、Smoke、Search 三个产品编排器、generic Scheduler、workflow、report 与 terminal 的现有
   ownership；
5. 删除开放 object projector、workflow 提供的 lifecycle callback 和旧 shallow tests，不增加
   compatibility layer；
6. 保持 CLI、退出码、Schema 1、Attempt/Failure/SourcePlan identity 与 report/apply authority 不变。

删除深化后的 `VerificationRunner` 时，host selection、matrix、task/deadline assembly、Role/Journal
projection、Process Log association、durable-before-diagnose 与 persist-failure timing 会重新散回三个
workflow；这就是本次 depth 的删除测试。

## 2. Interface alternatives 与选择

本设计比较三种独立形状，并把当前 record-envelope 作为否决基线。

### 2.1 方案 A：command-discriminated request（选择）

`VerificationRunner.run(...)` 保持一个 entry point，接收 Check、Smoke 或 Search 的 frozen request variant。
Variant 固定 command、合法 operation、duration 能力与返回类型；caller 不传 Cell 或 task。Runner 构造时
固定当前进程的 `host_target`，避免三个 request 重复同一 invocation policy。

该形状的 depth 最高：interface 只有一个行为入口，caller 只学习自己的 variant；command-specific
差异在 closed union 中显式表达，不以 optional fields 或 runtime string 组合编码。

### 2.2 方案 B：三个 verb-first entry point（不选择）

```text
VerificationRunner.check(subject, operation, jobs) -> tuple[CheckCellOutcome, ...]
VerificationRunner.smoke(subject, operation, jobs) -> tuple[HighestVersionOutcome, ...]
VerificationRunner.search(subject, operation, jobs, duration) -> tuple[CellResult, ...]
```

该形状让单个 caller 最短，且 Check/Smoke 无法表达 duration；但 Runner 的总 interface 增为三个方法，
公共 subject 与参数在三个签名重复。PF 当前没有需要独立扩展这些 entry point 的第四类 Verification Run，
也没有异构 Run batch caller，因此该灵活性不足以抵消 interface 扩张。

### 2.3 方案 C：可注册 command handler / Run session（不选择）

扩展性优先的形状可以先建立 one-shot Run session，再通过注册的 opaque command handle 执行：

```text
VerificationRunner.start(VerificationSubject, RunLimits) -> RunSession
RunSession.execute(VerificationCommand[Outcome]) -> tuple[Outcome, ...]
```

Composition root 为 Check、Smoke 与 Search 各注册一个 handler；handler 提供 operation、outcome family 与
lifecycle policy。它便于增加未知命令，却要求 command catalog、session state 和 registry freeze contract，
而 `RunLimits` 仍能表达 Check/Smoke + duration 这类非法组合。当前只有三类封闭产品 Run；这一 registry
只有一组生产 handlers，删除后只需改为直接调用，不能通过删除测试。具体 mechanics 即使藏在 handler
implementation，Role、deadline 与 projector 知识也会从 D008 的 Runner 分散到多个 policy module；若再
收回 session，就会退化为 §2.4 的通用 hooks/envelope。

### 2.4 否决基线：通用 result envelope

不采用 operation 返回 `result + completion + journal entries + associations` 的通用 record，也不接受
request 携带 `project_completion`、`journal_entries`、`runtime_associations` 或 `deadline_result` callback。
这些形状只把当前 `VerificationTask` 字段搬到新名字下；删除 wrapper 后复杂度不会重新分散，因为复杂度
从未离开 caller，不能通过删除测试。

| 方案 | Depth | Locality | Seam placement |
| --- | --- | --- | --- |
| A：判别 request | 一个行为入口；每个 caller 只见合法 variant | 三类 lifecycle projector 与共同 gate 都在 Runner | 现有 workflow → Runner seam 变窄，Scheduler 留在 implementation 后 |
| B：三个动词 | 单 caller 简单，但 module 总 interface 为三个入口 | lifecycle 仍集中，公共 subject/signature 重复 | 同一 seam 被切成三种调用形状 |
| C：注册 session | 对假想新命令开放，当前 caller 需学习 catalog/session | Role/deadline/projector 知识分散到 handlers | 增加没有真实第二类 adapter 的 registry seam |

最终选择方案 A，并吸收方案 B 的一项优点：三个 request variant 分别只包含合法字段，Check/Smoke 不含
duration。`host_target` 则放在 Runner 构造 interface，因为同一 CLI process 只执行一个真实宿主 target；
composition root 解析一次，测试显式注入，不由 Runner 隐式探测。

## 3. 目标 interface

### 3.1 Command-specific Cell operation

Runner 接收的是三个现有产品 module 的单 Cell interface，不是 lifecycle callback 或新 adapter：

```text
CheckCellOperations.check(
    *, package, cell, snapshot, source_plan
) -> CheckCellOutcome

SmokeCellOperations.verify(
    *, package, cell, snapshot, source_plan
) -> HighestVersionOutcome

CellSearchOperations.search(
    *, package, cell, snapshot, source_plan
) -> CellResult
```

以上名称复用现有 `CheckCellOperations`、`SmokeCellOperations` 与 `CellSearchOperations` Protocol；本迁移
可以移动其声明位置以闭合依赖方向，但不得新建等价的 singular Protocol 或第二套 operation seam。
`CompatibilityChecker`、`HighestVersionVerifier` 与 `SearchCoordinator` 分别满足以上 interface。它们是
in-process 产品 module，不命名为 Adapter，也不合并成一个 `run_cell` facade。R005 轨 C 以后可以调整
它们内部的评价依赖 seam，但不能改变本设计对三个产品角色分离的要求。

### 3.2 Request union 与 Runner

```text
CheckVerificationRun
  command: ClassVar[Literal["check"]] = "check"
  package: PackagePlan
  source_plan: SourcePlan
  snapshot: SourceSnapshot         # borrowed
  operation: CheckCellOperations
  jobs: positive int | auto

SmokeVerificationRun
  command: ClassVar[Literal["smoke"]] = "smoke"
  package: PackagePlan
  source_plan: SourcePlan
  snapshot: SourceSnapshot         # borrowed
  operation: SmokeCellOperations
  jobs: positive int | auto

SearchVerificationRun
  command: ClassVar[Literal["search"]] = "search"
  package: PackagePlan
  source_plan: SourcePlan
  snapshot: SourceSnapshot         # borrowed
  operation: CellSearchOperations
  jobs: positive int | auto
  max_duration_seconds: positive float | None

VerificationRun =
    CheckVerificationRun | SmokeVerificationRun | SearchVerificationRun

VerificationRunner(
    events: ActivityConsumer,
    logs: JournalStore | None,
    host_target: str,
    monotonic = time.monotonic,
)

VerificationRunner.run(CheckVerificationRun)
    -> tuple[CheckCellOutcome, ...]
VerificationRunner.run(SmokeVerificationRun)
    -> tuple[HighestVersionOutcome, ...]
VerificationRunner.run(SearchVerificationRun)
    -> tuple[CellResult, ...]
```

实现使用 typed overload 保留精确返回类型；运行期只按 request variant 判别，不再组合
`command: Literal + Generic[T] + object projector`。每个 `command` 是 `ClassVar` 类型判别，不进入 dataclass
实例字段或生成的构造器；caller 不能传入、更不能覆盖一个与 request variant 冲突的 command 值。

Request 是 invocation-local dataclass，不是 Pydantic Schema，不进入 report、Journal、identity、cache、
CLI context persistence 或生成物。`host_target` 是 Runner 的显式构造输入；生产 composition root 只解析
一次，测试可构造不同 Runner，不增加可变 setter 或 per-request override。

## 4. Run admission、Cell 集与执行顺序

Runner 在任何 operation 启动前按固定顺序完成：

1. 验证 `jobs`；Search duration 只接受正有限值或 `None`；
2. 由 request variant 决定 command，验证 `smoke=DEVELOPMENT`、`check/search=SEARCH`，并要求
   `source_plan.routes == package.source_routes`；
3. 从 `package.cells` 选择全部且仅有 `cell.target == host_target` 的 Cell，并验证规范 identity 唯一；
4. 从这一个 Cell 集投影并发布一次 `CellMatrixEvent`；
5. Check/Smoke 先执行一次 full-evaluation contract 检查，再让空 host Cell 集形成现行 typed
   `ConfigurationError`；Search 只在 Cell 集非空时检查一次 full contract，空集继续是合法 Run；
6. Search 空集不启动 operation，但仍完成空 Journal 的 final persistence并返回 `()`，让 Search workflow
   继续形成或更新带 `MISSING_CELL` 的 incomplete report；
7. 将所选 Cell 私有地翻译为 `ScheduledCellTask`，并调用 generic Scheduler；
8. Scheduler 只为已取得 worker slot、即将执行 operation 的 task 调用 Runner `on_started`；Runner 在该
   callback 中发布唯一的 initial `CellContextEvent(BaselineDetailIdentity)`。`on_started` 必须完成后才能
   执行该 task 的 operation，不能采用“先 `submit(task.run)`、后通知 started”的可竞态顺序；
9. 每个 completion 先做 command-specific typed projection，再合并并持久化 Journal/association，随后
   发布 `CellCompletedEvent`；Scheduler 结束后确认 final Journal，再返回规范排序的 typed outcomes。

`CellMatrixEvent` 的 Cell 集必须与 Scheduler task 集、completion `total` 和 Search deadline scope 完全相同。
返回 tuple 按现行 `cell_schedule_key` 规范排序；completion events 保留真实完成顺序。单 Cell 内的
Check two-phase Attempt、Highest evaluation 与 Search probe 仍串行并由各产品 operation 拥有。

Initial context 的迁移只移动每个已启动 Cell 的第一次 baseline 发布：Smoke workflow `_cell_task`、
`CompatibilityChecker.check()` 与 `SearchCoordinator.search()` 不再发布 `BaselineDetailIdentity`。
`CompatibilityChecker` 后续的 `DeclarationDetailIdentity`、`SearchCoordinator` 后续的 `detail=None` context
与 `SearchProbeDetailIdentity` 继续由原 operation 按现行阶段发布；`HighestVersionVerifier` 继续不发布
context。Scheduler deadline 留在 pending、从未启动的 Cell 不调用 `on_started`，因而不发布任何虚假
initial 或产品 context。每个已启动 Cell 必须恰有一个 initial baseline context，且它 happens-before 该
operation 发出的任何 context 或 stage event。

Full-evaluation contract 从 Check/Smoke workflow 及三个单 Cell operation 的重复入口删除。Runner 只验证
`test-command` 与 test dependency group 的 Run admission；它不解释 harness、执行 verifier 或决定
Failure disposition。Check/Smoke 保留“contract error 先于 no-host error”的现行顺序；Search 空集保留
无需启动完整评价、仍可写 incomplete report 的现行产品语义。集中 ownership 不强迫三个命令拥有相同的
空集结果。

## 5. Typed completion、Journal 与 Role projection

`completion_outcome(result: object)` 删除。Runner 使用三个 closed、implementation-private projector；
每个 projector 同时形成 `CellCompletedEvent.outcome`、Journal entries 与 Journal-side Process Log facts：

| Request | 合法 operation outcome | Journal Role / entry |
| --- | --- | --- |
| Check | `CheckCellOutcome` | 非 PASS 使用 outcome 的 `declaration-capture | declaration`、Attempt 与 FailureRecord；PASS 无 entry |
| Smoke | `HighestVersionPass | BaselineRejection | BaselineIndeterminate` | 两种非 PASS 固定 `baseline`、其 Attempt 与 FailureRecord；PASS 无 entry |
| Search | `CellResult` | highest Attempt 为 `baseline`；exact-vector Attempt 与 `CellFailureScope` 为 `probe`；全部 FailureRecord 保留 |

Search result 中出现 lowest-direct Attempt 是内部契约错误；Runner 不把未知 request/outcome/object 猜成
任意 completion。Projector 必须验证 outcome Cell 与当前 scheduled Cell 一致；错误类型或 Cell mismatch
在写 Journal 前失败，不产生虚构 Rejection/Indeterminate。

允许一个 implementation-private `_CellProjection(completion, entries, processes)` 在 Runner 内部连接 typed
projector 与 Journal gate，但 operation 不返回它，workflow/test 不导入它；它不是 §2.4 的公共 envelope。

Completion 与 presentation 分成三条闭合面：

1. **Run live：** `CellCompletedEvent.outcome` 是一次 Verification Run live activity 的唯一 completion
   projection；Terminal live 只消费该 event，不从 operation 或 command result 重建 completion；
2. **Run final：** Terminal 从 Check/Smoke command result 的 typed `outcomes`、Search report 的 typed
   `cell_results`，经 terminal-private、按命令闭合的 projector 形成 final presentation；它不要求 workflow
   缓存或重放 Runner event，也不调用 Runner 私有 projector；
3. **Explain / Evaluation：** Explain 与剩余 `SearchFailureEvent` 从 Evaluation/Failure 的 closed typed
   facts，经第三条 terminal-private projector 形成 presentation；它不接受任意 `object`，也不承担 Run
   completion、Journal 或 association authority。

三条路径不得抽回一个 shared public projector 或开放 `isinstance` 猜测入口。Terminal 的两类私有投影与
Runner live completion 必须对共同适用的 `kind/status/phase`、detail 及其 Failure source、process 及其
Failure source、Failure 集与 verification Role 保持语义相等，但“相等”不要求共享实现、对象 identity 或
易变的完整渲染文本。Rich、TTY/plain、stdout/stderr、文案与 exit decision 均不进入 Runner。

## 6. Deadline、durability 与 association

只有 `SearchVerificationRun` 能表达 total duration。Runner 从 request 的 package、snapshot、policy 与当前
Cell 构造 `CellFailureScope`；Scheduler deadline 到达时，未启动 Cell 形成
`TIMEOUT @ scheduler-deadline` 的 `CellIndeterminate`。Deadline 不是 exception，不建立 Attempt，不调用
Search operation，也不发布虚假 started/context。Check/Smoke interface 不含 duration 或 deadline policy。
当 `max_duration_seconds is None` 时，Runner 不为 Search task 安装 deadline-result callback，Scheduler 不
建立 deadline，也不进入 deadline classification。Runner 在本设计中只调用 `FailurePolicy` 分类
`TIMEOUT @ scheduler-deadline`；普通 operation outcome 的 Failure classification 继续由产品 operation
拥有，Runner 不用 FailurePolicy 重新解释它们。

每个 Cell 完成后的顺序保持：

```text
typed projection
-> merge buffered + terminal FailureRecord
-> write current complete Verification Journal
-> write journal:<run-id> Process Log associations
-> CellCompletedEvent(diagnose_available = true)
```

只有 Journal 与相应 Journal-side association 都成功时才宣称 diagnose available。`logs=None` 正常运行，
但所有 completion 都是 `diagnose_available=false`。持久化失败时仍发布该 Cell 的 false completion，停止
继续宣称本机诊断可用，并在 Scheduler 收尾后抛 `InfrastructureError`；final persist failure 也不能被
typed command result 吞掉。同一 Failure ID 对应冲突 portable entry 时 fail closed。

Runner 只写 `journal:<run-id>` association。Search 成功写入/更新 report 后的
`report_generation_id -> Process Log` replacement 继续由 Search workflow 执行；ReportStore、report
generation、removed Failure ID 与跨 host merge facts 不进入 Runner request。

## 7. Workflow 与 snapshot ownership

三个 workflow 继续拥有：

```text
project load
-> SourceSnapshot build
-> SourcePlan.for_package
-> VerificationRunner.run(command request)
-> typed command aggregation
```

Search 还继续拥有：

```text
post-run source drift check
-> report build/update
-> report-generation association replacement
```

SourceSnapshot 以 borrowed resource 进入 request，Runner 不关闭、materialize 或重建它。Workflow 继续以
`try/finally` 独占 snapshot lifecycle，因为 Search 在 Runner 返回后仍要读取 snapshot identity、执行 drift
check 并写 report；Runner/Journaling/operation 抛错时同一 `finally` 也必须关闭。把 close 收进 Runner 会
迫使 Search 接受资源转移、返回额外 envelope 或在关闭后继续使用 snapshot，都会扩大 interface。

Workflow 保留 status event、SourcePlan 构造、Check/Smoke typed aggregation 与 Search report result；删除
`selected_host_cells`、`_cell_matrix_event`、三个 `_cell_task`、Journal projector、Journal-side runtime
association projector 和 deadline scope assembly。Search workflow 为 report association 使用现有 typed
Failure/runtime facts，不向 Runner 回传 report ID。

`pf.project.host_target()` 保持唯一的宿主探测函数。生产 composition root 在构造共享 Runner 时调用它一次；
Runner 不隐式探测，request 不携带 override，三个 workflow 的构造 interface 删除 `host_target` 参数。
测试通过显式 host target 构造 Runner，不修改全局 platform facts，也不为 workflow 增加第二个注入点。

## 8. Module、seam 与依赖 ownership

| Owner | 本设计后的唯一职责 | 明确不吸收 |
| --- | --- | --- |
| `VerificationRunner` | Run admission、host Cell/matrix、每个已启动 Cell 唯一 initial baseline context、跨 Cell scheduling、Search deadline、typed live completion、Journal timing 与 Journal-side association | operation 后续 context、单 Cell产品算法、report、terminal、snapshot close、命令聚合 |
| `Scheduler` | generic task、worker、deadline callback、clock、started-before-operation 顺序、completion callback 与规范结果排序 | Cell outcome、context identity、Failure、Role、Journal、report、terminal |
| `CompatibilityChecker` | declaration-capture → declaration、baseline 传递、`DeclarationDetailIdentity` context、两次环境 lifecycle | initial baseline context、跨 Cell scheduling、Journal、report |
| `HighestVersionVerifier` | highest prepare、capture、full evaluation 与 close；不发布 context | command aggregation、Journal |
| `SearchCoordinator` | baseline、`detail=None`/probe context、candidate freeze、probe、coordinate search 与单 Cell terminal result | initial baseline context、跨 Cell deadline、report write、Journal durability |
| Check/Smoke workflow | load/snapshot、SourcePlan、status 与 typed result aggregation | host task assembly、Journal、deadline |
| Search workflow | load/snapshot/SourcePlan、drift、report build/update 与 report-side association replacement | Journal-side association、Scheduler |
| `TerminalPresenter` | live event consumption、按命令闭合的 Run final 投影、Evaluation/Failure 闭合投影、channel、final 与 exit semantics | Runner completion authority、Journal、domain identity、shared public projector |
| `FailurePolicy` | D005 Failure classification；Runner 仅使用 scheduler-deadline timeout classification | scheduling、Journal、terminal projection |
| `RunLogStore` | Journal/Diagnosis Index/Process Log storage、locator 与原子性 | Run policy、report authority |

Scheduling、operation、projection、FailurePolicy 与 clock 是 in-process dependencies。Scheduler 作为独立算法
module 保留在 Runner implementation 后面；它不是可由 caller 选择的 Adapter，也不导入领域类型。

Journal / Diagnosis Index 是 local-substitutable dependency：生产使用现有 RunLogStore adapter，测试优先在
真实临时目录上运行，并为 persist failure 使用窄的本地替身。保留 Runner 构造 seam 上最小的
`JournalStore` interface，不把 storage port 放入 request，不增加 repository、remote port 或通用 runtime。
ActivityConsumer 继续是现有进程内 seam；本设计不增加第二套 event bus。

## 9. Interface 原地替换

| 当前形状 | 目标形状 |
| --- | --- |
| `VerificationRun(command, package, plan, snapshot, tasks, jobs, duration)` | `CheckVerificationRun | SmokeVerificationRun | SearchVerificationRun` |
| public `VerificationTask(cell, execute, journal_entries, associations?, deadline_scope?)` | 删除；Runner 从 package/host/operation 私有装配 task |
| workflow `_cell_task` closure | request 携带现有产品 operation module |
| workflow 构造 Journal entry / runtime association | Runner 的 typed private projector |
| Search workflow 构造 scheduler deadline scope | Runner 从 Run facts构造 |
| workflow 选择 host Cell并投影 matrix | Runner 形成唯一 task/Cell/matrix 集 |
| workflow/operation 重复 full contract 检查 | Runner 在调度前检查一次 |
| Smoke workflow、Check/Search operation 分别发布 initial baseline context | Runner 经有序 `on_started` 为每个已启动 Cell 发布一次；后续产品 context 留在 operation |
| `completion_outcome(object)` export | Runner live、terminal Run final、terminal Evaluation/Failure 三条 closed private projection；无 shared public projector |
| 可由 request 构造器传入的 command 值 | request variant 的 `ClassVar[Literal[...]]`；构造器不接受 command |
| 三个 workflow 各自保存 `host_target` | 保留 `pf.project.host_target()`，composition root 探测一次并注入共享 Runner；workflow 删除该参数 |
| Search 无 duration 仍携带 deadline-result capability | `max_duration_seconds=None` 不安装 deadline callback；Runner 仅以 FailurePolicy 分类 scheduler deadline timeout |

PF 尚未发布。接受后以一次迁移替换旧 interface；不保留 `VerificationTask` alias、旧 `VerificationRun`
constructor、`completion_outcome` forwarding wrapper、old/new dual path 或只证明旧语法失败的交付测试。
临时切片内若需要桥接，只能存在于未提交状态。

## 10. Error contract

- 非正 `jobs`、非法 Search duration、request/operation outcome 类型或 outcome Cell mismatch 是内部
  interface violation，必须在可判定的最早时点以 `ValueError` / `TypeError` fail closed；
- SourcePlan command/mode 或 package/routes 不闭合时，在 operation 与 Journal 写入前失败；
- 缺少 `test-command` / test group，或 Check/Smoke 无 host Cell，继续形成 typed
  `ConfigurationError`；测试断言类型与稳定语义片段，不锁定 volatile 全文；
- Search 无 host Cell 是合法空 Run result，最终由 report coverage 表达 `MISSING_CELL`；
- operation 返回的 Rejection/Indeterminate 保留 D005 分类；未预期 exception 原样传播，Runner 不猜测
  为领域 disposition；
- deadline 的 CellIndeterminate、Journal conflict、Journal/Index persistence failure 按 §6 处理；
- Search drift、report build/update 与 report-side association failure 继续属于 Search workflow error 面；
- error 文案、CLI 通道和数值退出码默认不变；若实施发现必须改变产品可观察语义，先修订并重新接受
  D021，不在 Plan 或代码中暗改。

## 11. 测试与证据策略

测试以深化后的 interface 为表面，替换旧 task/helper tests，不叠加两套测试。

1. Runner request tests 分别用 Check、Smoke、Search operation module/substitute 调用唯一 `run(...)`，证明
   source mode/routes、同一 SourcePlan object、host-only 完整 Cell 集、规范返回顺序、精确 outcome type，
   并证明 request 构造器不能接收冲突 command；测试直接复用现有三个 Operations Protocol；
2. host selection tests 覆盖一个/多个/非 host/空 Cell；matrix cells、completion total 与实际 operation
   调用完全一致，并锁定 Check/Smoke error 与 Search empty/incomplete report 语义；
3. admission tests 证明 full contract 在 operation 前只由 Runner 验证；invalid contract 不启动 operation，
   并锁定 Check/Smoke 的 error ordering 与 Search 空集不触发评价契约检查的现行语义；
4. command projector tests 通过 `run(...)` 覆盖 Check capture/declaration、Smoke baseline、Search
   baseline/probe/Cell scope 的 PASS/Rejection/Indeterminate、detail、process 与 Journal Role；不直接调用
   私有 projector；
5. deterministic concurrency tests 使用 Barrier/Event 与 injected monotonic，证明 completion 的实际顺序、
   返回规范顺序、`on_started` happens-before operation、每个已启动 Cell 恰有一个 initial baseline context、
   Check declaration 与 Search `detail=None`/probe context 保持原顺序、Highest 不发 context、Search 未启动
   deadline Cell 无任何 context，且单 Cell 串行；不使用 wall-clock sleep；
6. 使用真实临时目录 RunLogStore 验证逐 Cell durable-before-diagnose、完整 Journal、Journal-side Process Log
   locator 与 final persist；窄 failing substitute 验证 false completion 后的 `InfrastructureError`；
7. workflow tests 继续从 Check/Smoke/Search public seam 覆盖 load/snapshot close、SourcePlan、status、typed
   aggregation、Search drift/report update/report-side association；不构造 `VerificationTask`；
8. terminal tests 分别穿过 public seam 证明：live 只消费 `CellCompletedEvent`；Check/Smoke/Search final 使用
   按命令闭合的 terminal-private projection；Explain 与剩余 Search failure 使用 Evaluation/Failure 闭合投影；
   三条路径共同适用的 kind/status/phase、detail/process Failure source、Failure 集与 Role 语义相等，
   TTY/plain、channel 与 exactly-one-final 不变；
   测试不导入 `completion_outcome(object)` 或任何替代的 private projector；
9. Scheduler tests 继续只覆盖 generic task/concurrency/deadline/clock/order，静态审计证明它不导入
   Evaluation、Failure、Role、Journal、CellResult 或 terminal facts；Runner public-seam tests 与 ownership
   scan 共同证明 Search duration `None` 不进入 deadline callback/classification 路径。

旧 duplicate/outside `VerificationTask`、unknown-object projector 与 workflow `_cell_task` tests 被目标
interface 消除，迁移为上述正向 current-contract tests；不保留枚举obsolete构造器或 helper 的 negative
tests。迁移期可以用 `rg` 证明旧路径删除，但交付测试不把 private 名称当 contract。

当前直接 import `completion_outcome` 的 `test_verification.py`、`test_evaluation.py` 与
`test_search_coordinator.py` 用例必须替换：Runner 语义穿过 `run(...)` 与 Activity public seam 断言；
Evaluation/Failure 语义穿过其公共 facts 或 terminal 公共 seam 断言。不得为保留旧测试而导出新的公共
projector，也不得用私有常量、helper 或完整输出 snapshot 代替公共行为。

Focused suites 至少覆盖：

```text
tests/test_verification.py
tests/test_scheduling.py
tests/test_check.py
tests/test_smoke.py
tests/test_search_workflow.py
tests/test_search_coordinator.py
tests/test_evaluation.py
tests/test_terminal.py
tests/test_runlog.py
tests/test_cli.py
```

对应 P027 必须逐项预留并回填 focused tests、Ruff、ty、Python 3.10 coverage/full suite、顺序 Python
3.11/3.12 full suites、build、文档 link/diff 与旧 interface/ownership scan。Collection、focused pass、单一
Python 版本或未通过 coverage gate 的 test run 都不能替代完整证据。

## 12. 非目标

- 不改变 CLI grammar、command request、exit code、状态聚合或终端措辞；
- 不把 project load、SourceSnapshot build/close、SourcePlan 构造或 Search post-run drift 收入 Runner；
- 不把 Check、Highest 与 Search 合并为一个评价 facade 或统一 outcome；
- 不实施 R005 轨 C 的 env/static/full Protocol 合并或 `SearchCoordinator` 测试表面整改；
- 不实施 R005 轨 D 的 terminal-private result-card emitter；
- 不把 Scheduler 私有化到无法独立测试，也不让 Scheduler 导入领域类型；
- 不把 report build/write/update、generation association、merge/apply authority 收入 Runner；
- 不改变 D003 搜索算法、D004 static/witness、D005 classification、D012 harness 或 adapter policy；
- 不改变 Schema 1、Verification Journal v2、Diagnosis Index、SourcePlan/Attempt/Failure/report identity 或
  任何生成物；
- 不引入 generic workflow/planning layer、operation registry、public projector policy、event bus、repository、
  DI framework、daemon 或 remote port；
- 不为旧 request/task/projector 增加 alias、兼容 Adapter、fallback 或双轨测试。

## 13. 临时设计生命周期与 owner 归并

本设计只有以下生命周期：

1. **草案、待接受：** 当前 D021 只记录目标选择；现行 D001/D002/D005–D008/D012/D014 与当前代码
   继续是行为 owner，不创建 P027，不编辑 production code；
2. **已接受、待实施：** 接受 D021 后建立 durable P027，把 §14 全部验收标准映射到有序切片、
   interface/ownership migration、tests、文档与 evidence slots；
3. **实施中：** 按 P027 原地替换 interface，并持续记录 actions、decisions/deviations、结论及精确命令结果；
4. **完成、归档：** 只有实现、全量证据与逐项验收闭合后，才在同一完成变更中归并 owner、回写 R005、
   更新索引，并同时归档 D021/P027。

完成变更必须把稳定规则归并到：

| 现行 owner | 吸收或核对内容 |
| --- | --- |
| D002 | command-discriminated request interface、operation seam、workflow/Runner/Scheduler依赖方向与 snapshot ownership |
| D008 | Run admission、host Cell/matrix、initial context、deadline、typed completion、Role/Journal/association 时序与空集语义；把 §5 的全局“唯一投影路径”改述为 Runner Run-live、terminal Run-final、terminal Explain/Evaluation 三条 closed projection，并要求它们从 retained diagnostics/Evaluation 形成语义相等且绑定 Failure source 的 detail，不从序列化 status 猜测 |
| D001 | 核对 command/full-evaluation/report 产品行为；只有目标契约确实改变时修订 |
| D005 | 核对 Failure classification、scope 与 disposition 未变 |
| D006 | 核对 Activity/terminal presentation 与 exit semantics 未变；移除对开放 projector 的结构引用 |
| D007 | 核对 ProcessObservation、Process Log 与 Journal-side locator authority 未变 |
| D012 | 核对同一 SourcePlan/Attempt/harness chain 未变 |
| D014 | 核对 Search report、wire、generation 与 report-side association authority 未变 |

归档动作必须同时完成：

- 将 D021 移到 `docs/archived/designs/`，状态改为“已完成并归档”，补完成日期、P027 与实现提交；
- 将 P027 移到 `docs/archived/plans/`，保留最终 acceptance/evidence matrix；
- 更新 `docs/README.md` 与 `docs/archived/README.md`，不让归档 Design 继续充当现行 owner；
- 在 R005 中把轨 B 标为已由 D021/P027 解决；轨 C/D 仍开放时不归档 R005；
- 复查所有相对链接、Design/Plan/R005 状态、owner 条款与当前实现一致。

## 14. 验收标准

1. `VerificationRunner` 只暴露一个 `run(VerificationRun)` 行为入口；`VerificationRun` 是 Check、Smoke、
   Search 三个 frozen request 的 closed union，复用现有 `CheckCellOperations`、`SmokeCellOperations`、
   `CellSearchOperations` seam。Variant 以不进入构造器的 `ClassVar` 固定 command、合法 duration 字段与精确
   返回 type，不进入 Schema/report/Journal/identity/cache，也不存在平行的 singular operation Protocol。
2. `pf.project.host_target()` 继续是唯一宿主探测函数；production composition root 只调用一次并把结果注入
   Runner，三个 workflow 删除 `host_target` 构造参数。每个 Run 从 `package.cells` 选择完整 host Cell
   集，且该集合与一次 `CellMatrixEvent`、Scheduler tasks、completion total、deadline scope 和规范返回集合
   完全一致。Runner 不隐式探测，request 不携带 override，workflow 不选择或提交 Cell/task。
3. Runner 在 operation 前精确闭合 command→SourcePlan mode、package/routes、jobs/duration；
   full-evaluation contract 只由 Runner 验证：Check/Smoke 在空集判定前验证，Search 仅在非空时验证。
   同一 SourcePlan object、PackagePlan 与 borrowed SourceSnapshot 传给全部 Cell operation。Check/Smoke
   contract→空集 typed error 的顺序、Search 空集不启动评价且继续形成 incomplete report 的现行语义由
   public tests 锁定。
4. public `VerificationTask`、workflow per-Cell execute closure、Journal/runtime-association callback 与
   deadline scope 全部删除；Runner 只在 implementation 内建立 `ScheduledCellTask` 与 deadline callback，
   不存在 alias、generic envelope、registry 或等宽 facade。
5. 每个真正启动的 Cell 仅由 Runner 经 Scheduler `on_started` 发布一次 initial
   `CellContextEvent(BaselineDetailIdentity)`，且 callback happens-before operation；Smoke workflow、
   `CompatibilityChecker` 与 `SearchCoordinator` 的原 initial baseline 发布点删除。Check declaration、Search
   `detail=None`/probe context 留在原 operation，`HighestVersionVerifier` 继续不发 context，未启动 deadline
   Cell 不发 context。`completion_outcome(object)` 及其 production/test imports 删除；Runner 的三个 private
   typed projector 拒绝 outcome family/Cell mismatch，并唯一形成 Run live completion、Journal entries 与
   Journal-side process facts。Run live 只读 `CellCompletedEvent`；Run final 使用按命令闭合的 terminal-private
   投影；Explain 与剩余 Search failure 使用 Evaluation/Failure terminal-private 投影。三者共同稳定语义相等，
   但不共享 public projector，Terminal 不调用 Runner 私有函数，现有 live/final/report 展示语义不变。
6. Check 的 declaration-capture/declaration、Smoke 的 baseline、Search 的 baseline/probe/Cell scope Role 与
   Attempt/FailureRecord 精确进入 Journal；Search 中 lowest-direct 或冲突 Failure ID fail closed。PASS 不
   生成 Failure entry，runtime-only detail 不进入 Journal/report/identity。
7. Search duration 只存在于 Search request；未启动 deadline Cell 形成 D005 合法的
   `TIMEOUT @ scheduler-deadline` CellIndeterminate、probe Journal entry 与 completion，不启动 operation、
   不建立 Attempt、不发布虚假 started/context。`max_duration_seconds=None` 不安装 deadline-result callback；
   Runner 只用 FailurePolicy 分类该 scheduler-deadline timeout，不重新分类普通 operation outcome；Check/Smoke
   无 deadline interface。
8. Journal 写入与 `journal:<run-id>` Process Log association durable 后才发布
   `diagnose_available=true`；`logs=None` 永远 false；persist failure 先发布 false completion，最终抛
   `InfrastructureError`。返回 outcome 顺序规范且 completion 保留实际并发顺序。
9. `CompatibilityChecker`、`HighestVersionVerifier`、`SearchCoordinator`、Scheduler、workflow、
   TerminalPresenter 与 RunLogStore 保持 §8 ownership；Scheduler 无领域 import，Runner 不拥有单 Cell产品
   算法、snapshot lifecycle、typed command aggregation、report 或 terminal facts。
10. Search workflow 在 Runner 返回后继续完成 source drift、report build/update 与
    report-generation association replacement；SourceSnapshot 无论成功、operation/Journal failure 或 report
    failure都由 workflow `finally` 关闭。CLI、exit、Schema 1、Journal v2、Diagnosis Index 与全部 identity
    形状无变化，生成物无 drift。
11. §11 的 Runner、workflow、concurrency/deadline、Journal、terminal 与 Scheduler public-seam测试全部
    通过；旧 shallow tests 被替换，不断言 private projector/task/helper，不以 obsolete compatibility
    negative tests 充当 current contract。
12. P027 的 acceptance/evidence matrix 逐项闭合，适用的 focused、Ruff、ty、coverage、Python
    3.10/3.11/3.12 顺序全量、build、generated-artifact、link/diff 与 ownership/删除扫描均有精确结果；
    D002/D008 稳定规则已归并，其他 owner 已核对，R005/索引与 D021/P027 同步归档状态一致。

# R002 — PF v1 架构评审

- **状态：** 快照
- **日期：** 2026-08-22
- **性质：** 非规范性评审；不定义命令、算法、Schema 或 module interface
- **对照：** 当前 `main` / `af10d0c`（`test: fix ty diagnostics in test suite`）
- **契约所有者：** [D001](../designs/D001-pf.md)–[D009](../designs/D009-pf-v1-refactor.md)
- **前序评审：** [R001](R001-pf-v1-review.md)；其整改结果见 [D009](../designs/D009-pf-v1-refactor.md) 与 [P008](../plans/P008-pf-v1-refactor.md)
- **整改：** [D010](../designs/D010-pf-v1-architecture.md) / [P009](../plans/P009-pf-v1-architecture.md)

本文评审 D009 落地后的 PF v1 架构，回答「现行安全与证据契约已经收紧后，下一步哪些 module 值得继续加深」。它不重复 R001 已经发现并由 D009 整改的流式脱敏、artifact 绑定、complete authority、workspace apply transaction、canonical package identity、离线 discovery 和 Journal identity 问题，也不把建议自动提升为现行契约。

评审使用 `module`、`interface`、`implementation`、`seam` 和 `adapter` 描述结构。`depth` 指调用方通过较小 interface 获得较多行为，而不是实现文件的行数。文件较长本身不构成拆分理由；只有规则所有权交叉、interface 暴露非法状态、真实 adapter 已经分化或测试必须穿透 interface 时，才建议调整 seam。

## 1. 结论

PF 当前是依赖方向清晰的模块化单体。静态检查 `src/pf` 的内部 import graph 未发现跨 module 循环；`cli.build_context` 是唯一生产 composition root，Project planning、Snapshot、Evaluation、CoordinateSearch、Report authority、Apply transaction 和外部工具 adapter 均有明确所有者。D009 已经消除了 R001 中优先于纯重构的高风险契约缺口。

本次未发现需要阻断 v1 的新 P0 架构缺口。下一轮优化的最高收益集中在四处：

1. `EnvironmentFactory.prepare` 用可组合参数表达互斥的 resolution 意图，调用方仍需了解隐藏优先级；
2. `Scheduler` 同时拥有并发、deadline failure 和领域结果到 ProgressEvent 的投影，越过了 scheduling seam；
3. `ProgressEvent` 用 `completed == 0` 表示阶段事件，以可空字段组合隐含状态；
4. 调度排序当前直接委托给 `cell_identity`，没有守住 identity 与 order 的所有权分离。

后续 P2/P3 工作主要改善 locality：收拢 RunLogStore 的平台 implementation、继续拆 TerminalPresenter 的内部视图、消除 `_ProposalRunner` 的调用顺序约束，并让 production composition 的非法状态不可表示。

当前仓库约 14,488 行源码、18,890 行测试，31 个测试文件包含 385 个测试函数；参数化后 `pytest --collect-only` 收集 592 个用例。这些规模数据只用于定位维护热点，不用于衡量 module depth。

## 2. P1：Resolution interface 表达了非法组合

### 2.1 现状

`src/pf/environment.py:144-166` 的 `EnvironmentFactory.prepare` 同时接收：

```text
resolution: highest | lowest-direct
managed_vector: VersionPin[] | None
selection: SelectedCandidate[] | None
```

实现禁止 `managed_vector + selection`，在收到 selection 后再投影 managed vector，并据此把 Attempt 的 requested resolution 隐式改为 `exact-vector`。`src/pf/search.py:349-366` 因此以 `resolution="highest" + selection` 请求一次实际语义为 exact-vector 的 probe。调用方必须知道参数优先级、Attempt 推导和 UvAdapter 对 selection 的特殊解释，才能正确使用该 interface。

### 2.2 影响

- `highest`、`lowest-direct` 和 `exact-vector` 是互斥意图，却可以被组合参数表达成矛盾状态；
- EnvironmentFactory、Search 和 UvAdapter 分别知道一部分 resolution 规则，降低 locality；
- 新调用方可以通过类型检查但构造出只在运行期被拒绝或被重新解释的请求；
- 测试需要覆盖参数组合，而不是直接覆盖三个领域变体。

### 2.3 建议

使用单一判别请求作为 interface：

```python
ResolutionRequest = HighestResolution | LowestDirectResolution | ExactSelection

prepare(
    *,
    package: PackagePlan,
    cell: Cell,
    snapshot: SourceSnapshot,
    resolution: ResolutionRequest,
) -> PreparedEnvironment | PrepareFailure
```

`ExactSelection` 只携带冻结的 artifact selection；managed vector 由 EnvironmentFactory 在 implementation 内投影。UvAdapter 消费同一解析后的安装意图，不再同时接收 resolution 和可空 selection。不要为三个变体增加三个 public method；一个判别参数能保持更小的 interface。

**完成标准：** 类型层面无法表达 `highest + selection`、`lowest-direct + managed_vector` 或 `managed_vector + selection`；Attempt identity、实际安装策略和 Proposal managed vector 由同一变体导出。

## 3. P1：VerificationRunner 尚未完全隐藏 scheduling

### 3.1 现状

`src/pf/scheduling.py:10-30` 导入 FailurePolicy、Check/Baseline/Evaluation outcome、ProcessResult、TyDiagnostic 和三种 CellResult。`Scheduler.run` 除限制并发、处理 deadline 和规范排序外，还负责：

- 为所有 cell 构造 `phase="start"` 的 ProgressEvent；
- 为未启动 cell 构造 `TIMEOUT / INDETERMINATE` FailureRecord；
- 在 `src/pf/scheduling.py:192-350` 把所有领域结果投影为 diagnostics、process、failure、detail 和 verification role；
- 读取 `ProcessResult.diagnostic()`，形成展示所需的文本事实。

与此同时，`src/pf/verification.py:60-103` 已声明 VerificationRunner 拥有 scheduling、completion 和 durable Journal 时序，但它仍把关键 completion 语义委托给 Scheduler。任何新 outcome 变体都会同时修改 Schema、Scheduler、VerificationRunner 和 Terminal 测试。

### 3.2 建议

继续加深 VerificationRunner：

```text
VerificationRunner.run(VerificationRun)
  ├── 验证 run/task identity
  ├── 调用内部 scheduler implementation
  ├── 构造 deadline outcome
  ├── 把 outcome 投影成 completion event
  ├── 在 completion 前持久化 Journal
  └── 返回规范排序的 outcomes
```

Scheduler 可以保留在独立文件中，但应成为 Runner 的内部 implementation，只理解 task、worker、deadline、started/completed callback 和顺序。它不应导入 Evaluation、FailureRecord、CellResult 或终端诊断事实。当前只有一个生产 Scheduler，没有第二个真实 adapter；composition root 无需把它作为可替换依赖暴露给 VerificationRunner。

**完成标准：** `scheduling.py` 不导入 `pf.failure`、`schemas.evaluation` 的领域 outcome 或 `schemas.report`；删除 Scheduler 后，并发复杂度只会回到 VerificationRunner implementation，不会扩散到三个 command workflow。

## 4. P1：Activity event 需要判别状态

### 4.1 现状

`src/pf/schemas/evaluation.py:942-955` 的 ProgressEvent 同时表达 cell 阶段和 cell 完成：

```text
completed == 0  -> 阶段或初始事件
completed > 0   -> 完成事件
```

同一 Schema 还包含 detail、diagnostics、process、failure、verification_role、stage 和 diagnose_available 等可空或带默认值字段。`src/pf/verification.py:139-181` 必须用 `completed == 0` 决定是否进入 Journal gate，TerminalPresenter 也必须用同一隐式规则选择 live 行为。`package` 与 `cell.package` 重复保存，但 Schema 没有强制二者相等。

Scheduler 在真正提交 task 之前为所有 tasks 发出 `phase="start"`；被 deadline 阻止的 task 因而也曾被表示为 started。CellMatrixEvent 已经拥有初始 cell 集合，这个事件的语义并不准确。

### 4.2 建议

使用带 discriminator 的 event union：

```python
CellStageEvent(kind="stage", cell=..., stage=...)
CellCompletedEvent(
    kind="completed",
    cell=...,
    completed=...,
    total=...,
    outcome=...,
    diagnose_available=...,
)
```

初始 cell 集合继续由 CellMatrixEvent 表达。若产品确实需要区分 queued 与 started，再增加显式变体，不能复用阶段字符串。package 从 `cell.package` 获取，不另存重复身份。

Completion outcome 可以是一个只包含终端所需机械事实的判别联合；它由 VerificationRunner 一次构造，TerminalPresenter 只消费，不重新从任意领域结果猜测。不要把 Rich renderable 或文案放进 event Schema。

**完成标准：** Journal gate 不再检查数值哨兵；每个 event 变体只有自身合法字段；未实际提交的 deadline cell 不会产生 started 事实。

## 5. P1：Cell identity 与 scheduling order 仍然耦合

`src/pf/scheduling.py:33-34` 的 `cell_schedule_key` 直接返回 `cell_identity(cell)`。虽然当前 identity 与 scheduling order 都是 `(package, target, python, extras)`，D009 §5.1 和 §13 已明确：identity 只拥有 equality / lookup，调度、展示和报告排序各自拥有显式 order key。

建议让 scheduling module 直接写出自己的顺序：

```python
return (
    cell.package,
    cell.target,
    cell.python_minor,
    cell.extra_surface,
)
```

**完成标准：** 修改 `cell_identity` 的表示不会自动改变 scheduling order；测试分别锁定 identity 与调度排序。这是低成本修复，应先于较大的 Runner/event 重构落地。

## 6. P2：RunLogStore 的平台 implementation 应进入私有 seam

### 6.1 现状

RunLogStore 同时保存 Process Log、Verification Journal 和 diagnosis index 是合理的深 module：三者共享 run identity、安全目录和 failure locator，拆成三个 public store 只会复制安全不变量。

剩余问题在 implementation。`src/pf/runlog.py:89-100`、`:146-157`、`:221-270`、`:321-390` 等流程反复判断 POSIX secure `dir_fd` 或 WindowsRunDirectory；index read/update/lookup 又分别维护 POSIX 与 Windows 路径。平台差异穿过日志、Journal 和 locator 语义，增加安全审计面。

### 6.2 建议

建立只对 RunLogStore 可见的安全目录 seam：

```text
SecureLogDirectory
  ├── PosixDirectoryAdapter
  └── WindowsDirectoryAdapter
```

它只隐藏安全目录打开、原子私有写、有界读取、regular-file/identity 验证和 close。RunLogStore 继续唯一拥有日志格式、Journal、diagnosis-index JSON、failure association 和 locator 规则。

这里有 POSIX 与 Windows 两个真实 adapter，建立 seam 有实际变化来源。不要把它扩大为通用 filesystem、repository 或项目级存储层。

**完成标准：** RunLogStore 的产品流程不包含平台条件分支；平台测试直接覆盖各 adapter 的安全 interface，RunLogStore 测试只覆盖日志、Journal 和 index 行为。

## 7. P2：TerminalPresenter 需要内部视图，而不是新的 public interface

### 7.1 现状

TerminalPresenter 的 `render_X(result) -> exit_code` 对 CLI 具有较高 leverage，应保持不变。其内部仍同时拥有：

- command 最终摘要；
- Rich live Progress 生命周期；
- setup card、pending outcome 和 cell task 状态；
- SearchFailureEvent 聚合；
- outcome / FailureRecord / TyDiagnostic 到 cell 卡片的转换；
- Process Log 链接。

`src/pf/terminal/__init__.py:921-1038` 的 `_print_cell_report` / `_cell_result_lines` 通过十多个可选参数表达展示状态；check、smoke、search 和 live completion 又各自重复 outcome 到展示事实的转换。

### 7.2 建议

在 `pf.terminal` 包内提取两个私有 module：

```text
CellPresentation
  由 command result 或 completion event 生成一个合法 cell 视图

LiveVerificationView
  consume(event) / close()
  拥有 Rich Progress、task order 与 pending setup 状态
```

TerminalPresenter 保留 public `render_X`、stdout/stderr routing 和最终 exit code；私有 CellPresentation 不进入公共 Pydantic Schema。测试继续通过 `render_X` / `consume` interface 断言输出，不直接测试内部视图字段。

**完成标准：** `_print_cell_report` 不再接收可任意组合的十多个参数；同一种 outcome 到展示事实只有一个转换入口；D006 的布局、颜色、通道和文案不变。

## 8. P2：ProposalRunner interface 暴露调用历史

`src/pf/search.py:267-318` 的 `_ProposalRunner` 通过 `evaluate_full(vector)` 返回 ProbeEvidence，再要求调用方以 `full_evaluation(vector)` 查询刚才的 Evaluation。`src/pf/search.py:650-656` 和 `:723-729` 都依赖「先 evaluate、再 lookup」的顺序；lookup 返回 `None`，正确性取决于调用历史和多个平行 dict。

建议一次返回不可变结果：

```python
ProbeRun(
    evidence: ProbeEvidence,
    evaluation: Evaluation | None,
)
```

CoordinateSearch 的 VectorEvaluator adapter 只取 `ProbeRun.evidence`；SearchCoordinator 在 fast path 和 final vector 上同时取得 evidence 与 Evaluation。ProposalRunner 可以同时改为 context manager，集中关闭 PreparedEnvironment，并删除只为二次 lookup 存在的状态。

**完成标准：** SearchCoordinator 不再调用 `full_evaluation(vector)`；取得最终 Evaluation 不依赖先前调用顺序；同一 full Evaluation context 仍最多执行一次完整测试。

## 9. P3：Production composition 仍允许不完整状态

`src/pf/cli.py:84-94` 的 CliContext 只有 check_workflow 和 presenter 必填，其余 production workflow 与 RunLogStore 都可空；每个 handler 因此保留 production composition 不应触发的 `workflow is not assembled` 分支。现行可空性主要服务只装配一个 command 的 CLI 测试。

`src/pf/snapshot.py:59-64` 的 SnapshotBuilder 也会在 runner 缺失时自行创建 SubprocessRunner。临时非 Git 测试因此方便，但 Git 调用可能绕过 production composition 的 listener、Process Log 和 SecretRedactor。

建议：

- CliContext 要求全部 production workflows，并集中拥有 `close()` 或 context-manager 生命周期；
- CLI 测试 fixture 提供显式 NeverCalled adapter，不让测试便利制造 production 非法状态；
- SnapshotBuilder 的 production 构造显式接收 ProcessRunner；若保留无进程的测试构造，应使 Git manifest 能力在类型或命名上清晰可见。

**完成标准：** `build_context()` 构造的类型不允许缺少任一命令 workflow；资源关闭顺序由一个 interface 拥有；SnapshotBuilder 不在调用方不知情时创建外部进程 adapter。

## 10. P3：Deadline 测试需要确定性时间 seam

`src/pf/scheduling.py:71-74`、`:170-172` 直接调用 `time.monotonic()`；`tests/test_scheduling.py` 使用真实 `time.sleep()` 制造并发与 deadline。当前用例很短，但负载较高或计时粒度不同的平台会放大偶发失败风险。

在 VerificationRunner / scheduler ownership 调整后，可以把 `monotonic` 作为内部 callable 或 Clock adapter 注入并发 implementation；并发上限用 Barrier/Event 验证，deadline 用 fake clock 验证。这个 seam 只用于时间这一真实变化来源，不应扩展成通用 runtime abstraction。

**完成标准：** deadline 测试不依赖 wall-clock sleep；生产仍使用 `time.monotonic`；并发调度语义不进入 public VerificationRun interface。

## 11. 建议顺序

| 优先级 | 项 | 原因 | 完成证据 |
| --- | --- | --- | --- |
| P1 | 显式 scheduling order | 独立、小改动，立即恢复规则所有权 | identity 与 order 独立测试 |
| P1 | ResolutionRequest | 消除核心 prepare interface 的非法状态 | 三个判别变体与 Attempt/install 一致性测试 |
| P1 | VerificationRunner + event union | 同一改动解决 scheduling 越界和 sentinel event | Runner interface 测试；Scheduler 不导入领域 outcome |
| P2 | ProbeRun 返回值 | 消除 search 调用历史约束 | fast/dynamic final 不再二次 lookup |
| P2 | SecureLogDirectory | 收拢两个真实平台 adapter | POSIX/Windows adapter 契约测试 |
| P2 | Terminal 内部视图 | 提高展示 locality，不改变 D006 | 现有 terminal interface 测试保持 |
| P3 | Composition 完整性 | 让 production 非法状态不可表示 | CliContext / SnapshotBuilder 构造测试 |
| P3 | Clock seam | 移除真实 sleep 的不确定性 | fake clock + Barrier/Event 测试 |

建议先为前三项建立新的 Design 所有者，再写对应 Plan；它们会改变 D002/D008/D009 当前描述的 module interface 或 event Schema。P2/P3 若只调整私有 implementation 且不改变现行契约，可以在同一 Design 中明确为内部重构，不应由本 Review 直接取得规范性地位。

## 12. 已经足够深，不建议先动

- `ProjectDiscovery.discover` 与 `ProjectLoader.load` 已把离线定位和完整 planning 分离；不要重新合并，也不需要通用 repository；
- `CoordinateSearch.minimize` 是纯向量 interface，调用私有状态支持重入；不要把环境、artifact 或 FailurePolicy 引入算法 module；
- `SearchCoordinator.search` 隐藏单 cell baseline、静态 fast path 和 dynamic fixpoint，保持深 module 方向；
- `FailurePolicy.classify_evaluation` 已集中 Evaluation 到 disposition 的机械映射，不应回散到 workflow 或 adapter；
- `PackageFloorReportV1` validators、PackageReportBuilder 和 ProjectEditor 已形成完整 authority 链，不要因 Schema 文件较长而拆散证据不变量；
- `ReportStore` 的 read/write/update/merge 共同拥有 package-floor 文档生命周期，拆成多个浅 store 不会增加 leverage；
- consumer-owned Protocol 应继续保持窄 interface，不应合成统一 Environment/Evaluator port；
- 不需要 DI 框架、event bus、通用 filesystem、跨运行 cache 或常驻 daemon。

## 13. 验证范围

本次完成：

- 对照当前 `af10d0c` 阅读 D002、D009、R001 与上述源码；
- 静态检查 `src/pf` 内部 import graph，未发现跨 module 循环；
- 统计源码与测试规模；
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest --collect-only -q --no-testmon -p no:cacheprovider`：592 tests collected；
- `git diff --check`：通过。

本次没有执行全量 pytest、`ty check src` 或 build；R002 是架构快照，不把测试收集冒充行为验证。D009 落地时的全量执行证据见 P008，当前 CI 命令仍以 `.github/workflows/ci.yml` 为准。

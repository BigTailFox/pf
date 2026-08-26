# PF v1 架构加深

- **状态：** 现行
- **日期：** 2026-08-22
- **最后核对：** 2026-08-23
- **来源：** [R002](../reviews/R002-pf-v1-architecture-review.md)（`af10d0c` 快照）
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **CLI 展示：** [D006](D006-pf-cli-enhancement.md)
- **验证运行：** [D008](D008-pf-verification-run.md)
- **前序重构：** [D009](D009-pf-v1-refactor.md)
- **现行搜索决策：** [D011](D011-pf-runtime-backed-static-search.md)

本文是 R002 全部架构改进的规范性所有者。它消除 resolution、activity event 与 production composition 中可表示的非法状态，继续加深 VerificationRunner，并把平台存储、终端 live 状态和搜索运行历史收回私有 implementation。本文不增加命令，不改变 failure 分类、报告授权、安全约束或终端文案。D011 后续取代了本文当时依赖的 static/dynamic 双阶段搜索；当前 `_ProposalRunner` 生命周期保留，probe 顺序以 D003 为准。

R002 是非规范性快照。本文已经落地；D002 已同步当前 module interface，D006、D008、D009 中与本文冲突的历史描述由 §13 明确取代。

## 1. 问题

D009 已经建立了正确的模块方向，但四个 interface 仍允许调用方知道或构造 implementation 状态。

1. `EnvironmentFactory.prepare` 用 `resolution + managed_vector + selection` 表达三个互斥意图，exact selection 仍伪装成 `highest`。
2. Scheduler 同时拥有并发、deadline、领域 failure 和 outcome → terminal facts 投影；VerificationRunner 没有完全隐藏 scheduling。
3. `ProgressEvent` 用 `completed == 0` 区分阶段与完成，并保存重复 package identity 和互不约束的可空字段。
4. `CliContext`、`SnapshotBuilder` 和 `_ProposalRunner` 的正确使用依赖调用历史、可空 production 依赖或隐藏 fallback。

另外两处 implementation 缺少 locality：RunLogStore 的 POSIX/Windows 分支穿过日志、Journal 和 index 流程；TerminalPresenter 的合法 cell 视图与 Rich live 生命周期仍混在 public presenter 中。

## 2. 目标与非目标

### 2.1 目标

- 一个判别 `ResolutionRequest` 同时决定 Attempt identity、项目安装策略和 Proposal managed vector；
- scheduling order 与 Cell identity 各有唯一所有者；
- VerificationRunner 唯一拥有 deadline outcome、领域 completion 投影、Journal gate 和 completion 发布；
- Scheduler 只理解 task、worker、deadline、callback 和显式 order，不导入领域 outcome；
- activity event 以 discriminator 表达 stage 与 completed，不能用数值哨兵构造混合状态；
- `_ProposalRunner.evaluate_full` 一次返回 evidence 与 Evaluation，不再要求二次 lookup；
- RunLogStore 只拥有产品格式与关联语义，平台安全原语进入两个私有 adapter；
- TerminalPresenter 的 public `render_X(result) -> exit_code` 不变，合法 cell presentation 与 live state 进入包内私有 module；
- production `CliContext` 必然完整，并由一个 interface 关闭资源；SnapshotBuilder 不隐式创建外部进程 adapter；
- deadline 与并发测试不依赖 wall-clock sleep。

### 2.2 非目标

- 不改变 D001 命令、参数、退出码、floor 或 apply 语义；
- 不在本文重新定义 D003 的 probe 顺序、hint 与单调性；后续取代见 D011；
- 不改变 D005/D008 的 cause、disposition、Verification Role、Journal 内容或写入授权；
- 不改变 D006 的布局、颜色、stdout/stderr routing、文案或卡片信息层级；
- 不拆分 RunLogStore 的 Process Log、Journal 和 diagnosis index 产品 interface；
- 不把 SecureLogDirectory 扩大为通用 filesystem/repository；
- 不引入 DI 框架、event bus、通用 runtime 或公共 presentation Schema；
- 不新增 Scheduler adapter；当前只有一个生产并发 implementation。

## 3. 实施范围与顺序

| 顺序 | 改进 | 完成标准 |
| --- | --- | --- |
| 1 | 显式 scheduling order | `cell_schedule_key` 写出自身字段；identity 改动不影响 order 测试 |
| 2 | `ResolutionRequest` | 三种意图类型互斥；Attempt/install/Proposal 由同一变体导出 |
| 3 | Runner + event union + Clock | Scheduler 无领域 outcome import；stage/completed 判别；deadline 无 sleep |
| 4 | `ProbeRun` | final Evaluation 不依赖先 evaluate 再 lookup；full context 最多执行一次 |
| 5 | `SecureLogDirectory` | RunLogStore 产品流程无平台分支；两个 adapter 直接测试 |
| 6 | Terminal 私有视图 | cell facts 单一转换入口；`_print_cell_report` 不再接收自由组合参数 |
| 7 | production composition | 所有 workflow 与资源必填；context 唯一关闭；snapshot 无隐藏 runner |
| 8 | 同步所有者与全量门禁 | D002/索引同步；Ruff、ty、pytest、build 与安装集成通过 |

顺序 1 是后续 Scheduler 改造的独立护栏；2 先消除调用方非法状态；3 建立新的完成事件后，6 才迁移 live presentation。SecureLogDirectory 与 ProbeRun 可在 3 之后独立实施。production composition 最后收紧，避免中间切片为测试继续制造可空状态。

## 4. Cell scheduling order

`cell_identity` 继续只拥有 equality、lookup 与 dedup。`scheduling.py` 的规范顺序由以下显式 key 唯一拥有：

```python
def cell_schedule_key(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]:
    return (
        cell.package,
        cell.target,
        cell.python_minor,
        cell.extra_surface,
    )
```

Scheduler 只通过该 key 排序结果。测试分别锁定：

- `cell_identity` 是 `(package, target, python_minor, extra_surface)` 的 compatibility identity；
- `cell_schedule_key` 直接读取四个字段，不调用 `cell_identity`；
- monkeypatch 或未来修改 identity 表示不会改变 scheduling order。

Terminal 与 report order 仍由 D006 和 report module 各自拥有。

## 5. ResolutionRequest

### 5.1 判别请求

`environment.py` 定义不可变 runtime value；它们不是持久化 Schema：

```python
@dataclass(frozen=True)
class HighestResolution:
    kind: Literal["highest"] = "highest"

@dataclass(frozen=True)
class LowestDirectResolution:
    harness_baseline: HarnessBaseline
    kind: Literal["lowest-direct"] = "lowest-direct"

@dataclass(frozen=True)
class ExactSelection:
    selection: tuple[SelectedCandidate, ...]
    harness_baseline: HarnessBaseline
    kind: Literal["exact-selection"] = "exact-selection"

ResolutionRequest = HighestResolution | LowestDirectResolution | ExactSelection
```

`EnvironmentFactory` 只有一个 prepare method：

```python
prepare(
    *,
    package: PackagePlan,
    cell: Cell,
    snapshot: SourceSnapshot,
    resolution: ResolutionRequest,
) -> PreparedEnvironment | PrepareFailure
```

不得保留 `managed_vector`、可空 `selection` 或 string resolution 兼容参数。三个调用意图必须在类型层面互斥；`HighestResolution` 固定使用原始 harness，两个 relaxed 变体必须携带同 cell `HarnessBaseline`，因此调用方不能表达 `highest + relaxed`、`exact/lowest + original harness` 或缺 baseline 的 relaxed Attempt。

### 5.2 唯一投影

EnvironmentFactory 在一次私有解析中从同一个 request 导出：

| Request | Attempt `requested_resolution` | requested managed vector | uv resolver mode | selected artifacts |
| --- | --- | --- | --- | --- |
| `HighestResolution` | `highest` | `None` | `highest` | 无 |
| `LowestDirectResolution` | `lowest-direct` | `None` | `lowest-direct` | 无；携带 baseline harness ceiling |
| `ExactSelection` | `exact-vector` | 由 selection 投影 | `highest` | request 中冻结 selection；携带 baseline harness ceiling |

`ExactSelection.selection` 的 dependency 必须排序且唯一；managed vector 只在 EnvironmentFactory implementation 内投影。项目声明 materialization、Attempt identity、安装后的 graph 校验和 Proposal managed vector 都消费该投影。

EnvironmentFactory 从同一 request 建立 request-level `AttemptIdentity`，再依次取得 project plan 与 `Exact(G(P)) + original/relaxed harness` 的 environment plan。`UvOperations.install_resolution` 只接收经校验的 final plan，不再开放依赖解析；UvAdapter 只负责把 request/plan 机械翻译为 argv，不重新推断 Attempt 语义。exact selection 继续强制 locator/hash/kind，EnvironmentFactory 在安装前后核对 project graph、final graph 与实际 managed vector；post-resolution evidence 只进入 `EnvironmentIdentity`。

## 6. VerificationRunner、Scheduler 与 activity event

### 6.1 Scheduler 是 Runner 的内部 implementation

`VerificationRunner` 不再从 production composition 注入 Scheduler。Runner 构造依赖只保留 activity consumer、JournalStore 与内部 Clock callable；默认 Clock 是 `time.monotonic`。Clock 不进入 `VerificationRun`。

Scheduler 可以留在 `scheduling.py`，但其 interface 只包含：

```text
ScheduledCellTask[T]
  cell
  run: () -> T
  deadline_result: () -> T | None

Scheduler.run(
  tasks,
  jobs,
  max_duration_seconds,
  on_started(task),
  on_completed(task, result, completed, total),
) -> tuple[T, ...]
```

`deadline_result` 由 VerificationRunner 构造；Scheduler 只在 deadline 阻止提交时调用。`on_started` 只在 task 实际提交给 executor 后调用。未提交的 task 不产生 started 事实。

`scheduling.py` 可以导入 `Cell`，但不得导入 `pf.failure`、`schemas.evaluation` 的 Evaluation/FailureRecord/Cell outcome 或 `schemas.report`。它不读取 `ProcessResult.diagnostic()`，不构造终端事实，不知道 Journal。

### 6.2 判别 activity event

删除 `ProgressEvent`。Cell 活动使用两个带 discriminator 的 Schema：

```python
class CellStageEvent(FrozenSchema):
    kind: Literal["stage"] = "stage"
    cell: Cell
    stage: str

class CellSucceeded(FrozenSchema):
    kind: Literal["succeeded"] = "succeeded"
    status: str
    phase: str
    diagnostics: tuple[TyDiagnostic, ...] = ()
    process: ProcessResult | None = None

class CellFailed(FrozenSchema):
    kind: Literal["failed"] = "failed"
    status: str
    phase: str
    diagnostics: tuple[TyDiagnostic, ...] = ()
    process: ProcessResult | None = None
    failures: tuple[FailureRecord, ...] = ()
    verification_role: VerificationRole | None = None

CellCompletionOutcome = Annotated[
    CellSucceeded | CellFailed,
    Field(discriminator="kind"),
]

class CellCompletedEvent(FrozenSchema):
    kind: Literal["completed"] = "completed"
    cell: Cell
    completed: int
    total: int
    outcome: CellCompletionOutcome
    diagnose_available: bool = False
```

`package` 只从 `event.cell.package` 取得。Stage event 不含 completed/total/outcome/diagnose 字段；Completed event 不含 stage/message/detail 等自由字段。`CellMatrixEvent` 继续唯一表达初始 cell 集合。v1 不新增 queued/started public event；若以后有产品需求，必须增加显式变体。

### 6.3 Runner 完成顺序

VerificationRunner 对每个 task 执行：

1. 验证 run package 与 task Cell identity；
2. 交给内部 Scheduler；
3. 对未提交的 deadline task 构造 `TIMEOUT / INDETERMINATE` `CellIndeterminate`；
4. 通过一个 `completion_outcome(result)` 入口把强类型领域结果投影为 `CellSucceeded | CellFailed`；
5. 合并 SearchFailureEvent 缓冲与 task Journal entries；
6. 失败记录存在时先写 Journal，再发布 `CellCompletedEvent`；写失败时 `diagnose_available=false`，发布后延迟抛错；
7. 最终写 canonical Journal，并返回按 `cell_schedule_key` 排序的 outcomes。

Journal gate 只匹配 `CellCompletedEvent`，不得检查数值哨兵。`completion_outcome` 是 outcome → 终端机械事实的唯一实现；Scheduler、workflow 与 TerminalPresenter 不复制领域 `isinstance` 投影。

### 6.4 确定性时间测试

Scheduler 构造时接受一个私有 `monotonic: () -> float` callable，生产默认 `time.monotonic`。deadline 测试用 fake clock 推进边界；并发上限使用 `Barrier`/`Event` 证明两个 task 同时进入 worker，不调用 `time.sleep`。结果顺序、实际 started callback 和 deadline 未提交集合分别断言。

## 7. ProposalRunner 返回值

定义不可变内部结果：

```python
@dataclass(frozen=True)
class ProbeRun:
    evidence: ProbeEvidence
    evaluation: Evaluation | None
```

`_ProposalRunner.evaluate_full(vector) -> ProbeRun`。同一 key 的缓存保存一个 ProbeRun；完整 Evaluation context 最多执行一次。D011 落地后，`_RuntimeBackedVectorEvaluator.evaluate_in_slice` 可返回 static-only guidance，`promote` 则通过 `evaluate_full` 取得该精确 Proposal 的 `run.evidence` / `run.evaluation`。SearchCoordinator 只有这一条 runtime-backed 路径。

删除 `full_evaluation(vector)` 及只为二次 lookup 存在的 `_evaluations` / `_full_evidence_by_key` 平行状态。`_ProposalRunner` 实现 context manager；`SearchCoordinator` 用 `with` 集中关闭仍存活的 PreparedEnvironment。取得 final Evaluation 不依赖此前调用顺序。

## 8. SecureLogDirectory 私有 seam

### 8.1 Seam 范围

`pf._secure_runlog` 定义只对 RunLogStore 可见的 interface：

```text
SecureLogDirectory
  ensure_run(manifest)
  write_run_text(name, content)
  write_run_stream(name, write_body)
  read_run_text(run_id, name, limit)
  read_run_stream(run_id, name, read_body)
  read_logs_text(name, limit)
  write_logs_text(name, content)
  resolve_regular_log(relative)
  close()
```

该 interface 只隐藏安全目录创建/打开、原子 `0600` 私有写、有界/流式读取、regular-file 与 directory identity 验证及资源关闭。locator 仍是相对 `.pf/logs` 的 Path；adapter 不解析 Process Log、Journal 或 diagnosis index JSON。

### 8.2 两个真实 adapter

- `PosixDirectoryAdapter` 拥有逐级 `dir_fd`、`O_NOFOLLOW`、run inode identity、atomic replace 与 mode 校验；
- `WindowsDirectoryAdapter` 组合现有 `WindowsRunDirectory`，拥有 reparse/DACL/volume guard 和原子私有文件操作。

platform factory 只在 RunLogStore 初始化时选择一次 adapter。不支持等价安全原语的平台 fail closed。RunLogStore 的 Process Log、Journal、latest journal、association update 和 lookup 产品流程中不再出现 `os.name`、`_supports_*`、`dir_fd` 或 `WindowsRunDirectory` 分支。

RunLogStore 继续唯一拥有：

- `pf-run-log-v1`、现行 `pf-process-log-v2` 与保守 v1 reader、Journal 和 diagnosis-index 格式；
- ProcessResult runtime reference；
- failure association、latest journal 和 locator 规则；
- JSON 验证、字段上限与用户可见错误分类。

adapter 契约测试直接覆盖 POSIX 与 Windows 安全 interface；RunLogStore 测试只通过产品 interface 覆盖格式、Journal 与 index 行为。

## 9. Terminal 私有视图

`pf.terminal` 新增两个私有 module；不新增公共 Pydantic Schema。

### 9.1 CellPresentation

`_presentation.py` 定义不可变 `CellPresentation`，只能由 `CellCompletedEvent` 或 `completion_outcome(result)` 构造。它一次确定：cell、outcome kind、elapsed、failure records、diagnostics、process、role、command 与 diagnose availability。

SearchFailureEvent 只补充运行中出现的增量 failure/diagnostic；按 failure ID 与 diagnostic identity 去重后进入同一 constructor。`_print_cell_report` 改为只接收一个 CellPresentation；删除十多个可自由组合参数。Check、Smoke、Search 和 live completion 不再各写一套 outcome → presentation facts。

### 9.2 LiveVerificationView

`_live.py` 的 `LiveVerificationView.consume(event)` / `close()` 拥有：

- Rich Progress 创建、cell/stage/overall task 与规范展示顺序；
- setup card、pending status/outcome 和 elapsed；
- SearchFailureEvent 缓冲；
- CellCompletedEvent 冻结并交给 CellPresentation；
- TTY live 与非 TTY 稳定行的生命周期。

TerminalPresenter 保留 public `render_X`、stdout/stderr routing、最终摘要、failure 文案和 exit code；它把 ActivityEvent 委托给 LiveVerificationView。D006 的布局、颜色、通道、文案与 `render_X` interface 必须字节级回归保持。

测试继续只调用 `TerminalPresenter.render_X` / `consume` / `close`，不得直接断言私有 presentation 字段或 Rich task state。

## 10. Production composition 与 SnapshotBuilder

### 10.1 CliContext

`CliContext` 支撑八个命令的七个 command workflow、TerminalPresenter 和 RunLogStore 全部必填，不允许 `None`。`minimize` 顺序复用 search/apply workflow；handler 直接调用已装配 workflow；删除所有 `workflow is not assembled` production 分支。

CliContext 实现 `close()` 与 context manager。`close()` 幂等并唯一拥有资源顺序：先关闭 presenter/live view，再关闭 RunLogStore。`main()` 只关闭 context，不分别理解资源。`build_context()` 若完整 context 建立前失败，必须关闭已创建资源。

CLI 单命令测试使用共享 fixture：为目标 workflow 提供 recording adapter，为其余字段提供显式 `NeverCalledWorkflow`，为资源提供可观察 close adapter。测试便利不得重新使 production 字段可空。

### 10.2 SnapshotBuilder

`SnapshotBuilder` 的 production constructor 必须显式接收 `ProcessRunner`；不得在 runner 缺失时创建 SubprocessRunner。

纯文件系统测试使用命名构造 `SnapshotBuilder.without_processes()`。该构造只允许非 Git traversal；根目录含 `.git` 时以明确 ConfigurationError 停止，不能调用外部进程，也不能退回不完整 manifest。Git snapshot 测试与 production 始终显式提供 ProcessRunner。

## 11. 测试面

| Seam | 必须观察的行为 |
| --- | --- |
| `cell_identity` / `cell_schedule_key` | equality 与 order 独立；order 不委托 identity |
| `EnvironmentFactory.prepare` | 三变体、Attempt identity、uv request、Proposal vector、非法 selection |
| `UvAdapter.resolve_project/resolve_environment/install_resolution` | 三变体 request；exact locator/hash/kind；relaxed baseline；两份 validated plan；一次 final-plan sync |
| `VerificationRunner.run` | task/run identity、deadline outcome、Journal-before-completion、最终排序 |
| ActivityEvent Schema | stage/completed discriminator、额外字段 forbid、无 package 重复 identity |
| Scheduler internal interface | Barrier/Event 并发上限、fake clock deadline、actual started callback |
| `_ProposalRunner` 经 SearchCoordinator | static-only 观察在提交前精确 promote；同一 full context 一次 |
| SecureLogDirectory adapters | secure create/open、atomic write、有界 read、identity/regular-file guard、close |
| `RunLogStore` | Process Log、Journal v1/v2、latest、association replace/lookup 行为不变 |
| `TerminalPresenter` | D006 现有 TTY/non-TTY golden behavior；live 与 final 不重复 |
| `CliContext` / `build_context` | 类型完整、handler 无装配分支、close 顺序与构建失败清理 |
| `SnapshotBuilder` | production runner 必填；no-process 非 Git；Git 无 runner fail closed |

## 12. 模块所有权

| 规则 | 唯一所有者 |
| --- | --- |
| compatibility Cell equality/lookup | `schemas.project.cell_identity` |
| scheduling order、worker、deadline 检查 | `scheduling.py` |
| deadline Cell outcome | VerificationRunner |
| Attempt/install/Proposal resolution projection | EnvironmentFactory；UvAdapter 只翻译 request |
| stage/completed activity shape | 本文 §6.2 的 Schema |
| domain outcome → completion facts | `verification.completion_outcome` |
| Journal gate、completion 发布与最终排序 | VerificationRunner |
| full probe evidence + Evaluation lifetime | `_ProposalRunner.evaluate_full -> ProbeRun` |
| 平台安全目录与文件原语 | `pf._secure_runlog` 两个 adapter |
| 日志/Journal/index 产品语义 | RunLogStore |
| cell presentation facts | `pf.terminal._presentation` |
| live Rich 生命周期 | `pf.terminal._live` |
| 最终命令 renderer 与 exit code | TerminalPresenter / D006 |
| production object graph 与资源生命周期 | CliContext / `cli.build_context` |
| source traversal/manifest/snapshot identity | SnapshotBuilder；Git 外部调用必须显式注入 |

## 13. 对现行契约的取代

本文已落地，以下替换关系现行有效：

- D002 §7.2 中 SnapshotBuilder 可缺省创建 SubprocessRunner 的实现作废；
- D002 §8.2 与 D008 §3/§6 中 `resolution + managed_vector/selection` interface 被 §5 取代；Attempt/Failure 语义不变；
- D002 §5 的 `ProgressEvent` 列表与 D008 §7 completion 投影形状被 §6.2 取代；
- D002 §9 与 D009 §6 中 Scheduler 构造 deadline failure、投影 completion、作为 Runner production 注入的描述被 §6.1/§6.3 取代；Journal 时序继续有效；
- D002 §9 的 scheduler order 由本文 §4 明确，不再借用 identity；
- D002 §10 的 RunLogStore 产品职责继续有效，平台 implementation 由 §8 收回私有 seam；D007 安全契约不变；
- D002 §12 与 D009 §11 的 terminal 包范围继续有效，并由 §9 进一步拆为私有视图；D006 展示契约不变；
- D002 §6 的 CliContext 可空 workflow 与 `main()` 分散关闭资源被 §10.1 取代；
- D009 §8 的 ProposalRunner 生命周期被 §7 加深，D003 算法不变。

D001、D003、D004、D005、D006、D007 的产品、算法、诊断、展示和安全语义均不被本文重定义。

## 14. 被拒绝的方案

- **三个 prepare method。** 扩大 interface；一个判别参数已经让意图互斥。
- **保留 string resolution 并只增加 validator。** 非法组合仍能通过类型检查，调用方仍需知道优先级。
- **给 Scheduler 定义 port/adapter。** 当前只有一个生产 implementation；测试 Clock 是内部时间 seam，不证明 Scheduler adapter 分化。
- **让 Scheduler 继续投影但移动 helper 文件。** 领域知识仍越过 scheduling seam，只改变文件位置。
- **在 ProgressEvent 增加 `kind` 但保留全部字段。** Stage 仍可携带 completion/failure 状态，非法组合仍可表示。
- **deadline task 预先发 started。** CellMatrix 已表达 queued 集合；未提交 task 不能宣称 started。
- **ProbeRun 之外保留 `full_evaluation` 兼容 lookup。** 调用历史约束仍存在，两个状态源可能漂移。
- **拆成 ProcessLogStore/JournalStore/IndexStore。** 三者共享安全目录、run identity 与 locator，不会增加 depth。
- **通用 filesystem abstraction。** seam 只需要 PF 日志目录的安全原语。
- **把 CellPresentation 放进公共 Schema。** presentation 是终端私有状态，不是 portable evidence。
- **为 CLI 测试保留 Optional workflow。** 测试便利不能使 production 非法状态可表示。
- **SnapshotBuilder 静默退回普通遍历。** Git manifest 是 snapshot identity 的一部分；缺 runner 必须 fail closed。

## 15. 验证契约

- `rg 'cell_identity' src/pf/scheduling.py` 不命中，`cell_schedule_key` 与 identity 各有独立测试；
- EnvironmentFactory 与 UvOperations 不再出现 `managed_vector: ... | None` 或 `selection: ... | None` prepare/install 参数；
- highest、lowest-direct、exact-selection 的 Attempt、uv argv、安装 graph 与 Proposal vector 一致；
- `scheduling.py` 不导入 `pf.failure`、Evaluation/FailureRecord/ProgressEvent 或 `schemas.report`；
- deadline 未提交 Cell 没有 started/stage 事实，但有 Runner 构造并持久化的 completion failure；
- activity union 不含 `ProgressEvent`；Journal gate 只匹配 `CellCompletedEvent`；event 不重复 package identity；
- fake clock 与 Barrier/Event 测试不调用 wall-clock sleep；生产 Clock 是 `time.monotonic`；
- SearchCoordinator 不调用 `full_evaluation`；static-only frontier 由 `promote` 得到对应 ProbeRun 后才能提交；
- RunLogStore 产品流程不含平台条件；POSIX/Windows adapter 安全测试与现有 RunLogStore 行为测试通过；
- `_print_cell_report` 只接收 CellPresentation；同一 outcome 只经 `completion_outcome` 转换；
- TerminalPresenter 现有 D006 TTY/non-TTY 输出与 exit code 回归通过；
- CliContext 任一 workflow 或 resource 缺失时无法构造；所有 handler 无 `workflow is not assembled`；context close 顺序唯一且幂等；
- SnapshotBuilder production 构造显式接收 runner；no-process 构造遇到 Git root fail closed；
- 全量 Ruff、`ty check src`、Python 3.10–3.12 pytest `--no-testmon`、build 与真实安装集成通过。

## 16. 不变量

1. ResolutionRequest 是 Attempt requested intent、安装 intent 与 Proposal vector 的唯一输入。
2. Cell identity 不拥有任何排序；每个排序界面写出自己的 key。
3. Scheduler 不构造领域 failure，不投影 terminal facts，不持久化 Journal。
4. 只有实际完成的 CellCompletedEvent 可以进入 Journal gate。
5. Journal 成功前 completion 不宣称 diagnose 可用。
6. 平台 adapter 不解析产品格式；RunLogStore 不执行平台分支。
7. Rich renderable 与 CellPresentation 不离开 `pf.terminal` 包。
8. production object graph 没有 Optional workflow 或隐藏外部进程 adapter。

## 17. 决策记录

### D1：一个 ResolutionRequest，三个不可变变体（已确认）

三个变体共享 prepare 行为，但不共享 payload。判别 value 比三个 public method 更小，也比可空参数更能表达合法状态。

### D2：Scheduler 是 VerificationRunner 的内部 implementation（已确认）

只有一个生产 Scheduler。Runner 拥有领域 deadline 和 completion；Scheduler 只保留并发机制与显式 scheduling order。

### D3：Stage 与 Completed 是不同 event（已确认）

`completed == 0` 不是领域状态。判别 event 让 Journal 与 terminal 在类型层面只处理各自合法字段。

### D4：Clock 是内部 callable（已确认）

生产与测试确有时间来源差异，但不需要通用 runtime interface。Clock 不进入 VerificationRun 或 workflow。

### D5：SecureLogDirectory 是私有双 adapter seam（已确认）

POSIX 与 Windows 是两个真实安全 implementation；变化来源止于日志目录原语，产品格式继续留在 RunLogStore。

### D6：Terminal public interface 不变（已确认）

改进目标是 presentation locality，不是 CLI redesign。所有变化限定在 `pf.terminal` 包内私有 module。

### D7：Production composition 完整，测试显式提供 NeverCalled adapter（已确认）

缺 command workflow 是装配错误，不是运行期配置错误。测试替身应表达“不得调用”，不能靠 `None` 表达未装配。

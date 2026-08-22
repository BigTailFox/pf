# PF v1 模块加深与内部 seam

- **状态：** 现行契约，待实现
- **日期：** 2026-08-22
- **来源：** [R001](../reviews/R001-pf-v1-review.md)（`d1b8614` 快照，含独立复核）
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **进程输出与日志：** [D007](D007-pf-process-output.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)

本文是 v1 主路径落地之后「先补齐现行契约，再加深已有模块、收回泄漏的内部 seam、补测试面和工程门禁」的唯一整改契约。它不增加命令，不扩展 D001 的产品范围，不改 D003 的 probe 顺序，不改 D005 的 cause/disposition 矩阵，也不把 D001 §10 的非目标改写成待办。

R001 是非规范性快照。整改行为、模块 interface、顺序和完成标准以本文为准。落地前，模块位置与 interface 仍以 D002 / 当前实现为准；落地后，本文取代 §14 列出的条款。

## 1. 问题

现行实现的税分四类。

1. **已有契约没有完全落地。** 流式脱敏可泄露跨分块 secret；CandidateSnapshot 的 artifact/hash 没有约束实际安装；complete report 没有把 final vector 绑定到 PASS Proposal；apply 失败不回滚；workspace canonical package name 不唯一。
2. **同一含义多套实现。** Cell lookup 至少五套，FailureRecord 提取至少三套；Evaluation → classify 输入写了两遍；多包 Journal 却只保存第一包 policy identity。
3. **共同编排没有形成深模块。** Check / Smoke / Search 复制 Journal gate + schedule + final Journal；离线读取命令复用完整 ProjectLoader，带入不需要的 Python discovery。
4. **测试没有穿过真正的 interface。** CoordinateSearch 的连续调用测不到重入；ProjectEditor 没有 rollback/workspace transaction 测试；RunLogStore 没有独立测试面；Registry adapter 没有认证和畸形输入矩阵。

D002 的拆分条件仍有效：文件变长不是拆分理由；只有已经存在两个可独立描述、测试和演进的深模块时才拆。

## 2. 目标与非目标

### 2.1 目标

- D001 / D007 要求的凭据不落日志，在任意流分块下成立；
- exact-vector probe 安装 CandidateSnapshot 选中的精确 artifact，并校验 hash；
- complete report 的 final vector、ProbePass、Attempt、Proposal、Evaluation 与 projection 形成闭环；
- `ProjectEditor.apply_many` 对 workspace 是可恢复事务，失败不留下半应用状态；
- workspace canonical package name 在 discovery seam 唯一；
- Explain / Diagnose 只做离线 package/report discovery，不调用 uv、Python inventory、SnapshotBuilder 或 evaluator；
- smoke / check / search 共用一个深的 VerificationRunner；
- Cell lookup、FailureRecord 提取、Evaluation → classify 输入各只有一个实现所有者；
- CoordinateSearch 按已有 seam 分文件，同一实例可嵌套和并发调用；
- Journal 正确表达每个 package 的 policy identity，并在 cell 完成卡片冻结前持久化可诊断记录；
- Registry adapter 支持运行时认证，并把畸形外部输入保守分类；
- 全量 CI 固定关闭 testmon，并覆盖 Ruff、ty、pytest、build 与支持的 Python minor。

### 2.2 非目标

- 不增加、删除或重命名 D001 命令；
- 不改变 D003 的候选顺序、hint、指数探针、单调性或 fixpoint 语义；
- 不引入上界搜索、failure attribution、static-only floor、flaky retry、跨运行 Evaluation cache 或非宿主执行；
- 不按行数把七个 workflow 拆成七个文件，不优先拆 `schemas/evaluation.py`、`project.py` 或 `environment.py`；
- 不重写 terminal 视觉，不把业务 Rich 带出 `pf.terminal` 包；
- 不引入 DI 框架、`utils.py` / `helpers.py` / `common.py`；
- 不把 credential 写入 SourceIdentity、PackagePlan、Attempt、Proposal、ProcessSpec、Journal、报告或日志；
- 不把 LICENSE / CHANGELOG 或 coverage 红线当作本文完成条件。

## 3. 范围与顺序

| 阶段 | 项 | 完成标准 |
| --- | --- | --- |
| P0 | 流式脱敏 | 任意 byte 分块与一次性脱敏等价；production secret 已装配但不落盘 |
| P0 | Report authority | final vector、terminal search、ProbePass、Attempt、Proposal、Evaluation、projection 闭环 |
| P0 | Apply transaction | 单包与 workspace 任一写后失败均回滚，重启恢复不误提交 target |
| P0 | Workspace identity | canonical package name 重复在 discovery 阶段失败并列出冲突路径 |
| P0 | Candidate artifact binding | exact-vector 使用冻结 artifact locator + hash，不允许同版本改选 |
| P0 | 工程门禁 | Python 3.10–3.12；Ruff、ty、pytest `--no-testmon`、build |
| P1 | 离线 ProjectDiscovery | Explain / Diagnose 不启动工具、不联网、不创建运行日志 |
| P1 | Registry / Journal identity | 私有认证可用；畸形外部输入保守失败；Journal 按包记录 policy |
| P1 | Cell identity / Failure 提取 | lookup 与 order 分离；全库使用单一 lookup / extraction owner |
| P1 | VerificationRunner | 三个 workflow 不再复制 gate + schedule + journal，interface 只接收一次运行 |
| P1 | Evaluation → FailureRecord | 两个调用方只调用 `FailurePolicy.classify_evaluation` |
| P1 | CoordinateSearch | 真重入、并发安全、表驱动算法测试、按 seam 分文件 |
| P1 | RunLogStore 测试 | 独立覆盖 journal 与 diagnosis index interface |
| P2 | PreparedEnvironment 生命周期 | 静态 PASS 不随候选数长期保留完整环境 |
| P2 | TerminalPresenter 内部视图 | explain / diagnose 移到包内私有模块，对外 interface 不变 |

P0 是现行产品和安全契约的修复，必须先于纯重构。P1 可以按 seam 独立落地；P2 不得阻塞 P0/P1。

## 4. 现行契约修复

### 4.1 流式脱敏

D007 的行为不变：已知 credential 和 URL userinfo 在进入 listener、Output Cache、Process Log 或异常 detail 前必须脱敏。

`SecretRedactor` 的单值 interface 保留；流式读取由 Process adapter 内部拥有，不把 chunk state 暴露给调用方。实现必须满足：

```text
concat(redact_stream(chunks)) == redact(concat(chunks))
```

上式按同一 UTF-8 replacement 规则比较，并对任意 chunk partition 成立。实现可以在内部保留未决前缀或先对组合窗口脱敏，但不得把可能属于 secret 的前缀提前交给 consumer。

composition root 必须把本次运行实际使用的 credential literals 交给 `SecretRedactor`。credential 来源可以是 uv/index 的运行时认证配置或受支持环境变量；它们只存在于进程内。`ProcessSpec.environment` 继续只在运行时携带值，日志只记录变量名和 `***`。

测试至少覆盖：

- secret 的每一个 64 KiB 边界偏移；
- URL userinfo 的 scheme、userinfo、`@` 分别跨 chunk；
- 多字节 UTF-8 紧邻 secret；
- 多个重叠 secret，以最长值优先；
- stdout、stderr、listener、Output Cache、Process Log 四个观察面均无明文。

### 4.2 精确 Candidate artifact

CandidateSnapshot 已为每个 dependency/version 冻结一个 `Candidate.artifact`。CoordinateSearch 继续只操作 `VersionPin`，不学习 artifact 或安装知识。

Search implementation 在每次 evaluator 调用前做唯一映射：

```text
select_probe(
    vector: tuple[VersionPin, ...],
    snapshots: tuple[CandidateSnapshot, ...],
) -> tuple[SelectedCandidate, ...]

SelectedCandidate = (dependency, version, artifact)
```

映射必须要求 dependency 集合完全相等、version 在对应 snapshot 中唯一存在、artifact locator/hash/kind 完整。它是 SearchCoordinator / ProposalRunner 的内部 seam，不进入 CoordinateSearch interface。

`EnvironmentFactory.prepare` 的 probe 路径接收 selection，而不是只接收裸 vector；Attempt 的 `requested_managed_vector` 仍由 selection 投影得到。生产安装必须：

- 使用 selection 中每个 artifact 的精确 locator；
- 强制校验 SHA-256；
- 遵守已选 wheel/sdist kind，不让 resolver 改选同版本其他构件；
- 安装完成后检查实际解析图仍等于 requested vector；
- 认证失败、artifact 不可取得、hash 不符或实际选择漂移时形成受控 failure，不退回普通 `==version` 安装。

CellResult / report validator 验证 final vector 可以唯一映射回本次报告保存的 CandidateSnapshot；Proposal 只保存实际 managed vector，不反向依赖报告。D001「来源或构件身份变化使旧证据失效」继续由 CandidateSnapshot digest、report merge/update 的 snapshot 冲突检查和上述闭环共同实现。

### 4.3 CellSuccess 与 complete authority

`CellSuccess` 新增并集中强制以下不变量：

```text
terminal_search = dynamic_search if dynamic_search is not None else static_search

final_vector
  == terminal_search.vector
  == final_evaluation.proposal.managed_vector
  == final ProbePass observation.vector
  == final ProbePass attempt.identity.requested_managed_vector
```

最终 ProbePass 的 `proposal_id`、Attempt ID、cell、snapshot、policy 和 Evaluation 必须与 `final_evaluation` 完全一致。Vector dependency name 排序且唯一，并与 managed CandidateSnapshot dependency 集合相等。

`PackageFloorReportV1.validate_completion_authority` 继续验证 cell 覆盖与 projection，但 floor 只能来自已经闭环的 `CellSuccess.final_vector`。Builder、Store、Editor 不各写一套 final evidence 推断。

新增防篡改矩阵：分别只改 final vector、terminal search vector、ProbePass vector、Attempt requested vector、Proposal managed vector、final Evaluation、Candidate artifact 和 projection floor，每一种都必须在 Schema 或 apply 前失败。

### 4.4 Workspace apply transaction

`ProjectEditor.apply` 委托给 `apply_many((report,), root)`；事务所有者只有 `apply_many`。

事务分两个阶段。

1. **Prepare：** 对全部 report 完成 complete authority、package/policy/source identity、projection 复核、TOML 渲染和重新解析；此阶段不得写用户文件。任何失败返回时 workspace 字节不变。
2. **Commit：** 为全部会改变的文件写 backup 和一个 workspace recovery journal，再逐个原子 replace；全部文件替换后重新读取并做一次 ProjectLoader 验证。只有全部通过才提交并清理 backup。

Recovery journal 至少保存每个文件的相对路径、original digest、target digest、backup path 和事务状态：

```text
PREPARED -> PROJECTS_REPLACED -> VALIDATED -> COMMITTED
                              \-> ROLLING_BACK -> ROLLED_BACK
```

规则：

- 任一 replace、目录 sync 或写后验证失败，立即恢复所有已替换文件并 `fsync`；
- rollback 自身失败时抛 InfrastructureError，并保留 journal/backup 供人工恢复；
- 重启看到 original digest 视为该文件已回滚；看到 target digest 必须继续回滚，不得直接标记 COMMITTED；
- 任一文件既非 original 也非 target，停止并报告未知用户修改，不覆盖；
- 所有 target 已验证且 journal 已到 VALIDATED 才可恢复为 COMMITTED；
- 无实际变更时保持幂等，不创建新的 recovery transaction。

### 4.5 ProjectDiscovery 与 canonical package identity

新增深模块：

```text
ProjectDiscovery.discover(
    root: Path,
    package_selection: str | None,
) -> tuple[PackageLocation, ...]

PackageLocation = (name, package_root, pyproject_path, report_path)
```

它唯一拥有 workspace member、root installable package、`packages` / `exclude-packages` / explicit path、selection 和 canonical name 唯一性。发现两个 canonical name 相同的路径时，无论 selection 形式为何都拒绝，并在 ConfigurationError 中列出全部冲突相对路径。

ProjectDiscovery 只读取定位所需 TOML 字段；不加载 EffectiveConfig，不计算 marker/cell，不调用 PythonMinorProvider，不建立 snapshot，不启动外部工具。

`ProjectLoader` 在 discovery 结果上继续完成配置、声明、source plan、Python minor 和 cell planning。生产 composition root 共享一个 `ProjectDiscovery`，而不是让所有 workflow 共享一个完整 ProjectLoader。

Explain / Diagnose 只依赖 ProjectDiscovery + ReportStore / DiagnosisLogLocator。它们验证读到的 report/journal package 与 PackageLocation.name 一致，并保持 D001 / D008 的离线只读契约。Apply 仍使用完整 ProjectLoader，因为它必须核对当前 policy/source/project。

### 4.6 Registry 认证与输入验证

可移植 `SourceIdentity` 继续只保存公开 locator/index，不保存 userinfo、query token 或 credential。生产 registry adapter 另接收进程内 `RegistryAccess`，或复用 uv 的认证能力；该对象不得可序列化，也不得出现在错误 detail。

Registry adapter 是 true external seam。生产 HTTP/uv adapter 与测试 mock 都满足现有 query 行为，CandidateBuilder 不学习认证协议。

Adapter 在返回 `AvailableCandidate` 前完整验证：

- Content-Length 是非负十进制且不超过上限；
- JSON root 与 `files` 是正确类型；
- 每个 file 是 mapping；filename/url/hash/requires-python/yanked 是允许类型；
- locator 解析、hash、Version、SpecifierSet 和 artifact tag 错误都被捕获；
- `HTTPError`、`URLError`、timeout、OSError、ValueError、TypeError、AttributeError 和 JSON/schema 错误统一包装成 InfrastructureError。

认证失败不得回退到匿名不同来源；异常 detail 先脱敏再离开 adapter。

### 4.7 多包 Verification Journal identity

新写入使用 `verification-journal-v2`：

```text
run_id
command
source_snapshot_digest
package_policies[]
  package
  evaluation_policy_identity
entries[]
```

`package_policies` 按 canonical package 排序且唯一，精确覆盖本次运行 packages。每个 entry 的 package、cell.package、FailureRecord scope cell 和 scope policy 必须与对应 package policy 一致。

RunLogStore reader 可以读取 v1 历史 Journal 供 diagnose；writer 只写 v2。v1 顶层单 policy identity 只作为历史展示元数据，不用于重新授权、merge 或 apply。

### 4.8 PreparedEnvironment 生命周期

静态 probe 完成后：

- StaticFail / Indeterminate 立即关闭；
- StaticPass 保存 Evaluation/cache，不保留 PreparedEnvironment；
- static coordinate search 确定 final vector 后，为 full evaluation 重新 prepare 精确 selection；
- dynamic search 的每个 full evaluation 结束即关闭；
- 同一 Evaluation context 的完整测试仍最多执行一次。

资源测试通过公开 search interface 观察 prepare/close 数量与最终结果，不读取 `_prepared` 私有 dict。并行 N cells 时长期存活环境数由 active jobs 限制，不随已探测候选总数增长。

## 5. Cell identity 与 FailureRecord 提取

### 5.1 `cell_identity`

CONTEXT.md 的 compatibility Cell identity 是 `(package, target, CPython minor, extra surface)`，不含 `active_declaration_ids`。唯一 lookup owner 放在 `schemas/project.py` 旁：

```text
cell_identity(cell: Cell) -> tuple[str, str, str, tuple[str, ...]]
  = (cell.package, cell.target, cell.python_minor, cell.extra_surface)
```

它只用于 dict/set lookup、dedup 和 equality projection。不得因为 tuple 可排序就把它当作所有界面的 order owner。

- Scheduler 的规范 order 仍由 D002 的 package/target/python/extra 拥有，可以显式 `cell_schedule_key`；
- Terminal 的展示/诊断 order 由 D006 拥有，使用自己的显式 key；
- Report canonical JSON order 由 report 模块拥有，但 equality 只走 `cell_identity`。

删除 workflow、scheduling、report schema/store 中承担 lookup 的私有副本。Terminal 若同一函数同时用于 lookup 与 sort，先拆成 identity 与 presentation key，再替换 lookup。

### 5.2 `failure_records_for_result`

把 `schemas/report.py._failure_records_for_result` 升为公共纯函数：

```text
failure_records_for_result(result: CellResult) -> tuple[FailureRecord, ...]
```

PackageFloorReport、Search workflow、Journal 投影、Diagnose 都只调用它。禁止再次按 `BaselineRejection | BaselineIndeterminate` 复制分支。

### 5.3 `incomplete_reason`

Builder 私有方法不是 Store 的 seam。定义 `report.py` 模块级纯函数：

```text
incomplete_reason(result: CellResult) -> str | None
```

PackageReportBuilder 与 ReportStore 共用；projection 仍只属于 PackageReportBuilder。

## 6. VerificationRunner

Check / Smoke / Search 共享的不是 load 或报告持久化，而是「已 load、已 snapshot 后的 cell schedule + Journal 可用性」。新模块 `verification.py` 对外 interface：

```text
class VerificationRunner:
    def __init__(scheduler, events, logs: JournalStore | None): ...
    def run(self, request: VerificationRun[T]) -> tuple[T, ...]: ...

VerificationRun[T]
  command: "smoke" | "check" | "search"
  packages: tuple[PackagePlan, ...]
  snapshot: SourceSnapshot
  tasks: tuple[VerificationTask[T], ...]
  jobs: int | "auto"
  max_duration_seconds: float | None

VerificationTask[T]
  cell: Cell
  execute: () -> T
  journal_entries: (T) -> tuple[VerificationJournalEntry, ...]
  deadline_scope: CellFailureScope | None
```

Scheduler、ProgressConsumer 和 JournalStore 是 Runner 的稳定构造依赖，不由三个 workflow 每次透传。Task 自己知道如何把强类型 outcome 投影为 Journal entry；Runner 不 `isinstance` 所有命令结果，也不接一个运行级大 callback。

Runner 隐藏：

- start/completion event 与 scheduler 调用；
- SearchFailureEvent 的 per-cell 缓冲与 failure ID 去重；
- cell completion 时合并 buffered entries 和 outcome entries；
- Journal v2 构造、package policy 校验和 write；
- 写入成功后才把 completion event 的 `diagnose_available` 设为真；
- 运行结束 final Journal；
- journal 基础设施错误的延迟展示与最终 raise。

`logs is None` 时不写 Journal，并把所有非成功 completion 的 `diagnose_available` 设为 false；不得把 no-op 当作成功持久化。`logs` 非空则直接调用完整 JournalStore interface，不做 `hasattr`。

一个 cell 内多个 SearchFailureEvent 只在该 cell completion 到达时写一次当前累积 Journal。运行结束再写一次 canonical final Journal；final write 可以与最后一个 completion 内容相同，但必须保留为完整性确认。写入失败后不得写 package-floor report 或 diagnosis association。

三个 workflow 仍拥有：

| Workflow | 自身职责 |
| --- | --- |
| Check | load → snapshot → contract 检查 → tasks → Runner → D008 §7.1 聚合 |
| Smoke | load → snapshot → contract 检查 → tasks → Runner → D008 §7.2 聚合 |
| Search | load → snapshot → deadline tasks → Runner → report build/write → diagnosis associations |

Explain / Diagnose / Apply / Merge 不走 VerificationRunner。

## 7. Evaluation → FailureRecord

D008 §6.2 继续唯一拥有 Evaluation → cause/stage 的语义表；本文不复制该表。D005 继续唯一拥有 cause/disposition 矩阵。

实现加深 `FailurePolicy`：

```text
classify_evaluation(scope, evaluation) -> FailureRecord | None
```

它严格实现 D008 §6.2；PassEvaluation / StaticPassEvaluation 返回 `None`，其余 Evaluation 取出 D008 要求的 cause、stage、process 和 summary_code 后调用现有 `classify(...)`。

CompatibilityChecker 与 ProposalRunner 只区分「None 是 pass」和「FailureRecord 如何包成各自 outcome/evidence」，不再写 cause 字符串。PrepareFailure 和 CellFailureScope 继续直接调用 `classify(...)`。FailurePolicy 不接收 command 或 VerificationRole。

## 8. CoordinateSearch 与 search.py

### 8.1 拆分

```text
src/pf/coordinate_search.py   # CoordinateSearch + VectorEvaluator
src/pf/search.py              # SearchCoordinator + ProposalRunner
```

生产可以暂时从 `pf.search` re-export CoordinateSearch，算法测试只依赖 `coordinate_search.py`。先移动文件保持行为，再局部化状态和改注入。

### 8.2 可重入与并发安全

`minimize` 签名不变，`small_threshold` 仍是只读构造配置。Evaluator、known pass set、evidence cache、observations 和 slice observations 全部属于一次调用的局部 state；helper 显式接收 state 或由一次调用私有的内部对象持有。

同一个 CoordinateSearch 实例必须允许：

- 顺序使用不同 evaluator；
- evaluator 在一次外层 evaluate 中嵌套调用同实例；
- 两个线程在不同 cell 上交错调用；
- 内层/另一线程结束后，外层 observation、cache 和结果不变化。

### 8.3 SearchCoordinator 必注入

```text
SearchCoordinator(
    environments,
    candidates,
    static,
    full,
    highest: HighestOperations,
    coordinate_search: CoordinateSearch,
    ...,
)
```

`highest` 与 `coordinate_search` 都是必需构造参数。`cli.build_context` 现行只构造 highest；落地时新增一个 CoordinateSearch 并显式注入。SearchCoordinator 不再现场构造 HighestVersionVerifier 或 CoordinateSearch，也不只读取 threshold 后丢掉实例。

### 8.4 算法测试

纯函数式 fixture evaluator，不碰文件系统、不经 SearchCoordinator。至少覆盖：

- hint 命中 / hint 位于 Reject 侧 / hint dependency 不存在；
- slice 长度 `<` / `=` / `>` small_threshold；
- start_is_known_pass 不重复 evaluate start；
- 空 slice、单点 slice；
- NON_MONOTONIC counterexample；
- ProbeIndeterminate 终止 cell；
- 嵌套重入；
- barrier 双线程交错。

顺序调用两次只能作为基本回归，不能作为可重入完成标准。

## 9. Consumer-owned Protocol

不再把 Check / Search 的 Environment、Static、Full Protocol 合成一个宽 interface。Protocol 描述调用方必须知道的最小能力，测试 adapter 是真实 adapter，不因只有一个生产实现而变成假 seam。

建议能力：

```text
DeclarationEnvironmentOperations.prepare(..., resolution)
ProbeEnvironmentOperations.prepare(..., selection)
StaticCaptureOperations.capture(...)
StaticEvaluateOperations.evaluate(...)
FullEvaluateOperations.evaluate(...)
```

同一个 EnvironmentFactory / StaticEvaluator 可以满足多个 Protocol。只有方法集合、参数语义和错误模式完全一致的两个 consumer 才共用同一个 Protocol。

FullEvaluator 的 `static` 构造参数类型改为 StaticEvaluateOperations，不绑具体 StaticEvaluator，也不要求无用的 `capture`。测试 fake 只实现被测调用方实际使用的方法。

DiagnosisLogLocator 已声明 `lookup` / `lookup_run` / `read_latest_journal`；Diagnose 直接调用，不做 `getattr` / `hasattr`。测试 fake 缺方法应在类型检查或测试运行时立即失败。

## 10. 测试面

### 10.1 Report / Editor

新增独立测试：

- §4.3 的 final evidence 防篡改矩阵；
- incomplete report 在任何 recovery/write 前失败；
- projection tamper 不写回（从现有幂等测试拆出）；
- PREPARED / PROJECTS_REPLACED / VALIDATED 各状态重启恢复；
- target/original/unknown digest 三分支；
- 写第 N 个 workspace member 失败时全部 rollback；
- rollback 失败保留可人工恢复的 journal/backup；
- 重复 apply 幂等。

ProjectEditor 不注入无状态 PackageReportBuilder。Projection 的一个实现已经是生产与测试共同 interface；若未来出现第二个真实 projection adapter，再另开 seam。

### 10.2 RunLogStore

新 `tests/test_runlog.py`，以这些 interface 为测试面：

```text
write_journal / read_latest_journal
replace_associations / lookup / lookup_run
```

覆盖 v2 round-trip、v1 只读兼容、package policy exact coverage、latest_journal 按 package 更新、index replacement 和原子写。Windows DACL / directory fd / symlink guard 的实现细节留在平台测试。

### 10.3 Registry / redaction / discovery

- Registry mock 覆盖认证成功/失败、401、不合法 Content-Length、root/files/file/hash/url/requires-python 类型矩阵；
- Redaction 只从 process/log public observation 断言，不调用私有 chunk helper；
- ProjectDiscovery 覆盖 root/member/explicit path/selection、canonical duplicate、escape、离线无工具；
- Explain / Diagnose 用会在任何外部调用时报错的 adapter 证明严格离线。

### 10.4 仍后置的薄面

`evaluation.py`、`snapshot.py`、`scheduling.py` 仍偏薄，但不是本文 P0。Scheduling 的真实 `time.sleep` 可以后续替换时钟 seam，不作为本轮拆分条件。

## 11. TerminalPresenter 内部视图

D002「只有 `terminal.py` 创建业务 Rich renderable」收窄为：只有 `pf.terminal` 包创建业务 Rich renderable。对外 interface 不变：

```text
render_X(result) -> exit_code
```

P2 先把 explain / diagnose renderer 移到包内私有模块，测试跟随 interface。Check / Smoke / Search live progress 保留在主 presenter，直到本轮 Runner/Journal 行为稳定。不得改变 D006 的布局、颜色、通道、卡片行数或文案。

Cell presentation order 使用 D006 所有的显式 order key，不复用 `cell_identity` 充当排序契约。

## 12. 工程门禁

test dependency group 增加 Ruff；ty 已是运行依赖。GitHub Actions 对 main 与 pull request 使用 Python 3.10、3.11、3.12 matrix，至少执行：

```text
uv sync --group test
uv run ruff check src tests
uv run ty check src
uv run pytest --no-testmon
uv build
```

规则：

- CI 明确显示 testmon disabled；默认 addopts 可以继续用于本地增量；
- Ruff 从 error-level 规则开始，但命令必须真的进入 workflow，不能只写配置；
- `ty check src` 是全 src 门禁；当前代码已能通过，不再只选两个小模块；
- build 验证 sdist/wheel，不发布；
- coverage `fail_under=90` 仍是可选报告，不作为本文红线；
- 需要真实网络、私有 registry、Windows ACL 或非宿主执行的测试单独标注，不能由 mock 冒充。

## 13. 模块所有权

| 规则 | 唯一所有者 |
| --- | --- |
| Secret 值与 URL userinfo 的机械脱敏 | D007；Process adapter 实现，本文补流式完成标准 |
| Candidate eligibility / artifact selection | CandidateBuilder + CandidateSnapshot；安装必须消费 selection |
| final evidence 闭环 / complete authority | CellSuccess / PackageFloorReportV1 validators |
| workspace apply transaction / recovery | ProjectEditor.apply_many |
| workspace/member discovery 与 package name 唯一性 | ProjectDiscovery |
| config、声明、source plan、Python minor、cell planning | ProjectLoader |
| registry 外部输入与运行时认证 | Registry adapter；portable identity 仍是 SourceIdentity |
| cell lookup identity | `schemas/project.py.cell_identity` |
| scheduling / presentation / report order | 各自契约所有者；不是 cell_identity |
| FailureRecord extraction | `schemas/report.py.failure_records_for_result` |
| incomplete reason | `report.py.incomplete_reason` |
| schedule + Journal gate/timing | VerificationRunner |
| Evaluation → cause/stage 语义 | D008 §6.2 |
| Evaluation → classify 实现入口 | FailurePolicy.classify_evaluation |
| cause / disposition 矩阵 | D005 |
| CoordinateSearch.minimize | `coordinate_search.py`；算法仍 D003 |
| 单 cell 搜索状态机 / artifact mapping | SearchCoordinator / ProposalRunner |
| Process-local Evaluation cache | EvaluationCache |
| Verification Journal v2 shape | 本文 §4.7；FailureRecord shape 仍 D005/D008 |
| 业务 Rich | `pf.terminal` 包 |
| production composition | `cli.build_context` |
| 全量门禁 | 本文 §12；落地后以 workflow 文件为执行证据 |
| 命令、退出码、D001 §10 非目标 | D001 |

落地后更新 D002 布局表，加入 `project_discovery.py`、`verification.py`、`coordinate_search.py`，并写明 terminal 可以是包。D002 不复制本文的函数签名。

## 14. 对现行契约的取代

落地本文后，下列 D002 条款作废或收窄：

- §3：search.py 同时拥有算法与 coordinator；业务 Rich 只能在单文件 terminal.py；
- §6.2：Check / Smoke / Search 各自拥有完整 schedule + Journal 循环；Explain / Diagnose 都必须依赖完整 ProjectLoader；
- §7：ProjectLoader 同时拥有 discovery 与 planning；
- §8.5：FailurePolicy 只有 classify，调用方自行拆 Evaluation；
- §9：SearchCoordinator 可缺省构造 HighestVersionVerifier / CoordinateSearch；probe 只传 managed vector 而不绑定 artifact selection；
- §10：Registry query 无单独运行时认证与完整外部输入验证要求；
- §11.2：ProjectEditor 逐包提交，未完成 recovery 可把 target 直接视为 COMMITTED；
- §12：业务 Rich 字面上只能位于 terminal.py。

落地本文后，D008 §8.1 的单个顶层 `evaluation_policy_identity` 与未规定写入频率被本文 §4.7 / §6 取代。D008 §6.2 的 Evaluation → cause/stage 语义继续有效且是唯一所有者；本文不取代它。

D001、D003、D004、D005、D006、D007 的产品、算法、展示和保密语义不被取代。本文中对应 P0 项是让实现满足它们，而不是重定义它们。

## 15. 被拒绝的方案

- **先做纯重构再修授权/保密缺口。** P0 是现行契约，优先级高于 locality 优化。
- **11 参数自由函数 `run_verification(...)`。** 它透传稳定依赖，是浅 wrapper；使用构造时注入的 VerificationRunner。
- **把 Check / Search Protocol 合成一个宽 Protocol。** Consumer 使用的能力不同，测试 adapter 是真实变化点。
- **让 Explain / Diagnose 共享完整 ProjectLoader。** 未配置 Python 时会启动 uv，违反离线读取契约；共享 ProjectDiscovery。
- **用连续调用两次证明 CoordinateSearch 可重入。** 现行重置字段就能通过；必须嵌套和并发交错。
- **ProjectEditor 注入同一个无状态 PackageReportBuilder。** 没有第二个实现，不增加一致性或测试价值。
- **CandidateSnapshot 只作报告说明，probe 继续 `==version`。** 这使 artifact 证据与执行脱节。
- **Apply 校验失败后保留 target，等待下次提交。** 命令已失败，保守行为是自动 rollback。
- **Journal 用第一包 policy 代表 workspace。** Package config 可以不同；必须按包记录。
- **cell_identity 同时当排序键。** Lookup equality 与 presentation/scheduling order 是不同含义。
- **`hasattr` 当作可选 Journal 能力。** `logs=None` 才表示功能关闭；残缺对象是装配错误。
- **只在运行结束写 Journal。** live Diagnose 会先暴露打不开的 failure ID。
- **跨运行 Proposal / Evaluation cache。** D001 §10。
- **先重写 terminal 视觉。** 本轮只移动包内 implementation，不改 D006。

## 16. 验证契约

- Secret 或 URL userinfo 位于任意 process stream chunk 边界时，listener、Output Cache、Process Log 和异常 detail 都不出现明文；
- 每个 exact-vector Attempt 的安装输入可唯一回到 CandidateSnapshot 的 artifact locator/hash/kind；
- 只改 report 中 final vector、terminal search、ProbePass、Attempt、Proposal、Evaluation 或 projection 任一处都会验证失败；
- 任一单包/workspace 写后验证失败，所有 pyproject bytes 恢复为调用前值；
- 两个 workspace member 使用相同 canonical package name 时，ProjectDiscovery 在 workflow 前失败；
- Explain / Diagnose 在未配置 Python minor 时也不调用 PythonMinorProvider 或任何外部工具；
- Journal v2 package policies 唯一并精确覆盖 packages，entry scope policy 与所属 package 相等；
- `logs=None` 的非成功 completion 不打印 Diagnose；Journal write 成功前也不打印；
- 任意 Cell lookup 等于 cell_identity，但 Scheduler/Terminal order 使用各自显式 key；
- FailureRecord 列举只经 failure_records_for_result；
- Check / ProposalRunner 的评价 failure 只经 classify_evaluation，语义等于 D008 §6.2；
- VerificationRunner 之外没有第二个 gate + scheduler + Journal timing 循环；
- CoordinateSearch 同实例嵌套和 barrier 双线程结果互不污染；
- SearchCoordinator 省略 highest 或 coordinate_search 时构造失败；cli.build_context 显式构造二者；
- 静态 PASS probe 结束后其 PreparedEnvironment 已关闭；长期存活数不随历史 probe 数增长；
- Registry 畸形 response 只形成受控 infrastructure failure，不裸 traceback，异常 detail 不含 credential；
- CI matrix 的 Ruff、ty、pytest --no-testmon 和 build 全部通过；
- 公开 CLI、退出码、probe 顺序、FailureRecord disposition、terminal 文案和 D001 §10 范围不变。

## 17. 实施顺序

1. **安全与授权：** redaction chunk tests → CellSuccess/report 防篡改 tests → apply rollback/workspace transaction tests → 修实现。
2. **证据执行：** artifact selection 映射 → 精确安装/hash → registry 认证/输入矩阵。
3. **发现与门禁：** ProjectDiscovery + duplicate names + Explain/Diagnose 离线；CI matrix + Ruff/ty/pytest/build。
4. **持久化 identity：** Journal v2 + v1 reader + RunLogStore 独立测试。
5. **lookup：** cell_identity、failure_records_for_result、incomplete_reason；先分离 order，再删私有副本。
6. **编排：** VerificationRunner + VerificationRun/Task；三个 workflow 迁移；删除 Journal `hasattr`。
7. **分类输入：** classify_evaluation；Check 与 ProposalRunner 改调用，不复制 D008 语义表。
8. **搜索：** 表驱动与真重入测试 → 移 coordinate_search.py → 局部 state → 必注入实例。
9. **资源：** 静态 PASS 立即 close，final vector 重新 prepare；以 public search interface 测生命周期。
10. **后置：** terminal 包内拆 explain / diagnose。

行为修复先写能在现行代码上失败的 observable test。纯文件移动或 import 调整可以保持全程绿，不为满足形式而编写脆弱的内部结构测试。

## 18. 不变量

1. 凭据不进入任何持久化、portable schema、terminal 或 exception detail。
2. Candidate artifact evidence 与 exact-vector 执行是同一选择。
3. Apply 只消费闭合的 PASS evidence，失败不改变用户元数据。
4. Canonical package name 在一个 ProjectPlan / VerificationRun 内唯一。
5. Explain / Diagnose 只依赖离线 ProjectDiscovery，不依赖 planning/tool adapters。
6. Cell equality lookup、FailureRecord extraction 和 Evaluation classification input 各只有一个实现所有者。
7. Scheduler/events/logs 是 VerificationRunner 内部知识，不透传给三个 workflow 的共同函数。
8. Protocol 按 consumer 保持最小 surface；不为只有一个生产实现而扩大 interface。
9. CoordinateSearch 不导入 coordinator、Cyclopts、Rich、subprocess、TOML、Candidate artifact 或环境实现。
10. cli.py 仍是唯一 production composition root；不引入 DI 框架。
11. 业务 Rich 不出 pf.terminal 包。
12. D001 §10 仍是拒绝清单。

## 19. 决策记录

### D1：先补现行契约，再做纯重构（已确认）

日志保密、报告授权、精确 artifact 和 apply 恢复已经是 D001–D008 承诺。它们不是下一版产品，必须进入本轮 P0。

### D2：Discovery 与 Planning 分层（已确认）

Explain / Diagnose 只需要 package identity/path。让它们依赖完整 ProjectLoader 会引入 Python discovery 和外部工具；共享的深 seam 是 ProjectDiscovery，不是 ProjectLoader 实例。

### D3：VerificationRunner 使用构造依赖（已确认）

Scheduler、events、logs 在一次 CLI context 中稳定。构造时注入能隐藏共同编排；11 参数自由函数只搬运依赖，不能形成深 module。

### D4：Protocol 按 consumer 窄化（已确认）

生产与测试 adapter 都是 adapter。Check capture、Search evaluate、Full evaluate 的知识不同，不以“大一统 Protocol”制造假共用。

### D5：CoordinateSearch 验收真重入（已确认）

连续调用只能证明入口会重置。嵌套 evaluator 与双线程交错才能证明调用状态没有存在实例上。

### D6：Journal 按 package 保存 policy（已确认）

Workspace package config 可以不同；第一包不能代表整个 VerificationRun。v2 writer + v1 reader保留本机历史诊断可读性。

### D7：Apply 失败自动回滚（已确认）

命令返回失败时用户文件必须回到调用前状态。保留 target 等待下次“恢复提交”会把失败误当成功，且 workspace 会半应用。

### D8：Cell identity 不拥有 order（已确认）

同一 compatibility identity 可以有多个合法排序需求。Lookup 统一不等于 presentation/scheduling order 统一。

### D9：产品范围继续守住 D001 §10（已确认）

本轮修复既有证据、保密和恢复保证；不借机加入算法野心或新命令。

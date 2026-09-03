# R005 — PF 模块深化架构评审

- **状态：** 开放（SourcePlan 已由归档 [D019](../archived/designs/D019-pf-source-plan-depth.md) / [P025](../archived/plans/P025-pf-source-plan-depth.md) 解决；其余候选尚未设计或实施）
- **日期：** 2026-09-02
- **性质：** 非规范性架构评审；不定义命令、算法、Schema 或 module interface
- **对照：** `main` / `b8efadc`（`docs: archive diagnostic result card design`）
- **输入材料：** `architecture-review-20260902-123228.html`；同日一次未读取本文的独立实现评审，再对照源码与现行契约校准
- **契约所有者：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、[D003](../designs/D003-pf-search-algorithm.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、[D006](../designs/D006-pf-cli-enhancement.md)、[D008](../designs/D008-pf-verification-run.md)、[D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)
- **历史决策：** [D010](../archived/designs/D010-pf-v1-architecture.md)、[D017](../archived/designs/D017-pf-single-target-workspace-dependencies.md)、[D018](../archived/designs/D018-pf-diagnostic-result-cards.md)
- **前序评审：** [R002](../archived/reviews/R002-pf-v1-architecture-review.md)
- **已解决项：** [D019](../archived/designs/D019-pf-source-plan-depth.md) 与 [P025](../archived/plans/P025-pf-source-plan-depth.md) 已完成 §3 SourcePlan 候选；本文因其余候选继续开放而不归档

本文把输入材料中的四项架构建议，以及独立实现评审中经源码核对成立的中层 depth 问题，整理为可追踪的 Review，并对照当前源码与现行契约校准 ownership、优先级和完成标准。本文只记录时间点判断；任何被接受的 substantial 架构变更都必须先进入规范性 Design，再以 durable Plan 实施和验证。

评审沿用 `module`、`interface`、`implementation`、`seam`、`adapter`、`depth`、`leverage` 与 `locality`。Depth 取决于调用方通过较小 interface 获得多少行为，不取决于文件行数或 helper 数量。叶子层一批 module 已经是深的：`EnvironmentFactory.prepare`、`CoordinateSearch.minimize`、`ConfiguredVerifier.run`、`SnapshotBuilder`、`ProjectLoader`、`ValidatedReport` / `ReportStore`。剩余问题在中层 interface 税，不是实现行数不够。

## 1. 结论

本次没有发现新的 P0 正确性、安全或证据授权缺口。D016–D018 落地后的主要 module ownership 仍然成立；下列候选都是减少调用方知识、重复观察、假想 seam 和展示 mechanics 的架构优化，不能由本 Review 直接改写现行契约。

| 优先级 | 把握度 | 候选 | 主要收益 |
| --- | --- | --- | --- |
| P1 | Strong；已解决 | 深化 SourcePlan module | 已由 D019/P025 把逐 dependency route、mode、lookup、identity 与 dual-route 结构事实收回一个 locality |
| P1 | Strong | 把 workspace discovery 深化为一次 canonical inventory | 让 selection、planning facts、member facts 与 owned paths 来自同一次文件系统观察 |
| P2 | Worth exploring | 继续深化 Verification Run module | 从三个 workflow 收回 task assembly、Journal projection、association 与 deadline plumbing |
| P2 | Worth exploring | 合并评价层假想 Protocol | 三套同形 env/static/full Protocol 对应同一组生产 adapter；测试不再手造 `PreparedEnvironment` |
| P2 | Worth exploring | 深化 terminal-private result-card module | 统一 TTY/plain、literal path、宽度布局与 final emission mechanics |
| P3 | Later | 降低 `SearchCoordinator` 测试表面 | 协作槽位留在内部；替换走已有 `UvOperations` / `ProcessRunner` |

SourcePlan 已按 D019/P025 完成并归并到现行 owner；当前最高优先级开放项是 Workspace inventory，它有明确重复成本，但必须保持 offline discovery 轻量。独立实现评审主张先收成一个 Cell 评价 module；该方向看到了真实重复，但会越过 D002 对 `CompatibilityChecker`、`HighestVersionVerifier` 与 `SearchCoordinator` 的产品所有权，因此下调为「只合并假想 Protocol，不合并三个产品编排器」。Verification Run 与 result-card 的目标成立，具体 interface 尚不应在 Review 中定死。

## 2. 当前证据与校准

### 2.1 SourcePlan 已存在，但 interface 仍浅

`src/pf/schemas/project.py:93-161,721-764` 已定义 `DependencySourceRoute`、`SourcePlan`、identity 与 effective-source helper。当前 `SourcePlan` 的主要行为只有排序/唯一性验证；candidate、harness、environment、uv adapter、authorizer 与 report reader 仍分别执行 route lookup、mode 分支、dual-route 识别或 identity 计算：

- `src/pf/candidates.py:52-103` 同时构造 plan identity 和查询 effective source；
- `src/pf/harness.py:67-82` 重新查询 requirement source；
- `src/pf/environment.py:232-350,680-715` 构造 SourcePlan 后仍直接遍历 `package.source_routes`；
- `src/pf/adapters/uv.py:704-723` 自行 lookup route 并推导 workspace source suppression；
- `src/pf/authorization.py:104-114,215-257` 比较完整 SourcePlan 后又直接解释 workspace member metadata；
- `src/pf/report.py:1939-1965,2038-2083` 重新建立 route map 并闭合 report/candidate evidence。

因此，材料所说的“一个数据结构、多个懂规则的调用方”成立。需要校准的是：SourcePlan 应吸收共享的 source facts，不应夺走 ProjectLoader、UvAdapter、ApplyAuthorizer 或 ReportStore 的领域决定权。独立实现评审把 CandidateBuilder / harness 判为 deep，是因为它们的方法面藏住了冻结与变换规则；source facts 本身仍浅，调用方继续各自解释 `source_routes`。

### 2.2 Workspace facts 来自多轮观察

`ProjectDiscovery.select` 读取 root、展开 workspace candidates 并读取成员 identity；`ProjectDiscovery.owned_pyproject_paths` 再次读取 root、workspace 与递归 path package。随后 `ProjectLoader.load` 先调用前两者，再由 `_load_package` 重读 root/target，并由 `_workspace_members` 再次展开成员、读取 path 与 version facts（`src/pf/project_discovery.py:23-175`；`src/pf/project.py:143-193,783-863`）。

这不构成当前行为错误，但同一 invocation 中存在重复 traversal、重复 TOML parse 和观察时序差异。当前 `package identity changed during project loading` 防线也反映出 selection 与 planning 并非消费同一份 observation。

材料建议的“一次 canonical inventory”成立；但 inventory 不能让 `explain` / `diagnose` 因未选中成员的 planning-only metadata 而失败，也不能把 declaration、config、Cell 或 source classification 从 ProjectLoader 移走。ProjectDiscovery 与 ProjectLoader 作为 module 可以继续深；重复的是它们之间的 filesystem observation，不是要把两者合成一个 module。

### 2.3 VerificationRunner implementation 已深，task interface 仍泄漏 mechanics

`VerificationRunner` 已独占 generic Scheduler、deadline result、completion projection、Journal timing 与 association（`src/pf/verification.py:99-295`），符合 D010/D008。剩余问题在 `VerificationTask` interface：每个 workflow 仍构造 `execute` closure、`journal_entries` callback、可选 `runtime_associations` callback 和 `deadline_scope`；Check、Smoke、Search 分别保留相似的 task assembly 与 Journal projection（`src/pf/workflow.py:324-423,472-574,621-814`）。

`completion_outcome(result: object)`（`src/pf/verification.py:295`）再用一长串 `isinstance` 认识 `CheckCellOutcome`、`HighestVersionPass`、`BaselineRejection`、`CellSuccess`、`CellSearchFailure`、`CellIndeterminate` 与 `PassEvaluation`。Runner 并未因 `Generic[T]` 而变成领域无关：journal / deadline 知识在调用方，具体 outcome 类型又漏回 runner。

三条在线 command 还各自复制 load → snapshot → host cell → `VerificationRun` 装配。空 cell 与 `require_full_evaluation_contract` 的处理已经分叉。若 Verification Run 深化成立，这段 planning 管道作为同一 Design 的一部分收回；不另建 `run_host_cells` 总模块。

继续深化有潜在 leverage，但不能把 generic Scheduler 收成 Runner 私有实现或恢复 D010 已删除的 domain leakage，也不能用一个参数与 callback 数量接近现有 implementation 的 wrapper 假装抽象已经完成。Scheduler 是与 `CoordinateSearch` 同类的 in-process 算法 module；问题在 `VerificationTask`，不在 Scheduler 独立存在。

### 2.4 Result-card primitive 已共享，但仍只封装 Rich mechanics 的薄片

`src/pf/terminal/__init__.py:123-179` 已有 `_result_card`、`_plain_result_card`、`_fact_grid` 与 `_path_text`。它们统一了 Panel、gutter、grid 和 path text 的局部构造，但 explain、diagnose、apply、merge 与 typed command errors 仍各自组装 rows、选择 TTY/plain renderable、打印 card 和 final（例如 `terminal/_explain.py:111-203`、`terminal/_diagnose.py:93-203`、`terminal/__init__.py:652-690,1215-1375`）。

因此，D002/D006 所要求的共享 primitive 已存在，材料建议针对的是 implementation depth，而不是补一份新的产品契约或建立 public rendering interface。把全部 `render_*` 收成一个 public `render(command-result union)` 会把 D006 的命令信息层级卷进同一 interface，不是 deepening。

### 2.5 评价层 Protocol 是假想 seam，三个产品编排器不是

`CheckEnvironmentOperations`（`src/pf/workflow.py:124-133`）、`HighestEnvironmentOperations`（`src/pf/baseline.py:32-41`）与 `SearchEnvironmentOperations`（`src/pf/search.py:125-134`）方法面同形；static capture / full evaluate 同样各抄一份。生产装配在 `src/pf/cli.py:387-405` 把同一个 `EnvironmentFactory`、`StaticEvaluator`、`RuntimeEvaluator` 注入三处——只有一个生产 adapter，三套 Protocol 是假想 seam。

`src/` 里唯一构造 `PreparedEnvironment(...)` 的是 `EnvironmentFactory`（`src/pf/environment.py:544`）。`tests/test_baseline.py`、`tests/test_check.py`、`tests/test_search_coordinator.py`、`tests/test_evaluation.py` 与 `tests/test_static_transition.py` 手造该构造器，测试表面低于 `prepare()`。

三个编排器的产品角色仍然不同，D002 明确拆开：

- `HighestVersionVerifier.verify`：一次 highest prepare，full evaluate，并在 `finally` 中 `close()`；
- `CompatibilityChecker.check`：先 highest capture 并关闭，再按 `harness_baseline` 做 lowest-direct prepare 与 evaluate；
- `SearchCoordinator.search`：baseline → candidates → coordinate search，并由私有 `_ProposalRunner` 管理 prepare 缓存。

prepare→evaluate→classify 的步骤相似，不构成把三种 outcome 收进一个 Cell 评价 module 的理由。正确动作是合并假想 Protocol，让三个 owner 共用同一套真实 seam。`CompatibilityChecker` 与七个 command workflow 同住 `workflow.py` 是文件边界问题；Protocol 合并后 Cell 评价类型离开该文件，不作为独立 P1。

`FailurePolicy` 可选注入且只有一个生产 adapter，`classify` 参数面宽。这是次要 interface 税，跟在评价 Protocol 合并之后处理，不单独开 Design。

### 2.6 `SearchCoordinator.search` 已是外部 interface，测试表面偏高

`SearchCoordinator.search(...)`（`src/pf/search.py:984-991`）藏住了 candidate discovery、`_ProposalRunner`、coordinate minimize 与 region 回填。要正确构造它，仍须提供 `environments` / `candidates` / `static` / `full` / `highest` / `coordinate_search` 以及两个 consumer（`src/pf/search.py:933-954`）。`tests/test_search_coordinator.py` 用假 `prepare` 绕过 `EnvironmentFactory`，等于在外层重编码环境生命周期。

这是测试表面高度问题，不是再画一条产品 seam。替换应走已有真实 adapter（`UvOperations`、`ProcessRunner`、`ConfiguredVerifier`），不再在每个评价步骤上开 Protocol。

### 2.7 已核对但不作为候选的判断

独立实现评审中下列主张经源码与 D002 核对后不进入候选表：

- ApplyAuthorizer 在授权路径调用 `PackageReportBuilder.project` 是 D002 赋予的 intended-requirement 权威，不是应当改为只读 `ValidatedReport.projection_evidence` 的泄漏。
- `UvOperations` 是 EnvironmentFactory 内部的真实 seam（生产 `UvAdapter` + 测试 fake），位置正确；宽步骤面可在 Factory 内部继续加深，不对外暴露为新 module。

## 3. P1：深化 SourcePlan module

### 3.1 问题

调用方收到 `routes + source_mode` 后仍需知道：

- 如何按 canonical dependency 唯一 lookup；
- development/search mode 如何选择 effective source；
- 哪些 route 是 workspace development → registry search；
- 哪些结构事实可用于 candidate、harness、resolution、suppression、apply 与 report identity；
- stable identity 必须覆盖 route、mode 与 workspace member version metadata。

这些知识跨越多个调用方，降低 locality；增加 source 变体或调整 dual-route 资格时，需要同步修改多处。

### 3.2 方向

把现有 `SourcePlan` 深化为逐 Run 的 canonical source-facts module。它从 ProjectLoader 已分类的 route 与 Run mode 构造，向调用方提供少量结构化查询，内部统一完成 effective lookup、stable identity、dual-route facts 与 workspace member metadata lookup。

Ownership 必须保持：

| Owner | 保留职责 |
| --- | --- |
| ProjectLoader | 读取项目声明并分类 development/search route、registry、workspace member 与 version metadata |
| SourcePlan | canonical route set、Run mode、effective source、stable identity 与可复用的结构事实 |
| UvAdapter | 把 SourcePlan facts 翻译为 uv argv，并独占 source suppression 的 adapter policy |
| ApplyAuthorizer | 判断 report/current plan 是否一致、workspace member version 是否满足 intended requirement |
| ReportStore / D014 | wire codec、public locator 与 report evidence authority |

不要增加第二套 source identity、缓存原始 TOML，或把 uv argv、apply decision、report validation 搬进 SourcePlan。Schema 是否继续直接承载该 module，或由 domain module 包住 wire record，应由后续 Design 一次决定，不能叠加兼容层。

该候选已由归档 [D019](../archived/designs/D019-pf-source-plan-depth.md) 与
[P025](../archived/plans/P025-pf-source-plan-depth.md) 实施、验收并归并到现行 owner。本节以下完成
标准保留为评审来源；当前行为只由现行 owner 定义。

### 3.3 完成标准

- Candidate、Harness、Environment、UvAdapter、ApplyAuthorizer 与 ReportStore 不再各自重复 effective route lookup、mode 选择或 dual-route 结构判定；consumer-specific authority 仍留在原 owner。
- 一个 Run 只形成一个 canonical SourcePlan identity；Attempt、CandidateSnapshot、resolution 与 report 继续闭合到同一 identity。
- `PackagePlan.source_routes`、Run mode 与任何新 interface 不形成平行身份或双读路径。
- SourcePlan interface 测试覆盖缺失 dependency、DEVELOPMENT/SEARCH、local workspace route、workspace→registry dual route、static/dynamic member metadata 与稳定 identity。
- 调用方测试只断言通过其 public seam 可观察的 source outcome 或 argv，不依赖 SourcePlan 私有 helper。

## 4. P1：建立 canonical WorkspaceInventory

### 4.1 问题

同一 workspace 的 root/member/path facts 被 discovery 与 loader 分阶段丢弃后重读。调用方也需要理解先 `select`、再 `_load_package`、再 `owned_pyproject_paths` 的观察时序。重复 I/O 本身不是主要性能瓶颈；更重要的问题是同一 invocation 的 selection、member classification 和 snapshot owned paths 没有天然闭合到一份观察。

### 4.2 方向

让 ProjectDiscovery 一次产生不可变的 canonical `WorkspaceInventory`，至少能投影：

- root 与全部可安装 package location；
- canonical name uniqueness 与 selector lookup；
- workspace membership/path，以及供 planning 使用的 version observation；
- root、workspace candidates 与递归 in-tree path package 的 owned pyproject paths。

`ProjectLoader` 消费同一 inventory，只为选中 target 完成 config、declaration、Cell、source route 与 harness planning。`ExplainCommandWorkflow` / `DiagnoseCommandWorkflow` 只消费 location/report facts。

Inventory 属于 local-substitutable filesystem module：使用真实临时目录测试即可，不需要为唯一生产文件系统引入 public port 或通用 repository。它可以保留一次读取的内部 TOML observation，但不能把原始 TOML 暴露为外部 interface。

### 4.3 完成标准

- 一次 command invocation 中，selection、workspace member facts 与 owned paths 来自同一 inventory；root/member glob 与 TOML parse 不在 discovery/loader 间重复执行。
- offline `explain` / `diagnose` 仍不启动 uv、不构造 Cell，也不因 planning-only metadata validation 扩大失败面。
- ProjectLoader 继续唯一拥有 declaration/config/Cell/source classification；ProjectDiscovery 不知道 evaluation policy 或 verification。
- root package、virtual root、include/exclude glob、重复 canonical name、递归 path package、越界 path、static/dynamic member version 与 observation drift 都通过 public discovery/loader seam 测试。
- SourceSnapshot 仍是执行与 apply authorization 的持久 evidence；inventory 不替代 snapshot identity、drift check 或 immutable materialization。

## 5. P2：继续深化 Verification Run module

### 5.1 问题

Runner 的 implementation 已拥有 lifecycle，但 caller-facing task interface 仍暴露 closure、Journal projection、association 与 deadline failure 所需知识。删除当前 `VerificationTask` 后，这些 mechanics 会重新散回三个 workflow，说明还有可收回的复杂度。`completion_outcome` 对开放 `object` 的 overload，以及三条 workflow 各自投影 journal，是同一泄漏的具体证据。

### 5.2 方向

后续 Design 应比较至少两种更窄的 interface，例如按 command 判别的 run request，或 Runner 内部的 command-specific task assembly。共同目标是：workflow 只提供选中 package、command-specific Cell operation 和 invocation policy；Runner 内部拥有 host Cell task lifecycle、deadline outcome、completion projection、Journal entry 与 Process Log association timing。host 过滤、snapshot close 与契约检查若能随同一 interface 变窄，则一并收回；Search 独有的 report write 仍留在 Search workflow。

Command workflow 继续拥有 load/snapshot、Search report write、最终结果聚合与命令返回类型。Scheduler 继续只理解 task、worker、deadline callback、clock 与 order，不导入 Failure、Evaluation、CellResult、Journal 或 terminal facts。

### 5.3 进入实施前的判定条件

- 新 interface 明显小于当前 `VerificationTask + VerificationRun` 的参数、callbacks 与隐含顺序；删除新 module 会把 lifecycle 复杂度重新分散到三个 workflow。
- Check、Smoke、Search workflow 不再自行构造 Journal entries、runtime associations 或 scheduler-deadline FailureRecord。
- 三条 workflow 的 typed outcome 与聚合 ownership 不变；Search 独有的 report/association replacement 不被泛化进 Runner。
- `completion_outcome` 不再对任意 `object` 做开放 overload；投影从 Runner 已拥有的统一 cell outcome 派生。
- 新 Runner tests 通过 interface 覆盖并发完成顺序、deadline 未启动 Cell、Journal 先于 diagnose availability、persist failure 和 deterministic clock；不保留同语义的旧 shallow tests 叠层。
- 若无法在不引入宽 callback interface 或 command union 膨胀的前提下满足以上条件，则保留现状。

## 6. P2：合并评价层假想 Protocol

### 6.1 问题

Check / Highest / Search 各维护一套 env/static/full Protocol，生产路径却注入同一组 adapter。改 `prepare` 签名或 failure 映射是 shotgun surgery；测试手造 `PreparedEnvironment` 证明外部 seam 画在了错误高度。这是 interface 重复，不是三个产品角色应当合并的证据。

### 6.2 方向

后续 Design 只比较如何让三个 owner 共用一套真实的 environment / static / full seam（直接使用生产类型，或一份共享 Protocol）。`PreparedEnvironment` 构造器退回 `EnvironmentFactory` 私有实现；需要替换 uv/ty/verifier 时从已有真实 adapter 下手。

`HighestVersionVerifier`、`CompatibilityChecker` 与 `SearchCoordinator` 继续分别拥有 highest full-verify、declaration two-phase check 与单 Cell search 状态机。`FailurePolicy` 的 facts record 化若能随同一 Design 缩小 `classify` 参数面，可以一并做；单独包一层 wrapper 则不做。

### 6.3 进入实施前的判定条件

- 源码中只剩一套 env/static/full seam；三套 `*Operations` Protocol 消失或退化为别名并在同一变更中删除。
- 评价相关测试穿过 `prepare()` / Evaluator public seam，或穿过 `UvOperations` / `ProcessRunner` / `ConfiguredVerifier`；不再直接构造 `PreparedEnvironment`。
- Check 的 two-phase prepare、Highest 的 `close()`、Search 的 prepare 缓存与 coordinate 状态机仍由原 owner 测试，产品 outcome union 不合并。
- 新 interface 小于「三个产品方法 + 三套步骤 Protocol」；若结果只是再包一层同样宽的评价 facade，则不实施。

## 7. P2：深化 terminal-private result-card module

### 7.1 问题

现有 helper 统一了单个 Rich 构件，却没有隐藏完整 card lifecycle。每个 command renderer 仍知道 rows 形状、marker gutter、TTY/plain 选择、literal path、console print 与 final 顺序；宽度或 plain parity 缺陷可能需要跨多个 renderer 修复。

### 7.2 方向

建立更深的 terminal-private result-card implementation，让它消费 terminal-private structured presentation facts，统一：

- outcome marker、gutter、section/fact layout；
- TTY Panel 与 non-TTY plain 的同层级输出；
- literal path、折行与 OSC 8 link mechanics；
- card 与唯一 final summary 的 emission 顺序。

`TerminalPresenter` 继续拥有 domain outcome → presentation facts、stdout/stderr channel、最终 exit decision 和 public test surface。该 module 不成为 domain Schema，不进入 report/Journal/identity，也不向 workflow 暴露 Rich 类型。命令卡片仍按命令走 public `render_*`；D006 继续拥有各命令的信息层级。

### 7.3 进入实施前的判定条件

- Explain、diagnose、apply/minimize、merge 与 typed command errors 共用同一 private card lifecycle，而不仅是相同 `Panel(...)` helper。
- TerminalPresenter 的 public interface、channel 与 exit semantics 不变；不同 command 的信息层级和领域措辞仍由 D006 拥有。
- 56/80/120 列、TTY/non-TTY、literal path/ID、stdout/stderr 和 exactly-one-final 都通过 public presenter/CLI seam 验证。
- 测试断言稳定语义与可观察顺序，不断言 private row objects、helper 名称、ANSI 边框或整段 volatile snapshot。
- 若抽取结果只是把 command-specific row assembly 搬到同样数量的函数，未减少 caller knowledge，则不实施。

## 8. P3：降低 `SearchCoordinator` 测试表面

### 8.1 问题

`search(...)` 已经是外部 interface，但测试学习的是构造器上的协作槽位和手造 `prepare`。Interface is the test surface：当前测试证明调用方仍须知道内部步骤。

### 8.2 方向

协作对象留在 coordinator 内部。需要替换时从 `UvOperations` / `ProcessRunner` / `ConfiguredVerifier` 这些已有真实 adapter 的 seam 下手。本项是测试纪律，等 P2 评价 Protocol 合并后再做；不作为独立架构 Design 的第一项。

### 8.3 进入实施前的判定条件

- coordinator 测试不再手造 `PreparedEnvironment`，也不再为每个评价步骤提供独立 Protocol fake。
- `search(...)` 的可观察 outcome（`CellResult`、probe evidence、region）仍是断言表面。
- 不引入新的评价步骤 Protocol，也不把 coordinator 与 Highest/Check 收成一个 module。

## 9. 建议顺序与非目标

建议按独立 Design 逐项推进，避免一次改动多个 seam：

1. SourcePlan 已按归档 [D019](../archived/designs/D019-pf-source-plan-depth.md) 与
   [P025](../archived/plans/P025-pf-source-plan-depth.md) 完成；多处 route/mode 知识已经删除。
2. 再建立 WorkspaceInventory；它收敛 SourcePlan 上游的 filesystem observation，但不必与 SourcePlan 合成一个 module。
3. Verification Run 先做 interface alternatives 与删除测试，只有明显变深时才实施。
4. 合并评价层假想 Protocol；三个产品编排器保持独立 owner。
5. Result-card 在下一次真实跨命令展示改动前设计，避免为纯整理制造 churn。
6. SearchCoordinator 测试表面跟在第 4 项之后，作为测试改写而不是新的产品 module。

本 Review 建议保持的结构：

- SourcePlan 只提供 source facts；uv argv、apply authority、report wire validation 留在原 owner。
- ProjectDiscovery 继续只做 filesystem inventory；ProjectLoader 继续独占 planning；offline command 不执行完整 planning。
- Scheduler 继续作为领域无关的算法 module；Verification Run 深化的是 `VerificationTask` 调用面。
- Check / Highest / Search 继续作为三个产品编排器；共享的是 env/static/full seam。
- ApplyAuthorizer 继续重求值 intended requirement；`PackageReportBuilder` 继续拥有 projection。
- TerminalPresenter 继续按命令暴露 `render_*`；card lifecycle 留在 terminal-private implementation。
- 现行契约一次替换，不为旧 interface 添加 alias、adapter 或双读/双写兼容层。
- 不引入 DI framework、通用 repository、event bus、service layer 或常驻 daemon。

## 10. 本次验证范围

本次整理完成了以下静态核对：

- 读取输入 HTML 的四项候选、推荐等级、文件定位与 design constraints；
- 对照一次未读取本文的独立实现评审：采纳评价层假想 Protocol 与 SearchCoordinator 测试表面，把「Cell 评价上帝 module」下调为 Protocol 合并，否决 Scheduler 私有化、ApplyAuthorizer 停止重求值、以及把全部 `render_*` 收成一个 public `render`；
- 对照 `b8efadc` 的 D001/D002/D006/D008/D012/D014，以及 D010/D017/D018 历史 ownership；
- 读取 SourcePlan 六类调用方、ProjectDiscovery/ProjectLoader、VerificationRunner/三个 workflow、check/baseline/search Protocol、`cli._assemble_context`、`PreparedEnvironment` 测试构造点，以及 terminal renderer 的当前实现；
- 确认上表均为未实施的 module-depth 候选，不把它们写成现行行为或已确认缺陷。

本次未运行 pytest、ty、build 或真实 PF command；文档不声称行为验证。

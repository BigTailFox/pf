# R005 — PF 模块深化架构评审

- **状态：** 开放（SourcePlan、WorkspaceInventory 与轨 B 已解决；评价 seam、terminal result-card 与 SearchCoordinator 测试表面尚未设计或实施）
- **日期：** 2026-09-02
- **性质：** 非规范性架构评审；不定义命令、算法、Schema 或 module interface
- **对照：** `main` / `b8efadc`（`docs: archive diagnostic result card design`）
- **改进方案核对：** `main` / `c1aff33`（2026-09-03；SourcePlan 深化后的剩余项）
- **轨 B Design 核对：** `main` / `e570cea`（2026-09-03；WorkspaceInventory 深化后的当前实现）
- **输入材料：** `architecture-review-20260902-123228.html`；同日一次未读取本文的独立实现评审，再对照源码与现行契约校准
- **契约所有者：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、[D003](../designs/D003-pf-search-algorithm.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、[D006](../designs/D006-pf-cli-enhancement.md)、[D008](../designs/D008-pf-verification-run.md)、[D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)
- **历史决策：** [D010](../archived/designs/D010-pf-v1-architecture.md)、[D017](../archived/designs/D017-pf-single-target-workspace-dependencies.md)、[D018](../archived/designs/D018-pf-diagnostic-result-cards.md)
- **前序评审：** [R002](../archived/reviews/R002-pf-v1-architecture-review.md)
- **已解决项：** [D019](../archived/designs/D019-pf-source-plan-depth.md) / [P025](../archived/plans/P025-pf-source-plan-depth.md) 已完成 §3 SourcePlan；[D020](../archived/designs/D020-pf-workspace-inventory.md) / [P026](../archived/plans/P026-pf-workspace-inventory.md) 已完成 §4 WorkspaceInventory；[D021](../archived/designs/D021-pf-verification-run-request.md) / [P027](../archived/plans/P027-pf-verification-run-request.md) 已完成 §5 / §11.3 Verification Run request；本文因其余候选继续开放而不归档

本文把输入材料中的四项架构建议，以及独立实现评审中经源码核对成立的中层 depth 问题，整理为可追踪的 Review，并对照当前源码与现行契约校准 ownership、优先级和完成标准。本文只记录时间点判断；任何被接受的 substantial 架构变更都必须先进入规范性 Design，再以 durable Plan 实施和验证。

评审沿用 `module`、`interface`、`implementation`、`seam`、`adapter`、`depth`、`leverage` 与 `locality`。Depth 取决于调用方通过较小 interface 获得多少行为，不取决于文件行数或 helper 数量。叶子层一批 module 已经是深的：`EnvironmentFactory.prepare`、`CoordinateSearch.minimize`、`ConfiguredVerifier.run`、`SnapshotBuilder`、`ProjectLoader`、`ValidatedReport` / `ReportStore`。剩余问题在中层 interface 税，不是实现行数不够。

## 1. 结论

本次没有发现新的 P0 正确性、安全或证据授权缺口。D016–D018 落地后的主要 module ownership 仍然成立；下列候选都是减少调用方知识、重复观察、假想 seam 和展示 mechanics 的架构优化，不能由本 Review 直接改写现行契约。

| 优先级 | 把握度 | 候选 | 主要收益 |
| --- | --- | --- | --- |
| P1 | Strong；已解决 | 深化 SourcePlan module | 已由 D019/P025 把逐 dependency route、mode、lookup、identity 与 dual-route 结构事实收回一个 locality |
| P1 | Strong；已解决 | 把 workspace discovery 深化为一次 canonical inventory | 已由 D020/P026 让 selection、planning facts、member facts 与 owned paths 来自同一次文件系统观察 |
| P2 | Strong；已解决 | 继续深化 Verification Run module | D021/P027 已从三个 workflow 收回 task assembly、Journal projection、association 与 deadline plumbing |
| P2 | Worth exploring | 合并评价层假想 Protocol | 三套同形 env/static/full Protocol 对应同一组生产 adapter；测试不再手造 `PreparedEnvironment` |
| P2 | Worth exploring | 深化 terminal-private result-card module | 统一 TTY/plain、literal path、宽度布局与 final emission mechanics |
| P3 | Later | 降低 `SearchCoordinator` 测试表面 | 协作槽位留在内部；替换走已有 `UvOperations` / `ProcessRunner` |

SourcePlan 与 WorkspaceInventory 已分别按 D019/P025、D020/P026 完成并归并到现行 owner。轨 B 也已由 [D021](../archived/designs/D021-pf-verification-run-request.md) / [P027](../archived/plans/P027-pf-verification-run-request.md) 完成 command-discriminated Verification Run request，稳定规则已由 D002/D006/D008 接管并同步归档。评价 seam、result-card 与 SearchCoordinator 测试表面继续按独立轨道处理。独立实现评审主张先收成一个 Cell 评价 module；该方向看到了真实重复，但会越过 D002 对 `CompatibilityChecker`、`HighestVersionVerifier` 与 `SearchCoordinator` 的产品所有权，因此仍校准为「只合并假想 Protocol，不合并三个产品编排器」。

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

[D020](../archived/designs/D020-pf-workspace-inventory.md) / [P026](../archived/plans/P026-pf-workspace-inventory.md) 已完成本节方向；稳定 interface 与 ownership 已归并到 D002。

### 4.3 完成标准

- 一次 command invocation 中，selection、workspace member facts 与 owned paths 来自同一 inventory；root/member glob 与 TOML parse 不在 discovery/loader 间重复执行。
- offline `explain` / `diagnose` 仍不启动 uv、不构造 Cell，也不因 planning-only metadata validation 扩大失败面。
- ProjectLoader 继续唯一拥有 declaration/config/Cell/source classification；ProjectDiscovery 不知道 evaluation policy 或 verification。
- root package、virtual root、include/exclude glob、重复 canonical name、递归 path package、越界 path、static/dynamic member version 与 observation drift 都通过 public discovery/loader seam 测试。
- SourceSnapshot 仍是执行与 apply authorization 的持久 evidence；inventory 不替代 snapshot identity、drift check 或 immutable materialization。

## 5. P2：继续深化 Verification Run module

[D021](../archived/designs/D021-pf-verification-run-request.md) 已将本节与 §11.3 收敛为临时性迁移目标，
[P027](../archived/plans/P027-pf-verification-run-request.md) 已完成实现、验证、owner 归并与同步归档。
下列内容继续保留为 Review 来源；当前行为只由现行 owner 定义。

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
2. WorkspaceInventory 已按 [D020](../archived/designs/D020-pf-workspace-inventory.md) / [P026](../archived/plans/P026-pf-workspace-inventory.md) 完成；它收敛 SourcePlan 上游的 filesystem observation，但不与 SourcePlan 合成一个 module。
3. Verification Run 已按D021/P027完成interface alternatives、删除测试、实现、owner归并与同步归档。
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

## 11. 剩余问题改进方案

本节基于 `c1aff33` 重新核对 R005 的开放项。它给出后续 Design 的推荐拆分、目标 interface、
迁移边界和验收证据，但仍属于非规范性 Review，不构成实施授权。每一轨都必须先形成独立的临时
Design 并获得接受，再建立映射全部验收项的 durable Plan；不能用本节替代 Design 或 Plan。

### 11.1 总体决策

剩余项不合成一个通用 planning、evaluation 或 rendering layer。按四条独立轨道推进：

| 顺序 | 独立 Design 范围 | 目标 | 进入条件 |
| --- | --- | --- | --- |
| A | [`WorkspaceInventory`](../archived/designs/D020-pf-workspace-inventory.md) | 一次 planning invocation 只读取并解释一份 workspace observation | 已由 D020/P026 完成 |
| B | [Verification Run request](../archived/designs/D021-pf-verification-run-request.md) | 删除 workflow 暴露的 task callback、Journal、association 与 deadline mechanics | D021/P027 已实现、通过验收并同步归档 |
| C | 评价 seam 与 SearchCoordinator tests | 删除三套同形 Protocol，并把测试替换移到已有真实 adapter seam | B 后进入；SearchCoordinator 测试整改并入本轨 |
| D | terminal-private result card | 一个私有 emitter 独占 TTY/plain、path、layout 与 final emission | 下一次真实跨命令展示变更触发，不为纯搬运提前实施 |

四轨只共享以下约束，不共享新的 facade：

- `ProjectLoader`、三个产品评价编排器、`Scheduler`、`TerminalPresenter` 的现有产品 ownership 不合并；
- CLI grammar、退出码、Schema 1、Failure/Attempt/SourcePlan identity 与 report/apply authority 默认不变；
- PF 尚未发布，接受后的 interface 原地替换，旧 Protocol、callback、helper 和测试路径在同一轨删除，
  不保留 alias、兼容 adapter 或双轨测试；
- 任一轨若不能通过删除测试，或新 interface 不小于被替换的调用知识，则停止该轨并保留现状；
- 每轨完成时只把稳定规则归并到对应现行 owner；四轨全部完成后才归档 R005。

### 11.2 轨 A：canonical WorkspaceInventory

#### 目标形状

在线 planning 保持 `ProjectLoader.load(root, selector) -> ProjectPlan` 这一外部 interface；
`ProjectLoader` 内部先取得一个不可变 inventory，再从同一对象完成 selection、workspace member facts
与 owned paths 投影：

```text
ProjectDiscovery.inventory(root, selector) -> WorkspaceInventory
WorkspaceInventory.target -> PackageLocation
WorkspaceInventory.owned_pyproject_paths -> tuple[str, ...]
WorkspaceInventory.workspace_member_for(name) -> member facts | None

ProjectLoader.load(root, selector)
    -> one inventory
    -> inventory.target
    -> PackagePlan + inventory.owned_pyproject_paths
    -> ProjectPlan
```

Inventory 可以在 `project_discovery` / `project` implementation 内保存一次读取的不可变 TOML
observation，供 `ProjectLoader` 与 `ConfigLoader` 消费；raw document 不进入 public workflow interface、
`ProjectPlan`、Schema 或 report。`PackageLocation`、member version 和 owned path 都必须从该 observation
派生，不能由 loader 再读同一路径重建。

`explain` / `diagnose` 继续走轻量 location lookup，只验证选择和报告 identity；它们不构造 full
inventory，不验证未选中成员的 version、dependency、PF config 或 Cell。为此，离线 `select` 可以保留
为独立轻量入口；禁止用 `mode` flag 让一个宽 inventory interface 同时承担 offline 与 planning。

#### Ownership 与迁移切片

1. 先定义 inventory 的不可变 location/member/owned-path facts，以及唯一的 filesystem read owner；
2. 把 workspace glob、exclude、canonical name、member version 与 recursive in-tree path traversal 收入
   inventory；同一路径的 bytes 在一次 inventory 中只解析一次；
3. 让 `ConfigLoader` 消费 inventory 的私有 observation 并继续独占三层 config 解析/合并；
   `ProjectLoader` 继续独占 declaration、Cell、source route 与 harness planning；
4. `ProjectLoader.load` 改为只消费一个 inventory，删除 identity-change 补丁式二次观察和独立
   `owned_pyproject_paths()` traversal；
5. 保持 `ProjectPlan.owned_pyproject_paths` 作为 `SnapshotBuilder` 输入；inventory 不进入持久 evidence，
   不替代 `SourceSnapshot`、搜索结束 drift check 或 apply CAS；
6. 删除旧 discovery/loader 重复读取测试，改由 public `ProjectDiscovery` / `ProjectLoader` seam 证明一次
   observation 及错误语义。

#### 验收与停止条件

- 一次 `ProjectLoader.load` 中 root、target、workspace candidates 与递归 path package 各自最多形成一份
  TOML byte observation；selection、member version 和 owned paths 全部来自该 inventory；
- root package、virtual root、include/exclude glob、重复 canonical name、missing pyproject、越界/symlink、
  static/dynamic member version 与递归 path package 都有 public behavior 测试；
- offline `explain` / `diagnose` 的读取范围和失败面不扩大，且不创建 uv/process/evaluation 能力；
- `PackagePlan`、`ProjectPlan` 与 Schema 1 不保存 raw TOML 或第二份 snapshot identity；
- 若消除重读必须把 planning-only validation 推给离线命令，或让 inventory 接管 declaration/Cell/source
  classification，则停止并缩回 Design。

### 11.3 轨 B：command-discriminated Verification Run

[D021](../archived/designs/D021-pf-verification-run-request.md) 已按本节形成并获得接受，进一步比较单一判别 request、
三个 verb-first entry point 与 registered Run session，并选择前者；[P027](../archived/plans/P027-pf-verification-run-request.md)
已完成该目标的实现、测试、证据、owner 归并与同步归档。下列原始方向继续作为追踪来源。

#### Interface 比较与选择

后续 Design 必须至少比较两种形状：

1. **推荐：command-discriminated run request。** `run(...)` 接收 Check、Smoke 或 Search request；每种
   request 只携带 package、SourcePlan、snapshot、host target、该命令的 Cell operation、jobs 与合法的
   duration。Runner 内部选择 Cell、建立 task、投影 completion/Journal/association/deadline，并保持返回
   outcome 的精确类型。
2. **否决基线：通用 result envelope。** operation 返回 `result + completion + journal entries +
   associations`。该形状只是把现有 callbacks 搬进另一个 record，workflow 或 operation 仍须知道全部
   lifecycle mechanics，不能通过删除测试。

目标 interface 形状为：

```text
VerificationRunner.run(
    CheckVerificationRun | SmokeVerificationRun | SearchVerificationRun
) -> tuple[command-specific outcome, ...]
```

request 是 D008 内部的判别联合，不进入 report wire。Runner 继续验证 command/SourcePlan/package，
并新增统一 host Cell selection、full-evaluation contract、matrix event、deadline scope 和 task assembly。
`Scheduler` 仍只看到 `ScheduledCellTask`、worker、deadline callback、clock 与排序。

#### 迁移结果

- 删除 public `VerificationTask`、四个 callback/data 槽位、三个 workflow 的 `_cell_task` 与 Journal /
  runtime association projector；
- 把 `completion_outcome(object)` 拆为 Runner 私有的 command-specific projection，不再对开放 `object`
  overload；
- Check、Smoke、Search workflow 只拥有 load/snapshot、SourcePlan 构造、命令结果聚合；Search 继续独占
  report update 与 report-generation association replacement；
- Journal durable-before-diagnose、persist failure、deterministic completion order 和 scheduler deadline
  仍由 Runner 独占；不把 report write 或 terminal facts 收进 Runner。

#### 验收与停止条件

- workflow 不再构造 task closure、Journal entry、runtime association 或 deadline failure；
- 新 request 的字段总量与隐含顺序显著小于 `VerificationRun + VerificationTask + callbacks`；
- public Runner tests 覆盖三类 outcome、并发完成顺序、未启动 Cell deadline、Journal 写入时序、
  association 与 persist failure；旧 shallow tests 同步删除；
- 若判别联合迫使所有命令学习彼此字段，或需要再加一个参数面等宽的 generic task facade，则不实施。

### 11.4 轨 C：删除评价层假想 Protocol，并重写 SearchCoordinator 测试

#### 目标形状

`EnvironmentFactory.prepare`、`StaticEvaluator.capture/evaluate` 与 `RuntimeEvaluator.evaluate` 已是深
module interface。三个产品编排器直接依赖这些现有 module；删除
`Check*Operations`、`Highest*Operations` 和 Search 的同形 env/static/full Protocol，不再为每个 consumer
复制一套结构类型。若后续 Design 证明 production adapter 与一个有独立行为的长期 test adapter 都需
稳定满足该 seam，可以收敛为一套共享 Protocol；一次性 duck-typed fake 不足以保留三套平行 interface。

测试替换下沉到已经真实变化的 adapter：`UvOperations`、`TyOperations` / `ProcessRunner`、
`ConfiguredVerifier` 与 runtime witness adapter。评价编排测试使用这些 adapter 组装真实
`EnvironmentFactory` / Evaluator，通过 `prepare()` 取得 `PreparedEnvironment`；测试不直接调用其构造器。

`HighestVersionVerifier`、`CompatibilityChecker`、`SearchCoordinator` 仍分别拥有 highest full verify、
two-phase declaration check 和单 Cell search。`CandidateOperations`、highest reuse、diagnostic/event consumer
等非同形 seam 只有在同一 Design 证明是假想 seam 时才能删除，不能顺手合并产品 outcome。

#### SearchCoordinator 测试整改

- 建立一个测试侧 assembly helper，把 lower-adapter fixtures 组装成真实评价 modules 与 coordinator；
- coordinator 测试只调用 `search(...)` 并断言 `CellResult`、probe evidence、static region、prepare reuse 与
  lifecycle；不直接测试 `_ProposalRunner` 私有状态或为每一步注入独立 fake；
- `search(...)` 与 D003 状态机保持不变；构造器是否还能缩小由删除测试决定，不为测试方便增加 factory、
  facade 或 DI framework。

#### 验收与停止条件

- 三套 env/static/full Protocol 全部删除，生产 composition 仍只有同一组评价 modules；
- `tests/test_check.py`、`test_baseline.py`、`test_search_coordinator.py`、`test_evaluation.py` 与
  `test_static_transition.py` 不再直接构造 `PreparedEnvironment`；
- Check capture-before-lowest、Highest close、Search prepare cache / coordinate state machine 的 public
  行为覆盖不减少；
- 若测试只能通过复制一套等宽 fake facade 才能表达，说明 seam 仍未找对，本轨停止并重新设计。

### 11.5 轨 D：terminal-private ResultCardEmitter

该轨只在一次需求同时改变至少两个命令的 card lifecycle，或出现可复现的 TTY/plain/path/final parity
缺陷时启动。目标是 terminal package 内部的一个私有 emitter，而不是 public renderer union：

```text
ResultCardEmitter.emit(console, ResultCardSpec, FinalSummary) -> None
```

`ResultCardSpec` 只含 terminal-private heading、section、fact 与 literal path value；emitter 独占 outcome
marker/gutter、TTY Panel 与 plain Group、path/OSC 8、折行和 card-before-final 顺序。命令 `render_*` 继续
把 domain result 投影为这些 presentation facts，并继续决定 stdout/stderr、exit code、命令措辞与信息层级。

实施时用一次迁移替换 `_result_card`、`_plain_result_card`、调用方 `_path_text` 和直接 card
`console.print`；不要保留一层新 emitter 再调用原四个 helper。验收必须从 public presenter/CLI seam
覆盖 explain、diagnose、apply/minimize、merge 与 typed errors 的 56/80/120 列、TTY/non-TTY、literal
path/ID、stdout/stderr 和 exactly-one-final。若 `ResultCardSpec` 的字段与现有 rows/Rich tree 等宽，或只把
command-specific row assembly 换文件，则不实施。

### 11.6 每轨 Plan 与证据要求

每个被接受的 Design 分别建立 Plan，并至少包含：基线 ownership 扫描、目标 interface 的 public
contract tests、生产迁移、旧路径删除、owner 文档归并、focused tests、全量质量门禁和验收审计。
Plan 中预留而不预填以下证据：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon <focused modules> -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ty check src
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --cov=pf --cov-report=term-missing -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.12 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv build
git diff --check
```

Python 3.10/3.11/3.12 全量套件必须在同一工作树顺序执行；collection、focused pass、coverage gate 与
build 分别记录，网络限制不归类为代码失败。每轨完成时以 Design 验收表逐项回填命令、范围、计数、
coverage 与结论，不能用“全量绿色”替代 interface 删除、ownership 和文档归并证据。

### 11.7 本方案核对范围

- §11 改进方案与 D020 草案的实现基线均为 `c1aff33`；
- 源码扫描确认 `VerificationTask` 仍暴露 Journal、runtime association 与 deadline 槽位，
  `completion_outcome(result: object)` 仍存在；
- 源码扫描确认 Check / Highest / Search 仍各有三套 env/static/full Protocol；五个相关测试文件仍有
  7 处直接 `PreparedEnvironment(...)` 构造；
- 源码扫描确认 `ProjectDiscovery.select` 与 `owned_pyproject_paths` 仍是两次独立 observation；
- 仓库内 57 份 Markdown（含 D020 草案）的相对链接存在性审计为 0 个缺失；
  `git diff --check` exit 0。

改进方案与当时的 [D020 草案](../archived/designs/D020-pf-workspace-inventory.md) 没有修改生产代码、现行 owner Design、
测试或生成物，也没有运行 pytest、ty、coverage、build 或真实 PF command；以上只证明方案与
`c1aff33` 的静态实现形状闭合，不是行为验证。

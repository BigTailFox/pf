# R007 — PF 当前改进优先级评审

- **状态：** 开放
- **日期：** 2026-09-04
- **性质：** 非规范性产品、架构与工程评审；不定义命令、退出码、Schema 或 module interface，不授权实施
- **对照：** 当前 `main`，HEAD `d231a0e`
- **输入：** 两轮当前仓库独立梳理、[E002](../experiments/E002-pf-search-performance.md)、
  [R006](R006-pf-cli-system-review.md)、[E001](../experiments/E001-pf-self-bootstrap-validation-contract.md)
- **现行契约所有者：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、
  [D003](../designs/D003-pf-search-algorithm.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、
  [D006](../designs/D006-pf-cli-enhancement.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)
- **与既有文档的关系：** 本文汇总当前优先级和新增发现；E002 保存搜索性能运行证据，R006
  继续保存 CLI 详细评审，后续 [R008](R008-pf-search-performance-review.md) 汇总当前搜索性能候选。
  本文不以重复摘要替代这些文档。

本文使用 `module`、`interface`、`seam`、`adapter`、`depth`、`leverage` 与 `locality` 判断架构候选，
并对每个候选应用删除测试。文件行数、helper 数量或把实现移动到新文件，不单独构成改进理由。

## 1. 最终结论

当前没有发现新的 P0 正确性、安全、证据授权或 fail-closed 缺口。[D019](../archived/designs/D019-pf-source-plan-depth.md)–
[D023](../archived/designs/D023-pf-configuration-model.md) 已依次收口 SourcePlan、WorkspaceInventory、
Verification Run request、评价 seam 与配置模型；[R005](../archived/reviews/R005-pf-module-depth-review.md)
已归档。现阶段最高价值不在继续拆分叶子实现，而在完成用户和 CI 可观察的协议、降低搜索成本，并让少数
仍由多个调用方重复学习的规则拥有单一 owner。

当前建议优先级如下：

| 优先级 | 事项 | 路径与治理 |
| --- | --- | --- |
| P1 | 多宿主 host-partial 的 search/minimize 协议 | R006 已跟踪；建立一份 D001/D006 临时 Design，必须共同覆盖 search 数值退出与 minimize 展示/授权流程 |
| P1 | CI coverage 门禁 | 新发现；现行阈值已明确，可直接修改 CI 与验证，不需要产品 Design |
| P1 | 昂贵 configured verifier 的有效裁剪率 | R008 已汇总；先在当前 HEAD 刷新性能基线，再决定是否建立 D003/D012 Design |
| P2 | 按命令装配 capability graph | R006 已跟踪；建立 D002 临时 Design，保持唯一 composition root |
| P2 | Ctrl+C 稳定终态 | R006 已跟踪；先由 D001/D006 接受退出码和终态语义，可与 composition 共用实现但保持独立验收 |
| P2 | `package-floor.json` 路径规则单一 owner | 新发现；目标是传递已解析路径值，不建立 path module、不持久化路径 |
| P2 | 发布支持与资格证据 | E001 已记录部分缺口；发布前明确 host 支持范围并补足对应 current-pin/平台证据 |

搜索前报告预检、非 TTY 搜索活动、typed result-card、targeted runtime floor、局部假想 Protocol、PEP 508
规范化和 ty qualification 都是有效候选，但不应挤占上述主线或被错误捆成一次大改。

## 2. P1：多宿主 host-partial search/minimize 协议

### 2.1 已确认问题

`TerminalPresenter` 的 `_search_exit_code()` 仍把任何非空 incomplete reasons 映射为退出 `2`。因此本宿主
全部已执行 Cell 都成功、唯一缺失是其他宿主的 `MISSING_CELL` 时，`pf search` 虽然写出了应交给
`pf merge` 的有效 host-partial artifact，仍会令常见的 fail-fast CI 在上传 artifact 前停止。R006 已完成
reason-aware 文案修复，当前缺口只在自动化协议，不应把文案修复重新打包。

现行 `minimize` 顺序调用 search 与默认 apply workflow，不依据 search 的退出映射提前返回；
`ApplyAuthorizer` 可以把只缺完整 MissingSelector、且至少有一个完整 EvidencePlatform 的 incomplete report
授权为 `PLATFORM_SCOPED`。随后 `render_minimize(report, result)` 丢弃 report，只渲染 apply 卡。因此
host-partial minimize 可以正确退出 `0` 并应用已授权平台，却不说明其 report 仍需和其他宿主 artifact merge。

### 2.2 进入 Design 时必须一起决定

优先评估把“本宿主有 Cell、全部成功、唯一 reason 为其他宿主 `MISSING_CELL`”定义为退出 `0` 的
host-partial success-with-warning。Design 必须同时区分：

1. 本宿主成功且只缺其他宿主；
2. 当前宿主没有任何匹配 Cell；
3. 本机存在 rejection、indeterminate、no-floor 或 search failure；
4. 本机失败与远端 missing 混合；
5. 完整单宿主成功。

同一 Design 必须把 `minimize` 纳入验收：

- 不得仅因 report 为 incomplete 就跳过默认 `ApplyAuthorizer`；
- 可授权的 host-partial report 继续按现行 `PLATFORM_SCOPED` 规则决定 apply，不由 Presenter 重算；
- minimize 仍只渲染一张 apply/minimize 结果卡和一个 final，不重新展开完整 search card；
- 结果卡或 final 必须明确剩余宿主证据及 `pf merge` 下一步，且不能把已保留原约束的平台称为 passed；
- 不增加 `minimize --force`，不改变 report wire、merge identity 或 apply authority。

这是一份 D001/D006 产品 Design 的两个相连验收面，不应为 minimize 另开平行 Design。

## 3. P1：让 coverage 阈值真正成为 CI 门禁

[`pyproject.toml`](../../pyproject.toml) 已启用 branch coverage 并定义 `fail_under = 90`，归档 Plan 也一直
把 full coverage 作为完成证据；但 [CI](../../.github/workflows/ci.yml) 当前只执行
`uv run pytest --no-testmon`。普通测试可以全部通过而 coverage 低于 90%，PR 不会因此失败。

这是现行工程规则没有接入自动化，不需要新的产品或架构 Design。直接整改应：

- 至少在一个 canonical Python job 执行完整 `pytest --no-testmon --cov`，让现有 branch threshold 阻断 PR；
- 其余受支持 Python minor 仍完整运行无 testmon 的套件；
- 不降低 90% 阈值，不用 testmon 或 focused selection 生成门禁 coverage；
- 把 network-dependent installed-CLI 资格失败与 coverage failure 分开报告，不能用 deselect 换取绿色覆盖率。

现有历史惯例是在 Python 3.10 记录 coverage、3.11/3.12 运行完整无 coverage 套件；最终 CI 形状可以沿用
这一惯例，也可以在全部 matrix job 计算 coverage，但必须只有一条清晰、可复现的门禁定义。

## 4. P2：按命令装配 capability graph

### 4.1 当前 interface 税

`main()` 在 Cyclopts 解析前进入 `build_context()`；`_assemble_context()` 因而为 `--help`、`--version`、
`explain`、`diagnose` 与 `merge` 也建立 RegistryAccess、SubprocessRunner、UvAdapter、EnvironmentFactory、
static/runtime evaluators、三个评价编排器、SearchCoordinator、VerificationRunner 和七个 workflow。
离线命令必须认识完整在线验证图，说明当前 `CliContext` interface 对所有调用路径征收了等宽装配税。

同时，`try/except PfError` 位于 `with build_context()` 内部。composition 期间若发生预期
`ConfigurationError`，它不会经过 `TerminalPresenter.render_error()`，资源关闭虽有防线，CLI 的
no-traceback/唯一结果表面却没有覆盖这个阶段。

### 4.2 目标和删除测试

临时 Design 应比较 minimal bootstrap 与同一 root 内 lazy provider，选择调用方知识更少的形状。必须保持：

- `cli.py` 是唯一生产 composition root，不增加第二个 root；
- help/version 只装配 parser/presenter 所需能力；
- explain、diagnose、merge 不构造 UvAdapter、host target、evaluation graph 或 SearchCoordinator；
- apply 不构造 static/runtime evaluator 与 SearchCoordinator；
- composition-time expected `PfError` 进入统一 typed error/final，所有已建立资源只关闭一次；
- 不使用 DI framework、service locator、七个 optional workflow 槽位或 module bundle。

删除测试是：若删除 command-scoped assembly，完整在线 graph 的知识会重新出现在每个命令路径；若新形状
仍要求七个 workflow 同时存在，则没有获得 depth，应停止实施。

## 5. P2：Ctrl+C 稳定终态

`main()` 当前只捕获 `PfError` 与 `CycloptsError`。`KeyboardInterrupt` 会经过 context manager 的资源关闭，
但没有 PF 定义的退出码、stderr final、运行中 child/process-group 中断或 report 落盘规则，也可能由 Python
展示 traceback。D001 现行退出码只有 `0..4`；用户中断不能被误映射成基础设施或兼容性未知的退出 `4`。

若选择常见的退出 `130`，必须先由 D001/D006 明确：

- 解析前、装配中、运行中和持久化阶段的中断终态；
- child/process-group 的停止与等待规则；
- 什么条件下 incomplete report 可以安全写入，什么条件下不得写入；
- TTY/non-TTY 的 stderr、exactly-one-final 与 traceback 禁止；
- 中断期间再次中断的资源关闭行为。

它可与 command-scoped composition 共用 bootstrap 实现和同一 Plan，但仍是独立产品验收，不能借重构暗中
加入退出 `130`。

## 6. P2：报告路径规则只由一处拥有

### 6.1 已确认的重复知识

`ProjectDiscovery` 已在 `PackageLocation.report_path` 中建立 `package_root / "package-floor.json"`。然而：

- Search workflow 从 `root + package.pyproject_path.parent` 重算写入路径；
- Apply workflow 用同一规则重算读取路径；
- `TerminalPresenter` 与 `_explain` 各自再从 report 的 `pyproject_path` 重算展示路径。

四处调用方都学习了文件名、package-relative location 和 root-relative display 之间的关系。若未来修改报告
文件名或定位策略，变更会跨 Discovery、Workflow 与 Terminal 扩散，删除 `PackageLocation.report_path` 也
不会让复杂度明显增加；当前 interface 尚未获得应有 leverage。

### 6.2 校准后的目标

“直接让所有调用方调用 `PackageLocation.report_path`”并不完整：在线 workflow 只拿 `ProjectPlan`，
Presenter 只拿 `ValidatedReport`，Explain 成功结果也没有携带读取路径。未来 Design 应先决定已解析路径值
如何沿现有 seam 传递，例如由 planning result 和 command result 明确携带，而不是让 Presenter 重建。

必须保持：

- 报告位置规则只有一个 owner，Search/Apply/Explain/Diagnose/Terminal 只消费路径值；
- 不为了路径建立新的 public path module、repository 或 locator registry；
- 绝对 filesystem path、当前 checkout root 和 display path 不进入 Schema 1、report identity、Journal 或 merge；
- Merge 的显式 input/output path 继续来自命令 request/result，不被 package 默认路径覆盖；
- 现有 workspace root/member 选择和离线 discovery seam 不改变。

如果完整修复需要改变 ProjectPlan、Search/Explain command result 或 Presenter interface，它属于 D002 临时
Design；不能用一个新的 helper 掩盖路径仍由多个调用方重建的事实。

## 7. 既有开放轨：继续跟踪，不重复新开

### 7.1 提高昂贵 verifier 的有效裁剪率

E002 的基线显示 coordinate descent 有效，但 static region 只让约 15% 的 search-only 唯一向量免于完整
verifier；106 次 configured verifier 累计约 3,470 秒。D022/P028 后续已消除同 Proposal
static-to-runtime promotion 的重复 environment prepare，因此任何新 Design 前必须在当前 HEAD 重新记录：

- 每个 Cell 的 candidate、sweep、唯一 vector、prepare、static-only、promotion 和 verifier 次数；
- configured verifier 累计时间与 wall-clock critical path；
- current D022 prepared lifecycle 的实际复用率；
- final vector、boundary、FailureRecord 和 disposition 与基线的可比性。

优化可以调整 probe 顺序或 static region reference 建立时机，但 static shortcut 只能提供 guidance；floor
与 predecessor 仍须由 D003/D005 要求的 runtime authority 认证。不得改写用户 `test-command`、自动启用
testmon、跳过完整 verifier 或把 static PASS 称为 floor。

### 7.2 在昂贵工作前预检已有报告

Search workflow 目前在完成 Verification Run 和新 report build 后，才由 `ReportStore.update_path()` 读取
已有报告。旧内联布局、损坏 JSON 或不受支持 Schema 因而可能在付出全部 verifier 成本后才失败。

候选 Design 应在昂贵工作前完成有界 read/schema 预检，同时在最终更新前继续复证当前文件，不能把预读
结果当成跨长运行的可信 cache。还应区分：不可读/不支持的旧报告应早失败；合法但 generation 不同的报告
按现行规则开始新 generation，不应被误判为 blocker。

### 7.3 非 TTY 搜索活动

SearchCoordinator 已发布 `CellSearchProgressEvent`，但 `LiveVerificationView` 在非 TTY 下丢弃 Cell stage 与
search progress；重定向输出在阶段开始后直到 Cell 完成没有语义进展。R006 §5.2 继续拥有该候选。

未来独立 presentation/activity Design 只能输出有界、低频、invocation-local 的真实进展，不得用 spinner
tick、日志行数或 wall-clock heartbeat 冒充搜索推进，也不得进入 report、policy、Candidate、Failure、
Journal 或 identity。

### 7.4 terminal-private result-card

Explain/Diagnose/Merge 的 typed errors 已有结果卡，ApplyAuthorizationError、NoApplicableFloorError 与普通
ConfigurationError 仍回退为 `category: message`。只有下一次真实需求同时改变至少两个命令的 card lifecycle，
或出现可复现的 TTY/plain/path/final parity 缺陷时，才启动 R006 §5.1 的 private ResultCardEmitter。

不得因 `TerminalPresenter` 行数较多就启动该轨；若 ResultCardSpec 与现有 Rich rows 等宽，或新 interface
反向拥有命令措辞、stdout/stderr 和退出码，则停止。

## 8. 新发现但杠杆较低

### 8.1 自举 report 的 validation contract 与历史 artifact 链接

E001 已证明 `packaging>=22` 是相对于当时完整 `pytest --no-testmon` 仓库 contract 的真实 floor，不是已经
证明的发布 runtime floor。`targeted-runtime-contract floor` 仍值得按 Python 3.10/3.11/3.12 单独执行；
它是实验标签，不是新的 PF 命令，也不能通过削弱 D005 configured-verifier authority 获得。

当前还有一个文档证据漂移：E001 把根目录 [`package-floor.json`](../../package-floor.json) 链接为 generation
`608d6226...`、snapshot `d40a8b36...` 的实验产物；该文件之后已更新，当前为 generation `4bc0ce85...`、
snapshot `855e6dc6...`。因此后续直接文档整改应：

- 把 E001 的 artifact 引用改成明确的历史 tag/commit 证据，而不是指向可变根文件；
- 在根 README 说明当前入库 report 使用的 validation contract 和用途，不把 full-repository floor 简称为
  发布 runtime floor；
- 后续每次更新入库 report 时记录 contract 标签、完整 verifier argv 与 report identity。

这属于文档/实验事实完整性修复，不需要产品 Design。

### 8.2 只删除已证明的假想 Protocol

`FailureLogAssociations` 只有 `RunLogStore` 一个生产 adapter，Search workflow tests 也直接使用真实
`RunLogStore`；删除该 Protocol 后不会把复杂度推回多个 adapter，删除测试成立。若 command-scoped
composition Design 进入实施，可以把该清理作为小切片并继续从 workflow public behavior 验证 association。

不能把这个结论推广为“workflow 内单生产 adapter Protocol 全删”：

- `DiagnosisLogLocator` 有 recording test adapter；
- `ApplyAuthorizationOperations` 与 `ProjectEditOperations` 有明确的 workflow test adapters；
- CLI 七个 workflow Protocol 有 production workflow 与 `NeverCalledWorkflow` 类测试 adapter。

这些 seam 当前都有两个真实用途。除非未来以更深 module 的 public tests 替换旧测试，否则保留。

### 8.3 PEP 508 机械规范化的局部重复

`PackageReportBuilder._effective_requirement()` 与 `ApplyAuthorizer._requirement_semantic()` 都解析
`packaging.Requirement` 并规范 extras、specifier 与 URL；后者另外拥有 marker activation、kind 与 managed
授权语义。这里存在小范围 locality 改进，但 projection 和 authorization 的独立重求值是安全要求，不能
让 Authorizer 只读取 report projection 或信任 builder 的 representable 结论。

若相邻改动触及这两处，可以抽取 terminal-free、wire-free 的 private mechanical normalization value/helper，
由 Builder 与 Authorizer 分别补充自己的领域语义，并从两个 public seam 保留 mutation witness。它不值得
单独建立 public requirement module 或临时 Design，也不能把 marker activation、group ownership 和 apply
authority 合并到 builder。

### 8.4 ty 与 host 平台资格

uv `0.12.5` 有明确 diagnostic qualification；ty `0.0.74` 当前只有 exact pin、current-lock CLI/E2E 与
synthetic JSON tests，没有 `ty pin × Python minor × diagnostic case` 的动态资格矩阵。至少在更新 ty pin
前，应覆盖 baseline stability、强诊断、general diagnostic、输出完整性、exit 与 runtime witness。

当前 CI host 只有 Ubuntu。发布前必须明确 PF 的 host 支持范围：若声明支持 macOS/Windows，应补真实 host、
process、runlog、path 与 installed-CLI 证据；若 v1 只支持 Linux，应在发布契约和 README 明示。这里的矩阵
PASS 只证明对应坐标，不能外推整个 PF runtime。

## 9. 已证明较深的 module 与明确非目标

以下叶子或领域 module 目前都以较小 interface 隐藏了大量实现，删除后复杂度会重新散到多个调用方；没有
新证据时不继续拆分：

- `EnvironmentFactory.prepare(...)`：两次 resolution、materialization、Attempt/Proposal 与 cleanup；
- `CoordinateSearch.minimize(...)`：有限搜索、cache、region、promotion 与终止；
- `ConfiguredVerifier.run(...)`：configured command、pytest observer、progress/detail 与 terminal projection；
- `SnapshotBuilder.build(...)`：Git/non-Git traversal、identity、immutable staging 与安全；
- `ProjectLoader.load(...)`：declaration、Cell、source route、config binding 与 planning；
- `ValidatedReport` / `ReportStore`：Schema 1 codec、join、验证、merge/update 与原子持久化。

R002 发现的互斥 resolution 参数已经收敛为
`HighestResolution | LowestDirectResolution | ExactSelection` 判别联合，非法组合不可表示，不再是开放项。

本文明确不建议：

- 合并 CompatibilityChecker、HighestVersionVerifier 与 SearchCoordinator；
- 按行数拆 `report.py`、`TerminalPresenter` 或 Schema 文件；
- 建立 public `TerminalPresenter.render(result_union)`；
- 引入 DI framework、service locator、repository、event bus、daemon 或第二个 composition root；
- 给 minimize 增加 `--force`，或无真实消费者时增加 `--json`、`--quiet`、`--root`；
- 隐式改写 `test-command`、自动启用 testmon、跳过完整 verifier 或把 static PASS 称为 floor；
- 为 pre-release 已删除的 contract 增加 alias、兼容 reader 或 dual path。

## 10. 建议实施顺序与治理

1. 以小型 CI 改动接入 coverage gate；运行 canonical coverage、其余 Python minor full suite、Ruff、ty 与
   build，单独记录网络资格限制。
2. 建立 host-partial 临时 Design，一次钉住 search exit 与 minimize 的 authorizer/merge 提示验收；接受后
   再建立 durable Plan。
3. 在当前 HEAD 记录新的性能基线；只有新数据仍证明昂贵 verifier 裁剪不足时，才建立性能 Design。
4. 建立 command-scoped composition 临时 Design；可把 `FailureLogAssociations` 清理作为非目标驱动的小切片，
   但不得借机删除其他真实 seam。
5. 单独接受 Ctrl+C 产品语义；若与 composition 同时接受，可共享 Plan 和 bootstrap implementation，验收仍
   分列。
6. 为报告路径 ownership 选择明确 value flow；若跨 ProjectPlan/command result/Presenter interface，先建立
   D002 Design，不以 helper-only patch 假装完成。
7. 报告预检、非 TTY activity、ResultCardEmitter、targeted runtime floor 与资格矩阵继续按各自触发条件推进，
   不合并成“CLI 重构”或“发布整改”大包。

任何实质性产品、module、Schema 或退出码变更仍须先接受 normative Design，再建立把每条验收标准映射到
有序切片、ownership 迁移、旧路径删除、测试和证据槽的 durable Plan。本文是 Review，不构成实施授权。

## 11. 本次核对范围

本次以 HEAD `d231a0e` 静态对照了：

- `src/pf/cli.py`、`workflow.py`、`project_discovery.py`、`authorization.py`、`report.py`；
- `src/pf/terminal/__init__.py`、`terminal/_live.py`、`terminal/_explain.py`；
- `.github/workflows/ci.yml`、`pyproject.toml`、根 README 与入库 `package-floor.json`；
- workflow/CLI/terminal/authorization tests 中的公开 seam 与 test adapters；
- D001、D002、D003、D005、D006、D008、D012、D014、E001、E002、R006 与归档 R002/R005。

入库 report 的历史核对确认：`pf-self-bootstrap` 中的 SHA-256 为 `8e6c7d98...`，当前根文件为
`4514a69d...`；E001 和当前文件的 generation/snapshot identity 也不同。本轮没有重新执行真实多宿主 CI、
`pf search` 性能基线或跨平台 qualification，不能把静态结论表述为这些行为已验证。文档落盘只需执行
diff、空白与 Markdown 相对链接检查。

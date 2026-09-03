# R006 — PF CLI 系统评审

- **状态：** 开放（help/README 与 reason-aware incomplete 文案已修复；其余候选待接受或 Design）
- **日期：** 2026-09-03
- **性质：** 非规范性产品与架构评审；不定义命令、退出码、展示或 module interface，不授权实施
- **对照：** 初评基于 `010e048`；源码位置已在 D022/P028 完成后重校，本轮直接修复以 `9e4d1bb` 为起点
- **已解决项：** 诊断 help 的 Failure ID 语义、apply 的唯一 `--force` 语法、README 命令/apply 摘要与 search incomplete reason-aware final
- **输入材料：** 两份独立 CLI 评审意见，再对照当前源码、现行契约、公共 help 与 focused tests 校准
- **契约所有者：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、[D006](../designs/D006-pf-cli-enhancement.md)、[D008](../designs/D008-pf-verification-run.md)
- **收归来源与边界：** [R004](R004-pf-search-performance-review.md) §5 的非 TTY 搜索遥测、[R005](../archived/reviews/R005-pf-module-depth-review.md) 轨 D 的 terminal-private result-card 已移交本文；[D022](../archived/designs/D022-pf-evaluation-seam.md) / [P028](../archived/plans/P028-pf-evaluation-seam.md) 的评价 seam 与 SearchCoordinator 测试不属于 CLI 问题

本文只回答当前 PF CLI 还有哪些值得优化、各事项由谁拥有，以及进入实现前需要什么治理步骤。
结论按当前时间点成立；现行行为和唯一规范仍由 owner Design 定义。

评审使用 `module`、`interface`、`seam`、`adapter`、`depth`、`leverage`、`locality` 与删除测试判断
架构候选。文件大小、helper 数量或把代码移到新文件都不单独构成深化理由。

## 1. 最终结论

当前没有发现新的 P0 安全、证据授权或 fail-closed 缺口；D001/D006 已建立命令、数值退出码、live
Cell、explain/diagnose/merge 结果卡的主体契约。但“没有新的正确性缺口”不能作为完整结论：
`[tool.pf].jobs` 当前会被解析并保存，却不会影响任何调度，暴露了一个 P1 失效配置表面以及 D001
对“省略 CLI”语义尚未写死的契约歧义。此外，help/README 有三项可直接对照现行契约修复的公开表面偏差。

| 优先级 | 分类 | 事项 | 结论 |
| --- | --- | --- | --- |
| P1 | 契约歧义与失效配置 | `[tool.pf].jobs` 没有调度消费者 | 先在 D001 写死“省略时读 effective config、显式 CLI 覆盖”，接受后再建立 focused Plan |
| P1 | 小型契约修复 | `diagnose` help 吞掉 `<id>`；`apply` 暴露 `--no-force`；README 过期 | 已按 D001/D006 修复；公共 CLI 测试覆盖 help 与 parser 拒绝 |
| P1 | D006 展示契约修复 | incomplete reasons 被统一说成“no applicable floor” | 已恢复 reason-aware 文案；现行退出码不变 |
| P1 | 需要 Design | 多宿主 `search` 的纯 host-partial artifact 仍退出 `2` | 先钉住自动化协议，再由 D001/D006 决定是否改为成功-with-warning |
| P2 | 需要 Design | 所有命令在解析前装配完整验证图，且 composition-time `PfError` 越过统一错误映射 | 在唯一 composition root 内按 capability 惰性装配；不得引入第二个 root 或 DI framework |
| P2 | R006；原 R005 轨 D | apply/no-floor/普通配置错误仍是 `category: message` | 下一次真实跨命令错误展示变更时启动 terminal-private result-card，不另建错误 module |
| P2 | 需要 Design | `KeyboardInterrupt` 没有 CLI 终态，可能显示 traceback | 若采用退出 `130`，先修改 D001/D006；不得错误映射成基础设施退出 `4` |
| P2 | R006；原 R004 §5(3) | 非 TTY 搜索在阶段开始后没有持续活动反馈 | 作为独立 presentation/activity Design 候选；activity 不进入 report identity |

上述 help/README 与 D006 incomplete 文案小修复已完成。后续再分别接受
D001 的 jobs 省略语义、设计 multi-host outcome 与 command-scoped composition。这些项不应被捆成
一个 CLI 大重构。

## 2. 已确认的公开表面与契约问题

### 2.1 `[tool.pf].jobs` 被解析但从未参与调度

`EffectiveConfig.jobs` 由 `ConfigLoader` 从三层 `[tool.pf]` 配置合并结果中建立
（`src/pf/config.py:140-172`、`src/pf/schemas/config.py:12-55`），且 `PackagePlan.config` 保留该值。
但 CLI handler 把省略的 `--jobs` 立即固定为字符串 `"auto"`，构造 `CheckRequest`、`SmokeRequest`、
`SearchRequest`；`CheckCommandWorkflow.run`、`SmokeCommandWorkflow.run` 与 `SearchCommandWorkflow.run`
随后只把 `request.jobs` 传给 `VerificationRunner`，scheduler 最终也只消费这个 request 字段
（`src/pf/cli.py:214-261,292-314`、`src/pf/workflow.py:91-115,175-199,263-287`、
`src/pf/verification.py:185-200`）。`src/pf` 中没有 `package.config.jobs` 或 `config.jobs` 的消费点。

D001 §5 把 `--jobs` 的默认值写成 `auto`，§7 又定义 `[tool.pf].jobs` 并规定“CLI 选项覆盖对应调度
输入”。一个配置项若在 CLI 省略时仍不生效，就没有可观察用途；但“省略 CLI”究竟等于显式 `auto`，
还是应继承 effective config，并未由这两句唯一决定。因此现状不是单纯的 interface polish，也不能只靠
Review 宣布 precedence。

推荐先把以下目标行为写入并接受 D001：

- CLI 必须保留“未提供 `--jobs`”与“显式提供 `--jobs auto`”的区别；
- project load 后只解析一次有效值：未提供时取所选 package 的 `EffectiveConfig.jobs`，显式值覆盖配置；
- `VerificationRun.jobs` 和 Scheduler 仍只接收一个已经解析的 `"auto" | positive int`，不读取配置；
- `minimize` 复用同一 search 调度规则；配置、CLI、workflow 不建立平行 identity。

这会迁移 CLI request 与三个 workflow 的 scheduling ownership。必须先修订并接受 D001 对“省略/显式”
的定义，再建立 focused durable Plan。若上述推荐目标未获接受，则保持现状，不以测试或实现替 Design
作出 precedence 决策。接受后的公共验收至少覆盖：配置 `jobs = 1` 且省略 CLI、显式 `--jobs auto` 覆盖
配置、显式正整数覆盖配置，以及四条命令的等价行为。测试应观察传入 VerificationRunner/Scheduler 的
稳定 public value，不断言 private helper。

### 2.2 `pf diagnose --help` 丢失字面量 `<id>`

`_FAILURE_ID` 的 help 是 `A failure-<id> value; the failure- prefix may be omitted.`，但当前
`pf diagnose --help` 实际显示：

```text
A failure- value; the failure- prefix may be omitted.
```

Rich 把 `<id>` 解释为 markup。`tests/test_cli.py` 只断言后半句和 `FAILURE_ID` usage，因此没有发现
中间字面量被删除。这里应转义 markup 或改写为不会进入 markup 语法的等价句，并从公共 help seam
断言完整可见语义；不使用整页 snapshot。

**处理状态：已修复。** Help 现以不含 markup 的人类语言说明 16 位小写十六进制字符与可选 `failure-` 前缀，公共 help 测试断言这两层语义。

### 2.3 `pf apply --help` 暴露并接受未定义的 `--no-force`

D001/D006 只定义 apply 的 `--force`。Cyclopts 目前从默认 `False` 的 bool 参数自动生成：

```text
--force --no-force
```

这不只是 help 噪声，而是新增了契约没有的可调用语法。应关闭 negative alias，并用公共 CLI 测试同时
证明 help 只出现 `--force`、`--no-force` 被 parser 拒绝。PF 处于 prerelease，不需要为该意外语法保留
兼容 alias。

**处理状态：已修复。** Apply 的 bool parameter 已关闭 negative alias，help 只展示 `--force`，`--no-force` 在 parser 层形成退出 `1` 的调用错误。

### 2.4 README 的命令与 apply 描述落后于 D001

根 README 仍写 `pf diagnose [--package PACKAGE] [--failure FAILURE_ID]`，apply 行遗漏 `--force`；同时
“apply 只消费完整报告”已经不符合 D001 的 `PLATFORM_SCOPED` 授权规则。README 应改成
`pf diagnose FAILURE_ID [--package PACKAGE]`、`pf apply [--package PACKAGE] [--force]`，并准确说明
某些仅缺完整 MissingSelector 的 incomplete report 可以产生 platform-scoped apply evidence。

README 只做入口导航与摘要，不复制一套 Cyclopts help；命令和授权细节仍链接到 D001。

**处理状态：已修复。** 根 README 已对齐 apply/diagnose 调用形状，并用“至少一个完整 EvidencePlatform，缺失项只来自完整 MissingSelector”摘要 platform-scoped 授权。

## 3. P1：incomplete 文案与 multi-host search outcome

### 3.1 当前问题

D008 明确定义每个进程只运行 `cell.target == host_target` 的 Cell；Search 的 host 集为空也合法，workflow
继续写出带 `MISSING_CELL` 的 incomplete report（`src/pf/verification.py:243-265`、D008 §1）。公共 workflow
测试也证明多平台项目只执行本机 target，并保留其他 target 为 missing。

修复前 Presenter 把除 baseline rejection/indeterminate 之外的所有 incomplete reasons 统一映射为退出 `2`，
并固定输出：

```text
Search incomplete · package-floor.json written · no applicable floor
```

因此，只要项目声明多个宿主，每个宿主即使本地 Cell 全部成功，也会因其他宿主 `MISSING_CELL` 得到
非零退出；自动化若按常规 fail-fast，可能在上传或 merge artifact 前停止。文案也不总成立：D006 已要求
`NO_PASS_IN_SEARCH_SPACE` 与 `NON_MONOTONIC` / `NONDETERMINISTIC` 使用不同结论语言，D001 又允许“已有
完整 EvidencePlatform、其余只缺完整 MissingSelector”的 incomplete report 产生 platform-scoped apply
evidence。当前实现却把这些原因与 `MISSING_CELL` 收成同一句 no-floor。

这里包含两个不同层次的问题：reason 文案塌缩是可直接对照 D006 修复的展示偏差；纯 host-partial 是否
应继续退出 `2` 则是 D001/D006 之间的产品语义张力。两者不应捆成一个退出码变更，也不能只在 CI 文档中
要求 `|| true`。

### 3.2 先恢复 reason-aware 文案

**处理状态：已修复。** Search final 现从 report reasons 与 target/observed Cell 分布投影准确结论；纯 host-partial 仍返回 `2`，不预判 §3.3 待 Design 的自动化协议。

在不改变现行退出码的前提下，search summary 应依据 `report.result.reasons` 与本机 `CellResult` 集合选择
准确结论；`MISSING_CELL`、`NON_MONOTONIC`、`NONDETERMINISTIC`、`UNREPRESENTABLE_PROJECTION`
不得继续统一叫作“no applicable floor”。这属于 D006 现有信息层级的修复，可与 §2 的小型契约修复
分开交付。

至少应区分：

1. 本宿主有 Cell 且全部成功，唯一不完整原因是其他宿主 `MISSING_CELL`；
2. 本宿主没有匹配 Cell；
3. 本宿主得到 `NO_PASS_IN_SEARCH_SPACE`；
4. 本宿主得到 `NON_MONOTONIC`、`NONDETERMINISTIC` 或 `UNREPRESENTABLE_PROJECTION`；
5. 本宿主有 incomplete/failure，同时还有其他宿主 `MISSING_CELL` 的混合结果；
6. baseline rejection 或 `INDETERMINATE`。

混合本机失败不得落入“本宿主成功、等待 merge”。scheduler deadline / `--max-duration` 形成的
`CellIndeterminate` 仍按 D008 聚合为 `INDETERMINATE` 并走现有退出 `4`；本 Review 不建议借此修改 deadline
退出语义。

### 3.3 host-partial 退出码需要 Design

Design 应以 `(report.result.reasons, local host CellResult set)` 为判定输入，并保留 D008 已定义的 aggregate
dominance。优先评估把第 1 类定义为“host-partial artifact 成功”：保留 report 的 `incomplete` 与
`MISSING_CELL`，以 warning 明确“本宿主 N cells 完成、M cells 待其他宿主、下一步 `pf merge`”，但让 CI
能继续收集 artifact。empty-host 与任何本机 incomplete/failure 不得冒充成功；真实 no-pass 仍是 `2`，
baseline rejection 与 indeterminate 保持 D001 的 `1` / `4`。备选方案是保留纯 host-partial 的退出 `2`，
但必须给出稳定的
自动化协议和不误称 no-floor 的结果卡。

Design 必须先钉住自动化协议；只有接受改变 host-partial 的数值退出语义时，才共同修改 D001/D006。
无论选择哪种退出码，都需证明：

- 单宿主 search、真实 no-floor、baseline rejection、indeterminate 的现行结果不变；
- 每个 host 仍只执行自己的 Cell，report wire、generation update 与 merge authority 不变；
- host-partial、empty-host、local partial failure 与“本机失败 + 远端 missing”的混合结果可由 final
  `CellResult` 与 target/observed Cell 唯一判定；
- `NON_MONOTONIC` / `NONDETERMINISTIC` 不再被称作 no-floor，deadline 的 `INDETERMINATE` 退出语义不变；
- stdout/stderr、exactly-one-final 与 artifact path 在 TTY/non-TTY 下稳定；
- 不把 orchestration activity 或 host 运行状态写进 report identity。

## 4. P2 Design 候选：command-scoped composition

### 4.1 当前 interface 税与错误边界

`main()` 在 Cyclopts 解析命令前进入 `build_context()`。`_assemble_context()` 因而为 `--help`、
`--version`、`explain`、`diagnose` 和 `merge` 也构造 RegistryAccess、SubprocessRunner、UvAdapter、
EnvironmentFactory、static/runtime evaluators、三个评价编排器、SearchCoordinator、VerificationRunner 与
七个 workflow（`src/pf/cli.py:369-489`）。这里的核心问题不是未经稳定基准证明的 wall time，而是离线
命令必须认识整条在线验证图才能启动。

此外，`try/except PfError` 位于 `with build_context()` 内部。composition 阶段若出现预期的配置错误，
异常不会进入 `TerminalPresenter.render_error()`，会越过 D006 的统一 no-traceback 错误表面。资源关闭已有
防线，但“关闭资源”与“呈现稳定 CLI 结果”目前不是同一个边界。

### 4.2 推荐方向与删除测试

D002 规定 `cli.py` 是唯一生产 composition root，也固定了当前 `CliContext`/`build_context()` 形状；因此
该改动必须先建立临时 Design，而不是直接把 `_assemble_context()` 切成命令 if/else。

Design 应比较以下形状，并优先选择 interface 更小、调用方知识更少的一种：

- 一个最小 bootstrap 只负责 parser、presenter 与最外层错误/资源生命周期，解析后向同一个 root 请求
  当前 command 的 capability graph；
- 同一个 root 内由惰性 provider 建立 command 需要的 workflow，但 provider 不能退化为 service locator、
  七个 optional workflow 槽位或第二套 root。

建议按真实 capability 而不是粗略“在线/离线”二分：

- help/version：parser 与 presenter；
- explain：discovery + report；diagnose：discovery + report + local Journal/log index；merge：report；
- apply：project planning、uv Python facts、snapshot、report、authorization 与 editor，但不装配评价器或
  SearchCoordinator；
- smoke/check/search/minimize：各自需要的验证图；minimize 仍顺序复用 search/apply ownership。

删除测试是本候选的关键验收：help/version 与 explain/diagnose/merge 不得构造 UvAdapter、探测 host target
或构造 evaluation/SearchCoordinator；apply 不得构造 static/runtime evaluation graph。另需证明
composition-time expected `PfError` 走一个稳定结果、所有已建立资源只关闭一次、`cli.py` 仍是唯一生产
root。若新 interface 仍要求七个 workflow 同时存在，或只把现有等宽 assembly 搬到多个文件，本候选停止。

## 5. 从 R005/R004 收归的 CLI 问题

本节完整接管原 R005 轨 D 与 R004 §5(3) 的跟踪职责。R004/R005 只保留来源链接；这两项的后续
Design、Plan、证据与完成状态只在 R006 更新。

### 5.1 原 R005 轨 D：terminal-private ResultCardEmitter

`TerminalPresenter.render_error()` 已对 explain report、diagnose not-found、merge input/compatibility/output
使用 typed result card；`ApplyAuthorizationError`、`NoApplicableFloorError` 与普通 `ConfigurationError`
仍落入通用 `category: message`。它们缺少稳定的 Outcome → reason → next action → 唯一 final 层级，且
某些深层 Schema 错误仍可能暴露不适合用户的内部细节。

现有 `_result_card`、`_plain_result_card`、`_fact_grid` 与 `_path_text` 统一了单个 Rich 构件，却没有隐藏
完整 card lifecycle。各 command renderer 仍分别知道 rows 形状、marker gutter、TTY/plain 选择、literal
path、console print 与 final 顺序；宽度或 parity 缺陷需要跨多个 renderer 修复。

这是 D006 typed command errors 与共享 terminal mechanics 的交叉，不建立新的 public error renderer。
该轨只在一次需求同时改变至少两个命令的 card lifecycle，或出现可复现的 TTY/plain/path/final parity
缺陷时启动。目标是 terminal package 内部的一个私有 interface：

```text
ResultCardEmitter.emit(console, ResultCardSpec, FinalSummary) -> None
```

`ResultCardSpec` 只含 terminal-private heading、section、fact 与 literal path value；emitter 独占 outcome
marker/gutter、TTY Panel 与 plain Group、path/OSC 8、折行和 card-before-final 顺序。command-specific
`render_*` 继续把 domain result 投影为 presentation facts，并继续决定 stdout/stderr、退出码、命令措辞
与信息层级。该 module 不成为 domain Schema，不进入 report/Journal/identity，也不向 workflow 暴露 Rich。

若进入实施，必须在一次迁移中替换 `_result_card`、`_plain_result_card`、调用方 `_path_text` 和直接 card
`console.print`；不能增加新 emitter 后继续保留原四条平行路径。验收从 public presenter/CLI seam 覆盖：

- explain、diagnose、apply/minimize、merge 与 typed command errors；
- 56/80/120 列、TTY/non-TTY、literal path/Failure ID；
- stdout/stderr、card-before-final 与 exactly-one-final；
- command-specific Outcome、reason、next action 和退出码不被 generic emitter 反向决定；
- 测试断言稳定语义与可观察顺序，不断言 private row、helper 名称、ANSI 边框或整页 snapshot。

停止条件：若 `ResultCardSpec` 与现有 rows/Rich tree 等宽，若只把 command-specific row assembly 换文件，
或若新 interface 迫使所有命令进入 `render(command_result_union)` public facade，则保留现状。文件行数不是
启动理由。

### 5.2 原 R004 §5(3)：非 TTY 搜索活动遥测

重定向输出的 search 在阶段开始后直到 Cell 终态没有持续反馈，用户只能观察 Process Log 增长。R004 从
一次约 37 分钟的 PF 自搜索中确认：搜索是有限但昂贵的，然而非交互用户无法从 CLI 区分“仍在有限推进”
与“没有进展”。

候选方向是在现有 activity/presenter seam 上提供有界、低频、invocation-local 的阶段事实，例如：

- 已观察唯一向量、prepare 与 runtime promotion 计数；
- 当前 active dependency 与候选窗口；
- 已完成/总 Cell，以及最近一次可观察推进。

这些事实只解释 activity，不建立 compatibility evidence，不进入 report、policy、SourcePlan、Candidate、
Failure、Journal identity 或 merge authority。它也不能用日志行数、spinner tick 或 wall-clock 心跳冒充算法
进展。

该项是 search-only presentation 变化，不自动触发 §5.1 的跨命令 result-card module。实施前应建立独立
Design，并至少证明：

- 非 TTY stderr 在长运行中能以稳定、受限频率观察到语义进展，不包含 ANSI/OSC；
- TTY live Cell、Process Log、Cell final 与 exactly-one-final 的现行顺序不回归；
- 重定向输出不会随每个 probe 无界增长，计数来自现有 owner 而非 Presenter 重算搜索状态；
- 相同输入的 Proposal、CellResult、FailureRecord、report bytes 与退出码不因遥测改变；
- 禁用或没有新 activity consumer 时不为搜索算法增加平行状态机。

### 5.3 不收归：R005 轨 C / D022/P028

评价层假想 Protocol 和 SearchCoordinator 测试表面已经由 D022/P028 完成并归档。CLI 中七个
workflow Protocol 则有生产 context 与 `NeverCalledWorkflow` 等测试 adapter 两类消费者，是实际 seam，
不因 D022 删除评价 Protocol 而删除。Check/Smoke/Search 的产品编排器也继续独立。

## 6. 中断语义需要单独接受

`main()` 只捕获 `PfError` 与 `CycloptsError`。Ctrl+C 会经过 context manager 关闭 presenter/logs，但
`KeyboardInterrupt` 仍可能由 Python 以 traceback 结束，没有 PF 的唯一 final。常见 shell 语义是退出
`130`，但 D001 当前只定义 `0..4`；把用户中断映射为 `4` 又会误称为基础设施/indeterminate。

若要修复，应先在 D001/D006 中明确：退出码、stderr 最终文案、运行中 child/process-group 中断、report
是否可安全落盘，以及 exactly-one-final。它可以和 composition Design 共用 bootstrap 实现，但不能借
架构重构暗中改变产品退出语义。

## 7. 不进入当前候选的方向

下列方向没有已证明的当前消费者，或会破坏已建立的 ownership；本 Review 不建议实施：

- 增加 `--root`、`--quiet`、`--json` 或 shell completion：分别改变 cwd root、D006 人类终端表面或发布
  产品面；有真实消费者后另立 Design；
- 给 `minimize` 增加 `--force`：D006 明确 `--force` 只属于 apply；
- 把 Check/Smoke/Search 合成一个 CLI 编排器：共享的是 environment/static/runtime seam，不是命令 outcome；
- 删除 CLI 的七个 workflow Protocol：它们有真实 production/test adapters，与 D022 已删除的评价层假想
  Protocol 不是同一问题；
- 建立 public terminal `render(union)`：会把 D006 的命令信息层级卷入同一 interface；
- 只因 `TerminalPresenter`、`_live.py` 行数较多而拆文件：没有删除 interface 知识就没有 module depth；
- 让 `render_minimize` 额外展开 search report：D006 已定义 minimize 复用 apply card；没有新的审计用例前
  不扩展信息层级；
- 把 root help epilogue 是否出现在每个 subcommand help 提升为架构工作：当前换行观感是低优先级
  presentation polish，不与上述契约缺口捆绑。

## 8. 建议实施顺序与治理

1. 已在一个小型契约修复中处理 diagnose Failure ID help、隐藏并拒绝 `--no-force`、对齐 README；只修改现行
   contract projection 与公共测试，未建立兼容层。
2. 先修订并接受 D001，明确省略 `--jobs` 是否继承 effective config、显式 `auto|N` 如何覆盖；只有目标
   行为成为规范后，才建立 focused Plan，映射 request ownership、三个 workflow、minimize、测试与文档证据。
3. 已按 D006 修正 incomplete 的 reason-aware 文案并保持现行自动化协议；后续再单独建立 multi-host search
   outcome 临时 Design，决定纯 host-partial 与 empty-host 的退出语义。不得把文案合规修复和退出码变更
   捆成一次实现。
4. 单独建立 command-scoped composition 临时 Design，比较 bootstrap 与 lazy provider，使用 UvAdapter/
   evaluation graph 删除测试选择方案，并把 composition-time expected failure 纳入同一验收。
5. 若接受 Ctrl+C 退出 `130`，先修改 D001/D006；可与 composition 共用实施 Plan 的 slice，但保持独立
   验收项。
6. 下一次真实跨命令错误展示需求按 R006 §5.1 触发 private result-card Design；非 TTY search progress
   按 R006 §5.2 建立独立 Design。两项不互相捆绑。
7. D022/P028 的评价 seam 与 SearchCoordinator 测试已由 R005 轨 C 完成并归档；其稳定规则由
   D002/D003/D004 接管，R006 不重复跟踪。

任何 substantial contract/module 变更都必须先接受 normative Design，再建立覆盖每个验收项的 durable
Plan。Plan 需记录 implementation slices、旧路径删除、owner 文档归并、public tests、完整门禁与精确证据；
Review 本身不代替 Design 或 Plan。

## 9. 本次核对范围

本次静态核对了：

- `src/pf/cli.py`、`config.py`、`schemas/config.py`、`workflow.py`、`verification.py`、`errors.py`；
- `src/pf/terminal/` 的 search summary、typed errors、apply/minimize/merge card；
- `tests/test_cli.py`、`tests/test_search_workflow.py` 的 public help/request 与 host selection 覆盖；
- D001、D002、D006、D008、R004、归档 R005、D022/P028 与根 README。

R006 初次评审时动态抽查了 `pf diagnose --help` 与 `pf apply --help`，确认 `<id>` 丢失和
`--no-force` 暴露。对照 `010e048` 的 focused 测试命令与结果：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon \
  tests/test_cli.py tests/test_explain_terminal.py -q
70 passed in 1.62s
```

初评未运行全量 pytest、coverage、真实 smoke/search、多宿主 CI 或非宿主平台验证；focused pass 只证明
被抽查的现行测试通过，不证明上述缺口已修复。

把 R004/R005 CLI 项收归本文时只运行了 `git diff --check` 与相对 Markdown 链接检查，结果均通过；当时
D022/P028 尚在实施，其后完成的测试不计入上述 R006 `010e048` 初始评审基线证据。

2026-09-03 的直接修复没有改动数值退出码、report/schema、workflow ownership 或生成物。
验证证据：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon \
  tests/test_cli.py tests/test_terminal.py -q
173 passed in 2.32s

UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon --cov -q
1466 passed in 29.01s
Total coverage: 90.60% (required: 90.0%; branch coverage enabled)

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/ty check src
All checks passed!

UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build
Successfully built dist/pf-0.1.0.tar.gz
Successfully built dist/pf-0.1.0-py3-none-any.whl

git diff --check
passed
```

全量套件在 sandbox 内初次运行时，真实安装用例因 PyPI 连接被禁止而得到 `1465 passed, 1 failed`；
Process Log 将失败定位到 `uv-build` 获取的 `Operation not permitted`。同一 focused e2e 在允许网络后
`1 passed in 1.88s`，随后允许网络的全量 coverage 命令按上述结果通过；该初次失败是环境证据，不是代码回归。

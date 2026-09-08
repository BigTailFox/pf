# PF CLI 交互与展示

- **状态：** 现行
- **最后核对：** 2026-09-08
- **命令与退出码：** [D001](D001-pf.md)
- **诊断事实：** [D004](D004-pf-ty-enhancement.md)、[D005](D005-pf-failure-and-diagnose.md)
- **Process Log：** [D007](D007-pf-process-output.md)
- **Role 与 Journal：** [D008](D008-pf-verification-run.md)

本文是 help、调用错误、输出通道、TTY/non-TTY 层级、命令摘要、Cell detail 和 `explain` 展示的唯一所有者。它只组织结构化事实，不定义命令语义、disposition、日志或报告 authority。

[视觉附录](appendices/D006-visual-specification.md) 是 D006 的规范性组成部分，集中定义固定 help
文案与通用卡片样式、折行和示例；主文保留通道、状态、命令信息层级和事实来源。两者共同构成一个 owner。

## 1. 展示原则

默认信息顺序是 Outcome → Scope → reason/impact → next action → technical details。只有 `diagnose` 展开 Enum、ID、process facts 和日志；普通命令不要求用户理解 Proposal ID、declaration digest、cause 或 Schema status。

- `✓` 只用于无 warning 的退出 `0`；`⚠` 表示 warning、no floor、host-partial remainder 或用户中断；`✗` 表示 Rejection/compatibility failure；`!` 表示 Indeterminate/infrastructure failure。host-partial search 与 source-drift apply/minimize 使用 `⚠` 且退出 `0`。用户中断使用 `⚠` 且退出 `130`。
- 非零结果不得输出无修饰的 `completed`。`complete` 只描述 D001 的完整可授权报告；host-partial artifact 仍说 incomplete。
- 颜色只作补充；去掉 ANSI/OSC 8 后文字仍完整。
- 用户 Cell 使用 `Python 3.11`、精确 target triple 和 `no-extra`；内部 Enum 不作为默认结论。
- 展示事实不进入 source、policy、Evaluation 或 report identity。

## 2. Help 与参数表面

命令说明来自 docstring，参数说明来自 `Parameter(help=...)`；固定分组、逐项文案与 Usage
见 [D006 附录 A.1](appendices/D006-visual-specification.md#a1-help-文案)。选项语义由 D001 拥有。

## 3. 调用错误

Search/minimize help 指向 `tool.pf.search-space` 与条件默认表，说明有下界使用
`majors[declaration-1:]`、无下界使用 `majors[baseline-2:]`，所有 space/resolution 组合合法；不新增 CLI space flag。
Declaration anchor 前提缺失是退出 3 的 configuration error。成功 registry 观测无法定位 anchor 或跨 scope
是退出 2 的 `search-space-resolution`，展示 dependency、Cell、canonical expression、失败原因、实际 anchors、
观察到的系列与 public source；它没有 Failure ID/diagnose 入口，也不声称写入了新报告。

未知 command/option、缺失或多余参数、非法 scheduling limit/duration、非distribution-name形状的`--package`值与request构造错误都形成D001的调用错误结果。结构错误尽早由Cyclopts拒绝；Request Schema只作defense-in-depth。合法形状但未知/重复package、non-package root省略selector、配置字段与project planning失败都是配置错误。不得宽泛捕获深模块ValidationError并伪装成调用错误。

配置、Schema 或 apply 授权错误不带 Usage 块；数值退出码只由 [D001 §8](D001-pf.md#8-退出码) 定义。

错误写 stderr，格式固定为：

```text
Error: <user-correctable message>
Usage: <exact command usage>
Try 'pf <command> --help' for more information.
```

不得输出 exception type、traceback、Pydantic URL 或内部 field path。Duration 错误必须给出 `30s, 10m, 2h, or none`。未知 package 的候选由 `ProjectDiscovery` 放入 `ConfigurationError.candidates`；Presenter 稳定排序并显示最多 10 个，剩余用 `... and N more`，不得自行扫描文件系统。

## 4. 输出通道与顺序

| 内容 | 通道 |
| --- | --- |
| 成功 final summary、成功 explain/diagnose、成功 artifact | stdout |
| warning、failure、incomplete/stopped summary、用户中断 | stderr |
| TTY live progress、scope facts、Cell completion | stderr |

`explain`成功读取后全文在stdout，即使报告incomplete；读取失败走stderr与D001的typed配置错误结果。无source override的apply成功card与final走stdout；实际使用source override时，全部facts与warning final走stderr且退出0。host-partial 的 search 与成功 minimize 同样走 stderr warning、退出 0。动态workspace member或静态member version不满足intended requirement是`3 + stderr + no Usage`，必须显示dependency/member、intended requirement、离线验证限制与恢复动作，不得建议`--force`。一个顶层命令只有一个final summary，且它是最后一条结果信息。`minimize`只调用`render_minimize(report, result)`，不能连续渲染search/apply两份summary，也不能仅因report顶层status incomplete就跳过默认authorizer。host-partial 成功 apply 后，minimize 仍只渲染一张 apply/minimize 卡和一个 final；final 必须包含剩余其他宿主 Cell 计数与 `pf merge` 下一步，Preserved 只表示 original constraints retained。

TTY 运行中顺序固定：

```text
scope facts 首部卡片
-> Cells 完成时立即冻结的结果块
-> 仍运行的 Cell 卡片
-> live footer
```

scope facts 与已完成 Cell 同属 pinned live 区域：Cell 一完成就从运行卡片移入首部
下方的冻结结果块并保持可见；Presenter 得到命令级最终 outcome 后，再将两者按
“scope facts 首部 -> 完成块”一次性固结。随后顺序为：

```text
scope facts 首部卡片 + 完成块
-> artifact paths
-> final summary
```

非 TTY 不显示 live 工作动词或控制序列，只输出 scope、完成块和 summary。

边框与 run ID 样式见 [附录 A.2](appendices/D006-visual-specification.md#a2-scope-样式)。

scope facts 首部卡片先显示 setup 完成状态，再显示 matrix heading
`selected N cells, P active packages (F pinned)`；其下一行是 `run-id: <id>`，随后显示
Python、platform 与 extra surface。`active packages` 是所选 Cell 中至少
生效一次的唯一 direct dependency name，`pinned` 是其中 fixed declaration 的唯一
package name。上述计数由 VerificationRunner 随 `CellMatrixEvent` 发布，Presenter 不读取项目或
declaration。

## 5. Live Cell

TTY 的每个运行中 Cell 使用独立卡片；示例见 [附录 A.3](appendices/D006-visual-specification.md#a3-live-样式与示例)。

`CellSearchProgressEvent` 提供当前 coordinate sweep 的完整有序 vector 与已完成前缀；
search Cell 把 vector 放在 probe identity 上方，并排除正在下降的 coordinate。coordinate 完成后以新 floor 回填原搜索顺序位置并切为已完成样式；每轮 sweep 开始把
全部 token 重置为未搜索样式。搜索进度只属于运行期 Activity，不进入 report、cache、
Journal、FailureRecord 或 identity。

`CellContextEvent` 提供当前 detail identity。Identity 切换清空旧 stage；同一 probe 的
evaluator lookup/cache hit 不制造新的 `CellContextEvent`、Attempt、验证进度或耗时；只有实际首次执行才切换
probe identity 并展示 activity。同一完整结果被另一 active dependency 使用时，算法仍登记该 Slice 的证据。
static-probe / oracle-probe / test 阶段保留 identity，并标明 static/oracle 窗口。Candidate discovery 清空 identity。Evaluator cache/baseline-seed
lookup 未执行真实 probe 时不制造 detail。cache hit 不生成新 ty stage 或复制耗时。

只有 direct serial pytest 在 collection 完成并取得唯一 nodeid 集时显示 determinate `completed/total` 与 ETA；ETA 以当前 dynamic stage elapsed 的平均吞吐估计，尚无完成测试时为 `ETA --:--:--`。generic、collect-only、xdist/unknown、bootstrap/collection 未完成或首个合法 snapshot 前 telemetry 失败都保持 spinner。同一 stage 已显示 determinate progress 后，协议失效只冻结最后合法进度输入，不能降回 spinner。Progress/ETA 是 UI-only，不改变 TestOutcome。

EnvironmentFactory 的 project-only 路径只发出 `resolving project` 与 `installing project plan`；不得发出未执行的 `resolving environment`。有 active external harness 时保留 `resolving environment` / `installing environment plan`。failure stage 原样展示 D005 的 `install-project` / `inspect-project-plan` 或相应 environment stage，不能按 group 名称推断活动。

Cell matrix 只登记总数；未启动 Cell 不建立 panel，因此可见 live Cell 数由 scheduler 实际并发自然约束为不超过 resolved `max-cells`。最后一行只显示 spinner、命令 phase、`N running`、`F finished`、`M left` 和右对齐总耗时，其中 `finished = completed`、`left = total - completed - running`；三个数字使用 dim bold 默认前景色，不显示方块矩阵或 completed/total。Cell title elapsed 与 footer 总 elapsed 都使用 dim cyan。Live view 以 20 Hz 刷新 spinner/elapsed；一次 ActivityEvent 的 task snapshot 必须原子可见，stage progress 不通过删除/重建 task 产生闪烁。

## 6. Cell completion 与 detail

成功、搜索完成和失败卡片示例见 [附录 A.4](appendices/D006-visual-specification.md#a4-completion-布局与示例)。

固定层级：

1. 图标、完整 Cell 与 elapsed；
2. search 可选绿色已完成包行；
3. 命令 completion action、可选 detail identity 与用户阶段；
4. 一个 D005 title 或 search completion Reason；普通 Cell 不展示 Role impact；
5. 可选 `CellResultDetail` 的第一条典型详情与 `... and N more`；
6. Journal/Index 可用时显示精确 diagnose command。

completion 的列对齐、action 文案与 stage token 见附录 A.4。

search 卡片的 primary failure 与结构化 detail 只取该 Cell 终止时收到的最新 `SearchFailureEvent`。末事件没有 detail 时不得回退到历史 probe；历史 failure 仍留在报告、Journal 与 `pf diagnose`。Reason 使用默认前景色，另行表达 Cell 为何结束：`INDETERMINATE` 明示搜索空间尚未评估完成并附终止 failure title；`NO_PASS_IN_SEARCH_SPACE` 只说明在配置空间与 D003 搜索规则下未得到可应用 floor，不声称穷举了全部候选或组合。当前代码文案的偏移见 [R010 §2.1](../reviews/R010-pf-engineering-document-audit.md#21-p2-no-pass-文案夸大已验证范围)。`NON_MONOTONIC` / `NONDETERMINISTIC` 显示各自的搜索结论，不借用某次历史候选拒绝作为 Cell 结论。

普通 Cell 不展示 baseline `ty` warning、stdout/stderr tail、Process Log link、cause/status Enum 或全部 Failures。若应有的 Journal/Index 写入失败使 diagnose 不可用，才回退到对应 Process Log link；没有日志则显示 `Detailed diagnosis unavailable.`。

`PytestFailureDetail` 只展示 `FAILED <nodeid>`、非 call phase 和数量，并以 dim 与 Reason 区分。最终 summary 只依据动态结果；ty diagnostic 或不可用不把动态 PASS 显示成失败。diagnose 若展示静态关联，须标明 GLOBAL vs S_hi 与 SLICE vs S_slice，并写明这些事实解释探测路径，不是兼容性结论。静态单行格式为：

```text
path[:line[:column]] [check_name] single-line message
```

每个 Cell 独立展示，不跨 Cell 聚合。TTY completion 立即从 active Cell 区移入 setup
首部下方的 pinned 完成区；命令 outcome 确定后与首部一起固结，不得改变两者顺序。
非 TTY 在 Cell completion 时立即输出等价稳定文本。

Live completion只消费Runner发布的`CellCompletedEvent`；Check/Smoke/Search final分别从typed command
outcome或report `CellResult`经terminal-private、按命令闭合的projector形成；Explain与剩余
`SearchFailureEvent`从Evaluation/Failure facts经另一terminal-private projector形成。两类private
projector与Run live对共同事实保持D008规定的语义相等，但Terminal不导入Runner private函数，也不建立
接受任意object的shared public projector。

## 7. Result card 与 final summary

结果命令与 typed command errors 复用结果卡 primitive；布局、literal path 与折行见
[附录 A.5](appendices/D006-visual-specification.md#a5-result-card-布局)。

`search` summary 与 `explain` overview 的报告路径等于对应 command result 的 `report_path`；
`diagnose` 的 report 来源等于 `FailureDiagnosis.source_path`。Presenter 不从
`ValidatedReport.package.pyproject_path` 拼接文件名。

结果卡先于唯一 final summary；格式、宽度验收与单复数规则见附录 A.5。

Check 聚合为 `COMPATIBILITY_FAILED` 且含失败的 declaration-capture outcome 时，摘要使用
`Check failed · baseline capture did not pass · N cells`，不能称 declared lower bounds
不兼容；只有实际 declaration rejection 才使用下界不兼容结论。数值退出码见 D001；聚合为 `INDETERMINATE` 时仍走 unknown summary，逐 Cell impact 见 D008。

典型 final summary 文案见 [附录 A.6](appendices/D006-visual-specification.md#a6-final-summary-示例)。

Apply卡片必须从`ApplyCommandResult`显示package、Evidence、Scope、可选Preserved、可选Override/Paths与Metadata。default/scoped/noop共享此结构；Preserved只表示保留original constraints，不得描述为passed/covered。实际source override最多展示8条规范相对路径，不显示内容、diff或digest；整张卡和warning final走stderr且退出0：

```text
⚠  demo · applied with source-drift override
   Evidence  6 observed cells passed
   Scope     linux/x86_64 verified
   Preserved windows/x86_64, macos/arm64 · original constraints retained
   Override  source drift accepted · 31 paths
   Paths     src/a.py, ... (+23 more)
   Metadata  pyproject.toml updated
⚠  Applied floors with source-drift override · project updated
```

selector标签把`win32/AMD64`显示为`windows/x86_64`、`darwin/arm64`显示为`macos/arm64`。`minimize`复用同一apply card，只把final outcome改为Minimized。host-partial 成功时 card 仍走 stderr warning；Preserved 不得写成 passed/covered；final 追加 remaining other-host 计数与 `next: collect reports and run pf merge`。source-drift 与 host-partial 同时成立时仍是一张卡、一个 warning final。

Merge成功卡必须显示全部有序input paths、合并后report的complete/incomplete状态、Cell分布和output path，随后只有一个final。input读取失败显示第一个失败路径；compatibility失败显示有序inputs与output；output失败显示目标路径。三类都是typed stderr error card且不带Usage，Presenter不得硬编码输入数量。

Search/incomplete reason 的主导映射：

| 主导 reason | 文案 | 图标 |
| --- | --- | --- |
| `BASELINE_REJECTION` | stopped | `✗` |
| `INDETERMINATE` | stopped | `!` |
| D001 定义的纯 host-partial `MISSING_CELL` | incomplete | `⚠` |
| 其余 no-floor / search-failed / missing / projection reason | incomplete | `⚠` |

多 reason 使用 D008 聚合结果；数值退出码只见 D001。host-partial 的判定输入是 `(reasons, cell_results, target_cells)`，Presenter 不重算 apply authority。Summary 使用人类语言，不回显 Enum，也不把一个 Proposal 的结果说成 dependency version 的全局结论。

## 8. Explain

`explain`回答：读取的 package/report、complete 状态、intrinsic apply eligibility/blocker、final success Cell
计数、declaration floor/projection、目标 Cell 终态、搜索策略/系列范围，以及可用的精确 diagnose 入口。
它不读取当前项目树，不能断言当前 apply 已授权、force 可用或当前 identity 匹配；不转储 Proposal/process output。

Search spaces 部分从 `ValidatedReport` 读取 report artifact、规范策略分组、requested space（含省略）、完整
默认表、resolution/prereleases，以及逐 dependency/Cell 派生的原因、effective expression、实际 anchor versions、
选中系列和精确代表。无 CandidateSnapshot 的 Cell 显示 selection evidence unavailable，不猜测系列；
即使有 baseline 也不能伪造未取得的 registry 事实。Explain 不解析 DSL、不 join refs、不读取 registry。
系列范围不被描述为完整兼容区间，all 仍受公共资格过滤。

默认结构是一张overview card、零到多个异常Cell card和一个final；成功Cell只在overview中紧凑出现，不再重复展开：

```text
<icon>  PACKAGE · package-floor.json
        complete | incomplete · <intrinsic report conclusion>
        current project was not inspected

        Cells
        passed N · rejected N · indeterminate N · no floor N · search failed N · missing N
        <compact rows for successful Cells>

        Requirements
        <raw declaration>  <projected floors | fixed, not managed | blocked/no floor>

        <at most one authoritative next command>

<one card for each anomalous target Cell, exactly once>
<bold final 0/1/N managed dependency summary or incomplete reason summary>
```

Presenter 用 declaration ID 关联 raw declaration 与 projection，不能显示 digest 代替名称。Requirements必须区分有floor的projection、`fixed, not managed`、blocked和no applicable floor；多marker requirements在声明下缩进。Cell分布依据实际`CellResult`判别类统计，`SEARCH_FAILED`不得折叠成no floor。每个target Cell恰好出现一次：success只进入overview，rejection、indeterminate、no-floor、search-failed和missing进入异常card。

完整report只说eligible并明确当前项目未检查、apply仍会复核。Incomplete report若已有至少一个完整EvidencePlatform、缺失项只来自完整MissingSelector及其full-matrix projection不可表示，可条件式说明platform-scoped apply evidence available；没有final success、selector内局部/非成功root、non-monotonic或其它reason仍说blocked。`UNREPRESENTABLE_PROJECTION`在上述MissingSelector情形只描述complete report projection，不冒充apply-time scoped blocker。

Requirements 的对齐、颜色与折行见 [附录 A.7](appendices/D006-visual-specification.md#a7-explain-requirements-样式)。

Cell 只投影报告中的最终状态。`CellIndeterminate` 选择其 `failure_id` 指向的终止 Failure；baseline rejection/indeterminate选择baseline Failure；未得到 floor、non-monotonic与nondeterministic显示命令级结论；没有CellResult的target Cell显示missing warning。仅一个权威终止Failure可以生成短hint `-> pf diagnose FAILURE_ID --package PACKAGE`；若不存在诊断目标，complete/scoped overview最多显示一个apply hint。next action不能在final之后重复。

默认explain不显示历史Failure轨迹、ty baseline、static increment、pytest detail、Proposal/source/policy IDs或process output。上述机械证据属于`pf diagnose`。成功读取complete或incomplete report都走stdout并退出0；报告missing/invalid/mismatched时使用typed stderr card、退出3且无Usage。missing report的安全recovery按selector显示`pf search`或`pf search --package PACKAGE`；invalid/mismatched report只显示稳定原因，不建议自动覆盖证据。

## 9. Diagnose

`diagnose`的数据语义与单记录lookup由D005/D008定义。成功时stdout只显示一张failure result card和一个success final：

```text
Failure / Outcome
What happened / Impact / Next step
Context
Technical details
optional last 3 non-empty output lines
optional Process Log link
```

header使用disposition对应的red/yellow事实色；What happened与Next step逐cause复用D005稳定文案，Impact逐Role逐字使用D008映射。Context至少显示package、完整Cell、stage、source `report | journal`及source path。Technical details必须显示disposition、cause，以及适用的attempt/resolution/vector/proposal/boundary/process/detail；不得用`None`占位。

execution authority 的细节从 terminal/attribution 派生：显示 qualified attribution 或 normal
nonzero fallback，异常终态单独说明；operation-structured 显示 fact.code。普通卡片仍简洁，
RESOLUTION_FAILED 不说已证明依赖冲突，INSTALLATION_FAILED 只说所选 plan 本次安装未通过。
后端 build 原因留在日志，不创建独立 build stage、归因某个版本或自动建议提高下界。
新 authority 没有嵌入 ProcessResult 时，本机日志 fallback 使用与 primary failure ID 匹配的
运行期 sidecar；不得取另一条 failure 的 process，live 与 final 保持同一关联语义。

输出tail来自D007安全Process Log，stderr非空时优先，否则stdout，只保留最后3条非空行；不能从tail重新分类。Process Log和report/source path在TTY中使用literal OSC 8 file link，non-TTY为无控制序列的路径文本。缺少本地locator时显示`Detailed local log is unavailable.`，不降低portable authority。用户数据使用literal Rich Text，不解释markup。

合法但未知ID使用typed stderr not-found card，显示Failure ID、package、已查source与恢复动作，退出3且无Usage。缺失、非法或多余位置ID属于调用错误，退出1，并使用完整`Usage: pf diagnose FAILURE_ID [OPTIONS]`和Try hint。

## 10. 所有权与不变量

| 规则 | Owner |
| --- | --- |
| 命令、参数语义、exit | D001 |
| Cyclopts registration/cardinality | `cli.py` |
| Failure title/next step/disposition meaning | D005 |
| Role→impact 与 diagnose sources | D008 |
| Process output/log tail | D007 |
| Help、channel、layout、summary、explain | D006 / `TerminalPresenter` |

Worker、adapter、Evaluator、workflow、report 与 editor 不导入 Rich 或拼用户文案。Presenter 不发现 package、不读 TOML、不扫描 artifact，也不改变领域结果。

必须保持：调用错误无 traceback；非零命令无成功措辞；每个顶层命令只有一个 final summary；用户中断在尚无命令 final 时于 stderr 发出唯一 `⚠ Interrupted`，TTY/non-TTY 同一句且无 traceback；Cell 始终含 Python/target/extra；diagnostic folding 不丢重数；非 TTY 无控制序列；display-only facts 不持久化。

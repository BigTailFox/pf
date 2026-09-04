# PF CLI 交互与展示

- **状态：** 现行
- **最后核对：** 2026-09-04
- **命令与退出码：** [D001](D001-pf.md)
- **诊断事实：** [D004](D004-pf-ty-enhancement.md)、[D005](D005-pf-failure-and-diagnose.md)
- **Process Log：** [D007](D007-pf-process-output.md)
- **Role 与 Journal：** [D008](D008-pf-verification-run.md)

本文是 help、调用错误、输出通道、TTY/non-TTY 层级、命令摘要、Cell detail 和 `explain` 展示的唯一所有者。它只组织结构化事实，不定义命令语义、disposition、日志或报告 authority。

## 1. 展示原则

默认信息顺序是 Outcome → Scope → reason/impact → next action → technical details。只有 `diagnose` 展开 Enum、ID、process facts 和日志；普通命令不要求用户理解 Proposal ID、declaration digest、cause 或 Schema status。

- `✓` 只用于无 warning 的退出 `0`；`⚠` 表示 warning、no floor、host-partial remainder 或用户中断；`✗` 表示 Rejection/compatibility failure；`!` 表示 Indeterminate/infrastructure failure。host-partial search 与 source-drift apply/minimize 使用 `⚠` 且退出 `0`。用户中断使用 `⚠` 且退出 `130`。
- 非零结果不得输出无修饰的 `completed`。`complete` 只描述 D001 的完整可授权报告；host-partial artifact 仍说 incomplete。
- 颜色只作补充；去掉 ANSI/OSC 8 后文字仍完整。
- 用户 Cell 使用 `Python 3.11`、精确 target triple 和 `no-extra`；内部 Enum 不作为默认结论。
- 展示事实不进入 source、policy、Evaluation 或 report identity。

## 2. Help 与参数表面

`pf --help` 按固定工作流顺序分组：

```text
Verify
  smoke
  check

Find and apply floors
  search
  explain
  apply
  minimize

Inspect and combine reports
  diagnose
  merge
```

Epilogue 固定为 `Typical workflow: pf smoke -> pf search -> pf explain -> pf apply Use pf minimize to search and apply in one command.`；实际换行由终端宽度决定。

命令说明来自可解析 docstring；参数说明来自唯一 `Parameter(help=...)`，不维护第二套手写 help 页面。

所有package-scoped命令只提供同一个optional长选项，不提供短别名或package positional：

```text
--package PACKAGE
    Select one installable package by distribution name. Omit to select the
    installable workspace root.
```

公共选项：

```text
--max-cells auto|N
    Maximum concurrent cells. Omit to use project configuration.

--ty-jobs auto|N
    Maximum concurrent ty checks. Omit to use project configuration.

--test-jobs auto|N
    Maximum concurrent configured test commands. Omit to use project configuration.

--max-duration DURATION
    Stop scheduling after DURATION and save an incomplete report.
    Accepts a positive integer followed by s, m, or h; use none for no limit.

--force
    Accept source-layer drift after structural authorization.
```

三个 scheduling flags 同时属于 smoke/check/search/minimize；parser/request 必须保留省略为 `None` 与显式 `auto` 的区别。`--force`只属于`apply`，不出现在`minimize`；它不表示partial/platform选择。Duration只停止新增调度，不承诺杀死运行中process。`merge`必须显示`REPORT [REPORT ...] --output PATH`并在parser层要求至少一个REPORT。`diagnose`的Usage固定为`pf diagnose FAILURE_ID [OPTIONS]`；Failure ID是必填位置参数，可传`failure-<16 hex>`或省略前缀的`<16 hex>`。

## 3. 调用错误

未知 command/option、缺失或多余参数、非法 scheduling limit/duration、非distribution-name形状的`--package`值与request构造错误都形成D001的调用错误结果。结构错误尽早由Cyclopts拒绝；Request Schema只作defense-in-depth。合法形状但未知/重复package、non-package root省略selector、配置字段与project planning失败都是配置错误。不得宽泛捕获深模块ValidationError并伪装成调用错误。

所有按本节格式渲染的调用错误退出`1`；配置、Schema或apply授权错误不带Usage块并按D001退出`3`。

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

scope facts 首部卡片的运行中边框使用默认前景色 dim；完成固结时保持 dim，并切换为
命令最终 outcome 的 green/red/yellow。边框样式不得传给 `loaded project`、
`built snapshot`、matrix facts 等正文。

scope facts 首部卡片先显示 setup 完成状态，再显示 matrix heading
`selected N cells, P active packages (F pinned)`；其下一行是 `run-id: <id>`，随后显示
Python、platform 与 extra surface。`active packages` 是所选 Cell 中至少
生效一次的唯一 direct dependency name，`pinned` 是其中 fixed declaration 的唯一
package name。上述计数由 VerificationRunner 随 `CellMatrixEvent` 发布，Presenter 不读取项目或
declaration。run ID 整行 dim；`YYYYMMDD` 与 `HHMMSS` 分别使用 dim bold green，点号后
三个连字符分段分别使用 dim bold magenta。Python minor 版本使用 dim bold 默认前景色。
setup、run ID、live/footer、完成 Cell、错误与 final summary 都使用 native Rich marker/content
表格；marker 固定一列，marker 与正文之间统一为 2 个空格宽的 gutter，不以字符串空格实现对齐。

## 5. Live Cell

TTY 的每个运行中 Cell 使用独立卡片：

```text
⠋  [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:12
   [baseline][highest][testing]  ━━━━━━╺━━━━━━━━━━━━━ 37/120 ETA 00:00:41

⠋  [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:12
   [cyclopts=2.4.0][packaging=24.0][rich=13.0]
   [pydantic=1.7.4][1.7.4~2.13.4#18][testing]  ━━━━━━╺━━━━ 37/120 ETA 00:00:41

⠋  searching cells · 3 running · 4 finished · 0 left                 0:00:12
```

`CellSearchProgressEvent` 提供当前 coordinate sweep 的完整有序 vector 与已完成前缀；
search Cell 把 vector 放在 probe identity 上方，并排除正在下降的 coordinate。未搜索的
token 内容使用 dim 默认前景色且 package name 不加粗；已完成 token 内容使用 green 且
去掉 dim，其 package name 为 bold，`=version` 保持普通字重；bracket 始终为 dim 默认前景色。
coordinate 完成后以新 floor 回填原搜索顺序位置并切为已完成样式；每轮 sweep 开始把
全部 token 重置为未搜索样式。搜索进度只属于运行期 Activity，不进入 report、cache、
Journal、FailureRecord 或 identity。

Cell title `[py...][target][extra]` 的 token 内容使用 bold 默认前景色，bracket 使用 dim
默认前景色。`CellContextEvent` 提供当前 detail identity；baseline、declaration 与
search probe 都放在 title 后的 identity detail。Live identity 的 bracket 使用 dim 默认
前景色；第一、第二 token 内容分别为 bold cyan 与 cyan。任意当前 stage 都作为第三个
token 与 identity 保持在同一逻辑行，其内容使用默认前景色且不 dim；dynamic tests
精简为 `[testing]`，其中 `testing` 使用 cyan，并与 progress bar/count/ETA 保持同行。
count 与 ETA 使用 dim 默认前景色，count 只显示 `completed/total`，不追加
`tests`。没有 identity 时只显示第三个 stage token。候选窗口使用 `~`。Identity 切换清空旧 stage；同一 probe 的
static/witness/test 阶段保留 identity。Candidate discovery 清空 identity。Cache/known-PASS
未执行真实 probe 时不制造 detail。

只有 direct serial pytest 在 collection 完成并取得唯一 nodeid 集时显示 determinate `completed/total` 与 ETA；ETA 以当前 dynamic stage elapsed 的平均吞吐估计，尚无完成测试时为 `ETA --:--:--`。generic、collect-only、xdist/unknown、bootstrap/collection 未完成或首个合法 snapshot 前 telemetry 失败都保持 spinner。同一 stage 已显示 determinate progress 后，协议失效只冻结最后合法进度输入，不能降回 spinner。Progress/ETA 是 UI-only，不改变 TestOutcome。

Cell matrix 只登记总数；未启动 Cell 不建立 panel，因此可见 live Cell 数由 scheduler 实际并发自然约束为不超过 resolved `max-cells`。最后一行只显示 spinner、命令 phase、`N running`、`F finished`、`M left` 和右对齐总耗时，其中 `finished = completed`、`left = total - completed - running`；三个数字使用 dim bold 默认前景色，不显示方块矩阵或 completed/total。Cell title elapsed 与 footer 总 elapsed 都使用 dim cyan。Live view 以 20 Hz 刷新 spinner/elapsed；一次 ActivityEvent 的 task snapshot 必须原子可见，stage progress 不通过删除/重建 task 产生闪烁。

外层 Console 最宽 120 列；内部 renderable 不设置固定 width/height。窄终端优先隐藏 bar、换行或改为 label block，不能丢失 package、Cell、artifact 或 next action。非 TTY 无 box drawing。

TTY Cell 卡片边框统一使用 dim，并保留原有颜色：live 卡片仍为默认前景色；完成卡片 success 为 green，failure 为 red，warning/indeterminate 为 yellow。dim 只作用于边框，不传递到卡片正文。

## 6. Cell completion 与 detail

成功 Cell 的 header 只有结果图标、Cell 与 elapsed；普通 identity 紧随其后：

```text
✓  [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:12
   check passed at [declaration][lowest-direct]
```

search 若已有 coordinate progress，则先显示绿色已完成包行，再显示当前 probe：

```text
✓  [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:12
   [baseline][packaging=24.0][cyclopts=2.4.0]
   search completed at [pydantic=1.7.4][1.7.4~2.13.4#18]
```

有 FailureRecord 的 Cell 块为：

```text
✗  [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:19
   smoke failed at [baseline][highest][testing]
   The full test command failed for this version combination.
   FAILED tests/test_cli.py::test_example
   ... and 2 more
   -> run `pf diagnose failure-38ac8f69eb9a182a --package demo` for more information.
```

固定层级：

1. 图标、完整 Cell 与 elapsed；
2. search 可选绿色已完成包行；
3. 命令 completion action、可选 detail identity 与用户阶段；
4. 一个 D005 title 或 search completion Reason；普通 Cell 不展示 Role impact；
5. 可选 `CellResultDetail` 的第一条典型详情与 `... and N more`；
6. Journal/Index 可用时显示精确 diagnose command。

TTY 与非 TTY completion 都使用固定 icon/content 两列；title、每条 detail 及其所有物理换行都从同一 content 列开始，不以字符串空格猜测 Rich 的换行位置。icon 与 content 间固定为 2 个空格宽的 gutter。completion action 统一为 smoke `passed/failed at`、check `passed/failed at`、search `completed/stopped at`。失败阶段作为 identity 后的第三个 bracket token；没有 identity 时仍使用单独的 bracket token。result detail 整行使用对应结果色并 bold；identity/stage 的 bracket 例外，始终使用 dim 默认前景色且不 bold。

search 卡片的 primary failure 与结构化 detail 只取该 Cell 终止时收到的最新 `SearchFailureEvent`。末事件没有 detail 时不得回退到历史 probe；历史 failure 仍留在报告、Journal 与 `pf diagnose`。Reason 使用默认前景色，另行表达 Cell 为何结束：`INDETERMINATE` 明示搜索空间尚未评估完成并附终止 failure title；`NO_PASS_IN_SEARCH_SPACE` 明示搜索空间已完整评估但没有兼容组合。`NON_MONOTONIC` / `NONDETERMINISTIC` 显示各自的搜索结论，不借用某次历史候选拒绝作为 Cell 结论。

普通 Cell 不展示 baseline `ty` warning、stdout/stderr tail、Process Log link、cause/status Enum 或全部 Failures。若应有的 Journal/Index 写入失败使 diagnose 不可用，才回退到对应 Process Log link；没有日志则显示 `Detailed diagnosis unavailable.`。

`PytestFailureDetail` 只展示 `FAILED <nodeid>`、非 call phase 和数量，并以 dim 与 Reason 区分。`StaticIssueDetail` 同样使用 dim，且仅用于最终 `CONFIRMED_MISSING` witness 覆盖的 incremental issues；普通 static regression 与无关 `ty` facts 不显示。静态单行格式为：

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

`explain`、`apply`、`minimize`、`diagnose`、`merge`及其typed command errors复用同一结果卡primitive。TTY卡片使用rounded border、固定marker/content两列和2空格gutter；标题marker、border与结果色一致。Field label固定宽度，长value从value列继续换行，不能回到终端左边缘。路径按literal user data渲染；TTY可使用underline cyan与OSC 8 file link，non-TTY不得包含ANSI/OSC。不得为获得路径链接调用`resolve()`而改变用户给出的相对路径。

56、80、120列是必测宽度。窄宽度可以增加物理换行，但不能丢失package、Cell、artifact、ordered inputs、reason、next action或technical facts；TTY/non-TTY拥有相同信息层级和语义。final summary位于卡片之后，是最后一条结果信息，icon与整句同色且bold。成功结果中的red/yellow事实仍随成功card走stdout，不拆到stderr。

格式按需组合：

```text
<icon>  <command outcome> · <count/scope> · <artifact or next state>
```

所有 renderer 复用统一 `0/1/N` 单复数 formatter。只有实际写入/修改的 artifact 能使用 `written | updated | merged`。Package-scoped命令只有一个target artifact与一条命令级summary；`merge`仍可消费多份report。
Final summary 的 icon 与整句文字使用同一个结果色且 bold。

典型结果：

```text
✓ Smoke passed · 1 cell
✓ Check passed · 3 cells
✗ Check failed · declared lower bounds are incompatible · 1 cell
✓ Search complete · package-floor.json
⚠ Search incomplete · package-floor.json written · 3 cells have no applicable floor
⚠ Search incomplete · package-floor.json written · 2 cells passed · 1 cell awaits another host · next: collect reports and run pf merge
! Search stopped · compatibility is unknown · package-floor.json written
✗ Search stopped · highest-version baseline did not pass · package-floor.json written
✓ Applied floors · project updated
✓ Applied floors · no metadata changes
✓ Merge complete · merged.json
✓ Minimized floors · project updated
⚠ Minimized floors · project updated · 1 cell awaits another host · next: collect reports and run pf merge
```

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

| 主导 reason | 文案 | 图标 | 退出 |
| --- | --- | --- | --- |
| `BASELINE_REJECTION` | stopped | `✗` | `1` |
| `INDETERMINATE` | stopped | `!` | `4` |
| 纯 host-partial `MISSING_CELL`（本宿主全部成功，缺失只来自其他宿主） | incomplete | `⚠` | `0` |
| `NO_PASS_IN_SEARCH_SPACE`、`NON_MONOTONIC`、`NONDETERMINISTIC`、empty-host/`同宿主 MISSING_CELL`、`UNREPRESENTABLE_PROJECTION` | incomplete | `⚠` | `2` |

多 reason 使用 D008 聚合结果；数值退出码只见 D001。host-partial 的判定输入是 `(reasons, cell_results, target_cells)`，Presenter 不重算 apply authority。Summary 使用人类语言，不回显 Enum，也不把一个 Proposal 的结果说成 dependency version 的全局结论。

## 8. Explain

`explain`只回答：读取的package/report、report complete状态、report intrinsic apply eligibility/blocker、final success Cell计数、每条declaration的floor/projection、每个目标Cell的最终状态与终止原因，以及可用的精确diagnose入口。它不读取当前项目树，不能断言当前apply已授权、force可用或apply-time dependency/source identity匹配；不转储observation、Proposal、process output或技术Enum。

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

Requirements 的 declaration 与单条 projection/detail 使用按当前内容计算的对齐列，
不得固定终端宽度。原始 declaration 的 dependency specifier 使用 cyan，其中 version
operand 使用 bold cyan；搜索得到的 projected requirement 使用 green，其中 version
operand 使用 bold green。package name、extras 与 marker 保持默认前景色；blocked / no
applicable floor 保持 warning 色。多 marker projection 继续在 declaration 下逐行缩进。

Cell 只投影报告中的最终状态。`CellIndeterminate` 选择其 `failure_id` 指向的终止 Failure；baseline rejection/indeterminate选择baseline Failure；完整评估无解、non-monotonic与nondeterministic显示命令级结论；没有CellResult的target Cell显示missing warning。仅一个权威终止Failure可以生成短hint `-> pf diagnose FAILURE_ID --package PACKAGE`；若不存在诊断目标，complete/scoped overview最多显示一个apply hint。next action不能在final之后重复。

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

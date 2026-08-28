# PF CLI 交互与展示

- **状态：** 现行
- **最后核对：** 2026-08-28
- **命令与退出码：** [D001](D001-pf.md)
- **诊断事实：** [D004](D004-pf-ty-enhancement.md)、[D005](D005-pf-failure-and-diagnose.md)
- **Process Log：** [D007](D007-pf-process-output.md)
- **Role 与 Journal：** [D008](D008-pf-verification-run.md)

本文是 help、调用错误、输出通道、TTY/non-TTY 层级、命令摘要、Cell detail 和 `explain` 展示的唯一所有者。它只组织结构化事实，不定义命令语义、disposition、日志或报告 authority。

## 1. 展示原则

默认信息顺序是 Outcome → Scope → reason/impact → next action → technical details。只有 `diagnose` 展开 Enum、ID、process facts 和日志；普通命令不要求用户理解 Proposal ID、declaration digest、cause 或 Schema status。

- `✓` 只用于退出 `0`；`⚠` 表示 warning/no floor；`✗` 表示 Rejection/compatibility failure；`!` 表示 Indeterminate/infrastructure failure。
- 非零结果不得输出无修饰的 `completed`。`complete` 只描述 D001 的可授权报告。
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

`package` 对所有相关命令都是 optional positional-only 参数：

```text
PACKAGE  Package name, directory, or pyproject.toml path. Omit to select all
         installable packages allowed by the root configuration.
```

公共选项：

```text
--jobs auto|N
    Maximum concurrent cells. Use auto or a positive integer. [default: auto]

--max-duration DURATION
    Stop scheduling after DURATION and save an incomplete report.
    Accepts a positive integer followed by s, m, or h; use none for no limit.
```

Duration 只停止新增调度，不承诺杀死运行中 process。`merge` 必须显示 `REPORT [REPORT ...] --output PATH` 并在 parser 层要求至少一个 REPORT。`diagnose --failure` 说明省略时列出全部记录。

## 3. 调用错误

未知 command/option、缺失或多余参数、非法 jobs/duration、未知 package 与 request 构造错误都形成 D001 的调用错误结果。结构错误尽早由 Cyclopts 拒绝；Request Schema 只作 defense-in-depth。不得宽泛捕获深模块 ValidationError 并伪装成调用错误。

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
| warning、failure、incomplete/stopped summary | stderr |
| TTY live progress、scope facts、Cell completion | stderr |

`explain` 成功读取后全文在 stdout，即使报告 incomplete；读取失败走 stderr 与 D001 的配置错误结果。一个顶层命令只有一个 final summary，且它是最后一条结果信息。`minimize` 只调用 `render_minimize(reports, edits)`，不能连续渲染 search/apply 两份 summary。

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
package name。上述计数由 workflow 随 `CellMatrixEvent` 发布，Presenter 不读取项目或
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

Cell matrix 只登记总数；未启动 Cell 不建立 panel，因此可见 live Cell 数由 scheduler 实际并发自然约束为不超过 `jobs`。最后一行只显示 spinner、命令 phase、`N running`、`F finished`、`M left` 和右对齐总耗时，其中 `finished = completed`、`left = total - completed - running`；三个数字使用 dim bold 默认前景色，不显示方块矩阵或 completed/total。Cell title elapsed 与 footer 总 elapsed 都使用 dim cyan。Live view 以 20 Hz 刷新 spinner/elapsed；一次 ActivityEvent 的 task snapshot 必须原子可见，stage progress 不通过删除/重建 task 产生闪烁。

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
   -> run `pf diagnose demo --failure failure-38ac8f69eb9a182a` for more information.
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

## 7. Final summary

格式按需组合：

```text
<icon>  <command outcome> · <count/scope> · <artifact or next state>
```

所有 renderer 复用统一 `0/1/N` 单复数 formatter。只有实际写入/修改的 artifact 能使用 `written | updated | merged`。多 package 先逐行列 artifact，再输出一条命令级 summary。
Final summary 的 icon 与整句文字使用同一个结果色且 bold。

典型结果：

```text
✓ Smoke passed · 1 cell
✓ Check passed · 3 cells
✗ Check failed · declared lower bounds are incompatible · 1 cell
✓ Search complete · 1 report · package-floor.json
⚠ Search incomplete · 1 report written · 3 cells have no applicable floor
! Search stopped · compatibility is unknown for 1 cell · report written: package-floor.json
✗ Search stopped · highest-version baseline did not pass · 1 report written
✓ Applied floors · 1 project updated · pyproject.toml
✓ Applied floors · no metadata changes
✓ Merged 3 reports · merged.json
✓ Minimized floors · 1 project updated
⚠ Minimize stopped before apply · search report is incomplete
```

Search/incomplete reason 的主导映射：

| 主导 reason | 文案 | 图标 |
| --- | --- | --- |
| `BASELINE_REJECTION` | stopped | `✗` |
| `INDETERMINATE` | stopped | `!` |
| `NO_PASS_IN_SEARCH_SPACE`、`NON_MONOTONIC`、`NONDETERMINISTIC`、`MISSING_CELL`、`UNREPRESENTABLE_PROJECTION` | incomplete | `⚠` |

多 reason 使用 D008 聚合结果；数值退出码只见 D001。Summary 使用人类语言，不回显 Enum，也不把一个 Proposal 的结果说成 dependency version 的全局结论。

## 8. Explain

`explain` 只回答：读取的 package/report、report complete 状态、该 report 是否授权 apply、Cell coverage、每条 declaration 的 floor/projection、每个目标 Cell 的最终状态与终止原因，以及可用的精确 diagnose 入口。它不核对当前 source/policy drift，不转储 observation、Proposal、process output 或技术 Enum。

默认结构：

```text
╭─ report card ─────────────────────────────────────────────╮
│ PACKAGE · package-floor.json                              │
│ Status: complete | incomplete                             │
│ Apply: authorized by this report | not authorized ...     │
│ Cells: covered/total                                      │
│                                                          │
│ Requirements                                              │
│   <raw declaration> <projection | no floor | blocked>     │
╰──────────────────────────────────────────────────────────╯

╭─ one card per target Cell ───────────────────────────────╮
│ <outcome icon> <Cell identity>                            │
│ <final status>                                            │
│ <terminal reason>                                         │
│ <optional exact pf diagnose --failure entry>              │
╰──────────────────────────────────────────────────────────╯

Summary: ...
Next: pf apply PACKAGE
```

Presenter 用 declaration ID 关联 raw declaration 与 projection，不能显示 digest 代替名称。多 marker requirements 在声明下缩进。Cell 卡片复用 smoke/check/search 的 outcome、identity、边框、Reason 和 diagnose-hint 视觉语言；TTY 使用 Rich Panel，非 TTY 保留相同信息顺序的纯文本降级。

Requirements 的 declaration 与单条 projection/detail 使用按当前内容计算的对齐列，
不得固定终端宽度。原始 declaration 的 dependency specifier 使用 cyan，其中 version
operand 使用 bold cyan；搜索得到的 projected requirement 使用 green，其中 version
operand 使用 bold green。package name、extras 与 marker 保持默认前景色；blocked / no
applicable floor 保持 warning 色。多 marker projection 继续在 declaration 下逐行缩进。

Cell 只投影报告中的最终状态。`CellIndeterminate` 选择其 `failure_id` 指向的终止 Failure；baseline rejection/indeterminate 选择 baseline Failure；完整评估无解、non-monotonic 与 nondeterministic 显示命令级结论；没有 CellResult 的 target Cell 显示 missing warning。仅权威终止 Failure 可以生成精确 `--failure` 入口，不能把历史候选 Failure 当作当前 blocker 或诊断目标。

默认 explain 不显示历史 Failure 轨迹、ty baseline、static increment、pytest detail、Proposal/source/policy IDs 或 process output。上述机械证据属于 `pf diagnose`。

## 9. Diagnose

`diagnose` 的数据语义与排序由 D005/D008 定义。D006 只固定 stdout 层级：

```text
Failure / Outcome
What happened / Impact / Next step
Context
Technical details
optional last 3 non-empty output lines
optional Process Log link
```

输出 tail 来自 D007 的安全 Process Log，stderr 非空时优先，否则 stdout；不能从 tail 重新分类。用户数据使用 literal Rich Text，不解释 markup。

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

必须保持：调用错误无 traceback；非零命令无成功措辞；每个顶层命令只有一个 final summary；Cell 始终含 Python/target/extra；diagnostic folding 不丢重数；非 TTY 无控制序列；display-only facts 不持久化。

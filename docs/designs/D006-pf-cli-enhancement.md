# PF CLI 交互与展示

- **状态：** 现行
- **最后核对：** 2026-08-26
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

未知 command/option、缺失或多余参数、非法 jobs/duration、未知 package 与 request 构造错误都返回 D001 退出码 `3`。结构错误尽早由 Cyclopts 拒绝；Request Schema 只作 defense-in-depth。不得宽泛捕获深模块 ValidationError 并伪装成调用错误。

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

`explain` 成功读取后全文在 stdout，即使报告 incomplete；读取失败仍走 stderr/exit 3。一个顶层命令只有一个 final summary，且它是最后一条结果信息。`minimize` 只调用 `render_minimize(reports, edits)`，不能连续渲染 search/apply 两份 summary。

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

## 5. Live Cell

TTY 的每个运行中 Cell 使用独立卡片：

```text
⠋ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:12
  [baseline][highest]
  dynamic tests ━━━━━━╺━━━━━━━━━━━━━ 37/120 tests ETA 0:00:41

⠋ [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:12
  [pydantic=1.5][1.0..2.0#7]
  dynamic tests ━━━━━━╺━━━━━━━━━━━━━ 37/120 tests ETA 0:00:41

⠋ searching cells · 2 running · 5 left                              0:00:12
```

`CellContextEvent` 提供当前 detail identity；baseline、declaration 与 search probe 都放在 title 后的第一条 detail，并将整条 identity 渲染为默认亮度 cyan，不按 token 降低亮度。Identity 切换清空旧 stage；同一 probe 的 static/witness/test 阶段保留 identity。Candidate discovery 清空 identity。Cache/known-PASS 未执行真实 probe 时不制造 detail。

只有 direct serial pytest 在 collection 完成并取得唯一 nodeid 集时显示 determinate `completed/total tests` 与 ETA；ETA 以当前 dynamic stage elapsed 的平均吞吐估计，尚无完成测试时为 `ETA --:--:--`。generic、collect-only、xdist/unknown、bootstrap/collection 未完成或首个合法 snapshot 前 telemetry 失败都保持 spinner。同一 stage 已显示 determinate progress 后，协议失效只冻结最后合法进度输入，不能降回 spinner。Progress/ETA 是 UI-only，不改变 TestOutcome。

Cell matrix 只登记总数；未启动 Cell 不建立 panel，因此可见 live Cell 数由 scheduler 实际并发自然约束为不超过 `jobs`。最后一行只显示 spinner、命令 phase、`N running`、`M left` 和右对齐总耗时，其中 `left = total - completed - running`；不显示方块矩阵或 completed/total。Live view 以 20 Hz 刷新 spinner/elapsed；一次 ActivityEvent 的 task snapshot 必须原子可见，stage progress 不通过删除/重建 task 产生闪烁。

外层 Console 最宽 120 列；内部 renderable 不设置固定 width/height。窄终端优先隐藏 bar、换行或改为 label block，不能丢失 package、Cell、artifact 或 next action。非 TTY 无 box drawing。

TTY Cell 卡片边框统一使用 dim，并保留原有颜色：live 卡片仍为默认前景色；完成卡片 success 为 green，failure 为 red，warning/indeterminate 为 yellow。dim 只作用于边框，不传递到卡片正文。

## 6. Cell completion 与 detail

成功 Cell 的 header 只有结果图标、Cell 与 elapsed；identity 位于第一条 detail：

```text
✓ [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:12
check passed at [declaration][lowest-direct]
```

有 FailureRecord 的 Cell 块为：

```text
✗ [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:19
smoke failed at [baseline][highest][testing]
The full test command failed for this version combination.
FAILED tests/test_cli.py::test_example
... and 2 more
-> run `pf diagnose demo --failure failure-38ac8f69eb9a182a` for more information.
```

固定层级：

1. 图标、完整 Cell 与 elapsed；
2. 命令 completion action、可选 detail identity 与用户阶段；
3. 一个 D005 title 或 search completion Reason；普通 Cell 不展示 Role impact；
4. 可选 `CellResultDetail` 的第一条典型详情与 `... and N more`；
5. Journal/Index 可用时显示精确 diagnose command。

completion action 统一为 smoke `passed/failed at`、check `passed/failed at`、search `completed/stopped at`。失败阶段作为 identity 后的第三个 bracket token；没有 identity 时仍使用单独的 bracket token。完成 detail 整行只使用对应结果色的默认亮度，不使用 dim、cyan、bold 或其他局部样式。

search 卡片的 primary failure 与结构化 detail 只取该 Cell 终止时收到的最新 `SearchFailureEvent`。末事件没有 detail 时不得回退到历史 probe；历史 failure 仍留在报告、Journal 与 `pf diagnose`。Reason 另行表达 Cell 为何结束：`INDETERMINATE` 明示搜索空间尚未评估完成并附终止 failure title；`NO_PASS_IN_SEARCH_SPACE` 明示搜索空间已完整评估但没有兼容组合。`NON_MONOTONIC` / `NONDETERMINISTIC` 显示各自的搜索结论，不借用某次历史候选拒绝作为 Cell 结论。

普通 Cell 不展示 baseline `ty` warning、stdout/stderr tail、Process Log link、cause/status Enum 或全部 Failures。若应有的 Journal/Index 写入失败使 diagnose 不可用，才回退到对应 Process Log link；没有日志则显示 `Detailed diagnosis unavailable.`。

`PytestFailureDetail` 只展示 `FAILED <nodeid>`、非 call phase 和数量。`StaticIssueDetail` 仅用于最终 `CONFIRMED_MISSING` witness 覆盖的 incremental issues；普通 static regression 与无关 `ty` facts 不显示。静态单行格式为：

```text
path[:line[:column]] [check_name] single-line message
```

每个 Cell 独立展示，不跨 Cell 聚合。TTY completion 立即从 active Cell 区移入 setup
首部下方的 pinned 完成区；命令 outcome 确定后与首部一起固结，不得改变两者顺序。
非 TTY 在 Cell completion 时立即输出等价稳定文本。

## 7. Final summary

格式按需组合：

```text
<icon> <command outcome> · <count/scope> · <artifact or next state>
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

| 主导 reason | 文案 | 图标 | exit |
| --- | --- | --- | --- |
| `BASELINE_REJECTION` | stopped | `✗` | 1 |
| `INDETERMINATE` | stopped | `!` | 4 |
| `NO_PASS_IN_SEARCH_SPACE`、`NON_MONOTONIC`、`NONDETERMINISTIC`、`MISSING_CELL`、`UNREPRESENTABLE_PROJECTION` | incomplete | `⚠` | 2 |

多 reason 使用 D001 优先级。Summary 使用人类语言，不回显 Enum，也不把一个 Proposal 的结果说成 dependency version 的全局结论。

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

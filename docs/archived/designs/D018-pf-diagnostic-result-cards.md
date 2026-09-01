# PF 诊断与结果命令卡片（归档）

- **状态：** 已完成，已归档
- **日期：** 2026-08-31
- **完成日期：** 2026-08-31
- **产品命令与退出码：** [D001](../../designs/D001-pf.md)
- **模块与 workflow：** [D002](../../designs/D002-pf-implementation.md)
- **Failure 与 diagnose 语义：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **现行终端展示：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **Journal 与诊断读取面：** [D008](../../designs/D008-pf-verification-run.md)
- **报告与 merge：** [D014](../../designs/D014-pf-report-schema.md)
- **实施计划：** [P024](../plans/P024-pf-diagnostic-result-cards.md)

本文记录 `pf explain | diagnose | apply | merge` 统一结果卡片、diagnose 单 Failure入口、
全部打印面的diagnose grammar及merge输入路径展示的迁移决策。实现与稳定规则已由上列现行owner
接管；本文只保留历史设计理由与验收来源。

PF 尚未上线。若本设计被接受，CLI grammar、workflow result、presenter、文档和测试直接替换为
本文目标，不保留 `--failure` alias、可选 Failure ID、批量 diagnose 或新旧输出双轨。

## 1. 问题与目标

`smoke | check | search` 已形成稳定的视觉层级：scope facts 卡片、必要的 Cell 结果卡片和最后一条
命令级摘要。四个离线诊断/结果命令仍不一致：

- `explain` 已有报告和 Cell 卡片，但成功 Cell 重复铺开，Requirements 在窄终端依赖手工空格，
  固定声明和 blocked projection 的信息不完整，summary 不复用统一图标语言；
- `diagnose` 可以在未指定 Failure ID 时打印全部记录，输出是无卡片的长文本，技术事实和用户结论
  视觉权重相同；
- `apply` 的普通成功只保留一条摘要，source waiver 又使用手工对齐的事实行；
- `merge` 不展示每个输入路径，presenter 还把输入数硬编码为一份报告。

本设计目标是：

1. 四个命令统一采用 Outcome card → optional detail card → final summary；
2. 颜色、图标、亮度和字重只强化结构，去除 ANSI 后仍无歧义；
3. `explain` 明确区分报告内失败与 explain 命令自身失败；
4. `diagnose` 一次只解释一个显式 Failure ID；所有打印出的 diagnose 调用使用同一 grammar；
5. `merge` 逐行完整显示输入路径和输出路径；
6. 保持 explain/diagnose/apply/merge 的领域、authority、通道和退出码边界。

## 2. 决策摘要

1. TTY 卡片统一使用 Rich rounded Panel、固定 icon/content 两列和 dim outcome-color 边框；
   非 TTY 按相同顺序输出无边框纯文本。
2. 结果图标保持 D006：`✓` success、`✗` Rejection/failure、`⚠` warning、`!` Indeterminate。
   `⚠` 同时用于 no floor、search failed 和 missing；图标不能替代文字。warning 与
   indeterminate 都可以是黄色，靠图标和文案区分。
3. 每个顶层命令只有一个 final summary，且它是最后一条结果信息；summary 使用统一 icon、
   outcome color 和 bold。
4. 通道跟命令结果走，不跟卡片颜色走。命令成功则事实卡和 final 都在 stdout，即使图标是
   `✗` / `!` / `⚠`。只有命令失败和实际使用 source waiver 的 apply 走 stderr。
5. `explain` 成功 Cell 使用紧凑行；rejected、indeterminate、no-floor、search-failed 和
   missing Cell 才使用完整 detail card。所有 target Cell 仍必须出现，不做截断。
6. 命令级 next action 在结果信息中只出现一次，且不是 final summary。
7. `pf diagnose FAILURE_ID [--package PACKAGE]` 是唯一 grammar。`FAILURE_ID` 是必填位置
   参数；接受规范形式 `failure-<id>` 和省略前缀的 `<id>`，进入 request 前统一为规范
   Failure ID。live Cell、explain Cell、Usage 和 Help 打印同一命令正文。删除 `--failure`
   和省略 ID 时列出全部的行为。
8. Diagnose workflow 只解析一个 Failure，report 优先于 latest Journal；不存在时抛出
   typed `DiagnoseNotFoundError`，不扫描历史 run，也不存在“成功展示零条”。
9. Merge 卡片的 Inputs 区逐行显示 `MergeRequest.reports` 中的每个规范展示路径，不用
   `2 reports` 等计数替代路径；final summary 使用 `Merge complete · OUTPUT`，不重复输入数量。
   输入失败 fail-fast 于第一份不可读或非法报告。
10. Presenter 只消费 workflow 给出的结构化 presentation facts，不从异常正文、report wire、
    文件系统或 Rich 渲染结果反推路径、scope 或 domain outcome。
11. Apply 卡片的 package 只来自 `ApplyCommandResult.package`；scope 只来自已有
    `selected_selectors` / `preserved_selectors`。`minimize` 复用同一张 apply 卡，仍只有
    一条 final。

## 3. 共享视觉语言

### 3.1 卡片与层级

TTY 结果卡片使用：

```text
╭──────────────────────────────────────────────────────────╮
│ <icon>  <subject · outcome>                              │
│         <primary fact>                                   │
│                                                          │
│ <Section>                                                │
│   <label>  <value>                                       │
│                                                          │
│ -> <next action when one exists>                         │
╰──────────────────────────────────────────────────────────╯
<icon>  <one final command summary>
```

- subject 和 section heading 使用 bold 默认前景色；package 使用 bold cyan；
- success/failure/warning/indeterminate outcome 使用对应结果色并 bold；
- field label 使用 dim 默认前景色，field value 使用默认前景色；
- path 使用 cyan；
- Requirements 保留 D006：原始 declaration 的 dependency specifier 使用 cyan，version
  operand 使用 bold cyan；projected requirement 使用 green，version operand 使用 bold
  green；package name、extras 与 marker 保持默认前景色；blocked / no applicable floor
  保持 warning 色；
- next action 使用 italic cyan，命令正文不 dim；
- technical details 与 output tail 使用 dim，但 ID、Enum 和输出正文仍保持可读；
- 用户提供或报告读取的字符串一律构造 literal `Text`，禁用 markup 解释和自动 highlighter。

OSC 8 file link 只改变 TTY 的 underline target，不改变 display path：

- explain / diagnose / apply 的相对 path：以该命令 `request.root` 为前缀构造绝对 target；
- merge 的相对 path：以进程 `Path.cwd()` 为前缀构造绝对 target；绝对 path 直接用作
  target；
- 不调用 `resolve()`、不 `stat`、不读取内容、不展开 symlink；
- 非 TTY 不输出 OSC，仍保留完整 display path。

外层最宽 120 列。56、80、120 列下，事实表使用 `Table.grid` 自然换行；marker、label 和续行都由
Rich 列布局保持悬挂缩进，不能通过手工空格猜测宽度。路径、ID 和 Cell identity 可在 content
列内折行但不得省略。宽度测试通过 public presenter 在指定 console width 下记录无色纯文本行宽，
不断言边框或完整 ANSI snapshot。

### 3.2 通道和结果

| 情形 | 通道 | 退出码 / final |
| --- | --- | --- |
| explain 成功读取 complete 或 incomplete report | stdout | `0`；final 反映 report outcome |
| diagnose 成功解释指定 Failure | stdout | `0`；绿色命令级 final，不继承原运行退出码 |
| merge 成功写 output，含结果仍 incomplete | stdout | `0`；绿色命令级 final |
| apply 默认成功 | stdout | `0`；绿色命令级 final |
| apply 实际使用 source waiver | stderr | `0`；黄色卡片和 final |
| 预期调用错误 | stderr | `1`；Usage 与 Try hint |
| report/Failure/config/apply authorization 错误 | stderr | D001 对应退出码；无 Usage |

报告内 red/yellow Cell、diagnose 事实卡上的 `✗`/`!`、merge Result 的 yellow incomplete
都不改通道。卡片必须分别写出“report is incomplete”与“explain failed”，不能只靠红色判断
命令是否执行成功。

## 4. `pf explain`

### 4.1 报告卡片

Report overview 固定包含：

1. package 与 report path；
2. report `complete | incomplete`；
3. report-intrinsic apply evidence，明确 `pf apply` 仍会复核当前项目；
4. Cell 结果分布，以及 overview 内的成功 Cell 紧凑行；
5. 每条 requirement declaration 的 projection 状态；
6. 仅当本节 next-action 规则选中 overview 时，给出唯一命令级 next action。

Cell 分布只显示非零 bucket：

```text
passed         CellSuccess
rejected       BaselineRejection
unknown        BaselineIndeterminate | CellIndeterminate
no floor       CellSearchFailure.reason == NO_PASS_IN_SEARCH_SPACE
search failed  CellSearchFailure.reason == NON_MONOTONIC | NONDETERMINISTIC
missing        target Cell 没有 CellResult
```

例如 `Cells  1 passed · 1 rejected · 1 missing · 3 total`。这不是 report coverage 的别名。
`no floor` 不得用于 `NON_MONOTONIC` 或 `NONDETERMINISTIC`；后两者在分布中使用 `search failed`，
detail card 使用 D006 的终止原因文案。

成功 Cell 在 overview 的 Cells section 先写分布行，再逐行显示 icon 和完整 Cell identity，
不再创建单独 Panel。其余 Cell 各用一张共享 Cell outcome card，展示最终状态、终止原因和
适用的精确 diagnose command。混合报告中每个 target 恰好出现一次：成功行只在 overview，
异常 Cell 只在后续独立卡。

Requirements 使用 declaration ID 关联原声明与 projection：

- managed 且 representable：`RAW -> PROJECTED`；
- managed 且空 projection：`no applicable floor`；
- managed 且不可表示：`projection blocked`，随后列出已有 `ProjectionEvidence.floors` 的
  Cell/version，不得因最终 projection 失败而隐藏已验证事实；
- 非 managed 声明：显示 `fixed · not managed`；
- 宽度不足时单条 requirement 改为 declaration 与 projection 上下两行，不让 marker 续行落到
  卡片左边界。

命令级 next action 只出现一次，且不是 final summary：

- complete 且 report-intrinsic eligible，或 incomplete 但满足 D006 的 scoped-eligible
  条件：overview 给出 `pf apply --package PACKAGE`；`PACKAGE` 是该报告的 canonical
  distribution name。省略 `--package` 的 explain 调用仍打印带 `--package` 的 apply 命令，
  以便复制；
- 存在权威终止 Failure：该 Cell 卡给出 diagnose 命令；overview 不再给出 apply 或 diagnose；
- 其余 incomplete：无 next-action 行；summary 说明 apply blocked。

Complete report 的 summary 按实际 managed projection 使用专门的 0/1/N 文案：

```text
0  No managed dependencies require floor changes.
1  1 managed dependency has a verified floor.
N  N managed dependencies have verified floors.
```

### 4.2 报告内失败示例

以下示例表示 explain 成功读到了一个包含 baseline rejection 的 incomplete report。全文在 stdout，
exit `0`；红色说明报告证据，`Explain failed` 不得出现。overview 无 next action；diagnose
命令只在 rejected Cell 卡上：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✗  demo · package-floor.json                                      │
│    incomplete · apply blocked by report evidence                   │
│    current project was not inspected                               │
│                                                                    │
│ Cells                                                              │
│   1 rejected · 1 total                                             │
│                                                                    │
│ Requirements                                                       │
│   idna>=2  no applicable floor                                     │
╰────────────────────────────────────────────────────────────────────╯

╭────────────────────────────────────────────────────────────────────╮
│ ✗  [py3.11][x86_64-unknown-linux-gnu][no-extra]                    │
│    search stopped at [baseline][highest][testing]                  │
│    The configured verifier rejected this version combination.      │
│    -> pf diagnose failure-38ac8f69eb9a182a --package demo          │
╰────────────────────────────────────────────────────────────────────╯
✗  Report incomplete · 1 rejected cell · apply blocked
```

passed + rejected 的混合报告把成功 Cell 留在 overview，rejected Cell 仍是独立卡：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✗  demo · package-floor.json                                      │
│    incomplete · apply blocked by report evidence                   │
│    current project was not inspected                               │
│                                                                    │
│ Cells                                                              │
│   1 passed · 1 rejected · 2 total                                  │
│   ✓  [py3.11][x86_64-unknown-linux-gnu][no-extra]                  │
│                                                                    │
│ Requirements                                                       │
│   idna>=2  no applicable floor                                     │
╰────────────────────────────────────────────────────────────────────╯

╭────────────────────────────────────────────────────────────────────╮
│ ✗  [py3.10][x86_64-unknown-linux-gnu][no-extra]                    │
│    search stopped at [baseline][highest][testing]                  │
│    The configured verifier rejected this version combination.      │
│    -> pf diagnose failure-38ac8f69eb9a182a --package demo          │
╰────────────────────────────────────────────────────────────────────╯
✗  Report incomplete · 1 rejected cell · apply blocked
```

Cell 图标：rejected 使用 `✗`；indeterminate 使用 `!` 和 `compatibility is unknown`；
`NO_PASS_IN_SEARCH_SPACE` 使用 `⚠` 与 no-floor 文案；`NON_MONOTONIC` / `NONDETERMINISTIC`
使用 `⚠` 与各自的 D006 终止原因；missing 使用 `⚠`。只有权威终止 Failure 可以生成
diagnose 命令；missing、no-floor 和 search-failed 不得拿历史 probe Failure 冒充入口。

打印出的 diagnose 命令正文始终是：

```text
pf diagnose failure-<16 lowercase hex> --package <canonical-name>
```

live Cell（smoke / check / search）继续用 D006 的 `run \`...\` for more information`
包装同一命令正文；explain Cell 使用上面的短 hint。两者都不使用 `--failure`。

### 4.3 Explain 命令自身失败示例

报告缺失、不可读、Schema 非法或 package identity 不匹配表示 explain 命令失败，走 stderr 和
D001 的配置错误 exit `3`。缺失报告示例：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✗  Explain failed                                                  │
│    report  package-floor.json                                      │
│    reason  report is unavailable                                   │
│    -> run `pf search --package demo` to create the report           │
╰────────────────────────────────────────────────────────────────────╯
✗  Explain failed · package-floor.json unavailable
```

`ExplainCommandWorkflow` 把 report read/validation/identity 错误转换为 typed
`ExplainReportError(ConfigurationError)`，携带 display path、稳定 reason 和可用时的 recovery
command；Presenter 不解析异常正文。recovery command 回显本次 explain 的 selector：调用带
`--package PACKAGE` 时建议 `pf search --package PACKAGE`；省略 `--package`（选中可安装
root）时建议 `pf search`。无安全恢复命令的 invalid report 只显示原因，不建议 apply、
force 或自动覆盖证据。Project selection 等非 report 错误继续使用现行通用错误展示。

### 4.4 Explain 边界

Explain 仍然离线，只描述 `ValidatedReport`。它不读取当前 source/policy/dependency state，不运行
工具，不输出 observation、历史 candidate Failure、Proposal/process output，不承诺 apply 已授权。
live Cell 的视觉语言除 diagnose 命令正文外仍由 D006 拥有；本设计不改 live 的 elapsed、detail
folding 或 pinned 完成区。

## 5. `pf diagnose FAILURE_ID`

### 5.1 CLI grammar

目标 interface：

```text
pf diagnose FAILURE_ID [--package PACKAGE]
```

- `FAILURE_ID` 是唯一必填位置参数，接受且只接受 `failure-<16 位小写十六进制>` 或
  `<16 位小写十六进制>`；
- CLI value converter 对短形式只补上 `failure-`，随后按 D005 的规范完整形式校验；
- `DiagnoseRequest`、report/Journal lookup、Process Log association 和所有输出始终使用完整的
  `failure-<16 位小写十六进制>`；短形式不是第二种领域 identity；
- 缺少、多余或形状非法的 ID 是 invocation error：exit `1`、stderr、精确 Usage 和 Try hint；
- 形状合法但 report/latest Journal 中不存在是配置错误：exit `3`、stderr、无 Usage；
- 删除 `--failure`；不提供 alias、deprecation、dual grammar 或 compatibility parser；
- 删除无 ID 时合并/排序/打印全部记录和 `diagnosed 0 failures` 的行为。

该 grammar 同时约束 CLI、Help、live Cell hint 和 explain Cell hint。生产源码、公开测试和
文档示例中不存在作为 diagnose 选项或命令正文的 `--failure`。

Usage 固定为：

```text
Usage: pf diagnose FAILURE_ID [OPTIONS]
```

Help 中位置参数说明固定为：

```text
FAILURE_ID  A failure-<id> value; the failure- prefix may be omitted.
```

`--package` 的 Help 文案保持 D006 公共选项。命令 docstring 保持“Explain a recorded
rejection or indeterminate result.”，不再写“Omit to list every recorded failure”。

### 5.2 单 Failure 读取面

`DiagnoseRequest.failure_id` 是 required `str`，且已经是规范完整 Failure ID。
`DiagnoseCommandWorkflow.run`：

1. 选择一个 package；
2. 若 package report 存在，按 ID 查找；命中后解析 context 与本机 report association；
3. 未命中时只查该 package latest Journal；
4. 仍未命中则抛出 `DiagnoseNotFoundError`，不遍历历史 runs；
5. 返回一个 `FailureDiagnosis`，不返回 tuple。

Report 与 latest Journal 同时包含同一 ID 时 report 优先；两边的 portable authority 仍必须满足
D008 的一致性约束。Process Log 缺失只降低 technical detail，不改变 Failure authority 或命令成功。

### 5.3 Diagnose 卡片

每次只渲染一张 Failure 卡片和一条 final summary：

```text
pf diagnose failure-02cc9a72fbcd6cf0 --package pf
pf diagnose 02cc9a72fbcd6cf0 --package pf
```

两条 invocation 等价；卡片、final 和后续诊断命令始终显示规范完整 ID。

What happened 与 next action 使用 D005 现行英文 title / next step，Presenter 不得改写。
Impact 使用本节归并到 D008 的 Role 表。probe Rejection 且 `boundary_role == predecessor`
时，在 probe Rejected 句后追加 “It helped establish the verified floor.”

Technical details 必选出齐，缺省写稳定占位，不省略字段名：

```text
disposition
cause
attempt            attempt id，或 not available
resolution         requested_resolution，或 not applicable
vector             requested managed vector，或 not applicable
proposal           proposal id，或 not available
boundary           predecessor，或 none
process            有 process/verifier terminal 时写出；否则省略该行
detail / detail code  仅当 FailureRecord.detail 存在
output             仅当 D007 tail 非空；最多最后 3 行
log                本机相对 path，或 Detailed local log is unavailable.
```

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✗  failure-02cc9a72fbcd6cf0 · rejected candidate                  │
│                                                                    │
│ What happened  The configured verifier rejected this version       │
│                combination.                                        │
│ Impact         This candidate was excluded from the search.        │
│ -> Review the verifier diagnostics and log before changing code    │
│    or dependency constraints.                                      │
│                                                                    │
│ Context                                                            │
│   package  pf                                                      │
│   cell     [py3.10][x86_64-unknown-linux-gnu][no-extra]            │
│   stage    testing                                                 │
│   source   package-floor.json                                      │
│                                                                    │
│ Technical details                                                  │
│   disposition  REJECTED                                            │
│   cause        VERIFIER_EXITED_NONZERO                             │
│   attempt      7f3c2a1b                                            │
│   resolution   exact-vector                                        │
│   vector       cyclopts==4.10.2, pydantic==1.7.4                   │
│   proposal     not available                                       │
│   boundary     none                                                │
│   process      exited 4                                            │
│   output       FAILED tests/test_cli.py::test_example              │
│   log          .pf/logs/run/process-1187.log                       │
╰────────────────────────────────────────────────────────────────────╯
✓  Diagnosis complete · failure-02cc9a72fbcd6cf0
```

Failure card 的 `✗`/`!` 表达被诊断事实；绿色 final 表达离线诊断命令成功，不得把原 smoke/check/
search exit code 传播给 diagnose。

### 5.4 Role→impact（归并到 D008）

离线 diagnose 不得承诺搜索仍在进行。实施时 D008 §10 替换为：

| Role | Rejected | Indeterminate |
| --- | --- | --- |
| probe | This candidate was excluded from the search. | Compatibility for this candidate is unknown, so this cell stopped. |
| baseline/search | The highest-version baseline did not pass, so the floor search did not start for this cell. | Compatibility of the highest-version baseline is unknown, so this cell stopped. |
| baseline/smoke | The highest-version resolution did not pass the required checks. | Compatibility of the highest-version resolution is unknown. |
| declaration-capture | A static baseline could not be captured from the current declarations, so declared lower bounds were not verified for this cell. | Whether a static baseline can be captured is unknown, so declared lower bounds were not verified for this cell. |
| declaration | The declared lower bounds did not pass the required checks. | Compatibility of the declared lower bounds is unknown. |
| Cell scope | 不允许 | PF could not obtain the information needed to start or continue this cell. |

D005 拥有 title/next step 与 disposition；D008 唯一选择上表；D006 只渲染。live Cell 默认仍不
展示 Role impact。

### 5.5 Diagnose 场景示例

用户可观察的结果分为：

| 场景 | 展示与结果 |
| --- | --- |
| 完整 ID / 短 ID | 两种输入规范化为同一完整 ID；stdout、exit `0` |
| report 中的 Rejected | 红色事实卡；展示必选 technical 字段、process output 和本机 log（若有） |
| latest Journal 中的 Indeterminate | 黄色事实卡；`source` 显示具体 latest command；stdout、exit `0` |
| Process Log 缺失 | 仍诊断成功；明确写出 `Detailed local log is unavailable.` |
| 形状合法但不存在 | stderr error card、exit `3`，无 Usage；`DiagnoseNotFoundError` |
| 缺失、非法、多余 ID 或旧 `--failure` | stderr、exit `1`，有 Usage 与 Try hint |

短 ID、latest Journal、Indeterminate 且没有本机 Process Log。What happened / next step 为
D005 的 `SOURCE_FAILURE` 原文：

```text
$ pf diagnose da3093130a15a1c6 --package pf
╭────────────────────────────────────────────────────────────────────╮
│ !  failure-da3093130a15a1c6 · compatibility unknown               │
│                                                                    │
│ What happened  PF could not reach or read a configured package     │
│                source.                                             │
│ Impact         Compatibility for this candidate is unknown, so     │
│                this cell stopped.                                  │
│ -> Check the index URL, network, credentials, and source           │
│    availability, then rerun PF.                                    │
│                                                                    │
│ Context                                                            │
│   package  pf                                                      │
│   cell     [py3.12][x86_64-unknown-linux-gnu][no-extra]            │
│   stage    testing                                                 │
│   source   latest pf check                                         │
│                                                                    │
│ Technical details                                                  │
│   disposition  INDETERMINATE                                       │
│   cause        SOURCE_FAILURE                                      │
│   attempt      b91e04c2                                            │
│   resolution   exact-vector                                        │
│   vector       not applicable                                      │
│   proposal     not available                                       │
│   boundary     none                                                │
│   process      could not start                                     │
│   log          Detailed local log is unavailable.                  │
╰────────────────────────────────────────────────────────────────────╯
✓  Diagnosis complete · failure-da3093130a15a1c6
```

完整或短 ID 形状合法，但选中 package 的 report 与 latest Journal 都没有该 Failure。
Presenter 只读 `DiagnoseNotFoundError` 字段：

```text
$ pf diagnose aaaaaaaaaaaaaaaa --package pf
╭────────────────────────────────────────────────────────────────────╮
│ ✗  Diagnosis failed                                                │
│    failure  failure-aaaaaaaaaaaaaaaa                               │
│    package  pf                                                     │
│    reason   failure ID was not found in package-floor.json or the  │
│             latest local Journal                                   │
╰────────────────────────────────────────────────────────────────────╯
✗  Diagnosis failed · failure ID not found
```

缺少 ID 的 invocation error 不使用结果卡片：

```text
Error: Missing argument 'FAILURE_ID'.
Usage: pf diagnose FAILURE_ID [OPTIONS]
Try 'pf diagnose --help' for more information.
```

非法形状使用相同 Usage 结构，error 写明
`expected failure-<16 hex> or <16 hex>`；多余位置参数和旧 `--failure` 分别由 CLI parser 报告
unexpected argument / unknown option，不进入 workflow。

## 6. `pf apply`

Apply 成功卡片必须展示授权后才成立的事实，不从 report 顶层 status 推断。卡片 subject 的
package 只读 `ApplyCommandResult.package`。`ApplyPresentationFacts` 不增加 package 字段，
继续只携带 `observed_cells`、`selected_selectors`、`preserved_selectors`、
`source_drift_path_count` 和最多 8 条 `source_drift_paths`。Presenter 用 selector 元组
格式化 Scope / Preserved，不另造第三份 scope 字符串。

```text
╭──────────────────────────────────────────────────────────╮
│ ✓  pf · applied verified floors                         │
│ Evidence   3 observed cells passed                       │
│ Scope      all declared platforms                        │
│ Metadata   pyproject.toml updated                        │
╰──────────────────────────────────────────────────────────╯
✓  Applied floors · project updated
```

`selected_selectors` 覆盖声明矩阵且 `preserved_selectors` 为空时，Scope 为
`all declared platforms`。Platform-scoped 时显示：

```text
Scope       linux/x86_64 verified
Preserved   windows/x86_64 · original constraints retained
```

`preserved` 不得使用 passed/covered；它只表示本 generation 未验证而保留原约束。NOOP 显示
`Metadata pyproject.toml unchanged`。实际 source waiver 使用黄色卡片：

```text
╭──────────────────────────────────────────────────────────╮
│ ⚠  pf · applied with source-drift override              │
│ Evidence   2 observed cells passed                       │
│ Override   source drift accepted · 10 paths              │
│ Paths      src/a.py, ... (+2 more)                       │
│ Metadata   pyproject.toml updated                        │
╰──────────────────────────────────────────────────────────╯
⚠  Applied floors with source-drift override · project updated
```

最多展示 8 条规范相对路径，不输出内容、diff 或 digest。`ProjectEditResult` 继续提供
pyproject path 与 changed。Presenter 不重新授权、不读取 TOML，也不把 `--force` 本身当成
waiver 已使用。`render_minimize` 继续只调用同一 `render_apply`；search 的 live 输出已经
在 minimize 前完成，apply 段仍只有一张卡和一条 final。

## 7. `pf merge`

### 7.1 路径展示

Merge grammar 保持：

```text
pf merge REPORT [REPORT ...] --output PATH
```

成功卡片逐行显示全部输入路径，顺序与 invocation 一致：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✓  Merge completed                                                 │
│                                                                    │
│ Inputs                                                             │
│   reports/linux/package-floor.json                                 │
│   reports/windows/package-floor.json                               │
│                                                                    │
│ Result   demo · complete · 3/3 cells passed                        │
│ Output   merged/package-floor.json                                 │
╰────────────────────────────────────────────────────────────────────╯
✓  Merge complete · merged/package-floor.json
```

路径展示规则：

- 使用 `MergeRequest` 经过 `Path(...).as_posix()` 的路径字符串；
- 相对输入保持相对，绝对输入保持绝对；不调用 `resolve()`、不展开 symlink；
- 每个 argument 独立成行，重复路径也按 invocation 原样保留；
- 不截断为 basename，不用 `N reports` 替代，不隐藏中间项；
- TTY OSC 8 按 §3.1：相对 path 以 `Path.cwd()` 为前缀，绝对 path 直接作 target；不改变
  display path；
- output 使用实际成功写入的规范展示路径。

成功 merge 但合并结果仍 incomplete 时，header/final 仍表示 merge 操作成功，全文 stdout；
`Result` 的 `incomplete`、Cell 分布和 apply blocker 使用黄色。不能把“成功写入合并报告”
说成“报告已经可 apply”。

### 7.2 Merge 场景示例

| 场景 | 展示与结果 |
| --- | --- |
| 单个或多个兼容输入，结果 complete | 绿色成功卡；逐行显示全部输入和 output；exit `0` |
| 兼容输入，结果 incomplete | merge 仍成功；Result 与 apply blocker 为黄色；stdout、exit `0` |
| 输入缺失、不可读或 Schema 非法 | 失败卡保留全部输入并标记第一份失败路径；exit `3` |
| generation/package identity 不兼容或 Cell 冲突 | 失败卡保留全部输入和稳定 detail；exit `3` |
| output 原子写入失败 | 红色 command-failure card；明确 `not written`；exit `4` |
| 缺少 REPORT、缺少 `--output` 或多余 option | parser error、Usage 与 Try hint；exit `1` |

单个输入使用相同成功布局，只在 Inputs 下显示一行，不写 `1 report`。多个输入合并为 incomplete
report：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✓  Merge completed                                                 │
│                                                                    │
│ Inputs                                                             │
│   /workspace/reports/linux/package-floor.json                      │
│   /workspace/reports/windows/package-floor.json                    │
│                                                                    │
│ Result   demo · incomplete                                         │
│          2 passed · 1 missing · 3 total                            │
│ Apply    blocked by report evidence                                │
│ Output   /workspace/reports/merged/package-floor.json              │
╰────────────────────────────────────────────────────────────────────╯
✓  Merge complete · /workspace/reports/merged/package-floor.json
```

输入缺失、不可读或 Schema 非法时 fail-fast：workflow 在第一份失败处停止，不继续读取后续
输入以凑更多 Failed 行。`input_paths` 仍是 invocation 的完整元组；`failed_input_path`
是该份：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✗  Merge failed                                                    │
│                                                                    │
│ Inputs                                                             │
│   /workspace/reports/linux/package-floor.json                      │
│   /workspace/reports/windows/package-floor.json                    │
│ Failed  /workspace/reports/windows/package-floor.json              │
│ Reason  input report is unavailable or invalid                     │
│ Output  /workspace/reports/merged/package-floor.json · not written │
╰────────────────────────────────────────────────────────────────────╯
✗  Merge failed · input report unavailable
```

所有输入均可读，但 generation、package identity 不兼容或同一 Cell 的结果冲突：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✗  Merge failed                                                    │
│                                                                    │
│ Inputs                                                             │
│   /workspace/reports/linux/package-floor.json                      │
│   /workspace/reports/windows/package-floor.json                    │
│ Reason  reports are incompatible and cannot be merged              │
│ Detail  report generation identity mismatch                        │
│ Output  /workspace/reports/merged/package-floor.json · not written │
╰────────────────────────────────────────────────────────────────────╯
✗  Merge failed · reports are incompatible
```

报告兼容且已完成内存合并，但 output 无法原子写入：

```text
╭────────────────────────────────────────────────────────────────────╮
│ ✗  Merge failed                                                    │
│                                                                    │
│ Inputs                                                             │
│   /workspace/reports/linux/package-floor.json                      │
│   /workspace/reports/windows/package-floor.json                    │
│ Reason  merged report could not be written reliably                │
│ Output  /read-only/merged/package-floor.json · not written         │
╰────────────────────────────────────────────────────────────────────╯
✗  Merge failed · output was not written
```

调用错误不使用结果卡片，例如缺少 output：

```text
Error: Missing option '--output'.
Usage: pf merge REPORT [REPORT ...] --output PATH
Try 'pf merge --help' for more information.
```

### 7.3 Merge result seam

Workflow 返回：

```text
MergeCommandResult
  report: ValidatedReport
  input_paths: tuple[str, ...]
  output_path: str

MergeCommandWorkflow.run(MergeRequest) -> MergeCommandResult
```

成功时，`input_paths` 在全部 report 成功读取、兼容 merge 且 output 原子写入后随 result 返回。
失败时，workflow 按阶段抛出 typed error：

```text
MergeInputError(ConfigurationError)
MergeCompatibilityError(ConfigurationError)
MergeOutputError(InfrastructureError)
```

每种 error 均携带 `input_paths: tuple[str, ...]`、`output_path: str`；input error 另带
`failed_input_path: str`，compatibility error 另带稳定的 `detail: str`。这些字段来自 request 和
ReportStore 的结构化判断，Presenter 不解析 D014 的异常正文。read/validation 错误映射为 exit `3`，
merge compatibility 错误映射为 exit `3`，atomic write 的环境错误映射为 exit `4`。Presenter 不再
接收一个未使用的 report 后把输入数硬编码为 1。
D014 的 merge compatibility、canonical graph、generation 和 atomic write 语义不变。

## 8. Interface 与 ownership 迁移

目标 public seam：

```text
DiagnoseRequest
  root
  selector
  failure_id: str  # canonical failure-<16 lowercase hex>

DiagnoseCommandWorkflow.run(request) -> FailureDiagnosis
TerminalPresenter.render_diagnose(diagnosis) -> int

DiagnoseNotFoundError(ConfigurationError)
  failure_id: str
  package: str
  reason: str

ExplainReportError(ConfigurationError)
  report_path: str
  reason: str
  recovery_command: str | None

MergeCommandWorkflow.run(request) -> MergeCommandResult
TerminalPresenter.render_merge(result) -> int

MergeInputError(ConfigurationError)
  input_paths: tuple[str, ...]
  output_path: str
  failed_input_path: str

MergeCompatibilityError(ConfigurationError)
  input_paths: tuple[str, ...]
  output_path: str
  detail: str

MergeOutputError(InfrastructureError)
  input_paths: tuple[str, ...]
  output_path: str

ApplyCommandResult
  package: str  # canonical distribution name; sole card subject
  edit: ProjectEditResult
  presentation_facts: ApplyPresentationFacts
```

`DiagnoseNotFoundError.reason` 的稳定句子是
`failure ID was not found in package-floor.json or the latest local Journal`。
`ApplyPresentationFacts` 保持现有字段，不复制 package。

`pf.terminal` 内建立共享 result-card/fact-grid renderable，复用 D006 的 marker/content gutter、
outcome border 和 final summary。该 helper 是 terminal private implementation，不成为领域 schema。
Workflow、ReportStore、authorizer 与 editor 不导入 Rich 或拼接用户文案。

接受并实施后，稳定规则分别归并：

| Owner | 归并内容 |
| --- | --- |
| D001 | diagnose 必填位置 ID、全部打印面 grammar、命令行为和退出码 |
| D002 | 单 Failure workflow/result、typed diagnose/explain/merge errors 与 MergeCommandResult / ApplyCommandResult.package |
| D005 | 单 Failure diagnose、不存在/不批量；Cause title/next step 原文不变 |
| D006 | 四命令卡片、live/explain diagnose 命令正文、Help、图标、通道、summary、宽度、Requirements 色和全部示例 |
| D008 | report-first/latest-Journal-second 的 ID-only lookup 与 §5.4 Role impact 表 |
| D014 | 不改 merge authority；只引用 workflow 的结构化结果边界 |

随后建立 durable Plan，完成 CLI/schema/workflow/presenter/tests/docs 迁移；稳定规则归并到 owner 后，
D018 与 Plan 在同一完成变更中归档。

## 9. 非目标

- 不改变 report Schema 1 wire、merge compatibility 或 apply authority；
- 不让 explain 读取当前项目、Journal、Process Log 或历史 probe；
- 不让 diagnose 重放 Failure、扫描历史 run、自动修复或推断根因；
- 不提供批量 diagnose、`--all`、分页、交互选择器或 Failure 搜索命令；
- 不增加 merge report 内容 dump、diff 或 cross-generation rebase；
- 不把颜色、边框、terminal width 或 presentation paths 写入 report/Journal/identity；
- 不为旧 `--failure` grammar 或旧 renderer 添加兼容层；
- 不改 live Cell 的 elapsed、detail folding、pinned 完成区或 Role impact 展示策略，只替换
  其中的 diagnose 命令正文。

## 10. 验收标准

1. `pf diagnose FAILURE_ID [--package PACKAGE]` 是唯一 grammar；`FAILURE_ID` 接受
   `failure-<16 lowercase hex>` 或 `<16 lowercase hex>`，进入 request 前规范化为完整形式；
   缺失/多余/非法形状 exit `1` 且带精确 Usage，合法但未知 ID 抛 `DiagnoseNotFoundError`、
   exit `3` 且无 Usage。
2. Diagnose request/workflow/presenter 全部使用规范完整 ID 和单数结果。生产源码、公开测试和
   文档示例中不存在 optional failure ID、list-all、空成功，也不存在作为 diagnose 选项或
   命令正文的 `--failure`。live Cell、explain Cell、Usage 和 Help 打印同一命令正文
   `pf diagnose failure-<id> --package <name>`。
3. ID lookup 只访问选中 package report 和 latest Journal，report 优先；不扫描历史 run；Process Log
   缺失不改变 authority。
4. Explain 对 report-contained rejection / indeterminate / no-floor /
   search-failed / missing 分别使用 `✗` / `!` / `⚠` / `⚠` / `⚠`，全文 stdout、exit `0`。
   分布文案区分 `no floor` 与 `search failed`。只为权威终止 Failure 提供新 diagnose command。
5. Explain 命令自身的 report read/validation/identity 错误使用 stderr error card 和 D001 exit；
   recovery command 回显本次 selector；不得误写成 report 内 failure。
6. Explain 完整列出所有 target Cell；成功 Cell 只出现在 overview 紧凑行，异常 Cell 只出现在
   独立卡。Cell 分布、fixed declaration、blocked projection floors 和 0/1/N managed summary
   无歧义。命令级 next action 至多一条，且不在 final summary 里。
7. Apply default/scoped/no-op/source-waiver 卡片只消费 `ApplyCommandResult.package`、
   `edit` 和现有 `ApplyPresentationFacts`；preserved 不冒充本 generation PASS，source waiver
   路径最多 8 条。minimize 复用同一张 apply 卡和一条 final。
8. Merge complete/incomplete 成功及 input/compatibility/output 失败卡片都逐行、按 invocation 顺序
   完整显示所有 input paths 和 output path；input failure 只标记第一份失败路径；成功 final 为
   `Merge complete · OUTPUT`；代码中没有硬编码输入数量。
9. 四个命令在 TTY 复用 rounded card、outcome border、icon/content gutter 和 bold final；非 TTY
   事实、顺序、通道和 final 数量相同且无 ANSI/OSC。命令成功时红/黄事实卡仍在 stdout。
10. 56/80/120 列下，public presenter 的无色纯文本行宽不超过 console width，续行保持在
    content/value 列；路径、ID、Cell identity 和 literal user strings 不被 markup/highlighter
    改写。不断言边框 ANSI 或整段颜色 snapshot。
11. Diagnose What happened / next step 与 D005 英文 title/next step 字节一致；Impact 与
    §5.4 表一致。Technical details 必选字段出齐。测试锁定 public CLI/workflow/presenter
    语义和关键字段，不依赖 private helper。
12. 实施完成时 D001/D002/D005/D006/D008 的唯一 owner 已归并，D014 明确保持不变；Design/Plan
    状态、文档索引、验证证据和归档位置一致。

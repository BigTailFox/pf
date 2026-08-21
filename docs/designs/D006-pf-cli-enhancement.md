# PF CLI 交互与展示增强

- **状态：** 现行契约，待实现
- **最后核对：** 2026-08-21
- **产品与命令：** [D001](D001-pf.md)
- **模块接口：** [D002](D002-pf-implementation.md)
- **静态证据：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)

本文是 PF CLI 帮助信息架构、调用错误反馈、终端信息层级、命令结果摘要和 `explain` 展示的唯一契约。D001 继续定义命令、参数语义、产品结果和退出码；D002 继续定义 `cli.py` / `TerminalPresenter` 的模块位置；D004 定义静态诊断事实；D005 定义 failure 的 title、impact、next step、技术信息和 `diagnose` 行为。本文只组织这些事实，不重新分类证据或改变产品语义。

## 1. 问题

现有 CLI 已具备 Rich 自适应布局、TTY 动态进度、非 TTY 稳定文本、单行诊断摘要和本地日志链接，但完成一个用户任务仍有五类界面摩擦：

1. 顶层帮助按字母排序，`smoke`、`check`、`search`、`explain`、`apply` 和 `minimize` 的关系不清楚；
2. 可选 `package` 同时显示成位置参数和 `--package`，帮助没有说明省略行为，也没有完整说明 `--max-duration` 格式；
3. 缺少 `merge` 输入等可预期调用错误会越过 CLI 边界，泄露 Pydantic traceback；
4. `search completed (1 reports)`、`apply completed (1 changed)` 等摘要既有单复数问题，也不能区分“执行结束”和“报告完整可应用”；
5. `explain` 逐 cell、逐 Proposal 展开重复诊断并直接展示 declaration digest，用户难以先回答“floor 是什么、为什么不能 apply、下一步做什么”。

D005 已经禁止把 `UNRESOLVABLE`、`TOOL_ERROR`、cause Enum 或 Schema status 单独作为用户结论。P004 已落地 FailureRecord 与 `FailurePresentation`。D006 的 failure 展示必须消费这些结构化事实，不建立旧 status 到文案的临时映射。

## 2. 目标与非目标

### 2.1 目标

- 第一次运行 `pf --help` 就能看懂验证、搜索、应用和报告调查的主路径；
- 所有可预期的调用错误在 CLI 边界转为简短、可修正的用户反馈，不输出 traceback；
- 最终摘要准确表达命令结果、影响范围和产生或修改的 artifact；
- `explain` 默认回答 floor、coverage 和 apply blocker，不转储每次 attempt 的机械细节；
- 相同结构化结果在非 TTY 下产生稳定文本，在 TTY 下使用自适应 Rich 展示；
- 保留 D001/D005 的保守证据语义、stdout/stderr 边界、退出码和日志安全要求。

### 2.2 非目标

- 不增加、删除或重命名 D001 已定义的命令；
- 不改变 `smoke`、`check`、`search`、`apply`、`minimize`、`explain`、`diagnose` 或 `merge` 的业务语义；
- 不改变 D003 搜索顺序、D004 诊断身份或 D005 failure 分类；
- 不增加 `--verbose`、`--json`、交互式 pager、自动重试或自动修复；
- 不设计本地化框架；v1 CLI 主文案继续使用 D005 已确认的英文；
- 不把终端文案、颜色、宽度或日志路径写入报告或 Evaluation identity。

## 3. 展示原则

### 3.1 先回答用户问题

普通命令输出按以下依赖顺序组织：

1. **Outcome**：任务成功、兼容性失败、不可应用还是无法判断；
2. **Scope**：哪个 package、cell、声明或 artifact 受影响；
3. **Reason and impact**：结构化事实说明了什么，命令是否继续；
4. **Next action**：可执行的下一条命令或调查入口；
5. **Technical details**：稳定 Enum、ID 和进程事实，只在 `diagnose` 等明确调查界面展开。

信息必须按层级渐进披露。默认输出不能要求用户先理解 Proposal ID、declaration digest、cause Enum、Schema status 或 adapter stage。

### 3.2 结果必须语义真实

- 只有退出 `0` 的成功结果使用 `✓`；成功动词按对象选择：`passed`（smoke/check）、`updated`（apply/minimize 改写了元数据）、`complete`（仅 D001 完整可授权报告）；
- 退出非零的命令不得输出无修饰的 `completed`，也不得使用成功图标；
- `search` 写出了不完整报告，只能描述为 `incomplete` 或 `stopped`，不能描述为搜索成功；二者的选择由 §8.4 的 reason 表决定；
- 图标稳定映射为：`✓` 成功，`⚠` warning 或无可应用 floor，`✗` Rejection/兼容性失败，`!` Indeterminate/基础设施错误；
- 调用错误使用 §6.2 的 `Error:` 块，不加结果图标；
- 颜色只作补充；移除 ANSI 后，图标和文字仍必须完整表达结果；
- `complete` 专指 D001 定义的完整、可授权报告。cell 覆盖、apply 成功和 minimize 成功不得使用该词。

### 3.3 术语一致

- 用户文案使用 `Python 3.11`、精确 target triple 和 `no-extra`，不使用无标签的 `3.11` / `none`；
- `report` 指 `package-floor.json`，`project metadata` 指目标 `pyproject.toml`；
- 一行命令说明避免 `highest-resolution`、`projection evidence` 等实现术语；详细帮助可以解释对应语义；
- D005 的 cause/disposition Enum，以及 `STATIC_FAIL`、`BASELINE_REJECTION`、`BASELINE_INDETERMINATE`、`CELL_INDETERMINATE` 等 Schema status，只出现在 `diagnose` 的 `Technical details`。实时 cell 行、命令摘要和默认 `explain` 不得把它们当作结论。

## 4. 顶层帮助信息架构

`pf --help` 按用户工作流分组并保持以下顺序，不按字母排序。`explain` 放在 Find and apply 中，是因为 D1 的 `search -> inspect -> apply` 里它是 apply 前的报告检查；`diagnose` / `merge` 是事后调查与跨宿主组合，放在 Inspect and combine。

```text
Usage: pf COMMAND

Find verified lower bounds for direct Python dependencies.

Verify
  smoke      Verify a fresh install with the newest versions allowed by current declarations.
  check      Verify the lower bounds declared by the project.

Find and apply floors
  search     Find verified floors and write package-floor.json.
  explain    Show verified floors, coverage, and apply blockers in an existing report.
  apply      Update project metadata from a complete, current floor report.
  minimize   Search for floors, then apply only a complete result.

Inspect and combine reports
  diagnose   Explain a recorded rejection or indeterminate result.
  merge      Combine compatible reports produced on different hosts.
```

帮助底部追加一条最短主路径：

```text
Typical workflow: pf smoke -> pf search -> pf explain -> pf apply
Use pf minimize to search and apply in one command.
```

该主路径是导航，不改变各命令独立语义；`check` 仍是验证当前声明下界的独立 CI 用例。Cyclopts 内置的 `--help` / `--version` 继续显示在命令组之后。

## 5. 子命令帮助与参数表面

### 5.1 `package`

D001 的 `[package]` 是可选位置参数。所有接受它的命令都使用 positional-only 表面，不再同时生成 `--package`：

```text
PACKAGE  Package name, directory, or pyproject.toml path. Omit to select all
         installable packages allowed by the root configuration.
```

Usage 必须显示具体参数名，例如 `pf search [OPTIONS] [PACKAGE]`，不能退化为不透明的 `[ARGS]`。

### 5.2 公共选项

```text
--jobs auto|N
    Maximum concurrent cells. Use auto or a positive integer. [default: auto]

--max-duration DURATION
    Stop scheduling after DURATION and save an incomplete report.
    Accepts a positive integer followed by s, m, or h; use none for no limit.
    [default: none]
```

`--max-duration` 只停止新调度并保存不完整报告；帮助不能暗示它会杀死正在运行的进程。阶段 timeout 仍由 D001 配置拥有。未启动 cell 按 D005 记为 `TIMEOUT` / Indeterminate，摘要按 §8.4 使用 `stopped`。

### 5.3 `merge`

`merge` 在解析层要求一个或多个输入：

```text
Usage: pf merge REPORT [REPORT ...] --output PATH

REPORT      A package-floor.json report to merge. [required]
--output    Destination for the merged report. [required]
```

零输入不能进入 `MergeRequest` 或 workflow。

### 5.4 `diagnose`

```text
Usage: pf diagnose [OPTIONS] [PACKAGE]

--failure FAILURE_ID
    Inspect one recorded failure. Omit to list every recorded rejection
    or indeterminate result.
```

### 5.5 帮助来源

Cyclopts 的命令说明和参数说明继续从可解析 docstring 生成。类型 annotation 只承载类型、位置性和约束，不复制帮助字符串；生产代码不维护第二套 help 常量或手写 Rich help 页面。

## 6. 调用错误

### 6.1 分类边界

以下属于可预期的用户调用错误，按 D001 返回退出码 `3`：

- 未知命令或选项；
- 缺少、重复或多余参数；
- `merge` 没有输入；
- `--jobs`、`--max-duration` 等 CLI 值非法；
- package selector 不能解析；
- Request Schema 在 CLI 输入构造阶段拒绝值。

解析器应尽早拒绝结构性错误。Request Schema 保留 defense-in-depth；`cli.py` 只把命令输入构造产生的 ValidationError 转为稳定 `ConfigurationError`，不得全局捕获深模块的任意 ValidationError 并伪装成用户错误。

### 6.2 错误格式

调用错误输出到 stderr，并包含三个部分：

```text
Error: at least one REPORT is required.
Usage: pf merge REPORT [REPORT ...] --output PATH
Try 'pf merge --help' for more information.
```

规则：

- 不输出 Python exception 类型、stack frame、Pydantic 文档 URL 或内部字段路径；
- 错误必须复述可接受格式，例如 duration 使用 `30s`, `10m`, `2h`, or `none`；
- 未知 package 的候选名称由 `ProjectLoader` 放进 `ConfigurationError.candidates`；`TerminalPresenter` 按规范顺序列出最多 10 个，超过 10 个时追加 `... and N more`。Presenter 不扫描文件系统去发现包名；
- 非 TTY 不包含 ANSI、OSC 8 或 Rich live 控制序列；
- 未预期的编程错误不属于本节，不能通过宽泛捕获改写成 configuration error。

## 7. 输出通道与事件顺序

| 内容 | 通道 |
| --- | --- |
| 退出 `0` 的最终摘要 | stdout |
| 成功读取并展示的 `explain` / `diagnose` 全文，含 Summary/Next | stdout |
| 成功的 `merge` / `apply` artifact 结果 | stdout |
| 错误、warning、failure、incomplete/stopped 命令摘要 | stderr |
| TTY 动态进度 | stderr |
| 冻结的范围事实（selected cells）与 cell 完成块 | stderr |
| D004 单行诊断与 D005 failure 摘要 | stderr |

`explain` 在成功读取报告后始终退出 `0` 并把全文放在 stdout，即使报告 `incomplete`。报告缺失、非法或 package 无法解析仍是命令错误，走 stderr 与退出码 `3`。不要把 `explain` 的 Summary 拆到 stderr。

一个顶层命令只有一个最终摘要。TTY 的 live progress 始终固定在输出底部。每当一个 cell 完成，该 cell 立即从运行时进度中移除，并作为稳定诊断块写入上方 log；不要在命令结束时再输出第二份 cell 诊断。

阶段事件分两类：

- **范围事实**可以冻结：`loaded project`、`built snapshot`、`selected N cells` 及 python/platform/extra 明细。它们回答 Scope，完成后仍可读。
- **工作动词**只出现在 TTY 底部进度：`checking declarations` / `searching cells` / `smoke testing`。完成后不得冻结成 `checked declarations` 一类过去时阶段行。
- 非 TTY 没有 live 进度，因此不输出工作动词行；只输出范围事实、cell 完成块和最终摘要。

顺序固定为：

```text
scope facts -> per-cell frozen diagnostics (as cells complete) -> remaining live progress at bottom -> artifact details -> final summary
```

最终摘要必须是该命令最后一条用户可见结果。

`minimize` 的唯一最终 renderer 是 `TerminalPresenter.render_minimize(reports, edits)`。`edits` 为 `None` 表示 apply 未运行。handler 仍按 D002 顺序调用 search/apply workflow，但不得连续调用 `render_search` 和 `render_apply`。search 未得到完整报告时摘要明确说明 apply 未运行。

## 8. 最终摘要

### 8.1 组成

最终摘要按需包含：

```text
<icon> <command outcome> · <count/scope> · <artifact or next state>
```

计数必须使用统一的 singular/plural formatter；`1 cell`、`2 cells`、`1 report`、`2 reports`、`1 project`、`2 projects`，不得在各 renderer 内拼接 `cells` / `reports`。多 package 时先按 §8.3 列出 artifact，再输出一条命令级摘要；图标和动词跟随 D001 混合终态优先级下的主导结果。

### 8.2 示例

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

具体 failure title 和 impact 仍来自 D005。摘要不把一个 Proposal 的结果概括成某个 dependency version 的全局兼容性结论。`smoke` 测试通过但存在 ty 诊断时，最终摘要仍是退出 `0` 的 `Smoke passed`；cell 行用 `⚠` 展示诊断，不把 warning 升级成命令失败。

### 8.3 Artifact

- `search` 显示每个写入报告的项目相对路径；只有一个报告时可放在最终摘要，多报告时先逐行列出再给总数；
- `apply` 显示实际修改的 `pyproject.toml` 路径；没有修改时明确输出 `no metadata changes`；
- `merge` 显示 `--output` 路径；
- 路径支持 OSC 8 时可以成为链接，但可见文本始终是项目相对路径或用户传入路径；
- 只有实际成功写入的 artifact 才能用 `written` / `updated` / `merged`。

### 8.4 Search / minimize 的 reason 映射

报告 `result.reasons` 到摘要动词、图标和 D001 退出码的映射如下。多 reason 时先按 D001 优先级选择主导类：Baseline Rejection（1）> Indeterminate/基础设施（4）> 其他不可应用（2）。

| 主导 reason | 动词 | 图标 | 退出码 |
| --- | --- | --- | --- |
| `BASELINE_REJECTION` | `stopped` | `✗` | 1 |
| `INDETERMINATE` | `stopped` | `!` | 4 |
| `NO_PASS_IN_SEARCH_SPACE`、`NON_MONOTONIC`、`NONDETERMINISTIC`、`MISSING_CELL`、`UNREPRESENTABLE_PROJECTION` | `incomplete` | `⚠` | 2 |

`--max-duration` 导致未启动 cell 记 `TIMEOUT` / `INDETERMINATE` 时走 `stopped`。摘要句子使用人类语言（`compatibility is unknown`、`no applicable floor`、`highest-version baseline did not pass`），不回显 reason Enum。

## 9. Cell 与诊断格式

### 9.1 Cell

`smoke` / `check` / `search` 的 cell 结果行使用与 live 进度相同的标题（不含 package 名；范围已由 selected-cells 事实给出），并带上运行时间。第一行不含 Schema status 或 cause Enum。

有 FailureRecord 的 `smoke` / `search` 完成块：

```text
! [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:19
  PF could not complete a verification tool operation reliably.
  PF could not determine whether the highest-version baseline works, so it stopped this cell.
  Diagnose: pf diagnose demo --failure failure-38ac8f69eb9a182a
  details: .pf/logs/...
```

缩进行依次为 D005 title、D005 impact、`Diagnose:` 入口、可选 D004 单行诊断、日志链接。title/impact 完全来自 D005；`Diagnose:` 的 package 参数是报告中的包名，即使它碰巧与 CLI 名 `pf` 相同。

`check` 走 Evaluation 而不是 FailureRecord，不能提供 `diagnose` 入口：

```text
✗ [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:16
  src/pf/environment.py:405:29 [not-subscriptable] Cannot subscript object of type `Item` ...
  details: .pf/logs/...
```

成功 cell 只有图标、标题和耗时：

```text
✓ [py3.11][x86_64-unknown-linux-gnu][no-extra] 0:00:12
```

第一行是图标、`[py…]`、精确 target triple、extra surface 和运行时间。下方详情使用缩进和暗色样式。Python 前缀、精确 target triple 和 extra surface 都不能省略或用无标签 `none` 代替。

TTY 下该结果行是对应 live 进度行的固结形态，在 cell 完成时立即写入上方 log，并从底部进度中移除。非 TTY 在 cell 完成时直接输出同样的稳定文本。排序继续使用 D001/D002 的规范 cell 顺序来调度；完成顺序可以按实际结束时间出现在 log 中。

`explain` 仍可使用带 package 名的 cell 标题：

```text
demo [py3.11][x86_64-unknown-linux-gnu][no-extra]
```

D005 §12.4 的实时摘要示例遵循本节布局：title 与 impact 在缩进行，而不是写在第一行冒号之后。

### 9.2 静态诊断

实时 `smoke` / `check` / `search` 按实际重数逐条输出规范单行诊断，不在 Presenter 中改变 D004 的多重集语义。

规范单行格式由本文统一为：

```text
path[:line[:column]] [check_name] message
```

external 诊断没有可靠行列时只显示 path；消息内空白折叠为一个空格，不能换行或内联原始工具输出。缺失 `check_name` 属于 D004 定义的不合法 `TyCheck`，Presenter 不用空标签掩盖它。

`explain` 是报告概览，可以把展示字段完全相同的诊断行分组为：

```text
tests/test_cli.py:128:9 [unknown-argument] Argument ...  ×9
```

分组 key 包含所有可见字段和规范化消息，不能只按 path、code 或 D004 identity 合并不同文本。`×N` 保留实际重数；跨 cell 分组时同时说明涉及的 cell 数。每个 blocker group 默认最多展示 10 条唯一诊断，随后输出省略数量和 `pf diagnose PACKAGE`；该组只有一个 FailureRecord 时才追加更具体的 `--failure FAILURE_ID`。

## 10. `explain` 展示

### 10.1 职责

`explain` 回答：

1. 读取了哪个 package/report；
2. 报告是否 complete，以及**该报告是否授权 apply**（不是当前工作树现在执行 `apply` 是否会成功）；
3. target cell 覆盖是否完整；
4. 每条直接依赖声明的已验证 floor 和投影结果是什么；
5. 哪些 blocker 阻止 apply，以及如何进入 `diagnose`。

它不按 attempt 顺序转储全部 observation、Proposal、进程输出或技术 Enum。单次 attempt 的完整调查继续属于 D005 `diagnose`。源码漂移和策略 identity 不匹配由 `apply` 在退出码 `3` 时报告；`explain` 不重新核对快照。

### 10.2 默认层级

无 floor、报告不完整：

```text
demo · package-floor.json
Status: incomplete
Apply: not authorized by this report
Cells: 0/3 covered

Requirements
  cyclopts>=4.0   no applicable floor
  pydantic>=2.0   no applicable floor

Blockers
  3 cells · Python 3.10, 3.11, 3.12 · x86_64-unknown-linux-gnu · no-extra
  What happened: <D005 failure title>
  Impact: <D005 impact for the grouped scopes>
  Diagnose: pf diagnose demo

Summary: report is incomplete and cannot be applied.
```

缺覆盖（`MISSING_CELL`）时 `Cells` 显示已覆盖数，Blockers 说明哪些 target cell 没有本次宿主证据，Diagnose 只在存在 FailureRecord 时出现。

投影不可表示（`UNREPRESENTABLE_PROJECTION`）时对应声明显示 `projection blocked`，Apply 为 `not authorized by this report`。

完整报告示例：

```text
demo · package-floor.json
Status: complete
Apply: authorized by this report
Cells: 3/3 covered

Requirements
  httpx>=0.20   -> httpx>=0.24
  rich>=12      -> rich>=13.7; python_version >= '3.11'

Summary: 2 dependency declarations have verified floors.
Next: pf apply demo
```

示例中的版本只说明布局，不定义 floor 结果。多 package 时每个报告重复该块，最后一条 `Summary`/`Next` 仍属于 `explain` 全文，留在 stdout。

### 10.3 声明与投影

Presenter 必须用 `declaration_id` 关联 `requirement_declarations`，默认展示 `raw` / `name` 和 `projected_requirements`：

- 不能把 declaration digest 当作依赖名称；
- `representable = false` 显示 `projection blocked` 或更具体的结构化 blocker，不能显示含义不明的 `none`；
- 多条 marker requirement 在同一声明下缩进展示；
- digest、policy identity、Proposal ID 和 snapshot hash 只在 `diagnose` 技术详情或报告 JSON 中保留；
- 关联缺失是 Schema/内部一致性错误，不得静默退回裸 digest。

### 10.4 Blocker

Blocker 可以按相同 D005 `FailurePresentation` 聚合多个 cell，但必须保留涉及的规范 cell 列表和 FailureRecord 数量。标题、影响和 next step 完全来自 D005；D006 只规定段落位置、cell 聚合、诊断折叠和 `Diagnose:` 入口。单个 FailureRecord 使用带 `--failure` 的精确入口；多个 FailureRecord 使用 `pf diagnose PACKAGE` 列表入口，不能任选一个 ID 冒充整组。

P004 已完成。`explain` 的 blocker 层必须消费 `FailurePresentation`；没有结构化 failure 事实就不能声称 explain 完成。禁止旧 status 兼容分支，也禁止从 stderr 自由文本生成临时解释。帮助、参数校验和通用摘要可以先行，但不得用它们冒充 blocker 文案。

## 11. 自适应终端

- 生产 `Console`、`Table` 和 `Progress` 不固定 width、height、列宽或 ratio；固定尺寸只允许出现在测试中；
- TTY 使用动态进度，进度条固定在输出底部；cell 完成后立即冻结为稳定结果行并移出 live 表；非 TTY 不显示逐进程活动；
- 窄终端优先换行或把表格降级为带标签的纵向 block，不能截断 package、cell、状态、artifact 路径或 next action；
- 不依赖光标定位表达唯一信息；live display 关闭后，最终冻结行必须能独立阅读；
- 不支持颜色或 OSC 8 时保留相同可见文本；
- 帮助、摘要和 `explain` 在 56、80、120 列下都必须可读，不要求视觉像素级相同。

## 12. 模块所有权

| 规则 | 唯一所有者 |
| --- | --- |
| 命令存在、参数语义、退出码 | D001 |
| Cyclopts 注册、位置性、参数 cardinality、docstring help | `cli.py` |
| workflow 业务编排和 artifact 写入 | `workflow.py` 与对应深模块 |
| 未知 package 的候选名称 | `ProjectLoader` via `ConfigurationError.candidates` |
| failure title、impact、next step、technical code | D005；`TerminalPresenter` 只渲染 |
| help 分组、结果摘要、cell/diagnostic/explain 布局 | D006；`TerminalPresenter` 只渲染 |
| `render_minimize` | `TerminalPresenter` |
| TTY event、Rich renderable、stdout/stderr | `TerminalPresenter` |
| 诊断事实与多重集 | D004 |
| 报告证据与 canonical JSON | `ReportStore` / Schema |

实现约束：

- `cli.py` 仍是唯一 composition root，不读取报告内容来决定文案；
- `TerminalPresenter` 可以建立内部 presentation object，但该对象不进入公共 Schema；
- adapter、Evaluator、workflow、report 和 editor 不拼用户文案、不导入 Rich；
- 不创建第二个 CLI renderer、通用 `formatting.py` 或只被调用一次的 helper module；
- artifact 路径来自 workflow/result 的结构化事实，Presenter 不扫描文件系统猜测输出；
- 未知 package 的候选列表不得由 Presenter 重新发现。

## 13. 验证契约

### 13.1 Help 与调用错误

- `pf` 和 `python -m pf` 的顶层 help 分组、顺序和文案一致；
- 八个命令的 `--help` 覆盖位置参数、默认值、`--failure` 和 duration 格式；
- `package` 只显示为可选位置参数；
- `merge --output PATH` 在 workflow 前失败，退出 `3`，无 traceback；
- 未知 option、非法 jobs/duration、未知 package 都验证 stderr、usage、退出码和无 ANSI 的非 TTY 输出；
- 未知 package 验证候选列表上限 10 和 `... and N more`。

### 13.2 结果与通道

- success、compatibility failure、no-applicable-floor、indeterminate 分别验证图标、措辞和 D001 退出码；
- 单复数覆盖 `0/1/N`；
- incomplete/stopped search 不输出无修饰的 `search completed`，并按 §8.4 断言动词和图标；
- `minimize` 成功和 search-blocked 路径都只调用 `render_minimize`，只有一个最终摘要；
- stdout/stderr 分别断言，不依赖合并流的调度顺序。

### 13.3 Cell 与 failure

- 冻结 cell 第一行不含 Schema status 或 cause Enum；
- `smoke` / `search` 的 FailureRecord 路径包含 D005 title、impact 和 `Diagnose:`；
- baseline Indeterminate 的 impact 不得把 baseline 说成 candidate；
- `check` 兼容性失败没有 `Diagnose:` 行。

### 13.4 `explain`

- complete、incomplete、missing-cell、projection-blocked 和多 package 报告；
- `Apply: authorized by this report` / `not authorized by this report`，不出现 `Apply: ready`；
- 声明名称/原始 requirement 替代 digest；
- 重复诊断以 `×N` 保留重数，并验证 10 条上限和省略计数；
- failure title 与 diagnose 入口复用 D005，不出现裸 cause/status；
- 默认输出不含 Proposal ID、snapshot hash、policy identity 或多行工具输出；
- 全文在 stdout。

### 13.5 终端模式

- 非 TTY 是无 ANSI/OSC 8 的稳定文本，且没有工作动词阶段行；
- TTY 在 56、80、120 列验证不溢出、不丢字段和完成行冻结；
- 生产 Presenter 构造不传固定终端尺寸；
- wheel 安装后按 `smoke -> check -> search -> explain -> diagnose -> apply` 做入口 smoke，并验证 `pf` 与 `python -m pf` 一致。

## 14. 实施顺序

1. **Cell and failure placement**：去掉 cell 行上的 Schema status，消费 D005 title/impact/`Diagnose:`，修正 baseline Indeterminate impact；
2. **Help and invocation**：命令分组、位置参数、duration 文案、`merge` 非空、未知 package 候选和统一 usage error；
3. **Command summary**：按 §8.4 统一 count/artifact/outcome、修正 stdout/stderr、落地 `render_minimize`；
4. **Explain hierarchy**：声明关联、coverage/projection、blocker 分组和诊断折叠；必须消费 `FailurePresentation`；
5. **Installed validation**：窄/宽 TTY、非 TTY、两个入口和真实 artifact 路径。

每个阶段以公开 CLI 行为测试开始。第 4 阶段不得保留旧 status 兼容分支或从 stderr 猜测文案。

## 15. 不变量

1. 可预期的用户调用错误不输出 traceback。
2. 退出非零的命令不使用成功图标或无修饰的 `completed`。
3. 一个顶层命令只有一个最终摘要，且它是最后一条结果信息。
4. 默认 `explain` 不显示裸 declaration/proposal/snapshot/policy digest。
5. `explain` 只汇总 attempt failure；完整机械事实由 `diagnose` 展开。
6. D005 拥有 failure 文案含义，D006 不建立平行分类或 remediation 表。
7. D004 诊断重数不能因展示折叠而丢失；折叠必须显示 `×N`。
8. cell 标题始终包含 Python、精确 target triple 和 extra surface。
9. artifact 只有在实际写入或修改后才能描述为 written/updated/merged。
10. 生产终端布局不硬编码宽高，非 TTY 不输出控制序列。
11. 展示选择不进入报告、Evaluation、policy 或 source identity。
12. 默认 cell 行、命令摘要和 `explain` 不把 Schema status 或 cause Enum 当作结论。
13. `complete` 只描述 D001 完整可授权报告。

## 16. 决策记录

### D1：命令按工作流分组，不按字母排序（已确认）

CLI 是任务导航，不是符号索引。分组和顺序优先表达 `verify -> search -> inspect -> apply` 的关系。`explain` 放在 Find and apply 中，因为它是 apply 前的 inspect；`diagnose` 是事后调查。

### D2：`package` 只保留位置参数（已确认）

D001 已把表面定义为 `[package]`。移除自动生成的 `--package` 可以消除 `PACKAGE --package` 与 `[ARGS]` 歧义，不新增命令语义。

### D3：不完整 search 不是成功摘要（已确认）

写出合法不完整报告与得到完整可应用 floor 是两个事实。CLI 同时展示 artifact 和 incomplete/stopped outcome，不用 `completed` 混淆二者。动词由图标表 §8.4 决定。

### D4：`explain` 展示声明，不展示 digest（已确认）

digest 是报告关联键，不是用户术语。Presenter 必须通过结构化关联展示原始 requirement；关联失败应作为一致性错误暴露。

### D5：`minimize` 只有一个最终摘要（已确认）

`minimize` 是一个用户命令。内部 search/apply 阶段可以产生进度，但不能各自冒充顶层最终结果。唯一入口是 `render_minimize`。

### D6：不实现临时 failure 文案（已确认）

旧 status 缺少 D005 scope/disposition/cause，无法可靠决定影响或 next step。P004 已提供结构化事实；展示层直接消费，不保留平行映射。

### D7：冻结 cell 行不显示 Schema status（已确认）

图标已经编码成功/失败/警告/不确定。Schema status 是机器词汇，放在第一行会盖过 D005 title。title、impact 和 `Diagnose:` 放在缩进行。

### D8：`explain` 的 Apply 只表示报告授权（已确认）

`explain` 不核对当前工作树。`authorized by this report` 避免把“报告完整”说成“现在执行 apply 一定成功”。

# D006 视觉与固定文案附录

- **状态：** 现行
- **最后核对：** 2026-09-08
- **主文：** [D006 CLI 交互与展示](../D006-pf-cli-enhancement.md)

本附录保存从 D006 主文移来的视觉细则与固定文案。通道、状态、事实来源和信息层级以主文为准；
产品选项语义与退出码由 D001 拥有。示例中的 package/version/ID 是展示数据，不作为兼容性证据。

## A.1 Help 文案

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

--search-resolution major|minor|patch
    Series representative granularity. Omit to use project configuration;
    accepts major, minor, or patch.

--force
    Accept source-layer drift after structural authorization.
```

选项适用命令、默认/override、duration、force 与 Failure ID 准入只见 [D001 §5、§7](../D001-pf.md#5-命令)。本节只固定 help 文案；`diagnose` 的 Usage 为 `pf diagnose FAILURE_ID [OPTIONS]`，`merge` 为 `REPORT [REPORT ...] --output PATH`。

## A.2 Scope 样式

scope facts 首部卡片的运行中边框使用默认前景色 dim；完成固结时保持 dim，并切换为
命令最终 outcome 的 green/red/yellow。边框样式不得传给 `loaded project`、
`built snapshot`、matrix facts 等正文。

run ID 整行 dim；`YYYYMMDD` 与 `HHMMSS` 分别使用 dim bold green，点号后
三个连字符分段分别使用 dim bold magenta。Python minor 版本使用 dim bold 默认前景色。
setup、run ID、live/footer、完成 Cell、错误与 final summary 都使用 native Rich marker/content
表格；marker 固定一列，marker 与正文之间统一为 2 个空格宽的 gutter，不以字符串空格实现对齐。

## A.3 Live 样式与示例

TTY 的每个运行中 Cell 使用独立卡片：

```text
⠋  [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:12
   [baseline][highest][testing]  ━━━━━━╺━━━━━━━━━━━━━ 37/120 ETA 00:00:41

⠋  [py3.10][x86_64-unknown-linux-gnu][no-extra] 0:00:12
   [cyclopts=2.4.0][packaging=24.0][rich=13.0]
   [pydantic=1.7.4][1.7.4~2.13.4#18][testing]  ━━━━━━╺━━━━ 37/120 ETA 00:00:41

⠋  searching cells · 3 running · 4 finished · 0 left                 0:00:12
```

未搜索的
token 内容使用 dim 默认前景色且 package name 不加粗；已完成 token 内容使用 green 且
去掉 dim，其 package name 为 bold，`=version` 保持普通字重；bracket 始终为 dim 默认前景色。

Cell title `[py...][target][extra]` 的 token 内容使用 bold 默认前景色，bracket 使用 dim
默认前景色。`CellContextEvent` 提供当前 detail identity；baseline、declaration 与
search probe 都放在 title 后的 identity detail。Live identity 的 bracket 使用 dim 默认
前景色；第一、第二 token 内容分别为 bold cyan 与 cyan。任意当前 stage 都作为第三个
token 与 identity 保持在同一逻辑行，其内容使用默认前景色且不 dim；dynamic tests
精简为 `[testing]`，其中 `testing` 使用 cyan，并与 progress bar/count/ETA 保持同行。
count 与 ETA 使用 dim 默认前景色，count 只显示 `completed/total`，不追加
`tests`。没有 identity 时只显示第三个 stage token。候选窗口使用 `~`。

外层 Console 最宽 120 列；内部 renderable 不设置固定 width/height。窄终端优先隐藏 bar、换行或改为 label block，不能丢失 package、Cell、artifact 或 next action。非 TTY 无 box drawing。

TTY Cell 卡片边框统一使用 dim，并保留原有颜色：live 卡片仍为默认前景色；完成卡片 success 为 green，failure 为 red，warning/indeterminate 为 yellow。dim 只作用于边框，不传递到卡片正文。

## A.4 Completion 布局与示例

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
   The configured verifier rejected this version combination.
   FAILED tests/test_cli.py::test_example
   ... and 2 more
   -> run `pf diagnose failure-38ac8f69eb9a182a --package demo` for more information.
```

TTY 与非 TTY completion 都使用固定 icon/content 两列；title、每条 detail 及其所有物理换行都从同一 content 列开始，不以字符串空格猜测 Rich 的换行位置。icon 与 content 间固定为 2 个空格宽的 gutter。completion action 统一为 smoke `passed/failed at`、check `passed/failed at`、search `completed/stopped at`。失败阶段作为 identity 后的第三个 bracket token；没有 identity 时仍使用单独的 bracket token。result detail 整行使用对应结果色并 bold；identity/stage 的 bracket 例外，始终使用 dim 默认前景色且不 bold。

## A.5 Result card 布局

`explain`、`apply`、`minimize`、`diagnose`、`merge`及其typed command errors复用同一结果卡primitive。TTY卡片使用rounded border、固定marker/content两列和2空格gutter；标题marker、border与结果色一致。Field label固定宽度，长value从value列继续换行，不能回到终端左边缘。路径按literal user data渲染；TTY可使用underline cyan与OSC 8 file link，non-TTY不得包含ANSI/OSC。不得为获得路径链接调用`resolve()`而改变用户给出的相对路径。

56、80、120列是必测宽度。窄宽度可以增加物理换行，但不能丢失package、Cell、artifact、ordered inputs、reason、next action或technical facts；TTY/non-TTY拥有相同信息层级和语义。final summary位于卡片之后，是最后一条结果信息，icon与整句同色且bold。成功结果中的red/yellow事实仍随成功card走stdout，不拆到stderr。

格式按需组合：

```text
<icon>  <command outcome> · <count/scope> · <artifact or next state>
```

所有 renderer 复用统一 `0/1/N` 单复数 formatter。只有实际写入/修改的 artifact 能使用 `written | updated | merged`。Package-scoped命令只有一个target artifact与一条命令级summary；`merge`仍可消费多份report。
Final summary 的 icon 与整句文字使用同一个结果色且 bold。

## A.6 Final summary 示例

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

## A.7 Explain requirements 样式

Requirements 的 declaration 与单条 projection/detail 使用按当前内容计算的对齐列，
不得固定终端宽度。原始 declaration 的 dependency specifier 使用 cyan，其中 version
operand 使用 bold cyan；搜索得到的 projected requirement 使用 green，其中 version
operand 使用 bold green。package name、extras 与 marker 保持默认前景色；blocked / no
applicable floor 保持 warning 色。多 marker projection 继续在 declaration 下逐行缩进。

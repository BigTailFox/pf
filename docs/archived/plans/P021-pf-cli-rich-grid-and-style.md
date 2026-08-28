# P021 — verification CLI Rich grid 与语义样式统一

- **状态：** 已完成
- **日期：** 2026-08-28
- **展示契约：** [D006](../designs/D006-pf-cli-enhancement.md)
- **前序实施：** [P020](../archived/plans/P020-pf-cli-live-density.md)

## 1. 目标

1. setup、live Cell、完成 Cell 与底部 live 状态都通过 native Rich table/grid 管理
   marker 与正文列；spinner/emoji 到正文统一使用 2 个空格宽的 gutter，不在文本中手写缩进。
2. 完成 Cell 的 result detail 行整体 bold；identity/stage 的 bracket 保持 dim 默认前景色。
3. Cell 与 footer elapsed 使用 dim cyan；dynamic progress count/ETA 使用 dim 默认前景色；
   live `[testing]` 内容使用 cyan。
4. run ID 移到 selected matrix heading 下方；整行 dim，日期和时间块为 dim bold green，
   后续三段分别为 dim bold magenta。
5. setup 的 Python minor 版本与 footer 的 running/finished/left 数字使用 bold。

上述调整只属于 terminal presentation，不进入 Activity、report、cache、Journal、
FailureRecord 或 identity。

## 2. Interface 与所有权

- `pf.terminal._presentation` 提供私有共享 marker-row grid 与语义 Text formatter；
  `TerminalPresenter` 和 `LiveVerificationView` 复用，不向 workflow 暴露 Rich。
- `LiveVerificationView` 仍只消费 Activity，`_OrderedProgress` 继续让 Rich 测量宽度；
  marker、正文与进度数据不通过空格拼接建立对齐。
- 测试只通过 `TerminalPresenter` 的 TTY/非 TTY 可见输出和 ANSI style 验证契约。

## 3. 纵向 TDD

1. 完成 Cell 与 summary marker 两列布局：先锁定 2-space gutter，再提取共享 grid。
2. setup/live/footer 布局：先证明现有手工空格，再改为固定 marker 列和 Rich padding。
3. result/run-id/python/footer/dynamic/elapsed 样式逐项 RED→GREEN。
4. focused/full suite、Ruff、ty、真实 smoke/check/search 与双轴 review。

## 4. 实施记录

### 4.1 Native Rich marker grid

- RED：完成 Cell、setup、live Cell、footer、错误与 summary 的 marker gutter 测试先证明
  旧输出仍混用单空格、手工空白 marker 与 `Padding`。
- GREEN：`pf.terminal._presentation.marker_group` 统一构造固定一列 marker 与两列 gutter；
  `TerminalPresenter`、setup card 和 `_OrderedProgress` 均只传 marker/content renderable，
  不再把对齐空格写入文本。
- 窄终端由 Rich 自然续行；测试验证 Cell identity、package、footer count、elapsed 与 next
  action 不丢失，不锁定偶然物理换行。

### 4.2 Result 与 live semantic style

- RED：ANSI 测试先覆盖 result 整行 bold、bracket dim default、Cell/footer elapsed cyan、
  `[testing]` cyan、dynamic count/ETA dim default，以及 footer 三个数字 bold。
- GREEN：完成 result 使用 outcome-specific bold style，identity/stage bracket 显式取消 bold
  并回到 dim default；live 的 stage、progress telemetry 与计时分别由语义 formatter/column
  持有样式。

### 4.3 setup facts

- RED：setup card 测试锁定 `selected -> run-id -> python` 顺序及 run ID/Python 分段 ANSI。
- GREEN：run ID 的日期、时间使用 dim bold green，后三段使用 dim bold magenta；Python
  minor 使用 dim bold 默认前景色。非标准 run ID 安全回退为整段 dim。

### 4.4 验证证据

- `tests/test_terminal.py`：108 passed。
- `ruff check src tests`：passed。
- `ty check src`：passed。
- 完整测试：`1321 passed`。受限网络下唯一安装型 E2E 首次因 package source tunnel
  被拒而 indeterminate；读取 report 并以 `pf diagnose` 确认 `SOURCE_FAILURE` 后，在允许
  依赖访问的同一测试与完整 suite 中通过。
- 真实 CLI fixture：`pf smoke`、`pf check`、`pf search` 均返回 0；search 前先以
  `pf explain` 确认既有 report complete，再覆盖一次性 fixture 的 report。

### 4.5 双轴 review

- Standards：无硬违规或判断项；Terminal/Rich 所有权、native marker/content 布局、
  TTY/non-TTY 信息层级、样式与窄宽度契约均符合 D002/D006。
- Spec：初审唯一低优先级 finding 是实施证据尚未写回 P021；补齐 4.1–4.4 后复审确认
  已解决。功能要求无缺失、错误实现或明显 scope creep。

# P016 — smoke/check/search 统一 Cell 展示

- **状态：** 已完成
- **日期：** 2026-08-27
- **展示契约：** [D006](../designs/D006-pf-cli-enhancement.md)
- **前序实施：** [P014](P014-pf-cell-diagnostics.md)、[P015](P015-pf-pytest-progress-isolation.md)

## 1. 目标

深化现有 `CellPresentation -> LiveVerificationView -> TerminalPresenter` seam，令
`pf smoke`、`pf check`、`pf search` 复用同一个 identity detail formatter、完成态
首行和 live footer，不向 workflow、adapter、report 或持久化 schema 增加展示字段。

本轮稳定行为是：

1. live Cell 的第一条 detail 总是当前 identity。baseline/declaration 使用
   `[baseline][highest]` / `[declaration][lowest-direct]`；search probe 使用紧凑
   `[pydantic=2.0.1][1.0..3.0#14]`。第一组使用默认亮度，第二组 dim；版本和
   candidate 数字统一 cyan，第二组数字同时 dim。
2. 完成 Cell 的标题只保留 icon、Cell 与耗时，identity 移到第一条 detail。
   smoke/check/search 的非成功前缀分别为 `smoke failed at`、`check failed at`、
   `search stopped at`；其后保留用户阶段。完成态在 live 样式基础上只把默认
   前景替换为结果色，cyan 数字不变。成功态对应 `smoke passed at`、
   `check passed at`、`search completed at`。
3. 命令级最后一行的 icon 与整句文字统一使用结果色并 bold。
4. matrix 只建立总数，不为未启动 Cell 创建 live panel。运行面板数因此由真实
   scheduler 并发自然约束为不超过 `jobs`。footer 只显示 spinner、phase、
   `N running`、`M left` 和右对齐总耗时，其中
   `left = total - completed - running`；不再显示 Cell 方块矩阵或 `completed/total`。
5. direct serial pytest 的确定进度行增加 `ETA H:MM:SS`。估计值使用当前 dynamic
   stage elapsed 的平均吞吐计算；尚无完成测试时显示 `ETA --:--:--`。telemetry
   缺失/损坏仍冻结最后一个合法进度与 ETA 输入，不改变验证 outcome。
6. search 完成卡片只投影该 Cell 终止搜索时收到的最新 `SearchFailureEvent`。若该
   terminal failure 没有结构化 detail，就省略 detail；不得回退到更早 probe 的
   pytest/static detail。历史 failure 仍保留在 report、Journal 与离线 diagnose。
7. search Cell Reason 区分终止语义：`INDETERMINATE` 明示搜索空间尚未评估完成并
   保留终止 Failure 的 D005 title；`NO_PASS_IN_SEARCH_SPACE` 明示搜索空间已完整
   评估但没有兼容组合，不把最后一个候选 rejection 误写成提前终止原因。

## 2. 非目标与所有权

- 不改变 `ActivityEvent`、`StageProgress`、scheduler、验证结论、FailureRecord、
  report、cache 或 policy identity；`jobs` 不进入 Presenter interface。
- 不让 workflow 拼终端文案，不让 Presenter 解析 pytest 文件或进程输出。
- 不固定生产 Rich 的 panel、列或 bar 宽度；弹性空白负责把总耗时推到右侧。
- 不恢复 baseline ty warning、process tail、跨 Cell 诊断聚合或普通日志链接。

## 3. 纵向 TDD 顺序

1. **Identity tracer bullet：** 先用公开 TTY `consume` 断言 baseline detail 的行位
   与 default/dim 样式，再实现统一 formatter；随后扩展 search 紧凑格式和 cyan
   数字。
2. **完成态：** 分别用 smoke/check/search 的公开完成路径断言命令前缀、identity
   第一 detail 行、结果色与保留阶段；最后覆盖成功态。
3. **Footer：** 以 4 Cell 的公开 event 序列证明未启动 Cell 不出现、同时最多显示
   2 个 panel，并断言 `2 running · 2 left` 与右对齐耗时。
4. **ETA：** 先断言 direct determinate progress 出现 ETA，再覆盖 completed=0 与
   同 stage `progress=None` 冻结；保留 spinner 定频回归。
5. **Summary：** 对 success/failure/warning/indeterminate 的 ANSI 输出断言整行结果
   色和 bold。
6. **Search terminal failure：** 构造早期 pytest detail 与更晚 harness failure，
   先证明旧实现错误展示早期 nodeid，再令完成卡片选择最新 failure/title/diagnose。
7. **Search conclusion：** 用同一 probe identity 分别完成 exhaustive rejection 与
   timeout indeterminate，断言 Reason 一边是完整评估无解、一边是提前终止且未知。

## 4. 实施记录

- **Slice 1 / baseline identity：** RED：公开 TTY event 测试观察到
  `[baseline][highest]` 仍与 Cell title 同行。GREEN：`cell_identity_text` 成为 live/
  final 共用的 typed formatter，baseline/declaration identity 迁到第一条 context
  detail，第一组默认亮度、第二组 dim；单项回归 `1 passed`。
- **Slice 2 / search identity：** RED：live 仍输出
  `[pydantic==1.5][1.0…2.0 · 7 candidates]` 且没有 cyan 数字。GREEN：同一 formatter
  输出 `[pydantic=1.5][1.0..2.0#7]`，第一组版本 cyan、第二组版本/count cyan+dim；
  公开 ANSI 单项回归 `1 passed`。
- **Slice 3 / live 并发与 footer：** RED：4 Cell matrix 在只有 2 个 Cell 真正启动时仍
  渲染 4 张 panel，footer 为方块矩阵加 `0/4`。GREEN：matrix 只登记 total，首次
  context/stage 才创建 panel；footer 从 live task 数计算 `running`，从
  `total-completed-running` 计算 `left`，弹性列把总耗时推到右侧。完成补位与 56 列
  回归共 `3 passed`；删除方块 outcome 状态及其浅展示测试。
- **Slice 4 / dynamic ETA：** RED：`3/8 tests` 的确定进度没有剩余时间。GREEN：
  cell-stage task 在 stage 切换时重置独立时钟，ETA 由
  `ceil(stage_elapsed * remaining / completed)` 计算；0 completed 显示
  `ETA --:--:--`，同 stage telemetry 缺失继续使用最后合法进度。确定进度、窄屏、
  0 completed 与冻结路径回归 `4 passed`。
- **Slice 5 / final summary：** RED：indeterminate summary 只有 `!` 是 bold yellow，
  `Search stopped ...` 在 ANSI reset 后回到默认色。GREEN：四种结果分别使用独立
  `summary.<kind>` theme style，icon 与整句在同一 bold 结果色 span；indeterminate
  公开 ANSI 回归 `1 passed`。
- **完成态补全：** 非 TTY smoke tracer 的 RED 证明旧 header 仍携带 `failed at
  testing` 且丢失 live identity。完成事件现在在 TTY/非 TTY 都保留最后 context，
  header 收窄为 icon/Cell/elapsed，第一 detail 统一由 command action + typed identity
  + user stage 组成。smoke/check/search 精确前缀、成功 green、第二 token dim 结果色
  回归 `4 passed`；cyan 数字保持独立样式。
- **Slice 6 / search terminal failure：** RED：事件序列为早期 pytest failure、
  中间结构化 pytest detail、最终 harness failure 时，卡片仍选择中间 detail。
  GREEN：每个 Cell 保留真实事件顺序，projection 固定选择最后事件；末事件没有
  detail 时不向历史回退。卡片 title 与 diagnose ID 均指向 terminal failure，
  单项公开回归 `1 passed`。
- **Slice 7 / search conclusion：** RED：`NO_PASS_IN_SEARCH_SPACE` 仍显示最后候选的
  `TEST_FAILURE` title，indeterminate 也没有说明搜索空间未完成。GREEN：
  `CellSearchFailure.reason` 保留到 runtime completion status；Presenter 对 exhaustive
  / non-monotonic / nondeterministic 结论使用命令级 Reason，indeterminate 则在 D005
  title 前明确 early-stop。两类公开输出对照回归 `1 passed`。

## 5. 验证结论

- Terminal 与相邻命令/workflow 回归：`196 passed`。
- 完整测试套件：沙箱内先得到 `1266 passed, 1 failed`，唯一失败是安装态 E2E
  获取 `uv_build` 时被网络策略拒绝；在允许依赖访问的同一完整命令下为
  `1267 passed in 21.13s`。
- 静态检查：任务范围 `ruff check`、`ty check` 均通过，`git diff --check` 通过。
- 真实 CLI：临时最小项目的 `pf smoke --jobs 2`、`pf check --jobs 2`、
  `pf search --jobs 2` 均成功；TTY smoke 也验证了完成卡片的 baseline 第一 detail。
- 根目录既有未跟踪 `package-floor.json` 未读取、未修改且不进入本次提交。

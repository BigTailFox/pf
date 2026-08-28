# P018 — search Cell 候选与已完成包展示

- **状态：** 已归档（已完成）
- **日期：** 2026-08-27
- **展示契约：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **前序实施：** [P016](P016-pf-cli-live-presentation.md)

## 1. 目标

优化 `pf search` 的 Cell live 与终态卡片：

1. search probe identity 使用 `~` 表示候选窗口，例如
   `[pydantic=1.7.4][1.7.4~2.13.4#18]`；dependency 保持普通字重，版本与候选数
   使用 bold，并继承 live cyan 或终态 outcome 色；第二个 bracket segment 在继承色
   基础上使用 dim。
2. search Cell 在 probe identity 上方增加绿色已完成包行，例如
   `[baseline][packaging=24.0][cyclopts=2.4.0]`。`baseline` 固定表示已通过的最高版本
   锚点，其余 token 按当前 coordinate sweep 的实际完成顺序展示；每个完成版本值
   使用 bold green，包名与分隔符保持普通 green。
3. 已完成包是 invocation-local Activity 事实，不进入 report、cache、Journal、
   FailureRecord 或 identity。

## 2. Interface 与所有权

- `CoordinateSearch` 在每轮 sweep 开始和每个 coordinate 找到边界后，通过一个可选
  progress callback 发布当前轮已经完成的 `VersionPin`；它拥有“何时完成”的事实。
- `SearchCoordinator` 把该事实适配为带 Cell 的 `CellSearchProgressEvent`。
- `LiveVerificationView` 只保留每个 Cell 的最新运行期投影，并在 live / completion
  之间传递；`TerminalPresenter` 只负责绿色、位置与 Rich 自适应折行。
- 不从当前 probe dependency、声明顺序或版本向量猜测已完成包。

## 3. 纵向 TDD 顺序

1. **候选 identity：** 公开 TTY live 测试先证明旧输出仍为 `..` 且数字不加粗；最小
   修改共享 formatter，验证 `~`、数字 bold、dependency 普通字重，并覆盖终态色继承。
2. **终态已完成包：** 公开非 TTY completion 事件序列先断言卡片缺少绿色完成行；
   增加结构化 Activity event 与 presentation 投影，使该行位于 completion identity
   上方。
3. **真实 live/search 路径：** 公开 TTY event 测试断言完成行位于 probe identity 与
   stage 上方；SearchCoordinator 测试断言 baseline、逐 coordinate 完成和新 sweep
   reset 均由真实搜索时机发布。
4. **样式 follow-up：** 公开 ANSI 测试先证明 probe 第二段没有 dim、完成版本没有
   bold；再只调整共享 formatter，覆盖 live 与冻结卡片，不扩大 Activity interface。

## 4. 实施记录

- **Slice 1 / probe identity：** RED：公开 TTY live 测试仍观察到
  `[pydantic=1.7.4][1.7.4..2.13.4#18]`，且数值与 dependency 同字重。GREEN：共享
  `cell_identity_text` 改用 `~`，只给 active/lower/upper version 与 candidate count
  增加 bold span；live 继承 cyan，终态继承 outcome 色，dependency、分隔符与阶段不
  加粗。
- **Slice 2 / 终态完成行：** RED：非 TTY search completion event 序列只输出 header
  与 probe identity。GREEN：新增 invocation-local `CellSearchProgressEvent` 与
  `CellPresentation.completed_packages`，完成行固定插在 completion identity 之前；
  TTY / 非 TTY 共用绿色 formatter。
- **Slice 3 / 真实搜索时机：** RED：真实 `SearchCoordinator` 成功搜索没有任何完成包
  Activity。GREEN：baseline 一通过，`SearchCoordinator` 就发布空 tuple；
  `CoordinateSearch.minimize(..., progress=)` 在每轮 sweep 开始发布空 tuple，并在每个
  `_find_floor` 返回后按实际完成顺序发布累计 `VersionPin`；Coordinator 只负责补 Cell
  与去重首轮 reset。三轮 interaction 回归证明每轮 reset；candidate discovery failure
  回归证明终态仍显示 `[baseline]`；第二个 coordinate indeterminate 回归证明 active
  package 不会提前进入完成行。
- **布局回归：** live 完成行在 56/80/120 列均位于 probe 与 stage 上方；live 与冻结
  卡片的 ANSI 都证明整行 green，包名/分隔符无 bold/dim/cyan，version 为 bold green。
- **Review 修正：** 首轮 Spec review 发现 `[baseline]` 原本要等到 candidate build
  成功并进入 coordinate sweep 才发布，导致 candidate discovery 失败时遗漏已完成事实。
  发布时机前移到 baseline 成功后，并用 invocation-local callback state 去重首轮 sweep
  的相同 reset；补充 candidate discovery failure 与 active coordinate indeterminate
  回归。Standards review 要求拆分 declaration 与 search probe 两种不同样式语义的
  参数化测试，并同步完成计划状态。修正后的 Standards / Spec 双轴复审均为 0 findings。
- **样式 follow-up：** RED 证明 search window 第二段没有 dim、completed package
  version 没有 bold。GREEN 在共享 formatter 内令 `[window#count]` 继承上下文色并
  dim，同时只给完成 version 增加 bold；live 与冻结路径复用同一实现，Activity/schema
  无变化。冻结块的 completed packages 与 probe detail 同时遵循两字符缩进。

## 5. 验证结论

- terminal/search/coordinator 聚焦回归：`150 passed`。
- CLI/workflow/schema/verification 相邻回归：`216 passed`。
- `ruff check src tests`、`ty check src` 与 `git diff --check` 均通过。
- 完整套件：受限沙箱首次为 `1299 passed, 1 failed`，唯一失败是安装态 E2E 无法访问
  `pypi.nvidia.com/uv-build`；同一 E2E 在允许依赖访问后通过。最终代码状态在允许依赖
  访问的完整复跑为 `1304 passed in 21.37s`。
- 本次样式 follow-up 的 terminal/search/coordinator 扩展相邻回归包含在
  `217 passed` 中；全仓 Ruff、ty 与允许依赖源访问的无 testmon 完整 suite 均通过，
  最终为 `1304 passed in 22.66s`。
- 根目录既有未跟踪 `package-floor.json` 未读取、未修改，也不进入本次提交。

# P020 — smoke/check/search live 信息密度与搜索向量

- **状态：** 已归档（已完成）
- **日期：** 2026-08-28
- **展示契约：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **前序实施：** [P016](P016-pf-cli-live-presentation.md)、[P018](P018-pf-search-completed-packages.md)

## 1. 目标

1. setup 卡片首行展示本次 Verification Run 的 run ID；Cell matrix 摘要同时展示
   selected Cell 数、active direct package 数与其中 pinned package 数。
2. live Cell 把 identity 与任意当前 stage 合并为一行；三个 bracket token 的内容依次
   使用 bold cyan、cyan、默认前景色，bracket 本身使用 dim 默认前景色；dynamic tests
   的第三段精简为 `testing`，其 progress count/ETA 同样使用默认前景色且不 dim，
   count 不追加 `tests`。
3. search 第一条 detail 展示当前 sweep 的完整有序 vector，但排除正在下降的
   coordinate；本轮已完成 coordinate 为 green 且 package name bold，未完成 coordinate
   为 dim 默认前景色且 package name 不加粗，新 sweep 重置完成态。
4. Cell title 内容使用 bold 默认前景色，bracket 使用 dim 默认前景色；Cell 与 footer
   elapsed 使用 dim magenta。

上述状态只属于 invocation-local Activity / terminal presentation，不进入 report、
cache、Journal、FailureRecord 或 identity。

## 2. Interface 与所有权

- workflow 从选中 Cell 的 `active_declaration_ids` 与 `PackagePlan.declarations` 计算
  active/pinned package 数，并随 `CellMatrixEvent` 发布；Presenter 只排版。
- `CoordinateSearch` 发布按实际搜索顺序排列的当前 vector 与本轮完成前缀；
  `SearchCoordinator` 只适配 Cell identity，`LiveVerificationView` 结合当前 probe 排除
  active coordinate。
- `TerminalPresenter` / `LiveVerificationView` 共用 title、live identity 与 search vector
  formatter，TTY 宽度仍交给 Rich 测量。

## 3. 纵向 TDD 顺序

1. **Setup / title / elapsed：** 从公开 Activity event 与 ANSI 输出证明 run ID、计数和
   bracket/content 风格；再扩展 event producer 与共享 formatter。
2. **Stage identity：** 从 TTY live 输出证明 identity 与 stage 仍占两行；再把所有 stage
   合并为第三个 token，其中 dynamic tests 使用 `[testing]`，并保持 progress/ETA 冻结
   规则；progress count/ETA 的文本 style 与第三个 token 一致，count 不显示单位。
3. **Search vector：** 先在真实 `CoordinateSearch` / `SearchCoordinator` 事件序列证明只
   有 completed prefix；再发布完整 vector，覆盖 active 排除、完成回填和新 sweep reset。
4. **Qualification：** focused terminal/search/workflow、Ruff、ty、真实 smoke/check/search、
   全量 suite 与 Standards/Spec 双轴 review。

## 4. 实施记录

- Setup/title/elapsed：新增公开 event 与 ANSI 断言先失败；实现 run-id、active/pinned
  计数、共享 Cell title formatter 与 dim magenta elapsed 后通过。
- Stage identity：dynamic tests 与其他 stage 的单行 token 断言先失败；实现统一第三 token，
  并将动态进度精简为默认色 `completed/total ETA ...` 后通过。
- Search vector：真实 coordinate/search event 序列先证明旧事件只有 completed prefix；扩展为
  完整 vector + completed prefix 后，active 排除、完成回填与新 sweep reset 均通过。
- 证据：terminal 107 passed；受影响模块 344 passed；最终完整 suite 1320 passed；Ruff 与 ty
  通过。联网真实 fixture 的 smoke/check/search 均成功，三条路径均显示 run-id 与
  `selected 1 cell, 2 active packages (1 pinned)`。
- Standards/Spec 双轴审查的 findings 已全部关闭，最新复核无 findings。
- 偏差：首次真实 smoke/check 在受限网络中得到 source indeterminate；获准联网重跑后
  通过，属于执行环境证据，不是产品回归。

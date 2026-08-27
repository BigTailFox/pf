# P017 — explain 报告与终态 Cell 卡片

- **状态：** 已完成
- **日期：** 2026-08-27
- **展示契约：** [D006](../designs/D006-pf-cli-enhancement.md)
- **前序实施：** [P014](P014-pf-cell-diagnostics.md)、[P016](P016-pf-cli-live-presentation.md)

## 1. 目标

把 `pf explain` 收敛为报告摘要卡和逐 Cell 终态卡，并复用
`smoke` / `check` / `search` 的 Rich outcome、Cell identity、边框、Reason 与
diagnose-hint 视觉语言。

默认 explain 只回答：

1. 报告身份、complete 状态、apply 授权、Cell coverage；
2. 每条 declaration 的 floor/projection；
3. 每个目标 Cell 的最终状态与终止原因；
4. 仅在 Cell 有权威终止 Failure 时给出精确 `pf diagnose --failure` 入口；
5. 最后一条命令级 summary，以及 complete 报告的 apply 下一步。

报告中被后续探针覆盖的历史 Failure、ty baseline/incremental diagnostics、pytest
failure detail、process output 与日志都不进入默认 explain；它们由 `pf diagnose`
承担。

## 2. 接口与所有权

`TerminalPresenter.render_explain(reports)` 保持唯一公开 interface 和 stdout/exit
契约。`ValidatedReport -> explain CellPresentation` 的投影属于 terminal 模块内部
seam：

- `CellSuccess` 只显示成功终态，不展示搜索历史；
- `BaselineRejection` / `BaselineIndeterminate` 显示 baseline 终止 Failure；
- `CellIndeterminate` 只选择其 `failure_id` 指向的终止 Failure；
- `CellSearchFailure` 显示其命令级搜索结论，不选择历史候选 Failure；
- target Cell 没有 CellResult 时显示 `MISSING_CELL` warning；
- 所有 explain 投影都丢弃运行时 pytest/static detail。

Rich layout、theme、TTY Panel 与非 TTY 降级仍由 `TerminalPresenter` 统一拥有；
workflow、report schema 和 search 编排不学习展示字段。

## 3. 非目标

- 不改变 `package-floor.json` Schema 2、identity、FailureRecord 或验证结论；
- 不改变 `pf search` 写报告、exit code 或 live/final Cell 选择；
- 不让默认 explain 成为 Failure history 或工具证据浏览器；
- 不移除 `pf diagnose` 的现有技术详情与安全日志能力；
- 不固定生产 Panel、列或文本宽度。

## 4. 纵向 TDD 顺序

1. **终态 Cell tracer bullet：** 构造含历史 rejection 与终止 indeterminate 的公开
   `render_explain` 路径；先证明旧输出按 Failure title 聚合并泄漏 ty/pytest 证据，
   再实现逐 Cell 终态卡、精确终止原因和 diagnose ID。
2. **报告摘要卡：** TTY 断言 package/status/apply/coverage/requirements 位于 Rich
   Panel 内，并验证用户字符串继续按 literal Text 渲染。
3. **结果变体：** 覆盖 success、baseline rejection/indeterminate、exhaustive search
   failure 和 missing target Cell；证明 history 不会替代最终 Cell reason。
4. **布局与降级：** 覆盖非 TTY 可读输出及 56/80/120 列自适应换行。
5. **验证：** explain/terminal 聚焦 pytest、Ruff、ty、完整 pytest、真实现有
   `package-floor.json` 的只读 `pf explain`；记录环境性失败与 review 结论。
6. **Summary 结果色：** 通过公开 `render_explain` 路径依次锁定 red Cell 优先、
   incomplete yellow、applyable green 三条契约；整条 Summary 使用对应 outcome 的
   bold theme，且不把颜色决策下沉到 report schema。

## 5. 实施记录

- **终态 Cell tracer：** RED 用同一 Cell 的历史 `TEST_FAILURE` rejection 与最终
  `TOOL_FAILURE` indeterminate 证明旧 explain 聚合两条 Blocker、没有 Cell 卡。
  GREEN 复用 `CellPresentation` 与统一 outcome Panel，但增加 explain 专用终态投影：
  `CellIndeterminate.failure_id` 精确选择唯一终止 Failure，历史 failure 不进入卡片。
- **报告卡与 Rich 样式：** package/path 使用既有 `cell`/`path` theme，status、apply、
  projection 和边框使用 outcome 色；requirements 与摘要同处一张自适应 Panel。
  Panel 构造收敛到 `_outcome_card`，smoke/check/search 完成卡与 explain 共用边框、
  padding 和 Group 布局，非 TTY 逐行降级。
- **证据降噪：** 删除 explain 对 static baseline/incremental diagnostics 的遍历与折叠；
  baseline rejection 的运行时 pytest detail 也在投影时显式丢弃。`CellSuccess` 和
  `CellSearchFailure` 不读取历史 Failure；exhaustive no-pass 只显示命令级搜索结论，
  没有可捏造的 diagnose ID。缺失 target Cell 显示独立 warning 卡。
- **回归：** 新增独立公开 `render_explain` 测试，覆盖终止 Failure 选择、报告卡、
  static/pytest 隐藏、missing/success Cell、ANSI outcome 色；原有两条 static history
  展示测试改为断言默认 explain 不泄漏历史。review follow-up 又覆盖多 marker 缩进
  和 baseline indeterminate；独立 explain + 既有 ExplainRendering 最终为
  `19 passed`。
- **完整验证：** 全仓 Ruff 与 ty 通过；允许构建依赖访问后的显式
  `pytest --no-testmon -q` 为 `1290 passed in 21.42s`；sdist/wheel 构建成功。初次
  restricted sandbox 的 installed-CLI E2E 因无法下载 `uv_build` indeterminate，联网
  复跑后通过，不归类为产品失败。
- **真实 CLI：** 对根目录既有 `package-floor.json` 只读运行非 TTY 与伪 TTY
  `pf explain`；TTY 输出一张报告卡和 3 张 Cell 卡，每张只保留最终
  `INDETERMINATE/TOOL_FAILURE`、终止原因与精确 failure ID，不再显示历史 rejection、
  ty baseline 或 302 条 static diagnostics。既有未跟踪报告未修改、不进入提交。
- **并行边界：** 验证期间 P016 setup-card follow-up 同时修改 `_live.py`、P016 与
  terminal 测试；本任务不修改或暂存这些 hunks，最终全量验证覆盖两组当前改动。
- **双轴 review：** 初审 Standards 为 0 findings；Spec 指出多 marker projection 仍
  被压成一行，以及 P017 计划中的 baseline indeterminate 缺少独立公开测试。新增
  缩进布局 RED→GREEN 与对应终态测试后复审，Standards/Spec 均为 0 个遗留问题、
  无 scope creep。
- **Summary 结果色 follow-up：** 新增 red Cell 优先于 yellow、仅 yellow Cell、
  complete/applyable 三条公开 ANSI 契约。`_explain` 只聚合既有
  `CellPresentation.kind`：complete 为 bold green；不可 apply 且任一 Cell failure 为
  bold red；其余不可 apply 状态为 bold yellow。整条 Summary 共享一种样式。
- **Follow-up 验证：** explain 聚焦测试 `22 passed`，全仓 Ruff 与 ty 通过；允许依赖
  访问后的完整 pytest 为 `1294 passed in 21.57s`，sdist/wheel 构建成功。根目录既有
  报告只读 TTY 验证为 3 个 yellow Cell，Summary 输出 bold yellow；环境原有
  `NO_COLOR=1` 时按 Rich 标准只保留 bold，不把用户级禁色设置当作产品失败。
  固定区间 `3ef5485...a67650c` 的 Standards/Spec 双轴复审均为 0 findings、无 scope
  creep。

# D025 — 多宿主 host-partial search/minimize 协议

- **状态：** 已完成、已归档
- **日期：** 2026-09-04
- **最后修订：** 2026-09-04
- **性质：** 临时迁移 Design；稳定规则已归并到现行 owner，本文不再承担规范性
- **评审来源：** [R006](../../reviews/R006-pf-cli-system-review.md) §3.3、
  [R007](../../reviews/R007-pf-current-improvement-priorities.md) §2
- **产品边界：** [D001](../../designs/D001-pf.md)
- **CLI 展示：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **运行语义：** [D008](../../designs/D008-pf-verification-run.md)
- **Apply 授权：** D001 §6；Presenter 不重算 scope
- **实施计划：** [P031](../plans/P031-pf-host-partial-protocol.md)

本文拥有 search 在纯 host-partial 结果上的数值退出，以及 minimize 在可授权 host-partial
report 上的唯一结果卡/final 提示。D008 的 Cell 选择与聚合、D014 wire、merge identity 与
ApplyAuthorizer 的 `PLATFORM_SCOPED` 规则保持不变。

## 1. 问题与目标

每个进程只执行 `cell.target == host_target` 的 Cell；其他宿主必须写出各自 report 再
`pf merge`。本宿主全部已执行 Cell 都成功、唯一 incomplete reason 为其他宿主
`MISSING_CELL` 时，报告是合法 host-partial artifact，但现行 `_search_exit_code()` 把任何
非空 reasons 映射为退出 `2`。fail-fast CI 会在上传 artifact 前停止。

`minimize` 已顺序调用 search 与默认 apply，Authorizer 也可把只缺完整 MissingSelector 的
incomplete report 授权为 `PLATFORM_SCOPED`。`render_minimize` 丢弃 report，只渲染 apply
卡，因此可以退出 `0` 并改元数据，却不说明仍需 merge，也容易把 Preserved 平台理解成
passed。

目标：把上述纯 host-partial search 定义为退出 `0` 的 success-with-warning；minimize 在同一
判定下继续默认授权，并用一张卡和一个 final 给出剩余宿主与 `pf merge` 下一步。

## 2. 判定输入

CLI 判定只使用 `(report.result.reasons, report.cell_results, report.target_cells)`。
`host-partial success` 当且仅当同时成立：

1. `report.result.status == incomplete` 且 `reasons == {"MISSING_CELL"}`；
2. `cell_results` 非空，且每一项都是 `CellSuccess`；
3. 每个缺失的 `target_cells` 项（identity 不在 `cell_results` 中）的 `cell.target` 都不属于
   已观察 Cell 的 `target` 集合。

第 3 条把「其他宿主缺失」与「本宿主还有未写出的 Cell」分开。D008 聚合 dominance 不变：
`BASELINE_REJECTION` 仍退出 `1`，`INDETERMINATE` 仍退出 `4`，二者都优先于本节。

## 3. Search 退出与展示

| 类 | 判定 | 退出 | final |
| --- | --- | --- | --- |
| 完整单宿主成功 | 非 incomplete | `0` | stdout `✓ Search complete` |
| host-partial success | §2 | `0` | stderr `⚠ Search incomplete · <path> written · N cells passed · M cells await other hosts · next: collect reports and run pf merge` |
| 本宿主无匹配 Cell | `MISSING_CELL` 且 `cell_results` 为空 | `2` | stderr `⚠ Search incomplete · … · no configured cells match this host` |
| 本机 no-floor / 非单调 / 不确定投影 | 对应 reasons，无 baseline/indeterminate dominance | `2` | 现行 reason-aware incomplete final |
| 本机失败与远端 missing 混合 | `MISSING_CELL` 与本机 incomplete/failure 同时存在 | `2` | 本机原因 + 远端 missing；不出现 merge next |
| 同宿主仍有缺失 Cell | 不满足 §2.3 | `2` | 不得使用「await other hosts」或 merge next |
| Baseline Rejection | `BASELINE_REJECTION` | `1` | 现行 stopped/failure |
| Indeterminate | `INDETERMINATE` | `4` | 现行 stopped/indeterminate |

host-partial 的报告仍是 incomplete，final 不得使用 `Search complete`，也不得把 `complete`
用于该 artifact。通道遵循 D006：warning/incomplete summary 走 stderr；退出 `0` 配 `⚠`
沿用 source-drift apply 已有模式。`✓` 仍只用于无 warning 的成功。

## 4. Minimize

`minimize` 仍顺序复用 search 与默认 `ApplyAuthorizer`：

- 不得仅因 report 为 incomplete 就跳过默认授权；
- 可授权的 host-partial report 继续按现行 `PLATFORM_SCOPED` 规则决定 apply；Presenter 不重算
  scope、不发明 `--force`、不把 Preserved 说成 passed/covered；
- 只渲染一张 apply/minimize 结果卡和一个 final，不展开 search card；
- 当 search report 满足 §2 且 apply 成功：卡结构仍是 Evidence / Scope / 可选 Preserved /
  可选 Override/Paths / Metadata；final 为 stderr `⚠ Minimized floors · <updated|unchanged> ·
  M cells await other hosts · next: collect reports and run pf merge`，退出 `0`；
- 若同时使用 source-drift waiver，仍是一张 warning 卡和一个 warning final，把 override 与
  remainder/merge 写进同一 final；
- 完整单宿主成功的 minimize 保持现行 stdout `✓ Minimized floors`。

apply 命令本身的授权、退出与 Preserved 文案不在本文迁移范围内。

## 5. 不变项

- report wire、generation update、merge identity 与 apply authority；
- D008 每进程只跑本机 target、空宿主仍形成 `MISSING_CELL` incomplete report；
- `minimize` 不增加 `--force`；`--force` 只属于 `apply`；
- 不为 host-partial 增加 JSON 结果、quiet 模式或第二份 summary。

## 6. 验收标准

1. 本宿主有 Cell、全部 `CellSuccess`、缺失 Cell 的 `target` 均与已观察集合不同、reasons 仅为
   `MISSING_CELL` 时，`pf search` 退出 `0`，stderr 含 passed / await other hosts / `pf merge`，
   stdout 无 `Search complete`。
2. 本宿主无匹配 Cell、同宿主仍有缺失 Cell、本机 no-floor/非单调/不确定投影、本机失败与远端
   missing 混合、Baseline Rejection、Indeterminate 的退出码与现行非 host-partial 语义一致。
3. 单宿主完整成功仍退出 `0` 且 stdout `Search complete`。
4. host-partial search 之后默认 apply 仍按 Authorizer 授权；minimize 成功时只有一张卡和一个
   final，final 含剩余其他宿主计数与 `pf merge`，Preserved 只写 original constraints retained。
5. source-drift 与 host-partial 同时成立时仍是一张 warning 卡、一个 warning final、退出 `0`。
6. 公共 presenter/CLI tests 覆盖 §3 各类与 minimize 的 host-partial/完整成功；不断言 private
   helper 名称。
7. 稳定规则归并到 D001/D006 与 README 入口摘要；R006/R007 关闭对应项；本文与 P031 在同一
   完成变更中归档。

## 7. 非目标

- 改 report/Schema、merge 算法或 ApplyAuthorizer 资格；
- 给 empty-host 或同宿主缺失 Cell 退出 `0`；
- 非 TTY 搜索活动、command-scoped composition、Ctrl+C 退出 `130`。

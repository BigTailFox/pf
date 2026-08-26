# PF runtime-backed 静态引导搜索

- **状态：** 已归并
- **日期：** 2026-08-23
- **最后核对：** 2026-08-26
- **实施记录：** [P010](../plans/P010-pf-runtime-backed-static-search.md)
- **实现提交：** `c4e0cf4`

本文保留从 static disposition 迁移到 runtime-backed 搜索的决策边界。迁移已经完成，现行算法、静态证据和失败资格分别由 D003、D004、D005 定义；本文不再复制这些条款。

## 决策

- `ty` 只产生 `STATIC_UNCHANGED | STATIC_REGRESSION` transition evidence；静态 regression 本身不是 Rejection；
- 完整增量多重集生成版本化 canonical fingerprint，同一固定 Slice 内的连续相同 fingerprint 可以形成 invocation-local static region；
- 结构化 strong diagnostic 可生成 adapter-owned runtime witness plan；只有 `CONFIRMED_MISSING` 是 runtime negative evidence；
- `PRESENT`、`NOT_APPLICABLE`、异常、timeout、signal、协议错误或工具失败都不能授权 PASS 或 Rejection boundary；
- static-only observation 只能引导 probing，不能成为边界或 final floor；
- 每个已提交边界必须有当前 Proposal 的直接 runtime evidence；最终 floor 必须由该精确 Proposal 自身的完整 PASS 闭环授权；
- region、cache 与 promotion 不能跨 cell、snapshot、policy、baseline 或 coordinate Slice 复用；非单调结果仍 fail closed。

本决策不引入 static-only floor、任意用户脚本 witness、stderr 解析、跨运行 region cache 或新的搜索坐标。

## 现行所有者

| 规则 | 现行所有者 |
| --- | --- |
| Probe 顺序、region、promotion、边界与终止 | [D003](D003-pf-search-algorithm.md) |
| Static baseline、fingerprint、diagnostic classifier 与 witness protocol | [D004](D004-pf-ty-enhancement.md) |
| Runtime negative evidence 的 disposition 资格 | [D005](D005-pf-failure-and-diagnose.md) |
| Attempt 序列与运行投影 | [D008](D008-pf-verification-run.md) |
| Schema 2 中的 static/terminal evidence refs | [D014](D014-pf-report-schema.md) |

实施顺序、验收矩阵和命令结果只保留在 P010。

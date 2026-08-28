# PF v1 模块加深与内部 seam

- **状态：** 已归档（已归并）
- **日期：** 2026-08-22
- **最后核对：** 2026-08-26
- **来源：** [R001](../reviews/R001-pf-v1-review.md)
- **实施记录：** [P008](../plans/P008-pf-v1-refactor.md)
- **实现提交：** `acd2a20`

本文保留 D009 的决策边界。整改已经实施，现行规则已归入下表所有者；本文不再定义命令、Schema、算法或模块接口。

## 决策

D009 先修复安全与证据授权，再加深现有模块：

- 对每个输出观察面独立执行流式脱敏，不让凭据进入日志、终端或公共报告；
- Candidate 的 locator/hash 约束实际安装，complete report 只能由 final PASS Proposal 授权；
- workspace apply 使用恢复日志、原子替换和失败回滚；canonical package name 必须唯一；
- 离线 discovery 与完整 planning 分离；Verification Journal 按 package 保存 policy identity；
- 统一 cell lookup、FailureRecord 提取、Evaluation 分类与 VerificationRunner 编排；
- CoordinateSearch 的调用状态私有，可重入且可并发；Protocol 由 consumer 定义最小表面；
- PreparedEnvironment 显式管理生命周期；终端内部视图不泄漏到业务模块。

这些决策不增加命令，不扩大产品范围，也不引入 DI framework、通用 repository、event bus 或 daemon。

## 现行所有者

| 规则 | 现行所有者 |
| --- | --- |
| 产品范围、完整报告与 apply 授权 | [D001](../../designs/D001-pf.md) |
| 当前模块、interface、composition 与资源生命周期 | [D002](../../designs/D002-pf-implementation.md) |
| 单 cell 算法与 CoordinateSearch | [D003](../../designs/D003-pf-search-algorithm.md) |
| FailureRecord 与 Evaluation 分类 | [D005](../../designs/D005-pf-failure-and-diagnose.md) |
| 终端展示 | [D006](../../designs/D006-pf-cli-enhancement.md) |
| 脱敏、Process Log 与 Output Cache | [D007](../../designs/D007-pf-process-output.md) |
| VerificationRunner、Journal 与 diagnose 读取面 | [D008](../../designs/D008-pf-verification-run.md) |
| Schema 1 引用图、complete authority 与 report transaction | [D014](../../designs/D014-pf-report-schema.md) |

实施顺序、测试计数、命令与完成证据只保留在 P008；评审发现只保留在 R001。

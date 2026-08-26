# PF v1 架构加深

- **状态：** 已归并
- **日期：** 2026-08-22
- **最后核对：** 2026-08-26
- **来源：** [R002](../reviews/R002-pf-v1-architecture-review.md)
- **实施记录：** [P009](../plans/P009-pf-v1-architecture.md)
- **实现提交：** `e8ec5e1`

本文保留 D010 的架构决策。实现已经落地，当前接口和行为由下表所有者描述；本文不再充当第二份现行结构说明。

## 决策

- `ResolutionRequest` 使用 `HighestResolution | LowestDirectResolution | ExactSelection` 判别变体，非法参数组合不可表示；
- cell identity 与 scheduling order 分离，排序只由显式 `cell_schedule_key` 负责；
- `VerificationRunner` 拥有领域调度、deadline outcome、completion 投影与 Journal 时序；generic `Scheduler` 是其内部实现；
- stage、context 与 completion 使用判别 activity event，不用 `completed == 0` 或可空字段组合编码状态；
- `_ProposalRunner.evaluate_full` 一次返回绑定的 `ProbeRun(evidence, evaluation)`；
- 安全日志目录由私有 POSIX/Windows adapter 实现，产品层不包含平台条件；
- `CellPresentation` 与 `LiveVerificationView` 是终端私有模型；
- `build_context()` 是唯一生产 composition root，`CliContext.close()` 统一关闭资源；SnapshotBuilder 显式接收 process seam。

这些决策不改变 failure 分类、报告授权、终端文案或搜索顺序。

## 现行所有者

| 规则 | 现行所有者 |
| --- | --- |
| 模块布局、ResolutionRequest、composition 与 private seam | [D002](D002-pf-implementation.md) |
| 搜索调用与 ProbeRun | [D003](D003-pf-search-algorithm.md) |
| 终端活动与展示 | [D006](D006-pf-cli-enhancement.md) |
| 安全日志行为 | [D007](D007-pf-process-output.md) |
| Runner、Scheduler、event 与 Journal 时序 | [D008](D008-pf-verification-run.md) |
| harness request 与 environment identity | [D012](D012-pf-harness-relaxation.md) |

完整实施过程与验证证据只保留在 P009；架构评审只保留在 R002。

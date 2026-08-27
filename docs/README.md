# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-08-26

本页只负责文档治理、契约所有权和导航。一个规则只有一个规范性所有者；其他文档只能引用它。代码与现行契约冲突时，应在同一变更中修正实现或所有者文档。

## 文档类型与状态

```text
README.md                 # 使用入口
CONTEXT.md                # 现行领域词汇，不定义行为
docs/
├── README.md             # 本页
├── designs/              # 现行契约、草案与已归并设计 D001–D015
├── investigation/        # 非规范性实验快照 I001–
├── plans/                # 非规范性实施记录 P001–P016
├── reviews/              # 非规范性评审快照 R001–
├── schemas/              # 机器可读公共 Schema
└── examples/             # 由 Schema 模型生成的示例
```

| 状态 | 规范性 | 含义 |
| --- | --- | --- |
| 现行 | 是 | 已落地且必须与代码一致 |
| 草案 | 否 | 尚未批准或落地，不能解释当前行为 |
| 已归并 | 否 | 设计已经实施，现行规则已移入列明的所有者；本文只保留决策边界 |
| 已完成 | 否 | Plan 的历史执行与验证记录 |
| 快照 | 否 | Review 或 Investigation 对当时状态的记录 |

Plan、Review 和 Investigation 中的状态、命令、计数与结论都属于历史快照，不随当前实现回写。它们不能覆盖现行 Design，也不能作为当前行为的第二份契约。

## 现行契约所有权

| 所有者 | 唯一负责 |
| --- | --- |
| [D001](designs/D001-pf.md) | 产品边界、命令和参数、配置、报告用途、apply 条件、退出码 |
| [D002](designs/D002-pf-implementation.md) | 当前模块、接口、依赖方向、组合与持久化边界 |
| [D003](designs/D003-pf-search-algorithm.md) | 单 cell 坐标搜索、probe 顺序、static region、终止和非单调处理 |
| [D004](designs/D004-pf-ty-enhancement.md) | `ty` 基线、诊断 identity、static transition、runtime witness |
| [D005](designs/D005-pf-failure-and-diagnose.md) | Attempt、cause、disposition、FailureRecord 与 `diagnose` 语义 |
| [D006](designs/D006-pf-cli-enhancement.md) | help、调用错误、输出通道、终端层级、摘要与 `explain` 展示 |
| [D007](designs/D007-pf-process-output.md) | Process Log、Output Cache、输出完整性与日志保密 |
| [D008](designs/D008-pf-verification-run.md) | 命令的 Attempt 序列、Verification Role、Journal、运行终态与 diagnose 读取面 |
| [D012](designs/D012-pf-harness-relaxation.md) | structured harness、relaxation、两次 resolution/一次 installation、resolver 资格边界 |
| [D013](designs/D013-pf-pytest-failure-evidence.md) | 现行 direct pytest failure-witness profile 与 UI-only pytest telemetry |
| [D014](designs/D014-pf-report-schema.md) | `package-floor.json` Schema 2 wire、typed refs、规范编码与跨引用验证 |

[D014](designs/D014-pf-report-schema.md) 的 Schema 2 是现行唯一报告布局；Schema 1 不在兼容性或资格化范围内。

以下 Design 已实施并归并，不再拥有现行条款：

- [D009](designs/D009-pf-v1-refactor.md)：安全、证据授权、apply transaction、discovery 与模块加深决策；现行所有者见文内映射。
- [D010](designs/D010-pf-v1-architecture.md)：判别 request/event、Runner/Scheduler、平台日志 seam、终端私有视图与 composition 决策；现行结构由 D002 等文档拥有。
- [D011](designs/D011-pf-runtime-backed-static-search.md)：runtime-backed static search 的迁移决策；现行算法与证据分别由 D003/D004/D005 拥有。

[D015](designs/D015-pf-authoritative-verification-outcome.md) 是草案，只描述可能替换 D013 outcome classifier 的方案，不解释当前行为。

## 非规范性记录

- [I001](investigation/I001-pf-pytest-witness-collection.md) 是 D013 的 pytest 6–9 实验输入快照。
- [P001](plans/P001-pf-v1.md)–[P016](plans/P016-pf-cli-live-presentation.md) 是实施与验证记录；其中 P013 如实保留 D014 固定样本资格化尚未完成时的状态。
- [R001](reviews/R001-pf-v1-review.md)–[R003](reviews/R003-pf-search-indeterminate-review.md) 是对应提交和运行证据的评审快照。

根目录 [README](../README.md) 只提供使用入口；[CONTEXT.md](../CONTEXT.md) 只约束现行术语。机器可读报告结构见 [JSON Schema](schemas/package-floor-v2.schema.json)，最小文档见 [complete](examples/package-floor-v2-minimal-complete.json) 与 [incomplete](examples/package-floor-v2-minimal-incomplete.json) 示例。

# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-08-28

本页只负责文档治理、契约所有权和导航。每条现行规则只有一个规范性所有者；其他文档只引用，不复述。代码与文档冲突时，同一变更必须修正实现或所有者文档。

## 目录

```text
README.md                  使用入口
CONTEXT.md                 领域词汇；不定义行为
docs/
├── designs/               现行或草案设计
├── reviews/               尚未解决的评审
├── schemas/               D014 生成的机器可读投影
├── examples/              D014 生成的最小示例
└── archived/
    ├── designs/           已被现行设计覆盖的决策
    ├── plans/             已完成的实施记录
    ├── reviews/           问题已解决的评审
    └── investigations/    结论已被设计吸收的探索
```

现行文档使用“现行”或“草案”；归档文档不再承担规范性。Plan、Review 和 Investigation 中的命令、计数与结论都是历史证据，不随当前实现回写。

## 契约所有权

| 唯一所有者 | 负责的现行契约 |
| --- | --- |
| [D001](designs/D001-pf.md) | 产品边界、命令与配置、报告用途、apply 条件、数值退出码 |
| [D002](designs/D002-pf-implementation.md) | 模块 interface、seam、依赖方向、composition 与持久化边界 |
| [D003](designs/D003-pf-search-algorithm.md) | 单 Cell 搜索、probe、static region、边界提交与终止 |
| [D004](designs/D004-pf-ty-enhancement.md) | `ty` 基线、诊断 identity、static transition 与 runtime witness |
| [D005](designs/D005-pf-failure-and-diagnose.md) | disposition、Rejection 资格、cause、FailureRecord identity 与诊断语义 |
| [D006](designs/D006-pf-cli-enhancement.md) | help、通道、live/final Cell、summary、`explain` 与 `diagnose` 展示 |
| [D007](designs/D007-pf-process-output.md) | ProcessObservation、Process Log、Output Cache、脱敏与安全读取 |
| [D008](designs/D008-pf-verification-run.md) | Attempt 序列、Role、跨 Cell 调度、命令聚合、Journal 与 Diagnosis Index |
| [D012](designs/D012-pf-harness-relaxation.md) | structured harness、relaxation、resolution/install 与 uv 资格边界 |
| [D013](designs/D013-pf-pytest-observer.md) | direct pytest observer、progress/detail telemetry 与透明性资格 |
| [D014](designs/D014-pf-report-schema.md) | `package-floor.json` Schema 1 wire、typed refs、编码与 reader 验证 |

[JSON Schema](schemas/package-floor-v1.schema.json) 与 [complete](examples/package-floor-v1-minimal-complete.json) / [incomplete](examples/package-floor-v1-minimal-incomplete.json) 示例是 D014 Pydantic wire model 的生成物，不建立第二份契约。

## 开放事项与归档

- [R004](reviews/R004-pf-search-performance-review.md) 保留在现行目录，因为其中的性能优化候选尚未实施。
- [归档索引](archived/README.md) 记录已归并设计、已完成计划、已解决评审和已吸收探索。

新增文档先放入对应现行目录；Plan 完成、Design 被替代、Review 解决或 Investigation 被吸收后，在同一变更中移入 `archived/<type>/` 并修复引用。

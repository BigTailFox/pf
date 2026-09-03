# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-09-03

本页只负责文档治理、契约所有权和导航。每条现行规则只有一个规范性所有者；其他文档只引用，不复述。代码与文档冲突时，同一变更必须修正实现或所有者文档。

## 目录

```text
README.md                  使用入口
CONTEXT.md                 领域词汇；不定义行为
docs/
├── designs/               现行或临时迁移 Design
├── plans/                 进行中的实施计划与证据记录
├── experiments/           非规范性实验事实与 dogfood 结论
├── reviews/               尚未解决的评审
├── schemas/               D014 生成的机器可读投影
├── examples/              D014 生成的最小示例
└── archived/
    ├── designs/           已被现行设计覆盖的决策
    ├── plans/             已完成的实施记录
    ├── reviews/           问题已解决的评审
    └── investigations/    结论已被设计吸收的探索
```

长期 owner Design 使用“现行”；临时迁移 Design 可以是“草案”或“已接受、待实施/实施中”。已接受
Design 定义唯一目标契约，但在实现与 owner 归并完成前不冒充现行行为。归档文档不再承担规范性。
Experiment、Plan、Review 和 Investigation 中的命令、计数与结论都是历史证据，不随当前实现回写。

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

## 实验报告

- [E001](experiments/E001-pf-self-bootstrap-validation-contract.md) 记录 PF 自举 full-repository contract 下 `packaging>=22` 的 dogfood 归因，以及当前单测锁定的 pytest、uv、ty 兼容性证据边界；它是非规范性实验证据。

## 开放事项与归档

- [R004](reviews/R004-pf-search-performance-review.md) 保留在现行目录，因为其中的性能优化候选尚未实施。
- [R005](reviews/R005-pf-module-depth-review.md) 仍保留 workspace inventory、Verification Run、评价层假想 Protocol、terminal-private result-card 与 SearchCoordinator 测试表面的未实施 module-depth 候选；SourcePlan 已由 D019/P025 解决并归并到现行 owner。
- [D019](archived/designs/D019-pf-source-plan-depth.md) / [P025](archived/plans/P025-pf-source-plan-depth.md) 的 SourcePlan 深化迁移已完成；设计理由与完整验证证据见归档记录。
- D018/P024的诊断与结果命令卡片迁移已完成；稳定规则已由D001/D002/D005/D006/D008接管，
  D014的merge authority保持不变，设计理由和实施证据见归档索引。
- D017/P023的单target与workspace direct dependency迁移已完成；设计理由和实施证据见归档索引。
- [归档索引](archived/README.md) 记录已归并设计、已完成计划、已解决评审和已吸收探索。

新增文档先放入对应现行目录；Plan 完成、Design 被替代、Review 解决或 Investigation 被吸收后，在同一变更中移入 `archived/<type>/` 并修复引用。

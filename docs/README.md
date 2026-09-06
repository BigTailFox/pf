# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-09-06（源码与文档核对；不表示重新完成运行资格矩阵）

本页拥有文档分类、生命周期、权威归属与导航。工程实施门槛由 [AGENTS.md](../AGENTS.md) 拥有。

## 1. 文档规则

| 类型 | 用途与权威 | 状态与最小记录 |
| --- | --- | --- |
| owner Design（D） | 唯一现行行为契约 | 现行；范围、owner/关联、规则、不变量、验证边界、最后核对日期 |
| 临时 Design（D） | 已接受时定义唯一目标契约；完成吸收前不冒充已交付行为 | 草案 / 已接受待实施 / 实施中；目标 owner、验收标准、接受状态 |
| Plan（P） | 实施步骤、决定与验收证据；不另立行为契约 | 进行中 / 已完成；按 AGENTS.md 维护 Design 验收映射 |
| Review（R） | 问题证据与开放项；不授权实施 | 开放 / 已解决或已移交；基准 commit、owner、影响、证据、状态与去向 |
| Concept（C） | 待证构想；不授权实施、不要求 Plan | 开放 / 转入 Design / 关闭；来源、证据缺口、进入 Design 的条件 |
| Experiment（E） / Investigation（I） | 固定环境下的事实、调查与结论；非规范性 | 进行中 / 已完成；日期、源码/工具/配置、命令、结果、局限与固定证据位置 |
| README / CONTEXT | 使用摘要 / 领域词汇 | 引用 owner，不增加行为或验收义务 |
| schemas / examples | wire model 的生成投影 | 由 D014 指定脚本生成，不手改、不形成平行契约 |

每条规则只在一个 owner 中完整定义。消费方保留接口关系与链接；必要的用户摘要、示例和生成投影
须明确来源，不能独立改变规范。表中的“D002 + D008”式关联表示各自拥有不同边界，不表示共同定义同一规则。

临时 Design 要逐项标明替代哪个 owner 的哪条规则；目标被接受不等于实现已验证。接受、Plan、实施、
验收与 owner 吸收的工程门槛直接遵循 AGENTS.md，本页不再复制一套流程。

文档与代码不一致时先查现行 owner、已接受变更和公开 seam：契约残留/过时则修订 owner；实现偏移则
记录 Review，保持应有契约，不能把 bug 写成新规范。无法确定意图时记录未决项，不以代码自动胜出。
纯文档纠错、去重、链接修复与历史归档不另建产品 Design/实施 Plan；改变产品承诺仍走 AGENTS.md。
纯文档拆分可迁移到新的长期 owner，必须在同一变更中搬走原规范、更新所有现行消费方引用并记录
规则迁移对照；不把这种内容迁移记为新功能实施。规范性附录属于其主文 owner，不占独立 D 编号。

Review/Plan/Experiment/Investigation 的命令、计数和当时结论是历史证据；后续判断追加带日期的状态说明，
不回写旧运行。实验产物使用固定 commit、不可变路径或保存的 identity；可变根 `package-floor.json` 不作历史证据链接。
静态检查、fixture 回放、宿主测试与真实资格运行必须分别标注，不能相互替代。

## 2. 归档规则

Plan 完成、临时 Design 被 owner 吸收、Review 问题解决或明确移交、Investigation 结论被吸收后归档。
开放项不能只因日期旧而关闭；移交须指定接收文档与事项。已完成 Experiment 可继续保留在
`experiments/` 作为稳定证据库，不在其中跟踪当前整改；完成不等于现行产品资格。

归档文件是历史记录，不再有规范性。归档完成后的文件不修改；新归档时只处理
状态、交接与路径重定位，不改历史事实。若既有归档引用旧地址，旧地址可保留仅导航的入口，
不能保留第二套契约或跟踪清单。修复现行入链，原编号永久保留、不复用。

索引只保留现行 owner、开放事项和简短历史入口；迁移过程、验收计数与逐项完成摘要留在归档。

## 3. 契约所有权

| 唯一所有者 | 负责的现行契约 |
| --- | --- |
| [D001](designs/D001-pf.md) | 产品承诺、声明/Cell 准入、命令/通用配置、统一 artifact policy、apply 条件、数值退出码 |
| [D002](designs/D002-pf-implementation.md) | 模块 interface、依赖方向、composition、资源/持久化 owner 与测试 seam |
| [D003](designs/D003-pf-search-algorithm.md) | 单 Cell 搜索、probe、static region、执行复用、边界与终止 |
| [D004](designs/D004-pf-ty-enhancement.md) | ty 基线、诊断 identity、static transition、runtime witness 与静态策略子对象 |
| [D005](designs/D005-pf-failure-and-diagnose.md) | Attempt/failure scope、disposition/cause、执行事实资格、FailureRecord identity、诊断 title/next step |
| [D006](designs/D006-pf-cli-enhancement.md) | 通道、live/final Cell、summary、explain/diagnose 展示；[附录](designs/appendices/D006-visual-specification.md) 固定 help 文案与视觉细则 |
| [D007](designs/D007-pf-process-output.md) | ProcessObservation、Process Log、Output Cache、完整性、脱敏与安全读取 |
| [D008](designs/D008-pf-verification-run.md) | Verification Run/Role/Attempt 序列、跨 Cell 调度、命令聚合、Journal/Diagnosis Index、impact |
| [D012](designs/D012-pf-harness-relaxation.md) | structured harness、relaxation、resolution/install、解释器/plan 时序与 uv 资格 |
| [D013](designs/D013-pf-pytest-observer.md) | direct pytest observer、progress/detail/cases telemetry 与透明性资格 |
| [D014](designs/D014-pf-report-schema.md) | Schema 1 wire、typed refs、identity/编码/reader、merge/update 与生成投影 |
| [D037](designs/D037-pf-candidate-search-policy.md) | 候选观测/准入、系列 DSL、anchor、条件默认、采样与 baseline artifact 选择域 |

[JSON Schema](schemas/package-floor-v1.schema.json)、[complete](examples/package-floor-v1-minimal-complete.json) /
[incomplete](examples/package-floor-v1-minimal-incomplete.json) 示例从 D014 的 wire model 生成。
[英文 README](../README.md)、[中文 README](../README.zh.md) 是使用入口，[CONTEXT](../CONTEXT.md) 固定领域词汇。

## 4. 开放事项

| 文档 | 当前跟踪范围 |
| --- | --- |
| [R010](reviews/R010-pf-engineering-document-audit.md) | 文档审计/重构记录、实现偏移与 R007 工程事项交接 |
| [R006](reviews/R006-pf-cli-system-review.md) | 非 TTY 活动、terminal-private result-card；历史已解决项保留证据 |
| [R008](reviews/R008-pf-search-performance-review.md) | region/hints/single-flight/materialize/xdist 与真实性能证据；过期 preflight 候选已撤销 |
| [C001](concepts/C001-pf-multi-resolution-coordinate-search.md) | 原 D031 的树搜索设想；E005 尚未证明树的默认收益，predecessor 重验已另行完成 |
| [C002](concepts/C002-pf-registry-analysis-cli.md) | 独立 registry 发布分布分析 CLI，命令与数据契约待探索 |
| [C003](concepts/C003-pf-resolution-output-completeness.md) | 成功 resolve 的日志完整性是否可与 lock authority 分离，依据待验证 |

<a id="uv-resolution-output-completeness"></a>

成功解析日志完整性开放项的稳定入口为 [C003](concepts/C003-pf-resolution-output-completeness.md)。
目前没有进行中的临时 Design 或 Plan。原 D031 拆至 C001，编号不复用。

## 5. 历史证据与归档入口

| 稳定记录 | 记录范围（均非规范性） |
| --- | --- |
| [E001](experiments/E001-pf-self-bootstrap-validation-contract.md) | PF 自举 full-repository validation contract 与固定报告检查点 |
| [E002](experiments/E002-pf-search-performance.md) | 2026-08-28 搜索性能计数，不能代表当前 HEAD 性能 |
| [E003](experiments/E003-requests-dependency-validation.md) | requests required-surface 修复前的 baseline 失败 |
| [E004](experiments/E004-requests-validation-surfaces.md) | required-surface、条件节点与搜索范围修复前后证据 |
| [E005](experiments/E005-pf-multi-resolution-search-simulation.md) | 纯算法模拟；支持 predecessor 重验，不证明真实 evaluator 耗时 |
| [E006](experiments/E006-requests-complete-search.md) | requests 两阶段完整 search 与 smoke/check 记录 |
| [E007](experiments/E007-mkdocs-baseline-and-build-failures.md) | MkDocs 基线/build 调查；后续 D036/P041 已完成，不表示重跑了 MkDocs search |

既有 Design/Plan/Review/Investigation 见[归档索引](archived/README.md)。
补充归档入口：[R007 历史优先级评审](archived/reviews/R007-pf-current-improvement-priorities.md)；
旧地址仅作导航，开放事项已移交 R006/R008/R010。既有归档索引保持原状。

## 6. 文档变更验证

核对现行 owner 与代码/公开 tests 的具体 seam，检查状态与开放项去向、相对链接和章节锚点、生成物漂移、
`git diff --check`，并逐字节确认既有归档未变。行为测试按改动范围运行；只改文档不宣称交付了行为修复。
审计证据与剩余实现问题见 R010，不在索引复制测试计数。

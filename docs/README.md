# PF 工程文档索引

- **状态：** 现行
- **最后核对：** 2026-09-05

本页只负责文档治理、契约所有权和导航。每条现行规则只有一个规范性所有者；其他文档只引用，不复述。代码与文档冲突时，同一变更必须修正实现或所有者文档。

## 目录

```text
README.md                  英文使用入口
README.zh.md               中文使用入口
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
- [E002](experiments/E002-pf-search-performance.md) 保存 2026-08-28 PF 自搜索的空间裁剪、verifier 成本、
  static region 与异常 source timeout 计数；当前瓶颈判断与优化候选由 R008 汇总。E002 是已完成的
  非规范性性能实验，不跟踪实施状态。
- [E003](experiments/E003-requests-dependency-validation.md) 记录对 `expirements/requests` 依次执行
  `pf smoke` / `pf check` / `pf search` 的结果：20 个 Cell 均在 baseline
  `resolve-environment` 因 `resolution-plan-invalid` 终止，未得到 verified floor。
  E003 是已完成的非规范性 dogfood 实验，不跟踪实施状态；产品判断由
  [R009](archived/reviews/R009-requests-harness-self-reference.md) 接收。

- [E004](experiments/E004-requests-validation-surfaces.md) 记录 D028 后 requests 的 15 个 required-surface
  Cells 的 smoke/check/search 实测终态，以及空 extra 修正后的 10 Cells planning；原自引用投影缺口
  已消失，后续失败按实际 authority 保存。
- [E005](experiments/E005-pf-multi-resolution-search-simulation.md) 完成 D031 第一阶段纯算法模拟：
  A/B/C/D 四组、独立穷举与当前算法差分对照表明 predecessor 重验减少合成探针，树本身收益不稳定；
  保存脚本、逐案结果和 trace，不代表真实 evaluator 耗时或产品实现验收。

## 开放事项与归档

- [D032](archived/designs/D032-pf-runtime-witness-stderr.md) /
  [P037](archived/plans/P037-pf-adapter-evidence-admission.md) 已完成并归档：witness stderr 诊断化、
  pytest summary 可选化、不适用 artifact 的 locator/哈希检查后置、ty 项目展示默认值覆盖。
  稳定规则已归并 D003/D004/D013/D014；逐项验收与工作区独立限制见 P037。

<a id="uv-resolution-output-completeness"></a>

- **独立开放项：uv 成功解析的日志完整性门槛（开放、待验证）。** 来源：
  [D032 §9](archived/designs/D032-pf-runtime-witness-stderr.md#9-待验证uv-成功解析的日志完整性门槛)。
  当前只确认 exit 0 后 stdout/stderr 不完整会在读取 pylock 前停止；后续独立验证完整可信 lock
  是否足以授权成功解析，并核对 D012/D007 的权威与完整性边界。该研究及其实施不属于 D032/P037，
  不阻塞它们开始实施、验收完成或归档。若后续形成变更，单独建立 Design/Plan；不放宽非零退出
  UNSAT classifier 所需的完整诊断。D032/P037 已归档，本项仍开放。

- [D031](designs/D031-pf-multi-resolution-coordinate-search.md) 为多分辨率坐标搜索草案：
  以 `search-resolution` 替换 `search-step`，统一层级 refinement、evaluator 缓存与跨 sweep 边界重验；
  保留虚拟 PASS sentinel 和非单调终止。第一阶段证据见 E005；真实集成未执行，树策略与实施范围待评审。
  尚未创建实施 Plan。
- [D030](archived/designs/D030-pf-search-space-dsl.md) / [P036](archived/plans/P036-pf-search-space-dsl.md) 已完成系列切片 DSL、
  条件默认、独立求值错误、报告策略/系列观测去重与离线授权；稳定规则由 D001/D002/D003/D006/D008/D014
  接管，实施与验收记录同步归档。
- 待办：独立 registry 分析 CLI Design，分析系列分布、稀疏程度与 release 数，比较 space/step 的范围和候选数并提供有依据的配置建议；来源见 [D030 §12](archived/designs/D030-pf-search-space-dsl.md#12-待办独立-registry-分析-cli-design)。不预留编号或创建 Plan；命令/数据/输出契约由后续 Design 确定，不纳入 D030 实施范围。分析不自动改写 pyproject 或搜索策略；发布分布不证明兼容性，候选数不等同 verifier 次数或耗时。

- [D029](archived/designs/D029-pf-conditional-resolution-projection.md) /
  [P035](archived/plans/P035-pf-conditional-resolution-projection.md) 已完成条件 resolution 节点投影修复；
  稳定规则由 D002/D012/D014 接管，E004 §10 保存三组日志回放和 requests py3.11+socks smoke PASS。

- [D028](archived/designs/D028-pf-validation-contract-surfaces.md) /
  [P034](archived/plans/P034-pf-validation-contract-surfaces.md) 已完成 required Cell surface、external
  harness satisfaction/current graph ceiling、默认 any/pytest 与 policy identity 隔离；稳定规则由
  D001/D002/D005/D012/D014 接管。[R009](archived/reviews/R009-requests-harness-self-reference.md) 已解决并同步归档。
- [R008](reviews/R008-pf-search-performance-review.md) 汇总当前搜索流程、E002 性能基线的适用边界、
  verifier 主导瓶颈，以及 region guidance、hints、per-key single-flight、源码物化、报告预检与
  xdist failed-set 早停候选；
  E002 保存历史运行证据，R007 继续保存全项目优先级。R008 是非规范性评审，不授权实施。
  搜索期 FailedCaseSet 拒绝预言与 pytest early-exit 已落地为默认内部策略；稳定规则由
  D001/D002/D003/D004/D005/D013 接管。历史 Design/Plan 见
  [D024](archived/designs/D024-pf-failed-case-pruning.md) /
  [P030](archived/plans/P030-pf-failed-case-pruning.md)。
- [D027](archived/designs/D027-pf-report-path-ownership.md) / [P033](archived/plans/P033-pf-report-path-ownership.md)
  已完成包默认 `package-floor.json` 路径规则单一 owner；稳定规则由 D002/D006 接管。
- [R007](reviews/R007-pf-current-improvement-priorities.md) 汇总当前产品、架构、性能与工程改进优先级，
  新增报告路径 owner 与自举 artifact 引用漂移等发现，并校准 composition、中断、搜索性能和资格候选的
  治理边界；CI coverage 门禁、host-partial 协议、command-scoped composition、Ctrl+C 终态与报告路径
  owner 已实施。
  E002/R006 继续保存各自详细证据。R007 是非规范性评审，不授权其余开放项。
- [R006](reviews/R006-pf-cli-system-review.md) 是当前 CLI 问题的单一汇总 Review：help/README 公开表面偏差、
  reason-aware incomplete 文案、jobs 契约歧义、host-partial 自动化协议、command-scoped composition 与
  Ctrl+C 终态已解决；它继续跟踪非 TTY 搜索遥测和 terminal-private result-card；它是非规范性评审，
  不授权实施。
- [D026](archived/designs/D026-pf-command-composition-and-interrupt.md) / [P032](archived/plans/P032-pf-command-composition-and-interrupt.md)
  已完成按命令装配 capability graph 与 Ctrl+C 退出 `130`；稳定规则由 D001/D002/D006/D007 接管。
- [D025](archived/designs/D025-pf-host-partial-protocol.md) / [P031](archived/plans/P031-pf-host-partial-protocol.md)
  已完成纯 host-partial search 退出 `0` 与 minimize merge 提示；稳定规则由 D001/D006 接管。
- [D024](archived/designs/D024-pf-failed-case-pruning.md) / [P030](archived/plans/P030-pf-failed-case-pruning.md)
  已完成搜索期 FailedCaseSet 拒绝预言与 pytest early-exit；稳定规则由 D001/D002/D003/D004/D005/D013
  接管。FailedCaseSet 第二段 wall-clock 收益未证实，R008 保持开放。
- [D023](archived/designs/D023-pf-configuration-model.md) / [P029](archived/plans/P029-pf-configuration-model.md)
  已完成配置模型收敛、uv ownership、分层并发与R006 jobs项修复；稳定规则由D001/D002/D003/D004/D006/
  D008/D012/D014接管。
- [D022](archived/designs/D022-pf-evaluation-seam.md) / [P028](archived/plans/P028-pf-evaluation-seam.md) 已完成
  评价 seam 收敛与 SearchCoordinator 测试替换；稳定规则由 D002/D003/D004 接管。
- [R005](archived/reviews/R005-pf-module-depth-review.md) 的 SourcePlan、WorkspaceInventory、Verification Run
  与评价 seam 轨均已解决；terminal-private result-card 轨移交 R006 后，Review 已同步归档。
- [D021](archived/designs/D021-pf-verification-run-request.md) / [P027](archived/plans/P027-pf-verification-run-request.md) 已完成 R005 轨 B 的实现与验证；稳定 request/Run/展示规则由 D002/D006/D008 接管。
- [D020](archived/designs/D020-pf-workspace-inventory.md) / [P026](archived/plans/P026-pf-workspace-inventory.md) 的 WorkspaceInventory 深化迁移已完成；稳定 interface 与 ownership 由 D002 接管。
- [D019](archived/designs/D019-pf-source-plan-depth.md) / [P025](archived/plans/P025-pf-source-plan-depth.md) 的 SourcePlan 深化迁移已完成；设计理由与完整验证证据见归档记录。
- D018/P024的诊断与结果命令卡片迁移已完成；稳定规则已由D001/D002/D005/D006/D008接管，
  D014的merge authority保持不变，设计理由和实施证据见归档索引。
- D017/P023的单target与workspace direct dependency迁移已完成；设计理由和实施证据见归档索引。
- [归档索引](archived/README.md) 记录已归并设计、已完成计划、已解决评审和已吸收探索。

新增文档先放入对应现行目录；Plan 完成、Design 被替代、Review 解决或 Investigation 被吸收后，在同一变更中移入 `archived/<type>/` 并修复引用。

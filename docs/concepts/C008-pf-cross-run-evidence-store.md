# C008 — PF 跨 Run 观察存储与准入

- **状态：** 开放
- **日期：** 2026-09-12
- **性质：** 非规范性 Concept；不授权跨 Run PASS 复用或生产实现
- **来源：** 接收 [C005 §4](../archived/concepts/C005-pf-check-first-lifecycle.md#4-统一观察缓存) 的独立开放问题
- **相关 owner：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、[D004](../designs/D004-pf-ty-enhancement.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、[D014](../designs/D014-pf-report-schema.md)
- **相关评审：** [R008](../reviews/R008-pf-search-performance-review.md)；历史 cache 否决不因本文失效

## 1. 独立问题

昂贵的 oracle observations 能否在不同 invocation 中积累，并在身份和准入闭合时复用？潜在消费者
包括 check/search、同快照重试、线性对照及 interaction replay。存储是历史经验记录；是否成为
当前直接证据是另一项决定，不能从“找到记录”直接推出“无需执行”。

当前 check 每次重新准备并执行完整 verifier；ty cache 限于 Run，公共 report 不是 Evaluation cache。
本构想不改变这些规则，也不要求 C006 的跨快照影响面分析先完成。第一批只研究同快照的复用，
生命周期、incremental apply、应用回执和历史回滚分别由现行 owner 或[研究目录](README.md) 接收。

## 2. 记录与准入分开

工作方向是一套观察记录，Run 内热层与持久层共享记录语义，避免平行短期/长期 cache API。
不同观察种类仍保留各自 identity、资格和生命周期，不强行把 registry、TyCheck、runtime outcome
折成一个短 key 或一个 PASS 类型，也不预设通用 cache 服务或最终 wire。

身份闭包至少调查：source snapshot/pyproject、Cell 与实际 Python/platform、SourcePlan、完整依赖
向量、实际解析与安装产物、verifier/外部工具、ExecutionPolicy，以及该观察所需的静态投影和 policy。
版本号、解析图或 artifact alternatives 集合不自动证明实际安装环境相同；工具、配置、进程上下文及
外部可变输入若无法闭合，必须 miss/重验或只作 hint，不能按字段看似相同准入。

| 记录种类 | 可以保存什么 | 当前复用所需判断 |
| --- | --- | --- |
| 完整 verifier PASS | 精确执行身份、完整命令终态与来源 | 是否具有当前完整 PASS 资格，不能用子集通过替代 |
| Rejection / failed cases | 拒绝事实、阶段、上下文、失败用例 | 是否仍是当前上下文的合格拒绝；不能迁移到另一向量自动拒绝 |
| INDETERMINATE | 当时无法判断的原因与执行事实 | 保存不等于未来跳过重试；瞬态性、失效与重验策略待证 |
| 原始静态观察 | TyCheck/Unavailable 与静态身份 | 只复用相应静态事实，不提升 runtime authority |
| Registry/artifact 观察 | 来源、时间、冻结候选与 artifact 身份 | 候选 freshness 与实际执行身份分别判断 |

旧快照记录至多按资格提供 hint；静态投影相同不证明动态 Evaluation 可复用。相同查询出现矛盾时保留
冲突与来源，不按最后写入覆盖事实。历史记录、准入结果和当前 Run 的证据引用需要可追溯地连接。

可写 PreparedEnvironment 不作为跨 invocation 准入记录；不借用已经运行 verifier 的可写 venv。
Store 不替代 report 的 apply authority、Journal 的执行审计或 Process Log，也不参与声明 mutation
的事务。消费者所需事实引用在记录清理后如何保留或 fail closed，须在持久化设计中明确。

## 3. 第一批验证范围

先选择同快照、同 Cell、同向量、同实际执行身份的重复工作，测 exact identity hit、真实可准入 hit、
miss 原因和节省的阶段成本。check→search 不保证天然同向量/同解析环境，应按实际身份统计，不能
把同一个项目版本当作命中。崩溃续跑仅复用已完整记录的观察，不把部分执行当作完成结果。

研究次序：

1. 经现有 evaluator 公开 seam 获取重复运行事实，列出身份闭包缺口及可能的 false hit。
2. 建立准入/拒绝矩阵，覆盖变更 source、Cell、工具、policy、安装产物、测试命令和静态投影。
3. 对真实重复工作测完整 verifier 成本、准入验证与存储成本、命中分布；分开冷暖基础设施缓存。
4. 确认足够收益后再确定记录边界、原子写入、并发、损坏处理、保留/清理及 Run 证据接线。

[E016](../experiments/E016-pf-linear-search-control.md) 可以用独立实验事实表做离线回放，不等待生产
Store，也不能把实验回放视为产品准入已交付。共享历史结果的暖启动收益与独立算法执行成本分报。

## 4. 进入 Design 的条件

身份闭包足以排除错误复用，准入矩阵涵盖 PASS/Rejection/INDETERMINATE 及静态事实的不同资格；
有真实可准入收益，失效、冲突、损坏与证据来源能够解释。届时明确 D001/D004 等现行跨 Run 禁止
规则的最小替代范围，并评审 report/reader/Journal 的接线，不把 Concept 的记录清单直接变成 schema。

若身份验证成本或不确定性使复用不值得，可收缩为历史提示/实验记录或暂缓；跨快照复用、测试级
失效、共享环境和可回滚历史均不作为本轮准入成功的前提。

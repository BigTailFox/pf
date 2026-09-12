# PF 研究目录

- **状态：** 现行
- **最后核对：** 2026-09-12

本页集中导航开放构想与暂缓问题，不定义行为契约或版本交付承诺。文档分类、状态和归档规则由
[工程文档索引](../README.md) 拥有；独立 Concept 保存可单独验证、推进或关闭的研究问题。
目录接收的开放项仍待证，不因原文归档而视为解决。编号永久保留、不复用。

## 1. 已交付基础

| 能力 | 现行 owner | 与未来构想的边界 |
| --- | --- | --- |
| check-first 生命周期 | [D001](../designs/D001-pf.md)、[D008](../designs/D008-pf-verification-run.md)、[D012](../designs/D012-pf-harness-relaxation.md) | 日常 check；search 用于接入与重定界；尚无 init/repair preset |
| 搜索空间 DSL、extra-policy | [D001](../designs/D001-pf.md)、[D037](../designs/D037-pf-candidate-search-policy.md) | 范围与 `search-resolution` 已有契约；不等于 lifecycle overlay |
| ty guidance 与 Run 内缓存 | [D004](../designs/D004-pf-ty-enhancement.md)、[D003](../designs/D003-pf-search-algorithm.md) | 静态事实只指导选点；不是跨 Run runtime evidence store |
| Cell 与阶段资源限制 | [D001](../designs/D001-pf.md)、[D008](../designs/D008-pf-verification-run.md) | max_cells 与共享 ty/test permit pools 已实现；不覆盖完整 oracle 工作调度 |

已交付内容只引用 owner，不保留平行开放 Concept。Check-first 的历史来源见
[C005](../archived/concepts/C005-pf-check-first-lifecycle.md) 与
[D044](../archived/designs/D044-pf-check-first-minimal-verification.md)。

## 2. 独立开放构想

| 文档 | 独立问题 | 当前判断与下一步 |
| --- | --- | --- |
| [C007](C007-pf-search-lifecycle-presets.md) | init/repair 意图如何与已有候选策略组合 | 近期产品候选；先闭合默认、交集、空空间与声明边界，再决定进入 Design |
| [C004](C004-pf-evidence-respecting-optimistic-monotone-search.md) | 已见反例后的搜索与结果语义，以及主动探索是否值得 | 近期研究；暂缓 Design，用 E016 真实固定 Slice 数据补充 E011 合成证据 |
| [C008](C008-pf-cross-run-evidence-store.md) | 历史观察何时可在当前执行中准入 | 近期基础设施研究；先证同快照身份闭包与复用收益，不要求跨快照复用 |
| [C009](C009-pf-contextual-interaction-discovery.md) | context 改变后的重新搜索与小范围联合探索 | 独立研究；先比较顺序/延后重试，再评价 block 与 coarse-to-refine |
| [C006](C006-pf-test-dependency-association.md) | 测试/源码与依赖的影响面和失效 | 后续研究；不改变 PASS 资格，不作为 C007/C008 的前置 |

三条问题轴为生命周期语义、固定 context 的单坐标搜索、跨坐标 interaction。Evidence Store、
资源调度和测试关联分别影响证据复用、执行安排及工作量；不能以性能理由越过证据准入。
“局部非单调、稀疏交互且 component 小”仍是工作假设，E014 的个案不能证明总体分布。

[E016 线性对照协议](../experiments/E016-pf-linear-search-control.md) 是实验方法，不另占 Concept：
固定 Slice 扫描用于测 hole；独立整轮算法对照用于测路径与成本。当前只有协议，尚未执行。
近期顺序是 C007 收敛、E016 采集准备、C008 准入验证、据数据选择 C004 范围及 C009 最小对照。
这些优先级不承诺全部进入 V1；interaction、关联分析与更广泛调度保留后续研究空间。

## 3. 暂缓与工程调查

每项保留问题、证据缺口和重启条件；具体研究启动后再决定是否需要独立文档。

<a id="deferred-multi-resolution"></a>

### 普通 floor 多分辨率搜索

接收 [C001](../archived/concepts/C001-pf-multi-resolution-coordinate-search.md) 的开放问题。
[E005](../experiments/E005-pf-multi-resolution-search-simulation.md) 未证明树在重验与等价缓存之上的
稳定净收益。重启需要同候选、同 oracle 条件下的增量成本收益，包括内存、报告与执行成本；
不把 major/minor/patch 分层本身当作更强单调性。Interaction localization 的分层探索由 C009 跟踪，
不复活原 D031 或将 C001 草案接口视为新目标。

<a id="deferred-registry-analysis"></a>

### Registry 发布分布分析

接收 [C002](../archived/concepts/C002-pf-registry-analysis-cli.md)。先用真实发布观测证明展示能支持
明确的搜索空间/粒度配置决策，再决定是否需要独立 CLI。候选数量不等于 verifier 成本，版本分布
不证明兼容性；命令、输出与观测保存均未选定。

<a id="deferred-resolution-output"></a>

### 成功 resolve 的日志完整性

接收 [C003](../archived/concepts/C003-pf-resolution-output-completeness.md) 的工程调查。
先经公开 resolve seam 复现正常 terminal、可信完整 lock、仅诊断流不完整的情形，并证明日志失败
与 lock/terminal authority 可分离，再考虑修改 D012/D007 准入。尚无完整路径证据，不能直接认定
现行拒绝是 bug；失败路径的 UNSAT 诊断资格不随之放宽。

<a id="deferred-incremental-apply"></a>

### 增量 search / apply

接收 [C005 §5](../archived/concepts/C005-pf-check-first-lifecycle.md#5-增量搜索与只放松增量-apply)。
新 extra 的矩阵覆盖与同 Cell 新坐标定界分别研究；先证明部分覆盖如何授权写入，并保留未覆盖声明。
不默认依赖本机 apply 历史，不放松普通 source/dependency drift。重启条件是具体用户场景、
覆盖证明及仅靠当前声明/本次证据能否授权的结论。C007 的 repair 不等同增量 apply。

<a id="deferred-application-history"></a>

### 应用记录、历史与回滚

接收 [C005 §6](../archived/concepts/C005-pf-check-first-lifecycle.md#6-可选应用记录历史与回滚)。
历史展示、增量 provenance、恢复前态是三个不同消费者。先证明谁需要记录、缺失后果与一致性要求，
再决定日志、回执或历史的形式；不默认扩展声明/报告事务。现有单次 apply 安全与恢复由 D001/D002
拥有，完整 search evidence 不与临时声明 mutation 合并成新事务系统。

<a id="deferred-work-scheduling"></a>

### 超出现有 permit pools 的统一工作调度

现有 Cell 与 ty/test 资源限制保持；未来才研究 resolve/install/完整 oracle、interaction 工作生成、
背压、公平性与预算的统一安排。重启需要当前阶段耗时、排队及资源瓶颈证据，并说明现有 pools
为何不足。算法生成候选和资源安排可分开，不以 nested 并发或完整新 Scheduler 作为算法前置。
现行性能开放项仍由 [R008](../reviews/R008-pf-search-performance-review.md) 跟踪，本项不接管其整改。

## 4. 历史与证据

C001–C003、C005 已关闭独立跟踪并归档；开放问题去向见上文及 C008。
[归档索引](../archived/README.md) 保存历史入口。
[E011](../experiments/E011-pf-optimistic-search-exploration.md) 与
[E012](../experiments/E012-flask-complete-search.md)、
[E013](../experiments/E013-requests-complete-search.md)、
[E014](../experiments/E014-mkdocs-complete-search.md)、
[E015](../experiments/E015-pf-self-bootstrap-complete-search.md) 保留当时证据，不回写新实验结果。

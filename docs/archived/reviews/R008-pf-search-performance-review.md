# R008 — PF 搜索流程与性能优化评审

- **状态：** 已归档
- **日期：** 2026-09-08（全文重评；初评 2026-09-04）
- **性质：** 非规范性性能与架构评审；不定义命令、算法、Schema 或 module interface，不授权实施
- **对照：** 当前 HEAD；算法 owner 为 [D003](../../designs/D003-pf-search-algorithm.md) `direct-first-coordinate-guidance-v1`
- **输入：** 现行 D003/D012 与 `SearchCoordinator` / `CoordinateSearch` / prepare / verifier 实现；
  [E002](../../experiments/E002-pf-search-performance.md)（2026-08-28，region/witness 时代）；
  [E006](../../experiments/E006-requests-complete-search.md)（2026-09-05–06，仍含 witness）；
  [E009](../../experiments/E009-mkdocs-static-guidance.md)（2026-09-08，现行 guidance 权限）；
  [I002](../investigations/I002-pf-self-search-py310-static-collection.md)（2026-09-09，PF 自搜索 3.10 intern / `unclosed-symlink`）
- **现行契约所有者：** [D001](../../designs/D001-pf.md)、
  [D002](../../designs/D002-pf-implementation.md)、
  [D003](../../designs/D003-pf-search-algorithm.md)、
  [D004](../../designs/D004-pf-ty-enhancement.md)、
  [D005](../../designs/D005-pf-failure-and-diagnose.md)、
  [D008](../../designs/D008-pf-verification-run.md)、
  [D012](../../designs/D012-pf-harness-relaxation.md)、
  [D013](../../designs/D013-pf-pytest-observer.md)、
  [D014](../../designs/D014-pf-report-schema.md)
- **与既有文档的关系：** 初评正文按当时 region / witness / StaticOnlyEvidence 模型写成。
  D033 predecessor 重验、D035 project-only prepare、D036 正常非零分类、D038 删除 witness 与
  region 权限后，那套流程不再是现行算法。本文重写现行结论与开放项；不回写 E002/E006 的历史计数。
  FailedCaseSet 拒绝预言已落地，稳定规则由 D001/D002/D003/D004/D005/D013 拥有。


## 2026-09-12 归档交接

本次对照 `05dbf604812e37bafdf96bd6247a1665bc3e83c7` 静态核对，未重跑性能实验。
§5 的 hints、跨 key I/O 重叠、materialize、xdist 收益假设及 §9 的分阶段基线，全部由
[C010](../../concepts/C010-pf-search-efficiency.md) 接收；不沿用 P1/P2 排序或预选实现架构。
非 TTY 展示仍由 [R006](../../reviews/R006-pf-cli-system-review.md) 跟踪。

§1/§4 的“当前 verifier 主导”缺少当前分阶段对照，不作为现行判断。E012–E015 已有更新运行，
仍不能跨配置推算收益。I002 的文件树采集与公开静态 intern 已被 D043/P045 替换；
旧计数只保留历史意义。§6 已解决/撤销项不复活；§10 的 D044/C005 交接沿用其最终状态。
以下保留归档前正文、日期和当时结论；其中“当前 HEAD”均不是本次或未来 HEAD 的资格说明。

## 1. 现行结论

搜索仍然是有限、确定、会终止的坐标下降，主导墙钟成本仍是用户配置的完整 `test-command`。
组合空间裁剪继续有效：CoordinateSearch 不会枚举笛卡尔积。这是算法不变量，不依赖 E002 的具体次数。

2026-09-04 初评把「让 static region 更早免掉 pytest」列为 P1。该机制已不存在。
[D038](../designs/D038-pf-static-guidance-authority.md) 删除了 region、带 disposition 的
static-only evidence、promotion 定界和 witness 拒绝权限。现行静态阶段只产出 hint，不能排除候选、
不能更新兼容性边界。floor / predecessor / final 仍须当前 context 的直接 runtime 证据。
因此 E002 的「18/121 个 search-only 向量免 pytest、约 14.9%」只描述已删除路径，不能当作当前收益或目标。

当前 HEAD 上最接近现行算法的真实墙钟是 E009 的 MkDocs 五 Cell search：进程 2,454,196 ms
（约 41 分钟）。Cell 卡片 3.8 / 3.9 / 3.11 / 3.12 为 7:05–9:25，3.10 为 37:54。
该差未归因于 guidance、锁或探针数；E009 也没有逐探针的 prepare / ty / verifier 分解。
四个 Cell 的静态 scope `facts=0`，只有一个 Cell 形成 40 条 facts / 13 次静态搜索。
S_hi 在该隔离树不可用。受控 `measure_d038_guidance.py` 在 scripted adapter 上六组
guided/mechanical 的 floor 全部相同；有的用例 guided 的 verifier 次数还多于 mechanical。
静态 guidance 目前不是已证实的 pytest 削减杠杆。

**2026-09-09：** [I002](../investigations/I002-pf-self-search-py310-static-collection.md)
在 PF 自搜索、单次 `test-command` <20s 的前提下，把 3.10 与 3.11/3.12 的墙钟差归因于
`S_hi` 采集成功（散列并 intern 解释器/venv 文件树）对 3.11/3.12 `unclosed-symlink` 导致整格
`anchor-unavailable`。oracle 次数没有差三倍；`json.dumps` 约 0.2s，`ReportStore.read` 在
42MB intern 报告上约 37s。这不回写 E009 的 MkDocs 计数，也不把「长 test-command 仍是一般主导项」
改成新规范；它说明当前 HEAD 基线必须把静态 subject 是否闭合计入分阶段墙钟。

仍成立、且未接线的探针顺序杠杆是 `CoordinateSearch.minimize(..., hints=)`：产品
`SearchCoordinator` 仍不传入 hints，无静态 hint 时首次 oracle 探针仍是窗口内最早候选。
同一 invocation 内后续 sweep 的 predecessor 重验已经由 D033 落地，优先于外部 hint 与静态 hint。

开放项不再包含 region 改造或 report preflight。需要新的当前 HEAD 分阶段基线之后，才能在
hints、single-flight、materialize、xdist failed-set 之间用墙钟做取舍。

## 2. 现行搜索流程

现行时序只见 D003/D012；此处只标热点，不复制规则。

```text
SearchCommandWorkflow
  -> 一次 project load、SourceSnapshot、SEARCH SourcePlan、RunLimits
  -> VerificationRunner 按 max-cells 调度 host Cells（ty-jobs / test-jobs 分 stage）
       -> 每 Cell 串行 SearchCoordinator
            -> HighestVersionVerifier：prepare + ty + 完整 test-command
            -> 冻结 CandidateSnapshots
            -> CoordinateSearch 从 baseline 做坐标下降，直到一轮无变化
                 -> 已有直接证据可定界：跳过静态阶段（direct-bound）
                 -> 否则：历史 predecessor 在当前完整 context 重验
                 -> 仍未定界：打开独立静态窗口（只 ty，不跑 verifier）→ StaticHint 或 NO_HINT
                 -> oracle：直接 Probe evidence；无 hint 则最早候选 / 二分
                 -> floor 与 predecessor 必须是当前 context 的直接 runtime 观察
  -> 源码未漂移后构建报告；ReportStore.update_path 读/合并已有报告
```

不再存在 static fixpoint、`V_static`、region/promotion、witness rejection 或第二轮 dynamic search。

### 2.1 Run 与 Cell

一次 invocation 一份 snapshot 与一份 SEARCH SourcePlan。Cells 可并行；同一 Cell 的探针串行，
以保持坐标、history、静态 slice 与终止顺序确定。`max-cells=auto` 为逻辑 CPU 数。

### 2.2 Baseline 与候选

每个 Cell 先取得最高合格向量的直接完整 PASS，才冻结候选并进入搜索。Candidate Simple JSON 按
dependency/source 在 invocation 内跨 Cell 缓存；资格与兼容性结论仍按 Cell 解释。

### 2.3 每个坐标

1. 消费已有直接观察；能定界则 `StaticPhaseSkip(direct-bound)`，不获取静态 anchor。
2. 若上一 sweep 的 history floor 仍是 current，且 predecessor 仍是 `C[d]` 中的直接前驱，
   在**当前完整 context** 重验该 predecessor（D033）。Rejection 立即建界；PASS 则把上界降到
   predecessor 后继续向下。
3. 否则打开至多一次 `open_static_slice`，纯静态二分，得到 suspect / clean_neighbor 或 NO_HINT。
4. oracle 继续：有效静态 suspect 优先于外部 hint，外部 hint 优先于最早候选。Hint 只改顺序。
5. 窗口距离 ≤ 8 升序线性，更大窗口 lower-bound 二分。静态二分有独立对数上限，其 bracket
   不传入 oracle 窗口。
6. 提交前 floor 与 predecessor 必须再取当前 context 的直接 runtime 证据。

同一 Proposal 的静态采集若留下未跑 verifier 的 `PreparedEnvironment`，随后 oracle 仍可复用该
lifecycle（D022）。不同 Proposal 不原地升降级，不共享已跑 verifier 的 venv。
无活跃 external harness 时 prepare 走 project-only 安装，不再无条件二次 environment resolve（D035）。

### 2.4 产品路径未使用的 hints

`CoordinateSearch.minimize(..., hints=())` 仍接受每坐标 hint：选不高于 hint 的最新窗口候选作
首次探针。`SearchCoordinator.search` 调用 `minimize` 时不传 `hints`，因此产品路径没有跨运行
floor seed。Run 内 history 重验不是这个参数，已经生效。

已有 report 不是跨运行 Evaluation cache。`ReportStore.update_path` 把非法 existing 当缺席并写
replacement（D014 §5）。

## 3. 证据分层

性能计数属于运行证据，不进入 report identity。下列实验口径不可互换。

| 记录 | 算法口径 | 能支持的判断 | 不能支持的判断 |
| --- | --- | --- | --- |
| E002，2026-08-28，PF 自搜索约 37 分钟 | region + witness；随后 D022 才修 promotion 重复 prepare | 组合空间裁剪有效（3.11/3.12 各 54 唯一向量 vs 已观察笛卡尔积 114,048）；当时 106 次 configured verifier 累计 3,470.40 s，中位 36.22 s，P90 39.34 s，是**当时**主导成本 | 当前 verifier 次数、static 免 pytest 比例、promotion 重复 prepare、现行 guidance 收益 |
| E006，2026-09-05–06，requests 10 Cell | 第一阶段仍有 `RUNTIME_INTERFACE_MISSING` / witness；第二阶段在 D033 之后，仍早于 D038 | pytest 仍是第三方仓库上的贵 oracle；第一阶段最长 Cell 24m11s、330 runtime evaluations；第二阶段最长 14m37s、145 runtime evaluations；两次 invocation 的 coarse-to-fine 可行 | 现行无 witness 路径的 verifier 次数；把 40 次 witness 拒绝外推为 D038 之后仍会跳过 pytest |
| E009，2026-09-08，MkDocs 5 Cell | 现行 D038：无 witness，静态只做 hint | 现行算法能跑完 13–14 依赖矩阵；墙钟约 41 分钟；check 中原命令 unittest 单次约 9.4–9.6 s；四个 Cell 静态事实为空；scripted guided/mechanical floor 相同 | 逐探针 prepare/ty/verifier 分解；把 3.10 的 37:54 解释成 guidance 或锁；E008 的 18.9% 固定算例 |

E009 的 `test-command` 是 `python -m unittest ...`，不是 direct pytest，FailedCaseSet 拒绝预言
在该次运行中不会触发。E002/E006 的 pytest 命中率仍然缺失。

## 4. 瓶颈判断

| 层级 | 现行判断 | 依据 |
| --- | --- | --- |
| 组合空间 | 不是主因 | D003 坐标下降与有限候选；E002 的 54 vs 114,048 仍说明该裁剪，不是当前次数 |
| 完整 verifier | 仍是主导墙钟成本 | E002 当时定量；E006 每 Cell 17–24 分钟级 pytest search；E009 原命令 unittest 单次已约 9.5 s，search 墙钟 41 分钟 |
| 静态 guidance | 不削减 oracle 权限；E009 多数 Cell 未形成静态事实；受控测量未显示稳定少跑 verifier | E009 static_scopes 与 measurement.json；D003 §3.4 / §6 |
| 同 Proposal 再 prepare | 已不是开放瓶颈 | D022/P028 |
| 无 harness 的二次 resolve | 已不是无条件成本 | D035 project-only prepare |
| 后续 sweep 重复定界 | 部分已由 history 重验吸收 | D033；无当前 HEAD 的「因此少了几次 verifier」对照 |
| Proposal 环境 | 每个唯一 Proposal 仍独立 materialize / resolve / venv / sync | 结构仍在；E002/E009 都没有 copytree 分段耗时 |
| 并发 | Cell 可并行、Cell 内串行；candidate HTTP、CandidateBuilder.query、`_resolve_once` 仍是一把锁包住执行 | 源码仍如此；锁等待未计入 E009 |
| FailedCaseSet × xdist | serial pytest 已有拒绝预言；xdist controller 仍无 `session.items` 权威，failed-set 回退原命令 | `ConfiguredVerifier._selection_decision` 与 `_pytest_observer._record_collection` |
| 坏报告晚失败 | 不再是「跑完整次再因非法 JSON 失败」 | D014 `update_path` 把非法 existing 当缺席 |

## 5. 开放候选

### 5.1 P1：把 hints 接入产品路径

Hints 仍是 CoordinateSearch 的现行 interface，产品调用方仍不提供值。最有希望的来源仍是可读旧报告中
相同 Cell/coordinate 的历史 floor：只做 run-local 调度 seed，当前 invocation 必须重做全部权威证据。
版本不在当前 CandidateSnapshot、Cell 不匹配或报告不可读时应忽略或按明确规则早失败，不能把历史
floor 当成兼容性事实或硬下界。

兄弟 Cell 已提交的 floor 仍低于旧报告：Cells 通常同时启动。不得为获得 sibling hint 串行化 Cells，
也不得让调度竞态改变最终证据。

这会把 report/run 事实传入 SearchCoordinator。保持小的 immutable value flow，不新增
`HintProvider`。删除独立 hint module 后若复杂度不回到多个调用方，就不要建新 seam。

D033 的 history 重验已经覆盖**同一次** search 的后续 sweep，不替代跨运行 hints。
在没有当前 HEAD 的「最早候选首探浪费了多少 verifier」计数前，不能宣称墙钟收益。

### 5.2 P2：把全局 I/O 锁收窄为 per-key single-flight

仍成立，源码未改：

- `CandidateBuilder` 在一把 `_query_lock` 内调用 provider；
- uv candidate adapter 在一把 `_candidate_lock` 内 `urlopen`；
- `EnvironmentFactory._resolve_once` 在一把 `_plan_lock` 内执行完整 resolve。

同 key 只计算一次是对的；不同 key 被同一把锁堵住。应在各自 owner 内改为同 key 单飞、不同 key
有界重叠。不新建 public seam，不改变 probe 数或 report。测试从现有 interface 证明去重、重叠、
失败不永久占位、并发上限。E009 的 3.10 与其余 Cell 墙钟差不能在计入 lock wait 之前归到这项。

### 5.3 P2：减少 Proposal 源码物化成本

`SourceSnapshot.materialize()` 仍是每个 Proposal 一次 `shutil.copytree(..., symlinks=True)`。
先把 materialize duration、文件数与逻辑字节数纳入当前基线；只有真实仓库证明占比显著后，再评估
reflink/CoW 或 immutable base 加 proposal overlay。必须保持独立可写、symlink、mode、排除规则、
snapshot identity 与 cleanup；不支持 reflink 时安全回退。Hardlink 会让 Proposal 写入污染基线。

### 5.4 P2：xdist `test-command` 的 failed-set Rejection

serial/controller 的 collection 证明仍来自 `pytest_collection_finish` 之后的 `session.items`。
pytest-xdist 的 controller 不做这份收集时，controller 记录为空或不可用，`_selection_decision`
不应用 pruning，回退原命令。这只影响 `test-command` 本身走 xdist 的项目。

它不减少探针次数，只让部分 Rejection 有机会停在 failed-set 的一个 child process。没有 xdist
套件上的命中率与墙钟对照前，不能排到 hints 之前。

后续 Design 须先定义 xdist 下何谓 controller 侧 collection 证明，并保持：PASS 只来自不收窄用户
collection 的原命令；不得用 worker 列表并集在 Design 改写前授权 Rejection；不得改回自写 argv
parser。归属 D002 `ConfiguredVerifier` 与 D013，不进入 CoordinateSearch，不新增 policy identity。

## 6. 已落地或已撤销（不再作为开放性能项）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 初评 §4.1 region / 放宽连续 fingerprint 以免 pytest | **撤销** | 与 D038 相反。静态不能再给候选 disposition。若将来研究「hint 是否减少 oracle 探针」，那是新的 D003 问题，须用当前算法的探针计数，不能复活 region |
| 同 Proposal static→runtime 重复 prepare | 已落地 | D022/P028 |
| 后续 sweep predecessor 重验 | 已落地 | D033；E006 第二阶段不能单独当作其墙钟验收 |
| 无 harness 时两次 resolve/venv | 已落地为 project-only | D035 |
| FailedCaseSet 拒绝预言 | 已落地为默认内部策略 | 无命中率则不记为已证实的第二段收益；unittest `test-command` 不走该路径 |
| 坏报告晚失败 / preflight | **撤销** | 初评前提已失效 |
| witness 提前拒绝 | **正确性删除，不是性能回归项** | E006 的 `RUNTIME_INTERFACE_MISSING` 次数不能当作 D038 之后仍应跳过 pytest 的配额 |

## 7. 不采用的方向

- 改写用户 `test-command`、隐式 testmon / pytest `--lf`、跨运行 last-failed；两段 pytest 拼接冒充一次原命令 PASS；
- 把静态 hint、跨 Cell 结果或旧报告结果直接当作 floor、predecessor 或 final；
- 恢复 witness 或 region 作为「性能优化」；
- 跨运行 Evaluation cache，或不同 Proposal 共用已跑 verifier 的可写环境；
- 为 sibling hint 等待另一个 Cell，或在单 Cell 内并行、乱序执行状态相关探针；
- 以缩小 `search-space`、改变 `search-resolution` 或减少目标 Cell 冒充同一契约下的性能提升；
- 没有 materialize 分段数据就引入复杂 overlay filesystem；
- 建立通用 cache、hint manager 或 environment service；
- 把 C001 树搜索当作默认性能方案。E005 未证明相对 predecessor 重验的增量；E006 的两次独立
  invocation coarse-to-fine 是用户工作流，不是一次 search 内的树。

## 8. Module 与 seam

高层 ownership 不需要为了性能重排：

- `CoordinateSearch` 拥有 hint、probe order、history 重验、boundary、sweep 与终止；
- `SearchCoordinator` / `_ProposalRunner` 把 baseline、候选、静态 slice 与 runtime 接到该 interface；
- `EnvironmentFactory` 拥有 prepare；`StaticEvaluator` 拥有原始 ty 事实；`RuntimeEvaluator` 拥有 verifier；
- `VerificationRunner` / `Scheduler` 拥有跨 Cell 调度，不应学习坐标或 hint；
- `ReportStore` 拥有 reader/update；workflow 只决定何时调用。

优化应加深这些 module 的内部 locality。Hints 接线若把 report floor 传入 Coordinator，保持只读
value，不要新 facade。per-key single-flight 若保持现有 interface 与可观察结果，可作为 owner
implementation 内的修复。Region 类 D003 变更必须先有新的、与现行「静态无 disposition」一致的 Design。

## 9. 建议顺序

1. 在当前 HEAD 用固定 source、candidate cutoff、Cell 集合与缓存条件记录新基线，至少包括：
   每 Cell 的候选数、sweep 数、唯一向量、prepare、静态 probe、oracle verifier 次数与耗时；
   materialize / resolve / lock wait；wall-clock critical path；冷/热 registry。
   E009 的 MkDocs 矩阵适合作对照，但必须补上 E009 没有的分阶段计数，并单独标注 3.10 的 37:54。
2. 用固定 trace 比较「无 hints 的最早候选首探」与「旧 floor hint」，看 verifier 次数与结果等价性。
   不要再用「放宽 region 以免 pytest」作对照。
3. 有数据后再决定是否接受 hints 的 D003/D002 接线 Design。FailedCaseSet 已落地，不与 hints 捆一次改动。
4. per-key single-flight 可独立实施，但应带锁等待计数，避免用 E009 的 Cell 墙钟差充当证明。
5. materialize 与 xdist failed-set 分别按实测占比推进。
6. 非 TTY 搜索活动仍由 [R006 §5.2](../../reviews/R006-pf-cli-system-review.md#52-来源-e002-53非-tty-搜索活动遥测) 拥有。

若候选不能减少 configured verifier 次数或 wall-clock critical path，或需要削弱 runtime authority、
环境隔离与确定终止，则停止该方向。本文不构成实现授权。

## 10. 后续状态（2026-09-08）

[C005](../concepts/C005-pf-check-first-lifecycle.md) 另切「统一观察存储 + 准入策略」：命中不等于
当前快照的 PASS 权威，可写环境仍不跨 invocation 借用。§7 否决的仍是跳过当前契约 runtime 权威
的 cache，以及通用 cache/hint/environment 服务。测试级关联与 testmon 风格影响面见
[C006](../../concepts/C006-pf-test-dependency-association.md)，不在本评审范围。

**2026-09-10：** 同契约 `C` 上的直接观察准入已起草为临时
[D044](../designs/D044-pf-check-first-minimal-verification.md)（草案）。D044 接受前 §7
仍然适用；D044 把「当前契约」写成身份闭合的快照/Cell/SourcePlan/ExecutionPolicy/解析图，
并继续禁止共享可写环境与通用 cache 服务。跨运行 hints 仍由本评审 §5.1 跟踪，不并入 D044。

**2026-09-10 范围收敛：** 上段记录 D044 的初稿方向。后续讨论已将跨 Run 观察准入移回 C005
待证范围；[D044](../designs/D044-pf-check-first-minimal-verification.md) 现仅定义 check 稳态、
smoke/check 最小验证序列及有限 apply 回执/声明与报告事务。每次 check 都实际运行完整 verifier，
不保存成功 identity，不引入 Git、观察存储或跨 Run 复用；本评审 §7 的非目标保持原约束。

**2026-09-11 接受状态：** D044 在补齐验证命令范围与阶段间中断语义后已接受待实施；
[P048](../plans/P048-pf-check-first-minimal-verification.md) 已起草，命令与回执/事务分轴验收。
本轮未授权生产实现，不改变上述跨 Run 缓存的范围结论。

**2026-09-11 后续收缩：** 回执的现有消费者仅为有限历史展示，不是 check/search/apply 准入前提。
用户决定从 D044/P048 撤下回执、专属 identity 及报告参与 apply 事务的扩展，移回 C005 待证；
原有 apply 授权、NOOP、安全写入及恢复保持。D044 的已接受目标现仅为 check 稳态与
smoke/check 最小验证序列，P048 相应收缩为三切片；仍未授权生产实现。上段保留此前接受范围的记录。

**2026-09-11 完成：** D044/P048 已实施、验收并归档；稳定规则由 D001/D002/D004/D006/D008/D012
接管。跨 Run 观察复用仍由 C005 待证，本评审 §7 非目标保持。

**2026-09-12 文档交接：** C005 已归档，其跨 Run 观察存储与准入由
[C008](../../concepts/C008-pf-cross-run-evidence-store.md) 接收；增量 apply、应用记录与历史回滚见
[研究目录](../../concepts/README.md#deferred-incremental-apply)。上文日期段保留当时状态，
本次不改变 §7 的现行边界，也不授权跨 Run 复用。

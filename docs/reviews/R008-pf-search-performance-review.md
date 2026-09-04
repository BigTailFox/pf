# R008 — PF 搜索流程与性能优化评审

- **状态：** 开放
- **日期：** 2026-09-04
- **性质：** 非规范性性能与架构评审；不定义命令、算法、Schema 或 module interface，不授权实施
- **对照：** 当前 `main`
- **输入：** [E002](../experiments/E002-pf-search-performance.md) 的历史运行证据、
  [R007 §7.1–7.2](R007-pf-current-improvement-priorities.md#7-既有开放轨继续跟踪不重复新开)、
  当前实现与本轮汇总评审意见
- **现行契约所有者：** [D001](../designs/D001-pf.md)、
  [D002](../designs/D002-pf-implementation.md)、
  [D003](../designs/D003-pf-search-algorithm.md)、
  [D004](../designs/D004-pf-ty-enhancement.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、
  [D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、
  [D013](../designs/D013-pf-pytest-observer.md)、
  [D014](../designs/D014-pf-report-schema.md)
- **与既有文档的关系：** E002 保存 2026-08-28 运行的原始计数与当时结论；R007 继续保存
  全项目优先级。本文只汇总当前搜索流程、瓶颈判断、候选排序与治理边界，不把历史基线改写成当前性能实测。
  搜索期 FailedCaseSet 拒绝预言与 pytest early-exit 已落地为默认内部策略；完成后的稳定规则由
  D001/D002/D003/D004/D005/D013 拥有。历史见
  [D024](../archived/designs/D024-pf-failed-case-pruning.md) 与
  [P030](../archived/plans/P030-pf-failed-case-pruning.md)。本文仍不把协议测试描述为已证实的第二段 wall-clock 收益。

## 1. 最终结论

PF 搜索的主导瓶颈是用户配置的完整 `test-command`，通常是整份 pytest；不是死循环，也不是组合空间
失控。E002 中 Python 3.11/3.12 各只访问 54 个唯一向量，而这些已观察坐标版本的笛卡尔积已经达到
114,048。`CoordinateSearch` 的坐标下降、二分/小窗口线性定位、单调下降与 invocation-local cache 已经
有效避免枚举组合空间。

真正昂贵的是每个需要直接 runtime 证据的 Proposal 仍须运行权威 verifier。E002 的 106 次 configured
verifier 累计 3,470.40 秒，中位 36.22 秒，P90 39.34 秒；static region 只让 18 个 search-only 唯一
向量免于运行 pytest，约占 14.9%。因此最高杠杆仍是减少进入 verifier 的探针数，同时继续直接认证最终
floor 与 predecessor。

对带 active dependency 的 search runtime probe/promotion，默认 reject-oracle 会先用已知失败
nodeid 做拒绝预言，并在首败后提前结束；PASS、current、floor 与 final 仍只来自不收窄用户 collection
的原命令阶段 `NormalExit(0)`。该策略不新增 evaluation policy identity。early-exit 的 wall-clock
对照见实施证据；没有 FailedCaseSet 命中率数据时，不得把协议测试写成已证实的第二段收益。

每个唯一 Proposal 还需要独立的可写源码副本、resolution、venv、sync 与静态评价。这是明确的重复
结构成本，但 E002 没有分离记录 `copytree` 等进程内耗时，不能据此声称它已经是第二大 wall-clock
来源。D022/P028 已解决同一 Proposal 从 static-only promotion 到 runtime 时的重复 prepare；不同
Proposal 的环境隔离仍是现行正确性要求。

## 2. 现行搜索流程

```text
SearchCommandWorkflow
  -> 加载目标项目和 effective config
  -> 建立一次 immutable SourceSnapshot 与 SEARCH SourcePlan
  -> VerificationRunner 按 max-cells 调度 host Cells
       -> 每个 Cell 内串行执行 SearchCoordinator
            -> highest baseline：prepare + ty + 完整 test-command
            -> 冻结该 Cell 的 CandidateSnapshots
            -> CoordinateSearch 从 highest vector 做多轮坐标下降
                 -> 每个新 Proposal：materialize + resolve + venv/sync + ty
                 -> 有合法 static-region guidance：保存 static-only evidence
                 -> 否则：运行 witness / 完整 test-command
                 -> floor 与 predecessor：promote 为直接 runtime evidence
            -> 一轮没有坐标下降后提交 final vector
  -> 复查 source snapshot 未漂移
  -> build report；最后才读取、合并并写入已有 report
```

### 2.1 Run 与 Cell

`SearchCommandWorkflow` 只加载一次项目、建立一次源快照，并把 immutable package、snapshot、SourcePlan
与运行限制交给 `VerificationRunner`。Cells 可以并行，`max-cells=auto` 解析为逻辑 CPU 数；`ty-jobs`
和 `test-jobs` 分别限制两个昂贵 stage。同一 Cell 的探针保持串行，以维持确定的坐标状态、region、
promotion 与终止顺序。

### 2.2 Baseline 与候选冻结

每个 Cell 先按 SEARCH 源解析最高合格版本，运行完整静态与 runtime 评价。只有 direct PASS 才冻结
static baseline 并进入搜索；该 baseline 的环境随后关闭。

候选按受管直接依赖和规范顺序冻结为 Cell-specific `CandidateSnapshot`。Simple JSON 原始响应按
dependency/source 在一次 invocation 内跨 Cell 缓存；`requires-python`、wheel tag、specifier 与搜索策略
等资格仍按 Cell 解释，不能把一个 Cell 的候选或兼容性结论直接复制给另一个 Cell。

### 2.3 坐标下降与单个探针

依赖按 canonical name 顺序逐坐标定界。首次 probe 默认选择最早候选；窗口距离不超过 8 时升序线性
扫描，更大窗口使用 lower-bound 二分。后续坐标下降会改变早先 Slice，因此必须执行最终无变化 sweep。
候选有限且每次提交只会严格降低一个坐标，搜索必然终止。

单个新 Proposal 的热路径为：

1. 从 `SourceSnapshot` 以 `copytree` 建立独立可写源码树，并写入精确 managed vector；
2. 分别解析 project plan 与 environment plan；相同 resolution request 在本次 run 内只计算一次；
3. 创建 venv、同步精确 artifact、检查 interpreter 与已安装 graph；
4. 运行 `ty` 并形成该 Proposal 的 static fingerprint；
5. 只有同一 Slice 上相邻、连续、相同 fingerprint 的已观察 component 已有唯一一致的 direct runtime
   status 时，才返回 `StaticOnlyEvidence`；否则运行完整 runtime evaluator；
6. 若该点成为 floor 或 predecessor，static-only evidence 必须 promotion，不能作为最终边界权威。

同一 Proposal 的 static-to-runtime promotion 现在复用尚未被 verifier 污染的 `PreparedEnvironment`。
不同 Proposal 不原地升级/降级，也不共享运行过 verifier 的 venv。

### 2.4 Hints 与持久化

`CoordinateSearch.minimize(..., hints=...)` 已支持每坐标 hint：选择不高于 hint 的最新候选作为首次探针，
但 hint 只改变探针顺序，不是硬下界，也不是可复用证据。当前 `SearchCoordinator` 没有传入 `hints`，所以
产品路径每个 Slice 仍从最早候选开始。

Evaluation cache、observation、region 与 prepared lifecycle 全部 invocation-local。全部 Cells 结束且源码
漂移检查通过后，workflow 才构建新报告，并由 `ReportStore.update_path()` 读取、校验和合并已有报告；
已有 report 不是跨运行 Evaluation cache。

## 3. 瓶颈判断

| 层级 | 当前证据 | 对 wall-clock 的判断 |
| --- | --- | --- |
| 组合空间 | 3.11/3.12 各 54 个唯一向量，对照 114,048 个已观察坐标组合 | 裁剪有效，不是主因 |
| 完整 verifier | 106 次；累计 3,470.40s；median 36.22s；P90 39.34s | 已证实的主导成本 |
| Static region | 18/121 个 search-only 唯一向量免 verifier，约 14.9% | 二分点通常不相邻，guidance 建立较晚 |
| Proposal 环境 | 每个唯一 Proposal 独立 materialize、resolve、venv、sync | 重复结构成本；缺少当前分阶段 wall-time 证明 |
| Promotion | E002 有 19 次同 Proposal 重复 prepare | 已由 D022/P028 解决，不再是开放瓶颈 |
| 并发 | Cell 可并行、Cell 内串行；resolution 与 candidate HTTP 各有全局锁 | 不同 key 也会排队，可能削弱多 Cell prepare 并行；尚未量化 |
| Report 校验 | 已有 report 在全部搜索完成后才读取 | 不增加正常搜索成本，但失败时可能浪费整次运行 |

E002 是当前最完整的可复查定量基线，但它早于 D022。启动任何性能 Design 前，应在当前 HEAD 用固定
source、candidate cutoff、Cell 集合与缓存条件重跑基线；历史计数只能定位问题，不能作为改动后的验收对照。

## 4. 优化候选与排序

### 4.1 P1：让 direct runtime reference 更早服务 static guidance

当前 region 只沿已经观察到的相邻候选扩展。二分探针往往相隔较远，即使两侧已经有相同 fingerprint 和
一致的 direct status，中点仍可能因为尚未形成连续 component 而进入 verifier。

后续 D003 Design 可以比较两类策略：

- 调整 probe 顺序，以较低成本优先建立相邻 fingerprint component；
- 在同一精确 Slice 内，当中点自身 fingerprint 与两侧已观察 direct reference 相同且状态唯一一致时，
  允许该中点只形成 guidance，而不立即运行 verifier。

第二种策略放宽了现行“连续相邻 component”条件，可能改变定界路径，不能直接当作内部优化。Design 必须
定义非单调、稀疏 hole、状态冲突与 promotion 反证时的行为。无论选择哪种策略，static-only 仍无 status，
final、floor 与 predecessor 仍须直接 runtime 认证。

### 4.2 P1：把 hints 接入产品路径

Hints 已是 `CoordinateSearch` 的现行 interface，但产品调用方没有提供值。最有希望的来源是可读旧报告中
相同 Cell/coordinate 的历史 floor；它只能成为 run-local 调度 seed，当前 invocation 必须重新生成全部
权威证据。版本不在当前 CandidateSnapshot、Cell 不匹配或报告不可读时应忽略 hint 或按明确规则早失败，
不能把历史 floor 当成兼容性事实或硬下界。

兄弟 Cell 已提交的 floor 也可以作为实验输入，但优先级低于旧报告：Cells 通常同时启动，数据是否可用取决于
`max-cells` 与完成顺序。不得为了获得 sibling hint 串行化 Cells，也不得让调度竞态改变最终证据或结果。

这项工作会把 report/run 事实传入 `SearchCoordinator`。应保持一个小的 immutable value flow，不新增
`HintProvider`、repository 或跨运行 cache module。按删除测试，删除独立 hint module 后如果复杂度没有回到
多个调用方，它就没有建立新 seam 的价值。

### 4.3 P2：把全局 I/O 锁收窄为 per-key single-flight

`CandidateBuilder` 在一把锁内调用 provider，uv candidate adapter 在一把锁内执行 `urlopen`，
`EnvironmentFactory._resolve_once` 也在一把锁内执行完整 resolve。它们保证同 key 只计算一次，却同时阻塞
不同 key。

应在各自 owner module 内改为：同 key 一个执行者、其余调用等待同一结果；不同 key 在有界并发下运行。
这不需要建立新的 public seam，也不改变 probe 数、Cell evidence 或 report。测试应从现有 interface 证明
同 key 去重、不同 key 可重叠、失败不会永久占位以及并发上限成立。

### 4.4 P2：减少 Proposal 源码物化成本

`SourceSnapshot.materialize()` 当前为每个 Proposal 完整 `copytree`。应先把 materialize duration、文件数与
逻辑字节数纳入当前基线；只有真实仓库数据证明其占比显著后，再评估 filesystem reflink/CoW 或 immutable
base 加 proposal overlay。

任何替代实现都必须保持独立可写、symlink、mode、排除规则、snapshot identity 与 cleanup 语义，并在不支持
reflink 的文件系统上安全回退。Hardlink 会让 Proposal 写入污染基线，不是合法优化。

### 4.5 P2：把已有 report 的有界校验前移

在 snapshot 建立后、启动 Cells 前读取并校验已有报告，可以让损坏 JSON、不支持 Schema 与非法布局尽早
失败。最终持久化前仍须重新读取并按现行 generation/update 规则处理，预读对象不能跨长运行被当成可信
cache。合法但 generation 不同的报告继续被替换，不能误报为 blocker。

该候选不减少正常 verifier 次数，但能消除晚失败造成的整次成本浪费。若实现改变 workflow/interface 或
错误时序，应由 D002/D014 Design 明确；不能借 preflight 引入旧 Schema compatibility reader。

### 4.6 已落地：按坐标 FailedCaseSet 做搜索期拒绝预言

完整 verifier 次数即使不变，同一坐标相继 Rejection 仍可能被同一批测试打死。现行默认策略在 Cell 内
按主动坐标记录失败 pytest nodeid，后续探针先跑该集合；collection 证明成立且任意 normal nonzero
时直接 Rejection，不再跑原命令。PASS 只来自一次原命令进程。direct pytest 原样保留用户 argv，并
在末位附加 `--maxfail=1` 与 invocation-local `cache_dir`。

正确性前提是用户测试 oracle 无跨 invocation 外部副作用、无用例间关联副作用。固定内部策略、具体
nodeid 与 pruning context 都不进入 evaluation policy identity。E002 没有 nodeid 命中率；没有
§11 第 2 组 wall-clock / 命中率数据时，不把 FailedCaseSet 作为已证实的第二段收益关闭本 Review。

## 5. 不采用的方向

- 改写用户 `test-command` 文本、隐式启用 testmon / pytest `--lf`，或把 last-failed 做成跨运行
  cache；也不得用两段 pytest 拼接冒充一次原命令 PASS。direct pytest 的 `--maxfail=1` overlay
  与 failed-set 拒绝预言是默认内部策略；PASS 仍须一次原命令进程，不新增 policy identity；
- 把 static-only evidence、跨 Cell 结果或旧报告结果直接当作 floor、predecessor 或 final authority；
- 跨运行 Evaluation cache，或不同 Proposal 共用已经运行 verifier 的可写环境；
- 为获取 sibling hint 而等待另一个 Cell，或在单 Cell 内并行、乱序执行状态相关探针；
- 以缩小 `search-space`、改变 `search-step` 或减少目标 Cell 冒充同一搜索契约下的性能提升；
- 在没有 materialize 分段数据前直接引入复杂 overlay filesystem；
- 建立通用 cache、hint manager 或 environment service，把本来属于 `CoordinateSearch`、
  `EnvironmentFactory`、candidate adapter 与 workflow 的知识搬到新的浅 module。

## 6. Module 与 seam 判断

现有高层 ownership 不需要为了性能重排：

- `CoordinateSearch` 是有深度的算法 module，拥有 hint、probe order、promotion、boundary、sweep 与终止；
- `SearchCoordinator` 把产品 baseline/candidate/evaluation 图适配到该算法 interface；
- `_ProposalRunner`、`EnvironmentFactory`、`StaticEvaluator` 与 `RuntimeEvaluator` 分别拥有 proposal lifecycle、
  prepare、static transition 与 runtime authority；
- `VerificationRunner`/`Scheduler` 拥有跨 Cell 调度，不应学习坐标或 region；
- `ReportStore` 拥有 reader/validator/update，workflow 只拥有何时调用持久化 seam。

因此优化应增加这些 module 的内部 locality，而不是增加 pass-through facade。Region/hint 改动触及 D003
interface 或跨 module value flow，必须先建立并接受 normative Design；per-key single-flight 若保持现有
interface 与可观察结果，可以作为 owner implementation 内的独立修复。任何实质变更实施前仍须建立 durable
Plan，并把每条验收标准映射到有序切片、迁移、测试和证据。

## 7. 验证口径与建议顺序

### 7.1 基线必须记录

- 每个 Cell 的 candidate 数、sweep 数、唯一 vector、prepare、static-only、promotion 与 verifier 次数；
- candidate HTTP、resolution lock wait、materialize、resolve、venv/sync、ty 与 configured verifier 的耗时；
- wall-clock critical path，而不只累加并行子进程 duration；
- 冷/热 registry 与 resolution cache 分开报告；
- final vector、每个 boundary、FailureRecord、disposition 与终止原因。

性能计数属于运行与 qualification 证据，不进入 report identity、Candidate、Failure 或 compatibility authority；
面向非 TTY 用户的活动展示仍由 R006 §5.2 独立拥有。

### 7.2 建议顺序

1. 在当前 HEAD 另行记录新的性能基线，补齐进程内 materialize 与锁等待数据；
2. 用固定 trace/fake evaluator 分别模拟“旧 floor hint”和“更早 region guidance”，比较 verifier 次数、
   unique vector、最坏探针数与结果等价性；
3. 根据数据只选择一个 P1 方向进入 D003 相关 Design；若收益接近，优先选择已经存在 interface 的 hints
   接线，避免先放宽 static guidance。坐标内 FailedCaseSet 拒绝预言已落地为默认策略，不与
   region/hint 捆成一次算法改动；
4. per-key single-flight 可独立实施和验证，不与算法 Design 绑定；
5. materialize 与 report preflight 分别按实测占比和晚失败频率决定是否推进，不打包成“搜索重构”。

若候选不能减少 configured verifier 次数或 wall-clock critical path，或者需要削弱 runtime authority、
环境隔离与确定终止，则停止该方向。本文本身不构成任何实现授权。

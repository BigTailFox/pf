# D038 — PF ty guidance 与动态兼容性证据收敛

- **状态：** 已完成并归档；2026-09-08 通过 AC1–AC26 验收，稳定规则已由现行 owner 接管；实施与证据见 [P042](../plans/P042-pf-static-guidance-authority.md)
- **日期：** 2026-09-07；验收完成 2026-09-08
- **类型：** 已归档临时迁移 Design；不再承担现行规范
- **来源：** 用户整改设想与 [E008](../../experiments/E008-mkdocs-complete-search.md)
- **源码核对：** `d707476d1a862effb8d90198ca4c4af7f05a95b5`
- **现行 owner：** [D003](../../designs/D003-pf-search-algorithm.md)、[D004](../../designs/D004-pf-ty-enhancement.md)、
  [D005](../../designs/D005-pf-failure-and-diagnose.md)；相关 interface、命令、wire 与展示分别归
  [D002](../../designs/D002-pf-implementation.md)、[D008](../../designs/D008-pf-verification-run.md)、
  [D014](../../designs/D014-pf-report-schema.md)、[D006](../../designs/D006-pf-cli-enhancement.md)

本文保存已完成的静态 guidance 权限迁移。E008 的方向经本 Design 接受、P042 规划与实施验收后，
稳定规则已归并现行 owner，Design 与 Plan 同步归档。正文保留迁移时的目标与理由，不再承担现行规范。
实施门槛见 [AGENTS.md](../../../AGENTS.md)。

2026-09-08 独立核验发现 AC14/S8 残留：归档当时的现行 owner 仍写出 `failure-execution-v3`、把 ty
采集写进统一 evaluation policy，并保留 `RuntimeWitnessAdapter` 生产路径。同日仅在现行 owner
吸收修复，目标契约与生产代码未改；完成记录见 [P042 §8.71](../plans/P042-pf-static-guidance-authority.md)。
本节以下迁移目标与 §12 历史评审记录保持原样，不回写为从未有过缺口。

## 1. 结论与本轮选择

> Static evidence may choose a probe, but may not eliminate a candidate or update a compatibility boundary.

ty 提供静态诊断、增量与 probe 选择线索；兼容性 disposition 来自当前配置执行契约允许的动态证据。
“动态”不包括孤立 import/getattr witness：执行过 Python 并不自动取得 configured oracle 的判断权限。

本 Design 作出以下选择，无运行期开关或旧规则兼容路径：

1. 选择用户方案 B：从 smoke/check/search 的生产路径移除 static witness、planner 与 strong attribution。
2. 每个坐标先消费可定界的直接动态证据；仍未定界时，执行纯静态二分 → oracle continuation。
3. `S_hi` 固定用于全局诊断；`S_slice` 绑定当前直接 PASS 上端，用于局部 guidance。两者分别
   计算多重集 delta 与 fingerprint，ty 工具不可用不阻止 verifier。
4. 静态阶段可按需新增 prepare/ty probe，不运行 verifier；失败最多退回无提示的 oracle 搜索。
   静态搜索窗口与兼容性搜索窗口分开，前者不能排除后者的候选。
5. 保留现有 resolve/install/build failure policy、原命令 PASS authority、已交付的 failed-case rejection
   优化及 predecessor 重验。新增 ty × testcase selection 不在本轮实现。
6. ExecutionPolicy、GuidancePolicy 与 SearchPolicy 分别绑定动态证据、静态事实和搜索推导；
   ty/guidance 改变不单独改变同一执行对象已有动态证据的 authority identity。
7. 原始 `TyCheck / TyCheckUnavailable` 由 StaticEvaluator 通过 Run 内独立缓存共享；缓存静态
   事实，不缓存某次 guidance 解释。key 使用实际静态对象的规范投影，不依赖完整动态 Proposal
   identity；comparison 先验证 GLOBAL/SLICE 准入。物化环境释放不清除原始事实。

收益是消除静态归因错误对候选排除的权限。本文不承诺消除执行策略本身允许的误拒绝，也不承诺
任意非单调空间中的全局最低版本。

## 2. 当前证据与问题范围

### 2.1 E008 的直接证据

[冻结源码](../../experiments/data/E008/rendering.py.txt) 访问
`markdown.treeprocessors.UnescapeTreeprocessor`，且显式捕获 `AttributeError`。
[冻结进程证据](../../experiments/data/E008/diagnostics.txt) 中的 witness 实际检查
`markdown.treeprocessors.treeprocessors`。Python 3.10–3.12 的 check 因此提前拒绝，未执行该
lowest-direct 组合的完整 unittest；search 的 Markdown 3.3.7 predecessor 也使用了错误 plan。

两个问题独立成立：目标恢复错误；即使目标正确，接口缺失也不能证明有 fallback 的项目会违反
configured verifier。条件导入、未执行分支、optional feature 与未被配置测试覆盖的路径同样不能
由孤立 witness 获得拒绝权限。不以 CFG、exception-flow 或 reachability 分析弥补这一权限缺口。

E008 的最终向量仍有完整测试 PASS。本文不改写历史报告，不预言修复后最低组合一定 PASS，
也不把采样 floor 解释成所有更低 patch 的不兼容证明。

### 2.2 不止一处 hard rejection

当前公开 module 实现及 owner 有三条相关路径：

| 当前路径 | 当前后果 | 目标替代 |
| --- | --- | --- |
| `RuntimeEvaluator` 将 `CONFIRMED_MISSING` 转为 `RuntimeInterfaceMissingEvaluation`，`FailurePolicy` 形成拒绝 | witness 可成为 check 终态与 predecessor evidence | 删除该 Evaluation/cause/authority 及生产 witness 路由 |
| `_ProposalRunner.evaluate_in_slice` 从同 fingerprint region 返回 `StaticOnlyEvidence.guidance`；`CoordinateSearch._status` 将其视为 PASS/REJECTED | `_guided_floor/_locate` 在直接执行前收缩窗口；仅最终 promotion 补证 | 独立静态窗口只产出 hint，oracle 窗口更新读取直接 Probe evidence |
| ty failure 返回 `IndeterminateEvaluation`；check 仅在合法 `S_hi` 后启动 declaration | 辅助静态协议仍可阻止动态验证 | 将静态可用性与 compatibility Evaluation 分离 |

因此只删除 witness 的 early return 不满足本次 invariant。D003 当前的“guidance 可定位、提交前
promotion”也须替换，不能只调整文案或把原 guidance 改名。

## 3. 证据权限与失败分层

| 事实 | compatibility authority | 搜索消费 |
| --- | --- | --- |
| ty import/member/其他新增 diagnostic、delta、fingerprint | 无 | 诊断、probe ordering |
| ty 工具失败、无法形成 local anchor/delta | 无 | 静态阶段返回 NO_HINT，继续 oracle 路径；仅 S_hi 不可用不禁用 local guidance |
| 孤立 runtime interface missing | 无；本轮不再生产 | 无 |
| configured verifier 原命令阶段有效 `NormalExit(0)` | 当前精确 Proposal 的 PASS | 更新通过端、current、floor/final |
| configured verifier 的 D005 合格非零；现行合格 failed-set rejection | 当前 Attempt 的 REJECTED | 更新拒绝端、predecessor，参与直接非单调检测 |
| oracle 阶段 resolve/install/build 执行结果 | 完全遵循现行 D005/D012 | 合格 prepare Rejection 可定界；Indeterminate 停止 Cell |
| 为纯静态探测准备环境失败 | 此阶段不形成 compatibility disposition | 保存操作事实，返回 NO_HINT，不自动移入 oracle 观测集 |
| cancellation、Run deadline、关键资源/identity 一致性失败、verifier 异常终态 | 现行停止/Indeterminate 规则 | 不伪造 PASS 或 REJECTED |

纯静态阶段所有已建模的局部失败都只影响 hint，包括 prepare 失败、ty timeout/异常 exit/坏 JSON、
输出截断、静态结果冲突或无法比较；不返回 ProbeIndeterminate，不更新兼容性失败集合。
同一不可用事实不在静态阶段反复重试，不把未知状态填成 regression 或 unchanged 来完成二分。

oracle 阶段的 prepare/verifier 失败及动态 identity/cache 冲突仍遵循现行处置；该阶段的 ty 失败
仍只是辅助诊断不可用。全局配置准入、候选冻结、用户取消、Run deadline 与无法维持源码/环境
隔离的基础设施终止是独立运行条件，不伪装成静态搜索的 Indeterminate，也不捕获全部 PF 异常
后在已破坏的 context 中继续。非法 owned ty options 不因 guidance 降级而合法。

静态收集有原有 timeout 上限，仍占 ty permit；verifier 使用 test permit。Run cancellation/deadline
始终优先，不把“环境可执行时进入 verifier”解释成忽略用户中止或已耗尽的 Run 预算。

## 4. 静态事实与 baseline 可用性

`StaticEvaluator` 拥有 ty 收集、diagnostic identity、多重集 subtraction 和 fingerprint；
`RuntimeEvaluator` 独占 verifier 调用及动态结果组装。两者通过显式静态结果相连，静态结果不再
复用兼容性的 `IndeterminateEvaluation`。名称可在 Plan 中细化，但以下状态与字段义务固定。

### 4.1 Global Diagnostic Baseline：`S_hi`

每个 Cell/run 有一个最高版本的显式 `StaticBaselineState`：

- `AVAILABLE`：真实最高版本 Proposal、完整 TyCheck、诊断多重集与 digest。
- `UNAVAILABLE`：真实 capture Proposal、明确采集不可用原因；实际 ty 失败关联其 typed failure
  和已有 process/log refs，静态输入无法闭合时保存未采集原因而无 ty process/fact。没有
  diagnostic baseline digest，不以空诊断或空增量代替失败。

highest prepare 失败没有 Proposal，继续按现行 preparation contract 处理，不伪造上述状态。
可用时捕获 TyCheck 同时作为最高版本 Proposal 的空增量事实，不重复运行 ty。

`S_hi` 回答“与最高版本配置相比发生了什么静态变化”，用于全局 diagnostic delta、fingerprint、
report/diagnose 和审计。它不随 sweep/coordinate 改变，也不再作为每个坐标静态二分的比较基准。
捕获失败不通过换一个候选重建全局 baseline；全局比较保持不可用，局部比较独立判定可用性。

### 4.2 Slice-local Guidance Anchor：`S_slice`

每个坐标需要静态 guidance 时，从 §6.1 动态 fast path 之后的直接 PASS 上端 `U` 冻结 anchor。
通常 `U=current`；若 predecessor 的真实 PASS 已提供更低上端，则用该上端的精确 Proposal。
`U` 是本坐标定位中的 working upper，不因建立 anchor 就提前提交外层 current 或 floor。

`S_slice` 保存该 Proposal 的完整 ty diagnostics，而不是该 Proposal 相对 `S_hi` 的增量。
anchor 绑定精确 Proposal、真实完整 TyCheck、同 Slice 的直接完整 PASS ref 和 GuidancePolicyIdentity；
PASS ref 只证明选择该 anchor 合法，不赋予局部 delta 兼容性权限。通过 StaticEvaluator 查询与
该 Proposal 的 StaticSubjectIdentity、TyObservationPolicy 匹配的 Run 原始缓存；静态关联须复证
消费 Proposal 到静态对象的投影。原始观察可以由另一个动态 Proposal 产生，原 producer/process
provenance 保持不变，不能把复用写成当前 anchor 又执行了一次 ty。
没有合格完整观察时返回 NO_HINT(`anchor-unavailable`)，不以空诊断补齐，也不
把另一 Proposal 的动态 PASS 冒充该 anchor 的 PASS。当前阶段不为失败 anchor 自动重试 ty。

```text
global_delta(P) = diagnostics(P) ⊖ diagnostics(S_hi)
local_delta(P, S_slice) = diagnostics(P) ⊖ diagnostics(S_slice)
```

两种比较都使用多重集 subtraction。前序坐标留下的相同 diagnostic occurrence 被 local anchor
抵消；global delta 仍如实保留它们。局部比较不证明某个 diagnostic 的 package/member 根因。

anchor 相对自身的 local delta 必然为空，因为比较的是同一份真实 TyCheck；它相对 `S_hi` 仍可
是 regression。仅有动态 PASS 而没有 TyCheck 时不能推出 local unchanged。anchor 在本坐标
静态阶段及后续 oracle continuation 中固定，不随着后续动态窗口收缩重新捕获或重启静态搜索。

### 4.3 原始 TyCheck 与 StaticComparison

原始 `TyCheck` 保存 immutable diagnostics、规范 diagnostic identities、实际 ty process outcome、
安全 process/log refs 与静态请求 scope，不保存 baseline、delta、STATIC_UNCHANGED/REGRESSION、
StaticHint 或 compatibility disposition。失败由独立的 `TyCheckUnavailable` 保存。
两者按 §4.4 的 TyCheckKey 在一次 Run 中取得一次原始观测。

`StaticComparison` 每次显式指定 `GLOBAL(S_hi ref)` 或 `SLICE(S_slice ref)`，以下状态属于该次
比较，不能成为 Proposal 唯一、无 scope 的静态 status。同一份 TyCheck 可被多个比较引用，不重复
执行或复制原始诊断。

| 状态 | 必须保存 | 禁止推导 |
| --- | --- | --- |
| `COMPARED` | 消费 Proposal 合法关联的 TyCheck ref、通过准入的比较 context 与 anchor/baseline ref、delta、`STATIC_UNCHANGED/STATIC_REGRESSION`、fingerprint | compatibility disposition |
| `UNCOMPARED` | 消费 Proposal 合法关联的完整 TyCheck ref、比较 context、缺失/不可用比较对象或 context-mismatch 原因 | 该比较的 delta、fingerprint、clean/regression |
| `UNAVAILABLE` | 消费 Proposal 的明确采集不可用原因、比较种类及已知 context；实际 ty 失败才关联原 typed failure/process refs，未形成静态请求不造 fact | TyCheck、delta、fingerprint、clean/regression |

只有 `SLICE + COMPARED` 可用于静态二分；global COMPARED 不替代 local 比较。
允许同一 Proposal 同时 global regression / local unchanged，或 global UNCOMPARED / local COMPARED。
`S_hi` 不可用但 `S_slice` 与候选 TyCheck 可用时，guidance 正常工作。任一实际静态 probe 的 local
比较失败才结束本次静态阶段并返回 NO_HINT，已有事实仍保留；不把未完成二分冒充有效 hint。

global/local fingerprint 使用区分比较种类的 domain tag，并绑定比较对象 identity、GuidancePolicyIdentity
以及下述 StaticComparisonContext 和规范增量 identity 多重集。空增量有合法 fingerprint；
UNCOMPARED/UNAVAILABLE 没有该 fingerprint。

`StaticEvaluator.compare()` 必须在 subtraction 前验证显式的 `StaticComparisonContext`。
该 context 是带 GLOBAL/SLICE 判别的比较事实与准入参数，不新增 ComparisonPolicyIdentity、
AnchorPolicyIdentity 或其他顶层 policy。必须绑定两端 TyCheck/static subject refs、消费方的
Proposal/prepare provenance、GuidancePolicyIdentity，以及下表要求的比较对象/约束。

| 比较种类 | 共同准入条件 | 允许不同的事实与语义 |
| --- | --- | --- |
| GLOBAL | 相同源码快照/分析目标、Cell、精确解释器、platform/surface、SourcePlan 语义、逻辑分析根/cwd、分析范围及 TyObservationPolicy；reference 是本 Cell 固定的 S_hi | 允许完整依赖向量及其实际解析/安装闭包不同，允许下述合法 harness 关系；只表示两份环境观察的 diagnostic 差异，不自动表示某个 dependency regression |
| SLICE | 满足上述静态共同 context，reference 改为 S_slice；显式绑定活动坐标 d、冻结的其他 direct coordinate 值、固定声明/来源、候选窗口、anchor 的真实 PASS，以及同一相关 preparation/resolution/harness 语义 | 仅 d 的候选取值和在固定准备规则下允许的解析/安装后果可变；不要求最终安装图/传递依赖/harness closure 完全相同，不声称变化已唯一归因到 d |

相同 TyObservationPolicyIdentity 只是必要条件；同 ty args 不代表同 Python/surface/安装语境。
GLOBAL 在本轮也不跨源码、Python 或 surface 任意比较。完整静态 subject 无需相等——比较本来
就是观察不同依赖环境——但所有固定字段及允许变化的关系必须通过上表准入。

两种比较的 harness 关系都复用 [D012 §3](../../designs/D012-pf-harness-relaxation.md#3-structured-harness)
的语义，校验同一组选定 harness、active declaration IDs、原始声明/来源、同 Cell HarnessBaseline
及相关 normalization/resolution 规则；允许的关系为：

- 同一 original harness，或两端均从同一 HarnessBaseline 按 D012 合法生成的 relaxed harness；
- reference 是产生该 HarnessBaseline 的 highest/original Proposal，subject 是消费该 baseline
  的 declaration/exact-probe relaxed Proposal；
- active external harness 为空时，两端均满足 D012 的 project-only 与 empty HarnessBaseline 规则。

不能仅比较 original/relaxed 的 policy 字符串或要求规范化后的 requirements 字节相等。
relaxation、project-owned exact constraints 与 harness-only ceilings 必须从保存的输入和 baseline
按 D012 规则验证，不能自行放宽声明。SLICE 还要求除 d 外的受管直接坐标不变，并使用相同冻结
resolver context（来源/配置、release cutoff 等）；闭包变化必须来自这些允许的请求及准备事实。
这是结构化准入校验，不新增全图因果分析，也不为了取得可比较性额外执行一轮 baseline oracle。

这使 search 初始最高版本 original anchor 能合法比较 exact/relaxed probe；check 的
highest/original 与 lowest-direct/relaxed 则可以做 GLOBAL 诊断，不能因两者同 ty policy 自动
获得 SLICE 语义。未经上述关系验证的 harness 差异不准入。

两端原始 TyCheck 完整但固定 context/允许关系不匹配时，返回
`UNCOMPARED(context-mismatch)`，不计算 delta/fingerprint；local guidance 消费后返回 NO_HINT。
没有可用 baseline/anchor 保留其 unavailable reason，实际 ty 失败保留 UNAVAILABLE，三者不混淆。
producer 和 reader 共享该纯准入规则；reader 对伪造的 COMPARED、context 或 refs 确定性拒绝，
不能按调用方自报的 GLOBAL/SLICE 标签放行。

保留当前 diagnostic identity、规范排序、多重集重数、message/severity 仅展示的规则。
移除依赖 AST 恢复的 strong/general classification 与 witness plan；import/member 等分类可以直接
展示 structured diagnostic code，不另建 package/member attribution authority。

### 4.4 TyCheck 请求身份与采集策略子身份

> Cache the static fact, not the guidance interpretation.

```text
StaticSubjectIdentity = hash("pf:static-subject:v1", canonical static projection)
TyCheckKey = (StaticSubjectIdentity, TyObservationPolicyIdentity)
```

Run 容器是隐式 namespace，并按 Cell 隔离。StaticSubject 是从成功 prepare 的 Proposal 及其
已验证事实展开的规范投影；它是 ty 观察的执行对象，不是第四类 policy。`static-subject-v1`
冻结以下六组语义输入；不存在由 Plan 自行扩展的“其他相关事实”字段：

| 输入组 | 必须绑定的语义事实 |
| --- | --- |
| Source | 全部受观察源码/配置的不可变 snapshot 内容 identity、目标 package/member 与 workspace 映射；包含 pyproject，不能只散列被报告 diagnostic 的文件 |
| Target | Cell 的 package、目标 Python/platform/ABI、active surface，以及实际解释器实现、完整版本与可观察的解释器/stdlib 内容 identity；不只绑定 Python minor |
| Installed world | 已解析并安装复证的 project/harness/transitive 完整图，节点的 version/source/artifact、实际安装内容 identity、metadata/type stubs、安装模式和 editable/source 映射；相同 sdist/version 不证明两次 build 产物相同 |
| Analysis layout | 有序检查目标与 import/type 搜索根、逻辑 cwd、根之间的相对布局、文件种类/符号链接目标与内容映射；保留搜索顺序，不把有序路径当成集合 |
| Configuration closure | 实际生效的配置文件内容及优先顺序、配置发现边界和外部 stub/source 根的 snapshot；内置配置/类型资源随精确 ty 工具 identity 绑定，CLI override 随采集策略绑定 |
| Process context | ty 实际收到的显式进程环境、配置/路径展开所用值与文件系统大小写语义；环境值以内容摘要及逻辑路径映射绑定，日志不暴露敏感原文 |

V1 只支持上述输入可闭合到冻结 snapshot、已复证安装内容、固定解释器和精确 ty 工具的观察。
采集进程不得隐式继承未登记的环境变量、用户/父目录配置或可变外部搜索根；已有显式配置须
先纳入上述闭包，不能静默丢弃来制造 cache hit。进程环境在 Run 内显式固定，新增外部输入必须
先取得不可变内容事实。不能完成闭包时返回静态不可用 `static-subject-unavailable`，继而 NO_HINT；
不启动该请求的 ty、不登记 TyCheckUnavailable，不影响独立 oracle。该规则不承诺支持任意宿主环境。

上述资格在构造 StaticEvaluator request 前由共享投影 factory 判定；未通过时由 capture/inspect
返回带该原因的静态不可用状态，不调用 lookup/collect，不合成假 key。已完成合法 prepare 的
环境仍按 §5.4 保留，后续动态操作独立进行。

canonical projection 采用现行 canonical JSON/hash 编码规则：固定字段、明确空值、对象键规范
排序；集合按规范 identity 排序，有序 roots/config/argv 不重排；路径替换为 snapshot/environment/
interpreter/tool 的逻辑根及 POSIX 相对路径，保留大小写语义。只能消除已证明等价的物化根重定位，
不能删除实际影响解析的相对布局或外部路径。内容摘要绑定文件种类、逻辑路径、目标和实际字节；
已有不可变 artifact manifest 可以复用，未复证的包名/version 或原始 sdist hash 不替代安装内容。
未知字段、缺失必需事实或不支持的 projection version 不按默认值参与等价判断。新增语义输入或
改变规范化须升级 projection version；producer、cache 与 reader 共用同一规范模型/factory。

原始 Proposal/Attempt ID、完整 ExecutionPolicyIdentity、verifier command/cwd/timeout、动态
failure/failed-case/disposition 规则不进入投影。导致实际源码、artifact/安装内容、解释器或分析
输入改变的事实仍必须进入；不能为提高命中率忽略真实环境差异。
不直接复用含动态 policy 或请求 provenance 的整份 plan/EnvironmentIdentity digest；由共享
factory 展开上述事实后计算投影，reader 从保存的规范输入及内容 refs 复算，不信任自报投影摘要。
离线 reader 不重读宿主环境或重新安装，也不声称已重新验证附件之外的文件内容。

多个动态 Proposal 可以投影到同一 StaticSubject。原始 TyCheck/Unavailable identity 绑定
StaticSubject、采集策略与实际观察；原 producer Proposal 和 process/log provenance 单独保存。
消费关联保存 `(consumer Proposal ref, StaticSubject ref, ty fact ref)` 并复证投影相等，不改写
原始观察的 producer，不凭复用获得另一 Proposal 的动态 evidence。

`TyObservationPolicyIdentity` 是 GuidancePolicy 内的采集策略子身份，不新增第四类顶层 policy。
它包含精确 ty 工具 version/内容 identity、有效 args/timeout/owned options、分析范围选择规则、协议解析、diagnostic
identity/规范化与原始 unavailable 分类规则。具体逻辑根/cwd/检查目标值属于 StaticSubject，
规则及有效 ty options 属于该子身份；同一规范请求生成二者，不能遗漏实际分析 scope。

baseline/anchor、GLOBAL/SLICE delta、fingerprint、二分、NO_HINT、hint 消费及阶段名称均不进入
TyCheckKey。更换它们可以重新计算 comparison/hint，不重跑相同请求的 ty。更改 ty version、
有效参数、采集协议/分析范围，或实际 static projection 改变，均为不同 key。
仅外部有效 verifier timeout/动态 classifier 改变、而 StaticSubject 与采集策略不变时，允许
不同 Proposal 复用 TyCheck；若改动了实际 pyproject 源码快照，则仍是不同 static subject。
调用方从规范化请求通过共享 identity factory 得到 key，不手工拼接或漏掉 owned options。

随机物化目录不成为新静态请求身份；scope 用 snapshot/package/interpreter 等逻辑根表达，
诊断路径与日志按 D004/D007 规范化。新环境必须实际复证其 Proposal 到 StaticSubject 的投影，
不能只复用其版本号标签。
只有与不可变输入/prepare 状态匹配的环境可以执行 cache miss 的 ty；不能在 verifier 已修改的
环境上启动检查，再把结果登记为原 StaticSubject 的静态事实。缓存命中可读取先前事实，不授权复用
已污染的物化环境来运行 verifier。

### 4.5 所有权、接口与 Run 生命周期

Verification Run 开始时建立一个 `TyCheckCache`，在 baseline capture 前就可用；Run 内的最高
版本 capture、check、per-coordinate static phase、oracle 附属静态收集均通过 StaticEvaluator
共享相应 Cell 分区。组合层传递明确的 Run cache handle；StaticEvaluator 可以被多个 Run 共用，
但不能在实例字段中保存隐式 current-run cache，不能以 `clear()` 切换共享实例的 Run 身份。

建议的基础 interface 为：

```text
StaticEvaluator.lookup(request, *, run_cache)
  -> RunTyFactRef[TyCheck | TyCheckUnavailable] | CacheMiss
StaticEvaluator.collect(prepared, request, *, run_cache)
  -> RunTyFactRef[TyCheck | TyCheckUnavailable]
StaticEvaluator.compare(subject_ref, reference_ref, *, run_cache,
                        context: StaticComparisonContext, guidance_policy: GuidancePolicy)
  -> StaticComparison
```

`guidance_policy` 就是 §8 的 GuidancePolicy（实现可传只读视图），不是独立 ComparisonPolicy，
不产生第四类顶层 identity，也不整体进入 TyCheckKey。
`RunTyFactRef` 是 cache 签发的 typed ref，包含不可冒用的容器归属、Cell 分区和原始事实引用，
可只读访问其 payload；归属不进入 TyCheckKey 或原始事实 identity。仅有内容相同的 TyCheck
或 fact digest，不能自行构造已登记引用。没有 reference 时显式传缺失原因，不伪造 ref。
compare 首先通过 run_cache 验证两端及 comparison context 的消费关联均属于该开放 Run/Cell；
跨 Run/Cell 或已关闭容器的输入返回 UNCOMPARED(context-mismatch)，不计算 delta、不导入事实。
即使两个重叠 Run 的 key、payload、GuidancePolicy 和 source 全相同，也不能混用 refs。

lookup 不要求 PreparedEnvironment，只读取已完成结果；CacheMiss 表示尚无完成事实，不等于
“先前检查失败”。IN_FLIGHT 的 lookup 也返回 CacheMiss，只有 collect 决定等待还是启动；
collect 验证 prepared/request 的 StaticSubject 投影与采集策略，在原子缓存入口中再次检查后执行
或等待已有同 key 检查。算法与 `_ProposalRunner` 不另存原始 ty 字典，不直接绕过该入口调用
TyAdapter。capture/evaluate 迁移为这些基础行为的消费者，不形成第二套采集路径。

compare 只读取当前 cache 的事实与归属，不执行 I/O；准入与多重集计算共用纯规则。V1 不增加 comparison cache；若需要报告，按带比较 context 的
identity intern 派生结果即可。算法可以保存本次静态二分的点/结果，复用时须满足原有
Slice/anchor/policy/窗口规则；这不是独立的通用 hint cache 服务。
compare 先验证 §4.3 的完整准入条件，包括两端采集策略、consumer Proposal 到 StaticSubject
的关联、GLOBAL/SLICE 固定 context 与允许的 harness 关系，再计算增量。不匹配返回
UNCOMPARED(context-mismatch)，缺观测保留其不可用原因；reader 拒绝伪造的 COMPARED。
只改变 anchor 或 guidance 算法时，可使用同一对原始事实重新计算通过准入的 comparison。

原始结果保留到 Run 收尾：先完成已有报告/Journal 事实持久化，再释放内存缓存。单个坐标结束、
环境 close、Cell 完成都不清除可供本 Run 读取的原始事实；跨 Cell 不互相命中，下一次 Run 创建
新容器。同一 CLI invocation 中多个 Verification Run 也不能隐式共用。异常/取消时按现行收尾
规则保存已取得事实并关闭容器；关闭时先拒绝新 collect、取消或收拢在途操作并唤醒等待者，
再释放其状态，不允许 pending 请求使 Run 永久等待。

Run 内已登记的 StaticSubject 在环境释放后仍可 lookup/compare，无需重新 prepare。oracle 若
需要环境则独立复用或重建，复证新 Proposal 的静态投影后再关联对应 TyCheck；动态结果另按
精确 Proposal 与 ExecutionPolicy 验证，不能由静态投影相同推出。缓存本身不持有环境
句柄，也不凭 direct vector 预测一次尚未完成的 prepare 必将得到哪个 Proposal。

### 4.6 Negative cache 与并发去重

缓存状态机在 StaticEvaluator 内原子执行：

```text
ABSENT -> IN_FLIGHT -> TyCheck | TyCheckUnavailable
```

同 key 的重叠 collect 请求共享一个进行中的操作和终态；只有 owner 取得 ty permit 并启动 adapter。
等待者不占 ty permit、不使用另一个 prepared 环境并行重跑；owner 完成前，其实际输入环境必须
保持有效，消费者不能提前 close 或交给 verifier。返回同一观察不创建新的 process、执行阶段、
耗时或输出日志；关联记录可说明 reused，但不得伪造第二次运行。

StaticEvaluator 不拥有或关闭 PreparedEnvironment。两个 runner-owned 环境投影到同 key 时，
collect 可报告 joined-existing-operation，但该结果只去重 ty 进程，不证明环境对 oracle 冗余。
坐标结束前，仅当精确 ExecutionSubject/Proposal 与 ExecutionPolicy 等价，并且 runner 已保留
另一份满足该 oracle request 的 clean、未被占用且可供后续执行的环境时，才允许因冗余关闭
等待者环境。检查与保留必须由 runner 原子完成；另一个正在执行 ty/verifier 的环境不满足此条件。
不满足则保留到本坐标 oracle 消费或坐标结束；不同 Proposal 不共享可写环境。
owner 环境在 ty 进程及其终止清理完成前不得交给 verifier 或关闭；取消时先停止/收拢操作，
再释放资源并唤醒等待者。Plan 在 StaticEvaluator/cache foundation slice 同步实现这些时序。

TyCheckUnavailable 只缓存实际等价 ty 请求的 typed failure，例如 ty timeout、启动失败、异常
exit/signal、非法或截断输出。原始 failure fact 保留具体原因和实际已取得的 process refs，无
compatibility status；调用方据此产生 UNAVAILABLE/NO_HINT，不能产生 REJECTED 或 ProbeIndeterminate。
同一 key 不重试、不因换阶段或重建相同环境清除失败；下一 Run 自然允许新检查。
ty timeout、启动失败或坏输出不单独使成功 prepare 的环境失效；进程结束且没有独立的污染/
完整性失败事实时，仍保留供 oracle 使用。不得把 TyCheckUnavailable 等同于 prepare failure。

prepare failure、没有 baseline/anchor、无法完成 comparison 和 NO_HINT 本身不写入 TyCheck
negative cache。它们没有证明本 key 的 ty 已运行失败。ty 自身的进程终态与用户取消/整个 Run
deadline 必须区分：后两者按运行停止规则唤醒等待者并终止，不伪装成普通 TyCheckUnavailable。
未建模 PF 异常也不得捕获为可继续的 negative cache；按基础设施异常收尾并释放等待者。

“最多一次”指同一 Run/Cell/key 的原始 ty 操作至多执行一次，包括失败和并发请求。结果固定
本 Run 的实际观察，不证明 ty 重跑一定相同，也不以缓存命中检测 flaky/nondeterminism。

### 4.7 消费与持久化范围

本轮实现原始事实缓存与当前 per-coordinate/check/baseline/oracle 消费。未来全局 `V_guess`
或 testcase-selection 阶段可使用同一基础 interface，但本 Design 不新增这些算法，也不把
实现 V_guess 作为验收前提；跨阶段复用通过当前公开消费者或两个独立调用方的测试证明。

Run 内消费者可以读取缓存。独立 `pf diagnose FAILURE_ID` 保持现行 failure-centric interface，
只从所命中的 report/Journal 读取与该动态 Failure 显式关联的静态事实；关联路径和审计存储
见 §8.1。无关联的 S_hi、NO_HINT 和未被 oracle 选中的 static probe 只作为审计事实保存，
不新增 observation CLI selector，也不伪造 Failure ID 使它们可查。
原始事实及 process/log refs 独立于物化环境目录存续；日志的保留、脱敏和缺失处理遵循 D007。
diagnose 不访问已结束的 Run 内存、不启动 ty；持久化报告不是下一 Run 的自动缓存导入源。

## 5. 命令与执行生命周期

### 5.1 Smoke 与 search baseline

最高版本 prepare → 收集静态 baseline state → 同一环境运行完整 verifier → 关闭环境。
即使 baseline state 不可用，真实完整 PASS 仍可成为 search 起点；snapshot/candidates 的冻结与
baseline artifact closure 不变。静态收集失败本身不令最高版本 baseline rejection/Indeterminate。

### 5.2 Check

用户语义为验证声明的 lowest-direct 组合是否通过配置执行契约。保持当前 SEARCH SourcePlan、
required surface、两次 preparation 与 harness relaxation：

```text
declaration-capture:
  prepare(highest, original harness) -> HarnessBaseline
  -> 尝试捕获 StaticBaselineState -> close
declaration:
  prepare(lowest-direct, relaxed harness, HarnessBaseline)
  -> 收集/复用 TyCheck 并计算 GLOBAL StaticComparison -> configured verifier -> close
```

最高版本 preparation 还负责获取真实 `HarnessBaseline`，并非只为了 ty；不能因取消静态门槛就
删除此 preparation 或用空 harness facts 继续。该步骤失败仍遵循现行 D008，并明确是 capture
准备失败，不能声称已测试 declared-lowest。成功取得 HarnessBaseline 后，ty capture 失败不得
阻止 declaration preparation；lowest-direct 环境成功构造后，ty diagnostics/failure 不阻止 verifier。

Check 不调用 search planner，不做 testcase selection。其最终 PASS/REJECTED 来自 declaration
verifier，或按现行策略报告 preparation failure；辅助静态不可用不参与命令失败聚合。

### 5.3 每个坐标的两阶段执行

```text
固定 Slice 与当前已直接 PASS 的 current
  -> direct fast path: 消费同 context 动态 cache / 有效 history predecessor 重验
       -> 已定界：直接结束坐标
       -> 未定界：保留动态窗口与直接 PASS 上端 U
  -> 冻结 S_slice(U)
  -> static phase: 按需 prepare -> ty -> local delta 二分 -> StaticHint 或 NO_HINT
  -> oracle continuation: 从已有动态窗口按 hint/机械顺序选择向量
       -> 复用真实动态结果，或 prepare/reuse -> configured verifier
       -> 直接 ProbePass / ProbeRejection / ProbeIndeterminate
       -> 只按直接结果更新 oracle 窗口、认证 floor/predecessor
  -> oracle PASS 授权坐标提交 -> 释放未使用的静态环境
```

“纯静态”指阶段内只消费静态结果、不运行 configured verifier；为 ty 构造真实候选环境仍需
resolve/install，不能把它宣传成只付 ty 的 CPU 成本。该阶段即使命中已有完整动态结果，也只读取
其中的静态事实；动态窗口的消费由 fast path 或 oracle continuation 显式进行。

oracle 实际选中的向量必须取得本向量的直接动态 evidence。静态命中的准备环境和 TyCheck 可
供同 Proposal 补齐 verifier，但不复制代表点结果。prepare terminal 没有 Proposal 静态事实。
最高版本真实 baseline seed、当前 Slice 的 predecessor 重验和原命令完整 PASS authority 保留。

### 5.4 执行复用与清理

`_ProposalRunner` 独占物化环境与动态结果 cache；原始 ty cache 按 §4.5 由 Run 管理、StaticEvaluator
提供唯一采集/查询入口。算法只发出阶段请求和坐标结束通知，
不持有资源句柄。静态二分成功准备的 materialized prepared environments 全部可保留到该坐标
oracle 结束，不只保留 suspect/clean 两端；至多为 §6 的对数次静态
探测数量。不同 Cell 的数量按并发度相加，不保留整次 search 的所有静态环境。

物化环境是已经完成 resolve/install 的目录与 identity，不是仍在运行的 ty/verifier 进程，也不是
compatibility evidence。同一有效环境交给 oracle 时复用 prepare/ty，只补 verifier；完整结果产生后
立即关闭。静态 NO_HINT 后仍保留此前合法环境供 oracle fallback；仅 prepare 未成功或有独立
污染/完整性失败事实的环境按进程收尾规则清理。ty unavailable 不单独触发环境清理；
等待者去重释放必须满足 §4.6 的精确动态等价与可用替代环境条件。
坐标结束或 Cell 退出时清理未消费环境，finally 覆盖取消与异常。以后另一个坐标或
sweep 再请求已释放环境的向量时，可以从冻结输入重新 prepare；不能因静态事实缓存存在就
声称可写环境仍存在。若新 prepare 的 StaticSubjectIdentity 与 TyObservationPolicyIdentity 均匹配，可复用
TyCheck；否则不得复用。因此本次把“同向量 prepare 至多一次”改为“有效物化环境复用、完整结果
去重、释放后允许重建”；既有成功 resolution/immutable artifact cache 规则保留，不伪造重新
准备的活动或耗时。

纯静态 prepare 失败只保存真实 operation facts，不填充动态终态 cache。oracle 后续显式选择
同一向量时，才可按现行精确 request/context 规则消费可复用的 preparation facts 并分类；
cache 不合格则实际重新 prepare。不能直接把 NO_HINT 或 ty failure 转为 oracle Indeterminate，
也不为了“fallback”强制重试每个静态失败向量。不同 Proposal 不共享可写环境。

| 对象 | 生命周期与 owner |
| --- | --- |
| Materialized prepared environment | `_ProposalRunner` 按坐标保留，执行终态/坐标结束清理；不拥有原始 ty cache |
| TyCheck / TyCheckUnavailable | Verification Run 的 Cell 分区；StaticEvaluator 唯一消费入口，Run 收尾后释放 |
| StaticComparison / StaticHint | 当前比较/搜索 context 的派生事实，可写入报告；不增加独立的通用 comparison/hint cache |

## 6. Per-coordinate 两阶段搜索

### 6.1 编排层级选择

本 Design 采用 **per-coordinate**：先消费可以直接定界的动态证据；尚未定界时，在同一坐标内执行
静态阶段，再立即 oracle continuation。这是两阶段在外层坐标下降中的嵌套关系，不要求每个坐标
先付静态成本，也不增加一个可以提交 current 的静态坐标下降过程。

| 层级 | 组织方式 | 适配当前坐标搜索的判断 |
| --- | --- | --- |
| 整体坐标下降层面 | 先完成一轮静态坐标下降/静态不动点，再启动 oracle 坐标下降 | 静态向量没有直接 PASS，不能成为 current；oracle 修改任一其他坐标后，静态 hint 的 context 可能失效，还需维护两套轨迹 |
| per-sweep | sweep 开始时为各坐标做静态定位，然后逐坐标 oracle 提交 | 若统一固定 sweep 起始向量，第一个坐标提交后后续 hint 可能过时；若静态阶段先逐坐标改变向量，则后续 hint 又依赖未认证的静态 context |
| **per-coordinate** | 固定其他坐标，direct fast path；未定界才 static binary search → oracle continuation → commit | 共用准确 Slice，已有 authority 优先，提示就地消费，失败局部回退，按坐标复用/清理 |

以上是基于 context 的设计判断，不是三个方案的性能对照。选择 per-coordinate 的代价是每个新
Slice 仍可能付出静态探测成本；最终无变化 sweep 中能够由动态 cache/predecessor 重验定界的坐标
不付静态成本。同 Slice、anchor、guidance/search policy 和窗口才可复用已完成的静态搜索结果，
不能用不同 context 的缓存省略必要比较。

```text
current = directly_verified_baseline
repeat sweep:
  for d in canonical_dependency_order:
    slice = freeze(other_coordinates=current except d)
    state = consume_direct_evidence(slice, current, previous_boundary[d])
    if state.boundary_unresolved:
      anchor = freeze_local_anchor(state.direct_pass_upper)
      hint = static_binary_search(slice, anchor, state.dynamic_window)
      state = oracle_continue(state, hint)
    boundary = state.direct_boundary
    commit only boundary.floor's direct PASS
    release_unused_prepared_environments()
until no coordinate changes
```

direct fast path 的 cache 读取只消费当前精确执行 context 中真实可复用的动态结果，不调用 ty、
不建立 anchor。
已知直接通过点与其候选直接前驱的合格拒绝可直接定界；通过点为首候选时无需 predecessor。
所有消费仍做同 Slice 的动态非单调/冲突校验，不能在已有反例时提前返回成功。

有效 history 只提供当前 floor 的直接 predecessor 位置；在当前 context 取其直接结果，未命中可
执行一次真实 oracle 重验。Rejection 立即以已有 current PASS 定界；PASS 收紧动态上端 U；
Indeterminate 停止 Cell。该重验属于动态操作，不受静态 NO_HINT 规则保护。fast path 不为寻找
更多证据发起额外全量执行；没有已可消费结果或有效 history 就进入 guidance。
cache miss 的真实 predecessor 重验仍按普通 oracle 路径收集附属静态诊断，但不启动静态二分。
其结果已定界时，不再为 guidance 获取 anchor 或新增 ty；仅 cache hit 的定界路径无任何新进程。

已能定界、无更低未决候选时跳过静态阶段；没有可用 `S_slice` 或没有 static evaluator 的普通
算法 seam 返回 NO_HINT。两者都不新增静态进程。空候选空间仍由 oracle/现行准入规则处理，
静态阶段不报告 NO_PASS；仅 `S_hi` 不可用不构成跳过 local guidance 的理由。
每个坐标至多一次静态阶段；oracle 进行期间不再为修正 hint 启动第二次静态二分。

### 6.2 Interface 与 scope

`CoordinateSearch` 拥有 direct fast path、两阶段编排、两个独立窗口、静态二分、hint 消费和动态定界；
`_ProposalRunner` 编排实际 prepare/verifier 执行，持有环境和动态 cache；ty 统一委托 StaticEvaluator
及当前 Run cache。建议的 search-facing module interface 为：

```text
lookup_direct_in_slice(...) -> 已有直接结果（只读，无 prepare/ty/verifier）
inspect_in_slice(StaticProbeRequest) -> StaticProbeObservation | StaticProbeUnavailable
evaluate_in_slice(SearchProbeRequest) -> ProbeEvidence
finish_coordinate(...) -> cleanup unused prepared environments
```

`StaticProbeUnavailable` 可以只有实际 Attempt 和 preparation facts，没有 Proposal；不能因此
补造 TyCheck 或 StaticComparison。所有静态返回类型均无 compatibility status、FailureRecord authority 或
预测 PASS/REJECTED。删除原代表点 `StaticOnlyEvidence`、`regions` 和 disposition 传播 interface，
保留新纯静态 observation；两者不能混为同一种“跳过执行”的机制。

执行 Slice 由 Cell、source snapshot、SourcePlan、ExecutionPolicyIdentity、活动依赖和其他坐标
精确值界定。静态 hint 另外绑定 S_slice 的 Proposal/TyCheck、GuidancePolicyIdentity、
SearchPolicyIdentity、冻结候选快照及本次窗口，不由全局 baseline 的可用性决定。
其他坐标任一变化就建立新 Slice；相同 Slice 但换 anchor 也不能复用旧 local delta/fingerprint/hint。
完整 vector 的原始动态/ty 事实按各自 identity 复用，本地比较须绑定正确 anchor，并通过
§4.3 的 StaticComparisonContext 准入；原始 ty cache hit 不证明当前 Slice 可比较。

`S_hi` 始终是全局诊断基线。`U` 的 global regression 可以与 local unchanged 并存；local clean
上端来自真实 TyCheck 的自比较。`U[d]` 在 `C[d]` 外时允许作为只读比较上端及动态 PASS sentinel，
不得加入 CandidateSnapshot、形成 candidate probe request 或被返回为候选 floor。

### 6.3 纯静态二分与可疑边界

令 W 为 fast path 后动态窗口中仍待定位的升序候选，均不高于直接 PASS 上端 U。静态定位序列
`T = sorted(W union {U[d]})`；若 U 在 W 外，它只有 anchor 角色，不计入候选空间。
每次静态 probe 固定其他坐标，只替换 d；谓词为相对 `S_slice` 的 local delta 是否非空。

1. 优先读取合法已有原始静态观测并计算当前 anchor 的 local 比较，缺少候选观测时才实际
   prepare/ty。读取 anchor 的直接 PASS 只作 anchor 准入，不把 verifier 结果作为二分谓词。
2. 检查最低候选；若 unchanged，返回 NO_HINT(`lower-unchanged`)，不宣称其余版本全部 clean。
   若 context 不匹配，立即返回 NO_HINT(`context-mismatch`)；其他不可用返回
   NO_HINT(`static-unavailable`)，并保留原始原因。
3. 最低候选 local regression 时，以 U 的真实自比较作为 local unchanged 上端建立 bracket。
   不要求最高候选相对 S_hi clean，也不额外 probe 虚拟 anchor；若 U 的 TyCheck 不可用，已在
   建立 anchor 时返回 NO_HINT(`anchor-unavailable`)。
4. 对 `lo=regression, hi=unchanged`，取 `mid=floor((lo+hi)/2)`；regression 更新静态 lo，
   unchanged 更新静态 hi。任一点不可用/失败即结束静态阶段，返回 NO_HINT。
5. 直到 hi 与 lo 在 T 中相邻，返回 `StaticHint(suspect=T[lo], clean_neighbor=T[hi])`，保存
   两个 local comparison refs 及 S_slice ref。suspect 必须是真实候选；clean_neighbor 为候选或
   显式 anchor 变体，后者只能引用 U 的真实 Proposal/TyCheck，不制造虚拟候选 observation。
   suspect 是用户所指的“可疑边界版本”。

静态二分使用 `local regression* local unchanged*` 的启发式假设。同 Slice、同 anchor、同 guidance
policy 的已有比较若出现低版本 unchanged、高版本 regression，或同点静态冲突，返回
NO_HINT(`static-inconsistent`)，不产生兼容性的
NON_MONOTONIC/NONDETERMINISTIC，也不扫描全空间修复假设。未观测 hole 不作任何保证。

令 n 为 T 的长度（含可能的只读 anchor），有效搜索 n >= 2。一次冷静态阶段至多
`2 + ceil(log2(n))` 次点查询，anchor 来自已有观察，缓存命中不启动进程；无阶段内
重试，无小区间线性扫描。没有有效 bracket 就回退，禁止为了得到 hint 放宽候选域或增加全量扫描；
很小的空间中端点/二分恰好覆盖全部候选不构成另一次扫描。
“确认”的仅是两个端点实际静态状态及采样邻接关系，不是全局唯一转折或 compatibility boundary。

### 6.4 Oracle 如何消费 hint

oracle 整体从原完整候选域和真实 current PASS 开始。continuation 接续 fast path 仅用动态证据
得到的原动态窗口，不恢复已经动态排除的候选，也不接收静态 lo/hi 作为其动态上下界。

需要继续定位时，有效 hint 优先级高于普通位置 hint；先选择尚在动态未决窗口内的 suspect：

- suspect 动态 PASS：以该点为直接通过上端，向低版本定位；clean 端不必为 hint 而执行。
- suspect 动态 REJECTED：此时才能移动拒绝下端；若 clean 端仍在未决窗口且无直接结果，优先
  执行 clean 候选。clean 若也拒绝，继续向高版本找；不能相信它的静态 unchanged。clean 为只读
  anchor 时复用已知 U PASS，不启动虚拟候选执行；空间内没有 PASS 时照常返回 NO_PASS。
- 任一 oracle ProbeIndeterminate：按现行动态规则停止，不能退回静态判定。

若已消费的真实动态结果使 hint 越界或不再有价值，跳过该提示，不扩大已经动态确定的窗口。
每个 hint 最多引导两个直接候选，其余继续原机械搜索：首次最低候选/普通 hint、小区间升序
线性扫描、大区间确定 midpoint。NO_HINT 使用完全相同的无静态提示路径，不缩候选空间。
oracle 新选向量的 ty 仍可收集/复用为诊断，但不触发本坐标的第二轮静态搜索。

例如静态二分得到 `1.4 regression / 1.5 unchanged`，oracle 先真实验证 1.4。
如果 1.4 PASS，继续探测 1.4 以下；如果 1.4 REJECTED，才验证 1.5 并用其真实结果决定后续方向。
静态阶段无需对 1.0–1.7 全部运行 ty，也没有权力把 1.0–1.4 标成动态拒绝。

报告保存 fast path/两阶段的实际顺序、跳过静态阶段的直接 boundary refs、NO_HINT 的稳定 reason，
以及 hint 的 Slice/anchor/窗口/端点 refs；oracle 选择
记录 `static-suspect`、`static-clean-neighbor` 或普通机械/history reason。引用观察必须先于选择，
不能循环或引用未来事实。新静态 probe 与 oracle probe 分别计数，不把前者算成 verifier 次数。

### 6.5 Correctness 与终止的准确范围

静态 lo/hi 只缩小“哪里可能出现静态转折”的探测窗口；只有 oracle 的 lo/hi 排除兼容性搜索候选。
无通过点结论、current/floor/predecessor 提交及动态非单调检测只消费直接动态 evidence。
既有单调 lower-bound 假设仍会利用一个动态结果跳过未执行点，被跳过点不获得独立拒绝事实。

在确定性且满足 `REJECTED* PASS*` 的固定 Slice 中，改变静态诊断或关闭 guidance 只能改变顺序
和成本，不能改变 floor。任意非单调 oracle 中，不同 probe 顺序可能观察到不同 holes；本文不承诺
全局 floor 或完全相同的非单调发现。观测到直接 PASS 低于直接 REJECTED 时仍按 D003 停止。

静态阶段查询有限、失败立即回退；oracle hint 至多引导两个点后恢复机械搜索，动态更新严格
缩窗或停止，每次坐标提交严格降低，因此不存在 static promotion 纠偏重试循环。静态阶段没有
终止 Cell 的 Indeterminate；真实 oracle Indeterminate 不被 hint 跳过。

## 7. Witness 移除范围

删除生产 composition 中的 `RuntimeWitnessAdapter/Operations` 注入、AST witness planner、
`RuntimeWitnessPlan/Attempt`、`RuntimeInterfaceMissingEvaluation`、`RUNTIME_INTERFACE_MISSING`
failure authority 与所有依赖其拒绝语义的 reader/serializer/CLI 分支。无用途的 adapter/harness
与专属测试一并删除，不留下备用 hard-rejection 实现。

保留历史 E008 及归档证据。无需修复当前 target recovery bug 才能满足本 Design；移除该语义路径
即消除其兼容性权限。如未来确需 witness 诊断，另立 Design 定义价值、成本与非权威的数据契约。

## 8. 报告、identity 与展示

### 8.1 Wire 与离线闭环

Schema version 保持 1，按 PF pre-release 规则直接替换当前形状，不提供旧 witness/region reader、
dual-write 或 migration。旧报告需用对应历史版本审计；不能通过新 reader 获得现行 apply authority。
不支持的 contract shape/generation 必须得到稳定的结构化拒绝原因，而非偶然缺字段异常；
具体错误码沿用现有 provenance 错误或在 Plan 固定，不为识别历史变体增加旧格式 parser。

目标 wire 要满足：

- 原始 TyCheck/TyCheckUnavailable table 按实际事实 identity intern；记录 StaticSubject 规范投影、
  TyObservationPolicy 子对象与实际请求 scope，reader 复算 identity。producer Proposal/process
  provenance 与 consumer Proposal association 分别保存；每个关联均复证消费方到该静态投影，
  不要求 producer 与 consumer Proposal ID 相等，不复制 process 或重写原始事实。
  内存的 ABSENT/IN_FLIGHT、锁和 cache handle 不进入 wire。
- 每次 Run/Cell 的静态消费保存独立的 evidence scope 与 membership/producer/consumer refs。
  Journal scope 绑定其 run_id/Cell；report 使用文档内 scope refs，不暴露本机 run_id。
  compare 的两端、anchor 与消费关联必须在同一 scope 闭合。内容相同的 raw facts 可 intern，
  不能因此合并 scope membership。report generation 相同不是 Run 相同的证明；合法 merge/update
  保留来源 scope，必要时整体重命名局部 refs，不重算跨 scope comparison 或把它导入当前 Run cache。
  离线 reader 以所在文档的 scope resolver 执行与在线相同的引用准入，拒绝悬空/跨 scope 关联；
  它验证保存的 provenance 闭包，不伪称能从相同内容推断运行时 cache 的来源。
  scope token/局部 ref 名属于关联 provenance，不进入 raw fact 或 comparison 的语义 identity；
  comparison identity 展开其规范语义 context 后计算。局部重命名不改 delta/fingerprint，但
  必须同步迁移全部 membership 和日志 association，不能借重命名让原本跨 scope 的比较合法化。
- global baseline root 保存 S_hi 的明确可用性；local anchors 保存 S_slice 的精确 Proposal、原始
  TyCheck 和直接完整 PASS refs。原始静态观测与 GLOBAL/SLICE 比较分别 intern，可用 digest/fingerprint
  继续复算；不可用状态不得伪造 digest 或 empty TyCheck，也不能禁止另一种合法比较。
- 不可用比较对象的 identity 由比较种类、已知 Proposal/context、明确 unavailable 状态和
  GuidancePolicyIdentity 决定，使用带 tag 的 preimage，不能与真实空诊断 baseline 碰撞。
- 动态 Evaluation record 只保存执行对象、ExecutionPolicyIdentity 与合格执行事实；静态关联
  放在 evaluation 外的 run/observation association 中，以 typed refs 连接。关联可以改变，不能
  改变动态 evidence ID/payload 或给同 ID 填入不同静态内容。公共聚合对象可同时暴露两类事实，
  但不得让静态 refs、S_hi/S_slice digest 进入动态 authority preimage 或动态 cache key。
- ty failure 作为静态诊断事实保存，不进入兼容性 FailureRecord，不使用 `failure_ref` 假装它是
  ProbeIndeterminate。保留 typed failure code/terminal、D007 安全 process/log references。
- 纯静态探测单独保存阶段、实际 request/Attempt、可选 Proposal/TyCheck/StaticComparison refs、探测
  窗口与 unavailable operation facts；hint 保存 S_slice、两个 local 端点 refs 及 clean 端的
  candidate/anchor 判别，NO_HINT 保存稳定 reason。
  prepare 失败不补造 Proposal。纯静态记录可以是报告可达证据，不要求附加一次虚假的动态 Evaluation。
- 静态阶段和 oracle 阶段的请求明确区分用途，但不靠给同一向量伪造另一套 Proposal identity
  来阻止复用。阶段/窗口进入调度记录，实际执行/结果引用遵循原 request/context 身份；
  从 preparation facts 形成 oracle authority 必须有后续 oracle 选择记录和合格执行事实绑定。
- 动态 Evaluation 的 PASS/REJECTED/INDETERMINATE 可关联任一合法静态可用性状态；直接失败
  authority 继续由共享 D005 classifier 与 reader 验证，不能只接受 producer 自报 disposition。
- 删除 witness 与 region wire records、representative refs、带预测 disposition 的旧 static-only
  variants；新纯静态记录与 selection reason 中的静态 refs 只解释探测路径，不进入 boundary/final authority。
- reader 验证三类 policy、静态投影及消费关联、baseline/anchor/Proposal/Cell/context，按 §4.3
  验证 GLOBAL/SLICE comparability（含 D012 允许关系）后复算 global/local delta/fingerprint、
  阶段及窗口类型、hint 的实际 local regression/unchanged 端点及 T 中邻接关系、选择引用时序；
  anchor 变体必须闭合至直接 U PASS，不能伪造 CandidateSnapshot 成员或候选执行，
  并要求每个 boundary 与 final 都闭合至该精确向量的合格动态证据。执行日志不替代 authority。
- schema、最小 complete/incomplete examples 只由现有 generator 更新；read→write byte-stable、
  merge/update 和 apply-time policy 校验同步覆盖新状态，不能仅改内存模型。

**Journal 与 diagnose 的目标读取面。** 本轮 Journal 采用 `verification-journal-v3` 作为唯一
目标读写形状，替换受影响的旧接口；不增加历史 Journal reader 或双写。Failure entries 继续
保存动态 FailureRecord；另设静态审计区，保存上述 raw fact、静态 scope、消费关联、comparison、
hint/NO_HINT、纯静态 preparation operation facts 与必要的 Proposal/输入闭包。静态区不要求
存在 Failure，不进入 completion/退出码/Failure 计数。check/smoke 不写 floor report，其静态
审计通过 Journal 保留；search 的可移植事实同时写 report。Journal 自含本机诊断所需闭包，
不依赖 floor report refs，不保存 stdout/stderr 原文或完整动态 Evaluation。

Failure 的辅助关联属于独立 audit association，不进入 FailureRecord、Failure ID 或动态
authority preimage，允许以下路径：

```text
Failure -> exact execution association -> TyCheck / StaticComparison
Failure -> oracle probe selection -> StaticHint / StaticComparison -> TyCheck
```

第一条适用于 smoke/check 及普通 oracle，关联核对 Failure 的实际 Attempt/Proposal、Cell、
scope 与静态 consumer；第二条还须核对实际选择该 oracle request 的记录和 hint 消费时序。
prepare failure 可以没有 Proposal，此时只有存在真实 selection 时才沿第二条访问静态提示，
不得补造本 Proposal 的 TyCheck。baseline/anchor 仅作为已关联 comparison 的显式引用可达。
同一 Failure 有多个合法关联时按规范顺序分别展示，不以“同 Cell”或“相近时间”猜测关联，
也不把提示用语解释成 Failure 原因。未关联材料仍仅供 report/Journal 审计。

`diagnose FAILURE_ID` 的参数、选中 report 优先/否则 latest Journal 的查找规则保持不变；
命中 Failure 后只遍历该来源内合法辅助关联，report 命中后不再从 Journal 拼接静态事实。
缺少静态关联时正常展示动态 Failure，缺日志只报告日志不可用，不削弱动态 authority。
未找到 Failure 保持现行 typed error；不搜索静态 fact ID、不枚举历史 runs。

D008 Diagnosis Index 保留原 Failure 索引，另为静态原始操作建立 typed 日志 association：
`(run_id, Cell scope, producer fact ref)` 与
`(report_generation_id, document scope ref, producer fact ref)` 分别映射 D007 relative Process Log。
这些是内部日志定位键，不是 CLI selector；缓存消费者引用原 producer，不新造 process/log。
producer fact ref 使用 typed tag 区分 ty 事实与纯静态 prepare operation fact；后者即使没有
Proposal/TyCheck 也能保存原日志，不能为关联日志补造这些对象。
Journal/Index 原子持久化及 report-side association 更新遵循现行 D008 时序，日志安全规则
遵循 D007。只有 durable 后才能展示可用日志链接；不扫描目录或输出文本推导路径。

### 8.2 三类 policy identity 与执行对象

用三个独立、带 domain tag 的 policy identity 替换统一 evaluation-policy 身份。它们定义规则，
不把具体 source/Proposal 反向塞进 policy，避免 `Proposal → policy → Proposal` 循环。

| Identity | 拥有的规则/配置 | 明确不包含 |
| --- | --- | --- |
| `ExecutionPolicyIdentity` | preparation/安装复证、harness/required surface/marker 语义、实际执行相关 artifact/timeout 约束、configured verifier command/cwd/timeout、原命令 PASS、动态失败与 failed-case 资格、resolve/install/build disposition 与执行工具协议 | ty version/config、静态比较/anchor/bisection、hint 消费、坐标搜索顺序、S_hi/S_slice |
| `GuidancePolicyIdentity` | TyObservationPolicy 子对象及 identity；多重集 delta 与 global/local fingerprint、S_hi 与 S_slice 语义、静态二分、NO_HINT 解释规则 | 动态 verifier authority、实际 anchor Proposal 的值 |
| `SearchPolicyIdentity` | 坐标算法、单调假设、机械 probe 策略、动态 fast path、predecessor 策略、hint 消费、D037 的完整有效候选策略及所用 GuidancePolicyIdentity | 某次运行的具体 observation、动态终态或未冻结候选列表 |

`SearchPolicyIdentity` 是搜索推导策略 identity，不是给 D037 已有 `inputs.search_policy` 候选 DSL
改名；后者作为其规范化输入继续由 D037 拥有。实际 CandidateSnapshot、source、Cell、release
cutoff 等是运行事实，另与 search trace 绑定，不由一个 opaque policy digest 替代。

TyObservationPolicy 子身份按 §4.4 独立复算，GuidancePolicy 显式包含该子对象及其 identity；
不是仅保存调用方自报的 opaque hash。它不包含比较/二分/NO_HINT/hint 算法，也不因为它们改变
而改变原始 TyCheck identity。三类顶层 policy 的划分和动态 execution authority 保持不变。

```text
ExecutionSubject = source snapshot + Cell + SourcePlan + exact request/vector
                 + actual preparation/harness facts + interpreter/artifact closure

Attempt / Proposal identity = applicable ExecutionSubject facts + ExecutionPolicyIdentity
DynamicEvidence identity    = exact Attempt/Proposal + ExecutionPolicyIdentity + dynamic facts
StaticSubjectIdentity       = canonical projection of actual static inputs (see §4.4)
Ty fact identity            = StaticSubjectIdentity + TyObservationPolicyIdentity + actual ty observation/failure
Static comparison identity  = observations + StaticComparisonContext + GuidancePolicyIdentity
Search derivation identity  = execution context + SearchPolicyIdentity + candidate snapshots + trace
```

prepare 失败仍只有实际 Attempt 和已经取得的执行事实，没有 Proposal。直接 PASS 始终绑定完整
执行对象和真实结果；相同依赖版本向量并不充分，source、解析图、artifact、解释器与执行契约都
必须闭合。ty 版本/heuristic 在执行对象和 ExecutionPolicy 不变时变化，不能单独否定该直接证据。
Attempt 只包含按现行时序在启动时已知的身份事实；实际成功解析/安装图在 Proposal 中闭合，
不为 policy 分离把未来执行结果提前写入 Attempt，亦不把被重建环境的临时 locator 当作执行身份。

必须实际拆除间接依赖，不能只在外层增加三个字段：

- Attempt、EnvironmentIdentity、Proposal、FailureScope/FailureRecord 和动态 evidence 的身份链
  使用 execution identity；不经统一 policy、static ref 或全报告 generation 绕回 guidance/search。
- `selected_candidate_evidence_digest` 只绑定本次 exact request 的实际安装选择及执行准入事实；
  选中的版本、来源、artifact 和完整向量不能省略。候选空间/排序/采样 provenance 另由 search
  selection refs 与 reader 验证，不把整份含 search/guidance identity 的 snapshot digest 纳入动态身份。
- 原始 ty cache 以 Run/Cell 隔离的 `(StaticSubjectIdentity, TyObservationPolicyIdentity)` 区分，
  不绑定完整 ExecutionPolicy/Proposal ID、环境句柄、baseline 或 guidance/search 阶段。
  静态投影不能通过整份 Environment/plan digest 间接依赖动态 policy；producer/consumer
  provenance 不进入原始事实身份。StaticComparison 按需纯计算、保存时按比较 context intern，
  无独立 comparison cache；已完成静态定位的复用仍须核对 Slice、anchor、SearchPolicyIdentity、
  候选快照和输入窗口，不据此另建通用 hint cache。
- 动态 cache 以精确执行对象/ExecutionPolicy 为 context，移除 `baseline_digest` 和 ty 依赖。
  一次 Cell search 的动态执行复用仍只由 `_ProposalRunner` 拥有；原始 ty 复用独立由 Run-owned
  cache/StaticEvaluator 提供。本轮不新增跨运行缓存、自动重试或证据导入。
- dynamic evidence payload 不嵌入影响其 identity 的静态诊断。ty 改变后另建静态 association，
  不修改原动态事实，也不把旧静态 association 当作新 policy 下可复现的 observation。

采用以下固定规则标识，各自只进入对应 policy 对象；各对象使用不同的 canonical hash domain。
Plan 的 identity slice 只把这里冻结的语义输入、版本与 canonical 规则映射到具体字段、模型、
producer 和 reader；不得自行改变缓存等价性。StaticSubject 的完整语义 preimage 由 §4.4
六组输入及 `static-subject-v1` 固定，不能在实现中另选子集或以 opaque 动态 policy digest 替代。

| Policy owner | 固定规则事实 |
| --- | --- |
| Execution | `failure-execution-v4`、`configured-execution-only-v1`、原 configured verifier outcome 与原命令 PASS rule；witness authority 不存在 |
| Guidance | 采集子对象 `ty-observation-v1`（含 §4.4 完整采集规则、`run-first-observation-v1`）；外层 `static-guidance-v1`、`global-diagnostic-and-slice-anchor-v1`、`slice-local-static-bisection-v1`、`advisory-v1`、`no-hint-fallback-v1` |
| Search | `direct-first-coordinate-guidance-v1`、`suspect-then-clean-neighbor-v1`、现行机械阈值/前驱策略、完整候选策略与 GuidancePolicyIdentity |

`failure-execution-v4` 移除 witness authority 与静态工具失败的 compatibility 分类入口；纯静态
阶段不发布 compatibility FailureRecord。oracle 中既有 resolve/install/build、configured verifier
与 failed-case qualification 不变，不通过“静态 prepare 失败”增加一种拒绝 cause。

### 8.3 Report provenance 与 apply

report generation 保存并校验 execution/guidance/search 三类 policy facts/identity，以及现行
source、SourcePlan、声明、target Cells 和生成器事实。完整报告仍须属于同一 generation 才可
merge；apply 继续要求当前支持的执行契约与报告/search provenance 资格均满足，`--force`
不豁免 policy 不匹配。不能因 direct evidence 的身份独立，就自动允许跨 generation merge/apply。

ty/guidance 改变可以令旧 hint/trace 不再满足当前搜索 provenance，进而使旧完整报告不能直接
merge/apply；应诊断为 report/search provenance 不匹配，而不是“原 verifier PASS 失效”。
执行 policy 不匹配与 provenance 不匹配须有可区分的结构化原因。离线 reader 必须分别复证
三类身份，不以一个统一摘要取代 authority/provenance 两层检查。

源码 identity 继续遵循 D014，不为本次分离投影掉 `pyproject.toml` 中的 ty/PF 配置。修改实际
快照文件仍改变执行对象；不能假定用户代码不会读取该文件。本 Design 的“仅 guidance 改变”
性质测试固定源码/解析图/解释器/执行配置，只改变外部 ty 版本、有效 ty override 或 heuristic。
若实际执行对象变化，必须独立满足新对象的证据资格；本轮不新增 source-drift 豁免。

### 8.4 用户展示

最终 summary/check status/floor 只依据动态结果；ty 有 diagnostic 或不可用不把动态 PASS 显示成失败。
辅助静态状态写入独立 Journal 审计区，不生成终止 failure ID、不改变 Run 聚合；diagnose 仅
展示与所查询动态 Failure 显式关联的静态材料，未关联事实不提供独立 CLI 查询入口。
search 的 Journal/进度显式区分 `static-probe` 与 `oracle-probe`，前者可结束于静态事实或
NO_HINT，无需伪造 compatibility completion。Probe window 展示标明 static/oracle，不把静态
lo/hi 展示为已确认的通过/拒绝范围；阶段记录、role 与聚合投影由 D008/D006 同步迁移。
Report 保留源码位置、code、delta、process 信息；diagnose 仅在上述关联范围展示这些信息；explain 如展示 selection reason，只说明
“为何探测此版本”，不展开大量 static diagnostics，也不声称“低于此版本必然不兼容”。
global delta 与相对 S_slice 的 local delta 标注比较对象；不能把 local unchanged 显示成相对最高
版本 clean。fast path 跳过静态搜索展示真实直接证据/复用，不生成 ty 进程、hint 或静态探测耗时。
TyCheck cache hit/negative hit 同样不生成新 ty stage/进程或复制耗时，独立 diagnose 只读取已持久化
事实；cache miss、cached unavailable 与 lack-of-comparison 的原因不可混为同一个“ty 失败”。
不制造 witness stage、静态跳过测试的成功活动或虚假 verifier 时间。

## 9. Owner 迁移对照与实施边界

| 目标 owner / 位置 | 替换规则或 interface |
| --- | --- |
| D001 命令语义、完整验证摘要 | check 的动态含义；static availability 不影响兼容性 disposition；diagnose 保留 Failure ID selector，仅展示合法关联静态材料 |
| D002 §7、§11 | StaticEvaluator lookup/collect/compare、Run-owned TyCheckCache、同 key 并发去重；替换 runner 私有静态 cache，保留环境/动态 owner 与 public tests |
| D003 §2–§3、§5–§8、§11 | direct fast path → local 静态二分 → oracle continuation；改写 §3 不变量 10 和 EvaluationCache 表，分开物化重建、ty 去重与精确动态复用；替换 region，保留 baseline seed/predecessor |
| D004 §2–§3、§6–§12 | StaticSubject 投影、原始 TyCheck/Unavailable、TyObservationPolicy、negative cache、comparison 准入、S_hi/S_slice、GuidancePolicy；移除 witness |
| D005 failure 分类、authority、identity | 删除 witness/static 工具终止权限；ExecutionPolicy 与执行对象分离，保留动态资格 |
| D008 §2–§3、§7–§9 Journal/Index/diagnose | capture 前创建 Run ty cache，typed refs 隔离、并发等待；Journal v3 独立静态审计区与内部日志索引、Failure 辅助关联和持久化时序；静态无 disposition 聚合 |
| D012 preparation / D002 EnvironmentFactory | Attempt/Environment/Proposal 仅绑定 execution identity，提供已复证事实供静态投影；exact artifact 选择与搜索 provenance 分离，comparison 消费既有 original/relaxed 关系，harness 构造规则不变 |
| D014 §1–§4 | 三类 policy 与 TyObservation 子身份、静态投影复算、raw fact intern 与独立 scope membership、Failure 辅助关联、producer/consumer refs、comparison 准入、reader/生成物/provenance |
| D037 候选策略消费关系 | 保留候选 DSL/采样规则，由 SearchPolicyIdentity 消费；exact execution 选择与 snapshot 搜索资格分别绑定 |
| D006 与 CONTEXT/README | 动态结论与辅助诊断措辞；移除已失效 stage 和术语 |

候选 DSL、采样、baseline selection 域仍由 D037 拥有；harness/environment 构造仍由 D012 拥有。
不修改 D007 日志完整性资格，不借本次整改放宽执行 authority。已有归档与 E008 历史附件不改写。

实施由 [P042](../plans/P042-pf-static-guidance-authority.md) 跟踪，按 identity/static fact foundation → 移除 witness/static authority →
StaticEvaluator/Run cache 及环境时序 → per-coordinate guidance → report/wire/reader → CLI/docs → E008 重跑与
owner 吸收的有序 slices 映射以下全部 AC，记录文件迁移、实际命令、结果与偏差。先闭合 identity
再修改 search。foundation 的第一批 public compare/identity 测试必须独立覆盖 original→relaxed、
同一 baseline 的两个 relaxed、非法 harness，以及相同 ty policy 下不同 Python/surface；不能
只在 search 集成中验证准入。cache slice 同步闭合 Run refs、owner/等待者环境保留、取消/收拢/
释放/唤醒时序和 AC25，完成后再接 search。Plan 可按 public seam 合并重叠证据槽，但必须保留
全部 AC 到证据的映射；CLI/owner 吸收与临时 fixture 清理列入收尾清单。此处不是实施 Plan。

## 10. 验收标准与证据要求

| AC | 必须成立的目标 | public seam / 验收证据 |
| --- | --- | --- |
| AC1 | import/member/其他 ty regression + verifier PASS 得到 PASS；fallback/optional 路径不早拒绝 | lower ty/verifier adapters 装配真实 RuntimeEvaluator；保留 diagnostic/delta 正向断言 |
| AC2 | 所有生产兼容性拒绝均来自当前允许的动态 authority，static/witness 不能认证 boundary | FailurePolicy、ReportStore 的语义测试；伪造静态 authority 的报告被拒绝 |
| AC3 | ty baseline/proposal failure 不阻止 verifier；静态 prepare/ty/比较失败只能返回 NO_HINT，不产出 ProbeIndeterminate | smoke/check/search module graph；NO_HINT、无 Proposal prepare 失败与不可用状态 round-trip；oracle 请求前不消费静态准备失败 |
| AC4 | check 保留 HarnessBaseline，capture ty 失败仍验证 lowest-direct；prepare 失败仍遵循原规则 | CompatibilityChecker/VerificationRunner，核对真实请求 role、argv、终态和 Journal 聚合 |
| AC5 | 先消费直接证据，未定界才 local 静态阶段 → oracle continuation；每个动态窗口更新均有直接 evidence | CoordinateSearch recording evaluator；静态 phase verifier 调用为零，fast path/两阶段窗口与 current 提交分别断言 |
| AC6 | 相对 S_slice 对数次二分，suspect/clean 端引导 oracle；缺提示退回原动态窗口的机械路径 | minimize seam；local 端点/中点、冷查询上限、suspect PASS/REJECT、clean REJECT、NO_HINT reason、无全扫描 |
| AC7 | 在确定性单调 oracle 中，任意静态状态分配/静态不可用不改变 floor | 有限候选矩阵：guided 与 mechanical 对照；涵盖全部 PASS、无空间内 PASS、虚拟 sentinel |
| AC8 | baseline 复用、fast path 内当前 context predecessor 重验、直接非单调/冲突检测仍成立；跨坐标变更令旧 hint 失效 | D033 public cases；多坐标/多 sweep scope、current PASS 但 global regression、局部非单调仅 NO_HINT |
| AC9 | 坐标内合法静态物化环境可供 oracle 复用；NO_HINT/ty unavailable 不单独清理合法环境；冗余释放要求精确动态等价及可用替代环境，释放后允许重建 | lower adapters 调用、oracle 命中非 bracket 中间点、对数数量约束；timeout/坏输出后复用 prepare；跨 Proposal 同 ty key 仍保留各自环境、clean 替代/在途不可替代、跨 sweep 重建 |
| AC10 | wire/reader/identity/merge/update/apply 全链只接受目标契约，静态 refs 无兼容性权限；不支持的 contract 得到稳定结构化拒绝 | public ReportStore/authorizer 正反例、byte-stable round-trip、generated artifacts 无漂移；迁移验证用旧 schema-v1 witness/region report 确认确定性拒绝，永久测试覆盖当前 contract/authority 准入 |
| AC11 | oracle 中既有 resolve/install/build 与 failed-case 动态资格保持；完整 PASS 仍要求原命令 | execution qualification/配置命令 tests；静态 NO_HINT 不伪装成动态失败，oracle 真正选中后的执行结果按原规则处理 |
| AC12 | E008 Python 3.10–3.12 declared-lowest 确实进入原完整 unittest，记录真实结果 | 固定 MkDocs 源码/配置下的新 check run、实际安装向量、argv、ProcessResult 与日志；不预设 PASS |
| AC13 | 新 MkDocs search 的 Markdown predecessor/final 均有动态 authority | 新 run 的报告 reader、boundary refs 和完整 final PASS；与旧 run 分开保存，不手改旧报告 |
| AC14 | 用户展示、owner 吸收、文档状态与实现一致 | CLI semantic fragments、文档/链接/生成检查、Plan 逐项审计，临时 D/P 同变更归档 |
| AC15 | S_hi 固定作全局诊断，S_slice 作局部 guidance；前序 harmless regression 不污染 local predicate；COMPARED 必须通过显式 GLOBAL/SLICE 准入 | global regression/local unchanged、global UNCOMPARED/local COMPARED、多重集；同 ty policy 下不同 Python/surface/冻结坐标/context 返回 UNCOMPARED(context-mismatch)；D012 original→relaxed、同 baseline relaxed 的合法关系与非法 harness 差异；public compare/reader round-trip |
| AC16 | anchor 绑定精确 Proposal/TyCheck/PASS；换 anchor 不复用比较；候选域外 U 仅作只读上端 | 同 Slice 不同 anchor、不同 GuidancePolicy 的比较隔离；候选域外 anchor bracket、单个域内候选、无域内 PASS，reader 拒绝伪造候选/floor |
| AC17 | 相同执行对象与执行事实下，仅 guidance 改变不改变动态身份/authority；ExecutionPolicy 改变隔离动态证据，但相同静态投影/采集策略仍可复用 TyCheck | 公共 identity factory/ReportStore/评估 seam；仅外部 verifier timeout 或动态 classifier 改变：Proposal/动态 cache 隔离、ty 命中；覆盖执行身份链与静态 consumer association，真实 source 变化另测 |
| AC18 | 动态 authority 独立不授权跨 search provenance merge/apply；真实 source 变化仍改变执行对象 | ReportStore/ApplyAuthorizer 区分 execution 与 provenance mismatch；固定 source 的 ty/heuristic 变化与实际 pyproject 配置变化分别验证 |
| AC19 | 精确直接证据或有效 predecessor 重验已能定界时，不获取 anchor、不新增 prepare/ty guidance 成本 | 最终 no-change sweep、缓存 boundary、predecessor cache miss 真重验、PASS 后才进入 local phase；动态 Indeterminate 仍停止 |
| AC20 | 同一 Run/Cell/等价 ty 请求跨 capture、static、oracle 消费时只执行一次；同一 TyCheck 可对不同基准重算 | StaticEvaluator public lookup/collect/compare 与真实消费者图、recording TyOperations；调用次数与不同 delta 的正向语义断言，不要求实现 V_guess |
| AC21 | 改变 baseline/anchor/二分/hint policy 不导致原始 ty miss；六组 StaticSubject 输入和版本化 canonical projection 固定缓存等价性；同采集策略不自动准入 comparison | 公共 factory/reader 对六组输入与采集子身份的变化矩阵；相同 sdist 不同安装内容、有序 roots/config 改变、未闭合外部输入、未知 projection version；合法重定位/不同 Proposal 同投影正例；拒绝伪造关联及 context |
| AC22 | typed ty failure Run 内 negative-cache；cached unavailable 不产生兼容性 disposition，也不等同 CacheMiss | timeout/异常 exit/坏输出重复 collect、后续 verifier PASS；prepare 失败、缺 baseline/anchor 不填入原始 negative cache |
| AC23 | 环境释放后 TyCheck/Unavailable 可 lookup/compare；重建环境并复证同静态投影/采集策略时不重跑 ty，动态证据仍独立验证 | 真实环境 close 后无资源句柄查询；重建相同/不同静态投影分别断言 adapter 调用与隔离，涵盖不同 Proposal 共享 TyCheck 而不共享动态 authority |
| AC24 | baseline capture 前已有缓存；lookup/collect/compare 共用 Run/Cell 隔离，原始事实 identity 不绑定 Run；离线 refs 在保存的 scope 内闭合 | 同 evaluator 顺序/重叠 Run 的 typed refs 测试；key/payload/policy 相同仍拒绝跨 Run/Cell 与已关闭引用；report 同 generation 不合并来源 scope、reader 拒绝跨 scope comparison |
| AC25 | 同 key 并发请求只启动一个 ty 操作、共享终态；等待者不占 ty permit；runner 保护 owner 环境至进程收拢，取消/异常不泄漏资源或等待 | foundation 内可控阻塞 TyOperations 测试：成功/Unavailable、Run cancellation、在途 close/verifier 禁止、同 key 不同 Proposal 环境保留与精确冗余释放，完成时序通过 public runner seam 验证 |
| AC26 | Journal 静态审计与动态 Failure 分开；diagnose 仅按 Failure ID 遍历合法辅助关联；原 producer/log refs 独立持久化，命中不伪造执行 | ReportStore/Journal v3/Index round-trip、check/smoke 无 Failure 仍保存审计且不计失败；execution/selection 两路径、prepare 无 Proposal、未关联静态事实不可单独 diagnose、report 命中不拼 Journal、缺日志、跨 Proposal 原 producer 保留及静态关联不改 Failure ID |

AC7 的有限矩阵是本契约的安全性质测试，不能只断言测试实现复制出的 probe 列表；并为具体
hint 策略保留一个确实改变顺序的正例。直接动态失败、static regression + runtime PASS、无 hint
与 hint 指错方向均要覆盖。永久负例仅保留当前权限/错误合同要求，不枚举旧语法变体。
AC10 的历史报告 fixture 属于临时迁移验证，完成后移除；保留稳定结构化拒绝与 authority
准入的当前契约测试，不以兼容读取历史格式作为验收条件。

真实实验从 PF 仓库根启动并使用绝对 MkDocs 路径，按 AGENTS.md 在完成风险检查后沙箱外运行。
固定源码/配置、解释器和实际向量；新 registry snapshot 与历史不同则明确说明。完整 E008 矩阵
优先复测全部五个 Python Cell，AC12 至少覆盖问题所在三项。测试通过、fixture 回放、真实 check、
真实 search 和性能测量分别记证，不互相替代。当前按 P042 推进实施；MkDocs check（AC12）已有
E009 真实结果。首轮 search 产品运行 5/5 Cell SUCCESS，但写出的 128 MiB 报告被 64 MiB
reader 拒绝，不能当作 AC13 证据；正在以 content/subject intern 重跑，结束前不写通过。

## 11. 成本估算与非目标

E008 保存的 search 数据为 190 次完整 unittest、累计 1,486.01 秒；36 次 witness missing rejection。
若 probe 集合和平均 verifier 成本不变，额外执行这 36 次 verifier 约增加 281.56 秒，即 verifier
数量和原累计 verifier 时间均增加约 18.9%；新增部分约占假设新累计 verifier 时间的 15.9%。
按全部可见子进程原累计 1,739.90 秒作分母则为 16.2%，与前述 verifier 口径不同，均非 wall time。
这些只是固定 probe 集合与平均成本的局部算例。

移除 region 跳过、新增纯静态二分 preparation、环境保留/重建、改变 oracle 顺序和不同失败提前
返回都会改变次数与成本，因此上述估算不是整改总成本上限。E008 未保存精确命令 wall time，
不能据此确认 wall time 增加约 20%。实际耗时须在新 run 中测量，受控性能结论需独立对照。

新实验分别记录每坐标/sweep 的 direct fast path 命中、静态阶段跳过、静态请求/cache hit、
prepare/ty 耗时、local anchor 可用性与 NO_HINT 原因、oracle 次数/耗时及保留环境峰值。
ty 另外区分逻辑请求、首次实际调用、positive/negative cache hit、进行中操作等待；累计进程耗时
只计算真实 owner 的一次调用。记录 Run 内原始事实数量/缓存内存与并发等待，不把对数级物化
环境数量当成 Run-lived TyCheck 缓存的数量上限。
对数次静态 probe 只限制请求数量，不证明端到端足够快；静态提示是否减少 oracle 次数、最终
无变化 sweep 的 fast path 节省多少成本，均需实测。

Plan 的成本观测按坐标/sweep 分开记录：hint 产出率（有效 hint / 实际启动静态阶段）、hint
消费率（至少一个特殊 oracle probe 被选择的 hint / 已产出 hint）、NO_HINT 各原因数量及占
已启动静态阶段比例、oracle 对静态物化环境的复用数量/请求比例；分母为零标注不适用。
已有 direct evidence 复用与真实新增 verifier 次数分别计数，不把“生成 hint”当成减少执行。
非单调只有被观察到才触发 static-inconsistent；NO_HINT 少不证明提示有效，NO_HINT 多也不
证明准备成本全部浪费，因为 oracle 仍可能复用环境。context-mismatch 偏高先核查准入实现。
净收益须与固定 source/Cell/候选快照、ExecutionPolicy、机械策略和资源配置的无 guidance
对照实测，分别记录冷/热缓存条件与真实 wall time；不能从单个 run 的提示率推算节省时间。
这些是成本观测与实验解释要求，不新增正确性 AC 或正收益阈值；本轮不增加自动禁用/自适应
开关。未来默认跳过静态阶段的决定属于 SearchPolicy 变更，须基于净收益证据，authority 不变。

本轮不实现 CFG、dead-code/exception-flow/conditional-import 分析、oracle 失败静态证明、
high-recall attribution、完整候选静态扫描、全局 V_guess 搜索、复杂全局搜索或新 testcase selection；
不做跨 Run 持久化结果缓存/自动导入，不新增独立 comparison cache 或通用 hint cache 服务。

未来 ty × dependency-testcase mapping 可单独设计：静态定位选择真实执行子集；合格动态失败
可以早拒绝，子集通过仍继续原完整 verifier。来源可包括 testmon/coverage/runtime trace/历史失败，
但必须先定义 mapping 失效、隔离、资格和完整 PASS authority。它不构成本 Design 的验收依赖；
现有 failed-case pruning 继续按已交付契约工作。

## 12. 本轮评审意见落实

本节记录 2026-09-07 用户提供的 RXXX 评审、TyCheck 独立缓存提案及后续核对的设计修订，不另占 Review 编号。
用户已明确接受最终整改建议并要求修改方案；本版据此收敛为已接受 Design，尚未完成实施验收。

| 意见 | 本次修订落点 | 验收映射 |
| --- | --- | --- |
| Required：分离全局 baseline/local anchor | §4、§6；固定 S_hi，direct fast path 后冻结 S_slice；局部比较独立可用，候选域外 anchor 只读 | AC6、AC15–AC16 |
| Required：拆分 policy identity | §8；执行对象与三类 policy 分离，拆除动态 cache/静态 refs/选择摘要的间接耦合，区分 report provenance | AC10、AC17–AC18 |
| Required：TyCheck 使用静态投影 | §4.4–§4.6、§8；StaticSubjectIdentity 替代完整 Proposal cache key，原始事实身份与 producer/consumer provenance 同步解耦 | AC17、AC21、AC23、AC25–AC26 |
| Required：比较准入闭合 | §4.3、§4.5、§6、§8；StaticComparisonContext 固定 GLOBAL/SLICE 准入，复用 D012 合法 harness 关系；不匹配 UNCOMPARED(context-mismatch) | AC15–AC16、AC21 |
| Required：diagnose/Journal 寻址闭合 | §4.7、§8.1/§8.4；保留 Failure ID selector、execution/selection 两条辅助路径；Journal v3 独立静态区和内部日志索引，不赋予静态 Failure ID | AC3–AC4、AC10、AC26 |
| Required：compare 的 Run 隔离 | §4.5、§8.1；cache 签发 typed refs，compare 显式 run_cache；离线验证 scope membership，generation 不充当 Run identity | AC20、AC24 |
| Required：环境冗余与 ty 去重分离 | §4.6、§5.4；释放需要精确动态等价和 clean 可用替代，ty failure 不使 prepare 失效；环境时序紧跟 cache | AC9、AC23、AC25 |
| Required：冻结静态投影语义 | §4.4、§8.2；六组穷尽输入、canonical/version、闭包不成立时静态不可用；Plan 只映射，不决定等价性 | AC17、AC21 |
| Required：成本口径 | §11；主口径改为 verifier 时间增加 18.9%，新 verifier 总时间占比 15.9%；明确原 16.2% 的全部子进程分母 | 固定 E008 附件算例核对；不作运行性能验收 |
| Recommended：direct evidence fast path | §5–§6；已定界不支付 guidance，真正 predecessor 重验保持动态终止规则 | AC5、AC8、AC19 |
| Recommended：Guidance 参数/基础测试/迁移顺序 | §4.5、§9；guidance_policy 非第四类 policy，比较准入第一批测试、cache 同批资源时序；明确 D003 不变量 10/EvaluationCache 与 fixture 收尾 | AC9、AC14–AC15、AC21、AC25 |
| Recommended：guidance 成本观测 | §11；提示产出/消费、NO_HINT 原因、环境复用及受控净收益；不把非单调频率当已有证据，不增加自动开关 | Plan 实验槽；无新增正确性 AC/正收益门槛 |
| Retained：全部坐标内静态物化环境 | §5.4；对数数量、NO_HINT 后可复用合法中间点、坐标/Cell/异常清理 | AC9 |
| 追加：TyCheck 独立 Run 缓存 | §4.3–§4.7、§5.4、§8；采集子身份、StaticEvaluator 基础接口、Run/Cell 隔离、negative cache、并发去重及持久化关联；不实现 V_guess | AC20–AC26 |

仍保留 witness 删除、静态失败 NO_HINT、suspect/clean 的动态消费规则与完整 verifier PASS authority。
本节所记评审完成已接受 Design 的修订；后续已建立 [P042](../plans/P042-pf-static-guidance-authority.md)，
S1 实施已开始；完整行为验证与 owner 吸收尚未完成。

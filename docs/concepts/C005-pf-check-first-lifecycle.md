# C005 — PF 以 check 为稳态的库作者周期

- **状态：** 开放
- **日期：** 2026-09-08
- **性质：** 非规范性 Concept；不授权实施、不改变现行命令/apply/cache 契约
- **来源：** 库作者长期开发周期中，现行快照绑定 floor 与 apply 整组准入过严，search 被当成日常路径
- **现行对照：** [D001](../designs/D001-pf.md)、[D002](../designs/D002-pf-implementation.md)、
  [D003](../designs/D003-pf-search-algorithm.md)、[D004](../designs/D004-pf-ty-enhancement.md)、
  [D007](../designs/D007-pf-process-output.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D014](../designs/D014-pf-report-schema.md)
- **相关评审：** [R008](../reviews/R008-pf-search-performance-review.md) §7 否决的是把跨运行
  cache 当成当前契约的 PASS 权威；本文讨论的是带准入策略的统一观察存储
- **相关构想：** [C004](C004-pf-evidence-respecting-optimistic-monotone-search.md) 处理一维
  单调性假设；[C006](C006-pf-test-dependency-association.md) 处理源码/测试与依赖的关联分析，
  不在本文范围
- **第一刀 Design：** [D044](../designs/D044-pf-check-first-and-apply-receipts.md)（已接受待实施）
  覆盖 check 稳态、smoke/check 最小验证序列、报告中的有限 apply 回执与声明/报告事务；
  本文仍开放，跟踪增量 apply、跨 Run 观察复用与可回滚历史。后两者不是 D044 前置

本文不定义当前或已接受的目标契约。已移交第一刀的目标以 D044 为准，其余仍是待验证设想。
跨 Run 观察复用、增量 apply 与可回滚历史仍须另建 Design。D044 不接入 Git，也不维护 check 历史。

## 1. 构想

PF 主要被库作者使用。依赖下界应是开发周期里变化很慢的声明，而不是每次代码或测试改动都要
重新证明的搜索结果。

设想的周期：

```text
接入     一次完整 search → apply 严格准入并写入声明下界
稳态     每次 CI / release 跑 pf check，验证当前快照上声明下界仍成立
治理     新增 extra / 新增受管依赖 / 主动重搜时，做增量 search，再 apply 增量结果
```

权威规则（已确认的工作假设）：

```text
稳定性假设引导要不要搜、搜哪里
当前快照上的 check / oracle 证据决定真值
错误启发式只准多跑 verifier 或退回完整 search
不得写出与当前失败证据冲突的 floor
```

`pf check` 在现行命令面上已经「不搜索、不写报告、只验证声明下界」。缺口是产品把
search + 快照绑定报告当成主产物：任意源码变化换 generation，apply 要求整组声明与完整矩阵，
Run 内缓存随进程结束丢弃。长期循环因此不友好。

## 2. 已确认的工作假设

1. **稳态只承认 check。** 代码或测试的日常修改不期待重新 `pf search`。check 按实际 terminal
   区分拒绝与无法判定；CI 不必在失败路径上自动 search。
2. **观察缓存是一套系统。** 若上跨 run 持久化，运行时命中与长期命中共用同一存储与同一套
   完整 identity；哪些情况算命中、哪些算过期由**准入策略**决定，不按「内存一份、磁盘一份」
   分裂实现。
3. **apply 只为增量搜索结果放松。** 不默认放宽普通 source drift，不对手工乱改 requirement
   放行。增量之外的授权仍按现行严格规则。
4. **有限回执与可回滚历史分开。** D044 已接受的目标是在报告记录当前应用目标的成功回执；它不保持跨报告
   历史、不提供用户回滚。可复原历史前态的本机记录仍是本 Concept 的待证方向。
5. **关联分析不在本文。** 源码/测试与依赖的影响面分析见 C006，可独立进入或关闭。

本文沿用现行词：**受管依赖** 仍是搜索集合（`managed-deps`）。上次合法 apply 写进声明的精确
`>=` 在构想里称为 **PF 地板**，与受管集合不是同一件事。未进入 Design 前不写入
[CONTEXT.md](../../CONTEXT.md)。

D007 的 **Output Cache** 仍只是进程内 Process Log 正文投影，与本文观察存储不是同一对象。

## 3. 稳态：验证当前声明

日常 check 的目标契约已移交 [D044](../designs/D044-pf-check-first-and-apply-receipts.md)。
它每次验证当前声明，不要求声明来自 PF，不恢复旧报告的精确向量，不维护 check 历史或缓存。
本 Concept 后续讨论增量治理时，不能把一次 check 通过提升为新快照上的历史 search 最小性证明。

## 4. 统一观察缓存

**2026-09-10 范围调整：** 本节已从 D044 移出。D044 的每次 check 都重新 prepare 并执行完整
verifier，不依赖本节。未来 runtime 准入必须先证明实际执行环境/外部工具与安装产物的身份闭包；
仅有源码快照、版本图或 artifact alternatives 集合不足以授权跳过 verifier。以下仍是待证构想。

现行实现按生命周期裂开：

| 存储 | 范围 | key 口径（现行） |
| --- | --- | --- |
| `TyCheckCache` | 一次 Verification Run | 规范静态投影 + TyObservationPolicy |
| `EvaluationCache` | 进程内、精确 Proposal | `proposal_id` + execution policy |
| `_ProposalRunner` | 一次 Cell search | 完整向量上的 prepare/full、FailedCaseSet、未关闭环境 |
| Candidate query | 一次 invocation | dependency / source |
| Output Cache | 当前 CLI 进程 | 单个 Process Log 正文投影 |

跨 Run、跨 Cell、已关闭环境即使 key/payload 相同也被拒绝（D004）。`package-floor.json`
不是 Evaluation cache（D001）。R008 把「跨运行 Evaluation cache」与「不同 Proposal 共用已跑
verifier 的可写环境」列为不采用方向。

### 4.1 一套存储

构想：观察记录只有一种。Run 内内存层是同一记录的热投影，进程结束后仍可按策略再读。
不维护平行的「短期 cache API」和「长期 cache API」。

记录尽量保存完整 identity 与完整观察，而不是先按某一种命中规则折成短 key。后续策略可以
决定「这些字段必须逐位相等才准入」，也可以决定「忽略其中一维当作过期」。换策略不应强迫
迁移另一套存储。

完整身份至少应能区分（待证清单，不是 wire）：

```text
source snapshot（含 PyprojectIdentity）
Cell（target, CPython minor, extra surface）
SourcePlan identity
ExecutionPolicy / GuidancePolicy / TyObservationPolicy
Proposal / managed vector / artifact hashes
configured verifier 身份
观察种类（ty 原始事实 / 完整 verifier Evaluation / registry 候选 / …）
```

可写 PreparedEnvironment **默认不作为可准入的跨进程记录**。存储可以记载「该 Proposal 曾
物化过」，但不把已跑 verifier 的 venv 借给另一次 invocation。这保持 R008 对共享可写环境的
否决，而不需要第二个 cache 类型。

### 4.2 准入策略，不是命中即权威

lookup 返回记录；**当前契约是否可采用这条记录**由策略回答。同一条记录上可以叠不同策略，
例如：

| 策略 | 可能用途 |
| --- | --- |
| 身份与当前 `C` 完全闭合 | 当作本 invocation 的直接观察，不重跑对应操作 |
| 快照已变 | 不得当作当前 floor / PASS；至多当搜索 hint（R008 的 hints 仍要求本 invocation 重做权威证据） |
| extra surface 不同 | 运行时观察 miss；候选查询仍可能命中 |
| 仅静态投影相同 | 可复用原始 TyCheck，不复用动态 Evaluation |

首批值得证的策略是：**同快照、同 Cell、同向量、同执行身份 → 准入为直接观察**。这覆盖崩溃
续跑、同快照上 check 之后再 search、CI 重试。跨快照复用不是第一刀；没有 C006 时，源码变化
应走 check，而不是从旧快照偷 PASS。

报告、Journal、Process Log 仍各有职责。观察缓存不替代 `package-floor.json` 的 apply 权威，
也不替代 Journal 的失败审计。

## 5. 增量搜索与只放松增量 apply

增量在这里拆成两件，都还待证：

- **增量证明：** 矩阵里新增 Cell（典型是新 extra；平台轴已有 PLATFORM_SCOPED 可对照），
  本次只搜索新增 Cell。
- **增量定界：** 同一 Cell 里新增受管依赖，本次主要对该坐标 `find_floor`；其它坐标的声明
  下界先经 check 确认仍成立。

加新 extra 时，旧 extra Cell 的安装集往往不变。这首先是覆盖与 apply 问题，不依赖 C006。
加一条 base 依赖会改变所有 Cell 的已安装图；「其它依赖下界一般不变」只是乐观假设，check
是反例闸门。

### 5.1 现行 apply 为什么挡增量

选中包当前声明必须等于报告 original（WRITABLE）或本次 intended 整组投影（NOOP）。group
增删、新 extra、新受管名都是不可 waiver 的 `DRIFTED`。任一已有 root 失败、selector 内缺 Cell
或 `NON_MONOTONIC` 同时挡住默认与 `--force`。`--force` 只 waiver 源码层 drift。

PLATFORM_SCOPED 已允许「本 generation 未证明的平台 selector 保留 original 有效约束」。
增量 extra / 增量受管名没有对等物。

### 5.2 只放松什么

放松对象是 **本次增量搜索声称覆盖的那一部分**，以及「未覆盖轴上保留已有 PF 地板」。

仍应阻止（工作假设，不是契约）：

- 本次纳入搜索的 Cell 失败、局部缺 Cell、非单调；
- 未选中 workspace/path package 的 dependency-array drift；
- policy / SourcePlan / requires-python / test-command 不匹配；
- 相对上次 apply 历史，用户改动了本应保留的 PF 地板或其它 requirement 语义；
- 普通 source drift 的默认 apply（继续需要显式 `--force`，除非另开 Design）。

可以想像的增量放行（待证）：

- 新 extra 的 Cell 全部 `CellSuccess`，旧 extra 不在本次搜索集合中：写入新 extra 相关声明，
  其它 extra 的 PF 地板保留；
- 新受管依赖已定界，且 check（或等价直接证据）表明旧坐标下界在新环境仍 PASS：只写入新依赖
  的 `>=`，其它 PF 地板保留。

用户如何声明「这是一次增量、覆盖哪些轴」尚未决定：可由本次 Cell 集合与 apply 历史推断，
也可显式配置。推断错误时必须失败，不能静默把未证明轴写成已证明。

## 6. Apply 历史与回滚

现行 `ProjectEditor` 的 rollback 只覆盖**一次事务崩溃**，不记录「昨天那次成功 apply」的前态。
D044 已接受将声明与报告回执纳入同一可恢复事务的目标；回执随当前报告保留，不含可回滚前态、不授权
增量 apply。完整历史仍是后续构想。`.pf/` 已从 SourceSnapshot 排除，并被 gitignore。

构想：在 PF 本机数据（`.pf/`，不进入快照、默认不进 git）写下每次合法 apply 的历史，使
下一次增量 apply 能回答「上次 PF 写了哪些地板」，并支持回到上一份已记录前态。

历史至少应能复原（待证）：

```text
时间与 package
所用报告 generation / 增量覆盖轴
授权 scope 与是否 force
写前 / 写后的选中包 dependency-array 语义（或等价 raw）
哪些声明被当作 PF 地板写入
```

两件不同的用途不要混成一条 CLI 承诺：

| 用途 | 说明 |
| --- | --- |
| 增量 apply 的 provenance | 区分 PF 地板、用户手写约束、尚无地板的新依赖 |
| 操作回滚 | 把工作树声明恢复到某条历史前态 |

`.pf/` 不被 git 跟踪，因此回滚默认是**当前 checkout 本机**的。已推送的 pyproject 仍以
版本控制为协作 undo。历史若只为 provenance，本机副本在别人的 clone 上不存在；增量 apply
能否只靠当前声明 + 本次报告工作，是进入 Design 前必须闭合的问题。

回滚与之后的人手编辑冲突时 fail closed，不能覆盖用户在 apply 之后写进同一数组的改动。
命令形状（`pf apply` 的子动作、独立命令、或仅保存供手动恢复）未定。

## 7. 明确不在本文

- 源码/测试用例与依赖坐标的影响面分析、覆盖率、import 图、fork testmon：见 C006。
- 用测试子集或缓存的旧 PASS 冒充当前快照的完整 `test-command` PASS。
- 跨 generation merge/rebase `package-floor.json` 证据。
- 把旧报告 CellSuccess 在快照已变后直接授权 apply。
- 持久化可写 venv、隐式 testmon / pytest `--lf` 作为 PASS 路径。
- C001 树搜索、C004 非单调 refinement。它们不构成本周期的前置。

## 8. 与现行非目标的对照

D001 §9 与 D003/D004 将「跨运行 Proposal/Evaluation environment cache」「写入 apply lineage」
列为 v1 非目标。R008 §7 并列否决跨运行 Evaluation cache 与共享可写环境。

本文若进入 Design，需要**逐项改写那些非目标**，不能把本构想读成现行允许项：

- 跨运行的是带准入的观察存储，不是 environment cache，也不是跳过当前 `C` 的 PASS；
- apply lineage 若落地，落在本机 PF 数据，不写进公共报告 wire，除非另有 Design 接受；
- 「通用 cache 服务」仍应避免：加深现有 evaluator / Run / editor 的存储，而不是新 facade。

## 9. 待验证问题

1. 库作者 CI 是否真能把 search 从默认路径拿掉；check 在真实套件上的墙钟是否可接受。
2. 统一存储的记录种类与完整身份字段；首批准入策略是否只做同快照直接观察。
3. 增量覆盖轴如何表达，错误推断如何失败；与 PLATFORM_SCOPED 是推广还是并列规则。
4. apply 历史只放 `.pf/` 时，别人 clone 后的增量 apply 靠什么 provenance。
5. 回滚的产品形状、与 git、与 apply 后人手编辑的冲突规则。
6. 观察缓存与 Journal / Process Log / 报告的去重：哪些 payload 只存一份。
7. 没有 C006 时，增量定界是否只覆盖「声明集合变化 + check 仍通过」；check 失败是否一律
   要求完整 search。

## 10. 进入 Design 的条件

稳态叙事、smoke/check 最小验证序列、有限 apply 回执与两文件事务已移交
[D044](../designs/D044-pf-check-first-and-apply-receipts.md)；目标已接受，实施计划见
[P048](../plans/P048-pf-check-first-and-apply-receipts.md)，尚未授权生产实现。
观察缓存另待完整身份、执行准入与真实收益证据，不作为日常 check 的前置。

增量 apply 与 apply 历史仍须至少同时成立：

1. 增量 apply 能指出相对 D001 §6 放宽的恰好哪些检查，以及仍 fail closed 的检查；有具体的
   extra 新增与依赖新增场景。
2. apply 历史的存放位置、与公共报告的分界、provenance 在缺本机历史时的行为、回滚冲突规则
   都有明确失败语义。

然后另建临时 Design，逐项标明替代 D001/D002/D014 的哪条规则，接受后再 Plan。
C006 不是前置。没有关联分析也可以在 D044 之后单独做增量 extra 证明。

## 11. 可能涉及的 owner

若后续推进，新 Design 需要评审的增量（目前不是承诺）：

| Owner | 可能增量 |
| --- | --- |
| D001 | §6 增量 apply；按未来独立 Design 收缩跨运行 cache / 完整 apply 历史非目标 |
| D002 | 观察存储与 Apply 历史的 module 边界；不新增通用 cache 服务 |
| D003 | 增量定界是否改变「每次覆盖全部受管坐标」；hints 是否只消费观察存储 |
| D004 | Run 内 TyCheckCache 并入统一存储；跨 Run 拒绝规则改为策略 |
| D006 | 增量 apply / 回滚 / 未证明轴的措辞，避免写成已验证 |
| D008 | check 与增量 search 的 Role 序列是否变化 |
| D014 | 有限回执不足以授权增量 apply；generation、增量覆盖与历史如何并存 |
| Editor / Authorizer | 增量投影、历史前态、回滚 fail closed |

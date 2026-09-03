# D022 — PF 评价 seam 收敛与 SearchCoordinator 测试面（归档）

- **状态：** 已完成，已归档
- **日期：** 2026-09-03
- **接受日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 临时性架构优化设计；完成后归并到现行 owner 并与实施 Plan 一同归档
- **设计核对基线：** `010e048`（`docs: archive verification run request design`）
- **评审来源：** [R005](../reviews/R005-pf-module-depth-review.md) §6、§8、§11.4
- **现行产品契约：** [D001](../../designs/D001-pf.md)
- **现行实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **现行搜索算法：** [D003](../../designs/D003-pf-search-algorithm.md)
- **现行 static/runtime 评价：** [D004](../../designs/D004-pf-ty-enhancement.md)
- **现行 Failure 契约：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **现行运行语义：** [D008](../../designs/D008-pf-verification-run.md)
- **现行解析与环境：** [D012](../../designs/D012-pf-harness-relaxation.md)
- **前序迁移：** [D021](D021-pf-verification-run-request.md)
- **实施计划：** [P028](../plans/P028-pf-evaluation-seam.md)

> **归档声明：** 本文只保留评价依赖 seam 与相应测试表面的迁移理由和验收历史；稳定规则已经
> 由 D002/D003/D004 接管，D008/D012 核对无冲突，本文不再承担规范性 ownership。

[R006](../../reviews/R006-pf-cli-system-review.md) §5.1 已接管原 R005 的 terminal-private result-card 轨道，
不进入本文。D019/P025、D020/P026 与 D021/P027 已完成的 SourcePlan、WorkspaceInventory 和 Verification Run
request 也不重新打开。

## 1. 问题、目标与删除测试

现有三个产品 module 共声明九份 consumer-specific 评价 Protocol：

```text
CompatibilityChecker
  CheckEnvironmentOperations
  CheckStaticOperations
  CheckFullOperations

HighestVersionVerifier
  HighestEnvironmentOperations
  HighestStaticOperations
  HighestFullOperations

SearchCoordinator
  SearchEnvironmentOperations
  StaticOperations
  FullOperations
```

九份 Protocol 并非签名完全相同：三份 environment 与三份 full interface 分别平行，Search 的
`StaticOperations` 同时暴露 `capture` 与 `evaluate`，Check/Highest 的 static interface 只暴露
`capture`。差异只反映各 caller 使用了现有 `StaticEvaluator` 的不同方法，不构成三条独立 seam。

生产 composition 实际只把同一个 `EnvironmentFactory`、`StaticEvaluator` 与 `RuntimeEvaluator` 注入三处。
这些 Protocol 没有第二组有独立行为的生产 adapter；它们主要让 tests 用短 fake 跳过真实评价 module。
改动 `prepare`、static capture 或 runtime evaluation interface 时，同一结构知识因而分散到三个产品
module 和多组测试。

测试表面已经反向塑造实现。设计基线上，`tests/test_baseline.py`、`tests/test_check.py`、
`tests/test_search_coordinator.py`、`tests/test_evaluation.py` 与 `tests/test_static_transition.py` 共有 7 处
直接 `PreparedEnvironment(...)` 构造；前三者还为 `prepare`、`capture`、`evaluate` 和 highest/coordinate
步骤建立大量 product-level fake。它们可以制造 `EnvironmentFactory` interface 不允许的结果，例如没有
Attempt 的 prepare failure，并迫使消费方保留只服务于 fake 的防御分支。

目标是：

1. 删除三组 consumer-specific env/static/full 假想 seam，让三个产品 module 直接依赖现有深 module
   的 interface；
2. 让 `PreparedEnvironment` 只由 `EnvironmentFactory.prepare(...)` 构造，测试与生产遵守相同 lifecycle；
3. 把可变测试行为下沉到已有真实 seam：uv、candidate provider、ty/process、configured verifier 与
   runtime witness；
4. 以真实 `HighestVersionVerifier`、`CandidateBuilder`、`CoordinateSearch` 和评价 modules 装配
   `SearchCoordinator` tests，只从 `search(...)` 观察产品结果；
5. 保持 Check two-phase、Highest full verify/close、Search baseline→candidates→coordinate-search 与
   D003 evidence authority 不变；
6. 删除旧 Protocol、fake-only branches 和浅层 tests，不增加共享 facade、factory、DI framework、alias
   或兼容路径。

删除迁移后的现有评价 module 时，resolution/Attempt、static baseline、runtime witness、configured verifier、
candidate freeze 与 prepared lifecycle 会重新散回三个产品 owner 和测试；这是 depth 的删除测试。删除一个
仅转发三套 evaluator 的新 wrapper 不会导致这些复杂度重新出现，因此本设计拒绝该 wrapper。

## 2. 决策摘要

1. `EnvironmentFactory.prepare`、`StaticEvaluator.capture/evaluate` 与 `RuntimeEvaluator.evaluate` 是唯一
   product-facing 评价 interface。`CompatibilityChecker`、`HighestVersionVerifier` 与
   `SearchCoordinator` 的构造参数直接使用这些 module 类型，不另声明 consumer-specific Protocol。
   Check/Highest 因而依赖完整 `StaticEvaluator` 类型，即使各自只调用 `capture`；这是用一种现有 module
   interface 替换三份 caller-specific 类型的有意选择，不增加构造参数或要求它们调用 `evaluate`。
2. 不建立一套共享 `EnvironmentOperations` / `StaticOperations` / `FullOperations` Protocol。当前没有
   第二组长期 adapter；测试 stand-in 下沉到真实变化点，不足以证明新的 product-facing port。
3. `CompatibilityChecker` 从 `workflow.py` 移到 `src/pf/check.py`；其 public `check(...)` interface 与
   two-phase ownership 不变。Workflow 与 CLI 直接从 `pf.check` 导入，不从 `workflow.py` re-export，
   也不把 checker 并入 `evaluation.py`。
4. `HighestVersionVerifier` 保留 highest prepare → static capture → full evaluate → close ownership；
   `SearchCoordinator` 继续复用 composition root 注入的同一实例，不内建或复制 highest 流程。
5. `SearchCoordinator` 直接依赖 `CandidateBuilder` 与 `HighestVersionVerifier`。只有一个生产 satisfier、
   只供高层 fake 使用的 `CandidateOperations`、`HighestOperations` Protocol 一并删除；Candidate 的真实
   变化点仍是 `CandidateProvider`。
6. `CoordinateSearch` 已是直接 concrete dependency，并继续独占 in-process vector 算法；不私有化、
   不包进评价 facade，也不为 tests 增加替代 Protocol。
7. `SearchDiagnosticConsumer`、`SearchActivityConsumer` 以及 evaluator 的 stage/event consumer 保留。
   生产 presenter 与测试 recording sink 会真实改变 side effect，因而这些是有效 seam。
8. `UvOperations`、`CandidateProvider`、`TyOperations`、`VerifierOperations`、
   `RuntimeWitnessOperations` 与 `ProcessRunner` seam 保留。它们隔离外部工具、registry 或进程执行，具有
   production adapter 与长期 test adapter。
9. `FailurePolicy` ownership、FailureRecord 形成、D005 disposition 与其当前 concrete 注入方式不在本次
   改动中。宽 `classify(...)` 是否改为 facts record 需要独立 leverage 证据，不顺手包装。
10. `PreparedEnvironment` 仍是 evaluator 可读取并显式关闭的 runtime value；改变的是构造 seam：生产与
    tests 都只能从 `EnvironmentFactory.prepare(...)` 取得它。它不变成 Schema、Protocol 或 public builder。
11. PF 未发布；接受后直接替换旧 interface 与 tests。不得保留 Protocol alias、dual typing、兼容 adapter
    或新旧测试双轨。

### 2.1 Rejected shapes

下列形状集中否决，实施时不得以改名或 test helper 的形式恢复：

| 形状 | 否决理由 |
| --- | --- |
| 共享 `EnvironmentOperations` / `StaticOperations` / `FullOperations` Protocol | 生产仍只有一组 satisfier；新的 product-facing port 没有第二 adapter，不能通过删除测试 |
| `EvaluationPipeline` | 把 environment、static 与 runtime witness/verifier ownership 混进一个更宽 interface |
| `CellEvaluator` | 进一步混合 Check、Highest、Search 的 outcome 与产品顺序，越过三个 owner |
| `SearchServices` 参数包 | 只隐藏 constructor 宽度；删除后原参数原样返回，没有新增 depth |

Test-private assembly 只能装配既有 module，不能成为上述任一形状的 test-only 版本。

## 3. Seam 分类与目标依赖

| 依赖 | 分类 | 决策 |
| --- | --- | --- |
| Environment / Static / Runtime evaluator | in-process 深 module | 三个产品 owner 直接依赖现有 concrete module interface |
| HighestVersionVerifier / CandidateBuilder / CoordinateSearch | in-process 产品或算法 module | SearchCoordinator 直接依赖；不为 tests 建 port |
| uv / registry / ty / verifier / runtime witness / process | true external 或 process adapter | 保留现有 Protocol，tests 在这里替换 |
| activity / diagnostic consumer | side-effect sink | 保留 consumer seam；生产与 recording adapter 都有真实行为 |
| PreparedEnvironment filesystem resources | EnvironmentFactory 拥有的 local-substitutable lifecycle | 真实临时目录测试；不直接构造，不增加 filesystem port |

目标依赖图为：

```text
composition root
  ├── UvAdapter ----------------------> EnvironmentFactory
  ├── TyAdapter ----------------------> StaticEvaluator
  ├── ConfiguredVerifier ------------>
  └── RuntimeWitnessAdapter ----------> RuntimeEvaluator(StaticEvaluator, ...)

EnvironmentFactory + StaticEvaluator + RuntimeEvaluator
  ├──> CompatibilityChecker
  ├──> HighestVersionVerifier
  └──> SearchCoordinator
         ├── CandidateBuilder(CandidateProvider)
         ├── HighestVersionVerifier
         └── CoordinateSearch
```

`RuntimeEvaluator` 继续拥有它使用的 `StaticEvaluator`。三个产品 module 同时需要 baseline capture 或
candidate static evaluation，因此 composition root 必须把同一个 `StaticEvaluator` 实例显式传给它们，
不得通过 `RuntimeEvaluator.static` property、module bundle 或 service locator 取回。

## 4. 目标 interface 与 ownership

### 4.1 三个产品 module

目标构造与行为 interface 为：

```text
CompatibilityChecker(
    environments: EnvironmentFactory,
    static: StaticEvaluator,
    full: RuntimeEvaluator,
    failures: FailurePolicy | None = None,
    events: ActivityConsumer | None = None,
)
CompatibilityChecker.check(package, cell, snapshot, source_plan)
    -> CheckCellOutcome

HighestVersionVerifier(
    environments: EnvironmentFactory,
    static: StaticEvaluator,
    full: RuntimeEvaluator,
    failures: FailurePolicy | None = None,
)
HighestVersionVerifier.verify(package, cell, snapshot, source_plan)
    -> HighestVersionOutcome

SearchCoordinator(
    environments: EnvironmentFactory,
    candidates: CandidateBuilder,
    static: StaticEvaluator,
    full: RuntimeEvaluator,
    highest: HighestVersionVerifier,
    coordinate_search: CoordinateSearch,
    diagnostics: SearchDiagnosticConsumer | None = None,
    events: SearchActivityConsumer | None = None,
    failures: FailurePolicy | None = None,
)
SearchCoordinator.search(package, cell, snapshot, source_plan)
    -> CellResult
```

这里的 class 名称标识 module，调用方仍只依赖其文档化 interface，不获得 private state。构造参数没有因
改为 concrete 类型而扩大；相反，删除了十一份调用方必须对齐的结构契约：九份 env/static/full Protocol，
以及 Search 的 `CandidateOperations`、`HighestOperations`。

`CompatibilityChecker`、`HighestVersionVerifier` 与 `SearchCoordinator` 的 `check/verify/search` 参数、
返回 union、Failure disposition、event 顺序和 close 语义保持不变。D021 定义的 `CheckCellOperations`、
`SmokeCellOperations`、`CellSearchOperations` 继续作为 VerificationRunner request 的 command-discriminated
operation seam；它们隔离跨 Cell Runner 与三个不同产品算法，不属于本次删除对象。

### 4.2 EnvironmentFactory 成功与失败契约

`EnvironmentFactory.prepare(...)` 的 closed outcome 继续是：

```text
PreparedEnvironment | PrepareFailure
```

- 成功值一定含与 request、Cell、snapshot、SourcePlan 闭合的 `Attempt`、`Proposal`、resolution evidence
  与唯一 cleanup lifecycle；
- 任何已经建立 Attempt 后的 uv/create/install/inspect failure 都包装成 `PrepareFailure` 并保留已有 plan
  evidence；
- 无法建立 resolution run context 的错误在 Factory 内形成现行 command error，不把裸 `ToolFailure`
  交给产品 module；
- `ExactSelection` 成功时 Proposal vector 与选择的 frozen candidates 精确一致。

因此，依赖裸 `ToolFailure`、缺失 Attempt 或手造矛盾 `PreparedEnvironment` 的消费方 branches/tests 不属于
目标 interface，应在迁移中删除。D003/D012 要求的 exact selection、graph/source、plan digest 与 cleanup
安全检查继续由 `EnvironmentFactory` 的 public seam 测试；不得通过高层 fake 把同一规则复制到
`SearchCoordinator`。

### 4.3 产品 ownership 保持

| Module | 本设计后唯一 ownership |
| --- | --- |
| EnvironmentFactory | materialize、两阶段 resolution、Attempt/Proposal、install/inspect 与 PreparedEnvironment lifecycle |
| StaticEvaluator | baseline capture、multiset increment、static transition 与 classifier 调用 |
| RuntimeEvaluator | static→witness→configured verifier 路由及 RuntimeEvaluationRun |
| CompatibilityChecker | highest capture 后关闭，再执行 lowest-direct full evaluation 与 declaration outcome |
| HighestVersionVerifier | highest capture/full reuse、baseline outcome 与无条件 close |
| CandidateBuilder | registry candidate query、policy filter、artifact freeze 与 CandidateSnapshot |
| SearchCoordinator | 单 Cell baseline→candidate→coordinate state machine、private proposal cache/regions 与 CellResult |
| CoordinateSearch | runtime-backed vector 最小化、slice/window/promotion 与 termination |
| VerificationRunner | D021 的跨 Cell request、scheduling、completion、Journal 与 association |

不得合并上述 ownership；具体 rejected shapes 由 §2.1 集中定义。

## 5. SearchCoordinator 测试替换

### 5.1 Test-private assembly

测试侧建立一份共享、private assembly helper，只负责装配真实 module graph：

```text
scripted lower adapters
  -> EnvironmentFactory
  -> StaticEvaluator
  -> RuntimeEvaluator
  -> HighestVersionVerifier
  -> CandidateBuilder
  -> CoordinateSearch
  -> SearchCoordinator
```

Assembly 优先迁移并扩展已有 `tests/test_environment.py` 的 `SuccessfulUv`、
`tests/test_evaluation.py` 的 `PreparedUv`，以及现有 ty/verifier/witness recording adapter 的共同能力；
共同部分进入一处 test-private support。测试模块不得互相导入，也不得在 coordinator helper 中再维护
第三套平行 uv fake。该 support 只实现 lower adapter interface，不复制评价或搜索步骤。

该 helper 可以返回 concrete modules、lower-adapter call records 与 cleanup handle，但不得提供自己的
`prepare/capture/evaluate/search` 方法，不得返回预制 `PreparedEnvironment` / `Evaluation` / `CellResult`，
也不得复刻 production composition root。它是 tests 的装配代码，不进入 `src/pf`，不成为 public factory。

脚本化 adapter 只在真实低层 seam 提供确定输入：

- uv adapter 提供 resolution context、ResolutionPlan、create/install/interpreter/graph outcome，并记录
  resolution kind、exact selection 与 lifecycle path；
- candidate provider 提供 registry candidates/artifacts，并记录 canonical query；
- ty 或其 ProcessRunner 提供 TyCheck / ToolFailure；
- configured verifier 或其 ProcessRunner 提供 pass/rejection/timeout/diagnostics；
- runtime witness adapter 提供 PRESENT、NOT_APPLICABLE、CONFIRMED_MISSING 或 ToolFailure；
- activity/diagnostic recording adapter 只记录 public events。

tests 可以读取这些 lower-adapter records 来证明调用次数、顺序和输入，但不读取 evaluator、
`_ProposalRunner`、EvaluationCache 或 CoordinateSearch 的 private state。

### 5.2 Replace, don't layer

以下旧测试在新的 public behavior 覆盖建立后删除，不作为第二层保留：

- tests 全库任何直接 `PreparedEnvironment(...)` 构造，无例外；`test_static_transition.py` 与
  `test_evaluation.py` 的 helper 同样必须改由 scripted uv + `EnvironmentFactory.prepare(...)` 取得；
- monkey-patch `PreparedEnvironment.close`，或通过 subclass override / `mock.patch` 替换
  `EnvironmentFactory.prepare`、`StaticEvaluator.capture/evaluate`、`RuntimeEvaluator.evaluate`、
  `HighestVersionVerifier.verify`、`CoordinateSearch.minimize` 的评价/coordinator test；
- 分别实现 `Environments`、`Static`、`Full`、`Highest` 的 product-level fake；
- 用 fake `CoordinateSearch` 预先指定 Search 结果、只验证转发或构造器槽位的测试；coordinator tests
  不得以任何形式替换 `CoordinateSearch`；
- 制造 Factory contract 不允许的裸 `ToolFailure`、missing Attempt 或矛盾 Proposal 的测试；
- 断言 `_ProposalRunner` cache、private helper、private record 或 concrete call chain 的测试。

依赖 `PreparedEnvironment` 的 classification 行为只通过 `StaticEvaluator.evaluate(...)` 的 public outcome
观察，不直接调用 `StaticTransitionClassifier.classify(...)`。`static_fingerprint(...)` 等不依赖
PreparedEnvironment 的 pure interface 可以继续直接测试。上述禁令不影响 `test_search.py` 为
`CoordinateSearch` 提供 `VectorEvaluator`，也不影响 lower adapter 实现 `TyOperations`、
`VerifierOperations` 或 `RuntimeWitnessOperations`。

以下只证明旧假想 seam 的 negative tests 必须点名删除，而不是改写为新断言：

- `test_highest_version_verifier_rejects_prepare_without_attempt`；
- `test_search_rejects_a_probe_prepare_without_an_attempt`；
- `test_search_rejects_a_probe_prepare_without_attempt_identity`。

`prepared_resolution_evidence(...)` 在 direct constructor 全部删除后若无剩余调用方，也必须随迁移删除，
不得作为旧构造 seam 的残留保留。

不得先保留旧 tests，再新增一套真实 module integration tests。重复覆盖会让假想 seam 继续成为维护契约。
迁移期可以短暂加入 symbol-absence 或 constructor-count 检查，但交付前删除；最终 tests 只证明目标行为。

### 5.3 Public behavior ownership 与必须保留的矩阵

`tests/test_search_coordinator.py` 只拥有 SearchCoordinator 的 composition/state closure：baseline 终止、
candidate 空集或失败、prepare reuse、由最小真实搜索形成的 probe/region/failure refs、diagnostics/events 与
cleanup。它用最小候选集和 lower-adapter outcomes 驱动真实 `CoordinateSearch.minimize(...)`，可以断言
返回的 public evidence 闭合，但不重复算法矩阵。

`tests/test_search.py` 继续唯一拥有 D003 的 slice、window、hint、linear/binary strategy、promotion、
predecessor、multi-sweep、termination、reentrancy 与 concurrency 行为。Coordinator tests 禁止注入、
subclass 或 patch `CoordinateSearch`；改变算法路径只能修改 `tests/test_search.py` 的 public
`CoordinateSearch.minimize(...)` tests。

| Public seam | 必须证明的稳定行为 | 允许观察 |
| --- | --- | --- |
| EnvironmentFactory.prepare | highest/lowest/exact resolution、source/graph/plan failure、Attempt evidence、cleanup | outcome 与 lower uv records/filesystem |
| StaticEvaluator.capture/evaluate | baseline scope、multiset increment、static classification 与 Ty failure | StaticBaselineCapture/StaticEvaluation 与 ty records |
| RuntimeEvaluator.evaluate | unchanged/regression、witness routing、verifier pass/reject/indeterminate、tested marker | RuntimeEvaluationRun 与 lower verifier/witness records |
| HighestVersionVerifier.verify | capture 复用于 full、所有 terminal path close、baseline rejection/indeterminate | HighestVersionOutcome 与 filesystem/lower records |
| CompatibilityChecker.check | highest capture-before-close-before-lowest、lowest full、role/disposition 与 declaration context | CheckCellOutcome、events 与 lower records |
| SearchCoordinator.search | baseline stop、candidate failure/empty、prepare reuse、最小真实 search 的 probe/region/failure refs、diagnostics/events 与 cleanup | CellResult、public evidence/events 与 lower records |
| CoordinateSearch.minimize | D003 slice/window/hint/strategy/promotion/predecessor/sweep/termination/reentrancy/concurrency | `test_search.py` 的 CoordinateOutcome、probe order 与 progress |

Coordinator tests 需要构造不同状态时，通过候选集合、ty、witness、verifier 和 uv outcome 控制，而不是
直接返回 `CoordinateSuccess` / `CoordinateFailure`。
`search(...)` 返回的 final、Attempt、ProbeEvidence、StaticRegion 和 failure refs 继续按 D003/D014 public
records 做正向语义断言，不使用整段 volatile snapshot。

## 6. Production 迁移与依赖方向

实施必须一次完成以下替换：

1. 建立 `src/pf/check.py` 承载 `CompatibilityChecker`；workflow 与 CLI 直接从 `pf.check` 导入，
   VerificationRunner 的既有 operation Protocol 只引用其 public behavior。不保留从 `workflow.py` 的
   re-export，也不把 checker 并入 `evaluation.py`。
2. 从 workflow、baseline、search 删除九份 env/static/full Protocol，构造参数改为三个 concrete
   evaluation modules。
3. 从 search 删除 `CandidateOperations`、`HighestOperations`，分别直接使用 `CandidateBuilder`、
   `HighestVersionVerifier`；保留 consumer Protocol 与 lower adapter Protocol。
4. `cli._assemble_context` 仍只构造一份 EnvironmentFactory/StaticEvaluator/RuntimeEvaluator，并把同一
   对象图传给 Check、Highest 与 Search；不得增加 command-specific evaluator 或 module locator。
5. 删除只由旧 fake surface 支持的 unreachable branches/imports/casts；保留并在正确 owner 上验证 D003、
   D005、D012 的 error/safety behavior。
6. 用 §5 的 test-private assembly 原地替换旧高层 fakes，并删除全部 direct PreparedEnvironment 构造。
7. 稳定 interface 与 dependency rules 归并到 D002；Search state/test surface 归并到 D003；评价路由/测试
   规则归并到 D004。D008/D012 只在现行文字与新 seam 冲突时做最小同步。

本迁移不改变 CLI request/result、numeric exit code、Schema 1、JSON Schema/examples、source/report identity、
Journal、diagnose/apply authority 或 terminal presentation；这些区域没有生成物工作。

## 7. 非目标与停止条件

非目标：

- 合并 CompatibilityChecker、HighestVersionVerifier 与 SearchCoordinator 的 outcome 或产品算法；
- 新建 CellEvaluator、EvaluationPipeline、SearchFacade、module bundle、registry、service locator 或 DI framework；
- 私有化 CoordinateSearch、改变 D003 probe/region/promotion 算法或降低 runtime evidence 要求；
- 改写 FailurePolicy、Candidate policy、EnvironmentFactory resolution/cache、RuntimeEvaluator witness 路由；
- 为测试开放 PreparedEnvironment builder、setter、factory override 或 production-only backdoor；
- 调整 R006 §5.1 接管的 terminal result-card。

出现以下任一情况必须停止实施、保持现状并回到 Design，而不是叠加兼容层：

1. test assembly 只能通过复制与 EnvironmentFactory/StaticEvaluator/RuntimeEvaluator 等宽的 facade 才能
   表达现行行为；
2. concrete module 依赖形成无法通过 ownership 调整消除的 import cycle；
3. Search public behavior 不能通过 lower adapters + real CoordinateSearch 确定重现，必须读取 private state；
4. 删除新目标形状后复杂度不会重新散回调用方，说明迁移没有产生 depth；
5. 为保持现行 safety contract 必须重新引入 consumer-specific env/static/full port。

任何停止结论及证据必须先回填 P028，再由用户决定修订或拒绝 D022。

## 8. 验收标准与后续生命周期

| ID | 验收标准 | P028 证据槽 |
| --- | --- | --- |
| AC1 | 九份 env/static/full Protocol 全部删除；三个产品 module 直接依赖同一 concrete evaluator graph | symbol scan、constructor/type review、composition test |
| AC2 | CandidateOperations 与 HighestOperations 删除；lower adapter 与 consumer seam 保留且 ownership 清楚 | dependency scan、focused type/tests |
| AC3 | tests 全库无例外地没有直接 `PreparedEnvironment(...)` 构造，src 中只有 EnvironmentFactory 构造成功值；classification 从 StaticEvaluator outcome 观察 | repository scan、EnvironmentFactory/StaticEvaluator semantic tests |
| AC4 | 评价/coordinator tests 不再实现 product-level fake，也不 subclass/patch concrete prepare/capture/evaluate/verify/minimize；CoordinateSearch tests 的 VectorEvaluator 与 lower adapters 明确保留 | targeted source/AST review、old-path deletion scan |
| AC5 | §5.3 的具名语义矩阵通过：Highest capture/full/close、Check two-phase、Search prepare reuse/evidence closure 由各 owner 测试，D003 算法矩阵只在 test_search；三个 obsolete Attempt negative tests 已删除 | exact test names、commands、results 与 ownership audit；不使用 coverage 百分比代替 |
| AC6 | Search 的 final/probe/static-region/failure/diagnostic/event facts继续满足 D003/D004/D005，不改变 report identity 或 wire | search/report focused tests |
| AC7 | cli composition 只装配一份 evaluator graph；没有 facade、factory、bundle、locator、alias 或 compatibility adapter | composition inspection 与 tests |
| AC8 | D002/D003/D004 吸收稳定规则，D008/D012 完成冲突核对；D022/P028 状态、链接与归档一致 | owner diff、Markdown/link audit |
| AC9 | focused、ruff、ty、coverage、Python 3.10/3.11/3.12 full suites 与 build 通过，命令和结果逐项记录 | exact command/result log |

本 Design 已按 P028 完成。每项 AC 均已由有序实现切片、旧路径删除、owner 归并、public tests 与精确
证据闭合；稳定规则由现行 owner 接管。R005 的轨 A/B/C 均已解决，轨 D 已移交 R006，因此 R005 与
D022/P028 在同一完成变更中归档。

P028 至少预留并按实际环境记录：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon \
  tests/test_environment.py tests/test_evaluation.py tests/test_static_transition.py \
  tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ty check src
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --cov=pf --cov-report=term-missing -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.10 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.12 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv build
git diff --check
```

Python 版本全量套件在同一工作树顺序执行。Focused pass、full pass、coverage gate、build 与网络/环境限制
分别记录，不能互相替代。

## 9. 接受前核对范围

接受前草案只基于 `010e048` 做静态核对：

- 对照 R005 §6、§8、§11.4 与 D002/D003/D004/D008/D012 的现行 ownership；
- 核对 production composition 只装配一份 EnvironmentFactory/StaticEvaluator/RuntimeEvaluator；
- 核对三组九份 consumer-specific 评价 Protocol（Search static 比 Check/Highest 多 `evaluate`）、Search 的
  Candidate/Highest Protocol 与保留的 lower/consumer seam；
- 核对五个相关测试文件共 7 处 direct PreparedEnvironment 构造及 product-level fakes；
- 核对 CompatibilityChecker、HighestVersionVerifier、SearchCoordinator、EnvironmentFactory、
  CandidateBuilder、StaticEvaluator、RuntimeEvaluator 与 CoordinateSearch 当前 interface。

接受前未创建 P028，未修改生产代码、tests、现行 owner 或生成物，也未运行 pytest、ruff、ty、coverage、
build 或真实 PF command。该文档检查只证明草案与当时源码形状闭合，不是行为验证或实施证据；实施证据
统一记录在 P028。

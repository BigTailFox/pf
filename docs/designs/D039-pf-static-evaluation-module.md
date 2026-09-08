# D039 — PF 静态评价深模块

- **状态：** 草案
- **日期：** 2026-09-08
- **性质：** 临时重构 Design；未接受、不授权实施、不冒充已交付行为
- **目标 owner：** [D002](D002-pf-implementation.md)、[D004](D004-pf-ty-enhancement.md)、[D005](D005-pf-failure-and-diagnose.md)
- **验收标准：** [§9](#9-验收标准)
- **来源：** [R011](../reviews/R011-pf-architecture-review.md) §3–§6
- **关联：** [D003](D003-pf-search-algorithm.md) 只改 StaticSlice 的提供方指针；[D012](D012-pf-harness-relaxation.md) 保留 harness 变换；[D014](D014-pf-report-schema.md) 保留 Schema 1 字段，改离线复算调用点

本文定义 R011 的**目标** module interface：把静态采集、Run cache、准入、比较与纯 guidance 收成一个深模块，并把 FrozenSchema 从这些算法中拆出；同一吸收改写 D002 模块地图，并收回 `FailurePolicy` 可选注入。行为语义仍以现行 D003/D004/D005 为准；本文件只改规则住在哪里、调用方必须学习什么。接受前不是现行契约。

## 1. 结论

当前公开 seam 写成 `StaticEvaluator.lookup/collect/compare`，实现却是十余个平行文件，且 `schemas/` 向上执行准入、二分与 harness 变换。Evaluator 通不过删除测试：删掉它之后复杂度仍留在 cache、request factory、schema 比较函数和 SearchCoordinator 手写的 slice 里。

目标是**一个**静态 module，对外大约：

```text
StaticEvaluator.lookup / collect_prepared / compare
StaticEvaluator.capture_highest / compare_global
TyCheckCache                          # Run 拥有，VerificationRunner 构造并显式传入
StaticGuidanceEvaluator.open_static_slice
  -> 静态 module 提供的 StaticSlice adapter
locate_static_hint(slice, versions)   # CoordinateSearch 的纯 hint 入口
admit_saved_static_audit(...)         # ReportStore / RunLogStore 离线读入；Plan 定精确名字
```

路径解析、ignore、relocation、configuration materialize、request 装配留在 implementation。产品代码与产品测试走同一公开表面。`TyCheckCache` 不收成 service locator。

R011 要求 §3 与 §4 同一份 Design：先把算法从 schemas 拿回静态 module，再收调用方 import。只搬家名或只建 `pf/static/` 子目录而不减少调用方知识，视为未完成。D002 过时文件名与 `FailurePolicy` 假想 seam 随本次吸收一起改，不另开 Design、不留给下一次文档修正。

## 2. 范围与非目标

**替换（接受并吸收后）：**

| 目标 owner | 替换内容 |
| --- | --- |
| D002 §3 | 包布局改成 **module 地图**，与代码同一变更吸收。静态是一个 module；D002 不再把不存在的 `src/pf/static.py` 画成独立 module（`schemas/static.py` 记录模块保留）；补上 `cancellation.py`；不把每个 `static_*.py` / `ty_fact.py` / `ty_options.py` 写成独立 owner |
| D002 §2、§4 | Schema 只保存记录与结构/identity 闭合；禁止 schemas 进口或执行 admit、hint、harness、ty-args。§4 表补上 policy / journal / static_* 记录 |
| D002 §7 | 静态公开 interface 收成上列名字；`StaticRequestFactory` 不再出现在 composition root。Check / Highest / Search / `_ProposalRunner` 不再接受可选 `failures=` |
| D002 §11 | 静态产品测试只经 Evaluator / Run cache / guidance seam 观察 outcome。分类从公开 Check/Highest/Search outcome 观察，不注入假 `FailurePolicy` |
| D004 §6–§7、§10 | 规范投影与采集请求是 Evaluator implementation；比较准入、减法、hint 定位的唯一实现在静态 module，不在 FrozenSchema 里 |
| D005 §5 | `classify` / `record_prepare` / `record_evaluation` 规则不变。编排器内部构造 `FailurePolicy`，与 `VerificationRunner` 一致；它不是可替换 seam |

**保持不变：** D001 命令与退出码；D003 两阶段搜索与「静态无 compatibility disposition」；D004 的 `S_hi`/`S_slice`、多重集减法、Run 内原始 TyCheck 缓存、lookup 只读 / collect 占 permit；D005 的 cause/disposition/authority；D008 Run/Journal 时序；D012 original/relaxed 变换本身；D014 Schema 1 字段集合与 identity 字节。D038 已归档，不把静态拒绝权限改回去。

**本文件不覆盖：** R006/R008/R010 开放项；按行数拆 `report.py` / `schemas/evaluation.py`；把 Check/Smoke/Search 收成评价 facade。

## 3. 目标 module interface

静态是一个 module：小 interface，完整 implementation。物理文件可以是 `pf.static` 包或少数私有文件；Plan 选定布局。D002 §3 只画 module，不把每个内部 `.py` 写成独立规则所有权。

### 3.1 调用方必须学习的名字

| 名字 | 谁用 | 义务 |
| --- | --- | --- |
| `StaticEvaluator` | composition root、Check、Highest、Search、产品测试 | ty 收集、最高版本登记、GLOBAL/SLICE 比较；内部装配 request |
| `TyCheckCache` | `VerificationRunner` 构造；Check/Highest/Search 接收 | 一个 Verification Run 的原始事实与静态审计账本；显式传入，Run 结束关闭 |
| `CacheMiss` | lookup 的只读未命中 | 不是 unavailable，不产生 disposition |
| `StaticGuidanceEvaluator` / `StaticSlice` | `CoordinateSearch`；产品 SearchCoordinator 实现前者 | `open_static_slice` 至多每坐标一次；返回的 slice **由静态 module 构造** |
| `locate_static_hint` / `StaticPoint` / `StaticHint` / `StaticSearchResult` | `CoordinateSearch` | 纯 hint 算法；schema 不得重放 |
| 离线静态 admission（Plan 定精确名字，一类函数） | `ReportStore`、`RunLogStore` / Journal reader | 读入已保存比较与搜索审计时复算准入/减法/hint 序列；与在线 `compare` / `locate_static_hint` 同一实现 |
| producer/log 关联（现行 `static_producer_log_associations` 一类） | `runlog.py`、diagnose workflow | Journal/diagnose 遍历合法关联；不是第三条平行静态 API |

产品路径不再进口：`StaticRequestFactory`、`static_paths` / `static_ignores` / `static_relocation` / `static_configuration` / `static_process` / `static_external` / `static_subject` / `static_admission` / `ty_fact` / `ty_options` / `adapters.static_inputs`。`RunStaticPassRef` / `RunStaticConsumerRef` 不用于 SearchCoordinator 手写 slice。`report.py` / `runlog.py` 只进口上表的离线 admission 与关联函数，不进口 schema 内的重放实现。

### 3.2 Evaluator

生产构造隐藏 request factory。Ty 仍是真实 adapter seam：

```text
StaticEvaluator(ty, *, processes, events=None, permits=None)
```

`processes` 是 `ProcessRunner`（与 `TyAdapter` / request 装配共用），不是 `VerificationRunner`。`cli.py` 不再构造 `StaticRequestFactory`。`TyOperations` 继续由 `TyAdapter` 与 recording adapter 满足；不为 request 装配再设一条产品 Protocol。

公开方法：

```text
lookup(subject, observation_policy, *, run_cache)
    -> 已完成原始事实 | CacheMiss
    产品编排器不调用。公开 seam 供产品测试断言「只读、不占 permit、不启动 ty」。

collect_prepared(prepared, *, package, run_cache)
    -> 已完成原始事实 | StaticContentUnavailable
    内部：闭合六组输入、revalidate、只让 owner 占 ty permit、原子登记
    成功返回值即后续 compare_global 的 subject，调用方不读 prepared.static_consumer

capture_highest(prepared, *, package, run_cache)
    -> 同上，并登记该 Cell 的 S_hi 或 UNAVAILABLE 基线
    Check/Highest 的 highest 路径只走这一方法。成功/不可用均由返回值区分，
    删除 assert prepared.static_consumer is not None

compare(subject, reference, *, run_cache, context, guidance_policy, anchor_pass=None)
    -> StaticComparisonResult
    先准入，再多重集减法；不缓存该次解释。
    产品编排器不调用：GLOBAL 走 compare_global，SLICE 走 StaticSlice。
    保留为产品测试观察准入/减法的 seam（AC5）；删除测试：去掉它则测试必须穿透 cache/schema。

compare_global(subject, *, run_cache, guidance_policy)
    -> 对已登记 S_hi 做 GLOBAL 比较
    subject 是同一次 collect_prepared 的成功返回值。Unavailable 不调用。
```

`lookup` / `collect` 的现行时序与 negative cache 规则不改，只把 presently 转给 `TyCheckCache` 的浅转发收进同一 module，使删除 Evaluator 时复杂度回到调用方。`collect(prepared, request)` 若保留，只作 module 内部方法，不再作为 composition 或 Check/Search 的入口。

### 3.3 Run cache

`VerificationRunner` 在 baseline capture 前构造 `TyCheckCache`，经 `check/verify/search(..., run_cache=)` 传入，Run 收尾 `stop`/`close`。同一 `StaticEvaluator` 实例可服务多个 Run，但不得隐式共享 cache。

Cache 仍拥有：原始 TyCheck/Unavailable、consumer/pass 登记、比较审计、静态搜索/省略/跳过/oracle 选择账本、Cell 分区 snapshot（供 Journal）。这些账本方法属于 Run 作用域，不是 CLI 装配内部类型。

SearchCoordinator **停止**进口 cache ref 去实现 `_RunnerStaticSlice`。它只提供「已有直接 PASS 上端」与「按版本 prepare+collect」的 collector，然后向 Evaluator 要 slice：

```text
SearchCoordinator.open_static_slice(vector, dependency, versions)
    校验该向量已有直接 PASS
    若无合格原始/PASS 锚点 -> 记 omission，返回 None
    否则 return static.open_slice(
        run_cache,
        upper_proposal,
        dependency,
        versions,
        candidates,   # 该坐标冻结的 CandidateSnapshot；Search 显式传入
        collect=prepare_and_collect_version,   # Search 拥有 prepare
    )
```

`CandidateSnapshot` 仍由 Search 冻结并拥有。静态 module 用传入的 snapshot 装配 `SliceComparisonContext.window`（`snapshot.select(version)`）和 `StaticSearchAudit.candidates`。window / audit 字段装配不留在 SearchCoordinator。缺这条通道则 slice 无法在不进口 cache ref 的前提下复现现行 hint 语义。

`inspect` 的准备/环境生命周期仍由 Search/`_ProposalRunner` 拥有；比较、`StaticPoint`、search audit 的字段装配由静态 module 的 `StaticSlice` 完成。`CoordinateSearch` 继续只依赖 `StaticGuidanceEvaluator` 与 `locate_static_hint`，不进口 cache。

### 3.4 内部 implementation（不是产品 interface）

下列行为留在 module 内，可以有内部 seam 和内部测试：

- 六组 StaticSubject 投影与 `StaticTyRequest` 装配
- 搜索路径、ignore、relocation、ty 配置物化、进程环境
- `admit_*`、diagnostic subtraction 的实现（公开入口是 `compare` 与离线 admission，不是 `static_admission.py`）
- producer/log 关联的具体遍历

`PreparedEnvironment` 可以继续持有不透明的 static materialization/consumer 句柄，供 Evaluator 在 `static_use` 租约内使用；产品编排器不读取其内部形状。

### 3.5 evaluation.py

`RuntimeEvaluator`、`EvaluationCache`、`StagePermitPools` 留在评价/动态侧。`StaticEvaluator` 不再作为 `evaluation.py` 的平行出口。composition root 仍把**同一个** Environment/Static/Runtime 实例注入 Check、Highest、Search，不引入 facade 或 service registry。

## 4. Schema 底层

FrozenSchema 只保存不可变记录与结构/identity 闭合。比较减法、hint 定位、harness 变换、ty argv 语义不是结构不变量。

### 4.1 算法迁回静态 module

| 现行位置 | 目标 |
| --- | --- |
| `schemas.static_comparison` 调用 `admit_static_consumer_context` / `derive_static_comparison` | 唯一实现在静态 module；在线 `compare` 与离线 admission 调用同一函数。记录只保存已承认的 context/result/identity |
| `schemas.static_search` 重放 `locate_static_hint`（`StaticSearchAudit.validate_in_scope`） | 审计记录的闭合由静态 module 的 admission 执行。Schema 不进口 `StaticPoint` |
| `schemas.static_scope`：`StaticScopeEvidence.validate_closure` 对每条 comparison 调用 `self.compare` 复算 identity/result，并对 searches 调用 `validate_in_scope` | 构造 scope 记录只做 ref/membership 结构闭合。语义重放改由 §3.1 离线 admission 执行 |
| `schemas.static_preparation` 调用 `original_harness` / `relax_harness` / `active_harness_requirements` | 构造该记录的 prepare/D012 路径负责变换；schema 核对已保存字段的结构关系与 identity 字符串，不再生 harness |
| `schemas.policy` 调用 `validate_ty_args` | `ConfigLoader` 与静态 request 装配在写入前资格化；policy 记录只保存已资格化的 args 投影 |

离线读入链（必须改调用点，禁止只搬前四行而留下这条）：

```text
ReportEvidenceV1.validate_interned_static_audit
    -> resolve_static_scopes
        -> 构造 StaticScopeEvidence
            -> validate_closure 重放 compare
            -> search.validate_in_scope 重放 locate_static_hint

VerificationJournal validator（schemas/journal.py）同样调用 resolve_static_scopes
report.py _resolve_report_static_scopes 是 ReportStore 的第二条入口
```

`resolve_static_scopes` / `intern_static_scopes` 的 intern inflate 可以保留为结构闭合；**进入 FrozenSchema `model_validator` 的 derive/hint 重放必须离开**。ReportStore 与 RunLogStore 在 inflate 之后调用静态 module 的 admission，拒绝漂移。`StaticComparisonDocument` 不再于 `model_validator` 里执行 subtraction。

这样 schemas 不再依赖 domain 算法，也不再出现 `static_search` → `static_guidance` → `static_comparison` 的往返，也不再经 `static_scope` 间接重放。

### 4.2 Schema 仍可做的 identity 闭合

记录若必须从已保存 preimage 复算 digest，函数必须住在 **schemas 可依赖的纯层**（与 `canonical_identity_json` 同层，或随记录类型下放到 `schemas/`）。`environment_identity_digest` / `resolution_graph_id` / `resolution_request_digest` 以及 `ResolutionPlanEvidence` 这类 FrozenSchema 记录，若继续被静态 records 引用，其**定义**下放到该层；`pf.resolution` 改为使用它们，而不是被 `schemas/` 进口。

下放后 digest **字节不变**。禁止为搬家复制第二套算法。

本 Design 的 schema import **禁入**（AC4 扫描这些，命中即未完成）：`pf.harness`、`pf.static_*`、`pf.ty_options`、为执行搜索/准入/harness/ty-args/digest 而进口 `pf.resolution`。允许：`pf.schemas.*`、标准库、`packaging`、Pydantic。

下列**既有例外不在本次范围**，AC4 不得把它们当失败：

| 位置 | 进口 | 说明 |
| --- | --- | --- |
| `schemas/config.py` | `pf.errors.ConfigurationError` | 配置资格错误类型；非静态算法 |
| `schemas/config.py` | `pf.search_space`（`parse` / defaults） | 搜索 DSL；非 D004 |
| `schemas/project.py` | `pf.search_space.SpaceSelection` | 候选空间值类型；非 D004 |

禁止只豁免 `schemas.config` 而漏掉 `schemas.project` 的 `search_space`。本 Design 不把这些例外改成「schemas 只能进口 schemas」。

### 4.3 顺序约束

Plan 必须先完成 §4（算法离开 schemas——含 `static_scope` / report / journal 读入链，identity 纯函数就位），再收 §3 的产品 import。禁止把「换了路径的同一函数仍被 schema 或 `resolve_static_scopes` 构造路径调用」当作切片完成。

## 5. 调用方与测试表面

### 5.1 产品进口

`cli.py`、`check.py`、`baseline.py`、`search.py`、`verification.py`、`workflow.py`、`coordinate_search.py`、`report.py`、`runlog.py` 只进口 §3.1 的公开名字与 FrozenSchema 记录。`adapters/ty.py` 可以继续消费内部 request 形状，因为它是 ty 真实 adapter。

### 5.2 测试

产品测试与调用方走同一 seam：`lookup` / `collect_prepared` / `capture_highest` / `compare` / `compare_global` / `open_static_slice` / `locate_static_hint` / 离线 admission，以及真实 Check/Highest/Search 图。`lookup` 与 `compare` 的产品调用方是测试，不是 Check/Search。静态事实从这些 outcome 观察，不读取 Evaluator/cache private state，不直接构造 `PreparedEnvironment` 成功值。

路径/ignore/relocation/configuration/process/external/subject 的现行叶子测试不再写入 D002 §11，也不再单独规定产品契约。它们要么改写为公开表面的语义断言（六组输入变化、合法重定位命中、未闭合输入不启动 ty），要么降为静态 module 内部测试。

Plan **首切片**必须列出逐文件去向，AC7/AC8 按该表核对，至少包括：

`tests/test_static_{cache,comparison,configuration,external,guidance,guidance_qualification,ignores,inputs,journal,lifecycle,ownership,paths,process,relocation,report,request,subject}.py`、`tests/test_runtime_static_scope.py`、`tests/scripted_static.py`、`tests/static_fixtures.py`。每项标明：改写到 §3.1 表面 / 降为 module 内部 / 删除。漏列视为 AC7 未完成。

`CoordinateSearch` 测试继续注入 `StaticGuidanceEvaluator`；产品 Search 测试消费真实 slice，不在测试里复制 `_RunnerStaticSlice`。

Ty 与 filesystem 仍用 lower adapter / 临时项目替换；这是已有真实 seam，不为本 module 新增假想 Protocol。

## 6. D002 地图（与加深同一吸收）

吸收时重写 D002 §3 为 **module 地图**，不以文件清单冒充所有权。地图与静态加深必须同一次 owner 吸收，避免先改文件名、后再改 interface 造成两次漂移。

必须成立：

```text
static module                 原始 TyCheck、Run cache、准入/比较、纯 guidance
evaluation.py                 RuntimeEvaluator、动态 cache、stage permits
cancellation.py               Run 取消（现行代码已有，地图补上）
failure.py                    FailurePolicy；编排器内部构造
```

不再把不存在的 `src/pf/static.py` 画成独立 module。`schemas/static.py` 是记录模块，保留。`static_request.py`、`static_guidance.py`、其余 `static_*.py`、`ty_fact.py`、`ty_options.py` 不是独立 module；内部文件名由实现决定，地图不跟踪。现行其余 module 行（cli、project、environment、search、verification、report 等）按代码核对后保留或更正，不借本次删掉真实 seam。

§4 schema 表在现行 config/project/evaluation/report/apply 之外补上 `schemas.policy` / `schemas.journal` / `schemas.static_*` / `schemas.ty_fact` 记录范围，并写明：validator 只做结构与 identity；D004 算法不在此执行。

## 7. FailurePolicy 构造

`CompatibilityChecker`、`HighestVersionVerifier`、`SearchCoordinator` 与 `_ProposalRunner` 现行接受 `failures: FailurePolicy | None = None`，缺省再 `FailurePolicy()`。生产只有一个实现；`cli.py` 不传入；测试也不替换它。这是假想 seam。`VerificationRunner` 已内部构造，作为目标形状。

目标：四个编排器内部构造，删除可选参数。`FailurePolicy.classify` / `record_prepare` / `record_evaluation` 仍是 D005 的公开分类 interface，产品测试与 `tests/test_failure.py` 可直接调用以构造记录或断言分类；Check/Highest/Search 的产品测试从公开 outcome 观察 disposition/cause，不注入第二份 policy。

不能仅因「一个生产实现」删除 `ConfiguredVerifier`、`UvOperations` 或 CLI 七个 workflow Protocol：那些有生产与测试两套 adapter，是真实 seam。

## 8. 明确不做

- 只把浅文件挪进子目录，或让公开 interface 与现在的 Evaluator + Cache + RequestFactory + 叶子类型等宽。
- 新增 evaluator facade、parameter bundle、DI、hint/cache manager。
- 把三个产品编排器合成一个评价 module。
- 恢复 region、witness、静态 compatibility disposition，或跨运行 Evaluation cache。
- 改 Schema 1 字段、generation 规则或 JSON 投影手改。
- 另开一份只搬 schema 函数路径的平行 Design。
- 以本文件为现行 D002/D004/D005；吸收前调用方仍以现行 owner 为准。
- 仅因单实现而删除 `ConfiguredVerifier`、`UvOperations` 或 workflow Protocol。

## 9. 验收标准

| AC | 必须成立的目标 | 公开 seam / 证据 |
| --- | --- | --- |
| AC1 | `src/pf` 中 cli/check/baseline/search/verification/workflow/coordinate_search/report/runlog 只进口 §3.1 公开静态名字与 schema 记录，不进口 §3.1 禁止清单。ReportStore/Journal 进口离线 admission，不进口 schema 重放 | import 扫描；失败即未完成 |
| AC2 | 生产构造 `StaticEvaluator` 不出现 `StaticRequestFactory`；request 装配在 module 内 | `cli.py` 与产品测试装配 |
| AC3 | SearchCoordinator 不再定义手写 slice，不进口 cache ref 去构造 `StaticPoint`；`open_slice` 接收该坐标 `CandidateSnapshot`；`open_static_slice` 返回静态 module 的 adapter | `search.py` 与 Search/CoordinateSearch 测试；hint 语义与现行 D003/D004 一致 |
| AC4 | `src/pf/schemas/` 满足 §4.2：禁入 harness/static_*/ty_options/resolution 算法进口；§4.2 表内既有例外不报失败；`static_scope` / report / journal validator 不重放 compare 或 `locate_static_hint` | import 扫描 + validator 调用点；比较/搜索审计正反例改走静态 admission |
| AC5 | 准入、减法、`locate_static_hint` 各只有一处实现；在线 `compare` 与离线 admission 共用 | 改算法一处则两边同时变化的语义测试（经 `compare` / 离线 admission，不经 schema validator） |
| AC6 | 吸收后 D002 §3 是与代码一致的 module 地图：静态一个 module、有 `cancellation.py`、无虚构的 `src/pf/static.py`、保留 `schemas/static.py` 记录、不把静态叶子/`ty_fact`/`ty_options` 写成独立 module；§4 含 policy/journal/static_* 且禁止向上执行算法 | owner 正文与索引；与静态加深同一 Plan 吸收 |
| AC7 | 吸收后 D002 §11 与 D004 §6–§7 描述 §3 表面；叶子测试不再作为产品契约。Plan 首切片的逐文件去向表覆盖 §5.2 所列 tests，AC 按表核对 | owner 正文；去向表；产品测试进口 |
| AC8 | lookup 只读、collect 占 permit、同 key 单次 ty、GLOBAL/SLICE 准入、无静态 disposition、Run 作用域 cache 均保持 | 现行公开语义测试迁移到新表面后仍成立，不靠叶子文件；去向表中「改写」项有对应公开断言 |
| AC9 | `TyCheckCache` 仍由 VerificationRunner 构造并显式传入；cli/check 不装配 cache 内部类型；不出现跨 Run 隐式共享 | Runner/Check/Search 装配测试 |
| AC10 | Schema 1 字段集合不变；identity digest 字节不变；生成投影 `--check` 通过 | `generate_report_schema.py --check`、既有 report round-trip |
| AC11 | 生产路径无 static witness/region/compatibility 拒绝；静态失败最多 NO_HINT | 现行 D003/D004 权威测试，无回归 |
| AC12 | Check / Highest / Search / `_ProposalRunner` 不再接受 `failures=`；内部构造 `FailurePolicy`。D005 分类规则不变。产品编排测试不注入假 policy | 构造器扫描；Check/Highest/Search 公开 outcome；`ConfiguredVerifier` / `UvOperations` / workflow Protocol 仍在 |

停止条件（任一成立则本 Design 未交付）：新公开表面与 Evaluator + Cache + RequestFactory + 叶子类型等宽；只改目录；schemas 仍调用换了路径的 admit/hint/harness；`static_scope` / report / journal 读入链仍在 schema validator 内重放比较或 hint；D002 地图另一次变更才改；`failures=` 仍留在四个编排器上。

## 10. 接受状态

草案。接受后状态改为「已接受待实施」，再写覆盖 AC1–AC12 的 Plan；Plan 完成并吸收进 D002/D004/D005（及相关指针）后，本文与 Plan 一并归档。本文不授权改生产代码。

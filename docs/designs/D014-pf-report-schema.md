# PF 报告 Schema 1

- **状态：** 现行
- **版本：** `schema_version = 1`
- **最后核对：** 2026-09-09
- **产品语义：** [D001](D001-pf.md)
- **领域模型：** [D002](D002-pf-implementation.md)–[D005](D005-pf-failure-and-diagnose.md)、[D008](D008-pf-verification-run.md)、[D012](D012-pf-harness-relaxation.md)、[D013](D013-pf-pytest-observer.md)
- **机器结构：** [package-floor-v1.schema.json](../schemas/package-floor-v1.schema.json)
- **最小示例：** [complete](../examples/package-floor-v1-minimal-complete.json)、[incomplete](../examples/package-floor-v1-minimal-incomplete.json)
- **实施记录：** [P013](../archived/plans/P013-pf-report-schema.md)

本文是 `package-floor.json` wire interface、typed refs、规范编码和跨引用验证的唯一所有者。JSON Schema 是由同一 Pydantic wire model 生成的机器可读结构投影；搜索、failure、static/runtime evidence、harness 和 apply 的领域含义仍由上列文档拥有。

规范化引用图是首发 Schema 1。它已原地替换此前未发布、未命名的开发期内联布局；Reader
只接受版本 1，不提供旧 reader、migrator、alias、dual-read 或 dual-write。

## 1. 文档模型

报告是一个自包含、规范化的有向引用图：每类高扇出实体只定义一次，CellResult 与其他证据只保存 typed ref。顶层固定为：

```text
schema_version = 1
identity        generation identity 与 source/policy
inputs          declarations、target Cells、search policy、series inventories、CandidateSnapshots
evidence        graphs、Attempts、Proposals、terminal Evaluations、Failures
cell_results    每个已观察 Cell 的唯一 root
projections     apply 所需的声明投影证据
result          complete | incomplete
```

字段、判别值、required/optional 形状以生成的 JSON Schema 为准。可选事实不存在时必须省略；只有 required
`SearchPolicyBinding.requested_space`、`CandidateSnapshot.series_inventory_ref`、Proposal 的
`environment_plan_digest`、operation-structured authority 的 `terminal`、UNSAT request_binding
的两个 plan digest、以及 `ProbeObservation.selection_reason`（`dependency is None` 时）允许下文规定的 null。
其他显式 null、额外字段、Pydantic coercion 或由默认值补出的 wire facts 都无效。

### 1.1 Identity

`identity` 保存：

- `report_generation_id`；
- generator name/version/algorithm；
- canonical package name、项目相对`pyproject.toml`路径与`requires_python`；
- 完整 SourceSnapshot identity；
- required `execution_policy` 对象（`ExecutionPolicy`，hash domain `pf:execution-policy:v1`）；
- required `guidance_policy_identity`；
- required `search_derivation_identity`；
- `policy_identity`：report provenance digest（domain `pf:policy:v1`），不是 Attempt 的执行身份；
- required `verifier_outcome_policy = configured-verifier-terminal-v1`；
- required `failure_policy = failure-execution-v4`。

Generation ID 的唯一算法是：

```text
sha256(
  "pf:report-generation:v1\0" + canonical_identity_json({
    generator,
    package,
    source_snapshot,
    policy_identity,
    execution_policy_identity,
    guidance_policy_identity,
    search_derivation_identity,
    verifier_outcome_policy,
    failure_policy,
    source_plan,
    search_policy,
    requirement_declarations sorted by declaration_id,
    target_cells sorted by cell_identity,
  })
)
```

系列观测、CandidateSnapshot、CellResult、Projection、Failure、Evaluation 和 wire refs 不进入 generation ID。
它们仍必须属于该 generation，并由 reader 复证。完整规范 search_policy 含未选默认分支，即使纯 host-partial
且无 CandidateSnapshot 也进入 generation；不能从当前内建默认值补历史策略。

SourceSnapshot identity包含普通`entries`和全部owned `pyproject_identities`。owned pyproject在entries中仍保留path/kind/mode与空content digest以维持路径成员集合，同时要求：

```text
PyprojectIdentity = (path, mode, remainder_digest, dependency_arrays_digest)
sha256("pf:pyproject-remainder:v1\0" + canonical_identity_json(tagged(remainder)))
sha256("pf:pyproject-dependencies:v1\0" + canonical_identity_json(tagged(dependency_arrays)))
sha256("pf:source-snapshot:v1\0" + canonical_identity_json({entries, pyproject_identities}))
```

`dependency_arrays`保留`project.dependencies`与`project.optional-dependencies`字段是否存在；remainder是移除这两项后的parsed TOML。tagged TOML tree区分table/array/string/bool/int/float、offset/local datetime、date与time；table key排序、array保序，finite float用hex并保留`-0.0`，inf/-inf/nan使用规范token。缺`pyproject_identities`的旧Schema 1开发期报告fail closed，不提供fallback。

raw `[tool.pf]`仍属于owned pyproject remainder，因此任何持久PF配置变化都会改变SourceSnapshot与
generation identity；报告不另行序列化raw配置。每次resolution另外从冻结snapshot中的root与target
`pyproject.toml`输入计算
`sha256("pf:uv-project-configuration:v1\0" + canonical_identity_json(inputs))`：owned pyproject使用完整
`PyprojectIdentity`，非owned target使用snapshot file entry。该摘要进入`pf:resolution-context:v1`，与exact
uv version、实际InterpreterIdentity、Cell、SourcePlan和既有resolution/yanked policy facts共同闭合Attempt；PF search、timeout、
scheduling和prerelease推断不进入resolution context。Apply authorizer从report/current SourceSnapshot中的
同一root/target inputs重算该摘要并在任何source-drift waiver前比较；target dependency arrays先投影为
已由apply结构授权的report值，以保留original/projected/no-op语义，不新增wire字段。

报告 identity 拆成三类独立 policy，外加 generation/apply 用的 provenance digest。Attempt、Proposal、
FailureScope 与动态 evidence 只绑定 `ExecutionPolicy.identity`。公开报告不保存静态 fact 或
比较；Run 内原始静态事实只在 ty-cache。

| 字段 / 对象 | Domain / 字面量 | 拥有的规则 | 明确不包含 |
| --- | --- | --- | --- |
| `execution_policy` | `pf:execution-policy:v1`；`failure = failure-execution-v4`；`rules = configured-execution-only-v1` | resolution 的 `artifact/timeout_seconds`、configured verifier 的 `command/cwd/timeout_seconds`、原命令 PASS、failed-case 资格、`execution-outcome-v1` / `operation-structured-facts-v1`、uv protocol/profile、validation-contract 字面量 | ty version/args/timeout/内容、静态比较/anchor/二分、hint 消费、坐标搜索顺序 |
| `guidance_policy_identity` | 等于 [D004](D004-pf-ty-enhancement.md) `GuidancePolicy.identity`（域 `pf:guidance-policy:v1`） | 完整 GuidancePolicy 预像：观测策略 generation、比较/anchor/hint/fallback | 观测 digest；动态 verifier authority、实际 anchor Proposal |
| `search_derivation_identity` | SearchDerivationPolicy | 坐标算法、机械 probe、fast path、hint 消费及所用 Guidance identity | 某次运行的 observation 或动态终态 |
| `policy_identity` | `pf:policy:v1` | 仅作 report provenance：绑定 guidance/search identity 与 generation 隔离所需的固定执行契约事实 | Attempt/Proposal 执行身份；ty 采集字段只存在于 Guidance/TyObservation |

`execution_policy` 对象还固定：`verifier_outcome = configured-verifier-terminal-v1`、
`pass_authority = direct-test-command-pass`、`failed_case_authority =
qualified-rejection-only-original-command-for-pass`、`execution_outcome = execution-outcome-v1`、
`structured_facts = operation-structured-facts-v1`、`uv_protocol = uv-pip-compile-pylock-v1`、
`uv_diagnostic_profile = uv-diagnostics-0.12.5-v1`，以及
`test_group_selection` / `empty_harness_prepare` / `project_marker_projection` /
`resolution_projection` / `inspection` / `self_reference` / `extra_exploration` /
`baseline_harness` / `probe_harness` / `project_overlap` / `external_ceiling` 的现行字面量。
这些不是用户配置或运行期匹配结果；classifier/profile 或 normalization 语义变化须同步变更
ExecutionPolicy identity。uv 归因 code 表由 D005 拥有。test group 名称、target/extra、candidate
search 和全部 scheduling limits 不进入 ExecutionPolicy。具体 surface、declarations 和 observations
仍由 Cell/source snapshot、Attempt/resolution evidence 绑定。

`project_marker_projection` 隔离五字段 target-derived project 求值语义；其余 contextual 字段不因此
获得可移植承诺。RequirementDeclaration/TargetCell/marker 字符串与 active refs 沿用 Schema 1，
不序列化 marker AST/environment，也不增加 ApplySelector 维度。JSON Schema 与 v1 digest prefix
不变，examples 随三类 identity 重生成；不提供旧 policy reader、fallback 或 migrator。

Apply 在任何 source-drift waiver 前分别比较当前 `execution_policy.identity` 与
`policy_identity` / `guidance_policy_identity` / `search_derivation_identity`；前者不匹配是
`execution-policy-mismatch`，后者不匹配是 `search-provenance-mismatch`。force 不绕过任一类。
ty/guidance 改变可以令旧报告不能直接 merge/apply，应诊断为 provenance 不匹配，而不是原 verifier
PASS 失效。merge/update 拒绝跨 generation 混合；update_path 整体替换。离线 read 内部自洽的
报告不自动授予当前语义的 apply 或与新 generation merge 的权限。Schema 版本和 generation 前缀保持 v1；
baseline/Attempt digest 的变化不能代替上述 generation/apply 检查。


### 1.2 Inputs

`inputs` 是 generation 的声明与搜索输入：

- `requirement_declarations` 以 `declaration_id` 唯一、排序；
- `target_cells` 以内容寻址 `cell_id` 唯一、排序，并只引用本表声明；
- `candidate_snapshots` 以 `candidate_snapshot_id` 唯一、排序，每个 `(cell_ref, dependency)` 最多一条；每条保存
  required `baseline_selection` 与非空 `candidates`。
- required `search_policy` 保存规范请求分组，见 §1.2.1；required `series_inventories` 保存可达必要观测，见 §1.2.2。
- `source_plan`是required generation input，保存`source_mode = SEARCH`与按dependency排序、唯一的
  `DependencySourceRoute`；每条route绑定development/search source及可选workspace member version
  metadata。它是唯一 SourcePlan wire 值；派生 `identity` 与查询不进入 JSON。

CandidateSnapshot 的 selection policy 与报告三类 policy identity 是不同事实，因此 record 自带
`policy_identity`，但 reader 必须从保存的请求和已验证 evidence 复算，不能信任 opaque digest。
每条 CandidateSnapshot 还保存`source_plan_identity`；它必须等于完整generation SourcePlan的唯一摘要，
且 Reader 必须通过 `SourcePlan.source_for` 证明其 dependency/source 精确对应 registry SEARCH effective source。Workspace member当前版本
不进入candidate records。

### 1.2.1 请求策略

DSL、anchor、条件默认与候选准入语义只见 [D037](D037-pf-candidate-search-policy.md)；
本节定义它们的 wire 投影和离线校验，不重新定义配置继承或算法。

`inputs.search_policy` 的 required 结构为：

```text
profile = registry-series-slice-v1
artifact = any | wheel | sdist
bindings[]
  dependencies[]        非空 canonical names
  requested_space       canonical explicit string 或 null（省略）
  space_defaults        {with_lower_bound, without_lower_bound}，完整 canonical DSL
  resolution            major | minor | patch
  prereleases           bool
```

profile/artifact 每报告只保存一次。完整策略相同的 names 合为一组；组内 names 排序唯一，组间不重叠且
按首 name 排序，恰好覆盖全部 managed searchable names。无 searchable name 时 bindings 为空。Reader
拒绝相同策略拆组、缺失/多余 name 与非规范输入；展开 typed mapping，不重做配置继承或引入策略 ID/ref。
即使显式 space 生效，完整 defaults 仍保存并绑定 generation，用于授权比较未选分支变化。

### 1.2.2 必要系列观测

`inputs.series_inventories` 按 `series_inventory_id` 排序唯一，无 series selection 时为空。记录只包含
canonical dependency、public registry source、family 和必要 scope 的完整非空有序唯一 `series_keys`。
Major key 为 `(epoch, major)`，minor key 为 `(epoch, major, minor)`；共同前缀派生 scope，不另存 scope。
ID 是 `sha256("pf:series-inventory:v1\0" + canonical_identity_json({dependency, source, family, series_keys}))`，
其中 `\0` 是 NUL 字节。只保存必要 scope，不保存全部精确 releases、未选 artifacts 或 HTTP response。

同内容跨 Cell intern 一次；同名/source/family/scope 但 keys 不同保留不同观测，不能覆盖。内容去重只说明
DSL 所需事实相同，不证明查询时刻或完整 response 相同。CandidateSnapshot 的 required
`series_inventory_ref` 在 series space 时引用匹配 dependency、SEARCH source、family 与 anchor scope 的记录；
all/specifier 时必须为 null。Reader 拒绝悬空、错配和 roots 不可达观测。不得截断列表或设置任意条数上限
来改变偏移语义。重复 Cell 只增加引用，不重复嵌入策略或系列列表。

### 1.2.3 离线派生与候选 identity

Wire 不保存 selection、effective expression、默认分支/原因、anchor versions、索引或选中系列。Reader
先验证原始 declarations、请求策略和持有 CandidateSnapshot 的 Cell 的真实 full PASS baseline，再经共享
`search_space` 派生 `explicit/default-declaration/default-unbounded`、effective expression、实际引用的原始
anchors、scope 和选中 keys；不用当前项目下界重解释。无 snapshot 的 Cell 按其终态要求验证，不强求 PASS。

`pf:candidate-policy:v1` preimage 为
`{profile, policy:{name,space,resolution,prereleases}, artifact, artifact_admission}`，其中
`artifact_admission = "cell-eligibility-before-sha256"` 表示先判断 Cell 适用性，再要求安装 locator
与 SHA-256 的当前准入语义；space 是该 Cell 的 effective canonical expression。前缀仍为
`pf:candidate-policy:v1\0`，追加上述对象的 canonical JSON UTF-8 后作 SHA-256。
Reader 使用生产共享 `candidate_policy_identity` 构造完整 preimage 复算并核对，包括固定准入事实；
不接受 opaque identity、自报策略事实或第二套兼容复算实现。Snapshot digest 在 Cell/source/SourcePlan、
完整 `baseline_selection` payload、candidates/representatives 上另绑定派生的 effective expression、原因、实际使用 anchor versions
与 series inventory ref；即使代表相同，使用的 anchor 或观测变化仍改变 identity。

Reader 检查 `baseline_selection` 的 dependency、版本与当前 Cell baseline Proposal、public locator/hash、artifact
policy/Cell compatibility，以及与同版本 Candidate 的完全一致性；再检查 anchor membership/scope、切片结果，所有候选的 space、保留声明限制、baseline cap、prerelease、
artifact 与 wheel compatibility，并按 resolution 复算 series key、版本排序和每系列唯一性。它未保存完整候选观测，
因此不证明 registry 完备性、没有未保存 release 或代表为 registry 最高合格版本；最高代表由 CandidateBuilder
对同次完整观测过滤后采样保证。baseline selection 不进入上述候选资格、顺序或系列证明。

每个 exact-vector Attempt 的完整 requested vector 必须逐 dependency 通过对应 CandidateSnapshot 的
`select(version)` 从 `candidates ∪ {baseline_selection}` 唯一重建，并以 canonical dependency order 重算
`pf:selected-candidates:v1` digest；任何缺 snapshot、空间外非 baseline 版本、artifact 不一致或 digest 漂移都
fail closed。SUCCESS、SEARCH_FAILED 与带搜索证据的 CELL_INDETERMINATE 都须保存重建其全部 exact Attempt
所需的 snapshots；不得从 resolved graph、active dependency 或 baseline Attempt 猜测 selection。

保持 Schema 1、generator algorithm v1 和既有 v1 前缀；缺上述 required 输入或引用的 wire fail closed，
不提供兼容 reader、默认推断或 aliases。

### 1.3 Evidence

`evidence` 只含动态定义表。公开报告删除 `static_contents` / `static_subjects` /
`static_facts` / `static_comparisons` / `static_scopes`，不新增 `static_audits`。旧 intern
字段校验失败即停。

| 表 | 稳定 owner/key | 主要依赖 |
| --- | --- | --- |
| `resolution_graphs` | `resolution_graph_id` | canonical nodes |
| `attempts` | `attempt_id` | Cell、SourcePlan identity、ExecutionPolicy identity、resolution context、harness facts、request |
| `proposals` | `proposal_id` | Attempt、managed vector、fixed declarations、graph、project 与 nullable environment plan digest、interpreter |
| `evaluations` | `proposal_ref` | Proposal、verifier terminal/failure ref |
| `failures` | `failure_id` | Cell/Attempt scope、disposition、cause、stage、FailureAuthority、已取得 plan digests |

Proposal 只有在 prepare 成功并复证实际 graph 后才能存在；prepare failure 可以引用 Attempt，但不能虚构 Proposal。成功 Proposal 的 `project_plan_digest` 必须非空，`environment_plan_digest` 是 required-nullable：key 必须存在，null 当且仅当引用 Attempt 的 active harness declaration IDs 为空；非 null digest 必须非空。interpreter Python minor 必须匹配 Attempt Cell。

Project-only Proposal 的 graph 来自已安装并复证的 project plan；有 harness 时来自 environment plan。EnvironmentIdentity preimage 显式保留 null，reader 重算 identity 并拒绝分支不一致、缺 key 与 digest 漂移。wire serializer 在 exclude_none=True 下显式保留该 null，Schema generator 保留其 null 分支；generation、write/read、merge/update、examples 共享此契约。其他 optional fields 仍须省略。Schema version 保持 1，无旧形状 fallback。

固定 test-group selection / empty-harness facts 进入 ExecutionPolicy，具体 group 与原文仍由 SourceSnapshot 绑定。跨 policy generation 不可 merge/apply，update_path 按既有规则整体替换，force 不豁免 execution-policy 或 search-provenance mismatch。

公开 `ProbeObservation` 增加 required-nullable `selection_reason`：`dependency is None`
（baseline 起点、final confirmation）时为 `null`；否则为
`mechanical-lowest | mechanical-midpoint | history | static-suspect |
static-clean-neighbor | direct-existing | external-hint | current-upper`。
Reader 只验证字面量与 null 规则，不把 `static-suspect` 当 authority。
Terminal Evaluation 只能是现行领域 union
`PASS | VERIFIER_REJECTED | INDETERMINATE`。Verifier evaluation 保存 `ExecutionTerminal`；
不保存完整 `ProcessResult` 或 pytest diagnostics。

Failure wire 必须恰有一个判别 `authority`：
`process | configured-verifier | structured | execution | operation-structured`。Reader
拒绝缺失、混合、额外或与 cause/stage/disposition 不匹配的 authority，并以完整
`pf:failure:v3` preimage 重算 failure ID。

execution 保存 terminal/attribution，operation-structured 保存 fact/required-nullable terminal，
configured-verifier 仅保存 terminal。Reader 复用 [D005 §3–4](D005-pf-failure-and-diagnose.md#3-rejection-资格)
的 authority family、stage、terminal、cause/disposition、harness/plan 时序与 binding 校验；
wire 不另建分类表。ProcessObservation sidecar 不进入 wire 或 Failure ID。

Prepare failure 可只引用 Attempt 和已取得 plan，不要求 Proposal；失败 R 的未通过检查输出
不得变成 plan evidence。FailureRecord 顶层 optional plan 空值仍省略，嵌套 binding 的 nullable
plan 与 structured terminal 即使 exclude_none=True 也必须保留 null。Schema/examples 同步生成，
没有旧 wire reader、缺省 failure policy、别名或迁移层。

Attempt 的 resolution_context_digest 必须非空并参与 Attempt identity 复算，但其 preimage 不在
report，仍是 opaque digest。离线 reader 不检查其未保存原文与 credential 的相等性，不加载
当前 context 或日志；该逐字段校验属于 D012 producer。重新哈希也不能豁免可观察的 stage、
terminal、cause/disposition、binding、plan timing、identity/refs 约束。

Schema 1只接受当前完整 preimage 的`attempt-v1` identity。每个Attempt的`source_plan_identity`必须等于generation
SourcePlan；exact-vector的`selected_candidate_evidence_digest`继续绑定由registry search route
取得的CandidateSnapshots。Proposal保存运行期已闭合的project/nullable environment plan digest与graph ref，不把native
pylock或本地workspace provenance复制进公共wire。

### 1.4 Roots 与 projection

`cell_results` 的判别变体是：

```text
SUCCESS
CELL_INDETERMINATE
BASELINE_REJECTION
BASELINE_INDETERMINATE
SEARCH_FAILED
```

每个结果只通过 ref 连接 baseline、candidate snapshots、observations、boundaries、FailureRecords 和 final Proposal。`SUCCESS.final_proposal_ref` 是 final vector 与 final PASS Evaluation 的唯一 authority；wire 不保存第二份 `final_vector` 或 `observed_upper`。

Direct observation 必须引用当前 Attempt，并闭合到同一 Proposal/Evaluation/Failure。Rejection/Indeterminate
要求 exact-vector Attempt；PASS 可以引用 exact-vector，或引用当前 CellResult 的真实 baseline highest Attempt、
Proposal 与 PassEvaluation。后一种 observation 的 vector 只从 baseline Proposal managed vector 展开，且必须与
CellResult 的 `baseline_attempt`、static baseline 和 baseline evaluation roots 完全一致；其他 highest、跨 Cell/
context、漂移向量与非 PASS highest observation 均无效。静态事实不进入报告，也不能成为 boundary 或 final
authority。Boundary predecessor failure、failure disposition、non-monotonic counterexample 和 coordinate
outcome 必须与 D003–D005 的展开语义一致。

`projections`只保存declaration ref、Cell ref、exact floor、生成requirements与`representable`。Reader展开后复证D001的完整TargetCell→floor映射和complete authority。`result.status = complete`当且仅当全部target Cells有成功root且全部full-matrix projection可表示；否则为`incomplete`并保存规范reason集合。Incomplete的空/不可表示full-matrix projection本身不授权apply；`ApplyAuthorizer`只从final `CellSuccess` roots请求一次apply-time group projection，scope/waiver/history不写回wire。

## 2. 引用规则

1. Ref 只在同一报告内有效；不得引用路径、外部文件、另一个 generation 或数组下标。
2. 每个定义 ID 唯一；同一 ref 可重复使用，但重复定义即使 payload 相同也无效。
3. ID 对展开后的领域事实计算，wire ref 字符串本身不替代 identity 输入。
4. RequirementDeclaration、Cell、CandidateSnapshot、Attempt、Proposal、Static/Terminal Evaluation 与 Failure 的 Cell scope 必须一致。ResolutionGraph 可以在 Cells 间按内容共享。
5. 图必须符合固定依赖方向，不允许任意 JSON Pointer、反向 owner 或循环引用。
6. 从 `cell_results` roots 出发不可达的 CandidateSnapshot、Attempt、Proposal、Evaluation、Failure 或 graph 是附加数据池，必须拒绝。Generation inputs 中声明和 target Cells 仍需完整保留。
7. `harness_declaration_ids` 是 Attempt identity 中的 opaque declaration IDs；公共报告没有 HarnessRequirement table，不能伪装成 typed refs。

## 3. Reader 验证

`ReportStore.read` 按以下顺序 fail closed：

1. `stat` 预拒绝超过 64 MiB 的输入，读取后再次检查以覆盖竞态；
2. 只按 UTF-8 解析 JSON，拒绝非法编码、语法、递归深度、非对象根和非版本 1；
3. 以严格 wire model 验证字段、类型、判别 union、无额外字段，仅允许 §1 规定的 required-nullable 路径；
4. 要求输入与 `model_dump(exclude_none=True)` 完全一致，禁止 coercion/default 补事实；
5. 建立线性的 typed indexes，拒绝重复、未知或错误种类的 ref；
6. 要求SourcePlan为SEARCH，通过其 interface 复算唯一摘要与 effective source，并复算generation、
   Cell、CandidateSnapshot、ResolutionGraph、当前 Attempt v1、Proposal 与 Failure v3 identity；
7. 验证 cross-cell scope、Evaluation/Failure/Proposal 闭环、搜索边界、projection 与 result；
8. 从 roots 检查全图可达性和规范顺序；
9. 返回 immutable、resolved `ValidatedReport`，不向调用方泄漏 wire refs 或 join 规则。

任何失败都映射为不含输入正文、凭据、路径或不可信动态 ID 的 `ConfigurationError`。Reader 不访问网络、不读取当前项目来补事实，也不根据本地日志改变报告 authority。

公共 locator 必须是规范、可移植且无凭据的相对路径或安全 source/artifact locator；绝对路径、file URL、credential/query 泄漏、Windows drive 路径和越界 `..` 均无效。Process output、`RuntimeEvaluationRun`/pytest diagnostics、run ID、临时路径和本地日志 locator 不进入报告。

## 4. 规范编码与持久化

Writer 的唯一编码是：

```python
json.dumps(
    wire.model_dump(mode="json", exclude_none=True),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
) + "\n"
```

§1 列举的全部 required-nullable 路径在 exclude_none 时仍保留 null；生成 Schema 保留其 nullable
形状。其余 optional facts 缺失时省略。编码为 UTF-8，只有一个末尾换行。实体表按稳定 ID 排序；CellResult
按 Cell identity 排序；projection 按 declaration ID 排序。一次 read→write 必须 byte-stable。

写入在目标目录创建临时文件，flush + file `fsync` 后原子 replace，再同步父目录；失败不得留下半写目标。

## 5. Module interface

Build/reintern/update/merge 保留原始规范 search_policy 与最终 roots 可达的 inventories，替换结果独占的旧
观测随不可达 evidence 清理；重建 PackagePlan 使用保存的完整 policy/artifact，不以 all/minor 或空 config
填补新事实。Generation compatibility 包括完整 search_policy，跨 generation 仍不混合。

`ApplyAuthorizer` 在 source-drift waiver 前比较当前合并的完整 search_policy（含 profile、artifact 和未选
默认分支），并分别检查 ExecutionPolicy 与 search/guidance provenance，force 不绕过 mismatch。它不联网重算系列；报告锚点始终由
原始声明派生。首次 original、已投影 projected 与重复 no-op apply 沿用既有声明结构授权，不因改写下界
重解释旧报告。`ValidatedReport.search_spaces()` 提供 typed policy/selection/representatives，Explain 只消费
已验证投影，不解析 DSL 或 join wire refs。

```text
PackageReportBuilder.build(package, source_plan, source_snapshot, cell_results)
    -> ValidatedReport
PackageReportBuilder.project(declarations, target_cells, floors,
                             selected_selectors, platform_scoped)
    -> DependencyGroupProjection

ReportStore.read(path) -> ValidatedReport
ReportStore.write(path, report) -> None
ReportStore.merge(reports) -> ValidatedReport
ReportStore.update(existing, replacement) -> ValidatedReport
ReportStore.update_path(path, replacement) -> ReportUpdate
```

`MergeCommandWorkflow`可把validated report、ordered input paths和output path包装为结构化command result/error供Presenter使用；这些presentation facts不进入report，不改变下述merge compatibility、canonical graph、generation或atomic write authority。

`PackageReportBuilder`把领域`CellResult` intern为规范图并计算report projection/result；Search writer 把真实 Run plan 同时用于 generation identity 与 `inputs.source_plan`，merge/update reintern 复用 generation plan，不从 PackagePlan 重建。同一owner的`project`按dependency group重生成Cell→PEP 508 projection并重求值。`ReportStore`独占wire codec、typed index、ref展开、完整验证、merge/update和原子事务；raw routes 只用于严格 codec、public locator 与 cross-ref，effective source/identity 闭合走 SourcePlan interface。Workflow、authorizer、explain与diagnose只消费`ValidatedReport`；editor只消费authorized edits。上述模块不得import wire records、读取`_wire`或自行join refs。

同generation merge/update要求generator、package/requires-python、source snapshot（含dependency-array identity）、ExecutionPolicy、guidance/search identity、report provenance、verifier/failure policy、SourcePlan、declarations与target Cells完全兼容；先展开final CellResult roots，再重新intern整图，因此旧的不可达evidence被清理，共享graph只保留一次。相同Cell的冲突结果或相同evidence ID的不同payload直接拒绝，不合成运行期NONDETERMINISTIC，也不跨运行重试Attempt归因。`--force`不参与merge。不同generation的`update_path`整体替换；空replacement不删除existing Cells。`read`/`merge` 对坏报告 fail closed，含旧 intern 字段。`update_path` 在 replacement 已是现行 `ValidatedReport` 时，把不存在、不可读或非法 existing（含旧 intern）视为缺席并写入 replacement，不与坏文件 merge。合法但 generation 不同的 existing 仍整体替换。合法apply会改变dependency-array/full snapshot identity并开始新generation，apply前后reports不可merge/rebase。

`ReportUpdate` 只向 diagnosis association seam 暴露 `replace_generation` 与已移除 Failure IDs；ReportStore 不依赖 RunLogStore。

## 6. 已发布工件与验证状态

- JSON Schema 和两个最小示例只能由 `scripts/generate_report_schema.py` 从唯一 wire model 生成；`--check` 必须无漂移。
- 示例必须同时通过 Draft 2020-12 JSON Schema 与 `ReportStore.read`。
- Schema 1 的 canonical encoding、identity、typed refs、可达性与 merge/update 契约由独立的 public-behavior 测试验证。
- 开发期旧内联布局不是可读布局，也不作为 Schema 1 的兼容性、体积或性能验收基线。
- D017实施前仅保存去重`SourcePlan.identities`的开发期Schema 1布局不再可读；不提供alias、迁移或
  dual reader/writer。

`schema_version = 1` 是唯一可读写布局。

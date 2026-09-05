# PF 报告 Schema 1

- **状态：** 现行
- **版本：** `schema_version = 1`
- **最后核对：** 2026-09-05
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
evidence        graphs、Attempts、Proposals、static/terminal Evaluations、Failures
cell_results    每个已观察 Cell 的唯一 root
projections     apply 所需的声明投影证据
result          complete | incomplete
```

字段、判别值、required/optional 形状以生成的 JSON Schema 为准。可选事实不存在时必须省略；只有 required
`SearchPolicyBinding.requested_space` 与 `CandidateSnapshot.series_inventory_ref` 允许下文规定的 null。
其他显式 null、额外字段、Pydantic coercion 或由默认值补出的 wire facts 都无效。

### 1.1 Identity

`identity` 保存：

- `report_generation_id`；
- generator name/version/algorithm；
- canonical package name、项目相对`pyproject.toml`路径与`requires_python`；
- 完整 SourceSnapshot identity；
- evaluation policy identity；
- required `verifier_outcome_policy = configured-verifier-terminal-v1`。

Generation ID 的唯一算法是：

```text
sha256(
  "pf:report-generation:v1\0" + canonical_identity_json({
    generator,
    package,
    source_snapshot,
    policy_identity,
    verifier_outcome_policy,
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

evaluation policy identity的配置与既有工具preimage是resolution的`artifact/timeout_seconds`、ty的
`args/timeout_seconds`、test的`command/cwd/timeout_seconds`，以及既有ty tool version、diagnostic、configured
verifier outcome与failure policy facts；前缀仍为`pf:policy:v1`。test group、target/extra、candidate search和
全部scheduling limits不进入该identity。

Evaluation-policy canonical preimage 另含固定 `validation_contract_policy` 字段，其值为：

```json
{
  "self_reference": "required-effective-cell-surface",
  "extra_exploration": "nonempty-declared-groups-only",
  "resolution_projection": "actual-interpreter-target-active-pylock",
  "baseline_harness": "original-external-declarations",
  "probe_harness": "remove-eligible-direct-lower-bounds",
  "project_overlap": "exact-project-node-without-harness-ceiling",
  "external_ceiling": "baseline-observed-version-for-current-harness-only-node"
}
```

`policy.py` 统一物化这些语义事实，不是用户配置项；baseline 同时指 declaration-capture，probe 同时指
check declaration。行为本身由 D001/D012 拥有；具体 surface、declarations 和 observations 仍分别由
Cell/source snapshot、Attempt/resolution evidence 绑定，不放入此固定 policy 字段。
即使 source、Cells、generator 和显式 any/pytest 配置相同，normalization policy 不同也产生不同
evaluation policy/generation。merge/update 拒绝跨 generation 混合；update_path 整体替换。Apply 在
任何 source-drift waiver 前检查当前 evaluation policy，force 不绕过 mismatch。离线 read 内部自洽的
报告不自动授予当前语义的 apply 或与新 generation merge 的权限。Schema 1 字段不扩形，前缀保持 v1；
baseline/Attempt digest 的变化不能代替上述 generation/apply 检查。


### 1.2 Inputs

`inputs` 是 generation 的声明与搜索输入：

- `requirement_declarations` 以 `declaration_id` 唯一、排序；
- `target_cells` 以内容寻址 `cell_id` 唯一、排序，并只引用本表声明；
- `candidate_snapshots` 以 `candidate_snapshot_id` 唯一、排序，每个 `(cell_ref, dependency)` 最多一条。
- required `search_policy` 保存规范请求分组，见 §1.2.1；required `series_inventories` 保存可达必要观测，见 §1.2.2。
- `source_plan`是required generation input，保存`source_mode = SEARCH`与按dependency排序、唯一的
  `DependencySourceRoute`；每条route绑定development/search source及可选workspace member version
  metadata。它是唯一 SourcePlan wire 值；派生 `identity` 与查询不进入 JSON。

CandidateSnapshot 的 selection policy 与顶层 evaluation policy 是不同事实，因此 record 自带
`policy_identity`，但 reader 必须从保存的请求和已验证 evidence 复算，不能信任 opaque digest。
每条 CandidateSnapshot 还保存`source_plan_identity`；它必须等于完整generation SourcePlan的唯一摘要，
且 Reader 必须通过 `SourcePlan.source_for` 证明其 dependency/source 精确对应 registry SEARCH effective source。Workspace member当前版本
不进入candidate records。

### 1.2.1 请求策略

`inputs.search_policy` 的 required 结构为：

```text
profile = registry-series-slice-v1
artifact = any | wheel | sdist
bindings[]
  dependencies[]        非空 canonical names
  requested_space       canonical explicit string 或 null（省略）
  space_defaults        {with_lower_bound, without_lower_bound}，完整 canonical DSL
  step                  major | minor | patch
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
`{profile, policy:{name,space,step,prereleases}, artifact, artifact_admission}`，其中
`artifact_admission = "cell-eligibility-before-sha256"` 表示先判断 Cell 适用性，再要求安装 locator
与 SHA-256 的当前准入语义；space 是该 Cell 的 effective canonical expression。前缀仍为
`pf:candidate-policy:v1\0`，追加上述对象的 canonical JSON UTF-8 后作 SHA-256。
Reader 使用生产共享 `candidate_policy_identity` 构造完整 preimage 复算并核对，包括固定准入事实；
不接受 opaque identity、自报策略事实或第二套兼容复算实现。Snapshot digest 在既有 Cell/source/
SourcePlan/candidates/representatives 上另绑定派生的 effective expression、原因、实际使用 anchor versions
与 series inventory ref；即使代表相同，使用的 anchor 或观测变化仍改变 identity。

Reader 检查 anchor membership/scope、切片结果，所有候选的 space、保留声明限制、baseline cap、prerelease、
artifact 与 wheel compatibility，并按 step 复算 series key、版本排序和每系列唯一性。它未保存完整候选观测，
因此不证明 registry 完备性、没有未保存 release 或代表为 registry 最高合格版本；最高代表由 CandidateBuilder
对同次完整观测过滤后采样保证。既有精确 artifact、PASS/predecessor/final 证据要求保持。

保持 Schema 1、generator algorithm v1 和既有 v1 前缀；缺上述 required 输入或引用的 wire fail closed，
不提供兼容 reader、默认推断或 aliases。

### 1.3 Evidence

`evidence` 包含六张定义表：

| 表 | 稳定 owner/key | 主要依赖 |
| --- | --- | --- |
| `resolution_graphs` | `resolution_graph_id` | canonical nodes |
| `attempts` | `attempt_id` | Cell、SourcePlan identity、policy、resolution context、harness facts、request |
| `proposals` | `proposal_id` | Attempt、managed vector、fixed declarations、graph、两个 plan digest、interpreter |
| `static_evaluations` | `proposal_ref` | Proposal、TyCheck、baseline digest、increment/fingerprint/classification |
| `evaluations` | `proposal_ref` | Proposal、static evaluation、witnesses、verifier terminal/failure ref |
| `failures` | `failure_id` | Cell/Attempt scope、disposition、cause、stage、FailureAuthority、已取得 plan digests |

Proposal 只有在 prepare 成功并复证实际 graph 后才能存在；prepare failure 可以引用 Attempt，但不能虚构 Proposal。成功 Proposal 的 `project_plan_digest` 与 `environment_plan_digest` 必须非空，interpreter Python minor 必须匹配 Attempt Cell。

Static Evaluation 只能是 `STATIC_UNCHANGED | STATIC_REGRESSION`。Terminal Evaluation 只能是
现行领域 union `PASS | VERIFIER_REJECTED | RUNTIME_INTERFACE_MISSING | INDETERMINATE`。
Verifier evaluation 保存 `VerifierTerminal`；不保存完整 `ProcessResult` 或 pytest diagnostics。

Failure wire 必须恰有一个判别 `authority`：`process | configured-verifier | structured`。Reader
拒绝缺失、混合、额外或与 cause/stage/disposition 不匹配的 authority，并以完整
`pf:failure:v2` preimage 重算 failure ID。

Schema 1只接受当前完整 preimage 的`attempt-v1` identity。每个Attempt的`source_plan_identity`必须等于generation
SourcePlan；exact-vector的`selected_candidate_evidence_digest`继续绑定由registry search route
取得的CandidateSnapshots。Proposal保存运行期已闭合的两个plan digest与graph ref，不把native
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

每个结果只通过 ref 连接 baseline、candidate snapshots、observations、regions、boundaries、FailureRecords 和 final Proposal。`SUCCESS.final_proposal_ref` 是 final vector 与 final PASS Evaluation 的唯一 authority；wire 不保存第二份 `final_vector` 或 `observed_upper`。

Direct observation 必须引用当前 Attempt，并闭合到同一 Proposal/Evaluation/Failure。Static-only observation 只能引用同 Cell、baseline、Slice 和 fingerprint 的 region 与 runtime representative；它不能成为 boundary 或 final authority。Boundary predecessor failure、failure disposition、region runtime reference、non-monotonic counterexample 和 coordinate outcome 必须与 D003–D005 的展开语义一致。

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
3. 以严格 wire model 验证字段、类型、判别 union、无额外字段，仅允许规定的两个 required nullable 字段；
4. 要求输入与 `model_dump(exclude_none=True)` 完全一致，禁止 coercion/default 补事实；
5. 建立线性的 typed indexes，拒绝重复、未知或错误种类的 ref；
6. 要求SourcePlan为SEARCH，通过其 interface 复算唯一摘要与 effective source，并复算generation、
   Cell、CandidateSnapshot、ResolutionGraph、当前 Attempt v1、Proposal、region与Failure v2 identity；
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

两个 required nullable 字段的 wire serializer 在 exclude_none 时仍保留 null；生成 Schema 保留其 nullable
形状。其余 optional facts 缺失时省略。编码为 UTF-8，只有一个末尾换行。实体表按稳定 ID 排序；CellResult
按 Cell identity 排序；projection 按 declaration ID 排序。一次 read→write 必须 byte-stable。

写入在目标目录创建临时文件，flush + file `fsync` 后原子 replace，再同步父目录；失败不得留下半写目标。

## 5. Module interface

Build/reintern/update/merge 保留原始规范 search_policy 与最终 roots 可达的 inventories，替换结果独占的旧
观测随不可达 evidence 清理；重建 PackagePlan 使用保存的完整 policy/artifact，不以 all/minor 或空 config
填补新事实。Generation compatibility 包括完整 search_policy，跨 generation 仍不混合。

`ApplyAuthorizer` 在 source-drift waiver 前比较当前合并的完整 search_policy（含 profile、artifact 和未选
默认分支），保持既有 evaluation policy 检查，force 不绕过 mismatch。它不联网重算系列；报告锚点始终由
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

同generation merge/update要求generator、package/requires-python、source snapshot（含dependency-array identity）、policy、verifier policy、SourcePlan、declarations与target Cells完全兼容；先展开final CellResult roots，再重新intern整图，因此旧的不可达evidence被清理，共享graph只保留一次。相同Cell的冲突结果失败。`--force`不参与merge。不同generation的`update_path`整体替换；空replacement不删除existing Cells。`read`/`merge` 对坏报告 fail closed。`update_path` 在 replacement 已是现行 `ValidatedReport` 时，把不存在、不可读或非法 existing 视为缺席并写入 replacement，不与坏文件 merge。合法但 generation 不同的 existing 仍整体替换。合法apply会改变dependency-array/full snapshot identity并开始新generation，apply前后reports不可merge/rebase。

`ReportUpdate` 只向 diagnosis association seam 暴露 `replace_generation` 与已移除 Failure IDs；ReportStore 不依赖 RunLogStore。

## 6. 已发布工件与验证状态

- JSON Schema 和两个最小示例只能由 `scripts/generate_report_schema.py` 从唯一 wire model 生成；`--check` 必须无漂移。
- 示例必须同时通过 Draft 2020-12 JSON Schema 与 `ReportStore.read`。
- Schema 1 的 canonical encoding、identity、typed refs、可达性与 merge/update 契约由独立的 public-behavior 测试验证。
- 开发期旧内联布局不是可读布局，也不作为 Schema 1 的兼容性、体积或性能验收基线。
- D017实施前仅保存去重`SourcePlan.identities`的开发期Schema 1布局不再可读；不提供alias、迁移或
  dual reader/writer。

`schema_version = 1` 是唯一可读写布局。

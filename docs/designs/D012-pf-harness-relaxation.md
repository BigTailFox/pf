# PF Harness Resolution

- **状态：** 现行
- **最后核对：** 2026-09-09
- **适用范围：** smoke/check/search 各角色的环境准备；relaxation 仅适用于 declaration/probe
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行：** [D008](D008-pf-verification-run.md)
- **实施记录：** [P011](../archived/plans/P011-pf-harness-relaxation.md)

本文是 harness requirement relaxation、project/optional environment resolution、一次安装和 uv 诊断资格的唯一契约。操作结果的 cause/disposition 由 D005 定义。

## 1. 目标与边界

PF 搜索受管 project direct dependency 的最低可用版本。测试依赖可能通过自身约束否决 project graph；certified UNSAT 证明当前 Attempt 不满足 configured validation contract，不声称项目在其他 oracle 下不兼容。

PF 因此：

- 只搜索 project dependency vector `P`，不搜索 harness version；
- 只放宽 eligible direct harness declaration 的显式下限；
- 让 uv 独占传递依赖求解；
- 先确定 project graph；有活跃 external harness 时再确定 environment plan，安装唯一 final plan；
- 只为已资格化的矛盾生成 UNSAT attribution；其余执行事实交由 D005 分类。

## 2. 模型

```text
P       = 当前 Attempt 的受管 project direct dependency vector
G(P)    = ResolveProject(P, C_run).graph
D_H     = 当前 Cell 中活跃的 direct harness declarations
U_B     = baseline 对可变 direct harness distributions 记录的版本 ceiling
E(P)    = ResolveEnvironment(Exact(G(P)) + Relax(D_H, U_B), C_run).graph  if D_H 非空
Final(P)= E(P) if D_H 非空 else G(P)
```

`ResolutionContext` 固定本次运行的：

- 精确 uv 版本、protocol 和 qualification profile；
- SourcePlan identity、source snapshot 中 uv project-configuration identity、release cutoff 和规范输入顺序；
- 实际 InterpreterIdentity（CPython 完整 patch 与 ABI）、Cell target、marker 与 wheel-tag 环境；
- release cutoff 与共享 cache policy。

一次 `VerificationRun` 还固定单个 target package 与一个 canonical `SourcePlan`。Loader 为 target
每条 direct declaration 给出规范化 `DependencySourceRoute(development_source, search_source,
workspace_member_version)`；SourcePlan 按 Run mode 提供 effective source、已分类 dual-route facts、
冻结 member metadata 与唯一 identity。`smoke` 使用 `DEVELOPMENT`，`check`/`search` 使用 `SEARCH`。
Candidate、harness、project/environment resolution、Attempt 和 report 必须消费同一 plan/identity，resolver 不得
重新读取 source table 推导另一条领域 route。

`UvAdapter` 运行 resolver 与 installer 时必须隔离用户级 uv 配置文件及外部 source-selection
环境变量，只消费 source snapshot 内的 `pyproject.toml [tool.uv]` 与显式 RegistryAccess
凭据。进程级额外 index、find-links 或 config-file 不得绕过 `SourcePlan` 进入解析。

它不表示完整或离线的 package universe。source、凭据、metadata、artifact 或 cache 问题不能被解释为版本无解。

### 2.1 Attempt 适用规则

命令、Role、request 与 full/static 执行序列只见 [D008 §2–3](D008-pf-verification-run.md#2-attempt-request-与-role)。
本文件 §3、§6 定义 original/relaxed harness 与 request 的绑定。group 选择与自引用归一化由 D001 拥有；self-reference-only 或当前 Cell 的 external declarations 全不活跃时，均为空 external harness。

### 2.2 Workspace source 投影

`UvAdapter` 独占 `SourcePlan -> uv argv` 投影。SEARCH plan 中，由 ProjectLoader 分类为
development workspace → search registry 的 direct dependency 进入 plan 的 dual-route 查询结果；
adapter 不再读取 `declaration.managed` 或重新分类。名称按
canonical distribution name 排序、去重，并在 project/environment 两次 compile 中生成完全相同的
重复参数：

```text
--no-sources-package <dependency>
```

DEVELOPMENT 或空集合不增加参数。全局 `--no-sources` 禁止使用；adapter 同时从子进程环境移除
`UV_NO_SOURCES`、`UV_NO_SOURCES_PACKAGE` 及其它 uv index/source 注入。installation 只消费已经
校验的最终 native plan，不重新选择 source，也不改写 Proposal 或 checkout 的
`[tool.uv.sources]`。

SEARCH project plan 对每个上述受管 coordinate 必须满足：highest/lowest resolution 的 source 与
规范 registry locator 等价且 native plan 含带 locator/hash 的 artifact alternatives；`resolve-artifact = wheel | sdist | any` 同时约束 project/environment resolution，任何 registry selected artifact 与该 policy 不一致都在 Proposal/PASS 前失败。exact-vector
以 CandidateSnapshot 选定的 artifact URL/hash materialize 时，version、filename、locator 与 hash
必须全部匹配。path/workspace leakage、缺 artifact 或 source/artifact mismatch 在 Proposal 建立前
fail closed。project graph 随后必须按名称、版本、source 与可靠 selected artifact 原样嵌入
environment plan；安装图再复证最终名称/版本图。

## 3. Structured harness

ProjectLoader 先按 D001 归一化 test-group 自引用为 required Cell surface；此规则适用于所有 verification role。自引用不进入 harness 或其 source routes。其余 external requirement 在展开 `include-group` 后投影为 `HarnessRequirement`，至少保存：

- declaration identity 与 group provenance；
- 规范 distribution name、extras 和结构化 specifier clauses；
- marker、source identity 和原始文本。

只有 ProjectLoader 解析 dependency group；其他 module 不重新解析原始 group 或 source。

external harness activation 使用 `pf.markers.evaluate_contextual_marker` 完整求值：D001 五字段显式
来自 Cell，其他字段保留现行 context/default environment 行为，extra 对空字符串与 surface 逐项取 any。
此入口不收紧既有准入、不扩大 extras/dependency_groups context 支持，不伪造尚未观察的 interpreter
patch，也不宣称混合表达式可移植。harness module 补充使用语境并映射配置错误；不能因 portable
资格失败转入该路径。self-reference 则始终属于 Loader 的 portable project planning。

### 3.1 Baseline evidence

Baseline 有活跃 external harness 时先以原始 harness 得到并安装 `E(B)`；否则安装 `G(B)` 并建立同一 Cell 的 empty HarnessBaseline（IDs 与 observations 均为空）。`HarnessBaseline` 保存活跃 declaration IDs，以及按 distribution 聚合的 `observations: tuple[HarnessSatisfaction, ...]`。每个 observation 保存 name、version/source/selected_artifact、`satisfied_by: PROJECT_GRAPH | EXTERNAL_HARNESS` 与 `ceiling_eligible`。它引用 environment 中唯一同名节点；PROJECT_GRAPH 必须与 project node exact 相等，EXTERNAL_HARNESS 要求同名节点不在 project graph。此 record 不声称对 harness 独立选择了第二个版本。

来自 registry 且仍允许 resolver 选择多个版本的 distribution 进入 `U_B`；是否含可删除下限不影响 ceiling 资格。精确 `==X`、`===X` 和固定 source 不追加 ceiling，但仍保留 baseline satisfaction evidence。

### 3.2 Relaxation

每条 declaration 独立分类：

```text
fixed          非 registry source，或精确 ==X / ===X
relaxable      非 fixed，且含显式 > / >= clause
ceiling_eligible  registry 且非 fixed
```

变换规则：

| 原 specifier | Relaxed 结果 |
| --- | --- |
| `>X`、`>=X` | eligible 时删除 |
| `<X`、`<=X`、`!=X` | 保留 |
| `~=X`、`==X`、`==X.*`、`===X` | 保留 |
| URL、Git、path、workspace source | 原样固定 |

仅 declaration/probe 执行该变换：当前 `A in G(P)` 时由 Exact(G(P))[A] 独占版本，不追加 ceiling；当前 harness-only 且 `ceiling_eligible` 时追加 `<=U_B[A]`。Baseline 的 observation 即使来自 project graph，也为后续变为 harness-only 的 A 提供 upper bound；反向变为 project-owned 时 ceiling 不得约束该 project node。Baseline/declaration-capture 始终保留原始 external specifier 且无 ceiling。名称、extras、marker、source、upper bound、exclusion 和其它原始 specifier 语义均保持不变。多个同名 declaration 由 uv 求交集。

该变换由 `packaging` 支持的纯函数实现，并有版本化 policy identity；PF 不扩展 `~=` 或 wildcard equality，也不建立第二套 requirement semantics engine。

## 4. Resolve project, optionally augment, install once

每个 Attempt 先执行 project resolution；以下完整序列适用于 active external harness 非空：

```text
Create empty environment -> Inspect/qualify actual interpreter
Bind ResolutionContext and Attempt to InterpreterIdentity
ResolveProject(P, attempt strategy) -> active ProjectResolutionPlan -> G(P)
ResolveEnvironment(project + Exact(G(P)) + harness, highest)
  -> EnvironmentResolutionPlan -> E(P)
Install(EnvironmentResolutionPlan)
Inspect installed environment
```

active external harness 为空时，从 project resolution 直接 `Install(ProjectResolutionPlan)`，并复证 installed graph == project plan；不调用 original_harness/relax_harness/resolve_environment，不创建 environment request/cache entry。project-only 使用 `installing project plan` 活动与 `install-project` / `inspect-project-plan` failure stage。即使 external requirement 已由 project graph 满足，active IDs 非空仍须走完整路径，证明 satisfaction/source/exact ownership。

resolution 前先创建空 venv 并观察真实解释器，此时不安装依赖。两次 compile 显式传同一
`--python` executable 与实际完整 `--python-version`，安装继续使用该 executable。第二次始终用 uv highest strategy；project 的 `lowest-direct` strategy 不传播到 harness。PF 只安装经过校验的最终 native `pylock.toml` plan，不在安装阶段重新开放 resolution。

安装前后必须满足：

```text
G(P) ⊆exact E(P)
installed graph == EnvironmentResolutionPlan
```

安装 metadata 可能重复枚举同一 editable distribution；adapter 归并相同的 canonical name/version/
dependency-name graph observation，同名但不同版本或依赖图的观测返回 `graph-observation-invalid`。最终 installed graph
仍要求名称排序唯一，不让 metadata 重复在 EnvironmentIdentity 构造时造成未分类异常。

`⊆exact` 要求 project plan 中每个 package 的名称、版本、source 及可靠可得的 selected artifact evidence 在 environment plan 中不变。

Harness-only transitive nodes 可以出现、消失或改变版本；它们没有 baseline ceiling，不进入 candidate catalog、search coordinate 或 floor result。direct external harness 即使也属于 `G(P)`，仍保留 PROJECT_GRAPH satisfaction observation；它不成为第二个 search coordinate。

相同 resolution request 在一次运行内只求解一次；static-probe 与 oracle-probe 在坐标内可复用同一合法 `PreparedEnvironment`。

### 4.1 Native 条件节点的 active projection

`uv_lock.parse_uv_pylock(content, *, python_version, target, source_root, lock_root)` 是条件图投影唯一 owner。
`python_version` 必须是已观察的完整 patch；不能用 Cell minor 补 `.0`。先求 package marker，再校验
active 节点的 requires-python、source/artifact 与 canonical name 唯一性；未生效节点不进入 constraints、
harness satisfaction 或 installed expectation。互斥同名节点可选出一个，多个 active 同名节点失败。

支持 `python_version`、`python_full_version`、`implementation_name`、`implementation_version`、
`platform_python_implementation`、`sys_platform`、`os_name`、`platform_system`、`platform_machine`。
这些值只来自实际 CPython identity 与 exact target，不使用 PF host 的默认 marker 值补缺。
四个 target-derived platform facts 复用 `pf.markers.platform_marker_facts`；native 变量资格、actual
patch/implementation 求值与图闭合仍只由 uv_lock 拥有。此投影发生于 environment prepare 观察实际
解释器后，与 project load/report/apply 的 portable Cell profile 分离，不反向改变 Cell/generation
identity。native 错误继续是 UvLockError，经现行 D005/D012 disposition 分类。
`platform_release`、`platform_version`、`extra`、`extras`、`dependency_groups` 等不可证明的变量 fail closed，
即使位于短路分支。非空 multi-use environments/extras/dependency-groups/default-groups 不受支持。

active package 的 dependencies references 按 native 字段匹配原始节点；只命中 inactive 节点的边被移除，
未知引用或 active 歧义失败。返回图保持闭合。Native content/digest 保留 uv 输出和既有路径归一化，
不为 active filtering 改写安装输入。安装后的包名/版本 equality 与 exact project inclusion 继续严格复证。


## 5. Resolution outcomes

`UvOperations.resolve_*` 只返回：

```text
ResolutionPlan
ResolutionFailure(failure: OperationFailure)
```

`ResolutionPlan` 同时保存：

- 规范 semantic projection：package name、version、source、dependencies、direct harness satisfaction，以及可靠可得的 selected artifact；
- 经校验的 native `pylock.toml` 与 digest，供 installation 使用；
- request、context、semantic 和 native identities；
- NormalExit(0) 与成功协议要求的完整 `ProcessResult` evidence。

Raw artifact alternatives 可以作为 native provenance 保留，但不是 PF coordinate。URL、Git、path、workspace 和 editable source 使用各自的稳定 source identity。

`ResolutionFailure` 是中性事实，不接收 cause/disposition；保存当前 stage、request_digest 和
运行期 ResolutionContext envelope，以及 D005 的 ExecutionFailure 或 StructuredOperationFailure。
ProcessObservation 可作 excluded sidecar，不进入 operation fact wire。InstallFailure 同样携带
OperationFailure，但 envelope 是被安装的 plan_digest。无兼容 alias 或旧 outcome 分支。

只有固定 profile 已认证的完整 direct/transitive contradiction 才构造 UvUnsatAttribution；普通
非零、单个 substring、candidate unavailable、source/build 排除形状、未知或不完整 diagnostic
均为 Unattributed。它们在有效 R/I 请求中按 D005 的正常非零兜底，不预先命名为 Indeterminate。

### 5.1 uv qualification

当前只支持依赖中精确固定的 uv `0.12.5`，protocol 为 `uv-pip-compile-pylock-v1`，profile 为
`uv-diagnostics-0.12.5-v1`。其他版本或无法取得完整 NormalExit(0) version observation 时，在
Attempt 前以 ConfigurationError fail closed，不生成 prepare failure。

2026-08-25 Linux x86_64 的 13-case 历史 manifest 位于
[`tests/uv_qualification/matrix-manifest.json`](../../tests/uv_qualification/matrix-manifest.json)，
原始 disposition 保留不回写。现行只有 direct/transitive version contradiction 被认证为 UNSAT；
以下 diagnostic shape 排除 UNSAT，不再从这些文本取得 source/build 等 disposition authority：

- package/version、platform wheel 或 Python candidate unavailable；
- index 401/403/timeout、metadata failure、hash mismatch 或 offline cache miss；
- sdist build failure；
- 输出不完整或未知 diagnostic shape。

真实 timeout、signal、启动失败与 terminal unavailable 仍是异常执行终态；仅工具输出中的
timeout/source/build 文字不等于这些进程事实。首轮不扩展 matcher 识别能力、不注册 build 或
source 工具归因，也不删除已有 build 排除条件。成功路径 output 不完整或 native plan 不可读/
无效按 D005 的 resolution-output-incomplete / resolution-plan-invalid；非零残留产物不解析。

[2026-09-06 qualification](../../tests/execution_qualification/README.md) 保存现行真实 sdist
resolve/install 多候选搜索证据和新捕获的完整 diagnostic envelopes；测试回放两类 typed UNSAT、
全部排除形状及截断 envelope，且保留 project/environment 分支和实际 install hash-failure stage。

截至 2026-08-29，同一固定 uv 的 workspace source manifest 位于
[`tests/uv_workspace_qualification/matrix-manifest.json`](../../tests/uv_workspace_qualification/matrix-manifest.json)。
root source、member source 与两处等价声明均资格化两个受管 suppression、registry candidate、
highest 与 exact-artifact Attempt、每个 Attempt 的 two resolutions/one install、native plan、安装图、
一个未受管 in-tree path source 保留，以及 source table byte preservation。

固定 uv `0.12.5` 在同一次 compile 中一旦逐包 suppression 任一 workspace source，就不能继续解析
另一条未被 suppression 的 `{ workspace = true }` source。PF 不增加全局或额外逐包 suppression，
不把该 source 改写为 path，也不回退 development graph；这种 mixed source class 当前未资格化，
当前 uv 的普通非零在 `resolve-project` 形成 REJECTED/RESOLUTION_FAILED；不据此宣称该 source
组合逻辑无解或可重复失败。需要该组合的项目必须把本地固定依赖
声明为显式 in-tree path source，或等待新的 uv profile 完成资格化。

更换 uv 版本必须更新精确 allowlist、profile、qualification evidence、classifier tests 和
ExecutionPolicy。分类先检查请求与直接结构化事实，再归一化 terminal；合格归因优先，正常
非零兜底。不得仅因 diagnostic classifier 仍返回内部 indeterminate 标记而改变父操作 disposition。

## 6. Interface 与 identity

```text
UvOperations.resolve_project(..., interpreter: Path, request_binding: OperationRequestBinding) -> ResolutionOutcome
UvOperations.resolve_environment(..., interpreter: Path, request_binding: OperationRequestBinding) -> ResolutionOutcome
UvOperations.install_resolution(plan, request_binding: OperationRequestBinding, ...) -> InstallOutcome
EnvironmentFactory.prepare(...) -> PreparedEnvironment | PrepareFailure
EnvironmentFactory.reprepare(proposal, snapshot, source_plan)
    -> PreparedEnvironment | StaticContentUnavailable
```

`EnvironmentFactory` 在 project plan 成功且 active external IDs 非空时把当前 graph 交给 harness normalization；UvAdapter 投影 satisfaction 并复证 graph ownership。Baseline/observation、resolution request/plan 与 Attempt baseline digest 均绑定新 evidence，旧 HarnessSelection 不保留 alias。跨语义 generation/apply 隔离由 D014 的 execution / guidance / search 三类 identity 拥有。

`EnvironmentFactory.prepare` 是首次环境准备入口；active IDs 分支、harness relaxation、project/optional environment resolution、一次 installation 和 graph 复证都隐藏在其内。
调用者只传 package、Cell、resolution request、snapshot 与同一 SourcePlan；suppression names 不是 public
interface。首次 prepare 成功时按 `proposal_id` 登记私有不可变 `ReprepareRecipe`（plan /
digest / snapshot identity）；关闭 `PreparedEnvironment` 不删除 recipe。

`reprepare` 只按 recipe 重建并核对 identity / digest，安装缓存的既有 plan 后 inspect
name/version；不得重新 resolve、分配 Attempt 或调用 verifier。recipe 缺失、核对失败、安装
或 inspect 失败返回 `StaticContentUnavailable`，不调用 `record_prepare`。安装复证仍是
name/version，不枚举 `distribution.files`。

Factory 在调用前从当前 Attempt、stage 和已通过检查的 plan digests 建立 request binding；Adapter
不得创造 Attempt，只为 qualified UNSAT 回传该 binding。producer 校验运行期 context 的 uv
version/protocol/profile 与凭据逐字段相等。Factory 校验 stage/request/context 或 install plan
envelope，不一致在预期操作下记 request-invariant；非法内部对象为 InfrastructureError。
普通非零和结构化 failure 不重复保存 binding，身份由 PrepareFailure 的 Attempt/stage/plans 绑定。

Request 类型限制非法组合：

- `HighestResolution` 使用原始 harness；
- `LowestDirectResolution` 和 `ExactSelection` 必须携带同一 Cell 的 `HarnessBaseline`；分支前复证 Cell 与 active IDs，空 IDs 要求空 observations。非空 harness 使用 relaxed requirements；空 baseline 仍绑定 request/Attempt digest，不增加 optional baseline。

Identity 按取得证据的时点分开：

1. `AttemptIdentity` 在外部操作前覆盖 snapshot、Cell、resolution request、selected candidates、source plan、ExecutionPolicy、resolution context、harness policy 和 baseline identity；
2. 初始 `ResolutionContext.interpreter = None` 仅用于创建/检查解释器以前的准备失败。创建或检查失败不解析；
   观察成功后重建 context 与 Attempt，完整 interpreter 进入 context/request/cache identity，临时路径不进入；
   resolver 拒绝未观察 interpreter 的 context；
3. `EnvironmentIdentity` 在 prepare 成功后覆盖 project semantic digest、optional environment semantic digest 和最终 graph；canonical payload 显式保留 environment null，不能复制 project digest 填充。

`PreparedEnvironment.environment_plan` 可为空，调用方通过 EnvironmentIdentity/Proposal 消费 nullable digest facts，不重做 group 分支。Schema 1 required-nullable 规则由 D014 拥有。Evaluation cache 以 `EnvironmentIdentity` 为边界；FailureRecord 只保存失败发生前已经取得的 evidence，不虚构尚未产生的 plan 或 artifact。

`CandidateBuilder` 只建立受管 project direct dependencies 的有限搜索空间，并通过 SourcePlan 查询 SEARCH effective source。它不缓存或重建 source facts，不递归构造 project/harness catalog，不枚举 harness version，也不证明 resolution 无解。

PF 不从 requirement 或 `search-prereleases` 推断 uv prerelease mode，也不传 `--prerelease`、package allowlist 或对应环境变量。Highest 与 exact resolution 是否选择 prerelease 完整服从 snapshot 内 uv 项目配置；该配置的 canonical input identity 进入 `pf:resolution-context:v1`，并由apply authorization在任何source-drift waiver前复核；target dependency arrays按apply已授权的original/projected语义独立处理。Search candidate 的 prerelease inclusion 是 [D037](D037-pf-candidate-search-policy.md) 的独立 named policy，不改变 resolver ownership。

## 7. Failure projection

`EnvironmentFactory` 原样传递 OperationFailure 并附当前 Attempt/stage/已提交 plan digests；
FailurePolicy.record_prepare 使用 D005 共享规则决定 cause/disposition。qualified UNSAT 仅在 R
的 NormalExit(1) 使用 project/environment 对应 conflict cause；未知正常非零为 RESOLUTION_FAILED
或 INSTALLATION_FAILED。create/interpreter/graph 辅助执行异常仍 Indeterminate，Q 图/向量检查
只保存结构化 fact 与 null terminal，不能借用 inspect 进程。installation/inspection 不反推 UNSAT。

project plan 通过全部 artifact/source 检查后才提交 digest；environment plan 还须通过 exact
project preservation 才提交。PrepareFailure 不虚构 Proposal、安装图或未通过检查的输出 plan。

这些 operation facts 的 disposition、Baseline/Declaration/Probe 影响和用户文案分别只由 D005、D008 和 D006 定义。

`HARNESS_CONFLICT` 只证明当前精确 `G(P)`、本次实际使用的 original/relaxed direct harness、适用的 baseline ceilings 与固定 resolver policy 逻辑不兼容；baseline 的原始 harness 同样可以产生该 cause。它不证明其他 harness version、source 或 artifact 的性质。

## 8. 不变量

- PF 只搜索 project direct dependency vector。
- Baseline 使用原始 harness declarations。
- Relaxation 只删除 eligible 的显式 `>` / `>=`，ceiling 是独立规则。
- active external harness 为空时 resolve project 一次，否则 resolve 两次；install 最终 plan 一次。
- 有 harness 时 `G(P) ⊆exact E(P)`；两分支安装结果均与最终 plan 完全一致。
- Harness transitive resolution 归 uv 所有。
- certified UNSAT 细分冲突 cause；有效 R/I 的未知正常非零也拒绝当前 Attempt，不证明冲突或根因。
- PF 直接 source/artifact/environment/invariant facts 和异常终态按 D005 为 Indeterminate；后端
  自由文本诊断不取得这些 authority。
- 同一 SourcePlan identity 在一次 Attempt 内固定；两次 compile 的逐 package suppression 必须相同。
- 未资格化的 mixed managed-suppressed/unmanaged-workspace source 不得通过 local fallback 继续。

## 9. 非目标

本文不定义：

- harness 或 transitive dependency 的最低版本；
- 完整离线 package universe、本地 mirror 或 uv 私有 cache 解释；
- PF 内部的第二个 dependency resolver；
- `package version × artifact` 搜索；
- 为通过测试而枚举 harness configuration；
- 不同 harness version 的行为等价性。

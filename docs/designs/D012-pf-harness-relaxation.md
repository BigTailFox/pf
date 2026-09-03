# PF Harness Resolution

- **状态：** 现行
- **最后核对：** 2026-09-03
- **适用范围：** search probe 与 `check` Declaration Attempt 的环境准备
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行：** [D008](D008-pf-verification-run.md)
- **实施记录：** [P011](../archived/plans/P011-pf-harness-relaxation.md)

本文是 harness requirement relaxation、双阶段 resolution 和 uv 诊断认证的唯一契约。其他 Design 只定义各自消费这些结果的方式。

## 1. 目标与边界

PF 搜索受管 project direct dependency 的最低可用版本。测试依赖可能通过自身约束抬高 project graph；这只能证明原 harness 与被测 graph 冲突，不能证明 project 不兼容。

PF 因此：

- 只搜索 project dependency vector `P`，不搜索 harness version；
- 只放宽 eligible direct harness declaration 的显式下限；
- 让 uv 独占传递依赖求解；
- 先确定 project graph，再确定并安装完整 environment plan；
- 只有已认证的逻辑无解可以成为 Rejection。

## 2. 模型

```text
P       = 当前 Attempt 的受管 project direct dependency vector
G(P)    = ResolveProject(P, C_run).graph
D_H     = 当前 Cell 中活跃的 direct harness declarations
U_B     = baseline 对可变 direct harness distributions 记录的版本 ceiling
E(P)    = ResolveEnvironment(Exact(G(P)) + Relax(D_H, U_B), C_run).graph
```

`ResolutionContext` 固定本次运行的：

- 精确 uv 版本、protocol 和 qualification profile；
- SourcePlan identity、prerelease/yanked policy 和规范输入顺序；
- Python、target、marker 与 wheel-tag 环境；
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

| Verification Role | Project strategy | Harness | 运行 `test-command` |
| --- | --- | --- | --- |
| search baseline | `highest` | 原始 | 是 |
| smoke baseline | `highest` | 原始 | 是 |
| check declaration-capture | `highest` | 原始 | 否 |
| check declaration | `lowest-direct` | relaxed | 是 |
| search probe | `exact-vector` | relaxed | 由 D003 决定 |

Baseline 和 declaration-capture 不用 relaxed harness 修复用户当前声明的验证锚点。空 testing group 等价于空 harness。

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
规范 registry locator 等价且 native plan 含带 locator/hash 的 artifact alternatives；exact-vector
以 CandidateSnapshot 选定的 artifact URL/hash materialize 时，version、filename、locator 与 hash
必须全部匹配。path/workspace leakage、缺 artifact 或 source/artifact mismatch 在 Proposal 建立前
fail closed。project graph 随后必须按名称、版本、source 与可靠 selected artifact 原样嵌入
environment plan；安装图再复证最终名称/版本图。

## 3. Structured harness

Project discovery 将展开 `include-group` 后的每条 direct harness requirement 投影为 `HarnessRequirement`，至少保存：

- declaration identity 与 group provenance；
- 规范 distribution name、extras 和结构化 specifier clauses；
- marker、source identity、prerelease admission 和原始文本。

只有 project discovery 解析 dependency group；其他 module 不重新解析原始字符串或 source。

### 3.1 Baseline evidence

Baseline 先以原始 harness 得到并安装 `E(B)`。`HarnessBaseline` 保存活跃 declaration IDs，以及每个 direct harness distribution 的实际 selection。

来自 registry 且仍允许 resolver 选择多个版本的 distribution 进入 `U_B`；是否含可删除下限不影响 ceiling 资格。精确 `==X`、`===X` 和固定 source 不追加 ceiling，但仍保留 baseline selection evidence。

### 3.2 Relaxation

每条 declaration 独立分类：

```text
fixed          非 registry source，或精确 ==X / ===X
relaxable      非 fixed，且含显式 > / >= clause
ceiling_bound  registry 且非 fixed
```

变换规则：

| 原 specifier | Relaxed 结果 |
| --- | --- |
| `>X`、`>=X` | eligible 时删除 |
| `<X`、`<=X`、`!=X` | 保留 |
| `~=X`、`==X`、`==X.*`、`===X` | 保留 |
| URL、Git、path、workspace source | 原样固定 |

变换随后为每个 `ceiling_bound` distribution 追加 `<=U_B[name]`。名称、extras、marker、source、upper bound、exclusion 和既定 prerelease admission 均保持不变。多个同名 declaration 由 uv 求交集。

该变换由 `packaging` 支持的纯函数实现，并有版本化 policy identity；PF 不扩展 `~=` 或 wildcard equality，也不建立第二套 requirement semantics engine。

## 4. Resolve twice, install once

每个 Attempt 的环境准备顺序固定为：

```text
ResolveProject(P, attempt strategy) -> ProjectResolutionPlan -> G(P)
ResolveEnvironment(project + Exact(G(P)) + harness, highest)
  -> EnvironmentResolutionPlan -> E(P)
Install(EnvironmentResolutionPlan)
Inspect installed environment
```

第一次 resolution 不创建环境。第二次始终用 uv highest strategy；project 的 `lowest-direct` strategy 不传播到 harness。PF 只安装经过校验的最终 native `pylock.toml` plan，不在安装阶段重新开放 resolution。

安装前后必须满足：

```text
G(P) ⊆exact E(P)
installed graph == EnvironmentResolutionPlan
```

`⊆exact` 要求 project plan 中每个 package 的名称、版本、source 及可靠可得的 selected artifact evidence 在 environment plan 中不变。

Harness-only transitive nodes 可以出现、消失或改变版本；它们没有 baseline ceiling，不进入 candidate catalog、search coordinate 或 floor result。直接 harness distribution 即使也属于 `G(P)`，仍须保留 direct-harness selection evidence。

相同 resolution request 在一次运行内只求解一次；static、witness 和 test 复用同一 `PreparedEnvironment`。

## 5. Resolution outcomes

`UvOperations.resolve_*` 只返回：

```text
ResolutionPlan
ResolutionUnsat
ResolutionIndeterminate
```

`ResolutionPlan` 同时保存：

- 规范 semantic projection：package name、version、source、dependencies、direct harness selection，以及可靠可得的 selected artifact；
- 经校验的 native `pylock.toml` 与 digest，供 installation 使用；
- request、context、semantic 和 native identities；
- 完整 `ProcessResult` evidence。

Raw artifact alternatives 可以作为 native provenance 保留，但不是 PF coordinate。URL、Git、path、workspace 和 editable source 使用各自的稳定 source identity。

`ResolutionUnsat` 只表示 qualification profile 已认证的完整逻辑 incompatibility。普通非零退出、单个 stderr substring、输出截断或 candidate unavailable 都不足以构造它。

`ResolutionIndeterminate` 表示没有足够事实判断 satisfiability，并保留具体 cause、summary code 和 process evidence。

### 5.1 uv qualification

当前只支持依赖中精确固定的 uv `0.12.5`，protocol 为 `uv-pip-compile-pylock-v1`，profile 为 `uv-diagnostics-0.12.5-v1`。其他版本 fail closed。

截至 2026-08-25，Linux x86_64 的 13-case manifest 位于 [`tests/uv_qualification/matrix-manifest.json`](../../tests/uv_qualification/matrix-manifest.json)。只有 direct/transitive version contradiction 被认证为 UNSAT；以下情形保持 Indeterminate：

- package/version、platform wheel 或 Python candidate unavailable；
- index 401/403/timeout、metadata failure、hash mismatch 或 offline cache miss；
- sdist build failure；
- timeout、signal、启动失败、输出不完整或未知 diagnostic shape。

截至 2026-08-29，同一固定 uv 的 workspace source manifest 位于
[`tests/uv_workspace_qualification/matrix-manifest.json`](../../tests/uv_workspace_qualification/matrix-manifest.json)。
root source、member source 与两处等价声明均资格化两个受管 suppression、registry candidate、
highest 与 exact-artifact Attempt、每个 Attempt 的 two resolutions/one install、native plan、安装图、
一个未受管 in-tree path source 保留，以及 source table byte preservation。

固定 uv `0.12.5` 在同一次 compile 中一旦逐包 suppression 任一 workspace source，就不能继续解析
另一条未被 suppression 的 `{ workspace = true }` source。PF 不增加全局或额外逐包 suppression，
不把该 source 改写为 path，也不回退 development graph；这种 mixed source class 当前未资格化，
在 `resolve-project` 以 ToolFailure/Indeterminate fail closed。需要该组合的项目必须把本地固定依赖
声明为显式 in-tree path source，或等待新的 uv profile 完成资格化。

更换 uv 版本必须更新精确 allowlist、profile、qualification manifest 和 classifier tests。退出码只校验完整 outcome，不能独立决定领域结果。

## 6. Interface 与 identity

```text
UvOperations.resolve_project(...) -> ResolutionOutcome
UvOperations.resolve_environment(...) -> ResolutionOutcome
UvOperations.install_resolution(plan, ...) -> InstallOutcome
EnvironmentFactory.prepare(...) -> PreparedEnvironment | PrepareFailure
```

`EnvironmentFactory.prepare` 是上层唯一环境准备入口；harness relaxation、两次 resolution、一次 installation 和 graph 复证都隐藏在其内。
调用者只传 package、Cell、resolution request、snapshot 与同一 SourcePlan；suppression names 不是 public
interface。

Request 类型限制非法组合：

- `HighestResolution` 使用原始 harness；
- `LowestDirectResolution` 和 `ExactSelection` 必须携带同一 Cell 的 `HarnessBaseline` 并使用 relaxed harness。

Identity 按取得证据的时点分开：

1. `AttemptIdentity` 在外部操作前覆盖 snapshot、Cell、resolution request、selected candidates、source/evaluation/resolution/harness policy 和 baseline identity；
2. `EnvironmentIdentity` 在 prepare 成功后覆盖两个 semantic plan digest 和最终 graph。

`PreparedEnvironment` 与 `Proposal` 保存两个 plan digest。Evaluation cache 以 `EnvironmentIdentity` 为边界；FailureRecord 只保存失败发生前已经取得的 evidence，不虚构尚未产生的 plan 或 artifact。

`CandidateBuilder` 只建立受管 project direct dependencies 的有限搜索空间，并通过 SourcePlan 查询 SEARCH effective source。它不缓存或重建 source facts，不递归构造 project/harness catalog，不枚举 harness version，也不证明 resolution 无解。

## 7. Failure projection

`EnvironmentFactory` 只把已认证的 project `ResolutionUnsat` 投影为 `RESOLUTION_CONFLICT @ resolve-project`，把 environment `ResolutionUnsat` 投影为 `HARNESS_CONFLICT @ resolve-environment`。`ResolutionIndeterminate` 保留 adapter 给出的 source、build、tool 或 timeout cause；installation 与 graph inspection 永远不能反推这两个 conflict cause。

这些 operation facts 的 disposition、Baseline/Declaration/Probe 影响和用户文案分别只由 D005、D008 和 D006 定义。

`HARNESS_CONFLICT` 只证明当前精确 `G(P)`、完整 relaxed direct harness contract、baseline ceilings 和固定 resolver policy 逻辑不兼容；它不证明其他 harness version、source 或 artifact 的性质。

## 8. 不变量

- PF 只搜索 project direct dependency vector。
- Baseline 使用原始 harness declarations。
- Relaxation 只删除 eligible 的显式 `>` / `>=`，ceiling 是独立规则。
- 每个 Attempt resolve 两次、install 最终 plan 一次。
- `G(P) ⊆exact E(P)`，安装结果与最终 plan 完全一致。
- Harness transitive resolution 归 uv 所有。
- 只有已认证且 evidence 完整的 resolver conflict 可以拒绝 Attempt。
- source、artifact、build、tool 和未知失败保持 Indeterminate。
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

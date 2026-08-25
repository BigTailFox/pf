# PF Harness Resolution

- **状态：** 现行
- **日期：** 2026-08-23
- **适用范围：** search probe 与 `check` Declaration Attempt 的环境准备
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)
- **架构接口：** [D010](D010-pf-v1-architecture.md)
- **现行搜索契约：** [D011](D011-pf-runtime-backed-static-search.md)
- **实施计划：** [P011](../plans/P011-pf-harness-relaxation.md)

本文定义 PF 如何在不把 test harness 变成搜索坐标的前提下，放宽直接 harness requirement 的最低版本压力，并以 uv 的结构化 resolution plan 建立、安装和识别验证环境。本文同时限定 candidate catalog、resolver、安装和 failure 分类的职责，避免为负向证明提前建立完整 package catalog。

本文已经落地；D001/D002/D003/D005/D008/D010/D011 已按 §17 归并相应现行条款，代码、CLI 与报告以本文及这些所有者文档共同定义的契约为准。

## 1. 问题

PF 搜索 package 自身受管直接依赖的最低可用版本。用户配置的 `test-command` 通常还需要 `pytest`、pytest plugin 和 dependency group 中的其他测试工具。这些直接 harness requirement 及其传递依赖可能对 project graph 中的 package 施加更高下限：

```text
Project:  A >= 1.0
Harness:  B >= 2.0
B >= 2.0 requires A >= 1.5
```

若每个 probe 都原样安装 `B>=2.0`，则 `A<1.5` 无法形成测试环境。该结果只证明当前 harness floor 与 probe 冲突，不能证明 package 自身不支持 `A>=1.0`。

当前 uv 安装路径还把 resolution 和 installation 合并在一次命令中。普通非零退出既可能表示依赖约束无解，也可能来自网络、index、artifact、build 或 uv 自身。若 PF 把这些失败统一解释成 `HARNESS_CONFLICT`，就会用不完整事实错误拒绝 project vector。

PF 因而需要同时解决四件事：

1. 保留 harness 的直接 package identity、固定版本和来源契约，只解除允许放宽的最低版本压力；
2. 让 uv 继续作为唯一 dependency resolver，不在 PF 中复制传递依赖求解；
3. 把 resolution 与 installation 分开，以 resolution plan 作为安装和环境 identity 的依据；
4. 只有确定的逻辑无解才能产生 Rejection，来源、构件和工具故障保持 Indeterminate。

## 2. 决策

PF 采用以下规则：

1. search baseline、`pf smoke` 和 `check` declaration-capture 使用用户原始 harness requirements；
2. search exact probe 和 `check` 的 `lowest-direct` Declaration Attempt 使用 relaxed harness requirements；
3. PF 只搜索受管 project dependency vector `P`，不搜索 harness version；
4. 只从 eligible direct registry harness declaration 中删除显式 `>X` / `>=X` clause；其他 operator 与固定 source 原样保留；
5. 是否删除 minimum、是否追加 baseline ceiling 和 declaration 是否固定是三个独立判定；允许 resolver 自由选择版本的 direct registry harness distribution 均受 baseline ceiling 约束；
6. PF 先解析 project 得到 `G(P)`，再解析 `Exact(G(P)) + Relax(D_H,U_B)` 得到完整 environment plan，最后只安装该 environment plan；
7. 最终 environment plan 必须 exact 包含 project graph；harness 不得改变其中任何节点；
8. harness-only 间接依赖由 uv 的既定 highest 策略选择，可以随 `P` 出现、消失或改变版本；它们没有 baseline ceiling，也不是 PF 搜索坐标；
9. 现有 `CandidateBuilder` 只建立受管 project dependency 的有限搜索空间并缓存重复 source 查询，不递归建立 harness 或传递依赖的完整 catalog；
10. 同一 Verification Run 固定 uv 版本、resolution policy、source 配置、cell 环境和 release cutoff，并复用 cache；不要求 probe 严格离线；
11. 只有 adapter 从完整、受支持的 resolver outcome 建立的逻辑无解才能成为 `RESOLUTION_CONFLICT` 或 `HARNESS_CONFLICT`；
12. resolution 未完成、安装失败或输出无法可靠分类时，不从“未安装成功”推导无解；受支持 uv 版本及其分类能力由 adapter-owned qualification matrix 决定。

## 3. 模型

受管 project dependency vector 为：

```text
P = {d1: v1, d2: v2, ...}
```

令：

```text
D_H       = 当前 cell 中活跃的直接 harness requirements
U_B       = baseline 对每个 ceiling-bound direct harness distribution 得到的版本 ceiling
C_run     = 当前 Verification Run 的 ResolutionContext
Relax(D_H, U_B) = 删除 eligible 显式 minimum 并施加适用 ceiling 后的直接 harness requirements
```

`C_run` 固定 resolver 与来源策略，不声称保存一个完整、离线的 package universe。它至少覆盖：

- PF 支持的精确 uv 版本与 adapter protocol identity；
- secret-free source/index 配置 identity；
- project request 的 resolution strategy、environment 的 highest strategy、prerelease/yanked 规则和规范输入顺序；
- Python、platform、marker 和 wheel tag 环境；
- Verification Run 开始时建立的 release cutoff；
- cache policy identity。

对一个精确 `P`，PF 先得到 project resolution plan：

```text
ProjectResolutionPlan(P) = ResolveProject(P, C_run)
G(P)                     = ProjectResolutionPlan(P).graph
```

`ResolveProject` 使用当前 Attempt 的 project strategy：baseline/smoke 为 highest，Declaration Attempt 为 lowest-direct，probe 为 exact selection。`P` 只 exact pin 受管直接依赖。对 `ExactSelection`，project request 携带 `SelectedCandidate` 的 version 与 artifact evidence；它们不把 artifact 扩展成独立搜索坐标。固定和非受管直接依赖保持声明语义；project 的间接依赖仍由 uv 选择。

随后解析完整最终环境：

```text
EnvironmentResolutionPlan(P) = ResolveEnvironment(
                                 project = current source snapshot,
                                 project_graph = Exact(G(P)),
                                 harness = Relax(D_H, U_B),
                                 context = C_run,
                               )
E(P) = EnvironmentResolutionPlan(P).graph
```

`ResolveEnvironment` 始终使用 uv 的 highest strategy。此时 `Exact(G(P))` 已固定 project graph，highest 只让 uv 在原始或 relaxed harness contract 内选择满足约束的尽可能新版本；project 的 lowest-direct strategy 不得传播到 harness。resolver 的既定 highest 策略表示固定 uv 版本、策略和规范输入顺序下的选择，不承诺在多包偏序中求数学上的逐坐标全局最大值。

PF 只安装最终 environment plan：

```text
Install(EnvironmentResolutionPlan(P))
```

PF 的显式搜索空间仍然只有 `P`：

```text
min P
```

`ProjectResolutionPlan(P)` 和 `EnvironmentResolutionPlan(P)` 都是环境准备 module 隐藏的辅助结果，不进入 `CoordinateSearch` interface。

### 3.1 Resolution outcome

resolver adapter 只返回以下判别结果：

```text
ResolutionPlan
ResolutionUnsat
ResolutionIndeterminate
```

`ResolutionPlan` 是结构化成功结果。`ResolutionUnsat` 是 adapter 已确认的逻辑约束无解。`ResolutionIndeterminate` 表示没有取得足以判断 satisfiability 的事实，包括 source、tool、输出完整性和无法分类的候选可用性问题。

普通非零退出、退出码或单个 stderr substring 都不能单独构造 `ResolutionUnsat`。

`ResolutionUnsat` identity 覆盖 resolution request、`C_run`、规范化 incompatibility proof 和完整诊断 digest；它不依赖预先存在的完整 catalog digest。

### 3.2 Artifact identity

`ResolutionPlan` 中的 registry distribution entry 保存：

```text
canonical name
version
source identity
selected artifact evidence, when uv exposes it reliably
```

selected artifact evidence 包含 locator、filename、kind 和 SHA-256。PF 不从人类输出或 artifact alternatives 猜测实际选择；uv 没有可靠暴露时，该字段可以缺失。

uv structured output 中的 raw/available artifact alternatives 可以作为 diagnostics 与 plan provenance 保留，但不进入 PF coordinate model，也不要求 `CandidateBuilder` 建立 artifact-set abstraction。v1 搜索坐标仍是 package version；同版本多 artifact 搜索属于未来设计。

URL、Git、path、workspace 或 editable source 使用各自稳定的 source/revision/content identity，不伪装成 registry artifact。

## 4. Attempt 适用规则

<table>
  <thead>
    <tr>
      <th>Verification Role</th>
      <th>Project resolution</th>
      <th>Harness requirements</th>
      <th>是否运行 <code>test-command</code></th>
    </tr>
  </thead>
  <tbody>
    <tr><td>search baseline</td><td><code>highest</code></td><td>原始</td><td>是</td></tr>
    <tr><td>smoke baseline</td><td><code>highest</code></td><td>原始</td><td>是</td></tr>
    <tr><td>check declaration-capture</td><td><code>highest</code></td><td>原始</td><td>否；只捕获静态基线</td></tr>
    <tr><td>check declaration</td><td><code>lowest-direct</code></td><td>relaxed</td><td>是</td></tr>
    <tr><td>search probe</td><td><code>exact-vector</code></td><td>relaxed</td><td>由 D003/D011 决定</td></tr>
  </tbody>
</table>

Baseline 不进行 relaxation。若原始 harness 无法解析或安装，search/smoke baseline 或 check declaration-capture 按现行 Verification Role 终止；PF 不使用 relaxed harness 修复用户当前声明的验证锚点。

空 testing dependency group 等价于 `D_H = {}`。此时 relaxation 和 harness ceiling 均为空，环境准备不增加 harness 节点。

## 5. Baseline 与 ResolutionContext

### 5.1 Run context

`C_run` 在任何 baseline 或 probe resolution 前建立。同一 Verification Run 的所有 cell 和 Attempt 使用同一受支持 uv 版本与 release cutoff；cell-specific marker 和 tag 环境作为其判别字段。

release cutoff 防止运行开始后发布的新版本进入后续 resolution。配置 source 无法可靠执行 cutoff 时，adapter 必须返回 Indeterminate，不能静默忽略。PF 不宣称 cutoff 能阻止删除、yank、凭据变化、可变 metadata 或 source 故障；这些变化导致的缺失或冲突不能成为负向兼容性事实。

PF 为整个运行复用一个 uv cache，且不主动 refresh 已取得的记录。`CandidateBuilder` 自己的 query cache 与 uv cache 分别由所属 module 管理；本设计不要求 PF 写入或解释 uv 的私有 cache 格式。

严格离线不是正确性的前提。probe 可以按需访问 source；访问失败、hash 不符或得到冲突的可变内容时，adapter 返回 `SOURCE_FAILURE / INDETERMINATE`，而不是假定 package 不存在或 graph 无解。

### 5.2 Baseline harness evidence

Baseline 环境准备顺序为：

```text
resolve original project plan
  -> obtain exact G(B)
  -> resolve original environment under Exact(G(B))
  -> install final environment plan once
  -> inspect E(B)
  -> record HarnessBaseline and U_B
  -> static contract
  -> role 要求时运行 test-command
```

对每个当前 cell 中活跃、来自 registry 且允许 resolver 在多个版本中选择的 direct harness distribution，`U_B` 保存 baseline 实际选择的规范名称、版本、source identity 和可得的 selected artifact evidence。是否进入 `U_B` 与 declaration 是否包含可删除的 lower-bound clause 无关。

精确 `==X`、`===X` 和固定来源本身已经固定，不追加 ceiling；其 declaration、source 和可得的 selected artifact evidence 仍进入 `HarnessBaseline`。多个 requirement 指向同一规范名称时分别保留 declaration identity，并由 resolver 求交集。

Baseline 成功只证明原始输入在当时形成了有效环境；它不把后续的网络、source 或工具故障转换成 Rejection。

## 6. Relaxation 变换

Relaxation 对展开 `include-group` 后的每条活跃直接 harness requirement 独立执行，再把同名 requirement 的结果交给 resolver 求交集。

<table>
  <thead>
    <tr><th>原 specifier 语义</th><th>Relaxed 语义</th></tr>
  </thead>
  <tbody>
    <tr><td>无 specifier</td><td>不变</td></tr>
    <tr><td><code>&gt;X</code>、<code>&gt;=X</code></td><td>删除该下限</td></tr>
    <tr><td><code>&lt;X</code>、<code>&lt;=X</code></td><td>原样保留</td></tr>
    <tr><td><code>!=X</code>、<code>!=X.*</code></td><td>原样保留</td></tr>
    <tr><td><code>~=X</code></td><td>原样保留</td></tr>
    <tr><td><code>==X</code>、<code>==X.*</code></td><td>原样保留</td></tr>
    <tr><td><code>===X</code></td><td>原样保留</td></tr>
    <tr><td>URL、Git、path、workspace</td><td>原样保留</td></tr>
  </tbody>
</table>

PF 只删除显式 ordered lower-bound clause（`>` 和 `>=`）。compound 或 equality-like specifier 被视为不可拆分的用户意图；PF 不把 `~=` 或 `==X.*` 展开为数学上下界，也不通过字符串删除符号。

每条 direct harness declaration 分别具有三个判定：

```text
relaxable       是否删除显式 > / >= clause
ceiling-bound   是否追加 <= U_B
fixed           是否完全不可变
```

v1 使用 syntax-based classification：精确 `==X`、`===X` 与固定 source 是 fixed；其他 direct registry declaration 全部是 ceiling-bound。非 fixed declaration 含显式 `>` / `>=` clause 时同时是 relaxable。fixed 优先，不需要 PF 判断一个任意 specifier set 在数学上是否只剩单一版本。

完成 minimum 变换后，PF 对 ceiling-bound distribution 追加：

```text
<= U_B[name]
```

其他 requirement 语义全部保留：

- 规范 package name 与 declaration identity；
- requested extras；
- environment marker；
- named/default index；
- URL、Git、path、workspace source 与 integrity 信息；
- upper bound 和 exclusions。

Prerelease admission 是独立的候选策略，不能因删除一个显式下限而意外改变。PF 必须在变换前确定原 requirement 与 resolver policy 的 prerelease 资格；relaxed specifier 只删除 eligible clause，不重新解释其余 syntax。

PF 不增加、删除、替换或重命名直接 harness requirement。固定来源保持同一 source identity；PF 不替换为同名 registry package。

该变换必须由一个纯函数实现并具有版本化 identity。PEP 440 parsing 由 `packaging` 完成，但 PF 不成为第二个 requirement semantics engine。

## 7. Candidate catalog

现有 `CandidateBuilder` interface 保持聚焦于 PF 搜索：

```text
build(package, cell, baseline)
  -> tuple[CandidateSnapshot, ...] | CellFailure
```

它负责：

- 为每个受管 project direct dependency 建立有限、稳定的 coordinate search space；
- 应用 source、specifier、prerelease/yanked、artifact compatibility、baseline cap、granularity 和 series representative 规则；
- 保存 search candidate 的 locator、kind 和 SHA-256；
- 按 source identity 与规范 package name memoize 原始 source response，再按 cell 投影 compatibility，避免跨 cell 重复 source 请求。

它不负责：

- 递归抓取 project 或 harness 的传递依赖闭包；
- 枚举全部 harness version；
- 模拟 uv 的 marker、extras 或 dependency resolution；
- 为 uv 生成完整本地 index；
- 证明某个 resolution request 无解；
- 决定 `EnvironmentResolutionPlan(P)` 或 environment artifact selection。

`CandidateBuilder` query 失败是 Attempt 前的 `SOURCE_FAILURE / INDETERMINATE`。空的 search candidate set 只能按 D001/D003 的 CandidateSnapshot 规则处置；它不能替代 uv 对完整 project 或 harness request 的 resolution outcome。

`CandidateBuilder` 与 uv 可能各自读取同一直接 package 的 source metadata。本设计优先保持两个 module 的 locality，不要求 PF 适配 uv 私有 cache 来消除这一小段重复访问。各自 module 内的重复请求必须合并；完整跨 module source proxy 或本地 mirror 不属于 v1 必需工作。

## 8. Resolution 与 installation

环境准备执行两次 resolution、一次 installation：

```text
1. 以当前 Attempt 的 project strategy resolve ProjectResolutionPlan(P)
2. 从 project plan 得到 G(P)，不安装
3. 以 highest strategy resolve EnvironmentResolutionPlan(P)
     from project + Exact(G(P)) + Relax(D_H,U_B)
4. 只安装 EnvironmentResolutionPlan(P)
5. inspect E(P)，复证实际 graph 等于 environment plan 且 exact 包含 G(P)
6. 运行静态检查及本 Attempt 要求的 test-command
```

resolution success 使用 uv 产生的机器可读 plan；v1 使用 `pylock.toml`。adapter 必须先验证 plan 完整性和输入 identity，再将其投影为 PF 的 `ResolutionPlan`。成功命令的人类输出不作为 plan。

每个已验证的 `ResolutionPlan` 同时保留 normalized semantic projection 与 validated native uv plan。前者用于 PF graph/identity，只包含 name、version、source 和可靠可得的 selected artifact evidence；后者保留 `pylock.toml`、hash 与 installer 所需 artifact material。

第一次 resolution 只建立 project graph，不创建实际 Python environment，并按 Attempt 选择 highest、lowest-direct 或 exact project coordinate。第二次 resolution 把同一 project、`Exact(G(P))` 和 harness declaration 以 highest strategy 一次性求解成完整最终环境；因此不存在 project install 后再由 harness install 修改环境的中间态，也不会把 project 的 lowest-direct strategy 误用于 harness。

installation 消费 `EnvironmentResolutionPlan(P)` 表示的 validated native plan。normalized semantic identity 不需要编码 installer 所需的全部 raw artifact alternatives；这些 alternatives 可以存在于 native plan，但不因此成为 PF coordinate 或 EnvironmentIdentity 字段。安装可以从共享 cache 或 source 下载 native plan 中有完整 integrity evidence 的 artifact，但不得重新进行开放式 dependency resolution。editable project 作为 final plan 的一部分安装。

uv 是唯一 resolver。PF 不为 resolution 构造第二套 PubGrub/SAT 求解，不遍历 harness version，也不因安装或测试失败尝试另一个 `EnvironmentResolutionPlan(P)`。

相同 project 或 environment resolution input 在一次 Verification Run 内最多解析一次。static、witness、test 和重复 observation 复用同一 final plan 与 PreparedEnvironment；不因重复检查再次访问 source。

## 9. Module interface 与 identity

### 9.1 UvOperations 与 UvAdapter

现有 `UvOperations` interface 增加判别的 resolution/install 操作；`UvAdapter` 把 uv 命令、plan 格式、版本特定诊断和 cache 行为隐藏在该 module 内：

```text
resolve(request, context)
  -> ResolutionPlan | ResolutionUnsat | ResolutionIndeterminate

install(plan, environment)
  -> InstalledResolution | InstallFailure
```

PF 必须固定并验证 adapter 支持的精确 uv 版本。更换 uv 版本必须显式更新 protocol identity、诊断 fixtures 和 conformance tests；未支持版本不能继续沿用旧 parser。

当前 uv 没有稳定的结构化 failure format。v1 adapter 因此按对应 `UvDiagnosticProfile` 对完整诊断做保守、版本化解析：

- qualification profile 已认证的 requirement incompatibility 可以形成 `ResolutionUnsat`；
- package/version unavailable、artifact unavailable、`requires-python` mismatch 等 candidate-availability diagnostic 默认形成 `ResolutionIndeterminate`；只有对应 profile 已认证其完整 outcome 是无 source/tool 歧义的逻辑 UNSAT 时例外；
- source/auth/transport/index/metadata 失败映射为 `SOURCE_FAILURE`；
- crash、timeout、截断输出、未知形状或 parser 失败映射为 `TOOL_FAILURE`。

退出码只参与诊断校验，不能独立决定 domain outcome。

adapter qualification runner 在受控 package/index/artifact fixtures 下，对每个候选支持的 uv 版本运行相同命令矩阵，嗅探 resolution failure 与 abnormal failure 的完整输出。该阶段不经过 `EnvironmentFactory`、CoordinateSearch、真实项目或 PF 端到端 workflow。

截至 2026-08-25，现行 profile 只支持发行依赖精确固定的 uv `0.12.5`。Linux x86_64 qualification 对该版本运行 13 个 case；未登记版本 fail closed。该 profile 仅认证 pure/transitive version contradiction 为 UNSAT，其余 candidate/source/build case 均保持 Indeterminate。

matrix 至少覆盖：

- pure version contradiction；
- transitive version contradiction；
- package/version 不存在；
- 当前 platform 没有 wheel；
- `requires-python` 不匹配；
- index 401 / 403 与 timeout；
- metadata failure；
- hash mismatch；
- sdist build failure；
- offline cache miss。

adapter-owned fixture/feature table 为每个 case 记录：

```text
uv version
command
exit code
stdout/stderr shape and completeness
structured output availability
diagnostic signature
PF classification
classifier confidence
```

`UvDiagnosticProfile` 可以是 adapter 私有值或 fixture table，不新增通用 resolver interface。只有 matrix 已确认的 incompatibility signature 才能返回 `ResolutionUnsat`；已知 source/tool ambiguity 和任何未知 shape 都返回 `ResolutionIndeterminate`。

### 9.2 Structured harness declarations

现有 `PackagePlan.test_requirements: tuple[str, ...]` 不足以表达本设计。ProjectLoader 必须把展开后的每条直接 harness requirement 投影为结构化 `HarnessRequirement`，至少保存：

```text
declaration identity
root/package group provenance
canonical name
requested extras
structured specifier
marker
source identity
original text
```

Relaxation、baseline ceiling、resolution request 和环境准备消费该结构化记录；report builder 只消费这些阶段已经投影出的领域 identity。

其他 module 不得重新解析 dependency group 字符串或重新解释 harness source。公共报告如何持久化 harness identity 不由本文拥有；wire 规则见 [D014](D014-pf-report-schema.md)。

### 9.3 Request、plan 与 environment identity

Identity 分成两个时点，不能把 post-resolution evidence 反向塞入 Attempt identity：

1. `AttemptIdentity` 在任何外部操作前建立，覆盖 package source snapshot、cell、`ResolutionRequest`、selected project candidates、`C_run`、relaxation policy、原始 `D_H` 和可用时的 `U_B`；
2. `EnvironmentIdentity` 在 prepare 成功后建立，另行覆盖两个 normalized plan identity、最终 graph、可靠可得的 selected artifact evidence 和 `E(P)` digest。

`PreparedEnvironment` 必须持有两个 resolution plan 和 `EnvironmentIdentity`。raw plan digest 与 artifact alternatives 可以保留在 provenance/diagnostics 中，但不定义 coordinate，也不进入 normalized semantic identity。static、witness 和 full Evaluation cache key 使用 `EnvironmentIdentity`，不能只按 `proposal_id` 或 request-level Attempt identity 在不同 harness environment 之间复用。

FailureRecord 保存失败发生前已经取得的 identity 和 plan evidence；resolution 尚未成功时不得虚构 plan 或 artifact identity。

`ResolutionRequest` 保留 D010 的判别结构。relaxed 变体必须携带建立 harness request 所需的结构化 context，且类型层面不得表达 `highest + relaxed harness`、`exact probe + original harness` 或缺少 `HarnessBaseline` 的 relaxed Attempt。

`EnvironmentFactory.prepare(...)` 仍是上层唯一环境准备 method；requirement relaxation、uv resolution、installation 和 graph verification 隐藏在其 implementation 与现有 `UvOperations` seam 后面。`CandidateBuilder` 仍是独立的 search-input module；workflow 先取得 CandidateSnapshot，`CoordinateSearch` 不直接访问 source、catalog 或 uv。

## 10. Failure 分类

<table>
  <thead>
    <tr><th>阶段与场景</th><th>Cause / disposition</th><th>搜索含义</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>project resolver 返回确定的 <code>ResolutionUnsat</code></td>
      <td><code>RESOLUTION_CONFLICT / REJECTED</code></td>
      <td>拒绝完整 project Attempt</td>
    </tr>
    <tr>
      <td>project plan 成功，但 environment resolver 在 <code>Exact(G(P)) + Relax(D_H,U_B)</code> 下返回确定 incompatibility</td>
      <td><code>HARNESS_CONFLICT / REJECTED</code></td>
      <td>拒绝完整 project Attempt，并继续定界</td>
    </tr>
    <tr>
      <td>resolution 中发生 index、DNS、凭据、metadata 或 transport failure</td>
      <td><code>SOURCE_FAILURE / INDETERMINATE</code></td>
      <td>未取得 satisfiability 事实，停止 cell</td>
    </tr>
    <tr>
      <td>resolution 输出截断、未知、parser 不支持或 uv 自身失败</td>
      <td><code>TOOL_FAILURE / INDETERMINATE</code></td>
      <td>分类不可靠，停止 cell</td>
    </tr>
    <tr>
      <td>plan 成功，安装时 artifact 缺失、为空、hash 不符、损坏或下载失败</td>
      <td><code>SOURCE_FAILURE / INDETERMINATE</code></td>
      <td>plan 未能实例化，不能反推无解</td>
    </tr>
    <tr>
      <td>final plan 安装中可确定归属到 <code>G(P)</code> 节点的 build failure</td>
      <td>按 D005 的 <code>BUILD_FAILURE</code> 规则</td>
      <td>当前 project Attempt 不可构建</td>
    </tr>
    <tr>
      <td>final plan 安装中 harness-only 节点的 build failure</td>
      <td><code>BUILD_FAILURE / INDETERMINATE</code></td>
      <td>未证明其他合法 environment plan 都不可构建</td>
    </tr>
    <tr>
      <td>安装后实际 graph 不等于 environment plan</td>
      <td><code>INTERNAL_INVARIANT / INDETERMINATE</code></td>
      <td>adapter 违反 plan 契约，停止 cell</td>
    </tr>
    <tr>
      <td>test command 以配置失败码正常退出</td>
      <td><code>TEST_FAILURE / REJECTED</code></td>
      <td>用户动态契约拒绝完整 Attempt</td>
    </tr>
    <tr>
      <td>test command 启动失败、timeout、signal 或输出不完整</td>
      <td>现行 tool cause / <code>INDETERMINATE</code></td>
      <td>停止 cell</td>
    </tr>
  </tbody>
</table>

Baseline 继续使用原始 harness。其确定 resolution、build、harness 或 test failure 按 D005/D008 的 Baseline 规则终止。`check` declaration 上的 `HARNESS_CONFLICT` 是 Declaration Rejection，表示当前声明下界不能形成用户要求的验证环境；它不进入 CoordinateSearch。

`HARNESS_CONFLICT` 证明的是：在当前精确 `G(P)`、完整 relaxed direct harness contract、baseline ceilings 和 resolver policy 下存在明确的 requirement incompatibility。source、build、tool、artifact 和未分类失败都不是 `HARNESS_CONFLICT`。

## 11. 搜索流程

```text
ESTABLISH C_run
  exact uv version + source policy + cutoff + cache
            |
            v
BASELINE / DECLARATION CAPTURE
  ResolveProject -> G(B)
            |
            v
  ResolveEnvironment(Exact(G(B)) + original harness)
            |
            v
  install once, inspect, verify role contract
            |
            v
  capture HarnessBaseline U_B
            |
            v
  build project CandidateSnapshots
            |
            v
SEARCH PROBE / DECLARATION
  exact P or lowest-direct request
            |
            v
  ResolveProject(P)
            |
            v
  ProjectResolutionPlan(P) -> G(P)
            |
            v
  ResolveEnvironment(
      project + Exact(G(P)) + Relax(D_H,U_B))
            |
       +----+-----------------------+------------------+
       |                            |                  |
       v                            v                  v
  ResolutionUnsat       ResolutionIndeterminate   ResolutionPlan
  HARNESS_CONFLICT      SOURCE/TOOL failure            |
  REJECTED              INDETERMINATE                   v
                                           EnvironmentResolutionPlan(P)
                                                        |
                                                        v
                                                install once + inspect
                                                        |
                                               static/runtime contract
```

Environment resolution 不构成新的 search phase。`EnvironmentResolutionPlan(P)` 是 `EnvironmentFactory.prepare` implementation；`CoordinateSearch` 仍只观察 project vector 对应的 `ProbePass | ProbeRejection | ProbeIndeterminate`。

D011 的 static region、runtime witness 和最终直接测试规则保持不变。`HARNESS_CONFLICT` 是 prepare 阶段对完整 Attempt 的确定 resolution Rejection，不是 static observation。

## 12. Project graph 与 harness graph

PF 分开记录：

```text
ProjectGraph       G(P)   ProjectResolutionPlan(P).graph
EnvironmentGraph   E(P)   EnvironmentResolutionPlan(P).graph，并由安装后 inspect 复证
DirectHarness      EnvironmentResolutionPlan(P) 中的直接 harness distributions
```

必须满足：

```text
G(P) ⊆exact E(P)
```

`⊆exact` 表示 `G(P)` 的每个 distribution 都在 `E(P)` 中保持同一规范名称、版本和 source contract；exact project candidate 的 selected artifact evidence 在 uv 可靠暴露时一并保持。

因此：

- harness 可以增加 `G(P)` 中不存在的间接节点；
- baseline harness-only 节点可以在 probe 中消失；
- 不同 `EnvironmentResolutionPlan(P)` 的 harness-only 间接节点可以使用不同版本；
- 新出现的 harness-only 间接节点没有 baseline ceiling；
- distribution 已属于 `G(P)` 时，无论是否也被 harness 引用，都必须保持 project version；
- 直接 harness ceiling 不扩散到完整 harness 闭包。

集合差 `E(P) - G(P)` 不能完整表达 harness identity：直接 harness distribution 可能已经存在于 `G(P)`。证据必须同时保存 DirectHarness selection、两个完整 graph 和两个 resolution plan。Harness-only transitive graph 完全由 uv 决定，不冻结、不施加 ceiling，也不进入 PF floor。

## 13. 动态测试契约

PF 不区分 full test、smoke test 或其他用户测试形式。`[tool.pf].test-command` 本身就是动态兼容性契约：

```text
environment prepared + command exits 0
  -> PASS

environment prepared + configured failure exit code
  -> TEST_FAILURE
```

PF 不要求 relaxed environment 与 baseline：

- 收集相同数量的 tests；
- 加载相同版本的可放宽测试工具；
- 保持相同 harness-only 间接 graph；
- 保持测试工具内部实现一致。

PF 不对正常 test failure 猜测是 package、test code 还是 relaxed harness 行为造成。用户应提供快速、稳定并覆盖关键 dependency-facing behavior 的命令；不同测试规模不改变分类规则。

## 14. Correctness Invariants

### H1 — Project coordinates only

PF 只搜索 project direct dependency vector `P`。Harness version、transitive dependency 和 artifact alternatives 都不是搜索坐标或 floor 结果。

### H2 — Baseline declarations unchanged

Baseline 和 declaration-capture 始终使用用户原始 harness declarations。

### H3 — Explicit minimum only

Probe relaxation 只从 eligible direct registry harness declaration 删除显式 `>` / `>=` clause。其他 specifier 与固定 source 原样保留；ceiling-bound declaration 独立追加 `<=U_B`。

### H4 — Resolve twice, install once

PF 先按 Attempt 的 project strategy resolve project 得到精确 `G(P)`，再以 highest strategy resolve project + `Exact(G(P))` + relaxed harness 得到 `EnvironmentResolutionPlan(P)`，并且只安装 final plan。project 的 lowest-direct strategy 不传播到 harness；必须满足 `G(P) ⊆exact E(P)`。

### H5 — Certified conflict only

只有 adapter qualification matrix 支持、完整 resolver outcome 确认的 relaxed harness incompatibility 才能形成 `HARNESS_CONFLICT / REJECTED`。source、build、tool、artifact 和未分类失败不能形成该结论。

### H6 — Harness transitive resolution belongs to uv

Harness transitive graph 完全由 uv 解析，可以出现、消失或改变版本；它不继承 baseline ceiling，不进入 candidate catalog、PF search 或 floor。

## 15. 非目标

本文不试图：

- 求直接或间接测试依赖的最低版本；
- 对 harness-only 间接依赖建立 baseline ceiling；
- 提前构造完整 project/harness transitive catalog；
- 实现本地 package mirror 或 source proxy；
- 解释、修改或跨 module 复用 uv 私有 cache 格式；
- 在 PF 中实现第二个 dependency resolver；
- 证明不同 harness version 行为完全一致；
- 隔离两套 Python dependency namespace；
- 修改用户 dependency group；
- 修复测试或为失败做因果归因；
- 搜索一个能让失败测试通过的 harness version；
- 在 selected harness build failure 后枚举其他 configuration；
- 把 `package version × artifact` 引入 v1 search；
- 把 transitive resolver selection 写成 PF floor 结果；
- 把当前 uv 尚未提供的通用结构化 failure protocol 提前抽象成多 resolver interface。

## 16. 所有权

<table>
  <thead>
    <tr><th>规则</th><th>唯一所有者</th></tr>
  </thead>
  <tbody>
    <tr><td>用户结果承诺、test group、test command 与退出码</td><td>D001</td></tr>
    <tr><td><code>EnvironmentFactory</code>、<code>ResolutionRequest</code>、environment identity 与 cache seam</td><td>D002/D010；本文定义的 harness 语义已归并</td></tr>
    <tr><td>project coordinate search、Probe Rejection 与搜索边界</td><td>D003/D011</td></tr>
    <tr><td>cause、disposition、FailureRecord 与 diagnose 文案</td><td>D005</td></tr>
    <tr><td>Attempt Role、check/search/smoke 序列与 Journal</td><td>D008</td></tr>
    <tr><td>harness relaxation、两次 resolution/一次 installation、artifact evidence 与 <code>UvOperations</code> / <code>UvAdapter</code> 契约</td><td>本文；D001/D002/D005/D008/D010 保存各自消费面的现行条款</td></tr>
    <tr><td>project CandidateSnapshot 与 <code>CandidateBuilder</code> 搜索规则</td><td>D001/D002；本文只限制其不承担 transitive resolution</td></tr>
  </tbody>
</table>

## 17. 与现行契约的归并

本文落地时已经同步以下所有者文档；本表记录归并结果，不保留落地前行为：

<table>
  <thead>
    <tr><th>文档</th><th>归并后的现行规则</th></tr>
  </thead>
  <tbody>
    <tr><td>D001 §5.2</td><td>baseline 使用原始 harness；relaxed Attempt 使用 direct ceiling；每个 Attempt 两次 resolution、一次 final-plan installation</td></tr>
    <tr><td>D002 §7.1、§8.1–§8.2</td><td>PackagePlan 保存结构化 harness；EnvironmentFactory 拥有双 plan、精确 graph 校验与 EnvironmentIdentity</td></tr>
    <tr><td>D003 §3</td><td>CandidateSnapshot 仍只描述 project search；ResolutionContext 固定运行级 resolver 输入</td></tr>
    <tr><td>D005 §8–§9.2</td><td>只有 certified resolution conflict 可拒绝；source、build、tool 与 installation failure 保持 Indeterminate</td></tr>
    <tr><td>D008 §5.2</td><td>check declaration-capture 使用原始 harness，<code>lowest-direct</code> 使用携带 baseline 的 relaxation</td></tr>
    <tr><td>D010 §5</td><td>relaxed ResolutionRequest 强制携带 HarnessBaseline；Attempt 与 post-resolution EnvironmentIdentity 分离</td></tr>
    <tr><td>D011 §7、§16</td><td>只有 certified project/harness conflict 形成 resolution rejection；static-only observation 资格不变</td></tr>
  </tbody>
</table>

## 18. 验收标准

1. search/smoke baseline 与 check declaration-capture 使用原始展开后的 harness requirements；
2. `pytest>=8,<9,!=8.2` 在 baseline `8.4` 下变为等价于 `pytest<9,!=8.2,<=8.4` 的结构化 requirement；
3. `~=1.4.5`、`==1.4.*`、`==1.4.5`、`===vendor` 和 URL/Git/path/workspace source 在 relaxed Attempt 中逐 clause 保持原样；
4. 无 specifier、仅 upper/exclusion、`~=` 和 wildcard equality 的 direct registry distribution 虽不删除 clause，仍记录 `U_B` 并追加 ceiling；精确 equality 和固定 source 不追加；
5. 删除显式 prerelease lower bound 不改变该 declaration 已确定的 prerelease admission policy；
6. ceiling-bound direct harness distribution 不能选择高于 `U_B` 的版本；
7. PF 对每个 Attempt 先按 project strategy resolve 得到 `G(P)`，再以 highest strategy resolve 完整 `EnvironmentResolutionPlan(P)`；第一次 resolution 不创建环境，project 的 lowest-direct strategy 不传播到 harness；
8. 每个 Attempt 只安装 final environment plan 一次；安装后 inspect 复证实际 `E(P)` 等于 plan 且 `G(P) ⊆exact E(P)`；
9. 两次 resolution success 都产生经 Schema 校验的 machine-readable plan；installation 不进行 plan 外 resolution；
10. registry plan entry 保存 package identity、version、source 和 uv 可靠暴露的 selected artifact evidence；raw artifact alternatives 只作 diagnostics/provenance，不形成 PF coordinate 或强制 semantic identity；
11. baseline harness-only transitive 节点可以在 probe 消失，新节点可以出现且版本可以变化；它们不进入 project vector、candidate catalog 或 floor；
12. 同一 Verification Run 固定受支持的精确 uv 版本、adapter protocol、source policy 和 release cutoff；
13. `CandidateBuilder` 只查询受管 project direct dependencies，并按 source/package memoize 原始 response；测试证明它不会递归读取 harness/transitive package；
14. uv resolution 复用运行级 cache；相同 resolution input 只解析一次，不因 static/witness/test 重复访问 source；
15. 独立 qualification runner 对每个候选支持的 uv 版本运行相同的 resolution/abnormal failure fixture matrix，并形成 §9.1 定义的 versioned diagnostic profile；该验收不依赖真实项目或 PF 端到端 workflow；
16. matrix 已确认的完整 requirement incompatibility 产生 `HARNESS_CONFLICT / ProbeRejection`；CoordinateSearch 可以据此继续提高当前坐标；
17. 普通非零退出、stderr substring、package/version not found、wheel unavailable、`requires-python` mismatch、offline/cache miss 和未知 shape 不能形成 `HARNESS_CONFLICT`，除非对应版本 profile 明确认证为无 source/tool 歧义的 UNSAT；
18. resolution 的网络、index、凭据或 metadata 故障产生 `SOURCE_FAILURE / Indeterminate`；未知 uv 诊断产生 `TOOL_FAILURE / Indeterminate`；
19. plan 成功后的 artifact 空文件、缺失、hash mismatch、损坏或下载失败产生 `SOURCE_FAILURE / Indeterminate`；
20. harness-only build failure 产生 `BUILD_FAILURE / Indeterminate`，不触发隐含 harness version 枚举；
21. `check` lowest-direct 使用 relaxation，确定的 HARNESS_CONFLICT 成为 Declaration Rejection；normal `test-command` failure 仍是 `TEST_FAILURE`；
22. AttemptIdentity 不依赖 post-resolution evidence；EnvironmentIdentity 覆盖两个 plan、最终 graph、relaxation policy、baseline ceiling、ResolutionContext 和可靠可得的 selected artifact evidence；
23. Evaluation cache 不得跨 EnvironmentIdentity 命中；FailureRecord 不虚构尚未得到的 plan/artifact evidence；D011 的 static-only observation 仍不能单独形成 Rejection。

## 19. 决策记录

### D1：Relaxation 同时用于 search probe 与 check declaration

两者都在验证 project dependency lower bound。只修 search 会让 `pf check` 继续把可放宽的 harness floor 误报为声明下界不兼容。

### D2：只删除显式 minimum，ceiling 独立判定

PF 只识别并删除 `>` / `>=` clause，不展开 `~=`、wildcard equality 或其他结构性语义。是否追加 `U_B` 只取决于 direct registry declaration 是否允许版本选择，与是否删除 lower bound 无关。该划分尊重用户意图，并防止 PF 成为第二个 requirement semantics engine。

### D3：两次 resolution，一次 installation

第一次 resolution 只确定 project graph `G(P)`。第二次在 `Exact(G(P))` 下加入 harness 并定义完整 `EnvironmentResolutionPlan(P)`；PF 只安装该 final plan。这同时保留 project graph 契约，并删除两次安装及其中间 artifact drift 状态。

### D4：确定 requirement incompatibility 才是 HARNESS_CONFLICT

“无法安装”不是 satisfiability 证明。只有 adapter qualification matrix 能在受支持 uv 版本的完整 outcome 中排除 source、tool、artifact 和 build 歧义时，resolution 无解才可以成为 Rejection。保守地漏掉一个可拒绝 conflict 只会少做搜索剪枝；错误拒绝可行 vector 会破坏 floor 正确性。

### D5：Candidate catalog 不承担负向证明

完整 catalog 需要提前抓取大量未必使用的传递 metadata 和 artifact，并会逐步复制 uv 的候选与 marker 语义。即使建立 catalog，普通 uv 非零退出仍不能区分求解无解和工具故障。现有 `CandidateBuilder` 因此只拥有 project search space 和查询缓存。

### D6：固定运行输入并按需访问 source，不建立完整离线 universe

精确 uv 版本、source policy、cell 环境、release cutoff 和共享 cache 提供一次运行内需要的稳定性。删除、yank、凭据或网络变化无法由预取彻底消除，遇到它们时保持 Indeterminate 即可保护正确性。严格离线 mirror 不是 v1 必需工作。

### D7：Artifact identity 属于 resolution/environment evidence

现有 graph 只有 name/version，无法区分同版本的不同来源或构件。ResolutionPlan 因而保存 uv 可靠暴露的 selected artifact evidence；raw alternatives 只作诊断和 provenance。Artifact 不是 v1 搜索维度，是否需要多 artifact model 必须由后续实验另行证明。

### D8：Harness transitive graph 完全属于 uv

PF 只拥有 direct project coordinates 与 direct harness declaration relaxation。Harness-only transitive 节点不继承 baseline ceiling、不进入 catalog 或 floor，也不因 build/test failure 触发枚举；这些选择全部由 uv 完成。

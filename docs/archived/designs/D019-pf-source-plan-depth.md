# D019 — PF SourcePlan 模块深化（归档）

- **状态：** 已完成，已归档
- **日期：** 2026-09-03
- **接受日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 临时性架构优化设计；完成后归并到现行 owner 并与实施 Plan 一同归档
- **实施基线：** `b8efadc`（`docs: archive diagnostic result card design`）
- **实现提交：** `166c272`（`refactor: deepen source plan module`）
- **评审来源：** [R005](../reviews/R005-pf-module-depth-review.md) §3
- **现行产品契约：** [D001](../../designs/D001-pf.md)
- **现行实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **搜索与运行：** [D003](../../designs/D003-pf-search-algorithm.md)、[D008](../../designs/D008-pf-verification-run.md)
- **失败与证据：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **解析边界：** [D012](../../designs/D012-pf-harness-relaxation.md)
- **报告 wire：** [D014](../../designs/D014-pf-report-schema.md)
- **实施计划：** [P025](../plans/P025-pf-source-plan-depth.md)

> **临时性声明：** 本文只拥有本次 `SourcePlan` interface 迁移。它不是长期契约所有者，
> 也不在实施完成前改写现行行为。实施、验收和 owner 文档归并完成后，必须将本文与 P025
> 在同一完成变更中移入归档；归档后的本文只保留设计理由与验收历史。

R005 还记录 `WorkspaceInventory`、Verification Run、评价层 Protocol、terminal-private
result-card 与 `SearchCoordinator` 测试表面候选。它们跨越不同 seam，且部分仍是
“Worth exploring”，不属于本设计。R005 仍保持开放；SourcePlan 项已解决，其余候选若被接受，
分别进入后续 Design。

## 1. 问题与目标

当前 `SourcePlan(source_mode, routes)` 已是 report generation input，却主要只验证 route 排序与
唯一性。调用方仍分别知道如何：

- 从 `PackagePlan.source_routes` 和 mode 重建计划；
- 按 canonical dependency 查找 route 并选择 development/search source；
- 识别 workspace development → registry search 的 dual route；
- 读取 workspace member version metadata；
- 对同一 payload 计算 `source-plan` 与 `source-policy` 两种摘要。

这些知识出现在 Candidate、Harness、Environment、UvAdapter、ApplyAuthorizer 与 ReportStore；
workflow 又把裸 `source_mode` 传给多个 operation，使同一 Verification Run 可以反复重建等价
计划。增加 source 变体或调整 route 资格时，规则会跨调用方同步修改。

本设计目标是把 `SourcePlan` 深化为逐 Run 的 canonical source-facts module：

1. 一个在线 Run 只构造一个 `SourcePlan` 值，并贯穿全部 Cell 与两阶段 resolution；
2. `SourcePlan` 以小 interface 独占 effective source、dual-route facts、member metadata 与唯一
   identity；
3. 调用方只消费所需事实，不遍历 route 或重新解释 mode；
4. 保持 ProjectLoader、UvAdapter、ApplyAuthorizer 与 ReportStore 的领域权威；
5. 直接替换旧 helper、裸 mode 参数与平行摘要，不建立兼容层。

删除深化后的 `SourcePlan` 时，lookup、mode、dual-route 与 identity 规则会重新散回上述六类
调用方；这就是本次 depth 的删除测试。文件行数、helper 数量或 class 数量不是完成标准。

## 2. 决策摘要

1. `SourcePlan` 继续是 `schemas.project` 中的 strict/frozen Pydantic Schema，也是 D014
   `inputs.source_plan` 的唯一领域值；不增加 domain wrapper、第二份 wire record 或平行 identity。
2. `SourcePlan` 自身提供从 `PackagePlan` 构造、按 dependency 查询 effective source、查询
   workspace→registry facts/member version，以及计算 canonical identity 的行为。
3. `PackagePlan.source_routes` 继续是 ProjectLoader 的分类结果；除 SourcePlan 构造与 D014
   wire codec/validation 外，生产调用方不得直接遍历或解释它。
4. 合法构造只有三条：在线 smoke/check/search workflow 用 `for_package` 在每次 invocation 内
   构造一次（snapshot 前后均可，`smoke=DEVELOPMENT`，`check`/`search=SEARCH`）；apply workflow
   从当前 PackagePlan 另一次构造 SEARCH plan；ReportStore reader 只通过 D014 wire 反序列化。
   生产代码不得用 Schema 构造器从 raw routes 另造一份“应当等价”的计划。
5. `VerificationRun` 携带完整 `source_plan`，不再携带裸 `source_mode`。Runner 验证 command、
   package、plan 与 Cell 闭合，并把该对象作为参数注入每个 task operation；零参数 task closure
   被删除，task 无法捕获第二份 plan。Runner 不解释具体 route。
6. Candidate、Harness、Highest、Check、Search 与 Environment interface 用 `source_plan` 直接替换
   `source_mode`；同一变更删除基于 `(package, source_mode)` 的重建路径。本次只改这一参数，不
   合并三套评价 Protocol。
7. `SourcePlan.identity` 是排除在 JSON 之外的派生摘要。`ResolutionContext`、CandidateSnapshot、
   Attempt、resolution request/cache 与 report generation 全部消费这个值；删除独立
   `source-policy` 摘要。
8. dual-route（workspace development → registry search）仍由 ProjectLoader 按 D001 managed
   规则分类进 `source_routes`；`unmanaged-deps`/fixed workspace 两边都保持 development source，
   因而不是 dual-route。SourcePlan 只查询已分类事实；UvAdapter 不再读 `declaration.managed`，
   只把查询结果翻译成排序、去重的 `--no-sources-package` argv。
9. ApplyAuthorizer 从当前 PackagePlan 构造一次 SEARCH SourcePlan，先与 report 计划精确比较，
   再消费 member-version facts判断 intended requirement；它继续独占授权决定。
10. ReportStore 继续独占 Schema 1 codec、public locator 与 cross-ref 验证，因此可以检查原始
    wire fields；effective source 与 identity 闭合必须走 SourcePlan interface。writer 的
    generation identity 与 `inputs.source_plan` 使用同一份 Search Run plan。
11. PF 首次发布前，本设计涉及的全部版本标签固定为 `v1`：Schema 1、`pf:source-plan:v1`、
    `pf:resolution-context:v1`、`attempt-v1` / `pf:attempt:v1`、`pf:report-generation:v1` 与
    generator algorithm `v1`。内部语义直接原地替换，不因开发期迭代递增版本，也不由相同版本
    标签产生兼容承诺。

## 3. 目标 interface

`SourcePlan` 的 caller-facing interface 为：

```text
SourcePlan.for_package(package: PackagePlan, mode: DEVELOPMENT | SEARCH)
    -> SourcePlan

# D014 wire fields；Runner 用 source_mode 做 command 闭合，ReportStore 可检查原始 fields
SourcePlan.source_mode
SourcePlan.routes

# 派生行为；不得成为 Schema field / computed_field，不得进入 model_dump 或 Schema 1 JSON
SourcePlan.identity
SourcePlan.source_for(dependency) -> SourceIdentity
SourcePlan.registry_routed_workspace_dependencies()
    -> tuple[canonical dependency name, ...]
SourcePlan.workspace_member_version_for(dependency)
    -> StaticWorkspaceMemberVersion | DynamicWorkspaceMemberVersion | None
```

语义如下：

- `for_package` 只消费 ProjectLoader 已规范化、排序且唯一的 `source_routes`；它不读 TOML、文件
  系统、环境变量或 uv 配置，也不解释 `declaration.managed`。
- `source_mode` 与 `routes` 是 wire 与 Run 聚合不变量。事实查询（effective source、dual-route、
  member version）不得再 `if source_mode == "SEARCH"` 或遍历 `routes`。
- `identity` 对现行 wire 字段 `source_mode + routes` 计算规范摘要，继续使用
  `pf:source-plan:v1` domain separator 与现行 `model_dump(mode="json")` preimage；相同 wire
  事实得到相同 identity，与 `for_package` 或 wire 反序列化的构造路径无关。
- `source_for` 统一选择当前 mode 的 effective source；dependency 必须是 canonical name 且存在，
  缺失时以稳定的 `ValueError` 表示调用方/计划不变量破坏，不尝试 fallback。
- `registry_routed_workspace_dependencies` 只返回当前 SEARCH plan 中 development source 为
  workspace、effective source 为 registry 的 dependency，结果按 canonical name 排序且唯一；
  DEVELOPMENT plan 返回空元组。该集合等于 ProjectLoader 已分类的 dual-route，不是 adapter
  再过滤 `declaration.managed` 的结果。
- `workspace_member_version_for` 返回 ProjectLoader 已冻结的 metadata，不重新读取 member
  `pyproject.toml`；非 workspace dependency 返回 `None`，未知 dependency 与 `source_for` 同样失败。

ProjectLoader 只构造 `PackagePlan.source_routes`。ReportStore 可以为严格 codec、public-locator
和 cross-reference validation 检查原始 wire fields；其它生产 module 只使用查询 interface，不
把 raw routes 当作平行 source facts。

不增加 `SourcePlanner`、repository、port 或 adapter。Source facts 是 in-process pure data，现有
Schema 就是正确 seam；再包一层只会增加调用方必须学习的转换和第二种身份。

## 4. Run 级数据流与 interface 替换

```text
ProjectLoader -> PackagePlan.source_routes
                    |
online workflow -> SourcePlan.for_package(...)  # 该 invocation 内唯一构造点
                    |
               VerificationRun.source_plan
                 /       |        \
        Candidate/Harness  Environment  Search/Check/Highest
                              |
                          UvAdapter
                    |
search workflow -> PackageReportBuilder -> ReportStore
                    generation_id 与 inputs.source_plan 用同一 plan

apply workflow -> SourcePlan.for_package(..., SEARCH)  # 独立 invocation
                    |
               ApplyAuthorizer

D014 wire decode -> SourcePlan  # reader 的唯一构造路径
```

必须原地替换以下 interface：

| 当前 interface | 目标 interface |
| --- | --- |
| `VerificationRun.source_mode` | `VerificationRun.source_plan` |
| `VerificationTask.execute: Callable[[], T]` | `execute(source_plan: SourcePlan) -> T`；只由 Runner 传入 Run plan |
| Candidate/Highest/Check/Search/Environment 的 `source_mode` 参数 | 同一 `source_plan` 值 |
| Harness 的 `(package, source_mode)` source lookup | `(package, source_plan)`；只由 plan 查询 source |
| `package_source_plan(package, mode)` | `SourcePlan.for_package(package, mode)`；旧 helper 删除 |
| `package_source(package, dependency, mode)` / `effective_source(route, mode)` | `source_plan.source_for(dependency)`；旧 helper 删除 |
| `source_plan_identity(plan)` | `source_plan.identity`；旧 helper 删除 |
| `ResolutionContext.source_policy_identity` | `ResolutionContext.source_plan_identity` |
| Environment 私有 `_source_policy_identity` | 删除；直接使用 `source_plan.identity` |
| Environment `_managed_source_failure` 遍历 `package.source_routes` | 查询 plan 的 dual-route facts 与 `source_for`；Cell 内哪些 coordinate 不得泄漏仍由 Environment 决定 |
| `_request_digest` 的 `source_plan.model_dump` | `source_plan.identity`；不保留第三份 dump 摘要 |
| PackageReportBuilder 两处 `package_source_plan(package, "SEARCH")` | workflow 传入的同一份 Run `source_plan` 同时用于 generation identity 与 `inputs.source_plan` |

Runner 必须拒绝：

- `smoke` 携带非 DEVELOPMENT plan；
- `check` / `search` 携带非 SEARCH plan；
- plan routes 与 Run package 的 routes 不相等；
- task Cell 不属于 Run package，或 Cell 重复。

这些是 Run 聚合不变量，不把 source classification 移入 Runner。Runner 在完成上述验证后，以
`task.execute(request.source_plan)` 调用 operation，因而 operation 不从 workflow closure 获得 plan。
无 Cell 的既有 command 行为、snapshot 生命周期、task 的 Journal/association/deadline callbacks 与
scheduler 语义不在本设计中改变。

## 5. Ownership

| Owner | 本设计后的唯一职责 | 明确不吸收 |
| --- | --- | --- |
| ProjectLoader | 读取声明；按 D001 managed/fixed 规则分类 development/search route、registry、workspace member 与 version metadata | mode、effective lookup、uv argv、apply decision |
| SourcePlan | canonical route set、Run mode、effective lookup、已分类 dual-route facts、member metadata query、唯一 identity | TOML/I/O、`declaration.managed` 选择、candidate policy、argv、授权、wire join |
| CandidateBuilder / Harness | candidate admission 与 harness 变换；从 plan 取得 dependency source | route lookup、mode 分支、SourcePlan identity 算法 |
| EnvironmentFactory | prepare、resolution/Proposal 与资源生命周期；传递同一 plan；泄漏检查消费 plan 查询 | 重建 plan、遍历 `package.source_routes` 或计算第二个 source-policy identity |
| UvAdapter | SourcePlan dual-route facts → 排序去重 `--no-sources-package`、resolver/install/inspection policy | source classification、`declaration.managed`、Run mode 决策 |
| VerificationRunner | Run/package/plan/Cell 聚合不变量、向 task operation 注入唯一 plan 与 D008 lifecycle | route 解释、source policy、report write |
| ApplyAuthorizer | report/current plan equality、member version对 intended requirement、最终授权 | 重读 member TOML、另算 route/identity |
| ReportStore / D014 | wire codec、public locator、identity/cross-ref validation | ProjectLoader classification、apply authority、effective-source 算法 |

`SourcePlan` 返回事实，不替调用方下产品结论：registry source 是否可查询仍由 CandidateBuilder / provider
处理；哪些 Cell coordinate 不得发生 workspace leakage 仍由 Environment 决定；uv suppression 的 argv
形状仍由 UvAdapter 决定；动态 member version 是否阻止 apply 仍由 ApplyAuthorizer 决定；report
locator 是否可公开仍由 ReportStore 决定。dual-route 资格本身仍由 ProjectLoader 写入 `source_routes`。

## 6. Identity、报告与错误

### 6.1 唯一 identity

`identity` 不是可序列化字段。canonical preimage 保持现行公式，且只覆盖两个 wire fields：

```text
sha256(
  b"pf:source-plan:v1\0" + json.dumps(
    source_plan.model_dump(mode="json"),
    sort_keys=True,
    separators=(",", ":"),
  )
)
```

`model_dump` 必须只含 `source_mode` 与 `routes`。`SourcePlan` 不保存 `PrivateAttr`、
`cached_property` 或其它 per-instance mutable lookup cache；查询只建立调用期局部索引，不能让查询
顺序改变 Pydantic equality、hash、dump 或 identity。routes 中 development/search source 与
workspace member version metadata 全部参与 identity，因为它们已在 `routes` 内。不得以
effective-source 去重集合、PackagePlan digest、uv argv 或 report ref 作为第二份 SourcePlan identity。

`ResolutionContext.source_plan_identity`、CandidateSnapshot、Attempt、`_request_digest` /
resolution cache 与 report generation 必须等于同一 `SourcePlan.identity`。本次只把独立
`pf:source-policy:v1` 摘要换成该值；不改 `pf:source-plan:v1` 的 preimage。

全部受影响的版本标签保持或重置为 `v1`：

- `ResolutionContext` 字段直接由 `source_policy_identity` 替换为 `source_plan_identity`；
  `pf:resolution-context:v1` 的 canonical preimage 同步只保存新字段和值，不保留旧 key；
- `AttemptIdentity` 只保留 `attempt-v1`，`pf:attempt:v1` 直接覆盖当前目标 preimage，包含
  resolution context、harness 与 selected-candidate evidence；删除现有 `attempt-v2` 分支、prefix
  与条件式旧布局；
- Schema 继续只读写 `schema_version = 1`，report generation 继续只使用
  `pf:report-generation:v1` 与 generator algorithm `v1`。

这是未发布阶段的原地契约替换，不是兼容承诺。Reader 只按当前 `attempt-v1` preimage 重建并复算
identity；包含旧 Attempt identity 的迁移前报告因 identity 不闭合而失败。实现直接重生成
fixtures/examples 和开发期报告；不提供旧 reader、alias、dual validation、migrator 或自动升级
路径，也不得在同一 generation 中保留迁移前 Attempt evidence。

### 6.2 Schema 1

D014 的 `inputs.source_plan` wire 形状仍为 required `source_mode + routes`；本设计不因深化而增加
methods、缓存或 derived facts 到 JSON。Writer 必须把 Search workflow 已经消费的同一 SEARCH
plan 同时写入 generation identity 与 `inputs.source_plan`，不得从 PackagePlan 再建一份“应当
等价”的 plan。

Reader 仍先做严格 wire/public-locator 验证（可检查原始 `routes`），再通过 SourcePlan interface：

- 复算唯一 identity；
- 证明 CandidateSnapshot dependency 存在且 source 等于 SEARCH effective source；
- 证明 Attempt、CandidateSnapshot 与 generation 绑定同一 identity；
- 拒绝未知 dependency、非 registry managed search source 或不闭合的 ref。

Schema 1 Reader 只接受按当前定义复算成立的 `attempt-v1`；旧 `attempt-v2` 或开发期旧
`attempt-v1` 不构成可读布局。`update` / `merge` 只能消费已经通过当前 Reader 的报告，因此不能把
迁移前后 evidence 混入同一 generation。

若 identity 变化影响生成示例，D014 的 JSON Schema、complete/incomplete examples 与测试 fixture
必须在同一实施切片更新；生成物不得手工形成第二份契约。

### 6.3 错误边界

ProjectLoader 在构造 PackagePlan 时继续拒绝非 canonical、重复、缺 direct dependency route 或非法
dual route。SourcePlan 查询未知 dependency 是内部不变量错误；Candidate/uv source 访问失败仍按
D005/D012 形成现有 structured Failure，不因本次重构改变 Rejection/Indeterminate 分类。

Apply 的 report/current SourcePlan 不相等仍是 `ApplyAuthorizationError`，不可由 `--force` waiver；
动态或不满足 intended requirement 的 workspace member version 继续在任何编辑前失败。错误文案、
CLI exit code、终端通道与诊断 authority 不在本设计中变化。

## 7. 测试策略

测试以深化后的 public interface 为表面，替换旧 helper/构造细节测试，不叠加两套测试：

| 表面 | 必须覆盖的可观察语义 |
| --- | --- |
| `SourcePlan` | 缺失 dependency、DEVELOPMENT/SEARCH、local workspace、workspace→registry、static/dynamic member metadata、排序唯一；`for_package` 与 wire round-trip 得到同一 identity；查询前后 equality/hash/dump/identity 不变 |
| CandidateBuilder | provider 收到 plan 的 SEARCH effective registry source；snapshot 绑定同一 plan identity |
| Harness / Environment | 原始/relaxed requirement 使用同一 effective source；两次 resolution、Attempt 与 cache request 闭合到同一 identity；SEARCH leakage 检查不读 `package.source_routes` |
| UvAdapter | DEVELOPMENT/空集合/local/fixed/unmanaged workspace 无 suppression；SEARCH 一个/多个 dual route 在两次 compile 生成相同、排序去重的重复参数 |
| VerificationRunner / workflows | Runner 向每个 task operation 注入同一 Run plan；mode/package/Cell mismatch fail closed；workflow 不捕获第二份 plan；typed command outcome 不变 |
| ApplyAuthorizer | report/current plan equality、static/dynamic member version与不可 waiver 行为通过 public authorize/CLI seam |
| ReportStore | writer 的 generation identity 与 `inputs.source_plan` 同源；当前 `attempt-v1` 的 read round-trip、candidate/attempt/generation identity、未知 route/public locator/cross-ref 拒绝与生成物一致 |

禁止通过 monkeypatch 私有 route map、断言 helper 名称、读取 `__dict__` 或复制 identity 算法来测试。
迁移期可以用静态检查证明旧 helper、裸 `source_mode` operation 参数和第二摘要已消失；交付测试不保留
“旧 interface 必须报错”的兼容性用例。

P025 必须把下列证据映射到 §10 每条验收标准：focused SourcePlan/consumer tests、report generated
artifacts、CLI/workflow 回归、Ruff、ty、Python 3.10/3.11/3.12 顺序全量测试、coverage gate、文档
link/diff 检查。collection、单一 Python 版本或通过测试但未过 coverage gate 均不能替代完整证据。

## 8. 非目标

- 不建立 R005 的 WorkspaceInventory，也不改变 discovery/loader 的文件系统观察次数；
- 不做 R005 所述 `VerificationTask` lifecycle 深化、合并评价 Protocol 或重塑 `SearchCoordinator`
  构造面；task 只增加由 Runner 注入的 `source_plan` 参数，三套 `*Operations` 只把
  `source_mode` 换成该值；
- 不抽取 terminal result-card、修改 CLI、输出、exit code 或 Failure 语义；
- 不改变 ProjectLoader 的 source 分类、managed/fixed 规则、registry policy 或 workspace member资格；
- 不把 `declaration.managed` 选择搬进 SourcePlan 或 UvAdapter；
- 不改变 D003 搜索顺序、candidate admission、harness relaxation 或 uv artifact inspection；
- 不改变 Schema 1 的 `source_plan` JSON 形状、schema version、merge/update authority 或 apply scope；
- 不在首次发布前为本设计涉及的 Schema、context、Attempt 或 generation identity 递增版本；它们
  统一使用当前 `v1` 定义；
- 不缓存原始 TOML、SourcePlan instance 或外部工具结果；
- 不增加 DI framework、通用 repository、source service、port 或只有一个 adapter 的假想 seam；
- 不为旧 helper、裸 mode 参数、`source-policy` identity 或旧 evidence ID 提供兼容层。

## 9. 临时设计生命周期与归档

本设计只有以下生命周期：

1. **已接受、待实施：** D019 于 2026-09-03 被接受，durable P025 已建立并逐条映射 §10；
   D001/D002/D003/D005/D008/D012/D014 在完成归并前仍是现行契约，不编辑 production code。
2. **实施中：** 按 P025 记录 interface/ownership 迁移、测试、actions、decisions/deviations、结论与
   精确证据。
3. **完成、归档：** 只有实现与全部验收证据闭合后，才在同一变更中完成 owner 归并、索引整理、
   R005 回写以及 D019/P025 归档。

完成变更必须把稳定规则归并到：

| 现行 owner | 吸收内容 |
| --- | --- |
| D001 | 一个 Run 的 canonical SourcePlan 与 apply 不可 waiver identity 条件；产品行为不变 |
| D002 | SourcePlan interface/ownership、Run 级数据流、各 module 的参数与依赖方向 |
| D003 | Candidate/probe 继续绑定同一 SEARCH plan identity；算法不变 |
| D005 | 当前 `attempt-v1` identity、Failure scope 与 evidence 闭合；不保留 `attempt-v2` |
| D008 | `VerificationRun.source_plan`、command→mode 与 package/Cell 闭合 |
| D012 | effective source query、唯一 identity；suppression 集是 SourcePlan dual-route 查询的 argv 投影，D012「受管」改述为 ProjectLoader 分类结果而非 adapter 再过滤 `declaration.managed` |
| D014 | reader/writer 使用同一 plan、identity 闭包及受影响生成物 |

归档动作必须同时完成：

- 将 `docs/designs/D019-pf-source-plan-depth.md` 移到 `docs/archived/designs/`，状态改为
  “已完成，已归档”，补充完成日期、P025 与实现提交；
- 将 P025 移到 `docs/archived/plans/`，保留最终 acceptance/evidence matrix；
- 更新 `docs/README.md` 与 `docs/archived/README.md`，不让归档 Design继续充当现行 owner；
- 在 R005 中把 SourcePlan 候选标为已由 D019/P025 解决并指向归档记录；其余候选继续开放，
  因而此时不归档 R005；
- 复查所有相对链接、Design/Plan/R005 状态、owner 条款与当前实现一致。

## 10. 验收标准

1. `SourcePlan` 继续是唯一可序列化领域/wire 值（JSON 只有 `source_mode + routes`），并通过 §3
   的查询 interface 独占构造、effective lookup、workspace→registry facts、member metadata query
   与 canonical identity；`identity` 不进入 dump；不存在 wrapper、第二 record、第二 identity 或
   per-instance cache，查询不改变 equality/hash/dump/identity。
2. 每个 smoke/check/search invocation 只构造一份 SourcePlan；`VerificationRun` 携带该 plan 而非
   裸 mode，并拒绝 command/mode、package/routes、重复或越界 Cell 不匹配；Runner 把同一对象注入
   每个 `VerificationTask.execute(source_plan)`，task operation 不从 closure 捕获 plan。
3. Candidate、Harness、Environment、UvAdapter、ApplyAuthorizer 与 ReportStore 不再直接从
   `PackagePlan.source_routes + source_mode` 重做 lookup、mode 选择或 dual-route 判定；
   Environment `_managed_source_failure` 不再遍历 `package.source_routes`；
   `package_source_plan`、`package_source`、`effective_source`、`source_plan_identity` 与
   `_source_policy_identity` 旧路径在同一变更删除。
4. CandidateSnapshot、ResolutionContext、两阶段 resolution request/cache、Attempt/Proposal、report
   generation 与 reader validation 全部绑定同一 `SourcePlan.identity`；`_request_digest` 使用该
   identity 而非 `model_dump`；生产代码不存在 `pf:source-policy:v1` 或语义等价的平行摘要。
   Schema、SourcePlan、ResolutionContext、Attempt、report generation 与 generator algorithm 全部
   使用当前 `v1` 定义；`attempt-v2` 与其它本设计引入的递增版本不存在。
5. UvAdapter 仍唯一拥有 argv：SEARCH dual-route 查询结果在 project/environment compile 中产生
   相同、排序去重的 `--no-sources-package`；DEVELOPMENT、local workspace、fixed 以及非 dual-route
   的 unmanaged workspace 不被抑制，且不使用全局 `--no-sources` 或外部环境 fallback。UvAdapter
   不读取 `declaration.managed`。
6. ApplyAuthorizer 仍独占授权：精确比较 report/current SEARCH plan，通过 SourcePlan 查询 static/
   dynamic member metadata，保持 intended-requirement 与 `--force` 不可 waiver 行为；不重读 TOML。
7. ReportStore writer 把真实 Search Run plan 同时用于 generation identity 与 `inputs.source_plan`；
   reader 可检查原始 wire fields 做 codec/public-locator/cross-ref，但 effective source 与
   identity 闭合走同一 SourcePlan interface；Schema 1 shape/version和现有 authority 不变，只接受
   当前 `attempt-v1` identity。包含旧 Attempt identity 的迁移前报告 fail closed，update/merge 不混合
   迁移前后 Attempt evidence；受影响的 schema、examples、fixtures 与开发期报告同步生成并验证。
8. §7 的 SourcePlan、consumer、adapter、workflow、apply、report 公共行为矩阵全部通过；旧 shallow
   tests 被替换，不断言私有 helper/route map，不以兼容性测试保留旧 interface。
9. D001/D002/D003/D005/D008/D012/D014 稳定条款与实现一致。除 SourcePlan 构造、D014 wire
   codec/public-locator/cross-ref 外，没有 production caller 通过 raw routes、裸 mode 或另造
   plan 建立平行 source facts。静态审计与文档链接检查对此给出直接证据。
10. P025 的 acceptance/evidence matrix 逐项闭合，适用的全量质量门禁通过；D019/P025 状态、owner
    归并、R005 SourcePlan resolution、文档索引和归档位置在同一完成变更中一致。

# D028 — PF validation contract surface 归一化

- **状态：** 已实施并归档（非规范性迁移记录；实施与证据见 [P034](../plans/P034-pf-validation-contract-surfaces.md)）
- **日期：** 2026-09-05
- **性质：** 临时性产品、planning 与 harness 迁移 Design；完成后归并到现行 owner 并与实施 Plan 一同归档
- **设计核对基线：** `3f79782`（`docs: clarify the omitted test-group default`）
- **评审来源：** [R009](../reviews/R009-requests-harness-self-reference.md)
- **实验来源：** [E003](../../experiments/E003-requests-dependency-validation.md)
- **现行产品契约：** [D001](../../designs/D001-pf.md)
- **现行实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **现行搜索算法：** [D003](../../designs/D003-pf-search-algorithm.md)
- **现行 Failure：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **现行 Harness：** [D012](../../designs/D012-pf-harness-relaxation.md)
- **现行报告 wire：** [D014](../../designs/D014-pf-report-schema.md)

> **归档说明：** D028/P034 已完成实现与逐项验收，稳定规则由 D001/D002/D005/D012/D014 接管。
> 本文保存迁移时的设计与决策；下文的“当前”和实施步骤均指设计时基线，不再定义现行契约。
> requests 的新运行事实见 [E004](../../experiments/E004-requests-validation-surfaces.md)。

## 1. 评审 disposition

R009 对 E003 的主因判断成立。当前 `ProjectLoader` 把 dependency group 中的 `requests[socks]`
投影成 direct harness；`UvAdapter` 又从 environment plan 中移除当前 editable project，随后要求每个
direct harness 都对应一个带版本的剩余 package。于是 uv 已成功产生 lock，PF 仍在
`_direct_harness` 投影中得到 `resolution-plan-invalid`。这不是 requests verifier 的负向事实。

R009 的主方案也成立：当前项目的自引用 extras 表达 validation contract 要求安装的 project
surface，不是外部 harness。PF 已经安装当前 target，因此裸自引用可以消去；带 extras 的自引用必须先
改变 Cell surface，再让 project resolution 从该 surface 取得 optional dependencies。不能只在 environment
里额外安装依赖而让 Cell 继续声称 `[]`。

本设计收紧三处评审表述：

1. required-extra marker 按完整的 `(target, Python minor)` Cell 投影求值，不只按 Python。
   Dependency group 不是 project optional-dependency metadata；`extra`、`extras`、`dependency_groups` 以及
   PF 不能从 Cell 稳定给出的 marker 变量不进入 v1 自引用语义，统一 fail closed。
2. D005 已经把 certified `HARNESS_CONFLICT` 定义为 Rejection，D003 也已消费 `REJECTED` 继续搜索。
   本迁移只把“完整负向事实”明确为“当前 Attempt 不满足 configured validation contract”，不改变状态机，
   也不把 static ty regression 本身新增为 Rejection cause。
3. project graph 与 direct harness 同名时只有一个 resolved node，不产生第二次版本选择。Baseline 记录
   “该 harness requirement 由哪个 graph owner 的节点满足”的 observation；ceiling 仍用于阻止真正的
   harness-only 节点漂移，但不能覆盖当前 Attempt 的 `Exact(G(P))`。

`resolve-artifact = "any"` 与省略 `test-command` 时使用 `["pytest"]` 是独立于自引用修复的产品默认值
变更。它们共同来自 R009 的开箱评审，可以在同一迁移中实现，但分别拥有独立验收项；任一默认值的实现
不能成为 required-surface 修复的前置条件。

Python dependency-group 标准只定义 requirement/include 数据，并不让安装 group 自动安装当前项目；
requirement extras 则表示启用被引用 distribution 的 optional dependencies。因此“自引用变成 project
surface”是 PF 在“始终安装 target project”前提下的产品归一化，不是对所有 dependency-group 工具的通用
解释。PF 不建立第二套 PEP 508 parser。

## 2. 目标、边界与删除测试

本设计目标是：

1. 把有效 test group 归一化为 required project surface 与 external harness 两部分；
2. 让 required extras 成为 Cell identity 中可见的 mandatory base，并与现行 extra policy/custom
   surfaces 正确合成；
3. 保持 project graph 的版本所有权，让 external harness 只能接受或否决 exact project graph；
4. 用一个 graph-node observation 替代同名 project/harness 的虚假双重 selection；
5. 保持 certified harness UNSAT 为 Rejection、测量或投影失败为 Indeterminate；
6. 将省略的 artifact policy 改为 `any`，省略的 test command 改为 `["pytest"]`；
7. PF 尚未发布，直接替换 v1 interface、实现、文档和测试，不保留旧默认或兼容分支。

删除 required-surface 归一化后，自引用识别、extra qualification、marker 求值、surface union、harness
排除与 source-route 排除会重新散到 `ProjectLoader`、`harness.py`、`EnvironmentFactory` 和 `UvAdapter`。
因此这些规则应继续隐藏在现有 planning interface 后，而不是增加一个只有单一调用方的 public module。

本设计不新增 `ValidationContractService`、`FirstPartyHarnessReference`、environment-only extra 或 resolver
adapter。`ProjectLoader.load(...) -> ProjectPlan` 保持外部 seam；调用方只看最终 `PackagePlan.cells`、
external `harness_requirements` 与既有 source routes。

## 3. Validation contract 模型

对一个选中 target 定义：

```text
E                 = target 静态声明的全部 optional-dependency extra names
N                 = E 中声明 dependency array 非空的 extra names
H_raw             = 展开 root/member include-group 后的有效 test group requirements
S                  = H_raw 中 canonical distribution name 等于 target name 的自引用
H_external         = H_raw - S
R(target, python)  = 当前 target/Python 投影下，S 中活跃 requirement 请求 extras 的 union
Q(R)               = extra policy 在 N - R 上产生的 explored surfaces
X                  = 已资格化的 custom extra-surfaces
Cells              = { R union q | q in Q(R) } union { R union x | x in X }
```

`R` 可随 target triple 与 Python minor 变化。每个最终 surface 排序、去重；同一 target/Python 下相同
surface 只形成一个 Cell。`Cell.extra_surface` 始终保存 effective surface，而不是只保存用户主动探索的
部分。现行 `cell_id`、Attempt、CandidateSnapshot、report TargetCell 与 apply coverage 因而自然绑定真实
验证对象，不增加第二个 hidden surface 字段。

PF 的结果是固定 configured validation contract `C` 下取得的验证向量 `F_C`。`C` 至少绑定 source
snapshot/plan、target、Python、effective extra surface、candidate/artifact policy、normalized external
harness、ty、test command 与 environment policy。`F_C` 仍是 D001 定义的 coordinate-wise verified
minimum，不升级为全局笛卡尔积最小值，也不是脱离 oracle 的 intrinsic compatibility floor。

## 4. 自引用识别与资格

### 4.1 单次解析与分类

`ProjectLoader` 继续独占 test group 的 include expansion 与 PEP 508 parsing。每条 expanded requirement
保留现行 root/package、pyproject、group path、item path 与 original text provenance，然后按 canonical
distribution name 与 target canonical name 比较：

```text
same name      -> project-surface requirement
different name -> external HarnessRequirement
```

分类发生在 Cell 展开和 source-route 注册之前。自引用不进入 `PackagePlan.harness_requirements`，也不为
target name 注册 registry/path/Git harness source route；当前 editable target 已由 project input 唯一拥有。
external requirement 继续形成现行 structured harness declaration 与 source route。

自引用 provenance 只在 `ProjectLoader` 内用于资格错误与测试，不成为新的跨模块 record。其稳定 identity
已经由 source snapshot、最终 Cell 与 external harness declarations 覆盖；把只供一个 owner 消费的
`RequiredExtraRequirement` 暴露到 `PackagePlan` 会扩大 interface，却不给调用方增加能力。

### 4.2 Extra name

自引用 requested extras 按 Python packaging extra-name 规则规范比较，再映射回 target
`project.optional-dependencies` 拥有的唯一声明名。两个声明 key 若规范化后相同，或 requested extra 找不到
唯一声明，project qualification 失败。PF 不等待 uv 才发现未知 extra。

多个活跃自引用取 requested extras 的 union；它们不生成多套 oracle。裸自引用没有 requested extras 时
不增加 `R`，但仍从 external harness 中消去，因为 PF 已经安装当前 target。

### 4.3 Marker

自引用 marker 只允许 PF 的 Cell 能完整投影的变量：

```text
python_version
sys_platform
platform_machine
```

值来自 planned Python minor 与 exact uv target triple。`python_full_version` 在解释器选择前未知；host 的
`os_name`、release/version 等不能代表远端 target；`extra` 在 dependency-group requirement 的这个解释
位置没有稳定的 containing context。使用任一不支持变量都在 planning 时失败，不读取 host 默认值猜测。

对每个 `(target, python)` pair 先求活跃自引用和 `R`，再展开 surfaces。裸且无增量的 marker 自引用仍可
消去。external harness marker 保持 D012 现行语义；本文不借机重写全部 harness marker contract。

### 4.4 Version 与 source

自引用不能静默丢弃 specifier：

- 无 specifier 时，静态或 dynamic target version 都合法；
- 有 specifier 时，每个活跃 self-reference 必须由 inventory 中该 target 的静态
  `project.version` 满足；不满足是 validation contract configuration error；
- target 使用 dynamic version 时，带 specifier 的 self-reference 在 v1 fail closed。PF 不为这个少见
  形状增加 build-metadata probe，也不把常量 qualification 延迟成每个 Attempt 的 resolver 结果；
- direct URL/Git/path 形式的自引用 fail closed。它表达替换 target source，而不是为当前 editable target
  选择 surface，超出本设计。

上述错误必须在 source snapshot、Attempt 以及 resolution/install/verifier 进程建立前作为配置错误返回，
因此不是 `HARNESS_CONFLICT`、Rejection 或 Indeterminate。省略 `pythons` 时，planning 可以先通过现行
Python discovery 运行 `uv python list`，取得 planned Python minors，再求 marker 活跃性和完成资格检查。
这一允许项只用于 Python discovery，不授权 target build-metadata probe 或提前创建验证环境；discovery
自身失败仍按现行基础设施错误处理，不伪装成 self-reference 配置错误。

requests 的 `requests[socks]` 没有 specifier，故 dynamic version 不妨碍本次修复。

## 5. Required surface 与 extra policy

对每个 `(target, python)` 求得 `R` 后，只让 policy 探索 `selectable = N - R`。
用户在实施中补充：自动展开跳过声明 dependency array 为空的 extra group（例如 `security = []`）。
这里按声明是否非空判定，不因某个 Cell 的 dependency marker 全不活跃而把 group 当空组。
显式 custom surface 与自引用 required extras 仍可包含已声明的空组，不删除显式要求：

```text
none -> { empty }
each -> { empty } union { {e} | e in selectable }
all  -> { empty } union { {e} | e in selectable } union { selectable }
```

每个 policy surface 与 `R` 取 union；每个 custom surface 先按现行规则验证为 `E` 的子集，再与 `R` 取
union。最终按 `(surface length, names)` 排序、去重。`all` 中空集、singleton 或与 custom 重叠不会制造
重复 Cell。

例：`E = N = {A, B, C}`、`R = {A}`：

| policy | effective surfaces |
| --- | --- |
| `none` | `[A]` |
| `each` | `[A]`, `[A,B]`, `[A,C]` |
| `all` | `[A]`, `[A,B]`, `[A,C]`, `[A,B,C]` |

`none` 的目标语义变为“不主动探索 validation contract 要求之外的 extra”，不是“禁用所有 extra”。
`active_declaration_ids` 必须在 effective Cell 建立后重算，因此 required extra 引入的 optional dependency
由 project graph、candidate/search、floor projection 与 apply 共同看到。

requests 中：

```text
E          = {security, socks, use_chardet_on_py3}
N          = {socks, use_chardet_on_py3}
R          = {socks}
selectable = {use_chardet_on_py3}
each       = [socks], [socks+use_chardet_on_py3]
```

在 E003 的 5 个 Python minor、一个 host target 下应形成 10 个 Cell；`PySocks` 在全部 Cell 的 project
graph 中，`requests` 不再是 direct harness distribution。

## 6. External harness 与 project graph overlap

### 6.1 唯一 graph owner

自引用移除后，D012 的 harness 只表示 `H_external`。自引用到 required surface 的 planning 归一化适用于
所有 verification role；external harness 的 lower-bound relaxation 和 baseline ceiling 只适用于
declaration/probe。D012 §2.1 的角色规则保持：

| Verification role | Project strategy | External harness |
| --- | --- | --- |
| search baseline | `highest` | 原始 specifier，无 ceiling |
| smoke baseline | `highest` | 原始 specifier，无 ceiling |
| check declaration-capture | `highest` | 原始 specifier，无 ceiling |
| check declaration | `lowest-direct` | 删除 eligible 下限，并按当前 graph owner 应用 ceiling |
| search probe | `exact-vector` | 删除 eligible 下限，并按当前 graph owner 应用 ceiling |

每个 Attempt 仍先解析 project graph，再按上述角色选择 external harness：

```text
ResolveProject(P) -> G(P)
H(P) = Original(H_external)                             # baseline / declaration-capture
     | Relax(H_external, baseline, G(P))                # declaration / probe
ResolveEnvironment(Exact(G(P)) + H(P)) -> E(P)
```

`Original` 保留活跃 external declarations 的完整 specifier，不通过删除下限修复 baseline 或
declaration-capture。两者仍产生 satisfaction observation，供后续 declaration/probe 使用；各角色是否运行
configured verifier 继续遵循 D012 §2.1。

`G(P) subset-exact E(P)` 保持硬约束。只要 distribution `A` 已在 `G(P)`，environment 必须保留同一
version/source/artifact；harness requirement 可以接受或否决该节点，不能产生另一个 A，也不能升级它。

declaration/probe 中，eligible direct external harness requirement 仍只删除显式 `>` / `>=` 下限。
upper/exclusion、`~=`、equality、URL/Git/path/workspace source 保持；transitive harness metadata 完整交给
uv，不 rewrite。project optional dependency 是发布契约，永不按 harness relaxation 处理。

### 6.2 Satisfaction observation

将运行时 `HarnessSelection` 直接替换为按 distribution 聚合的 satisfaction observation：

```text
HarnessSatisfaction
  name
  version / source / selected_artifact
  satisfied_by: PROJECT_GRAPH | EXTERNAL_HARNESS
  ceiling_eligible
```

它必须引用 environment plan 中唯一同名 resolved package。`PROJECT_GRAPH` 还必须与 project plan 的同名
node exact 相等；`EXTERNAL_HARNESS` 要求同名 node 不在 project plan。该 record 只观察“direct external
harness declarations 如何被最终 graph 满足”，不声称 resolver 为 harness 独立选择了第二个版本。

Baseline 对每个非 fixed direct external harness distribution 记录 observed version，作为后续真正
harness-owned 节点的 ceiling 来源。仅在当前 declaration/probe 的 eligible 非 fixed declaration 上应用：

```text
A in G(P)     -> Exact(G(P))[A] 独占版本；不追加 harness ceiling
A not in G(P) -> relaxed direct harness 追加 <= baseline observed version
```

因此 baseline 的 A 即使由 project graph 满足，也只是一个可复证的 observed upper bound；它只在后续
Attempt 的 A 变成 harness-only 时防止 harness 升级。反过来，当前 Attempt 的 A 一旦由 project graph
拥有，baseline ceiling 不得把 exact project node 变成 harness 选择。fixed declaration 不建立 ceiling。

`HarnessBaseline`、resolution request/plan digest 与 Attempt baseline digest 直接吸收新的 observation；
旧 `HarnessSelection` interface 不保留 alias。所有 policy/identity 名称继续固定为 v1，Schema 1 不升版。

### 6.3 Outcome

规范化后的 environment request 若由 D012 uv qualification profile 证明 UNSAT，仍形成
`HARNESS_CONFLICT @ resolve-environment` 与 `REJECTED`。D003 可以把它作为 predecessor 或负向边界并
继续搜索。报告必须保留 harness cause，不能改写成 verifier/runtime incompatibility。

uv 非零、candidate/source/artifact/build/tool 问题、成功 lock 的非法投影、timeout 或未知 diagnostic
仍为 Indeterminate。E003 当前的 `resolution-plan-invalid` 不能追溯改写为 Rejection；修复后通过正确
投影重新取得的运行事实才有新 authority。

## 7. 开箱默认值

### 7.1 Artifact

省略 `resolve-artifact` 时使用 `any`：

```text
wheel -> 只接受 wheel
sdist -> 只接受 sdist
any   -> wheel 或 sdist；同版本两者皆有时仍按现行规则优先冻结 wheel candidate
```

显式 `wheel` / `sdist` 语义不变。默认值继续统一作用于 smoke/check/search 的 project/environment
resolution 与 Candidate artifact，不为 requests 增加特例。build failure 与 artifact/source/tool
failure 的 qualification 不因默认变宽而改变。

### 7.2 Test command 与 group

省略 `test-command` 时 effective command 为 `("pytest",)`；显式 command 仍整体替换该值，保持无 shell
argv、非空且不能以 `uv run` 开头。`TestConfig.command` 不再用 `None` 表达正常 effective config，
verification workflow 删除“缺少 test-command”的准入分支。

`test-group` 已有且继续使用省略默认 `"test"`。该 group 必须在 root/target 至少一处存在，且可以为空；
缺 group 仍在 source snapshot、Attempt 和 resolution/install/verifier 前配置失败；planning 阶段允许的
Python discovery 同 §4.4。默认 pytest 没有自动扫描、fallback、tox/nox 探测或“未安装则换命令”行为；
若 effective group 没有提供可运行 pytest，后续事实按现行 verifier/process contract 处理。

两个默认值都以 effective value 进入 evaluation policy identity；只有 effective value 改变时，默认变化
本身才改变该 identity。已显式配置 `any` 和 `pytest` 的项目不能依靠默认值变化隔离旧证据；本迁移的
语义隔离由 §8.1 的 normalization policy facts 保证。不增加旧默认推断、dual identity 或 reader migration。

## 8. Interface、identity 与 wire

`ProjectLoader.load(...) -> ProjectPlan` 与 `EnvironmentFactory.prepare(...)` 外部 interface 不增加参数。
内部职责调整为：

| Owner | 目标职责 |
| --- | --- |
| `ConfigLoader` | 物化 `any` 与 `("pytest",)` 省略默认；保持两层 merge |
| `ProjectLoader` | 一次解析 test group；分离 self-reference/external harness；资格 self-reference；按 target/Python 生成 effective Cells |
| `harness.py` | 只转换 external harness；基于 current project graph 决定 exact ownership 与 ceiling 应用 |
| `EnvironmentFactory` | 在 project plan 成功后把 `G(P)` 交给 harness normalization；保存 baseline satisfaction |
| `UvAdapter` | 渲染 external harness、解析唯一 environment graph，并投影 satisfaction observation |
| `policy.py` | 把 §8.1 的固定 normalization policy facts 纳入 evaluation policy identity |
| `ReportStore` / `ApplyAuthorizer` | 继续通过 generation compatibility / current evaluation policy 检查隔离不同语义的证据 |

`PackagePlan.harness_requirements` 的 interface 收窄为“external direct harness requirements”。
`PackagePlan.cells[].extra_surface` 继续是唯一 surface interface。ProjectLoader 不暴露 raw group、inventory、
required-extra wrapper 或 provisional surfaces。

### 8.1 Evaluation policy 与跨语义证据隔离

resolution/Attempt digest 只标识各次执行证据。`HarnessBaseline` 或 satisfaction record 改变，不保证
report generation 改变，也不能代替 apply 对当前契约的准入检查。因此本迁移必须扩充现有
`evaluation_policy_identity` 的 canonical preimage，新增固定字段 `validation_contract_policy`：

```json
{
  "self_reference": "required-effective-cell-surface",
  "extra_exploration": "nonempty-declared-groups-only",
  "baseline_harness": "original-external-declarations",
  "probe_harness": "remove-eligible-direct-lower-bounds",
  "project_overlap": "exact-project-node-without-harness-ceiling",
  "external_ceiling": "baseline-observed-version-for-current-harness-only-node"
}
```

这些值是当前实现固定采用的语义事实，不是用户配置项；其中 `baseline_harness` 同时适用于
declaration-capture，`probe_harness` 同时适用于 check declaration。`policy.py` 统一物化这些事实，各调用方
不得自行组装另一份 policy。既有 preimage 字段保留，前缀仍为 `pf:policy:v1`；不引入版本递增、旧值推断
或兼容分支。具体的 required extras、external declarations 和 baseline observations 仍分别由
Cell/source snapshot、Attempt 和 resolution evidence 绑定，不把每次运行的 observation 放进 evaluation policy。

新增固定字段保证：即使源码、Cell、显式 `any`/`pytest` 配置及 generator 都不变，本迁移前后的
evaluation policy identity 仍不同。该 identity 已进入 generation 和 Attempt，并由 apply 与当前
effective config 所生成的 policy 比较，故：

- 新旧语义 report 的 generation 不同，`merge` / 同 generation `update` 拒绝混合不同 Cell 的证据；
- `update_path` 对不同 generation 继续整体替换，不保留旧 generation 的其他 Cell；
- `apply` 的 policy mismatch 在任何 source-drift waiver 前失败，`--force` 不能绕过；
- 离线 reader 可以按现行规则读取内部自洽的报告，但读取成功不授予当前语义下的 merge/apply authority。

D014 必须接收上述 evaluation-policy preimage 与 generation/apply 隔离规则；wire 字段形状不变不等于
identity 契约不变。D001/D012 继续拥有各条 normalization 行为，D014 只拥有它们如何绑定报告证据。

### 8.2 Wire

Schema 1 的 Cell、report、FailureRecord、projection 与 apply wire 形状不变：

- required extras 通过现有 `Cell.extra_surface` 与 active declaration refs 可见；
- source snapshot 已绑定 dependency-group 原文；
- self-reference 不新增 report declaration kind；
- satisfaction/baseline 是 resolution runtime evidence，不加入公共 report wire；
- §8.1 的固定 policy facts 负责跨语义 generation/apply 隔离；Cell、harness declaration IDs 和 baseline
  digest 另行绑定各自的输入或执行证据，不代替该隔离；
- PF pre-release，所有 version/prefix 保持 v1，不接受旧内部 record alias。

## 9. 测试与实施证据要求

测试只走稳定 public seam 与语义字段，不锁定 private helper 名称或整份易变 CLI snapshot。

1. `ProjectLoader.load` 正向证明 root/member/include-group 中 canonical self-reference 被移出 external
   harness，裸自引用消去，多个 extras 取 union，source routes 不含 target 的假 harness route。
2. 参数化 public planning tests 覆盖 `none/each/all`、custom union、required-only、空/singleton
   selectable、surface 去重、active optional declarations 与 Cell identity。
3. target/Python marker tests 覆盖 Linux/macOS/Windows projection、active/inactive 分支；unsupported marker
   variable、未知/歧义 extra、URL/Git 自引用、specifier mismatch 与 dynamic-version specifier 全部 fail
   closed。省略 `pythons` 的 public workflow tests 允许 Python discovery 先运行，并证明资格失败后没有
   source snapshot、Attempt 或 resolution/install/verifier；discovery 自身失败保留基础设施错误分类。
4. config tests 证明 root/member 省略与显式 override：artifact 为 `any`、command 为 `("pytest",)`，且
   policy identity 随显式不同值改变。
5. harness pure tests 覆盖 lower-bound deletion、fixed clauses、project-owned/harness-owned satisfaction、
   baseline/current ownership互换、ceiling 只约束 current harness-only node。参数化覆盖 §6.1 五种角色：
   baseline/declaration-capture 原始下限保留且无 ceiling，declaration/probe 才应用 relaxation 和 ceiling；
   各角色均使用包含 required extras 的 effective Cell。
6. EnvironmentFactory/UvAdapter contract tests 证明一个 overlap distribution 只有一个 resolved node；
   project-owned node exact 保留；external transitive conflict 不 rewrite；certified environment UNSAT 保持
   `HARNESS_CONFLICT / REJECTED`，非法投影保持 Indeterminate。通过 public environment seam 复证各角色
   实际提交的原始/relaxed harness 与 §6.1 一致，不只检查纯函数结果。
7. report/apply tests 证明 effective surface 使用现有 Schema 1 字段 round-trip，旧报告不会因 hidden
   required surface 被误授权，wire 不出现 satisfaction 或 raw required-extra provenance。另以没有自引用、
   显式配置 `any`/`pytest`、存在 external overlap 的项目证明 §8.1 policy facts 进入 identity：源码、
   Cell、effective config 和 generator 不变时，不同 normalization policy 仍不能跨 Cell merge/update，
   `apply` 与 `apply --force` 均拒绝 policy mismatch；`update_path` 整体替换 generation。测试覆盖现行
   policy-mismatch 安全契约，不保留旧内部 record alias 或历史 Schema 分支。
8. 完整 regression 包含 Ruff、ty、Python 3.10/3.11/3.12 suites、build、链接与 `git diff --check`；若
   PyPI/build 因环境受限失败，必须与代码 regression 分开记录。
9. 按 E003 §7 保存本次 requests `smoke` / `check` / `search` 的真实结果。空组策略补充前已启动的
   实验保留其 15 个 effective Cells 与旧 policy，不追认为最终 10 Cells 的完整实验。用户明确要求
   修复后不重复完整 search；以 public planning tests 与 live Loader 复核最终 10 Cells，另记录
   `requests[socks]` 不在 direct harness、是否越过 resolve-environment，以及随后真实 verifier/floor
   outcome。新的结果追加为新实验事实，不回写 E003 的历史结论。

## 10. 验收标准

1. 当前 target 的 canonical self-reference 在 group expansion 后、Cell/harness/source-route 建立前被识别；
   它不进入 external harness。
2. requested extras 唯一匹配 target 静态 optional-dependency keys；多个活跃 self-reference 取 union；
   未知或规范化歧义 extra 在 planning fail closed。
3. self-reference marker 只使用 `python_version/sys_platform/platform_machine`，按 planned target/Python
   求值；其他变量不读取 host 值猜测。
4. 无 specifier 的 dynamic-version target 可用；带 specifier 只接受满足条件的静态 target version；
   mismatch、dynamic、URL/Git/path source 在 source snapshot、Attempt 和 resolution/install/verifier
   前配置失败；省略 `pythons` 时允许先完成 Python discovery，其自身失败保留基础设施错误分类。
5. required extras 与 `none/each/all/custom` 按 §5 合成；自动展开跳过空组，显式 custom/required
   空组保留，非空但 marker 不活跃的 group 不当空组；最终 `Cell.extra_surface` 等于实际安装 surface，
   且 active declarations 从它重算。
6. requests 默认 `each` 的 planned matrix 是 5 Python × 2 surfaces = 10 Cells，自动跳过空 `security`，
   全部包含 `socks`，
   `requests` 不再形成 direct harness selection 或 source route。
7. external harness overlap 不改变 `G(P)` 的 version/source/artifact；environment 中每个 distribution 只有
   一个 resolved node，satisfaction observation 能区分 project-owned 与 harness-owned。
8. baseline/declaration-capture 保留原始 external harness specifier 且不追加 ceiling；declaration/probe
   才删除 eligible direct external lower bounds，并对 eligible current harness-only node 追加 baseline
   observed ceiling；current project-owned node 不追加 ceiling；fixed 与 transitive metadata 不 rewrite。
   所有角色均先完成自引用到 effective Cell surface 的归一化。
9. certified normalized environment UNSAT 继续形成 `HARNESS_CONFLICT / REJECTED` 并被 D003 当负向边界；
   投影、工具、source、artifact、build 与 timeout 失败保持 Indeterminate。
10. D003 搜索顺序、coordinate、promotion、monotonicity、floor/predecessor authority 与 D004 static/witness
    规则无行为变化；static ty regression 不成为新的直接 Rejection cause。
11. 省略 `resolve-artifact` 得到 `any`；显式 `wheel/sdist/any` 保持；artifact policy 仍统一作用于全部
    verification-producing commands。
12. 省略 `test-command` 得到 `["pytest"]`；显式 command 整体替换；默认 `test-group = "test"` 与 group
    existence admission 不变；不存在自动 command discovery。
13. §8.1 的固定 normalization policy facts 进入 evaluation-policy preimage；required surface、defaults 与
    harness observation 分别进入相应 Cell/policy/resolution/Attempt identity。没有自引用且显式配置
    `any`/`pytest` 时也不能跨语义 merge/update/apply；`--force` 不绕过 policy mismatch，`update_path`
    对不同 generation 整体替换。公共 Schema 仍为 Schema 1，所有版本号保持 v1。
14. public planning、harness、environment、config、report/apply tests 覆盖 §9 矩阵，不以 private helper 或
    full terminal snapshot 代替契约测试。
15. 完成时把稳定规则归并到 D001/D002/D005/D012/D014 与 README；D003 只核对不复制规则；归档
    R009，并在同一完成变更中归档 D028/P034。E003 保持历史实验原文。

## 11. Owner 归并与生命周期

Design 接受后先建立 P034，不直接实施。P034 至少按以下顺序切片：

1. public planning tests 与 self-reference qualification/Cell generation；
2. external harness 角色适用范围、satisfaction/ceiling interface 与 evaluation-policy preimage；
3. config defaults 与 workflow admission；
4. environment/uv integration、Failure regression 与 report/apply closure；
5. README/owner documents、requests dogfood、完整验证与逐项验收审计。

完成时的唯一稳定 owner：

- D001：configured validation result、required-base surface、extra policy 合成、两个默认值；
- D002：ProjectLoader/PackagePlan ownership、Python discovery 与验证进程时序、external harness planning、
  无新 public module/seam；
- D005：configured-contract Rejection wording与既有 cause/disposition边界；
- D012：self-reference 排除、external harness normalization 的角色适用范围、satisfaction、overlap、
  ceiling 与 UNSAT 资格；
- D003：只保留现行 `REJECTED` 消费，不增加重复规则；
- D014：扩充 evaluation-policy preimage 中的固定 normalization policy facts，闭合 generation 与
  merge/update/apply 的跨语义隔离；Schema 1 未扩形，不接收 satisfaction/required provenance。

实现、测试、owner 归并与验收全部完成后，R009 移入 `docs/archived/reviews/`，D028/P034 分别移入
`docs/archived/designs/` 与 `docs/archived/plans/`。本次已完成同步归档；逐项验收与最终验证证据由 P034 保存。

## 12. 非目标

- intrinsic compatibility floor、全局依赖笛卡尔积最小值或 harness version floor；
- 重写 external harness 的传递 metadata，或搜索 harness/transitive/artifact coordinate；
- 支持 dynamic target version 的 self-reference specifier build probe；
- 支持 URL/Git/path 自引用切换当前 target source；
- 在 environment 中偷偷启用不进入 Cell identity 的 extra；
- 为 required extras 新增 public module、配置项、CLI flag、report field 或 Schema 2；
- 自动发现 pytest/tox/nox、为缺失 pytest fallback，或改变 configured verifier authority；
- 改变 D003 coordinate search、D004 ty/witness、D008 scheduling、D013 observer 或 apply projection；
- 回写 E003，把历史 Indeterminate 追认为 Rejection 或 floor。

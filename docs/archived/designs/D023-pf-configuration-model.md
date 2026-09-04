# D023 — PF 配置模型收敛

- **状态：** 已完成、已归档
- **日期：** 2026-09-03
- **最后修订：** 2026-09-04
- **性质：** 临时性产品与跨契约迁移 Design；完成后归并到现行 owner 并与实施 Plan 一同归档
- **设计核对基线：** `d6b8a40`（`docs: record resolved R006 CLI findings`）
- **评审来源：** [R006](../../reviews/R006-pf-cli-system-review.md) §2.1、2026-09-03 配置讨论与
  2026-09-04 D023 Review
- **现行产品契约：** [D001](../../designs/D001-pf.md)
- **现行实现结构：** [D002](../../designs/D002-pf-implementation.md)
- **现行搜索算法：** [D003](../../designs/D003-pf-search-algorithm.md)
- **现行 static/runtime 评价：** [D004](../../designs/D004-pf-ty-enhancement.md)
- **现行 CLI：** [D006](../../designs/D006-pf-cli-enhancement.md)
- **现行运行语义：** [D008](../../designs/D008-pf-verification-run.md)
- **现行解析与环境：** [D012](../../designs/D012-pf-harness-relaxation.md)
- **现行报告 wire：** [D014](../../designs/D014-pf-report-schema.md)
- **实施计划：** [P029](../plans/P029-pf-configuration-model.md)

> **临时性声明：** 本文只拥有 PF 配置 interface 的目标迁移，不描述当前实现，也不在接受前授权修改
> 生产代码。接受后必须先建立 P029，把每条验收标准映射到有序切片、owner 归并、测试和证据槽；实现、
> 验收与 owner 文档归并完成后，D023/P029 必须在同一变更中归档。

## 1. 问题、目标与删除测试

现行 `[tool.pf]` 已经是扁平表，但字段名称没有形成稳定的用户词汇：它混合单复数、完整词与缩写，
并把只影响 Candidate search 的 `distribution`、会全局覆盖 uv 的 `allow-prereleases` 与 test/ty 配置并列。
`python`、`platform` 又容易被理解为重复定义项目 metadata，而不是 PF 的目标 Cell 选择。若公开 TOML
机械映射成多层 namespace，用户还要为每个常用字段切换 table，书写成本会高于这些短配置本身。

现行三层配置合并允许 workspace root 通过 `[tool.pf.package.<name>]` 集中覆盖 member，但 package 自己已经
拥有 `pyproject.toml` 和 `[tool.pf]`。集中 patch 重复表达 package-local ownership，还让配置值可能远离其
消费 package。root 同时是 workspace 默认配置 owner 和 installable target 时，集中 patch 又会为 root
制造第二个持久化局部层。列表/AoT 替换、CLI 省略值与显式 `auto` 的区别也没有形成一个完整
interface。R006 已证明 `[tool.pf].jobs` 会被解析却没有调度消费者。

本设计目标是：

1. 以一个扁平、带职责前缀的稳定 TOML interface 替换现行字段，同时让内部 schema 按消费者职责分组；
2. 删除集中 package patch，明确 root default、package-local 与显式 CLI override 的唯一优先级；
3. 将 Python/platform 命名为 target 选择，并禁止 target Python 扩大 `project.requires-python`；
4. 让 extra policy 与自定义 surface 组合，而不是互斥；
5. 以 `[[tool.pf.dep]]` 表达逐 dependency 的 search space、采样 step 与 prerelease 候选范围；
6. 让 artifact policy 同时作用于 smoke/check/search 的 registry resolution；
7. 删除 PF 的 prerelease resolution policy，完整委托 source snapshot 中的 uv 项目配置；
8. 把 Cell、ty 与 test 并发上限分开，并保证每个配置都有真实消费者；
9. PF 尚未发布，直接替换 schema、CLI、实现、文档和测试，不保留兼容别名或迁移 reader。

删除目标 `EffectiveConfig` interface 后，两层持久配置与 CLI override、target 收窄、extra 组合、逐 dependency
search policy、artifact/prerelease ownership、阶段并发与 identity 分类会重新散回 ProjectLoader、
CandidateBuilder、EnvironmentFactory、UvAdapter、evaluators、workflow 和 CLI；因此该 interface 具有实际
depth。公开 TOML 的扁平形状不要求内部 schema 同样扁平；`ConfigLoader` 隐藏两者之间的映射，本文不引入
config repository、provider registry 或第二套 domain config wrapper。

## 2. 决策摘要

1. `pyproject.toml` 是持久配置；CLI 是单次 invocation override。CLI 不写回配置，也不改变 project
   metadata；CLI 没有对应选项时，持久配置是唯一来源。
2. 选中 target 后按 root `[tool.pf]` → target member `[tool.pf]` → 显式 CLI 的顺序覆盖。CLI 省略不是
   一个值；它继承 effective persistent config。target 是 root 时只消费 root `[tool.pf]` 一次。
3. 完全删除 `[tool.pf.package.<name>]` interface；root 和 member 中出现 `tool.pf.package` 都是配置错误。
   package-specific 配置只能写入该 package 自己的 `pyproject.toml`；root 无独立 local override 是明确接受的
   interface tradeoff。
4. `[tool.pf]` scalar 替换；list 与 `dep` array-of-tables 整体替换而不拼接。省略 field 才表示继承。
   member 显式设置 `dep`（包括 `dep = []`）时整体替换 root `dep`，不按 name 合并；只有最终 entries 按最终
   dependency selection 做语义资格校验。unknown key、canonical duplicate 或类型错误全部 fail closed。
5. 所有常用持久配置直接位于 `[tool.pf]`。`pythons`、`platforms`、`managed-deps`、`unmanaged-deps`
   使用领域名；其余字段以 `extra-`、`search-`、`resolve-`、`ty-`、`test-` 前缀表达 owner。
6. 唯一子结构是逐依赖 `[[tool.pf.dep]]`；其 override 字段也完整使用 `search-` 前缀，不建立另一套短名称。
7. PF 不定义 `prerelease = explicit | if-necessary | ...`，不把 project/harness 的显式 prerelease
   提升为全局 `allow`。`search-prereleases` 只决定 CandidateSnapshot 是否枚举 prerelease，
   不是 resolver policy。
8. `resolve-artifact` 是 smoke/check/search 共享的 registry artifact admission policy；不再由 search
   独占。固定 path/workspace/Git/URL source 不因该字段改写。
9. `max-cells` 限制并发 Cell operation；`ty-jobs` 与 `test-jobs` 分别限制 invocation 内并发运行的 ty
   和 configured verifier 进程。后两者不是传给 ty、pytest 或任意 test command 的 argv；三个 stage timeout
   同等进入 evaluation policy identity，纯 scheduling limits 不进入。
10. `smoke`、`check`、`search` 与 `minimize` 都在 snapshot/process 前要求 test command/group，且不因当前
    宿主无匹配 Cell 而跳过；只读或 apply/merge commands 不作该直接准入检查。
11. PF 未发布；被替换的旧字段与目标字段不能并存，也不存在 alias、warning period、dual read/write 或自动改写。

## 3. 目标 TOML interface

以下示例覆盖完整形状；省略默认值见 §4：

```toml
[tool.pf]
pythons = ["3.11", "3.12"]
platforms = ["x86_64-unknown-linux-gnu"]
managed-deps = ["numpy", "pandas"]
# unmanaged-deps = ["torch"]     # 与 managed-deps 二选一
extra-policy = "each"
extra-surfaces = [["cpu", "cuda"]]
max-cells = "auto"
search-space = "all"
search-step = "minor"
search-prereleases = false
resolve-artifact = "wheel"
resolve-timeout = "10m"
ty-args = []
ty-timeout = "10m"
ty-jobs = "auto"
test-group = "test"
test-command = ["pytest", "-q"]
test-cwd = "package"
test-timeout = "30m"
test-jobs = "auto"

[[tool.pf.dep]]
name = "numpy"
search-space = ">=1,<3"
search-step = "patch"
search-prereleases = true
```

`managed-deps` 与 `unmanaged-deps` 仍互斥并保持 D001 的选择语义。某一较高优先级 persistent layer
设置其中一个时，它替换较低层的整个 dependency-selection variant，并清除另一字段；同一 layer 同时设置
两者是配置错误。`managed-deps = []` 继续表示不移动任何依赖。

## 4. 省略默认值

```toml
[tool.pf]
extra-policy = "each"
extra-surfaces = []
max-cells = "auto"
search-space = "all"
search-step = "minor"
search-prereleases = false
resolve-artifact = "wheel"
resolve-timeout = "10m"
ty-args = []
ty-timeout = "10m"
ty-jobs = "auto"
test-group = "test"
test-cwd = "package"
test-timeout = "30m"
test-jobs = "auto"
```

`pythons`、`platforms`、`managed-deps`、`unmanaged-deps` 与 `test-command` 没有静态值：

- 省略 `pythons` 时，按 target 的 `project.requires-python` 与 PF 支持且本机可用的稳定 CPython
  minors 推断；
- 省略 `platforms` 时，使用当前宿主的精确 uv target triple；
- 省略 dependency-selection variant 时，管理全部可搜索直接依赖；
- `smoke`、`check`、`search` 与 `minimize` 缺少 `test-command` 时配置失败。

上述四个 verification-producing commands 在 project/config load 后、构造 SourceSnapshot 或启动任何进程前
执行该准入检查；即使当前宿主没有匹配的 Cell，也不能省略 `test-command`。`explain`、`apply`、`diagnose`
与 `merge` 不直接运行 configured verifier，因此不以存在 `test-command` 作为命令准入条件。

所有 duration 接受正整数加 `s | m | h`，或字符串 `none`；`none` 表示不安装该阶段 timeout。
`max-cells`、`ty-jobs` 与 `test-jobs` 接受 `auto` 或正整数。`max-cells = "auto"` 按 invocation 开始时的逻辑 CPU
数解析且至少为 1；`ty-jobs = "auto"` 与 `test-jobs = "auto"` 继承解析后的 `max-cells`。阶段有效并发为阶段上限与当前活跃
Cell 数的较小值。

## 5. Target、extra 与依赖选择

### 5.1 Target Python 与 platform

`pythons` 是非空、排序唯一的 CPython minor 列表，如 `["3.10", "3.11"]`。它只选择 PF Cell，
不建立 `tool.pf.python`，也不修改或替代 `project.requires-python`。显式列表中的每个 minor 必须满足 target
的 `project.requires-python`；任何超出项使整个配置失败。没有 `requires-python` 时，项目范围视为 PF 支持的
全部稳定 CPython minors，显式值仍只是从中缩小。

`platforms` 是非空、排序唯一的完整 uv target triple 列表。Python packaging metadata 没有与之
等价的项目字段，因此它只声明 PF 要建立的 platform Cell；省略时仍为宿主 triple。PF 每个进程只执行
与宿主精确匹配的 target，跨宿主证据继续由 merge 组合。

### 5.2 Extra surface

`extra-policy` 为 `none | each | all`，默认 `each`：

```text
none -> [[]]
each -> [[], 每个单独 extra]
all  -> [[], 每个单独 extra, 全部 extras]
```

`extra-surfaces` 是自定义 extra-name 组合列表。最终 surfaces 是 policy 展开与自定义列表的规范化并集；
自定义列表不再要求包含 `[]` 或每个单 extra，也不替换 policy 结果。每个 surface 内名称去重排序，整体按
长度和名称排序去重；同一 surface 内的重复名称、custom surfaces 之间的重复项以及 custom 与 policy 展开
结果的重叠项都静默去重，因为重叠不改变目标 Cell 集合。未知 extra 仍是配置错误。空列表合法并只保留
policy 结果。

### 5.3 Managed dependency

`managed-deps` / `unmanaged-deps` 保持现行 top-level 名称、canonical distribution-name 归一化与 D001
fixed/searchable 资格。二者是 target dependency ownership，而不是 search 算法参数，因此不放入
search policy。

## 6. Search 配置

`search-space` 的全局值为 `all | current-major | current-minor`；`search-step` 为
`major | minor | patch`。全局设置对每个 managed dependency 生效。

`step` 表达候选版本的采样步长，比 `granularity` 更贴近实际行为：`major` 按 epoch + major 分组，
`minor` 按 epoch + major.minor 分组，`patch` 按 epoch + major.minor.patch 分组；每组只保留最新的合格
精确版本。因此它不表示 uv resolution 方式，也不表示每次 Proposal 相对 baseline 增减一个版本号。
`level` 容易被理解为约束层级，`resolution` 又与依赖解析冲突，均不采用。

每条 `[[tool.pf.dep]]` 必须有唯一 canonical `name`，并可以省略任一 override field：

```toml
[[tool.pf.dep]]
name = "numpy"
search-space = ">=1,<3"      # 或 all/current-major/current-minor
search-step = "patch"
search-prereleases = true
```

省略字段继承最终合并后的同名 `[tool.pf]` 全局字段。`search-space` 的显式形式是非空 PEP 440 specifier，
不包含 distribution name、extras、marker、URL 或 source。每个 dep entry 必须匹配 target 的 managed
searchable direct dependency；unknown、unmanaged、fixed 或 duplicate name 都是配置错误。

整个 `dep` array-of-tables 是一个按层整体替换的值，不按 `name` 跨层合并。member 完全省略 `dep` 时继承
root 整表；member 声明任意 `[[tool.pf.dep]]` 时，root 的全部 entries 都不进入该 target 的 effective
config，即使双方名称不同。需要保留全局 `search-*` 而清空 root 逐依赖 overrides 时，member 可以在
`[tool.pf]` 中写 `dep = []`；它是 empty AoT 的唯一形式，也整体替换 root。root `dep` 因而只是“member
未显式设置整表时的 inherited whole-table fallback”，不是逐 dependency 的共享默认。只适用于一个
package 的 entry 必须写在该 member 自己的 `pyproject.toml`。

每个 dependency/Cell 的最终候选集合为：

```text
registry candidates
∩ target declaration 的 upper/exclusion clauses
∩ effective search-space
∩ versions <= highest baseline
∩ effective resolve-artifact
∩ effective search-prereleases
− yanked versions
```

project declaration 的既有 `>` / `>=` 下界继续不限制向下搜索。`current-major + major` 非法；
`current-minor` 只允许 `patch`；约束按每个 dependency 的最终 effective space/step 校验。

`search-prereleases = false` 从 CandidateSnapshot 排除 prerelease；`true` 允许其进入候选采样。它不修改
uv 配置，也不保证 prerelease 能形成 Proposal。highest baseline 本身可以由 uv 解析为 prerelease；若最终
候选过滤为空，按现行 `NoApplicableFloor` 处理。step 仍从每个系列选择最新合格精确版本作为代表，
不改变 apply 写入的精确版本。

## 7. Resolution 与 prerelease ownership

### 7.1 Artifact

`resolve-artifact` 为 `wheel | sdist | any`，默认 `wheel`：

- `wheel`：registry candidate、project resolution 与 environment resolution 只接受目标 Cell 可用 wheel；
- `sdist`：只接受 sdist，并保留现行 build/indeterminate 资格；
- `any`：两者均可；同一 direct candidate version 同时存在两类时，CandidateBuilder 优先冻结 wheel，
  transitive artifact 的实际选择由 uv resolution plan 记录。

该策略统一用于 smoke、check declaration-capture/declaration、search baseline/probe，以及两阶段 resolution；
不能出现 search 用 wheel 得出 floor、check 却在同一 effective config 下用 sdist 的情况。固定 path、
workspace、Git 或带完整性 URL source 保持项目声明，不被转换成另一 artifact class。

### 7.2 Prerelease

PF 不再拥有 resolver prerelease strategy：

- 不存在 resolver prerelease 配置或 `allow-prereleases`；`search-prereleases` 只过滤 PF 搜索候选；
- UvAdapter 不传 `--prerelease` / `--prerelease-package`；
- project 或 harness 中出现显式 prerelease 不能触发 PF 的全局 `--prerelease allow`；
- PF 隔离用户级 uv 配置文件以及 `UV_PRERELEASE` 等进程环境覆盖；
- uv 只从 materialized source snapshot 的项目配置和精确 requirement 取得策略；root/member 的 uv 配置
  发现与优先级由 uv 自身定义，ConfigLoader 不解析、合并或复制它。

search 的 exact-vector prerelease 若被 uv 项目策略拒绝，就是正常 resolution rejection/indeterminate，
不能由 PF override。check 继续让 uv 从原 declaration 以 `lowest-direct` 形成精确 project plan；只有 search
`ExactSelection` 临时把 managed vector 写成等号。highest baseline 解析到 prerelease 不建立特殊错误。

ResolutionContext 必须绑定 exact uv version 与本次 source snapshot 中 uv project-configuration input 的
identity，但不保存 PF 推断的 `explicit | allow`。uv 项目配置变化必须开始新的 resolution/report generation，
且 apply 不得把 prerelease policy drift 当作普通可 waiver remainder drift。

## 8. Ty、test 与并发

### 8.1 Ty

`ty-args` 是用户追加参数；PF-owned interpreter、Python version/platform、output format 与 config safety
选项仍不可覆盖，冲突在启动进程前失败。`ty-timeout` 是每个 ty process 的 timeout。

`ty-jobs` 是 invocation-scoped ty execution pool 上限。一个 Cell 的一次 static stage 仍只启动一个 ty
process；该值只限制多个活跃 Cell 同时进入 ty 的数量，不作为 ty argv 或环境变量传递。

### 8.2 Test

`test-group` 是一个 dependency-group name，默认 `"test"`。它必须在 workspace root 或 target member
至少一处存在；两处都存在同名 group 时继续组合两处声明。group 自身可以为空，include-group 展开、
原始/relaxed harness 与 resolver 资格继续由 D012 定义。`smoke`、`check`、`search` 与 `minimize` 对
`test-group` 采用与 `test-command` 相同的进程启动前准入时机。本文不为尚无消费需求的多 group 列表扩大
interface。

`test-command` 是不经 shell 的非空 argv，不能以 `uv run` 开头。`test-cwd` 为 `package | root`，默认
`package`；`test-timeout` 是每次 configured verifier process 的 timeout。

`test-jobs` 是 invocation-scoped configured verifier execution pool 上限。一个 Proposal 仍只运行一条完整
`test-command`；PF 不拆分 nodeids、不自动添加 pytest-xdist 参数，也不把 jobs 传给任意命令。该 pool 只让
不同活跃 Cell/Proposal 的 test processes 在全局上限内并发。

### 8.3 Cell 与阶段调度

`max-cells` 替换现行 `jobs`，只限制 Scheduler 同时启动的 Cell operation。ty/test pools 位于 evaluator
process seam，独立于 Cell Scheduler；等待阶段 permit 的 Cell 仍占一个活跃 Cell slot。resolution、install、
witness 和其它 process 不借用 ty/test pool，也不由这两个字段隐式限制。

`max-cells`、`ty-jobs`、`test-jobs` 与 `search --max-duration` 只影响资源和调度，不进入 compatibility、
candidate、evaluation 或 report policy identity。它们进入 invocation-local request/Run limits 与运行日志，
但不进入 package-floor wire。`resolve-timeout`、`ty-timeout` 与 `test-timeout` 都会改变可取得的 evidence，
三者同等进入 evaluation policy identity 和各自的 process request；它们都不进入 CandidateSnapshot
selection policy。`resolve-timeout` 也不进入 D012 `ResolutionContext`，后者继续只描述 resolver 语义环境，
而一次 Attempt 已通过 evaluation policy identity 绑定执行预算。

## 9. 持久层与 CLI 覆盖

### 9.1 Persistent layers

对于 member target `demo`：

```text
root pyproject [tool.pf]
  < demo/pyproject.toml [tool.pf]
```

对于 installable root target：

```text
root pyproject [tool.pf]    # 同时是 workspace default 与 root local；只应用一次
```

root `[tool.pf]` 同时是 workspace members 的默认配置和 root package 自身的局部配置，但只作为一个 layer
应用。root package 若需要不同于 workspace defaults 的持久配置，必须把公共默认值下沉到各 member，或接受
root 与 defaults 相同；不建立 root-local 或 centralized package namespace。这是本设计为删除第二个
package-local owner 而明确接受的 deliberate tradeoff，不是待补的 namespace：PF 尚未发布，且这种 root
同时需要一套 workspace defaults 和另一套 root-local 配置的情况预计极少，不为它增加常驻 interface。

任何 `[tool.pf.package]` / `[tool.pf.package.<name>]` 都是 unknown config。每个 member 的局部配置必须位于
该 member 自己的 `pyproject.toml [tool.pf]`；这让配置 ownership 与 package metadata/依赖声明共址。
`packages`、`exclude-packages` 与 package `path` 也继续非法，workspace discovery 和 `--package` selector
独占 package 集合与路径。

### 9.2 List replacement

以下值在较高层出现时整体替换较低层，不拼接：

- `pythons`、`platforms`；
- `managed-deps` / `unmanaged-deps` variant；
- `extra-surfaces`；
- `dep` array-of-tables；
- `ty-args`；
- `test-command`。

`dep` 的唯一处理顺序是：

1. ConfigLoader 分别校验 root/member raw layer 的 table/AoT 类型、允许字段及 layer 内 canonical duplicate；
2. 按 root → member 合并所有字段；member 显式设置 `dep` 时整体替换 root `dep`，其中 `dep = []` 表示清空，
   完全省略才继承 root 整表；
3. 最终保留的 `dep` entries 从最终全局 `search-*` 字段继承省略值，并验证 fully inherited space/step 组合；
4. ProjectLoader 按最终 `managed-deps` / `unmanaged-deps` variant 与最终 declarations/source 分类，验证最终
   entries 都对应 managed searchable direct dependency。

已被 member 整表替换的 root entries 不参加该 member target 的 dependency 资格校验；它们的 raw 类型、
字段和 canonical duplicate 仍在第 1 步 fail closed。`extra-policy` 与最终有效的 `extra-surfaces` 也只在两个
persistent layers 合并完成后展开并取规范并集。

### 9.3 CLI overrides

验证命令的目标 CLI 为：

```text
pf smoke   [--package PACKAGE] [--max-cells auto|N] [--ty-jobs auto|N] [--test-jobs auto|N]
pf check   [--package PACKAGE] [--max-cells auto|N] [--ty-jobs auto|N] [--test-jobs auto|N]
pf search  [--package PACKAGE] [--max-cells auto|N] [--ty-jobs auto|N] [--test-jobs auto|N]
           [--max-duration DURATION]
pf minimize [--package PACKAGE] [--max-cells auto|N] [--ty-jobs auto|N] [--test-jobs auto|N]
            [--max-duration DURATION]
```

每个调度选项必须在 parser/request 中保留“省略”与显式 `auto` 的区别。project load 后只形成一次 resolved
Run limits：省略取 EffectiveConfig；显式 `auto|N` 覆盖对应持久值。`minimize` 原样复用 search limits。
旧 `--jobs` 不保留 alias。

`--package` 是先于配置合并的 target selector；`--max-duration`、`--force`、Failure ID、report paths 与
output path 是 invocation/action 输入，没有持久配置对应项。本文不新增 target、extra、search space、
artifact、timeout、ty args 或 test command 的 CLI override。

## 10. 目标内部 interface 与 ownership

公开 interface 的扁平性只服务用户书写，不成为内部 ownership。`ConfigLoader.load(root_observation,
target_observation) -> EffectiveConfig` 保持唯一 raw 配置入口，并输出 strict/frozen、按消费者职责分组的
Schemas：

```text
EffectiveConfig
  target: TargetConfig
    python_minors: tuple[str, ...] | None
    platforms: tuple[str, ...] | None
    dependency_selection: AllSearchable | Managed(names) | Unmanaged(names)
    extras: ExtraConfig(policy, custom_surfaces)
  search: SearchConfig
    default: SearchPolicy(space, step, prereleases)
    overrides: tuple[DependencySearchPolicy, ...]
  resolution: ResolutionConfig(artifact, timeout_seconds)
  ty: TyConfig(args, timeout_seconds)
  test: TestConfig(group, command, cwd, timeout_seconds)
  scheduling: SchedulingConfig(max_cells, ty_jobs, test_jobs)
```

`SearchPolicy.step` 与 public `search-step` 使用同一 `major | minor | patch` 词汇，不在内部保留
`granularity` 别名。`DependencySearchPolicy` 包含 canonical `name` 和完整的 `space/step/prereleases`，没有
optional override field：ConfigLoader 先合并 raw root/member layers，再让最终 `dep` entry 从最终 global
`SearchPolicy` 继承省略值。duration 在内部一律是正整数秒或 `None`；persistent scheduling 值仍是
`auto | PositiveInt`，供 invocation 开始时解析。

扁平 key 到 schema 的映射固定如下；调用方只消费右侧 schema，不理解 TOML 拼写：

| TOML key | normalized schema field | 首要消费者 |
| --- | --- | --- |
| `pythons` | `target.python_minors` | ProjectLoader 的 requires-python 收窄与 Cell 展开 |
| `platforms` | `target.platforms` | ProjectLoader 的 platform Cell 展开 |
| `managed-deps` / `unmanaged-deps` | `target.dependency_selection` tagged union | ProjectLoader 的 declaration ownership |
| `extra-policy` / `extra-surfaces` | `target.extras.policy/custom_surfaces` | ProjectLoader 的 effective surface 展开 |
| `search-space` | `search.default.space` | search policy binding |
| `search-step` | `search.default.step` | search policy binding |
| `search-prereleases` | `search.default.prereleases` | search policy binding |
| `dep[].name` | `search.overrides[].name` | ProjectLoader 的 dependency binding |
| `dep[].search-space` | `search.overrides[].space` | search policy binding |
| `dep[].search-step` | `search.overrides[].step` | search policy binding |
| `dep[].search-prereleases` | `search.overrides[].prereleases` | search policy binding |
| `resolve-artifact` | `resolution.artifact` | CandidateBuilder 与 project/environment resolution |
| `resolve-timeout` | `resolution.timeout_seconds` | EnvironmentFactory/UvAdapter |
| `ty-args` / `ty-timeout` | `ty.args/timeout_seconds` | StaticEvaluator |
| `test-group` / `test-command` / `test-cwd` / `test-timeout` | `test.group/command/cwd/timeout_seconds` | ProjectLoader 与 RuntimeEvaluator |
| `max-cells` / `ty-jobs` / `test-jobs` | `scheduling.max_cells/ty_jobs/test_jobs` | workflow 的 RunLimits composition |

ConfigLoader 独占 raw TOML key validation、root/member persistent merge、list/AoT replacement、canonicalization、
默认值、config-only cross-field validation 与 package namespace prohibition。它可以验证每条最终存活且
fully inherited 的 space/step 组合，却不重复解析 project dependency/source 语义。ProjectLoader 已拥有
declarations、managed/fixed 分类和 SourcePlan 输入，因此由它依据最终 dependency-selection variant 验证每个
named override 确实匹配 managed searchable direct dependency，
并在 `PackagePlan` 中产生按 canonical name 排序、覆盖全部 managed searchable direct dependencies 的
`dependency_search_policies: tuple[NamedSearchPolicy, ...]`。CandidateBuilder 只按 name 取得已经绑定的 policy，
不再实现 default inheritance、override merge 或 unknown-dependency 判断。

ProjectLoader 还消费 normalized target/extra/test facts；CandidateBuilder 消费 bound dependency search policy
和 `ResolutionConfig.artifact`；EnvironmentFactory/UvAdapter 消费 resolution facts 和 uv project-config identity；
evaluators 消费 ty/test facts。任何 caller 不得重新读取 `[tool.pf]`。这样删除 ConfigLoader/ProjectLoader 的
composition 后，raw merge、项目语义绑定和候选 policy 默认逻辑才会重新散回多个调用方，两个既有 modules
都通过其 interface 提供实际 leverage，而不是增加一个只转发字段的新 seam。

CLI override 不修改 `EffectiveConfig`，也不建立第二份配置 merge。workflow 在 project load 后把 CLI optional
values 与 `SchedulingConfig` 一次解析为 invocation-local `RunLimits(max_cells, ty_jobs, test_jobs,
max_duration)`；VerificationRunner 与 evaluator pools 只接收 resolved positive integers/`None`，不再理解
TOML、layer、`auto` 或“CLI 是否省略”。

### 10.1 Wire 与 identity 影响

`EffectiveConfig` 不是 public wire，D014 JSON 不保存 raw `[tool.pf]` key 或 nested internal schema。因此
`release-granularity` → `search-step` 等配置重命名本身不重命名任何 package-floor JSON property；现有
`identity.policy_identity`、`inputs.candidate_snapshots[].policy_identity` 与
`evidence.attempts[].resolution_context_digest` 等 opaque digest 字段保持原名和形状。

目标 identity ground truth 为：

- SourceSnapshot 的 pyproject `remainder_digest` 覆盖 `[tool.pf]`；配置重命名或值变化会自然改变
  SourceSnapshot 与 report generation identity，这是预期的 source drift，不增加单独 config wire；
- `pf:candidate-policy:v1` 的 canonical preimage 精确包含对应
  `NamedSearchPolicy(name, space, step, prereleases)` 与 `resolution.artifact`；不再 hash 整份
  `EffectiveConfig`，也不包含 timeout、target、test/ty 或 scheduling facts；
- `pf:policy:v1` 的 config preimage 精确包含
  `resolution(artifact, timeout_seconds)`、`ty(args, timeout_seconds)` 与
  `test(command, cwd, timeout_seconds)`，并继续包含现行 tool version、diagnostic、verifier outcome 和
  failure policy facts；target/extra 由 Cell、dependency selection 由 declarations/vector、`test-group` 选择
  的实际 harness 由 Attempt/harness facts 绑定，不在此重复；
- D012 `pf:resolution-context:v1` 删除 PF 推断的 prerelease policy，新增本次 source snapshot 中 uv
  project-configuration input identity；不包含 PF search policy 或任何 timeout；
- `SchedulingConfig` 与 resolved `RunLimits` 不进入上述任何 policy identity。

PF 尚未发布，以上 canonical preimage 都在现有 `v1` prefix 下直接替换，不提升 Schema 或 identity version，
也不读取旧 preimage。P029 的 D014 切片必须逐项证明：没有 raw config key 被加入或改名；JSON Schema 不因
配置重命名产生结构 diff；最小 examples 中受影响的 source/generation/policy/context digests 按新算法重建；
reader 只复证目标算法。若其他实施需要真正改变 wire shape，必须在 P029 中单独回指 D014 owner，不能把它
归因于配置名称变化。

## 11. 直接替换与 owner 归并

接受后以下旧 interface 必须原地删除：

| 旧配置 | 目标配置 |
| --- | --- |
| `python` | `pythons` |
| `platform` | `platforms` |
| `extras` | `extra-policy` |
| `extra-surfaces` | 保持名称，语义改为与 `extra-policy` 组合 |
| `search-space` | 保持全局名称；逐依赖使用 `dep[].search-space` |
| `release-granularity` | `search-step` / `dep[].search-step` |
| `distribution` | `resolve-artifact` |
| `allow-prereleases` | 删除；resolution 委托 uv，搜索候选用 `search-prereleases` |
| `ty-args` | 保持名称 |
| `test-group` | 保持名称与单 group 语义 |
| `test-command` | 保持名称 |
| `command-cwd` | `test-cwd` |
| `jobs` | `max-cells` |
| `resolve-timeout` | 保持名称 |
| `ty-timeout` | 保持名称 |
| `test-timeout` | 保持名称 |
| `--jobs` | `--max-cells` |
| `[tool.pf.package.<name>]` | 删除；package-local 配置写入该 package 自己的 `[tool.pf]` |

新增 `dep[]`、`search-prereleases`、`ty-jobs`、`test-jobs` 及其 CLI override。被替换的旧字段出现时与
任意 unknown key 相同，立即形成配置/调用错误；不得同时读取新旧字段、自动转换文件或只发 warning。

完成实施时，稳定规则必须归并到：

- D001：全部产品配置、默认值、precedence、命令选项与 artifact/prerelease 行为；
- D002：EffectiveConfig/PackagePlan search policy/RunLimits interface、ConfigLoader 与 ProjectLoader ownership；
- D003：逐 dependency candidate space、step 与 prerelease inclusion；
- D004：`ty-*` 与 `test-*` 的 evaluator 行为；
- D006：新 CLI flags、help、调用错误和省略/显式 override；
- D008：`max-cells`、阶段 pool 与 invocation Run limits；
- D012：shared artifact admission、uv prerelease delegation 与 resolution context；
- D014：保持 config rename 不进入 wire，归并新的 v1 identity preimage，并重建受影响的 example digests；
- README/R006：用户入口与 jobs P1 项关闭。

## 12. 验收标准

1. 一个 target 的 persistent config 严格按 root default → member local 合并；scalar 替换，list/AoT 整体
   替换，CLI 只在显式出现时覆盖。
2. root target 只应用 root `[tool.pf]` 一次；root/member 中任何 `tool.pf.package` namespace 都 fail closed，
   package-local 配置只能来自该 package 自己的 `pyproject.toml`。root 无独立 local override 是已明确选择的
   deliberate tradeoff，不预留隐藏扩展点。
3. `pythons` 只能缩小 `project.requires-python`，`platforms` 只建立 PF platform Cells；旧
   `python/platform` 被拒绝。
4. effective extra surfaces 等于 policy 展开与 custom surfaces 的规范并集；custom surfaces 不再承担 base/
   each 完整性要求，surface 内、custom 之间及 custom/policy 之间的重复项静默去重，未知 extra 失败。
5. `managed-deps` / `unmanaged-deps` 语义保持，较高 layer 可以以一个 variant 整体替换较低 layer。
6. global `search-*` defaults 与每个唯一 `dep` override 形成确定 effective policy。member 省略 `dep` 时继承
   root 整表；member 显式设置任意 entries 或 `dep = []` 时 root 整表完全不适用，不按 name 合并。只有最终
   存活的 entries 才依据最终 dependency-selection variant 做 managed searchable 资格校验；space/step
   组合与 canonical duplicate 全部被验证。
7. `search-prereleases` 只改变冻结候选；PF 不传任何 uv prerelease override。highest prerelease 合法，
   exact prerelease 服从 uv 项目配置，check 仍是 declaration `lowest-direct`。
8. `resolve-artifact` 对 smoke/check/search 的 project/environment resolution 和 search candidate 采用同一
   registry artifact admission；artifact 不一致不能产生 PASS evidence。
9. `test-group` 正确组合 root/member 的同名 group；扁平 `ty-*` / `test-*` 配置映射到内部职责 schema，
   并通过现行 public evaluator seam 体现。
10. `smoke`、`check`、`search` 与 `minimize` 在 snapshot/process 前要求有效 `test-command` 与存在的
    `test-group`，即使当前宿主没有匹配 Cell；`explain`、`apply`、`diagnose` 与 `merge` 不作该直接准入检查。
11. `max-cells`、`ty-jobs`、`test-jobs` 都有可观察调度消费者；CLI 省略继承 config，显式 `auto|N` 覆盖，
    stage limits 不被传给外部工具 argv，也不进入 compatibility/report policy identity。
12. ConfigLoader 是 raw `[tool.pf]` 的唯一 reader，并将扁平字段映射为 nested `EffectiveConfig`；ProjectLoader
    独占最终 named dependency policy 的项目语义绑定；CandidateBuilder、UvAdapter、evaluator、workflow 与
    CLI 不建立平行 merge/default/canonicalization。
13. package-floor 不序列化 raw config key；SourceSnapshot、`pf:candidate-policy:v1`、`pf:policy:v1` 与
    `pf:resolution-context:v1` 严格使用 §10.1 的目标 preimage，三个 timeout 同等进入 evaluation policy，
    scheduling limits 全部排除；Schema/prefix 保持 v1，examples 的受影响 digests 被重建和复证。
14. 全部被替换的旧 config/CLI 名称、compat aliases、dual-read 分支和只验证旧语法消失的临时测试在交付前
    删除；当前 contract 只通过 public seams 做正向语义验证。
15. D001/D002/D003/D004/D006/D008/D012/D014、README、Schema/examples 与 R006 同步；P029 记录每条验收的
    实现切片及 exact commands/results，完成后稳定规则由 owner 文档唯一持有并共同归档 D023/P029。

## 13. 非目标

- 修改 project dependency/floor 语义、D003 coordinate descent 或 apply projection；
- 由 PF 复刻 uv prerelease、index、fork、yanked 或 transitive resolution policy；
- 把 `pythons` 写回 `project.requires-python`，或为 platform 发明 project metadata；
- 让 PF 拆分任意 test command、自动启用 pytest-xdist、自动注入 ty worker 参数；
- 为配置建立独立文件、环境变量映射、用户级 config、interactive editor 或自动迁移器；
- 保留旧字段兼容期，或把临时 Design 当作长期配置 owner。

## 14. 接受与生命周期

本文没有未决产品分支。接受本文表示接受 §2 的完整目标契约、§11 的直接替换和 §12 的全部验收标准；
不等于实现已经完成。接受后下一步只能先建立 P029，再按 Plan 实施、持续回填证据、逐条审计验收，最后把
稳定规则归并到现行 owners，并在同一完成变更中归档 D023/P029。

- 2026-09-04：用户要求实现 D023，接受本文的完整目标契约并授权建立 P029 后实施；此状态不表示实现完成。
- 2026-09-04：P029完成AC1–AC15实现与三解释器验收，稳定规则已归并现行owners；D023/P029同步归档，
  本文不再承担现行契约。

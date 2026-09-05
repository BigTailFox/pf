# D030 — PF search-space 系列切片 DSL 与条件默认值

- **状态：** 已完成、归档；实施与验收记录见 [P036](../plans/P036-pf-search-space-dsl.md)
- **日期：** 2026-09-05
- **性质：** 临时性产品、配置、候选与报告契约迁移 Design
- **核对基线：** `bfe9947`；起草前工作区干净
- **来源：** 本轮 search-space 讨论与 [E004 §9](../../experiments/E004-requests-validation-surfaces.md#9-后续诊断search-为何在-idna-02-停止)
- **稳定 owner：** [D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)、[D003](../../designs/D003-pf-search-algorithm.md)、[D006](../../designs/D006-pf-cli-enhancement.md)、[D008](../../designs/D008-pf-verification-run.md)、[D014](../../designs/D014-pf-report-schema.md)
- **不变契约：** [D005](../../designs/D005-pf-failure-and-diagnose.md) 的证据资格与 [D012](../../designs/D012-pf-harness-relaxation.md) 的 resolution ownership

本文保存已实施迁移的决策与验收要求，不再承担现行契约。实施前建立 P036，将验收标准映射到接口迁移、
测试与证据；完成后稳定规则已吸收进上述 owner，并与 Plan 同步归档。

## 1. 问题与决策摘要

现行 `current-major/current-minor` 锚定 baseline，名称没有明确参照物；默认 `all + minor` 会探测很早的
历史版本。E004 的 idna 搜索在 0.2 构建失败后按契约停止，未取得 floor。本方案通过明确选择范围改善
默认探索，不把构建失败改成 Rejection，也不承诺所选范围内不会再次发生构建失败。

目标决策：

1. 用 `all`、`majors[...]`、`minors[...]` 表达全局与逐依赖空间；逐依赖继续支持非空 PEP 440 specifier。
2. DSL 使用 Python 风格的左闭右开切片；`baseline/declaration` 定位已存在系列的列表位置，`+N/-N`
   移动位置，不对版本号数字做加减。不存在的 major/minor 不占位置。
3. 锚点只有 `baseline` 与 `declaration`；不引入 `latest`、`oldest` 或用户可配置的 `auto` 字符串。
4. 未指定 space 时，按 dependency × Cell 从 `search-space-defaults` 选择表达式；内建值为有下界
   `majors[declaration-1:]`、无下界 `majors[baseline-2:]`。默认表可在全局和逐依赖覆盖，显式 space 优先。
5. `search-step = major | minor | patch` 独立，默认 `minor`；允许所有空间与 step 组合。
6. 无下界、系列无法定位和筛选后为空是不同结果；锚点资格只在 search/minimize 消费空间时检查。
   无效配置与有效策略无法针对冻结 evidence 求值使用不同错误类型；配置错误沿用退出3，后者退出2。
7. 报告保存去重后的请求策略与必要系列观测，离线派生锚点和 selection，并复算候选策略身份；
   新的默认语义与旧报告授权严格隔离，不重复保存求值结果或完整 registry response。
8. 特殊发布习惯由用户用 space/step 表达；PF 不从版本号或发布分布自动推断兼容性语义。Registry 分析
   与策略建议 CLI 作为独立 Design 待办，不进入本迁移，见 §12。

## 2. 配置入口与继承

```toml
[tool.pf]
# search-space 省略：按每个 dependency × Cell 选择默认表的分支
search-space-defaults = { with-lower-bound = "majors[declaration-1:]", without-lower-bound = "majors[baseline-2:]" }
search-step = "minor"

[[tool.pf.dep]]
name = "numpy"
search-space = ">=1,<=2"
search-step = "patch"

[[tool.pf.dep]]
name = "idna"
search-space = "majors[declaration-1:]"

[[tool.pf.dep]]
name = "urllib3"
search-space = "all"

[[tool.pf.dep]]
name = "zero-series-lib"
search-space-defaults = { with-lower-bound = "minors[declaration-1:]", without-lower-bound = "minors[baseline-2:]" }
search-step = "patch"
```

`numpy>=1,<=2` 在本结构中拆成 `name = "numpy"` 与 `search-space = ">=1,<=2"`。
space 不包含第二份 dependency name、extras、marker、URL 或 source；specifier 由 packaging 解析，
`<=2` 保持精确 PEP 440 含义，不扩写成“全部 2.x”。显式 specifier 入口仍仅属于逐依赖配置。

root → target member 的 scalar 覆盖和 `dep` AoT 整表替换不变。先验证 raw layer，再合并；只对最终
保留的 named entries 做 dependency 资格校验。`search-space-defaults` 是含两个命名字段的完整对象，
出现时必须同时提供两项；未知字段、缺项或非字符串值均报配置错误。member 提供全局默认表时整体替换
root 默认表，不逐分支深合并。最终保留的 dep entry 中提供默认表时整体替换最终全局表；省略则继承，
全局也省略时使用内建表。`dep = []` 清除 named overrides，但不清除全局默认表。

实际 space 的优先级固定为：逐依赖显式 space → 全局显式 space → 逐依赖默认表 → 全局默认表 → 内建表。
全局显式 space 因此优先于逐依赖默认表；只有两个层级都未指定 space，才选择默认表分支。
member 省略全局 space 是继承，不是清除 root 显式值。本次不增加 null/reset 语法。

`search-step`、`search-prereleases` 各自按逐依赖 → 全局 → 静态默认值继承；默认分别为 `minor/false`。
只覆盖 step 或默认表的 dep entry 不会使 space 变成显式设置。显式空间替换默认空间，不与默认 DSL 取交集。
所有 named override 仍只能指向有资格的 managed searchable direct dependency。

### 2.1 可配置的条件默认值

`search-space-defaults` 的分支按有效声明是否具有下界选择，不按 dependency 是否出现在 pyproject 判断：
`A<4` 已声明依赖，但属于 `without-lower-bound`。两项均复用全局 space 的合法值，即 `all` 或系列 DSL；
不支持逐依赖专用的 PEP 440 specifier。它不是新的语法引擎，不引入 `search-lookback`、
`search-space-traceback`、`search-space-default-policy` 或另一套数字参数配置。

`without-lower-bound` 表达式不得引用 `declaration`，在 raw 配置验证阶段报错，即使该分支暂未生效。
`with-lower-bound` 可以引用任一锚点或使用 all。自定义表只选择一次分支，不在 anchor/registry/build
失败后尝试另一分支。两个分支的来源不同于 anchor：无下界分支可以且默认确实引用 baseline。

| 输入情况 | effective space | 原因 |
| --- | --- | --- |
| 无显式 space，有有效下界 | 所选表的 `with-lower-bound`；内建为 `majors[declaration-1:]` | `default-declaration` |
| 无显式 space，没有有效下界 | 所选表的 `without-lower-bound`；内建为 `majors[baseline-2:]` | `default-unbounded` |
| 有显式 space | 该表达式 | `explicit` |

例如 `A>=2.5` 在内建默认下从声明 major 的前一个已存在系列向后选择；`A<4` 从 baseline major 的前
两个已存在系列向后选择，最终仍保留 `<4` 和 baseline 上界。不足所需系列时按切片规则截到列表开头。
开放右端不额外限制该方向，最终公共资格控制上界；并未把 `:baseline+1` 隐式加回表达式。含锚点的
scope 仍按 §4.2 限制，跨 epoch 不声称与显式双锚点区间等价。

不要求用户为使用 PF 人为补下界；希望扩大历史搜索可显式选择 all，或把默认表的相应分支设为 all。
所选表达式引用的系列不存在时仍报 `SearchSpaceResolutionError`，不切换分支。`default-unbounded` 仅表示声明没有下界，不意味着
实际空间必然是 all；原因与 effective expression 是不同的派生事实，分别展示，不作为重复 wire 字段保存。

ConfigLoader 必须保留省略状态，不能提前填充某个字符串；相同 dependency 在不同 Cell 上可能有不同
生效声明，因此默认空间不能只按 dependency name 在配置读取阶段冻结。合并后的完整默认表可以先
按 name 保存，分支选择推迟至 Cell 的有效下界确定后。

### 2.2 不同发布习惯

major/minor/patch 只表示版本号的位置，不代表破坏性变更、功能更新或兼容性保证。PF 自动处理可确定的
下界、baseline、系列存在性和候选资格；搜索意图由默认表或用户显式配置决定，不增加 version-scheme
标签，也不根据 0.x、发布密度、版本号空洞或发布时间自动改 space/step。

| 用户已知的发布习惯 | 可选空间 family | 可选 step |
| --- | --- | --- |
| 常规 SemVer：major 大版本、minor 功能版本 | majors | minor |
| major 主要发布、minor 修复发布 | majors | major |
| 0.x：minor 大版本、patch 功能版本 | minors | patch |

以上是配置示例，不是 PF 的自动分类表。major step 在每个 major 内取最高合格精确 release，不是只测
`x.0`。用户可以逐依赖写显式 DSL，或像 §2 的 zero-series-lib 一样覆盖两个默认分支并独立设置 step。
从 0.x 跨到 1.x 不触发策略自动切换；anchored minors 仍局限于一个 major，需要跨越时可显式设置
`search-space = ">=0.8,<2"` 与 `search-step = "patch"`。

## 3. DSL 语法与规范化

```ebnf
space    = "all" | family "[" selector "]"
family   = "majors" | "minors"
selector = bound | [bound] ":" [bound]
bound    = anchor [("+" | "-") uint]
anchor   = "baseline" | "declaration"
uint     = digit {digit}
```

关键字区分大小写，允许 token 之间的水平空白，canonical form 去除这些空白、偏移量前导零和 `+0/-0`。
不允许逗号选择、第三个冒号、乘除、嵌套表达式、调用、数字索引或任意 Python 执行。比如
`majors[declaration,baseline+1]` 是语法错误；提示使用冒号。解析不使用 eval。

`majors[:]` 与 `minors[:]` 都不引用锚点、不增加系列限制，直接规范化为 `all`。
单点选择与区间选择保留 typed 区别，不能通过文本替换实现求值。显式 PEP 440 字符串使用 packaging 的
规范形式；不把无效 DSL 当作其它表达式猜测，也不保留 current-major/current-minor 的兼容别名。

## 4. 冻结的系列列表

### 4.1 “已存在”的精确定义

本设计采用 **registry 发布记录视图**：一次成功 source query 中，由可识别的 wheel/sdist filename
得到的规范化版本集合，按 PEP 440 排序去重。只看本次 SEARCH SourcePlan 的 effective registry source；
不合并其它 registry，不混入 workspace HEAD，不以发布时间排序。

系列列表在以下过滤之前建立：Cell 的 Requires-Python/wheel compatibility、artifact policy、yanked、
prerelease policy、保留的声明约束、baseline 上界、space 和 step。因此“前一个已存在系列”不会因为
切换 wheel/sdist 或 prerelease 设置而移动。只有 yanked、预发布或不兼容 artifact 的系列也占据位置，
但它们未必贡献最终候选。损坏或无法可靠读取的 registry evidence 仍按 source failure 处理，不能当作
完整空记录；沿用 adapter 对无关/不可识别 filename 的现行处理。

当前 UvAdapter.query 返回的是已按 Cell 过滤的 AvailableCandidate，不能直接用它反推完整系列。
目标应从同一份 registry response 同时取得 `release_versions` 与原候选/artifact 观测，不额外查询 registry。
第一次成功 query 后缓存并冻结二者；失败不冻结为“空集合”。这是读取时点快照，不冒充发布时点历史查询；
既有 resolver release cutoff 规则不由本 DSL 改写。

### 4.2 系列与 scope

major key 为 `(epoch, major)`；minor key 为 `(epoch, major, minor)`。缺少 release 段时补零。
预发布/post/dev 后缀不建立额外系列，但精确版本排序及最终候选资格仍保留它们的含义。

- 含锚点的 `majors`：使用该锚点所在 epoch 的所有已存在 major，按 major 排序。
- 含锚点的 `minors`：使用该锚点所在 epoch/major 的所有已存在 minor，按 minor 排序。
- 同一表达式的两个锚点必须属于同一 scope；不同 epoch 的 majors 区间、不同 epoch/major 的 minors
  区间报 `SearchSpaceResolutionError`，不是 DSL 语法错误。偏移不能离开 scope。
- `all` 跨这些系列 scope，不要求任何锚点。因此不将 all 无条件定义成 `majors[:baseline+1]` 的语法糖。

### 4.3 锚点定位

`baseline` 是本 Cell 已完整验证通过的 baseline managed vector 中该 dependency 的精确版本；
不等于仓库最新版本，也不随坐标搜索中的 current 向量移动。

`declaration` 从该 dependency 在该 Cell 中 active 的直接声明约束合取取得，收集 `>` 与 `>=` 的版本
端点并取 PEP 440 最大者作为下界锚点。同值严格/非严格下界定位同一系列；约束本身的原语义保留。
默认值资格只针对 managed searchable dependency；同名 active 直接声明对 effective 下界有约束的均纳入，
harness specifier 不成为 declaration anchor。base 与 extras、marker 与 canonical name 先按现行 Loader
规则完成归一化。无这些下界端点就是无 anchor，不从 `<4`、`!=2` 或 registry 最早记录推导。

`==`、`===`、`~=`、固定 source 的现行 fixed/searchable 资格不变，本次不扩展固定依赖搜索。
下界可以是未实际发布的精确版本；DSL 只要求其所引用的 major/minor 系列在上述列表中存在。
例如 `>=2.5.3` 与发布记录 `2.5.4/2.6.0` 可定位 major 2 和 minor 2.5；若只有 `2.6.0`，major 可定位，
minor 2.5 不存在。即使表达式还带偏移，也必须先定位原锚点，不采用插入位置或邻近系列代替。

## 5. 列表位置切片

设 scope 内列表为 `S`，长度为 `n`，anchor 定位索引为 `i`。`anchor+k` 的值是 `i+k`，不是系列编号加 k。
所有被引用的锚点先完成资格检查，不因预期为空区间而短路错误。

- 单点 `S[anchor+k]`：索引在 `[0,n)` 内则选择该系列，否则结果为空；不回绕或钳制成首尾系列。
- 区间 `S[start:stop]`：左闭右开；省略 start/stop 分别为 `0/n`。显式端点计算后截到 `[0,n]`；
  start 不小于 stop 时为空。负偏移结果不再按 Python 的负索引从尾部换算。
- 空端点表示不额外限制该方向，不隐式替换为 declaration/baseline。最终公共资格仍可收窄结果。

这沿用 Python 的切片形状与半开区间，但锚点相对偏移采用上述明确的越界规则，不声称是完整 Python
表达式语义。向前不足一个系列时，默认切片从列表开头开始。

假设冻结 major 列表为 `1/3/7/9`，baseline 为 `7.4.2`：

| 表达式 | DSL 选择 |
| --- | --- |
| `majors[baseline]` | 7.* |
| `majors[baseline-1]` | 3.* |
| `majors[baseline-2:baseline+1]` | 1.*、3.*、7.* |
| `majors[baseline-2:]` | 1.*、3.*、7.*、9.* |
| `majors[:baseline+1]` | 1.*、3.*、7.* |

最终候选仍不高于 baseline，因此上表第4行的 9.* 不进入 CandidateSnapshot。偏移依赖冻结的已存在系列，
不是“最近三个精确 release”。`minors[declaration:]` 在 declaration major 内向后选，不跨 major。

## 6. 空间、资格与采样的执行顺序

```text
读取/验证/合并配置（包括完整默认表）与建立 Cells
→ 仅 search 按 Cell 选择默认分支并做无网络的 anchor 准入检查
→ 原 highest baseline 完整验证
→ 冻结 registry 发布版本与候选/artifact 观测
→ 根据 family/scope 建立系列，定位锚点，求出 DSL 选择
→ 公共资格 ∩ DSL 选择（或显式 specifier；all 不增加选择限制）
→ search-step 选系列内最高合格精确代表
→ 冻结 CandidateSnapshot
→ 既有 coordinate search / promotion / final verification
```

公共资格继续保留 `<`、`<=`、`!=`，移除可搜索声明下界的限制；同时保留 baseline 上界、yanked、
prerelease、source、Cell 与 artifact 资格。空间表达式不改变 resolver strategy，也不参与 baseline 的
版本选择。不能通过先按 step 采样再切 space 得到近似结果。

全部 `space × step` 组合合法。单个 major 配 major step、单个 minor 配 minor/major step 都可能仅留下
一个代表，这不是配置错误。代表是精确 release，apply 不截断到系列号，PASS 也不证明整个系列。
空间外 baseline 继续作为有 PASS 的搜索锚点而不是可返回 floor；空间内没有 PASS 时不得发布 baseline。

E004 的冻结 idna major 为 0/1/2/3，声明下界2.5，baseline3.19；内建默认表的有下界分支选择1/2/3，排除含0.2的0系列。
这只证明新空间会跳过该已知构建阻塞点，不证明1.x可构建、search完整成功或已取得最低版本。

## 7. 验证与错误处理

| 阶段/情况 | 目标行为 |
| --- | --- |
| 无效 TOML/requirement/DSL，重复或无资格 named override | 现行 `ConfigurationError` / 退出3；不查询 registry |
| 默认表缺项、未知字段、值类型/语法错误，或无下界分支引用 declaration | raw layer 的 `ConfigurationError`，即使该表或分支最终未生效 |
| search 引用 declaration，但该 Cell 的有效声明无下界 | 配置错误，列出 dependency/Cell/expression，建议 baseline 表达式或 all |
| search 的条件默认值遇到无下界 | 选择所选表的 without-lower-bound；内建为 majors[baseline-2:]，继续 |
| registry 访问/解析不可靠 | 既有 SOURCE_FAILURE / INDETERMINATE；不能声称锚点不存在 |
| 成功冻结后，被引用系列不存在或两个锚点 scope 不同 | `SearchSpaceResolutionError`，CLI 退出2；不转为配置无效、Rejection 或 NO_PASS |
| 表达式有效但切片为空、越界单点或公共资格筛空 | 既有 NO_PASS_IN_SEARCH_SPACE，不构造空 CandidateSnapshot |
| series 存在，但版本无法 build | D005 既有 BUILD_FAILURE / INDETERMINATE，按 D003 停止 |

无网络 anchor 检查在 Search workflow 完成项目/Cell 规划后、snapshot 与 baseline Attempt 前执行；遍历
已声明矩阵中的 active managed dependencies，即使某 Cell 不在当前 host 上也可检查声明下界。
registry 系列资格在实际执行 Cell 的 candidate discovery 阶段检查，不为非 host Cell 额外联网。
`smoke/check` 不消费 search anchor，不因合法的 `A<4` 与显式 declaration 表达式组合被阻断；通用配置
语法错误仍按现行加载规则报告。Apply/explain/merge 不查询当前 registry 来定位锚点。

### 7.1 错误类型与来源

`ConfigurationError` 表示输入本身无效，或请求缺少所需声明前提。例如无下界依赖显式引用 declaration，
无需 registry 即可判定；它不同于声明具有下界、但该下界系列不在本次 source inventory 中。

新增 `SearchSpaceResolutionError`，作为与 `ConfigurationError` 并列的 `PfError` 子类，不继承配置错误。
它表示合法策略无法针对本 Cell 的冻结 source inventory 和 anchors 求值。至少区分
`missing-anchor-series` 与 `anchor-scope-mismatch` 两种原因，携带 dependency、Cell、effective expression、
所用 anchor versions、相关 scope/series keys 和冻结 source 身份；不存在的 facts 不伪造。
这些是策略求值诊断，不进入 D005 的 Failure/Rejection cause 枚举，也不与 resolver 的 resolution failure 混用。

例如 `foo>=2.5` 配 `minors[declaration:]`，成功冻结的 inventory 只有2.4和2.6：配置合法，但 declaration
minor 2.5 无法定位，报 `SearchSpaceResolutionError(reason=missing-anchor-series)`。不得改用2.6、
插入位置或 all。镜像未提供该系列也不能解释成该系列在所有 registry 都不存在。

`ConfigurationError` 保持当前 `src/pf/errors.py` 的 `INVALID_INPUT=3`；新增
`SearchSpaceResolutionError` 使用退出2，呈现独立的搜索空间求值诊断。
现有 `NO_PASS_IN_SEARCH_SPACE` 也可退出2，但两者的类型、原因与证据资格不同，不能仅凭退出码归因。
VerificationRunner/Scheduler 必须保留类型与细节，停止继续派发并按现行异常收尾规则等待/清理在途任务，
保留已完成和在途任务已取得的日志。失败的 dependency/Cell 不产生 CandidateSnapshot 或 Rejection。
一旦本次 Run 因 SearchSpaceResolutionError 终止，Search workflow 不写入或更新 package-floor.json，
也不发布包含其它已完成 Cell 的部分报告；既有报告逐字节保持不变，原本没有报告则不创建。
不执行本次 report-generation association replacement；日志按异常收尾规则保存，不将已有成功报告
描述成本次 Run 的结果。错误收尾不得依赖 `isinstance(error, ConfigurationError)` 才能生效。
成功求值后候选为空仍是 `NO_PASS_IN_SEARCH_SPACE`；无法读取 inventory 仍是 source failure。此分类及收尾需专门验收。

## 8. 模块 ownership 与目标 interface

用一个纯 `search_space` module 集中隐藏 DSL AST、规范化、锚点/系列定位与切片求值，避免 ConfigLoader、
CandidateBuilder、report reader 和 explain 各写一个解释器。它不访问 registry，不读取文件，不运行 resolver。

| Module / seam | 目标责任 |
| --- | --- |
| ConfigLoader | raw layer 验证、默认表整对象继承、保留 space 省略；调用纯 parser，不定位 Cell anchor |
| SearchSpace typed value | default/all/series/specifier 判别 variant；series 内区分单点与切片，端点为 anchor+有符号 offset |
| ProjectLoader / PackagePlan | 保持唯一 named policy 绑定；保存 requested space 与完整合并默认表，不把每 Cell 结果塞回单个 name 字符串 |
| search_space | `parse`、按 active declarations 选择默认表分支/绑定下界、依据冻结 series/baseline 求值；供线上与 reader 共用 |
| Search workflow | 调用无网络搜索准入；smoke/check 不调用此 anchor 准入 |
| CandidateProvider / UvAdapter | 同一次 query 返回 frozen `release_versions + candidates` 观测；source/HTTP/cache/脱敏仍归 adapter |
| CandidateBuilder | 结合 registry view、baseline 与每 Cell policy，交给纯求值逻辑；保证先过滤再取最高合格代表；冻结必要系列观测与候选 evidence |
| errors / runner / scheduler / CLI | 独立 SearchSpaceResolutionError；保持错误类型、冻结来源诊断、异常收尾和退出2映射 |
| ReportStore / schemas | codec、策略分组与系列观测 intern、identity、cross-reference；从已有声明/baseline 派生 selection 并离线校验，公开 validated typed projection |
| ApplyAuthorizer | 校验当前 requested policy/profile 与报告授权；维持 original/projected/no-op 语义 |
| Explain / terminal | 消费 validated policy projection，不解析表达式、不查询 registry |

Default variant 是内部明确状态，携带合并后的默认表，不隐式依赖模块中的旧常量；不暴露为新的 TOML 关键字。
新增模块的价值是一次实现供实际多个调用方
复用，不建立 parser registry、通用表达式引擎、额外 service facade 或可插拔 evaluator。

## 9. 报告、身份与 explain

### 9.1 报告级请求策略

报告增加 required `inputs.search_policy`，只保存完整合并后的语义，不保存 raw root/member 层级：

```json
{
  "search_policy": {
    "profile": "registry-series-slice-v1",
    "artifact": "any",
    "bindings": [
      {
        "dependencies": ["idna", "urllib3"],
        "requested_space": null,
        "space_defaults": {
          "with_lower_bound": "majors[declaration-1:]",
          "without_lower_bound": "majors[baseline-2:]"
        },
        "step": "minor",
        "prereleases": false
      }
    ]
  }
}
```

此例仅说明 wire 形状，不是 §2 配置的求值结果。`profile` 为固定语义标识；`artifact` 为本次实际
`resolve-artifact` 的规范值 `wheel/sdist/any`，二者在报告级各存一次。所选 artifact 的 kind 不能反推
artifact policy，例如 wheel 候选可能来自 wheel 或 any。顶层 evaluation policy digest 也不能代替该输入。

每个 binding 保存规范 requested space（省略为 null）、完整 `space_defaults` 两分支表及 effective
step/prereleases。按这四项完整语义相等分组，相同策略只保存一个 binding。`dependencies` 非空、按
canonical name 排序唯一；各组不重叠，恰好覆盖 package 的 managed searchable names。bindings 按
各组首个 dependency name 排序；无 managed searchable names 时为空数组。Reader 拒绝重复策略组、
重复/缺失/多余 name 或非规范输入，再展开为按 name 查询的 typed 映射。此处不再执行配置继承，
不为小策略对象引入另一套 ID/ref。

即使显式 space 生效，也保存完整默认表。保存未选分支服务于“仅改变未选分支也拒绝 apply”的授权规则，
不声称它是证明本次候选选择所需的执行事实。整个规范 `search_policy` 对象进入 generation preimage，
包括 profile、artifact 和未选分支；纯 host-partial、无 CandidateSnapshot 的报告也绑定这些输入。
读取和重建均不得使用当前内建默认值补齐历史策略。

### 9.2 必要系列观测与去重

报告增加 required `inputs.series_inventories`，按内容寻址 ID 排序唯一；没有 series selection 时为空数组。
每条观测保存 canonical dependency name、public registry SourceIdentity、family 和所需 scope 内完整、
非空、有序唯一的 `series_keys`。keys 使用 §4.2 的 major/minor key，scope 由共同的 epoch 或 epoch/major
前缀派生，不再另存一份 scope。Reader 检查 key 形状、共同 scope 及其与实际 anchors 的对应关系。
该记录只保存 DSL 所需 scope，不保存其它 scope、全部精确 release、未选 artifact 或原始 HTTP response。

观测 ID 为 `sha256("pf:series-inventory:v1\0" + canonical_identity_json({dependency, source, family,
series_keys}))`，其中 `\0` 表示 NUL 分隔字节；ID 本身不进入该 preimage。
相同内容跨 Cell 只存一次，不同内容保留不同观测。
同名依赖在不同查询中取得不同系列列表时，即使 source/family/scope 相同也不能合并；同内容去重只表示
对本 DSL 等价的系列事实，不证明查询来自同一时刻或完整 registry response 相同。

CandidateSnapshot wire 增加 required `series_inventory_ref`：effective space 为 series 时必须引用
匹配 dependency、SEARCH effective source、family 与 anchor scope 的观测；all/specifier 时为 null。
引用只能指向本报告内的记录。Report build/reintern 负责 intern，reader 拒绝悬空或错配引用，以及
从 CellResult → CandidateSnapshot 不可达的额外观测。系列观测属于执行 evidence，不进入 generation ID。

不在 wire 另存 `space_selection`、默认分支/原因、effective expression、anchor versions、索引或选中系列。
这些信息由 §9.3 派生。新增体积主要随不同策略数、必要 name 绑定数和不同系列观测数增长；重复 Cell
只增加观测引用，不重复嵌入整张策略表或系列列表。不引入任意条数上限，也不通过截断系列列表改变偏移语义。

### 9.3 离线派生、验证与 identity

Reader 先验证请求策略、原始 declarations 和持有 CandidateSnapshot 的 Cell 的真实 full PASS baseline，再通过共享的
`search_space` module 派生 typed selection：所选分支、原因 `explicit/default-declaration/default-unbounded`、
effective canonical expression，以及表达式实际引用的 anchor versions、scope、索引和选中系列。
下界来自报告 active declarations，baseline anchor 来自本 Cell baseline managed vector；未使用的
anchor 不伪造。无 CandidateSnapshot 的 Cell 沿用各自终态的证据要求，不为 baseline 失败或未执行的
Cell 强求 PASS baseline。不得把 default-unbounded 硬编码成 all，也不能用当前项目声明重解释报告。

Reader 验证 anchor membership、scope、切片结果，并检查所有候选满足 space、保留声明约束、baseline
cap、prerelease policy 和保存的 artifact policy；按 step 复算每个代表的 series key，检查版本排序与
每系列唯一性。all/specifier 也按相同请求策略和公共资格检查，只是不消费系列观测。

最高合格代表的选择由 CandidateBuilder 对同次冻结的完整候选/artifact 观测保证，通过其 public seam
验证先过滤再采样。报告未保存所有被过滤或未选 release，因此 reader 只验证保存证据的结构与语义一致性，
不声称能离线证明代表是 registry 中该系列的最高合格 release，也不证明 registry 记录完备或不存在
未保存 release。既有精确 artifact、PASS 与 predecessor 证据要求保持。

`candidate_policy_identity` 继续使用 `pf:candidate-policy:v1`，绑定 profile、canonical dependency name、
该 Cell 的 effective canonical space、step/prereleases 和报告级 artifact policy；由完整保存/派生输入
复算并核对，不能仅把 wire 中的 opaque policy digest 纳入 snapshot digest 就视为策略已经复证。
CandidateSnapshot digest 继续绑定 Cell、source/SourcePlan、候选与代表，另绑定派生 selection 的
effective expression、原因、实际引用 anchor versions 和 `series_inventory_ref`；其余 scope、索引和
选中系列可由上述输入确定，不重复进入 wire。线上和 reader 共用这些派生与 identity 规则。

原始 requested policies 已由 generation 绑定，不能因结果恰好选出相同版本就跨语义复用报告。
实际引用的 anchor 或系列观测改变会改变 snapshot identity，即使最后代表集合碰巧相同。

### 9.4 读写、merge 与 apply

沿用 Schema 1、各 v1 identity 前缀与 generator algorithm v1，按 pre-release 规则直接更新模型和生成物。
没有新 required search_policy、series_inventories 或 snapshot 引用字段的旧 wire fail closed，
不增加 dual reader、推断默认值或兼容 alias；null 引用和空观测表仅用于上述明确允许的情况。

Report build/reintern/update/merge 必须保留原请求策略及最终 CellResult roots 可达的系列观测与引用，
并保持规范分组和内容去重；同一 name 的不同可达观测不得相互覆盖。被替换结果独占且已不可达的观测
随旧 evidence 一同清理。ValidatedReport 提供 typed 请求策略、观测与派生 selection，
消费者无需读取 raw wire 或自行 join refs。当前用空 EffectiveConfig 与
`all/minor` 占位重建 PackagePlan 的路径不能用于填补这些新事实。不同 generation 不混合，update_path
沿用整体替换规则。Evaluation policy 与 ResolutionContext 不新增用户 search 参数；搜索 profile 的授权
隔离由新 generation 输入和 ApplyAuthorizer 的显式检查负责，不挪用 D029 validation policy fact。

Apply 在 source-drift waiver 前比较当前合并后的规范 search_policy（含完整默认表、artifact 和固定 profile）；
保留既有 evaluation policy 校验。仅修改未选默认分支也属于 policy mismatch，force 不绕过。
不联网重算 series，不用 apply 后已经改写的声明下界重新解释原报告：首次/重复 apply 与原始或 projected
声明的关系继续由既有授权逻辑证明，报告锚点始终使用冻结的原始声明。这一点必须覆盖默认策略下的幂等 apply。

### 9.5 Explain

对每个已执行的 dependency/Cell 可展示：requested space、默认表及所选分支、effective space/default 原因、实际 anchor versions、
选中的系列、step 和最终代表；有配置但未执行的 Cell 显示尚未取得 registry/baseline 证据，不猜测系列。
Explain 消费 reader 已验证的 typed projection，不解析 DSL、不 join wire refs，也不访问 registry；
有 baseline 但候选发现尚未成功的 Cell 只展示已取得的事实，不补造系列 selection。
`all` 仍可能被公共资格筛空；界面不得把“系列选择”描述成完整兼容区间或无条件支持所有版本。

## 10. 迁移范围与验收标准

实施 Plan 必须覆盖以下 AC；实现与验收由 [P036](../plans/P036-pf-search-space-dsl.md) 跟踪：

| AC | 必须取得的证据 |
| --- | --- |
| 1 | global/dep DSL 与逐依赖 specifier 的 public config/parser tests；规范化与非法输入；默认表两项必填、未知字段/类型、无下界分支禁止 declaration，即使未生效 |
| 2 | root/member/dep 的默认表整对象继承、显式 space 优先、只覆盖 step、显式 all 与省略区分；按 Cell 选择有/无下界分支，内建与自定义表达式均覆盖 |
| 3 | 同名 active base/extra 下界合取、严格端点、无下界、精确版本未发布但系列存在、固定依赖资格不扩展 |
| 4 | 稀疏 major/minor 序列位置偏移、半开/开放区间、越界/反向/空列表、缺失 anchor、epoch/scope、无 anchor 的 [:] |
| 5 | 同次 registry response 在兼容过滤前建立发布系列；yanked/prerelease/不兼容系列仍占位；查询缓存冻结与 source failure |
| 6 | public CandidateBuilder 检查 space 后 step、公共资格保持、所有组合合法，并从含多个合格/不合格 release 的冻结观测选出系列内最高合格精确代表；major+major 与 minors+patch 示例按配置取代表，不按发布分布自动改策略 |
| 7 | search 缺下界前提的 ConfigurationError 退出3且不创建 Attempt；smoke/check 不作 anchor 准入；missing series/scope mismatch 为独立 SearchSpaceResolutionError，退出2且不创建失败项 snapshot 或 Rejection；多 Cell 并发中停止派发、等待/清理在途任务并保留已完成/在途日志，不创建或更新报告、不替换 report-generation associations，已有报告逐字节不变；source failure 与有效空空间仍各循既有路径 |
| 8 | Schema/examples/reader tamper tests；search_policy 含 profile/artifact/完整默认表进入 generation；由原始 declarations/baseline 派生分支/原因/表达式/anchors，核验 series 引用、候选资格与 step 结构，完整复算 candidate policy identity 并拒绝策略与 digest 不一致；anchor/series 改变但代表相同仍改变 snapshot identity；纯 host-partial generation、merge/reintern 无默认占位、旧 wire fail closed |
| 9 | apply 的 search_policy mismatch（含 profile/artifact/仅改变未选默认分支）在 force 前失败，default 下 original/projected/repeated apply 幂等；explain 通过 validated projection 展示默认表/分支/selection，缺执行证据时不猜测，全程离线 |
| 10 | 搜索公共 seam fixture 证明排除空间外 build failure，同时空间内 build failure 仍停止；不得削弱完整 PASS、predecessor 或 final 资格 |
| 11 | 对 E004 既有冻结事实离线展示新默认将 idna major 0 排除；记录其只是范围证据，不重跑完整 requests search |
| 12 | 实施时完成 focused、三 Python 全套、coverage、Ruff、ty、build、生成物与文档检查；owner 吸收并将 Design/Plan 同步归档 |
| 13 | public report build/read/merge fixtures 验证相同策略合为一组、name 恰好覆盖且 reader 拒绝非规范分组；相同系列观测跨 Cell 只存一次、同名不同观测均保留；错配/悬空/不可达观测被拒绝；增加共享观测的 Cell 不增加策略或观测记录数，记录序列化体积对比，确认不嵌入重复 selection 或完整 registry response |

Owner 迁移：D001 接收配置/默认表/继承/发布习惯的用户语义；D002 接收 typed space、inventory 与模块 seam；D003 接收
冻结、切片/过滤/采样及错误分类和时序；D006 接收独立错误诊断/退出码/explain；D008 接收搜索准入和异常收尾；D014 接收
required wire、策略分组/观测去重、离线派生与验证边界、generation/candidate identity/apply 隔离。
README 双语示例、CLI help、报告生成脚本和测试同步替换。

## 11. 评审重点与非目标

本轮已接受 DSL、位置偏移、无 oldest、可配置默认表、逐依赖范围、用户配置发布习惯及独立的空间求值
错误类型，并接受报告去重、派生 selection 与异常不写报告。实施时重点保持以下决定及其代价：

1. 系列按 registry 发布记录建立，资格过滤不移动索引；代价是“最近三个系列”可能少于三个有可用 artifact
   的系列，且 adapter 必须保留目前已被过滤掉的发布版本信息。
2. anchored minors 固定在一个 major、anchored majors 固定在一个 epoch；跨 scope 报错，全范围用 all。
3. 报告级保存去重策略及 artifact policy，独立 intern 必要系列观测；从原声明/baseline 派生 selection，
   闭合 generation/candidate identity/apply 校验，不重复落盘求值结果或完整 registry response。
4. 默认表提供时要求两项完整，按对象替换；generation 与 apply 绑定整个合并表，包含未选分支。
5. SearchSpaceResolutionError 终止整个 Run 的报告写入，保留既有报告与异常收尾日志；不发布其它 Cell
   的本次部分结果。Reader 不承担未落盘 release 的最高代表或 registry 完备性证明。

默认可能重新遇到其它历史 build failure；minor 采样也不证明最低 patch。无下界默认回看两个已有 major，
限制系列数不等于限制精确 release 数、验证成本或构建风险；major 0 内仍可能有很长的发布历史。用户
可用默认表、显式 DSL/range 和 step 调整范围与采样，也可主动选择 all。本次不改变 build disposition、候选探测算法、harness relaxation、
`~=` 的固定依赖资格、runtime verifier、部分 floor 授权或 registry 历史版本完备性保证。

## 12. 待办：独立 registry 分析 CLI Design

- [ ] 另起 Design，为 PF 增加独立的 registry 发布分布分析 CLI 命令；命令名、参数、输出与数据来源契约
  在该 Design 中确定。本项只登记待办，不预留 Design 编号，不创建 Plan 或实现命令，也不阻塞 D030 验收。

该提案可分析版本系列分布、稀疏程度和各系列 release 数，比较不同 space/step 的候选数量及覆盖范围，
给出带依据的配置建议，帮助用户选择适合包发布习惯的搜索策略。版本分布可证明形态和采样数量，不能
证明兼容性边界；候选数量也不等同于实际 verifier 执行次数或耗时。

分析结果用于辅助用户配置，不自动改写 pyproject、扩大/缩小 search 空间或替换 step，不让普通 search
隐式执行发布习惯推断。数据过滤口径、可复现的 inventory 证据与建议输出如何保存，留给独立 Design。
D030 为位置切片读取并冻结 registry 系列属于必要的候选事实采集，不等于此待办分析功能。

## 13. 完成记录

2026-09-05：AC1–13 全部通过；Python 3.10/3.11/3.12 各 1732 passed，coverage 90.40%，Ruff/ty、
构建与生成物检查通过。E004 §11 只作冻结事实的离线范围验证，未重跑完整 requests search。
稳定契约已归并 D001/D002/D003/D006/D008/D014，README 双语与 CLI help 同步；D030/P036 一起归档。
独立 registry 分析 CLI 待办保留在现行工程文档索引。完整命令、异常运行记录与逐项证据见 P036。

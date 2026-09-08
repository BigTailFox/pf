# PF 候选与搜索策略契约

- **状态：** 现行（长期 owner；从 D001 §4 迁移现有规则，无产品语义变更）
- **策略 profile：** `registry-series-slice-v1`
- **最后核对：** 2026-09-08
- **产品与配置层级：** [D001](D001-pf.md)
- **模块 interface：** [D002](D002-pf-implementation.md)
- **算法消费：** [D003](D003-pf-search-algorithm.md)
- **失败与 Run 收尾：** [D005](D005-pf-failure-and-diagnose.md)、[D008](D008-pf-verification-run.md)
- **Resolver：** [D012](D012-pf-harness-relaxation.md)
- **报告 wire 与 reader：** [D014](D014-pf-report-schema.md)

本文独占候选观测/准入、系列 DSL、anchor、条件默认、采样与 baseline artifact 选择域。
D001 继续拥有 managed/fixed 声明资格、Cell、root/member 通用配置合并、CLI override、统一 artifact
policy 与数值退出码；D003 拥有搜索顺序/边界，D014 拥有 identity/wire/reader。SearchPolicy 消费
本文的完整有效候选策略；精确安装选择与搜索 provenance 分开绑定。本文件不新增配置、
Schema 或运行行为，也不作为临时迁移 Design 归档。

## 1. 候选观测与准入

每个 managed searchable dependency/Cell 第一次查询后冻结规范化、升序、非空的精确候选快照；运行中新发布版本不参与。候选不高于 baseline，排除 yanked；`search-prereleases` 默认 `false`，只控制 PF 候选是否包含 prerelease。

Registry query 必须先验证响应结构并观测可解析 wheel/sdist release，再按当前 Cell 判断
Requires-Python 与 Python/ABI/platform tags 适用性，最后校验适用 artifact 的安装 locator 和 SHA-256。
URL 必须为非空字符串，hashes 必须为对象，Requires-Python/yanked 等必要字段类型始终严格；
明确不适用的 artifact 不要求可安装 HTTP(S) locator/SHA-256，其 release 仍进入过滤前观测。
适用 artifact 缺哈希或 locator 非法使整个 query 失败，不静默丢弃，不增加其他哈希安装路径。
Adapter 不复制 CandidateBuilder 的 yanked/prerelease/声明/baseline/space 筛选。

候选消费 [D001 §4](D001-pf.md#4-候选与验证边界) 的统一 artifact policy。Candidate 始终绑定实际 artifact locator/hash 与精确版本。

## 2. 搜索空间与采样

`search-resolution` 为 `major | minor | patch`，默认 `minor`；每个系列使用同次冻结观测内最高合格精确 release 作为代表，apply 不截断版本。`search-space` 支持：

```text
all
majors[baseline]             majors[declaration-1:]
minors[baseline-2:baseline+1] minors[declaration:]
```

所有 space 与 resolution 可组合，二者分别控制范围与采样。方括号内是单个 anchor 或左闭右开切片；端点只能是
`baseline` / `declaration`，可接 `+N/-N`，N 是非负十进制整数。允许 token 间空格/tab，规范化去空白、
前导零与零偏移；不允许数字索引、逗号、第三个切片参数或表达式执行。`majors[:]` / `minors[:]` 规范化为
`all`。`baseline` 是本 Cell full PASS baseline 的精确版本；`declaration` 是同名 active base/extra direct
声明中全部 `>` / `>=` 端点的最大值，不把严格端点改为后继版本，也不包括 harness 或不活跃 marker。
精确端点未发布但系列存在仍可定位；`~=` 等 fixed 资格保持不变。

偏移作用于 registry 中已存在的有序系列列表，稀疏版本号不占位。majors key 为 `(epoch, major)`，固定在
anchor 的同一 epoch；minors key 为 `(epoch, major, minor)`，固定在同一 epoch/major。多个 anchor 跨 scope
报错。单点越界为空，不作负索引回绕；切片端点夹到 `[0,len]`，反向切片为空。系列在 yanked、prerelease、
兼容性、artifact、声明限制、baseline cap 和 space/resolution 过滤前建立，不可用系列仍占位。

## 3. 条件默认与逐依赖配置

省略 space 按 dependency × Cell 选择完整 `search-space-defaults` 表：

```toml
[tool.pf.search-space-defaults]
with-lower-bound = "majors[declaration-1:]"
without-lower-bound = "majors[baseline-2:]"
```

这也是内建默认；显式 `all` 与省略不同。提供表时两项必填，只能用 DSL，未知键/类型无效，
`without-lower-bound` 不得引用 declaration。root → member → dep 按完整对象替换，raw layer 即使被覆盖
也先验证。有效 space 优先级为 dep explicit → global explicit → dep defaults → global defaults → builtin；
defaults 的合并独立于显式 space，完整表仍进入报告授权。只覆盖 resolution 不改变省略状态。

`[[tool.pf.dep]]` 按 canonical dependency name 绑定 space/defaults/resolution/prereleases；未列 dependency 使用
global policy。同名重复无效；dep AoT 的 root/member 替换与清空见 D001 §7。逐依赖显式 space 另支持
非空 PEP 440 specifier，不得含 extras、marker、URL 或 source。空间可以不含 baseline；有效空间筛空或
无 PASS 时是 `NO_PASS_IN_SEARCH_SPACE`，不能用空间外 baseline 冒充 floor。

## 4. 空间准入与错误边界

Search/minimize 在 snapshot/Attempt 前检查全部 declared Cells（含非宿主）的 declaration anchor 前提，
不满足为 ConfigurationError。Smoke/check 只验证语法，不消费 anchor。取得 registry 后无法定位
anchor 系列或跨 scope 是 SearchSpaceResolutionError，整个 Run 不写报告（D008）；数值退出码只见 D001 §8。source failure 仍按
Indeterminate 处理。空间内 resolve/install 的 build failure 按 D005 的执行契约分类。

默认只限制探索系列，不承诺验证成本、最低 patch 或兼容性。用户可按发布习惯配置默认表/显式范围/resolution：
`majors[baseline] + major` 仅取一个最高合格代表；`minors[baseline] + patch` 探测该 minor 内各 patch。
PF 不自动推断发布习惯或替用户改策略。

## 5. Baseline artifact 选择域

每个 CandidateSnapshot 的 `candidates` 是唯一搜索序列 `C[d]`；同一次成功 registry query 还必须冻结
required `baseline_selection`，保存本 Cell 已直接通过的 highest baseline `B[d]` 的精确 public artifact。
完整 exact-vector 的可选择域是 `S[d] = C[d] ∪ {B[d]}`：空间外 baseline 只为其他坐标下降时闭合安装选择，
不进入候选顺序、系列代表、窗口、predecessor、boundary 或 floor。`B[d]` 同时属于 `C[d]` 时两者 artifact
必须完全一致。baseline selection 不重新应用 search-space、resolution、prerelease、yanked 或代表采样资格，
但仍须满足当前 Cell 的 artifact policy、locator 与 SHA-256；同次观测无法闭合时为 candidate-discovery
`SOURCE_FAILURE`/Indeterminate，不扩大 `C[d]`、不重查 source，也不让 uv 自由解析该坐标。

## 6. Resolver policy 边界

候选的 `search-prereleases` 与 resolver prerelease mode 分属不同 owner；uv 配置、override 禁令与
resolution context identity 统一见 [D012 §6](D012-pf-harness-relaxation.md#6-interface-与-identity)。

## 7. 消费与验证边界

`ConfigLoader` 验证语法，`ProjectLoader` 绑定 named policy，Search workflow 执行全部 declared Cells
的 anchor admission；`CandidateBuilder` 从同次 registry 观测冻结结果，report reader 从保存的事实
重新派生。本节仅导航实现，模块接口由 D002、离线可证明范围与 identity preimage 由 D014 定义。

公开验证入口为 `tests/test_search_space.py`、`tests/test_candidates.py`、`tests/test_uv_adapter.py`、
`tests/test_search_space_workflow.py` 与 `tests/test_search_space_report.py`。语法、稀疏系列/epoch/scope、
空空间、artifact 准入、空间外 baseline、CLI 覆盖和离线复算均沿原规则验证；本次拆分不产生新的性能证据。

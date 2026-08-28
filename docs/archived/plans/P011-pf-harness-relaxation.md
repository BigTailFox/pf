# P011 — D012 Harness Resolution 实施记录

- **状态：** 已归档（已完成）
- **开始日期：** 2026-08-23
- **完成日期：** 2026-08-23
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D012](../../designs/D012-pf-harness-relaxation.md)
- **依赖：** [P010](P010-pf-runtime-backed-static-search.md)
- **实施基线：** `c4e0cf4`（`feat: implement runtime-backed static search`）

本文先于生产代码变更建立 D012 的实现切片、依赖顺序、测试矩阵和证据记录。每次实质行动后在 §7 追加“行动、目标、结论、证据”；完成标准只来自 D012 §18，本文不新增或缩小产品契约。

## 1. 目标与边界

本轮完整实现 D012：

- 把展开后的 test dependency group 从裸字符串提升为带 declaration/group/source provenance 的结构化 `HarnessRequirement`；
- 对 relaxed Attempt 只删除 eligible direct registry requirement 的显式 `>` / `>=` clause，并独立施加 baseline direct ceiling；
- 建立一次 Verification Run 内共享的 `ResolutionContext`，固定受支持 uv protocol、source policy、cell marker/tag 输入、release cutoff 和 cache policy；
- 用经校验的 `pylock.toml` 分别表达 `ProjectResolutionPlan(P)` 与 `EnvironmentResolutionPlan(P)`；
- 每个 Attempt 先解析 `G(P)`，再在 `Exact(G(P)) + D_H` 下解析最终环境，只安装 final plan；
- 将 uv resolution outcome 判别为 `ResolutionPlan | ResolutionUnsat | ResolutionIndeterminate`，只有认证过的完整 incompatibility 才形成 `HARNESS_CONFLICT / REJECTED`；
- 让 baseline/smoke/declaration-capture 使用原始 harness，让 search exact probe 与 check lowest-direct 使用携带 `HarnessBaseline` 的 relaxed request；
- 以 `EnvironmentIdentity` 隔离 static/witness/test cache，并让失败只保存失败发生前已取得的 plan evidence；
- 同步 D012 §17 指定的现行所有者条款、文档状态和索引。

不扩展 `CandidateBuilder` 为 transitive catalog，不搜索 harness version，不建立离线 mirror，不解释 uv 私有 cache，不把 artifact alternative 变成搜索坐标，也不以安装、构建或普通测试失败推导 resolver UNSAT。

## 2. 基线事实与差距

| 范围 | `c4e0cf4` 当前事实 | D012 目标状态 |
| --- | --- | --- |
| harness declaration | `PackagePlan.test_requirements: tuple[str, ...]`；group 展开后 provenance 丢失 | `HarnessRequirement` 保存 declaration identity、root/package group provenance、canonical name、extras、structured specifier、marker、source 与 original text |
| request 类型 | `HighestResolution` / `LowestDirectResolution` 无 harness context；`ExactSelection` 只保存 project artifact selection | relaxed 变体在类型层面必须携带 `HarnessBaseline`；不得表达 highest + relaxed 或 exact/lowest + 缺 baseline |
| prepare 顺序 | 建 venv → `uv pip install -e` 并解析/安装 → inspect → `uv pip install` harness 并再次解析/安装 | resolve project → resolve final environment → create venv → install validated final plan → inspect；只有 final plan 被安装 |
| graph identity | `ResolvedNode` 只有 name/version/dependencies；Proposal 使用首次 inspect graph | 两个 validated plan 分开保存；registry/source/artifact evidence、`G(P)`、`E(P)` 与 `EnvironmentIdentity` 可复证 |
| harness baseline | baseline prepared environment 不暴露 direct harness selection 或 ceiling | baseline 从原始 environment plan 捕获 direct selection 与 `U_B`，供同 cell relaxed Attempt 使用 |
| resolver context | uv version、cutoff 与 cache 未进入 request identity；各命令可隐式使用默认 cache | 一次运行固定精确 uv protocol、source policy、cutoff 和共享 cache；相同 input 只解析一次 |
| uv success | `uv pip install` 成功后只 inspect 已装环境 | `uv pip compile --format pylock.toml` 产生 Schema 校验后的 native/normalized plan；installer 消费 plan 且禁止 plan 外 resolution |
| uv failure | `_classify` 对 stderr phrase 做宽松归类；`no solution found` 可直接成为 conflict | versioned diagnostic profile 只认证完整 incompatibility；截断、未知、candidate/source/artifact/build ambiguity 保持 Indeterminate |
| evaluation cache | key 是 `proposal_id + baseline_digest`；Proposal identity 只覆盖首次 project graph | Proposal/`EnvironmentIdentity` 覆盖最终环境，cache 不跨不同 environment plan 命中 |
| candidate catalog | 只查询受管 project direct dependency，已有 provider query memoization | 保持当前边界，并补反例测试证明不读取 harness/transitive package |

## 3. 实现模型

### 3.1 Structured harness 与纯变换

在 `schemas/project.py` 建立不可变的 `HarnessRequirement`、`HarnessSpecifierClause` 与 group provenance。`ProjectLoader` 在 root/package 各自展开 `include-group` 时保留来源路径，并使用既有 source parser 生成每条 direct declaration；不同 provenance 的同名 requirement 不去重，交给 uv 求交集。

新增纯函数 `relax_harness(requirements, baseline) -> RelaxedHarness`：以 baseline cell 按 marker 选出 active declaration，再对非 fixed registry declaration 删除 structured clause 中的 `>` / `>=`，最后对所有 ceiling-bound direct registry distribution 追加 `<=U_B[name]`。函数保留原 extras、marker、source、exclusion、upper/equality-like clause 与预先确定的 prerelease admission policy；精确 equality、arbitrary equality 和固定 source 不追加 ceiling。

### 3.2 Resolution contract、context 与 plan

在 environment ownership 下建立：

- `ResolutionContext`：精确 uv version、adapter protocol、source policy identity、resolution/prerelease/yanked policy、cell target/Python/tag 输入、release cutoff、cache policy identity；
- 判别 `ProjectResolutionRequest` / `EnvironmentResolutionRequest`；
- `ResolutionPlan`：normalized nodes/direct selections、validated native `pylock.toml` 与 digest、request/context identity；
- `ResolutionUnsat`：仅保存 qualification profile 认证的 incompatibility proof 与完整 diagnostic digest；
- `ResolutionIndeterminate`：保存 source/tool/candidate-availability 等不能证明 satisfiability 的事实；
- `InstalledResolution` / `InstallFailure`：installation 只消费 validated final plan。

`AttemptIdentity` 只覆盖任何外部操作前已知的 request、context、原始 harness、relaxation policy 和可用 `U_B`；`EnvironmentIdentity` 在两个 plan 成功后建立，覆盖 normalized plan、最终 graph 和可靠可得的 artifact evidence。`PreparedEnvironment` 同时持有两个 plan、harness baseline（baseline Attempt）和 environment identity。

### 3.3 uv 0.12.5 垂直切面与十版本 adapter protocol

先以当前工程环境的 `uv 0.12.5` 打穿垂直切面。并行 qualification agent 在隔离临时目录对截至实施日最近十个稳定 uv 版本运行同一输出矩阵；最终 adapter 的受支持版本集和每版本 diagnostic profile 必须以该矩阵证据为准，而不是假定相邻 patch 的输出兼容。adapter 使用 `uv pip compile --format pylock.toml` 做 cell-specific resolution，并显式传入 Python minor、target triple、resolution strategy、release cutoff、cache 与无 refresh 策略；只读取 output file，不把成功的人类输出当 plan。

project compile 输入是当前 source snapshot 的 editable project metadata 及 exact selected candidate evidence；environment compile 复用相同 project input，以 project plan 生成的 exact constraints 加入 original/relaxed direct harness。native final `pylock.toml` 自身包含 editable project；安装阶段只执行一次 `install(plan, environment)` / `uv pip sync`，不再运行额外 editable 或 harness install，也不开放 plan 外解析。

adapter parser 校验 lock version、created-by、request identity、package name/version/source、hash/locator shape、唯一性和完整 graph，再投影 normalized plan。若 pylock 只暴露 alternatives 而未可靠声明当前 selected artifact，则只保存 native provenance，不猜测 selected evidence。

### 3.4 Qualification profile 与保守分类

新增不经过 `EnvironmentFactory` 或真实项目 workflow 的 qualification runner 和 fixture table。runner 对每个候选 uv 版本执行 D012 §9.1 的统一矩阵；profile 记录 command、exit facts、stdout/stderr completeness、diagnostic signature、PF classification 与 confidence。

生产 classifier 只允许 profile 明确认证、stdout/stderr 完整、精确 version/protocol 匹配的 pure/transitive version contradiction 形成 `ResolutionUnsat`。package/version unavailable、wheel unavailable、`requires-python` mismatch、offline miss、source/auth/transport/metadata、hash、build、截断和未知 shape 全部返回相应 `ResolutionIndeterminate`。退出码和单个 substring 永不单独授权 rejection。

### 3.5 Baseline 传播、缓存与 workflow

`HighestVersionVerifier` 从原始 final plan 捕获 `HarnessBaseline`；search 把它传给每个 `ExactSelection`，check declaration-capture 把它传给 `LowestDirectResolution`。smoke 只消费原始 baseline，不创建 relaxed request。

`EnvironmentFactory` 对相同 `(ResolutionRequest, ResolutionContext)` memoize validated plan；`_ProposalRunner` 继续按 project vector 复用单个 `PreparedEnvironment`。Proposal identity 改由 `EnvironmentIdentity` 定义，因此现有以 Proposal 为首键的 static/full cache 自动隔离 harness environment；Schema 加入显式交叉 identity 校验。

## 4. 实施顺序

### 切片 001 — structured harness、baseline 与 relaxation 纯函数

1. 先为 root/package include provenance、marker、source 与同名多声明写 Red 测试；
2. 建 `HarnessRequirement` / specifier clause / baseline selection Schema；
3. 迁移 `ProjectLoader` 和 `PackagePlan`，删除裸字符串重解析入口；
4. 用参数化矩阵实现并验证 fixed / relaxable / ceiling-bound 三个独立判定及 prerelease policy 保持；
5. 执行 project、schema 和 pure transformation 回归。

### 切片 002 — uv qualification runner 与判别 resolution seam

1. 建立独立 fixture server/package/index 与 qualification matrix runner；
2. 对 `uv 0.12.5` 记录 pure/transitive contradiction 和全部 abnormal cases；
3. 建 `ResolutionContext`、request/outcome/plan/native-plan Schema 与 identity 校验；
4. 实现 versioned diagnostic profile，只对白名单完整 signature 返回 `ResolutionUnsat`；
5. 执行 qualification、failure、schema 和 adapter classifier 测试。

### 切片 003 — pylock resolve/install adapter

1. 为 project/environment compile argv、cutoff/cache/target/Python/strategy 写 Red 测试；
2. 实现 pylock parser、normalized graph/source/artifact projection 与 native digest；
3. 实现 project/environment resolve input materialization和 exact project graph constraints；
4. 实现 `install(plan)`：创建 venv 后只消费 final lock，再无依赖解析地安装 editable project；
5. 验证成功、坏 schema、input drift、hash/source mismatch、安装 artifact/source/build failure 路由。

### 切片 004 — EnvironmentFactory 两次解析/一次安装与 identity

1. 把 `prepare` 迁移为 materialize → resolve project → resolve environment → create → install final → inspect；
2. 在安装前验证 `G(P) ⊆exact E(P)`，安装后复证实际 graph 等于 final plan；
3. baseline 捕获 `HarnessBaseline/U_B`；relaxed request 类型强制携带同 cell baseline；
4. 建 `EnvironmentIdentity`，迁移 Proposal、PreparedEnvironment、FailureRecord 已取得证据和 resolution memoization；
5. 执行 environment、evaluation/cache、failure 与 lifecycle 回归。

### 切片 005 — check/search/smoke 与搜索处置

1. CompatibilityChecker 传递 declaration-capture 的 `HarnessBaseline` 到 lowest-direct；
2. HighestVersionVerifier/HighestVersionPass 保存 baseline harness evidence，SearchCoordinator 传给每个 exact probe；
3. 认证 environment UNSAT 映射 `HARNESS_CONFLICT / Rejected`，project UNSAT 映射 `RESOLUTION_CONFLICT / Rejected`；其他 resolution/install failure保持 Indeterminate；
4. 验证 `CoordinateSearch` 只把 certified conflict 当完整 Proposal rejection，且 D011 static-only observation 资格不变；
5. 执行 check、baseline/smoke、search/coordinator/workflow/journal/terminal 回归。

### 切片 006 — 所有者同步、验收审计与最终门禁

1. 同步 D012 §17 的 D001/D002/D003/D005/D008/D010/D011 条款和 module layout；
2. 把 D012 改为现行、P011 改为已完成并更新索引；
3. 对 D012 §18 二十三项逐项回填实现与测试证据；
4. 执行 `ruff`、`ty`、Python 3.10–3.12 全量 pytest（显式 `--no-testmon`）、build 和真实安装/qualification 集成；
5. 检查 `git diff --check`、文档链接、工作树范围和最终状态。

## 5. 验收与测试矩阵

| D012 §18 | 切片 | 主要测试位置 | 直接证据 |
| --- | --- | --- | --- |
| 1 | 001、005 | `test_project.py`, `test_check.py`, `test_baseline.py`, `test_smoke.py` | baseline/capture 原始 declaration；probe/lowest relaxed |
| 2–6 | 001 | 新 `test_harness.py`, `test_project.py`, `test_schemas.py` | clause 矩阵、ceiling、fixed/source、prerelease policy |
| 7–10 | 003、004 | `test_uv_adapter.py`, `test_environment.py`, `test_schemas.py` | resolve 两次、install 调用一次、pylock 校验、artifact/source projection |
| 11 | 003、004 | `test_environment.py` | harness-only transitive 可增删/变版且不影响 project exact graph |
| 12 | 002、003 | `test_uv_adapter.py`, `test_environment.py` | exact uv/protocol/source/cutoff/cache identity 固定 |
| 13 | 001、005 | `test_candidates.py`, `test_project.py` | candidate provider 只收到 managed project direct name |
| 14 | 003、004 | `test_environment.py`, `test_evaluation_cache.py` | 同 input resolve once；static/witness/test 不触发 source |
| 15 | 002 | 新 `tests/uv_qualification/`, 新 `scripts/qualify_uv.py` | 独立 matrix 与 versioned profile，不经过 workflow |
| 16–18 | 002、005 | `test_uv_adapter.py`, `test_failure.py`, `test_search_coordinator.py` | certified UNSAT rejection；ambiguous/source/tool outcomes indeterminate |
| 19–20 | 003、005 | `test_uv_adapter.py`, `test_environment.py`, `test_failure.py` | plan 后 artifact/source failure；harness-only build indeterminate |
| 21 | 005 | `test_check.py`, `test_evaluation.py` | lowest relaxation/declaration rejection；test failure cause 不变 |
| 22–23 | 004、005 | `test_schemas.py`, `test_evaluation_cache.py`, `test_search_coordinator.py` | Attempt/Environment identity 时点、cache 隔离、failure 不虚构、static-only 不拒绝 |

每个切片先跑最窄测试，再跑相邻模块；切片 004 起执行主要 workflow 回归。最终用 `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.10|3.11|3.12 pytest --no-testmon -q` 明确全量选择。restricted network、coverage gate 与 code failure 分开记录，不因网络不可用削弱 profile 或测试。

## 6. 变更控制

- 当前工作树在本计划建立前已有 D012 与 `docs/README.md` 的已批准设计修订；它们保留为权威输入，不归因于本计划，也不被覆盖；
- 若 uv 0.12.5 的实际结构化能力不能满足 D012，先记录协议证据并修改 D012 或请求语义决定，不能用 stderr 猜测或退回两次开放安装；
- 不为旧 `test_requirements`、两次安装或 phrase-based conflict classification 增加长期兼容写层；内部测试 fixture 同步迁移；
- `CandidateBuilder` 的 public seam 保持不变；D012 implementation detail 不泄漏进 `CoordinateSearch`；
- 任何切片只有在对应验收反例和正例都通过后才能标记完成；最终全量测试不能替代逐项审计。

## 7. 过程记录

### 基线审计与 Plan

- **状态：** 已完成
- **行动：** 对照 D012 全文、其 §17 链接的现行契约、`environment.py`、`adapters/uv.py`、project/evaluation/failure/search/workflow Schema 与主要测试，建立本 Plan 后再开始生产代码变更。
- **目标：** 证明实现范围覆盖 D012 的 23 条验收标准，并确定不会保留旧两次开放安装语义的依赖顺序。
- **结论：** 现有 project CandidateSnapshot/search/D011 runtime evidence 可以保持；structured harness、run context、两类 resolution plan、final-plan installer、certified diagnostic、baseline 传播和 environment-scoped identity 均是新增工作，不能通过局部字符串改写完成。
- **证据：** `git log --oneline -8` 顶部为 `c4e0cf4`；计划前 `git status --short --branch` 显示 `main` ahead 1，且只有预存的 `docs/README.md`、D012 修改；源码检索确认生产代码仅有 `PackagePlan.test_requirements`、`install_editable`、`install_requirements` 和 phrase-based `_classify`。

### uv protocol 可行性预检

- **状态：** 已完成
- **行动：** 检查当前 uv 精确版本及本地 help，并用无依赖临时 project 试运行 cell-specific `uv pip compile --format pylock.toml`。
- **目标：** 在 adapter 设计前确认 pylock 输出、target/Python/cutoff 参数和成功输出边界真实存在。
- **结论：** 当前版本为 `uv 0.12.5`；`uv pip compile` 支持 `pylock.toml`、`--python-version`、`--python-platform`、`--resolution`、`--exclude-newer`、constraints 与 cache 选项；成功会写 `lock-version = "1.0"` / `created-by = "uv"`。真实仓库 compile 在 restricted network 下失败，不能作为代码失败或 qualification 结果。
- **证据：** `uv --version` → `uv 0.12.5 (x86_64-unknown-linux-gnu)`；临时无依赖 project compile → exit 0 并生成合法空 pylock；`UV_CACHE_DIR=/tmp/pf-uv-cache uv pip compile pyproject.toml ...` 对真实仓库 → exit 2，根因为 sandbox 阻止访问 `https://pypi.org/simple/pydantic/`。

### 十版本 uv 输出嗅探

- **状态：** 已完成
- **行动：** 按追加范围启动一个独立 agent，只在 `/tmp` 构建隔离的 uv qualification 环境，对最近十个稳定版本执行统一的 contradiction、candidate availability、source、metadata、hash、build 与 offline 矩阵；主实现先用当前 `uv 0.12.5` 打穿 resolve/install 垂直切面。
- **目标：** 以真实完整 stdout/stderr 和命令阶段证据决定 PF 的精确受支持版本集及 versioned diagnostic profile，避免从单一当前版本外推。
- **结论：** 精确支持 `0.12.5`、`0.12.4`、`0.12.3`、`0.12.2`、`0.12.1`、`0.12.0`、`0.11.33`、`0.11.32`、`0.11.31`、`0.11.30`。13 个 case 在十版本上各自只有一个归一化 shape；仅 pure/transitive version contradiction 可认证 UNSAT。package/version/platform/Python candidate unavailable 保持 TOOL Indeterminate；401/403/timeout/metadata/hash/offline 保持 SOURCE Indeterminate；sdist backend 保持 BUILD Indeterminate。hash mismatch 必须先成功 compile、篡改 pylock，再在 sync 阶段观察。
- **证据：** 独立 agent 在 `/tmp/pf-uv-matrix-agent` 完成 `10 versions × 13 cases = 130` 次真实命令，保存逐版本 binary SHA-256、raw/normalized stdout/stderr 与 classifier evaluation，130/130 符合预期；仓库提交面保存 `tests/uv_qualification/matrix-manifest.json`、`scripts/qualify_uv.py` 及 manifest/runner 测试，不提交大体积临时 raw artifacts。

### 切片 001 — structured harness、baseline 与 relaxation 纯函数

- **状态：** 已完成
- **行动：** 新增带 root/package/include item path 的 `HarnessGroupProvenance`、结构化 `HarnessSpecifierClause` / `HarnessRequirement`、direct selection/`HarnessBaseline` 与 `RelaxedHarness` identity；`ProjectLoader` 在 group 展开时保留每条 declaration，不再把同文本去重；新增 `harness-relaxation-v1` 纯函数和 renderer，生产环境的旧 harness 入口暂时只消费结构化记录的 `original_text`，等待切片 003/004 替换。
- **目标：** 先让 relaxation、baseline ceiling、source 与 declaration identity 有唯一结构化所有者，并直接覆盖 D012 §18.1–6 的纯语义。
- **结论：** 非 fixed registry declaration 均独立 ceiling-bound；只有其中含显式 `>` / `>=` 时才删除 minimum。精确 `==X`、`===X` 与 URL/Git/path/workspace source fixed；`~=X`、`==X.*`、无 specifier 和仅 upper/exclusion 保留原 clause 并追加 ceiling。marker-inactive declaration 不进入 cell baseline/relaxation；原 requirement/resolver 已确定的 prerelease admission 保存在 declaration，不因删除 lower clause 重算。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_harness.py tests/test_project.py tests/test_environment.py tests/test_schemas.py -q` → 177 passed；同范围 `ruff check` 与 `ty check` 均通过；`rg test_requirements src tests` 无输出；`git diff --check` 通过。

### 切片 002 — qualification、resolution contract 与 versioned diagnostic

- **状态：** 已完成
- **行动：** 建立固定十版本的 `ResolutionRunContext` / protocol/profile mapping、normalized/native `ResolutionPlan`、`ResolutionUnsat | ResolutionIndeterminate`、semantic/native identity，以及只对白名单完整 contradiction 开放的 diagnostic classifier；qualification runner 建立 13 类受控 package/index/artifact fixture。
- **目标：** resolution success 只来自 machine-readable plan，negative proof 只来自真实矩阵认证的完整 outcome。
- **结论：** 十版本共用 `uv-pip-compile-pylock-v1` 与一个认证 shape set，但每个精确版本拥有独立 profile identity；未支持版本、输出截断、未知 shape 和 candidate availability 不会形成 Rejection。
- **证据：** `tests/test_resolution.py`、`tests/test_uv_diagnostics.py`、`tests/test_uv_qualification.py` 覆盖精确 allowlist、profile mismatch、direct/transitive × 10、Indeterminate 分类、manifest 130 executions 与 runner absolute binary/version injection；独立 agent matrix 130/130。

### 切片 003 — pylock resolve/install adapter

- **状态：** 已完成
- **行动：** 新增 `uv_lock.py` 校验 lock/version/creator/Python/cell fork、完整 dependency graph、source、hash 与唯一 package；UvAdapter 固定绝对 uv executable，一次建立 run context，分别 compile project/environment pylock，final plan 只 sync 一次，并以 `InstalledResolution | InstallFailure` 绑定 plan digest。
- **目标：** 打通当前 uv 的两次结构化解析/一次 plan 安装垂直切面，同时让 installation 在类型和分类上都不能产生 resolver conflict。
- **结论：** registry artifact alternatives 只留 native provenance；direct archive 等 uv 可靠暴露的 selection 进入 normalized evidence。explicit harness prerelease admission 在 lower clause 删除后仍进入 uv candidate policy。hash/source/build/install failure 保持 Indeterminate。
- **证据：** adapter/lock/diagnostic/qualification 聚焦回归通过；`test_uv_adapter_resolves_two_pylocks_and_syncs_only_the_final_plan` 断言两次 compile、一次 sync、零次 pip install；当前 `uv 0.12.5` 的最小本地包真实 check 已在 network-enabled 复验中通过一次。

### 切片 004 — EnvironmentFactory、identity 与失败证据

- **状态：** 已完成
- **行动：** prepare 改为 materialize → project plan → original/relaxed harness → environment plan → exact inclusion → create → one sync → inspect exact graph；新增 run/request memoization、`EnvironmentIdentity`、Attempt v2、PreparedEnvironment 双 plan/baseline 和 FailureRecord acquired plan digests。
- **目标：** 消除两次开放安装及中间 graph drift，把 request-level 与 post-resolution evidence 分开，并让 cache 以最终环境隔离。
- **结论：** 安装前比较 project name/version/source/可靠 selected artifact；安装后实际 package 集合与版本必须精确等于 final plan 加 editable root，额外或缺失节点都触发 internal invariant。失败只保存当时已取得的 project/environment plan identity。
- **证据：** environment/evaluation/failure/adapter/baseline/check/search 聚焦回归最高一轮 180 passed；Ruff 与 `ty check src` 通过；新增 missing/extra/drift graph、plan evidence 时点、install outcome、memoization 与 v2 identity 断言。

### 切片 005 — baseline 传播、workflow 与搜索处置

- **状态：** 已完成
- **行动：** HighestVersionPass 保存 `HarnessBaseline`；check lowest-direct 与每个 search exact probe 强制接收它；failure policy 只认可两类 certified resolution stage、witness 和 test rejection；terminal 加入 resolve/final-plan stage 文案。
- **目标：** baseline/capture 保持 original harness，relaxed request 不可缺 baseline；build/install/source 不误建搜索拒绝边界，D011 static-only 资格不变。
- **结论：** project UNSAT → `RESOLUTION_CONFLICT`，environment UNSAT → `HARNESS_CONFLICT`；其他 prepare failure 为 Indeterminate。Proposal ID 是 EnvironmentIdentity，因此既有 cache key 自动按 final environment 隔离。
- **证据：** 相关 workflow/failure/search/evaluation 聚焦回归 156 passed 与 180 passed；首次全仓回归 678 passed，另 3 个真实安装用例因 restricted network 访问 PyPI/`uv_build` 失败，留给最终 network-enabled 门禁复验。

### 切片 006 — 所有者同步、验收审计与最终门禁

- **状态：** 已完成
- **行动：** 同步 D001/D002/D003/D005/D008/D010/D011 的消费契约，将 D012 与 P011 转为现行/已完成；补齐相对 pylock directory 的 lock-root 语义、CandidateBuilder 重复 query cache、UvAdapter 跨 cell raw source response cache，以及 harness-only transitive graph 变化的直接验收用例。
- **目标：** 逐项关闭 D012 §18 的 23 条标准，并在真实 current-uv vertical slice、十版本 qualification、三 Python 版本全量回归和 build 上形成互相独立的证据。
- **结论：** 当前 `uv 0.12.5` 的最小本地 package 已真实通过 project/environment 两次 pylock resolution、一次 final-plan sync、installed graph inspection 和 test；十个精确 uv 版本全部映射到独立 profile，未登记版本 fail closed。首次联网复验暴露的 relative `directory.path` 缺陷已经按锁文件目录解析并回归。
- **证据：** current uv 13-case qualification 全部 `expected=true`；独立矩阵 `10 × 13 = 130` 全部输出完整且分类符合预期；Python 3.10/3.11/3.12 隔离全仓各 `693 passed`；Ruff、`ty check src`、`uv build` 与 `git diff --check` 通过。§9 记录逐项验收证据。

### 运行回归修复 — smoke/check 自验证

- **状态：** 已完成
- **行动：** 用真实 `pf smoke --jobs 1` / `pf check --jobs 1` 建立回归环；修正 environment resolution 错误继承 project `lowest-direct`、单 cell pylock package marker 被误拒、当前 ty 暴露的测试夹具类型漂移，并校准仓库自身 Cyclopts/Pydantic/Tomlkit 与 pytest-env 下界。Packaging 25 对 `===vendor` 的 prerelease 推断异常改为保守的非 prerelease，而不抬高无关下界。
- **目标：** 让 D012 的“只放松 harness 约束、harness 仍按默认 highest 选择”在 adapter argv、pylock projection 和 PF 自验证中同时成立。
- **结论：** project compile 独立使用 Attempt strategy；environment compile 在 `Exact(G(P))` 约束下固定使用 highest。uv 为 Python 3.10 输出的 active package marker 属于合法 final plan evidence，parser 规范化并保留该 marker。
- **证据：** adapter strategy 与 marker 两个回归测试先 Red 后 Green；`pf smoke --jobs 1` 与 `pf check --jobs 1` 在 Python 3.10/3.11/3.12 三个 cell 均通过；聚焦回归 148 passed、host 全量 693 passed，Ruff、ty、`uv lock --check`、`uv build` 与 `git diff --check` 均通过。

## 8. 实现取舍记录

实施过程中只记录不改变 D012 产品语义的内部取舍。若取舍会改变验收或所有权，必须先更新 D012，而不是只写在这里。

## 9. 最终验收审计

| D012 §18 | 状态 | 实现证据 | 测试/运行证据 | 未决风险 |
| --- | --- | --- | --- | --- |
| 1 | 通过 | `ProjectLoader` 保留结构化原始 declaration；highest/capture 走 `original_harness`，lowest/exact 强制 baseline relaxation | `test_original_harness_keeps_active_declaration_semantics`、check/search/smoke baseline 回归 | 无 |
| 2 | 通过 | `relax_harness` 只删除 `>`/`>=` 并独立追加 direct ceiling | `test_explicit_minimum_is_removed_and_baseline_ceiling_is_added` | 无 |
| 3 | 通过 | fixed 判定覆盖精确 equality 与 URL/Git/path/workspace；非 minimum clause 原样保留 | `test_non_minimum_clauses_follow_independent_ceiling_policy`、`test_fixed_sources_are_retained_without_ceiling` | 无 |
| 4 | 通过 | ceiling-bound 与 relaxable 是独立 policy 位 | `test_non_minimum_clauses_follow_independent_ceiling_policy` 参数矩阵 | 无 |
| 5 | 通过 | declaration 保存 `prerelease_allowed`，adapter 在 compile argv 显式传递 | `test_removing_prerelease_minimum_keeps_admission_policy`、adapter prerelease argv 断言 | 无 |
| 6 | 通过 | `HarnessBaseline` selection 生成 `<=U_B`，environment direct selection 与 request/plan 交叉校验 | harness ceiling 参数矩阵与 environment plan Schema 回归 | 无 |
| 7 | 通过 | `EnvironmentFactory.prepare` 顺序为 project resolve → environment resolve → create | `test_environment_reports_prepare_stages`、`test_uv_adapter_resolves_two_pylocks_and_syncs_only_the_final_plan` | 无 |
| 8 | 通过 | installer 只消费 environment native plan；安装前 exact inclusion，安装后 exact graph equality | 同上；missing/extra/drift installed graph 三组反例；真实最小 package check | 无 |
| 9 | 通过 | `parse_uv_pylock` 校验 machine-readable plan；`InstallOutcome` 绑定 plan digest | `test_success_with_an_invalid_native_plan_is_indeterminate`、`test_install_outcomes_are_bound_to_the_validated_plan` | 无 |
| 10 | 通过 | normalized plan 保存 source/version/reliable selected artifact；alternatives 只留 native/provenance | `test_parser_projects_registry_graph_and_secret_free_artifacts`、direct archive 与 exact artifact selection 回归 | registry alternatives 不猜测 selected artifact，符合契约 |
| 11 | 通过 | project plan 与 harness-only final graph 分离 | `test_environment_allows_harness_only_transitive_graph_to_change` 证明节点消失/出现且 project graph/vector 不变 | 无 |
| 12 | 通过 | `ResolutionRunContext` 固定 exact uv/profile/protocol/cutoff/cache，`ResolutionContext` 加 source/cell policy | `test_run_context_supports_exactly_the_ten_qualified_uv_versions`、context identity 反例 | 无 |
| 13 | 通过 | CandidateBuilder 只遍历 active managed project direct names并缓存重复 cell query；UvAdapter 按 source/package 缓存 raw response后按 cell 投影 | `test_candidate_builder_queries_only_active_managed_project_dependencies`、`test_candidate_query_memoizes_raw_source_response_across_cells` | 无 |
| 14 | 通过 | `EnvironmentFactory._resolve_once` 按 request/context memoize；PreparedEnvironment 被 static/witness/test 复用 | `test_environment_factory_resolves_identical_inputs_only_once` 与 coordinator prepared reuse 回归 | uv 私有 cache 仅由 uv 管理 |
| 15 | 通过 | 独立 `scripts/qualify_uv.py` + committed manifest/profile mapping，不经过 workflow | current uv 13/13；agent 实验 10 versions × 13 cases = 130/130 | 诊断矩阵当前实测 host scope 为 Linux x86_64；其他未认证版本 fail closed |
| 16 | 通过 | certified direct/transitive contradiction → ResolutionUnsat → HARNESS_CONFLICT rejection | 10-version diagnostic 参数矩阵、`test_environment_maps_certified_harness_unsat_before_installation`、search rejection boundary 回归 | 无 |
| 17 | 通过 | classifier 先 source/build/candidate，再认证完整 contradiction；输出不完整/未知/version mismatch 保守 | `test_ambiguous_and_abnormal_failures_are_indeterminate`、incomplete/unqualified profile 反例、130-case matrix | 无 |
| 18 | 通过 | resolution source failure 与 unknown diagnostic 分别返回 SOURCE/TOOL Indeterminate | 401/403/timeout/metadata/offline qualification 与 adapter/failure tests | 无 |
| 19 | 通过 | plan 后 install classifier 不产生 resolver cause；empty/missing/hash/corrupt/download 都映射 SOURCE | adapter operation cause 参数矩阵；qualification 的 tampered-pylock hash case | 无 |
| 20 | 通过 | build failure 是 InstallFailure/ResolutionIndeterminate，不进入 rejection stages | `test_install_or_build_failure_does_not_prove_unsat`、sdist backend qualification | 无 |
| 21 | 通过 | check 顺序 original highest → baseline-bearing lowest relaxation；FailurePolicy 保留 HARNESS/TEST 的独立 stage | `test_compatibility_checker_captures_highest_before_testing_lowest_direct`、check cause/disposition 回归 | 无 |
| 22 | 通过 | Attempt v2 只含 request-time evidence；EnvironmentIdentity 由两个 semantic plan + final graph 构造，计划内含 context/ceiling/artifact | Attempt identity validator、environment identity/harness graph differentiation 与 plan identity tests | 无 |
| 23 | 通过 | Proposal ID 采用 EnvironmentIdentity；FailureRecord plan digest 按取得时点写入；D011 rejection gate 未放宽 | `test_failure_record_retains_only_acquired_resolution_plan_evidence`、cache/identity、static-only/rejection 全量回归 | 无 |

# P010 — D011 runtime-backed 静态引导搜索实施记录

- **状态：** 已归档（已完成）
- **开始日期：** 2026-08-23
- **完成日期：** 2026-08-23
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D011](../designs/D011-pf-runtime-backed-static-search.md)
- **依赖：** [P009](P009-pf-v1-architecture.md)
- **实施基线：** `e4e6d17`（`docs: draft harness relaxation design`）

本文先于生产代码变更建立 D011 的实现顺序与测试计划。每个切片完成后在 §7 记录实际行动、结论和可复现证据；完成标准以 D011 §15 为准，本文不另定义产品契约。

## 1. 目标与边界

本轮完整实现 D011：

- 把静态增量从 `STATIC_FAIL` 迁移为 `STATIC_UNCHANGED` / `STATIC_REGRESSION` transition evidence；
- 为完整增量多重集生成 `static-transition-v1` canonical fingerprint；
- 以版本化结构化规则分类 strong/general diagnostic；
- 增加 adapter-owned runtime witness plan、harness、结构化结果与机械 ProcessResult；
- 用固定 Slice 中连续 fingerprint region 引导 probe，同时区分 static frontier 与 committed current；
- 只用直接 runtime evidence 建立边界，只用当前 Proposal 自身完整 `test-command` pass 授权最终 floor；
- 迁移 check/search/report/cache/failure/policy/CLI 证据与 validator；
- 同步 D011 §14 列出的 D001–D008 现行所有者条款和文档索引。

不新增用户 witness/smoke 配置，不解析 diagnostic message，不把 witness `PRESENT` 当 pass，不认证未观察 hole，不改变 D012 草案中的 harness 规则。

## 2. 基线事实与需求映射

| D011 范围 | `e4e6d17` 基线事实 | 必须达到的状态 |
| --- | --- | --- |
| 静态状态 | `StaticPassEvaluation` / `StaticFailEvaluation`；非空增量立即短路 | 所有完整 TyCheck 都形成 transition；非空增量本身无 disposition |
| fingerprint | 只有 baseline `ty_diagnostic_digest(...increment-v2)` | baseline digest 与 ordered incremental multiset fingerprint 分离且版本化 |
| 分类 | 只有 diagnostic identity；没有分类证据 | code allowlist + AST + Proposal/dependency mapping + plan eligibility 全部结构化保存 |
| runtime witness | 无 plan、adapter 或 Schema | 无 shell harness；只认精确目标的 `CONFIRMED_MISSING`，其他结果按 D011 路由 |
| full evaluation | `FullEvaluator` 只接受 `StaticPassEvaluation` 后运行测试 | general/unchanged/PRESENT/NOT_APPLICABLE 都运行 test-command；witness failure 为 unknown |
| 搜索 | 先 static fixpoint，再 fast path/dynamic search | 单一 runtime-backed 搜索；current 只含直接 test pass，frontier 只有静态事实 |
| region | 无 Slice、连续区间或 representative | region 仅在同 cell/snapshot/policy/baseline/active dependency/其他坐标内连续扩张 |
| Probe Schema | static pass 可直接构造 `ProbePass`；static fail 可构造 `ProbeRejection` | static-only observation 无 disposition；ProbePass/Rejection 都闭环到本 Proposal runtime evidence |
| final authority | validator 允许 static-search 中 `StaticPassEvaluation` ProbePass | final vector、Attempt、Proposal、TestEvaluation、ProbePass 必须是同一直接测试事实 |
| policy/cache | `increment-v2`；cache key 只有 proposal + baseline | 分层 key 纳入 static/full/witness policy，旧新证据不可混合 |

## 3. 实现模型

### 3.1 Static transition evidence

在 `schemas/evaluation.py` 建立以下不可变证据：

- `StaticUnchangedEvaluation(status="STATIC_UNCHANGED")`；
- `StaticRegressionEvaluation(status="STATIC_REGRESSION")`；
- 两者都保存 Proposal、完整 TyCheck、baseline digest、完整 incremental、非空 `static_fingerprint`；
- regression 额外保存与每个增量 occurrence 一一对应的 `DiagnosticClassification`；
- capture 使用同一次 baseline TyCheck 建立空增量 unchanged，不重跑 ty。

`static_fingerprint` 只哈希按 DiagnosticIdentity 规范顺序排列的完整增量 identity JSON 列表，域分隔符固定为 `pf:ty-static-state:static-transition-v1\0`。重复 identity 不折叠；Schema 反算 fingerprint，并验证 incremental 是 TyCheck diagnostics 减 baseline 后的有序子多重集。

### 3.2 Strong classifier 与 witness planner

新增 `pf/static_transition.py`，统一拥有版本常量、fingerprint、classification 与 AST planner：

1. strong code 必须命中显式 allowlist；
2. diagnostic 必须来自 snapshot 且 line/column 唯一落到支持的 `Import`、`ImportFrom` 或可解析 Attribute AST；
3. import root 必须按版本化 canonical mapping 唯一对应当前 active managed dependency；
4. 仅为无歧义 `import-module`、`import-symbol` 或 `has-member` 建 plan；否则分类为 general，并保存稳定 reason code。

Planner 只读取规范路径对应的当前 Proposal 源文件和 Proposal graph/受管声明，不使用 message、severity 或模糊匹配。第一版宁可把复杂 alias、动态 owner、相对导入、多个 distribution 候选降级为 general，也不猜测。

### 3.3 Witness adapter 与路由

新增 `pf/adapters/runtime_witness.py`：

- 输入 frozen `RuntimeWitnessPlan`、当前 prepared interpreter/cwd 和 timeout；
- 用 `interpreter -I -c <adapter-owned harness> <canonical-plan-json>` 启动无 shell进程；
- harness 只输出一个 canonical JSON result，adapter 同时要求正常退出、stdout/stderr 完整且无额外输出；
- 精确目标缺失才返回 `CONFIRMED_MISSING`；目标存在返回 `PRESENT`；import side-effect exception、归因变化或无法无歧义判断返回 `NOT_APPLICABLE`；
- timeout、signal、启动失败、截断、非零退出和非法输出返回 `ToolFailure`。

`RuntimeEvaluator`（位于 `evaluation.py`）按固定顺序组合 static → eligible witness → test：只要仍有未被 confirmed-missing 覆盖的增量，就继续 test；任一 confirmed-missing 可产生 `RUNTIME_INTERFACE_MISSING` negative evaluation，但 witness `PRESENT` / `NOT_APPLICABLE` 永不产生 pass。

### 3.4 Region、frontier 与 coordinate commit

`coordinate_search.py` 继续拥有候选顺序、局部单调性和 boundary；新增 runtime-backed evaluator seam，显式传入 active dependency 与其他坐标：

- runner 对每个 Proposal 始终取得自己的完整 static transition；
- region tracker 的 key 包含 cell、snapshot、policy、baseline digest、active dependency、其他坐标和 fingerprint；
- 只有候选序列中已观测相邻点才能扩张同一 region；同 fingerprint 被其他 state 隔开时建立新 region；
- 新 region 的第一点必须取得 runtime result，随后相邻中间点可只记录 `StaticOnlyObservation`，其 guidance 只影响探测方向；
- 任何 floor/current/predecessor/boundary endpoint 在提交前调用 `promote`，取得该精确 Proposal 的 runtime evidence；若 promotion 与 region guidance 不同，清除该点的推断状态并在当前 Slice 重新定界；
- 每次坐标提交后 current 必须指向直接 `test-command` pass；Indeterminate 立即终止 cell；直接观察低版本 PASS、高版本 Rejection 立即返回 `NON_MONOTONIC`。

不再保留 static-search / fast-path / dynamic-search 三阶段。一个 `CoordinateSuccess` 保存 runtime-backed observations、regions、committed vector 与 boundary；static-only observation 不实现 `ProbeEvidence`，不能被 status 比较误当作 pass/rejection。

### 3.5 报告、failure、cache 与策略迁移

- 新增 cause `RUNTIME_INTERFACE_MISSING`，只允许完整 `CONFIRMED_MISSING` witness 在 `witness` stage 形成 Rejection；删除 static regression 的 rejection 资格；
- `PassEvaluation` / `TestFailEvaluation` 接受任一完整 static transition，并保留 witness attempts/results；runtime missing 使用单独 negative evaluation；
- Static cache、Witness cache、Test cache 分离；key 分别纳入 D011 §11 指定 identity，不跨 Proposal 或 policy 复用；region tracker 只保存调度事实；
- `ProbePass` 只接受 `PassEvaluation`；`ProbeRejection` 只接受 test failure、runtime-interface-missing 或既有 prepare rejection；
- `CellSuccess` 只保留单一 runtime-backed coordinate search，并反查 final Proposal 自身 TestPass；
- validator 拒绝 static-only boundary、PRESENT/NOT_APPLICABLE pass、跨 Proposal test 复用、跨 Slice region 和旧 `increment-v2` evidence；
- policy identity 写入 D011 §12 全部版本字段，`FailurePolicy.identity` 随 cause/disposition 规则提升。

## 4. 实施顺序

### 切片 001 — transition Schema、fingerprint 与 cache

1. 先写 multiset/fingerprint/Schema Red 测试；
2. 实现 `static_transition.py` 的 canonical helpers；
3. 迁移 StaticEvaluator/capture/EvaluationCache，删除 STATIC_PASS/STATIC_FAIL；
4. 执行 evaluation、schemas、ty adapter 与 baseline 回归。

### 切片 002 — classifier、planner 与 witness adapter

1. 用真实 AST fixture 覆盖支持/拒绝矩阵，证明 message 改动不影响结果；
2. 建 RuntimeWitnessPlan/Result/negative evaluation Schema；
3. 实现 strong classifier 与保守 planner；
4. 实现无 shell witness adapter 和严格输出协议；
5. 执行 classifier/witness/adapter/schema 测试与 Ruff/ty。

### 切片 003 — runtime evaluator、check 与 baseline/smoke

1. 先把 general regression、unplannable strong、PRESENT、NOT_APPLICABLE 路由测试改成期望 test-command；
2. 实现 static → witness → test 的单一 evaluator；
3. 迁移 FailurePolicy、CompatibilityChecker 与 HighestVersionVerifier；
4. 验证 check lowest-direct、search highest baseline 与 smoke 各自 Attempt 序列；
5. 执行 evaluation/failure/check/baseline/smoke 回归。

### 切片 004 — runtime-backed region/frontier 搜索

1. 为 Slice 隔离、非连续同 fingerprint、first observation、cheap middle 与 promotion 建表驱动 Red 测试；
2. 扩展 CoordinateSearch evaluator seam 和 invocation-local region tracker；
3. SearchCoordinator 删除 static fixpoint/fast path/dynamic rerun，改为单一 runtime-backed search；
4. 对 promotion failure 重新定界，对 Indeterminate/NON_MONOTONIC 立即终止；
5. 执行 search、coordinator、workflow、并发/重入和生命周期回归。

### 切片 005 — 公共报告、终端与持久化迁移

1. 迁移 ProbeObservation/CoordinateSuccess/CellSuccess 结构；
2. 增加 cross-scope、static-only boundary、跨 Proposal pass 与 final authority 反例测试；
3. 更新 report builder、projection、runlog/journal、terminal presentation 和 diagnose；
4. 删除旧 static/dynamic phase 兼容字段与旧 schema 写路径；
5. 执行 schemas/report/runlog/terminal/CLI/end-to-end 相邻回归。

### 切片 006 — 所有者同步、验收审计与最终门禁

1. 同步 D011 §14 指定的 D001–D008 条款、D002 module layout 和 docs index；
2. 把 D011/P010 状态分别改为现行/已完成；
3. 对 D011 §15 十四项逐项建立实现与测试证据；
4. 执行 Ruff、ty、Python 3.10–3.12 全量 pytest、build 和真实安装集成；
5. 把全部行动、结论、命令与结果回写 §7/§9。

## 5. 测试计划

| 验收点 | 主要测试位置 | 必须锁定的断言 |
| --- | --- | --- |
| 1–2 multiset/fingerprint | `test_evaluation.py`, `test_schemas.py` | baseline 抵消、重复重数、五类 digest 稳定互异、空值非法 |
| 3 region scope/连续性 | `test_search.py`, `test_search_coordinator.py` | 不同 Slice 不复用；A-B-A 是两个 region；稀疏点不预合并 |
| 4–5 general/unplannable | 新 `test_static_transition.py`, `test_evaluation.py` | 首点立即 test；只改 message/severity 不改变分类；无法归因不建 plan |
| 6–7 witness routing | 新 `test_runtime_witness.py`, `test_failure.py` | PRESENT/NA 后 test；missing 才 rejection；timeout/bad output/tool failure indeterminate |
| 8–9 cheap/promotion | `test_search_coordinator.py` | 中间点只有 TyCheck；final 精确运行 test；promotion fail 后重新定界 |
| 10 final authority | `test_schemas.py`, `test_projection.py` | vector/Attempt/Proposal/TestPass/ProbePass 同一闭环，跨 Proposal 复制被拒绝 |
| 11 non-monotonic | `test_search.py` | 低 PASS + 高 REJECTED 保留 counterexample 并停止 |
| 12 command semantics | `test_check.py`, `test_smoke.py`, `test_search_workflow.py` | check regression 动态化；smoke 只一次 ty + 一次 test；search baseline/final 直接 pass |
| 13 migration validation | `test_schemas.py`, `test_report.py`, `test_runlog.py` | static-only boundary、跨 Slice region、旧 policy 混合、截断证据均拒绝 |
| 14 config surface | `test_config.py`, `test_cli.py` | 无 witness/smoke command 配置；仅现有 test-command 决定动态测试 |

每个切片先执行最窄测试；切片 003 起执行相邻 workflow 回归；最终门禁使用 `--no-testmon` 保证完整选择。网络/构建依赖若受 sandbox 限制，先记录准确失败，再在获准联网环境复验，不把环境失败写成实现通过。

## 6. 变更控制

- 实施细节若改变 D011 行为，先修改 D011，再继续实现；纯内部取舍只记录 §8；
- 不为旧 `StaticFailEvaluation`、static/dynamic 双阶段报告或 `increment-v2` 增加兼容写层；历史报告只能作为不兼容输入被明确拒绝；
- 保留用户已有工作树变更；本轮开始时工作树干净；
- D012 仍是草案，不提前实现 relaxed harness 或 frozen resolver universe；
- 不以窄单测代替 §15 对应范围的报告/运行时证据。

## 7. 过程记录

### 基线与 Plan

- **状态：** 已完成
- **行动：** 对照 D011 全文检查当前 evaluation、failure、search、coordinate/report Schema、production composition 与测试；确认当前实现仍是 static fixpoint + fast/dynamic 两阶段。建立本 Plan 后才开始生产代码变更。
- **结论：** D011 §15 的十四项没有已完整落地项；现有 baseline multiset subtraction 与 final PASS authority 可迁移复用，但旧 static disposition、Probe Schema 和搜索状态机必须替换，不能只放宽 FullEvaluator 短路。
- **证据：** `git status --short` 无输出；`git log -5 --oneline` 顶部为 `e4e6d17`；`UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon -q` → 584 passed、3 failed。3 个失败均发生在需要构建隔离环境的真实安装用例，使用新空 uv cache 且 sandbox 无网络；其余基线测试通过。初次未设置 `UV_CACHE_DIR` 时 uv 因 `/home/llh/.cache/uv` 只读而退出，后续命令固定使用 `/tmp/pf-uv-cache`。

### 切片 001 — transition Schema、fingerprint 与 cache

- **状态：** 已完成
- **行动：** 新增 `static_transition.py` 的 `static-transition-v1` 域分隔 fingerprint；把旧 StaticPass/StaticFail Schema、StaticEvaluator、EvaluationCache 和调用方迁移为 unchanged/regression transition；Schema 校验 ordered multiset、显式 fingerprint 和逐 occurrence classification。
- **结论：** 空增量与非空增量都形成非空、可反算的静态状态；静态回归不再属于 `Evaluation` 或 compatibility disposition，旧证据形态不能进入新报告。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_evaluation.py tests/test_evaluation_cache.py tests/test_schemas.py tests/test_environment.py -q` → 119 passed（随后 schema migration 继续纳入相邻回归）；fingerprint 测试显式覆盖空、`{A}`、`{A×2}`、`{A,B}`、`{C}`。

### 切片 002 — classifier、planner 与 witness adapter

- **状态：** 已完成
- **行动：** 实现基于 structured code + source AST + managed dependency mapping 的保守 classifier/planner；新增 RuntimeWitnessPlan/Attempt/Result Schema 和 `RuntimeWitnessAdapter` 的 `-I -c` 无 shell harness、严格 JSON protocol 与精确缺失对象核对。
- **结论：** strong allowlist 只含当前 ty 可核实的 unresolved-import/unresolved-attribute；任何歧义、非受管归因、side-effect exception、非零退出、截断或坏输出均不猜测为 missing。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_static_transition.py tests/test_runtime_witness.py tests/test_evaluation.py tests/test_schemas.py -q` → 108 passed；真实 interpreter harness 覆盖 module/symbol present、confirmed missing、bad output、nonzero 与 timeout。

### 切片 003 — runtime evaluator、check 与 baseline/smoke

- **状态：** 已完成
- **行动：** 将 FullEvaluator 迁移为 RuntimeEvaluator，固定 static → eligible witness → test 路由；新增 `RUNTIME_INTERFACE_MISSING`，删除 `STATIC_REGRESSION` 的 FailureCause/Rejection/BaselineRejection 写入口；迁移 check、highest baseline、smoke 和 production composition。
- **结论：** general regression、witness PRESENT/NOT_APPLICABLE 都继续 test；confirmed missing 才形成 runtime rejection；witness tool failure 保留 static/witness evidence 并成为 Indeterminate；smoke 仍复用 capture TyCheck 后只运行现有 test-command。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_evaluation.py tests/test_failure.py tests/test_check.py tests/test_baseline.py tests/test_smoke.py -q --deselect=tests/test_check.py::TestCompatibilityChecker::test_check_passes_a_minimal_local_package` → 74 passed、1 deselected；deselect 项是基线已确认的 sandbox/空 uv cache 安装网络失败。

### 切片 004 — runtime-backed region/frontier 搜索

- **状态：** 已完成
- **行动：** 删除 SearchCoordinator 的 static fixpoint/fast-path/dynamic-search 三段状态机，建立单一 runtime-backed CoordinateSearch；加入 Slice、连续 region、无 disposition 的 StaticOnlyEvidence、floor/predecessor 精确 promotion、promotion 指导反转后重新定界、直接 runtime 非单调检测和 invocation-local region report。补入 A-B-A、跨 Slice、cheap middle、final promotion 和指导反转的验收用例。
- **结论：** coordinator 只以直接 runtime evidence 形成 boundary；static-only 只保存 guidance，不实现 ProbeEvidence status。任何待提交 floor/predecessor 都先精确 promote，反转后在同 Slice 重新定界；直接的低 PASS/高 REJECTED 才能形成 `NON_MONOTONIC` counterexample。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_search_coordinator.py -q` → 13 passed；`... tests/test_search.py tests/test_search_coordinator.py tests/test_schemas.py tests/test_terminal.py -q` → 163 passed；新增 `test_static_frontier_is_promoted_and_rebounded_on_runtime_rejection`、`test_search_coordinator_rebounds_after_frontier_promotion_changes_status`、A-B-A 与跨 Slice validator 用例。切片收口时 `ruff check src tests` 与 `ty check src` 均通过。

### 切片 005 — 公共报告、终端与持久化迁移

- **状态：** 已完成
- **行动：** 将 ProbeObservation/CoordinateSuccess/CellSuccess 迁移为单一 `search`，删除旧 static/dynamic phase 字段与写路径；迁移 report builder、projection、verification journal 投影、terminal/explain/diagnose。Schema 新增 Slice/region/static-only、runtime witness 序列、boundary/final authority 的闭环校验，并拒绝观察标签与 vector 不一致、非精确 predecessor、跨 Proposal pass 和跨 Slice counterexample。
- **结论：** 新报告不能把 static regression、representative 或 witness PRESENT 伪造成 compatibility disposition；final vector、CoordinateSuccess、Attempt、Proposal、ProbePass 和 TestPass 必须是同一精确事实。终端把动态通过的 static regression 展示为 transition warning，不再把 `STATIC_REGRESSION` 当 failure cause。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.12 pytest --no-testmon tests/test_schemas.py tests/test_editor.py tests/test_projection.py tests/test_report.py tests/test_terminal.py -q` → 170 passed；`... tests/test_runtime_witness.py tests/test_evaluation.py tests/test_schemas.py -q` → 112 passed。

### 切片 006 — 所有者同步与最终门禁

- **状态：** 已完成
- **行动：** 把现行规则归并到 D001–D005/D008，同步 D006/D007 展示与进程边界、D009/D010 历史架构前提、D012 草案对 D011 的引用以及文档索引；D011/P010 状态改为现行/已完成。最终审计又收紧 import-symbol submodule witness、witness canonical output、重复 occurrence plan 去重和 report boundary/counterexample validator。
- **结论：** D011 §15 十四项均有生产实现和测试证据。最终全量用例包含真实本地构建/安装；初次在 sandbox 中因 `uv-build` 无缓存且禁网失败，获准联网补入隔离构建后端后全部通过。Coverage 88.15% 低于可选 90% 报告阈值；按 D009 §12 该值不是本轮红线，未冒充为通过。
- **证据：** Python 3.10.16、3.11.15、3.12.3 分别执行 `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python <minor> pytest --no-testmon -q` → 各 611 passed；`ruff check src tests` → passed；`ty check src` → passed；`UV_CACHE_DIR=/tmp/pf-uv-cache uv build` → 成功生成 sdist/wheel；`git diff --check` → passed；可选 coverage 命令 → 611 passed、88.15%、因 `--cov-fail-under=90` 返回非零。

## 8. 实施决策

- 第一版 strong planner 只支持能由 AST 与 managed dependency canonical mapping 唯一证明的 import/module/member 形态；其余 unresolved diagnostic 留在 general 路径。该保守子集符合 D011 的 optional witness 与“不得猜测”边界。
- Region guidance 是 invocation-local 调度状态，不进入 Evaluation cache；报告保存观察事实，不能用 region representative 构造另一个 Proposal 的 Evaluation。
- 相同 `RuntimeWitnessPlan` 可由重复 DiagnosticIdentity occurrence 产生；fingerprint/classification 仍保留全部重数，执行层只对完全相同的 plan 保序去重，避免重复运行同一机械 witness。
- `import-symbol` witness 使用 Python `fromlist` 语义后再核对属性，避免把 `from package import submodule` 误判为缺失；adapter 要求 canonical stdout 且 stderr 为空。

## 9. 最终完成矩阵

| D011 §15 | 实现证据 | 测试/运行证据 | 状态 |
| --- | --- | --- | --- |
| 1 baseline 抵消/多重集 | `StaticEvaluator._increment`、Static Schema | multiset subtraction + 重复 occurrence 测试 | 完成 |
| 2 fingerprint | `static_fingerprint`、Schema 反算 | 空、A、A×2、A+B、C 稳定互异 | 完成 |
| 3 region scope/连续性 | `StaticRegionSlice`、`_region_points`、region validators | A-B-A 与跨 Slice 反例 | 完成 |
| 4 general regression | classifier general + `RuntimeEvaluator` | general regression 运行 test-command | 完成 |
| 5 不可规划 strong | AST/canonical dependency 保守 planner | message 变化不改分类；无归因不建 plan | 完成 |
| 6 witness 路由 | `RuntimeWitnessAdapter`、`RuntimeEvaluator` | PRESENT/NA 后 test；missing 才 negative；submodule fromlist | 完成 |
| 7 witness 故障 | strict canonical protocol + ToolFailure | timeout/nonzero/bad/extra output/stderr 均 Indeterminate | 完成 |
| 8 cheap middle | `evaluate_in_slice`、`StaticOnlyEvidence` | 中间点只有 TyCheck，无 status/PASS | 完成 |
| 9 exact promotion/rebound | `CoordinateSearch.promote`、`_guided_floor` 重定界 | search/coordinator promotion 反转用例 | 完成 |
| 10 final authority | `CellSuccess._validate_final_authority` | vector/Attempt/Proposal/ProbePass/TestPass 防篡改 | 完成 |
| 11 non-monotonic | 只记录直接 runtime status | 同 Slice 低 PASS/高 REJECTED counterexample validator | 完成 |
| 12 command semantics | check/smoke/search 统一 RuntimeEvaluator | check、smoke、search workflow + 真实 E2E | 完成 |
| 13 migration validation | 新 Evaluation/Probe/Region/Cell Schema 与 policy identity | static-only boundary、跨 Proposal/Slice、旧 policy 不兼容 | 完成 |
| 14 config surface | 复用现有 `test-command`，无 witness/smoke config | config/CLI 回归；3.10–3.12 各 611 passed | 完成 |

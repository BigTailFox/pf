# P038 — D033 PF Predecessor Revalidate 实施计划

- **状态：** 已完成并归档；AC1–AC14 与最终门禁均已闭合
- **日期：** 2026-09-05
- **依据：** [D033](../designs/D033-pf-predecessor-revalidate.md)，已接受并完成实施前评审
- **基线：** `a7bcc41`；工作区已有 D033 的已接受未提交修订
- **稳定 owner：** [D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)、
  [D003](../../designs/D003-pf-search-algorithm.md)、[D006](../../designs/D006-pf-cli-enhancement.md)、
  [D014](../../designs/D014-pf-report-schema.md)

本计划在生产修改前建立。用户已授权完整实现 D033，但未授权 commit 或 push。实施直接替换
`search-step`、搜索私有执行缓存和旧报告形状；不提供别名、兼容 reader、dual shape、产品开关或
第二套 artifact lookup map。C001 的树搜索与逐层 refinement 不在本计划范围内。

## 1. 实施不变量与顺序

实施始终保持以下不变量：

1. `C[d]` 是唯一搜索候选序列；`S[d] = C[d] ∪ {B[d]}` 只为 exact-vector 选择 artifact。
2. baseline highest PASS 保留原 Attempt、Proposal、PassEvaluation 与 original harness identity；
   只有新 exact-vector Attempt 才绑定完整 selected-candidate evidence digest。
3. static-only 只调度；floor、predecessor、current 提交与 final 必须有直接完整 evidence。
4. `_ProposalRunner` 拥有 prepare/static/runtime 结果与环境生命周期；`CoordinateSearch` 只拥有算法
   observation、Slice、history、区间与边界，不保存第二份执行结果权威。
5. `S[d]` 外向量在 prepare/Attempt 前按 D005 形成 Cell-scope `INTERNAL_INVARIANT` Indeterminate；
   合法窄 search-space 不得泄漏为 ConfigurationError 或自由解析。
6. 合法但空的 `C[d]` 继续在 candidate-discovery 返回 `NO_PASS_IN_SEARCH_SPACE`，不构造空 snapshot。

按 S1 → S2 → S3 → S4 → S5 → S6 → S7 推进。每个行为切片先固定 public seam 的目标测试，再实现并
回填 §6；不能以静态检查替代运行证据。

## 2. 有序实施切片

| 切片 | interface、owner 与迁移 | AC | 证据槽 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | ConfigLoader/ProjectLoader/CLI、SearchPolicy/NamedSearchPolicy/SearchPolicyBinding、candidate policy preimage 从 `step` 原地替换为 `resolution`；同步 help、双语 README 与直接调用方，不留旧输入 | AC1、AC2、AC8、AC11 | E1：配置、project、CLI、candidate identity 定向测试 | 完成 |
| S2 | CandidateBuilder 从同次 registry observation 冻结 required `baseline_selection`；CandidateSnapshot 封装 `S[d]` 选择；`select_probe` 只消费该 interface；先建立多坐标窄空间 public CandidateBuilder + SearchCoordinator 回归，并保持空空间/错误时序 | AC2、AC4、AC14 | E2：candidate/search/environment 公共图、artifact 不变量与 source failure | 完成 |
| S3 | ProbePass 精确放行本 Cell highest baseline；SearchCoordinator 注入真实 HighestVersionPass；`_ProposalRunner` 建立按完整 vector 的唯一结果入口，统一 prepare/static/runtime/prepare-terminal 缓存、首次 promotion、跨 Slice region 登记与资源关闭；移除 `_KnownPass`、`start_is_known_pass` 及搜索执行缓存 | AC5、AC6、AC12、AC13 | E3：真实 evaluator 图、领域校验、cache/activity/cleanup | 完成 |
| S4 | CoordinateSearch 加入上一 sweep boundary history 与 predecessor 优先重验；普通 evaluator 直接认证；current/首候选/cache hit observation 登记、窗口、promotion 反证、Slice 非单调检查和最终无变化 sweep 闭合 | AC3、AC4、AC6、AC7、AC13 | E4：public minimize 策略矩阵和 SearchCoordinator 集成 | 完成 |
| S5 | CandidateSnapshot/DirectPass Schema 1 wire、writer/read/reintern/merge/host-partial、exact Attempt selection digest 与 baseline roots 准入原地替换；ApplyAuthorizer、Explain/terminal 消费 resolution 和闭合 evidence；重生成 Schema/examples/fixtures | AC8、AC12、AC14 | E5：domain/wire round-trip、tamper、merge/apply/explain、生成物 | 完成 |
| S6 | 以同源、同候选、同策略、同 threshold、等价 invocation-local cache 的 A/B 对照记录 logical request/cache hit/unique vector/prepare/static/runtime miss/sweep；在有限真实 evaluator harness 中交替运行并记录冷热条件、阶段次数、wall-clock、missing/波动/退化 | AC9 | E6：可复现脚本或测试 harness、原始机器结果与结论 | 完成 |
| S7 | 全量质量门禁、逐 AC 审计、D001/D002/D003/D006/D014 owner 吸收；核对 D004/D005/D008/D013；D033/P038 同步归档并更新现行/归档索引 | AC10、AC11，汇总 AC1–AC14 | E7：三 Python、coverage、Ruff、ty、build、生成物、links、diff | 完成 |

S2 的现场回归固定至少两个 managed coordinates：`charset-normalizer` 的声明下界 `1.3.9`、baseline
`3.5.1`、`minors[declaration]` × patch；另一 canonical-first 坐标下降时，完整 exact vector 继续以
`3.5.1` 的 `baseline_selection` 闭合 artifact，而 `3.5.1` 不进入其 `candidates`。

S3 不新增 CacheManager/evaluator facade。普通算法 evaluator 可以在自身 interface 后复用结果；产品图
继续使用 concrete SearchCoordinator 与现有 lower uv/candidate/ty/verifier/witness/process adapters。

## 3. AC 到测试与证据映射

| AC | public seam 与必须覆盖的场景 | 目标证据 |
| --- | --- | --- |
| AC1 | ConfigLoader/ProjectLoader/CLI：global/dep/CLI resolution、minor 默认、继承、raw layer、非法输入；旧 step 全面消失 | E1、E5、E7 |
| AC2 | CandidateBuilder：现有精确代表/artifact 不变；同 query 冻结 baseline selection；全 space × resolution、特殊 Version 与错误时序 | E1、E2 |
| AC3 | CoordinateSearch.minimize：history predecessor 拒绝/转 PASS、context 变/不变、无 history、首候选、hint 优先级 | E4 |
| AC4 | public minimize：最低候选、平面定位、sentinel/no-pass、promotion 反证、准确窗口；baseline selection 不获得候选或边界权威 | E2、E4 |
| AC5 | SearchCoordinator 产品图：真实 baseline seed、跨定位/sweep 复用、完整命中不执行 prepare/static/runtime/解析、Proposal 与 invocation 隔离 | E3、E4 |
| AC6 | runtime-backed evaluator：static-only 不直接通过；跨 active dependency guidance 隔离；完整命中仍登记 observation/region 并检查非单调 | E3、E4 |
| AC7 | CoordinateSearch：NON_MONOTONIC 立即停止并保存同 Slice 反例；Indeterminate 不裁剪；顺序、终止、最终 context 边界 | E4 |
| AC8 | SearchPolicy/report/apply/explain：resolution identity/binding、snapshot 与 exact selection digest round-trip、merge/host-partial、force/幂等 | E1、E5 |
| AC9 | A/B 与真实 evaluator：等价 cache、探针和阶段成本、冷/热条件、missing/波动/退化诚实记录 | E6 |
| AC10 | focused/full、三 Python、coverage≥90%、Ruff、ty、build、Schema/examples 与链接检查 | E1–E7 |
| AC11 | README 双语、help、配置/model/schema/fixtures/scripts、owner 吸收与 D033/P038 同步归档 | E1、E5、E7 |
| AC12 | highest seed identity；baseline 在/不在候选、首候选、final 相同/不同；三种带搜索结果经 build/read/reintern/merge；拒绝错 roots/向量/非 PASS highest | E3、E5 |
| AC13 | 普通 evaluator 直接认证；真实首次 promotion、新 context predecessor、static 后 promotion、跨 dependency 完整命中；region/activity/历史 guidance | E3、E4 |
| AC14 | 多坐标 `B[d]` 在/不在 `C[d]`、混合向量、同版本 artifact 一致性、规范依赖顺序、现场形状、SOURCE_FAILURE、S 外 invariant、wire tamper 与所有 exact Attempt | E2、E5 |

## 4. Report、生成物与 owner 工作

S5 必须通过同一个 CandidateSnapshot 选择 interface 在在线 runner 与离线 reader 中重建完整
`SelectedCandidate` 序列，不能由 reader 重写 join 规则。CandidateSnapshot digest 纳入完整
`baseline_selection` payload；candidate policy identity 只绑定请求策略，不吸收 Cell baseline artifact。
保持 Schema 1、`attempt-v1`、`pf:attempt:v1`、`pf:candidate-snapshot:v1`、
`pf:selected-candidates:v1` 与现有 search/runtime profile 名称，但直接替换旧 preimage/字段。

完成时稳定规则分别归入：

- D001：resolution 配置、默认、系列代表与 `C[d]`/`S[d]` 用户语义；
- D002：CandidateSnapshot interface、evaluator 结果入口、highest seed 与资源 ownership；
- D003：selection invariant、认证、Slice/region、predecessor/history/cache/window/sweep；
- D006：CLI/help/活动展示中的 resolution 与 lookup 非活动语义；
- D014：baseline direct PASS、baseline selection wire/digest、exact Attempt 重建、roots/identity/apply。

D004/D005/D008/D013 只核对现行 static、failure、Run、pytest authority；除非实现发现直接冲突，不复制
或改写其 owner 规则。生成命令必须同时更新并检查 Schema 及两个 minimal examples。

## 5. 计划验证命令

所有 PF/pytest/uv 命令按 AGENTS.md 在 `/home/llh/pf` 沙箱外执行；使用独立 uv cache，并把网络、cache、
TTY 等环境限制与产品失败分开记录。全套 Python 矩阵串行执行。

| 槽 | 准确命令 |
| --- | --- |
| E1 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_config.py tests/test_project.py tests/test_cli.py tests/test_candidates.py tests/test_search_space.py` |
| E2 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_candidates.py tests/test_environment.py tests/test_search.py tests/test_search_coordinator.py` |
| E3 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_evaluation_cache.py tests/test_schemas.py tests/test_search.py tests/test_search_coordinator.py` |
| E4 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_search.py tests/test_search_coordinator.py tests/test_search_workflow.py` |
| E5a | `.venv/bin/python scripts/generate_report_schema.py` |
| E5b | `.venv/bin/python scripts/generate_report_schema.py --check` |
| E5c | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_schemas.py tests/test_report_schema.py tests/test_report_artifacts.py tests/test_report_workflows.py tests/test_search_space_report.py tests/test_authorization.py tests/test_explain_terminal.py tests/test_cli.py` |
| E6 | `.venv/bin/python scripts/measure_d033_predecessor_revalidate.py`（脚本在 S6 建立；参数、fixture、重复次数与输出文件随实施记录冻结） |
| E7a | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short --cov=pf --cov-report=term-missing tests` |
| E7b | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.11 --group test pytest --no-testmon -q --tb=short tests` |
| E7c | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.12 --group test pytest --no-testmon -q --tb=short tests` |
| E7d | `.venv/bin/ruff check src tests scripts` |
| E7e | `.venv/bin/ty check` |
| E7f | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` |
| E7g | `.venv/bin/python scripts/generate_report_schema.py --check`、`git diff --check`、Markdown 相对链接/anchor 检查 |

若 S1–S5 改变 qualification 脚本消费的命名或 report shape，补跑对应 qualification 并在 §6 写出准确
命令；不得以旧 manifest 证明新契约。E6 是收益与成本证据，不替代 E2–E5 的正确性证据。

## 6. 行动、决定与证据记录

| 记录 | 行动 / 结论 | 验证状态 |
| --- | --- | --- |
| E0 | 2026-09-05：复核 HEAD `a7bcc41`、D033、稳定 owners、CandidateBuilder/CoordinateSearch/SearchCoordinator/report reader 与现有测试；确认 D033 是唯一既有工作区修改；建立 P038 并链接 Design/索引 | `git diff --check` exit 0；引用文件存在；未执行产品命令，不属于产品验收 |
| E1 | 2026-09-06：Config/Project/CLI、candidate identity 全面迁移到 `resolution`；旧输入无 alias 或 reader fallback。按 §5 E1 原命令执行 | exit 0；`254 passed in 1.92s` |
| E2 | CandidateBuilder 同一次 registry observation 冻结 baseline selection，CandidateSnapshot 以唯一 interface 选择 `C[d] ∪ {B[d]}`；多坐标 `1.3.9`/`3.5.1` 回归、同版本一致性、source failure 与 S 外 invariant 均固定。按 §5 E2 原命令执行 | exit 0；`102 passed in 0.50s` |
| E3 | 真实 highest evidence 注入 `_ProposalRunner`；按完整 vector 统一 prepare/static/runtime 结果，覆盖命中活动、Slice region、promotion、冲突与 cleanup；搜索私有执行 cache/known-pass shortcut 已删除。按 §5 E3 原命令执行 | exit 0；`229 passed in 0.41s` |
| E4 | 实现 history predecessor 调度与普通 evaluator 认证。E005 产品差分最初发现 predecessor 转 PASS 后旧 floor 仍充当虚拟 upper；新增红测试后改为以 predecessor 作为后续定位 upper。按 §5 E4 原命令执行，并补跑 `scripts/simulate_d031_search.py` | exit 0；`58 passed in 0.44s`；历史正确性矩阵与 `3362` 个产品案例通过 |
| E5 | 原地替换 Schema 1 snapshot/search policy wire、reader/build/reintern/merge/host-partial/apply/explain；补齐 SUCCESS/SEARCH_FAILED/CELL_INDETERMINATE highest observation、final=baseline、tamper 与 exact selection digest。执行 E5a/E5b/E5c | 生成 exit 0、check exit 0；E5c `477 passed in 5.76s` |
| E6 | 建立 `scripts/measure_d033_predecessor_revalidate.py`，以 3 个预定场景、每变体 7 次、A/B 交替顺序运行真实产品 evaluator 图；原始结果见 [measurement.json](../../experiments/data/D033/measurement.json) | exit 0；两组 changed-context 中 B 的 logical request 中位数为 `46/59`（A `61/78`），prepare miss `17/23`（A `22/25`），runtime miss `15/18`（A `16/23`）；same-context 唯一 vector 与阶段 miss 均持平，logical request `25`（A `34`） |
| E7 | D001/D002/D003/D006/D014 已吸收稳定规则；README 双语、help、历史实验字段语境与生成 Schema 已同步；D033/P038 同步归档并更新两级索引 | Python 3.10 coverage：`1855 passed in 39.36s`、branch coverage `90.25%`；3.11：`1855 passed in 33.46s`；3.12：`1855 passed in 35.45s`；最终类型收口后相关回归 `160 passed in 0.59s`；Ruff/ty、Schema check、sdist/wheel build、`git diff --check` 均 exit 0；92 个 Markdown 文件的相对 target/anchor 全部解析 |

E6 的 wall-clock 只包含 deterministic in-memory uv/ty/verifier/witness adapters 与产品 orchestration，
不声称代表 registry、uv、ty 或 configured verifier 子进程时延。registry/resolution 每个 sample 冷启动，
evaluator cache 每个 Cell 冷启动后仅在 invocation 内变热；`missing_vectors=[]`。changed-context 两组中
B 的 Cell wall-clock 中位数分别下降 `23.16%` 与 `11.22%`；same-context 中位数下降 `2.09%`，但该组
阶段 miss 完全持平，因此只证明减少缓存查询开销，不能外推真实工具耗时。原始文件保留每组 repetition 0
的完整 trace、全部样本 min/median/max 与源码 SHA-256。

E7 完成后在最终树上补跑 coverage 时，唯一失败为网络型 installed-CLI E2E：`1854 passed, 1 failed`、
coverage `90.23%`；单独连续复现两次均在 baseline `uv pip sync` 获取 `uv-build` 时由 PyPI TLS
handshake EOF 终止，发生在 D033 搜索逻辑之前。成功的 `1855 passed`/三 Python 矩阵之后仅有 lint
要求的未用变量/导入删除，以及 report reader 原表达式的静态 `cast`，没有运行时契约改变；当前树的
受影响报告/协调器 `160 passed`，Ruff、ty、Schema 和 build 均再次通过。因此该补跑记为外部网络限制，
不覆盖已取得的完整产品证据，也不伪报为最终联网 PASS。

## 7. 最终验收审计

| AC | 审计结论 | 直接证据 |
| --- | --- | --- |
| AC1 | 通过：global/dep/CLI、默认/继承/raw/非法输入及 public wire 只接受 resolution | E1、E5、仓库旧名扫描 |
| AC2 | 通过：`C[d]` 代表语义保持，baseline selection 与相同 registry 观测一起冻结 | E1、E2 |
| AC3 | 通过：history predecessor 的拒绝、转 PASS、context、无历史、首候选和 hint 已覆盖 | E4 |
| AC4 | 通过：最低候选、平面定位、sentinel/no-pass、promotion、窗口及 floor authority 闭合 | E2、E4 |
| AC5 | 通过：真实 baseline seed 与 vector 级 evaluator cache 复用，不跨 invocation/Proposal | E3 |
| AC6 | 通过：static-only 不认证 PASS；cache hit 仍登记 Slice/region 并执行非单调检查 | E3、E4 |
| AC7 | 通过：NON_MONOTONIC/INDETERMINATE、顺序、终止和最终 context 边界闭合 | E4、E005 simulator |
| AC8 | 通过：resolution、snapshot/digest、reader/merge/host-partial/explain/apply 完整替换 | E1、E5 |
| AC9 | 通过：等价 cache 的产品 evaluator A/B 原始数据、波动和适用边界已保存 | E6 |
| AC10 | 通过：focused/full、三 Python、coverage、lint、typing、build、Schema 与最终文档门禁 | E1–E7 |
| AC11 | 通过：README/help/model/schema/fixtures/scripts 同步，owner 已吸收，归档在收口步骤完成 | E1、E5、E7 |
| AC12 | 通过：highest roots/identity、baseline 在/外 C、final 相同/不同和三种终态 round-trip 闭合 | E3、E5 |
| AC13 | 通过：普通/产品 evaluator、首次 promotion、新 context、完整命中与 guidance 失效闭合 | E3、E4 |
| AC14 | 通过：多坐标混合选择、现场形状、SOURCE_FAILURE、S 外 invariant 与 wire tamper 闭合 | E2、E5 |

未发现需要偏离 D033 的产品契约。实现期间唯一算法修正是 E4 记录的 predecessor 转 PASS 后 upper
收窄；它使实现回到 D033 §4.1 的既定规则。D004/D005/D008/D013 复核后无需修改。完成最终链接检查后，
D033/P038 同一工作区变更同步归档；用户未授权 commit 或 push。

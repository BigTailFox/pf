# P042 — PF 静态 guidance 权限与 Run 缓存实施计划

- **状态：** 已完成并归档；S1–S8 与 AC1–AC26、E1–E8 均有证据。稳定规则已由现行 owner 接管
- **日期：** 2026-09-08
- **规范：** [D038](../designs/D038-pf-static-guidance-authority.md)，已完成并归档
- **起点：** `d707476d1a862effb8d90198ca4c4af7f05a95b5`
- **工作区：** 起草前已有 `docs/README.md` 修改及未跟踪的 D038；保留其内容，仅同步 Plan 入口与状态
- **流程与测试：** [AGENTS.md](../../../AGENTS.md)、[测试说明](../../../tests/README.md)

本 Plan 记录实施安排、决定、偏差和证据；目标行为由 D038 唯一规定。用户已要求实现 D038，
现按下列切片推进生产实施并持续回填，不把计划中的命令记为已通过；未授权提交或推送。

## 1. 实施基线与优先风险

当前代码核对得到以下迁移入口：

| 当前实现 | 迁移目标与影响 |
| --- | --- |
| `evaluation.py` 的 StaticEvaluator.capture/evaluate 以静态失败返回 Indeterminate；RuntimeEvaluator 运行 witness 并可提前拒绝 | 静态事实独立；完整 verifier 与动态 disposition 由 RuntimeEvaluator/FailurePolicy 负责 |
| `search.py` 的 `_ProposalRunner.evaluate_in_slice` 传播 region guidance；`coordinate_search.py` 把 guidance 作为定位状态 | 删除代表点预测权限，拆开静态探测窗口与仅消费直接证据的 oracle 窗口 |
| `policy.py` 的统一 evaluation policy；EvaluationCache 以 Proposal/static baseline 为 key | 三类 policy 与两类执行对象分离；动态 cache 移除 ty/baseline，Run ty cache 使用静态投影 |
| `environment.py` 的安装复证主要检查包集合/版本；InterpreterIdentity 保存实现/version/ABI | 为静态投影新增实际安装内容、解释器/stdlib、配置与布局的不可变事实，不以现有 graph digest 替代 |
| `adapters/process.py` 默认复制宿主环境并注入终端尺寸；TyAdapter 未传完整显式环境 | 为 ty 提供准确的进程环境及配置发现闭包；其他进程现有执行语义不随此辅助能力被改变 |
| ReportStore 与 VerificationRunner/RunLogStore 绑定现行 Evaluation、Journal v2、Failure 日志索引 | Schema 1 目标形状、Journal v3、静态审计/关联、离线验证及展示同步替换 |

两项风险前置处理：S1 必须用真实 prepare/ty 环境证明静态投影可以闭合，不能只验 NO_HINT；
同时落地 producer/reader 共享规范模型与最小离线复算，避免完整 report 集成时才发现证据缺失。
静态投影的成本另行计量，不承诺缓存一定命中或指导搜索一定更快。

## 2. 有序实施切片

依赖顺序为 **S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8**。每片同步迁移其直接调用方、fixtures
和公开测试；完整 report/CLI 汇合分别在 S5/S6 验收。切片是工作与证据单位，不承诺每片都是可独立
发布的产品版本；不以兼容层维持两个契约。物理文件划分可随实现调整并记录，但不改变 D038 的 owner。

| 切片 / 状态 | 实施内容、文件与 interface | 完成门槛 / 证据 |
| --- | --- | --- |
| S1 身份与静态事实基础；已完成 | `policy.py`、`resolution.py`、`environment.py`、`snapshot.py`、`schemas/project.py`、`schemas/evaluation.py`、`adapters/ty.py`、`adapters/process.py`；拟新增 `static_subject.py` 与 `schemas/static.py`。拆三类 policy、TyObservation 子身份和 ExecutionSubject/StaticSubject，迁移 Attempt/Environment/Proposal/Failure 身份链与候选选择摘要。实现规范静态请求 factory、原始事实/可用性/比较 context 模型、共享纯比较准入与最小离线 codec | 六组静态输入都有 producer 与保存形式；真实闭包成功正例；身份变化矩阵、D012 harness 比较与最小反序列化复算通过；E1 |
| S2 移除 witness 与静态终止权限；已完成 | `evaluation.py`、`failure.py`、`baseline.py`、`check.py`、`search.py`、`cli.py` 及相关 schemas。RuntimeEvaluator 仅组装合格动态结果；highest ty 失败可继续，check 保留两次 preparation/HarnessBaseline。移除 region disposition 消费，接直接 oracle 路径。删除 `static_transition.py` 的 AST classifier/planner、`adapters/runtime_witness.py` 与无用途专属类型/测试 | import/member/其他 regression + verifier PASS；ty failure 不终止；动态 prepare/failed-set 资格保持。现有 report/CLI 的必要类型消费同步替换，不能残留 hard rejection；E2 |
| S3 Run cache 与环境时序；已完成 | 拟新增 `static.py`，承接 StaticEvaluator 的 lookup/collect/compare；`verification.py` 在 capture 前创建显式 Run cache。`evaluation.py` 仅保留动态 cache；`search.py` runner 独占环境和完整结果，组合层传 cache handle。原始事实去重、negative cache、typed refs、owner/等待者/取消/关闭一并实现 | AC25 在本片通过后才接搜索；关闭环境后事实仍可读，同 ty key 不代表环境冗余，ty unavailable 后仍能运行 verifier；E3 |
| S4 坐标内两阶段搜索；已完成 | `coordinate_search.py`、`search.py`、相关 search schemas；普通 VectorEvaluator 保持直接证据 seam，产品 evaluator 提供直接 lookup/oracle、静态 inspect 与坐标收尾请求。先 direct fast path，再固定 S_slice、静态二分、suspect/clean 消费、机械 continuation；删除 promote/regions/StaticOnlyEvidence 旧 interface 与预测 payload | 动态缩窗逐次有直接证据；有限单调矩阵 floor 相同；虚拟 anchor、历史前驱重验、跨 sweep、NO_HINT/异常清理全覆盖；E4 |
| S5 Wire、Journal 与离线权限；已完成 | `schemas/report.py`、`report.py`、`schemas/evaluation.py`、`verification.py`、`runlog.py`、`authorization.py`、`workflow.py`、`errors.py`；接入 S1 codec/共享准入，intern raw facts/比较/调度记录，保持独立 scope membership。Journal v3 静态区及 Diagnosis Index typed association；迁移 generation、merge/update/apply；用现有脚本生成 schema/examples | 完整/不完整及无 Failure 静态审计可往返；跨 scope/伪造 context/authority 拒绝；read→write byte-stable，policy mismatch 可区分；E5 |
| S6 命令与展示；已完成 | `cli.py`、`workflow.py`、`verification.py`、`terminal/_live.py`、`_diagnose.py`、`_explain.py`、`_presentation.py`；动态 summary 与辅助静态分开，static/oracle window 和计数分开。diagnose 只遍历命中来源中与 Failure 合法关联的静态事实 | smoke/check/search 的 role、退出码、Journal 聚合、日志可用性与 final 一致；不新增 selector 或伪造执行耗时；E6 |
| S7 真实资格与成本观察；E009 check/search 与受控对照已冻结 | `scripts/qualify_static_guidance.py`、`tests/test_static_guidance_qualification.py`、`scripts/measure_d038_guidance.py`；隔离 MkDocs 副本，不回写用户实验目录。受控 guidance/mechanical 比较只走实验 seam | AC12/13 真实证据闭合；观察 §6 所列成本，区分 fixture/真实命令/受控对照，不新增产品开关或正收益门槛；E7 |
| S8 全套门禁、owner 吸收与归档；已完成 | 按 §7 更新 owner、README/README.zh/CONTEXT、文档索引；清理临时旧格式 fixture，确认生成物。审计全部 AC 与 E1–E7，再同步归档 D038/P042 | Python 3.10–3.12 门禁、coverage、build、文档检查与 26 项验收全部有明确结果；未闭合项不写通过、不归档；E8 |

### 2.1 S1 的静态输入采集与离线闭包

下表是 D038 §4.4 六组输入的实施责任分配，不另选缓存等价性。共享 factory 只消费明确事实；
文件采集/安装属于下层，算法和 report reader 不访问宿主环境。

| 输入组 | producer / 保存与验证安排 |
| --- | --- |
| Source | SourceSnapshot 提供冻结源码与配置内容；补齐 package/member、受观察路径和内容 manifest。保存内容 refs 与规范映射，覆盖整个观察 scope |
| Target | EnvironmentFactory 组合实际解释器事实与静态内容采集；记录完整版本、实现/ABI/platform/surface、解释器与可观察 stdlib manifest；不能仅复用 version 字符串 |
| Installed world | 成功 prepare 后、首次 ty/verifier 前采集实际安装文件/metadata/stubs、节点 source/artifact、安装模式及 editable 映射；保存 manifest 与已验证 graph 的关联。不同 build 产物必须可区分 |
| Analysis layout | 静态请求 factory 根据实际分析目标、cwd、有序搜索根和映射生成逻辑布局；manifest 保留种类、符号链接与大小写语义，合法物化重定位单独验证 |
| Configuration closure | ty 请求构造层闭合有效配置、优先顺序、发现边界及外部根；显式输入先冻结，不能静默删除。精确 ty 可执行文件与内置资源绑定 TyObservationPolicy |
| Process context | ProcessSpec/ProcessRunner 提供调用方显式固定完整环境的执行能力，TyAdapter 消费该模式；请求 factory 与启动路径使用同一份实际值。隐式继承和终端尺寸注入不得造成 key 与实际进程不符；敏感原文不进入报告/日志 |

内容 manifest 的 schema、hash、逻辑路径规范化与内容 refs 验证由同一模型/factory 提供给 producer
和 reader。先保存可复算 preimage，再算 identity；离线只能复证已保存事实及附件，不声称重验宿主文件。
投影资格不足按 `static-subject-unavailable` 返回，且不造 raw ty failure；该分支不改变已成功 prepare
的动态可执行资格。采集失败与源码/环境隔离已破坏的基础设施终止按 D038 §3 区分。

S1 用受控真实环境准备并采集 TyCheck，保存闭包后离线复算；至少同时证明合法重定位、相同版本而
不同安装内容的隔离、未闭合外部输入不启动 ty。纯 factory 矩阵与真实 adapter 正例分别记证。
此时先验证纯 comparison 准入；完整 Run typed-ref facade 在 S3 接入同一规则，不复制第二套算法。

### 2.2 S3/S4 的资源与搜索交接

- cache 在 Run 开始、baseline capture 前创建，组合层显式传入；同 evaluator 的重叠 Run 不共享隐式状态。
- lookup 只读已登记结果；IN_FLIGHT 返回 CacheMiss，collect 才原子加入或启动，只有 owner 占 ty permit。
- runner 原子保留环境；owner ty 完成及进程清理前不能 close/运行 verifier。等待者环境只有在精确动态等价且另有 clean 可用替代时才能冗余释放。
- 同 key 的实际 ty 失败保留至 Run 收尾；NO_HINT、prepare failure、缺 anchor 与 context-mismatch 不进入原始 negative cache。
- Run 停止时拒绝新 collect、取消/收拢操作并唤醒等待者；持久化已完成事实，再释放 cache。测试使用事件同步和有上限等待，不以睡眠推断顺序。
- 坐标中所有合法静态 prepared 环境保留供 oracle 使用；NO_HINT 后仍可复用非 bracket 中间点，oracle 终态即关闭所用环境，坐标/Cell/finally 清理其余环境。
- 释放后的向量允许重新 prepare 并复证投影；raw ty 命中不意味着可写环境仍存在，不授权不同 Proposal 共用 verifier 环境。
- fast path 只读直接结果；有效 history miss 的 predecessor 重验是实际 oracle，可收集附属 ty，Indeterminate 停止。只有未定界才冻结 anchor，静态阶段不更新 current。

### 2.3 S5 的结构化拒绝与迁移收尾

D038 允许在 Plan 固定具体错误码。本计划采用现有 ConfigurationError/ApplyAuthorizationError 异常体系，
给对应失败提供稳定的 typed reason，展示层不用异常自由文本反推：

| 场景 | reason / 分类 |
| --- | --- |
| 无法按当前 Schema 1 contract 解码，或 Journal 非目标 v3 | `unsupported-report-contract` / `unsupported-journal-contract`；结构化准入拒绝 |
| 保存的静态 refs、context、投影或 hint 语义不合法 | `invalid-static-evidence`；不能静默降级伪造的 COMPARED |
| 当前执行策略与报告不匹配 | `execution-policy-mismatch` |
| guidance/search provenance 与当前支持规则或所选报告不匹配 | `search-provenance-mismatch` |

其他既有错误保持所属契约；不把所有坏报告归类成 policy mismatch。online 比较不匹配仍返回
UNCOMPARED(context-mismatch)，与 reader 拒绝伪造证据分开。Schema version 保持 1，Journal 目标为 v3。
旧 witness/region report 仅在临时迁移验证中确认确定性拒绝，随后删 fixture/专属历史分支测试；
永久测试保留当前格式、未知 contract、非法权限与稳定 reason，不实现历史 parser。

## 3. 验收矩阵

本表每项填写实际 test nodeid/实验 artifact 与 E 槽结论；同一测试可覆盖多个 AC，不能省略映射。
E8 最终审计包含全部 AC，不替代各项直接证据。

| AC | 切片 / 证据 | 必需正向与错误行为证据 | 状态 |
| --- | --- | --- | --- |
| AC1 | S2 / E2 | 真实 RuntimeEvaluator + lower ty/verifier adapters；import/member/其他 regression 与 fallback/optional 路径继续完整 verifier，PASS 且保留 diagnostic/delta | 证明：`test_runtime_evaluator_runs_tests_for_a_general_static_regression`、`test_static_regression_runs_the_configured_verifier` |
| AC2 | S2、S5 / E2、E5 | FailurePolicy 只消费合格动态 authority；ReportStore 拒绝静态事实伪装 boundary/final，即使重算相关 hash 也拒绝 | 证明：`test_reader_rejects_retired_static_authority_fields`、`test_reader_rejects_broken_scope_authority`；Failure 权威仅 `configured-verifier`/`execution`/`structured` 等动态 family |
| AC3 | S2–S5 / E2–E5 | 三命令 ty 不可用仍进入 verifier；纯静态 prepare/ty/比较失败 NO_HINT，Attempt-only 往返；oracle 选择前无动态失败观测 | 证明：§8.61–§8.64 五路径、Attempt-only 往返与选择时序；E009 check 与 `test_smoke_runs_verifier_when_static_capture_is_unavailable`；3.11/3.12 资格 S_hi 不可用仍完整 PASS |
| AC4 | S2、S6 / E2、E6 | check capture 保留真实 HarnessBaseline；ty 失败仍执行 lowest-direct，prepare 失败仍按原 role/终态，Journal 聚合一致 | 证明：`test_compatibility_checker_captures_highest_before_testing_lowest_direct`、`test_check_preserves_capture_when_lowest_preparation_fails`；E009 check ty 不可用仍 declaration PASS |
| AC5 | S4 / E4 | recording evaluator 记录 fast path/静态/oracle 顺序；静态 phase verifier 为零；动态缩窗和 current 提交有精确直接 refs | 证明：`test_local_static_phase_guides_real_oracle_and_reuses_prepared_inputs`、`test_oracle_selection_records_clean_neighbor_after_suspect_rejection`、§8.61–§8.64 阶段顺序 |
| AC6 | S4 / E4 | local bracket/中点/查询上限；suspect PASS/REJECT、clean REJECT、NO_HINT 与原机械路径；提示确实改变顺序的正例 | 证明：`test_oracle_consumes_suspect_then_only_needed_clean_neighbor`、`test_unavailable_aborts_without_retry_or_false_hint`、`test_guidance_preserves_monotone_dynamic_floor` |
| AC7 | S4 / E4 | 有限单调 oracle × 静态状态矩阵，guided/mechanical floor 相同；全部 PASS、无空间内 PASS、域外 sentinel、不可用与错误方向 | 证明：`test_guidance_preserves_monotone_dynamic_floor`、`test_unavailable_and_mechanical_paths_keep_the_same_dynamic_floor`；E009 measure `all_floors_match` |
| AC8 | S4 / E4 | baseline 原始直接 PASS 复用；当前 context predecessor 重验；多坐标/sweep hint 失效；直接矛盾停止，静态矛盾只 NO_HINT | 证明：`test_committed_floor_invalidates_prior_coordinate_static_hint`、`test_later_coordinate_opens_a_new_slice_after_another_floor_commits`、`test_prior_local_contradiction_aborts_without_scanning` |
| AC9 | S3、S4 / E3、E4 | lower adapter 次数与真实 close；非 bracket 环境复用、ty 失败后运行 verifier、不同 Proposal 同 ty key 环境保留、精确冗余判定、跨 sweep 重建及对数峰值 | 证明：`test_local_static_phase_guides_real_oracle_and_reuses_prepared_inputs`、`test_join_holds_each_proposal_inputs_until_static_completion`、`test_same_ty_key_is_collected_once_and_global_local_deltas_differ`（peak≤3） |
| AC10 | S1、S5、S8 / E1、E5、E8 | 身份与 wire 目标形状、public reader/authorizer、byte-stable、merge/update、生成物；临时旧报告拒绝记录后删除迁移 fixture | 证明：byte-stable/intern/policy mismatch；`test_reader_rejects_retired_static_authority_fields` 拒绝 regions/witnesses/RUNTIME_INTERFACE_MISSING，无历史 fixture |
| AC11 | S2、S7 / E2、E7 | 原四类 resolve/install stage 与 build fallback、failed-set rejection 和完整命令 PASS；qualification 回放，静态准备事实须经真实 oracle selection 才可消费 | 证明：`tests/test_execution_qualification.py`、`test_search_preserves_exact_prepare_failure_and_emits_one_diagnostic`、`test_search_reuses_failed_cases_on_the_same_coordinate`；E009 search Markdown predecessor 为 configured-verifier |
| AC12 | S7 / E7 | 新 MkDocs check 的 Python 3.10–3.12 实际最低声明向量进入原完整 unittest；保存 argv/ProcessResult/日志，不预设 PASS | 证明：E009 check，五 cell 均 Ran 725 + NormalExit(0)，Markdown 3.3.6；§8.67 |
| AC13 | S7 / E7 | 新 MkDocs search 的 Markdown predecessor 合格动态 authority，final 完整 PASS；冻结报告经新 reader 复证，失败不手改成成功 | 证明：E009 intern 后 search，五 Cell Markdown 3.3.7 / predecessor 3.2.2 / `configured-verifier` / final PASS；报告 57,004,655 B，`ReportStore.read` 成功；128 MiB 旧 intern 被拒绝且未手改 |
| AC14 | S6、S8 / E6、E8 | CLI 语义片段与真实 status、全部 owner/生成物/导航一致，逐项审计及 D038/P042 同变更归档 | 证明（§8.70 归档后独立核验曾判定未完成：owner 仍写 `failure-execution-v3` / 统一 evaluation policy / `RuntimeWitnessAdapter`；§8.71 仅文档吸收后闭合） |
| AC15 | S1、S4、S5 / E1、E4、E5 | global regression/local unchanged、global UNCOMPARED/local COMPARED、多重集；合法 original→relaxed/同 baseline relaxed、非法 harness；Python/surface/固定坐标/context mismatch | 证明：`tests/test_static_request.py` GLOBAL/SLICE 与 context-mismatch；`test_lower_unchanged_and_context_mismatch_return_stable_no_hint`；`test_static_evaluator_uses_multiset_subtraction_against_a_frozen_baseline` |
| AC16 | S1、S4、S5 / E1、E4、E5 | anchor 精确 Proposal/TyCheck/PASS；换 anchor/policy 不复用 comparison；域外只读 anchor、单候选/无 PASS，reader 拒绝虚拟候选或 floor | 证明：`tests/test_static_request.py` 换 anchor/context-mismatch；`test_reader_rejects_broken_scope_authority[anchor]`；§8.59–§8.61 域外 anchor |
| AC17 | S1、S3、S5 / E1、E3、E5 | 固定执行对象只改外部 ty/heuristic，动态身份不变；外部 verifier timeout/classifier 改变动态身份而同静态投影可命中；producer/consumer 关联不改原事实 | 证明：`test_factories_separate_authority_observation_and_search`、`test_report_identities_split_execution_guidance_and_search`；ty 改变不改 execution policy identity |
| AC18 | S1、S5 / E1、E5 | execution 与 provenance mismatch 分开；merge/apply force 不豁免；实际 pyproject/source 改变执行对象，不能借 guidance 分离免检 | 证明：`test_fixed_source_ty_change_preserves_execution_but_rejects_cross_provenance_apply`；force 不豁免 |
| AC19 | S4 / E4 | no-change sweep 和缓存 boundary 无新增 anchor/prepare/ty；history miss 真重验定界即结束，PASS 未定界才进入 local，Indeterminate 停止 | 证明：`test_history_miss_revalidates_predecessor_without_reopening_static_guidance` 与 §8.60–§8.64 skip；E009 `measurement.json` 受控对照 |
| AC20 | S3、S4 / E3、E4 | lookup/collect/compare 与 capture/static/oracle 消费图，同 Run/Cell/key 只一次 ty；同原始事实对不同基准得到不同合法 delta | 证明：`test_same_ty_key_is_collected_once_and_global_local_deltas_differ` |
| AC21 | S1、S3、S5 / E1、E3、E5 | 六组输入/采集 policy 变化矩阵；合法重定位、不同 Proposal 同投影，相同 sdist 不同安装内容、有序根/配置、外部输入与未知版本；改变 anchor/二分不 raw miss，reader 拒绝假关联 | 证明：`test_each_input_group_changes_identity`、`tests/test_static_relocation.py`、`tests/test_static_paths.py`、`test_reader_recomputes_saved_inputs_and_rejects_forged_bindings` |
| AC22 | S3 / E3 | timeout/启动失败/异常 exit/坏输出/截断只实际运行一次；cached unavailable 与 CacheMiss 不同，后续 verifier PASS；prepare failure/缺 anchor 不入 negative cache | 证明：`test_cached_unavailable_is_not_a_cache_miss_and_does_not_rerun`、`test_missing_input_does_not_create_a_negative_observation` |
| AC23 | S3 / E3 | 实际 close 后无句柄 lookup/compare；重建并复证同/不同投影分别命中/新 ty，不同 Proposal 不能共享动态 authority | 证明：`test_global_comparison_replays_after_actual_environment_close`、`test_closed_environment_cannot_be_collected_or_verified` |
| AC24 | S3、S5 / E3、E5 | capture 前 cache 存在；顺序/重叠 Run 相同 key/payload 仍拒绝跨 Run/Cell 与关闭 refs；同 generation merge 保留来源 scopes，局部重命名不改变语义 identity | 证明：`test_membership_is_run_owned_even_with_identical_payload`、`test_query_before_capture_does_not_create_a_provisional_global_root`、`test_merge_renames_colliding_local_scope_without_joining_membership` |
| AC25 | S3 / E3 | 可控阻塞 TyOperations 成功/失败共享终态，等待者不占 permit；owner 输入存活、禁止在途 close/verifier；取消/deadline/未建模异常均收拢资源、唤醒等待者 | 证明：`test_overlapping_consumers_share_one_actual_terminal`、`test_static_owner_cannot_close_or_mark_its_inputs_tested`、`test_unmodeled_exception_wakes_consumers_and_prevents_retry` |
| AC26 | S5、S6 / E5、E6 | Journal v3 无 Failure 仍保留静态审计、不计失败；diagnose execution/selection 两关联、prepare 无 Proposal、原 producer/log、缺日志、report 命中不拼 Journal；关联不改变 Failure ID | 阶段/selection/skip Journal 往返与 diagnose execution/selection 通过，§8.62–§8.65；`test_prepare_unavailable_diagnose_does_not_fabricate_ty_check` 与 `test_report_side_index_resolves_typed_producer_logs` 已补；缺日志展示见 smoke |

AC7 用独立 oracle 定义期望 floor，不能复制搜索实现生成期望 probe 列表。并发时序用公开 runner/
StaticEvaluator seam 与可控 lower adapter 观察，不断言锁/私有字典。遵循 TestClass、参数化与
公开语义断言；删除因 witness 移除而失去目标契约用途的测试，保留动态失败和安全准入负例。

## 4. 验证命令与执行规则

以下为 Plan 起草时的命令模板；实际执行与精确结果见 §8，E8 见 §8.70。cwd 固定 `/home/llh/pf`；PF CLI、pytest、uv 与真实进程资格
检查先核对命令/fixture/写入目标风险，再按 AGENTS.md 在沙箱外运行。沙箱、网络、缓存或解释器
问题单独记录，不能伪报产品失败或删减验收。uv cache 使用 `/tmp/pf-uv-cache`。

### 4.1 分片回归

新增测试文件名为本 Plan 拟定，在对应 slice 创建后执行；若组织调整，回填实际路径与命令。
既有 `test_static_transition.py` 中仍有价值的多重集/身份案例迁入新静态测试，旧 witness 专属用例删除。

```sh
# E1：identity/comparison foundation 与真实静态输入闭包
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_static_subject.py tests/test_static_comparison.py tests/test_policy.py tests/test_environment.py tests/test_ty_adapter.py tests/test_process.py
# E2：动态 authority 与三命令基础路径
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_evaluation.py tests/test_failure.py tests/test_baseline.py tests/test_check.py tests/test_smoke.py tests/test_prepare_execution.py tests/test_configured_verifier.py tests/test_pytest_pruning.py
# E3：cache 与资源时序（新增文件），现有 EvaluationCache 改验动态契约
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_static_cache.py tests/test_static_lifecycle.py tests/test_evaluation_cache.py tests/test_verification.py
# E4：算法性质、真实消费者图和 predecessor 回归
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_static_guidance.py tests/test_search.py tests/test_search_coordinator.py tests/test_search_workflow.py tests/test_search_space.py
# E5：离线闭包与授权
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_report.py tests/test_report_schema.py tests/test_report_artifacts.py tests/test_report_workflows.py tests/test_search_space_report.py tests/test_authorization.py tests/test_runlog.py tests/test_secure_runlog.py tests/test_windows_runlog.py
# E6：命令、持久化聚合与用户展示
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_cli.py tests/test_execution_run.py tests/test_verification.py tests/test_terminal.py tests/test_explain_terminal.py tests/test_diagnose.py tests/test_end_to_end.py
# E7：当前真实资格回放（新增与保留）
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_static_guidance_qualification.py tests/test_execution_qualification.py tests/test_uv_qualification.py tests/test_pytest_pruning.py
```

切片完成时运行其相关套件，记录 exit code/计数/耗时与失败原因；不等待全部生产修改后才运行。
生成物在 S5 按现有 generator 更新，之后 `--check` 作为门禁；不手写 schema 或 examples。

```sh
.venv/bin/python scripts/generate_report_schema.py
.venv/bin/python scripts/generate_report_schema.py --check
.venv/bin/ruff check src tests scripts
.venv/bin/ty check
git diff --check
```

### 4.2 最终全套门禁

按当前 CI 支持的 Python 3.10、3.11、3.12 顺序运行全套；使用隔离环境，保留仓库 `.venv`。
实际新建目录、解释器/工具版本与展开后的命令记录到 E8。以下 shell 变量只指向本次 mktemp 目录。

```sh
pf_d038_gates=$(mktemp -d /tmp/pf-d038-gates-XXXXXX)
UV_PROJECT_ENVIRONMENT="$pf_d038_gates/py310" UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.10 --group test pytest --no-testmon --cov=pf --cov-branch --cov-report=term-missing --cov-report=json:"$pf_d038_gates/coverage.json" --junitxml="$pf_d038_gates/py310.xml" -q --tb=short tests
UV_PROJECT_ENVIRONMENT="$pf_d038_gates/py311" UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.11 --group test pytest --no-testmon --junitxml="$pf_d038_gates/py311.xml" -q --tb=short tests
UV_PROJECT_ENVIRONMENT="$pf_d038_gates/py312" UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.12 --group test pytest --no-testmon --junitxml="$pf_d038_gates/py312.xml" -q --tb=short tests
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build --out-dir "$pf_d038_gates/dist"
```

沿用仓库 90% coverage 门槛；核对迁移后仍适用的公开行为/分支覆盖，不要求保留被删除 witness
实现的覆盖行。全套验证集合限 `tests`，不收集 `experiments/mkdocs` 的第三方测试。
最终执行 Ruff、ty、generator `--check` 与文档检查；修复导致实质代码变化时再重跑受影响门禁。

## 5. 真实实验与冻结证据

### 5.1 MkDocs check/search（AC12、AC13）

1. 实施完成后为新实验分配下一个可用 E 编号与 `docs/experiments/data/<新编号>/`；本轮不预占编号、不创建虚假运行附件。
2. 核对 `/home/llh/pf/experiments/mkdocs` 的 Git HEAD、工作区 patch、有效 PF 配置与 E008 冻结附件；保存 PF commit/未提交 diff、ty/uv 内容身份、精确解释器、source snapshot、完整候选快照与实际向量。若来源已变化，从 E008 记录的源码加配置构造隔离副本，不回写用户实验目录来配合历史结果。
3. PF 进程从仓库根启动，调用现有公开 CheckWorkflow/SearchWorkflow，给 CheckRequest/SearchRequest 传绝对 MkDocs root；拟定的 `qualify_static_guidance.py` 负责组合与保存结果。当前 CLI 从 cwd 取项目，不存在 `--project`/`--report` 路径参数，脚本不发明产品 CLI 参数。
4. 在绝对路径的隔离实验副本运行，避免覆盖原 `package-floor.json`、latest Journal/Index 或 E008 附件；冻结日志及报告后用新 ReportStore.read 验证。脚本具体参数、隔离副本路径与完整调用命令在执行前写入 E7，不把模板当真实命令。
5. 优先重跑五个 Python Cells（3.8–3.12）；AC12 至少覆盖 3.10–3.12 的 declared-lowest 原完整 unittest，保留实际终态与日志。PF 自身仍用受支持的 3.10–3.12 解释器运行，不把目标项目 Cell 等同 PF 运行解释器。
6. 新 search 验证 Markdown 的直接 predecessor 与 final 完整 PASS refs，记录实际安装向量及完整 verifier argv。新 registry snapshot 与历史不同就明确标注，不预设旧 floor 或新的 PASS；未获得 AC13 直接证据则保持待验收。

E7 分开保存：实验说明、源码/配置/工具身份、执行命令与退出码、check 每 Cell 结果、search 冻结报告、
reader 审计、ProcessResult/安全日志及成本摘要。沿用 E008 的固定原完整 unittest 命令；fixture
回放和替身测试不能替代这两项真实运行。日志按 D007 脱敏与定位，不复制敏感宿主环境原文。

### 5.2 受控静态采集与净收益观察

`qualify_static_guidance.py` 同时提供受控小项目的真实 prepare/ty/verifier 回放，复用仓库已有
本地 fixture/临时目录设施：证明实际静态闭包可用、缓存复用及动态 PASS 独立。针对缓存并发的
精确次数和时序仍由 E3 可控 adapter 测试负责，不让 registry 波动承担确定性断言。

`measure_d038_guidance.py` 经相同公开 evaluator/search seam 对比 guidance 与 mechanical；
无 guidance 仅是实验组合，不增加配置开关。固定 source/Cell/候选快照、ExecutionPolicy、机械
阈值与资源配置，分别记录新 Run 的冷/热底层缓存条件；不跨 Run 导入 TyCheck 或动态证据。
两组 search provenance 可以不同，不能为了对照伪装成同一 generation 或跨组 merge/apply。

## 6. 成本记录口径

每个坐标/sweep 保存以下计数、分母与时长；分母为零标注不适用：

| 观察项 | 记录方式 |
| --- | --- |
| direct fast path | cache 定界、真实 predecessor 重验及其结果、静态阶段跳过次数分别记录 |
| static 查询 | 逻辑查询数、实际 prepare/ty 次数、positive/negative hit、IN_FLIGHT 等待；owner 进程耗时只记一次 |
| hint 产出/消费 | 有效 hint / 实际启动静态阶段；至少选择一个特殊 oracle probe 的 hint / 已产出 hint |
| NO_HINT | reason、原始不可用原因及占已启动静态阶段比例；单列 anchor-unavailable 与 context-mismatch |
| 环境 | oracle 复用静态环境的数量/请求比例、prepare 重建数、坐标/Cell/Run 保留峰值 |
| 动态执行 | 已有 direct evidence 复用、新增 verifier 次数与耗时分开；static-probe 不计 verifier |
| 资源与总时间 | raw facts 数量、cache 内存/并发等待、投影采集成本、prepare/ty/verifier 累计进程时间与真实 wall time |

净收益只从受控对照得出；E008 的 18.9% 是固定 probe/平均成本算例，不作本次成本上限或 wall time
预测。没有正收益阈值，不因为 NO_HINT 少、hint 多或对数次查询就宣称更快，也不增加自动禁用策略。

## 7. Owner、生成物与归档清单

每片记录 owner 的目标修改点；S8 在实现证据齐全后吸收稳定规则，现行 owner 不提前冒充已交付行为。

| owner / 文档 | 吸收事项 |
| --- | --- |
| [D001](../../designs/D001-pf.md) | 三命令动态结论、辅助静态可用性、Failure ID diagnose 读取范围 |
| [D002](../../designs/D002-pf-implementation.md) | StaticEvaluator/Run cache、共享投影/准入、runner 资源 owner、composition 与公开测试 seam |
| [D003](../../designs/D003-pf-search-algorithm.md) | 两阶段搜索、direct fast path、独立窗口、原始事实与物化环境生命周期；替换不变量 10、EvaluationCache 表和 region/promotion |
| [D004](../../designs/D004-pf-ty-enhancement.md) | 六组静态投影、采集子身份、显式请求环境、原始事实/cache、比较准入、global/local baseline；移除 witness/AST 分类 |
| [D005](../../designs/D005-pf-failure-and-diagnose.md) | 仅动态分类与 identity、静态 failure 独立、保留既有 resolve/install/build/failed-set 资格 |
| [D007](../../designs/D007-pf-process-output.md) | 显式进程环境执行能力与静态 producer 日志关联所需的 interface 说明；完整性、安全与脱敏资格不放宽 |
| [D008](../../designs/D008-pf-verification-run.md) | Run cache 生命周期、static/oracle role 与聚合、Journal v3、Index 时序及两条 Failure 辅助关联 |
| [D012](../../designs/D012-pf-harness-relaxation.md) | 执行身份、prepare 事实供静态投影、installed/interpreter 内容采集关系；harness 构造语义保持 |
| [D014](../../designs/D014-pf-report-schema.md) | 三类 policy、scope membership、静态 wire/离线闭包、结构化拒绝、generation/merge/update/apply |
| [D037](../../designs/D037-pf-candidate-search-policy.md) | 候选 DSL 由 SearchPolicy 消费，精确安装选择与搜索 provenance 分开 |
| [D006](../../designs/D006-pf-cli-enhancement.md)、README/README.zh/CONTEXT | 动态结论与辅助静态术语、阶段/窗口、诊断与 explain；词汇引用 owner，不复制规则 |
| `docs/schemas/`、`docs/examples/` | 现有 generator 生成的 Schema 1 complete/incomplete 投影，保留 required-nullable 义务 |

归档前逐 AC 审计、移除临时迁移 fixture、修复现行入链，D038/P042 在同一完成变更中移入
`docs/archived/designs/` 与 `docs/archived/plans/`，更新现行与归档索引。现有历史归档与 E008 附件
不改写。归档后的 D038 §12 仍是历史评审记录，当前状态以前言与完成证据为准。

## 8. 决定、偏差与执行证据

### 8.1 已确定的实施安排

| 日期 | 决定 | 理由 / 影响 |
| --- | --- | --- |
| 2026-09-07 | 先完成 S1 静态投影真实正例和共享离线模型，再改 search；S3 同片完成 AC25 | 采集闭包与资源时序是后续 guidance 的前提，不能只靠回退分支证明基础能力 |
| 2026-09-07 | Schema 1 直接替换，Journal v3；拒绝原因按 §2.3 固定 | D038 允许 Plan 映射具体错误码；不添加历史兼容 reader |
| 2026-09-07 | MkDocs 用公开 workflow 的绝对 root 与隔离副本从 PF 根启动 | 当前 CLI 以 cwd 选择项目；避免新增路径参数及覆盖历史 report/Journal |
| 2026-09-07 | 新静态模块/测试/实验脚本路径为本 Plan 的实施安排 | 文件重组可记偏差，缓存等价性、authority 与 AC 不由 Plan 改写 |

当前无目标契约偏差。若实现发现 D038 语义必须改变，先记录具体事实与受影响 AC，返回 Design
决策；不能以测试便利、缓存命中或成本目标自行缩减六组输入、权限校验或真实实验。

### 8.2 证据槽

| 槽 | 当前结果 | 回填要求 |
| --- | --- | --- |
| E0 Plan 文档检查 | 通过；见下方记录 | 起点/dirty scope、AC1–AC26 覆盖、链接、diff check；只证明计划与导航 |
| E1 身份/静态输入/比较 | 通过；见 §8.4–8.26、`test_static_subject.py` / `test_static_request.py` | 实际命令、public cases、真实闭包样本、manifest/离线复算、限制 |
| E2 动态 authority | 通过；见 §8.27–8.34、`test_evaluation.py` | §8.27–8.34：纯 verifier 终态、原失败资格、check 两次 prepare、三命令 ty 不可用、非 verifier authority reader 拒绝 |
| E3 cache/生命周期 | 通过；见 `test_static_cache.py` / `test_static_lifecycle.py` / `test_static_ownership.py` | 同 key 次数、typed refs、负缓存、环境复用/关闭、取消与等待者时序 |
| E4 两阶段搜索 | 通过；见 `test_static_guidance.py` / `test_search.py` / `test_search_coordinator.py` | 有限矩阵范围、hint 顺序正例、窗口/边界、跨 sweep/前驱证据 |
| E5 report/Journal/授权 | 通过；content/subject intern 与 64 MiB 内 MkDocs 报告 | codec/reader、重算 hash 后非法语义、byte-stable、scope merge/update、稳定 reason 与生成物 |
| E6 CLI/diagnose | 通过；diagnose 56 列需显式 Console height 才让 Rich 认 width | role/summary/退出码、两关联、日志持久化、缺日志/无关联行为 |
| E7 真实运行/成本 | 通过；E009 check+search | 新实验路径、冻结身份、完整命令与原始结果、check/search reader refs、受控成本口径 |
| E8 全套/最终审计 | 通过；`/tmp/pf-d038-gates-RwBFnd` | 三解释器测试/coverage/build、文档与生成物检查、逐 AC 结论、owner 吸收/归档路径 |

### 8.3 E0 — 2026-09-07 计划建立与文档验证

- 在 `/home/llh/pf` 只读核对当前 HEAD、D038、相关 owner、现有公开实现/测试与 CI 命令；已有 D038 和索引改动保留。
- 新建 P042，列出 S1–S8、六组静态输入 producer、AC1–AC26 与 E1–E8、测试命令、真实实验/成本和 owner 归档工作；同步 D038 与文档索引的 Plan 入口和待实施状态。
- `node /tmp/pf-d038-plan-JoeFrv/check.mjs`：exit 0；3 份文档、68 个本地链接（含 1 个锚点）通过，AC1–AC26 各有一条映射，8 个切片均待开始；状态一致性、尾部空白与最终换行通过。该临时脚本只读文档，不是生产验收设施。
- `git diff --check`：exit 0。对未跟踪文件分别执行 `git diff --no-index --check /dev/null docs/plans/P042-pf-static-guidance-authority.md` 与 `git diff --no-index --check /dev/null docs/designs/D038-pf-static-guidance-authority.md`，均无 whitespace 诊断；exit 1 表示文件相对空文件有差异，另由上述脚本确认空白检查。
- `git diff --exit-code -- src tests scripts docs/archived`：exit 0，生产代码、测试、脚本和已有归档均未改变。
- 结论：本轮计划与导航检查完成；E1–E8 未执行，生产实施、行为验证、owner 吸收及归档尚未开始。未提交或推送。

### 8.4 E1 — 2026-09-07 S1 基础实施（未完成）

- 用户要求实现 D038；复核 Design 已接受、Plan 已存在及工作区后开始 S1。起始已有
  `docs/README.md`、D038、P042 改动保留，未提交或推送。
- `ProcessSpec.environment_mode` 增加显式完整环境语义。`SubprocessRunner` 在此模式下不继承
  宿主变量、不注入终端尺寸，继续按 D007 脱敏；拒绝重复变量名与 removal 的歧义组合。
  其他动态进程继续使用原继承语义。尚未将 ty 生产入口切换到显式请求：须先闭合其有效配置与输入。
- 新增 `schemas/static.py` 的内容 manifest 与 `static_subject.py` 的实际文件采集基础：
  规范逻辑路径、目录成员、文件字节摘要和已登记逻辑根内符号链接；拒绝未知格式、无效摘要、
  缺失父目录、未闭合链接和特殊文件。按实际内容区分安装结果，可脱离原目录离线复算 manifest
  identity。此 manifest 是六组投影的内容基础，**不是完整 StaticSubject 或 comparison 准入**。
  调用方仍须保证根来自冻结输入和 clean prepare；不能把任意宿主目录采集视作已冻结。
- `uv run pytest tests/test_process.py tests/test_schemas.py --no-testmon -q`：exit 0，
  **228 passed in 1.05s**。真实子进程验证显式环境隔离、终端值保留与 Process Log 脱敏。
- `uv run pytest tests/test_static_subject.py --no-testmon -q`：exit 0，
  **10 passed in 0.04s**。真实临时文件覆盖安装字节变化、重定位、跨根链接、离线 round-trip、
  外部输入与 FIFO 拒绝；不代表真实 EnvironmentFactory/ty 的 S1 闭包正例已完成。
- `uv run ruff check src/pf/adapters/process.py src/pf/schemas/evaluation.py tests/test_process.py`
  及 `uv run ruff check src/pf/schemas/static.py src/pf/static_subject.py tests/test_static_subject.py`：
  均 exit 0。`uv run ty check` 在新增 collector 后首次报告两处 Pydantic 构造上下文引起的 lambda
  参数联合类型错误；改为排序已声明类型的 entries 后重跑，exit 0。
- 上述 uv 命令均 cwd=`/home/llh/pf`，风险核对后按 AGENTS.md 在沙箱外执行。
- 剩余：S1 六组静态投影完整模型/producer、三类 policy 与执行身份链、真实 prepare/ty 正例、
  comparison harness/context 准入及其 reader；随后依次 S2–S8。当前没有任何 AC 被判定最终通过，
  不归档 D/P，不把此次基础测试代替 AC12/13 真实 MkDocs 验收。

### 8.5 E1 — 2026-09-07 policy 与六组静态输入模型（S1 仍未完成）

- 复核工作区与 §8.4：上一目标轮有生产改动与直接测试证据，属于 progress，无运行中实验需等待。
- 新增 `schemas/policy.py`，实现 ExecutionPolicy、GuidancePolicy、SearchDerivationPolicy 与
  Guidance 内 TyObservationPolicy 子对象的规范 preimage/domain identity；`policy.py` 提供
  相应 factory。原始 key 由 `static_subject.ty_check_key` 从已验证 StaticSubject 与采集策略生成，
  无 Proposal、verifier、anchor 或 search 字段。
- ty 工具 identity 绑定精确版本、内容 manifest 与可复算 executable ref；采集策略保存有效
  TyConfig/owned-options/协议/首次观察规则。Guidance 验证子身份与完整子对象一致，search 绑定
  其 GuidanceIdentity、候选策略和机械阈值。此处是基础对象；旧统一 evaluation policy 的生产
  调用仍待 S1 身份链迁移，不能据这些 factory 测试声称生产动态 authority 已独立。
- 将 TyAdapter 的 owned-option 校验移至共享 `ty_options.validate_ty_args`，adapter 与采集策略
  factory 共用，非法配置仍产生 ConfigurationError。该迁移不添加配置兼容分支。
- `schemas/static.py` 增加 D038 六组输入的规范模型：源码/成员映射与 SourcePlan、精确目标解释器、
  完整安装节点/内容关联、保留顺序的分析布局、配置发现/优先级闭包、带摘要和逻辑路径的显式环境。
  StaticSubject 必须显式给出 projection version 与六组输入；reader 拒绝内容冲突、悬空路径、
  未闭合安装图、未登记文件、错误 Python 与环境变量大小写冲突。manifest 的公开 `entry_at`
  从已登记闭包解析符号链接；解释器与 ty executable 必须落到实际文件。
- 对内容 entry、安装节点与 policy 的 required-nullable 投影补齐序列化，验证
  `model_dump(mode="json", exclude_none=True)` 后 StaticSubject 仍可完整恢复并复算。
  schema/examples 的完整报告接入和生成检查仍由 S5 执行。
- 当前 `selected_candidate_evidence_digest` 已直接散列精确 SelectedCandidate 列表，未包含
  CandidateSnapshot digest；已读 `schemas/project.py`、EnvironmentFactory 与 ReportStore 的
  当前消费链。其完整 source/execution/provenance 验收仍待整条身份链迁移，不凭此局部核对关闭 AC。
- `uv run pytest tests/test_policy.py tests/test_ty_adapter.py --no-testmon -q`：exit 0，
  **56 passed in 0.26s**；随后六组模型专项
  `uv run pytest tests/test_static_subject.py tests/test_policy.py --no-testmon -q`：exit 0，
  **46 passed in 0.14s**。
- required-nullable 与路径准入改动后的最终相关命令：
  `uv run pytest tests/test_static_subject.py tests/test_policy.py tests/test_ty_adapter.py tests/test_process.py --no-testmon -q`，
  exit 0，**136 passed in 1.20s**。六组输入/配置变化矩阵是纯 factory/schema 证据，
  文件采集与子进程测试各自验证下层行为，不是真实 EnvironmentFactory/ty 闭包验收。
- `uv run ruff check src/pf/policy.py src/pf/schemas/policy.py src/pf/schemas/static.py src/pf/static_subject.py src/pf/ty_options.py src/pf/adapters/ty.py tests/test_policy.py tests/test_static_subject.py`：
  首次发现测试中一处 unused import，删除后重跑 exit 0；`uv run ty check`：exit 0。
  uv 命令均从 `/home/llh/pf` 风险核对后沙箱外运行。
- 剩余优先顺序：真实 prepare 的已复证安装/source/interpreter 内容 producer → 配置与完整进程
  环境闭包、真实 ty 请求正例 → GLOBAL/SLICE harness/context 纯准入与离线测试 →
  ExecutionPolicy/Attempt/Environment/Proposal/动态 evidence 链及 report provenance 迁移。
  然后继续 S2–S8。所有 AC 仍待完整验收，无目标契约缩减、提交或归档。

### 8.6 E1 — 2026-09-07 真实 prepare 静态输入采集（S1 仍未完成）

- 核对工作区与当前 prepare/ty seam；上一轮有模型/factory 改动和验证，属于 progress。
  本轮继续 S1，未重启任何已有实验或进程。
- 新增 `adapters/static_inputs.py`。StaticInputsAdapter 在 clean PreparedEnvironment 上运行
  `python -I -S -B` 的只读安装 inventory，显式空进程环境；读取完整解释器版本/ABI、实际
  executable、stdlib/shared-library 位置、distribution 文件列表和 direct_url。复证安装版本图、
  source plan 与 package/prepare 关联后，采集实际文件内容，形成 source/target/installed-world
  三组输入。失败只返回 PreparedStaticInputsUnavailable 与原 inspection process；没有
  TyCheck/compatibility disposition。已 tested 或错误 SourcePlan 在进程启动前拒绝。
- manifest 新增公开 `for_roots`，选择逻辑根并保留注册符号链接的闭包；world 内容保留 editable
  源码映射及文件关联，未被节点拥有的环境/解释器支持文件仍绑定内容。工作区 package mapping
  从 PackagePlan/SourcePlan 的明确成员建立，不把测试 fixture 中任意 pyproject 当 workspace member。
- 初次真实测试失败：错误地要求项目自身一定在 dependency ResolutionPlan.packages 中。
  当前 prepare 合同允许该节点只由安装图及项目源码闭合。修正为对项目自身复证 direct_url
  指向精确 prepared.package_root，保存实际 editable 模式/source mapping；其他节点继续要求
  与 final ResolutionPlan 和已验证安装图一致。临时定位用重新抛出已移除，恢复 typed unavailable。
- `uv run ty check --help`：exit 0，核对本机 ty 的 `--python`、`--config-file`、
  `--extra-search-path`、`--typeshed` 和配置发现说明。确认 `--project` 仍向父目录发现配置，
  不能把指定项目目录误当配置发现闭包；真实 TyRequest 构造仍待实现。
- `uv run pytest tests/test_static_inputs.py --no-testmon -q`：首轮 1 failed；定位重跑
  `uv run pytest tests/test_static_inputs.py --no-testmon -q --tb=short` 暴露上述项目节点假设。
  修复后同命令 **1 passed in 0.59s**；补充 project-only/registry-dependency 两项后
  **2 passed in 1.94s**。
- 最终相关验证：
  `uv run pytest tests/test_static_inputs.py tests/test_static_subject.py tests/test_policy.py --no-testmon -q --tb=short`：
  exit 0，**48 passed in 1.76s**。真实项目在临时目录通过 UvAdapter/EnvironmentFactory
  解析、安装 uv_build 构建的 editable demo；第二项还安装 idna 3.10。验证实际节点/文件、
  解释器内容和 source mapping；修改安装 METADATA 后版本/节点不变而内容 identity 改变；
  close 后三组输入可独立反序列化。测试清理所有创建的 prepared 环境与 snapshot。
- `uv run ruff check src/pf/adapters/static_inputs.py src/pf/schemas/static.py tests/test_static_inputs.py`：
  首次有一处 unused import，修正后 exit 0。`uv run ty check`：exit 0。
  uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 未完成项：完整配置发现/进程环境闭包与真实 ty request；已安装 direct_url、editable pth、
  脚本 shebang 等生成文件中物化根的合法规范化；原始字节 manifest 当前仍会区分这些路径字节，
  **不能据此声称重新 prepare 能命中同一个 TyCheckKey**。还需 consumer association 复证、
  GLOBAL/SLICE harness/context 准入、执行身份链迁移，随后 S2–S8。此次真实 prepare 是 E1 的
  三组输入证据，不替代完整 StaticSubject/ty 正例、AC23 或 MkDocs check/search 验收。

### 8.7 E1 — 2026-09-07 安装路径重定位与 RECORD（S1 仍未完成）

- 从当前工作区和 §8.6 继续；上一轮为真实采集实现与测试进展，无需等待的外部任务。
- 临时只读实验 `uv run python /tmp/pf-d038-relocation.py` 在两个独立 prepare 上比较实际
  manifest，exit 0。最初差异为 venv 激活脚本、editable pth、direct_url、RECORD、
  `_virtualenv` pyc 与 uv_cache 时间戳。脚本使用受控临时 demo、同一 snapshot，每次独立
  EnvironmentFactory；finally 关闭两个环境和 snapshot，不修改仓库实验或历史报告。
- 新增 `static_relocation.py`，对已采集字节先复核摘要，再把限定安装文件中的物化根替换成
  typed logical-root token；保留全部非路径文本。范围为路径型 pth、direct_url 的 file URL、
  venv 激活脚本和 console script 的 shebang。源码字面量、可执行 pth 和未知元数据保持原始
  字节身份。路径前缀必须匹配边界；规范文本及其 digest 使用独立 domain，并保存可离线复算 preimage。
- RECORD 仅对需要重定位的目标行复证实际 SHA-256 与 byte size，再以 typed file-value 关联
  替换这两个派生字段。无法复证的 RECORD 保留精确原始内容；不相信自报 checksum 来获得
  等价性。reader 验证文件 profile/位置、CSV 列、文件名、hash/size 关联同一目标、目标规范
  digest 与闭包；拒绝重算外层 identity 后的伪造。不能以此把不合法的安装声明升级为动态 authority。
- StaticInputsAdapter 已消费这条规范化路径；manifest.for_roots 同时闭合 logical-root 和
  RECORD 文件关联。UvAdapter 的 interpreter/installed-graph 检查加入 `-B`，避免只读检查
  自己写入 pyc；目标 ExecutionPolicy 增加该 inspection 规则事实。生产执行策略链迁移仍待完成。
- `uv run pytest tests/test_static_relocation.py tests/test_static_subject.py --no-testmon -q --tb=short`：
  基础文本阶段 **36 passed in 0.14s**；补齐 RECORD 后 **38 passed in 0.16s**。
  覆盖不同根/不同根字符串长度的规范身份相等、实际内容变化仍隔离、原始字节复证、
  source/可执行 pth 不归一、未知元数据保留和 reader 伪造拒绝。
- 接入后的最终相关命令：
  `uv run pytest tests/test_static_inputs.py tests/test_static_relocation.py tests/test_uv_adapter.py --no-testmon -q --tb=short`：
  exit 0，**127 passed in 3.07s**。
- 重跑 `uv run python /tmp/pf-d038-relocation.py`：exit 0；两次 fresh prepare 现在只剩
  uv_cache 时间戳及其 RECORD 行差异。它们是本次实际非路径内容差异，未被忽略；此实验没有
  证明 fresh prepare 的完整 StaticSubject 相同或 raw ty 命中。正式缓存/重建验收仍在 AC23。
- `uv run ty check` 在扩展 token union 后首次报告三处联合类型未收窄；修正类型分支与已验证
  digest 的非空断言后重跑 exit 0。
  `uv run ruff check src/pf/schemas/static.py src/pf/static_relocation.py src/pf/adapters/static_inputs.py src/pf/adapters/uv.py src/pf/schemas/policy.py tests/test_static_relocation.py`：
  exit 0。`git diff --check`：exit 0。uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 为下一步配置闭包核对了 ty 的[官方配置发现规则](https://docs.astral.sh/ty/configuration/)、
  [环境变量](https://docs.astral.sh/ty/reference/environment/)与
  [配置 reference](https://docs.astral.sh/ty/reference/configuration/)：无 tool.ty 的 pyproject
  不停止配置查找；ty.toml 优先，用户配置与项目配置合并，数组有顺序；TY_CONFIG_FILE 可
  取代自动发现。ignore files 还可能来自用户/global gitignore，PYTHONPATH 参与模块查找。
  这些资料用于输入闭包实现准备，不能替代本机固定 ty 的真实行为测试，未据此改写用户配置。
- 剩余：完整配置/进程环境/分析布局 producer、真实封闭 ty request、consumer association、
  GLOBAL/SLICE harness/context 准入、执行身份链迁移；其后 S2–S8。没有 AC 最终关闭或归档。

### 8.8 E1 — 2026-09-07 配置选择、冻结与分析根（S1 仍未完成）

- 从当前实现和 §8.7 继续；上一轮为重定位实现及直接验证进展。没有待恢复的实验进程。
- `uv run python /tmp/pf-d038-ty-config-probe.py`：exit 0；本机工具为 **ty 0.0.74**。
  在不同目录放置互相冲突的 vendor stub，核对显式 config-file 的相对 extra-paths 解析基准。
  该脚本仅运行受控临时文件与本机 ty，自动清理。
- 新增 `static_configuration.py`：TyConfigurationResolver 仅使用显式传入的环境查询配置，
  优先处理 TY_CONFIG_FILE（只展开已绑定变量和 HOME，不借宿主环境补值）；自动路径按
  user → selected project 合并，ty.toml 优先，无 tool.ty 的 pyproject 继续向上查找。
  保存实际查询路径、所选文件的原始文本/role/format、合并后的 TOML 和显式 analysis_root。
  文件读前后核对大小/mtime/ctime，未知环境、读取或解析不闭合返回静态不可用，没有 ty failure fact。
- `materialize_ty_configuration` 将已捕获的输入与有效配置写入全新的 caller-owned 目录，
  不重读原配置、不覆盖已有目录；产出配置内容 manifest 与有序 input refs。目录（含失败时
  部分写入）由 prepared/run owner 清理。原文件删除后仍能物化，同内容的不同目录 identity 相同。
- 初始真实配置对照为 **9 passed in 0.20s**。补充父目录配置后发现 **2 failed, 10 passed**：
  原生发现以所选项目配置所在目录作为分析根，仅复制合并配置仍以 package 为 project 根会
  改变相对 extra-paths。修正为显式保存 analysis_root，物化调用方同时传递该根。
  `StaticAnalysisLayout.project_root` 作为必需输入绑定这一实际分析条件，并验证内容闭包。
  修复后 **12 passed in 0.33s**，包含 package/parent × pyproject/ty 四项真实 ty 对照。
- 真实对照使用相互冲突的 package/vendor 与 parent/vendor 验证路径基准；原生发现与冻结
  config-file 的完整 GitLab diagnostic 文档相等，并断言仅有预期 invalid-assignment，用户与
  项目 allowed-unresolved-imports 数组均生效。测试明确传 `--no-respect-ignore-files`，
  因而**不证明默认 ignore/global gitignore 输入闭包**，也不等同完整 TyCheckKey 验收。
- 最终相关命令：
  `uv run pytest tests/test_static_configuration.py tests/test_static_subject.py tests/test_policy.py --no-testmon -q --tb=short`：
  exit 0，**58 passed in 0.44s**；配置物化接入真实对照后同命令同为 58 项通过。
- `uv run ruff check src/pf/static_configuration.py src/pf/schemas/static.py tests/test_static_configuration.py tests/test_static_subject.py`：
  exit 0；`uv run ty check`：exit 0；`git diff --check`：exit 0。
  uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 剩余：ignore/global gitignore 的冻结及发现边界、配置/环境中的外部搜索根与路径展开、
  完整显式进程环境和分析顺序；将这些与已有真实 prepared inputs 组合为 StaticSubject 和
  TyObservationPolicy 所对应的实际 ty request。随后 consumer association、GLOBAL/SLICE
  harness/context 准入、执行身份链迁移以及 S2–S8。未将上述配置对照当作完整 S1 通过，未提交或归档。

### 8.9 E1 — 2026-09-07 外部搜索根冻结与真实路径准入（S1 仍未完成）

- 新增 `static_paths.py`：从冻结配置、显式环境与 cwd 解析有序 first-party/extra/PYTHONPATH/
  typeshed 输入、ignore 开关及实际展开变量。未绑定变量、未知选项、无法闭合的路径返回静态
  不可用；不借宿主环境补值。PYTHONPATH 保留字面语义，并记录不参与搜索的缺失路径。
- 新增 `static_external.py`：保留请求的搜索顺序与嵌套引用，将未知外部目录完整复制到新的
  caller-owned 目录；包含隐藏文件，按已捕获的逻辑链接重定位 symlink，复制后完整复证内容。
  未闭合链接拒绝；拒绝把输出目录建在任何输入树内，避免修改原始输入或递归复制自身。
  失败时部分目录仍由调用方清理；本模块不自行关闭 prepared environment。
- `ty_options.py` 将 project 根和 fix/add-ignore/watch 纳入采集方所有权；`-c=...`、带引号
  TOML key 及内联 table 不能绕过既有 interpreter/output 所有权检查。配置拒绝发生在进程前。
- 受控实测脚本 `uv run python /tmp/pf-d038-path-probe.py`、
  `uv run python /tmp/pf-d038-default-roots.py`、
  `uv run python /tmp/pf-d038-expansion.py` 均 exit 0。本机 ty 0.0.74 的事实：
  配置 extra-paths 保留并排在 CLI 路径之前；CLI config override 路径排在专用 flag 路径前，
  不随这两类 argv 先后变化；配置相对路径基于 analysis_root，CLI 相对路径基于 cwd；
  默认 first-party 顺序为 src → project-named → python → root（各项有存在/包结构条件）；
  CLI 搜索路径展开绑定变量，而 PYTHONPATH 不作第二次变量展开。
- 将优先级/路径基准与变量展开观察转为永久真实 ty public tests：冲突 stub 的最终
  GitLab diagnostic 结果验证三层搜索路径和两个 argv 顺序；避免依赖 ty 的诊断 prose。
  `uv run pytest tests/test_static_paths.py --no-testmon -q --tb=short`：
  exit 0，**26 passed in 0.28s**。这些测试明确关闭 ignore，仅验证对应路径语义。
- 恢复时上一轮工具输出丢失，未据此声称历史通过；含最新目录安全检查重跑局部四文件：
  **72 passed in 0.60s**。加入真实路径测试和嵌套配置所有权检查后的最终命令：
  `uv run pytest tests/test_static_external.py tests/test_static_paths.py tests/test_ty_adapter.py tests/test_static_configuration.py --no-testmon -q --tb=short`：
  exit 0，**84 passed in 0.78s**。
- `uv run ruff check src/pf/static_paths.py src/pf/static_external.py src/pf/ty_options.py tests/test_static_paths.py tests/test_static_external.py tests/test_ty_adapter.py`：
  exit 0；`uv run ty check`：exit 0。uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 核对[官方 ignore 配置](https://docs.astral.sh/ty/reference/configuration/#respect-ignore-files)
  和[文件排除说明](https://docs.astral.sh/ty/exclusions/)，并执行
  `uv run python /tmp/pf-d038-ignore-probe.py`：exit 0。受控显式 HOME 下，没有 `.git` 时
  本地/父目录 `.ignore`、`.gitignore` 和全局 gitignore 都能排除文件；增加项目 `.git`
  后父目录 `.gitignore` 不再生效，但父目录 `.ignore` 仍生效，`.git/info/exclude` 生效。
  这证明仅源码树快照并不足以闭合默认 ignore 行为；不能用禁用默认 ignore 作为产品替代。
- 剩余：ignore/global gitignore 冻结和祖先发现边界、完整显式进程环境，以及这些 producer
  与 prepared inputs 的完整 TyRequest/StaticSubject 组合；consumer association、比较准入、
  动态身份链与 S2–S8 仍待实施。没有最终关闭 AC、提交或归档。

### 8.10 E1 — 2026-09-07 全局 ignore 重放与显式环境输出（S1 仍未完成）

- 上一轮有路径冻结、真实测试和 Plan 更新，是实质进展；本轮无待恢复的后台操作。
- 从当前 §4.4 六组输入及 `static_configuration`/`StaticContentCollector` 接口继续。
  核对 [ignore 上游发现实现](https://github.com/BurntSushi/ripgrep/blob/master/crates/ignore/src/gitignore.rs)，
  仅作为调查线索；本机固定 ty 的行为由受控脚本和永久测试直接验证，不把 upstream master
  当作本机版本证明。
- `uv run python /tmp/pf-d038-global-ignore-probe.py`：exit 0。ty 0.0.74 实测读取 HOME、
  XDG、GIT_CONFIG_GLOBAL、GIT_CONFIG_SYSTEM 指定配置；相对 excludesFile 按 cwd 解析，
  在非 core section 的该字段仍被识别，带空格的 quoted 路径未排除目标。不能用完整 Git INI
  语义替代此工具的观察输入解析。
- 新增 `static_ignores.py`：POSIX 显式 HOME/XDG 与配置路径下，按实际发现顺序捕获原始字节、
  缺失事实及最终 patterns。捕获时复核文件身份/内容稳定；不确定的 excludesFile 形式或
  非 POSIX 环境返回 `static-subject-unavailable`。不读取隐式 HOME，不猜测未绑定变量。
- 物化到新的私有 caller-owned 目录，保存捕获输入和 discovery facts，构造固定 HOME/XDG/
  GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM，最终指向已冻结 patterns；没有 patterns 时也使用
  一个确定的空文件终止全局 fallback。之后无需原始宿主文件。配置/CLI 中其他 HOME 展开
  必须先解析再重定位，不能直接覆盖环境后声称完整请求等价。局部/祖先 ignore 尚未由本模块闭合。
- `uv run pytest tests/test_static_ignores.py --no-testmon -q --tb=short`：exit 0，
  **15 passed in 0.36s**。六项真实 ty 对照验证 global/home/xdg/system/default/absent 分支
  和优先级，删除原文件后冻结重放的完整 GitLab diagnostics 相等，未关闭 respect-ignore-files。
  另验证不同物化位置内容身份相等、输入删除后仍可物化及不闭合输入不可用。
- 发现完整显式环境会把所有值当 secret literals，普通数字值可能破坏 JSON protocol。
  `EnvironmentVariable.sensitive` 默认 true；调用方可显式标记公开值，runner 只将敏感值
  追加为 secrets，原全局 SecretRedactor 仍生效。环境 header 仍只保存变量名。
  D007 §4 已吸收此稳定输出规则；尚未把完整环境 producer 接入实际 ty 请求。
- `uv run pytest tests/test_process.py tests/test_static_ignores.py --no-testmon -q --tb=short`：
  exit 0，**68 passed in 1.27s**，包含真实子进程数字 JSON、秘密隐藏、cache/log 一致性。
  最终 `uv run pytest tests/test_process.py tests/test_schemas.py tests/test_static_ignores.py --no-testmon -q --tb=short`：
  exit 0，**244 passed in 1.39s**。
- `uv run ruff check src/pf/static_ignores.py src/pf/adapters/process.py src/pf/schemas/evaluation.py tests/test_static_ignores.py tests/test_process.py`：
  exit 0；`uv run ty check`：exit 0。uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 剩余：局部/祖先 ignore 边界、完整固定进程环境和路径重定位整合、真正的 request factory
  与六组完整 StaticSubject 正例；比较准入、执行身份链及 S2–S8 仍待实施。未最终关闭 AC、
  未提交或归档；全局 ignore 正例不能作为完整采集闭包验收。

### 8.11 E1 — 2026-09-07 原始静态事实与最小离线 codec（S1 仍未完成）

- 上一轮已完成全局 ignore 与过程输出代码/直接测试，为实质进展；本轮核对当前 D038
  §4.4–4.6、§8.2 和现行 lower TyAdapter 输出后，推进同一 S1 的原始事实与 codec 工作。
- 新增 `schemas/ty_fact.py` 与 `ty_fact.py`：`TyCheckFact` 保存规范诊断多重集与正常终态，
  `TyCheckUnavailable` 保存 typed reason/终态，无兼容性 disposition。两者身份使用独立 domain，
  绑定 StaticSubjectIdentity、TyObservationPolicyIdentity 和实际观察，不绑定 Proposal、
  baseline、comparison、Run、耗时或 process/log locator。message/severity 仍为展示信息，
  不替代稳定 diagnostic identity；终态和诊断重数仍参与原始事实 identity。
- lower `TyCheck` 暂继续作为 adapter 完整输出；新 projection factory 只接受真实 ty 结果，
  非 ty stage 或缺过程观测不能产生原始 ty failure。区分 timeout、start-failed、signal、
  exit-code、output-incomplete、invalid-output 和 terminal-unavailable；未将 prepare failure、
  缺 anchor 或比较失败导入 negative-fact 类型。生产 StaticEvaluator 尚未消费新 factory。
- `TyFactDocument` 是最小离线 input/fact codec：保存完整 StaticSubject 和采集 policy，
  从 preimage 复算输入关联与 fact identity。它不签发 Run ref、不验证尚未保存的 producer
  Proposal 关联、不重读文件，不作为完整 ReportStore/Journal reader 的替代。
- TyDiagnostic 的 required-nullable line/column 加入明确 null 序列化与 schema 标注，
  保证 exclude_none 的保存路径仍可恢复外部/未知列位置事实。
- `uv run pytest tests/test_static_subject.py tests/test_policy.py --no-testmon -q --tb=short`：
  exit 0，**59 passed in 0.22s**。加入 nullable 语义后的相关最终命令：
  `uv run pytest tests/test_static_subject.py tests/test_policy.py tests/test_ty_adapter.py tests/test_schemas.py --no-testmon -q --tb=short`：
  exit 0，**276 passed in 0.55s**。覆盖原文件删除后的 byte-stable codec、重数、展示/耗时
  不改变 identity、终态改变 identity、失败矩阵和伪造输入/终态/identity 拒绝。
- `uv run ty check` 初次提示 ToolFailure.process 可空和联合类型收窄问题，修正缺过程拒绝、
  显式参数与 ProcessResult 分支后通过；新增测试随后提示 fact union 未收窄，补足 kind 对应
  类型断言后再次 exit 0。`uv run ruff check src/pf/schemas/ty_fact.py src/pf/ty_fact.py src/pf/schemas/evaluation.py tests/test_static_subject.py`：
  exit 0。uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 剩余完整范围不变：祖先 ignore/完整显式请求与真实六组整合正例、consumer association/
  GLOBAL-SLICE 共享准入、执行身份链，以及后续 S2–S8。codec 测试不能证明真实 TyRequest
  已封闭、Run 缓存已运行或任何动态 authority 已迁移。未最终关闭 AC、未提交或归档。

### 8.12 E1 — 2026-09-07 祖先 ignore 准入与进程环境投影（S1 仍未完成）

- 上一轮原始事实/codec 有代码和验证进展；本轮返回完整请求入口，核对当前配置闭包和
  Process context owner，未恢复或重启任何实验进程。
- `static_ignores.py` 新增 `TyIgnoreBoundaries`：检查目标所属完整已登记目录内的本地 ignore
  内容保持在 manifest 中；外部祖先 `.ignore`/`.gitignore` 或未闭合 gitdir/commondir 拒绝。
  对空外部 Git 标记，要求 info/exclude 与 commondir 均缺失并登记目录种类，不能只凭 `.git`
  存在拒绝正常临时源码目录。保存逻辑 discovery profile，物化路径不进入其内容身份。
- 边界 guard 保存实际外部缺失查询和空 Git 目录，必须在请求启动前和观察结束后复证；
  外部输入出现、Git 标记种类变化或读取失败使该静态请求失效，不改变动态 prepare 资格。
  该 guard 不取代源码/安装内容 owner 的保留与复证，也未接入生产启动路径。
- 首次局部测试 **1 failed, 19 passed in 0.37s**：本环境 `/tmp/.git` 是真实存在的只读
  目录，过宽的 Git 标记拒绝使正例不可用。改为验证上述实际外部输入后 **21 passed in 0.37s**。
  没有删除、修改或忽略宿主 `/tmp/.git`。
- 增加组合真实 ty 对照：本地 `.ignore`、`.gitignore`、`.git/info/exclude` 与冻结全局
  gitignore 同时生效，仅预期 kept.py 产生诊断。删除原 HOME 后重放完整 GitLab 结果相等；
  respect-ignore-files 保持默认。`uv run pytest tests/test_static_ignores.py --no-testmon -q --tb=short`：
  exit 0，**22 passed in 0.38s**。这验证 ignore 输入组合，不代表完整 StaticSubject/请求已接入。
- 新增 `static_process.py`：从精确 EnvironmentVariable 元组和调用方显式 scalar/path/path-list
  声明绑定 Process context。路径必须先展开为注册内容中的绝对路径；保留 path-list 顺序，
  明确环境值与 sensitive 输出处理共同进入摘要。返回用于 ProcessSpec 的实际变量和仅含
  摘要/逻辑路径的保存投影；不从任意字符串猜测路径，不读取隐式宿主环境。
- `uv run pytest tests/test_static_process.py tests/test_static_ignores.py --no-testmon -q --tb=short`：
  exit 0，**30 passed in 0.39s**。验证实际执行值不变、不同物化根投影相等、顺序/值/敏感性
  改变隔离、未登记路径拒绝；报告投影不暴露秘密原文或物化根。
- `uv run ty check` 先指出 environment payload 字典推导不包含 bool，显式标注 JSON payload
  类型后重跑 exit 0。`uv run ruff check src/pf/static_ignores.py src/pf/static_process.py tests/test_static_ignores.py tests/test_static_process.py`：
  exit 0。uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 下一步直接实现请求工厂，将现有 prepared inputs、配置/外部根/ignore/环境 producer 串联，
  完成实际 ty 启动、复证与六组 StaticSubject 正例；不将这些独立 helper 测试当作整合完成。
  比较准入、consumer association、执行身份链及 S2–S8 仍待实施，未最终关闭 AC、提交或归档。

### 8.13 E1 — 2026-09-07 完整请求工厂与真实六组采集正例（S1 仍未完成）

- 上一轮 ignore/进程输入有代码与测试进展；本轮按 §8.12 直接组合已有模块，新增
  `static_request.py` 的 StaticRequestFactory/StaticTyRequest，未再以独立 helper 代替整合。
- factory 从 clean PreparedEnvironment 重新采集安装/源码/解释器事实，选择并冻结配置，
  物化外部搜索根与全局 ignore，登记本地/祖先 ignore 边界；将已解析路径写入有效配置，
  生成显式环境、逻辑分析目标/搜索顺序/root placements，以及精确 ty version/可执行内容
  的 TyObservationPolicy。有效配置中的相对路径保持逻辑布局，原输入文件仍单独绑定。
- 把 CLI config overrides 合入冻结配置并使用已解析的路径顺序；保留其他合法参数及其值。
  明确变量展开到已选外部根的路径值随该根重定位；仅支持已闭合的环境语义，未知环境输入、
  尚无不可变事实的缺失 PYTHONPATH、无法物化的外部根返回静态不可用，不默默删除用户输入。
  物化目录是 prepared runtime 下新建的 caller-owned static 目录，失败清理由 owner 负责。
- 最终采集内容与最初的 source/target/installed-world 三组事实逐一复证，避免组装期间变化
  造成旧 subject 对新文件的错绑；工具内容也在 version 查询和最终采集间复证。
  StaticTyRequest 保留运行时 roots/原始 manifest/ignore guard，subject 中仅保留规范事实。
- `TyAdapter.observe(request)` 消费新显式 ProcessSpec，在实际 ty 前后复证请求；不完整/变化
  的请求返回 StaticContentUnavailable，不冒充 TyCheckUnavailable。现行 check 消费者暂未
  迁移，但两入口已共用 GitLab 解析，未复制诊断算法。具体 Run 资源占用/取消与完整隔离失败
  的 runner 处置仍须在 S3 接入，不凭本 adapter 的局部 guard 声称生命周期验收完成。
- `uv run pytest tests/test_ty_adapter.py --no-testmon -q --tb=short`：exit 0，
  **41 passed in 0.24s**。首条真实 factory 整合：
  `uv run pytest tests/test_static_request.py --no-testmon -q --tb=long`：exit 0，
  **1 passed in 2.72s**；加入初始/最终输入复证后的 request+inputs：**3 passed in 3.97s**。
- 真实 uv prepare → complete request → ty → TyFactDocument → 环境 close → 离线 byte-stable
  恢复正例已成立，六组输入均由实际 producer 提供。默认本地 ignore 生效，预期源码
  invalid-assignment 保留。第二项加入用户配置 `$STUBS` 外部源，capture 后删除原 stub 与
  原 HOME，实际 ty 仍得到相同预期诊断；**2 passed in 3.61s**。
- 安装 metadata 字节改变使旧请求在 ty 启动前失效；caller 清理旧静态物化目录后重新
  capture 得到不同 StaticSubject，同 source、target、installed node 元数据和采集 policy。
  新请求实际 ty diagnostics 相同仍有不同请求身份；recording ProcessRunner 证明旧请求
  和 tested prepared 均未新增 ty check。此处未据此声称跨环境 raw cache 命中或动态证据复用。
- 最终相关命令：
  `uv run pytest tests/test_static_request.py tests/test_static_inputs.py tests/test_ty_adapter.py --no-testmon -q --tb=short`：
  exit 0，**45 passed in 6.96s**。
  `uv run ruff check src/pf/static_request.py src/pf/adapters/ty.py tests/test_static_request.py`：exit 0；
  `uv run ty check`：exit 0。uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- S1 已有真实完整采集正例，仍待完整请求的合法重定位/更多实际参数整合、producer/consumer
  关联、GLOBAL/SLICE 共享准入与执行身份链；其后 S2–S8、真实 MkDocs、owner 归档及全部
  AC 审计仍待完成。未最终关闭 AC、未提交或归档。

### 8.14 E1 — 2026-09-07 完整请求重定位与身份迁移入口核对（S1 仍未完成）

- 上一轮已有真实完整请求与离线正例，为实质进展。本轮继续 E1 重定位门槛，未开始修改
  动态 policy 的生产字段或规则，未将部分采集通过解释为完整 D038 完成。
- 两次 `uv run python /tmp/pf-d038-relocation.py` 均 exit 0；第二次增加源 pyproject/src
  mtime/ctime 直接观测。两次 fresh prepare 的 mtime 相同，ctime 不同，uv_cache.timestamp
  对应该差异；uv_cache 及其 RECORD 行是真实安装内容差异，继续保留，不更改 uv、Snapshot
  或 EnvironmentFactory 来强造同一 key。
- `tests/test_static_request.py` 增加已采集真实安装的搬移/重建 fixture：完整复制源码与
  安装目录，只用已验证 StaticTextProjection 重写路径引用和相应 RECORD hash/size，
  symlink 按闭合逻辑目标重定位；uv_cache 与其他非路径字节逐字保留。
  fixture 验证被重写文件原字节与其已保存 projection 一致，不根据文件名盲目替换。
- 原环境关闭后，新 PreparedEnvironment 通过真实 StaticInputsAdapter 和完整 request
  factory 重新复证；新旧 StaticSubject、TyObservationPolicy 均相等，实际 ty diagnostics
  和原始 fact identity 相等。新旧根名称长度不同，实际旧目录已释放。此处是同一已观察
  安装内容的合法重定位正例，不是 fresh uv build 必然相等或已实现 raw cache 命中。
- `uv run pytest tests/test_static_request.py --no-testmon -q --tb=short`：exit 0，
  **3 passed in 8.18s**；`uv run ruff check tests/test_static_request.py`：exit 0；
  `uv run ty check`：exit 0。uv 命令均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 动态身份入口核对发现：ReportStore._policy_identity 当前从 Proposal/Attempt 直接
  返回统一 evaluation policy，ApplyAuthorizer 用同一值作 report 授权；Journal package
  policy 也共用该字段。不能仅替换 policy factory 为 ExecutionPolicy，否则会丢掉
  report merge/apply 对 guidance/search provenance 的独立检查。
- 后续 S1 身份迁移必须同步拆开 report 的 execution/provenance 关联及对应 writer/reader/
  authorizer 检查，不能把此安全闭包推迟到 S5；S5 仍负责完整静态区域、Journal v3 及关联
  寻址。此为当前调用方依赖的实施顺序细化，不改变 D038 的三类 policy/authority 语义。
- 其余完整范围继续保留：共享 GLOBAL/SLICE 比较准入和 consumer association、执行身份
  迁移、S2–S8、真实 MkDocs、成本观测和 owner 归档。未最终关闭 AC、未提交或归档。

### 8.15 E1 — 2026-09-07 动态身份链与报告 provenance 拆分（S1 仍未完成）

- 上一轮有完整重定位实测进展；本轮按 §8.14 同步迁移执行身份与报告调用方，未仅替换
  最外层 hash。生产、测试与生成脚本的 `evaluation_policy_identity` 名称清除，直接使用
  `execution_policy_identity` → ExecutionPolicy.identity；不提供旧字段别名或双 reader。
- AttemptIdentity、Cell Failure scope、Journal package/scopes 及其消费者切换 execution 字段。
  EnvironmentIdentity 现在显式绑定实际 Attempt ID、计划与安装图，Proposal 保持引用该完整
  环境身份。Attempt 的 source/Cell/request/harness/选中 artifacts 与 ExecutionPolicy
  经这条身份链继续绑定，不因计划 semantic digest 不含 verifier 设置而漏掉执行契约变化。
- ReportIdentity 保存完整 ExecutionPolicy，reader 从该模型复算动态 Attempt/Proposal/
  Failure；report generation 同时绑定执行策略 identity。报告整体 provenance 独立保存，
  merge/update/reintern 保留它和完整 execution preimage；apply 分别检查 execution 与
  provenance，force 不豁免。ReportStore 不再从任意 Proposal 推断整体 report policy。
- `report_provenance_identity` 暂保留原报告规则/配置的保守投影，以免迁移中丢失授权围栏；
  它不是最终 GuidancePolicy/SearchDerivationPolicy 的完整保存形式。旧 witness/region 行为、
  failure-execution-v3 的现行报告判定与 Journal v2 仍待 S2/S5 替换，不能将新增目标 ExecutionPolicy
  描述符解释为生产静态拒绝权限已删除。S1/全目标均未标完成。
- 第一次 environment/report/authorization/workflows 局部验证 **9 failed, 106 passed in 4.12s**：
  修正旧统一 hash 的镜像断言和两类授权错误断言后，同命令 **115 passed in 3.99s**。
  新身份矩阵另发现 **2 failed, 43 passed in 0.35s**：Attempt 已隔离但 Environment/Proposal
  仅绑定计划/图；上述 Attempt ID 绑定修复该实际遗漏，没有弱化测试。
- `uv run pytest tests/test_environment.py tests/test_projection.py tests/test_report_schema.py tests/test_authorization.py --no-testmon -q --tb=short`：
  exit 0，**251 passed in 4.44s**。固定源码下 ty timeout/args、search 配置改变不改三层执行
  身份，verifier timeout/command 改变均隔离；同静态配置改变下，report 动态 cell results
  相同而 provenance/generation 不同，merge/apply（含 force）拒绝。authorization 追加正例后
  独立命令 **49 passed in 3.62s**。
- 正式全量验证过程中依次发现并修复生成物与 nullable 闭包：**11 failed, 2412 passed in
  55.05s**（schema/examples 漂移）；生成后 **1 failed, 2428 passed in 53.08s**（nullable
  字段清单）；增加 timeout=None 正例后 **1 failed, 2429 passed in 52.95s**（reader null
  准入）。补齐 ExecutionPolicy/ResolutionConfig/TyConfig 标注、reader 精确路径和必要诊断
  null，移除旧的“所有字段都不能为 null”粗断言。新增 Field 首次漏导入使生成和测试收集失败，
  随即修复；不将其记录为生成成功。
- `uv run python scripts/generate_report_schema.py` 已重新生成 schema 与两个示例；
  `uv run python scripts/generate_report_schema.py --check` exit 0。
  最终 `uv run pytest tests --no-testmon -q --tb=short`：exit 0，**2430 passed in 52.68s**。
  最后的诊断 reader nullable 补充另由
  `uv run pytest tests/test_report_schema.py tests/test_static_subject.py --no-testmon -q --tb=short`
  验证：exit 0，**189 passed in 0.65s**。
- `uv run ruff check src tests scripts`、`uv run ty check` 均 exit 0。
  所有 uv 命令 cwd=`/home/llh/pf`，风险核对后沙箱外执行。没有修改历史实验/qualification
  JSON 来伪造新运行结果；当前正式测试通过不等同新 MkDocs 或目标权限已上线。
- 剩余：完整静态 policy/provenance wire、producer/consumer 关联与 GLOBAL/SLICE 共享准入，
  S2–S8、真实 MkDocs、成本观测、owner 吸收归档及逐项 AC 审计。未提交、推送或最终关闭 AC。

### 8.16 2026-09-07 · S1 preparation 请求摘要共享接口

- 将 EnvironmentFactory 私有请求摘要计算移入 resolution owner 的
  `resolution_request_digest`，project/environment 生产请求均调用该接口。
  接口只接收可保存的输入事实，不依赖临时 SourceSnapshot、PackagePlan 或 process handle；
  后续 consumer/reader 可使用同一规则复算。保留现行 domain 和全部 preimage 字段。
- `uv run pytest tests/test_environment.py tests/test_resolution.py --no-testmon -q --tb=short`：
  exit 0，**76 passed in 0.24s**。
  `uv run ruff check src/pf/environment.py src/pf/resolution.py`、`uv run ty check`：exit 0。
  uv 命令均 cwd=`/home/llh/pf`，核对测试/检查风险后沙箱外执行。
- 该步仅提供共享摘要接口，尚未建立静态 consumer 关联或 GLOBAL/SLICE 准入；
  不构成比较、缓存或权限迁移的完成证据。S1 和整体目标保持未完成。

### 8.17 2026-09-07 · S1 可保存 preparation 语义及 harness 输入

- 新增 resolution owner 的 `ResolutionPlanEvidence`，保存 kind/request/context/packages/
  direct_harness/semantic_digest，提供完整 JSON 往返。生产 ResolutionPlan 继承该证据模型，
  共用排序、harness 归属/实际 artifact 与 semantic digest 校验，另外验证 native/process。
  离线复算不需要伪造 process 或临时 native lock 内容；此投影本身不声称进程运行成功。
- `relax_harness` 直接接收保存的 declarations、HarnessBaseline、ResolutionPlanEvidence 和
  SourcePlan；`original_harness` 接收 declarations/Cell，删除未使用的 SourcePlan 参数。
  EnvironmentFactory 与所有调用者迁移，不加旧接口兼容层。生产计划仍是可接受的证据子类。
- 新增证据往返与请求/kind/packages/digest 篡改拒绝测试；现有 project-owned/harness-only
  ceiling 正例改用 JSON 往返后的证据，验证可以从保存输入执行实际 D012 变换。
- `uv run pytest tests/test_environment.py tests/test_resolution.py tests/test_report_schema.py --no-testmon -q --tb=short`：
  exit 0，**229 passed in 0.67s**。
- `uv run pytest tests/test_harness.py tests/test_uv_adapter.py tests/test_environment.py tests/test_resolution.py --no-testmon -q --tb=short`：
  首次 exit 0，**220 passed in 0.64s**；去掉 original_harness 无用参数后重跑 exit 0，
  **220 passed in 0.56s**。补充离线 harness 输入测试后
  `uv run pytest tests/test_harness.py --no-testmon -q --tb=short`：exit 0，**24 passed in 0.08s**。
- 最终 `uv run ruff check src tests scripts`、`uv run ty check`：exit 0。
  uv 命令均 cwd=`/home/llh/pf`，风险检查后沙箱外执行。
- consumer 的完整 Attempt/Proposal/subject/prepare 关联、GLOBAL/SLICE 准入仍待实现；
  以上不是比较、Run cache 或权限迁移的完成证据。S1/总目标保持未完成。

### 8.18 2026-09-07 · S1 实际静态请求的 preparation 关联

- 新增 `StaticPreparationEvidence`，实际 StaticRequestFactory 每次成功 capture 保存该证据。
  共享 validator 复证 Attempt/Proposal/source snapshot/Cell/精确解释器/SourcePlan/执行策略、
  resolution context、project/environment semantic plans 以及 Environment/Proposal 身份摘要。
- 复证实际 installed names/versions 与 Proposal 图、planned source/artifact/dependencies 与
  静态投影，environment plan 保持 project selections，额外根项目绑定源码 mapping；
  managed vector 排序唯一并匹配实际安装版本，exact Attempt 与 Proposal 向量相等。
  不把 metadata 的未求值 Requires-Dist 图当成与 plan 活动依赖完全相同的结构。
- 真实请求测试增加完整 preparation JSON 往返，临时环境关闭后仍可复读，以及六类
  Proposal 关联字段篡改拒绝。原有实际 relocation、外部配置冻结和安装文件变化继续通过。
- `uv run pytest tests/test_static_request.py --no-testmon -q --tb=short`：初次 exit 0，
  **3 passed in 8.62s**。
  `uv run pytest tests/test_static_request.py tests/test_static_subject.py --no-testmon -q --tb=short`：
  exit 0，**44 passed in 8.76s**。补充 project selections/source mapping 后重跑真实请求测试，
  exit 0，**3 passed in 8.77s**。最终 `uv run ruff check src tests scripts`、
  `uv run ty check`、`git diff --check` 均 exit 0。
- uv 命令 cwd=`/home/llh/pf`，风险核对后沙箱外执行。尚缺完整 harness/request 预像复算、
  raw fact 引用及 Run scope membership；当前关联证据不能独立授予 GLOBAL/SLICE 可比较性。
  S1 和整体目标保持未完成，未提交或推送。

### 8.19 2026-09-07 · S1 preparation 请求与 harness 复算

- PreparedEnvironment 保存实际 exact selection 输入（非 exact 为 None），资源 relocation
  传递同一保存事实。StaticPreparationEvidence 保存原 harness declarations、同 Cell baseline
  与 selected candidates；不尝试从摘要倒推原输入。
- 关联 validator 复算 active declarations、project-only/empty baseline 边界、highest 产出的
  baseline，或 relaxed 消费 baseline 的 D012 requirements；再通过 resolution owner 的共享
  函数复算 project/environment 请求摘要。exact selection 复证 artifact evidence digest、
  requested vector 与实际 project selected artifacts。
- `uv run pytest tests/test_static_request.py tests/test_environment.py --no-testmon -q --tb=short`：
  exit 0，**48 passed in 9.06s**。真实请求测试扩展 highest/lowest-direct/exact-vector 后，
  `uv run pytest tests/test_static_request.py --no-testmon -q --tb=short`：exit 0，
  **9 passed in 27.49s**。该真实 fixture 是 empty-harness/project-only，不能据此声称非空
  harness 或非空 selected artifacts 的集成矩阵已完成；原 D012 单元测试仍覆盖其变换规则。
- `uv run ruff check src tests scripts`、`uv run ty check`：exit 0；补充 selected artifact
  校验后局部 ruff、最终 ty 与 `git diff --check` exit 0。
  `uv run pytest tests --no-testmon -q --tb=short`：exit 0，**2441 passed in 74.70s**。
  selected artifact 校验在全量收集后追加，非空 artifact 集成证据仍缺失，不以本次全量替代。
- uv 均 cwd=`/home/llh/pf`，风险核对后沙箱外执行。仍需比较层选择组/声明语义、baseline
  producer、raw fact 与 scope 引用闭包及 GLOBAL/SLICE 准入；S1/全目标未完成。

### 8.20 2026-09-07 · S1 非空 preparation 集成与 exact artifact 修复

- 增加真实 idna managed dependency + idna project-owned/packaging harness-only 声明的
  highest、lowest-direct、exact-vector 三类 preparation → static capture → ty → 资源关闭 →
  离线 JSON reader 用例，实际静态诊断为空。补上上一节明确缺失的非空输入集成证据。
- 首次 `uv run pytest tests/test_static_request.py::TestNonemptyStaticPreparation --no-testmon -q --tb=short`：
  **3 failed in 1.22s**；fixture 错误要求 registry selected_artifact 非空，改用实际允许的
  universal wheel 构造 exact selection。重跑 **1 failed, 2 passed in 4.88s**，暴露生产
  preparation validator 对 direct URL wheel 的错误 kind 相等要求。
- 用 diagnosing-bugs 定位：临时透出被 capture 转换的异常，最小 exact 用例两次均
  **1 failed in 1.51s**。实际 candidate.kind=wheel，pylock direct URL selected.kind=archive，
  filename/locator/SHA-256 相同。修复复用 EnvironmentFactory 的实际 artifact 绑定语义，
  不把 pylock 表的类别误当成候选分发形式；保留 filename/locator/hash/version 校验。
  全部 DEBUG-d038-exact-request 临时探针已移除，capture 不可用处理恢复。
- 同一三用例命令修复后 exit 0，**3 passed in 5.30s**。继续增加 coherent tampering：
  改 project request 后重算 plan semantic digest 和 Proposal ID，仍必须因保存输入无法导出
  该 request 而拒绝；同命令最终 exit 0，**3 passed in 5.70s**。
  最终 `uv run ruff check src tests scripts`、`uv run ty check`、`git diff --check` exit 0。
- uv 命令 cwd=`/home/llh/pf`，风险核对后沙箱外执行。该集成通过不代表 GLOBAL/SLICE
  比较准入或 Run cache 已实现；S1/全目标仍未完成。

### 8.21 2026-09-07 · S1 比较用 declaration/policy 证据与 harness 关系

- StaticPreparationEvidence 保存完整 ExecutionPolicy、原 RequirementDeclarations 与
  selected_test_group；复算 execution policy identity，验证 active declaration 闭包、fixed IDs、
  managed coordinate 覆盖和空选择组边界。StaticRequestFactory 直接填充生产 PackagePlan 的
  事实，新增字段不进入 StaticSubject/raw ty identity。
- 新增共享纯规则 `static_admission.admit_harness_relation`，先比较 Cell、选定组、原始
  harness declarations、SourcePlan 和 resolution config，再验证合法方向：original/original、
  同 baseline relaxed/relaxed、实际 highest producer→消费其 baseline 的 relaxed，以及
  empty/project-only。两端自身的 D012 request 复算仍由 preparation validator 承担；
  非空 relaxed→original 不准入，不依赖调用方自报 mode 字符串。
- 此函数仅为两种比较的 harness 子条件，不授予完整 GLOBAL/SLICE 准入或 subtraction。
  后续仍需静态共同 context、固定 reference、SLICE PASS/window/其他坐标及 scope 关联。
- 新增字段后 `uv run pytest tests/test_static_request.py::TestNonemptyStaticPreparation --no-testmon -q --tb=short`：
  exit 0，**3 passed in 5.95s**。加入实际 highest 静态证据、正向/自比较、反向拒绝和不同
  selected group 拒绝后，同命令 exit 0，**3 passed in 7.80s**。
- `uv run ruff check src tests scripts`、`uv run ty check`、`git diff --check`：exit 0。
  `uv run pytest tests/test_static_request.py tests/test_harness.py --no-testmon -q --tb=short`：
  exit 0，**36 passed in 35.93s**。
  uv 命令 cwd=`/home/llh/pf`，风险核对后沙箱外执行。
  S1 与整体目标仍未完成。

### 8.22 2026-09-07 · S1 共同静态 context 与 raw consumer 投影

- 真实 highest→exact 输入对照确认 source.content 差异为准备过程管理的 pyproject；
  target/layout/process 不变。临时字段名/路径探针两次 exact 用例通过（3.47s、3.04s），
  DEBUG-d038-common 已移除，不保留调试输出。
- StaticConfigurationInput 显式绑定实际 effective_file（要求为 precedence 末项并在内容闭包中）；
  原配置 bytes 保持 raw identity。比较原始文档变化时，使用其实际 materialized effective
  配置并继续核对外部/ignore 输入，不能把合法 managed dependency 改写误作 ty 配置变化。
- `admit_common_static_context` 共享验证 source snapshot/package mapping/SourcePlan/原声明、
  精确 target、逻辑布局、进程语义、harness 关系，保持非 governed-pyproject 源码内容相等。
  不要求 installed-world 相等；准备所管理的 pyproject 变化由原声明/来源/request 证据约束。
- 新增 StaticConsumerEvidence，明确绑定实际 preparation 与完整 TyFactDocument，复证相同
  StaticSubject 投影；新增含 observation policy 的共享 consumer context 函数。
  语义关联本身不授予 Run membership，不伪造 producer process，也不授予完整比较准入。
- `uv run pytest tests/test_static_subject.py tests/test_static_request.py::TestNonemptyStaticPreparation --no-testmon -q --tb=short`：
  初次 exit 0，**44 passed in 7.88s**。加真实 `[tool.ty.rules]` 后非空三用例命令 exit 0，
  **3 passed in 8.45s**；验证 exact 改写仍可共同准入，filesystem case 变化拒绝。
  增加 consumer JSON 往返与投影不一致拒绝后，同一组合命令最终 exit 0，
  **44 passed in 9.26s**；`git diff --check` exit 0。
- `uv run ruff check src tests scripts`、`uv run ty check`：exit 0。uv 命令均
  cwd=`/home/llh/pf`，风险核对后沙箱外执行。GLOBAL 固定 reference、SLICE PASS/window/
  固定坐标、scope refs 与最终 comparison 状态/增量仍待实现，S1 和整体目标未完成。

### 8.23 2026-09-07 · S1 GLOBAL 语义比较与离线结果重放

- 新增 GlobalComparisonContext 与 GLOBAL 比较文档：保存 subject/reference 的合法 consumer
  关联、GuidancePolicy 和显式 highest Proposal identity。共享 derive 先核对实际 highest
  preparation、固定 reference identity、observation policy 与共同 context，再做多重集差分。
- 三种互斥结果：COMPARED 保存 STATIC_UNCHANGED/STATIC_REGRESSION、规范增量 identity
  多重集与 GLOBAL domain fingerprint；UNCOMPARED 保存 reference-missing/reference-unavailable/
  context-mismatch；当前实际 ty 失败为 UNAVAILABLE，原因来自原 typed raw fact。
  该文档覆盖已形成静态请求的事实；未形成 subject 的采集不可用仍由外层采集状态保留。
- fingerprint 绑定两端实际 Proposal/static subject/raw fact identities、显式 context、
  GuidancePolicy 与增量；空增量合法，message/severity 不进入 identity。reader 共享 derive
  重放并拒绝伪造 context/result，不只验证调用者自报的 COMPARED 标签。
- 非空真实三类 preparation 测试增加实际最高版本 ty 观测和 GLOBAL empty delta 正例，
  并用保存事实单元变体覆盖 repeated diagnostic multiset、display-only 变化、timeout、
  reference 缺失及 coherent comparison context 篡改拒绝；合成事实不声称实际额外执行。
- `uv run pytest tests/test_static_request.py::TestNonemptyStaticPreparation --no-testmon -q --tb=short`：
  exit 0，**3 passed in 13.07s**。`uv run ruff check src tests scripts`、`uv run ty check`、
  `git diff --check`：exit 0。uv cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 该文档仅完成 GLOBAL 的语义比较，不自行建立 Run/Cell scope membership 或选择固定
  highest ref；两者必须由后续共享 scope resolver 验证。SLICE 的实际 PASS/window/固定坐标、
  未形成请求状态以及生产 StaticEvaluator/Run cache 接入仍待完成。S1 和总目标保持未完成。

### 8.24 2026-09-07 · S1 SLICE 语义约束与共用 comparison reader

- 将 GLOBAL-only 文档/derive 替换为统一 StaticComparisonDocument/derive_static_comparison，
  context 以 GLOBAL/SLICE 判别；没有旧接口别名。差分实现与结果模型共享，fingerprint
  domain 根据比较种类区分并绑定对应完整 context。
- SliceComparisonContext 显式保存活动 dependency、排序唯一且排除 d 的 fixed coordinates、
  规范版本有序且唯一的实际 artifact candidate window，以及可缺失的 SliceAnchorPass。
  从真实 VerifierRun 创建 anchor 时仅接受 VerifierPass，保存 Proposal/ExecutionPolicy 关联；
  rejected 不能被转换成 anchor。scope/process provenance 仍由外层后续绑定。
- SLICE 准入要求共同 static context、anchor PASS 与 reference Proposal/执行策略匹配、
  同一 resolver context、实际 exact subject、两端其他 managed coordinates 等于冻结值，
  window 不高于 anchor，subject 的实际 selected artifact 属于 window；缺 PASS 为
  UNCOMPARED(anchor-missing)，其余不匹配无 subtraction/fingerprint。
- 非空真实用例通过 ConfiguredVerifier 原命令执行最高版本环境并取得 PASS，再关闭并
  新建 exact subject；静态 capture 先于动态执行，已测试的资源不再用于新静态采集。
  验证 local unchanged、GLOBAL/SLICE fingerprint 分域、JSON 重放、anchor 缺失，以及
  错误 anchor、fixed coordinates、window artifact 的 UNCOMPARED 和伪造 COMPARED 拒绝。
- `uv run pytest tests/test_static_request.py::TestNonemptyStaticPreparation --no-testmon -q --tb=short`：
  exit 0，**3 passed in 14.55s**。`uv run ruff check src tests scripts`、`uv run ty check`、
  `git diff --check`：exit 0。uv cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 当前真实 fixture 仅一个 managed coordinate，已有错误额外 fixed coordinate 拒绝用例，
  多 managed coordinate 真正固定/改变、跨 scope/foreign PASS/raw cache membership 等仍需补证。
  S1/完整目标未完成；不以语义文档通过等同生产 search/Run cache 或静态权限迁移上线。

### 8.25 2026-09-07 · S1 多 managed coordinate 的真实 SLICE 证据

- 非空真实 fixture 增加 idna/packaging 均为 managed direct coordinates 的 exact 分支；
  候选从真实 plan 的可用 universal wheels 提取，不硬编码远端版本、URL 或 hash。
  reference 的 configured verifier PASS 保持直接执行来源，context 固定其实际其他坐标。
- 先用 baseline selections 验证多坐标 local COMPARED，再实际运行 lowest-direct 获取较低
  artifact selections，并分别独立准备/采集两种 exact Proposal：仅降低 idna，packaging
  固定，应 local COMPARED；仅降低 packaging，idna 及其 window 保持不变，应
  UNCOMPARED(context-mismatch)。后者共同 static context 仍合法，拒绝来自 SLICE 固定坐标。
- 初版仅补 packaging 变化拒绝时，
  `uv run pytest tests/test_static_request.py::TestNonemptyStaticPreparation --no-testmon -q --tb=short`：
  exit 0，**4 passed in 23.27s**；补齐 idna 实际变化正例后，同命令最终 exit 0，
  **4 passed in 25.66s**。
- `uv run ruff check src tests scripts`、`uv run ty check` 与最终局部 ruff、
  `git diff --check`：exit 0。uv cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 本节补上 §8.24 的多坐标实际准备证据；Run/Cell scope、foreign PASS/raw membership、
  生产 cache/search/evaluation 接入仍未完成，S1 与总目标保持未完成。

### 8.26 2026-09-07 · S1 静态 Run/Cell scope 的保存引用闭包

- 新增 StaticScopeEvidence：独立 process/fact membership/consumer/PASS tables 与固定
  highest ref；每个 raw request key 在 scope 内只允许一个观测。producer 与 consumer 都
  通过同一 preparation→raw subject 校验，Cell 必须相同；实际 ty terminal/output completeness
  匹配 raw fact，不能把同一 process 借给另一观测或 verifier PASS。
- 共享 scope.compare 先验证 scope 名及 consumer/PASS/固定最高版本 refs 闭合，再调用
  已有语义 derive；GLOBAL 必须使用 scope 固定 highest，SLICE PASS 必须在本 scope 关联
  其实际 reference consumer。悬空、foreign scope/consumer/PASS 确定性拒绝，不导入事实。
- 实际 ty 与 configured verifier process 用于测试 scope JSON 往返、跨 scope/悬空引用拒绝、
  duplicate raw request 拒绝、借用 ty process 作为 PASS 拒绝，以及所有局部 refs 整体重命名后
  delta/fingerprint 不变。语义 context 不包含局部 refs，重命名不能自动合并 scope。
- 首次 `uv run pytest tests/test_static_request.py::TestNonemptyStaticPreparation --no-testmon -q --tb=short`：
  **2 failed, 2 passed in 21.14s**，错误使用完整 ProcessResult 对象相等断言；stdout/stderr
  按既有契约 excluded，改为验证规范 JSON 与重放结果，不把输出加入 wire。修正后同命令
  exit 0，**4 passed in 29.37s**。最后追加离线 compare 和 output-incomplete 拒绝测试后，
  `uv run pytest tests/test_static_request.py::TestNonemptyStaticPreparation -k exact-vector --no-testmon -q --tb=short`：
  exit 0，**2 passed, 2 deselected in 22.24s**；`git diff --check` exit 0。
- `uv run ruff check src tests scripts`、`uv run ty check`：exit 0。uv 均
  cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- 此步是保存数据的 scope resolver；尚非 cache 签发的不可冒用内存 ref。开放/关闭容器、
  运行时 foreign ref→UNCOMPARED、single-owner 并发、资源生命周期及 report/Journal table
  接入仍待完成，S1 与总目标未完成。

### 8.27 2026-09-07 · S2 移除生产 RuntimeEvaluator witness 路由

- S1 六组闭包、分离身份、实际 request/prepare/consumer/scope 证据、GLOBAL/SLICE 共享
  准入与最小离线复算已由 §§8.4–8.26 落地；基础实现门槛满足，跨片 AC 仍待最终审计。
  本次核对 Plan 顺序后先进入 S2，未跳到 S3 启动运行时缓存或改变依赖顺序。
- RuntimeEvaluator 删除 RuntimeWitnessOperations 注入接口和全部 witness 执行/早退分支；
  CLI 与 qualification 脚本不再构造/注入 RuntimeWitnessAdapter。生产 static regression
  直接进入原 configured verifier，动态结果仍由 verifier terminal 决定。
- 替换旧 witness outcome 路由/去重测试为当前公开行为矩阵：unresolved-import、
  unresolved-attribute、invalid-assignment × verifier PASS/nonzero，均执行完整 verifier，
  保留实际静态 increment 并标记环境已测试。不保留枚举旧 witness outcome 的迁移断言。
- `uv run pytest tests/test_evaluation.py --no-testmon -q --tb=short`：exit 0，
  **21 passed in 0.35s**。首次 ruff 发现删除测试后的无用 ProcessResult 导入；首次 ty 发现
  qualification 脚本遗漏的 witnesses 参数；已分别删除/迁移。最终
  `uv run ruff check src tests scripts`、`uv run ty check`：exit 0。
- `uv run pytest tests --no-testmon -q --tb=short`：exit 0，
  **2442 passed in 106.73s**。`git diff --check` exit 0。
  uv cwd=`/home/llh/pf`，风险核对后沙箱外执行。
- S2 尚未完成：ty 不可用仍有旧提前终止路径；AST/classification、RuntimeInterfaceMissing
  类型及 report/failure authority 消费、region/static 阶段调度仍需清除。此次只移除生产
  witness 调用权限，不声称完整动态契约或 Schema1 权限已切换。没有提交、推送或归档。

### 8.28 2026-09-07 · S2 删除 AST 分类器、planner 与 witness adapter

- 删除 StaticTransitionClassifier 的 AST 读取、import/member 恢复与 witness plan 生成，
  StaticEvaluator 不再接收 classifier 注入；static_transition.py 暂只保留旧比较消费者仍用的
  fingerprint 函数/版本常量。删除旧 AST 分类专属 tests/test_static_transition.py。
- 生产 witness 路由已在上一节删除，本节删除无生产消费者的 adapters/runtime_witness.py
  与专属 tests/test_runtime_witness.py；execution-contract 测试移除已消失 witness stage 的
  历史枚举，保留当前 ty 阶段不能成为动态 authority 的安全规则。
- 现行旧 static/report 模型的 classifications 字段尚未迁移：生产暂只填逐 diagnostic 的
  general/structured-diagnostic 记录，不读取 AST 或生成 witness plan。该剩余字段及
  RuntimeInterfaceMissing/Failure/report 分类权限仍须在 S2 清除，不能把这一步说成目标 wire
  已完成或继续承认强分类权限。新版 S1 raw/comparison 模型已不含这些字段。
- `uv run pytest tests/test_evaluation.py tests/test_report_schema.py --no-testmon -q --tb=short`：
  exit 0，**169 passed in 0.83s**。删除 adapter 后
  `uv run pytest tests/test_evaluation.py tests/test_execution_contract.py tests/test_report_schema.py --no-testmon -q --tb=short`
  exit 0，**283 passed in 0.85s**。
- `uv run ruff check src tests scripts`、`uv run ty check`：exit 0。
  首次 `git diff --check` 发现 static_transition.py 多余 EOF 空行，删除后 exit 0。
  uv cwd=`/home/llh/pf`，风险核对后沙箱外执行。S2 与整体目标未完成，未提交或归档。

### 8.29 2026-09-07 · S2 删除 classifications 类型与 wire 字段

- 删除 DiagnosticClassification 类型/公开导出以及 StaticRegressionEvaluation、Schema1
  StaticRegressionEvaluationV1 的 classifications 字段；生产构造、builder、reader、fixtures、
  terminal/report/current schema tests 同步迁移。上一节的临时 general/structured-diagnostic
  填充已删除，不保留替代分类数据、兼容 alias 或旧字段读取。
- 旧 witness-prefix 准入不再从分类恢复计划，任何非空 witness evidence 被拒绝。旧 witness
  类型/字段及 FailurePolicy 的遗留分支尚需进一步删除；这一步不声称它们已完全清空。
- 删除过时分类模型测试及 report fixture 中 runtime-interface-missing 分支，保留并继续验证
  同 fixture 的实际 verifier rejection/indeterminate、region 与正常报告往返部分。
  原诊断正例改为直接断言增量 code，而不是分类 reason。
- 首次局部命令 `uv run pytest tests/test_schemas.py tests/test_report_schema.py tests/test_evaluation.py tests/test_evaluation_cache.py --no-testmon -q --tb=short`：
  **1 failed, 342 passed in 1.04s**，新断言误写 fixture code；按实际 fixture 的
  invalid-argument-type 修正。首次 ty 定位剩余 schemas 导出/report 测试导入；已删除。
  `uv run ruff check src tests scripts --fix` 仅清除四个迁移后无用导入。
- `uv run python scripts/generate_report_schema.py` 已生成 schema/examples；
  `uv run python scripts/generate_report_schema.py --check` exit 0。
  `uv run pytest tests/test_schemas.py tests/test_report_schema.py tests/test_evaluation.py tests/test_evaluation_cache.py tests/test_terminal.py --no-testmon -q --tb=short`：
  exit 0，**471 passed in 1.83s**。
- 最终 `uv run ruff check src tests scripts`、`uv run ty check`、`git diff --check` exit 0；
  src/tests/scripts 无 DiagnosticClassification/classifications/structured-diagnostic 残留。
  uv cwd=`/home/llh/pf`，风险核对后沙箱外执行。S2 与总目标未完成。

### 8.30 2026-09-07 · S2 普通动态结果不再携带 witness 字段

- PassEvaluation、VerifierRejectedEvaluation、IndeterminateEvaluation 及对应 Schema1 V1
  records 删除 witnesses 字段和相关校验；builder/reader 不再写出或读取普通终态的 witness
  列表。不保留默认空字段、aliases 或兼容读取。相应过时 pass/witness 测试移除。
- report 的旧 witness resolver 暂只隔离于尚待删除的 RuntimeInterfaceMissing 分支；普通
  PASS/nonzero/indeterminate reader 不再调用。该分支及 witness plan/result 类型仍是 S2
  待清理项，不能据此声称全部 witness wire 已消失。
- 删除 evaluation_fixtures 中没有消费者的 ScriptedWitnesses、handler、assembly 字段，
  现行 fixture 仅保留真实测试所用的静态与 configured verifier seams。
- `uv run pytest tests/test_schemas.py tests/test_report_schema.py tests/test_evaluation.py --no-testmon -q --tb=short`：
  exit 0，**335 passed in 0.92s**。
  `uv run pytest tests/test_report.py tests/test_report_workflows.py tests/test_terminal.py tests/test_verification.py --no-testmon -q --tb=short`：
  exit 0，**190 passed in 0.95s**。
  最后 fixture 清理后 `uv run pytest tests/test_evaluation.py --no-testmon -q --tb=short`：
  exit 0，**21 passed in 0.34s**；`git diff --check` exit 0。
- `uv run python scripts/generate_report_schema.py` 已重新生成 schema/examples，最终
  `uv run python scripts/generate_report_schema.py --check`、`uv run ruff check src tests scripts`、
  `uv run ty check` 均 exit 0。uv cwd=`/home/llh/pf`，风险核对后沙箱外执行。
  S2 与整体目标仍未完成。

### 8.31 2026-09-07 · S2 删除 runtime-interface-missing 与 witness failure 权限

- 删除 RuntimeInterfaceMissingEvaluation 及 V1 terminal 类型，所有 RuntimeWitness
  plan/result/attempt/wire 类型和公开导出；FailurePolicy、report serializer/resolver、search
  类型、verification/terminal 进程与 causal-static-detail 分支同步删除。不存在旧 witness
  类型、状态字符串或兼容读取，普通静态诊断仍可作为真实 verifier failure 的附属展示。
- 删除 RUNTIME_INTERFACE_MISSING FailureCause 和 rejection_is_supported 的 witness
  特例。通用 FailurePolicy.classify 仅产生 INDETERMINATE；REJECTED 必须由合格 operation
  authority 或 configured-verifier authority 支持。FailurePolicy identity 切到
  failure-execution-v4，相关 builder/reader/fixtures/qualification script 同步；旧 classifier/
  witness 报告 provenance 项删除。ty 不可用的早退路径尚待后续 S2 迁移。
- 删除过时 witness 模型/肯定性拒绝测试；保留终端静态问题展示正例，改为绑定实际 verifier
  rejection。初次类型检查发现 terminal 的否定类型分支残留，已删除。两次 ruff --fix
  分别只清除三个与两个无用导入。
- 局部 487 项测试先通过（1.91s）；补齐 failure 权限后
  `uv run pytest tests/test_failure.py tests/test_schemas.py tests/test_report_schema.py tests/test_evaluation.py tests/test_verification.py tests/test_terminal.py --no-testmon -q --tb=short`：
  exit 0，**516 passed in 2.03s**。
- 正式全量首轮 `uv run pytest tests --no-testmon -q --tb=short`：
  **2 failed, 2353 passed in 107.40s**。失败为旧 diagnose cause 枚举和旧 v3 qualification
  manifest；清除前者，不改写历史 qualification JSON 冒充新运行。
- `uv run python scripts/qualify_execution_failures.py --output tests/execution_qualification/2026-09-07-d038-uv-0.12.5-v1.json`：
  exit 0；新记录 recorded_at=2026-09-07T12:03:09.579127+00:00，failure policy v4。
  resolve/install 分别实际 18/19 processes、各 2 full verifiers；均 new_full_pass_after_rejection
  与 report_roundtrip=true。正式 replay 测试改读新记录，历史 2026-09-06 文件保留为历史事实。
- `uv run pytest tests/test_execution_qualification.py tests/test_diagnose.py --no-testmon -q --tb=short`：
  exit 0，**37 passed in 2.56s**。最终同一正式全量命令 exit 0，
  **2354 passed in 105.15s**。
- schema/examples 已再生成，`uv run python scripts/generate_report_schema.py --check`、
  `uv run ruff check src tests scripts`、`uv run ty check`、`git diff --check` exit 0。
  src/tests/scripts 的 Python 文件无 RuntimeWitness/RuntimeInterfaceMissing/witness/
  rejection_is_supported 残留；历史 JSON 不作为当前 wire 消费。全部 uv 命令
  cwd=`/home/llh/pf`，风险核对后沙箱外执行。S2 与整体目标未完成。

后续每次实质行动追加：日期与 slice、目标/改动、精确命令与 cwd、运行环境、退出码/结果/计数、
证据路径、结论、剩余项及偏差。失败后修复与重跑均保留，不将历史失败改写成首次通过。
最终状态只有在全部 AC 有直接证据、owner 吸收完成后才改为已完成；实验受阻或仅静态检查通过
时保持未完成并说明限制。


### 8.32 2026-09-07 · S2 候选静态不可用不再终止动态验证

- 新增独立 `StaticUnavailableEvaluation(UNAVAILABLE, proposal, failure)`，约束 failure
  为 ty 采集阶段；没有 baseline digest、delta 或 fingerprint。候选 `StaticEvaluator.evaluate`
  不再返回动态 `IndeterminateEvaluation`，`RuntimeEvaluator` 在该结果下继续执行配置 verifier，
  保留静态采集失败。最高版本 `capture` 的旧早退仍待下一步整体迁移，不能据此关闭 AC3/4。
- 搜索候选遇到该状态绕过 region guidance/point，进入实际 verifier；删除把静态失败转换为
  ProbeIndeterminate 的 `_static_evidence` 路径。现有 static cache conflict、普通 region
  disposition 与最高版本 baseline 仍待后续 S2/S3/S4 清理，未宣称搜索目标契约完成。
- 同步独立静态 union 的动态关联、报告 producer/reader 与 wire UNAVAILABLE variant；报告
  baseline 与 region 引用继续要求相应可用静态事实，不允许不可用结果伪装 fingerprint。
  schema/examples 已重生成。独立 raw fact scope/Journal v3 仍在 S5 接入，当前 wire 不是该片验收。
- RuntimeEvaluator 参数化验证静态可用/失败 × verifier PASS/nonzero/timeout；check 同样验证
  三种 verifier 终态与两次实际 preparation。搜索公开 coordinator 正例验证 ty 失败时仍有
  floor PASS 与前驱 configured-verifier rejection、环境清理及报告 write/read 往返。
- 验证（uv 均在仓库根风险核对后沙箱外执行）：
  - `uv run pytest tests/test_evaluation.py tests/test_evaluation_cache.py tests/test_search_workflow.py tests/test_report_workflows.py --no-testmon -q --tb=short`：53 passed in 0.85s。
  - `uv run pytest tests/test_search_coordinator.py --no-testmon -q --tb=short`：21 passed in 0.29s。
  - 首次完整 `uv run pytest tests --no-testmon -q --tb=short`：1 failed, 2358 passed in 106.30s。
    唯一失败为 check 旧断言将 ty 失败认作 INDETERMINATE；已替换为实际 verifier 三终态的正例。
  - `uv run pytest tests/test_check.py tests/test_report_schema.py tests/test_report_artifacts.py tests/test_authorization.py tests/test_projection.py tests/test_baseline.py --no-testmon -q --tb=short`：236 passed in 4.31s。
  - `uv run ty check`、`uv run ruff check src tests scripts`、`uv run python scripts/generate_report_schema.py`、`git diff --check`：exit 0。
  - 最终完整 `uv run pytest tests --no-testmon -q --tb=short`：2363 passed in 106.27s。
  - 最终 `uv run ruff check src tests scripts`、`uv run python scripts/generate_report_schema.py --check`、`git diff --check`：exit 0。
- 下一步整体迁移最高版本 `StaticBaselineState` 与 capture；无可用全局参考时 candidate
  保留 `UNCOMPARED` 和原采集失败，再同步 highest/check/search/report 的 baseline 引用。
  该迁移尚未编辑，S2 与整体目标未完成，未提交、推送或归档。


### 8.33 2026-09-07 · S2 最高版本不可用 baseline 与直接 oracle

- `StaticBaseline` 明确为 AVAILABLE；`StaticBaselineState` 用 AVAILABLE 与独立
  `StaticUnavailableEvaluation` 的 UNAVAILABLE 两支表达最高版本捕获，后者不含诊断 digest。
  `StaticBaselineCapture` 在两个状态下都保存原 capture Proposal 与同一次静态结果；共享
  `require_captured_static` 校验最高版本完整结果、搜索 baseline 与捕获的一致性。
- `StaticEvaluator.capture` 不再返回动态 Indeterminate；highest/check 的静态失败早退删除。
  highest 继续一次 verifier 且不重复 ty；check 保留 highest + lowest-direct 和实际 HarnessBaseline。
  lowest preparation 失败仍按 declaration role/原动态失败处理，同时保留 capture 状态。
- 参考 baseline 不可用而候选 ty 成功时，返回 `StaticUncomparedEvaluation(UNCOMPARED,
  reference-unavailable)`，保留原不可用参考；候选 ty 本身失败仍为 UNAVAILABLE。两支均无
  delta/fingerprint。baseline 不会被后续候选偷偷重建，二者都继续动态 verifier。
- 同步 report producer/reader：UNCOMPARED 用 Proposal reference 引用实际不可用采集；reader
  先恢复采集事实再恢复比较引用，并校验冻结 baseline。BaselineRefs 的诊断 digest 为必填
  nullable；UNAVAILABLE 只接受 null，AVAILABLE 要求真实 captured check/digest。同步
  required-null reader 规则、生成 schema/examples 与 schema nullable 字段契约测试。
- 产品 `_ProposalRunner.evaluate_in_slice` 现在直接调用动态 oracle；删除约 150 行静态缓存
  冲突终止/region 推断消费路径及 `_region_guidance`。保留的 region 只记录已运行的动态点，
  没有代表点预测权限；旧 region/promotion interface 与 wire 在 S4/S5 整体删除。
- 正例覆盖：smoke 真实最高版本组件在 ty 失败后 PASS、一次 ty/一次 verifier/环境关闭；
  highest verifier rejection/timeout × 静态可用性及失败报告往返；check verifier 三终态 ×
  capture/candidate/both/none 四种采集组合与 lowest prepare failure；搜索四组合 × runtime
  diagnostics 两种组合，所有 observation 均为直接证据，保留 floor/predecessor 与报告字节稳定往返。
- 验证（uv 均在仓库根风险核对后沙箱外执行）：
  - 初次六模块定向：1 failed, 373 passed in 1.51s；唯一失败是共享 validator 的错误消息断言，已同步。
  - 初次 check/search 四组合：4 failed, 44 passed in 0.73s；均暴露新必填 null 未列入 reader
    nullable 路径，已修复该真实遗漏。
  - `uv run pytest tests/test_check.py tests/test_search_coordinator.py tests/test_schemas.py --no-testmon -q --tb=short`：204 passed in 0.66s。
  - 八模块定向：1 failed, 393 passed in 1.87s；schema 严格 nullable 字段集合未加新 BaselineRefs 字段，已同步。
  - `uv run pytest tests/test_check.py tests/test_baseline.py tests/test_schemas.py tests/test_smoke.py tests/test_report_schema.py tests/test_report_artifacts.py tests/test_search_coordinator.py --no-testmon -q --tb=short`：371 passed in 1.39s。
  - `uv run pytest tests/test_search_coordinator.py tests/test_search_workflow.py tests/test_execution_qualification.py --no-testmon -q --tb=short`：39 passed in 2.86s。
  - `uv run ty check`、`uv run ruff check src tests scripts`、`uv run python scripts/generate_report_schema.py --check`、`git diff --check`：exit 0。
- 完整 `uv run pytest tests --no-testmon -q --tb=short`：13 failed, 2362 passed, 2 errors in 378.75s。真实 CLI 多例遇到外部 PyPI
  TLS EOF：`/tmp/pytest-of-llh/pytest-181/test_cli_verifies_project_only0/.pf/logs/*/process-0004.log`
  显示 uv resolve-project 25.2867s 后 exit 2，stderr 为获取 `https://pypi.org/simple/idna/`
  三次重试 TLS handshake eof。独立沙箱外 urllib 请求也返回 SSL UNEXPECTED_EOF_WHILE_READING；
  不将这些失败伪报通过或改写资格附件。另有 qualification 的 final floor 断言失败，尚未证明
  与外网同因，独立复核。完整测试结束后 PyPI 只读检查恢复 HTTP 200，开始 --lf 重跑受影响用例。
- 最后四模块命令/报告定向 `uv run pytest tests/test_search_coordinator.py tests/test_check.py tests/test_smoke.py tests/test_baseline.py --no-testmon -q --tb=short`：63 passed in 0.70s。
- S2 尚需收紧动态 Indeterminate 的旧 ToolFailure wire/FailurePolicy fallback；原始完整请求
  factory/cache、静态 scope/Journal v3 和两阶段定位仍属 S3–S5。未关闭最终 AC、未提交或归档。

- 网络恢复后的 `uv run pytest tests/test_end_to_end.py tests/test_static_inputs.py tests/test_static_request.py tests/test_execution_qualification.py --lf --no-testmon -q --tb=short`：15 passed, 13 deselected in 17.79s。
  qualification 重放也通过；先前 floor 断言失败的具体根因未由该次输出证明，不将其强行归类。
  脚本断言现在附 actual_vector 和受控失败进程 stderr 摘要，保持原 floor=2 验收不变，便于再发时定位。
  最终完整 `uv run pytest tests --no-testmon -q --tb=short`：2377 passed in 105.50s。
  最终 ruff、ty、生成物 `--check`、`git diff --check` 均 exit 0。没有残留运行中的测试。
- 下一步收紧动态 Indeterminate 的纯 verifier authority 与相应 FailurePolicy/report fallback，
  再进入 S3 完整静态请求/cache facade 和资源时序；本片不将旧 Evaluation 静态关联、region wire
  或当前无 raw cache 的实现判定为 D038 完成。


### 8.34 2026-09-07 · S2 动态 Indeterminate 只接 verifier authority

- `IndeterminateEvaluation` 现在必含 `VerifierIndeterminate` 与显式静态关联，cause 只为与
  verifier terminal 一致的 TIMEOUT/TOOL_FAILURE。删除旧 ToolFailure 字段与动态静态失败
  fallback；`FailurePolicy.record_evaluation` 仅组装配置 verifier authority。
- `FailureRecord` 禁止 ty 采集阶段创建动态失败；相同校验供本地构造、Journal 与报告 reader
  使用。静态失败仍保存于独立 UNAVAILABLE，无动态 Failure ID。prepare 与独立基础设施
  FailureRecord 的既有 owner 保留，纯静态 prepare 的 scope/admission 仍由 S3–S5 完成。
- report reader 不再从 generic process failure 恢复动态 Indeterminate；必须有 test stage、
  合法 verifier terminal 与配置 verifier authority。静态引用成为 wire 必填字段，生成
  schema/examples 同步。runtime process 只消费显式诊断 sidecar，删除原静态 failure.process
  回退。baseline、probe/event 校验同步收紧，动态 cache authority 只比较 verifier terminal。
- 测试迁移到当前契约：保留 verifier terminal/cause、失败关联和 round-trip 的正例；移除
  static-cache 作为动态终态的旧用例，静态阶段不再充当 smoke 失败展示样例。smoke timeout
  测试改用真实 highest/runtime 组件和带终止信号的过程事实。
- 安全验证：独立 static evaluation 不能反序列化为动态 Evaluation；ty 采集不能生成动态
  FailureRecord；离线 reader 在非 verifier process authority 的 Failure ID 与所有 refs 均
  重算后仍明确拒绝其动态 Indeterminate 关联，不依赖 identity drift 偶然拦截。
- 验证（uv 均在仓库根风险核对后沙箱外执行）：
  - `uv run pytest tests/test_evaluation.py tests/test_evaluation_cache.py tests/test_failure.py tests/test_schemas.py tests/test_report_schema.py tests/test_verification.py tests/test_terminal.py --no-testmon -q --tb=short`：525 passed in 1.78s。
  - 五模块后续定向：2 failed, 464 passed in 1.61s；超时 fixture 缺实际终止信号、旧 terminal
    参数化仍把 ty 作为失败阶段，均已按当前契约修正。
  - `uv run pytest tests --no-testmon -q --tb=short`：2377 passed in 105.18s。
  - 完整测试启动后追加离线重算 ID 攻击用例，单独执行 `uv run pytest tests/test_report_schema.py -k recomputed_failure_id --no-testmon -q --tb=short`：1 passed, 147 deselected in 0.14s；不冒称该新增用例已进入上述完整收集。
  - `uv run ty check`、`uv run ruff check src tests scripts`、`uv run python scripts/generate_report_schema.py --check`、`git diff --check`：exit 0。
- S2 基础实现完成，后续进入 S3。没有最终关闭 AC：旧 baseline-only cache/静态关联模型须接入
  S1 完整原始事实，Run refs/lifetime/concurrency 与 S4 两阶段窗口、S5 scope/Journal、真实
  MkDocs/成本和 owner 吸收仍待实现。未提交、推送或归档。

- 收尾扫描还发现展示层不可达的 `evaluation.failure` 分支（因 verifier 已必填而未被类型检查
  报错），已删除；`uv run pytest tests/test_terminal.py tests/test_report_schema.py --no-testmon -q --tb=short`：275 passed in 1.24s，包含新增离线攻击用例。最终 ty/ruff exit 0，生产代码不再引用该删除字段。


### 8.35 2026-09-07 · S3 取消、物化环境生命周期与缓存原子入口基础

- 新增显式 `Cancellation`；`ProcessRunner.run` 的控制参数独立于 ProcessSpec/执行身份。
  生产 runner 对实际进程组注册取消回调，处理 spawn 与取消竞态，终止并收拢输出后抛
  `OperationCancelled`。取消一个 operation 不设置 runner 的全局中断，不影响另一个进程。
  全部 runner 实现与透传 fixtures/qualification scripts 同步迁移。
- `PreparedEnvironment.static_use/verifier_use` 保护真实输入存活期；静态 owner 清理结束前，
  其他线程 close/verifier 等待，同线程误用明确拒绝。verifier 开始即标记 tested，完成或
  异常后均不能作为干净环境重跑。capture、输入采集、TyAdapter.observe 与当前求值入口
  接入借用，完整请求准备中的实际子进程也透传 cancellation。
- 并发 verifier 配额测试原来重复使用同一已测试环境，新约束暴露 1 个失败；改为每个调用
  使用独立真实 PreparedEnvironment，仍检查共享配额限制，不放宽生命周期约束。
- 新增 `static_cache.py` 的 `TyCheckCache` 基础：完整 subject/policy key、原子 owner/join、
  IN_FLIGHT lookup 为 CacheMiss、成功与真实 typed failure 共用一次终态。ref 通过 cache
  已登记对象身份验证归属，相同 payload 的手工对象或另一 Run 引用不能冒用。
  cache 不保存 callback/prepared，完成后仅留不可变 observation/process。
- `stop` 拒绝新收集，取消并等待 owner 清理完成，保留已完成事实供持久化；`close` 随后释放
  状态。未建模异常停止该 Run 并唤醒等待者，不能变成 negative cache，也不能重试。
  此处是基础存储 owner seam，仍需 StaticEvaluator 完整投影校验、consumer/PASS 关联、
  compare、VerificationRunner 生命周期接入；尚未替代产品原有静态路径。
- 验证（uv 均在仓库根风险核对后沙箱外执行）：
  - `uv run pytest tests/test_cancellation.py tests/test_process.py tests/test_ty_adapter.py --no-testmon -q --tb=short`：99 passed in 1.18s。
  - `uv run pytest tests/test_environment.py tests/test_evaluation.py tests/test_static_request.py tests/test_static_inputs.py --no-testmon -q --tb=short`：1 failed, 83 passed in 58.89s；失败为上述环境重复使用 fixture，已修正。
  - `uv run pytest tests/test_static_lifecycle.py tests/test_cancellation.py tests/test_evaluation.py --no-testmon -q --tb=short`：35 passed in 0.55s。
  - `uv run pytest tests --no-testmon -q --tb=short`：2389 passed in 103.51s；此轮收集未包含随后新增 cache 测试。
  - `uv run pytest tests/test_static_cache.py --no-testmon -q --tb=short`：5 passed in 0.10s。
  - `uv run ty check`、`uv run ruff check src tests scripts`、`git diff --check`：exit 0。
- S3 仍进行中；AC25 尚未完成生产采集与 runner 环境保留的联合验收。S4–S8 未开始，未提交、
  推送或归档。后续需要把此原子入口接入唯一 StaticEvaluator 路径并消除旧静态 cache/关联。

### 8.36 2026-09-07 · S3 完整请求采集 public seam 与可取消资源等待

- `StaticEvaluator.lookup/collect` 接入完整 StaticSubject/TyObservationPolicy 与显式 Run cache。
  lookup 只读已登记输入；collect 复证 prepared/request/Proposal 与当前物化内容，缓存命中也
  不跳过 consumer 输入校验。原子入口只让 owner 取得 ty permit、借用干净输入并调用
  `TyOperations.observe`，离开输入借用后才发布原始事实；不在 evaluator 内保存隐式 Run。
- 完整请求观察协议同步加入生产 TyAdapter 与测试 doubles。当前 `capture/evaluate` 的旧
  `check` 入口还未迁移，不能把本次 public seam 落地称为唯一生产路径完成。
- 内容不可用在 cache 中只完成当前等待者的无事实结果，不写 raw negative cache，也不作为
  未建模异常终止 Run。实际 ty 终态才进入 TyFactDocument。closed prepared 的 revalidate
  明确返回 False，已保存 subject 的 lookup 则不检查物化环境。
- ty permit 和静态输入锁支持显式取消：等待资源时定期检查 token，成功取得资源后再次检查，
  finally 归还；防止 Run stop 必须等另一个无期限 owner 释放输入/配额才能收拢本次请求。
  request factory、输入 adapter 和 TyAdapter 的借用同步传递 operation cancellation。
- 将真实完整请求测试迁移到 StaticEvaluator public seam：原始采集加重复消费只运行一次；
  已验证安装内容的重定位重建命中同一 Run ref；修改安装字节会重新采集；tested 环境不能
  新消费，实际 close 后仍可 lookup 原事实。三种 resolution 各覆盖这些路径。
- 验证（uv 均在仓库根风险核对后沙箱外执行）：
  - 首次测试迁移误改同名断言的缩进，pytest collection error；ty/ruff 同时指出该语法错误，
    已使用精确局部替换修正，未将此轮误报为运行行为证据。
  - `uv run pytest tests/test_static_request.py tests/test_static_cache.py tests/test_evaluation.py --no-testmon -q --tb=short`：42 passed in 63.67s。
  - `uv run pytest tests/test_static_lifecycle.py tests/test_cancellation.py tests/test_static_cache.py --no-testmon -q --tb=short`：18 passed in 0.36s，覆盖之后新增的可取消输入等待。
  - `uv run ty check`、`uv run ruff check src tests scripts`、`git diff --check`：exit 0。
- S3 仍进行中：还需 producer/consumer/PASS 注册及 Run/Cell comparison facade、持久化快照、
  VerificationRunner 创建与关闭、capture/evaluate 唯一路径迁移和 runner 独占环境保留。
  上述 public seam 定向 PASS 不代替 AC25 的最终联合验收。S4–S8 未开始，未提交或归档。

- 后续完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2396 passed in 110.31s。
  运行期间收尾将缓存 key 统一到 S1 的 `ty_check_key`，并让内容不可用结果发布也与 stop
  互斥，避免取消已发生后仍返回普通辅助结果；该收尾另执行
  `uv run pytest tests/test_static_cache.py tests/test_static_lifecycle.py --no-testmon -q --tb=short`：
  13 passed in 0.30s。最终 ty/ruff/diff check 均 exit 0，没有未收拢的测试进程。

### 8.37 2026-09-07 · S3 producer/consumer、PASS 注册与 Run scope 比较

- cache collect 的输入现在必含实际 StaticPreparationEvidence。发布终态前原子登记 elected
  owner 的 preparation 与 producer 关联；命中/等待者分别登记自身 consumer，不能由谁先
  读取结果来决定 producer。完成事实只保存不可变证据，不保存 prepared/callback。
- `RunStaticConsumerRef`、`RunStaticPassRef` 由开放 cache 的登记表验证对象归属；手工复制
  同 payload、另一 Run 或关闭 Run 的引用不具有比较准入。GLOBAL 固定最高 consumer，
  SLICE 必须解析同 Run、同 reference consumer 的实际配置 verifier PASS 与 process。
  PASS 注册拒绝借用 ty process，重复同一次实际 PASS 不制造第二次运行。
- `StaticEvaluator.compare` 与 cache comparison facade 接入已有 StaticScopeEvidence 的
  纯准入/派生规则；不另写 delta 算法，不导入外来 refs。GLOBAL 与 SLICE 的完整静态
  消费准入、固定 context 和多重集计算仍由 S1 共用模型负责。
- `snapshot(cell)` 导出完整 fact/producer/consumer/PASS/process 关联与固定最高引用；scope
  identity 属于 Run/Cell，不进入 raw fact/key。stop 后保留快照供持久化，close 后释放缓存；
  已导出的 scope 可在环境和 cache 均关闭后独立重放。
- 原始 ty terminal/输出完整性绑定提取为 `validate_ty_fact_process`，运行期缓存与离线 scope
  共同调用，避免在缓存发布与 reader 中维护不一致规则。
- cache 测试从手工 StaticSubject fixture 改用真实 prepare/request 证据，首次新 fixture 未
  显式给出隔离 HOME/Git 输入，捕获返回 invalid-layout、5 setup errors；补齐输入后 5 passed
  in 1.97s。新增 GLOBAL 比较、跨 Run/复制引用拒绝、实际环境 close 后读取与 scope replay
  后，同命令 6 passed in 2.12s。
- `uv run pytest tests/test_static_request.py tests/test_static_cache.py tests/test_static_subject.py --no-testmon -q --tb=short`：60 passed in 67.21s。
  包含真实 highest/exact-vector 的 configured verifier PASS 注册、SLICE 运行期与离线
  comparison 一致性、scope 关闭后重放；不以只验证 wire 结构代替这些运行证据。
- `uv run ty check`、`uv run ruff check src tests scripts`：exit 0。随后新增“缺输入不产生
  negative observation”用例单独复核，不计入前述 60 项收集。
- 本片仍未完成 S3：VerificationRunner 生命周期与日志持久化、capture/evaluate 唯一采集
  路径、真实多 Cell/不同 Proposal 同投影的消费矩阵及 runner 原子环境保留仍待接入。
  AC25 最终联合验收未关闭；S4–S8 未开始，未提交、推送或归档。
- 最后 `uv run pytest tests/test_static_cache.py --no-testmon -q --tb=short`：7 passed in 2.14s；
  `git diff --check` exit 0。所有本片测试进程已完成。

### 8.38 2026-09-07 · S3 动态缓存去静态身份与 prepared SourcePlan 所有权

- `EvaluationCache` 现在只保存直接配置 verifier 证据。删除旧静态结果表、get_static/
  record_static 及 baseline_digest key/校验；动态 key 为精确 Proposal ID 与 ExecutionPolicy，
  并核对同 identity 的完整 Proposal 事实。静态关联变化不能隔离动态终态冲突或产生新的动态
  cache entry。search 调用方同步迁移，raw ty 的缓存仍只属于 TyCheckCache。
- 通过 public cache seam 测试：改变静态基准/诊断而保持 verifier authority 时复用直接证据；
  即使静态基准不同，PASS/REJECTED 冲突仍报告 NONDETERMINISTIC；独立 Proposal/执行策略
  分别缓存，已登记 identity 的 Proposal 事实不一致明确拒绝。原动态 terminal/signaled/
  timeout 冲突用例继续保留，删除旧静态缓存行为的测试。
- `PreparedEnvironment` 保留准备时实际 SourcePlan，并在构造时验证其 identity 与 Attempt
  一致。完整 StaticRequestFactory.capture 直接消费该事实，不再要求调用方重复提供一个
  可能不同的 SourcePlan。EnvironmentFactory、真实重定位 helper 与 request fixtures 同步
  迁移，为后续 capture/evaluate 唯一路径接入提供完整准备输入。
- 第一次定向因遗漏一处旧字符串 getter 参数，1 failed, 71 passed in 0.67s；ty 同时指出该
  参数类型，ruff 发现删除 baseline key 后的未用 import，均已修正。
- `uv run pytest tests/test_environment.py tests/test_static_request.py tests/test_evaluation_cache.py tests/test_search.py tests/test_search_coordinator.py tests/test_search_workflow.py --no-testmon -q --tb=short`：130 passed in 64.81s。
  `uv run ty check`、`uv run ruff check src tests scripts`、`git diff --check`：exit 0。
- 仍未关闭 S3/AC25：当前动态 Evaluation 的旧内嵌静态 association 尚待 S5 wire 迁移；生产
  capture/evaluate 的旧 lower check 路径、VerificationRunner 显式 cache 生命周期与持久化、
  runner 原子环境保留仍未完成。S4–S8 未开始，未提交、推送或归档。
- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2400 passed in 115.97s。
  最终 ty/ruff/diff check 均 exit 0，没有残留测试进程。此完整测试也覆盖 8.37 新增 scope
  注册与重放用例；不代表尚未接入的产品 Run cache 生命周期已经验收。

### 8.39 2026-09-07 · S3 产品完整请求调用链迁移（进行中）

- `StaticEvaluator` 现在显式注入完整 request factory；capture/evaluate 经完整 request、
  TyCheckCache.collect、TyOperations.observe 执行，删除 evaluator 对旧参数式 lower check
  的依赖。CLI 与 qualification composition root 使用真实 StaticRequestFactory。
- baseline/check/search/RuntimeEvaluator 显式传递同一 Run cache；VerificationRunner 在调度
  前创建新容器，成功或异常收尾关闭。该生命周期尚未把静态 scope 接入 Journal/report，
  持久化顺序与完整静态关联还未验收，不能视为 S3 完成。
- 测试受控 lower operation 改为 observe，新增 ScriptedStaticRequests，物化其声明的受控
  字节输入并构造完整验证过的 StaticSubject/PreparationEvidence。其占位解释器不宣称为
  真实安装；实际 interpreter/ty 仍由 real request 集成测试验证。未用 model_construct
  或绕过 Pydantic 闭包校验来维持旧 fixtures。
- factory 从调用环境解析配置/扩展路径后，只向显式 ty process 传递已消费并登记的变量；
  无关宿主变量不因此成为“无法构造请求”，也不继承到 ty。解释器和 tool 输入仍完整捕获。
- 接口迁移期间的类型/语法错误已定向修正：显式 Run cache kwargs、fixture 参数、测试
  operation 协议、脚本 context manager 缩进及完整 subject 必填 projection。
- 首轮主调用链 67 failed, 15 passed in 2.06s，主要为新 fixture 缺 projection；修正后
  13 failed, 69 passed in 1.41s。后续失败识别为旧 fixture 未提供 canonical diagnostic
  order，以及依赖同 key 第二次返回另一结果的旧假设。按新 at-most-once 契约更新：相同
  输入共享真实终态，基准/候选失败组合使用不同实际安装投影；动态 verifier 仍各自执行。
- ty 配额测试改用两个独立 Run cache 与不同 owner 环境，避免同 key 去重或同环境输入锁
  偶然满足最大并发断言。剩余实际调用链验证及完整回归正在执行，尚未报告 PASS。
- 未提交、推送或归档；S4–S8 未开始。还须完成原始缓存/typed-ref 对旧 baseline-only 比较
  的替换、重复请求物化复用、实际 Run scopes 持久化与 runner 环境所有权联合验收。

- 本片主调用链/顶层 Run 定向：
  `uv run pytest tests/test_baseline.py tests/test_evaluation.py tests/test_check.py tests/test_search_coordinator.py tests/test_verification.py tests/test_search_workflow.py tests/test_smoke.py --no-testmon -q --tb=short`：133 passed in 1.86s。
- 完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2400 passed in 145.28s。
  此轮收集不含随后加入的两项 Run 生命周期用例和最后请求物化复用改动。
- 后续新增同一 VerificationRunner/request 连续调用的 public 测试：同 Run Cell 共享一个
  开放 cache，不同 Run 取得不同容器；正常及异常退出后均拒绝使用其关闭状态。请求准备
  阶段也使用 Run cancellation，实际 metadata/tool-version 进程可以被一起停止。
  `uv run pytest tests/test_verification.py tests/test_evaluation.py --no-testmon -q --tb=short`：61 passed in 0.57s。
- PreparedEnvironment 现在只保留自身的一个冻结 request materialization（非原始事实
  cache）；在输入租用内复证配置/输入并复用同一请求，输入改变后仅替换其已拥有的文件，
  close 时释放关联。避免重复 capture 因 static 目录已经存在而错误地降为不可用。
  实际 request 测试通过 public capture 检查复用同一个已复证对象。
  `uv run pytest tests/test_static_request.py tests/test_static_cache.py tests/test_static_lifecycle.py tests/test_environment.py --no-testmon -q --tb=short`：73 passed in 67.39s。
- Run 收尾明确先 stop/drain，再 finalize，最后 close；操作异常与收尾异常同时发生时保留
  原操作异常。`uv run pytest tests/test_verification.py --no-testmon -q --tb=short`：37 passed in 0.12s。
- 最终 ty/ruff/diff check 均 exit 0，所有测试进程已完成。产品 capture/evaluate 的原始采集
  已走完整 request/cache/observe；旧 TyAdapter.check 仍有直接 adapter 测试、待移除，旧
  baseline-only `_increment` 比较及内嵌静态关联也尚未迁移为 scoped consumer 结果。
  配置 verifier PASS 的生产登记和 scope 的 Journal/report 持久化仍待接入；没有据此关闭
  S3 或任何最终 AC。

### 8.40 2026-09-07 · S3 配置 verifier 实际 PASS 的生产 scope 登记

- PreparedEnvironment 保留最后一次完成收集的 typed consumer；新采集开始、输入重建及
  close 会清除旧关联。它不拥有 Run cache，raw facts/consumer 证据仍由明确的容器持有。
- RuntimeEvaluator 在 verifier 开始前取得该 consumer；完整配置 verifier 实际 PASS 后，
  仅当 consumer 属于当前开放 Run、精确 Proposal 且有实际 verifier process 时登记 PASS。
  静态不可用可与动态 PASS 同时保存；外来 Run 的 consumer 不导入当前 scope，也不改变
  动态 PASS。没有过程事实时不伪造可用 anchor。
- 新增 public runtime 测试，使用真实 prepare、实际 ty 和 ConfiguredVerifier，检查 PASS
  的 consumer/Proposal/process 关联及实际环境关闭后的 scope 离线重放；受控组合验证
  ty unavailable 与跨 Run 引用不改变配置 verifier outcome，且不创建跨 scope facts/PASS。
- 第一轮定向 2 failed, 34 passed in 6.32s；两失败为新增 fixture 向 keyword-only 的
  successful_process 误传位置参数，ty 同时指出，已修正。该轮真实进程 PASS 重放测试通过。
- 完整比较结果迁移、旧 TyAdapter.check 删除、Journal/report scope 持久化及环境污染/
  oracle 保留联合验收仍待完成；本片未关闭 S3/AC25，未提交、推送或归档。
- 修正 fixture 后 `uv run pytest tests/test_runtime_static_scope.py tests/test_evaluation.py tests/test_static_cache.py --no-testmon -q --tb=short`：36 passed in 5.66s。
- 随后新增旧 consumer 失效测试发现真实缺口：请求重采集不可用时，原无 process 的
  ToolFailure 没有必需 structured detail，导致静态辅助失败意外抛 ValidationError。
  改为明确 static-subject-unavailable detail，不伪造 process/raw fact，动态 verifier
  继续执行，且不能给旧 consumer 登记 PASS。发现轮为 1 failed, 100 passed in 4.35s。
- RuntimeEvaluator 在任何执行前核对 package 的 ExecutionPolicy 与实际 prepared Proposal
  一致，拒绝把另一配置命令/timeout 的 PASS 记到原策略；仍允许不改变 ExecutionPolicy
  的静态策略变化。新增 command/timeout 错配 public tests，核对未启动 verifier/未登记 PASS。
- `uv run pytest tests/test_runtime_static_scope.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py tests/test_verification.py tests/test_evaluation.py --no-testmon -q --tb=short`：127 passed in 4.67s。
  ty/ruff/diff check 均 exit 0。后续完整回归正在执行。
- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2410 passed in 150.96s。
  本轮完整收集包含生产 PASS scope、新增执行策略/失效 consumer 用例及上一片收尾的 Run
  生命周期/请求物化测试。最终 ty/ruff/diff check 均 exit 0，没有残留测试进程。


### 8.41 2026-09-07 · S3 删除旧 TyAdapter.check 执行入口

- 删除 TyAdapter.check 与其旧 target fallback；生产 adapter 只通过完整 StaticTyRequest
  的 observe 执行，并在输入租用内做前后复证。现有调用方均使用该入口。
- 将实际 ProcessObservation 的协议/诊断解析提取为公共 TyOutputDecoder；它只读取已有
  process 输出，不构造请求或执行进程。诊断路径、重复诊断、坏输出、超时及真实 ty
  terminal 配置测试迁移到该 public seam，真实完整请求测试继续验证 capture/observe。
- 初次迁移定向验证为 11 failed, 48 passed in 68.69s；失败全部来自将 owned-option
  admission 错放到 TyConfig 测试。当前 owner 是 TyObservationPolicy，故将全部参数案例
  合并到既有策略 admission 测试，未给配置数据模型添加重复校验。
- 真实 request 测试新增实际 interpreter、Python minor、平台、GitLab 输出、颜色/进度
  固定参数以及 cwd/package 路径断言。平台预期来自真实宿主，适用于本地实际准备场景。
- `uv run pytest tests/test_ty_adapter.py tests/test_policy.py tests/test_static_request.py tests/test_runtime_static_scope.py --no-testmon -q --tb=short`：75 passed in 70.64s。
  输出 `/tmp/d038-decoder-tests-fixed.txt`；测试进程已结束。
- `uv run ty check --output-format concise`、
  `uv run ruff check src tests scripts --output-format concise`、`git diff --check` 均 exit 0。
  本片无完整套件新结果，上一片 2410 passed 仍为此前完整回归证据。
- 旧 baseline-only 比较、动态 Evaluation 内嵌静态关联、环境污染联合验收和 S4–S8
  仍未完成；本片不关闭 S3/任何最终 AC，不提交、推送或归档。


### 8.42 2026-09-07 · S3 固定最高版本的未采集基线证据

- 核对生产比较迁移发现：输入无法闭合时 scope 仅有空最高引用，无法区分未捕获与真实
  capture 不可用。补入 StaticUncollectedBaseline，保存实际 highest Attempt/Proposal
  和 typed StaticContentUnavailable；核对 Attempt 关联、Cell/snapshot/执行策略、准备
  必需字段，并从已保存 Attempt、plan digest、实际 graph 复算 Proposal identity。
- Run cache 保存该固定根，不创建 TyCheckUnavailable/raw key/process。它与最高版本
  consumer 互斥，后续得到可用 raw fact 也不能替换原全局根；关闭后不允许登记。
  snapshot 保留此根供离线重放，关闭容器时释放其运行态索引。
- StaticEvaluator.capture 在请求无法形成时登记此根，实际 ty 成功/失败则登记对应
  consumer。普通附属采集不再改写全局根。现有动态 verifier 继续执行并可 PASS。
- scope 和纯 comparison document 显式保存未采集 reference；GLOBAL 比较核对固定
  Proposal 后返回 UNCOMPARED(reference-unavailable)，保留具体原因，无 delta/fingerprint。
  SLICE 不借用全局根，可独立解析已有 consumer/PASS 并得到原合法比较结果。
- 定向初检 `uv run pytest tests/test_evaluation.py tests/test_runtime_static_scope.py tests/test_static_cache.py --no-testmon -q --tb=short`：39 passed in 5.74s。
- 新增 production capture→verifier PASS、无 raw/process、基线固定、在线/离线错引用拒绝、
  关闭后重放及保存根互斥测试；真实 request scope 用例验证 GLOBAL 不可用但 SLICE
  COMPARED。`uv run pytest tests/test_evaluation.py tests/test_runtime_static_scope.py tests/test_static_cache.py tests/test_static_request.py tests/test_baseline.py tests/test_check.py --no-testmon -q --tb=short`：92 passed in 73.28s。
- 补充 Proposal identity 复算及互斥 reader 检查后，
  `uv run pytest tests/test_static_cache.py tests/test_runtime_static_scope.py --no-testmon -q --tb=short`：23 passed in 5.85s。
  ty/ruff/diff check 均 exit 0；上述测试进程均已完成，随后进行完整回归。
- 该 scope 根尚未进入 Journal/report；旧生产 baseline-only `_increment`、内嵌静态
  Evaluation 及 S4–S8 仍需迁移，本片不宣告 S3 或最终 AC 完成。
- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2413 passed in 153.22s。
  输出 `/tmp/d038-uncollected-full.txt`；测试进程已正常退出。此结果覆盖上一片 decoder
  迁移与本片未采集基线生产登记、scope/比较重放；不替代尚未接入的搜索/持久化验收。


### 8.43 2026-09-07 · S3 scoped comparison identity 与 Run 审计闭包

- 核对生产旧比较结果发现其仍兼任报告关联；替换时必须同步迁移引用。本片先补齐
  当前 scoped compare 的可持久化结果接口，未用新包装维持旧 fingerprint 契约。
- StaticComparisonDocument 增加语义 identity：展开 GLOBAL/SLICE context、subject
  Proposal/static subject/raw fact、observed/uncollected/missing 标签、GuidancePolicy
  identity 及实际结果。不可用 reference 的具体原因进入 tagged preimage，避免与真实
  空诊断或另一 unavailable 原因碰撞；无 COMPARED 时仍不生成 diagnostic fingerprint。
  scope token 与表内 ref 名不进入该语义 identity。
- StaticScopeEvidence 增加 comparisons 表，使用本 scope 的 subject/reference/PASS refs；
  reader 解析成员、复证固定最高根/局部 PASS 后重新推导结果和 identity，拒绝悬空、
  错误 fingerprint/identity 及重复语义 identity。未采集全局根仍与 SLICE 独立。
- Run cache 的 compare 完成 membership admission 后直接调用共享纯比较推导，并将
  已推导结果 intern 到审计 ledger；不再为了每次比较构造完整 scope snapshot。ledger
  不用于回答比较请求，每次仍做准入/推导，不引入 comparison cache 或跨 Run 导入。
  snapshot 将 ledger 转成闭合局部引用，close 清理运行态记录；不保存物化环境句柄。
- capture 前的 GLOBAL 查询返回当前状态，但没有固定根时不创建比较 membership，避免
  将临时缺失固化成另一基线。真实 unavailable subject 保持 UNAVAILABLE，随后合法 capture
  可以固定最高根。跨 Run/Cell、关闭及未登记引用的拒绝不导入 audit records。
- 初检 `uv run pytest tests/test_static_cache.py tests/test_runtime_static_scope.py --no-testmon -q --tb=short`：23 passed in 7.01s。
- 新增语义 identity 区分空诊断/实际 ty 失败/未采集原因/缺 reference/指导策略；同语义
  不同 Run identity 相等但 scope membership 独立。真实 request 测试保存 SLICE comparison
  refs/PASS 并验证局部重命名不改 identity；已有重复比较只 intern 一份审计记录。
  `uv run pytest tests/test_static_cache.py tests/test_static_request.py tests/test_runtime_static_scope.py --no-testmon -q --tb=short`：37 passed in 82.46s。
- 补充 capture 前查询边界后，
  `uv run pytest tests/test_static_cache.py --no-testmon -q --tb=short`：17 passed in 5.96s。
  最终 `uv run ty check --output-format concise`、
  `uv run ruff check src tests scripts --output-format concise`、`git diff --check` 均 exit 0。
  测试进程已结束；本片没有新的全套测试结果，上片 2413 passed 是此前完整回归证据。
- 本片仅完善已有 public scoped compare 路径与 scope 审计；生产 capture/evaluate 的旧
  `_increment` 及内嵌静态 Evaluation 尚未替换，Journal/report 尚未消费新 comparisons 表。
  S3 与 S4–S8 及最终 AC 保持未完成；无提交、推送或归档。


### 8.44 2026-09-07 · S3/S5 Run 收尾持久化与唯一 Journal v3

- 顺序细化：S3 的原始 scope/比较审计已可独立保存，先接入不依赖 S4 hint/probe 模型的
  Journal 部分，避免 Run close 丢弃现有证据。S5 报告与其余静态关联继续等待后续契约迁移；
  此前未完成的 S3/AC25 不因此关闭，S4 仍未开始。
- 将 Journal/entry/package policy 移至 `schemas/journal.py`，迁移所有生产、测试与脚本
  imports。唯一 contract 为 verification-journal-v3；删除历史模型、union/reader 与专属
  compatibility 测试，不提供旧 import alias 或双写。当前 fixtures 显式提供 static_scopes。
- VerificationRunner 在 gate 建立前创建 Run cache。gate 在 Cell 完成及最终 stop/drain 后
  snapshot 已完成 scope，写入 Journal 后才允许 owner close；操作异常也走同一收尾路径。
  JournalStaticScope 显式绑定 run_id/Cell；reader 核对唯一 scope/Cell、源码、执行策略及
  共享静态模型的 producer/consumer/PASS/comparison 闭包。静态区不增加 Failure entries。
- RunLogStore 拒绝非目标形状/无法解码 Journal，提供 JournalReadError 的稳定
  unsupported-journal-contract / invalid-static-evidence reason。拒绝不同 Run 的 writer
  输入和 reader 定位不一致，不再把坏静态证据当成缺少 Journal。
- 静态 process logs 使用独立 namespace 与 `lookup_static(run_id, scope_ref, process_ref)`；
  不用 Failure ID 冒充静态记录。同 Run portable rewrite 保留原静态日志 association，
  copied Journal 可在没有原日志的目录中复证 portable facts，日志缺失不伪造执行证据。
- 现有调用方定向命令：
  `uv run pytest tests/test_runlog.py tests/test_verification.py tests/test_diagnose.py tests/test_report_schema.py tests/test_report_workflows.py --no-testmon -q --tb=short`：242 passed in 1.05s。
- 新增实际 prepare/ty/ConfiguredVerifier→VerificationRunner→RunLogStore 测试：动态全 PASS、
  无 Failure 的 Journal 保存并重放 raw fact/PASS/comparison，Run cache 关闭后仍可读真实
  ty/verifier 日志，read→write bytes 稳定；伪造 Run/source/policy/consumer/comparison、
  duplicate Cell 与 unsupported/undecodable contract 均按稳定 reason 拒绝。
- fixture 第一轮 8 errors in 0.23s：误将 smoke SourcePlan 写成不存在的 SMOKE，且 RunLimits
  缺必需并发参数；类型检查同时指出。修正为 DEVELOPMENT/显式 limits。第二轮
  1 failed, 7 passed in 4.97s：测试错误地将 root-relative log locator 当 cwd 路径读取；
  改用公开安全 read_tail。修正后 10 passed in 5.34s。
- 真实容量用例以同一准备环境执行 12 个不同 timeout policy 的 ty 观察，生成
  13,311,199-byte Journal；旧 8 MiB metadata cap 使 reader 将已写文档当成 None，发现轮
  1 failed, 10 deselected in 25.10s。移除 Journal 专属小型 metadata cap，继续使用安全
  regular-file/path 读取；Index/Process Log 自有策略不变。
- 容量修正轮 1 failed, 13 passed in 33.85s：原断言比较了故意不进入 wire 的 ProcessResult
  内存 stdout cache；改验完整 portable model_dump，并保留单独实际日志读取证据。
- 追加受控 input-unavailable、ty-unavailable、PASS 后操作异常三种收尾，均保存已完成
  scope 且无兼容性 Failure；input-unavailable 保存未采集最高根而无 raw/process。
  最终 `uv run pytest tests/test_static_journal.py --no-testmon -q --tb=short -o junit_family=xunit1 --junitxml=/tmp/d038-static-journal-verified.xml`：14 passed in 32.24s。
  最终记录单观察 Journal 1,113,258 bytes、12 观察 13,311,197 bytes；这是本 fixture 的
  实际序列化大小，不是生产规模上限或缓存性能结论。
- D008 的 Journal/Index owner 规则同步至 v3；完整目标还需 report、两类静态 association、
  hint/纯静态 probe 以及 diagnose 展示迁移。旧 `_increment` 和内嵌静态 Evaluation 尚未
  替换；未完成最终 AC，不提交、推送或归档。ty/ruff/diff check 已通过，完整回归正在执行。
- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2428 passed in 194.31s。
  输出 `/tmp/d038-journal-v3-full.txt`；测试进程已正常退出。该结果同时覆盖上一片 comparison
  ledger 与本片唯一 Journal v3、容量修复、日志索引、正常/异常收尾及现有调用方。
- `uv run python scripts/generate_report_schema.py --check` exit 0；D008/P042 的 18 个本地
  Markdown 链接解析通过，最终 ty/ruff/diff check 均 exit 0。这些是当前实现/文档检查，
  不替代仍未实现的 S4、report/diagnose association 与最终 AC 验收。


### 8.45 2026-09-07 · S3 输入准入等待的 Run 取消接线

- 检查 public StaticEvaluator.collect 发现初次 prepared.static_use 未传 Run cancellation；
  即使后续 owner operation/permit 使用同一取消对象，等待初次输入锁的调用仍不能退出。
- 新增 cache miss/hit 两种 public collect 回归。另一个线程保持输入租用，调用 Run stop
  后等待者必须在该 owner 仍持有资源时收到 OperationCancelled；不额外启动 ty，也不
  丢失此前已完成事实。测试使用可控 Event 与公开资源/缓存接口，不读取私有锁或字典。
- 修复前 `uv run pytest tests/test_static_lifecycle.py -k run_stop_cancels --no-testmon -q --tb=short`：
  2 failed, 8 deselected in 2.19s；均为等待者 future.result 超时，确认是生产入口缺少取消接线。
- collect 的初次输入租用现传入 run_cache.cancellation；底层已实现的 cancellable lock
  等待负责唤醒与资源释放，不修改 raw fact/negative cache 语义。该文件 fixture 也显式
  关闭其创建的 SourceSnapshot，确保测试资源完整收尾。
- 修复后 `uv run pytest tests/test_static_lifecycle.py tests/test_static_cache.py tests/test_cancellation.py tests/test_runtime_static_scope.py tests/test_verification.py --no-testmon -q --tb=short`：
  78 passed in 9.45s。ty/ruff/diff check 均 exit 0；测试进程已结束。
- 本片不重复完整回归，上片 2428 passed 为此前全套证据。环境污染后的 owner 重建/复用
  联合验收仍待完成，不关闭 AC25/S3；旧生产静态比较、动态/静态关联分离、S4 及其余
  S5–S8 保持未完成。无提交、推送或归档。


### 8.46 2026-09-07 · S2/S4 删除算法层预测 oracle 与 promote 入口

- 核对发现产品 runner 虽已只返回直接动态证据，CoordinateSearch 仍接受旧 StaticOnlyEvidence，
  并将其 guidance 当成 PASS/REJECTED。删除该算法分支与 SearchEvidence union；oracle
  查询只接受 ProbePass/ProbeRejection/ProbeIndeterminate，否则抛明确的接口 TypeError，
  不记录成动态观测或缩小动态窗口。
- 删除 RuntimeBackedVectorEvaluator、产品 adapter/runner 和测量 wrapper 的 promote；
  已有直接复证统一通过 evaluate_in_slice。合并重复的 `_promote_version` 与 `_probe_version`
  路径，仍对每份直接证据执行终态与非单调/冲突检查。未实现新的静态二分或 hint 消费。
- 删除验证旧静态代表点预测/提升的专属测试；保留当前离散窗口与域外 sentinel 公开测试，
  新增三类真实静态 comparison result 被拒绝跨入 oracle seam 的权限负例。
- 初检 `uv run pytest tests/test_search.py tests/test_search_coordinator.py tests/test_search_workflow.py --no-testmon -q --tb=short`：63 passed in 0.95s。
- 最终 `uv run pytest tests/test_search.py tests/test_search_coordinator.py tests/test_search_workflow.py tests/test_search_space_report.py tests/test_report_workflows.py --no-testmon -q --tb=short`：98 passed in 1.66s。
- D033 受控测量脚本迁移为统一的直接接口，删除旧 STATIC_ONLY/静态预测计数；类型检查
  指出一个失去用途的 cast，已删除。`uv run python scripts/measure_d033_predecessor_revalidate.py --repeat 1 --output /tmp/d038-direct-oracle-measurement.json`：exit 0。
  此次运行验证脚本可执行，不作为性能比较或搜索收益证据。
- 最终 ty/ruff/diff check 均 exit 0，测试/测量进程已退出。本片未重复全套回归；最近完整
  证据仍为 8.44 的 2428 passed。region 的历史内存/wire 类型、旧生产 `_increment` 与
  内嵌静态 Evaluation 仍待清理；未据此通过 S4 或关闭最终 AC，无提交、推送或归档。


### 8.47 2026-09-07 · S2/S5 删除预测 observation 的内存与 wire 类型

- 删除 StaticOnlyEvidence/StaticOnlyEvidenceV1、旧 observation kind union 及公共导出，
  ProbeObservation 仅包含直接 ProbeEvidence。Report builder/reader 删除静态代表点编码、
  复原第二遍与旧区域预测准入分支；不再为 observation 编码传递 region ID 映射。
- 保留尚未迁移的区域诊断记录所需直接证据验证。原大型 report fixture 的低候选改成
  自己的 VerifierRejectedEvaluation/FailureRecord，而非借代表点预测；保留 source、
  Proposal、Attempt、failure refs 的通用安全负例，删除 obsolete static-only Slice 测试。
- `uv run pytest tests/test_schemas.py tests/test_report_schema.py tests/test_report.py --no-testmon -q --tb=short`：318 passed in 0.74s。
- `uv run python scripts/generate_report_schema.py` exit 0，生成 schema/examples；随后
  `uv run python scripts/generate_report_schema.py --check` exit 0。
  `uv run pytest tests/test_schemas.py tests/test_report_schema.py tests/test_report.py tests/test_report_workflows.py tests/test_search.py tests/test_search_workflow.py tests/test_diagnose.py --no-testmon -q --tb=short`：405 passed in 1.15s。
- 扫描发现当前 D038 资格记录尚含旧预测 wire；没有手工改写 evidence。执行
  `uv run python scripts/qualify_execution_failures.py --output /tmp/d038-direct-report-qualification.json`
  exit 0，再将完整生成结果写入当前 `tests/execution_qualification/2026-09-07-d038-uv-0.12.5-v1.json`。
  两个真实 resolve/install 场景均证明版本 1 的直接拒绝、最终版本 2 完整 PASS、两次
  configured verifier、失败之后新增完整 PASS 与现行 report 往返。旧日期资格文件作为历史
  记录保留，不由新 reader 兼容。
- `uv run pytest tests/test_execution_qualification.py -k manifest --no-testmon -q --tb=short`：
  1 passed, 2 deselected in 0.07s；此处验证新 manifest/fixture hash，真实重跑已由上一命令完成。
- 最终 ty/ruff/diff check 通过，当前生产代码、生成 schema/examples 与 D038 资格记录无
  StaticOnlyEvidence/STATIC_ONLY；完整回归正在执行。区域诊断 wire、内嵌静态 Evaluation、
  旧 `_increment` 及 S4 两阶段行为仍待迁移，不宣告整体 S5 或最终 AC 完成。
- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2431 passed in 196.05s。
  输出 `/tmp/d038-no-prediction-wire-full.txt`，测试进程已正常退出；同时覆盖 8.45 的取消
  接线、8.46 的直接 oracle 与本片 wire/reader/生成物迁移。此结果不替代未完成的目标行为
  验收，无提交、推送或归档。


### 8.48 2026-09-07 · S2/S4 删除搜索运行时旧区域收集

- 删除 `_ProposalRunner` 的 `_RegionPoint`、region point/index 聚合、重复 full region 记录、
  regions property 及只供区域索引使用的候选映射；产品 vector adapter、坐标搜索协议及
  D033 测量 wrapper 不再要求或返回 regions。CoordinateSearch 与 coordinator 不再输出
  旧区域收集结果，现有结果模型中的区域字段/wire 将在后续整体移除。
- 直接 PASS/REJECTED、当前 context 的 predecessor 重验、缓存复用与非单调/冲突检查保留。
  测试移除区域分组的旧断言，保留实际 Proposal/Attempt、边界 Failure ref、准备/执行次数
  与事件证据；不添加“旧字段为空”的长期迁移测试。
- `uv run pytest tests/test_search.py tests/test_search_coordinator.py tests/test_search_workflow.py tests/test_report_workflows.py tests/test_search_space_report.py --no-testmon -q --tb=short`：98 passed in 1.64s。
- `uv run python scripts/qualify_execution_failures.py --output /tmp/d038-without-runtime-regions.json`：
  exit 0；实际 resolve/install 场景仍为版本 1 合格拒绝、版本 2 完整 PASS、两次 verifier，
  report 往返通过。以完整生成结果更新当前 D038 资格记录，未手写转换历史 evidence。
  `uv run pytest tests/test_execution_qualification.py -k manifest --no-testmon -q --tb=short`：
  1 passed, 2 deselected in 0.07s。
- `uv run python scripts/measure_d033_predecessor_revalidate.py --repeat 1 --output /tmp/d038-no-runtime-regions-measurement.json`：
  exit 0，仅验证迁移后的脚本可运行，不提供性能结论。测试/资格/测量进程均已结束。
- 最终 ty/ruff/diff check 通过；本片未重复完整回归，8.47 的 2431 passed 是此前完整证据。
  旧区域模型/wire、内嵌静态 Evaluation、旧 `_increment` 与 S4 两阶段搜索仍待完成，
  不据此关闭最终 AC，无提交、推送或归档。


### 8.49 2026-09-07 · S2/S5 删除旧区域模型与报告 wire

- 删除 StaticRegionSlice、StaticRegion、代表点引用及 region identity/校验函数、公共导出；
  CoordinateSuccess/Failure 与对应 Schema 1 模型删除 regions 字段。Report builder/reader
  删除区域编码、两阶段区域复原及区域专用 CandidateSnapshot 参数，仍验证直接 observation、
  Proposal/Attempt、边界与 Failure 的闭包；不提供旧 region reader 或兼容层。
- 删除区域专属测试；原大型报告 fixture 改名为 direct_search，保留真实直接拒绝/PASS 的
  往返、身份和安全准入测试。删除旧 TY_DIAGNOSTIC_POLICY 的 region_scope provenance 标记，
  尚未完成的完整 Guidance/SearchDerivation wire 迁移仍按 S5 推进。
- `uv run pytest tests/test_schemas.py tests/test_report_schema.py tests/test_report.py tests/test_report_workflows.py tests/test_search.py tests/test_search_workflow.py tests/test_diagnose.py --no-testmon -q --tb=short`：391 passed in 1.21s。
  最终加入 `tests/test_policy.py` 的同组命令：418 passed in 1.22s。
- `uv run python scripts/generate_report_schema.py` 及 `--check` 均 exit 0；schema/examples
  由生成器更新。类型/lint 检查通过；当前源代码不再引用旧区域模型。
- `uv run python scripts/qualify_execution_failures.py --output /tmp/d038-no-region-wire.json`
  exit 0；删除 provenance 标记后重新执行，以完整实际生成结果替换当前 D038 资格记录。
  旧日期资格记录保留为历史证据，不承担现行 reader 兼容性。完整回归输出至
  `/tmp/d038-no-region-wire-full.txt`，结果待回填。
- 本片只关闭旧区域契约清理。生产 StaticEvaluator 的旧比较、内嵌静态 Evaluation、
  S4 两阶段搜索及其他最终 AC 仍待完成；不归档、不提交或推送。

- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2417 passed in 194.70s (0:03:14)。
  测试进程 exit 0，最终 ty/ruff/diff check 均通过；本结果不替代尚未完成的 D038 行为验收。


### 8.50 2026-09-08 · S2/S5 动态终态与静态关联分离

- 从三种动态 Evaluation 及其 wire record 删除 static/static_evaluation_ref；Run wrapper
  保存单次静态结果，Cell/最高版本/check 聚合保存独立 static_evaluations。关联继续校验
  Proposal/Cell/context，动态终态的构造、缓存与 failure authority 不依赖静态内容。
- 同步生产聚合、report 表收集/reader、fixtures、生成物与资格证据；不保留旧字段 alias。
  当前 StaticEvaluation 类型本身与新 scope 的替换另随 S3/S5 完成，不将本片视为全部闭环。
- 验证结果待回填。

- 已完成生产接线：RuntimeEvaluationRun.static_evaluation 独立关联一次执行；最高版本、
  check 与搜索 Cell 聚合的 static_evaluations 保留采集结果。三种动态 Evaluation 仅含
  Proposal、verifier 终态（及动态 cause），不再含 static。ProbeEvidence 删除动态对象上
  的派生静态 accessor；搜索 runner 独立收集静态关联，缓存仍只接收 Evaluation。
- 三种动态 wire record 删除 static_evaluation_ref；Report builder 从 Cell 关联收集独立
  静态表，reader 单独复原动态终态，再对 Cell 关联重新执行模型校验。修复最终 PASS 没有
  静态关联时的虚假 reachability；仍需保留实际最高版本 capture root，后者的 scope 迁移
  不在本片冒充完成。
- 保留静态关联的 Proposal/Cell/source/policy/baseline 闭包与捕获 TyCheck 一致性检查；
  RuntimeEvaluationRun 拒绝外来 Proposal 的静态关联。动态 cache 测试通过两个不同静态
  wrapper 的实际相同 Evaluation payload 验证复用；report 正例验证同一最终 Proposal 的
  unchanged/regression 关联生成完全相同的动态 PASS wire，并允许最终 PASS 无关联。
- 迁移原 fixtures；删除仅供旧动态构造使用的静态 setup，摘要/explain 静态场景改为真正
  携带独立关联，避免失去语义覆盖。大诊断 fixture 修正为按稳定 identity 排序；内存 process
  输出缓存不进入 portable facts，往返关联断言比较 model_dump，而不误比本机缓存。
- 初轮生产与相关测试 90 passed/1 个旧字段断言失败，已迁移；报告/schema/授权/失败组
  377 passed/1 个校验文本断言失败，已改到现行关联校验。首轮完整回归为 2415 passed、
  1 failed in 196.78s，失败为 diagnose fixture 漏传新关联，已修复。此轮非最终 PASS 证据。
- `uv run pytest tests/test_terminal.py tests/test_evaluation_cache.py tests/test_report_schema.py tests/test_schemas.py tests/test_evaluation.py tests/test_runtime_static_scope.py tests/test_execution_qualification.py -k 'not failure_cannot_be_a_floor' --no-testmon -q --tb=short`：462 passed in 14.85s，包含当前 native qualification。
- `uv run python scripts/qualify_execution_failures.py --output /tmp/d038-dynamic-associations-qualification.json`
  exit 0，resolve/install 均版本 1 直接拒绝、版本 2 完整 PASS、两次 verifier、报告往返通过；
  已以完整生成结果更新当前 D038 资格文件。generator 及 --check 通过；新 schema/examples
  的动态终态不含静态引用。新完整回归写入 `/tmp/d038-dynamic-associations-full.txt`，待回填。
- 旧 StaticEvaluator._increment/StaticEvaluation 比较类型及完整 report scope 替换仍待完成；
  S4 两阶段算法、AC25 所有权联合场景、S6–S8 未完成。不关闭最终 AC，不归档、提交或推送。

- 最终定向命令 `uv run pytest tests/test_diagnose.py tests/test_report_schema.py tests/test_terminal.py tests/test_evaluation_cache.py --no-testmon -q --tb=short`：316 passed in 1.52s。
- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2420 passed in 190.79s (0:03:10)。
  测试进程 exit 0；最终 ty/ruff/generator --check/diff check 通过。全部本片测试/资格进程已结束；
  此结果不替代 S3–S8 未完成行为的验收，无提交或推送。


### 8.51 2026-09-08 · S2/S3 RuntimeEvaluator 执行边界

- 移除 RuntimeEvaluator 对 StaticEvaluator 的构造依赖和 evaluate 的 baseline 参数；
  configured verifier 执行不再隐式触发采集或比较。最高版本/check/search 编排者显式负责
  静态步骤，传入的可选 static_result 仅用于独立 Run 关联；Run cache 仍登记实际完整 PASS。
- 同步装配、公开测试和原命令/pruning 资格入口，保留静态不可用时的动态执行及真实 PASS refs。
  验证结果待回填；此片不替代共享 comparison/完整 scope/两阶段算法的后续工作。

- 生产 RuntimeEvaluator 不再拥有 StaticEvaluator，不接收 baseline，不隐式调用 capture/evaluate。
  check 显式在最低直接环境上采集/比较后启动 verifier；highest/search 继续复用已显式取得的
  静态结果。构造器、CLI、资格脚本、public fixtures 及测试调用点全部迁移，无兼容参数。
- 新增三类 verifier 终态无需任何静态 capture 的公开正例：实际 Proposal/verifier outcome
  保持、ty 调用数 0、verifier 调用数 1、prepared 标记 tested，空静态 scope 不补造 PASS anchor。
  静态 regression/unavailable、清除失效 consumer 后的实际 PASS、配置 policy mismatch 的原测试
  改为显式静态步骤，继续断言真实诊断与 scope membership。
- 批量迁移脚本尚运行时曾过早启动类型检查/测试，读取了中间状态；这轮失败不作为产品回归
  结论。脚本正常结束后重跑；一个遗漏的显式 recapture 调用已修复。
- 实际 `tests/static_fixtures.py` 的 prepare 在两次运行中返回 PrepareFailure；原断言未保留
  足够底层 stderr，无法确定原因。按 diagnosing-bugs 流程缩到单个真实 consumer/PASS 用例并
  临时加入脱敏失败输出后，在未修改生产 prepare 路径的情况下 1 passed in 8.30s；随后扩大
  回归通过。没有据历史 TLS/cache 经验推断本次根因，也未修改生产失败分类或放宽断言。
- `uv run pytest tests/test_evaluation.py tests/test_baseline.py tests/test_check.py tests/test_runtime_static_scope.py tests/test_search_coordinator.py tests/test_pytest_pruning.py tests/test_static_journal.py tests/test_verification.py --no-testmon -q --tb=short`：188 passed in 41.03s，输出 `/tmp/d038-runtime-boundary-final.txt`。
- `uv run python scripts/qualify_execution_failures.py --output /tmp/d038-explicit-runtime-qualification.json`
  exit 0，resolve/install 均版本 1 合格直接拒绝、版本 2 完整 PASS、两次 verifier、report 往返通过；
  以真实生成结果更新当前 D038 资格文件。临时 DEBUG 输出已移除，所有运行进程已正常退出。
- 本片未重复全套回归，最近全套是 8.50 的 2420 passed。旧 StaticEvaluation/_increment、
  report 完整 scope、S4/S6–S8 仍待完成；不关闭最终 AC、不归档、提交或推送。

- 最终 ty/ruff/generator --check/diff check 通过；`uv run pytest tests/test_execution_qualification.py -k manifest --no-testmon -q --tb=short`：1 passed, 2 deselected in 0.07s。


### 8.52 2026-09-08 · S5 搜索报告静态 scope 接线

- Search Verification Run 返回独立 SearchVerificationResult，包含 Cell results 与停止/排空后、
  close 前取得的静态 scope 快照；check/smoke 现有结果入口不变，仍由 Journal 保存 scope。
- 报告 evidence 增加唯一当前形状的 static_scopes，builder/reader 绑定 Cell/source/ExecutionPolicy/
  SourcePlan 与动态 anchor PASS；workflow 显式传入，merge/update 保留来源 scope，局部引用
  冲突只重命名所属 scope，不构造跨 scope comparison。不提供旧 wire fallback。
- 原始事实全局 interning、静态日志 report association、旧静态比较/table 替换仍随 S3/S5
  继续，本片只建立生产可达的 scope 保存/读取闭包。验证结果待回填。

- SearchVerificationResult 已生产接线至 SearchCommandWorkflow → PackageReportBuilder；cache
  stop/drain、Journal finalize、scope snapshot、cache close 的顺序固定，未将活环境或 cache handle
  保存在报告。ValidatedReport 公开独立 static_scopes，动态终态仍不含静态引用。
- reader 对整个 scope 执行共享模型准入，再绑定报告的 Cell/source/ExecutionPolicy/SourcePlan；
  scope consumer 与已知动态 Proposal 必须一致，scope anchor PASS 必须对应真实动态 PASS。
  merge 对每个输入文档保留独立 membership，局部 scope_ref 冲突只重命名顶层 scope，内部
  引用仍闭合且 comparison 语义 identity 不变；update 移除被替换 Cell 的旧 scope、保留其他 Cell。
- 实际报告测试从 native prepare/ty/configured verifier 取得证据，Run/cache/source snapshot
  关闭后才构建和读写报告；覆盖 readonly lookup/compare 回放、byte-stable roundtrip、外来源码
  scope、伪造最高 root/anchor/comparison、非法 null 与缺失 required-nullable 拒绝。
  追加真实 SearchCommandWorkflow 落盘及新的 Run scope、不采集最高静态内容但动态完整 PASS
  的报告正例。共享 comparison 由测试显式调用 public seam；生产旧 _increment 尚未替换。
- 实际 unavailable 场景暴露 ToolFailure.process 的 required-nullable 被 exclude_none 丢弃；
  为该现行静态失败字段保留显式 null，并同步 reader/schema。静态 scope 使用完整 preimage
  子协议，与普通紧凑 report 的 optional-omit 规则分开；generator 对 scope 可达定义生成独立
  Scope_ schema，保留完整 nullable/default 字段并剪去不可达定义，避免改变其他共享类型的
  紧凑 wire。真实可用/不可用报告额外由 Draft202012Validator 验证生成 schema。
- 首轮相关公开回归 204 passed/1 个 SearchVerificationResult 调用点断言遗漏，已修复；
  首轮完整回归 2431 passed、1 failed in 205.32s，失败为 schema nullable 契约测试未迁移。
  后续 scope/schema 组 `uv run pytest tests/test_static_report.py tests/test_report_artifacts.py --no-testmon -q --tb=short`：16 passed in 12.19s。生成器克隆过程中一次错误地把临时 schema
  标记当 required property 的问题已由真实 JSON Schema 验证发现并修复；无 DEBUG 遗留。
- `uv run python scripts/qualify_execution_failures.py --output /tmp/d038-report-scopes-qualification.json`
  exit 0。脚本在 Run cache close 前取得真实 scope，并将其送入报告往返；两个执行失败场景
  仍得到最终版本 2 完整 PASS、两次 verifier 与 report roundtrip。当前资格文件已由实际结果更新。
- 最终 ty/ruff/generator --check/diff check 通过。最终全套回归待回填；原始事实跨 scope
  interning、报告静态日志 association、旧比较/table 删除及 S4/S6–S8 仍未完成。

- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short`：2434 passed in 210.98s (0:03:30)，输出
  `/tmp/d038-report-scopes-final-full.txt`，进程 exit 0。包含新 scope 的真实 workflow、available/
  uncollected 报告 JSON Schema 验证与前片 RuntimeEvaluator 的三种纯动态终态测试。
  最终 ty/ruff/generator --check/diff check 通过，无提交、推送或归档。

### 8.53 S3：check 迁移至独立 GLOBAL comparison（本片完成）

- 先完成 declaration check 这一完整生产路径：公开 prepared collection，Run cache 从固定
  highest root 生成显式 GLOBAL context，经共享 comparison 准入与复算并保存 scope audit。
  check outcome 删除旧静态 baseline/evaluation 聚合字段，动态终态继续独立。
- 原始 collection 仍遵守 materialization/cancellation/owner 生命周期；内容未采集不伪造
  consumer、process 或 comparison。测试以公开 scope 的实际状态、producer 与比较记录为证据。
- 后续同样迁移 highest/search 与旧报告静态表。本片不把旧 `_increment` 在其他路径的存在
  或部分字段删除视作 S3 完成。验证命令与结果待回填。

- `StaticEvaluator.collect_prepared` 统一公开请求捕获与原始 collect；旧 `_check` 暂时委托该
  seam，但 check 已不调用 capture/evaluate 或构造任何旧 StaticEvaluation/StaticBaseline。
  `TyCheckCache.compare_global` 在同一锁内解析固定 root 并调用已有 compare，结果仍逐次推导，
  audit 只做证据 interning。GLOBAL 的完整 context/guidance/membership 准入不变。
- check 保留 highest→close→lowest-direct→ty→configured verifier→close 时序；最高内容未
  采集时固定 uncollected root，lowest 内容未采集时仍执行 verifier，但不构造比较或 PASS anchor。
  CheckCellOutcome 删除旧静态聚合字段；完整动态 Evaluation、FailureRecord 及运行诊断保留。
- 公开生产测试覆盖 12 组 ty collection 可用性 × verifier 终态、highest 静态失败后 lowest
  prepare 失败的事实保留，以及 3 组实际 duplicate diagnostic/未采集输入组合。
  最高 1 条、lowest 3 条相同 diagnostic 得到 GLOBAL multiset 增量 2 条且最终 verifier PASS。
  terminal 测试迁移到纯动态 outcome，不继续构造不再属于 check 契约的旧静态关联。
- 首轮局部回归 166 passed/1 failed，失败为迁移测试误用了 raw fact 的 `.status`（现行字段
  为 kind/reason）；已改为 typed unavailable 与 exit-code reason 断言。扩大局部回归
  `uv run pytest tests/test_check.py tests/test_terminal.py tests/test_static_cache.py tests/test_evaluation.py --no-testmon -q --tb=short`：196 passed/1 failed in 7.38s，唯一失败为旧阶段文字；
  公开 collect 统一发出 static check，断言同步。ty/ruff/generator --check/diff check 已通过。
- 完整回归 `uv run pytest tests --no-testmon -q --tb=short` 输出到
  `/tmp/d038-check-global-full.txt`，结果待回填。S3 其他路径仍存在旧 `_increment` 与静态聚合，
  旧 report table 尚未删除；未把本片声明为全部迁移完成。

- 最终完整回归 2435 passed in 209.88s (0:03:29)，进程 exit 0，输出
  `/tmp/d038-check-global-full.txt`。本片完成；整体 S3 继续进行，无提交、推送或归档。

### 8.54 S3：search 候选路径迁移至共享 GLOBAL comparison（本片完成）

- `_ProposalRunner.evaluate_full` 改用公开 prepared collection 与 Run 固定 root GLOBAL
  comparison；完整 verifier 单独执行，候选不再构造旧 StaticEvaluation 或调用 `_increment`。
- 先保留 highest 的旧 report root，候选静态事实由 scope 独立保存；移除 runner 无用的静态
  baseline 参数与候选聚合写入。后续删除 highest 旧聚合和 report table，不增加兼容读取。
- 公开 search→report 往返测试迁移为真实 scope comparison 状态和 membership 的断言；
  验证候选及最高静态不可用时直接动态结果、failure authority 和报告证据闭包。

- search 候选生产链路已切至 collect_prepared→固定 root GLOBAL compare→configured verifier；
  runner 不再接收 StaticBaseline，也不再写入候选旧静态表。highest 的旧聚合暂时保留，
  report 已支持候选静态事实只由 scope 承载；动态 Proposal/PASS/Failure 关联仍由 reader 验证。
- 首轮相关回归 67 passed/2 failed in 23.77s；失败是新共享准入拒绝旧 scripted fixture 的
  effective config。单例命令
  `uv run pytest 'tests/test_search_coordinator.py::TestSearchCoordinator::test_search_returns_a_runtime_backed_floor_with_closed_public_evidence[none-False]' --no-testmon -q --tb=short`
  稳定 0.16s 复现。按 diagnosing-bugs 检查了配置、布局、harness、policy，只有配置不同。
  ScriptedStaticRequests 原先将 exact-vector 会改写的 pyproject 当有效配置；现像 native factory
  一样物化独立配置文件，保留原 pyproject 输入。修复后单例 1 passed in 0.15s，未放宽生产准入。
- 扩大公开回归
  `uv run pytest tests/test_search_coordinator.py tests/test_search.py tests/test_static_report.py tests/test_execution_qualification.py tests/test_check.py tests/test_static_cache.py tests/test_evaluation.py --no-testmon -q --tb=short`
  140 passed in 29.85s（原生资格 profile 扩充前）。包含 check 的多重集及 unavailable 公共断言。
- 原生资格脚本新增 GLOBAL 状态断言时首次失败，定位到 build backend 导入产生的
  `snapshot/__pycache__/qualification_backend.cpython-310.pyc` 含物化路径。其配置、layout、harness、
  policy 一致，pyproject 差异由现行规则准入，额外 bytecode 内容差异则正确返回
  UNCOMPARED(context-mismatch)。这不是生产 regression，也不通过忽略未知源码 bytes 修复。
- 资格脚本现显式覆盖 backend bytecode 开启/关闭两个 profile，临时设置并恢复
  PYTHONDONTWRITEBYTECODE；开启场景保留真实 context-mismatch，关闭场景证明原生
  COMPARED/STATIC_UNCHANGED。resolve/install 共四个 case 都最终版本 2 完整 PASS、两次
  verifier、真实失败边界和报告 scope roundtrip；comparison 由生产 search 产生，非测试补写。
- `uv run python scripts/qualify_execution_failures.py --output /tmp/d038-search-global-qualification.json`
  exit 0；当前 qualification manifest 已由该实际输出更新，公开 replay 测试覆盖四个 profile。
  所有 DEBUG 探针已清除；ty/ruff/generator --check 通过（最终完整回归后复查）。
- 最终完整回归 `uv run pytest tests --no-testmon -q --tb=short` 正在执行，输出
  `/tmp/d038-search-global-full.txt`。旧 evaluator evaluate/_increment 已无 check/search 生产调用，
  仍需删除其 API/专属旧测试，并迁移 highest 静态聚合与旧 report table；S3/S4–S8 未完成。

- 最终完整回归 2437 passed in 219.74s (0:03:39)，进程 exit 0，输出
  `/tmp/d038-search-global-full.txt`。最终 ty/ruff/generator --check/diff check 全部通过。
  四种原生资格 profile 均由完整测试重放通过；无 DEBUG 遗留，无提交、推送或归档。
- 下一片优先删除无生产调用的 StaticEvaluator.evaluate/_increment，随后将 highest root
  彻底归入 scope，删除 HighestVersionPass/CellResult 的旧静态字段及 report 静态 table/
  baseline digest，迁移其公开 fixtures/reader tests。不要以保留旧聚合作为最终兼容方案。

### 8.55 S3：删除旧比较入口与动态运行静态侧载（本片完成）

- 删除已无生产调用的 StaticEvaluator.evaluate 与私有 `_increment`；共享 comparison 是唯一
  在线诊断差分实现。旧测试迁移到 collect_prepared/Run GLOBAL comparison，删除旧 baseline
  wrapper 准入专属测试；现行跨 scope 安全测试仍由 Run/scope public seam 覆盖。
- RuntimeEvaluationRun 删除静态侧载，RuntimeEvaluator 删除 static_result 参数；highest
  暂时保留的旧 aggregate 不再穿过动态 owner。实际静态 consumer→完整 PASS 的 Run 登记保留。
- 回填精确验证结果后继续 highest/report 聚合清理，不以本片作为 D038 完成证据。

- 已删除旧 evaluate/_increment 和 RuntimeEvaluationRun.static_evaluation/
  RuntimeEvaluator.static_result，highest 的动态调用同步迁移。静态多重集、regression 对
  verifier 无终止权限和 consumer 清除测试改用 raw observation/共享 comparison。
- 首轮 46 passed/3 failed 为迁移断言误写 raw kind；修正为 ty-check-unavailable。
  `uv run pytest tests/test_evaluation.py tests/test_evaluation_cache.py tests/test_runtime_static_scope.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py tests/test_report.py --no-testmon -q --tb=short`：116 passed in 5.15s。ty/ruff 通过。

### 8.56 S3/S5：highest 与 report 旧静态聚合删除（本片完成）

- HighestVersionPass 与 baseline 失败只保留真实 Attempt、动态 Evaluation、HarnessBaseline
  和 FailureRecord；highest 静态 root 由 Run scope 保存。
- CellResult 的上下文准入改依赖最高动态 PASS 的 Proposal；保留 Cell/source/policy/Attempt
  及直接搜索证据验证。删除旧 static_evaluations/static_baseline 字段。
- report 删除旧 StaticEvaluation table 与 baseline static digest，producer/reader 仅通过
  scope 记录和验证静态事实。更新 schema/examples、公开 fixtures 和报告准入测试。
- 本片允许迁移中的测试暂时失败；完成须以目标 wire 与动态/静态各自闭合的实际证据验证。

- HighestVersionVerifier 已直接采集 raw fact 并设置 Run highest 或 uncollected root；不调用
  旧 capture wrapper。HighestVersionPass/BaselineRejection/BaselineIndeterminate 和三种
  CellResult 删除旧静态字段；所有生产调用方已迁移，类型检查无遗留引用。
- CellResult 保留最高动态 Proposal 的 Cell/source/ExecutionPolicy/Attempt 一致性、候选
  窗口、直接动态证据与 failure 引用检查。最高 baseline 的 Proposal/Attempt 安全测试已迁移；
  对比两个旧 static wrappers 的测试删除，相关静态跨 scope/root 准入由实际 scope 测试负责。
- report 的 StaticEvaluation wire table、encoder/reader、可达性集合、baseline static digest
  和 required-nullable 特例已删除；静态事实完全通过 static_scopes 保存/回放。生成 schema 与
  示例已重新生成，不接受旧 wire；完整动态 PASS 可独立于任何静态 scope 存在。
- 旧表引用、旧静态 aggregate 指纹和旧 terminal 静态历史 fixtures 已清理；不保留证明旧字段
  消失的过渡测试。完整 PASS、失败诊断和报告上下文测试保留；scope 内静态事实/比较测试保留。
- 首轮 focused 39 passed/6 failed 为旧 baseline 属性断言和生成 schema 未更新；后续
  310 passed/3 failed 为旧 captured wrapper 测试、空 scope duplicate fixture、已删除 schema
  nullable 字段预期。分别迁移为动态 Attempt 准入、移除旧表专属 case、同步目标 schema 预期。
- `uv run pytest tests/test_baseline.py tests/test_smoke.py tests/test_schemas.py tests/test_report_schema.py tests/test_static_report.py tests/test_report_artifacts.py tests/test_terminal.py tests/test_explain_terminal.py --no-testmon -q --tb=short`
  456 passed in 13.87s，输出 `/tmp/d038-aggregate-final-focused.txt`。ty/ruff 已通过。
- 完整回归 `uv run pytest tests --no-testmon -q --tb=short` 正在执行，输出
  `/tmp/d038-scope-only-report-full.txt`。旧 StaticEvaluator.capture 与旧 StaticEvaluation 类型
  仍存在于测试/旧 detail 类型，尚待删除；scope raw fact 全局 interning、静态日志 report
  association、S4 guidance 与 S6–S8 仍未完成。

- 首轮全套 2419 passed/2 failed in 222.19s；两项遗漏位于 test_failure，仍期待已删除的
  HighestVersionPass ty/digest wrapper 校验。该组改为现行 Cell 与 Attempt 动态不匹配拒绝，
  `uv run pytest tests/test_failure.py --no-testmon -q --tb=short`：28 passed in 0.06s。
  最终全套输出改为 `/tmp/d038-scope-only-report-final-full.txt`，结果待回填。

- 最终完整回归 2419 passed in 219.05s (0:03:39)，进程 exit 0，输出
  `/tmp/d038-scope-only-report-final-full.txt`。测试数量下降来自删除旧静态 wrapper/table 专属
  case；当前动态安全、scope 准入和四个原生资格场景全部包含在本次完整 PASS 中。
  ty/ruff/generator --check/diff check 通过。无提交、推送或归档。
- 后续先清除无生产调用的 StaticEvaluator.capture/_check 和残余旧静态类型/fixture/detail，
  再继续 S4 两阶段局部 guidance 及剩余持久化/UI/资格审计；整体目标仍未完成。

### 8.57 S3/S6：删除残余静态 wrapper 与失败展示 detail（进行中）

- 删除无生产调用的 StaticEvaluator.capture/_check/_input_unavailable 与旧
  StaticBaseline/StaticEvaluation/StaticBaselineCapture 类型；测试使用真实 raw collect、固定
  highest root 和共享 comparison，不通过旧 wrapper 构造静态结果。
- CellFailed 的 detail 仅接受实际 pytest failure detail，移除无生产来源的 StaticIssueDetail
  及其失败卡展示。静态诊断的 scope-linked diagnose 展示仍由后续 S6 实现。
- 清理旧 fingerprint/diagnostic baseline digest、fixtures 与专属过渡测试；保留 raw diagnostic
  格式、多重集、比较指纹、跨 scope 准入、静态不可用下的完整 verifier 等目标契约测试。

- wrapper 清理相关回归最终 485 passed in 5.36s；ty 通过，schema/examples 已生成。
  唯一中间失败是未采集输入错误期待 RunTyFactRef，已改为真实 StaticContentUnavailable。
- S4 前置核对：D038 §9 明确要求 cache slice/AC25 联合验收后再接 search。当前 Plan 尚未
  关闭该项，因此先审计并补齐 runner 所有权场景，不将已有分层测试拼接为完整联合证据。

### 8.58 S3/AC25：同 key consumer 输入租用联合验收（进行中）

- 当前 StaticEvaluator 在初次 revalidate 后释放 consumer 的 static_use，再进入 cache join；
  owner 回调重新持锁，但同 key 不同 Proposal 的等待者未持有输入租用。这可能允许等待中的
  consumer 被 close/交给 verifier。先以公开 StaticEvaluator/PreparedEnvironment/cache seam
  的阻塞 lower TyOperations 复现，再修复整个 collect/join 的 consumer 生命周期。
- 检查等待期内容变化的准入时刻，保证失效 consumer 不进入 scope，仍保留已完成 owner raw
  fact。补齐成功/Unavailable、close/verifier、取消/异常及后续干净重建证据后再审计 AC25。

- 公开 runner 联合测试实际复现了等待者提前 close/verifier（4 failed、4 passed，0.38s），
  以及 RuntimeEvaluator 在获取 verifier lease 前读取 consumer 导致 PASS 关联丢失
  （6 failed、2 passed，0.42s）。StaticEvaluator 现持有每个 consumer 的输入 lease 直到
  capture/owner-or-join/consumer admission 全部完成；RuntimeEvaluator 在 verifier lease 内
  读取 consumer。只有 elected owner 获取 ty permit。修复后相关回归 49 passed in 4.96s。
- 等待期间输入改变的 consumer 原先仍会注册（2 failed，0.09s）。TyCheckCache.collect
  增加必填 revalidate 回调；hit/join 在 caller lease 内、cache condition 外复证输入，失效
  consumer 不注册，保留干净 owner 的 raw fact。相关回归 68 passed in 10.73s。
- 成功/Unavailable、不同 Proposal 的 owner/waiter、close/真实 RuntimeEvaluator、取消与
  未建模异常的联合回归 90 passed in 10.13s：进程 cleanup 完成前 owner 输入不释放，取消
  stop 等待收拢，waiter 唤醒，无 raw negative 伪造，停止后的 Run 不重试。
- 另复现已证明污染的 waiter 仍能启动 verifier（2 failed，0.10s）。PreparedEnvironment
  记录独立检测到的执行输入失效；StaticTyRequest 对 source/installed/target 内容的变化
  使该环境失效，verifier_use 以 InfrastructureError 子类 MaterializationIntegrityError
  拒绝执行。request recapture 不清除失效标记。单纯 ty 工具变化不污染动态输入。
  相关回归 53 passed in 5.52s。
- 原生测试改为先证明旧环境污染拒绝，再使用独立、验证过路径重定位的安装副本，在首次
  capture 前引入不同安装内容并独立采集。该 fixture 不声称 fresh uv build 字节相同；
  scripted 联合用例另从原 SourceSnapshot 实际重新 prepare，并验证相同/不同投影复用。
  `uv run pytest tests/test_static_request.py tests/test_static_ownership.py --no-testmon -q --tb=short`
  → 25 passed in 84.80s。随后添加静态工具变化仍允许完整 verifier 的用例；首次断言误写
  小写 pass，实际为 PASS，已修正。完整回归正在运行，结果待填。
- `uv run ty check`、`uv run ruff check src tests scripts`、
  `uv run python scripts/generate_report_schema.py --check` 已通过；清理了 wrapper 删除留下的
  空白。当前没有提前冗余环境释放分支：每个 consumer 保留自身环境，满足 §5.4 允许的保留
  路径；未来 S4 若新增释放分支，仍必须验证精确动态等价与 clean 替代，不可由静态同 key 推断。

- 操作返回 StaticContentUnavailable 时仍须完成独立输入复证：新增公开测试复现旧提前返回
  导致污染环境进入 verifier（1 failed in 0.10s），调整 StaticEvaluator 所有正常返回路径的
  post-check 顺序。`uv run pytest tests/test_static_ownership.py tests/test_static_cache.py tests/test_static_lifecycle.py tests/test_cancellation.py tests/test_verification.py tests/test_runtime_static_scope.py --no-testmon -q --tb=short`
  → **92 passed in 10.82s**。ty、ruff、diff check 再次通过。此前已启动的全套测试仍运行中，
  它不能替代本次最终 post-check 修改的这条回归证据。
- **AC25 foundation 审计通过**：公开 StaticEvaluator/PreparedEnvironment/RuntimeEvaluator 联合
  用例覆盖 elected owner 唯一操作与 ty permit、同 key 不同 Proposal 的全部输入保留、共享
  成功与 Unavailable、等待中 close/verifier 排斥及准确 PASS 关联、取消/异常 cleanup 后
  释放与唤醒。底层真实进程取消/收拢由 test_cancellation，输入/permit admission 取消由
  test_static_lifecycle 补充。没有执行任何同 ty key 的冗余环境提前释放；不能用该结论批准
  未来释放优化。S4 的坐标结束/NO_HINT/跨 sweep 环境清理与峰值属于尚待实现的 AC9/E4。
  AC25 前置现已闭合，可以接入 S4；S3 其余 AC 与完整目标仍待最终矩阵审计。

### 8.59 S4：local anchor 自比较及两阶段入口（进行中）

- 对照 §6.2–§6.3，local unchanged 上端必须来自真实 anchor 自比较，包括 highest anchor
  和不属于当前候选域的 U。现有 SLICE admission 对所有 subject 强制 exact-vector 和窗口
  membership，尚不能接受这些只读 anchor；先补公共比较/离线 scope 正例，再连接静态二分。
  anchor 自比较仍须准确 PASS、同 scope、同执行/静态 context 和固定其他坐标，不放宽普通
  candidate 的 exact-vector/窗口准入。两阶段算法和 search-facing runner 仍未实现。

- §8.57–§8.58 完整回归终态：
  `uv run pytest tests --no-testmon -q --tb=short > /tmp/d038-ownership-final-full.txt 2>&1`
  → **2425 passed in 259.17s**，exit 0。该进程先于最后的 unavailable post-check 调整启动，
  后者由 §8.58 的 92 项最终联合回归验证；不把本条记为后续 S4 修改的完整回归。
- 新增真实 highest Proposal + 实际 verifier PASS 的 scope 自比较正例，首先得到
  `2 failed, 2 passed in 21.35s`（错误结果为 UNCOMPARED）。共享 SLICE admission 现在先
  验证 PASS/context/固定坐标和不越过 U 的窗口，再允许 subject 与 reference 完全相同的
  anchor 自比较；其他 subject 仍须 exact-vector 及所选 artifact 的窗口 membership。
  `uv run pytest tests/test_static_request.py -k nonempty --no-testmon -q --tb=short`
  → **4 passed, 9 deselected in 34.43s**。相同比较的离线 document round-trip 同时通过。
  候选域外 anchor 的 search 集成、hint tag/refs 与真正两阶段时序仍须在 S4 补齐；不由本次
  highest 正例推断这些路径已交付。ty 检查通过。
- `uv run pytest tests/test_static_cache.py tests/test_static_report.py tests/test_static_journal.py --no-testmon -q --tb=short`
  → **42 passed in 59.29s**；S4 anchor 修改后的 ruff 与 diff check 通过。
  下一实施点是 search-facing direct lookup / anchor / inspect / finish_coordinate 的实际 runner
  连接与两阶段算法，同时形成可持久化的阶段、窗口和 endpoint 证据。整体目标保持进行中，
  无提交、推送或归档。

### 8.60 S4：只读直接证据与坐标收尾接入（进行中）

- 上一轮为实际进展：所有权/污染保护与真实 anchor 自比较均已修改并验证。本轮从当前
  CoordinateSearch/_ProposalRunner 继续实现 §6.1/§6.2，未重启任何旧验证进程。
- 新增公开 DirectEvidenceLookup 和 CoordinateEnvironmentOwner 协议，产品 evaluator 转交
  runner 的 lookup_direct_in_slice / finish_coordinate。只读查询仅返回本 runner 固定
  execution context 中已有的完整 ProbeRun，不 prepare、ty 或 verifier。
- current 的直接前驱若已有合格结果，即使尚无 history，也由同一动态观察/冲突检查路径
  消费；拒绝立即定界，通过收紧动态上端。未命中且无有效 history 不启动额外前驱执行。
  普通 oracle 调用先读取该直接 cache，保留所有既有 evidence 校验。
- 每个坐标以 finally 调用 finish_coordinate，runner 关闭未消费物化环境但保留完整结果和
  Run-owned 原始静态事实。后续 inspect 所保留环境将沿此收尾路径释放；目前尚未接入静态
  prepare，因此本片不声称证明 NO_HINT 环境复用或对数峰值。
- 公共算法测试证明无 history 的缓存边界不发起 oracle，以及 oracle 抛异常时坐标仍收尾。
  `uv run pytest tests/test_search.py tests/test_search_workflow.py tests/test_search_coordinator.py --no-testmon -q --tb=short`
  → **68 passed in 1.31s**；`uv run ty check` 通过。
- 下一步继续 freeze actual local anchor / inspect 的原始观测与比较、静态二分和 oracle
  suspect/clean 消费；阶段/窗口/端点记录必须同步闭合，不能让这里只读 fast path 代替 S4。

### 8.61 S4：局部静态二分与真实 oracle continuation（进行中）

- 新增 `static_guidance.py` 的纯静态点、hint、结果和 Slice observer；最低候选优先，之后仅
  midpoint 二分，anchor 为已有直接 PASS 的真实自比较。不可用立即 NO_HINT，无阶段内重试；
  读取同 anchor/固定坐标/guidance 的既有比较，矛盾只产生 static-inconsistent，不生成动态
  NON_MONOTONIC。算法不扫描全候选，不把静态 bracket 传入 oracle 动态窗口。
- CoordinateSearch 每坐标至多打开一次静态阶段；有效 suspect 优先于普通 hint。suspect PASS
  后向低定位；suspect REJECTED 后才选尚有意义的 clean 候选，clean 的真实拒绝才能推进
  动态下端。clean 为 anchor 时消费已知上端，候选域外 U 不创建候选请求。
- 产品 `_RunnerStaticSlice` 从当前 runner 的完整 PASS 找同 Run consumer/PASS，使用共享
  comparator 建立真实 anchor 与 local 结果。cache 新增只读 exact Proposal/PASS 查询、
  带 document 的共享比较返回和同 anchor 比较审计读取，不引入第二套减法或判断算法。
- `_inspect_static` 只收集/复用原始事实与准备环境，不运行 verifier，不登记动态终态。
  成功准备的中间点保留至 oracle 使用或坐标 finally；ty unavailable 不关闭干净环境。
  已有完整执行的静态关联只读消费，无关联则返回采集不可用，不补造新 anchor 进程。
- `tests/test_static_guidance.py`：110 个有限单调 floor × 静态边界组合（含错误方向和域外
  sentinel）、suspect/clean 选择、不可用中断、已知静态矛盾、冷查询上限与阶段顺序，
  **115 passed in 0.27s**。该矩阵是普通公开算法 seam 的受控状态，非原生 ty 性能结论。
- 搜索/cache 联合回归：
  `uv run pytest tests/test_static_guidance.py tests/test_search.py tests/test_search_coordinator.py tests/test_static_cache.py --no-testmon -q --tb=short`
  → **190 passed in 6.96s**。
- 真实 SearchCoordinator + EnvironmentFactory/StaticEvaluator/RuntimeEvaluator 联合用例：
  guidance 模式先 ty(1), ty(2)，再 verifier(2), verifier(1)；中间 ty unavailable 模式保留环境
  并以 verifier(1), verifier(2) 回退；lower-unchanged 不继续二分，直接普通 oracle。
  三者各仅 highest + 两个 exact preparation，所有环境最终关闭，scope 保存真实 local
  comparison 和两个完整 PASS。`uv run pytest tests/test_search_coordinator.py -k local_static_phase --no-testmon -q --tb=short`
  → **3 passed, 25 deselected in 0.23s**。最初测试把 fixture 下层操作名 exact-selection
  写成 exact-vector，校正后通过；未更改生产语义迎合测试。
- ty/ruff 已通过。完整回归正在 `/tmp/d038-local-guidance-full.txt` 运行，结果待填。
- **未闭合项**：目前 runtime 静态阶段结果、NO_HINT/prepare facts 保存在 runner 内的 typed
  记录，尚未接到 portable report/Journal；anchor 不可用/fast-path 的完整阶段记录、hint 的
  policy/window/端点 refs 与选择时序 reader 校验仍待接入。S4 不以算法 PASS 宣称完成，
  S5 的跨 scope intern、日志关联、policy/merge/apply 仍待实施。也尚无成本或 E008 证据。

- fast path 后续审计复现缓存 PASS 链已有直接边界，却消费一个 PASS 后仍打开 guidance
  （`cached_pass_chain`：1 failed in 0.09s）。现先只读查询并消费窗口内已有直接结果，统一
  做动态冲突/非单调检查，取最小已知 PASS 上端，再沿合法直接 predecessor/history 定界。
  这些是内存查询，不发起 prepare/ty/verifier；静态查询仍保持原对数上限。新增已缓存
  低 PASS/高 REJECTED 反例测试，防止提前定界隐藏实际反例。
- 最后 fast path 修改后的回归：
  `uv run pytest tests/test_static_guidance.py tests/test_search.py tests/test_search_coordinator.py --no-testmon -q --tb=short`
  → **177 passed in 1.60s**。其中包含完整有限单调矩阵、真实三路径与缓存边界/反例。
- 先前启动的全套命令终态：
  `uv run pytest tests --no-testmon -q --tb=short > /tmp/d038-local-guidance-full.txt 2>&1`
  → **2546 passed in 258.57s**，exit 0。该进程早于最后 fast path 修改与两项新增测试，
  最后修改由上条 177 项覆盖；不将全套结果描述为最终工作树所有新路径的单次完整验证。
- 类型与 ruff 复核通过。S4 核心算法和产品运行已接通，§8.61 列出的持久化/阶段选择/完整
  矩阵审计仍待继续。下一实施重点是把 runner 静态阶段及实际不可用 preparation 事实接到
  scope 审计，形成 report/Journal 可离线验证的 hint、NO_HINT 和 oracle selection 时序。

### 8.62 S4/S5：静态阶段进入 scope、报告与 Journal（进行中）

- 上轮为有实现与测试证据的实际进展。本轮从真实 runner 保留但未持久化的阶段记录继续，
  新增 `schemas/static_search.py`：静态查询点引用、真实采集/prepare 不可用 operation、hint
  端点、冻结 CandidateSnapshot、SLICE context、GuidancePolicy 与 SearchDerivationPolicy。
  actual small_threshold 由 CoordinateSearch 传入 runner 后进入派生策略，不依赖默认推断。
- `StaticScopeEvidence.searches` 与 Run cache 的 append-only search audit 保存完成阶段。
  此记录没有 lookup/hit 行为，不成为 hint cache。比较引用使用同 scope 内已登记的语义
  comparison identity；scope token/局部 ref 改名不改变比较内容，也不授权跨 scope 关联。
- scope reader 先复证所有原始比较，再将保存的点序列交给同一个静态二分算法重放，验证
  anchor 自比较、local context、window/artifact、候选邻接、查询顺序、hint 端点与 NO_HINT
  结果；无法闭合的引用或不符的查询/端点拒绝。普通动态 authority payload 保持独立。
- 未形成请求的静态采集失败保留实际 Attempt、已完成 Proposal 与内容错误；prepare failure
  保留实际 Attempt/operation/process 而没有 Proposal 或 comparison。复证 Proposal 的
  materialization digest、source snapshot/plan、execution identity 和准确向量。
  发现 PrepareFailure 隐含 runtime process sidecar 导致往返对象不一致后，将可移植失败
  facts 与独立 process 分开保存，不丢弃实际 process 证据。
- 真实 coordinator 覆盖 guided、ty unavailable、lower-unchanged、capture-unavailable、
  prepare-unavailable 五条路径；每条均经过 PackageReportBuilder/ReportStore、生成 schema
  校验、byte-stable read/write、RunLogStore Journal 写读，以及错误端点/原因的 reader 拒绝。
  `uv run pytest tests/test_search_coordinator.py -k local_static_phase --no-testmon -q --tb=line`
  → **5 passed, 25 deselected in 1.06s**，完整日志 `/tmp/d038-static-search-wire.txt`。
- schema/examples 已由 `uv run python scripts/generate_report_schema.py` 更新，--check 通过。
  初次已有回归进程读入旧 schema，导致两个新增 searches 字段拒绝（166 passed、2 failed）；
  schema 更新后 `uv run pytest tests/test_static_report.py --no-testmon -q --tb=line`
  → **11 passed in 12.91s**，`/tmp/d038-static-report-schema-refresh.txt`。另一次 Journal
  测试误把未创建的日志目录当项目根，修正为实际项目根后五路径通过。
- ty 检查通过。完整测试正在 `/tmp/d038-static-search-audit-full.txt` 运行，结果待填。
- **仍待闭合**：没有可用 anchor 的阶段缺席/fast-path 记录、完整 oracle 选择时序及其
  future-ref 校验、跨 scope raw intern、静态日志关联、完整 policy/merge/apply 消费与 CLI。
  已保存的 anchored 阶段 replay 不能证明这些尚缺路径。成本资格、E008 与 owner 吸收仍未完成。

- 后续增加静态 prepare process 与 operation terminal 一致性检查；capture-unavailable 不可
  携带无 owner 的虚构 process。五路径报告/Journal/篡改拒绝最终仍为 **5 passed in 1.06s**。
- 首次完整门禁结果 **1 failed, 2549 passed in 265.14s**。失败是新增嵌套 CandidateSnapshot
  暴露 SpaceSelection 的 Pydantic dataclass 未声明 extra-forbid，导致生成 schema 不严格。
  SpaceSelection 现显式配置 extra="forbid"，同步收紧 reader 与 schema；未改变候选 DSL。
  增加了非空审计中未知 selection 字段被拒绝的公开读取断言。
- 随后的严格 schema 测试误把 SearchConfig 的合法字段名 `default` 当成 JSON Schema
  `default` 注解。修正测试遍历：properties/$defs 等映射的键是字段/定义名，继续检查其
  子 schema 的注解，保留禁止隐含默认值规则。不是删除或放宽严格 schema 检查。
- `uv run pytest tests/test_search_coordinator.py tests/test_report_artifacts.py tests/test_search_space.py tests/test_search_space_report.py --no-testmon -q --tb=line`
  → **91 passed in 3.07s**，`/tmp/d038-strict-static-search.txt`。
- 最终完整门禁正在 `/tmp/d038-static-search-audit-final-full.txt` 重跑，结果待填；无提交或推送。

- 最终完整门禁完成：
  `uv run pytest tests --no-testmon -q --tb=line > /tmp/d038-static-search-audit-final-full.txt 2>&1`
  → **2550 passed in 262.27s**，exit 0。本条覆盖本轮最终生产代码、strict schema 与五条真实
  非空 report/Journal 路径；之后仅更新 Plan。最终 ruff/diff check 通过，未提交、推送或归档。
- 验收矩阵已按实际证据更新部分完成状态：核心算法、动态顺序、anchored 阶段持久化已有
  正例，但无 anchor/fast-path 阶段记录、oracle selection 全局时序、完整跨 scope intern/
  policy/日志/CLI 与 S7–S8 未完成。整体目标继续进行，不以本轮完整 pytest PASS 替代逐项验收。

### 8.63 S4/S5：无 admitted anchor 的阶段记录（进行中）

- 上轮已交付 anchored 阶段持久化并通过完整 2550 项回归，本轮为继续实现而非重启验证。
  新增 StaticPhaseOmission，记录 actual upper Attempt/Proposal、冻结候选窗口和
  anchor-unavailable 原因，不伪造 TyCheck、比较或完整 GuidancePolicy 的工具输入。
- 产品 open_static_slice 必须先找到真实完整 PASS 上端；没有已登记 static consumer/PASS
  anchor 时写入 omission，随后走普通 oracle。无真实动态上端是 owner 错误，不能静默
  当作静态不可用。Run cache 仅追加审计，不将 omission 作为静态 negative cache。
- scope 复证 Proposal identity、Run source snapshot/plan/execution context、候选窗口；
  保存当时已观察的 PASS 引用前缀，避免未来合法事实使先前记录失真。报告 reader 另外
  要求 omission 的 upper Proposal 对应实际动态 PASS；Journal 纳入该 Attempt 的 Run 输入
  校验。该前缀不替代尚未实现的全局选择事件时序证明。
- 真实最高版本 capture-unavailable 用例：ty 向量仅 1、2；verifier 向量 3、1、2；floor=2。
  scope 保存一个 omission、两个真实 raw facts 及一个 consumer PASS，无 fabricated anchor
  或静态比较。实际 ReportStore byte-stable 往返与 RunLogStore Journal 写读通过，所有
  准备环境关闭。与既有五条路径合并回归 **6 passed in 1.14s**。
- `uv run pytest tests/test_search_coordinator.py tests/test_static_report.py tests/test_static_journal.py --no-testmon -q --tb=line`
  → **55 passed in 55.17s**，`/tmp/d038-omission-focused.txt`。
- 最终输入-context guard 和非空 omission Journal 覆盖后：
  `uv run pytest tests/test_search_coordinator.py tests/test_report_artifacts.py --no-testmon -q --tb=line`
  → **36 passed in 2.58s**，`/tmp/d038-omission-runtime-final.txt`。
  ty、ruff、generator --check 和 diff check 通过；本轮未重新运行完整 pytest。
- 后续 selection 审计必须独立于动态 identity：保留普通/history/static-suspect/
  static-clean-neighbor 选择原因及已完成 search ref，分别记录选择与结果绑定；直接 cache
  lookup 保持只读，消费行为另行记录。同时补齐直接定界跳过阶段及 chronology/future-ref
  reader 闭环。当前 omissions 仅处理 anchor-unavailable，不宣称覆盖全部阶段缺席。
- 整体目标仍进行中；无提交、推送或归档。

### 8.64 S4/S5：oracle 选择时序与直接定界跳过（进行中）

- 上轮已交付无 admitted anchor 的 omission，本轮继续实现而非重启验证。oracle 选择在
  实际动态结果之后落盘：保留 mechanical/history/static-suspect/static-clean-neighbor
  原因、已完成 static search ref，以及 Attempt/Proposal/Failure 结果绑定。lookup 仍只读；
  只有 consume/evaluate 记选择。选择原因与静态 refs 不进入 Attempt/Evaluation identity，
  同一动态结果可被不同选择原因复用。
- 已能定界的 fast path 写入 `StaticPhaseSkip(direct-bound)`，引用真实 floor PASS 与
  predecessor rejection，不伪造 TyCheck、比较或 hint。普通算法 seam 无 recorder 时不
  持久化。scope/report/Journal 复证 Run 输入、PASS/search 前缀、结果绑定；future-ref
  与拉长 prefix 被拒绝。
- 真实 coordinator：guided 路径记录 suspect→已完成 search、后续 reuse 同一 Attempt；
  clean-neighbor 在 suspect REJECTED 后选择候选 clean；缺 highest anchor 只有机械/
  history 选择与空 search prefix 的 skip。五路径、omission、clean-neighbor 均经
  ReportStore byte-stable 与 Journal 往返。算法 PASS 链在打开静态阶段前记录 skip。
- `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=line tests/test_static_guidance.py tests/test_search.py tests/test_search_coordinator.py tests/test_static_cache.py tests/test_static_report.py tests/test_static_journal.py tests/test_report_artifacts.py tests/test_search_space_report.py`
  → **248 passed in 64.99s**。另 `tests/test_report.py tests/test_authorization.py tests/test_runlog.py`
  → **75 passed in 3.74s**。schema/examples 已由 `scripts/generate_report_schema.py` 更新，
  `--check`、ty、ruff、`git diff --check` 通过。本轮未重新运行完整 pytest。
- **仍待闭合**：跨 scope raw intern、完整 policy/merge/apply、Index 日志关联、CLI、
  跨坐标/sweep 失效、history miss 真重验成本与 S7–S8。已保存的 selection/skip 不能证明
  这些尚缺路径。diagnose execution/selection 见 §8.65。无提交、推送或归档。

### 8.65 S5：diagnose 的 execution / selection 关联（进行中）

- 上轮已交付 oracle 选择时序与直接定界跳过。本轮把 D038 §8.1 的两条辅助路径接到
  公开 diagnose：`Failure -> exact execution association -> TyCheck / StaticComparison`，
  以及 `Failure -> oracle probe selection -> StaticHint / StaticComparison -> TyCheck`。
  关联在命中来源内按规范顺序分别展示，不按同 Cell 或近时间猜测，不进入 Failure ID
  或动态 authority preimage。
- `diagnose_static_associations` 只读已保存 scope。execution 要求 `AttemptFailureScope`
  且 consumer 的 Attempt 与 Failure 一致，可带该 consumer 的 GLOBAL/SLICE 比较，不包括
  highest 自比较。selection 要求 `selection.failure_id` 一致，优先未复用记录，且必须有
  已完成 `static_search_ref` 与 hint；只挂被选中端点的比较。机械选择无 search ref，故
  无第二条路径。prepare 无 Proposal 时不补造 TyCheck。
- `DiagnoseCommandWorkflow`：报告命中只用 `report.static_scopes`，不读 Journal；Journal
  命中只用 journal scopes。未知 Failure ID 与静态 fact id 一样不可作 selector。终端在
  有关联时显示 “Related static evidence”，并写明这些事实解释探测路径，不是兼容性结论。
- 真实 clean-neighbor 路径：suspect REJECT 的 Failure 同时得到 execution 与
  `static-suspect` selection；SLICE regression 在场，highest 自比较不在 execution 中。
  删除 floor 报告后，从已有 `RunLogStore(run_id="guided")` Journal 再 diagnose，两条
  关联相同。报告命中时 `logs.journal_reads == []`。
- 删除 unused import 后重跑（含 diagnose 关联用例，排除无关的 56 列卡片宽度）：
  `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=line tests/test_static_guidance.py tests/test_search.py tests/test_search_coordinator.py tests/test_static_cache.py tests/test_static_report.py tests/test_static_journal.py tests/test_report_artifacts.py tests/test_search_space_report.py tests/test_report.py tests/test_authorization.py tests/test_runlog.py tests/test_diagnose.py -k 'not test_diagnose_card_preserves_fields_at_common_widths[56]'`
  → **356 passed, 1 deselected in 68.04s**。先前整组含 `test_diagnose.py` 时
  `test_diagnose_card_preserves_fields_at_common_widths[56]` 失败：该 indeterminate
  报告 `static_associations==()`，卡片边框仍按 80 列测量，不是本轮关联回归。80/120
  宽度通过。未改 diagnose 卡片布局。
- `PATH=/home/llh/pf/.venv/bin:$PATH .venv/bin/ruff check src tests scripts && .venv/bin/ty check && .venv/bin/python scripts/generate_report_schema.py --check && git diff --check`
  全部通过。未重新运行完整 pytest。无提交、推送或归档。
- **仍待闭合**：完整 policy/merge/apply、Diagnosis Index 原 producer/log typed
  association、prepare 无 Proposal 的 diagnose 正例、缺日志展示，以及 S6–S8。
  跨 scope intern 见 §8.66。

### 8.66 S5：跨 scope raw / comparison intern（进行中）

- 上轮已交付 diagnose execution/selection。本轮按 D038 §8.1 把原始 TyCheck/
  TyCheckUnavailable 与 GLOBAL/SLICE 比较按语义 identity intern 到文档级 table；
  scope 只保留 membership、producer/process 与 consumer association。同内容 raw
  fact 可共享 intern 条目，不得合并独立 Run/Cell membership，不得复制 process 或
  重写原始事实。比较两端与 anchor 仍须在同一 scope 闭合；局部 ref 不进入语义
  identity。
- 运行时 `StaticScopeEvidence` / cache snapshot 仍自包含，便于诊断与算法。报告
  wire 与 Journal v3 只接受 intern 形状：`static_facts`、`static_comparisons` 加
  `StaticScopeWire` 引用。reader 复证 intern identity、拒绝悬空/未使用 intern
  条目、拒绝嵌入旧 observation、拒绝跨 scope compare。merge 两个相同报告 intern
  一份事实，保留两个 scope。
- 真实报告 byte-stable 往返与 Journal 写读保持；schema/examples 已由
  `scripts/generate_report_schema.py` 更新。公开负例覆盖 dangling identity、
  unused intern、identity 失配、Journal 嵌入旧 fact。
- `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_report_artifacts.py tests/test_static_report.py tests/test_static_journal.py tests/test_static_request.py`
  → **49 passed in 135.66s**。
- `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=line tests/test_static_guidance.py tests/test_search.py tests/test_search_coordinator.py tests/test_static_cache.py tests/test_report.py tests/test_authorization.py tests/test_runlog.py tests/test_search_space_report.py tests/test_diagnose.py -k 'not test_diagnose_card_preserves_fields_at_common_widths[56]'`
  → **326 passed, 1 deselected in 16.12s**。
- `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=line tests/test_check.py tests/test_evaluation.py tests/test_runtime_static_scope.py tests/test_verification.py tests/test_static_cache.py`
  → **112 passed in 9.99s**。
- `PATH=/home/llh/pf/.venv/bin:$PATH .venv/bin/ty check && .venv/bin/python scripts/generate_report_schema.py --check && git diff --check && .venv/bin/ruff check src tests scripts`
  全部通过。未重新运行完整 pytest。无提交、推送或归档。
- **仍待闭合**：完整 policy/merge/apply 的 `execution-policy-mismatch` 与
  `search-provenance-mismatch`、Diagnosis Index 原 producer/log、以及 S6–S8。

### 8.67 S5–S8 续：报告侧 Index、资格脚本、owner 吸收与 MkDocs check（进行中）

- 上轮已交付跨 scope intern。本轮继续实现，不重启验证。`RunLogStore.index_report_static`
  在 Journal 反序列化后按 `journal-static:{run_id}:{scope_ref}` 复制已写入 locator，不再依赖
  live `id(process)`。`SearchCommandWorkflow` 写报告后改调该入口。
  `tests/test_static_journal.py::TestStaticJournal::test_report_side_index_resolves_typed_producer_logs`
  用 `lookup_report_static` 相等与 `read_tail` 断言，不用相对 cwd 的 `path.is_file()`。
- `TERM=dumb` 时 Rich Live 不写缓冲。仅在 `tests/test_terminal.py` 的 Progress/Explain/
  Verification/Search 渲染夹具设 `TERM=xterm-256color`，不全局改 TERM。
- 新增 `scripts/qualify_static_guidance.py`、`scripts/measure_d038_guidance.py`、
  `tests/test_static_guidance_qualification.py`。机械对照只包装 `open_static_slice`，并显式
  转发 Protocol 方法。受控 measure：`all_floors_match: true`。
- 隔离副本 `/tmp/pf-d038-e009-mkdocs`，源 `/home/llh/pf/experiments/mkdocs`，上游 HEAD
  `2862536793b3c67d9d83c33e0dd6d50a791928f8`，`pyproject.toml` dirty 仅为 PF 实验配置。
  Check：`CheckPass`；Python 3.8–3.12 五 cell 均 `declaration` + `entered_verifier` +
  `NormalExit(0)`；3.10–3.12 声明向量含 Markdown **3.3.6**，argv 为原完整 unittest，各 Ran 725、
  exit 0。run_id `20260908T013259.640291Z-1649178-5c2d0cfa`。S_hi 为 UNAVAILABLE
  （`invalid-layout` / `static-subject-unavailable`），Journal facts=[] 但仍有
  `highest_uncollected`。这满足 AC12：ty 不可用仍进入原完整 unittest。
- Search 于 2026-09-08T01:35:34Z 启动，输出待 `/tmp/pf-d038-e009-search.json`；未结束前
  AC13 不写通过。
- 现行 owner 已吸收稳定规则（D001–D008、D012、D014、D037、README/CONTEXT、docs/README）。
  D003/D004 文首仍链到同目录 D038，**未归档**。新增当前契约负例
  `test_reader_rejects_retired_static_authority_fields`：注入 `regions` / `witnesses` /
  `RUNTIME_INTERFACE_MISSING` 均被 ReportStore 拒绝，**3 passed in 7.52s**；不保留历史
  报告 fixture。
- 公开回归：
  `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=line tests/test_static_cache.py tests/test_static_lifecycle.py tests/test_static_ownership.py tests/test_static_guidance.py tests/test_search.py tests/test_authorization.py tests/test_static_journal.py tests/test_static_guidance_qualification.py tests/test_evaluation.py tests/test_check.py`
  → **331 passed in 59.52s**。
- `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=line tests/test_terminal.py tests/test_diagnose.py tests/test_static_report.py tests/test_report.py tests/test_runlog.py -k 'not test_diagnose_card_preserves_fields_at_common_widths[56]'`
  → **203 passed, 1 deselected in 16.46s**。
- E009 已建：`docs/experiments/E009-mkdocs-static-guidance.md` 与
  `docs/experiments/data/E009/{isolate,check,measurement}.json`。check 的
  source snapshot 与 E008 相同；Journal 五 scope 的 `highest_uncollected` 为
  `static-subject-unavailable`（3.10=`invalid-layout`，其余 `unclosed-symlink`）。
- **仍待闭合**：真实 MkDocs search 与 E009 search 冻结、E8 三解释器门禁、逐 AC 最终审计、
  D038/P042 归档。无提交、推送。

### 8.68 S5：文档级 content / subject intern（进行中）

- 上轮 E009 check 已证明 AC12。产品 search（run-id
  `20260908T013538.533995Z-1654191-fdfd05bd`）五 Cell 均为 SUCCESS，
  `result.status=complete`，但资格脚本在 `ReportStore.read` 处失败：
  `ConfigurationError(reason=unsupported-report-contract, report exceeds the 64 MiB read limit)`。
  报告 `/tmp/pf-d038-e009-package-floor-pre-intern.json` **128 MiB**（原
  `/tmp/pf-d038-e009-mkdocs/package-floor.json`）。search.json 未写出。失败保持失败，
  不手改报告，不提高 D014 64 MiB 上限，无兼容 reader。
- 体积：`static_facts` ≈62 MiB（40 条均嵌入完整 `observation.subject`），
  `static_scopes.consumers[].preparation.subject` 再复制同一预像 ≈77 MiB。
  40 个 unique StaticSubject；target/config content 各 1 份却被复制 40 次。
- 按 D038 §4.4 / §8.1：内容 manifest 用 content identity intern；消费关联只保存
  `(consumer ref, fact_ref, subject_identity)`，preparation 不再嵌入 StaticSubject。
  新增 `static_contents`、`static_subjects`；`InternedStaticFact` 改为
  `identity + subject_identity + observation_policy + fact`。旧 wire 含
  `observation.subject` 被确定性拒绝。离线分析（非产品转换）估算 intern 后约 **54.5 MiB**
  compact JSON，低于 64 MiB。
- schema/examples 由 `scripts/generate_report_schema.py` 更新。
  `assert_interned_static_audit` 断言 facts 无完整 subject、consumers.preparation 无
  `subject`、contents/subjects 按 identity 排序。
- intern 相关测试：
  `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_static_report.py tests/test_static_journal.py tests/test_report_artifacts.py tests/test_static_request.py`
  → **42 passed, 11 deselected in 161.10s**（当时尚未加 unused-content/dangling-subject 参数）。
- 身份/cache/guidance：
  `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=line tests/test_policy.py tests/test_failure.py tests/test_authorization.py tests/test_static_cache.py tests/test_static_guidance.py tests/test_search.py tests/test_static_lifecycle.py tests/test_static_ownership.py tests/test_static_subject.py`
  → **357 passed in 11.32s**。
- `.venv/bin/ty check` 通过（顺带修正 `tests/test_search.py` 的 `list[object]` 下标与
  `tests/test_static_guidance.py` 的方法赋值/union 收窄，行为不变）。
  `generate_report_schema.py --check` 通过。
- 旧 128 MiB 报告已移出隔离树，避免 search 开局再读。正在重跑：
  `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_static_guidance.py --mode search --root /tmp/pf-d038-e009-mkdocs --output /tmp/pf-d038-e009-search.json`
  新 run-id `20260908T022518.098497Z-1722063-e8888b4a`。结束前 AC13 不写通过。
- D014/D008 已吸收 intern 表名：`static_contents` / `static_subjects` / `static_facts` /
  `static_comparisons` / `static_scopes`。D038/P042 **未归档**。无提交、推送。

### 8.69 S6/S7/E8：intern 后 search 闭合与 Live 捕获修复（进行中）

- intern 后 search 已结束，资格脚本 exit 0。有效 run-id
  `20260908T022518.098497Z-1722063-e8888b4a`；报告
  `/tmp/pf-d038-e009-mkdocs/package-floor.json` **57,004,655 B**，低于 D014 64 MiB；
  SHA-256 `5af3ca77d84996c248b95aaeacd97e8aa075e64b0270e320f02942c8c99f0ea2`；
  generation `a5c47dc73443ee5b890c4fe49e26baabe3e6bb3db252722b4413a882af8fc9c2`。
  intern：contents 82、subjects 40、facts 40、comparisons 59、scopes 5。五 Cell SUCCESS，
  Markdown floor 3.3.7 / predecessor 3.2.2 / `authority.kind=configured-verifier`。
  `report_roundtrip: true`。冻结：`docs/experiments/E009-mkdocs-static-guidance.md` 与
  `docs/experiments/data/E009/{isolate,check,measurement,search,search-summary}.json`。
  128 MiB 旧 intern 仍被 reader 以 `unsupported-report-contract` 拒绝，不是 AC13 证据。
- 首轮 E8（`/tmp/pf-d038-gates-9A3XYb`，TERM=dumb）**未通过**：py310 15 failed / coverage
  89.90%；py311/py312 同 15 failed，另 4 个 `test_execution_qualification` setup ERROR
  （`qualify_execution_failures.py` 对 final GLOBAL comparison `StopIteration`）。build/ruff/ty/schema/diff-check 通过。
- 根因不是缩小卡片或降低 coverage。Rich 14 在 `TERM=dumb` 下 `Console(width=56)` 的
  `size` 回落到 80，除非同时给 `height`。Live 只应在真实 TTY（`isatty` 且非 dumb）
  `start()`；非 TTY/`TERM=dumb` 捕获必须把结果卡立即打到 Console 文件，不能等
  `close()` 或写入未启动的 Progress 缓冲。`print_step` / setup persist 在 Live 未启动时
  走 `_stderr.print`。
- 测试：`TestResultCardWidths` 与 diagnose 一样给 `height=25`；smoke live 用例设
  `TERM=xterm-256color`。intern 公开负例补 unused-subject / dangling-content /
  consumer-subject-mismatch / unused-comparison，以及 `resolve_static_scopes` 的 unique/sorted
  与 missing content/subject/consumer 路径。
- 定向回归（TERM=dumb）：
  `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_run.py::TestExecutionFailureRun::test_run_persists_diagnosable_execution_failure tests/test_smoke.py::TestSmokeWorkflow::test_smoke_workflow_emits_live_baseline_identity_before_verification tests/test_cli.py::TestResultCardWidths tests/test_terminal.py::TestProgressRendering tests/test_static_report.py::TestStaticReport::test_reader_rejects_broken_static_intern`
  → **84 passed in 8.75s**。
  `tests/test_static_request.py::TestNonemptyStaticPreparation tests/test_terminal.py::TestVerificationRendering tests/test_terminal.py::TestSearchRendering tests/test_smoke.py`
  → **53 passed in 35.64s**。`ty check` 与 `ruff check` 针对改动文件通过。
- 正在重跑 §4.2：隔离目录 `/tmp/pf-d038-gates-MGeUmh`。结束前 E8/AC14/S8 不写通过，不归档。
  无提交、推送。
- 第二轮 py310：`1 failed, 2600 passed`，coverage **89.999%**（显示 90.00% 仍低于 fail_under 90.0）。
  失败为 `test_render_error_colors_the_setup_border_as_failure`：`TERM=dumb` 时 TTYBuffer
  仍 `is_terminal`，但 Live 未 start，`_flush_setup_card` 若按 live-only 立即打印，
  则失败色边框的 persist 没有 setup 卡。已改回：is_terminal 的 setup 卡仍进入 persist，
  Live 未启动时用 `_stderr.print` 写出。`TestErrorRendering` 在 TERM=dumb 下已绿。
  intern identity 失配负例补 `InternedStaticContent`/`InternedStaticFact` 的 ValidationError。
  第三轮 E8 待跑。未归档。
- 第三轮 py310：2601 passed，coverage 显示 90.00% / 实际 89.999%，pytest exit 0。py311：2 failed
  journal + 4 qualification ERROR；py312：4 qualification ERROR，journal 绿。
  qualification 在 3.11/3.12 cell 上 S_hi 为 `static-subject-unavailable`/`unclosed-symlink`，
  无 GLOBAL comparison；search 仍 SUCCESS、floor=2、两次完整 verifier。这是 D038 允许的
  静态不可用，不能当成动态失败。脚本与 `assert_case` 在 subject 不可用时记录
  `global_comparison=None` + `static_unavailable_detail`，仍强制 execution REJECTED 与
  完整 PASS。subject 可用时（3.10）保持 bytecode GLOBAL 对照。
  `tests/test_execution_qualification.py tests/test_static_journal.py` 在 py311 上
  **23 passed in 42.52s**。第四轮 E8 待跑。未归档。

### 8.70 S8：第四轮 E8 通过、owner 吸收与归档

- 第四轮 E8 隔离目录 `/tmp/pf-d038-gates-RwBFnd`。cwd `/home/llh/pf`。uv 0.12.5；
  解释器 3.10.16 / 3.11.15 / 3.12.3。不覆盖仓库 `.venv`。

```sh
pf_d038_gates=/tmp/pf-d038-gates-RwBFnd
UV_PROJECT_ENVIRONMENT="$pf_d038_gates/py310" UV_CACHE_DIR=/tmp/pf-uv-cache \
  .venv/bin/uv run --frozen --python 3.10 --group test pytest --no-testmon \
  --cov=pf --cov-branch --cov-report=term-missing \
  --cov-report=json:"$pf_d038_gates/coverage.json" \
  --junitxml="$pf_d038_gates/py310.xml" -q --tb=short tests
# 2601 passed；Required test coverage of 90.0% reached. Total coverage: 90.01%
# coverage.json totals.percent_covered=90.00797589507268；PY310_EXIT:0
# junit py310.xml tests=2601 failures=0 errors=0 skipped=0 time=457.8s

UV_PROJECT_ENVIRONMENT="$pf_d038_gates/py311" UV_CACHE_DIR=/tmp/pf-uv-cache \
  .venv/bin/uv run --frozen --python 3.11 --group test pytest --no-testmon \
  --junitxml="$pf_d038_gates/py311.xml" -q --tb=short tests
# 2601 passed；PY311_EXIT:0；junit tests=2601 failures=0 errors=0 skipped=0 time=240.349s

UV_PROJECT_ENVIRONMENT="$pf_d038_gates/py312" UV_CACHE_DIR=/tmp/pf-uv-cache \
  .venv/bin/uv run --frozen --python 3.12 --group test pytest --no-testmon \
  --junitxml="$pf_d038_gates/py312.xml" -q --tb=short tests
# 2601 passed；PY312_EXIT:0；junit tests=2601 failures=0 errors=0 skipped=0 time=293.614s

UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build --out-dir "$pf_d038_gates/dist"
# BUILD_EXIT:0；dist/package_floor-0.2.0-py3-none-any.whl 与 .tar.gz
```

- 前三轮（`9A3XYb` / `MGeUmh` / `GjUzyt`）保持失败记录，不是 E8 通过证据。
- S8 owner 吸收：D001–D008、D012、D014、D037、D006 与 CONTEXT/README 已写入两阶段搜索、
  无 witness、六组投影、三类 policy、Journal v3、intern 表与 diagnose Failure 关联。
  D002 核心 interface 从过期的 `StaticEvaluator.capture/evaluate` 改为
  `lookup/collect/compare`，与生产 `src/pf/evaluation.py` 一致。
- 逐 AC 审计见 §3 矩阵：AC1–AC26 均为证明。E0–E8 均为通过。
- 同变更归档：`docs/designs/D038-pf-static-guidance-authority.md` →
  `docs/archived/designs/`；`docs/plans/P042-pf-static-guidance-authority.md` →
  `docs/archived/plans/`。现行入链改为归档路径。E008 附件未改写。
  `tests/execution_qualification/README.md` 改为指向现行 D038 清单
  `2026-09-07-d038-uv-0.12.5-v1.json`；`2026-09-06-uv-0.12.5-v1.json` 仅作 D036 历史证据。
  无提交、推送。

### 8.71 S8 owner 吸收残留：独立核验缺口与文档修复

- 独立核验判定 D038/P042 **未完成**：实现与 E8/E009 大体成立，但 AC14/S8 矛盾——§8.70
  已归档并写「owner 已吸收」，而当时现行 owner 仍主张与目标契约/生产代码不一致的规则。
- 缺口（仅文档）：D014 `failure_policy = failure-execution-v3` 且 identity 仍把 ty
  args/timeout/tool version 写入统一 `pf:policy:v1` evaluation preimage；D005 文首
  `failure-execution-v3`、Attempt 仍绑 evaluation policy；D002 仍列 `RuntimeWitnessAdapter`
  生产路径，测试节仍写 `StaticEvaluator.capture/evaluate`；D001 把 `ty-timeout` 与
  resolve/test timeout 同等写入统一 evaluation policy；D012 仍以 evaluation-policy
  preimage 描述 generation/apply 与 Attempt 身份；D004/D013 残留「外层 evaluation-policy」
  与 `RuntimeWitness` 生产操作。
- 本轮只改文档，不改契约、不重跑三解释器。稳定规则写入现行 owner：D014 identity 改为
  execution / guidance / search 三类，`failure_policy = failure-execution-v4`，ty 采集只进
  Guidance/TyObservation；D005 策略版本与 Attempt 对齐 ExecutionPolicy；D002 删除 witness
  adapter 并改测试缝为 `lookup/collect/compare`；D001/D012/D004/D013 指向现行三类字段。
  归档 D038/P042 只补本条完成记录，不改写 §8.70 及更早「实施中」正文。
- 对照：`src/pf/failure.py` `FailurePolicy.identity = failure-execution-v4`；
  `src/pf/schemas/report.py` / `docs/examples/package-floor-v1-minimal-complete.json` 的
  identity 三类字段与 v4；`src/pf/schemas/policy.py` 的 ExecutionPolicy / GuidancePolicy /
  TyObservationPolicy / SearchDerivationPolicy。
- 门禁（cwd `/home/llh/pf`，沙箱外）：`ruff check` exit 0（All checks passed）；
  `.venv/bin/ty check` exit 0（All checks passed）；
  `.venv/bin/python scripts/generate_report_schema.py --check` exit 0。未重跑 E8。
- 无提交、推送。

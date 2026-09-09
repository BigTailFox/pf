# P046 — D039 静态评价深模块实施计划

- **状态：** 已完成
- **日期：** 2026-09-10
- **对应 Design：** [D039](../designs/D039-pf-static-evaluation-module.md)
- **目标 owner：** [D002](../../designs/D002-pf-implementation.md)、[D004](../../designs/D004-pf-ty-enhancement.md)、[D008](../../designs/D008-pf-verification-run.md)；指针 [D003](../../designs/D003-pf-search-algorithm.md)；[D005](../../designs/D005-pf-failure-and-diagnose.md) 只核对
- **起点：** `09b742cdd99b4485aa0e7f1236a8b3bde3264795`
- **流程与测试：** [AGENTS.md](../../../AGENTS.md)、[测试说明](../../../tests/README.md)
- **来源：** [R011](../../reviews/R011-pf-architecture-review.md) §3–§6

本 Plan 记录切片、决定、去向与验收证据；目标行为只由 D039 规定。不另立契约。未开切片时不改 `src/` 或 `tests/`。

## 1. 切片与验收映射

依赖：**S1 → S2 → S3 → S4**。D039 §4.3：先让算法离开 schemas，再收产品 import。S3 是一次不可拆分合并的产品 interface cutover；禁止为中间态保留 `StaticRequestFactory`、`failures=`、`RuntimeEvaluator(..., run_cache=)` 或 `prepared.static_consumer` 兼容层。

| 顺序 | 接口、实现、文档与测试工作 | D039 AC | 完成标准 | 证据槽 |
| --- | --- | --- | --- | --- |
| S1 | 锁定 §4 去向表；新增 `tests/test_identity_golden.py` 三组字面 hex（对现行 `pf.resolution` 函数，用真实 keyword） | AC7 表、AC10 预像 | 下表三个 nodeid 对现行函数成立 | E1 |
| S2 | schema 纯化；digest / `ResolutionPlanEvidence` 下放；`StaticAuditDocument` 含 `scope_ref` / `run_identity` / `preparations[]`；`pass.preparation_ref` 必填、`consumer_ref` 可空；`_admit_saved_static_audit`；`admitted_membership` 在 Cell 完成时调用；AC4 扫描；共享 derive/hint 的 S2 结构检查 | AC4、AC10、AC5 前置 | schemas 允许表成立；未绑定 PASS 无 preparation / 错 Run·Cell 时 fail closed；`_admit` 已委托即将共用的唯一 derive/hint；golden 搬家后不变。**不**要求 `compare_global` / `StaticSlice` 已存在 | E2 |
| S3 | 产品表面一次切齐：`pf.static`、公开五方法、Preparation registry + Direct-PASS ledger、collector、RuntimeEvaluator 去 cache、删除 `failures=` / RequestFactory；按 §4 改写测试；AC1/AC5/AC12/AC14 扫描 | AC1–AC3、AC5、AC7–AC9、AC11–AC14 | 三入口委托同一 derive/hint；import graph 与公开语义成立 | E3 |
| S4 | 吸收 D002/D004/D008；**改写** D003 §5（`record_runtime`、Direct-PASS runtime owner、`open_static_slice` 只转交 collector + snapshot，不是只改 StaticSlice 指针）；核对 D005；更新 CONTEXT / 索引 / R011；按 §5 逐 AC 回填命令与结果；同变更归档 | AC6、AC7 owner | owner 与代码一致；D039/P046 离开现行目录 | E4 |

每片结束跑该片命令。禁止把「换了路径的同一函数仍被 schema validator 调用」当作 S2 完成。S2 未证明未绑定 PASS 的 preparation/Run 闭合之前，不得开 S3。

## 2. 接口与所有权迁移

| 现行缝 | 目标 | 吸收后 owner |
| --- | --- | --- |
| `StaticEvaluator.lookup/collect/compare`；`evaluation.py` 平行出口 | `collect_prepared` / `capture_highest` / `compare_global` / `record_runtime` / `open_slice`；`pf.static` 产品出口 | D002 / D004 |
| `RunStaticPassRef.consumer` 必填；`StaticPassMembership.consumer_ref` 必填 | Preparation registry（prepared 对象身份）+ Direct-PASS ledger（Proposal 主键、一个 runtime owner）；snapshot `preparation_ref` 必填、`consumer_ref: str \| None` | D004 |
| `RuntimeEvaluator.evaluate(..., run_cache=)` 对 foreign cache 静默不登记 | `record_runtime` 按 D039 §3.2：先身份校验，再按 outcome 记账 | D004 |
| `TyCheckCache.snapshot` + `static_membership_from_scope` | `admitted_membership(cell)`；admit 失败 → `InfrastructureError` | D008 |
| Check/Highest `set_highest*`；`compare_global(..., guidance=)` | `capture_highest`；`compare_global(collected, *, run_cache)` | D004 |
| `_RunnerStaticSlice`；D003 §5 `record_pass` | `StaticSliceCollector` + `open_slice`；调用方 `record_runtime`；later collect 只绑 consumer | D003 / D004 |
| schemas validator 重放 | `_admit_saved_static_audit` | D002 / D004 |
| 三个 digest 与 `ResolutionPlanEvidence` 住在 `pf.resolution` | schema 纯层；字节不变；`tests/test_resolution.py` 随下放改 import | D002 / D014 |
| `failures=` | 内部构造 | D002；D005 不改 |
| v1 `StaticContentCollector` | 删除 | D004 |

`TyCheckCache` 生产只由 `VerificationRunner` 构造。公开方法：构造、`stop`、`documents`、`admitted_membership`、`close`。产品测试可构造 cache 以扮演 Runner，不得调用领域方法。

调用时序：`collect_prepared`/`capture_highest` →（若有非 highest handle）`compare_global` → `evaluate` → `record_runtime` → `close`。Search later collect 可在已有 runtime owner 之后再 `collect_prepared` 另一个 prepared。

## 3. 决策（实施前锁定）

1. **唯一目标契约。** 实施期间只实现 D039。owner 正文在 S4 吸收。禁止兼容层与测试专用公开导出。
2. **物理布局。** `src/pf/static/` 包。`__init__.py` 只导出 §3.1/§3.4 名字。`evaluation.py` 不 re-export `StaticEvaluator`。
3. **identity 纯层。** 三个 digest 与被静态 records 引用的 `ResolutionPlanEvidence` 下放到 `pf.schemas`。不下放整个 resolution 协议。禁止第二套算法。
4. **Golden vectors。** S1 用下表真实 keyword 与字面量；S2 搬家后同一字面量。禁止测试里重算哈希。
5. **`compare_global` 不接收 `guidance_policy`。**
6. **双登记。** Preparation registry 按 prepared 对象身份，允许同一 Proposal 多个 registered prepared。Direct-PASS ledger 每 Proposal 一个 runtime owner。later collect 只绑定 consumer。绑定规则与错误契约以 D039 §3.2/§3.3 为准，不在本 Plan 另写一份。
7. **S2 / S3。** S2 不建立产品 `compare_global` / `StaticSlice`。AC5 三入口统一在 S3。S2 的内部 `_admit` 必须调用**即将**被三入口委托的同一 derive/hint 函数；E2 用独立 nodeid 证明这一点，避免 S3 才发现第二套实现。S2 必须能构造并拒绝「未绑定 PASS 无 `preparation_ref` / 错 `scope_ref`」的 document。
8. **测试车道。** 结构扫描与公开缝走未标记。真实 ty/inspect 标 `process`。不新增产品 e2e，不扩 `qualification`。
9. **结构证据。** 扫描测试固定为 `tests/test_static_module_graph.py`（S2 落地 AC4 与 `test_admit_uses_shared_derive_and_hint`；S3 补 AC1/AC5 三入口/`AC12`/`AC14`）。延迟 import / re-export 失败。
10. **v1 文件树残留。** S3 删除，不降为内部测试。
11. **`scripted_static.py` 删除顺序。** 先把 `evaluation_fixtures.py` 改为 `StaticEvaluator(ty, processes=...)` + scripted `TyOperations`；`test_evaluation.py` 改 `capture_highest`；各测试去掉 `ScriptedStaticRequests` / `collect_highest`。最后删除 `scripted_static.py`。`static_fixtures.scripted_static_request` 改为内部 request 夹具或删除。
12. **失败先记。** 写入 §7。
13. **归档同变更。** E1–E4 齐全后归档 D039/P046。
14. **D003 §5。** S4 改写 `record_pass` → 调用方 `record_runtime`、Direct-PASS runtime owner、`open_static_slice` 委托；不是只把 StaticSlice 提供方指针从 SearchCoordinator 改到 Evaluator。
15. **E4 回填。** §5 十四行必须写成「证明：确切命令 → nodeid → 结果摘要」。全量 pytest 只作回归网，不能代替逐 AC 证据。

### S1 锁定的 golden preimage

`tests/test_identity_golden.py` 必须按下列**函数 keyword** 构造，不得转写成 `package` / `snapshot` / `baseline` / `context` / `project_plan` / `source_plan`。

共享 graph：

```python
graph = (
    ResolvedNode(name="demo", version="1.0", dependencies=("idna",)),
    ResolvedNode(name="idna", version="3.10", dependencies=()),
)
```

| nodeid | 调用 | 字面结果 |
| --- | --- | --- |
| `TestIdentityGolden::test_resolution_graph_id` | `resolution_graph_id(graph=graph)` | `resolution-f544a6e8d7807d357b5ce869b80e44f94f880dd76093a09302bbbde395cc8b2d` |
| `TestIdentityGolden::test_environment_identity_digest` | `environment_identity_digest(attempt_id="a" * 64, project_plan_digest="b" * 64, environment_plan_digest="c" * 64, graph=graph)` | `ce0ead5a082509dd2dff56d9f66bd9084c92628270c4a1b360dddde1844558c5` |
| `TestIdentityGolden::test_resolution_request_digest` | 见下块 | `3d1fb2ee2675c345c778311efdc04230af28846699626e6e78d9e3ad2487e09e` |

```python
resolution_request_digest(
    kind="project",
    package_name="demo",
    snapshot_digest="d" * 64,
    cell=Cell(
        package="demo",
        target="x86_64-unknown-linux-gnu",
        python_minor="3.10",
        extra_surface=(),
    ),
    resolution_kind="highest",
    selection=None,
    baseline_digest=None,
    context_digest="e" * 64,
    project_plan_digest=None,
    harness=(),
    source_plan_identity="f" * 64,
)
```

S1 对现行 `pf.resolution` 进口这些函数。S2 只改进口路径。

## 4. 测试去向表（S1 锁定；AC7 按表核对）

去向：`公开` = D039 §3.1/§3.4；`内部` = module 内部，不进 D002 §11；`删除` = replace-don't-layer。

| 文件 | 去向 |
| --- | --- |
| `tests/test_static_cache.py` | **内部**：同 key ty、negative cache、stop/cancel。删除产品向 cache 领域方法断言 |
| `tests/test_static_comparison.py` | **公开**：`compare_global` / 真实 slice。**内部**：`_admit` 坏 closure（含未绑定 PASS 无 `preparation_ref` / 错 `scope_ref`） |
| `tests/test_static_configuration.py` | **公开**：未闭合配置不启动 ty。已覆盖细节 **删除** |
| `tests/test_static_guidance.py` | **公开**：`CoordinateSearch` + `locate_static_hint` |
| `tests/test_static_guidance_qualification.py` | **公开**：真实 Search slice |
| `tests/test_static_inputs.py` | **公开/process**：inspect 不读 `distribution.files` |
| `tests/test_static_journal.py` | **公开**：`admitted_membership` + persist；删除 RequestFactory。保留 `test_ordinary_journal_read_does_not_open_ty_cache`；S3 新增 `test_ordinary_ty_cache_decode_does_not_replay_comparison_or_hint` |
| `tests/test_static_lifecycle.py` | **公开**：collect / reprepare / close |
| `tests/test_static_ownership.py` | **公开**：collect 后仍跑 verifier |
| `tests/test_static_paths.py` | **公开**：未闭合不启动 ty；已覆盖布局 **删除** |
| `tests/test_static_report.py` | **公开**：报告无 intern |
| `tests/test_static_request.py` | **公开**：连续 collect 证明 hit。RequestFactory 形状 **删除**。`record_pass` 幂等断言改 `record_runtime` |
| `tests/test_static_subject.py` | **公开/schema**：v2 identity。`TestStaticContentCollector` **删除** |
| `tests/test_runtime_static_scope.py` | **公开**：AC13。**删除** `test_static_availability_or_foreign_ref_does_not_change_pass`。替换见 §5.1 AC13 |
| `tests/scripted_static.py` | 按决定 11 **最后删除** |
| `tests/static_fixtures.py` | **公开**：`EnvironmentFactory.prepare`。删除 factory.capture |
| `tests/test_evaluation.py` | **公开**：改 `capture_highest` / `record_runtime`；foreign `other_cache` 改为期望 `ValueError` |
| `tests/test_ty_adapter.py` | **公开**：只经 `StaticTyRequest` |
| `tests/conftest.py` | **公开**：`run_cache` fixture 保留（扮演 Runner）。`scripted_static_request` 随 `static_fixtures` 改或删 |
| `tests/test_baseline.py` | **公开**：Highest + `capture_highest`/`record_runtime`。停止读 `run_cache.snapshot` 私有表；改公开 outcome / Journal membership |
| `tests/test_verification.py` | **公开**：Runner 构造/`stop`/`admitted_membership`/`close`。不调 cache 领域方法 |
| `tests/test_search_workflow.py` | **公开**：只转交 `TyCheckCache`；断言公开 Search/workflow outcome |
| `tests/test_smoke.py` | **公开**：同上，走 Highest/Smoke |
| `tests/test_check.py` | **公开**：切齐五方法；去掉 `ScriptedStaticRequests` |
| `tests/test_search.py` / `tests/test_search_coordinator.py` | **公开**：collector + 真实 slice；`open_slice` context 来源按 D039 §3.2；仅有内存 `PassEvaluation`、未 `record_runtime` 不得打开 slice |
| `tests/evaluation_fixtures.py` | 决定 11：去掉 RequestFactory 注入 |
| `tests/test_cli.py` | **公开**：composition。S3 新增 `TestDefaultContext::test_production_composition_shares_one_static_evaluator_and_process_runner` |
| `tests/test_identity_golden.py` | S1 **新增**；S2 只改进口 |
| `tests/test_static_module_graph.py` | S2/S3 **新增** 结构扫描 |
| `tests/test_resolution.py` | **公开/schema**：S2 随 `ResolutionPlanEvidence` 下放改 import；语义不改 |
| `tests/test_policy.py` | **公开/schema**：policy identity 与 observation/cache 分离。S2 后 `schemas.policy` 不再执行 `validate_ty_args`（AC4）；args 资格化在 `ConfigLoader` / 静态 request 装配。本文件继续经 `pf.policy` 工厂与已资格化记录断言，不把 schema validator 当 ty-args 执行入口 |

漏列或保留测试专用公开导出视为 AC7 未完成。

## 5. 验收矩阵（AC → 命令 → 结果）

状态在实施时改成「证明：`<确切命令>` → `<nodeid>` → `<结果摘要，如 N passed>`」。E4 必须逐行回填本节；文档检查与 §6 末尾全量 pytest 都不是十四行的替代。预定 nodeid 以 §5.1 为准，禁止再用「composition test」「既有代表项」占位。

| AC | 切片 | 命令 / 预定 nodeid | 状态 |
| --- | --- | --- | --- |
| AC1 | S3 | §5.1 AC1 五条 | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_module_graph.py::test_product_callers_import_only_public_static_names tests/test_static_journal.py::TestStaticJournal::test_ordinary_journal_read_does_not_open_ty_cache tests/test_static_journal.py::TestStaticJournal::test_ordinary_ty_cache_decode_does_not_replay_comparison_or_hint tests/test_static_report.py::TestStaticReport::test_search_workflow_writes_a_report_without_static_intern tests/test_static_report.py::TestStaticReport::test_reader_rejects_old_intern_tables` → 五条 nodeid（intern 表拒绝含参数化）→ 8 passed, 1 deselected |
| AC2 | S3 | `tests/test_cli.py::TestDefaultContext::test_production_composition_shares_one_static_evaluator_and_process_runner` | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_cli.py::TestDefaultContext::test_production_composition_shares_one_static_evaluator_and_process_runner` → 该 nodeid → 1 passed |
| AC3 | S3 | `tests/test_static_module_graph.py::test_search_has_no_handwritten_slice`；`tests/test_search_coordinator.py` / `tests/test_static_guidance.py` hint 语义；无 ledger 不得打开 slice | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_module_graph.py::test_search_has_no_handwritten_slice tests/test_runtime_static_scope.py::TestRuntimeStaticPassRegistration::test_open_slice_requires_direct_pass_ledger tests/test_static_guidance.py tests/test_search_coordinator.py` → 结构扫描 + 无 ledger `ValueError` + guidance/coordinator → 169 passed |
| AC4 | S2 | `tests/test_static_module_graph.py::test_schemas_obey_static_import_allowlist`；`::test_schema_validators_do_not_replay_compare_hint_or_harness`；`tests/test_policy.py`（ty-args 不在 schema 执行） | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_module_graph.py::test_schemas_obey_static_import_allowlist tests/test_static_module_graph.py::test_schema_validators_do_not_replay_compare_hint_or_harness tests/test_policy.py` → 三入口 → 32 passed |
| AC5 | S2 前置 + S3 | S2：`tests/test_static_module_graph.py::test_admit_uses_shared_derive_and_hint`。S3：`::test_derive_and_hint_have_one_implementation` + 三入口语义 | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_module_graph.py::test_admit_uses_shared_derive_and_hint tests/test_static_module_graph.py::test_derive_and_hint_have_one_implementation tests/test_static_comparison.py` → admit/单实现 + GLOBAL/SLICE/`_admit` 坏 closure → 5 passed |
| AC6 | S4 | owner 正文（含 D003 §5 改写）+ `scripts/check_docs.py` | 证明：D002 §3/§4/§7/§11、D003 §5、D004 §6–§7/§10、D008 §4/§7–§8、CONTEXT、R011、`docs/README.md` 已吸收；D005 只核对未改分类。E4：`.venv/bin/python scripts/check_docs.py` → exit 0 |
| AC7 | S1 表 + S3 + S4 | 去向表执行；`tests/test_static_module_graph.py::test_no_test_only_public_static_exports` | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_module_graph.py::test_no_test_only_public_static_exports` → 该 nodeid → 1 passed。`tests/scripted_static.py` 与 `StaticContentCollector` / `TestStaticContentCollector` 已删除；`pf.static.__all__` 仅 §3.1/§3.4 |
| AC8 | S3 | `tests/test_static_request.py` 连续 collect；`tests/test_static_comparison.py` GLOBAL/SLICE | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_comparison.py tests/test_search_coordinator.py::TestSearchCoordinator::test_same_ty_key_is_collected_once_and_global_local_deltas_differ` → 4 passed。process：`uv run pytest --no-testmon -q --tb=short -m "process and not qualification" tests/test_static_request.py` → `TestRealStaticRequest` 连续 `collect_prepared` hit → 3 passed |
| AC9 | S3 | `tests/test_verification.py` 生命周期；`tests/test_static_module_graph.py::test_product_callers_do_not_invoke_cache_domain_methods` | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_verification.py tests/test_static_module_graph.py::test_product_callers_do_not_invoke_cache_domain_methods` → 38 passed |
| AC10 | S1+S2 | E1 三 nodeid；S2 后再跑同一文件；`scripts/generate_report_schema.py --check` | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_identity_golden.py` → `TestIdentityGolden` 三 nodeid → 3 passed（S2 后进口 `pf.schemas.resolution` / `pf.resolution` re-export）。`.venv/bin/python scripts/generate_report_schema.py --check` → exit 0 |
| AC11 | S3 | 现行 D003/D004 权威测试（`tests/test_static_guidance.py`、`tests/test_search_coordinator.py`） | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_guidance.py tests/test_search_coordinator.py` → 167 passed |
| AC12 | S3 | `tests/test_static_module_graph.py::test_orchestrators_do_not_accept_failures` | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_static_module_graph.py::test_orchestrators_do_not_accept_failures` → 该 nodeid → 1 passed |
| AC13 | S3 | §5.1 AC13；`tests/test_evaluation.py` Runtime 无 `run_cache` | 证明：`uv run pytest --no-testmon -q --tb=short tests/test_runtime_static_scope.py::TestRuntimeStaticPassRegistration tests/test_evaluation.py` → ledger 八条（含跨 Run process）+ Runtime 无 `run_cache` → 34 passed, 1 deselected |
| AC14 | S3 | `tests/test_static_module_graph.py::test_static_facts_do_not_associate_process_logs` 加 §5.1 AC14 家族表 | 证明：`uv run pytest --no-testmon -q --tb=short` + §5.1 AC14 十三行 nodeid → 51 passed |

停止条件与 D039 §9 对齐。

### 5.1 预定 nodeid

**AC1**

- `tests/test_static_module_graph.py::test_product_callers_import_only_public_static_names`
- `tests/test_static_journal.py::TestStaticJournal::test_ordinary_journal_read_does_not_open_ty_cache`
- `tests/test_static_journal.py::TestStaticJournal::test_ordinary_ty_cache_decode_does_not_replay_comparison_or_hint`（S3 新增：普通 `TyCacheDocument` decode 不调用 `_admit_saved_static_audit`、不重放 comparison/hint）
- `tests/test_static_report.py::TestStaticReport::test_search_workflow_writes_a_report_without_static_intern`
- `tests/test_static_report.py::TestStaticReport::test_reader_rejects_old_intern_tables`

**AC5 前置（S2 / E2）**

- `tests/test_static_module_graph.py::test_admit_uses_shared_derive_and_hint`（S2 新增：`_admit_saved_static_audit` 调用即将被 `compare_global` / `StaticSlice` 委托的同一 module-private derive 与 `locate_static_hint`；schemas 与第二份实现不得存在。S3 再用 `test_derive_and_hint_have_one_implementation` 把三入口接上）

**AC2**

- `tests/test_cli.py::TestDefaultContext::test_production_composition_shares_one_static_evaluator_and_process_runner`（S3 新增：Check/Highest/Search 共用同一个 `StaticEvaluator`；生产 `TyAdapter` 与 request/inspect 装配绑定同一个 `ProcessRunner`；无 `StaticRequestFactory`）

**AC13**（均在 `tests/test_runtime_static_scope.py`，除另注明）

- `test_record_runtime_registers_pass_without_consumer`
- `test_record_runtime_rejects_foreign_or_unregistered_prepared`
- `test_record_runtime_rejects_process_reuse`
- `test_record_runtime_is_idempotent_for_same_process`
- `test_later_collect_binds_consumer_without_replacing_runtime_owner`
- `test_record_runtime_rejects_different_prepared_for_owned_proposal`
- `test_record_runtime_validates_registration_before_outcome`（Rejection / 无 diagnostics 但未注册 prepared 仍 `ValueError`）
- `test_record_runtime_rejects_process_from_another_run`（盲评 F4：同一 `ProcessObservation` 不得写入另一 Run）

**AC14** — 静态 fact 不关联 Process Log；下列既有代表项证明 verifier 与各 prepare authority family 的 sidecar / diagnose 仍按 D005/D008。S3 不得删除或改弱这些断言。

| family | 代表 nodeid |
| --- | --- |
| 静态 fact 不 associate | `tests/test_static_module_graph.py::test_static_facts_do_not_associate_process_logs` |
| execution @ R/I（unattributed nonzero） | `tests/test_prepare_execution.py::TestPrepareExecution::test_unattributed_normal_nonzero_rejects_through_factory_and_policy` |
| execution @ R（qualified UNSAT） | `tests/test_prepare_execution.py::TestPrepareExecution::test_qualified_unsat_keeps_attempt_binding` |
| execution @ A（timeout / 辅助不拒绝） | `tests/test_prepare_execution.py::TestPrepareExecution::test_timeout_cleanup_zero_never_becomes_success`；`::test_auxiliary_failure_does_not_reject` |
| operation-structured @ R/I/A/Q | `tests/test_prepare_execution.py::TestPrepareExecution::test_every_structured_fact_roundtrips_and_rehashed_semantic_forgery_is_rejected`（含 `inspect-project-plan` / `proposal-vector`） |
| operation-structured @ inspect 成功观察非法 | `tests/test_prepare_execution.py::TestPrepareExecution::test_bad_success_observation_retains_normal_zero` |
| configured-verifier @ `test` | `tests/test_failure.py::TestFailureRecords::test_configured_verifier_failure_identity_contains_only_terminal_facts` |
| verifier Process Log sidecar | `tests/test_verification.py::TestVerificationRunnerProjection::test_check_role_and_process_enter_the_journal` |
| diagnose availability / 本机日志 | `tests/test_diagnose.py::TestDiagnoseWorkflow::test_diagnose_shows_last_three_nonempty_stderr_lines_from_the_log`；`tests/test_verification.py::TestVerificationRunnerDurability::test_journal_is_durable_before_diagnose_and_finalized` |
| typed terminal → portable structured | `tests/test_failure.py::TestFailurePolicy::test_typed_terminal_unavailable_uses_portable_structured_authority` |
| 静态采集不可经 FailurePolicy | `tests/test_failure.py::TestFailurePolicy::test_static_collection_cannot_be_classified_as_a_dynamic_failure` |

`inspect-environment-plan` 与 `inspect-project-plan` 共用 `installed-graph-mismatch` 规则；本切片不新开 D005 用例，保持上表 Q 代表项即可。

## 6. 验证命令

cwd 仓库根。按 AGENTS.md 在沙箱外运行。

```sh
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/generate_report_schema.py --check

# E1
uv run pytest --no-testmon -q --tb=short \
  tests/test_identity_golden.py::TestIdentityGolden::test_resolution_graph_id \
  tests/test_identity_golden.py::TestIdentityGolden::test_environment_identity_digest \
  tests/test_identity_golden.py::TestIdentityGolden::test_resolution_request_digest

# E2
uv run pytest --no-testmon -q --tb=short \
  tests/test_identity_golden.py \
  tests/test_resolution.py \
  tests/test_static_module_graph.py::test_schemas_obey_static_import_allowlist \
  tests/test_static_module_graph.py::test_schema_validators_do_not_replay_compare_hint_or_harness \
  tests/test_static_module_graph.py::test_admit_uses_shared_derive_and_hint \
  tests/test_static_comparison.py tests/test_static_journal.py tests/test_policy.py
.venv/bin/python scripts/generate_report_schema.py --check

# E3
uv run pytest --no-testmon -q --tb=short \
  tests/test_static_module_graph.py \
  tests/test_evaluation.py tests/test_check.py tests/test_baseline.py \
  tests/test_smoke.py tests/test_search.py tests/test_search_coordinator.py \
  tests/test_search_workflow.py tests/test_verification.py \
  tests/test_static_guidance.py tests/test_static_lifecycle.py \
  tests/test_runtime_static_scope.py tests/test_static_ownership.py \
  tests/test_cli.py tests/test_static_report.py tests/test_static_cache.py \
  tests/test_prepare_execution.py tests/test_failure.py tests/test_diagnose.py
uv run pytest --no-testmon -q --durations=30
uv run pytest --no-testmon -q --tb=short -m "process and not qualification" \
  tests/test_static_inputs.py tests/test_static_request.py \
  tests/test_static_journal.py tests/test_static_report.py \
  tests/test_static_cache.py
uv run ruff check tests src
uv run ty check
git diff --check

# E4：§5 十四行都写成「证明：命令 → nodeid → 结果」后再跑；本段全量 pytest 不能代替回填
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/generate_report_schema.py --check
uv run pytest --no-testmon -q tests/test_static_module_graph.py tests/test_identity_golden.py
uv run pytest --no-testmon -q -m "not qualification"
```

## 7. 行动、偏差与未决

行动日志：

- 2026-09-10：复核 D039 后锁定五处设计修订并起草本 Plan；未改 `src/` / `tests/`。
- 2026-09-10：接受二次评审。D039 补 Direct-PASS ledger、`StaticAuditDocument` 结构 / `admitted_membership` / writer 错误映射、§3.2 fail-closed 契约。本 Plan 改 S2/S3 分界、补去向表与 §5 证据命令、锁定 E1 三组字面 hex。仍未改 `src/` / `tests/`。
- 2026-09-10：接受三次评审。D039 分 Preparation registry 与 Direct-PASS ledger；未绑定 PASS 补 `scope_ref` / `run_identity` / `preparations[]` / `pass.preparation_ref`；`record_runtime` 固定先身份后 outcome；`open_slice` 固定 context 来源；S4 改写 D003 §5。本 Plan 补 §5.1 nodeid、E2 `test_resolution.py`、E3 `test_static_report.py` / `test_static_cache.py`、golden 真实 keyword。仍未改 `src/` / `tests/`。S2/S3 在本修订落地前不开。
- 2026-09-10：接受收尾评审。D039 澄清 AC9 时序与 Search ledger 准入。本 Plan 补 E2 `test_admit_uses_shared_derive_and_hint`、E3 process 的 `test_static_cache.py`、§4 `test_policy.py` 去向、E4 逐 AC 回填格式。仍未改 `src/` / `tests/`。
- 2026-09-10：S1 落地 `tests/test_identity_golden.py`，三组字面 hex 对现行 `pf.resolution` 成立（E1：3 passed）。开始 S2。
- 2026-09-10：S2 纯化 schemas、`StaticAuditDocument` / `_admit_saved_static_audit` / `admitted_membership`；identity 下放到 `pf.schemas.resolution`。S3 一次切齐 `pf.static` 五方法、Preparation registry + Direct-PASS ledger、删除 `failures=` / RequestFactory 产品缝 / `scripted_static.py` / `StaticContentCollector`。S4 吸收 D002/D003 §5/D004/D008，回填 §5 十四行并归档。
- 2026-09-10：盲评后修复 F1–F6/F8，F7 记为保留偏差。未标记回归 `uv run pytest --no-testmon -q -m "not qualification"` → 2512 passed, 1 skipped, 8 deselected。

偏差与失败用例（实施时追加）：

- schema 不再重放 harness；`project_plan.request_digest` 仍由 `StaticPreparationEvidence` 从 attempt 预像复算。`environment_plan.request_digest` 的 harness 变换改由 `_admit_saved_static_audit` 执行。
- `open_slice` 无 ledger 的产品负向测试落在 `tests/test_runtime_static_scope.py::test_open_slice_requires_direct_pass_ledger`，与 AC3 结构扫描一并证明。
- 2026-09-10 盲评（对起点 `09b742c` 工作区；契约为接受时 `b79ed47` 的 D039/P046）。发现 must-fix F1–F6、F8 与偏差 F7；完成声明在本条修复落地前不成立。
  - **F1 / AC9：** Runner 在 `stop` 之后仍经 `finalize` 调用 `admitted_membership`。
  - **F2 / AC13：** `collect_prepared` 在 `static_preparation_evidence` 返回 unavailable 时不登记 prepared。
  - **F3 / AC13：** `TyCheckCache` 同时维护 `_DirectPassEntry` 与旧 `RunStaticPassRef` / `record_pass` / `find_pass`。
  - **F4 / AC13：** verifier/ty process 只在单一 cache 内查重，同一 `ProcessObservation` 可写入另一 Run。
  - **F5 / AC7：** 去向表未切完：产品测试读 `snapshot`；`test_static_request` 仍断言 RequestFactory 形状；`static_admission.py` / `static_guidance.py` 过渡 re-export 仍在。
  - **F6 / AC5：** `test_derive_and_hint_have_one_implementation` 右侧 `or "compare_global" in FunctionDef` 恒真。
  - **F7 / 偏差：** `StaticEvaluator` 除锁定五方法外另有 `record_phase_skip` / `record_oracle_selection`（Search 写 audit 且不得直接调 cache 领域方法）。保留这两入口，记为对「公开五方法」锁的偏差。
  - **F8 / E4：** 十四行证据未全部写成可复现「命令 → nodeid → 结果」；完成/归档与上列缺口冲突。

修复（2026-09-10，对上列盲评项）：

- **F1：** `_VerificationEvents.admit_remaining()` 在 `stop` 前跑完全部 Cell admission；`finalize` 只 persist。收尾为 `admit_remaining` → `stop` → `documents` → `close`。
- **F2：** `collect_prepared` 在无法形成 `StaticPreparationEvidence` 时 `ValueError`；`StaticContentUnavailable` 只在 `register_prepared` 之后返回。
- **F3：** 删除 `RunStaticPassRef`、`record_pass`、`find_pass` 与 `_passes`；比较与 snapshot 只认 Direct-PASS ledger。
- **F4：** ty/verifier process 按对象身份登记 Run owner；跨 Run 写入 `ValueError`。`test_record_runtime_rejects_process_from_another_run` 证明。相同 payload 的另一 process 对象仍可属于另一 Run。
- **F5：** 删除 `static_admission.py` / `static_guidance.py` 过渡 re-export。baseline / check / evaluation / verification 改 `admitted_membership` / `documents`。`test_static_request` 去掉 RequestFactory argv 形状断言。
- **F6：** `test_derive_and_hint_have_one_implementation` 要求 evaluator 实际调用 `compare_global` 与 `compare_document`，并证明 derive/hint 单实现；去掉恒真 `or`。
- **F7：** 保留 `record_phase_skip` / `record_oracle_selection`。Search 写 phase skip / oracle selection 不得直接调 cache 领域方法，故多两个 Evaluator 入口；对 P046「公开五方法」锁记偏差。
- **F8：** §5 十四行按本轮命令回填（AC13 现为 `TestRuntimeStaticPassRegistration` 整类 + `test_evaluation.py` → 34 passed, 1 deselected）。

残留（记偏差，不扩公开表面）：

- Search D003 权威测试（`test_search_coordinator.py`）仍经 `snapshot` 观察 Cell audit 的 searches/skips/selections；`admitted_membership` 只投影 Journal highest。产品调用方不读 snapshot。
- process 夹具与 adapter 测试仍用 request factory 装配 adapter-facing `StaticTyRequest`（`static_fixtures.static_request`、`test_ty_adapter`、relocate 路径）。这不是产品 Evaluator 形状。

## 8. 文档与生成物

| 切片 | 允许改的文档 / 生成物 |
| --- | --- |
| S1 | 本 Plan；`tests/test_identity_golden.py`。不改 owner 正文 |
| S2 | 不改 owner 正文。生成投影 `--check` 必须通过 |
| S3 | 测试与代码。D002/D004 等到 S4 |
| S4 | D002 §3/§4/§7/§11、D004 §6–§7/§10、D008 相关段、**D003 §5 改写**（`record_runtime` / Direct-PASS / `open_static_slice` 委托，不是只改指针）、CONTEXT、R011、`docs/README.md`。D002 §11 同时保留 D040/D041 车道句与 D039 新表面句。D005 只核对。§5 十四行必须已按「证明：命令 → nodeid → 结果」回填。归档 D039/P046 |

## 9. 明确不做

与 D039 §8 相同。

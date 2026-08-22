# P008 — D009 模块加深与现行契约修复实施记录

- **状态：** 已完成
- **开始日期：** 2026-08-22
- **性质：** 非规范性实施记录
- **设计来源：** [D009](../designs/D009-pf-v1-refactor.md)
- **依赖：** [P006](P006-pf-verification-run.md)、[P007](P007-pf-cli-enhancement.md)
- **对照快照：** [R001](../reviews/R001-pf-v1-review.md)

本文记录 D009 的落地顺序、测试面、过程与可核验证据，不复制契约。切片顺序对齐 D009 §17；完成标准对齐 D009 §16。

## 1. 范围

本轮实现 D009 全部 P0 / P1 / P2：

- P0：流式脱敏、报告 portable 字段、CellSuccess 闭环、apply 工作区事务、精确 artifact、工程门禁
- P1：ProjectDiscovery 离线、Journal v2、单一 lookup / Failure 提取、VerificationRunner、`classify_evaluation`、CoordinateSearch 真重入与拆分、RunLogStore 独立测试
- P2：PreparedEnvironment 生命周期、`pf.terminal` 包内拆 explain / diagnose

不增加命令，不改 D001 §10，不改 D003 probe 顺序，不改 D005 cause 矩阵。

## 2. 测试面（seam）

每个切片只在下列公开 interface 上变红/变绿，不穿过私有 helper。

| Seam | 观察方式 |
| --- | --- |
| `SubprocessRunner.run` + `RunLogStore` | Output Cache、`runner.output()`、Process Log 正文、流式 `write_stdout`/`write_stderr` |
| `SecretRedactor.redact` | 单值等价；流式只经 Runner 观察 |
| `PackageFloorReportV1.model_dump` / `CellSuccess` | dump 无 stdout/stderr；防篡改矩阵在 Schema 构造处失败 |
| `FailureDetail` | 由 diagnostic / HTTPError 构造后的 `message` 无凭据、无输出正文 |
| `AvailableArtifact.locator` | 写入 snapshot 后无 userinfo / query |
| `ProjectEditor.apply` / `apply_many` | 用户 `pyproject.toml` 字节、recovery journal 状态、不启动 Python provider |
| `ProjectDiscovery.discover` | 返回 `PackageLocation` 或列出冲突路径的 `ConfigurationError` |
| Explain / Diagnose workflow | 外部 adapter 一律报错仍能离线完成 |
| `UvAdapter.query` | 认证成败、畸形 JSON/header、保存的公开 locator |
| `RunLogStore` | `write_journal` / `read_latest_journal` / `replace_associations` / `lookup` / `lookup_run` |
| `cell_identity` / `failure_records_for_result` / `incomplete_reason` | 全库 lookup / 列举只走这三处 |
| `VerificationRunner.run` | 三命令共用 gate + schedule + journal timing |
| `FailurePolicy.classify_evaluation` | Check 与 ProposalRunner 不再手写 cause |
| `CoordinateSearch.minimize` | 纯向量 fixture；嵌套重入与双线程 barrier |
| `SearchCoordinator` | 省略 `highest` / `coordinate_search` 则构造失败 |
| CI workflow | `ruff` / `ty check src` / `pytest --no-testmon` / `uv build`；Python 3.10–3.12 |

## 3. 落地顺序与测试计划

| 切片 | 可观察行为 | 主要测试 | 状态 |
| --- | --- | --- | --- |
| 001 | 任意 chunk 切分与一次性脱敏等价；五观察面无明文 | `tests/test_process.py` | 已完成 |
| 002 | 报告 dump 无 stdout/stderr；FailureDetail / locator 为公开值 | `tests/test_schemas.py`、`tests/test_uv_adapter.py`、`tests/test_failure.py` | 已完成 |
| 003 | CellSuccess / report 防篡改矩阵 | `tests/test_schemas.py` | 已完成 |
| 004 | apply 两阶段事务、workspace rollback、未知 journal fail closed、写后不启动 uv | `tests/test_editor.py` | 已完成 |
| 005 | exact-vector 绑定 Candidate artifact + hash；畸形 registry 保守失败 | `tests/test_search_coordinator.py`、`tests/test_environment.py`、`tests/test_uv_adapter.py` | 已完成 |
| 006 | ProjectDiscovery 唯一 canonical name；Explain/Diagnose 离线；CI matrix | `tests/test_project.py`、`tests/test_cli.py`、`.github/workflows/` | 已完成 |
| 007 | Journal v2 + v1 只读；RunLogStore 独立测试面 | `tests/test_runlog.py` | 已完成 |
| 008 | `cell_identity` / `failure_records_for_result` / `incomplete_reason` 唯一入口 | `tests/test_schemas.py`、`tests/test_report.py` | 已完成 |
| 009 | VerificationRunner；删除 journal `hasattr` | `tests/test_check.py`、`tests/test_smoke.py`、`tests/test_search_workflow.py` | 已完成 |
| 010 | `classify_evaluation` 实现 D008 §6.2 | `tests/test_failure.py`、`tests/test_check.py` | 已完成 |
| 011 | CoordinateSearch 表驱动 + 真重入；拆文件；必注入 | `tests/test_search.py`、`tests/test_search_coordinator.py` | 已完成 |
| 012 | 静态 PASS 立即 close；长期存活数不随历史 probe 增长 | `tests/test_search_coordinator.py` | 已完成 |
| 013 | `pf.terminal` 包；explain/diagnose 私有模块；对外 `render_X` 不变 | `tests/test_terminal.py` | 已完成 |
| 014 | 全量回归与契约交叉引用 | `pytest --no-testmon`、`docs/README.md`、D002 布局表 | 已完成 |

P0（001–006）必须先于纯重构。P2（012–013）不得阻塞 P0/P1。

## 4. 过程与证据

下列记录按切片保留 RED 证据、GREEN 命令与结论。

### 切片 001 — 流式脱敏

- **目标：** `concat(redact_stream(chunks)) == redact(concat(chunks))`；stdout / stderr / 流式 listener / Output Cache / Process Log 均无明文。
- **现行缺口：** `_redact_stream` 对 `keep` 与 `pending` 分别 `redact()`。secret 或 URL userinfo 跨切点时，前缀会先交给 consumer。
- **过程：** 先写 `SubprocessRunner` + 流式 log / cache / stderr 观察测试并 RED；将流式所有权留在 Process adapter，用 `SecretRedactor.holdback_chars` 扣住 secret 前缀、未闭合 URL userinfo 和未完成 scheme。`$` 改为 `\Z`，避免把完整 secret 算错成 holdback。
- **结论：** 任意小 chunk 切分与一次性 `redact(concat)` 等价；stdout / stderr / 流式 write / Output Cache / Process Log 均无明文。
- **证据：** `uv run pytest -o addopts= -p no:testmon tests/test_process.py::test_streamed_* tests/test_process.py::test_subprocess_runner_captures_and_redacts_external_output tests/test_process.py::test_subprocess_runner_records_redacted_bounded_process_logs` → 9 passed。

### 切片 002 — 报告 portable 字段

- **目标：** dump 不含 stdout/stderr；FailureDetail 不含 credential / 输出正文；artifact locator 去掉 userinfo 与 query。
- **过程：** 抽出 `public_locator`；registry 保存的 file URL 去掉 userinfo/query；`InfrastructureError.detail` 先经 `SecretRedactor`；Search 的 FailureDetail 不再抄 `error.detail`。
- **结论：** 报告 dump 仍不含 stdout/stderr；artifact locator 与异常 detail 不再带凭据。
- **证据：** `test_candidate_query_stores_public_locators_and_redacts_error_detail`、`test_package_floor_report_dump_excludes_process_output_bodies` 通过。

### 切片 003 — complete authority

- **目标：** final vector、terminal search、ProbePass、Attempt、Proposal、Evaluation、projection 闭环；任一单独篡改失败。
- **过程：** `CellSuccess._validate_final_authority` 强制 terminal search / ProbePass / Proposal / Attempt 与 `final_vector` 一致。
- **结论：** 只改其中一处会在 Schema 构造失败。
- **证据：** `test_cell_success_rejects_tampered_final_evidence` 通过。

### 切片 004 — apply 事务

- **目标：** Prepare 零写；Commit 失败全回滚；target digest 重启继续回滚；未知 schema fail closed；写后不启动 Python provider。
- **过程：** `apply` 委托 `apply_many`。Prepare 渲染全部 TOML，Commit 写 backup + v2 recovery journal 再替换。失败或重启看到 target digest 一律回滚。写后只核对 TOML 与 package identity。
- **结论：** 第 N 个 member 失败时全部 pyproject 回到调用前；未知 schema 不覆盖用户文件。
- **证据：** `tests/test_editor.py` 6 passed，含 unknown schema / target rollback / workspace rollback / 不调用 `ProjectLoader.load`。

### 切片 005 — artifact 绑定与 registry

- **目标：** exact-vector 安装 selection 的 locator/hash/kind；畸形外部输入只形成受控 infrastructure failure。
- **过程：** 增加 `SelectedCandidate` 与 `select_probe`，probe 安装改为精确 locator + SHA-256；安装后核对 managed graph。`RegistryAccess` 仅在进程内持有认证；Simple JSON 对 header、根结构、file 字段、URL、filename、specifier 和 hash 做有界严格解析。CandidateSnapshot digest 由 Schema 统一计算并反向校验。
- **结论：** exact-vector Attempt 可唯一回到冻结 artifact；缺失/篡改 hash、locator、版本或安装图均保守失败，凭据不进入 portable locator/detail。
- **证据：** `tests/test_environment.py`、`tests/test_search_coordinator.py`、`tests/test_uv_adapter.py` 与 Candidate artifact 防篡改矩阵通过。

### 切片 006 — discovery 与门禁

- **目标：** 重复 canonical name 在 discovery 失败；Explain/Diagnose 不调用工具；CI 跑 Ruff / ty / pytest `--no-testmon` / build。
- **过程：** 增加 `.github/workflows/ci.yml`（Python 3.10–3.12；ruff / ty / pytest `--no-testmon` / build）与 `ProjectDiscovery`；ProjectLoader、Explain、Diagnose 复用同一发现规则，离线命令不进入 planning/Python provider。
- **结论：** workspace canonical 重名在命令选择前列出全部冲突路径；Explain/Diagnose 在不满足当前 Python 规划时仍可读已有证据。
- **证据：** `tests/test_project.py`、`tests/test_report_workflows.py`、`tests/test_diagnose.py`、`tests/test_cli.py` 通过；Ruff 与 ty 通过。

### 切片 007 — Journal v2 与 RunLogStore

- **目标：** writer 只写 v2 `package_policies`；reader 兼容 v1；独立 `tests/test_runlog.py`。
- **过程：** Journal v2 以 `package_policies` 保存逐包 policy，packages 由其派生；writer 运行时拒绝 v1，reader 按 schema 兼容历史 v1。为 RunLogStore 增加 journal round-trip、association replace、lookup/lookup_run 与失败原子性测试。
- **结论：** 新写入只有 v2；v1 仅用于离线历史诊断，不参与授权。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_runlog.py` → 5 passed。

### 切片 008 — lookup / 提取唯一入口

- **目标：** 全库 Cell lookup = `cell_identity`；FailureRecord 列举只经 `failure_records_for_result`；Store 用模块级 `incomplete_reason`。
- **过程：** `schemas/project.py.cell_identity` 成为 lookup owner；workflow / report / scheduling 改为调用它。Terminal 仍用自己的 presentation `_cell_key`（python 在 target 前）。`failure_records_for_result` 升为公共函数；`incomplete_reason` 放在 `report.py` 模块级。Diagnose 直接调用 `read_latest_journal` / `lookup_run`，去掉 `getattr`/`hasattr`。`build_context` 让 diagnose 与 check 共用同一个 `projects`。
- **结论：** lookup 与 FailureRecord 列举不再各写一份分支。
- **证据：** `test_cell_identity_is_the_compatibility_quadruple`；`tests/test_report.py` / `tests/test_diagnose.py` 随回归通过。

### 切片 009 — VerificationRunner

- **目标：** 三命令不再复制 gate + schedule + journal；`logs` 残缺对象不得 silently no-op。
- **过程：** `VerificationRunner` 统一 `VerificationRun/Task`、Scheduler、failure event 缓冲、逐 cell Journal 写入与最终确认；三个 workflow 只提交任务和 Journal entry 投影。写入失败延迟到 completion 发布后抛出。
- **结论：** 只有 Journal 已写成功的失败 completion 才宣告 Diagnose 可用；`logs=None` 与写入失败均为 false，不存在可选方法探测。
- **证据：** `tests/test_verification.py` 3 passed；`tests/test_check.py`、`tests/test_smoke.py`、`tests/test_search_workflow.py` 回归通过。

### 切片 010 — classify_evaluation

- **目标：** Check 与 ProposalRunner 只调用 `FailurePolicy.classify_evaluation`。
- **过程：** `FailurePolicy.classify_evaluation` 实现 D008 §6.2；`CompatibilityChecker._evaluation_outcome` 与 `_ProposalRunner._static_evidence` / `_full_evidence` 改为只调用它。PASS 返回 `None`。
- **结论：** Check 与 Search 不再各写 `STATIC_REGRESSION` / `TEST_FAILURE` 字符串。
- **证据：** `test_classify_evaluation_returns_none_for_pass`；`tests/test_check.py` / `tests/test_search_coordinator.py` 通过。

### 切片 011 — CoordinateSearch

- **目标：** 表驱动 + 嵌套/并发重入；`coordinate_search.py`；`highest` 与实例必注入。
- **过程：** 算法移入 `coordinate_search.py`；一次 `minimize` 的 evaluator/cache/observations 全部由私有 invocation object 持有。SearchCoordinator 的 highest 与 coordinate_search 改为必需注入，并复用注入实例。
- **结论：** 同一实例的嵌套调用与双线程 barrier 交错互不污染，算法 probe 顺序保持 D003。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_search.py tests/test_search_coordinator.py` → 24 passed。

### 切片 012 — PreparedEnvironment 生命周期

- **目标：** 静态 PASS 后环境已关闭；长期存活数由 active jobs 限制。
- **过程：** `_ProposalRunner.evaluate_static` 在所有终态用 `finally` 关闭环境；full path 从缓存静态证据并按相同冻结 selection 重新 prepare，也在异常路径关闭。
- **结论：** 静态 PASS 不再把历史 probe 环境保留到 cell 结束；最终向量的 static/full 是两个独立 prepare。
- **证据：** `test_search_closes_static_pass_before_repreparing_for_full_evaluation` 与 `tests/test_search_coordinator.py` 13 passed。

### 切片 013 — terminal 包

- **目标：** 对外 `render_X` 不变；explain/diagnose 在包内私有模块。
- **过程：** `terminal.py` 迁为 `pf.terminal` 包；live presenter 留在入口，Explain/Diagnose 分别移入 `_explain.py` / `_diagnose.py`。
- **结论：** `from pf.terminal import TerminalPresenter` 与全部 `render_X` 不变，业务 Rich 未离开该包，视觉与文案未改。
- **证据：** `tests/test_terminal.py`、`tests/test_diagnose.py`、`tests/test_cli.py` → 117 passed。

### 切片 014 — 门禁与文档

- **目标：** 全量绿；D009 改为现行；D002 布局表加入新模块。
- **过程：** D002 更新现行布局/接口，D009 与索引改为现行，P008 记录逐切片证据；执行 Ruff、ty、全量 pytest、build 和真实安装 CLI integration。
- **结论：** D009 P0/P1/P2 全部落地，未扩展 D001 命令或 D003/D005 行为范围。
- **证据：** 见本记录最终门禁与仓库 CI workflow。

## 5. 决策（实施中新增）

本轮没有超出 D009 已确认决策的新增取舍。

## 6. 最终门禁

- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests`：通过。
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run ty check src`：通过。
- Python 3.10：`uv run --isolated --python 3.10 --group test pytest --no-testmon`，581 passed。
- Python 3.11：`uv run --isolated --python 3.11 --group test pytest --no-testmon`，581 passed。
- Python 3.12：`uv run pytest --no-testmon`，581 passed。
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv build`：成功生成 sdist 与 wheel。
- 真实本地包安装集成验证：`tests/test_check.py::test_check_passes_a_minimal_local_package` 与 `tests/test_end_to_end.py`，3 passed。

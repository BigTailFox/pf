# P025 — D019 PF SourcePlan 模块深化实施计划

- **状态：** 已完成并归档
- **开始日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D019](../designs/D019-pf-source-plan-depth.md)
- **评审来源：** [R005](../reviews/R005-pf-module-depth-review.md) §3
- **实施基线：** `b8efadc`（`docs: archive diagnostic result card design`）
- **实现提交：** `166c272`（`refactor: deepen source plan module`）

本文在生产代码修改前建立 D019 的实施顺序、interface/ownership 迁移、测试矩阵与证据槽位。
每次实质行动后在 §7 记录目标、行动、结论、精确命令和结果；完成标准只来自 D019 §10，不以
局部绿色测试、collection 或静态扫描替代验收。

## 1. 目标与边界

本轮完整实现 D019：

- 深化现有 `SourcePlan`，让 canonical routes、Run mode、effective source、dual-route facts、workspace
  member metadata 与唯一 identity 由一个 interface 提供；
- 每个 smoke/check/search invocation 只构造一份 plan，由 `VerificationRunner` 向所有 task operation
  注入该对象，并贯穿 Candidate、Harness、Environment、UvAdapter、Attempt/Proposal 与 report；
- 删除 `package_source_plan`、`package_source`、`effective_source`、`source_plan_identity`、
  `_source_policy_identity`、裸 `source_mode` operation 参数和零参数 task closure；
- 保持 ProjectLoader classification、UvAdapter argv、ApplyAuthorizer authority 与 ReportStore wire
  validation 的唯一 ownership；
- 将本设计涉及的 Schema、SourcePlan、ResolutionContext、Attempt、report generation 与 generator
  algorithm 全部固定/重置为当前 `v1` 定义，不提供兼容层或迁移器；
- 完成后把稳定规则归并 D001/D002/D003/D005/D008/D012/D014，回写 R005，并将 D019/P025 同时归档。

不实施 R005 的 WorkspaceInventory、Verification Run lifecycle 深化、评价 Protocol 合并、result-card
或 SearchCoordinator 测试表面候选；不改变命令、搜索算法、Schema 1 JSON 形状、Failure disposition、
apply scope 或 uv resolution/install policy。

## 2. 基线事实与目标差距

| 切面 | `b8efadc` 当前事实 | D019 目标 |
| --- | --- | --- |
| SourcePlan | strict/frozen record；行为仅排序/唯一验证 | 同一 record 提供构造、lookup、dual-route/member facts 与 identity；无实例缓存 |
| source identity | module helpers 计算 `pf:source-plan:v1`；Environment 另算 `pf:source-policy:v1` | 唯一 `SourcePlan.identity`；ResolutionContext/request/cache/Attempt/report 全部消费该值 |
| consumer interface | Candidate/Harness/Highest/Check/Search/Environment 传裸 `source_mode` 并可重建 plan | consumer 接收 workflow 构造的同一 `source_plan`，不遍历 raw routes 或解释 mode |
| Run/task | `VerificationRun.source_mode`；`VerificationTask.execute: Callable[[], T]` | Run 携带 plan；Runner 校验后调用 `execute(source_plan)`，task closure 不捕获 plan |
| uv | adapter 私有 route lookup 与 suppression 判定 | SourcePlan 返回 dual-route 名称；UvAdapter 只投影排序去重 argv |
| report | builder 从 PackagePlan 两次重建 SEARCH plan；reader 建 route map；reintern 再造 PackagePlan | live writer 接收 Run plan；reintern 使用 generation plan；reader 的 source facts 走 SourcePlan interface |
| apply | 比较重建 plan，并遍历 package routes 读取 member version | 构造一个 current SEARCH plan，精确比较并通过其 query interface 授权 |
| version labels | Schema/report generation/source plan 为 v1；ResolutionContext 含旧 policy key；Attempt 为 v2 | 全部按当前目标 v1 原地定义；旧 Attempt identity fail closed，无 dual validation |

## 3. Interface 与 ownership 迁移

1. `SourcePlan.for_package(package, mode) -> SourcePlan` 是在线 workflow/apply 的唯一领域构造入口；
   D014 wire decode 是 reader 的构造入口。除 codec validation 外，生产调用方不解释 `routes`。
2. `SourcePlan.source_for`、`registry_routed_workspace_dependencies`、
   `workspace_member_version_for` 与 `identity` 是 source facts 的唯一查询 interface。实现不保存
   `PrivateAttr`、`cached_property` 或其它实例缓存。
3. `VerificationRun.source_plan` 替换 `source_mode`；`VerificationTask.execute(source_plan)` 由 Runner
   独占调用。Runner 先验证 command/mode、package/routes 和 Cell，再把同一 plan 对象注入每个 task。
4. Candidate/Highest/Check/Search/Environment 与 harness helpers 接收 `source_plan`；UvAdapter 继续接收
   plan，但不再私有 lookup route 或判断 mode/dual-route。
5. `ResolutionContext.source_plan_identity` 原地替换旧 policy 字段；`pf:resolution-context:v1` preimage
   使用新 key。`AttemptIdentity` 只保留当前 `attempt-v1` 与 `pf:attempt:v1`，包含现有 resolution/
   harness/selected-candidate facts；删除 v2 分支。
6. `PackageReportBuilder.build(..., source_plan)` 对 live search 使用 Run plan；ReportStore reintern 对
   update/merge 使用 generation plan。Reader 可以检查 raw wire fields，但 effective lookup 与 identity
   闭合走 SourcePlan；不可信查询错误仍映射为 D014 `ConfigurationError`。
7. ApplyAuthorizer 只从 current PackagePlan 构造一个 SEARCH plan，先做 report/current 精确相等，
   再通过 member-version query 完成 intended-requirement 授权。SourcePlan 查询不得改变模型相等性。

## 4. 实施顺序

### 切片 001 — SourcePlan interface 与 v1 identity 基座

1. 先以 `test_project.py` / `test_schemas.py` 锁定 `for_package`、两种 mode、缺失 dependency、local/
   dual route、static/dynamic member metadata、排序唯一、wire dump 与 identity；
2. 增加查询前后 equality/hash/dump/identity 不变的 public interface 测试，禁止实例缓存；
3. 实现 SourcePlan methods/property，保留现有 wire fields 和 `pf:source-plan:v1` preimage；
4. 将 ResolutionContext 字段/preimage 原地改为 `source_plan_identity` / `pf:resolution-context:v1`；
5. 将 Attempt identity 干净重置为唯一 `attempt-v1` / `pf:attempt:v1`，删除 v2 条件路径并更新领域测试。

### 切片 002 — Harness、Environment 与 UvAdapter

1. 把 original/relaxed harness source lookup 改为接收 SourcePlan 并只调用 `source_for`；
2. 让 EnvironmentFactory 接收调用方 plan，不再重建；context、两阶段 request/cache、Attempt 与
   Proposal 全部绑定 `plan.identity`；
3. `_request_digest` 只保存 `source_plan_identity`，删除 raw plan dump 和 `_source_policy_identity`；
4. leakage 检查用 plan dual-route/source query，仍由 Environment 按 active Cell/coordinate 下结论；
5. UvAdapter 通过 plan query 产生 project/environment 两次相同、排序去重的 suppression argv，保持
   resolver/install/inspection ownership 与环境隔离。

### 切片 003 — Candidate 与三个 Cell 产品编排器

1. CandidateBuilder 接收 SourcePlan，provider 使用 SEARCH effective registry source，所有 snapshot
   绑定同一 plan identity；
2. HighestVersionVerifier、CompatibilityChecker 与 SearchCoordinator 只把 plan 传入各自既有流程，
   不合并产品 outcome、评价 Protocol 或 coordinate 状态机；
3. Search 内 baseline、candidate freeze、exact probes 与 prepare cache 使用同一 plan；
4. 以 candidate/harness/baseline/check/search public seam 验证结果、source 与 identity，不断言私有 helper。

### 切片 004 — VerificationRun、task 注入与 workflow/report writer

1. 用 `VerificationRun.source_plan` 替换裸 mode，并增加 package/routes 与 command/mode fail-closed；
2. 将 task operation 改为 `execute(source_plan)`，由 Runner 注入 request 中的同一对象；Journal、
   association、deadline callbacks 与 Scheduler interface 保持不变；
3. smoke/check/search workflow 各构造一份 plan，task closure 不捕获 plan；无 Cell、snapshot 和 typed
   command outcome 行为保持不变；
4. PackageReportBuilder 显式接收 Search Run plan，同时用于 generation identity 与
   `inputs.source_plan`；ReportStore reintern 使用 generation 已有 plan，不从 PackagePlan 重建。

### 切片 005 — Apply、ReportStore reader 与 Schema 1 工件

1. ApplyAuthorizer 比较 report/current plan，并通过 SourcePlan 查询 dual-route member version；保持
   intended requirement、动态版本与 `--force` 不可 waiver 语义；
2. Reader 使用 SourcePlan identity/effective lookup 闭合 CandidateSnapshot、Attempt 与 generation，
   同时保留 raw wire/public locator/cross-ref validation 和稳定 `ConfigurationError`；
3. Schema 1 只接受当前 `attempt-v1`，含旧 Attempt identity 的开发报告 fail closed；update/merge 不
   混合迁移前后 Attempt evidence；
4. 同步唯一 wire model、生成脚本、JSON Schema、complete/incomplete examples 和 fixtures；不增加
   old reader、alias、dual validation、migrator 或逐个枚举旧 interface 的交付测试。

### 切片 006 — 删除旧路径、owner 归并、全量证据与归档

1. 删除旧 helpers、裸 mode operation 参数、零参数 task closure、`source_policy_identity`、
   `pf:source-policy:v1`、`attempt-v2` 及语义等价的平行 source facts；
2. 运行 focused tests、generator no-drift、Ruff、ty、Python 3.10/3.11/3.12 顺序全量 pytest、coverage、
   build、Markdown links、diff 与静态 ownership 扫描；
3. 按 §5 逐项审计 D019 §10，任何缺证据项继续实施，不以全量绿色替代；
4. 把稳定规则归并 D001/D002/D003/D005/D008/D012/D014，更新索引与 R005 SourcePlan 状态；
5. D019/P025 标记完成并同时移入 `docs/archived/designs` / `docs/archived/plans`，R005 因其余候选未解
   继续开放。

## 5. D019 §10 验收与证据矩阵

| 验收项 | 切片 | 主要 public 测试/检查 | 直接证据目标 |
| --- | --- | --- | --- |
| 1. 唯一 SourcePlan interface、wire 与无缓存相等性 | 001 | `test_project.py`, `test_schemas.py`, `test_authorization.py` | 全查询矩阵；round-trip；查询前后 equality/hash/dump/identity |
| 2. 每 invocation 一份 plan，Runner 注入 task | 004 | `test_verification.py`, `test_smoke.py`, `test_check.py`, `test_search_workflow.py` | command/package/routes/Cell 拒绝；每个 operation 收到 Run plan 同一对象 |
| 3. 六类 consumer 不重做 route/mode/dual facts，旧 helper 删除 | 002–006 | consumer public tests + production `rg` | 结果/source/argv 不变；raw routes 与旧 helper 无未授权命中 |
| 4. 唯一 identity、v1 原地定义、无 source-policy 摘要 | 001–006 | `test_resolution.py`, `test_environment.py`, `test_schemas.py`, `test_report_schema.py` | context/request/cache/Attempt/Proposal/report 同值；无 v2/平行摘要 |
| 5. UvAdapter suppression 与环境隔离 | 002 | `test_uv_adapter.py`, applicable uv qualification | 两次 compile argv 相同；DEVELOPMENT/local/fixed/unmanaged 不抑制；无全局 fallback |
| 6. ApplyAuthorizer authority 与 member version | 005 | `test_authorization.py`, `test_cli.py` | report/current equality；static/dynamic/intended requirement；force 不可 waiver；无 TOML 重读 |
| 7. Report writer/reader、Schema 1 与生成物 | 004–005 | `test_report_schema.py`, `test_projection.py`, generator `--check` | live/reintern plan 来源；current attempt-v1；cross-ref/public locator；旧 Attempt fail closed |
| 8. public test matrix 替换 shallow tests | 001–005 | 上述全部 focused suites | 不 monkeypatch route map、不测试 helper 名称/旧兼容行为 |
| 9. owner 与 production caller 静态闭合 | 006 | D001/D002/D003/D005/D008/D012/D014 + `rg`/links | owner/实现一致；允许点外无 raw routes、裸 mode、另造 plan |
| 10. Plan evidence、质量门禁、归并与归档 | 006 | §7–§8 ledger/audit | 所有证据有精确命令结果；状态、索引、R005、归档一致 |

## 6. 变更控制与验证命令

- Plan 建立时 HEAD 为 `b8efadc`；工作树已有用户范围内的 `docs/README.md`、D019、R005 修改，以及
  无关的 `.vscode/settings.json` 修改。实施必须保留无关修改，不覆盖、不提交或顺手整理；
- PF 尚未发布，目标 interface 与 identity 一次替换；临时共存只允许作为未提交实施步骤，交付时不得
  留下 alias、dual read/write、fallback 或旧行为测试；
- tests 断言 public behavior 与稳定语义；只有 Run 注入的一对象不变量可以断言对象同一性，不断言
  private route map/helper 或复制 identity 算法；
- 默认 uv cache 不可写时使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`；Python 版本全量测试必须在同一工作树
  顺序执行，网络/build/coverage/资格限制与代码失败分别记录；
- 计划验证命令如下，实施时记录精确结果而不是预填“通过”：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon <focused test modules> -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/generate_report_schema.py --check
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ty check src
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --cov=pf --cov-report=term-missing -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.12 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv build
git diff --check
```

## 7. 过程与证据记录

### 2026-09-03 — Design 接受与 Plan 建立

- **状态：** 已完成
- **目标：** 在 production code 修改前，把 D019 十项验收映射到有序 interface/ownership 迁移、public
  tests 和证据槽位，并吸收文档评审发现。
- **行动：** 将 D019 收敛为已接受目标；决定所有本设计版本标签固定/重置为 `v1`；让 Runner 注入
  task plan；禁止 SourcePlan 实例缓存；补齐 D005 owner；建立本 P025 并同步 R005/docs index。
- **结论：** SourcePlan 是 in-process pure-data module，不增加 port/adapter；Run plan 同一性必须由
  task interface 保证，不能只靠 closure 约定；开发期旧 Attempt 不提供兼容或迁移。
- **当前证据：** `git rev-parse --short HEAD` → `b8efadc`；`rg` 确认当前 `SourcePlan` 仍只有 record
  validation 和四个 module helpers，`VerificationRun` 仍携带 `source_mode`，`VerificationTask.execute`
  仍是 `Callable[[], T]`；production 涉及 14 个文件，tests 涉及 24 个文件。尚未修改 production code，
  尚未运行行为测试。

### 切片 001 — SourcePlan interface 与 v1 identity 基座

- **状态：** 已完成
- **行动：** 先以 `test_project.py` 锁定 mode/effective source、dual route、static/dynamic member
  metadata、未知 dependency、排序唯一、wire round-trip 与查询前后模型稳定性；再实现
  `SourcePlan.for_package`、四个查询/identity interface。`ResolutionContext` 改为
  `source_plan_identity`；Attempt 删除 v2 分支并以完整当前 facts 重定义 `attempt-v1`。
- **结论：** SourcePlan 仍是 strict/frozen wire record，JSON 只有 `source_mode + routes`；identity
  是无缓存派生 property。Context 与 Attempt 只保留当前 v1 preimage，没有兼容布局。
- **证据：** focused matrix 中 `tests/test_project.py`、`tests/test_schemas.py`、
  `tests/test_resolution.py` 全部通过；固定 identity、hash/equality/dump 与 wire round-trip 由 public
  schema seam 断言。

### 切片 002 — Harness、Environment 与 UvAdapter

- **状态：** 已完成
- **行动：** original/relaxed harness 与 EnvironmentFactory 改收调用方 plan；两阶段 request、context、
  Attempt 与 adapter 都传递同一对象。request digest 只保存 plan identity，leakage 检查消费 dual-route
  query；UvAdapter 删除私有 route lookup/suppression 分类，只投影 plan 返回的有序名称。
- **结论：** Environment 继续决定 active Cell leakage，UvAdapter 继续独占 argv；没有把 managed
  classification、failure 或 install authority 搬入 SourcePlan。
- **证据：** focused matrix 中 `test_harness.py`、`test_environment.py`、`test_uv_adapter.py` 通过；
  project/environment compile suppression、环境隔离、local/fixed/unmanaged 与 DEVELOPMENT 行为均由
  recording adapter public seam 覆盖。

### 切片 003 — Candidate 与三个 Cell 产品编排器

- **状态：** 已完成
- **行动：** CandidateBuilder、HighestVersionVerifier、CompatibilityChecker、SearchCoordinator 与
  `_ProposalRunner` 的裸 mode 参数全部替换为 SourcePlan；candidate source/snapshot、baseline、freeze
  与 exact probes 贯穿同一 identity。
- **结论：** 三个产品编排器与既有 outcome/state machine 保持独立；本轮只迁移 source seam，没有
  实施 R005 的评价 Protocol 或 SearchCoordinator 测试表面候选。
- **证据：** focused matrix 中 `test_candidates.py`、`test_baseline.py`、`test_check.py`、
  `test_search_coordinator.py` 通过。

### 切片 004 — VerificationRun、task 注入与 workflow/report writer

- **状态：** 已完成
- **行动：** VerificationRun 改携带 SourcePlan；Runner 在调度前闭合 command/mode、package/routes、
  重复与越界 Cell，再向 `execute(source_plan)` 注入同一对象。三个在线 workflow 各只构造一次 plan；
  Search 把该 plan 直接交给 PackageReportBuilder 的 generation 与 inputs。
- **结论：** task closure 只捕获 package/Cell/snapshot，不捕获 plan；Scheduler、Journal、deadline、
  association 和 typed command outcome 未改变。
- **证据：** `test_verification.py` 的 mismatch 与对象同一性矩阵，以及 focused matrix 中
  `test_smoke.py`、`test_check.py`、`test_search_workflow.py`、`test_cli.py` 全部通过。

### 切片 005 — Apply、ReportStore reader 与 Schema 1 工件

- **状态：** 已完成
- **行动：** ApplyAuthorizer 每次 authorize 构造一个 current SEARCH plan，精确比较后通过 member
  query 完成授权。Report writer/reader/reintern 使用 live/wire generation plan；effective source 与
  identity 走 SourcePlan，raw routes 只保留 codec/public-locator/cross-ref 用途。生成器、Schema 1
  complete example 与 Attempt fixtures 已重生成到当前 attempt-v1。
- **结论：** Schema 1 shape/version 与 apply/report authority 未变化；没有旧 reader、alias、迁移器、
  dual validation 或旧 interface 兼容测试。
- **证据：** focused matrix 中 `test_authorization.py`、`test_report_schema.py`、`test_projection.py`、
  `test_cli.py` 通过；`UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/generate_report_schema.py --check`
  → exit 0、无输出。

### 切片 006 — 删除旧路径、owner 归并、全量证据与归档

- **状态：** 已完成，归档动作随本完成变更提交
- **行动：** 删除旧 helpers、裸 operation mode、零参数 verification task、source-policy 摘要和
  Attempt v2；稳定规则归并 D001/D002/D003/D005/D008/D012/D014，并把 R005 SourcePlan 项标为已解决。
- **静态证据：** production/test/script 扫描中旧 helper 调用、`source_policy_identity`、
  `pf:source-policy:v1`、`attempt-v2`、`pf:attempt:v2` 均无命中；允许 owner 外的 raw
  `PackagePlan.source_routes` / `SourcePlan.routes`、裸 plan mode 与 production `SourcePlan(...)`
  构造均无命中。
- **质量证据：** 见下列完整 ledger；D019/P025 与索引在最终 diff/link 检查后同变更归档。

### 2026-09-03 — 最终验证 ledger

- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_project.py tests/test_schemas.py tests/test_resolution.py tests/test_harness.py tests/test_environment.py tests/test_uv_adapter.py tests/test_candidates.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py tests/test_verification.py tests/test_smoke.py tests/test_search_workflow.py tests/test_authorization.py tests/test_cli.py tests/test_report_schema.py tests/test_projection.py -q`
  → `706 passed in 5.74s`；
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/generate_report_schema.py --check` → exit 0、无漂移；
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests` → `All checks passed!`；
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run ty check src` → `All checks passed!`；
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --cov=pf --cov-report=term-missing -q`
  → Python 3.10.16，`1434 passed in 30.35s`，`90.62%`，达到 `fail_under = 90`；
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 --group test pytest --no-testmon -q`
  → `1434 passed in 25.05s`；随后顺序执行同命令的 `--python 3.12` →
  `1434 passed in 26.19s`；
- `UV_CACHE_DIR=/tmp/pf-uv-cache uv build` → 成功生成 `pf-0.1.0.tar.gz` 与
  `pf-0.1.0-py3-none-any.whl`；
- sandbox 内首次 full run 的唯一失败是 E2E 临时项目无法联网取得 `uv_build`；按相同代码在受控
  联网环境重跑上述 coverage/多版本门禁后全部通过，未把外部网络失败归类为代码失败；
- `git diff --check` → exit 0、无输出；全仓本地 Markdown relative-link existence 审计 →
  exit 0、无缺失链接。

## 8. 最终验收审计

| D019 §10 | 最终证据 | 结论 |
| --- | --- | --- |
| 1 | SourcePlan public query/round-trip/stability tests；wire dump 仅两字段；production 仅一个 record | 通过 |
| 2 | Runner mismatch/duplicate/out-of-set 与同一对象注入 tests；三个 workflow 单构造点静态审计 | 通过 |
| 3 | 六类 consumer public tests；旧 helper/raw route/mode ownership `rg` | 通过 |
| 4 | resolution/environment/schema/report identity tests；v1 与旧 policy/v2 静态扫描 | 通过 |
| 5 | UvAdapter project/environment suppression、DEVELOPMENT/local/fixed/unmanaged 与 env 隔离矩阵 | 通过 |
| 6 | authorization/CLI 的 plan equality、static/dynamic member、intended requirement、force tests | 通过 |
| 7 | report/schema/projection、generated artifacts、read/write/reintern/cross-ref tests | 通过 |
| 8 | `706 passed` focused public matrix；删除旧 identity compatibility test | 通过 |
| 9 | 七份 owner 已归并；production ownership 扫描与最终 Markdown link 审计 | 通过 |
| 10 | 本 ledger、3.10/3.11/3.12、coverage、Ruff、ty、build、diff、索引/R005/归档 | 通过 |

未决项：无。实施没有改变 D019 的 module interface、ownership、identity、wire 或错误边界；唯一
环境偏差是 sandbox 网络限制，已以受控联网的相同门禁复核。

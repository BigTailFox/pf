# P009 — D010 架构加深实施记录

- **状态：** 已完成
- **开始日期：** 2026-08-22
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D010](../designs/D010-pf-v1-architecture.md)
- **评审来源：** [R002](../reviews/R002-pf-v1-architecture-review.md)
- **依赖：** [P008](P008-pf-v1-refactor.md)
- **实施基线：** `af10d0c`（`test: fix ty diagnostics in test suite`）

本文先于实现建立 D010 的实施顺序和测试计划，并在每个切片完成后记录过程、结论与可核验证据。它不复制或修改规范性契约；完成标准以 D010 §15 为准。

## 1. 范围

本轮实现 R002 / D010 全部改进：

- 显式 scheduling order；
- 判别 `ResolutionRequest`；
- VerificationRunner 内部 Scheduler、deadline outcome、判别 activity event 与确定性 Clock；
- `_ProposalRunner.evaluate_full -> ProbeRun`；
- `SecureLogDirectory` POSIX/Windows 私有 adapter；
- `CellPresentation` 与 `LiveVerificationView` 终端私有 module；
- 完整 CliContext、统一资源关闭与显式 SnapshotBuilder runner；
- D002/文档索引同步和全量门禁。

不改变 D001 命令和退出码、D003 probe 顺序、D005 failure 矩阵、D006 输出契约、D007 安全语义或报告/apply authority。

## 2. 基线盘点

实施前对照 `af10d0c` 的静态证据：

| D010 项 | 基线事实 |
| --- | --- |
| scheduling order | `cell_schedule_key` 直接返回 `cell_identity(cell)` |
| resolution | `prepare(resolution, managed_vector=None, selection=None)`；exact probe 传 `highest + selection` |
| Scheduler ownership | `scheduling.py` 导入 FailurePolicy、Evaluation、FailureRecord、ProcessResult 与 CellResult |
| activity event | `ProgressEvent.completed == 0` 区分阶段；package 与 cell.package 重复 |
| ProbeRun | `evaluate_full` 后调用 `full_evaluation(vector)`；两个平行 dict 保存状态 |
| RunLogStore | Process Log、Journal、index 各自包含 POSIX/Windows 条件分支 |
| terminal | `_print_cell_report` 有 12 个可选 presentation 参数；live state 位于 TerminalPresenter |
| composition | CliContext 有 7 个可空 workflow/resource；SnapshotBuilder 缺 runner 时创建 SubprocessRunner |

R002 已执行 collect-only；本计划实施前另执行当前 checkout 的 targeted baseline，146 tests passed。

## 3. 测试策略

### 3.1 原则

- 每个切片先让 D010 指定 interface 的新测试失败，再改实现；
- interface 是测试面，不直接断言私有 dict、helper 或 Rich task 字段；
- 替换旧浅测试，不叠加保留对已删除 interface 的兼容测试；
- 等价输入用参数化；并发用 `Barrier`/`Event`，deadline 用 fake clock；
- 平台安全 adapter 直接测 interface，RunLogStore 只测产品行为；
- 每个切片至少执行最窄相关测试，跨模块切片再执行相邻回归；最终执行全量门禁。

### 3.2 Seam 与证据

| Seam | Red 条件 | Green / 回归证据 |
| --- | --- | --- |
| `cell_schedule_key` | identity monkeypatch 改变 order 或 source 委托 identity | `tests/test_scheduling.py` 独立 identity/order 测试 |
| `EnvironmentFactory.prepare` | string/nullable 组合仍可调用；三变体投影不一致 | `tests/test_environment.py` + baseline/check/search coordinator |
| `UvAdapter.install_editable` | exact 仍用 nullable selection 或丢 artifact/hash | `tests/test_uv_adapter.py` argv/selection matrix |
| `VerificationRunner.run` | deadline failure 由 Scheduler 构造；Journal 晚于 completion | `tests/test_verification.py` task identity、deadline、gate、order |
| Scheduler internal seam | sleep 才能触发并发/deadline；未提交 task 发 started | `tests/test_scheduling.py` fake clock + Barrier/Event |
| ActivityEvent Schema | stage 可携带 completed/failure；completed 用哨兵 | `tests/test_schemas.py` + terminal/verification event tests |
| SearchCoordinator | final Evaluation 需要二次 lookup；重复 full evaluation | `tests/test_search_coordinator.py` fast/dynamic/cache/lifecycle |
| SecureLogDirectory | 产品流程出现 platform branch；adapter 可越过 regular/identity guard | adapter 契约测试 + runlog/windows 回归 |
| TerminalPresenter | 同一 outcome 多处投影；自由参数可构造非法 card | `tests/test_terminal.py` public render/consume golden behavior |
| CliContext | workflow/resource 可空；main 分散 close | `tests/test_cli.py` 完整 fixture、handler、close 顺序 |
| SnapshotBuilder | production 隐式 runner；Git no-process 静默 fallback | `tests/test_snapshot.py` + workflow/editor snapshot 回归 |

## 4. 实施顺序

### 切片 001 — scheduling order 护栏

1. 新增 identity/order 独立测试；
2. `cell_schedule_key` 显式读取四字段；
3. 执行 scheduling、schemas/project 相关回归。

该切片不改事件或并发 interface，先建立后续重构护栏。

### 切片 002 — ResolutionRequest

1. 为 Highest/LowestDirect/ExactSelection 建立参数化 Red 测试；
2. 修改 EnvironmentFactory、UvOperations 与 UvAdapter；
3. 迁移 baseline、check、search、workflow 和测试 fake；
4. 删除 string resolution、managed_vector、nullable selection 兼容路径；
5. 执行 environment、uv、baseline、check、search coordinator 回归。

### 切片 003 — Runner、event union 与 Clock

1. 先新增 CellStageEvent/CellCompletedEvent Schema 拒绝非法字段的测试；
2. 把 Scheduler 改为 generic mechanism，deadline result/callback 由 Runner 提供；
3. 把 outcome → completion facts 移入 `verification.completion_outcome`；
4. Runner 内构造 deadline outcome、Journal gate 和 completed event；
5. 迁移所有 stage producer、Terminal consumer 与事件测试，删除 ProgressEvent；
6. 用 fake clock、Barrier/Event 替换 sleep 测试；
7. 执行 scheduling、verification、workflow、terminal、schema 回归。

### 切片 004 — ProbeRun 与 runner 生命周期

1. 新增 fast path / dynamic final 不调用二次 lookup 的行为测试；
2. `evaluate_full` 返回 frozen ProbeRun 并以单 dict 缓存；
3. `_FullVectorEvaluator` 只投影 evidence；SearchCoordinator 同时消费 result；
4. `_ProposalRunner` 改 context manager，删除 `full_evaluation` 与平行状态；
5. 执行 search algorithm/coordinator/workflow/report authority 回归。

### 切片 005 — SecureLogDirectory

1. 从现有安全原语建立 private interface 与 POSIX adapter 测试；
2. 用现有 WindowsRunDirectory 组合 Windows adapter 并补契约测试；
3. RunLogStore 在初始化时选择一次 adapter；
4. Process Log、Journal、index read/update/lookup 全部改走 adapter；
5. 删除 RunLogStore 平台判断与搬出的安全 helper；
6. 执行 process、runlog、windows_runlog、diagnose 回归和静态 branch 检查。

### 切片 006 — Terminal 私有视图

1. `CellPresentation` 只从 completion facts 建立合法 card；
2. `_print_cell_report` 收窄为单参数；所有 final renderer 走同一 completion projection；
3. 移出 Rich Progress、pending setup/outcome、task order 与 event lifecycle 到 `_live.py`；
4. TerminalPresenter 委托 consume/close，保留 render_X/exit code；
5. 执行全部 terminal、CLI、diagnose 和 workflow golden 回归。

### 切片 007 — Production composition 与 SnapshotBuilder

1. 测试 fixture 提供完整 workflow/resource adapter；
2. CliContext 所有字段必填，加入幂等 close/context manager；
3. 删除 handler 装配分支，main/build_context 集中清理；
4. SnapshotBuilder production constructor runner 必填，增加 `without_processes()`；
5. 迁移非 Git 测试构造；新增 Git no-process fail-closed 测试；
6. 执行 CLI、snapshot、editor 与所有 workflow 回归。

### 切片 008 — 所有者同步与最终门禁

1. D002 更新 module layout/interface；docs index 加 D010/P009 并把状态改为现行/已完成；
2. P009 回填每个切片过程、结论、命令与输出；
3. 静态审计 D010 §15 每一项；
4. 执行 Ruff、ty、Python 3.10–3.12 全量 pytest、build 与真实安装集成；
5. D010 改为现行，P009 改为已完成。

## 5. 变更控制

- 实施中新发现的取舍先记录在 §7；若改变规范行为，先修改 D010 再继续；
- 只调整私有 implementation 的细节记录在本 Plan，不扩写 Design；
- 不保留旧 ProgressEvent、Optional workflow、string resolution 或 `full_evaluation` 兼容层；
- 保留用户已有 R002 与 docs/README 修改，并在最终同步中只补所有者链接和状态；
- 不修改 D003/D005/D006/D007 的现行业务规则，若回归输出变化视为 defect。

## 6. 过程记录

### 基线

- **过程：** 已对照 R002 阅读 D002、D006、D008、D009 与相关源码/测试；确认 §2 八类基线事实。执行 8 个受影响测试文件的 targeted baseline。
- **结论：** R002 的完成标准均未在 `af10d0c` 实现，实施范围没有可删除项。
- **证据：** `uv run pytest --no-testmon tests/test_scheduling.py tests/test_environment.py tests/test_verification.py tests/test_search_coordinator.py tests/test_runlog.py tests/test_terminal.py tests/test_cli.py tests/test_snapshot.py -q` → 146 passed；`git diff --check -- docs` 通过。

### 切片 001 — scheduling order 护栏

- **状态：** 已完成
- **过程：** 先用 monkeypatch 改写 scheduling module 可见的 `cell_identity`，证明旧 `cell_schedule_key` 会随 identity 漂移；随后让 key 显式读取 package/target/python/extra，并删除 scheduling 对 identity owner 的 import。
- **结论：** compatibility identity 与 scheduling order 已分离；后续 Scheduler 重构有独立排序护栏。
- **证据：** Red：新增用例旧实现 1 failed；Green：`uv run pytest --no-testmon tests/test_scheduling.py tests/test_schemas.py -q` → 87 passed；`rg 'cell_identity' src/pf/scheduling.py` 无命中。

### 切片 002 — ResolutionRequest

- **状态：** 已完成
- **过程：** 在 `environment.py` 增加 frozen Highest/LowestDirect/ExactSelection runtime value；EnvironmentFactory 从单一 request 投影 Attempt resolution、managed vector、metadata materialization 和 graph check。UvOperations/UvAdapter 改为消费同一 request，exact request 自带 selection。迁移 baseline、check、search 及测试 adapter，删除 prepare/install 的 string、managed_vector 与 nullable selection 参数。
- **结论：** `highest + selection`、`lowest-direct + vector` 等组合已无法通过 interface 表达；exact probe 不再伪装成 highest，Attempt/install/Proposal 由同一对象导出。
- **证据：** Red：旧测试与调用点在新 interface 上 28 failed；Green：相关 environment/uv/baseline/check/search/evaluation（排除需要联网构建的真实安装用例）117 passed；Ruff 与 `ty check src` 通过；全量行为回归 590 passed，另 3 个真实安装用例因 sandbox 无法访问 PyPI 而失败，留待最终门禁联网复验。

### 切片 003 — Runner、event union 与 Clock

- **状态：** 已完成
- **过程：** 将 Scheduler 收窄为 generic task/concurrency/deadline callback mechanism，并注入 monotonic clock；VerificationRunner 内部创建 Scheduler，负责 deadline CellIndeterminate、唯一 completion projection、Journal merge/persist 以及持久化后再发布 CellCompletedEvent。ActivityEvent 拆为 CellStageEvent 与带判别 outcome 的 CellCompletedEvent，迁移 environment/evaluation producer、workflow、Terminal consumer 和全部旧事件测试；Scheduler 并发测试改用 Barrier，deadline 测试改用离散 fake clock，不再 sleep。
- **结论：** Scheduler 已不导入 failure/evaluation/report 领域；未提交 task 不发布 started，且 public activity contract 无 completed==0 哨兵、重复 package 或自由组合的 message/detail/failure 字段。诊断可用性仍只在 Journal 成功持久化后发布。
- **证据：** `rg 'ProgressEvent' src tests` 无命中；`ruff check src tests` 与 `ty check src` 通过；scheduling/verification/schemas/environment/evaluation/terminal/check/search-workflow/smoke 回归 206 passed，另 1 个真实安装用例因 sandbox 无法取得 uv_build 失败（与切片 002 同一联网限制）。

### 切片 004 — ProbeRun

- **状态：** 已完成
- **过程：** 增加 frozen `ProbeRun(evidence, evaluation)`；`_ProposalRunner.evaluate_full` 对每个 vector key 缓存并返回单一 ProbeRun，prepare failure/cache conflict 显式携带 `evaluation=None`。`_FullVectorEvaluator` 只投影 evidence，SearchCoordinator 的 fast path 与 dynamic final 从同一 run 同时取得 evidence/evaluation；runner 改为 context manager 集中关闭。
- **结论：** final Evaluation 不再依赖“先 evaluate 再 full_evaluation lookup”的调用历史；平行 `_evaluations`/`_full_evidence_by_key` 状态和兼容 lookup 已删除，同一 full vector context 最多执行一次。
- **证据：** search/search-coordinator/search-workflow/report 回归 53 passed；fast 与 dynamic final 测试分别断言目标 vector 的 FullOperations 只调用一次；Ruff 与 `ty check src` 通过；`rg 'full_evaluation|_full_evidence_by_key|_evaluations' src/pf/search.py` 无命中（仅 `require_full_evaluation_contract` 名称含该子串）。

### 切片 005 — SecureLogDirectory

- **状态：** 已完成
- **过程：** 新增私有 `_secure_runlog.py`，定义 SecureLogDirectory 及 POSIX/Windows 两个 adapter；POSIX adapter 接管逐级 dir_fd/no-follow、inode identity、0600 原子写和 regular/bounded read，Windows adapter 组合 WindowsRunDirectory 的 reparse/DACL/volume guard。RunLogStore 初始化时只选择一次 adapter，Process Log、Journal、latest/index update、offline read 与 locator resolve 全部改走 seam；产品层只保留格式、解析、association 与 reference 语义。
- **结论：** 平台变化已止于安全目录原语；RunLogStore 产品流程不再理解 os.name、dir_fd、WindowsRunDirectory 或 capability branch，现有 Process Log/Journal/diagnosis 行为保持。
- **证据：** 新增 POSIX/Windows adapter 直接契约与替换目录 fail-closed 测试；secure-runlog/runlog/process/windows/diagnose/search/report-workflow 回归 80 passed；Ruff、`ty check src`、`git diff --check` 通过；`rg 'os\.name|_supports_|dir_fd|WindowsRunDirectory' src/pf/runlog.py` 无命中。

### 切片 006 — Terminal 私有视图

- **状态：** 已完成
- **过程：** 新增 `_presentation.py` 的 frozen CellPresentation；它只从 CellCompletedEvent 或 `completion_outcome(result)` 建立 cell kind/failures/diagnostics/process/stage/role/diagnose facts，并合并去重 SearchFailureEvent。新增 `_live.py` 的 LiveVerificationView，迁入 Rich Progress、setup/status、cell/stage/overall task、elapsed、search buffer 与 freeze 生命周期。TerminalPresenter 只委托 consume/close，final check/smoke/search renderer 也统一经 CellPresentation；`_print_cell_report` 收窄为单一参数。删除直接断言 Rich task 私有状态的浅测试，以 public consume/close TTY 输出回归替换。
- **结论：** 非法 cell card 参数组合已无法从 TerminalPresenter 构造；live 与 final 结果共享唯一领域 completion projection 和唯一 presentation 入口，D006 文案、颜色、布局、通道与退出码未改变。
- **证据：** terminal 单测 54 passed；terminal/CLI/check/smoke/search/verification/diagnose 相邻回归 128 passed、1 个联网真实安装用例显式 deselected；Ruff 与 `ty check src` 通过；`_print_cell_report` 仅接 CellPresentation，TerminalPresenter 文件无 Rich Progress/live state 字段命中。

### 切片 007 — Production composition

- **状态：** 已完成
- **过程：** CliContext 的全部 command workflow、TerminalPresenter 与 RunLogStore 改为必填，删除 handler 的 assembled/None 分支；context 增加幂等 close/context-manager，唯一按 presenter→logs 顺序关闭。build_context 抽出完整 assembly，并在中途失败时清理已创建资源；main 只关闭 context。SnapshotBuilder production constructor 改为显式 ProcessRunner，新增 `without_processes()`；该构造在 Git root 明确 fail closed，所有非 Git 测试显式迁移，Git/production 测试显式注入 runner。
- **结论：** production object graph 不再能表达缺 workflow/resource；handler 不承担装配校验；外部 Git 进程能力不再由 SnapshotBuilder 隐式创建，资源所有权只有 CliContext 一个关闭入口。
- **证据：** CLI/snapshot/editor 定向 42 passed；全量排除 3 个已知联网真实安装用例后 582 passed；Ruff 与 `ty check src` 通过；静态无 `workflow is not assembled`、Optional workflow/resource、`SnapshotBuilder()` 调用。新增完整构造、幂等关闭顺序、assembly failure cleanup、Git no-process fail-closed 测试。

### 切片 008 — 文档与门禁

- **状态：** 已完成
- **过程：** 把 D002 的 module layout、activity Schema、CliContext/SnapshotBuilder、ResolutionRequest、ProbeRun、Scheduler、SecureLogDirectory 与 terminal 私有视图同步为已落地接口；校正 D010 的章节引用和七 workflow/八命令表述；更新文档索引与状态。逐项执行 D010 §15 静态审计，并为 exact selection 的排序/唯一边界补充 public prepare 参数化测试；再执行 Ruff、ty、Python 3.10–3.12 隔离全量测试、宿主全量测试、真实安装集成及 sdist/wheel 构建。sandbox 首轮准确暴露 3 个需要 PyPI 构建依赖的用例，联网复验后全部通过。
- **结论：** D002 与实现重新一致，D010 §15 的全部架构完成标准均有实现与测试/静态证据；没有遗留兼容层或待执行门禁，D010 可转为现行、P009 可关闭。
- **证据：** Ruff、ty、`git diff --check` 全部通过；Python 3.10、3.11、3.12 隔离环境各 587 passed；宿主环境 587 passed；真实安装集成 3 passed；`uv build` 成功生成 sdist 与 wheel。静态审计无 `ProgressEvent`、Scheduler 领域 import、RunLogStore 平台分支、旧 full-evaluation lookup、handler assembly branch 或零参 `SnapshotBuilder()`。

## 7. 实施决策

当前没有超出 D010 §17 的新增取舍。

## 8. 最终完成矩阵

| D010 §15 要求 | 实现证据 | 测试/静态证据 | 状态 |
| --- | --- | --- | --- |
| identity/order 独立 | `src/pf/scheduling.py:cell_schedule_key` 显式字段 | scheduling monkeypatch 护栏；87 passed；静态无 identity import | 已完成 |
| ResolutionRequest 三变体闭环 | `environment.py` 三变体；Environment/Uv/调用方只接 request | 117 targeted passed；最终 environment 21 passed（含 selection 排序/重复边界）；Ruff/ty 通过 | 已完成 |
| Scheduler 无领域知识 | generic ScheduledCellTask + callbacks；领域投影归 VerificationRunner | scheduling 无 failure/evaluation/report import；Ruff/ty 通过 | 已完成 |
| deadline 无虚假 started | VerificationRunner 构造 deadline fallback；Scheduler 仅在 submit 后回调 started | fake clock 证明仅首 task 启动、第二 task 直接 deadline completion | 已完成 |
| 判别 activity event / Journal gate | CellStageEvent / CellCompletedEvent；Runner completion gate | schema 拒绝 sentinel 字段；Journal→completion 时序测试；静态无 ProgressEvent | 已完成 |
| fake clock + Barrier/Event | Scheduler 注入 monotonic | scheduling Barrier/fake-clock 测试，无 sleep | 已完成 |
| ProbeRun 无调用历史约束 | ProbeRun 单缓存；SearchCoordinator 同时消费 evidence/evaluation；runner context manager | 53 passed；fast/dynamic final full vector 各只执行一次；静态无 lookup/平行 dict | 已完成 |
| SecureLogDirectory 双 adapter | `_secure_runlog.py` protocol + POSIX/Windows adapter；RunLogStore 只消费 seam | adapter 直接契约、替换目录/regular guard与 80 项产品回归；RunLogStore 静态无平台分支 | 已完成 |
| Terminal 单一 presentation 入口 | CellPresentation + LiveVerificationView；final/live 共用 completion_outcome；单参 `_print_cell_report` | 54 terminal + 128 adjacent passed；静态无 presenter Rich live state | 已完成 |
| 完整 CliContext 与统一 close | 全字段必填；context manager/幂等 close；build failure cleanup；main 只关 context | CLI 构造/close/cleanup 测试；静态无 handler assembled 分支 | 已完成 |
| SnapshotBuilder 无隐藏 runner | 显式 ProcessRunner + `without_processes()`；Git no-process fail closed | snapshot/editor/workflow 回归；静态无零参 constructor | 已完成 |
| 全量门禁与文档同步 | D002/索引状态已同步；D010/P009 转为现行/已完成 | Ruff/ty/diff 通过；3.10–3.12 各 587 passed；host 587 passed；integration 3 passed；build 成功 | 已完成 |

## 9. 最终门禁

最终执行结果：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests
  All checks passed!
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ty check src
  All checks passed!
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.10 --group test pytest --no-testmon -q
  587 passed in 10.32s
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 --group test pytest --no-testmon -q
  587 passed in 9.93s
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.12 --group test pytest --no-testmon -q
  587 passed in 10.38s
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon -q
  587 passed in 9.97s
UV_CACHE_DIR=/tmp/pf-uv-cache uv build
  Successfully built dist/pf-0.1.0.tar.gz
  Successfully built dist/pf-0.1.0-py3-none-any.whl
```

真实安装集成：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon \
  tests/test_check.py::TestCompatibilityChecker::test_check_passes_a_minimal_local_package \
  tests/test_end_to_end.py -q
  3 passed in 3.64s
```

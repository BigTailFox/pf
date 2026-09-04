# P030 — D024 FailedCaseSet 拒绝预言实施计划

- **状态：** 已完成、已归档
- **开始日期：** 2026-09-04
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D024](../designs/D024-pf-failed-case-pruning.md)
- **评审来源：** [R008](../../reviews/R008-pf-search-performance-review.md) §1、§3、§4.6、§5
- **实施基线：** `9903415`（`docs: design failed-case pruning`）；工作树中的 D024 为接受目标
- **实现提交：** 工作树未提交；本 Plan 与 D024 在同一完成变更中归档

本文在生产代码修改前建立 D024 的实施顺序、interface/ownership 迁移、测试矩阵与证据槽。每次实质行动后
在 §8 记录行动、结论、精确命令与结果；完成标准只来自 D024 §15，不以局部绿色、collection、单一 Python
版本或静态扫描替代验收。§11 两个切片的 wall-clock / 命中率数据是独立证据槽：协议测试不得描述为已取得
第二段收益。

## 1. 目标与边界

本轮完整实现 D024：

- 所有 direct pytest 原样保留用户 argv，并在第一个字面 `--` 前（无 `--` 时在末尾）注入生效的
  PF-owned `--maxfail=1` 与 `-o cache_dir=<invocation-temp>`；generic command 不变；
- 在 `ConfiguredVerifier` 背后实现 failed-set / 原命令两阶段、private pruning plugin 的
  `pytest_cmdline_main` pre-yield `Config.args` 替换、以及 D013 分阶段 collected/failed 可选 artifact；
- `_ProposalRunner` 按 `(Verification Run, Cell, active dependency)` 拥有 FailedCaseSet；无坐标路径、
  smoke/check/baseline、witness、generic command 不读写集合；
- PASS 只来自原命令阶段 `NormalExit(0)`；已证明有效 requested-set collection 的任意 normal nonzero
  按 D005 形成 Rejection；不完整终态不回退；
- 不新增 identity、portable authority、Evaluation context、报告字段、配置或 CLI 开关；
- 用 `ConfiguredVerifier.run`、`SearchCoordinator.search`、smoke/check 公开 seam 覆盖 §15；
- 记录 §11 两个切片的可复现命令与结果；owner 文档同步后归档 D024/P030。

不改变 D003 搜索算法、D005 disposition 表、D014 wire、evaluation policy preimage 字段，也不引入
compatibility layer、pruning opt-out 或 failed-set + complement 冒充 PASS。

## 2. 基线事实与目标差距

| 切面 | `9903415` 当前事实 | D024 目标 |
| --- | --- | --- |
| argv | direct pytest 只在 prefix 后插入 `-p observer`；不附加 `--maxfail` / `cache_dir` | 保留全部用户 token；末位 PF overlay last-wins；独立 invocation `cache_dir` |
| verifier request/run | `VerifierRequest` 无 nodeids；`VerifierRun` 只有 authoritative + excluded diagnostics | 增加 runtime-only `failed_case_nodeids` / `failed_case_additions` |
| 评价路径 | `RuntimeEvaluator` 每次一次 `verifier.run`；`_ProposalRunner` 无集合 | 有坐标时传入集合；最多两段 child process；只合并 additions |
| observer | mandatory summary + UI detail 首个 nodeid/总数；不写 collection/failed 列表 | 按阶段写 collected 或 failed 可选 projection；UI/summary 不变 |
| pruning plugin | 不存在 | 独立 plugin，`hookwrapper=True, trylast=True` 替换 `Config.args` |
| 集合 | 不存在 | `_ProposalRunner` 唯一 writer；跨坐标/Cell/Run 隔离 |
| identity | `pf:policy:v1` 含 test command/cwd/timeout；`configured-verifier-terminal-v1` | 不新增 pruning 字段；digest 不因集合成员或分段次数改变 |
| 文档 | D001/D003 仍写 partial tests 非目标；R008 称 D024 待接受 | §12 owner 归并后归档 |

## 3. Interface 与 ownership 迁移

1. `VerifierRequest.failed_case_nodeids: tuple[str, ...] = ()`；空 tuple 表示只跑原命令。generic
   command 收到非空 input 是调用方 invariant failure。
2. `VerifierRun.failed_case_additions` 与 `RuntimeEvaluationRun.failed_case_additions` 均为
   runtime-only、`exclude=True`；不进入 Schema wire、FailureRecord 或 report。
3. `ConfiguredVerifier` 拥有 direct-pytest 识别、用户 argv 原样保留、固定 overlay、`cache_dir`、
   requested-nodeid 私有文件、pruning plugin、两阶段回退、collection 证明与 adopted terminal。
4. `RuntimeEvaluator` 把不可变 nodeid tuple 传给 verifier，并原样上送 additions；不解释集合语义；
   一次逻辑评价持有一个 `test` permit 贯穿两段。
5. `_ProposalRunner` 唯一拥有 `failed_cases_by_active_dependency`；只在带 `active_dependency` 的
   search runtime probe/promotion 传入当前成员并合并 additions。
6. `CoordinateSearch` 继续只消费 Probe evidence。
7. D013 observer 增加分阶段 collected/failed 可选 artifact、xdist 合并与
   `PF_PYTEST_PRUNE_*` 嵌套删除；observer 仍不修改 selection。private pruning plugin 是独立 wheel
   资源 `pf/_pytest_pruning.py`，不属于 D013。

## 4. 实施顺序

### 切片 001 — argv overlay 与 invocation-local `cache_dir`

1. 用 `ConfiguredVerifier.run` 锁定：用户 token 原样保留（含 `-x` / `--exitfirst` / `--maxfail` /
   `-o cache_dir` / 畸形参数 / 字面 `--`）；PF `--maxfail=1` 与 `-o cache_dir=<temp>` 插在第一个
   `--` 之前或末尾；generic command 不变。
2. 实现 overlay 与独立 `cache_dir` 临时目录；创建/cleanup 失败为命令级 `InfrastructureError`。
3. 用真实 pytest 证明用户已写 `--maxfail` / `-o cache_dir` 时末位 overlay 生效，且进程结束后
   临时目录删除。
4. smoke/check/baseline 路径因共用 `ConfiguredVerifier` 自动获得 overlay，本切片用 verifier seam
   证明，公开 operation 回归放在切片 005。

### 切片 002 — D013 collected/failed projection

1. observer 按阶段写入同一私有协议的不同 projection：原命令只写 setup/call/teardown `failed`；
   failed-set 写 collection 完成与最终 `collected`。
2. serial/controller 以 `pytest_collection_finish` 后的 `session.items` 为 collected 权威；worker
   collected 只做防御；failed 作 set union 后按 nodeid 排序。
3. 越界、非法、冲突或无法证明本次 invocation 时丢弃整个可选 projection，不截断。
4. 嵌套 invocation 删除 `PF_PYTEST_PRUNE_REQUEST` / `PF_PYTEST_PRUNE_NONCE` 及全部既有 PF 私有变量。
5. UI detail 与 mandatory summary 保持不变；公开测试仍走 `ConfiguredVerifier.run`。

### 切片 003 — pruning plugin 与两阶段 `ConfiguredVerifier`

1. 增加 `VerifierRequest.failed_case_nodeids` / `VerifierRun.failed_case_additions`。
2. 非空 nodeids 时先跑 failed-set：写 request 文件、注入 pruning plugin、读 collection artifact。
3. `SelectionApplied` 且 adopted terminal 为 normal nonzero → 直接 Rejection，空 additions，一个进程。
4. failed-set `NormalExit(0)` 或 collection 无法证明 → 丢弃该 normal terminal，跑原命令。
5. timeout/signal/start failure/typed unavailable → 采用该终态，不回退。
6. 原命令 `VerifierRejected` 才返回合法 failed additions；资格不依赖具体 exit code。
7. 正向证明 `SelectionApplied` 时最终 items 来自 requested set。

### 切片 004 — FailedCaseSet 与 RuntimeEvaluator / `_ProposalRunner`

1. `RuntimeEvaluator.evaluate(..., failed_case_nodeids=())` 原样传递并上送 additions。
2. `_ProposalRunner` 按 active dependency 维护有序唯一集合；无坐标 / `evaluate_full` 传空 tuple。
3. 先到成员保留；新成员规范化、去重、排序后追加未出现项；无数量上限。
4. Evaluation cache 命中不重跑，也不因后来增长的集合失效。
5. 一次逻辑评价持有一个 test permit；每段完整 `test-timeout`。

### 切片 005 — 公开 seam 测试矩阵

1. `ConfiguredVerifier.run`：AC1–6、8–9、12 的 verifier 部分，含 pytest 6.2.5–9.1.1 资格、xdist
   `--dist load`、`--lf`/`--ff`/`--sw` 对照、plugin option / ini / `PYTEST_ADDOPTS` / `--` /
   rootdir/初始 conftest。
2. `SearchCoordinator.search`：同坐标复用、跨坐标隔离、无坐标 `evaluate_full`、PASS/Rejection、
   进程数、cache。
3. smoke/check 公开 operation：一次原命令、无 FailedCaseSet、direct pytest overlay。
4. identity：policy preimage 无 pruning 字段；固定配置 digest 不因集合成员/分段改变。
5. 扫描：无 `PruningObservation`、无公开 selector result、无 CLI/config pruning 开关。

### 切片 006 — §11 证据、owner 归并、归档

1. 在当前 HEAD、固定源与相同 Cell 上记录 early-exit+`cache_dir` wall-clock，以及 FailedCaseSet
   命中/回退/PASS 双进程成本、`--lf`/`--ff` 对照、资格矩阵与 report 语义差异。
2. 按 D024 §12 归并 D001/D002/D003/D004/D005/D013/R008 与 docs index/CONTEXT；无 schema 结构
   变化时记录检查结果。
3. 逐条审计 §15；通过后将 D024/P030 同步归档。

## 5. Acceptance / evidence matrix

| D024 §15 | 实施切片 | 直接证据 | 状态 |
| --- | --- | --- | --- |
| AC1 原命令 Rejection 增加合法 setup/call/teardown nodeid | 002、003、004 | `tests/test_pytest_pruning.py::test_original_command_adds_failed_nodeids`；`SearchCoordinator.search` 同坐标后续 | 通过 |
| AC2 failed-set 替换 `Config.args`、不进 OS argv、有效 collection 时单进程 Rejection | 003、005 | `test_failed_set_rejects_without_running_the_original_command`；`test_failed_set_requested_nodeids_do_not_enter_argv`；`test_failed_set_selection_only_collects_requested_nodeids` | 通过 |
| AC3 failed-set PASS 后必须原命令；只有原命令 `NormalExit(0)` 形成 PASS | 003、004 | `test_failed_set_pass_requires_original_command`；search PASS 路径 cache | 通过 |
| AC4 empty/collection-failed/unexpected/duplicate/missing/invalid 回退原命令 | 003、005 | `-k` empty；import collection error；parametrize；duplicate items；artifact mutate；worker extra | 通过 |
| AC5 不完整终态 Indeterminate、不回退、不更新集合 | 003、004 | timeout/signal/start/unavailable 单进程 | 通过 |
| AC6 normal exit 1/2/3/4/5 统一 `VerifierRejected` | 001、003 | `test_normal_nonzero_is_rejected_for_failed_set_and_original` | 通过 |
| AC7 跨坐标/Cell/Run 隔离；无坐标不读写；cache 不因集合变大失效 | 004、005 | `test_search_isolates_failed_cases_across_coordinates_and_runs`；passing vector 只评价一次 | 通过 |
| AC8 用户 argv 原样 + 末位 overlay；generic 不变；无开关 | 001、005 | `test_configured_verifier` overlay；smoke/check 公开 operation；CLI/config 扫描 | 通过 |
| AC9 独立 `cache_dir`；`--lf`/`--ff`/`--sw` 语义等于单次原命令 | 001、003、005 | overlay 集成；`test_lastfailed_flags_match_a_single_original_command` | 通过 |
| AC10 runtime-only additions；无公开 selector；report 不含 requested/collected | 003、005 | schema exclude；`test_portable_schemas_omit_pruning_context` | 通过 |
| AC11 policy identity 不新增字段 | 005 | `test_environment_factory_materializes_an_isolated_proposal` exact preimage | 通过 |
| AC12 pytest 6.2.5–9.1.1 与 xdist controller/worker | 002、003、005 | `scripts/qualify_pytest_pruning.py` 3×7 全通过；xdist 无 controller collection 时回退 | 通过 |
| AC13 §11 两切片证据 + §12 owner 归档 | 006 | `scripts/measure_d024_pruning.py`；owner 归并后本文件与 D024 同步归档 | 通过 |

## 6. 验证命令与证据槽

所有 pytest 使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`；显式 full regression 使用 `--no-testmon`。

| Gate | 计划命令 | 结果 |
| --- | --- | --- |
| focused overlay/verifier | `uv run --python 3.10 pytest --no-testmon tests/test_configured_verifier.py tests/test_pytest_observer_protocol.py tests/test_pytest_progress.py -q` | 与 pruning/search/eval 合并：153 passed（3.10 focused 包） |
| focused observer/pruning | `uv run --python 3.10 pytest --no-testmon tests/test_pytest_observer_integration.py tests/test_pytest_observer_plugin.py tests/test_pytest_pruning.py -q` | `tests/test_pytest_pruning.py` 39 passed |
| focused search/eval | `uv run --python 3.10 pytest --no-testmon tests/test_evaluation.py tests/test_search.py tests/test_search_coordinator.py tests/test_check.py tests/test_smoke.py -q` | 含在 153 passed focused 包；smoke live-identity 属 TTY 宽环境，见 §8 |
| identity/report | `uv run --python 3.10 pytest --no-testmon tests/test_environment.py tests/test_report.py tests/test_report_schema.py -q` | exact preimage 无 pruning 字段；schema `--check` 无漂移 |
| pytest pruning qualification | `uv run --python 3.10 python scripts/qualify_pytest_pruning.py` | `all_profiles_expected=true`；3.10/3.11/3.12 × pytest 6.2.5–9.1.1 |
| pytest pruning xdist | `uv run --python 3.10 python scripts/qualify_pytest_pruning.py --current-plugins --pytest-version 9.1.1` | 3.10/3.11/3.12 + pytest-xdist 3.8.0：worker-only collected → 回退原命令，`expected=true` |
| Ruff | `uv run --python 3.12 ruff check .` | 切片期间对改动文件 `All checks passed` |
| ty | `uv run --python 3.12 ty check` | `All checks passed!` |
| 3.10 full + coverage | `uv run --python 3.10 pytest --no-testmon --cov=pf --cov-report=term-missing -q` | 1496 passed；coverage 90.17%；33 failed 全部为 TTY `80 <= 56` 宽断言（`test_terminal`/`test_cli`/`test_diagnose`/`test_smoke` live），与 pruning 无关 |
| 3.11 full | `uv run --python 3.11 pytest --no-testmon -q --ignore=tests/test_terminal.py -k 'not (common_widths or live_baseline_identity)'` | 1396 passed, 10 deselected |
| 3.12 full | 同上 `--python 3.12` | 1396 passed, 10 deselected |
| build | `uv build` | `dist/pf-0.1.0.tar.gz` 与 wheel；wheel 含 `pf/_pytest_pruning.py` |
| generated | `uv run --python 3.12 python scripts/generate_report_schema.py --check` | 通过，无 schema 结构变化 |
| docs links | Markdown 相对链接存在性 | `files=69 checked=458 missing=0` |
| deletion/ownership | 无 pruning CLI/config；无 `PruningObservation`；identity 无 pruning 字段 | CLI help/TestConfig 扫描通过；`PruningObservation` 不存在 |
| §11 slice 1 early-exit | `uv run --python 3.10 python scripts/measure_d024_pruning.py --delay 0.2 --repeat 3` | raw pytest median 0.957s；`--maxfail=1` 0.351s；ConfiguredVerifier original 0.316s |
| §11 slice 2 FailedCaseSet | 同上脚本 + 协议测试 | failed-set hit 0.316s（与 overlay 原命令同量级，因首败已是第一例）；passing nodeid 后原命令 0.432s；协议测试覆盖命中/回退/`--lf`/`--ff`/`--sw`/pytest 矩阵。**不得描述为已证实的第二段 wall-clock 收益。** |

## 7. 决策、偏差与停止条件

- 2026-09-04：用户要求实现 D024，视为接受该 Design 完整目标并授权实施；按 AGENTS 要求先建立本
  Plan，再编辑 production code。
- 若任一受支持 pytest 版本不能在公开 `pytest_cmdline_main` 时点取得 D024 §6.2 顺序保证，不得以
  自写 argv parser 或 post-collection 过滤降级；必须在本节记录可复现证据并重新收敛资格。
- 任何 identity/wire/disposition 变化都必须先修订并重新接受 D024，不能在本 Plan 中暗改。
- 2026-09-04：pytest-xdist controller 的 `pytest_collection` 禁止收集 items，controller 无
  `pytest_collection_finish` session.items。Config.args 替换在 controller 与 worker 上均成立；
  缺少 controller collected projection 时回退原命令，不用 worker 投影单独授权 Rejection。这是
  D024 §6.4 在 xdist 现实行为下的保守解释，不是 argv parser / post-collection 降级。

## 8. 行动、结论与证据日志

### 2026-09-04 — 接受与建立实施基线

- 行动：核对 HEAD/worktree、D024、R008、文档治理、ConfiguredVerifier、observer、RuntimeEvaluator、
  `_ProposalRunner`、SearchCoordinator 与相关测试关键字。
- 命令：`git status --short --branch`；`git log -5 --oneline --decorate`；针对 Design、源码、测试与
  owner 文档的读取。
- 结果：HEAD `9903415`；工作树仅 D024 相对已提交稿有未提交修订（即本轮接受目标）。生产仍无
  FailedCaseSet、argv overlay、pruning plugin 或 collected/failed projection。
- 结论：D024 已接受，且本 Plan 已在任何 production 修改前建立。下一步执行切片 001。

### 2026-09-04 — 切片 001–006 完成与归档

- 行动：落地 argv overlay、`cache_dir`、D013 collected/failed artifact、pruning plugin、两阶段
  `ConfiguredVerifier`、`_ProposalRunner` FailedCaseSet、公开 seam 测试、pytest 6.2.5–9.1.1 资格、
  §11 测量、D001/D002/D003/D004/D005/D013/R008 owner 归并。
- 命令与结果：见 §6。`scripts/qualify_pytest_pruning.py` 21 profile 全通过；
  `scripts/measure_d024_pruning.py --delay 0.2 --repeat 3` 记录 early-exit wall-clock。
- 偏差：xdist controller 不收集 items，failed-set 回退原命令；3.10 全量 33 个 TTY 宽测试因
  Rich 默认 80 列失败，与 pruning 无关，3.11/3.12 在排除这些用例后 1396 passed。
- 结论：D024 §15 均有直接证据。稳定规则已写入现行 owner。本 Plan 与 D024 同步归档。
  FailedCaseSet 第二段 wall-clock 收益未证实，R008 保持开放。

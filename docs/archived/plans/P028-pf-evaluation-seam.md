# P028 — D022 PF 评价 seam 收敛与 SearchCoordinator 测试面实施计划（归档）

- **状态：** 已完成并归档
- **开始日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D022](../designs/D022-pf-evaluation-seam.md)
- **评审来源：** [R005](../reviews/R005-pf-module-depth-review.md) §6、§8、§11.4
- **实施基线：** `010e048`（`docs: archive verification run request design`）

本文在生产代码修改前建立 D022 的实施顺序、interface/ownership 迁移、测试矩阵与证据槽。每次实质
行动后在 §8 记录行动、结论、精确命令与结果；完成标准只来自 D022 §8，不以局部绿色、collection、
单一 Python 版本、coverage 百分比或静态扫描替代验收。

## 1. 目标与边界

本轮完整实现 D022：

- 删除 Check、Highest 与 Search 的九份 consumer-specific environment/static/full Protocol，让三个产品
  module 直接依赖 composition root 注入的同一 `EnvironmentFactory`、`StaticEvaluator`、
  `RuntimeEvaluator` graph；
- 将 `CompatibilityChecker` 移入 `pf.check`，保持其 public `check(...)`、two-phase、Failure、event 与
  close 语义；
- 删除 Search 的 `CandidateOperations`、`HighestOperations`，直接依赖 `CandidateBuilder`、
  `HighestVersionVerifier` 与现有 concrete `CoordinateSearch`；
- 让所有测试只经 `EnvironmentFactory.prepare(...)` 获得 `PreparedEnvironment`，并把可变行为下沉到
  uv、candidate provider、ty/process、configured verifier、runtime witness 与 consumer sink；
- 用真实 evaluator/highest/candidate/coordinate graph 重写 `SearchCoordinator` tests，只观察
  `search(...)` 的 public result/evidence/events 和 lower-adapter records；
- 保持 Check、Highest、Search、CoordinateSearch、EnvironmentFactory、evaluation、FailurePolicy、Runner、
  report 与 terminal 的既有 ownership，不改变 CLI、numeric exit、Schema 1、identity、wire 或展示；
- 完成时把稳定规则归并 D002/D003/D004，核对 D008/D012，更新 R005/索引并同步归档 D022/P028。

不增加 facade、共享 evaluator Protocol、parameter bundle、factory、locator、service registry、DI framework、
alias、dual path、compatibility adapter 或 PreparedEnvironment test backdoor；不实施 R006 已接管的
terminal result-card 或其他 CLI 候选。

## 2. 基线事实与目标差距

| 切面 | `010e048` 当前事实 | D022 目标 |
| --- | --- | --- |
| product evaluator seam | workflow、baseline、search 各自声明 env/static/full Protocol，共九份 | 三个产品 module 直接依赖同一 concrete evaluator graph |
| Check owner | `CompatibilityChecker` 与 command workflow 同在 `pf.workflow` | checker 位于 `pf.check`；workflow/CLI 直接导入，不 re-export |
| Search products | Search 另有 `CandidateOperations`、`HighestOperations`；`CoordinateSearch` 已 concrete | 直接依赖 CandidateBuilder、HighestVersionVerifier、CoordinateSearch |
| lower seam | uv、candidate provider、ty、verifier、witness、process 与 consumer Protocol 已存在 | 保留为唯一 scripted test substitute surface |
| Prepared lifecycle | src 只有 EnvironmentFactory 构造成功值；五个 test module 共七处直接构造 | tests 全库零直接构造；成功值都来自真实 prepare/cleanup lifecycle |
| product tests | baseline/check/search coordinator 以 product-level env/static/full/highest/coordinate fake 驱动 | 真实 module graph + lower adapters；只观察 public outcome/evidence/events |
| algorithm tests | `test_search.py` 通过 VectorEvaluator 独占 CoordinateSearch 算法矩阵 | ownership 保持，不在 coordinator tests 重复或替换 CoordinateSearch |
| fake-only behavior | product modules防御 Factory contract 不允许的裸 ToolFailure/missing Attempt | 删除对应 branches 与三个 obsolete negative tests |

## 3. Interface 与 ownership 迁移

1. 新建 `pf.check` 并原样承接 `CompatibilityChecker` 的 check、outcome/Failure formation 与 two-phase close；
   `pf.workflow` 仅消费 checker，不 re-export。
2. `CompatibilityChecker`、`HighestVersionVerifier`、`SearchCoordinator` 与 `_ProposalRunner` 的构造类型改为
   `EnvironmentFactory`、`StaticEvaluator`、`RuntimeEvaluator`；三个 caller显式接收同一 StaticEvaluator，
   不从 RuntimeEvaluator反查。
3. SearchCoordinator 与 Candidate path直接接收 `CandidateBuilder`、`HighestVersionVerifier` 和
   `CoordinateSearch`；保留 `SearchDiagnosticConsumer`、`SearchActivityConsumer` 与 lower adapters。
4. 删除 consumer-specific Protocol及其 typing imports；删除裸 ToolFailure/missing Attempt 防御路径；
   Factory、static、runtime evaluator 的 error/safety authority不搬入产品 owner。
5. 在一处 test-private support 汇集 scripted uv/candidate/ty/verifier/witness adapters及 records；support
   只装配现有 concrete modules，不暴露 prepare/capture/evaluate/search 转发或预制领域结果。
6. Environment/Static/Runtime、Highest、Check、SearchCoordinator 分别通过 public seam 证明 D022 §5.3矩阵；
   final/probe/region/failure closure继续使用 D003/D004/D005/D014 public records。

## 4. 实施顺序

### 切片 001 — Test-private lower-adapter support 与 PreparedEnvironment lifecycle

1. 盘点并统一 `test_environment` / `test_evaluation` 现有成功 uv、ty、verifier、witness adapter能力，建立一处
   private support；不得复制 production composition root或定义高层 operations fake；
2. 先把 `test_evaluation.py`、`test_static_transition.py` 的 direct constructor改为通过真实临时目录、
   SourceSnapshot、SourcePlan与 `EnvironmentFactory.prepare(...)`取得成功值并显式 close；
3. 保持 EnvironmentFactory highest/lowest/exact、plan/source/graph failure、Attempt与cleanup测试在
   `test_environment.py`，补足 assembly 所需的 lower record而不泄露 evaluator private state；
4. 扫描确认所有成功 PreparedEnvironment都由 Factory产生，先记录仍待迁移的 baseline/check/coordinator
   调用点，再在切片004清零。

### 切片 002 — Concrete evaluator seam 与 CompatibilityChecker owner

1. 以现有 public behavior tests锁定 Highest capture/full reuse/all-path close与 Check highest-close-before-lowest、
   lowest full、role/disposition/event语义；
2. 新建 `pf.check`、移动 `CompatibilityChecker`，更新 workflow、CLI与 tests直接导入；删除 workflow re-export；
3. 从 workflow/baseline删除六份 env/static/full Protocol，构造器直接使用三个 concrete evaluator类型；
4. 删除 Factory closed outcome不允许的裸 ToolFailure/missing Attempt branches及其 obsolete highest test；
5. 用 lower adapters + real modules替换 baseline/check product-level fakes与 direct PreparedEnvironment构造。

### 切片 003 — Search concrete dependencies 与真实 graph

1. 从 search删除 env/static/full、CandidateOperations、HighestOperations五份 Protocol；直接导入并注入
   EnvironmentFactory、StaticEvaluator、RuntimeEvaluator、CandidateBuilder、HighestVersionVerifier；
2. 保持 `_ProposalRunner` private cache/region与 `CoordinateSearch` algorithm ownership，只替换依赖类型和
   unreachable Factory outcome branches；
3. composition root继续只创建一份 evaluator graph并传给 Check、Highest、Search；不增加 command-specific
   evaluator、bundle、factory或 locator；
4. 运行 focused type/tests，先闭合 production interface与 import-cycle证据；若触发 D022 §7停止条件，先在
   §7/§8记录而不增加兼容层。

### 切片 004 — SearchCoordinator public-behavior tests 原地替换

1. 用切片001 support装配真实 EnvironmentFactory、StaticEvaluator、RuntimeEvaluator、HighestVersionVerifier、
   CandidateBuilder、CoordinateSearch与 SearchCoordinator；只脚本 lower adapters及 consumer sinks；
2. 用最小候选集覆盖 baseline stop、candidate failure/empty、prepare reuse、probe/region/failure refs、
   diagnostics/events与所有 terminal cleanup；
3. 删除 product-level Environments/Static/Full/Highest/Candidate fake、CoordinateSearch substitute/patch、
   private state/call-chain assertions及两个 obsolete Search missing-Attempt tests；
4. 核对 `test_search.py` 仍唯一覆盖 slice/window/hint/strategy/promotion/predecessor/multi-sweep/termination/
   reentrancy/concurrency；不改其合法 VectorEvaluator seam；
5. 全库清零 direct PreparedEnvironment constructor；若 `prepared_resolution_evidence(...)` 无调用方则删除。

### 切片 005 — Current-contract 清理、owner归并与生成物核对

1. 删除旧 Protocol imports、casts、helper与只枚举 obsolete path的 tests；保留 D003/D005/D012 要求的当前
   error/safety negatives；
2. 扫描确认 lower adapter/consumer Protocol保留、无 facade/bundle/alias/compatibility path、无 concrete
   prepare/capture/evaluate/verify/minimize patch或 subclass；
3. D002吸收 concrete module interface、CompatibilityChecker位置与 composition rules；D003吸收 coordinator
   public test surface/algorithm ownership；D004吸收 Factory-only PreparedEnvironment与 evaluation test seam；
4. 核对 D008/D012无冲突；CLI、report identity/wire、Journal、terminal与Schema未变，生成物应 no-drift。

### 切片 006 — 全量证据、逐项验收与同步归档

1. 运行 §6 的 focused、Ruff、ty、coverage、顺序3.10/3.11/3.12 full、build、generated no-drift、links/diff及
   ownership/deletion scans，并在 §8回填精确结果与环境限制；
2. 按 §5逐项审计 D022 §8；缺少直接证据即继续实施，不以其他 gate替代；
3. 将 R005 轨 C 标记为已解决、把原轨 D 移交 R006 并归档 R005，更新 README/R006 引用与索引；
4. 将 D022/P028标为完成并在同一变更中移入 `docs/archived/designs` / `docs/archived/plans`，再复查状态、
   相对链接、generated diff与 scoped worktree。

## 5. Acceptance / evidence matrix

| D022 §8 | 实施切片 | 直接证据 | 状态 |
| --- | --- | --- | --- |
| AC1 删除九份评价 Protocol且三个产品复用 concrete graph | 002、003、005 | symbol/type scan为零；CLI各构造一份evaluator并复用；focused/full通过 | 通过 |
| AC2 删除 Candidate/Highest Protocol并保留 lower/consumer seam | 003、005 | 两symbol为零；lower/consumer Protocol清单、search tests与ty通过 | 通过 |
| AC3 tests零 direct PreparedEnvironment构造且classification经 StaticEvaluator观察 | 001、002、004 | 全库constructor scan只剩Factory一处；evaluation/static public tests通过 | 通过 |
| AC4 无 product fake或 concrete method/minimize substitute，合法 lower/Vector seam保留 | 001、002、004、005 | 五个目标test的forbidden/fake source scan为零；lower/Vector seam仍在 | 通过 |
| AC5 §5.3具名矩阵、三个 obsolete tests删除且算法测试ownership唯一 | 001–005 | focused `113 passed`；obsolete name scan为零；算法矩阵只在test_search | 通过 |
| AC6 Search public evidence/diagnostics/events与 D003/D004/D005保持 | 003、004 | coordinator/search `34 passed`；composition/report `84 passed`；全量通过 | 通过 |
| AC7 CLI只装配一份 evaluator graph且无 rejected shape/compatibility | 003、005 | 三个evaluator constructor各一处；同实例注入；rejected symbol/shape为零 | 通过 |
| AC8 D002/D003/D004吸收、D008/D012核对、D022/P028同步归档 | 005、006 | owner diff与状态审计闭合；62份Markdown的393个相对link零缺失 | 通过 |
| AC9 全部质量门禁与三版本full/build逐项通过 | 006 | §6/§8记录3.10 coverage full、3.11/3.12 full、Ruff、ty与build | 通过 |

## 6. 验证命令与证据槽

所有 pytest使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`；显式 full regression使用 `--no-testmon`，三个 Python版本在
同一工作树顺序执行。最终命令可按项目配置补充精确参数，但不能缩小 D022 要求。

| Gate | 计划命令 | 结果 |
| --- | --- | --- |
| focused evaluation/product | `uv run --python 3.10 pytest --no-testmon tests/test_environment.py tests/test_evaluation.py tests/test_static_transition.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py tests/test_search.py -q` | 通过：`113 passed in 0.69s` |
| focused composition/report | `uv run --python 3.10 pytest --no-testmon tests/test_cli.py tests/test_search_workflow.py tests/test_report.py tests/test_report_workflows.py -q` | 通过：`84 passed in 2.08s` |
| Ruff | `uv run --python 3.10 ruff check .` | 通过：`All checks passed!` |
| ty | `uv run --python 3.10 ty check` | 通过：`All checks passed!` |
| 3.10 full + coverage | `uv run --python 3.10 pytest --no-testmon --cov=pf --cov-report=term-missing -q` | 受控网络最终结果：`1458 passed in 29.46s`；total coverage `90.58%`，通过`fail_under=90` |
| 3.11 full | `uv run --python 3.11 pytest --no-testmon -q` | 通过：`1458 passed in 23.81s` |
| 3.12 full | `uv run --python 3.12 pytest --no-testmon -q` | 通过：`1458 passed in 25.52s` |
| build | `uv build` | 通过：生成`pf-0.1.0.tar.gz`与`pf-0.1.0-py3-none-any.whl` |
| generated | `uv run --python 3.12 python scripts/generate_report_schema.py --check`；`git diff --exit-code -- docs/schemas docs/examples` | 两命令exit 0，无生成物drift |
| docs links | 全仓 Markdown 相对 link audit | 通过：`markdown_files=62 relative_links=393 missing=0` |
| diff | `git diff --check`、untracked文档/源码末尾空白scan、scoped `git status --short` / `git diff --stat` | 通过：diff与空白scan零输出；状态仅含本实现及预存R006同轨引用更新 |
| deletion/ownership | D022 AC1–AC7的 symbol、constructor、AST、import、composition与 test-owner扫描 | 通过：旧11个Protocol、obsolete names、product fake/concrete substitute均为零；Factory为唯一constructor |

## 7. 决策、偏差与停止条件

- 2026-09-03：用户要求实现 D022，视为接受该 Design并授权按其唯一目标实施；先建立本 Plan，再编辑
  production code。
- 2026-09-03：真实 coordinator graph暴露 static-only probe晋升会再次prepare/install同一Proposal；D022
  要求prepare reuse，D012又明确static、witness与test复用同一PreparedEnvironment，因此在 `_ProposalRunner`
  保留尚未污染的static-only prepared value直到promotion/full或runner关闭。这不是搜索算法或证据降级；它
  同时实现R004 §5(1)已经记录的同一性能候选，完成时同步更新该Review。
- 当前无偏差。任何 CLI/exit/wire/identity、D003算法、FailurePolicy或产品 outcome语义变化都必须先修订并
  重新接受 D022，不能在本 Plan或实现中暗改。
- 若出现 D022 §7任一停止条件，先在本节与§8记录可复现证据，保持现状并请求用户决定，不引入兼容层。

## 8. 行动、结论与证据日志

### 2026-09-03 — 接受与建立实施基线

- 行动：核对 HEAD/worktree、D022、R005、文档索引、D002/D003/D004/D008/D012、production evaluator/
  product/composition imports与相关测试；对照 P027格式建立本 Plan。
- 命令：`git status --short`；`git log -12 --oneline --decorate`；针对 D022、source、tests与 owner documents
  的 `sed` / `rg` / `wc -l` 静态读取。
- 结果：HEAD和Design基线均为 `010e048`；worktree含同轨 D022/R005/index文档及独立R006草案。production
  仍有九份 consumer-specific评价 Protocol和 Search的 Candidate/Highest Protocol；src只有
  EnvironmentFactory构造成功 PreparedEnvironment，五个tests共七处direct constructor；baseline/check/
  coordinator仍依赖 product-level fakes或 fake CoordinateSearch。
- 结论：D022 AC1–AC9均有实际迁移差距；Design已接受并在production修改前建立P028。下一步为切片001的
  lower-adapter support与 PreparedEnvironment lifecycle迁移。

### 2026-09-03 — 切片 001–002：lower adapters、评价 modules、Highest 与 Check

- 建立 `tests/evaluation_fixtures.py`，只实现 scripted uv/candidate/ty/verifier/witness与recording facts，并
  返回真实 Environment/Static/Runtime、Highest/Candidate/Coordinate/Search module graph；没有
  prepare/capture/evaluate/search转发或预制 PreparedEnvironment/Evaluation/CellResult。
- `test_evaluation.py` 与 `test_static_transition.py` 改为由 EnvironmentFactory取得成功值；static baseline、
  multiset increment、scope failure、Ty failure、witness route/dedup、verifier authority/progress及classifier
  全部从 StaticEvaluator/RuntimeEvaluator public outcome观察。
- `CompatibilityChecker` 移到 `pf.check`，workflow与CLI不re-export；Check与Highest删除六份评价Protocol并
  直接依赖concrete evaluator。删除裸ToolFailure/missing Attempt分支、obsolete highest negative test及
  Highest不可能从capture unchanged产生的baseline witness分支。
- `test_baseline.py` 与CompatibilityChecker tests改为real modules + lower records；验证capture/full同一
  TyCheck、highest-close-before-lowest、all-terminal close、prepare/static/verifier outcomes、role/event与
  Failure cause。workflow fake改为D021保留的 `CheckCellOperations`，不伪装concrete checker。

### 2026-09-03 — 切片 003–004：Search concrete graph 与 public coordinator matrix

- Search删除env/static/full/Candidate/Highest五份Protocol，直接依赖EnvironmentFactory、StaticEvaluator、
  RuntimeEvaluator、CandidateBuilder、HighestVersionVerifier与CoordinateSearch；CLI仍把唯一共享graph注入
  Check/Highest/Search。
- `test_search_coordinator.py` 原地替换为10个public behavior cases；真实 CoordinateSearch覆盖baseline
  rejection/indeterminate、candidate empty/source failure、runtime-backed floor、static region、frozen
  artifact、full reuse、prepare failure、diagnostics/events/runtime sidecar与cleanup。算法矩阵仍只在
  `test_search.py`通过合法VectorEvaluator覆盖。
- RED/GREEN：真实graph首先显示floor promotion对版本2有两次install；修正 `_ProposalRunner` 后install顺序
  为baseline 3、probe 1、static/full复用probe 2，verifier每精确Proposal最多一次。coordinator/search
  focused为`34 passed in 0.15s`，Ruff与ty均通过。
- 删除全库七处direct PreparedEnvironment constructor及无调用方 `prepared_resolution_evidence(...)`；保留
  其他tests仍使用的 `empty_harness_baseline(...)`。最终scan只有 `src/pf/environment.py` 的Factory成功路径
  一处constructor。

### 2026-09-03 — 阶段门禁与sandbox归因

- Focused evaluation/product：精确命令见§6，`113 passed in 0.69s`；composition/report为
  `84 passed in 2.08s`。全仓Ruff与ty均`All checks passed!`。
- 3.10 sandbox coverage/full：`1457 passed, 1 failed in 36.11s`，total `90.56%`达到`fail_under=90`。唯一
  failure为installed-CLI E2E；读取本次report/Journal/process log确认`SOURCE_FAILURE @ install-environment`
  来自下载`uv_build`时PyPI tunnel `Operation not permitted`，不是断言或产品回归。
- 同一E2E在受控网络下精确重跑：`1 passed in 1.86s`。最终仍按AC9顺序运行受控网络3.10 coverage/full、
  3.11与3.12 full，不以该局部pass替代full。

### 2026-09-03 — 切片 005：owner归并、删除与文档生命周期

- D002吸收`pf.check` ownership、三个产品直接依赖共享concrete evaluator graph、Factory-only
  PreparedEnvironment、promotion lifecycle与lower-adapter测试边界；D003吸收SearchCoordinator依赖/test
  ownership；D004吸收concrete evaluation与classification测试规则。
- 核对D008的Attempt/Role/Journal与D012的resolution/install、同Proposal PreparedEnvironment复用规则，
  无冲突且无需改文；CLI、exit、Schema 1、identity、wire、Journal与terminal均未改变。
- 删除扫描：九份env/static/full Protocol、CandidateOperations、HighestOperations及三个obsolete test name
  均为零；`PreparedEnvironment(`在src/tests只剩`src/pf/environment.py`的Factory成功路径一处。
- 目标五个test module的product fake、concrete evaluator/CoordinateSearch subclass或patch scan为零；
  `test_search.py`继续以VectorEvaluator独占D003算法矩阵，lower adapter与consumer Protocol保留。
- 具名public ownership抽查包括：EnvironmentFactory的
  `test_environment_factory_materializes_an_isolated_proposal` / `test_environment_prepare_keeps_attempt_when_a_stage_fails`，
  evaluator的`test_static_evaluator_uses_multiset_subtraction_against_a_frozen_baseline` /
  `test_runtime_evaluator_preserves_authoritative_verifier_outcome`，Highest的
  `test_highest_version_verifier_reuses_capture_for_full_test_and_closes`，Check的
  `test_compatibility_checker_captures_highest_before_testing_lowest_direct`，SearchCoordinator的
  `test_search_returns_a_runtime_backed_floor_with_closed_public_evidence` /
  `test_search_reuses_a_full_probe_for_final_evaluation`，以及`test_search.py`的promotion、multi-sweep、
  strategy、termination、nested/reentrancy与barrier concurrency tests。
- R004原§5(1)标记为已解决，R006只更新R005/D022/P028生命周期引用。R005在轨A/B/C完成且轨D移交
  R006后达到已解决条件，与D022/P028同步归档；历史引用全部改指归档位置。

### 2026-09-03 — 切片 006：最终门禁与逐项验收

- 受控网络3.10 coverage/full：
  `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.10 pytest --no-testmon --cov=pf --cov-report=term-missing -q`
  → `1458 passed in 29.46s`，total coverage `90.58%`，通过`fail_under=90`。
- 顺序版本full：3.11同一full命令去掉coverage → `1458 passed in 23.81s`；3.12 →
  `1458 passed in 25.52s`。三版本均在同一工作树顺序完成。
- 最终静态门禁：`ruff check .`与`ty check`均`All checks passed!`；`uv build`成功生成sdist/wheel；
  schema `--check`与schemas/examples diff均exit 0。
- 文档与diff：62份Markdown共393个相对link，`missing=0`；`git diff --check`与新文件末尾空白scan
  均零输出。composition scan显示EnvironmentFactory、StaticEvaluator、RuntimeEvaluator在`cli.py`各构造
  一次，并把同一实例传给Check、Highest与Search。
- AC1–AC9按§5逐项审计全部通过；没有触发D022 §7停止条件。稳定规则由D002/D003/D004接管，
  D022/P028与R005完成归档，本Plan不再开放实施事项。

## 9. 完成与归档检查

- [x] §5九项均由直接证据闭合。
- [x] §6所有适用 gate已记录精确命令、范围、计数、coverage与结果。
- [x] D002/D003/D004吸收稳定规则；D008/D012完成冲突核对。
- [x] R005轨C、R006引用、docs索引与 D022/P028同步归档状态一致。
- [x] 最终 scoped diff无 compatibility、obsolete交付测试或生成物drift；预存R006独立评审内容保持，
  仅同步本轨引用与状态。

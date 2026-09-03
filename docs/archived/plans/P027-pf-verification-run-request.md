# P027 — D021 PF Verification Run request 模块深化实施计划

- **状态：** 已完成并归档
- **开始日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D021](../designs/D021-pf-verification-run-request.md)
- **评审来源：** [R005](../reviews/R005-pf-module-depth-review.md) §5、§11.3
- **实施基线：** `e570cea`（`refactor: deepen workspace inventory module`）
- **实现提交：** `7bc21fe`（`refactor: deepen verification run request`）

本文在生产代码修改前建立 D021 的实施顺序、interface/ownership 迁移、测试矩阵与证据槽。
每次实质行动后在 §8 记录行动、结论、精确命令与结果；完成标准只来自 D021 §14，不以局部绿色、
collection、单一 Python 版本或静态扫描替代验收。

## 1. 目标与边界

本轮完整实现 D021：

- 以 Check、Smoke、Search 三个 frozen command request 的 closed union 原地替换
  `VerificationRun + VerificationTask[]`；
- 让 `VerificationRunner` 独占 Run admission、host Cell/matrix/task assembly、initial context、Search
  deadline、typed live completion、Journal 与 Journal-side Process Log association；
- 让 workflow 只交付 package、同一 SourcePlan、borrowed SourceSnapshot、command operation 与调度输入，
  并保留 load/snapshot lifecycle、typed aggregation 及 Search drift/report authority；
- 把开放 `completion_outcome(object)` 拆为 Runner live、terminal Run-final、terminal
  Evaluation/Failure 三条 closed private projection；
- 保持 Scheduler generic，保持三个产品 operation、TerminalPresenter、RunLogStore、FailurePolicy 与
  report owner 不变；
- 保持 CLI grammar/exit、Schema 1、Journal v2、Diagnosis Index 与全部 identity/wire 不变；完成时归并
  D002/D008、核对其他 owner，并同时归档 D021/P027。

不增加 compatibility alias、dual path、generic result envelope、operation registry、统一评价 facade、
public projector、event bus、repository 或 remote port；不实施 R005 轨 C/D。

## 2. 基线事实与目标差距

| 切面 | `e570cea` 当前事实 | D021 目标 |
| --- | --- | --- |
| request | generic `VerificationRun[T]` 携带 command、tasks、duration | 三个 command-discriminated frozen request；只有 Search 有 duration |
| task | public `VerificationTask` 携带 execute/Journal/association/deadline callback facts | Runner 从 package、host target 与现有 operation seam 私有装配 `ScheduledCellTask` |
| admission | Runner 只验证 source mode/routes/task membership；workflow 选择 host Cell并检查 full contract | Runner 在 operation 前独占 jobs/duration、mode/routes、host/identity 与 full contract |
| lifecycle | workflow/operation 发布 initial baseline；Scheduler 在 submit 后调用 `on_started` | Runner 的 `on_started` 在 operation 前发布每个已启动 Cell唯一 baseline context |
| completion | exported `completion_outcome(object)` 被 Runner、Terminal 与 tests 共享 | Runner 三个 typed projector；Terminal 两类 closed private projector |
| durability | task callbacks提供 Journal entry/runtime association；Runner gate 决定 durable-before-diagnose | Runner typed projection同时形成 completion、Journal与 process facts，保持持久化时序 |
| ownership | workflow 保存/探测 host target并装配 task/deadline | composition root 探测一次并注入共享 Runner；workflow 无 host/task/deadline ownership |
| snapshot/report | workflow `finally` 关闭 snapshot；Search 返回后 drift/report/association | 该 ownership 原样保留 |

## 3. Interface 与 ownership 迁移

1. 将现有 `CheckCellOperations`、`SmokeCellOperations`、`CellSearchOperations` 声明移动到不会造成
   workflow ↔ verification 环的共享 owner；只保留这三个复数 product seam，不建立 singular duplicate。
2. 在 verification owner 定义 `CheckVerificationRun`、`SmokeVerificationRun`、`SearchVerificationRun`；
   均为 frozen dataclass，`command` 是 `ClassVar[Literal[...]]`，union 名为 `VerificationRun`，并为
   `VerificationRunner.run` 提供三个精确 overload。
3. Runner 构造器新增必需 `host_target`；composition root 唯一调用 `pf.project.host_target()`，三个
   workflow 删除 `host_target` 参数、host selection、matrix/task/deadline assembly。
4. Runner 在调度前完成 admission、host-only 完整 Cell 集与唯一 identity，发布一次 matrix，并按命令
   执行 full-evaluation gate/空集顺序；同一 package/SourcePlan/snapshot object传给每个 operation。
5. Scheduler 调整为 `on_started` 完成后才提交 operation；保持 generic task/result、deadline callback、
   completion callback与规范结果排序，不导入领域 facts。
6. Runner 为三个 outcome family 建立 implementation-private typed projection，集中 live completion、
   Journal Role/entry、runtime process facts、deadline Failure 与 durable-before-diagnose gate；删除 public
   `VerificationTask` 与 `completion_outcome`。
7. Smoke workflow、`CompatibilityChecker`、`SearchCoordinator` 删除 initial baseline；三个单 Cell
   operation与 Check/Smoke workflow 删除 full-evaluation duplicate，但保留 declaration、Search
   `detail=None`/probe context及单 Cell算法。
8. Terminal 建立 command-closed Run-final projector与 Evaluation/Failure projector；live 继续只消费
   `CellCompletedEvent`，不导入 Runner private projector。
9. Search workflow在 Runner 后继续 drift、report build/update和 report-generation association；所有
   workflow继续在 `finally` 关闭 borrowed snapshot。

## 4. 实施顺序

### 切片 001 — Request union、admission、host Cell 与 Scheduler started 顺序

1. 先改写 Runner public-seam tests，覆盖三种 request constructor/返回 type、command `ClassVar`、
   jobs/duration、mode/routes、同一 object传递、host-only完整集合、identity、matrix/total/排序与空集语义；
2. 引入三个 frozen request与 operation seam移动，给 `run` 增加 typed overload并删除 caller提供 tasks；
3. 把 host selection、matrix与 full-evaluation admission收进 Runner；
4. 调整 Scheduler started-before-submit 顺序，以 Barrier/Event/injected monotonic证明 started happens-before
   operation、completion真实顺序、返回规范顺序与 pending deadline未启动；
5. 在 Runner `on_started` 发布唯一 initial baseline context，暂不删除 operation旧发布点前以测试标记迁移
   差距，切片 003 完成后不得保留重复路径。

### 切片 002 — Typed completion、Journal、deadline 与 durability

1. 以 Check capture/declaration、Smoke baseline、Search baseline/probe/Cell scope的 public `run(...)`
   矩阵替换 private projector tests，覆盖 PASS/Rejection/Indeterminate、detail/process Failure source、Role；
2. 实现三个 Runner-private typed projector和 private `_CellProjection`，校验 outcome family/Cell identity，
   形成 completion、Journal entries与 process facts；
3. 把 Search scheduler-deadline scope/failure assembly收进 Runner；`None` 不安装 callback，pending Cell不建
   Attempt/不启动 operation/不发 context，lowest-direct与 Failure ID conflict fail closed；
4. 保持逐 Cell complete Journal → `journal:<run-id>` association → completion时序；真实临时目录验证 locator
   与 final persist，窄 failing substitute验证 false completion后最终 `InfrastructureError`；
5. 删除 workflow Journal/runtime/deadline callback和 public `VerificationTask`。

### 切片 003 — Workflow、operation context 与 snapshot/report ownership

1. Check/Smoke/Search workflow改为构造对应 request；删除 `_cell_task`、host/matrix/task/deadline assembly与
   workflow `host_target`；保留 status、typed aggregation及 Search report-side runtime facts；
2. 从 Smoke workflow、`CompatibilityChecker`、`SearchCoordinator` 删除 initial baseline，从三个 operation
   及 Check/Smoke workflow删除 full-evaluation duplicate；保留后续产品 context顺序；
3. 用 workflow public seam验证 SourcePlan object、snapshot成功/operation/Journal/report failure均关闭、
   Check/Smoke聚合及 Search empty → incomplete、drift/report update/report association；
4. composition root只探测一次 host target并注入共享 Runner；CLI tests锁定 grammar、channel、exit与命令结果。

### 切片 004 — Terminal closed projections 与展示等价

1. 删除 terminal 对 `completion_outcome` 的 import；live只从 `CellCompletedEvent` 构造 presentation；
2. 为 Check/Smoke/Search typed final result建立 terminal-private closed projector；
3. 为 Explain与剩余 `SearchFailureEvent` 的 Evaluation/Failure facts建立独立 closed projector；
4. 通过 TerminalPresenter/public presentation tests比较三条路径共同适用的 kind/status/phase、detail及其
   Failure source、process及其 Failure source、Failure集与 Role；保持TTY/plain、channel、exactly-one-final。

### 切片 005 — Current-contract测试清理与所有权扫描

1. 替换直接 import `completion_outcome`、构造 `VerificationTask`、调用 workflow helper的 shallow tests；
2. 保留 required error/safety negatives，删除只枚举obsolete constructor/helper的交付测试；
3. 扫描确认无旧 task/projector/host/workflow callback、parallel operation seam或 compatibility path；
4. 静态确认 Scheduler无 Evaluation/Failure/Role/Journal/CellResult/terminal import，request未进入 schema/wire。

### 切片 006 — Owner归并、全量证据、逐项验收与归档

1. 将 command request、operation seam、workflow/Runner/Scheduler依赖和 snapshot ownership归并 D002；
2. 将 admission、host/matrix/context、deadline、typed completion、Journal/association与三条 projection规则
   归并 D008；核对 D001/D005/D006/D007/D012/D014，只有实际契约变化才修订；
3. 运行 §6 的 focused、Ruff、ty、3.10 coverage/full、顺序3.11/3.12 full、build、generated no-drift、
   links/diff和 ownership/删除扫描，并在 §8 回填精确结果；
4. 按 §5 逐项审计 D021 §14；缺证据即继续实施；
5. 更新 R005与索引，把 D021/P027标记完成并同时移入归档目录，复查全部相对链接与状态一致性。

## 5. Acceptance / evidence matrix

| D021 §14 | 实施切片 | 直接证据 | 状态 |
| --- | --- | --- | --- |
| AC1 closed request union、复用 operation seam、精确 type | 001、005 | `TestVerificationRunnerRequest`；ty；request/schema与Protocol扫描 | 通过 |
| AC2 唯一 host探测、Cell/matrix/task/total/deadline集合一致 | 001、003 | host-set/matrix/deadline tests；`TestDefaultContext`；host扫描 | 通过 |
| AC3 admission、full contract、同一对象与空集语义 | 001、003 | `TestVerificationRunnerAdmission`；empty Search workflow test | 通过 |
| AC4 删除 public task/callback/deadline plumbing且无兼容层 | 001、002、003、005 | focused suites；旧interface与workflow plumbing扫描均为零 | 通过 |
| AC5 initial context顺序、三条 closed projection、展示等价 | 001、003、004 | Scheduler/Runner happens-before tests；Check/Smoke/Search live-final及Evaluation public tests；import扫描 | 通过 |
| AC6 Role/Attempt/Failure Journal 与 fail-closed | 002 | projection Role/scope/冲突/lowest-direct tests；PASS空Journal test | 通过 |
| AC7 Search-only deadline及 `None` 无路径 | 001、002 | deterministic deadline test；request constructor/type tests | 通过 |
| AC8 durable-before-diagnose、failure与并发/返回顺序 | 001、002 | `TestVerificationRunnerDurability`；concurrency canonical-order test | 通过 |
| AC9 module ownership与 Scheduler领域隔离 | 001–005 | focused/full suites；Scheduler import扫描；D002/D008 owner归并 | 通过 |
| AC10 Search post-run、snapshot lifecycle与外部契约不变 | 003、006 | workflow成功/operation/Journal/report异常close tests；CLI/report；Schema no-drift；三版本full suites | 通过 |
| AC11 §11 public-seam tests与旧 shallow tests替换 | 001–005 | focused `290 passed`；旧test import/helper扫描为零 | 通过 |
| AC12 完整证据、owner归并与同步归档 | 006 | §6/§8结果；D002/D006/D008；最终links/status/diff audit | 通过 |

## 6. 验证命令与证据槽

所有 pytest使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`；显式全量回归使用 `--no-testmon`。最终命令可按项目
实际环境补充精确参数，但不能缩小 D021 要求的范围。

| Gate | 计划命令 | 结果 |
| --- | --- | --- |
| focused | `uv run --python 3.10 pytest --no-testmon tests/test_verification.py tests/test_scheduling.py tests/test_check.py tests/test_smoke.py tests/test_search_workflow.py tests/test_search_coordinator.py tests/test_evaluation.py tests/test_terminal.py tests/test_runlog.py tests/test_cli.py -q` | 通过：`290 passed in 2.84s` |
| Ruff | `uv run --python 3.10 ruff check .` | 通过：`All checks passed!` |
| ty | `uv run --python 3.10 ty check` | 通过：`All checks passed!` |
| 3.10 full + coverage | `uv run --python 3.10 pytest --no-testmon --cov=pf --cov-report=term-missing -q` | 通过：`1476 passed in 28.26s`；total `90.65%`，达到 `fail_under=90` |
| 3.11 full | `uv run --python 3.11 pytest --no-testmon -q` | 通过：`1476 passed in 23.84s`；在3.10后顺序执行 |
| 3.12 full | `uv run --python 3.12 pytest --no-testmon -q` | 通过：`1476 passed in 24.85s`；在3.11后顺序执行 |
| build | `uv build` | 通过：生成`dist/pf-0.1.0.tar.gz`与`dist/pf-0.1.0-py3-none-any.whl` |
| generated | `uv run --python 3.12 python scripts/generate_report_schema.py --check`；`git diff --exit-code -- docs/schemas docs/examples` | 通过：两命令exit 0、无输出/无diff |
| docs links | 全仓Markdown相对link audit | 通过：归档前后均为`files=60 checked=352 missing=0` |
| diff | `git diff --check`、scoped `git status --short`/`git diff --stat` | 通过：implementation与archive两笔scope分别核对，whitespace均无错误 |
| deletion/ownership | `rg`旧interface、imports、compatibility、host ownership与Scheduler领域词 | 通过：旧interface/workflow plumbing/terminal→Runner/Scheduler领域import均零；见§8 |

## 7. 决策与偏差

- 2026-09-03：用户要求实现 D021，视为接受该 Design并授权按其唯一目标实施；先建立本 Plan，再编辑
  production code。
- 当前无偏差。任何 CLI/exit/wire/identity或产品可观察语义变化都必须先修订并重新接受 D021，不能在
  本 Plan或实现中暗改。

## 8. 行动、结论与证据日志

### 2026-09-03 — 建立实施基线

- 行动：核对 HEAD、worktree、D021、R005、文档索引、现有 Runner/Scheduler/workflow/Terminal imports与
  P026历史Plan格式。
- 命令：`git status --short --branch`；`git log -10 --oneline --decorate`；针对 D021及上述代码/测试的
  `sed` / `rg` 静态读取。
- 结果：HEAD与Design基线均为 `e570cea`；worktree仅有同轨未提交文档；生产仍暴露
  `VerificationRun[T]`、`VerificationTask[T]`、`completion_outcome(object)`，三个workflow仍选择host Cell
  并装配task，Scheduler仍在submit后调用`on_started`。
- 结论：D021全部迁移差距仍存在；已接受Design并在production修改前建立P027。下一步是切片001的
  public-seam tests与request/admission实现。

### 2026-09-03 — 切片 001：request、admission 与 started 顺序

- 行动：先把Runner测试迁到三个command request的public seam；首个RED是
  `ImportError: cannot import name 'SearchVerificationRun'`，实现request union/overload后转绿；再以unknown
  request测试得到`AttributeError` RED，收紧为显式`TypeError`后转绿。
- 实现：把三个现有operation Protocol归到`pf.verification`；Runner收回host Cell、identity、matrix、
  admission与task assembly；composition root探测host target并注入共享Runner；Scheduler改为started
  callback完成后才submit operation。
- 证据：request/host/admission/lifecycle与Scheduler focused tests通过；同一package/SourcePlan/snapshot
  object、真实completion顺序/规范返回顺序及deadline pending Cell均由public-seam断言。

### 2026-09-03 — 切片 002–004：typed projection、workflow 与 Terminal

- 行动：实现Check/Smoke/Search三个Runner-private typed projector，迁入Role/Journal/runtime association与
  scheduler-deadline组装；删除public task/open projector以及workflow callbacks/host/matrix/deadline plumbing。
- 行动：把唯一initial baseline移到Runner started callback；保留Check declaration、Search空detail/probe
  context；workflow继续独占snapshot lifecycle、typed aggregation与Search drift/report/association。
- 行动：Terminal改用command-closed Run-final projector和独立Evaluation projector；live只消费
  `CellCompletedEvent`，并以public presenter测试比较live/final共同稳定语义。
- 证据：projection tests覆盖outcome family/Cell mismatch、Check/Smoke/Search Role、Attempt/Cell scope、
  lowest-direct与冲突Failure ID fail-closed；durability tests覆盖empty final Journal、association先于
  diagnose、`logs=None`和persist failure。

### 2026-09-03 — 切片 005–006：owner归并与全量门禁

- Owner：D002吸收request/operation/workflow/Runner/Scheduler/snapshot边界；D008吸收admission、matrix、
  context、deadline、typed completion、Journal与三条closed projection；D006把matrix生产者和展示投影描述
  对齐。D001/D005/D007/D012/D014经核对，其产品、Failure、Process Log、Harness/Attempt和wire authority未变，
  不修订。
- Focused：`UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.10 pytest --no-testmon
  tests/test_verification.py tests/test_scheduling.py tests/test_check.py tests/test_smoke.py
  tests/test_search_workflow.py tests/test_search_coordinator.py tests/test_evaluation.py
  tests/test_terminal.py tests/test_runlog.py tests/test_cli.py -q` → 初次`285 passed in 2.78s`；补强AC5/AC10
  直接证据后阶段性结果`289 passed in 2.81s`。
- 静态门禁：`... ruff check .`与`... ty check`均为`All checks passed!`；
  `... python scripts/generate_report_schema.py --check`和
  `git diff --exit-code -- docs/schemas docs/examples`均exit 0、无drift。
- 3.10：sandbox全量先得到`1463 passed, 1 failed in 31.49s`；唯一失败是installed-CLI E2E的PyPI
  build TCP被sandbox拒绝。读取该Run的report/Journal/process log确认是`Operation not permitted`的
  `SOURCE_FAILURE`，不是代码断言失败；同一精确E2E在受控网络下`1 passed in 1.82s`。
- 全量：补强前受控网络顺序执行3.10 coverage、3.11、3.12均为`1471 passed`；加入4个public behavior
  cases后重跑阶段性门禁，分别为`1475 passed in 28.77s`且total coverage `90.65%`、
  `1475 passed in 23.99s`、`1475 passed in 25.16s`。
- Build：`UV_CACHE_DIR=/tmp/pf-uv-cache uv build`成功生成sdist与wheel。
- Links/diff：读取tracked与非ignored untracked Markdown的相对link audit为
  `files=60 checked=352 missing=0`；`git diff --check`通过；归档后仍须复跑最终状态。
- 删除/所有权扫描：`VerificationTask|completion_outcome|selected_host_cells|workflow task plumbing`零命中；
  三个operation Protocol只在`pf.verification`各一份；full-contract调用只在Runner；Terminal无Runner import；
  Scheduler只导入`Cell`且无Evaluation/Failure/Role/Journal/CellResult/terminal领域依赖；production
  composition root只有一处`host_target()`调用，request未进入schema/report/runlog。
- 结论：AC1–AC11与AC12的实现、owner及全量证据已闭合；implementation以`7bc21fe`提交，随后在独立
  治理提交中回填元数据、同步归档并复查link/status/diff。

### 2026-09-03 — 补强 AC5 / AC10 直接行为证据

- AC10：Check正常返回、Smoke Journal persist failure、Search operation failure与report update failure现在
  都通过workflow public seam记录`SourceSnapshot.close()`；Search report路径同时证明drift-check临时snapshot
  与Run borrowed snapshot各关闭一次。
- AC5：除既有Check等价case外，新增Smoke与Search的live `CellCompletedEvent` / typed final result对照，
  断言相同identity/stage、Failure用户语义与diagnose ID；既有Check Evaluation-only与SearchFailureEvent
  cases继续覆盖独立Evaluation/Failure投影。
- RED/GREEN：初始Smoke断言使用内部stage直译而非既有用户化名称，Search断言跨80列折行；两者都以
  public输出失败暴露，随后收紧到稳定语义片段并通过。局部新增4 cases通过；随后补充Runner PASS只
  finalize空Journal、不建立process association且不宣称diagnose available的直接case。最终focused
  `290 passed in 2.84s`；三版本full为3.10 `1476 passed in 28.26s` / coverage `90.65%`、3.11
  `1476 passed in 23.84s`、3.12 `1476 passed in 24.85s`，Ruff与ty仍通过。

### 2026-09-03 — Implementation commit 与同步归档

- Commit：显式stage 21个production/test/current-owner文件，`git diff --cached --check`通过；提交
  `7bc21fe`（`refactor: deepen verification run request`），不混入D021/P027/R005/index归档状态。
- 归档：D021补接受/完成日期与implementation commit，D021/P027同步移入`docs/archived/`；D002/D006/D008
  继续是稳定规则唯一owner；R005轨B改为已解决但因轨C/D及SearchCoordinator测试表面仍开放而不归档；
  current/archive索引同步更新。
- Links：使用tracked及非ignored untracked Markdown执行全仓相对link audit，归档前后均为
  `files=60 checked=352 missing=0`。
- Diff：implementation与archive staged scope分别核对；`git diff --check`无输出。最终归档提交后再确认
  commit ID、ahead/push状态与clean worktree。
- 结论：AC1–AC12全部通过；无未决验收项、兼容层、生成物drift或未说明环境失败。

## 9. 完成与归档检查

- [x] §5 十二项均由直接证据闭合。
- [x] §6所有适用gate已记录精确命令、范围、计数、coverage与结果。
- [x] D002/D008吸收稳定规则；D001/D005/D006/D007/D012/D014逐项核对。
- [x] R005轨B、docs索引、Design/Plan状态与归档后的当前代码一致。
- [x] D021/P027在同一完成变更中归档且links通过。
- [x] 最终scoped diff无兼容层、obsolete交付测试、生成物drift或无关文件。

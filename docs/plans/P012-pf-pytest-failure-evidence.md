# P012 — D013 pytest failure evidence 实施记录

- **状态：** 已完成
- **开始日期：** 2026-08-25
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D013](../designs/D013-pf-pytest-failure-evidence.md)
- **依赖：** [D005](../designs/D005-pf-failure-and-diagnose.md)、[D007](../designs/D007-pf-process-output.md)、[D008](../designs/D008-pf-verification-run.md)
- **实施基线：** `ada1ca9`（`docs: design pytest failure evidence`）

本文先于生产代码变更建立 D013 的实现切面、依赖顺序、RED/GREEN 台账和验收证据。每个切片完成后在 §7 记录“行动、目标、结论、证据”；完成标准只来自 D013，本文不新增或缩小产品契约。

## 1. 目标与边界

本轮完整实现 D013：

- 对默认 `[1]` 且可机械识别的 direct pytest command 启用 PF-owned failure-witness profile；
- 在目标 pytest process 中注入零额外依赖 plugin，只收集 failed collection、failed setup/call/teardown 与 pytest internal error；
- 用 run-unique、原子、canonical、bounded finalized-summary protocol 联合 `ProcessResult` 分类；
- 让已资格化 serial pytest 的 ordinary collection、setup、call 与 teardown failure 进入既有 `TestFail -> TEST_FAILURE / Rejection` 路径；
- 让 bootstrap、usage/config、internal error、无 witness interrupt、协议损坏、outcome 冲突、未资格化 runtime 和 xdist 保持 `ToolFailure / Indeterminate`；
- 让 profile selector 同时决定实际执行和 `evaluation_policy_identity` 中的 test outcome policy identity；
- 保持 generic command 与显式自定义 failure exit-code contract；
- 用跨 pytest major、CPython minor、当前 PF plugin 组合和 xdist guard 的真实矩阵限定 negative-evidence authority；
- 验证 plugin resource 随 wheel 发布，并同步 D013 §14 指定的现行契约所有者。

不解析 stderr、traceback、node ID 或异常正文；不做责任归属；不改变 test selection、collection continuation、`maxfail` 或 runtest 顺序；不建立 lifecycle observer、classifier registry、Unknown-aware search、harness alternative-plan search或 xdist Rejection authority。

## 2. 基线事实与差距

| 范围 | `ada1ca9` 当前事实 | D013 目标状态 |
| --- | --- | --- |
| adapter interface | `TestOperations.run(...) -> TestOutcome` 已稳定 | interface 不变，pytest 复杂度全部留在 `TestAdapter` implementation |
| profile 选择 | 所有 command 都走配置退出码 | 默认 `[1]` + direct pytest 走 witness profile；其他走 generic |
| test 分类 | exit 0 Pass；配置码 Fail；其他 ToolFailure | pytest profile 先过完整性与协议门槛，再联合 exit + witness 分类 |
| process evidence | `ProcessResult` 已含 exit/signal/start error/timeout/双流完整性 | 复用现有机械事实，不解析输出正文 |
| plugin/resource | 不存在 | wheel 内保存 standalone plugin source，每次复制成唯一顶层 module |
| 临时协议 | 不存在 | run-unique temp tree、nonce、canonical per-process summary、bounded one-shot read |
| policy identity | 已覆盖完整 config、ty policy 与 failure policy | 追加 selector 产生的 `configured-exit-code-v1 | pytest-failure-witness-v1` |
| 下游 | RuntimeEvaluator 把 `TestFail` 投影为 `TestFailEvaluation`；FailurePolicy/搜索不懂 pytest | 保持不变 |
| 测试 | `test_test_adapter.py` 只覆盖 generic exit code | 公开 interface 决策表、协议反例、真实 pytest/xdist、qualification、wheel 集成 |
| 文档 | D001/D002/D005/D007/D008/D011 仍把 TestFail 写成配置退出码 | 按 D013 §14 同步唯一所有者与过时表述 |

计划建立前工作树已有用户改动：`.envrc`、`docs/README.md`、D013、I001、R003 与未跟踪 `package-floor.json`。它们是现有输入，不归因于本计划；实施时不覆盖无关修改，提交前用逐文件/逐 hunk 暂存隔离本轮变更。

## 3. 模块、interface 与 seam

外部 seam 保持：

```text
TestOperations.run(command, cwd, environment, failure_exit_codes, timeout)
  -> TestPass | TestFail | ToolFailure
```

`TestAdapter` 是唯一 profile owner。它内部隐藏：

```text
selector
  -> generic profile
  -> pytest failure-witness profile
       -> prepare run-unique plugin/evidence tree
       -> inject argv/env without changing user arguments
       -> run original test process
       -> validate bounded finalized summaries
       -> classify ProcessResult + evidence
```

production policy identity 只向该 owner 查询稳定的 profile identity，不复制 direct-command predicate。Standalone plugin 是 TestAdapter implementation resource，不成为外部 adapter 或公共 Schema。协议解析可以拆到同一 adapter package 的私有 module，以集中 canonical/bounded/qualification 规则；测试仍优先穿过 `TestAdapter.run`。

## 4. 实施顺序

### 切片 001 — selector、generic 完整性与 policy identity

1. 为 direct command shape、absolute executable、wrapper/coverage/env 反例和自定义 failure codes 写一个参数化 RED；
2. 建立唯一 selector，让执行 profile 与 identity 消费同一个结果；
3. 补齐 generic profile 对 signal、start error、timeout 和双流不完整的 fail-closed 分类；
4. 把 test outcome policy identity 加入 `evaluation_policy_identity`；
5. 运行 `test_test_adapter.py` 与 policy/environment identity 聚焦测试。

### 切片 002 — plugin 注入、finalized summary tracer bullet 与安全降级

1. 用 recording runner 经公开 `TestAdapter.run` 写第一个 collection witness RED；
2. 新增 standalone plugin resource、run-unique temp tree、nonce、argv prefix 注入与 `PYTHONPATH` 保真；
3. plugin 只实现 D013 的公开 hook，原子提交空或非空 canonical summary，不改 pytest exit；
4. 让 exit 2 + qualified collection witness 形成 `TestFail`，并证明用户 argv/环境/cwd/timeout 保真；
5. 覆盖 resource/temp/injection 准备失败的原 command 降级：exit 0 Pass，非零 ToolFailure。

### 切片 003 — bounded protocol 与完整 outcome 决策表

按一个行为一个 RED→GREEN 的顺序，经 `TestAdapter.run` 覆盖：

1. 空目录、空 facts、collection/test facts、exit 0/1/2/其他；
2. timeout、signal、start error、双流不完整的前置优先级；
3. malformed/truncated/non-UTF-8/non-canonical/unknown field/wrong nonce/非法 runtime identity；
4. unknown file、残留 temp、单文件 4 KiB、1024 文件上限；
5. summary identity 冲突、facts 非法/乱序/重复、等价重复与 set-union；
6. `INTERNAL_ERROR`、unwitnessed failure、exit 0 + witness conflict；
7. unqualified Python/pytest、prerelease/local build、`xdist | unknown` 只保留无 witness exit 0 Pass。

完成后重构协议实现，但保持 `TestAdapter` 的小 interface 与单一 selector owner。

### 切片 004 — 真实 pytest、xdist guard 与 qualification matrix

1. 用真实 subprocess 覆盖 pass、collection、nested conftest、setup/call/teardown、initial conftest、internal error、KeyboardInterrupt、early plugin import 和 exit rewrite；
2. 覆盖 failure 后 interrupt、failure 后 internal error，以及 sessionfinish/unconfigure/config cleanup 异常；
3. 覆盖 commit failure、残留 temp 与非零无 finalized summary；
4. 覆盖 xdist argv `-n2`、config `addopts=-n2`、worker failure/internal/crash，证明没有 v1 Rejection authority；
5. 建立可复跑 qualification runner/manifest，在 CPython 3.10–3.12 对 pytest 6.2.5、7.0.1、7.4.4、8.0.2、8.4.2、9.0.2、9.1.1 及 D013 指定核心场景记录真实结果；
6. production qualification 只能采用矩阵通过的 `(Python minor, pytest major profile)`；失败组合收缩 authority，不得用设计假设补齐。

### 切片 005 — PF 集成、wheel resource 与所有者文档

1. 用 `packaging==19.2` × Python 3.10–3.12 验证 collection witness 经 `TestFailEvaluation` 到 `ProbeRejection`；
2. 验证 generic/custom-code 回归，以及 RuntimeEvaluator、FailurePolicy、CoordinateSearch 没有 pytest 分支；
3. build wheel，在独立 target environment 复制并加载 plugin resource；
4. 同步 D001、D002、D005、D008 与 D004/D007/D011 的过时表述；
5. 把 D013 改为现行、P012 改为已完成，并更新文档索引。

### 切片 006 — 验收审计、review 与提交

1. 对 D013 §15 十二项不变量与 §13 十二类测试逐项回填直接证据；
2. 运行单文件/相邻模块、`ruff check src tests scripts`、`ty check src tests`；
3. 在 Python 3.10–3.12 运行显式 `--no-testmon` 全量 pytest；
4. 运行 build、wheel/独立环境 smoke、文档链接与 `git diff --check`；
5. 以实施基线 `ada1ca9` 做 Standards/Spec 双轴 review，修复 finding 后复跑受影响门禁；
6. 检查 staged 范围与 staged diff，提交本轮实现。

## 5. 验收矩阵

| D013 验收范围 | 主要证据面 |
| --- | --- |
| direct pytest 扩大确定 failure | public `TestAdapter.run` + real pytest collection/setup/call/teardown + `packaging==19.2` |
| bootstrap/internal/interrupt/protocol fail closed | public decision table + real failure topology |
| 不解析输出、不归责 | plugin summary schema/code review；测试用任意 stderr/traceback 不改变结果 |
| 不改变用户执行策略 | recording runner argv/env 保真；real pytest 原 exit/selection/order |
| pytest 细节不下泄 | import/diff 审计；RuntimeEvaluator/FailurePolicy/CoordinateSearch 回归 |
| observer 缺失/损坏不制造 Rejection | prepare/import/commit/protocol failure 表 |
| finalized-only / bounded / canonical | protocol table、文件/字节上限、temp/unknown artifact 反例 |
| qualification authority | checked-in manifest + production profile test + cross-minor real run |
| xdist 无 Rejection | argv/config/worker failure/internal/crash integration |
| policy/report/cache/apply 隔离 | selector identity + `evaluation_policy_identity` / merge/apply regression |
| generic contract 兼容 | wrapper、自定义 failure code 与非 pytest command 回归 |
| wheel 可用 | wheel member inspection + isolated load/run smoke |

## 6. 变更控制

- D013 当前工作树内容是实施权威；若真实 qualification 与设计中的资格集合冲突，只收缩 production authority并记录证据，不扩大；
- 不新增 public config 或 report Schema 字段；ephemeral summary 不持久化；
- 不让 policy、evaluation、failure 或 search module 复制 pytest command predicate、protocol或 outcome 表；
- 不因测试便利暴露 temp path/plugin module/summary parser 为公共 seam；
- 不用 private plugin 函数测试代替 public adapter 与 real pytest integration；
- 不把 restricted network、缺解释器或缺外部 wheel 记成产品测试失败；需要下载时单独请求授权；
- 每个切片只有在正例、反例和相邻回归均 GREEN 后才完成；最终全量不能替代逐项证据。

## 7. 过程记录

### 基线审计与 Plan

- **状态：** 已完成
- **行动：** 对照 D013 全文、D001/D002/D005/D007/D008/D011 现行条款，检查 `TestAdapter`、`ProcessResult/TestOutcome`、policy identity、RuntimeEvaluator 调用链、既有 adapter 测试与 P011/P010 计划格式；在任何生产代码变更前建立本 Plan。
- **目标：** 明确最小外部 interface、pytest 私有 implementation、实施依赖顺序、RED/GREEN 行为面和最终验证门槛。
- **结论：** 现有 `TestOperations.run -> TestOutcome` 是足够深的 interface；下游无需改变。新增复杂度集中在一个 selector owner、standalone plugin resource、ephemeral protocol validator 与 qualification data。最先必须固定 selector/identity，再做注入 tracer bullet，之后才有条件完整实现 fail-closed 决策表和真实矩阵。
- **证据：** `git status --short --branch` 显示基线 `ada1ca9` 及六类预存 dirty 项；`src/pf/adapters/test_command.py` 现为纯 exit-code adapter；`src/pf/policy.py` 尚无 test outcome policy identity；`tests/test_test_adapter.py` 只有 generic exit 分类；D013 §13/§15 分别给出测试范围与十二条验收不变量。

### 切片 001 — selector、generic 完整性与 policy identity

- **状态：** 已完成
- **行动：** 先用 13-case command-shape 表固定 direct `pytest` / `py.test` / `python[3|X.Y] -m pytest` 与 wrapper/custom-code 反例；实现单一 `_select_test_profile` owner，并让 public identity 与 `evaluation_policy_identity` 共同消费。随后为 generic exit 0/1 + 双流不完整写 RED，补齐 timeout、signal、start error 与完整性前置门槛。
- **目标：** 在注入实现之前先固定 profile 选择和缓存/report authority；同时避免 generic path 用不完整事实构造非法 `TestPass/TestFail`。
- **结论：** 只有 failure codes 精确为 `(1,)` 的 direct pytest invocation 选择 `pytest-failure-witness-v1`；absolute executable 用 basename 识别，coverage/tox/nox/env/wrapper 与显式其他 failure codes 保持 `configured-exit-code-v1`。profile identity 已进入 full evaluation policy hash，调度 `jobs` 仍不影响 identity。
- **证据：** selector 首次运行因 production symbol 不存在而 collection RED；实现后 13 passed。generic completeness 首次 4 cases 均因 `TestPass/TestFail` Schema validation RED；补门槛后 `tests/test_test_adapter.py` → 23 passed；与 policy 相邻用例合跑 → 24 passed；`ty check src/pf/adapters/test_command.py src/pf/policy.py tests/test_test_adapter.py` → passed。

### 切片 002 — plugin 注入、tracer bullet 与安全降级

- **状态：** 已完成
- **行动：** 先用 recording runner 经 `TestAdapter.run` 要求 exit 2 + collection summary 形成 TestFail，并核对 plugin resource、argv 插入、nonce/evidence env、原 PYTHONPATH/cwd/timeout；随后用真实 pytest 复证 collection、setup/call/teardown 与 internal error。补 resource preparation failure 反例，原 command 只执行一次。
- **目标：** 打穿 standalone resource → run-unique 注入 → finalized summary → TestOutcome 的完整路径，同时保证 observer 缺失只降低 negative-evidence completeness。
- **结论：** direct executable 在 launcher prefix 后注入唯一 `-p` module；plugin 只依赖 stdlib + pytest，收集公开 report/internal hooks，并在最外层 cmdline wrapper 完成原子提交。准备失败时 exit 0 保持 Pass，任意非零不再按 `[1]` 拒绝而是 unwitnessed ToolFailure。
- **证据：** tracer 首次因缺 `PF_PYTEST_WITNESS_DIR` RED，完成注入后 1 passed；真实 runtest 三 phase 首次均 unwitnessed RED，补 hook 后 3 passed；真实 internal error 首次得到 outcome-conflict RED，补 `pytest_internalerror` 后通过；preparation failure 首次抛 `FileNotFoundError` RED，降级实现后 2 passed。

### 切片 003 — bounded protocol 与完整 outcome 决策表

- **状态：** 已完成
- **行动：** 新增私有 deep module `adapters.pytest_witness`，经 public TestAdapter 覆盖 canonical bytes、精确字段、合法 fact tuple、UTF-8、regular file、单文件 4 KiB、最多 1024 summaries、unknown/temp artifact、nonce、跨 summary identity、等价重复与 set-union；再覆盖 ProcessResult 完整性、INTERNAL_ERROR、unwitnessed/conflict、runtime/version/mode qualification。
- **目标：** 只有合法 finalized fact 可授权 Rejection；任意部分写、协议歧义、身份冲突、不完整进程或未资格 profile fail closed。
- **结论：** protocol reader 一次有界枚举并逐文件有界读取；所有 summary 先完整验证再合并 facts。`INTERNAL_ERROR` 最高优先；已资格 serial 的 exit 1/2 + failure witness 才是 TestFail；xdist/unknown/prerelease/local/range 外组合只保留无 witness exit 0 Pass。
- **证据：** 首轮 protocol 表 9 cases 中 6 个被旧宽松 parser 错误接受而 RED；严格 module 落地后协议 + adapter 46 passed。补边界、qualification 与完整性后两文件 100 passed；Ruff passed；ty 的唯一测试 kwargs 推断问题修正后 passed。

### 切片 004 — real pytest、xdist 与 qualification matrix

- **状态：** 已完成
- **行动：** 真实覆盖 pass、initial/nested conftest、usage、early plugin、KeyboardInterrupt、exit rewrite、failure 后 interrupt/internal、commit failure、残留 temp、sessionfinish/unconfigure/config cleanup 异常；新增 xdist session mode guard与 argv/config/worker failure/internal/crash 测试。建立独立 `qualify_pytest_witness.py`，在隔离环境运行 24 profiles × 12 cases。
- **目标：** 用 pytest public hook 的真实 topology 与跨版本/跨 Python evidence 限定 production authority，不从 CPython 3.10 原型外推。
- **结论：** 首轮 11-case matrix 中，pytest 6.2.5、7.0.1/7.4.4、8.0.2/8.4.2、9.0.2/9.1.1 在 CPython 3.10–3.12 的 231 次 core execution 全部符合，当前 plugin 组合的 33 次 execution 也全部符合。补入 nested conftest 与实际 runtime identity 校验后，最终 12-case matrix 的 core/current-plugin execution 分别为 252/36，全部符合。发现并修复“xdist 已安装但被禁用”误标 unknown：只有 pytest plugin manager 确认 xdist 已加载才调用公开 helper。
- **证据：** real topology 初轮 12 cases 仅 commit-directory 删除场景 summary code 与测试过度约束不同，Disposition 正确，收窄断言后 12 passed；finalization exception 3 passed；xdist 5 passed。首轮 qualification 为 264/264；Spec review 补出 nested conftest 未进入跨版本核心矩阵后，runner 增加该 case 与实际 runtime identity 校验并重新执行，最终 288/288 expected、24/24 profiles qualified。compact manifest 保存每 profile 结果 SHA-256，runner 可复跑 raw evidence。

### 切片 005 — PF 集成、wheel resource 与所有者文档

- **状态：** 已完成
- **行动：** 建独立 dogfood runner，在 Python 3.10–3.12 target venv 中安装 packaging 19.2 与已资格 pytest，真实运行 missing `InvalidSdistFilename` collection，再经 RuntimeEvaluator 与 FailurePolicy 投影；构建 PF wheel，在独立 controller/target venv 中安装 wheel 与 pytest，检查并加载复制出的 plugin resource；同步 D013 §14 的契约所有者。
- **目标：** 直接证明 R003 的回归不再停在 ProbeIndeterminate，而是进入既有 TEST_FAILURE / ProbeRejection，并证明 source-tree 外的发布资源可用。
- **结论：** 首轮选 pytest 9.1.1 时 resolver 正确拒绝其对较新 packaging 的 harness 依赖，未错误记为 feature failure；改用可与 candidate 共存且已资格的 pytest 6.2.5 后，三个 minor 均得到 exit 2 → TestFail → TestFailEvaluation → TEST_FAILURE / REJECTED。wheel 同时包含 standalone plugin 与私有 protocol module；controller 安装 PF、target 仅安装 pytest 9.1.1 时，target direct pytest 成功加载注入 plugin 并返回 TestPass。
- **证据：** `scripts/qualify_packaging_19.py` 三 profile 全部 qualified；`packaging-19-manifest.json` 保存精确终态；qualification tests 5 passed。`uv build` 生成 sdist/wheel，zip member 检查命中 `pf/_pytest_failure_witness.py` 与 `pf/adapters/pytest_witness.py`；独立环境 smoke 输出 `TestPass 0`。D001/D002/D004/D005/D007/D008/D011、D013 与文档索引已同步。

### 切片 006 — 验收审计、review 前门禁与提交准备

- **状态：** 已完成
- **行动：** 合跑 adapter/protocol/integration/qualification；执行 lock、全仓 Ruff/ty、wheel、独立环境 smoke；用仓库已认证 uv 0.12.5 在 CPython 3.10–3.12 运行显式 `--no-testmon` 全量 pytest；以 `ada1ca9` 为固定点做 Standards/Spec 双轴 review，并审计 D013 §13/§15。
- **目标：** 让局部 evidence、发布形态和仓库级回归同时闭环，且不把 sandbox network 或 PATH 中未认证 uv 0.8.22 误记为 feature failure。
- **结论：** 首轮全量真实发现两个本轮回归：identity helper 的 `test_` 前缀被 pytest 收集，以及旧 policy hash fixture 缺少新增字段；两项均修复。PATH 中 uv 0.8.22 导致 resolver protocol fail closed，sandbox 禁网又导致临时 demo resolution Indeterminate；切换到仓库已认证 uv 0.12.5 并允许既有联网用例后，三种解释器均全绿。首轮双轴 review 共报告 10 项、合并重复后 9 个独立 finding：bounded 枚举、cleanup seam、dogfood host scope、根 README、非法容器/递归 JSON、nested qualification、缺失 failure topology、真实 ProbeRejection 投影与 D001 xdist pass 文案；全部按 RED/GREEN 或可复跑 evidence 修复。闭环复审继续把 host scope 收紧为复用唯一 `host_target()` owner 的 exact GNU target，给 dogfood projection 增加 disposition guard，澄清所有 mode 的 witness-free exit 0 Pass，并区分首轮/最终矩阵与测试计数；再次复审后 Standards/Spec 均无剩余或新增 finding。
- **证据：** 最终 adapter/protocol/integration/qualification `134 passed`；qualification 288/288；packaging 19.2 三 minor 均生成 `ProbeRejection(status=REJECTED)`；全仓 Ruff、ty、lock check 通过；CPython 3.10、3.11、3.12 各 `822 passed`；build 与最终 wheel isolated smoke 通过；最终 Standards/Spec 复审均为无 finding。

## 8. RED/GREEN 台账

| 切片 | 行为 | RED | GREEN | 结论 |
| --- | --- | --- | --- | --- |
| 001 | profile selector 与 policy identity | missing selector；4 个 incomplete case 抛 ValidationError | selector 13 passed；adapter 23 passed；policy adjacent 24 passed；ty passed | 单一 selector owner，generic 完整性 fail closed |
| 002 | collection witness tracer bullet | 缺注入 env；runtest/internal 无 witness；prepare error 外泄 | fake + real collection，三 runtest phase，internal，fallback 全部 GREEN | standalone plugin + 缺失时单次原 command 降级 |
| 003 | protocol / outcome table | 9-case 表中 6 个非法 artifact 被错误接受 | protocol/adapter 100 passed；Ruff/ty passed | bounded canonical finalized-only，完整性与 internal 优先 |
| 004 | real pytest / xdist / qualification | installed-but-disabled xdist 误标 unknown；首轮缺 nested matrix case | topology/finalization/xdist GREEN；288/288 matrix | 24 profiles 实测 qualified，xdist 非零无 authority |
| 005 | PF integration / wheel / docs | pytest 9.1.1 与 packaging 19.2 resolver conflict | pytest 6.2.5 下三 minor 均 TestFailEvaluation/REJECTED；wheel smoke GREEN | dogfood、发布资源与 owners 同步完成 |
| 006 | 全量、review 与环境审计 | helper 被误收集；旧 policy fixture；未认证 uv/禁网；双轴 review 9 个独立 finding | 聚焦 134 passed；三 minor 各 822 passed；findings 全部修复 | 产品/规格回归已修，环境失败与 feature failure 分离 |

## 9. 最终完成矩阵

| D013 §15 不变量 | 实现证据 | 测试/运行证据 | 状态 |
| --- | --- | --- | --- |
| 1. 不按 traceback 归责 | plugin 只保存枚举 fact；adapter 不读 stdout/stderr | protocol + real topology | 通过 |
| 2. witness 扩大 Rejection 且 fail closed | strict reader + qualification gate | collection/setup/call/teardown 与 malformed 表 | 通过 |
| 3. witnessed exit 2 / unwitnessed exit 2 | outcome decision table | real collection 与 KeyboardInterrupt/usage | 通过 |
| 4. pytest exit 1 也需 witness | pytest profile 不消费 generic failure codes | early import、exit rewrite、unwitnessed 表 | 通过 |
| 5. internal/protocol/conflict 不 Rejection | internal fact 最高优先，invalid/conflict ToolFailure | internal + rewrite + malformed integration | 通过 |
| 6. 不改变 pytest 执行策略 | 仅注入 `-p`，保留原 argv 顺序/env/cwd/timeout | recording runner + real pytest | 通过 |
| 7. pytest 细节不下泄 | `TestOperations -> TestOutcome` 不变 | 下游无 pytest 分支审计 + 822 全量 | 通过 |
| 8. Indeterminate 仍终止 cell | FailurePolicy/CoordinateSearch 未改 | 既有搜索全量 + dogfood对照 | 通过 |
| 9. authority 绑定 policy identity | selector 同时提供执行 profile 与 identity | policy/hash/merge/apply 相邻测试 | 通过 |
| 10. generic/custom contract 兼容 | generic profile 保留 configured codes | selector/generic 参数表 | 通过 |
| 11. finalized-only | 只接受 canonical `summary-*.json`，原子 replace | temp/commit/unknown/bytes/count 反例 | 通过 |
| 12. xdist/unknown 不 Rejection | execution mode qualification gate | argv/config/worker failure/internal/crash | 通过 |

最终门禁：qualification 288/288 expected；packaging 19.2 × 三 minor 全部形成 ProbeRejection/REJECTED；聚焦 134 passed；CPython 3.10/3.11/3.12 各 822 passed；Ruff、ty、lock、build、wheel isolated smoke、文档链接与 `git diff --check` 全部通过。Standards/Spec 双轴 review 的 findings 已全部修复，最终复审均无剩余或新增 finding。

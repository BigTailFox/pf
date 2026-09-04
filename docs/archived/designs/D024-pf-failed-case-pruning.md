# D024 — 搜索期 FailedCaseSet 拒绝预言

- **状态：** 已完成、已归档
- **日期：** 2026-09-04
- **最后修订：** 2026-09-04
- **性质：** 临时迁移 Design；稳定规则已归并到现行 owner，本文不再承担规范性
- **实施计划：** [P030](../plans/P030-pf-failed-case-pruning.md)
- **评审来源：** [R008](../../reviews/R008-pf-search-performance-review.md) §1、§3、§4.6、§5
- **产品边界：** [D001](../../designs/D001-pf.md)
- **模块 interface：** [D002](../../designs/D002-pf-implementation.md)
- **搜索算法：** [D003](../../designs/D003-pf-search-algorithm.md)
- **static / witness：** [D004](../../designs/D004-pf-ty-enhancement.md)
- **Failure：** [D005](../../designs/D005-pf-failure-and-diagnose.md)
- **pytest telemetry：** [D013](../../designs/D013-pf-pytest-observer.md)

本文拥有 FailedCaseSet、搜索探针的 failed-set 拒绝预言、direct pytest 的 PF 管理
argv overlay（`--maxfail=1`、invocation-local `cache_dir`）、failed-set 的 requested-nodeid
私有文件与 collection 前 target 替换，以及失败 / collection 私有 artifact。策略默认开启，
不提供配置或 CLI 开关。

`CoordinateSearch` 仍只消费 Probe evidence；D005 仍独占 configured verifier terminal 到
disposition 的映射；D013 observer 仍不决定 compatibility，也不改变 test selection。
FailedCaseSet 与 argv overlay 只是 `ConfiguredVerifier` 背后的运行期实现，不成为新的
持久化 authority 或 identity。

## 1. 用户测试 oracle 契约

PF 只支持满足下列契约的用户 `test-command`：

1. 对同一 Proposal，测试 oracle 不得产生会被后续 pytest invocation 观察到的外部副作用。
   “后续”包括同一 PF Run 内的相邻搜索探针，以及一次逻辑 Evaluation 内的 failed-set 与
   原命令阶段；外部状态包括 Proposal 文件、共享缓存、数据库、队列和远端服务。
   pytest 自带的 `cache_dir` 由 PF 按 §6.3 隔离，不算用户外部状态。
2. 内部用例之间不得通过执行顺序、其他用例的 fixture 生命周期或共享可变状态形成关联
   副作用。单独运行一个 pytest 已收集的 nodeid 必须是可靠的负向 oracle：若该用例失败，
   同一 Proposal 的完整用户 oracle 也应 Rejection。
3. PF 注入的 observer、pruning plugin、`--maxfail=1`、`cache_dir` overlay 和 failed-set
   target 替换不得改变项目对该 Proposal 的 compatibility 判断。

PF 不隔离、回滚或重置外部数据库、队列、服务及其他跨 invocation 状态，也不探测用例间
依赖。违反该契约的项目不在 PF 支持范围内；PF 不为其提供 pruning opt-out 或补偿性重跑。
这是现行产品契约，不是可配置例外。

在此契约下，failed-set 与 early-exit 不改变有效输入的 compatibility 语义，只改变取得同一
负向结论所需的执行量。因此本策略是固定的内部实现，不新增 evaluation policy identity、
Attempt/Evaluation context 或 Failure authority 字段。

## 2. 产品语义与目标

本文采用 **reject-oracle**：

1. **PASS 只来自原命令阶段的 `NormalExit(0)`。** 原命令阶段保留 pytest 从用户配置解析的
   collection targets，不用 FailedCaseSet 替换 `Config.args`；direct pytest 仍带 PF
   observer 和 §6 的 overlay。
   PASS 必须跑完整个 collection。
2. **failed-set 阶段只产生负向结论。** collection artifact 已证明最终 items 非空、
   唯一且全属于 requested set 时，任意 `NormalExit(exit_code != 0)` 都是该 Proposal 的
   直接 Rejection，不再运行原命令。
3. failed-set `NormalExit(0)` 不授权 PASS，随后必须运行一次原命令阶段。
4. 原命令阶段形成 Rejection 且能够取得合法的 setup / call / teardown 失败 nodeid 时，
   `_ProposalRunner` 把这些 nodeid 加入当前坐标的 FailedCaseSet。资格取决于 D005 的
   `VerifierRejected`，不取决于具体 normal exit code。
5. 所有 direct pytest（smoke、check、search baseline、search 原命令和 failed-set）
   都在用户 argv 之后附加生效的 PF-owned `--maxfail=1`；不解析、删除或修复用户参数。

“原命令阶段”表示不收窄用户 collection，而不是字节级复用用户 argv；PF 拥有的 observer、
环境变量和 §6 overlay 仍可注入。不得以 failed-set + complement 拼接 PASS。

目标：

1. 在一次 Verification Run 的单个 Cell 内，按当前下降坐标维护 FailedCaseSet；
2. 后续同坐标探针优先用已知失败用例形成 Rejection；
3. 已知失败用例均通过、已不再被 collection，或无法证明有效 collection 时，回到原命令；
4. 让所有支持的 direct pytest 在达到首个失败后请求 pytest 尽快停止；
5. 保持 D003 的 PASS/current/floor/final authority、D005 disposition 和现行报告 wire 不变。

删除测试：删除 FailedCaseSet 后，搜索仍正确，但同一坐标的重复 Rejection 恢复为每个探针
运行原命令。把 nodeid 搬进 `CoordinateSearch` 不会消除这项成本。

## 3. 适用范围

FailedCaseSet 只用于 search 的 runtime probe 与 promotion，并且只用于 D013 §1 可机械识别的
direct pytest command。下列路径不读取、不更新集合：

- `smoke`、`check` 和 highest baseline；
- runtime witness 与 static-only observation；
- wrapper、tox、nox、coverage 及其他 generic `test-command`；
- 没有 `SearchProbeRequest.active_dependency` 的 `evaluate_full`，包括最终不动点闭合，
  以及 `CoordinateSearch` 在 `dependency=None` 时的 probe。

这些路径中的 direct pytest 仍使用 §6 overlay。generic command 的 argv 完全不变，也不收集
FailedCaseSet 成员。

同一精确 Proposal 若已由 invocation-local Evaluation cache 命中，不重复启动 verifier，
也不因后来增长的 FailedCaseSet 重算 Evaluation。无坐标的 `evaluate_full` 即使 cache
miss，也只跑原命令阶段。

## 4. FailedCaseSet

```text
FailedCaseSet
  scope = (Verification Run, Cell, active dependency)
  members = ordered unique pytest nodeids
```

- 唯一 owner 与 writer：单个 Cell 的 `_ProposalRunner`。
- 键：`SearchProbeRequest.active_dependency`。同一坐标的后续 sweep 与 promotion 复用；不同
  坐标、Cell 或 PF Run 不共享。
- 初值为空。首次探针直接运行原命令阶段。
- 只从**已采用的原命令阶段**增加成员；failed-set 阶段不增加、删除或重排成员。
- 原命令只要按 D005 形成 `VerifierRejected`，其中合法的 setup / call / teardown 失败
  nodeid 就有资格加入；normal exit code 的具体整数不参与资格判断。
- collection error 没有可独立选择的 test item，不加入。timeout、signal、start failure、typed
  terminal unavailable 以及没有形成 `VerifierRejected` 的运行不加入。
- 成员没有数量上限。先到的成员永远留下；一次运行的新成员先规范化、去重并按 nodeid
  排序，再追加尚未出现的成员。
- requested set 通过 PF 创建的 invocation-local 私有文件传给 pruning plugin，不进入 OS
  argv，因此不设 argv 字节或成员数量预算。每个 nodeid 与 artifact 仍遵守 D013 的字符、
  单项长度、文件数和总字节安全边界；artifact 越界只使本次 additions 为空或 selection
  无法证明，不截断成一个看似有效的集合。

集合不是 Evaluation cache，不进入 Attempt、Proposal、Evaluation、FailureRecord、report、
Journal、merge/apply 或任何 identity。失败列表缺失、损坏或非法只使本次新增为空，不改变已
采用的 terminal。

一个非空集合在后续 Proposal 上可能收集为空：依赖版本可以通过条件 import、动态
`pytest_generate_tests`、marker 或 plugin 改变 collection；缺失 nodeid 会让 pytest
collection 失败。该情况必须显式回退，不能把 pytest 的 “no tests collected” 或
“not found” normal exit 当成 Rejection。

## 5. 一次逻辑 verifier 评价

同一精确 Proposal 在一次 search 内仍最多形成一个 Evaluation。direct pytest 的内部执行为：

```text
无 active dependency，或集合为空
  -> 原命令阶段

集合非空且有 active dependency
  -> failed-set 阶段
       ├── timeout / signal / start failure / typed terminal unavailable
       │     -> 采用该 terminal；按 D005 形成 Indeterminate；不回退
       └── NormalExit(any code)
             ├── 未证明有效 collection
             │     -> 丢弃该进程 terminal；运行原命令阶段
             ├── D005 VerifierRejected
             │     -> 采用该 terminal；Evaluation = REJECTED；不运行原命令
             └── D005 VerifierPass
                   -> 不授权 PASS；运行原命令阶段

原命令阶段
  ├── D005 VerifierPass
  │     -> Evaluation = PASS
  ├── D005 VerifierRejected
  │     -> Evaluation = REJECTED；返回合格的 failed-case additions
  └── D005 VerifierIndeterminate
        -> Evaluation = INDETERMINATE；不增加集合
```

“未证明有效 collection”只由 §6.4 的 collection artifact 判定，包括最终 collection
为空、出现请求集合外成员、nodeid 重复、collection failed、artifact 缺失或 artifact
非法；不读取数字退出码推测原因。D013 mandatory summary 协议失败仍按 D013 形成命令级
`InfrastructureError`，不降级为 collection 回退。

failed-set 的 timeout、signal、start failure 与 typed terminal unavailable 是已采用的
不完整终态，不回退原命令。这是接受的残差：failed-set 可能先碰到挂死的旧 nodeid，从而
把本可在原命令 `--maxfail=1` 下更快 Rejection 的 Cell 变成 Indeterminate 并终止。
实现不得为此“好心”重试。

进程次数：

| 路径 | child process |
| --- | --- |
| 集合为空，或无 active dependency | 1 次原命令 |
| failed-set Rejection | 1 次 failed-set |
| failed-set PASS 后原命令 | 2 次 |
| failed-set collection 无法证明后原命令 | 2 次 |
| failed-set 不完整终态 | 1 次 failed-set，无回退 |

两段在同一 `PreparedEnvironment` 中串行执行，不创建第二份 Proposal 副本，也不重置
§1 所述外部状态。每段 pytest 使用独立的 invocation-local `cache_dir`，因此两段不能
通过 `.pytest_cache` / `--lf` / `--ff` / `--sw` 耦合。除此之外的正确性来自 §1 的
用户测试 oracle 契约。

`current`、floor 与 final 的 PASS 仍只来自该精确向量的原命令阶段 PASS。failed-set
Rejection 可以成为 predecessor 或拒绝边界，但不能更新 `current`。

## 6. argv overlay、pytest-native target 替换与失败列表

### 6.1 PF 管理的 pytest 参数

`ConfiguredVerifier` 不解释 pytest 或第三方 plugin 的 option arity，不拆短选项簇，也不
删除、重排或修复用户 argv。包括 `-x`、`--exitfirst`、所有 `--maxfail` / `-o` 形式、
未知 plugin option、它们的 value、用户 positional 和字面 `--` 在内，每个用户 token
均原样保留。畸形用户参数仍由 pytest 自身报错并形成原有 normal terminal。

每个 direct pytest child process 注入下列 PF-owned 参数：

```text
--maxfail=1
-o cache_dir=<invocation-temp>
```

固定参数插在第一个字面 `--` 之前；若没有 `--`，则追加到 argv 末尾。这样 PF 参数是
pytest 实际 CLI options 中最后出现的对应项。`--maxfail` 的后写值覆盖先写值；`-o` /
`--override-ini` 对同一 ini key 按列表建成 dict 再 `update`，后写的 `cache_dir`
覆盖用户 CLI、ini `addopts` 与 `PYTEST_ADDOPTS` 的合法早期值。`--` 之后即使有以 `-`
开头的 token，也仍按用户原意作为 positional。不得为了得到“恰好一个”形式而解析或删除
用户 token，包括用户已经写过的 `-x`、`--exitfirst`、`--maxfail` 与 `-o cache_dir=...`。

PF observer 以及仅 failed-set 使用的 pruning plugin 也作为 PF-owned 参数注入第一个
字面 `--` 之前。overlay 不改写 `[tool.pf].test-command` 配置文本，也不提供开关。覆盖范围
包括 smoke、check、search baseline、search 原命令/回退原命令与 failed-set；generic
command 的 argv 完全不变。

`--maxfail=1` 只使 Rejection 更早结束；`NormalExit(0)` 仍要求 pytest 完整执行当前
阶段的 collection。xdist 可以有已经在途的用例，因此不承诺只观察到一个失败 nodeid。

### 6.2 pytest-native target 替换

direct pytest 前缀沿用 D013 §1：`pytest` / `py.test` 为 1 个 token；
`python -m pytest` / `python3 -m pytest` / `pythonX.Y -m pytest` 为 3 个 token。

原命令阶段只注入 §6.1 固定参数与 D013 observer；pytest 自己从完整用户 argv、ini、
`PYTEST_ADDOPTS`、entry-point / `PYTEST_PLUGINS` plugin 和初始 conftest 得出
`Config.args`，PF 不替换它。

failed-set 阶段额外执行下列私有协议：

1. `ConfiguredVerifier` 把 requested nodeids 按集合顺序写入 invocation-local 私有文件。
   私有环境变量与 D013 同一风格，且必须列入嵌套 invocation 的删除集合：

   ```text
   PF_PYTEST_PRUNE_REQUEST   # 本次 requested-nodeid 文件路径
   PF_PYTEST_PRUNE_NONCE
   ```

   nodeids 不进入 OS argv。nonce 必须与本次 observer `PF_PYTEST_OBSERVER_NONCE` 一致。
   嵌套 invocation 必须先删除这两项以及 D013 已列的全部 PF 私有变量，再应用本次 overlay。
2. PF 注入独立的 pruning plugin，不得把 target 替换做进 D013 observer。D013 observer
   已占用 `pytest_cmdline_main` 的 `hookwrapper=True, tryfirst=True`，并在 post-yield
   写 summary。pruning plugin 必须是：

   ```text
   pytest_cmdline_main: hookwrapper=True, trylast=True
   ```

   pytest 完成自身 argv / config / plugin 解析和初始 conftest 加载后，该 wrapper 在
   pre-yield 执行 `config.args[:] = requested_nodeids`，紧贴 core collection。
   `pytest_cmdline_main` 是 `firstresult=True`：plugin 不得自己返回退出码，必须 yield
   默认结果，把 session 交给 pytest 自己的 main。plugin 不实现
   `pytest_collection_modifyitems`，也不修改 hook outcome 或 exit status。
3. 用户 `-k` / `-m`、marker、`--pyargs`、第三方 plugin 和其他 collection 行为仍可在
   target 替换前后影响解析或最终 items；§6.4 因此验证最终 collection，而不假定替换必然
   成功。pruning plugin 未执行、未读到 request 文件或未改写 `Config.args` 时，不得当成
   命令级 `InfrastructureError`；表现为 artifact missing / unexpected-item，回退原命令。
   资格测试必须正向证明 `SelectionApplied` 时最终 items 来自 requested set，而不是只证明
   “回退了”。

pruning plugin 与 D013 observer 是两个职责分离的私有组件：前者只在 failed-set 阶段替换
解析后的 targets，后者保持纯观察并提供 collection 证明。原始用户 argv 仍用于 pytest
确定 rootdir、配置和初始 conftest；PF 不维护 pytest/plugin option 名单，也不重建 argv。

该 seam 必须在 D013 当前 pytest 6.2.5–9.1.1 资格矩阵中验证。任一受支持版本若不能在公开
`pytest_cmdline_main` 时点取得上述顺序保证，不得以自写 argv parser 或 post-collection
过滤降级；该版本的 failed-set 资格必须在实施 Design/Plan 证据中重新收敛。

### 6.3 invocation-local `cache_dir`

每个 direct pytest child process 使用独立临时目录作为 `cache_dir`。进程结束后删除。
这是 PF 拥有的 invocation 私有状态，与 observer 证据目录同类，不是对用户外部数据库
或共享缓存的隔离。

因此两段不能通过 pytest lastfailed / stepwise / nodeids cache 改变彼此的 collection。
用户 argv 中的 `--lf` / `--ff` / `--sw` 仍被保留，但各自看到空的本次 cache：
`--lf` 在无 lastfailed 时按 pytest 默认跑当前 collection（failed-set 是请求 nodeid，
原命令是用户 collection）。

observer/plugin 载入目录、request/artifact 目录和 `cache_dir` 都是本次 child process 的
mandatory 私有资源。创建、读取或 cleanup 失败与现行 D013 mandatory observer 目录一致，
形成命令级 `InfrastructureError`，不产生 Evaluation；不得把 PF 自身资源故障伪装成正常
collection fallback。只有内容完整性属于 §6.4 的可选 selection 证明。

### 6.4 Collection 证明

不在 `pytest_collection_modifyitems` 中改写或过滤 items。选择成立与否只由 D013 observer
扩展的运行期私有、可选、有界 artifact 证明；pruning plugin 自己不能宣称选择成立。

该 artifact 在正常进程结束后提供封闭 decision：

```text
SelectionApplied(collected_nodeids)   # collection completed；非空、唯一，且 collected ⊆ requested
SelectionFallback(reason)             # empty | collection-failed | unexpected-item
                                      # | duplicate | missing | invalid
```

`collected_nodeids` 必须是全部 collection 完成之后的最终 test items，包括用户
`-k` / marker / plugin 过滤之后的集合。`SelectionApplied` 的权威来源只认：

- serial：该 session 在 `pytest_collection_finish` 之后的 `session.items`；
- xdist：**controller** 在 `pytest_collection_finish` 之后的 `session.items`。

该权威列表必须非空、内部唯一且 `collected ⊆ requested`。合法子集可以少于 requested。
controller 缺席或越界是 `missing` / `invalid`；含请求外 item 或内部 duplicate 是
`unexpected-item` / `duplicate`。

若存在 worker collected 投影，只做防御：每个 worker 列表必须内部唯一且 ⊆ requested。
空列表、互不相同的 `--dist load` 划分都合法，不要求列表一致，也不要求每个 worker 都
有投影。任一 worker 含请求外 item（例如 worker 未加载 pruning、按用户 path 重 collect）
→ `unexpected-item` 回退，不得形成假 Rejection。

不能在 set 化时静默隐藏 duplicate。验证后最终 collected nodeids 按请求集合顺序规范化。
冲突、非法、资源越界或无法证明 artifact 同属本次 invocation 时使用
`SelectionFallback(invalid)`。empty、collection failed、unexpected item 与 duplicate 都是
正常回退，不是 D005 disposition，也不能从 exit code 推断。

该 decision 留在 `ConfiguredVerifier` 实现内部。它只决定某个 normal terminal 能否
作为 failed-set 证据，不进入 `VerifierRun.authoritative`、Failure authority 或任何
schema。不得用 pytest 数字退出码、stderr 或 facts 代替 artifact。

### 6.5 失败 nodeid 列表

现行 D013 failure detail 只有首个安全 nodeid/phase 与去重失败总数，不足以维护集合。
实施时由 D013 observer 按阶段写入同一私有协议的不同 projection：

- 原命令阶段只记录 `failed`：setup、call 或 teardown phase 的失败 test item nodeid，
  供 §4 使用；不得为了 additions 序列化完整原命令 collection。
- failed-set 阶段记录 collection 是否完成及最终 `collected`，供 §6.4 使用；即使同时观察到
  failure，也不得把它作为 additions 返回。

UI detail 与 mandatory summary wire 保持不变。

`failed` 与 §6.4 相反：测试跑在 worker 上，多个 worker 的合法 `failed` 作 set union
后按 nodeid 排序，不设 nodeid 数量上限。controller 未观察到的失败仍以 worker 为准。
collection failure 与未知 phase 不写入 `failed`；非法 nodeid、冲突、非规范内容或
artifact 枚举/总字节资源越界使整个可选 projection 被丢弃，不得截断后返回部分 additions，
也不得改变 D005 已形成的 terminal disposition。每个 nodeid 继续受 D013 的安全文本与
单项长度边界约束。

只有原命令阶段的 `VerifierRejected` 消费 `failed`。failed-set 阶段即使观察到新的
失败 nodeid，也返回空 additions，避免剪枝运行反过来改变自己的选择历史。

## 7. D005 disposition 保持不变

collection 证明与 adopted terminal disposition 是两个顺序判断：先确认 failed-set 的
normal terminal 是否来自有效 requested-set collection，再把**采用的** terminal 交给
D005。映射仍为：

| 采用的 terminal | D005 处置 |
| --- | --- |
| `NormalExit(0)` | `VerifierPass` |
| `NormalExit(exit_code != 0)` | `VerifierRejected` / `VERIFIER_EXITED_NONZERO` |
| timeout、signal、start failure、typed terminal unavailable | `VerifierIndeterminate` |

因此不得为 pytest exit 1、2、3、4、5 建立各自的 disposition 分支：

- failed-set 已确认有效 requested-set collection 时，任意 normal nonzero 都直接
  Rejection；
- 原命令阶段的任意 normal nonzero 都是 Rejection；
- empty、collection failed、unexpected item、duplicate、artifact missing/invalid 的回退
  来自私有 artifact，不来自 exit 5、4 或 stderr；
- timeout、signal、start failure 与 typed terminal unavailable 不是 normal exit，
  不重试原命令。

pytest facts、phase 与 nodeid 仍不得改写 terminal；它们只分别用于 D013 diagnostics 和
§4 / §6.4 的集合维护与 collection 证明。

## 8. Identity、authority 与运行期诊断

本方案不新增或修改：

- `evaluation_policy_identity` 的 preimage 与 `pf:policy:v1`；
- `configured-verifier-terminal-v1`；
- Attempt、Proposal 或 D004 Evaluation context；
- `ConfiguredVerifierFailureAuthority`、FailureRecord identity 与 report wire；
- Journal、merge/apply authority 或 generator identity。

用户 `test-command` 文本继续按现行规则进入 evaluation policy identity。FailedCaseSet、
具体 nodeid、request file、pruning hook、argv overlay、`cache_dir`、`--maxfail=1`、分段
次数和采用阶段都不进入 identity。

理由是：对满足 §1 契约的 oracle，failed-set failure 与完整 oracle 的 Rejection 等价，
`--maxfail=1` 也只缩短取得 normal nonzero 的路径；它们不是用户可选的 evaluation policy。
若未来允许有状态/顺序依赖 oracle、加入 pruning 开关，或让子集结果产生新的 compatibility
状态，必须另立 Design 并重新评估 identity。

Failure authority 继续只保存已采用的 verifier terminal。不得把 requested/collected
nodeids 或 fallback reason 加进 portable authority。实现可在本地运行期日志中按 D013
脱敏边界记录 requested/collected 数量、采用阶段与 fallback reason，供性能和故障诊断；
这些日志不参与 disposition、identity 或持久化报告。

## 9. 进程与并发预算

每个 child process 使用完整配置 `test-timeout` 作为自身 `ProcessSpec.timeout`；一次逻辑
Evaluation 不另切共享 wall-clock，所以两进程路径最坏可使用两次完整 timeout。

一次逻辑 configured verifier 评价取得一个 `test` permit，并在 failed-set 与可能的原命令
阶段之间持续持有；两段串行，任意时刻的测试 child process 数仍不超过 `test_jobs`。permit
排队时间不计入 child process timeout。动态 stage 名称保持 `dynamic tests`。

FailedCaseSet nodeids 通过私有文件传递，不消耗 OS argv 预算，也不设置 PF-owned 数量或总长
阈值。child process argv 只比原始用户 argv 多固定、有界的 PF plugin/overlay 参数，其总长
仍受运行平台本身的 POSIX argv+environment 或 Windows command-line 限制；启动失败沿用
D005 的 `StartFailed` / Indeterminate，不新增平台无关的近似预算或截断规则。

## 10. Module 与 seam

```text
CoordinateSearch
  只消费 Probe evidence；不感知 nodeid、argv overlay 或分段

_ProposalRunner
  唯一拥有 failed_cases_by_active_dependency
  evaluate(..., failed_case_nodeids=tuple)   # 无坐标时传空 tuple
  只合并 RuntimeEvaluationRun.failed_case_additions

RuntimeEvaluator
  继续拥有 static -> witness -> configured verifier 路由
  不持有 FailedCaseSet；只把不可变 nodeid tuple 传给 verifier，不解释其语义

ConfiguredVerifier.run(VerifierRequest) -> VerifierRun
  拥有 direct-pytest 识别、用户 argv 原样保留与固定 overlay、cache_dir、requested-nodeid
  私有文件、pre-collection Config.args 替换、两阶段回退、artifact 与 adopted terminal

private pruning plugin
  hookwrapper=True, trylast=True；只在 failed-set 的 pytest_cmdline_main pre-yield
  替换 Config.args；yield 默认结果；不做 disposition

D013 observer
  只观察最终 collection/failure；不修改 selection、hook outcome 或 exit status
```

最小 interface 变化：

```text
VerifierRequest.failed_case_nodeids: tuple[str, ...] = ()
VerifierRun.failed_case_additions: tuple[str, ...] = ()       # runtime-only, excluded
RuntimeEvaluationRun.failed_case_additions: tuple[str, ...] = ()  # runtime-only, excluded
```

空 input 表示只跑原命令阶段。generic command 收到非空 input 是调用方 invariant failure。
不引入 `PruningInput`、`PruningObservation` 或公开 selector result。private pruning plugin、
collection 证明与回退状态都封装在 `ConfiguredVerifier` 背后。

D013 只吸收分阶段 artifact 的 collected/failed projection、安全边界、xdist 合并与资格
矩阵；observer 仍然不得修改 selection、hook outcome 或 exit status。用户 argv 保留、
private pruning plugin 和 `--maxfail` / `cache_dir` overlay 属于 D002 的
`ConfiguredVerifier` interface。

测试只穿过现有 seam：

- `ConfiguredVerifier.run`：验证用户 argv 原样保留、固定参数位置、`cache_dir` last-wins、
  pytest-native target 替换、serial/controller collection 证明、worker 划分不误判、
  adopted terminal 与 additions；
- `SearchCoordinator.search`：验证同坐标复用、跨坐标隔离、无坐标 `evaluate_full`、
  PASS/Rejection 路径和进程数；
- smoke/check 的公开 operation：验证一次原命令、无 FailedCaseSet、direct pytest
  `--maxfail=1` 与独立 `cache_dir`。

不得通过直接测试 private pruning plugin、artifact parser 内部状态或 `_ProposalRunner`
字典来固定实现细节。

## 11. 证据缺口

E002 没有 `(Cell, active dependency, nodeid)` 重复命中率。操作者对照表明仅
`--maxfail=1` 可把约 30min 的失败 suite 降到约 10min；这支持 early-exit，不证明
FailedCaseSet 的第二段收益。floor promotion 在集合非空时几乎必然走
“failed-set 全过 + 原命令 PASS”，这是 FailedCaseSet 最贵的账单。

实施 Plan 必须分成两个切片，并在当前 HEAD、固定源与相同 Cell 上分别记录：

1. **early-exit + `cache_dir`：** 相对不附加 `--maxfail=1` 的 wall-clock；用户 CLI、ini
   或 `PYTEST_ADDOPTS` 已含 `-x` / `--exitfirst` / `--maxfail` 时，PF 的末位 overlay
   仍然生效且用户 argv 保持原样。
2. **FailedCaseSet：** 同坐标 Rejection 中 failed-set 直接 Rejection 与回到原命令
   的比例；各 collection fallback reason；PASS 路径多一次 child process 的成本；
   `--lf` / `--ff` 在隔离 cache 下与单次原命令的语义对照；pytest 6.2.5–9.1.1 下
   plugin option、ini/`PYTEST_ADDOPTS`、字面 `--`、rootdir/初始 conftest、动态
   collection，以及 xdist controller 的 `Config.args` 替换与 `--dist load` 划分；
   对照运行的 final vector、boundary、FailureRecord 与 report 语义差异。

没有第 2 组数据时，不把 FailedCaseSet 作为已证实的第二段收益关闭 R008。策略仍是
默认实现，但静态检查或协议测试不得描述为已经取得 wall-clock 收益。

## 12. 接受并实现后的 owner 文档同步

本 Design 接受后，Plan 必须把下列同步工作列为实施验收项；不能只修改实现或保留 D024 为
唯一长期说明：

- D001：加入 §1 用户测试 oracle 契约；写明 direct pytest **原样保留用户 argv**，并在
  末位追加 PF-owned `--maxfail=1` 与 `-o cache_dir=<temp>` 覆盖用户/ini/`PYTEST_ADDOPTS`
  的合法早期值，**不删除**用户的 `-x` / `--exitfirst` / `--maxfail` / `-o cache_dir`；
  写明该 overlay 适用于 smoke / check / baseline / search；把“partial tests / 测试选择”
  非目标替换为：PF 可以用已知失败 nodeid 做拒绝预言并在首败后提前结束，但不做任意用户
  选测、不用子集冒充 PASS、不重试 flaky。
- D002：吸收 `ConfiguredVerifier` 的用户 argv 保留 / 固定 overlay、private pruning
  plugin 与 request file、`RuntimeEvaluator` / `_ProposalRunner` 的 interface / owner
  关系，以及 runtime-only additions。
- D003：明确 failed-set Rejection 可建立边界，而 PASS/current/floor/final 仍只来自
  原命令阶段；把冲突的 `partial tests` 非目标改成与 D001 相同的表述。
- D004：保留 `final_verification = direct-test-command-pass`，明确原命令阶段不替换
  pytest 解析得到的 `Config.args`。
- D005：保持所有 normal nonzero 为 Rejection，只补充 adopted failed-set terminal 的
  资格引用，不增加 pytest 数字退出码规则。
- D013：吸收 stage-specific collected/failed 可选 artifact、安全边界、serial/controller
  collection 证明与 worker `failed` union、资格矩阵，以及
  `PF_PYTEST_PRUNE_REQUEST` / `PF_PYTEST_PRUNE_NONCE` 的嵌套删除；observer 仍不改变
  selection；UI detail 和 mandatory summary 语义不变。pruning plugin 与 argv overlay
  不写入 D013。
- R008：把 §1、§4.6、§5 收敛为默认 reject-oracle + early-exit，删除新增 policy
  identity 的旧结论。
- docs index / CONTEXT：按现行文档治理规则更新状态和稳定术语；若没有 schema 或生成物
  变化，Plan 仍须记录检查结果而不是虚构修改。

实施完成并由这些 owner 吸收稳定规则后，D024 与对应 Plan 在同一完成变更中归档。

## 13. 不变量

1. 策略默认开启且没有用户开关；正确性以 §1 的测试 oracle 契约为前提。
2. 只有带 `active_dependency` 的 search runtime probe/promotion 的 direct pytest
   使用 FailedCaseSet。
3. 同一 Proposal 仍最多一个 Evaluation；内部最多两个 child process。
4. PASS 当且仅当原命令阶段 `NormalExit(0)`。
5. collection artifact 已证明 serial/controller 最终 items 非空、唯一且全属于
   requested set 后，任意 normal nonzero 都按 D005 形成 Rejection；xdist worker 的
   collected 划分不构成 invalid，含请求外 item 的 worker 投影只触发回退。
6. empty / collection-failed / unexpected-item / duplicate / missing / invalid 只由
   collection artifact 触发原命令回退，不解释 pytest 数字退出码。
7. 只有原命令阶段的 `VerifierRejected` 可增加集合；setup/call/teardown nodeid 的资格
   不依赖 normal exit code 的具体整数。
8. `_ProposalRunner` 是集合唯一 writer；`ConfiguredVerifier` 隐藏 fixed overlay、
   pruning plugin、target 替换与回退。
9. 所有 direct pytest 都保留用户 argv，并追加生效的 `--maxfail=1` 与 invocation-local
   `cache_dir`；generic command 不附加。
10. pruning 不新增 identity、portable authority、Evaluation context 或报告字段。
11. 跨 active dependency、Cell 与 PF Run 不复用集合；无坐标路径不读写集合。

## 14. 非目标

- 支持有跨 invocation 外部副作用、用例顺序依赖或共享可变状态依赖的测试 oracle；
- 隔离、快照、回滚或清理外部数据库、队列、服务和用户共享缓存；
- 为 pruning / early-exit 提供配置、CLI opt-in/opt-out 或 pytest authority profile；
- 用补集或 collection 覆盖冒充一次原命令阶段 PASS；
- 在 failed-set Rejection 后再跑原命令确认；
- 用 pytest exit 1/2/3/4/5、stderr 或 facts 重新定义 D005 disposition；
- 启用 testmon、pytest `--lf` 或跨运行 last-failed/Evaluation cache；
- 用失败 nodeid 做 FailureRecord 根因归属，或让 `CoordinateSearch` 学习测试用例；
- post-collection selector/filter，或在 D013 observer 中修改 collection。

## 15. 验收标准

1. 原命令阶段以任意 normal nonzero 形成 `VerifierRejected` 且 artifact 含合法
   setup/call/teardown nodeid 时，当前坐标集合增加这些成员；collection error、
   非法/缺失列表和非 Rejection 不增加。先到成员保留；集合与 additions 都不设成员数量
   上限，也不截断一个合法 projection。
2. 同一坐标后续 Proposal 先运行 failed-set；requested nodeids 不进入 OS argv，private
   pruning plugin 以 `hookwrapper=True, trylast=True` 在 `pytest_cmdline_main` pre-yield
   替换已解析的 `Config.args`，并 yield 默认结果。collection artifact 证明
   serial/controller 最终 items 非空、唯一且全属于 requested set，采用的 terminal 为任意
   normal nonzero 时，只启动一个 child process，Evaluation 为 Rejection，集合不变。
3. failed-set `NormalExit(0)` 后必须运行原命令阶段；只有原命令阶段 `NormalExit(0)`
   能形成 PASS。
4. 用动态 collection、plugin 注入 item、重复 item 与缺失 nodeid 证明：empty、
   collection failed、unexpected item、duplicate、artifact missing 与 invalid 都丢弃该
   normal terminal 并运行原命令，判断不依赖 exit 4/5。
5. failed-set timeout、signal、start failure 与 typed terminal unavailable 按 D005
   形成 Indeterminate，不运行原命令，也不更新集合。
6. 参数化覆盖 normal exit 1/2/3/4/5：在已证明非空的 failed-set 和原命令阶段都统一
   形成 `VerifierRejected`；没有 pytest 专用 disposition 分支。
7. 不同 active dependency、Cell 或 PF Run 的首次探针看不到其他 scope 的集合；
   无 `active_dependency` 的 `evaluate_full` / final 闭合不读不写集合，只跑原命令；
   同一 Proposal 的 Evaluation cache 仍避免重复执行，且不因集合后来变大而失效。
8. smoke、check、baseline、search 原命令与 failed-set 的 direct pytest argv 完整保留
   每个用户 token，并在第一个字面 `--` 前（无 `--` 时在末尾）各注入一个 PF-owned
   `--maxfail=1` 与 `-o cache_dir=...`；用户 CLI、ini 或 `PYTEST_ADDOPTS` 已有 `-x` /
   `--exitfirst` / `--maxfail` / `-o cache_dir=...` 时这些 token 仍保留，实际生效的是
   末位 overlay。畸形参数仍由 pytest 报错。generic command argv 不变；配置 schema 与
   CLI 不出现 pruning 选项。
9. 每个 direct pytest child process 使用独立临时 `cache_dir`。用户 argv 已含
   `-o cache_dir=...` 时，实际 cache 仍是 PF 临时目录。用户 argv 含 `--lf` / `--ff` /
   `--sw` 时，两段之后的 PASS/Rejection 仍等于只跑一次原命令阶段的结论。
10. `VerifierRun` 和 `RuntimeEvaluationRun` 只暴露 runtime-only additions；不存在
    `PruningObservation` 或公开 selector result。private pruning plugin 不越过
    `ConfiguredVerifier` seam；FailureRecord/report/Journal 不含 requested/collected
    nodeids、采用阶段或 fallback reason。
11. `evaluation_policy_identity` preimage 不新增 pruning/early-exit 字段；
    `configured-verifier-terminal-v1` 不变；固定配置的 digest 不因某次集合成员、
    选择结果或分段次数改变。
12. pytest 6.2.5–9.1.1 的 plugin option、ini/`PYTEST_ADDOPTS`、字面 `--`、rootdir/
    初始 conftest 与 nodeid 安全边界均通过 `ConfiguredVerifier.run` seam 验证。
    `SelectionApplied` 必须正向证明最终 items 来自 requested set（用户 path 本会多收集
    的用例不得出现）。xdist `--dist load` 以 controller collection 为 §6.4 权威，
    worker collected 列表不一致不构成 invalid；worker 含请求外 item 时回退，不得
    Rejection。原命令 artifact 不序列化完整 collection，
    failed-set artifact 不接受请求外或 duplicate item，nodeid 数量不设上限。
13. §11 的两个切片都有可复现命令和结果；§12 owner 文档同步完成后，D024 与 Plan
    同步归档。

## 16. 被拒绝的替代

- failed-set + complement 视为原命令 PASS：两个进程不等价于一次用户 collection。
- pruning 做成 opt-in：增加用户决策面，且放弃搜索速度的默认产品语义。
- 为固定策略新增 policy identity：把满足用户契约时的透明执行优化误建模为用户 policy。
- 把 pruning context 写入 portable authority：扩大报告与 Failure identity，却不增加
  D005 Rejection 的可信度；运行期本地诊断足够。
- `RuntimeEvaluator` 或 `CoordinateSearch` 解释 collection artifact：泄漏 pytest
  实现细节并降低 `ConfiguredVerifier` 的 depth/locality。
- 仅允许 exit 1 进入集合或形成 failed-set Rejection：重复 D005 classifier，且漏掉
  normal nonzero 中已经观察到的合法失败用例。
- 用 exit 5 判断 empty selection，或把 exit 2/3 重新分类为 Indeterminate：都违反 D005。
- failed-set 阶段扩充集合：让选择运行改变自身历史，增加状态与复现复杂度。
- 每段重建 Proposal 环境：在 §1 用户契约与 §6.3 cache 隔离下没有语义收益，只增加
  materialize/install 成本。
- 用 argv **追加** nodeid：会与用户 positional path/selector 做并集，扩大 collection。
- 自写 argv splitter 后删除用户 positional、再用 nodeid 重建 OS argv：PF 无法可靠知道
  第三方 plugin option 的 arity，会把 option value 误判成测试目标；nodeids 还会引入平台
  argv 预算。pytest 已经把真实 collection targets 解析进 `Config.args`。
- `pytest_collection_modifyitems` selector/filter：在完整 collection 之后才收窄，付满量
  collection 成本，并引入 hook 顺序与最终 items 不一致的假 Rejection。本文只允许 private
  pruning plugin 在 `pytest_cmdline_main` 的 core collection 前替换 targets，最终选择仍由
  observer artifact 独立证明。

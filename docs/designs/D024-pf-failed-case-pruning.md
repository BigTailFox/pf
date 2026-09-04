# D024 — 搜索期 FailedCaseSet 拒绝预言

- **状态：** 草案，待接受
- **日期：** 2026-09-04
- **性质：** 规范性搜索期 verifier 内部执行策略与 direct pytest 提前退出；接受后为本契约的唯一所有者
- **评审来源：** [R008](../reviews/R008-pf-search-performance-review.md) §1、§3、§4.6、§5
- **产品边界：** [D001](D001-pf.md)
- **模块 interface：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **static / witness：** [D004](D004-pf-ty-enhancement.md)
- **Failure：** [D005](D005-pf-failure-and-diagnose.md)
- **pytest telemetry：** [D013](D013-pf-pytest-observer.md)

本文拥有 FailedCaseSet、搜索探针的 failed-set 拒绝预言、direct pytest 的
`--maxfail=1` overlay，以及 selector 与失败 nodeid 私有协议。策略默认开启，不提供配置或
CLI 开关。

`CoordinateSearch` 仍只消费 Probe evidence；D005 仍独占 configured verifier terminal 到
disposition 的映射；D013 observer 仍不决定 compatibility。FailedCaseSet 与 selector 只是
`ConfiguredVerifier` 背后的运行期实现，不成为新的持久化 authority 或 identity。

## 1. 用户测试 oracle 契约

PF 只支持满足下列契约的用户 `test-command`：

1. 对同一 Proposal，测试 oracle 不得产生会被后续 pytest invocation 观察到的外部副作用。
   “后续”包括同一 PF Run 内的相邻搜索探针，以及一次逻辑 Evaluation 内的 failed-set 与
   原命令阶段；外部状态包括 Proposal 文件、共享缓存、数据库、队列和远端服务。
2. 内部用例之间不得通过执行顺序、其他用例的 fixture 生命周期或共享可变状态形成关联
   副作用。单独运行一个 pytest 已收集的 nodeid 必须是可靠的负向 oracle：若该用例失败，
   同一 Proposal 的完整用户 oracle 也应 Rejection。
3. direct pytest 的选择 hook、PF observer 和 `--maxfail=1` 不得改变项目对该 Proposal 的
   compatibility 判断。

PF 不隔离、回滚或重置外部数据库、队列、服务及其他跨 invocation 状态，也不探测用例间
依赖。违反该契约的项目不在 PF 支持范围内；PF 不为其提供 pruning opt-out 或补偿性重跑。

在此契约下，failed-set 与 early-exit 不改变有效输入的 compatibility 语义，只改变取得同一
负向结论所需的执行量。因此本策略是固定的内部实现，不新增 evaluation policy identity、
Attempt/Evaluation context 或 Failure authority 字段。

## 2. 产品语义与目标

本文采用 **reject-oracle**：

1. **PASS 只来自原命令阶段的 `NormalExit(0)`。** 原命令阶段使用用户配置的 collection，
   不带 failed-set selector；direct pytest 仍可带 PF observer 和 §6 的
   `--maxfail=1`。PASS 必须跑完整个 collection。
2. **failed-set 阶段只产生负向结论。** selector 已确认实际选中至少一个成员时，任意
   `NormalExit(exit_code != 0)` 都是该 Proposal 的直接 Rejection，不再运行原命令。
3. failed-set `NormalExit(0)` 不授权 PASS，随后必须运行一次原命令阶段。
4. 原命令阶段形成 Rejection 且能够取得合法的 setup / call / teardown 失败 nodeid 时，
   `_ProposalRunner` 把这些 nodeid 加入当前坐标的 FailedCaseSet。资格取决于 D005 的
   `VerifierRejected`，不取决于具体 normal exit code。
5. direct pytest 的 smoke、check、search baseline、search 原命令和 failed-set 在用户尚未
   指定 `-x` / `--maxfail` 时都附加 `--maxfail=1`。

“原命令阶段”表示不收窄用户 collection，而不是字节级复用用户 argv；PF 拥有的 observer、
环境变量和 early-exit overlay 仍可注入。不得以 failed-set + complement 拼接 PASS。

目标：

1. 在一次 Verification Run 的单个 Cell 内，按当前下降坐标维护 FailedCaseSet；
2. 后续同坐标探针优先用已知失败用例形成 Rejection；
3. 已知失败用例均通过、已不再被 collection，或 selector 无法证明选择成立时，回到原命令；
4. 让所有支持的 direct pytest 在达到首个失败后请求 pytest 尽快停止；
5. 保持 D003 的 PASS/current/floor/final authority、D005 disposition 和现行报告 wire 不变。

删除测试：删除 FailedCaseSet 后，搜索仍正确，但同一坐标的重复 Rejection 恢复为每个探针
运行原命令。把 nodeid 搬进 `CoordinateSearch` 不会消除这项成本。

## 3. 适用范围

FailedCaseSet 只用于 search 的 runtime probe 与 promotion，并且只用于 D013 §1 可机械识别的
direct pytest command。下列路径不读取、不更新集合：

- `smoke`、`check` 和 highest baseline；
- runtime witness 与 static-only observation；
- wrapper、tox、nox、coverage 及其他 generic `test-command`。

这些路径中的 direct pytest 仍使用 `--maxfail=1` overlay。generic command 保持原 argv，
不使用 selector，也不收集 FailedCaseSet 成员。

同一精确 Proposal 若已由 invocation-local Evaluation cache 命中，不重复启动 verifier，
也不因后来增长的 FailedCaseSet 重算 Evaluation。

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
- 成员上限为 32。保留已有顺序；一次运行的新成员先规范化、去重并按 nodeid 排序，再追加
  尚未出现的成员，达到 32 后忽略其余成员。
- 每个 nodeid 与 artifact 遵守 D013 的字符、长度、文件数和总字节安全边界。

集合不是 Evaluation cache，不进入 Attempt、Proposal、Evaluation、FailureRecord、report、
Journal、merge/apply 或任何 identity。失败列表缺失、损坏或非法只使本次新增为空，不改变已
采用的 terminal。

一个非空集合在后续 Proposal 上可能选择为空：依赖版本可以通过条件 import、动态
`pytest_generate_tests`、marker 或 plugin 改变 collection。该情况必须显式回退，不能把
pytest 的 “no tests collected” normal exit 当成 Rejection。

## 5. 一次逻辑 verifier 评价

同一精确 Proposal 在一次 search 内仍最多形成一个 Evaluation。direct pytest 的内部执行为：

```text
集合为空
  -> 原命令阶段

集合非空
  -> failed-set 阶段
       ├── timeout / signal / start failure / typed terminal unavailable
       │     -> 采用该 terminal；按 D005 形成 Indeterminate；不回退
       └── NormalExit(any code)
             ├── selector 未证明非空选择
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

“selector 未证明非空选择”只由 §6 的 selector artifact 判定，包括请求集合与当前 collection
交集为空、artifact 缺失或 artifact 非法；不读取数字退出码推测原因。D013 mandatory summary
协议失败仍按 D013 形成命令级 `InfrastructureError`，不降级为 selector 回退。

进程次数：

| 路径 | child process |
| --- | --- |
| 集合为空 | 1 次原命令 |
| failed-set Rejection | 1 次 failed-set |
| failed-set PASS 后原命令 | 2 次 |
| failed-set 选择无法成立后原命令 | 2 次 |
| failed-set 不完整终态 | 1 次 failed-set，无回退 |

两段在同一 `PreparedEnvironment` 中串行执行，不创建第二份 Proposal 副本，也不重置外部状态；
其正确性来自 §1 的用户测试 oracle 契约。

`current`、floor 与 final 的 PASS 仍只来自该精确向量的原命令阶段 PASS。failed-set
Rejection 可以成为 predecessor 或拒绝边界，但不能更新 `current`。

## 6. Selector、early-exit 与失败列表

### 6.1 Selector 私有协议

`ConfiguredVerifier` 拥有 invocation-local 私有 selector plugin、nonce、文件传输、读取边界
和 xdist 合并：

```text
pytest_collection_modifyitems:
  只保留 nodeid ∈ requested FailedCaseSet
```

用户 argv、ini、marker 与 plugin 先决定 collection；selector 只收窄，不扩大，也不把 nodeid
追加为 positional argv。正常进程结束后，私有实现只产生下列封闭 decision：

```text
SelectionApplied(selected_nodeids)     # 非空，且是 requested ∩ collected
SelectionFallback(reason)              # empty-intersection | missing | invalid
```

该 decision 留在 `ConfiguredVerifier` 实现内部。它只决定某个 normal terminal 能否作为
failed-set 证据，不进入 `VerifierRun.authoritative`、Failure authority 或任何 schema。

xdist worker artifact 使用 nonce/runtime identity 校验并作 set union；最终 selected nodeids
按请求集合顺序规范化。冲突、非法、超量或无法证明所有 artifact 同属本次 invocation 时使用
`SelectionFallback(invalid)`。空交集是正常回退，不是工具故障。

### 6.2 `--maxfail=1` overlay

`ConfiguredVerifier` 对每次 direct pytest 检查用户 argv。若尚无 `-x`、`--maxfail N` 或
`--maxfail=N`，附加 `--maxfail=1`；已有任一形式则不重复、不覆盖。覆盖范围包括 smoke、
check、search baseline、search 原命令/回退原命令与 failed-set；generic command 不附加。

overlay 不改写 `[tool.pf].test-command` 配置文本，也不提供开关。它只使 Rejection 更早结束；
`NormalExit(0)` 仍要求 pytest 完整执行当前阶段的 collection。xdist 可以有已经在途的用例，
因此 `--maxfail=1` 不承诺只观察到一个失败 nodeid。

### 6.3 失败 nodeid 列表

现行 D013 failure detail 只有首个安全 nodeid/phase 与去重失败总数，不足以维护集合。实施时
由 D013 observer 扩展一个运行期私有、可选、有界的 failed-case artifact；UI detail 与
mandatory summary wire 保持不变。

artifact 只记录 setup、call 或 teardown phase 的失败 test item nodeid。多个 worker 的合法
结果作 set union 后按 nodeid 排序；最多向 `_ProposalRunner` 返回 32 个安全 nodeid。
collection failure 与未知 phase 不写入；非法 nodeid、冲突、非规范内容或文件/字节资源
越界使整个可选 artifact 被丢弃。合法 union 超过集合剩余容量时只按 §4 的确定性规则截断，
不得改变 D005 已形成的 terminal disposition。

只有原命令阶段的 `VerifierRejected` 消费该列表。failed-set 阶段即使观察到新的失败 nodeid，
也返回空 additions，避免剪枝运行反过来改变自己的选择历史。

## 7. D005 disposition 保持不变

selector applicability 与 adopted terminal disposition 是两个顺序判断：先确认 failed-set 的
normal terminal 是否来自非空选择，再把**采用的** terminal 交给 D005。映射仍为：

| 采用的 terminal | D005 处置 |
| --- | --- |
| `NormalExit(0)` | `VerifierPass` |
| `NormalExit(exit_code != 0)` | `VerifierRejected` / `VERIFIER_EXITED_NONZERO` |
| timeout、signal、start failure、typed terminal unavailable | `VerifierIndeterminate` |

因此不得为 pytest exit 1、2、3、4、5 建立各自的 disposition 分支：

- failed-set 已确认非空选择时，任意 normal nonzero 都直接 Rejection；
- 原命令阶段的任意 normal nonzero 都是 Rejection；
- 空交集、selector missing/invalid 的回退来自私有 artifact，不来自 exit 5、4 或 stderr；
- timeout、signal、start failure 与 typed terminal unavailable 不是 normal exit，不重试原命令。

pytest facts、phase 与 nodeid 仍不得改写 terminal；它们只分别用于 D013 diagnostics 和 §4 的
集合维护。

## 8. Identity、authority 与运行期诊断

本方案不新增或修改：

- `evaluation_policy_identity` 的 preimage 与 `pf:policy:v1`；
- Attempt、Proposal 或 D004 Evaluation context；
- `ConfiguredVerifierFailureAuthority`、FailureRecord identity 与 report wire；
- Journal、merge/apply authority 或 generator identity。

用户 `test-command` 文本继续按现行规则进入 evaluation policy identity。FailedCaseSet、具体
nodeid、selector overlay、`--maxfail=1`、分段次数和采用阶段都不进入 identity。

理由是：对满足 §1 契约的 oracle，failed-set failure 与完整 oracle 的 Rejection 等价，
`--maxfail=1` 也只缩短取得 normal nonzero 的路径；它们不是用户可选的 evaluation policy。
若未来允许有状态/顺序依赖 oracle、加入 pruning 开关，或让子集结果产生新的 compatibility
状态，必须另立 Design 并重新评估 identity。

Failure authority 继续只保存已采用的 verifier terminal。不得把 requested/selected nodeids
或 fallback reason 加进 portable authority。实现可在本地运行期日志中按 D013 脱敏边界记录
requested/selected 数量、采用阶段与 fallback reason，供性能和故障诊断；这些日志不参与
disposition、identity 或持久化报告。

## 9. 进程与并发预算

每个 child process 使用完整配置 `test-timeout` 作为自身 `ProcessSpec.timeout`；一次逻辑
Evaluation 不另切共享 wall-clock，所以两进程路径最坏可使用两次完整 timeout。

一次逻辑 configured verifier 评价取得一个 `test` permit，并在 failed-set 与可能的原命令
阶段之间持续持有；两段串行，任意时刻的测试 child process 数仍不超过 `test_jobs`。permit
排队时间不计入 child process timeout。动态 stage 名称保持 `dynamic tests`。

## 10. Module 与 seam

```text
CoordinateSearch
  只消费 Probe evidence；不感知 nodeid、selector 或分段

_ProposalRunner
  唯一拥有 failed_cases_by_active_dependency
  evaluate(..., failed_case_nodeids=tuple)
  只合并 RuntimeEvaluationRun.failed_case_additions

RuntimeEvaluator
  继续拥有 static -> witness -> configured verifier 路由
  不持有 FailedCaseSet；只把不可变 nodeid tuple 传给 verifier

ConfiguredVerifier.run(VerifierRequest) -> VerifierRun
  拥有 direct-pytest 识别、maxfail overlay、selector、两阶段回退、
  failed-case artifact、xdist 合并与 adopted terminal
```

最小 interface 变化：

```text
VerifierRequest.failed_case_nodeids: tuple[str, ...] = ()
VerifierRun.failed_case_additions: tuple[str, ...] = ()       # runtime-only, excluded
RuntimeEvaluationRun.failed_case_additions: tuple[str, ...] = ()  # runtime-only, excluded
```

空 input 表示只跑原命令阶段。generic command 收到非空 input 是调用方 invariant failure。
不引入 `PruningInput`、`PruningObservation` 或公开 selector result；selector applicability 与
回退状态都是 `ConfiguredVerifier` 的私有实现，以保持现有 module interface 的 depth 与
locality。

测试只穿过现有 seam：

- `ConfiguredVerifier.run`：验证 argv overlay、选择/回退、adopted terminal 与 additions；
- `SearchCoordinator.search`：验证同坐标复用、跨坐标隔离、PASS/Rejection 路径和进程数；
- smoke/check 的公开 operation：验证一次原命令、无 selector、direct pytest early-exit。

不得通过直接测试私有 selector helper、artifact parser 内部状态或 `_ProposalRunner` 字典来
固定实现细节。

## 11. 证据缺口

E002 没有 `(Cell, active dependency, nodeid)` 重复命中率。操作者对照表明仅
`--maxfail=1` 可把约 30min 的失败 suite 降到约 10min；这支持 early-exit，不证明
FailedCaseSet 的第二段收益。

实施 Plan 必须在当前 HEAD、固定源与相同 Cell 上分别记录：

- early-exit 相对不附加 `--maxfail=1` 的 wall-clock；
- 同坐标 Rejection 中 failed-set 直接 Rejection 与回到原命令的比例；
- 空交集与 selector fallback 次数；
- 项目原本已含 `-x` / `--maxfail` 时 FailedCaseSet 的增量收益；
- PASS 路径多一次 failed-set child process 的成本；
- 支持的 pytest/plugin/xdist 资格；
- 对照运行的 final vector、boundary、FailureRecord 与 report 语义差异。

没有这些数据时，不把 FailedCaseSet 作为已证实的第二段收益关闭 R008。静态检查或协议测试
也不得描述为已经取得 wall-clock 收益。

## 12. 接受并实现后的 owner 文档同步

本 Design 接受后，Plan 必须把下列同步工作列为实施验收项；不能只修改实现或保留 D024 为
唯一长期说明：

- D001：加入 §1 用户测试 oracle 契约、默认 direct pytest 策略，并移除与本方案冲突的
  “partial tests / 测试选择”非目标；
- D002：吸收 `ConfiguredVerifier`、`RuntimeEvaluator` 与 `_ProposalRunner` 的 interface / owner
  关系，以及 runtime-only additions；
- D003：明确 failed-set Rejection 可建立边界，而 PASS/current/floor/final 仍只来自原命令
  阶段；移除冲突的 `partial tests` 非目标；
- D004：保留 `final_verification = direct-test-command-pass`，明确原命令阶段没有 selector；
- D005：保持所有 normal nonzero 为 Rejection，只补充 adopted failed-set terminal 的资格引用，
  不增加 pytest 数字退出码规则；
- D013：吸收 selector、failed-case artifact、安全边界、xdist 合并与资格矩阵；UI detail 和
  mandatory summary 语义不变；
- R008：把 §1、§4.6、§5 收敛为默认 reject-oracle + early-exit，删除新增 policy identity 的
  旧结论；
- docs index / CONTEXT：按现行文档治理规则更新状态和稳定术语；若没有 schema 或生成物变化，
  Plan 仍须记录检查结果而不是虚构修改。

实施完成并由这些 owner 吸收稳定规则后，D024 与对应 Plan 在同一完成变更中归档。

## 13. 不变量

1. 策略默认开启且没有用户开关；正确性以 §1 的测试 oracle 契约为前提。
2. 只有 search runtime probe/promotion 的 direct pytest 使用 FailedCaseSet。
3. 同一 Proposal 仍最多一个 Evaluation；内部最多两个 child process。
4. PASS 当且仅当原命令阶段 `NormalExit(0)`。
5. selector 已证明非空选择后，任意 normal nonzero 都按 D005 形成 Rejection。
6. 空交集/missing/invalid 只由 selector artifact 触发原命令回退，不解释 pytest 数字退出码。
7. 只有原命令阶段的 `VerifierRejected` 可增加集合；setup/call/teardown nodeid 的资格不依赖
   normal exit code 的具体整数。
8. `_ProposalRunner` 是集合唯一 writer；`ConfiguredVerifier` 隐藏选择与回退实现。
9. 所有 direct pytest 在用户未指定时附加 `--maxfail=1`；generic command 不附加。
10. pruning 不新增 identity、portable authority、Evaluation context 或报告字段。
11. 跨 active dependency、Cell 与 PF Run 不复用集合。

## 14. 非目标

- 支持有跨 invocation 外部副作用、用例顺序依赖或共享可变状态依赖的测试 oracle；
- 隔离、快照、回滚或清理外部数据库、队列、服务和共享缓存；
- 为 pruning / early-exit 提供配置、CLI opt-in/opt-out 或 pytest authority profile；
- 用补集或 collection 覆盖冒充一次原命令阶段 PASS；
- 在 failed-set Rejection 后再跑原命令确认；
- 用 pytest exit 1/2/3/4/5、stderr 或 facts 重新定义 D005 disposition；
- 启用 testmon、pytest `--lf` 或跨运行 last-failed/Evaluation cache；
- 用失败 nodeid 做 FailureRecord 根因归属，或让 `CoordinateSearch` 学习测试用例。

## 15. 验收标准

1. 原命令阶段以任意 normal nonzero 形成 `VerifierRejected` 且 artifact 含合法 setup/call/teardown
   nodeid 时，当前坐标集合增加这些成员；collection error、非法/缺失列表和非 Rejection 不
   增加。
2. 同一坐标后续 Proposal 先运行 failed-set；selector 证明非空且采用的 terminal 为任意
   normal nonzero 时，只启动一个 child process，Evaluation 为 Rejection，集合不变。
3. failed-set `NormalExit(0)` 后必须运行原命令阶段；只有原命令阶段 `NormalExit(0)` 能形成
   PASS。
4. 用动态 collection fixture 证明旧 nodeid 在新 Proposal 上可形成空交集；空交集、selector
   missing 与 selector invalid 都丢弃该 normal terminal 并运行原命令，判断不依赖 exit 4/5。
5. failed-set timeout、signal、start failure 与 typed terminal unavailable 按 D005 形成
   Indeterminate，不运行原命令，也不更新集合。
6. 参数化覆盖 normal exit 1/2/3/4/5：在已证明非空的 failed-set 和原命令阶段都统一形成
   `VerifierRejected`；没有 pytest 专用 disposition 分支。
7. 不同 active dependency、Cell 或 PF Run 的首次探针看不到其他 scope 的集合；同一 Proposal
   的 Evaluation cache 仍避免重复执行。
8. smoke、check、baseline、search 原命令与 failed-set 的 direct pytest 在用户没有
   `-x` / `--maxfail` 时含 `--maxfail=1`；已有任一形式不覆盖；generic command argv 不变；
   配置 schema 与 CLI 不出现 pruning 选项。
9. `VerifierRun` 和 `RuntimeEvaluationRun` 只暴露 runtime-only additions；不存在
   `PruningObservation`。FailureRecord/report/Journal 不含 requested/selected nodeids、采用阶段
   或 fallback reason。
10. `evaluation_policy_identity` preimage 不新增 pruning/early-exit 字段；固定配置的 digest
    不因某次集合成员、选择结果或分段次数改变。
11. xdist 合并、nodeid 安全边界、32 成员上限和 deterministic overflow 规则通过
    `ConfiguredVerifier.run` seam 验证。
12. §11 的性能与语义对照有可复现命令和结果；§12 owner 文档同步完成后，D024 与 Plan 同步
    归档。

## 16. 被拒绝的替代

- failed-set + complement 视为原命令 PASS：两个进程不等价于一次用户 collection。
- pruning 做成 opt-in：增加用户决策面，且放弃搜索速度的默认产品语义。
- 为固定策略新增 policy identity：把满足用户契约时的透明执行优化误建模为用户 policy。
- 把 pruning context 写入 portable authority：扩大报告与 Failure identity，却不增加 D005
  Rejection 的可信度；运行期本地诊断足够。
- `RuntimeEvaluator` 或 `CoordinateSearch` 解释 selector artifact：泄漏 pytest 实现细节并降低
  `ConfiguredVerifier` 的 depth/locality。
- 仅允许 exit 1 进入集合或形成 failed-set Rejection：重复 D005 classifier，且漏掉 normal
  nonzero 中已经观察到的合法失败用例。
- 用 exit 5 判断空交集，或把 exit 2/3 重新分类为 Indeterminate：都违反 D005。
- failed-set 阶段扩充集合：让选择运行改变自身历史，增加状态与复现复杂度。
- 每段重建 Proposal 环境：在 §1 用户契约下没有语义收益，只增加 materialize/install 成本。
- 用 argv 追加 nodeid：会与用户 positional path/selector 组合产生新的 collection 语义。

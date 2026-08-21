# PF 统一运行语义

- **状态：** 现行契约，待实现
- **最后核对：** 2026-08-21
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **静态证据：** [D004](D004-pf-ty-enhancement.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **CLI 交互与展示：** [D006](D006-pf-cli-enhancement.md)
- **进程输出与日志：** [D007](D007-pf-process-output.md)

本文是 PF 中验证运行如何实例化 Attempt、cell 如何走同一条错误链路、非报告 FailureRecord 如何持久化，以及 `pf diagnose` 从哪些工件读取的唯一契约。D001 继续定义命令、退出码和 `package-floor.json` 的 apply 语义；D002 继续定义模块位置；D003 只消费搜索处置；D004 只定义静态增量；D005 继续定义 cause、disposition、FailureRecord 形状和用户文案；D006 只组织这些事实的终端层级；D007 只定义 Process Log 与 Output Cache。

## 1. 问题

D005 把一次验证请求做成 Attempt，并把调查入口做成 `FailureRecord` + `pf diagnose`。落地后这条链只覆盖 **smoke/search 的 highest 与 exact-vector**。`check` 被写成“结果是 Evaluation，不能 diagnose”：

1. `lowest-direct` 不创建 Attempt；prepare 失败被拆成裸 `ToolFailure`，stage 和 Attempt 一起丢掉；
2. `CompatibilityChecker` 把 highest 的 `PrepareFailure` 也 unwrap 成 `ToolFailure`，check 的第一轮捕获同样退出分类；
3. 调度器完成投影只在存在 `FailureRecord` 时保留 stage；Presenter 再用 `STATIC_FAIL` 特判补 `failed at`。安装失败的 cell 标题没有阶段，`TEST_FAIL` 在 live 冻结路径上同样会丢；
4. `pf diagnose` 只读 `package-floor.json`。check/smoke 即使当场打印了 `failure_id`，命令结束后也没有可打开的记录。

这四件事看起来像 CLI 缺口，根因是运行模型不统一：同一条 prepare → evaluate → classify → 完成观察 → 调查 的流水线，在 check 上被截断成 Evaluation 判别联合。

真正需要分开的是：

```text
这次命令跑了哪些 Attempt？     验证运行（本文）
该 Attempt 如何分类？         D005 disposition
用户事后从哪读到记录？         验证日志 ∪ floor 报告（本文）
搜索能不能当证据用？           只有写入 package-floor.json 的 search 记录
```

## 2. 目标与非目标

### 2.1 目标

- `smoke`、`check`、`search` 的每个 cell 验证请求在外部操作前都是 Attempt；
- 非成功 cell 终态一律经 `FailurePolicy` 得到 `FailureRecord`，完成事件携带 adapter `stage`；
- `check` 的 `lowest-direct` 是第三种 `requested_resolution`，不是伪装的 `exact-vector`，也不是 Baseline；
- `pf diagnose` 能解释最近一次验证运行中的 Rejection/Indeterminate，不要求先 `search`；
- check/smoke 的 FailureRecord **不** 写入 `package-floor.json`，不参与 apply / explain / merge；
- `CoordinateSearch` 仍然只看见 search 的 Probe 处置，看不见 Declaration Attempt。

### 2.2 非目标

- 不增加、删除或重命名 D001 已定义的命令；
- 不改变 D003 的 probe 顺序、单调假设或 `PASS`/`FAIL` 边界规则；
- 不把 `S_hi` 的捕获改成一次带测试的 Baseline；check 的 highest 仍只 capture，不跑 `test-command`；
- 不引入 Schema 2、dual reader 或把验证日志做成公共报告；
- 不改变 D007 的日志完整性或 D006 的卡片行数、颜色、通道；
- 不为 v1 提供跨机器的 check 诊断同步；验证日志与 Process Log 一样是本机工件。

## 3. 术语

**Verification Run**（验证运行）：
一次 `smoke`、`check` 或 `search`（含 `minimize` 的 search 阶段）对所选 package、当前源码快照和策略执行的完整验证。它对应 `.pf/logs/<run-id>/`。它不是 floor 报告。
_Avoid_: CLI session, Evaluation context, report generation

**Verification Journal**（验证日志）：
该 Verification Run 写入 `.pf/logs/<run-id>/journal.json` 的本机记录。它保存命令、Verification Role、FailureRecord 和 Attempt 引用。它不是 `package-floor.json`，不授权 apply。
_Avoid_: package-check.json, floor report, diagnosis-index

**Verification Role**（验证角色）：
同一 `requested_resolution` 在某次运行中承担的产品角色。它进入 Journal 与展示 impact，不进入 Attempt identity，不进入 `failure-v1` 分类矩阵。
_Avoid_: Attempt kind, requested_resolution, Schema status

**Declaration Attempt**（声明 Attempt）：
`requested_resolution = lowest-direct` 且 `requested_managed_vector` 为空的 Attempt。它验证当前声明在最低直接解析下是否满足完整验证契约。
_Avoid_: Probe Attempt, Baseline Attempt, lowest-direct Evaluation

**Cell Completion**（cell 完成观察）：
调度器在 cell 终态确定后投影给 Presenter 的结构化观察：结果种类、adapter `stage`、可选 `FailureRecord`、进程终态和诊断。它不是公共 Schema。
_Avoid_: ProgressEvent.message, STATIC_FAIL 特判, 裸 ToolFailure

本文沿用 D005 的 Attempt、Proposal、Rejection、Indeterminate、FailureRecord、Diagnosis，以及 D004 的 `S_hi`。

## 4. 统一运行模型

所有会验证 cell 的命令共用一条流水线。命令只选择 **要建立哪些 Attempt** 和 **每个 Attempt 的评价契约**；不得另开一条“只返回 Evaluation / ToolFailure”的旁路。

```text
Verification Run
  → 对每个宿主 cell：
       按命令建立 Attempt（外部操作前）
       prepare →（成功则）evaluate
       非 PASS 经 FailurePolicy.classify
       投影 Cell Completion（必含 stage）
  → 写入 Verification Journal
  → search 另将可移植 FailureRecord 写入 package-floor.json
  → Presenter 冻结 cell 卡；diagnose 读 Journal ∪ 报告
```

不变量：

1. 有评价契约的 cell 工作在外部操作前必须有 Attempt。没有 Attempt 的失败只能是 D005 的 `CellFailureScope`（候选发现、调度 deadline）。
2. `EnvironmentFactory.prepare` 在 `highest`、`lowest-direct` 或带 `managed_vector` 时返回 `PreparedEnvironment | PrepareFailure`，不再为 `lowest-direct` 返回裸 `ToolFailure`。
3. 调用方不得把 `PrepareFailure` unwrap 成内部 `ToolFailure` 再交给调度器。
4. 非成功 cell 完成观察必须带 adapter `stage`。stage 来自 `FailureRecord.stage`，与 live `phase` 动词分开。
5. `PASS` 仍不经过 `FailurePolicy`。合法 `TyCheck` 中的既有诊断只产生 warning，不产生 FailureRecord。

### 4.1 Attempt identity 的第三种解析方式

```text
AttemptIdentity.requested_resolution
  highest        Baseline / 静态捕获共用的最高解析请求
  lowest-direct  Declaration Attempt
  exact-vector   Probe Attempt（必须带 requested_managed_vector）
```

`lowest-direct` 与 `highest` 一样：解析前不知道受管向量，identity 不含事后图。禁止在 prepare 成功后再把实际向量改写成 `exact-vector` 来冒充 Probe；那样安装失败仍然没有 Attempt。

`highest` 的 Attempt identity 不包含“是否跑测试”。check 的静态捕获与 smoke/search 的 Baseline 在同一快照、cell、策略下是 **同一次 highest Attempt 请求**；差别只在本次运行的评价契约和 Verification Role。

项目尚未发布。该字段扩展仍属 Schema 1，不升 Schema 2。`failure-v1` 对 `highest` / `exact-vector` 的既有行保持不变。

### 4.2 评价契约

评价契约属于 Verification Run，不属于 Attempt identity：

| 契约 | 行为 |
| --- | --- |
| `static-capture` | 只运行 D004 `StaticEvaluator.capture`，不跑 `test-command` |
| `full` | 在已有 `S_hi` 上跑增量静态（或对 highest 先 capture 再测）和完整测试 |

Adapter 仍然不知道契约。`FailurePolicy` 仍然不知道命令。Presenter / Journal 用 Verification Role 选择 impact。

### 4.3 Verification Role

| Role | 命令 | `requested_resolution` | 评价契约 |
| --- | --- | --- | --- |
| `baseline` | smoke、search | `highest` | `full` |
| `declaration-capture` | check | `highest` | `static-capture` |
| `declaration` | check | `lowest-direct` | `full`（相对本 cell 刚捕获的 `S_hi`） |
| `probe` | search | `exact-vector` | 由 D003 决定 static 或 full |

Role 写入 Journal，供 diagnose 与 impact 使用。它不进入 `attempt_id` 或 `failure_id`。

## 5. 各命令的 Attempt 序列

### 5.1 `smoke`

每个宿主 cell 一次 `baseline` Attempt：`prepare(highest)` → capture → 同环境完整测试 → 关闭。分类与今日 `HighestVersionVerifier` 相同。结果写入 Journal，不写 `package-floor.json`。

### 5.2 `check`

每个宿主 cell **两次** Attempt，串行：

```text
1. declaration-capture    prepare(highest) → capture S_hi → close
2. declaration            仅当捕获得到合法 S_hi
                          prepare(lowest-direct) → 相对该 S_hi 做 full → close
```

规则：

- 第 1 步失败则 **不** 启动第 2 步。没有 `S_hi` 就不能对下界做 D004 增量。
- 第 1 步的合法 `TyCheck` 诊断构成 `S_hi`，其本身不是 FailureRecord；捕获过程的工具失败才分类。
- 第 2 步的增量非空是 `STATIC_REGRESSION`；测试以配置失败码退出是 `TEST_FAILURE`。二者都是 Declaration Attempt 上的 Rejection（证据完整时）。
- `CoordinateSearch` 不得看见这两次 Attempt。

第 1 步 Rejection（例如当前声明在 highest 下无解或不能构建）表示 **当前声明在该 cell 的最高解析上确定不满足验证契约**，命令级仍是 D001 的兼容性失败。它还不是“下界不兼容”——下界尚未被问到。diagnose 必须用 `declaration-capture` 的 impact，不得写成 Baseline“因此未开始 floor 搜索”，也不得写成 Declaration“下界未通过”。

第 1 步 Indeterminate 表示 **无法捕获 `S_hi`**，下界问题未回答，命令级为不确定结果。

### 5.3 `search`

每个宿主 cell：一次 `baseline`（与 smoke 相同的完整 highest）。只有 Baseline `PASS` 才进入候选发现和 D003。每个 probe 是 `exact-vector` Attempt。candidate discovery / 调度 deadline 仍是 `CellFailureScope`。

search 把可移植 FailureRecord 写入 `package-floor.json`，并同时写入本次 Journal。报告仍是 apply/explain/merge 的唯一公共接口。

## 6. 统一错误链路

```text
Adapter cause + stage + process
  → PrepareFailure | Evaluation | ToolFailure（仅评价阶段）
  → FailurePolicy.classify(AttemptFailureScope | CellFailureScope, ...)
  → FailureRecord
  → Cell Completion（kind, stage, failure, process, diagnostics）
  → Presenter `failed at` + Diagnose
  → Journal（及 search 的报告）
```

### 6.1 prepare

`EnvironmentFactory` 对 `highest` 和 `lowest-direct` 都在创建 venv 之前构造 Attempt。prepare 失败返回 `PrepareFailure(attempt, failure)`。`CompatibilityChecker`、`HighestVersionVerifier` 和 probe runner 都必须保留该对象直到 `classify`。

禁止：

- 把 `PrepareFailure.failure` 当作 cell 任务的返回值；
- 用 `getattr(result, "status") == "FAILURE"` 当作完成观察的全部信息；
- 在 Presenter 用 Schema status 推断 stage。

### 6.2 评价阶段

`IndeterminateEvaluation`、`StaticFailEvaluation`、`TestFailEvaluation` 在进入调度器之前（或在完成投影之内、但必须在 Presenter 之前）变成带 Attempt 的 `FailureRecord`：

| 评价结果 | cause | stage |
| --- | --- | --- |
| `StaticFailEvaluation` | `STATIC_REGRESSION` | `ty` |
| `TestFailEvaluation` | `TEST_FAILURE` | `test` |
| `IndeterminateEvaluation` | 其 `ToolFailure.cause` | 其 `ToolFailure.stage` |

check 不再把 `STATIC_FAIL` / `TEST_FAIL` 作为 Presenter 的 stage 来源。

### 6.3 Cell Completion

完成投影对 Presenter 只保证：

```text
kind          success | warning | failure | indeterminate
stage         非成功时必填，adapter 名（install / ty / test / …）
failure       非成功时必填 FailureRecord
process       若有
diagnostics   D004 增量或 warning 基线诊断
```

`failed at` 的用户文案仍由 D006 从 adapter stage 映射。未知 stage 仍把 `-` 换成空格。完成观察不得依赖 `message == "STATIC_FAIL"` 或遗留 `BUILD_UNAVAILABLE`。

调度器可以继续接收各命令自己的结果类型，但投影必须经过这一观察。不得再丢 `ToolFailure.stage`。

### 6.4 Declaration Attempt 的分类扩展

cause 集合、Rejection 资格和 Baseline/Probe 列仍由 D005 §8 唯一拥有，本文不复制该矩阵。本文只增加：

- `requested_resolution = lowest-direct` 使用与 Probe 相同的 Rejection 资格（证据完整且 cause/stage 属于验证契约时 Reject）；
- 搜索含义固定为“终止该 check cell”——check 没有后续 probe；
- `rejection_is_supported` 必须接受 `lowest-direct`；
- `highest && STATIC_REGRESSION` 仍不得成为 Rejection，规则仍在 D005。

Declaration Attempt 不要求事先存在一次 **完整通过测试的** Baseline。它要求本 cell 在同一次 check 运行中已经得到合法 `S_hi`。这是 D004 静态基线，不是 D005 搜索锚点。不得把 Declaration Attempt 解释为 Probe，也不得因此要求 smoke 式的 highest `PASS`。

check 的 `declaration-capture` 使用 D005 的 Baseline/`highest` 行：确定性安装/构建/harness 失败是 Rejection；工具/来源/超时是 Indeterminate。捕获成功后的 `TyCheck` 诊断不是 Rejection。

## 7. 命令终态

退出码仍由 D001 拥有。本文只规定如何从统一的 Attempt 结果聚合，避免再按 Evaluation 类型与裸 `ToolFailure` 分叉。

### 7.1 check

对每个 cell，取该 cell 上 `declaration` 的结果；若未启动，取 `declaration-capture`：

- 任一 cell 的该终态为 Rejection → 命令 `COMPATIBILITY_FAILED`，退出 1；
- 否则任一为 Indeterminate → 命令不确定，退出 4；
- 否则全部为声明 `PASS` → 退出 0。

摘要仍使用 D001/D006 的“current declarations are incompatible”或不确定措辞，不把单个 `pydantic-core` 版本说成全局不兼容。

### 7.2 smoke / search

聚合规则不改：Baseline Rejection 退出 1；Indeterminate 退出 4；search 的其他不可应用原因退出 2。search 的 Probe Rejection 不单独决定命令退出码，它们是报告内的搜索证据。

## 8. 持久化与 diagnose

### 8.1 Verification Journal

每次 Verification Run 在 `.pf/logs/<run-id>/journal.json` 写一份验证日志，至少能还原：

```text
schema                verification-journal-v1
run_id
command               smoke | check | search
packages[]            包名
source_snapshot_digest
evaluation_policy_identity
entries[]
  package
  cell
  role                Verification Role
  attempt             Attempt | 省略（仅 CellFailureScope）
  failure             FailureRecord
```

Journal 不保存 stdout/stderr 正文、绝对路径或完整 Evaluation。`STATIC_REGRESSION` 的诊断原文以对应 Process Log 为准。权限、脱敏、原子写与 `.pf/logs` 的其余规则由 D002 / D007 拥有。

search 成功写入 `package-floor.json` 之后，Journal 与报告中的 FailureRecord 必须能按 `failure_id` 对上。报告仍不保存 `run_id`。

### 8.2 Diagnosis Index

D005 的 locator 从“只键到报告世代”扩展为同时键到验证运行：

```text
.pf/logs/diagnosis-index.json
  latest_journal[package] = run_id
  (report_generation_id, failure_id) -> 相对 Process Log
  (run_id, failure_id)              -> 相对 Process Log
```

不得靠扫描 run 目录或匹配输出文本查找日志。同 generation 的报告更新仍替换报告侧映射；新的 Verification Run 替换该 package 的 `latest_journal`。

### 8.3 `pf diagnose` 的读取面

`diagnose` 仍严格离线，不重放、不联网、不改项目。读取顺序：

1. 指定 `--failure`：先在所选 package 的 `package-floor.json` 中找；没有则在 `latest_journal` 的 Journal 中找；再没有则该 ID 不存在（命令错误，退出 3）。不得遍历全部历史 run 目录。
2. 省略 `--failure`：列出报告中的 FailureRecord（若报告存在），再列出 `latest_journal` 中尚未按 `failure_id` 出现过的记录。两组都要标明来源：`package-floor.json` 或最近一次 `pf check` / `pf smoke` / `pf search`。

没有 floor 报告时，只列出最近一次 Journal。没有 Journal 且没有报告中的失败时，诊断 0 条，退出 0。

`explain` / `apply` / `merge` 继续只使用 `package-floor.json`。Journal 缺失不影响报告证据；报告缺失不影响用 Journal 诊断最近一次 check/smoke。

### 8.4 impact 选择

落地前，现行 impact 以 D005 §12.3 为准（由 `requested_resolution` 决定）。落地后本文取代该表：impact 由 disposition、scope 和 **Verification Role** 决定，不再只由 `requested_resolution` 决定（否则 check 的 highest 会误用 Baseline 的“未开始 floor 搜索”）。

| Role | REJECTED | INDETERMINATE |
| --- | --- | --- |
| `probe` | This candidate did not pass the required checks. PF will continue searching. | PF could not determine whether this candidate works, so it stopped this cell. |
| `baseline`（search） | The highest-version baseline did not pass, so PF did not start the floor search for this cell. | PF could not determine whether the highest-version baseline works, so it stopped this cell. |
| `baseline`（smoke） | The highest-version resolution did not pass the required checks. | PF could not determine whether the highest-version resolution works. |
| `declaration-capture` | PF could not capture a static baseline from the highest resolution of the current declarations, so it did not verify the declared lower bounds for this cell. | PF could not determine whether a static baseline can be captured, so it did not verify the declared lower bounds for this cell. |
| `declaration` | The declared lower bounds did not pass the required checks. | PF could not determine whether the declared lower bounds work. |
| Cell-scoped | （不能 Reject） | PF could not obtain the information needed to start or continue this cell. |

Journal 必须保存 Role，diagnose 才能在离线时选择正确 impact。不得从 `requested_resolution` 单独反推 Role。

## 9. Live CLI

D006 仍拥有布局。本文改变的是数据是否存在：

- `smoke` / `check` / `search` 的非成功 cell 第一行都写 `failed at <用户阶段>`；
- 都提供 `Diagnose:`，package 参数是正在验证的包名；
- check 不再有“无 FailureRecord 因此无 Diagnose”的例外；
- 成功 cell 仍不写 `failed at`。

`Diagnose:` 在 Journal 写入成功后才是事后可打开的入口。若 Journal 写入失败，卡片仍可展示 title、进程末 3 行和 Process Log 链接，但必须把 diagnose 入口视为不可用，不得打印事后 404 的 `failure_id`。

## 10. 模块所有权

| 规则 | 唯一所有者 |
| --- | --- |
| 验证运行、Journal、Role、check 的两次 Attempt 序列 | 本文 |
| diagnose 读取报告 ∪ `latest_journal`，不扫描 run 目录 | 本文 |
| Cell Completion 必须携带 stage 与 FailureRecord | 本文 |
| cause、disposition 矩阵、`failure_id`、title/next step | D005 |
| 上表 Role → impact 文案 | 本文；Presenter 只渲染 |
| `highest && STATIC_REGRESSION` 不得 Reject | D005 |
| `lowest-direct` 可对 `STATIC_REGRESSION` Reject | 本文扩展，由 `FailurePolicy` 实现 |
| 命令存在、退出码、`package-floor.json` apply 语义 | D001 |
| `prepare` 对三种 resolution 都返回 Attempt | `EnvironmentFactory`；接口形状见 D002 |
| check 不得 unwrap `PrepareFailure` | `CompatibilityChecker` |
| 完成投影 | `Scheduler` 内部；不新增公共模块，除非出现第二个真实投影 |
| Journal 文件与 diagnosis index 扩展 | `RunLogStore` |
| `failed at` 用户阶段名、卡片层级 | D006 |
| Process Log 原文与完整性 | D007 |
| probe 顺序 | D003 |

`FailurePolicy.classify` 仍不接收命令名或 Role。Role 是运行/展示事实，不是分类输入。

## 11. 对现行契约的取代

落地本文后，下列条款作废：

- D001 §6.1：`check` 的 `lowest-direct` 不建立 Attempt，非成功结果不能 `diagnose`；
- D001 §6.5：`diagnose` **只** 读取 `package-floor.json`，落地前不读取 Journal；
- D002 §8.2：`check` 的 `lowest-direct` 不创建 Attempt、prepare 失败直接 `ToolFailure`，`CompatibilityChecker` unwrap `PrepareFailure`；
- D005 §3.1：Attempt 只有 Baseline 与 Probe 两种；
- D005 §12 / §12.1：`diagnose` 只解释报告中的失败；`check` 是 Evaluation 不能 diagnose；
- D005 §12.3：impact **只** 由 `requested_resolution` 决定；
- D006 §9.1：没有 FailureRecord 的 check 失败不能提供 Diagnose；
- D006 §13.3：D008 落地前 check 的 Evaluation 失败路径没有 Diagnose；
- 实现：`CompatibilityChecker` unwrap `PrepareFailure`；`_completion_payload` 丢弃 `ToolFailure.stage`；Presenter 用 `STATIC_FAIL` / `BUILD_UNAVAILABLE` 猜 stage。

P004 中“check 兼容性失败仍走 Evaluation”是历史实施记录，不再描述现行目标。

## 12. 被拒绝的方案

- **把 check 的 FailureRecord 写入 `package-floor.json`。** 未搜索的验证会冒充 floor 证据，`explain`/`apply`/`merge` 被污染。
- **把 `lowest-direct` 做成 `exact-vector`。** 安装失败没有向量；Probe 还要求完整通过的搜索 Baseline，check 的 highest 故意不跑测试。
- **把 check 的 highest 捕获改名为单独的 `requested_resolution`。** 它与 smoke 的 highest 是同一解析请求；差别是 Role 和评价契约，不是 identity。
- **只用 `CellFailureScope` 给 check 分类。** Cell scope 不能 Reject，声明下界的确定性 ty/测试失败会变成 Indeterminate。
- **为 check 再写一个 FailurePolicy。** 分类矩阵只有一处；变化的是 Attempt identity 与 Role。
- **diagnose 遍历全部 `.pf/logs`。** 与 D005 禁止模糊查找日志同类。只认报告与 `latest_journal`。
- **只在 live 卡片上打印 `failure_id`、不写 Journal。** 命令结束后 Diagnose 入口是假的；smoke 今日已有此缺口。
- **check 的 highest 捕获失败一律叫下界不兼容。** 下界尚未评价；必须用 `declaration-capture` impact。

## 13. 验证契约

- `lowest-direct` prepare 失败保留 Attempt，`failure.stage` 为 `install` / `install-harness` 等 adapter 名；TTY 第一行是 `failed at installing dependencies`（或 harness），并有 Diagnose；
- check 的 `STATIC_FAIL` 分类为 Declaration Attempt + `STATIC_REGRESSION` + `ty`，不再依赖 `message == "STATIC_FAIL"`；
- check 的 highest 安装失败不启动 `lowest-direct`，diagnose impact 使用 `declaration-capture` 文案；
- Journal 不出现在 `explain` / `apply` 路径；把 Journal 误当作报告必须失败；
- `pf diagnose pkg --failure ID` 在仅跑过 check、没有 `package-floor.json` 时仍能展示该 ID；
- search 的 `failure_id` 在报告与 Journal 中一致；
- `CoordinateSearch` 测试不得读到 `lowest-direct` Attempt；
- smoke 结束后 `diagnose` 能打开本次 Baseline FailureRecord，不再留下悬空 ID。

## 14. 实施顺序

1. **Attempt 与分类**：`requested_resolution` 增加 `lowest-direct`；`FailurePolicy` / `rejection_is_supported` 接受它；`EnvironmentFactory` 为 `lowest-direct` 建 Attempt。
2. **check 错误链**：停止 unwrap；`CompatibilityChecker` 对两轮失败都 `classify`；调度投影始终带 `FailureRecord.stage`。
3. **Journal 与 index**：smoke/check/search 写 Journal；扩展 diagnosis index；`diagnose` 按 §8.3 读取。
4. **展示**：去掉 status 特判；check 与 smoke 同样输出 Diagnose；impact 按 Role 选择。
5. **入口验证**：真实 `check` 安装失败与静态回归都能 `diagnose`；`search` → `explain` → `apply` 仍只认报告。

每个阶段以公开 CLI 行为测试开始。不得保留“check 无 FailureRecord”兼容分支。

## 15. 不变量

1. 三种 `requested_resolution` 在外部操作前都有 Attempt；没有 Attempt 就不能 Reject。
2. Declaration Attempt 不是 Probe，不推进 D003 边界。
3. check/smoke 的 FailureRecord 只存在于 Journal 与本机 index，不授权 apply。
4. 非成功 Cell Completion 必有 `stage` 与 `FailureRecord`。
5. `FailurePolicy` 不接收命令名或 Role。
6. diagnose 不扫描 run 目录，不重放，不把 Journal 缺失说成新的兼容性失败。
7. 同一次 check cell 的捕获失败和下界失败必须能在 diagnose 中区分 Role。

## 16. 决策记录

### D1：`lowest-direct` 是第三种 `requested_resolution`（已确认）

它与 `highest` 一样是解析方式，不是事后向量。做成 Probe 会在 prepare 失败时失去 Attempt，并错误要求完整 Baseline `PASS`。

### D2：check/smoke 使用 Verification Journal，不写 floor 报告（已确认）

diagnose 需要持久化 FailureRecord；apply 需要未被验证运行污染的 floor 证据。本机 Journal 同时修好 smoke 卡片上事后无效的 `failure_id`。

### D3：diagnose 只认报告 ∪ `latest_journal`（已确认）

调查最近一次验证，不把 `.pf/logs` 做成隐式历史数据库。更早 run 的日志仍可按 Process Log 路径打开，但不出现在 `pf diagnose` 的默认列表里。

### D4：Role 选择 impact，不选择 disposition（已确认）

同一 highest Attempt 在 smoke 与 check 捕获中分类规则相同；用户影响不同。把命令塞进 `FailurePolicy` 会让分类器拥有文案。

### D5：check 每个 cell 两次 Attempt（已确认）

`S_hi` 捕获失败和下界验证失败是两个问题。合成一次 Attempt 会在 3.12 安装失败这类捕获期错误上谎称“下界不兼容”，或再次丢掉第一轮的 Attempt。

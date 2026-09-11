# D044 — PF check 稳态、最小验证序列与 apply 回执

- **状态：** 已接受待实施
- **日期：** 2026-09-11
- **性质：** 临时迁移 Design；已接受的目标契约，实施与验收完成前不表示已交付行为
- **目标 owner：** [D001](D001-pf.md)、[D002](D002-pf-implementation.md)、
  [D004](D004-pf-ty-enhancement.md)、[D006](D006-pf-cli-enhancement.md)、
  [D008](D008-pf-verification-run.md)、[D012](D012-pf-harness-relaxation.md)、
  [D014](D014-pf-report-schema.md)
- **验收标准：** [§8](#8-验收标准)
- **实施 Plan：** [P048](../plans/P048-pf-check-first-and-apply-receipts.md)
- **来源：** [C005](../concepts/C005-pf-check-first-lifecycle.md)；
  [R008](../reviews/R008-pf-search-performance-review.md) §10
- **关联：** [D003](D003-pf-search-algorithm.md) 搜索算法、
  [D005](D005-pf-failure-and-diagnose.md) verifier/prepare 资格、
  [D007](D007-pf-process-output.md) 日志与安全读取继续由原 owner 定义

本文收敛三个目标：check 每次验证当前声明；smoke/check 删除无关静态工作并减少必要环境准备；
apply 在既有严格准入后，把声明修改与报告中的应用回执一起提交为可恢复事务。

不引入 Git、check 历史或跨 Run 观察缓存。2026-09-11 按本轮接受条件补齐验证命令范围及
阶段间中断语义；本 Design 已接受，本轮仅授权修订文档与起草 P048，未授权生产实现。
完成实施和验收后，稳定规则吸收进目标 owner，本文件归档。

## 1. 产品周期与范围

```text
接入     pf smoke → pf search → pf explain → pf apply
稳态     pf check（可加入 CI）
重定界   作者主动治理，或 check 失败后选择 pf search → pf apply
```

- Search 报告回答固定契约 `C` 上得到什么 floor；apply 继续按当前内容 identity 严格授权。
- Apply 回执回答某个应用目标曾经成功满足；不表示当前工作树仍等于该目标，不产生长期管理权限。
- Check 只消费当前 package 声明与配置，不要求报告、回执、Git 仓库或曾执行 search/apply。
  每次 invocation 都重新准备环境；declaration prepare 成功后运行原 configured verifier 的完整命令阶段。
  历史 PASS、REJECTED 或相同 snapshot 均不跳过本次验证。
- Check 继续使用现行 `lowest-direct` 语义；不从旧报告恢复精确向量，不证明旧向量仍然最小，
  也不扩大到未运行的 Cell 或所有允许版本。源码/测试变化后，旧报告不自动取得新快照权威。
- Check 不搜索、不 apply、不读写报告，不保存“最近成功 identity”或供下次跳过验证的状态。
  现有 Process Log、Failure Journal 与诊断功能仍按原职责工作。
- 不增加“项目已被 PF 管理”的全局状态。用户自行提交、回滚或修改文件；Git dirty 与内容 drift
  是不同事实。Apply 的 source/policy/declaration 校验及 `--force` 有限豁免仍由 D001 拥有。

本次不覆盖：跨 Run ty/runtime/FailedCaseSet 复用、观察存储、check 成功历史、Git commit/检索、
增量 extra/受管依赖 apply、跨报告 apply 历史与用户回滚命令、跨 generation 证据继承、C006 影响面。
现有 Run 内 search 去重和两阶段算法继续由 D003/D004 拥有。

## 2. 替换的 owner 规则

| Owner | 接受并吸收后的变化 |
| --- | --- |
| D001 §5、§7 | 增加产品周期；smoke/check 零 ty，删除两命令的 --ty-jobs；无活跃 harness 时按命令区分静态行为；check 按 §3 条件取得 HarnessBaseline，每次执行完整 verifier |
| D001 §6、§9 | 将“公共报告不保存 apply-time scope/history”的笼统禁止收窄为允许 §5 的有限回执；保留现有准入、NOOP、source waiver 与 generation 隔离；两文件事务见 §6 |
| D002 §3、§5、§7、§9、§11 | SmokeCommandWorkflow 改用 SmokeVersionVerifier；移除 Smoke/Check static 依赖与 request 的 ty_jobs override；保留 Search cache；明确 ReportStore 准备报告替换、ProjectEditor 统一提交/恢复及公开测试 seam |
| D004 §7、§9 | static capture/compare/Direct-PASS 仅服务 Search；smoke/check 不取得 TyCheck、不登记 runtime PASS；Run 内身份和拒绝规则不变 |
| D006 §5–§7、附录 A.1、A.6 | 更新周期 epilogue、选项适用范围、真实 context/stage、harness preparation 摘要与 apply 回执展示；explain 的回执只表达历史事实 |
| D008 §2–§7、§10 | command-specific operation、最小 Attempt 序列、harness-prepare Role、初始 context、Check 聚合及准备失败 impact；总 deadline 只适用 Search；Smoke/Check 保留空 ty-cache sidecar 与空 static membership |
| D012 §3.1–§3.2、§6 | 按命令重写 baseline 取得规则：仅 Check DEGENERATE 可无 highest 构造 baseline；Search 包括全 fixed 仍取得完整 satisfaction evidence；同步 harness-prepare 名称，保留 resolution/install/graph 复证与 request/Attempt identity |
| D014 §1、§3–§5 | 新增必填 apply_receipts、回执 identity/reader/生成投影；报告 replacement 进入 apply 事务；search/update/merge 对回执的处理见 §5.3 |

D003、D005、D007 不新增 authority、failure cause、process provenance 或缓存规则。Schema 1 的
search evidence、generation、Attempt/Proposal/Failure identity 算法不因回执改写。

## 3. smoke/check 最小验证序列

StaticEvaluator 不参与 smoke/check；不运行 ty、不 capture/compare `S_hi`、不建立 Direct-PASS
ledger。准备失败仍走 D005；准备成功后的 PASS/REJECTED 只来自本次完整 runtime Evaluation。

Smoke 每个宿主 Cell：

```text
prepare(highest, original harness, DEVELOPMENT) → full evaluate → close
```

Check 在 prepare 前，通过 D012 唯一 harness policy owner，对已资格化的 active external
requirements 作纯决定：

```text
HarnessBaselineRequirement =
  REQUIRED     任一 active requirement 为 ceiling_eligible
  DEGENERATE   其余情况
```

Check 不另写 specifier/source 解析。该决定不增加持久 schema。

`DEGENERATE` 包括无 active external harness，以及全部 active requirement 为固定 source 或精确
`==X` / `===X`。构造同一 Cell 的普通 HarnessBaseline：保存排序唯一的 active external declaration
IDs，`observations=()`。这些 requirements 不删下限、不追加 ceiling，original/relaxed harness
语义相同。该 baseline 正常计算 digest 并进入 request/Attempt identity，不用缺失哨兵。
无 highest prepare 而构造此形状的权限仅属于 Check DEGENERATE；非空 IDs 加空 observations
表示本路径无需 baseline ceiling，不表示已经观察过 harness satisfaction。随后 declaration
仍按 D012 完整 resolve/install/inspect；active IDs 非空仍须复证 environment plan，不能改走
project-only 安装。

```text
degenerate HarnessBaseline
→ prepare(lowest-direct, baseline, SEARCH) → full evaluate → close
```

`REQUIRED` 仍从原始 harness 的最高环境取得 `U_B` 与 project/harness ownership：

```text
prepare(highest, original harness, SEARCH)
→ derive HarnessBaseline → close                 # 零 ty，零 verifier
prepare(lowest-direct, baseline, SEARCH)
→ full evaluate → close
```

首个 prepare 失败时不继续 declaration。只要任一 requirement ceiling-eligible 就走 REQUIRED；
`~=` 与 wildcard equality 仍属此分支。本次不在 lowest plan 形成后再懒启动 highest，不持久化
HarnessBaseline。D012 §3.1 的最高环境安装路径适用于 Smoke/Search baseline 与 Check REQUIRED：
有活跃 harness 时安装 `E(B)`，否则安装 `G(B)`，均完整复证 graph。Search 与 Check REQUIRED
保留供后续 relaxation 消费的完整 satisfaction observations，包含 fixed 项；Search 无活跃
harness 时得到 IDs/observations 均为空的 baseline。Smoke 不要求在成功结果中保留
HarnessBaseline 或 observations。Search 即使全部 harness fixed 也执行 highest prepare、
`S_hi` capture 和 full baseline verifier，再进入候选冻结与两阶段搜索；不得套用 Check
DEGENERATE 省略这一步。

## 4. 编排、Role 与展示

公开 operation 分别为：

```text
CheckCellOperations.check(package, cell, snapshot, source_plan, baseline_requirement)
SmokeCellOperations.verify(package, cell, snapshot, source_plan)
CellSearchOperations.search(..., run_cache)
```

`CompatibilityChecker` 与新增 runtime-only `SmokeVersionVerifier` 不接收 StaticEvaluator 或
TyCheckCache。SmokeVersionVerifier 与 Search 使用的 HighestVersionVerifier 位于现有
`baseline.py`，可共享 private preparation helper；HighestVersionVerifier 只供 Search。
不通过公开 purpose boolean 切换两种契约。

Runner 仍拥有 Run cache 生命周期与 Journal 提交，只向 Search 注入 TyCheckCache。
启用现有 Run 日志时，Smoke/Check 仍创建并持久化 `ty-cache.json`，其 entries 和 Journal
`static_membership` 均为空；不省略该 sidecar，也不增加成功历史表。沿用 D008 的
cache → Journal → latest 提交顺序与失败处理，不为这两条命令执行静态收集。

| 命令/路径 | Attempt Role | 初始及后续 context |
| --- | --- | --- |
| Smoke | baseline，full runtime | baseline |
| Check REQUIRED | harness-prepare 只取得 HarnessBaseline；之后 declaration full runtime | baseline → declaration |
| Check DEGENERATE | 只有 declaration | declaration |
| Search | 现行 baseline/probe | 现行 context |

Runner 仍唯一拥有 initial CellContextEvent；调用同一 harness policy 决定 Check 初始 context，
并把该不可变决定传给 Check，避免重复解析或不同分支。REQUIRED 在 declaration 开始前切换一次，
DEGENERATE 不发布 baseline，也不重复发布 declaration。只展示实际执行的 stage。

`harness-prepare` 干净替换 `declaration-capture`：同步 CheckCellOutcome、VerificationRole、
Journal 的 command/request/Role 校验、live/final projector 与 diagnose reader，不保留旧 Role alias。
它仍是 highest request，prepare 成功后不形成通过验证的 Evaluation；Role 不进入 Attempt、
Proposal 或 Failure identity。D008 §10 的该 Role impact 固定为：

- Rejected：`The highest-version harness baseline could not be prepared, so declared lower bounds were not verified for this cell.`
- Indeterminate：`Whether the highest-version harness baseline can be prepared is unknown, so declared lower bounds were not verified for this cell.`

D008 §6 的 Check 逐 Cell 结果选择改为：

- DEGENERATE 只使用 declaration 的结果。
- REQUIRED 若最高环境 prepare 失败，使用 `harness-prepare` 的原 Rejected/Indeterminate；
  不启动 declaration，不伪造下界 Evaluation。否则使用随后 declaration 的结果。
- 命令聚合仍为任一 Rejected → `COMPATIBILITY_FAILED`，否则任一 Indeterminate →
  `INDETERMINATE`，其余 → `PASS`。最高环境准备失败不证明声明下界不兼容。

`max-cells` 仅限制并发，排队不产生结果。Smoke/Check 沿用没有总 deadline 的命令表面，合法
Run 的 `max_duration_seconds` 必须为 None；正常完成覆盖全部选定宿主 Cells，typed failure
不截断其他 Cell。D008 §4 的未启动 deadline Cell 规则明确只适用 Search，
仍为 `TIMEOUT @ scheduler-deadline` 的 Cell-scoped Indeterminate，无 Attempt 或 initial context。
Smoke/Check 不新增对应 outcome variant。取消或基础设施异常造成未启动 Cell 时，按现行中断或
命令失败路径收尾，不给它补 capture/declaration 结果或 PASS；合法空 host Cell 集的处理不变。
REQUIRED 的 `harness-prepare` 已成功而 declaration 尚未开始时，若发生取消或基础设施异常，
同样走现行中断或命令失败路径；不得把成功的准备阶段当作 Cell PASS 或 declaration 结果。

D006 §7 的 Check 摘要同步更名：聚合为 `COMPATIBILITY_FAILED` 且含失败的 `harness-prepare`
outcome 时，使用 `Check failed · harness preparation did not pass · N cells`；只有 declaration
rejection 支持下界不兼容结论。聚合为 `INDETERMINATE` 时仍使用 unknown 摘要。
D005 title/next step、各命令数值退出码保持原契约。

D001 §5、§7 与 D006 附录 A.1 的命令表面同步为：

```text
pf smoke [--package PACKAGE] [--max-cells auto|N] [--test-jobs auto|N]
pf check [--package PACKAGE] [--max-cells auto|N] [--test-jobs auto|N]
```

两命令删除 `--ty-jobs` 及 CheckRequest/SmokeRequest 的同名 override 字段，不保留无操作选项。
Search/minimize 继续提供 `--ty-jobs`。共享 `[tool.pf].ty-jobs`、`ty-args`、`ty-timeout` 仍可配置并
按既有规则校验，但只影响 Search 的静态工作；Smoke/Check 不因这些设置执行 ty。共享 RunLimits
可保留解析后的 ty_jobs，不能据此把静态依赖重新注入 Smoke/Check。D001 §7 的无活跃 harness 规则
改为：smoke/check/search 均直接安装并复证 project plan、运行 configured verifier；只有 Search 仍运行 ty。

D006 附录 A.1 的 help epilogue 固定为：

```text
Onboarding: pf smoke -> pf search -> pf explain -> pf apply. Steady state: pf check. Use pf minimize to search and apply in one command.
```

双语 README 按各自语言表达同一流程，引用 owner；check 不是必须先 apply 才能使用的命令。

## 5. 报告中的应用回执

### 5.1 数据与 identity

Schema 1 新增顶层必填 `apply_receipts` 数组，按 `apply_id` 升序、唯一；新 search 报告为空。
它记录当前这份结果上不同应用目标的成功回执，不是跨报告历史、一次性令牌或 check 输入。

```text
ApplyReceipt {
  apply_id,
  floor_identity,
  target: ApplyTarget,
  applied_at                 # UTC，YYYY-MM-DDTHH:MM:SS.ffffffZ
}
ApplyTarget {
  package,                   # 报告中的 PackageIdentity
  scope,                     # DECLARED_MATRIX | PLATFORM_SCOPED
  selected_selectors,
  preserved_selectors,
  intended_declarations      # 完整目标声明组的规范投影
}
```

`ApplyTarget` 由 ApplyAuthorizer 在既有准入中冻结。它包含完整 intended groups，包括无需修改的组；
不使用 NOOP 时为空的 `authorized_edits` 代替。`intended_declarations` 是按 DependencyGroupKey
排序唯一的数组，每项为 `{key, projected_requirements}`。Key 保存 pyproject_path、location、name
及 optional 时必填的 optional_group；base 省略 optional_group。依次按 pyproject_path、location、
optional_group（缺席在字符串前）、name（规范 distribution name）排序。requirements 来自同一份报告上现有
`PackageReportBuilder.project` 的冻结输出，按完整字符串字典序排序、保留重复项。
Specifier、marker、source、extras 与 fixed/unmanaged 声明仍由原 projector 保持；不读取当前
TOML 原文来构造 target，也不新增独立 requirement semantic tuple。该 wire 编码由 D014 吸收。
Selector 数组按 `(sys_platform, platform_machine)` 排序唯一，selected 非空且与 preserved 不相交。

ReportStore 独占派生与复算。`wire_object` 是通过 D014 结构及 core 语义验证的 wire model
导出的 JSON 对象；`target_object` 必须来自回执 `target` 所使用的同一 ApplyTarget wire model，
按 `model_dump(mode="json", exclude_none=True)` 导出，沿用同一 required-nullable 保留规则与
规范数组顺序。生成与 reader 复算均使用这一投影，不另建字段选择或序列化算法：

```python
wire_object = wire.model_dump(mode="json", exclude_none=True)
core_object = {
    key: value for key, value in wire_object.items() if key != "apply_receipts"
}
floor_identity = sha256(
    b"pf:floor-result:v1\0" + canonical_identity_json(core_object)
).hexdigest()
apply_id = sha256(
    b"pf:apply-target:v1\0"
    + canonical_identity_json({
        "floor_identity": floor_identity,
        "target": target_object,
    })
).hexdigest()
```

core 完整包含 `schema_version`、`identity`、`inputs`、`evidence`、`cell_results`、`projections`、
`result` 及其全部嵌套 wire facts；只移除顶层 `apply_receipts`。D014 规定的 required-nullable
`null` 仍保留，其余缺失 optional facts 省略；数组保持 D014 及本节的规范顺序。
`canonical_identity_json` 只编码一次 JSON 对象：`sort_keys=True`、`separators=(",", ":")`、
`ensure_ascii=True`，UTF-8、无末尾换行。前缀末尾是一个 NUL byte，摘要输出 64 位小写 hex。
此预像不经过 D014 §4 的文件编码（`ensure_ascii=False`、末尾一个 LF）；原始文件 bytes
单独用于事务 revision/CAS，不作为 floor/apply identity 输入。

`report_generation_id` 不包含最终结果，不能独自充当 floor identity。同一完整冻结 core 和
同一 ApplyTarget 才保证同一 apply_id；仅最终 floor 向量相同不够。新 search/merge 若改变
core 中的 evidence、CandidateSnapshot 或 generator 等事实，即使向量未变，ID 也改变。
回执及其 applied_at、Git、本次 WRITABLE/NOOP 状态不进入预像；报告 core 中不另删减时间或
证据字段。原报告 generation/source/evidence 语义不因添加回执改变；回执不得把 apply 后
快照写成 search 快照。

Reader 按现行 strict/frozen、canonical、额外字段拒绝与 64 MiB 规则验证报告，同时：

- 从 core 重算 floor_identity，再从完整 target 重算 apply_id；
- 复证 package、selector partition、完整成功 roots 与 D001 授权投影，不能只信 digest；
- 通过现有纯投影 seam 检查 intended_declarations，不访问当前工作树、不重新授权；
- 校验 UTC 时间格式与排序唯一性；不同回执不能使用同一 apply_id。
  时间只定位历史，不授权 PASS、apply 或“最新记录优先”。

缺少必填数组的旧开发期 Schema 1 不提供兼容读取；实现时更新模型、fixtures、reader、
生成 schema/examples 和消费者。已有归档证据不回写。数组为空仅表示本报告无回执，
不证明项目从未应用过；数组非空不证明当前声明仍匹配。

### 5.2 成功、NOOP 与重入

每次 apply 都重新完成现行 D001 授权；回执不跳过 source、policy、scope、声明或 `--force` 检查。

| 当前状态 | 成功后的效果 |
| --- | --- |
| WRITABLE，尚无本 apply_id | 修改声明并新增回执 |
| NOOP，尚无本 apply_id | 声明确已满足目标，只新增回执 |
| NOOP，已有本 apply_id | 两文件字节不变，保留原 applied_at |
| 已有回执，用户恢复 original 且完整准入再次通过 | 按现行 WRITABLE 规则重新应用同一目标，保留原回执 |
| 任一现行准入失败 | 不修改声明、不新增或刷新回执 |

`applied_at` 是首次成功事务进入提交阶段时记录的时间；不记录调用次数或后续重入时间。
回执表达“PF apply 已确认并完成该目标”，不声称一定由 PF 首次写出这些声明，也不证明 apply
时重新运行过 verifier。用户复制、删除或回滚报告不会改变 check 的准入。

ApplyCommandResult 增加 `apply_id` 与 `receipt_created`，原 metadata changed 事实保持独立。
Apply/minimize 成功卡显示 Apply ID，并如实区分声明修改与仅新增回执；重复 NOOP 不报新写声明。
Explain 可离线显示 `Apply receipts: N`，只陈述所读报告的历史，不推断当前授权或匹配状态。

### 5.3 Search、merge 与持久化

为避免引入应用历史系统，search 新建/更新输出与 merge 输出统一建立 `apply_receipts=()`。
Reader 仍须先验证输入中的回执；非法输入按现行 read/merge/update 的错误规则处理。
不合并回执、不转移到新 floor，不把回执差异当成 Cell evidence 冲突。

原 generation compatibility、roots 冲突与更新范围保持 D014 契约。再次 apply 可经现行 NOOP
准入建立回执；报告被替换后不保证保存旧回执。原样 read/write 同一 validated report 则保留其回执。
Check、diagnose 不利用回执形成 compatibility 结论；回执不参与 source snapshot。

## 6. 两文件事务与恢复

### 6.1 唯一职责与公开 seam

| Module | 职责 |
| --- | --- |
| ApplyAuthorizer | 现行严格授权；冻结报告 floor_identity、完整 ApplyTarget 和实际声明 edits，不做 I/O |
| ReportStore | 从同一次读取的报告及原始 revision 准备新增回执；codec、identity、完整报告验证和不可变 replacement |
| ProjectEditor | 统一事务的 snapshot/raw revision 复核、两文件发布、durable recovery/rollback；不解释 report wire |
| Apply workflow | 在恢复后 load/read/snapshot/authorize，传递冻结值；不拼 JSON、不复制投影或 digest 算法 |

ReportStore 的 apply read 必须把 ValidatedReport、派生 floor_identity 与实际读取 bytes 的 revision
绑定；grant 固定同一 floor_identity，准备回执时必须与 read handle 相等，不能混用另一份报告的授权。
`prepare_apply_receipt(read_handle, authorized_target, ...)` 只准备、不发布文件，返回已验证的
完整目标 report replacement（相对路径、expected revision、原/目标 bytes 或等价封闭句柄）。
ProjectEditor 将它与授权 TOML edits 作为同一事务的 participants；写后复证目标 bytes。
ReportStore 不单独提前写回执，Editor 不进口 wire records 或读取 `_wire`。

现有 ProjectEditor 的 recovery 入口移到 apply 的 project load、report read、snapshot 和授权
之前；持有本次事务保护直至完成。恢复之后重新读取并授权，不使用恢复前的任何 grant。
Minimize 进入同一 apply workflow；旧未完成 apply 必须在其 search/report 更新之前先恢复。

Apply/recovery 及其他 PF report writer 通过持久化 owner 的 private implementation 协调写入，
不要求 Search/Merge 注入 ProjectEditor，不新增通用 filesystem/repository/transaction service：

- Editor 先取得 workspace guard，再按规范路径顺序取得 report guards；普通 ReportStore writer
  只取输出路径 guard，不反向获取 workspace guard。Merge 的任意输出路径同样按路径保护。
- Apply 的 report revision 在取得 guard 后复核，guard 保持至事务完成；update_path 的保护覆盖
  read existing、合并/替换决定与 publication 全程。底层锁机制由 Plan 选择并证明跨进程互斥。
- 首次 replace 前，为 report participant durable 登记 pending transaction，绑定可信 recovery
  定位与事务 identity。即使崩溃释放锁，普通 report writer 看到 pending 仍拒绝发布并提示先
  重新运行 apply 恢复；它不调用 Editor、不解释业务恢复。pending/guard 失败不能进入现行
  update 对非法 existing report 的替换分支。恢复或提交完成后才清理标记。
- 本机公开 report read 也受路径 guard/pending 保护：未恢复的 participant 不返回可消费的
  ValidatedReport，Explain 不展示其未提交回执。Reader 不执行恢复；独立只读报告的读取能力
  必须保留，不能为一次 read 要求报告或其父目录可写。具体只读 guard 机制在 Plan 中证明。
- 锁、revision 与 pending registration 通过 private 实现共享，不成为新的公开注入服务。
  取消释放进程资源；durable pending 状态保留给下次恢复。

### 6.2 完成点与失败

这里的原子性指可恢复的 all-or-nothing completion；两个文件不能靠两次 replace 对任意外部读者
提供瞬时共同可见。每份文件自身原子替换，PF 事务内读写遵守上述协调。

```text
恢复既有事务 → 重新规划/读取/授权 → 准备并验证两个目标
→ 保存恢复记录与原始备份
→ 复核 snapshot 与各 participant 的原始 revision
→ 发布声明与报告 → 复证全部目标 → durable COMMITTED
→ 清理恢复材料 → 返回成功
```

- Recovery record 绑定 workspace、participant 相对路径、原/目标 revision、备份与事务状态。
  在任一 replace 前它及必要备份必须 durable；成功点是全部目标与 COMMITTED 都已 durable。
- COMMITTED 前失败或中断，恢复到事务前两份内容；任何阶段都不能只消费报告或只完成声明后报成功。
  首次 NOOP 也参加事务，即使唯一待写文件是报告；已有回执的完整 NOOP 不产生新事务写入。
- 恢复先验证全部 participant/备份，再改任何文件。当前 bytes 只可等于原值或该事务目标值；
  发现第三种值、丢失或不可信恢复材料时保留证据、拒绝覆盖，不继续新 apply。
- COMMITTED 已 durable 后，只完成清理，不能因 cleanup 失败回滚目标或丢弃成功回执。
  下次 invocation 恢复清理后重新授权。用户中断的退出与已发 final 规则仍按 D001。
- PF writer 竞争须序列化或因 revision 改变而失败；报告须校验读取 revision，因为它被排除在
  SourceSnapshot 外。非配合的外部编辑在 snapshot、raw revision 和恢复检查点检测；
  不宣称两个 replace 能阻止任意瞬时外部写入。
- 新增 participant 的路径、备份、锁与 recovery 文件沿用现有安全要求，拒绝越界、symlink/
  reparse 替换和不可信 locator。恢复不能覆盖未选中 package 或新出现的用户修改。
- 当前内容/报告漂移按现行配置或授权失败处理；事务 I/O、恢复不可完成按基础设施失败处理。
  不制造 Verification FailureRecord、compatibility disposition 或成功回执；SIGINT 仍退出 130。

`package-floor.json` 是回执的持久位置；`.pf/` 只保存协调、完成和恢复该事务所需的材料。
不保留“昨天那次成功 apply”的可回滚历史。具体 journal schema 与临时路径由 Plan 决定。

## 7. 不变量

1. Check 与报告/apply 历史解耦；每次 declaration prepare 成功后都运行完整 verifier，无跨 Run 复用。
2. Smoke/Check 零 ty；只有 ceiling-eligible harness 令 Check 建立 highest Attempt。
3. 回执不授权当前 apply、check 或跨 generation PASS；现行严格准入与 NOOP 继续有效。
4. 同一冻结 floor 结果和应用目标产生同一 apply_id；时间与回执自身不形成 identity 循环。
5. 声明和回执共同完成或可恢复；无成功事务就没有新的成功回执。
6. 用户负责版本控制；PF 不创建 commit，不要求 Git clean，不维持项目管理状态。
7. Search/update/merge 不继承回执；回执不是成功 check 历史或跨报告应用历史。
8. 完整 search evidence 和临时事务材料分家；ReportStore 拥有 wire，ProjectEditor 拥有事务。

## 8. 验收标准

按 [tests/README.md](../../tests/README.md) 选择公开 seam。进程内 recording adapter 证明路径和调用
次数；schema 构造不代替产品行为。真实 CLI 连续运行证明跨进程持久化及恢复，不能由单进程替代。
实现 Plan 为每项保留具体命令、结果和日志；本文不填写尚未取得的 PASS。
本 Design 保持合并范围，验收分轴记录：AC1–AC7 为命令序列，AC8–AC14、AC16 为回执/事务，
AC15 为两轴的真实流程闭合，AC17 为交付闭合。命令轴通过不能代替回执/事务轴的验收；后者须
独立核对 D001/D002/D014 吸收文本及 AC12 的真实进程证据。

| ID | 标准 |
| --- | --- |
| AC1 | D001、双语 README 与 D006 help 表达 §1 周期；smoke/check CLI 与 typed request 只保留适用的 scheduling override，search/minimize 保留 --ty-jobs；共享 ty 配置不触发 smoke/check 静态工作；当前声明即使来自手写、无报告或回执，也可 check；不引入 Git 或自动 search |
| AC2 | Smoke 每 Cell 只 prepare highest 一次、full verifier 一次、ty 零次；相同快照再次 invocation 仍实际执行 verifier |
| AC3 | Check 无 active harness 与全部 fixed 两类 DEGENERATE 场景只 prepare lowest-direct；baseline 含正确 active IDs 与空 observations；fixed requirements 不删下限、不加 ceiling；非空 active IDs 仍完整复证 environment plan |
| AC4 | Check REQUIRED（含 ~=、wildcard equality）先 prepare highest 取得完整 HarnessBaseline、零 ty/verifier；成功后 declaration prepare 并 full verifier；highest 失败不启动 declaration |
| AC5 | 同快照连续两次真实 check 均执行完整 verifier；源码/测试变化后仍执行；不读写报告、不要求 apply provenance、不保存成功 identity 或观察缓存 |
| AC6 | Smoke/Check 构造不接收 StaticEvaluator/TyCheckCache；Search 包括全部 fixed harness 仍取得完整 highest satisfaction evidence 并执行 S_hi/full baseline；highest/static/candidates/probes 顺序及公开结果保持现行契约 |
| AC7 | DEGENERATE 初始 context 只有 declaration；REQUIRED baseline→declaration 恰一次切换；harness-prepare 的 typed outcome、Journal、live/final/diagnose 语义闭合；无 ty/static stage；有 Run 日志时实际持久化空 ty-cache sidecar 和空 Journal membership；多 Cell、max_cells=1 的两分支及失败混合遵守 §4 聚合并全部调度；未启动或 highest prepare 成功后、declaration 开始前的中断/异常不伪造结果或 PASS；Search 保留 Cell-scoped deadline |
| AC8 | Report strict reader 与生成投影覆盖必填 apply_receipts、空/有效多目标回执、canonical bytes、排序、时间、floor/apply identity 复算与目标投影验证；回执不能改变已有 evidence/generation identity |
| AC9 | 含非 ASCII 字符与 required-nullable null 的固定摘要向量证明 §5.1 单一预像；同 core/target 在 WRITABLE、NOOP、增删回执或改变 applied_at 后 ID 相同，当前 TOML 排版/顺序不是 target 输入；同 generation/向量但 evidence/core 不同仍改变 ID，package/scope/目标投影变化亦然；允许的报告文件编码差异不改变语义 ID，但仍触发 raw revision 检查；跨 floor grant 不能混用 |
| AC10 | 首次 WRITABLE 修改声明并新增回执；首次 NOOP 只新增回执；再次完整 NOOP 两文件及时间字节不变；结果/终端分别表达 metadata changed 与 receipt_created |
| AC11 | 已有回执仍执行全部现行授权；source/policy/declaration drift 与 --force 边界有公开证据；合法恢复 original 后可重入；Git dirty 本身不阻止内容一致的 apply |
| AC12 | 在备份、每个 participant 发布、目标复核和 COMMITTED 前后的代表性故障下，成功/回滚/清理符合 §6；至少在报告已替换但 COMMITTED 前外部强制终止真实子进程，绕过 finally/进程内 rollback，随后 read/explain 不确认回执、新 apply 在 load/authorize 前恢复；保留进程退出及连续命令证据，不能以进程内 adapter 替代 |
| AC13 | 报告 revision 变化独立于 SourceSnapshot 被检测；并行 apply 与 search/merge 同报告写入不丢更新；恢复遇第三种 bytes/不可信路径保留证据且不覆盖用户修改；无 pending 的独立只读报告仍可 read/explain |
| AC14 | search/update/merge 输出清空回执，报告证据与 generation 规则保持原契约；read/write 原报告保留回执；Explain 只显示历史，不从回执推断当前授权 |
| AC15 | minimize 使用同一 apply 事务；未完成旧事务早于 search planning/snapshot 恢复；真实 search→apply→重复 apply→修改源码→check 场景闭合，check 不改报告 |
| AC16 | 无 Git 仓库也能完成 apply/check；报告、回执、事务文件均不进入 SourceSnapshot；跨 target/selector 的 apply 投影依 tests/README.md 展开公开语义用例 |
| AC17 | 所需 daily/PR/coverage 车道、文档及 schema/example 生成检查完成；逐项审计 AC，完成 §9 owner 吸收后归档本 Design 与实施 Plan |

## 9. 吸收与后续

按 §2 回到各唯一 owner。D014 吸收回执 wire/identity/reader 与报告 publication；D002 吸收
事务 seam、恢复时序与验证边界；D001 吸收产品周期与有限回执。D006/D008 补齐 context、
impact、Check 聚合、help 和回执展示；D004/D012 吸收 Smoke/Check static 与按命令取得
HarnessBaseline 的规则。逐条替换旧全称句、Role 和选项适用范围，不只追加新 enum/段落。

吸收时才在 CONTEXT 增加“应用回执 / ApplyReceipt”与“应用目标 / ApplyTarget”现行术语，
明确不表示当前管理状态、一次性消费令牌或 check 证据；实施前不提前改写现行词汇。
模型、公开测试、生成 schema/examples、README、owner map 与全部现行入链在同一完成变更闭合。

C005 继续跟踪增量 apply、跨 Run 观察复用和可回滚历史的待证构想；均须独立论证并进入后续
Design。R008 的跨运行缓存非目标不被本文件撤销，C006 不是本次前置。

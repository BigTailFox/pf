# D036 — PF 统一执行失败契约

- **状态：** 已完成并归档；2026-09-06 通过 AC1–AC13 验收，稳定规则已由现行 owner 接管；实施与证据见 [P041](../plans/P041-pf-execution-failure-contract.md)
- **日期：** 2026-09-06；已按同意的修订方案明确 context 读取边界、准入失败路径和目标 cause 清理
- **核对基线：** `d06c3d1d8533f54174c1f3498e674ce0792bbbca`；起草前工作区干净
- **来源：** [E007 §6](../../experiments/E007-mkdocs-baseline-and-build-failures.md#6-准备责任与后续契约方向)
  已确认的“可靠归因优先、执行契约兜底”方向
- **现行 owner：** [D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)、
  [D003](../../designs/D003-pf-search-algorithm.md)、[D004](../../designs/D004-pf-ty-enhancement.md)、
  [D005](../../designs/D005-pf-failure-and-diagnose.md)、[D006](../../designs/D006-pf-cli-enhancement.md)、
  [D007](../../designs/D007-pf-process-output.md)、[D008](../../designs/D008-pf-verification-run.md)、
  [D012](../../designs/D012-pf-harness-relaxation.md)、[D013](../../designs/D013-pf-pytest-observer.md)、
  [D014](../../designs/D014-pf-report-schema.md)

本文保存已完成的执行结果、失败证据与报告身份迁移。E007 的方向经本 Design 接受、P041 规划与
实施验收后，稳定规则已归并现行 owner，Design 与 Plan 同步归档。正文保留迁移时的目标与理由，
不再承担现行规范；实验的历史 disposition 不回写。

## 1. 问题与决策

当前 configured verifier 已把正常非零退出映射为 Rejection；prepare 阶段却只有已认证的 project /
harness UNSAT 可以拒绝，build/install 的正常失败仍终止 Cell。E007 中 Jinja2 2.0 的构建失败因此
阻断后续搜索，尽管 PF 已观察到有效请求没有通过构建操作。另一方面，当前
`UvAdapter._classify` 与 `uv_diagnostics` 的部分 source/build cause 来自自由文本匹配，不能仅因
cause 名称看起来明确，就把它提升为新契约中的可靠归因证据。

采用以下唯一默认语义：

1. 验证执行先满足 PF 的请求准入和必要观察条件。有经资格验证的归因时按其含义 dispatch；
   没有可用归因时，正常非零退出拒绝当前 Attempt。
2. 超时、信号、启动失败、终态不可得，以及已确认的外部执行障碍、协议或一致性破坏仍为
   Indeterminate；PF 未建模异常沿现有 InfrastructureError 路径结束。
3. 严格保留成功产物、精确安装图和完整 verifier PASS 要求。普通非零只证明本次执行契约未通过，
   不自动证明 UNSAT、某个依赖是根因、可重复失败或整个版本区间不兼容。
4. Adapter 提供阶段事实和归因凭据；共享的执行分类规则决定 disposition。搜索、workflow、
   reader 与展示层不解析工具输出，也不各自维护 cause 白名单决定拒绝。
5. 新证据显式区分归因与兜底。失败身份、evaluation policy、报告读取、缓存和 apply 同步迁移。

这是工程搜索的取舍：后端可能把网络、权限或内部错误包装成普通非零；无法可靠识别时允许误拒绝。
误拒绝可能使结果下界偏高、遗漏可行向量，甚至没有结果。增加重试次数不能证明归因，本设计不增加
自动重试、quorum 或“实用/严格”两套模式。

## 2. 适用操作与准入

兜底作用于有真实 Attempt 的验证操作，而非任意 PF 子进程：

| 操作 | 正常非零、无可靠归因 | 成功继续条件 |
| --- | --- | --- |
| `resolve-project` | REJECTED / `RESOLUTION_FAILED` | 完整、有效的 project plan 与 request/source/artifact 一致性 |
| `resolve-environment` | REJECTED / `RESOLUTION_FAILED` | 有 active external harness；完整 plan 精确保留 project selection |
| `install-project` / `install-environment` | REJECTED / `INSTALLATION_FAILED` | 选定 final plan 安装成功；随后 inspection 复证实际图 |
| 上述操作内的 build | 父操作按统一规则拒绝；首轮不注册 build 归因，使用父操作兜底 cause | 服从父操作的成功产物要求 |
| `test` | REJECTED / `VERIFIER_EXITED_NONZERO` | 完整配置命令 NormalExit(0)，且前置准备与验证资格成立 |

当前 uv 将 build 嵌入 resolve/install，没有 PF 独立 build 子进程。失败 stage 保留实际父操作，
不得从日志制造一次 `build` Attempt、子进程 terminal 或已生成 plan。Jinja2 案例无需先识别 Python 2
语法；首轮记录 RESOLUTION_FAILED 或 INSTALLATION_FAILED，build 日志只供诊断。

有效请求指：PF 已完成相应配置、SourcePlan、Cell、request/plan identity、工具版本与 interface
profile 的准入，Adapter 使用该请求实际启动了操作。它不要求预先证明依赖可满足、编译器存在、
测试能收集或后端没有缺陷。PF 的 argv 投影错误、跨请求凭据、已知不支持的工具/interface profile
不能经普通非零兜底掩盖。

以下继续服从各自协议，不采用候选拒绝兜底：

- Attempt 以前的 planning、candidate discovery、source snapshot 与调度失败：Cell Indeterminate
  或已有配置/基础设施错误；不补造精确向量。
- 解释器查询、venv 创建、interpreter/installed graph inspection：这是执行前提与一致性观察；
  失败仍为 Indeterminate。即使已建立临时 Attempt，也不因此获得拒绝资格。
- `ty` 非零可能携带有效静态诊断，必须按 D004 解码，不能直接拒绝；static-only 仍无 disposition。
- runtime witness 的状态协议保持 D004：只有 `CONFIRMED_MISSING` 拒绝；其异常非零不兜底。
- 用户 Ctrl+C 继续由 D007/D008 清理和退出 `130`，不生成候选拒绝。

## 3. 分类顺序与成功要求

统一规则按以下顺序执行；上层不得跳过前置条件直接检查 exit code：

| 优先级 | 已取得的事实 | 结果 |
| --- | --- | --- |
| 1 | 已取得 §4.3 的有效结构化事实（请求/身份、访问、产物或图检查失败） | 按该表唯一 cause/authority 形成 INDETERMINATE；未建模异常或非法内部对象为 InfrastructureError |
| 2 | timeout、signal、start-failed、terminal-unavailable | INDETERMINATE；timeout 优先于超时清理后观察到的 exit/signal |
| 3 | NormalExit(0) | 通过该操作全部必要产物和一致性检查后才是阶段成功；否则 INDETERMINATE |
| 4 | NormalExit(nonzero) + 合格归因 | 按 §4 固定表映射 |
| 5 | NormalExit(nonzero) + 无可用归因 | 按 §2 拒绝本次操作所属 Attempt |

非零退出后的残留 lock/wheel 不是成功产物，不进入成功解析或安装分支。独立观察到的 hash mismatch、
跨请求产物等仍触发优先级 1；不能把正常失败未生成成功产物本身再当作优先级 1，吞掉所有兜底。
NormalExit(0) 与有效的失败归因同时出现属于权威事实冲突；不回退 PASS 或 Reject。

输出区分用途：

- terminal 来自 ProcessRunner 的进程事实，diagnostic log/tail 缺失或截断不抹去正常退出。
- 归因依赖的协议或完整 diagnostic envelope 不完整时，该归因不可采用；若仍有正常非零，使用兜底。
  可选 diagnostics 损坏不等于必需成功产物损坏。内部已声明为合法的凭据若字段矛盾，则失败关闭。
- 成功路径仍要求当前 D007/D012 规定的完整输出与有效 native plan；本设计不实施
  [C003](../../concepts/C003-pf-resolution-output-completeness.md) 的成功日志完整性放宽。
- 配置 verifier 的 stdout/stderr、pytest summary/detail/phase/退出码类别继续只作诊断；没有针对
  pytest 1/2/3/4/5 的可靠外因 dispatch。PF 不从 traceback 判断“测试代码”或“环境”谁负责。

归因框架对各验证操作采用同样的准入标准；某工具没有合格的细分协议，就使用终态兜底。统一规则
不要求为每个阶段发明归因器，也不允许把可选诊断升级成必须通过的执行 gate。

## 4. 首轮归因契约与完整映射

### 4.1 冻结的归因集合与 wire

首轮只准入当前 D012 的 uv UNSAT 两类证明。固定注册表为以下唯一一项：

```text
tool = "uv"
tool_version = "0.12.5"
protocol = "uv-pip-compile-pylock-v1"
profile = "uv-diagnostics-0.12.5-v1"
codes = ["direct-version-contradiction", "transitive-version-contradiction"]
```

`ExecutionAttribution` 是封闭判别 union，以下列出了全部 wire 字段；均为 required，拒绝额外字段：

```text
Unattributed = {kind: "unattributed"}
UvUnsatAttribution = {
  kind: "uv-unsat",
  tool: "uv", tool_version: "0.12.5",
  protocol: "uv-pip-compile-pylock-v1",
  profile: "uv-diagnostics-0.12.5-v1",
  request_binding: OperationRequestBinding,
  facts: DirectContradictionFacts | TransitiveContradictionFacts
}
DirectContradictionFacts = {
  code: "direct-version-contradiction",
  stdout_complete: true, stderr_complete: true
}
TransitiveContradictionFacts = {
  code: "transitive-version-contradiction",
  stdout_complete: true, stderr_complete: true
}
```

`facts` 以 `code` 判别；两个 completeness 字段必须是 JSON boolean true，不接受 false、整数或
缺省。它们证明采用该 profile 时的读取条件，不携带 stdout/stderr、diagnostic digest、自由文本
cause 或任意扩展 dict。profile 常量与两个 code 进入 wire Literal/enum，不设插件注册入口。
`OperationRequestBinding` 的字段和规则在 §6.1 冻结。

UvUnsatAttribution 只允许 `resolve-project` / `resolve-environment` 的 NormalExit(1)。producer
必须校验运行时 ResolutionContext 的 tool version/protocol/profile 与凭据逐字段相等；这是运行时
准入职责，不是离线 reader 的能力。producer 沿用现有 D012
UNSAT admission：完整输出、已准入版本、完整矛盾诊断形状，并排除现有 source/build/availability
歧义。具体既有行为以核对基线的
[`uv_diagnostics.py`](../../../src/pf/adapters/uv_diagnostics.py) 及
[`qualification manifest`](../../../tests/uv_qualification/matrix-manifest.json) 的两类 UNSAT 为依据；
首轮不扩展该 matcher 的识别能力，不声称它已具备一般的后端输出防伪能力。

profile 返回 UNSAT 时保存相应 typed facts；其余返回值一律没有 qualified attribution。
stderr 截断、未知 shape、source/build 短语或 candidate availability 均不能提升为 UNSAT。
reader 只验证固定 tool/version/protocol/profile 字面量、typed facts、Attempt binding、stage 与
terminal 的自洽性，以及 Attempt 的非空 resolution_context_digest 和可复算的身份。报告不保存
ResolutionContext preimage，因此 reader 不复算 context digest，也不证明 attribution 与该摘要
未保存的内容相等。它不读取日志重新推导求解证明；这仍是 PF producer 对工具观察的记录，不是
可抵抗恶意重写报告的密码学证明。

`build-backend-failed`、`source-unavailable`、`environment-unavailable`、`artifact-invalid`、
`tool-protocol-failed` 均**不注册为工具归因 code**，wire 不接受这些 code。构建、下载、权限等
普通 uv 非零在没有 PF 直接结构化事实时走兜底；不沿用旧文本 cause 作 disposition authority。
PF 自己观察的权限、产物、请求和图检查失败走 §4.3 的结构化分支，与工具归因注册表无关。
Plan 只实现和验证这套固定集合，不选择首轮 code/profile/facts。将来扩展须单独修改 Design、
资格证据和 policy identity；本次验收失败不能通过悄悄删减已冻结的 UNSAT 集合交付。

### 4.2 Terminal 的穷尽映射

本表覆盖本次迁移的 operation 集合。`R` = 两个 resolve stage，`I` = 两个 install stage；
`A` = `create-environment | inspect-interpreter | inspect`，是有真实 Attempt 的辅助执行；
`Q` = `inspect-project-plan | inspect-environment-plan | proposal-vector`，只产生结构化检查事实。
`V` = `test`。每个 stage 是上述明确字符串，不按前缀匹配未知 stage。

| Stage | terminal / attribution（没有更高优先级的结构化事实） | Disposition | cause | FailureRecord authority |
| --- | --- | --- | --- | --- |
| R / I / A | TimedOut / Unattributed | INDETERMINATE | TIMEOUT | execution |
| R / I / A | Signaled / Unattributed | INDETERMINATE | TOOL_FAILURE | execution |
| R / I / A | StartFailed / Unattributed | INDETERMINATE | TOOL_FAILURE | execution |
| R / I / A | Unavailable / Unattributed | INDETERMINATE | TOOL_FAILURE | execution |
| resolve-project | NormalExit(1) / UvUnsatAttribution | REJECTED | RESOLUTION_CONFLICT | execution |
| resolve-environment | NormalExit(1) / UvUnsatAttribution | REJECTED | HARNESS_CONFLICT | execution |
| R | NormalExit(nonzero) / Unattributed | REJECTED | RESOLUTION_FAILED | execution |
| I | NormalExit(nonzero) / Unattributed | REJECTED | INSTALLATION_FAILED | execution |
| A | NormalExit(nonzero) / Unattributed | INDETERMINATE | TOOL_FAILURE | execution |
| R / I / A | NormalExit(0)，且必要成功检查通过 | 阶段成功 | 无 | 不产生 FailureRecord |
| V | NormalExit(nonzero) | REJECTED | VERIFIER_EXITED_NONZERO | configured-verifier |
| V | TimedOut | INDETERMINATE | TIMEOUT | configured-verifier |
| V | Signaled / StartFailed / Unavailable | INDETERMINATE | TOOL_FAILURE | configured-verifier |
| V | 完整 configured verifier 的 NormalExit(0) | PASS | 无 | 不产生 FailureRecord |

正常退出必须有非负 strict integer exit code；signal 必须是正 strict integer。timeout 先于清理后的
exit/signal 归一化为 TimedOut；其他四种 terminal 的形状保持现有协议。Q 不接受 ExecutionFailure。
任何不在表中的组合（包括 install 的 UNSAT、exit 2 的 UNSAT、NormalExit(0) 的 ExecutionFailure、
辅助操作的 Reject）都是非法对象；producer 报 InfrastructureError，reader 拒绝报告，不能兜底。
有效原始观测中出现权威事实冲突时，producer 必须先构造下一节的 evidence-conflict，而非创建
一个非法 ExecutionFailure 后期待 classifier 修复。

### 4.3 Structured fact 的穷尽映射

`StructuredOperationFact` 是以 `code` 判别的封闭 union。下表每个 code 是一个独立变体，
**字段只有该字面量 code**；其观察位置由 stage 约束，身份由所属请求、实际已取得 plan 和 terminal
绑定。正文、路径、异常 message、期望/实际图详情只进入运行期 diagnostics，不作任意 fact 字段。
它记录 PF 已执行相应检查并失败，不假装离线重做缺失的成功产物检查。

下表全部产生 INDETERMINATE，authority 全部为
`operation-structured(fact, terminal)`；terminal 字段 required-nullable。`T?` 表示该操作若实际
有进程观察就保存归一化 terminal，无进程则 JSON null；禁止借用另一操作的 terminal。

| fact.code | 允许 stage | 必需 terminal / 直接观察条件 | 唯一 cause |
| --- | --- | --- | --- |
| request-invariant | R / I / A / Q | T?；当前合法请求与实际投影/绑定不一致 | INTERNAL_INVARIANT |
| evidence-conflict | R / I / A / Q | T?；直接观察的权威事实互斥 | INTERNAL_INVARIANT |
| source-access-failed | R / I | T?；PF 直接 source I/O 异常，非子进程错误文本 | SOURCE_FAILURE |
| environment-access-failed | R / I / A | T?；PF 直接文件/环境权限操作失败，非猜测后端缺工具 | ENVIRONMENT_FAILURE |
| artifact-invalid | R / I | T?；PF 直接 hash/格式/metadata 校验失败 | SOURCE_FAILURE |
| resolution-output-incomplete | R | NormalExit(0)；成功路径所需完整 output 不可得 | TOOL_FAILURE |
| resolution-plan-invalid | R | NormalExit(0)；native plan 缺失、不可读、解析或投影失败 | TOOL_FAILURE |
| artifact-policy-mismatch | R | NormalExit(0)；解析产物违反 artifact policy | INTERNAL_INVARIANT |
| managed-source-leakage | resolve-project | NormalExit(0)；SEARCH 的 managed selection 泄漏 local/editable source | INTERNAL_INVARIANT |
| managed-source-mismatch | R | NormalExit(0)；source/artifact selection 不闭合或 environment 未精确保留 project | INTERNAL_INVARIANT |
| interpreter-observation-invalid | inspect-interpreter | NormalExit(0)；必需完整输出/结构不可用 | TOOL_FAILURE |
| interpreter-mismatch | inspect-interpreter | NormalExit(0)；实际 implementation/Python minor 不符合 Cell | ENVIRONMENT_FAILURE |
| graph-observation-invalid | inspect | NormalExit(0)；必需完整输出/图结构不可用，含重复不一致节点 | TOOL_FAILURE |
| installed-graph-mismatch | inspect-project-plan / inspect-environment-plan | null；已观察图与对应 final plan 不一致，图观察进程属于 inspect | INTERNAL_INVARIANT |
| proposal-vector-mismatch | proposal-vector | null；实际 managed vector 与请求不一致 | INTERNAL_INVARIANT |

其中权限错误只在 PF 执行环境 I/O 时归入 environment-access-failed；显式 source 读取失败归入
source-access-failed；native plan 的读取失败统一归入 resolution-plan-invalid。缺 managed node
统一为 installed-graph-mismatch，不在 inspect 再创建无 code 的 invariant。非法输入配置仍按
D001 在执行前报配置错误；未列出的 PF 异常按 InfrastructureError，不以任意新 code 或 TOOL_FAILURE
记录凑齐。发生多个事实时，固定优先级为 request-invariant → evidence-conflict → 其余表中顺序；
只采用第一条适用事实。只有真实执行相应检查才能产生该 fact，不能通过检查本应缺失的失败产物
人为制造高优先级失败。表外 code/stage/terminal 在生产和 reader 中均拒绝。

上述 15 类 fact 覆盖所有本次新结构化 authority。不支持的 uv version/profile 在建立 Attempt 前
被拒绝；EnvironmentFactory 无法建立 resolver protocol 时抛出 ConfigurationError，不产生
PrepareFailure 或 FailureRecord，也不补造 Cell/Attempt。其他已有合法 Cell scope 的
candidate/source planning failure 继续按 D005 的 process/structured 路径，只能 Indeterminate；
runtime witness/ty 仍按 D004/D005
专用协议。其 cause/authority 不进入本表 classifier；旧 process/structured authority 不能用于
R/I/A/Q 绕过本表，新 authority 也不能用于这些排除 stage。PF observer 自身无法建立合法对象属于
InfrastructureError，不伪造 V 的结构化 verifier failure。

新增 cause 只有 RESOLUTION_FAILED 和 INSTALLATION_FAILED；从目标 cause enum 删除
BUILD_FAILURE，不保留 alias、兼容 reader 或不可达展示分支。现行 adapter 的 BUILD_FAILURE
输出必须同步移除：验证操作内的 build 失败按父阶段兜底，辅助操作按 §4.2/§4.3 分类。上表穷尽
指定的 authority/stage 中不得出现其他 cause。

删除 cause 不删除 UNSAT admission 对 build 诊断的排除条件。该条件继续让相应输出成为
Unattributed，防止误判冲突；它不再返回 BUILD_FAILURE。历史 Experiment、归档文档及现有
qualification manifest 保留原始结果，不为目标契约改写历史。

## 5. Interface 与职责迁移

不增加新的 workflow 或按包归因 module；职责集中在既有 seam：

| Module / seam | 目标职责 |
| --- | --- |
| ProcessRunner / D007 | 保持 ProcessObservation；只观察进程和输出，不判断兼容性 |
| 共享 execution facts 与分类规则 / D005 | 机械提取终态，验证 stage/attribution，统一 dispatch；供生产与 reader 共用 |
| UvOperations / UvAdapter / D012 | 准入 request/profile，验证成功产物，产生 OperationFailure；不向上返回名不副实的 UNSAT/Indeterminate |
| EnvironmentFactory / D002 | 保持唯一 prepare 入口；绑定 Attempt、阶段与已取得 plan，将失败事实向上传递，不重分类 |
| ConfiguredVerifier / D002 | 保持 VerifierRun 和 failed-set/full 调度；用共享规则形成 terminal outcome，不增加 pytest cause 推断 |
| FailurePolicy / D005 | 将 operation facts 或已有 Evaluation 投影为唯一 FailureRecord；不读取日志 |
| Search / D003 | 只消费 Probe disposition 和必要上下文；不依赖 cause、profile 或日志 |
| ReportStore / D014 | 用同一事实验证规则复算 identity、disposition 和 refs；不重新运行工具或解析日志 |

具体目标形状：

```text
ExecutionTerminal = NormalExit | StartFailed | TimedOut | Signaled | Unavailable
ExecutionFailure = {
  kind: "execution", terminal: ExecutionTerminal,
  attribution: Unattributed | UvUnsatAttribution
}
StructuredOperationFailure = {
  kind: "operation-structured", fact: StructuredOperationFact,
  terminal: ExecutionTerminal | null
}
OperationFailure = ExecutionFailure | StructuredOperationFailure
ResolutionOutcome = ResolutionPlan | ResolutionFailure(failure: OperationFailure)
InstallOutcome = InstalledResolution | InstallFailure(failure: OperationFailure)
PrepareFailure = {
  attempt: Attempt, stage: R | I | A | Q, failure: OperationFailure,
  project_plan_digest: Digest | null, environment_plan_digest: Digest | null
}
EnvironmentFactory.prepare(...) -> PreparedEnvironment | PrepareFailure
```

`ExecutionTerminal` 替换语义已通用的 `VerifierTerminal` 名称，保持五种 wire terminal kind；不留 alias。
`ResolutionUnsat` 与 `ResolutionIndeterminate` 合并为中性的 ResolutionFailure，UNSAT 作为
UvUnsatAttribution 保留，避免将普通失败称为 Indeterminate 后又被 FailurePolicy 改成拒绝。
两个 failure 分支均不接收 cause/disposition；由 §4 的唯一表派生。它们可以保留运行期、excluded 的
ProcessObservation/diagnostics sidecar，但 sidecar 不进入上述 fact wire。所有字段 required；
nullable 字段必须保留 JSON null，不能用缺省或虚构 NormalExit(0) 代替。

ResolutionFailure 保持当前 stage、request_digest 和运行期 ResolutionContext envelope；该 envelope
不因此进入报告 evidence。InstallFailure
保持 stage 和被安装的 plan_digest envelope。UvOperations 的 resolve/install interface 另接收
由 EnvironmentFactory 生成的 `request_binding: OperationRequestBinding`，Adapter 只回传用于
UNSAT 凭据，不自行创造 Attempt。Factory 校验 envelope 等于当前调用，再原样传递 OperationFailure
并附上当前 Attempt、stage 和已取得 plan digests。binding/envelope mismatch 由 Factory 在预期
请求下记录 request-invariant；未能构造合法 fact 对象的程序错误直接 InfrastructureError。

辅助 create/inspect 的失败也迁移为带 stage 的 OperationFailure，成功类型不变；不再通过泛化的
ToolFailure 任意填写 cause。Q 的图/向量检查由 EnvironmentFactory 创建 StructuredOperationFailure；
搜索 evaluator 发现 prepare 返回向量不符时使用相同 proposal-vector-mismatch 事实。查询 uv context
发生在 Attempt 以前，无法通过版本/profile 准入时沿用 ConfigurationError 路径，不进入
PrepareFailure。首轮不新增执行中的版本复查或工具漂移检测；已准入请求的实际绑定/投影不一致
仍由 request-invariant 处理，不用它伪装准入失败。

FailurePolicy 的 prepare 入口接收整个 PrepareFailure：ExecutionFailure 原样成为 execution
authority；StructuredOperationFailure 原样成为 operation-structured authority，scope 均为
AttemptFailureScope。§4 计算 cause/disposition，§6 计算 identity；不再把结构化失败拆成
`cause + process? + summary_code?` 后重新猜测。例：uv exit 0 后发现 plan 无效，产生
`StructuredOperationFailure(resolution-plan-invalid, NormalExit(0))`，经 PrepareFailure 原样到
`INDETERMINATE / TOOL_FAILURE / operation-structured`；没有成功 plan/Proposal，也没有假终态。

统一分类规则是一个纯规则实现：ConfiguredVerifier 消费它形成 verifier outcome，FailurePolicy
记录已形成的 verifier outcome 并消费 prepare facts；schema validator 复用同一规则校验。调用方
不需要理解 qualification matcher。Runtime witness 与 ty 的协议 decoder 保持独立。

本设计不改变两次 resolution/一次 install、空 harness 快路、harness relaxation、source ownership
和 prepare 成功后才建立 Proposal 的规则。

## 6. FailureRecord、身份与持久化

### 6.1 Authority

新增 `ExecutionFailureAuthority(kind="execution", terminal, attribution)` 和
`StructuredOperationFailureAuthority(kind="operation-structured", fact, terminal)`，分别与
OperationFailure 两个分支具有完全相同的字段与准入规则，覆盖 §4 的 R/I/A/Q。configured verifier
保留 `configured-verifier` authority（只含通用 terminal），对应无细分归因的执行规则。Cell scope
及 D004 等本次排除的操作继续使用原 process/structured authority。每条 FailureRecord 恰有一种
authority，reader 按 stage 分派到唯一规则族，不允许选择较宽松的 authority 绕过准入。

OperationRequestBinding 是如下封闭对象，全部字段 required，不另存 operation 别名：

```text
{
  attempt_id: canonical Attempt ID,
  stage: "resolve-project" | "resolve-environment" | "install-project" | "install-environment",
  project_plan_digest: Digest | null,
  environment_plan_digest: Digest | null
}
```

Digest 复用现行 plan semantic digest 的规范格式。binding 在 uv 调用前由 Factory 从预期请求建立，
所有 nullable 字段保留 JSON null。它是 UNSAT attribution 的唯一重复请求字段，必须等于顶层
scope/stage/plan evidence 的投影；普通未归因和结构化 authority 不复制 binding。Attempt ID 的
preimage 包含 resolution_context_digest，但不包含可供 reader 展开的 ResolutionContext 对象。
reader 检查该摘要非空，并用保存的摘要值参与 Attempt identity 复算；它检查 UNSAT 的固定
tool/version/protocol/profile 字面量与 request binding，不声称校验未保存的 context 内容。

请求绑定由 scope 的 Attempt identity 加 operation 与实际已取得的 plan digests 表达：

- resolve-project：当前 Attempt 的 project request，失败时不填未生成的 project plan；
- resolve-environment：当前 Attempt、project plan 与其 active harness contract；environment plan 未生成；
- install-project：当前 Attempt 与要安装的 project plan，environment digest 为空；
- install-environment：当前 Attempt 与 project/environment 两个 plan，选择 environment plan 安装。

这些是**成功通过全部准入检查后提交的** plan digests：R 内发现坏 plan、artifact policy/source
mismatch 时不提交该次输出 digest，只保留操作输入已有的 plan digests。project digest 的提交点
移到 project 所有检查之后，environment digest 同理，不能沿用当前部分检查前就赋值的时序。
A 的 create-environment/inspect-interpreter 两个 digest 均 null；inspect 及 Q 已完成安装操作，
必须有 project digest，environment digest 非 null 当且仅当 active external harness 非空。
inspect-project-plan 仅允许空 harness，inspect-environment-plan 仅允许非空 harness。

归因中的 request binding 必须逐字段等于上述 portable 绑定，不另创不可复算的 opaque request ID。
resolve-environment / install-environment 必须有 active external harness；project-only branch
不得冒充其失败。只有成功 prepare 才能引用 Proposal/installed graph；prepare Rejection 的报告
路径必须允许仅有 Attempt 和已取得 plan digests。

规则选择可以由 terminal/attribution 确定，因此不额外持久化一个可漂移的 `decision_basis` 字段。
未归因 normal nonzero 就是兜底；qualified code 就是细分归因；structured invariant 和异常 terminal
各有明确形状。展示与 reader 从这些字段导出依据，不从 cause 猜测。

### 6.2 Identity 与 wire

- FailurePolicy identity 改为 `failure-execution-v3`；FailureRecord 使用
  `sha256("pf:failure:v3\0" + canonical_identity_json(payload)) -> failure-<16 hex>`。
  payload 包含 scope、stage、cause、disposition、完整 authority 和已有 plan digests。
- 新 execution / operation-structured / configured-verifier authority 只吸收 terminal 与实际采用的
  attribution/fact，不吸收耗时、正文、diagnostic hash、日志 locator、run ID 或可选 metadata。
  只有 UNSAT typed facts 的两个 completeness=true 是准入事实并进入 identity；未采用的日志
  completeness 不进入。这一区别使截断日志不能构造 UNSAT，却不改变同一兜底拒绝身份。
  现有 process authority 的 portable payload 保持原规则，不在本次迁移中顺便改变 witness/其他
  排除操作的身份字段。
- 同一 Attempt/操作的 exit 整数、terminal kind 或采用的归因漂移产生不同 failure ID。仅改变日志
  文本、耗时或丢失 tail，而终态和所采用凭据不变，不能改变新 execution authority 的身份。
- evaluation-policy preimage 新增以下固定字段（对象 key 按 canonical JSON 排序，codes 数组保持所示顺序）：

  ```json
  {
    "execution_outcome_policy": {
      "rules": "execution-outcome-v1",
      "structured_facts": "operation-structured-facts-v1",
      "attribution_profiles": [{
        "tool": "uv", "tool_version": "0.12.5",
        "protocol": "uv-pip-compile-pylock-v1",
        "profile": "uv-diagnostics-0.12.5-v1",
        "codes": ["direct-version-contradiction", "transitive-version-contradiction"]
      }]
    }
  }
  ```

  structured_facts 版本绑定 §4.3 的完整 code/stage/terminal/cause 表，不是用户配置。
  ConfiguredVerifier 的终态规则仍为 `configured-verifier-terminal-v1`；改名不冒充行为变更。
  首轮 ResolutionContext 继续使用现有 uv profile，不因封装凭据换名。未来修改 classifier/profile
  须同步改变其版本和 policy preimage；归因集合不根据一次运行匹配了哪些错误动态决定。
- 顶层 Schema version 保持 `1`，增加必需的 `identity.failure_policy = failure-execution-v3` 并进入
  generation preimage；更新 authority union、cause enum、引用校验与 identity reconstruction。
  operation-structured.terminal 与 request_binding 的 nullable plan 字段均为 required-nullable，
  serializer/schema/examples 必须保留 null；FailureRecord 顶层原有 optional plan 字段仍省略空值。
  reader 只接受目标 wire，不双读旧形状；JSON Schema 与 complete/incomplete examples 同步生成。
- Attempt/Proposal 的布局无需改版，不新增 ResolutionContext evidence/ref；context digest 保持
  opaque。producer 的运行时 context equality 与 reader 的有限离线校验按 §4.1/§6.1 分工。
  新 evaluation policy 自然隔离其 identity、resolution/evaluation
  cache 和 generation。不能只升级 Failure ID 而让旧 PASS、拒绝边界和新语义混用。
- Report、Journal、Diagnosis Index 与 process sidecar association 同步适配新 authority；不能因
  execution authority 不保存 ProcessResult 而失去本机日志关联。merge 拒绝不同 generation；
  update_path 沿现有规则整体替换。apply 在 source-drift waiver 之前校验当前 policy，force 不绕过。

报告需离线复证：authority 形状、profile/code 及 typed facts、scope/stage/terminal 准入、request
binding、cause/disposition、plan 时序、Failure ID 和全图 refs。context digest 仅检查非空并参与
Attempt identity 复算，不复算其原文或借助本机环境补证。把 INSTALLATION_FAILED 改成
HARNESS_CONFLICT、把 timeout 改为拒绝、将另一 Attempt 的凭据接入，即使重新计算外层 hash 也
必须被语义校验拒绝。诊断正文和本地日志不是报告 authority。

## 7. 搜索、命令与用户承诺

新的 prepare Rejection 与现有 verifier Rejection 进入同一 ProbeRejection。它可以在当前 Slice
指导搜索方向、建立直接 predecessor 拒绝边界并参加直接非单调检测；没有 Proposal/静态事实时
不登记 static region，不填充 FailedCaseSet。不能把其归因为某个单独版本，也不能跨 Cell、源码、
策略或 Slice 复用拒绝结论。已有完整向量/context cache 可按 D003 复用同一次观察，不额外重试。

本次不新增 prepare 重复执行或 attribution 漂移探测。“同一逻辑请求”的泛化 nondeterminism
承诺删除；不同 failure ID 只表示记录事实不同，不自动产生 NONDETERMINISTIC。具体 seam 如下：

| seam / owner | 比较 identity 与实际观察行为 | 冲突处理 |
| --- | --- | --- |
| prepare/full-run cache / Cell search evaluator | 单 evaluator 固定 Cell/source/policy/baseline context，以完整排序 managed vector 为 key；prepare failure 命中直接复用 | 没有第二次执行，不检测 terminal/attribution 漂移；命中不是复现证明 |
| EvaluationCache / 既有 evaluation owner | 同 Proposal ID、同 static baseline digest 的实际写入；static 比 status，full 比 status 和已有 verifier authority（若有） | 既有 CacheConflict 由 search evaluator 在 static-cache/full-cache 转为 NONDETERMINISTIC；不扩展到无 Proposal 的 prepare failure |
| CoordinateSearch | 同一 Cell search、同 Slice（活动依赖和其他坐标固定）、同候选版本的直接 disposition 观察 | 已观察的 PASS/REJECTED 等 status 冲突按现行 NONDETERMINISTIC；两个 Reject 不比较 attribution |
| ReportStore.merge | 同 generation 同 Cell 的结果；同 evidence ID 的 payload | 不一致即拒绝 merge/report，不生成新的运行期 NONDETERMINISTIC，也不跨运行对 Attempt 归因作重试判断 |

不同 Attempt 的失败记录可以具有不同 ID；相同 failure ID 对应不同 payload 仍为非法冲突。
本设计既不为上述 seam 制造第二次观察，也不以测试注入不可达调用宣称产品能发现所有漂移。

BaselineRejection 仍终止当前 Cell，check 的 declaration-capture 失败仍不进入 declaration；
Indeterminate 仍终止当前 Cell。各 Role 的命令聚合和数值退出码仍由 D001/D008 决定，不新增退出码。
本设计不寻找替代基线，不实现 unknown-aware 搜索，也不改变坐标搜索和静态调度算法。

最终结果必须有该精确向量的直接完整 PASS：真实解析、安装、必要图与协议检查，以及完整 configured
verifier 成功。同 context 的既有 baseline/direct full PASS 可按现有缓存规则复用，不要求额外重跑；
static-only、failed-set PASS 和普通阶段成功都不能成为 final。搜索尽力降低这个已验证向量，不承诺
全局最优、搜索完备、未观察区间或所有更高版本的任意组合兼容。

`diagnose` 展示实际 operation、terminal、cause 和“qualified attribution / normal nonzero fallback”
依据，结构化分支展示 fact.code。RESOLUTION_FAILED 文案说明本次解析未通过且未证明依赖冲突；
INSTALLATION_FAILED 说明所选 plan 本次安装未通过。首轮后端构建细节只在日志显示；删除
BUILD_FAILURE 的 title、next-step 和其他不可达展示映射。Next step 指向该操作诊断，不自动建议
提高依赖下界。Effect/Impact 仍由
Role 决定。普通卡片保持简洁，必要细节放 diagnose；
README 的结果承诺补充本节的精确向量与误拒绝限制。

## 8. 备选方案与非目标

| 方案 | 决策理由 |
| --- | --- |
| 对每种 build 错误证明确定性或依赖根因才拒绝 | 否决；无法对通用后端闭合，与 configured verifier 的执行语义不一致 |
| 所有阶段不分条件地以非零拒绝 | 否决；会吞掉已确认外因、timeout、PF invariant 和辅助协议含义 |
| 扩充包名/traceback/错误短语规则 | 否决；不能可靠绑定 authority，规则随个案累积 |
| 增加重试或严格模式开关 | 否决；重试不是归因证据，双语义扩大身份与使用成本 |
| 合格归因优先，正常非零兜底 | 采用；保留已有可靠事实，并使无细分协议的有效失败可服务搜索 |

非目标还包括系统依赖自动安装、测试契约自动猜测、uv 升级、候选 catalog 扩展、build artifact
替代选择、额外 harness plan 搜索，以及 C003 的成功输出准入优化。

## 9. 验收标准与实施证据要求

以下验收标准已逐项完成，映射、有序切片、迁移面、文档/生成物、测试及精确命令见 P041。
验收包含新真实资格回放，不以本文检查或历史 E007 结果代替产品证据。

| AC | 公共 seam 与必须证明的行为 |
| --- | --- |
| AC1 | UvOperations → EnvironmentFactory → FailurePolicy：两个 resolution 与两个 installation stage 的未知正常非零均拒绝，cause 和 plan 时序正确，prepare failure 不虚构 Proposal |
| AC2 | 按 §4.2 穷尽验证 R/I/A/V 每种 terminal 的 disposition/cause/authority，含 timeout 清理后 exit 0；Q 不接受 execution，planning/ty/witness 不误用兜底；uv version/profile 准入失败在 Attempt 前产生 ConfigurationError，无 PrepareFailure/FailureRecord |
| AC3 | 固定 uv 0.12.5/profile 两个 UNSAT code 的现有真实 fixtures 回放得到 typed facts 和正确 conflict cause；producer 验证运行期 context equality；现有 source/build/availability 排除形状、未知 shape、不完整输出得到 Unattributed，build 排除条件不因 cause 删除而丢失；未注册 code/其他 profile 均不能作为凭据 |
| AC4 | 按 §4.3 的 15 类 fact 验证允许 stage/terminal、唯一 cause 与 operation-structured authority；NormalExit(0) 后坏 plan/output、无进程 invariant、图不符均穿过 PrepareFailure 到 FailureRecord；只提交已通过全部检查的 plan digests |
| AC5 | ConfiguredVerifier 公共 interface 证明完整 normal 0 PASS、任意 normal nonzero Reject、异常 terminal Indeterminate；pytest telemetry 不改 disposition，failed-set 资格和完整 PASS 限制保持 |
| AC6 | SearchCoordinator 的 prepare rejection 能继续当前搜索并形成正确的直接边界；同一构造若为 PF 直接结构化外因 Indeterminate 则终止；baseline/declaration-capture 的现有终止规则保持 |
| AC7 | exact-vector/context 缓存命中不重复执行 prepare；跨 Slice predecessor 重验、现有可观察 seam 的 NON_MONOTONIC/NONDETERMINISTIC 和最终完整 PASS 资格保持；prepare failure 不创建 region 或 failed-set facts |
| AC8 | FailureRecord/ReportStore write → read 往返涵盖兜底拒绝、两类 UNSAT、结构化外因/坏成功产物/无进程 invariant、异常 terminal、project-only/harness、仅 Attempt 失败以及成功 final；required-nullable 字段往返保留 null |
| AC9 | 离线 reader 对保存事实使用 §4.2/§4.3 相同规则，拒绝错误固定 tool/version/protocol/profile/code/completeness、terminal/stage/authority/plan 时序、跨 Attempt 凭据、伪造 cause/disposition、空 context digest 和坏 identity/refs；重新哈希不豁免这些可观察语义检查；无 context preimage、本机 context 或日志仍能读取合法报告，不宣称复证 opaque digest 的原文 |
| AC10 | 新 authority 的 Failure ID 只随采用事实变化，不随日志/耗时变化；冻结 policy 对象进入 generation/cache/apply 隔离；merge 只拒绝冲突，不合成运行期 NONDETERMINISTIC；update 和 apply force 保持现有策略检查 |
| AC11 | smoke/check/search 的 Journal、Diagnosis Index、result card、diagnose（有/无本机日志）及退出码一致；新增 cause 不显示为 UNSAT 或单版本根因 |
| AC12 | 用受控旧式 sdist 构建失败完成真实 resolve/install 集成验证，并在构造的多候选搜索中继续到可通过向量；E007 Jinja2 精确请求可复测作为 dogfood 补充，记录实际 stage/依据，不要求 MkDocs 全搜索成功 |
| AC13 | owner 文档、README、CONTEXT 中涉及的新术语、Schema/examples 和测试 fixtures 同步；从生产输出、领域/wire enum、展示映射和现行测试期待中删除不可达 BUILD_FAILURE，保留 UNSAT build 排除逻辑和历史记录；完整验收审计后归并并归档本 Design/Plan |

qualification 应新增本次日期与固定 profile 的策略验证证据，保留现有 manifest 的历史结果；不把旧 manifest 的
INDETERMINATE 改字就称为新策略验收。真实 uv/PF CLI 和测试按仓库 AGENTS 要求在仓库根目录、
检查风险后于沙箱外运行。测试通过公开 interface 断言稳定语义，不耦合 private matcher 或第三方
自然语言全文；qualification fixture 可以固定实际工具输出作为协议资格输入。

## 10. Owner 归并清单

| Owner | 完成时接收的稳定规则 |
| --- | --- |
| D001 / README | 执行契约与精确向量承诺、误拒绝取舍、既有命令退出码语义 |
| D002 | 共享 execution facts、ResolutionFailure、prepare/verifier/report seam 与日志 sidecar ownership |
| D003 / D004 | 新 prepare rejection 的消费资格；辅助协议、直接 PASS、静态与 witness 规则的适用边界 |
| D005 | 分类顺序、目标 cause 集合（删除 BUILD_FAILURE）、attribution、FailureRecord v3、identity 和 diagnose 语义 |
| D006 / D008 | 分类依据展示、不可达 cause 展示清理、Role impact、Journal/Diagnosis Index 与输出关联 |
| D007 / D012 / D013 | terminal 与诊断用途、uv 准入前 ConfigurationError、producer context 校验、UNSAT build 排除逻辑与失败 interface、pytest 诊断独立性 |
| D014 | 新 authority/cause/identity wire、opaque context digest 的离线校验范围、共享语义验证、generation/merge/apply 与生成物 |

本次迁移已验收，以上 owner 已接收稳定规则；本归档保留迁移理由与范围。

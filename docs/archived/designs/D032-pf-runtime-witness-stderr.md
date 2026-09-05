# D032 — Adapter 证据准入与诊断边界

- **状态：** 已完成、归档；稳定规则已归并 D003/D004/D013/D014，实施证据见 [P037](../plans/P037-pf-adapter-evidence-admission.md)
- **日期：** 2026-09-05
- **性质：** 临时性 witness、pytest telemetry、registry artifact 与 ty 配置准入契约迁移 Design
- **核对基线：** `85e195c`；工作区已有 D031/E005 等未提交工作，本设计独立于这些变更
- **来源：** 用户提供的 requests search 诊断、后续 adapter 过严判据审查与最小差分复现；用户已同意建议
- **稳定 owner：** [D003](../../designs/D003-pf-search-algorithm.md)、[D004](../../designs/D004-pf-ty-enhancement.md)、
  [D013](../../designs/D013-pf-pytest-observer.md)、[D014](../../designs/D014-pf-report-schema.md)
- **关联：** [D002](../../designs/D002-pf-implementation.md)、[D005](../../designs/D005-pf-failure-and-diagnose.md)、
  [D007](../../designs/D007-pf-process-output.md)

本临时 Design 的四项迁移已实施并验收，稳定规则归并到上述现行 owner；本文保留决策来源，
不再承担现行规范性。P037 记录三版本全套、逐 AC 证据、生成物与独立工作区限制。
§9 的 uv 日志完整性候选仍待验证，已具名移交 README 开放项；不属于本次已完成目标。

四项调整遵循同一原则：分别判断权威证据、诊断遥测和输入适用性；只对承担当前结论的证据
施加相应完整性要求，不让无关文本、文件或已被覆盖的默认设置阻断结果。

## 1. 问题与证据边界

当前 `RuntimeWitnessAdapter.run` 在正常退出与输出完整性检查后，只要 stderr 非空就返回
`ToolFailure(cause="TOOL_FAILURE", stage="witness")`。现行 D004 §7 和
`tests/test_runtime_witness.py` 的 `unexpected-stderr` 用例明确要求这个行为。
`RuntimeEvaluator` 随后形成 Indeterminate，D003 停止当前 Cell；实现符合现行契约。

用户提供的 run `20260905T102750.410038Z-466037-72d4b2fa` 诊断称：Python 3.12–3.14 的六个
requests Cells 在 urllib3 1.16 上得到 exit 0、`{"status":"NOT_APPLICABLE"}`，但导入时产生的
SyntaxWarning 使 witness 失败。代表 Failure ID 为 `failure-20e6e6e5fdc40155`。
这些是用户转述的运行证据，本轮未读取原始报告或日志，不据此重新认证计数和各 Cell floor。

本轮以真实 adapter 和提供 ProcessResult 的最小 runner 做差分复现，固定 plan、正常 exit 0、
完整 stdout/stderr 及 `{"status":"NOT_APPLICABLE"}\n`，只改变 stderr：

| stderr | 当前真实 adapter 结果 | 目标结果 |
| --- | --- | --- |
| 空 | RuntimeWitnessResult / NOT_APPLICABLE | NOT_APPLICABLE |
| 一行 SyntaxWarning | ToolFailure / TOOL_FAILURE | NOT_APPLICABLE |

从仓库根目录用 `.venv/bin/python -` 执行内联复现，两项均返回 NOT_APPLICABLE 的目标断言失败
（exit 1）。这证明 stderr 判据足以造成该症状；不代表已复现旧 urllib3 导入或完整 requests search。

Witness 仅回答精确 runtime 名称是否存在；PRESENT 与 NOT_APPLICABLE 不授权 PASS。
导入代码可向 stderr 写入警告或日志，stderr 是否为空不能证明存在性探针协议是否损坏。

## 2. Runtime witness 目标协议

### 2.1 资格与协议通道

继续由 adapter 执行当前 prepared environment 的
`<interpreter> -I -c <adapter-owned-harness> <canonical-plan-json>`，保持 shell-free。

只有以下条件全部满足，才能返回 RuntimeWitnessResult：

1. Process terminal 可用，正常 exit 0，无 signal、启动失败或 timeout。
2. stdout 与 stderr 都具有完整捕获事实；输出读取遵循 D007，不把有界 Output Cache 当作全文。
3. stdout 精确等于一行 canonical JSON 加一个换行；唯一字段为 `status`，其值为字符串
   `PRESENT`、`CONFIRMED_MISSING` 或 `NOT_APPLICABLE`。

非法 JSON、错误字段/类型、非 canonical 格式、额外 stdout、多行结果和缺少换行均为 ToolFailure。
包括 `status` 为数组或对象时，也必须返回 typed ToolFailure，不能让解析校验异常逃逸。
stdout 中的导入 side-effect 输出仍会破坏协议；本次不引入 stdout 重定向或新 IPC 通道。

### 2.2 stderr 只承载诊断

满足 §2.1 后，允许任意 stderr 文本，内容和非空性都不参与 witness status 或 disposition 分类。
这包括 SyntaxWarning、DeprecationWarning、普通日志及形似 traceback 的文本；不维护 warning
白名单，不按包名、Python 版本、正则或文本严重程度分流。

“任意文本允许”只描述 stderr 内容，不能绕过进程终态、输出完整性或 stdout 协议检查。
例如非零退出即使携带合法 JSON，或 stderr 捕获不完整但 stdout 合法，仍为 ToolFailure。

Harness 不为此禁用 warnings、重设过滤器、吞掉 stderr 或修改被测导入语义。
stderr 沿用 D007 的脱敏 Process Log 与读取路径保存，不能为满足协议把它清空；
公共报告继续遵循现有 portable process 投影，不增加 stderr 正文或新的警告字段。

### 2.3 结果路由

对三个合法 status 使用同一 stderr 规则：

| 合法 status（stderr 可非空） | RuntimeEvaluator 行为 |
| --- | --- |
| PRESENT | 继续其余 witness plans，随后执行 configured verifier |
| NOT_APPLICABLE | 继续其余 witness plans，随后执行 configured verifier |
| CONFIRMED_MISSING | 保留精确目标缺失证据，形成 RuntimeInterfaceMissingEvaluation |

精确目标 ModuleNotFoundError / AttributeError 的归因规则、fromlist 语义和 witness plan
资格继续由 D004 拥有。不能从 stderr 中提取 traceback 或缺失名称作为 CONFIRMED_MISSING 的证据；
无关缺失和 import side-effect exception 仍由 harness 返回 NOT_APPLICABLE。

真正的 witness ToolFailure 继续形成 Indeterminate，不改成 verifier fallback。
完整 PASS 仍要求当前 Proposal 的完整 configured verifier 正常 exit 0；
NOT_APPLICABLE 加 warning 本身既不证明兼容，也不证明不兼容。

## 3. Pytest summary 降为可选遥测

### 3.1 当前证据

`ConfiguredVerifier._run_pytest_profile` 在正常退出后调用 `read_pytest_observer`，summary 缺失、
损坏或不合法就抛出 `InfrastructureError("pytest observer protocol failed")`，不产生 Evaluation。
这符合现行 D013 的 mandatory summary 规则，但使遥测成为取得已知 terminal 结果的硬门槛。

前轮从仓库根目录执行 `.venv/bin/python /tmp/pf-strictness-review.py`（exit 0），以真实
ConfiguredVerifier 和模拟 ProcessResult 验证：同为 exit 0 / 1，generic command 分别产生
VerifierPass / VerifierRejected；direct pytest 在 summary 缺失或损坏时都抛 InfrastructureError。
这项证据证明 adapter 分支，不代表真实 pytest 在正常环境中已发生 artifact 丢失。
临时脚本已删除；实施时须将场景固化为公共 seam 测试，不能将此历史复现充作实施验收。

### 3.2 目标行为

主 summary 与 progress/detail 一样，只为诊断提供可选事实。正常 terminal 已取得后，summary
缺失、不可读、损坏、非 canonical、nonce 不符、冲突或资源超限，只丢弃整份 summary 投影，
不抛命令级 InfrastructureError、不修改 terminal、不重跑未注入的原命令。

Reader 对可采用的 artifact 仍执行现有严格协议和资源检查；不能把损坏文件部分解析为可信 facts。
summary 不可用时，diagnostics 保留 process，pytest version/minor/mode 不填，facts 为空；
不伪造“未发生失败”的观测，也不计算 terminal 与 summary 的 metadata-conflict。
合法的独立 detail/progress/cases 仍按各自 nonce 与资格检查消费。

原命令阶段 `NormalExit(0)` 为 VerifierPass，任意正常非零退出为 VerifierRejected；
timeout、signal、start failure、terminal unavailable 的 Indeterminate 映射保持不变。

failed-set 的 collection 证明独立于 summary：

- 只有合法 cases artifact 证明本次 requested-set collection，才可采用 failed-set 非零 terminal。
- 缺失、损坏或不能证明 collection 时，沿现行规则回退原命令；summary 无论是否可用都不能补证。
- 有效 failed-set exit 0 仍须运行完整原命令；failed-set timeout 等不定终态仍不回退。
- failed additions 只来自独立通过资格检查的 failed projection，不从 summary 推导。

本项改变的是 summary 读取失败的处置。进程启动前的 observer/pruning 资源准备失败、非法进程
终态，以及现行必需临时资源的 cleanup 失败处理不在本次放宽范围内；不引入静默取消插件注入。

## 4. Registry artifact 按适用性准入

### 4.1 当前证据

`UvAdapter._available_candidates` 在 Requires-Python 与 wheel 平台过滤之前要求每个文件提供
SHA-256。前轮同一复现脚本通过公共 `UvAdapter.query`、内存 HTTP 响应验证：

| Linux / Python 3.10 索引输入 | 当前结果 |
| --- | --- |
| 一个完整可用的 demo 1.0 wheel | 返回候选 1.0 |
| 再加 Windows / Python 3.12 的 demo 2.0 wheel，提供 SHA-256 | 返回候选 1.0 |
| 同一无关 wheel 只提供 SHA-512，或 hashes 为空 | 整个 query 抛 InfrastructureError |

[Simple API 规范](https://packaging.python.org/en/latest/specifications/simple-repository-api/#json-serialization)
要求 hashes 字典存在，但允许没有哈希，也允许其他算法；缺 SHA-256 本身不等于 Simple JSON 结构非法。
上述是合成响应的实际 adapter 复现，没有访问 registry 或认证任何真实包的候选完整性。

### 4.2 目标顺序与边界

Registry reader 区分以下三层：

1. **响应结构与 release 观测：** 校验响应与文件记录的必要结构；解析支持的 wheel/sdist 文件名。
   对可解析版本先加入完整 release_versions。无法解析的既有不支持文件类型继续忽略。
   malformed JSON、非法必要字段类型或不能判断适用性的 Requires-Python 仍使 query 失败。
2. **当前 Cell 适用性：** 对支持的文件按 Requires-Python、wheel Python/ABI/platform tags 判断。
   已明确不适用当前 Cell 的 artifact 不进入候选，不执行安装 locator 与 SHA-256 资格检查。
   URL 字段仍须满足第一层必要结构要求（非空字符串），但不要求其 scheme/host 可用于安装；
   其可解析 release version 仍保留在第一层观测中。
3. **可用 artifact 证据：** 对通过上述适用性检查的 artifact，继续严格校验现行 locator 与
   SHA-256 规则。缺少可靠证据仍失败，不能静默丢弃一个可能适用的版本并宣布更高的 floor。
   错误说明应区分响应结构非法与适用 artifact 缺少 PF 所需证据。

不新增 SHA-512 安装路径、下载后补哈希或无哈希安装。D003 的 yanked、prerelease、artifact
策略、声明保留限制、baseline 和 search-space 过滤继续由现有层负责；本次只调整 query 内可
证明的不适用文件与 locator/哈希验证顺序，不把 CandidateBuilder 的过滤提前复制到 adapter。

尤其不能把“跳过不适用 artifact”实现成“删去 release”。系列位置切片必须继续基于全部可解析
release keys 求值；无可用 artifact 的系列仍占位，不把 `majors[...]` / `minors[...]` 偏移补位。

## 5. Ty 项目默认配置由固定 argv 覆盖

前轮以仓库安装的实际 ty 验证：项目 `[tool.ty.terminal]` 的 `output-format` 为 `concise` 或
`gitlab`，使用 PF 当前固定 argv 均正常 exit 0 并输出合法 GitLab JSON；真实 TyAdapter 则因键
存在，在启动前一律抛 ConfigurationError。这项证据覆盖实际工具优先级，不是模拟 ty 输出。

目标行为是允许项目原有的合法 terminal 展示默认设置，由 PF 的固定 argv 决定本次
`output-format=gitlab`、`color=never` 与禁用 progress 的有效值。配置已经等于 PF 要求时同样允许。
不重写源码 pyproject，不要求用户删除供日常开发使用的设置，不屏蔽 ty 自身的配置语法校验。

用户 `ty-args` 和显式 config override 仍不能覆盖 adapter-owned 参数，`--config-file` 现有
限制保持不变。解释器、Python minor、target 等验证 scope 继续由 PF 指定，不能从项目默认设置
继承另一套 scope。固定参数是否生效由实际工具测试证明，不能通过放宽 GitLab 输出解析兜底。

## 6. 所有权、identity 与迁移

- **D004 / RuntimeWitnessAdapter：** 吸收正常结果资格、stdout 协议和 stderr 诊断语义。
  RuntimeEvaluator 保持现有路由，测试需把真实 adapter 结果接入该路由。
- **D004 / TyAdapter：** 吸收项目 terminal 默认配置覆盖规则；保留用户 argv 保护和 scope 所有权。
- **D013 / ConfiguredVerifier：** 删除 mandatory summary 对已知 terminal 的准入门槛，吸收可选
  summary 与独立 collection 证明规则；检查 D002 的相关 interface 说明是否需要同步。
- **D003 / UvAdapter、CandidateBuilder：** 吸收 query 内响应结构、Cell 适用性、artifact 证据的
  分层顺序，并继续独占完整 release 观测与系列位置含义。
- **D014 / Report reader：** 更新 §1.2.3「离线派生与候选 identity」的精确 candidate policy
  preimage，纳入下述 `artifact_admission` 事实，并同步 reader 完整复算规则。D003 负责准入与
  选择含义，D014 继续独占 wire identity 的精确 preimage；本项属于 AC11/AC12 必需的 owner 归并。
- **D005：** 继续引用 D004 的 witness 资格；不新增 cause、disposition 或 FailureRecord 类型。
- **D007：** 保持完整性、脱敏、Process Log 和 Output Cache 所有权；无日志布局迁移。
- **Schema：** RuntimeWitnessResult 已要求完整正常 exit 0，未要求 stderr 为空；
  VerifierDiagnostics 已允许省略 pytest summary 字段。上述调整不需要改变公共 wire 形状，
  不新增兼容 reader；报告不得收录临时 observer artifact 或未通过准入的 artifact。
- **Evaluation policy：** 在 `TY_DIAGNOSTIC_POLICY` 中增加明确参与 identity 的
  `witness_stderr = diagnostic-only`，使新运行与旧规则的 policy identity 不同。
  按项目 pre-release 原地替换约定保留 `witness-harness-v1`、`pf:policy:v1` 和 Schema 1；
  D004 §11 的协议变更版本规则需同步说明本次以显式策略事实隔离身份，不引入 v2 或双协议。
- **其他 identity：** `TY_DIAGNOSTIC_POLICY` 同时增加
  `project_terminal = adapter-cli-overrides`；candidate policy digest 的 preimage 增加
  `artifact_admission = cell-eligibility-before-sha256`，实现、reader 复算与相关生成物一并对齐。
  pytest summary 仍不进入 evaluation policy identity，`configured-verifier-terminal-v1` 不变；
  不修改 pytest/pruning 选择语义或增加 summary authority 版本。所有适用内部前缀继续为 v1。
- **生成物：** 实施时运行现有 schema/example 生成流程，核对 policy identity 派生示例，
  只保存实际产生的差异。历史 Experiment、报告与归档实施证据保持其原始结论。

原 requests incomplete 报告不能因为代码更新而升级为 complete，也不能把未完成 Cell 的过程向量
认作 floor。需要新运行取得新的 Proposal 验证与完整 Cell/投影证据。

## 7. 验收标准

1. **AC1 — 正常结果：** 真实 RuntimeWitnessAdapter 的公共 run seam 对三个 status 分别覆盖
   空 stderr、warning 与普通诊断文本；完整正常退出且 canonical stdout 时返回对应结果，保留 process。
2. **AC2 — 协议失败：** timeout、signal、启动失败、terminal unavailable、非零退出、任一流
   不完整，以及非法/多行/非 canonical stdout、额外字段、未知 status 和错误 status 类型，均按现行
   cause 规则返回 typed ToolFailure；非空 stderr 不掩盖这些失败。
3. **AC3 — 路由与证据：** 通过真实 adapter 连接 RuntimeEvaluator 的公开执行 seam，证明带
   warning 的 PRESENT/NOT_APPLICABLE 会执行完整 verifier，并分别得到 verifier 授权的 PASS 或
   REJECTED；CONFIRMED_MISSING 仍短路为 runtime-interface rejection；真正协议失败仍 Indeterminate。
   保留 static evidence 与 witness attempts，不从 stderr 产生正向或负向兼容性结论。
4. **AC4 — 导入与日志：** 在隔离的临时测试环境安装本地最小 fixture，通过真实子进程导入产生
   确定的 warning，覆盖 import side-effect exception 后 NOT_APPLICABLE 的路径；
   断言 stderr 仍可从 D007 输出读取 seam 取得，未被过滤或抹除。
   此测试无需 registry 或真实旧 urllib3，避免以解释器默认 warning 策略作为前提。
5. **AC5 — 可选 summary：** 通过 ConfiguredVerifier.run 覆盖正常 exit 0/非零，以及 summary
   缺失、损坏、不可读、非 canonical、nonce 不符、冲突和资源超限；结果仍由 terminal 决定，
   process 保留，无伪造 facts；合法 summary 继续提供诊断。用真实 pytest 子进程加受控 artifact
   故障注入验证结果保留，不能只测试 reader 或模拟终态。
6. **AC6 — Pruning 证明：** summary 不可用且 collection 证明有效时，failed-set 非零可拒绝；
   collection 无法证明时，即使 summary 合法也须回退原命令。failed-set exit 0 不授权 PASS，
   不定终态不回退；独立通过校验的 failed additions 与 detail 保持原有资格。
7. **AC7 — Artifact 准入：** 公共 UvAdapter.query 覆盖完整可用候选与不适用的 Python/platform
   wheel 混合响应；不适用文件 hashes 为空或仅有 SHA-512 时查询仍成功。另以有效 SHA-256、
   合法非空 URL 字符串但安装 locator 非法（如 `file:///demo.whl`）覆盖不适用 artifact，查询
   仍成功；至少一例仅 wheel platform 不匹配且 Requires-Python 满足当前 Cell，确保在 platform
   过滤前验证 locator 的实现不能通过验收。适用 artifact 的同类非法 locator 或缺少 SHA-256
   仍失败，错误区分结构与证据；响应必要结构损坏（例如 URL 非字符串）仍失败。
8. **AC8 — Release 观测：** 经真实 query、CandidateBuilder 与 search-space 求值路径证明，
   无适用 artifact 或无 SHA-256 的不适用文件所属版本仍进入完整系列观测，位置偏移不补位；
   不改变已合格候选的选择、代表和 floor 资格。
9. **AC9 — Ty 覆盖：** 真实 ty 子进程覆盖合法项目 terminal 的同值/不同值 output-format
   默认设置（固定 ty 不支持项目 terminal.color，非法配置仍由 ty 报错），固定 argv 仍产生合法 GitLab JSON；公共 TyAdapter.check 接受并保留诊断。用户 argv、
   config override 和 config-file 对 owned 参数的现行限制仍由公共 seam 测试证明。
10. **AC10 — 严格边界：** 上述各路径继续拒绝本路径必需的非法机器协议、不可用终态、非法
    scope 或不可靠选中 artifact；不将 uv 普通非零退出、source/build 失败或 warning 文本升级为
    Rejection。不为 §9 的待验证项提前放宽输出完整性规则。
11. **AC11 — Identity 与文档：** policy identity 包含 §6 的新语义事实；采用正向语义断言，
    D014 §1.2.3 的精确 preimage、reader 复算、其他 owner 与生成物一致。不保留“stderr 非空必失败”
    “summary 缺失必终止”及“项目
    terminal 键存在必冲突”的旧规则测试；相关可选通道坏协议测试断言丢弃，不断言接受坏数据。
12. **AC12 — 完成交付：** Plan 映射 AC1–AC12，逐项记录实施和验证证据。稳定 owner 吸收规则后，
    将本 Design 与 Plan 同步归档，更新索引；归并范围明确包含 D003/D004/D013/D014。§9 已移交
    [独立开放项](../../README.md#uv-resolution-output-completeness)，归档时保留其开放状态并修复来源链接；
    不要求解决或实施该项，D032 可以在 §9 仍开放的情况下完成。
    产品验收不以静态文档检查代替 AC1–AC10 的行为证据。

requests dogfood 复跑是独立的外部验证：优先复核原六个 witness 阻塞点是否已进入 verifier，
再观察新搜索终态。消除本协议阻塞不承诺整个搜索 complete；后续失败必须按新证据独立分类。

## 8. 接受范围与后续工作

用户已接受 witness stderr 诊断化、pytest summary 可选化、不适用 artifact 不受 SHA-256 门槛
阻断，以及 ty 项目 terminal 默认配置由固定 argv 覆盖的建议。本文将其合并为同一份目标方案。
评审补项已纳入本文，实施计划见 [P037](../plans/P037-pf-adapter-evidence-admission.md)；
生产实现、owner 归并与本计划验收已完成，dogfood 复跑仍属独立验证。

P037 分别覆盖四条路径的接口/owner 迁移、公开 seam 和真实子进程测试、identity 与
生成物、稳定规则归并和验收证据。D031 的搜索算法草案独立推进，不以其实施为本方案前提。

## 9. 待验证：uv 成功解析的日志完整性门槛

当前 `UvAdapter._resolve` 在 exit 0 后仍先要求 stdout/stderr 捕获完整，才读取并验证
`pylock.toml`；任一流不完整返回 `resolution-output-incomplete`。前轮只确认了代码分支，
尚未复现“进程正常退出、锁文件完整合法、只有诊断日志不完整”的完整 adapter 路径。

已接受的后续建议是评估成功解析能否只依赖正常 terminal 与完整可信锁文件；不能把待验证的
候选直接写成已证实缺陷或实施验收通过。该项已由
[README 独立开放项](../../README.md#uv-resolution-output-completeness) 接管跟踪；以下确认仅为未来
该项自身实施的前提，不是 D032/P037 的前置条件或验收任务：

1. 通过公开 resolve seam 构造合法锁文件和精确正常 terminal，仅改变 stdout/stderr 完整性，
   重现当前结果差异，并查清 D007 incomplete 的实际来源。
2. 明确成功解析所需证据是否完全来自 lock artifact，日志缺失是否可能同时表示 artifact 或
   terminal 不可靠；证明 native lock 读取、digest、解析与上下文归属仍完整。
3. 若确立目标，由独立后续 Design/Plan 明确 D012/D007 的 owner 迁移和独立 AC，再实施该项。
   非零退出的 UNSAT classifier 仍须完整诊断与已资格化 profile，不能与成功路径一起放宽。

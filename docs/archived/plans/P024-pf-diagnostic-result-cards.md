# P024 — D018 PF 诊断与结果命令卡片实施记录（归档）

- **状态：** 已完成，已归档
- **开始日期：** 2026-08-31
- **完成日期：** 2026-08-31
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D018](../designs/D018-pf-diagnostic-result-cards.md)
- **实施基线：** `23d5cbb`（`dev: update floor`）

本文在生产代码变更前建立 D018 的实现顺序、interface/ownership 迁移、测试矩阵与证据槽位。
每次实质行动后在 §7 记录行动、结论和精确命令结果；完成标准只来自 D018 §10，不缩小目标。

## 1. 目标与边界

本轮完整实现 D018：

- 将 `diagnose` 直接替换为必填位置 Failure ID、规范化完整 identity、report-first/latest-Journal-second
  的单 Failure workflow，以及 typed not-found error；删除 `--failure`、optional ID、批量和空成功；
- 让 explain 把报告 overview、成功 Cell 紧凑行、异常 Cell 卡片、Requirements projection 和唯一
  next action/final summary 分层，并把报告读取类失败转换为 typed command failure；
- 让 diagnose、apply、merge 与 explain 复用同一 rounded outcome card、fact grid、路径 link 和 final
  summary 语言，同时保持成功事实卡的 stdout 与 command failure 的 stderr/退出码边界；
- 让 apply card 只消费 `ApplyCommandResult.package/edit/presentation_facts`，让 merge workflow 返回带全部
  invocation paths 的 `MergeCommandResult` 并按阶段抛结构化 error；
- 以 public CLI/workflow/presenter 测试覆盖宽度、literal text、通道、顺序、完整字段与路径；
- 将稳定规则归并到 D001/D002/D005/D006/D008，确认 D014 authority 不变，再归档 D018/P024。

不改变 report Schema 1 wire、merge compatibility/apply authority、搜索算法、live Cell 的 elapsed/detail/
pinned layout，不增加兼容 grammar、批量 diagnose、历史 run 扫描或 presenter 侧领域推断。

## 2. 基线事实与差距

| 范围 | `23d5cbb` 当前事实 | D018 目标 |
| --- | --- | --- |
| diagnose grammar | `--failure` 可选；省略后列出全部，返回 tuple | 必填位置 ID；短 ID 规范化；单数 result；未知 typed error |
| diagnose lookup | report 与 latest Journal 合并后可筛选/全列 | report 命中即返回，否则仅查 latest Journal；不扫描历史 |
| diagnose output | 多段无边框文本；零条成功 | 一张 Failure card + 一条绿色 final；command failure 独立卡 |
| explain | 成功 Cell 各自成卡；success count 冒充 coverage；手工 requirement padding；Next 重复 | overview 紧凑成功行、六类分布、异常卡、完整 projection、唯一 next/final |
| explain failure | report/store 原始错误走通用 renderer | workflow 产生携带 path/reason/recovery 的 `ExplainReportError` |
| apply | 普通成功只有 final；waiver 用手工事实行；result 不携带 package | 一张结构化 apply card + 一条 final；waiver 同卡片语言且走 stderr |
| merge | workflow 只返 report；presenter 硬编码一份输入 | result/error 携带全部规范 display paths；成功/失败卡完整逐行展示 |
| width/literal | Cell 卡已有 marker/content grid；部分 explain/apply 手工 padding | 四命令复用 grid/card；56/80/120 列与 literal/OSC 语义经 public seam 验证 |

## 3. Interface 与 ownership 迁移

1. `DiagnoseRequest.failure_id` 改为 required canonical ID；CLI converter 是短形式进入领域前的唯一
   规范化位置。`DiagnoseCommandWorkflow.run -> FailureDiagnosis`，report lookup 优先，latest Journal
   只作单次 fallback；`DiagnoseNotFoundError` 拥有稳定 presentation fields。
2. `ExplainCommandWorkflow` 负责把 report unavailable/invalid/identity mismatch 映射为
   `ExplainReportError`，携带 display path、stable reason 与 selector-sensitive recovery command。
   Presenter 只消费 validated report 或 typed error 字段。
3. 新增 `MergeCommandResult(report, input_paths, output_path)`；workflow 规范化 request display paths，
   fail-fast 读取并按 input/compatibility/output 阶段映射 typed errors。Presenter 不读取文件或解析异常。
4. `ApplyCommandResult.package` 是 apply card 唯一 subject；scope 只由既有 selector tuples 格式化。
5. `pf.terminal` 私有共享 result-card/fact-grid/path helper 只拥有布局与 style；领域 status、reason、scope、
   paths 和 authority 均来自 workflow/schema。

## 4. 实施顺序

### 切片 001 — CLI、request、workflow 与 typed error seam

1. 先以 public CLI/workflow 测试锁定 diagnose 完整/短/缺失/非法/多余/旧 option，以及 report 优先、
   latest Journal fallback、not-found 和单数 result；
2. 迁移 `DiagnoseRequest`、protocol、CLI converter、workflow 与 composition；删除全部 list-all seam；
3. 增加 typed diagnose/explain/merge errors 与 `MergeCommandResult`，让 workflow 产出全部 presentation facts；
4. 以 report/workflow/CLI 聚焦回归确认 authority、exit 与 Usage 边界。

### 切片 002 — 共享 result card 与 explain

1. 建立 terminal-private rounded card、fact grid、safe path link 与 bold final primitive；
2. 重构 explain overview 为六类 Cell distribution + success compact rows，异常 Cell 各出现一次；
3. 用 declaration ID 投影 representable/empty/blocked/fixed，blocked 时保留 floors；
4. 实现 report-intrinsic apply evidence、唯一 next action、0/1/N managed summary 与 typed explain error card；
5. 在 56/80/120 宽度和 TTY/non-TTY 下测试内容、顺序、通道、literal text 与行宽。

### 切片 003 — diagnose result card 与 live grammar

1. 将单个 `FailureDiagnosis` 渲染为 What happened/Impact/next、Context、完整 Technical details 和 log/output；
2. 确认 facts card 的 disposition icon 与绿色 command final 分离，缺失 log 使用稳定占位；
3. 把 live/explain 的命令正文统一为 `pf diagnose failure-ID --package NAME`；
4. 按 D005 原文与 D018 Role 表测试 title/next/impact byte equality 和 predecessor 附句。

### 切片 004 — apply 与 merge cards

1. 用 result package、selector tuples、edit facts 渲染 default/scoped/no-op/source-waiver apply card；
2. 限制 waiver paths 最多 8 条，并让 minimize 只复用同一 card/final；
3. 用 `MergeCommandResult` 渲染 complete/incomplete 成功，逐行保留全部重复/相对/绝对 input 和 output；
4. 用 typed errors 渲染 input/compatibility/output failure，input 只标第一失败路径；
5. 复核 stdout/stderr、exit、单 final、OSC/path literal 与无硬编码输入数量。

### 切片 005 — 全量验证、owner 归并与归档

1. 运行聚焦矩阵、ruff、ty、Python 3.10–3.12 全量 pytest、coverage、build/生成物/Markdown link/diff checks；
2. 逐项审计 D018 §10 十二项，证据不足则继续实现而非仅记录绿色回归；
3. 把稳定 grammar/interface/failure/presentation/lookup 规则分别归并 D001/D002/D005/D006/D008；
4. 在 D014 只明确结构化 merge result 不改变其 authority；更新索引；
5. 将 D018/P024 状态改为已完成并同时移入 `docs/archived/designs`、`docs/archived/plans`。

## 5. D018 §10 验收与证据矩阵

| 验收项 | 切片 | 主要 public 测试 | 直接证据目标 |
| --- | --- | --- | --- |
| 1. 唯一 diagnose grammar、规范化、exit/Usage/not-found | 001 | `test_cli.py`, `test_diagnose.py` | 完整/短合法 request；四类 invocation error；typed unknown |
| 2. 单数全链路且无旧 grammar/list-all | 001、003 | `test_cli.py`, `test_diagnose.py`, `test_terminal.py`, `test_explain_terminal.py` | request/workflow/presenter 类型与所有打印面；源码/测试/docs 扫描 |
| 3. report-first/latest Journal-only lookup | 001 | `test_diagnose.py` | 同 ID report 优先、fallback、无历史读取、log 缺失仍成功 |
| 4. explain 五类异常 icon/distribution/stdout | 002 | `test_explain_terminal.py` | 每类 public rendering + no-floor/search-failed 区分 + authoritative hint |
| 5. explain command failure typed card | 001、002 | `test_report_workflows.py`, `test_cli.py`, `test_terminal.py` | read/validation/identity、selector recovery、stderr/exit 3/no Usage |
| 6. Cell/projection/summary/next 完整且唯一 | 002 | `test_explain_terminal.py` | 每 target 一次、fixed/blocked floors、0/1/N、至多一 next/final |
| 7. apply 四情形与 minimize | 004 | `test_cli.py`, `test_terminal.py` | package source、selector semantics、8 paths、channels、one card/final |
| 8. merge 成败路径与 fail-fast | 001、004 | `test_report_workflows.py`, `test_cli.py`, `test_terminal.py` | 全路径顺序/重复、first failed、output、无输入计数硬编码 |
| 9. 四命令共享 TTY/non-TTY 语义 | 002–004 | terminal test modules | rounded/icon-grid/bold final；无 ANSI/OSC；成功红黄 facts 在 stdout |
| 10. 56/80/120、续行、完整 literal/path/ID | 002–004 | terminal test modules | public presenter 行宽与语义 fragment；不测完整 ANSI snapshot |
| 11. D005 文案、Role impact、technical fields | 003 | `test_diagnose.py` | byte-equal title/next；全部 role/disposition；字段/占位/output/log |
| 12. owners、状态、索引、归档 | 005 | docs/link/diff scans | D001/D002/D005/D006/D008/D014 逐项检查；D018/P024 归档一致 |

## 6. 变更控制

- Plan 建立前工作树已有用户提供的 D018 与 `docs/README.md` 修改；它们是本轮输入，在其基础上接受
  设计并登记本 Plan，不覆盖或丢弃；
- PF pre-release 直接替换目标 interface/grammar；不增加 alias、兼容 property、dual renderer 或旧行为测试；
- 测试断言 public behavior 与稳定语义 fragment，不依赖 terminal private helper、完整 ANSI snapshot 或
  易变空格；临时 obsolete-behavior 检查在交付前删除；
- 默认 uv cache 若不可写，所有 uv 命令使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`；网络/coverage/平台限制与
  代码回归分别记录；
- 完成前逐条复核 D018 §10；归档、owner 归并、索引和证据必须在同一完成变更中一致。

## 7. 过程记录

### 基线审计与 Plan

- **状态：** 已完成
- **行动：** 完整阅读 D018、现行 D006、相邻 D005/D008/D014 指针、CLI/request/workflow/presenter 与
  explain/diagnose/merge/apply 测试入口；核对工作树和文档治理；在生产代码修改前接受 D018 并建立本 Plan。
- **结论：** diagnose 的单数 interface 是其余 presenter 迁移的前置；merge 必须先把 request paths 纳入
  workflow result/error 才能消除 presenter 推断；explain 可复用现有 CellPresentation 但必须重建 overview
  分布与 requirement grid；apply 已有足够 facts，主要是 presentation 迁移。
- **证据：** `git status --short --branch` 显示基线 `23d5cbb`，已有 `docs/README.md` 修改与未跟踪 D018；
  `rg` 定位当前 optional `DiagnoseRequest.failure_id`、tuple workflow/presenter、`diagnosed 0 failures`、
  live/explain `--failure`、merge presenter 的硬编码 `1 report` 和 apply 手工 waiver facts；D018 §10 共 12 项。

### 切片 001 — CLI、request、workflow 与 typed error seam

- **状态：** 已完成
- **行动：** `DiagnoseRequest`和CLI改为必填canonical位置ID；workflow改为单数report-first lookup；
  explain/diagnose/merge command failures改用typed errors；merge返回包含全部display paths的
  `MergeCommandResult`。
- **结论：** report命中不读Journal，fallback只读latest Journal；merge按input/compatibility/output
  分段并fail-fast；invocation error与command failure的Usage/exit边界保持分离。
- **证据：** `tests/test_cli.py`、`tests/test_diagnose.py`、`tests/test_report_workflows.py`覆盖完整/短ID、
  缺失/非法/多余ID、report priority、latest fallback、typed not-found与三类merge failure。

### 切片 002 — 共享 result card 与 explain

- **状态：** 已完成
- **行动：** 建立terminal-private result card、fact grid与non-resolving path link；explain改为overview
  success compact rows、六类distribution、Requirements grid、异常Cell cards与唯一next/final。
- **结论：** success计数不再冒充coverage；no-floor与search-failed分桶；fixed/blocked floors保留；
  report-contained anomaly继续stdout/0，report command error走typed stderr。
- **证据：** `tests/test_explain_terminal.py`与`tests/test_terminal.py`覆盖rejection/indeterminate/
  no-floor/search-failed/missing、0/1/N summary、selector recovery、literal text和56/80/120列。

### 切片 003 — diagnose result card 与 live grammar

- **状态：** 已完成
- **行动：** 单个Failure改为Outcome/Impact/Context/Technical card和绿色command final；live/explain
  hint统一为`pf diagnose FAILURE_ID --package PACKAGE`；输出tail限制最后3条非空行。
- **结论：** D005 title/next与D008 Role impact直接通过public presenter seam复用；log缺失不改变
  portable authority；canonical ID贯穿request/workflow/card/final。
- **证据：** `tests/test_diagnose.py`覆盖全部D005 cause文案、Role/disposition matrix、技术字段、
  predecessor补句、tail/log/source、literal output和56/80/120列。

### 切片 004 — apply 与 merge cards

- **状态：** 已完成
- **行动：** `ApplyCommandResult`增加必填package；default/scoped/noop/source-override和minimize
  复用结构化apply card；merge success/error cards逐行消费workflow paths与report facts。
- **结论：** preserved只表示original constraints retained；override paths最多8条；普通成功走stdout，
  实际override整卡走stderr/0；merge无硬编码输入计数并保留重复与顺序。
- **证据：** `tests/test_cli.py`、`tests/test_authorization.py`、`tests/test_report_workflows.py`、
  `tests/test_terminal.py`覆盖四类apply、minimize、complete/incomplete merge和三阶段error。

### 切片 005 — 全量验证、owner 归并与归档

- **状态：** 已完成
- **行动：** 稳定规则已归并D001/D002/D005/D006/D008；D014只补充结构化command result不改变
  merge authority。更新旧端到端展示断言并移除无ID diagnose lifecycle。
- **当前证据：**
  - `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest tests/test_cli.py tests/test_diagnose.py tests/test_explain_terminal.py tests/test_report_workflows.py tests/test_terminal.py -q --no-testmon` → `223 passed in 2.47s`；
  - `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest -q --no-testmon` → `1422 passed in 24.29s`；
  - `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 pytest -q --no-testmon` → `1422 passed in 23.79s`；
    `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python /usr/bin/python3.12 pytest -q --no-testmon` → `1422 passed in 24.97s`；
  - `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --cov=pf --cov-report=term-missing -q` →
    `1411 passed`、`90.64%`，达到`fail_under = 90`；其后只增加D005 public文案矩阵与文档；
  - `ruff check src tests`、`ty check src/pf`、`scripts/generate_report_schema.py --check`通过；
    sandboxed `uv build`因PyPI网络受限失败，network-enabled复核成功生成wheel与sdist；
    `git diff --check`和全仓本地Markdown link审计均无输出。

## 8. 最终验收审计

| D018 §10 | Authoritative evidence | 结论 |
| --- | --- | --- |
| 1 | CLI help/invocation matrix、canonical request和typed not-found public tests | 唯一位置ID grammar、exit 1/3与Usage边界通过 |
| 2 | `rg`扫描production/current owners/public tests；live/explain/Help断言 | 单数全链路，无optional/list-all/空成功或旧命令正文 |
| 3 | recording log locator断言report命中零Journal读取；latest fallback/not-found tests | 仅selected report→latest Journal，不扫描历史 |
| 4 | 五类report anomaly rendering与distribution tests | icon、stdout/0、no-floor/search-failed和权威hint通过 |
| 5 | explain workflow missing/invalid/identity tests及typed error card tests | stderr/3/no Usage；安全recovery回显selector |
| 6 | mixed Cell、projection fixed/blocked floors、0/1/N与next计数 tests | 每target一次、success紧凑、异常独立、单final通过 |
| 7 | apply default/scoped/noop/override、minimize及authorization E2E tests | package/edit/facts唯一来源；preserved与8-path上限通过 |
| 8 | merge workflow fail-fast/typed stages及duplicate ordered path rendering tests | complete/incomplete/error路径完整；无硬编码输入数 |
| 9 | TTY/non-TTY channel/card/final tests | rounded/gutter/bold final和成功红黄facts stdout通过 |
| 10 | explain/apply/diagnose/merge的56/80/120 public presenter tests | 行宽、续行、路径/ID/Cell/literal text通过 |
| 11 | 全部D005 cause title/next与D008 Role matrix、technical/log/tail tests | 稳定文案逐字、字段与public seam通过 |
| 12 | D001/D002/D005/D006/D008/D014 diff、README/archive link扫描 | owners已接管，D014 authority不变，Design/Plan同变更归档 |

未决项：无。一次把3.10/3.11/3.12 full suites并发放在同一工作区会让嵌套pytest observer
qualification互相干扰；按CI方式顺序运行后3.10/3.11均为1422通过，3.12的独立full run也为1422通过，
因此这是无效的并行验证方式，不是产品回归，未改生产代码。

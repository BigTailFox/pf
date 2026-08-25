# PF pytest failure evidence

- **状态：** 现行
- **日期：** 2026-08-25
- **适用范围：** direct pytest `test-command` 的动态失败分类
- **评审来源：** [R003](../reviews/R003-pf-search-indeterminate-review.md)
- **探索证据：** [I001](../investigation/I001-pf-pytest-witness-collection.md)
- **产品与命令：** [D001](D001-pf.md)
- **实现结构：** [D002](D002-pf-implementation.md)
- **搜索算法：** [D003](D003-pf-search-algorithm.md)
- **失败与诊断：** [D005](D005-pf-failure-and-diagnose.md)
- **验证运行语义：** [D008](D008-pf-verification-run.md)
- **运行时静态证据：** [D011](D011-pf-runtime-backed-static-search.md)
- **实施计划：** [P012](../plans/P012-pf-pytest-failure-evidence.md)

本文定义 PF 如何使用 pytest 自己产生的最小 failure witness，把 ordinary collection failure 稳定归一为现有 `TestFail`，同时让 pytest bootstrap、internal、无 failure witness 的 interrupt 与协议故障保持 `ToolFailure`。本文不改变用户测试的 collection/runtest 策略，不建立 pytest lifecycle observer，也不改变 CoordinateSearch 对 `ProbeIndeterminate` 的处置。

本文已按 P012 落地并同步 D001/D002/D005/D008。现行 `TestAdapter` 对默认 `[1]` 的 direct pytest command 使用本文的 failure-witness profile；generic command 与显式自定义 failure codes 保持配置退出码语义。现行 PF 能识别 xdist execution mode，但 v1 未授予 xdist failure Rejection authority：任何 xdist 非零结果仍保守归为 Indeterminate。

## 1. 问题

现行默认规则为：

```text
exit 0   -> TestPass
exit 1   -> TestFail
other    -> ToolFailure
```

pytest 的 ordinary test-module collection failure 默认记录 failed `CollectReport`，随后以 exit 2 中断 session。PF 因而把已经发生的 test-contract failure 归为 `ProbeIndeterminate`。R003 的 `packaging==19.2` × Python 3.10–3.12 正是该路径。

直接把 exit 2 加入默认失败码并不安全。pytest exit code 是 session summary；exit 2 也可能表示 KeyboardInterrupt。实验还确认，early plugin import failure 可以产生 exit 1，第三方 plugin 也可以改写最终 session status。因此，exit 1 或 2 都不能脱离 pytest failure evidence 单独授权结构化 pytest profile 下的 Rejection。

完整 lifecycle observer 同样不是合适的 v1 解法。pytest 的 bootstrap、initial/late conftest、collection、runtest 与 plugin hook topology 会随版本和 plugin 组合变化；PF 不应维护它们的影子状态机。

## 2. 目标与非目标

### 2.1 目标

1. ordinary collection、fixture setup、test call 与 fixture teardown failure 能进入现有 `TEST_FAILURE / Rejection` 路径；
2. pytest 启动、bootstrap、usage/config、internal error、无 witness 的 interrupt 与协议故障保持 Indeterminate；
3. 不解析 stderr、traceback 或 node ID，不判断 project/dependency/plugin/harness 的责任归属；
4. 不改变用户声明的测试选择、collection continuation、`maxfail` 或 runtest 顺序；
5. pytest-specific 复杂度停留在 `TestAdapter` implementation 内，RuntimeEvaluator、FailurePolicy 与 CoordinateSearch 继续只消费现有 `TestOutcome`；
6. observer 缺失或损坏只降低分类完整性，不能制造错误 Rejection。

### 2.2 非目标

- 不使用 `--continue-on-collection-errors` 或 PF-owned `pytest.main()` wrapper；
- 不观察 `OBSERVER_READY -> CONTRACT_ENTERED -> ...` lifecycle；
- 不做 traceback frame、module、distribution 或 project graph attribution；
- 不保证识别所有 candidate incompatibility；
- 不修改 static PASS 的语义；
- 不引入 Unknown-aware search；真实 `ProbeIndeterminate` 仍终止当前 cell；
- 不实现 harness alternative-plan search；
- 不为任意 test runner 建立 classifier registry；
- 不把 test process 或 failure-witness plugin 做成安全工具。

## 3. 实验依据

[I001](../investigation/I001-pf-pytest-witness-collection.md) 保存 throwaway prototype 的实验问题、输入矩阵、观察与结论。prototype source 不进入仓库；原型只实现两个公开 pytest hook，并保持 pytest 原始退出行为。

CPython 3.10 上验证的 pytest 版本为：

```text
6.2.5
7.0.1, 7.4.4
8.0.2, 8.4.2
9.0.2, 9.1.1
```

七个版本得到相同的核心矩阵：

| 场景 | Exit | 观察到的直接 witness |
| --- | ---: | --- |
| pass | 0 | 无 |
| assertion failure | 1 | test/call failed |
| fixture setup failure | 1 | test/setup failed |
| fixture teardown failure | 1 | test/teardown failed |
| test-module import failure | 2 | collection failed |
| late/nested conftest import failure | 2 | collection failed |
| initial conftest import failure | 4 | 无 |
| collection hook internal error | 3 | 无 |
| KeyboardInterrupt | 2 | 无 |
| early plugin import failure | 1 | 无 |
| no tests collected | 5 | 无 |
| invalid option | 4 | 无 |

反例矩阵同样稳定：passing session 可被 plugin 改写成 exit 1 而没有 failure witness；failing session 可被改写成 exit 0 但仍有 witness；failure report 后发生 internal error 时 exit 3 与先前 witness 可以同时存在。由此得到两个约束：

1. process exit 与 witness 必须联合分类；
2. 矛盾事实或 pytest internal error 必须优先 fail closed。

当前 PF plugin 组合 `pytest-cov==7.1.0`、`pytest-env==1.7.0`、`pytest-testmon==2.2.0`、`pytest-benchmark==5.2.3` 与 `pytest==9.1.1` 在 Python 3.10–3.12 上得到相同结果。针对 `packaging==19.2` 的聚焦复现也在三个 Python minor 中得到 exit 2 + collection witness。

核心矩阵只在 CPython 3.10 上跑过全部七个 pytest 版本。v1 的资格依赖 pytest public hook contract，并由跨 pytest major 与 CPython minor 的 representative integration matrix 经验确认；未覆盖或未通过的组合不能获得 negative-evidence authority。落地测试须在 CPython 3.11 与 3.12 上复跑各 major 代表版本的核心 witness 场景，测试结果可以收缩而不能无证据扩大资格范围。

pytest 6.0.2 不纳入资格范围：其 assertion rewriting 在 CPython 3.10 上先于本设计目标失败。v1 从 6.2.5 起支持。

## 4. 决策

PF 在现有 `TestOperations.run(...) -> TestOutcome` interface 后加深 `TestAdapter`：

```text
TestAdapter.run(...)
  ├── generic exit-code profile
  └── pytest failure-witness profile
          ├── 注入 PF-owned zero-additional-dependency plugin
          ├── 执行原 test command
          ├── 校验 ProcessResult + finalized-summary protocol
          └── 返回 TestPass | TestFail | ToolFailure
```

这里没有新的外部 seam。pytest profile 是 `TestAdapter` 的私有 implementation；调用方不学习 plugin module、临时路径、summary 文件或 pytest hook。

现有 `TestOutcome` union 保持不变：

```text
TestPass
TestFail
ToolFailure
```

`TestFail` 是 adapter 已完成证据归一后的结果，不再等价于“进程恰好返回用户配置的整数”。RuntimeEvaluator 继续把它映射为 `TestFailEvaluation`，FailurePolicy 继续产生 `TEST_FAILURE @ test`，CoordinateSearch 继续只消费 Rejection/Indeterminate。

## 5. Profile 选择

pytest failure-witness profile 只在以下条件同时成立时启用：

1. `test-failure-exit-codes == [1]`；
2. `test-command` 是 PF 可机械识别的 direct pytest invocation：

```text
pytest ...
py.test ...
python -m pytest ...
python3 -m pytest ...
pythonX.Y -m pytest ...
```

可执行文件可以是绝对路径；识别使用 basename，不依赖 shell。wrapper、`coverage run`、tox、nox、`env` 前缀或其他命令不做猜测，继续使用 generic exit-code profile。

只要用户把 `test-failure-exit-codes` 改成其他集合，即视为显式选择 generic contract，现行配置语义保持不变。ordinary collection failure 的修复是本 profile 的 failure witness，不是把 exit 2 加入 `test-failure-exit-codes`。v1 不新增 `test-adapter` 配置项；未来若真实项目需要为 wrapper 显式选择 pytest profile，再单独扩展配置。

profile 选择必须只有一个实现所有者。`TestAdapter` implementation 内的纯 selector 同时产生实际执行 profile 与 `test outcome policy identity`；argv/env 注入、结果分类和 `evaluation_policy_identity` 必须消费同一个选择结果，不得分别复制 direct-command predicate。

## 6. Minimal failure-witness plugin

PF 注入的 plugin 常规路径只依赖 Python standard library 与正在运行它的 pytest。它不导入 PF、project source 或其他 harness package；仅在 pytest-xdist 已被加载时，可选使用 xdist 公开的 controller/worker 判定 helper 标记未资格化 execution mode。

plugin 在 process 内累计不可变 fact set，实现三个 fact hook、一个 execution-mode hook 和一个最终提交 hook：

```text
pytest_collectreport(report)
  report.failed -> COLLECTION_FAILED / collect

pytest_runtest_logreport(report)
  report.failed and when in {setup, call, teardown}
    -> TEST_FAILED / when

pytest_internalerror(...)
  -> INTERNAL_ERROR / pytest

pytest_sessionstart(session)
  -> 记录 serial | xdist | unknown execution mode

pytest_cmdline_main(config)
  -> 在被包裹的 command-line action、sessionfinish、
     unconfigure 与 config cleanup 结束后
     原子提交本 process 的 finalized summary
```

三个 fact hook 使用 pytest 的公开 hook marker 与 `tryfirst=True`。`pytest_internalerror` 只记录 internal-error fact、返回 `None`，不抑制 pytest 自己的 fallback handling。`pytest_sessionstart` 只保存 execution mode，不建立阶段状态。

`pytest_cmdline_main` 使用兼容资格范围的 hook-wrapper 形式并以 `tryfirst=True` 成为最外层 wrapper：先 `yield` 让 pytest 默认 command-line action 运行，再原子提交 summary。pytest 的默认 action 在返回前已执行 session-finish 与 unconfigure/cleanup，因此 commit 不早于这些公开 teardown 路径。若被包裹的 action 或 teardown 抛异常，wrapper 先补入 `INTERNAL_ERROR` 并尝试提交，然后保留原异常语义。

plugin 不保存 traceback、longrepr、nodeid、path、测试名称、异常正文或输出正文。fact hook 不写独立 event；只有最终提交会产生 adapter 可信的 evidence。

以下内容不属于 plugin：

- pytest lifecycle state 或阶段转移；
- collection 是否“属于项目”；
- plugin/conftest 的加载阶段；
- 最终 disposition；
- pytest exit status 重写；
- observer-ready marker。

failed `CollectReport` 或 failed setup/call/teardown `TestReport` 本身就是直接 negative witness。PF 不进一步判断 failure 由谁触发。

`INTERNAL_ERROR` 是直接的 execution-invalid witness，不是 lifecycle attribution。只要存在合法 `INTERNAL_ERROR`，无论 failure witness 与最终 exit 为何，本次结果都只能是 `ToolFailure`。

finalized summary 是 evidence commit，不是 lifecycle observer。它只证明“该 process 已把自己观察到的 fact set 一次性提交”，不表示 PF 知道 pytest 进入过哪些阶段。

## 7. 注入与临时资源

每次 pytest-profile invocation 使用一个 run-unique `TemporaryDirectory`，内部包含：

```text
plugin/       单个复制出的 PF-owned plugin module
evidence/     原子 finalized-summary 文件
```

PF 把 isolated `plugin/` 前置到该进程的 `PYTHONPATH`，保留原有 `PYTHONPATH`，并在 pytest launcher prefix 后、用户参数前插入显式 `-p <unique-module-name>`。PF 不修改用户参数，不增加 collection continuation 或 `maxfail` 选项。

evidence directory 与 run nonce 通过只对该进程生效的环境变量传入；Process Log 只记录环境变量名，值继续按 D007 脱敏。临时目录在 adapter 完成分类后释放，不进入 source snapshot、prepared environment、报告或诊断索引。

plugin source 作为 PF wheel 的实现资源发布，但复制出的 module 是独立顶层 module，因此 target environment 不需要安装或导入 PF。若 plugin resource、临时目录或注入 argv/env 无法准备，adapter 仍执行未注入 observer 的原 test command：完整 exit 0 仍为 `TestPass`，任何非零结果均为 `ToolFailure`。这样降级只损失 negative-evidence completeness，不需要伪造未发生的 `ProcessResult.start_error`。

## 8. Ephemeral evidence protocol

协议 identity 为：

```text
pf-pytest-failure-witness-v1
```

每个进入 `pytest_cmdline_main` finalization wrapper 的 process 写一份 canonical JSON summary；即使 fact set 为空也可提交：

```json
{
  "execution_mode": "serial",
  "facts": [
    {
      "kind": "COLLECTION_FAILED",
      "phase": "collect"
    }
  ],
  "finalized": true,
  "protocol": "pf-pytest-failure-witness-v1",
  "pytest_version": "8.3.5",
  "python_implementation": "cpython",
  "python_minor": "3.12",
  "run_nonce": "<run-unique value>"
}
```

summary 的 UTF-8 无 BOM 编码、字段集合、字段顺序、JSON separators 与末尾单个 LF 由 protocol 固定；plugin 使用 `sort_keys=True`、compact separators 与 `ensure_ascii=True` 生成唯一 canonical bytes。`facts` 必须先按 `(kind, phase)` 去重并排序。

`execution_mode` 只允许 `serial | xdist | unknown`。`unknown` 表示 execution plugin 已可见，但公开 mode helper 缺失、抛异常或给出矛盾结果；它没有 negative-evidence authority。

合法的 `(kind, phase)` 只有：

```text
COLLECTION_FAILED / collect
TEST_FAILED       / setup | call | teardown
INTERNAL_ERROR    / pytest
```

每个 process 用同目录临时文件完整写入 summary，再原子替换成 `summary-<32 lowercase hex>.json`；不同 process 不共享可变 JSON document。临时文件名不匹配 final grammar，因此中断写入不会被当作 finalized evidence。

多进程写入规则：

1. 不同 process 可以并发写入等价或不同的 finalized summary。adapter 不区分 writer，也不验证 per-process uniqueness；所有合法 summary 的 facts 按 `(kind, phase)` set-union 后参与 §9。
2. protocol 是 multiprocess-safe 的；这只是文件并发属性，不构成 pytest-xdist 或任意 execution plugin 的 compatibility qualification。
3. 下列情况才是协议无效，不进入 §9 的 valid witness 分支：
   - 同一 run 内 summary 的 `protocol`、`run_nonce`、`execution_mode`、`python_implementation`、`python_minor` 或 `pytest_version` 不一致；
   - `finalized` 不是 literal `true`，或 facts 非法、未排序、重复；
   - unknown file、残留临时文件、超限、非法 JSON/UTF-8、未知字段、非 canonical bytes 或非单一 object。

等价重复合法；互相矛盾的 identity 字段不合法。

adapter 在 process 结束后进行一次有界读取：

- 最多 1024 个 final summary；
- 每个文件最多 4 KiB；
- 只接受精确字段、枚举、protocol、nonce、CPython identity 与 pytest version；
- 所有 summary 的 `protocol`、`run_nonce`、`execution_mode`、`python_implementation`、`python_minor` 与 `pytest_version` 必须一致。

v1 negative-evidence authority 只授予 `execution_mode == "serial"` 且 qualification matrix 中明确通过的 `(CPython minor, pytest major profile)`。首轮 matrix 用 pytest `6.2.5`、`7.0.1`、`7.4.4`、`8.0.2`、`8.4.2`、`9.0.2`、`9.1.1` 对 CPython 3.10–3.12 上每个 major 的最低代表与当前最新版本建立 profile；同一已资格化 major 内的普通 stable patch/minor 由该 profile 接受。未覆盖、未通过、prerelease、local build、无法规范化或范围外组合可以正常产生 exit 0 Pass，但其 failure witness 不授权 Rejection。

`execution_mode` 使用 pytest-xdist 公开的 `is_xdist_controller` / `is_xdist_worker` 语义判定，不从 `-n` argv 猜测，因为 xdist 也可以由 pytest config `addopts` 启用。只要 controller 或 worker 语义成立，该 process 的 summary 必须标记 `xdist`；helper 不可用或判定不完整时标记 `unknown`。v1 对 xdist 的唯一承诺是 fail closed：完整 exit 0 仍可 `TestPass`，任何非零结果均为 `ToolFailure`。

summary commit failure 不得从 plugin 抛出并改变 pytest 结果。仅 finalized summary 中的 fact 可以授权 Rejection；非零退出且缺失 finalized summary 时，adapter 必须 fail closed。

## 9. Outcome 决策表

分类先应用 D005 的完整性门槛。存在 timeout、signal、start error、`stdout_complete == false` 或 `stderr_complete == false` 时，无论 summary 内容如何都返回 `ToolFailure`。

通过完整性门槛后，按以下顺序分类：

1. evidence directory 为空时，只有 exit 0 可为 `TestPass`；任何非零退出都是无 finalized evidence 的 `ToolFailure`。
2. 只要存在 artifact，就必须先完整验证 protocol。malformed、超限、identity 冲突、残留 temp 或非 canonical summary 统一返回 `ToolFailure`。
3. 合法 `INTERNAL_ERROR` 以最高优先级返回 `ToolFailure`。
4. `execution_mode != "serial"` 或 runtime/pytest qualification 不合格时，只有 exit 0 且无 failure witness 可为 `TestPass`；其余统一为 `ToolFailure`。
5. 只有合法、finalized、已资格化的 serial evidence 才进入以下机械规则：

| Exit | Failure witness | TestOutcome |
| ---: | --- | --- |
| 0 | 无 | `TestPass` |
| 0 | 任一 failure witness | `ToolFailure`，事实矛盾 |
| 1 | 至少一个 collection 或 test witness | `TestFail` |
| 1 | 无 witness | `ToolFailure`，unwitnessed pytest failure |
| 2 | 至少一个 collection 或 test witness | `TestFail` |
| 2 | 无 witness | `ToolFailure` |
| 3、4、5、6 或其他 | 任意 | `ToolFailure` |

可以使用稳定 `summary_code` 区分 `pytest-evidence-invalid`、`pytest-internal-error`、`pytest-failure-unwitnessed` 与 `pytest-outcome-conflict`，但 summary 不改变 disposition。

通过前置门槛后，failure witness 对 exit 1/2 phase-insensitive：一个已经由 failed `CollectReport` 或 `TestReport` 证明的动态 contract failure，不会因随后 session 以 interrupted summary 结束而消失。这直接覆盖 pytest 默认的 ordinary collection abort，也让 test failure 后的 interrupt 得到相同结果；无 failure witness 的 exit 2 仍是 `ToolFailure`。这不是“failure witness 无条件压过所有终态”：exit 0 冲突、`INTERNAL_ERROR`、不完整执行与协议故障仍优先 fail closed。

若用户显式选择 generic profile，则完全沿用现行规则：exit 0 Pass、配置列表中的非零码 Fail、其他结果 ToolFailure；pytest witness protocol 不参与该路径。

## 10. Evidence authority 与持久化

本设计不扩展 `package-floor.json` Schema 1，也不把 ephemeral summary 写入报告。原因是 plugin facts 只用于 adapter 内部把 raw process outcome 归一为既有 `TestFail`；调用方需要知道的是稳定 TestOutcome，而不是 pytest hook topology。

报告仍保留：

- `TestFailEvaluation`；
- `TEST_FAILURE @ test`；
- 完整 `ProcessResult`，包括 exit 1 或 2；
- Attempt/Proposal 与 project/environment plan identity；
- 对应 Process Log 的本地 diagnosis association。

`evaluation_policy_identity` 必须新增 test outcome policy identity，并区分：

```text
configured-exit-code-v1
pytest-failure-witness-v1
```

这样不同分类策略下的 Evaluation、cache、Attempt、merge 与 report authority 不可混用。实际 pytest selection 与 CPython interpreter 已属于 Proposal 的 environment graph；plugin summary 的实际 runtime identity 只用于 adapter 当次资格校验，不另建搜索坐标或公共报告字段。profile identity 与实际执行 profile 必须来自 §5 的同一个 selector。

## 11. 可靠性边界

plugin 只是把 pytest 已公开报告的 failure 收成 adapter 内部证据，不是安全工具。

- 原始 pytest stdout/stderr 继续只进入 Process Log，不参与分类；
- summary 不包含 secret、源码内容、异常文本或用户路径；
- run-unique nonce 与一次性目录防止 stale summary 被误用；
- atomic per-summary file 防止正常并发写入互相截断，也保证部分 fact set 不会授权 Rejection；
- `SecureLogDirectory` 保持 D010 定义的私有 RunLogStore seam，不扩大成通用 evidence filesystem。

## 12. 失败与降级

| 场景 | 结果 |
| --- | --- |
| plugin resource/临时目录/注入无法准备 | 执行原 command；exit 0 可 Pass，非零为 `ToolFailure` |
| plugin import/bootstrap failure | 通常 exit 1，无 finalized summary，`ToolFailure` |
| summary commit failure | pytest 原结果不变；非零且无 finalized summary 时 `ToolFailure` |
| initial conftest/config/usage failure | 无合格 witness，`ToolFailure` |
| pytest internal error | `INTERNAL_ERROR` 优先，最终 exit 被改写也为 `ToolFailure` |
| KeyboardInterrupt before any failure witness | exit 2，无 witness，`ToolFailure` |
| KeyboardInterrupt after collection/test witness | exit 2 + failure witness，`TestFail` |
| collection failure | exit 2 + collection witness，`TestFail` |
| assertion/setup/teardown failure | exit 1 + test witness，`TestFail` |
| unsupported pytest failure | summary runtime/version 不合格，`ToolFailure` |
| pytest-xdist pass | 完整 exit 0 + 无 failure witness，`TestPass` |
| pytest-xdist 非零退出 | v1 未资格化，不论 summary facts 都为 `ToolFailure` |
| plugin 改写失败为 exit 0 | witness 与 exit 冲突，`ToolFailure` |

classifier 不要求 complete。一个真实 candidate incompatibility 可以因 observer 或 pytest profile 不受支持而保持 Indeterminate；它不能因不完整证据变成错误 Rejection。

## 13. 测试与资格矩阵

实现不得只测试 plugin 私有函数。主要测试面是 `TestOperations.run(...) -> TestOutcome`：

1. command-shape 表验证 pytest/generic profile 选择与 argv/env 保真；
2. recording/fake ProcessRunner 验证完整 outcome 决策表；
3. protocol 表覆盖空 facts、malformed、truncated、non-canonical bytes、unknown field、wrong nonce/runtime/version、身份字段冲突、超限、残留 temp 与等价重复 summary 的 set-union；
4. real pytest matrix 覆盖 6.2.5、7.0.1、7.4.4、8.0.2、8.4.2、9.0.2、9.1.1；核心 witness 场景在 CPython 3.11 与 3.12 上复跑每个 major 的最低代表与当前最新版本，失败组合必须从 qualification authority 移除；
5. current PF plugin matrix 覆盖 Python 3.10–3.12；
6. integration 覆盖 module collection import、nested conftest、setup/call/teardown、initial conftest、internal error、KeyboardInterrupt、early plugin import 与 exit rewrite；特别覆盖 test/collection failure 后 interrupt，以及 internal error 后把 exit 3 分别改写为 1/2；
7. finalization integration 覆盖 failure 后 internal error、sessionfinish/unconfigure/config cleanup 抛异常、summary commit 失败、残留 temp 与非零退出无 finalized summary；
8. xdist guard integration 至少覆盖 argv `-n2`、config `addopts=-n2`、worker failure、worker internal error 与 worker crash，并证明它们不获得 v1 Rejection authority；
9. `packaging==19.2` × Python 3.10–3.12 形成 `TestFailEvaluation -> ProbeRejection`；
10. generic command 与显式自定义 failure exit codes 保持现行行为；
11. 安装 PF wheel 后验证 plugin resource 可复制并在独立 target environment 中加载；
12. 全量 pytest、Ruff、ty、build 与 `git diff --check` 通过。

新增 pytest major、修改 hook/summary protocol、扩大 direct command shape 或为 xdist 授予 Rejection authority 前，必须更新 qualification matrix 与 test outcome policy identity。已资格化 major 内的普通 stable patch/minor upgrade 继续由对应 profile 接受，但 CI 至少固定每个 major 的最低代表与当前最新版本。

## 14. 现行契约同步

本文落地时已同步以下唯一所有者：

- D001：补充 direct pytest profile 的结构化 failure witness；`test-failure-exit-codes` 默认仍为 `[1]`，不把 exit 2 加入通用默认；
- D002：把 `TestAdapter` 从纯 exit-code adapter 加深为 generic/pytest 两个私有 profile，并记录 embedded plugin resource；
- D005：把“测试以配置失败码退出”扩展为“generic configured failure 或 pytest-profile witnessed failure”，保持 `TEST_FAILURE @ test` 的 disposition 规则；
- D008：`TestFailEvaluation` 的 Attempt/Journal 投影不变，只更新 TestFail 的来源说明；
- `evaluation_policy_identity`：已加入 test outcome policy identity。

D003、D004、D006、D007、D009–D012 不改变其算法、static、展示、日志、安全、架构或 harness 契约。D004/D007/D011 中关于测试失败来源的过时表述已同步到与本文一致；这不改变它们各自拥有的契约。

## 15. 验收不变量

1. PF 不按 traceback 归责测试失败；
2. pytest failure witness 扩大可证明的 Rejection，并用 witness 卡住假 Pass 与假 Rejection；fail-closed 门槛只升不降；
3. ordinary collection 或 test failure 后的 exit 2 可以产生 `TestFail`，无 failure witness 的 exit 2 不可以；
4. exit 1 在 pytest profile 中也必须有 direct failure witness；
5. pytest internal error、协议损坏与 outcome 冲突不能产生 Rejection；`INTERNAL_ERROR` authority 不依赖最终 exit status；
6. user test selection、collection continuation 与 runtest order 不因 PF observer 改变；
7. RuntimeEvaluator、FailurePolicy 与 CoordinateSearch 不学习 pytest 细节；
8. 真实 Indeterminate 仍终止当前 cell；
9. report、merge、cache 与 apply authority 绑定 test outcome policy identity；
10. generic test command 与用户显式 exit-code contract 保持兼容；
11. 只有 finalized summary 中的 fact 可以授权 Rejection，部分写入或缺失提交不可以；
12. v1 的 xdist/unknown execution mode 不得产生 Rejection。

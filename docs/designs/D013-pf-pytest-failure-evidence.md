# PF pytest failure evidence

- **状态：** 草案
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
- **实施计划：** 尚未建立

本文定义 PF 如何使用 pytest 自己产生的最小 failure witness，把 ordinary collection failure 稳定归一为现有 `TestFail`，同时让 pytest bootstrap、internal、interrupt 与协议故障保持 `ToolFailure`。本文不改变用户测试的 collection/runtest 策略，不建立 pytest lifecycle observer，也不改变 CoordinateSearch 对 `ProbeIndeterminate` 的处置。

本文尚未落地。实现完成并同步 D001/D002/D005/D008 前，现行行为仍是 `TestAdapter` 仅按用户配置的 `test-failure-exit-codes` 分类。

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
- 不把 test process 变成针对恶意项目代码的安全 sandbox。

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

pytest 6.0.2 不纳入资格范围：其 assertion rewriting 在 CPython 3.10 上先于本设计目标失败。v1 从 6.2.5 起支持。

## 4. 决策

PF 在现有 `TestOperations.run(...) -> TestOutcome` interface 后加深 `TestAdapter`：

```text
TestAdapter.run(...)
  ├── generic exit-code profile
  └── pytest failure-witness profile
          ├── 注入 PF-owned zero-additional-dependency plugin
          ├── 执行原 test command
          ├── 校验 ProcessResult + witness protocol
          └── 返回 TestPass | TestFail | ToolFailure
```

这里没有新的外部 seam。pytest profile 是 `TestAdapter` 的私有 implementation；调用方不学习 plugin module、临时路径、event 文件或 pytest hook。

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

只要用户把 `test-failure-exit-codes` 改成其他集合，即视为显式选择 generic contract，现行配置语义保持不变。v1 不新增 `test-adapter` 配置项；未来若真实项目需要为 wrapper 显式选择 pytest profile，再单独扩展配置。

## 6. Minimal failure-witness plugin

PF 注入的 plugin 只依赖 Python standard library 与正在运行它的 pytest。它不导入 PF、project source 或其他 harness package。

plugin 只实现：

```text
pytest_collectreport(report)
  report.failed -> COLLECTION_FAILED / collect

pytest_runtest_logreport(report)
  report.failed and when in {setup, call, teardown}
    -> TEST_FAILED / when
```

两个 hook 使用 pytest 的公开 hook marker 与 `tryfirst=True`。同一 process 对相同 `(kind, phase)` 最多写一个 event；plugin 不保存 traceback、longrepr、nodeid、path、测试名称或输出正文。

以下内容不属于 plugin：

- session state；
- collection 是否“属于项目”；
- plugin/conftest 的加载阶段；
- 最终 disposition；
- pytest exit status 重写；
- completed/ready lifecycle marker。

failed `CollectReport` 或 failed setup/call/teardown `TestReport` 本身就是直接 negative witness。PF 不进一步判断 failure 由谁触发。

## 7. 注入与临时资源

每次 pytest-profile invocation 使用一个 run-unique `TemporaryDirectory`，内部包含：

```text
plugin/       单个复制出的 PF-owned plugin module
evidence/     原子 event 文件
```

PF 把 isolated `plugin/` 前置到该进程的 `PYTHONPATH`，保留原有 `PYTHONPATH`，并在 pytest launcher prefix 后、用户参数前插入显式 `-p <unique-module-name>`。PF 不修改用户参数，不增加 collection continuation 或 `maxfail` 选项。

evidence directory 与 run nonce 通过只对该进程生效的环境变量传入；Process Log 只记录环境变量名，值继续按 D007 脱敏。临时目录在 adapter 完成分类后释放，不进入 source snapshot、prepared environment、报告或诊断索引。

plugin source 作为 PF wheel 的实现资源发布，但复制出的 module 是独立顶层 module，因此 target environment 不需要安装或导入 PF。若 module 无法复制、导入或写 evidence，非零测试结果不能使用 pytest profile 授权 Rejection。

## 8. Ephemeral evidence protocol

协议 identity 为：

```text
pf-pytest-failure-witness-v1
```

每个 event 是一个独立 canonical JSON object：

```json
{
  "kind": "COLLECTION_FAILED",
  "phase": "collect",
  "protocol": "pf-pytest-failure-witness-v1",
  "pytest_version": "8.3.5",
  "run_nonce": "<run-unique value>"
}
```

`TEST_FAILED` 的 phase 只能是 `setup | call | teardown`。每个 process 用同目录临时文件完整写入一个 event，再原子替换成唯一 final filename；不同 worker 不共享可变 JSON document。协议允许多 process/xdist 并发写入，但 v1 不因该能力承诺任意 pytest execution plugin 都已资格化。

adapter 在 process 结束后进行一次有界读取：

- 最多 1024 个 final event；
- 每个文件最多 4 KiB；
- 只接受精确字段、枚举、protocol、nonce 与 pytest version；
- 所有 event 的版本必须一致；
- unknown file、残留临时文件、重复冲突、超限、非法 JSON/UTF-8 或未知字段均使协议无效。

v1 negative-evidence 资格范围是 stable pytest `>=6.2.5,<10`。prerelease、local build、无法规范化或范围外版本可以正常产生 exit 0 Pass，但其 failure witness 不授权 Rejection。

event write failure 不得从 plugin 抛出并改变 pytest 结果。缺失 event 在 adapter 中 fail closed。

## 9. Outcome 决策表

分类先应用 D005 的完整性门槛。存在 timeout、signal、start error、`stdout_complete == false` 或 `stderr_complete == false` 时，无论 event 内容如何都返回 `ToolFailure`。

通过完整性门槛后，pytest profile 使用以下机械规则：

| Exit | Valid witness | TestOutcome |
| ---: | --- | --- |
| 0 | 无 | `TestPass` |
| 0 | 任一 failure witness | `ToolFailure`，事实矛盾 |
| 1 | 至少一个 collection 或 test witness | `TestFail` |
| 1 | 无 witness | `ToolFailure`，unwitnessed pytest failure |
| 2 | 至少一个 collection witness | `TestFail` |
| 2 | 仅 test witness 或无 witness | `ToolFailure` |
| 3、4、5、6 或其他 | 任意 | `ToolFailure` |

协议 malformed、unsupported、超限或相互矛盾时，不进入上表的 valid witness 分支，统一返回 `TOOL_FAILURE @ test`。可以使用稳定 `summary_code` 区分 `pytest-evidence-invalid`、`pytest-failure-unwitnessed` 与 `pytest-outcome-conflict`，但 summary 不改变 disposition。

exit 2 只因 collection witness 获得新增授权。这直接覆盖 pytest 默认的 ordinary collection abort，同时不把无 witness KeyboardInterrupt 当作 Rejection。exit 3 始终表示本次 pytest execution 不可靠；即使 internal error 前已经写出 witness，v1 仍保守返回 Indeterminate。

若用户显式选择 generic profile，则完全沿用现行规则：exit 0 Pass、配置列表中的非零码 Fail、其他结果 ToolFailure；pytest witness protocol 不参与该路径。

## 10. Evidence authority 与持久化

本设计不扩展 `package-floor.json` Schema 1，也不把 ephemeral event 写入报告。原因是 plugin facts 只用于 adapter 内部把 raw process outcome 归一为既有 `TestFail`；调用方需要知道的是稳定 TestOutcome，而不是 pytest hook topology。

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

这样不同分类策略下的 Evaluation、cache、Attempt、merge 与 report authority 不可混用。实际 pytest selection 已属于 Proposal 的 environment graph；plugin event 的 exact version 只用于 adapter 当次资格校验，不另建搜索坐标或公共报告字段。

## 11. 可靠性与安全边界

- 原始 pytest stdout/stderr 继续只进入 Process Log，不参与分类；
- event 不包含 secret、源码内容、异常文本或用户路径；
- run-unique nonce 与一次性目录防止 stale event 被误用；
- atomic per-event file 防止正常并发写入互相截断；
- project code 知道 evidence directory 后可以导致协议无效，因此最坏结果是 Indeterminate；
- 本设计不声称能在同一用户权限下抵抗恶意 project code 伪造进程内事实。PF 已在执行用户声明的 test contract，plugin 不是安全 attestation；
- `SecureLogDirectory` 保持 D010 定义的私有 RunLogStore seam，不扩大成通用 evidence filesystem。

## 12. 失败与降级

| 场景 | 结果 |
| --- | --- |
| plugin resource/临时目录无法建立 | `ToolFailure`，不启动或不授权 pytest rejection |
| plugin import/bootstrap failure | 通常 exit 1，无 witness，`ToolFailure` |
| event write failure | pytest 原结果不变；非零且无 witness 时 `ToolFailure` |
| initial conftest/config/usage failure | 无合格 witness，`ToolFailure` |
| collection hook/internal error | exit 3，`ToolFailure` |
| KeyboardInterrupt before any collection witness | exit 2，无 witness，`ToolFailure` |
| collection failure | exit 2 + collection witness，`TestFail` |
| assertion/setup/teardown failure | exit 1 + test witness，`TestFail` |
| unsupported pytest failure | event 版本不合格，`ToolFailure` |
| plugin 改写失败为 exit 0 | witness 与 exit 冲突，`ToolFailure` |

classifier 不要求 complete。一个真实 candidate incompatibility 可以因 observer 或 pytest profile 不受支持而保持 Indeterminate；它不能因不完整证据变成错误 Rejection。

## 13. 测试与资格矩阵

实现不得只测试 plugin 私有函数。主要测试面是 `TestOperations.run(...) -> TestOutcome`：

1. command-shape 表验证 pytest/generic profile 选择与 argv/env 保真；
2. recording/fake ProcessRunner 验证完整 outcome 决策表；
3. protocol 表覆盖 malformed、truncated、unknown field、wrong nonce/version、冲突、超限、残留 temp 与并发 event；
4. real pytest matrix 覆盖 6.2.5、7.0.1、7.4.4、8.0.2、8.4.2、9.0.2、9.1.1；
5. current PF plugin matrix 覆盖 Python 3.10–3.12；
6. integration 覆盖 module collection import、nested conftest、setup/call/teardown、initial conftest、internal error、KeyboardInterrupt、early plugin import 与 exit rewrite；
7. `packaging==19.2` × Python 3.10–3.12 形成 `TestFailEvaluation -> ProbeRejection`；
8. generic command 与显式自定义 failure exit codes 保持现行行为；
9. 安装 PF wheel 后验证 plugin resource 可复制并在独立 target environment 中加载；
10. 全量 pytest、Ruff、ty、build 与 `git diff --check` 通过。

新增 pytest major、修改 hook/event protocol 或扩大 direct command shape 前，必须更新 qualification matrix 与 test outcome policy identity。普通 patch/minor upgrade 仍由 supported range 接受，但 CI 至少固定每个 major 的最低代表与当前最新版本。

## 14. 对现行契约的取代

本文获批并落地时，应同步以下唯一所有者；落地前它们仍是现行规则：

- D001：补充 direct pytest profile 的结构化 failure witness；`test-failure-exit-codes` 默认仍为 `[1]`，不把 exit 2 加入通用默认；
- D002：把 `TestAdapter` 从纯 exit-code adapter 加深为 generic/pytest 两个私有 profile，并记录 embedded plugin resource；
- D005：把“测试以配置失败码退出”扩展为“generic configured failure 或 pytest-profile witnessed failure”，保持 `TEST_FAILURE @ test` 的 disposition 规则；
- D008：`TestFailEvaluation` 的 Attempt/Journal 投影不变，只更新 TestFail 的来源说明；
- `evaluation_policy_identity`：加入 test outcome policy identity。

D003、D004、D006、D007、D009–D012 不改变其算法、static、展示、日志、安全、架构或 harness 契约。文档索引在实现落地后把 D013 改为现行并关联后续 Plan。

## 15. 验收不变量

1. PF 不按 traceback 归责测试失败；
2. pytest failure witness 只扩大可靠 Rejection 集合，不降低 fail-closed 门槛；
3. ordinary collection failure 可以产生 `TestFail`，无 witness exit 2 不可以；
4. exit 1 在 pytest profile 中也必须有 direct failure witness；
5. pytest internal error、协议损坏与 outcome 冲突不能产生 Rejection；
6. user test selection、collection continuation 与 runtest order 不因 PF observer 改变；
7. RuntimeEvaluator、FailurePolicy 与 CoordinateSearch 不学习 pytest 细节；
8. 真实 Indeterminate 仍终止当前 cell；
9. report、merge、cache 与 apply authority 绑定 test outcome policy identity；
10. generic test command 与用户显式 exit-code contract 保持兼容。

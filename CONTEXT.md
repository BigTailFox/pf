# PF Domain

PF 为 Python 分发包在明确运行环境与验证策略下寻找可验证的直接依赖下界。本页只固定现行术语；行为规则见 [工程文档索引](docs/README.md)。

## Language

**Cell**

一个 package、精确 target、CPython minor 和 extra surface 组成的兼容性环境。

_Avoid_: Environment, platform

**Attempt**

PF 在已知 cell、源码快照和策略下，对一种解析方式或精确受管向量进行的验证尝试；环境解析成功前它已经存在。

_Avoid_: Failed Proposal, run

**Baseline Attempt**

按当前声明寻找最高版本验证锚点的 Attempt。

_Avoid_: Baseline Proposal

**Declaration Attempt**

按当前声明做最低直接解析的 Attempt；它验证声明下界，不是搜索探针。

_Avoid_: Probe Attempt, lowest-direct Evaluation

**Probe Attempt**

对一个精确受管向量取得搜索证据的 Attempt。

_Avoid_: Version check

**Proposal**

已经成功解析并确认实际依赖图的不可变候选方案。

_Avoid_: Attempt, candidate version

**Rejection**

完整且确定的事实证明一个 Attempt 不满足 PF 验证契约；它只否定该完整 Attempt，不归因到单个 dependency version。

_Avoid_: Error, tool failure, version failure

**Indeterminate**

PF 无法为一个 Attempt 获得完整、可靠兼容性结果的状态。

_Avoid_: Rejection, incompatible

**Cause**

Adapter 根据脱敏机械事实给出的稳定操作原因；它回答发生了什么，但不决定搜索是否继续。

_Avoid_: Disposition, exit code, stderr text

**Disposition**

验证结果对 Attempt 作出的 `PASS`、`REJECTED` 或 `INDETERMINATE` 处置。资格与映射只见
[D005](docs/designs/D005-pf-failure-and-diagnose.md)。

_Avoid_: Cause, status

**Configured Verifier**

用户通过 `test-command` 配置的完整运行时验证命令；其 terminal authority 只见
[D005](docs/designs/D005-pf-failure-and-diagnose.md)。

_Avoid_: Test adapter, pytest observer

**Verifier Terminal**

配置 verifier 的可移植权威终态。具体 union 与 disposition 映射只见
[D005](docs/designs/D005-pf-failure-and-diagnose.md)。

_Avoid_: Test failure evidence, diagnostic summary

**Baseline Rejection**

Baseline Attempt 被确定证明不满足验证契约的终态；它没有可供搜索使用的通过锚点。

_Avoid_: Baseline failure, Baseline Indeterminate

**Baseline Indeterminate**

PF 无法为 Baseline Attempt 获得完整可靠结果的终态；它不构成兼容性结论。

_Avoid_: Baseline failure, Baseline Rejection

**Diagnosis**

对 Rejection 或 Indeterminate 的结构化解释及其可用机械事实。

_Avoid_: Disposition, evidence classification

**FailureRecord**

一次 Rejection 或 Indeterminate 的可移植记录，包含 scope、disposition、cause、stage 和脱敏机械事实。

_Avoid_: Run log, error string

**Cell Failure Scope**

候选发现或调度在 Attempt 建立前失败时使用的 package/cell/snapshot/policy scope；它只能形成 Indeterminate。

_Avoid_: Failed Attempt, Rejection

**Verification Run**

一次 smoke、check 或 search 对所选包、当前快照和策略执行的完整验证。

_Avoid_: CLI session, report generation

**Verification Role**

同一次 Attempt request 在某次 Verification Run 中承担的产品角色；它决定编排、产品影响和诊断文案，但不改变 adapter/FailurePolicy 分类或 identity。

_Avoid_: requested_resolution, Attempt kind

**Verification Journal**

一次 Verification Run 写入本机的 FailureRecord 记录；它不是 floor 报告。

_Avoid_: package-floor.json, Diagnosis Index

**Process Log**

一次外部进程的本机脱敏记录，包含终态和 stdout/stderr；它不进入公共报告。

_Avoid_: report log, ProcessResult output

**Output Cache**

当前 CLI 进程内对单个 Process Log 正文的有界内存投影；未覆盖全文不等于进程证据不完整。

_Avoid_: capture limit, cross-process budget

**Process Observation**

外部进程的显式终态 union；结构、完整性与持久化规则只见
[D007](docs/designs/D007-pf-process-output.md)。

_Avoid_: head/tail, summary

**Diagnosis Index**

项目本地 `.pf/logs` 中把 FailureRecord 关联到相对 Process Log 路径的非证据索引。

_Avoid_: Report field, run ID

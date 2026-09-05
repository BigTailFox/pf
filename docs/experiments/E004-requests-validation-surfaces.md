# E004 — requests required-surface 修复后验证

- **状态：** 已完成（保存空组策略补充前的运行事实与补充后的 planning 复核）
- **日期：** 2026-09-05
- **性质：** 非规范性 dogfood 运行事实；不定义产品或实现契约
- **实现依据：** [D028](../archived/designs/D028-pf-validation-contract-surfaces.md) / [P034](../archived/plans/P034-pf-validation-contract-surfaces.md)
- **历史对照：** [E003](E003-requests-dependency-validation.md)
- **目标：** `experiments/requests`，commit `dae7ef63b4df6eded86637f251fc4e3a06c3b479`
- **PF：** `0.1.0`，基线 `3f79782` 上的 D028 工作树（尚未提交）
- **Source snapshot：** `75b5d4f7dd959f3f54a5fea2bb46aa8937d81fcdcd4d3f496040ce91bb6329b3`
- **三命令运行时 Evaluation policy：** `91eb0a1d5088579f747162ce604da5c2e28bbb9ab4968b2c490f851ff9cc6823`

## 1. 输入与口径

目标 clone 的当前目录为 `experiments/requests`，E003 的历史目录拼写为 `expirements/requests`。
目标 commit、配置和 source snapshot 与 E003 相同；原 `[tool.pf]` 显式 `test-command = ["pytest"]`、
`resolve-artifact = "any"` 保留。运行完整 repository pytest contract，不删除测试或缩小 nodeid 范围。

所有有效运行在目标目录执行，使用 PF 环境中的 uv、ty 与 pf：

```bash
UV_CACHE_DIR=/tmp/pf-uv-cache /home/llh/pf/.venv/bin/uv run --no-sync --project /home/llh/pf pf smoke
UV_CACHE_DIR=/tmp/pf-uv-cache /home/llh/pf/.venv/bin/uv run --no-sync --project /home/llh/pf pf check
UV_CACHE_DIR=/tmp/pf-uv-cache /home/llh/pf/.venv/bin/uv run --no-sync --project /home/llh/pf pf search
```

三条命令按顺序运行，有包源网络访问。运行日志由目标 `.pf/logs/<run-id>/` 保存；journal 的 entries
是失败证据，不把 entries 数当作所有已执行 Cell 数。成功数同时核对命令终态和实际 verifier 进程。

## 2. 三命令运行时的 required surface

空组策略补充前启动的三条实际命令均规划 CPython 3.10–3.14 × 一个 `x86_64-unknown-linux-gnu` target × 三个 surface：

```text
[socks]
[security, socks]
[socks, use_chardet_on_py3]
```

合计 15 Cells、6 个受管直接依赖，全部包含 `socks`。Loader 的 external harness declarations 为 7 条，
`requests[socks]` 已被吸收，SourcePlan 不再有 target requests 的假 harness route。`PySocks` 属于全部
Cell 的 project graph。新的 evaluation policy 绑定 normalization facts；不能与 E003 旧 policy 的证据混用。

## 3. 有效运行结果

| 命令 | run-id | 退出码 | 已核对终态 |
| --- | --- | --- | --- |
| smoke | `20260905T063558.981602Z-132551-456b7f88` | 4 | 12 PASS；3 个 py3.11 Cell 在 inspect-environment-plan 为 INTERNAL_INVARIANT / INDETERMINATE |
| check | `20260905T063738.393187Z-142006-4c8e5bf4` | 1 | 12 个 declaration 为 VERIFIER_EXITED_NONZERO / REJECTED；3 个 py3.11 declaration-capture 为 INTERNAL_INVARIANT / INDETERMINATE |
| search | `20260905T063827.102454Z-148781-2027f407` | 4 | 12 个 runtime-search Cell 为 BUILD_FAILURE / INDETERMINATE；3 个 py3.11 baseline 为 INTERNAL_INVARIANT / INDETERMINATE |

smoke 的 12 个 PASS 分布于 Python 3.10/3.12/3.13/3.14 的三个 surface，均越过 project/environment
resolution、installation、ty 和完整 configured pytest。Python 3.11 的三个失败具有 project 与 environment
plan digest，失败发生在最终安装图复证；它们不再是 E003 的 `resolution-plan-invalid`，也不构成 Rejection。

check 的 12 个 Rejection 均来自实际 configured verifier `NormalExit(1)`；不据此推断单个依赖根因。
代表 Failure ID 为 `failure-45c5b98bfbdbd841`（Python 3.10、security+socks）。Python 3.11 未进入 declaration
验证。check 退出 1 是其现行聚合规则，不表示全部 15 Cells 都已取得负向验证事实。

search 最终报告经 `ReportStore.read` 完整验证，generation 为
`709084acd89c29abe07736fd70f68a4d1031f0a8b6c6f8df1f47b0e14e5609ce`，source snapshot 与运行时
policy 均与本文页首一致。15 个 Cell 的终态为 12 `CELL_INDETERMINATE` / 3 `BASELINE_INDETERMINATE`；
result 为 `incomplete`，reasons 为 `INDETERMINATE`、`UNREPRESENTABLE_PROJECTION`。


12 个非 py3.11 Cell 都有完整 PASS baseline，并进入实际候选搜索，最终在 `idna=0.2` 的
`resolve-project` 构建阶段终止；构建 stderr 为 `Sorry, Python 3 not yet supported`。代表 Failure ID 为
`failure-8aa1ce1fd3ab233c`（Python 3.10、socks+use_chardet_on_py3）。py3.11 的代表 Failure ID 为
`failure-23c8d4144e5fd34d`（socks、inspect-environment-plan）。

报告保存 125 Attempts、110 evaluations（65 PASS、36 RUNTIME_INTERFACE_MISSING、9 VERIFIER_REJECTED），
以及 60 FailureRecords（36 runtime witness、9 verifier nonzero、12 build failure、3 internal invariant）；
journal 也有 60 entries，运行留下 932 份 process logs。原 `resolution-plan-invalid` 未再出现。
这证明修复后的执行确实越过 environment resolution 并进入 ty/configured pytest；这些计数不是成功 Cell 数。

六个受管依赖的 projection 全为 `representable=false, floors=[]`。没有 final success roots、verified floor
或可发布 predecessor，也没有 apply 授权。日志中的 certifi/chardet/charset-normalizer 候选只表示搜索过程，
不能当成最终最低版本。

## 4. 实施中取得的辅助事实

- 沙箱 smoke `20260905T062957.688120Z-2-8d93e5b3` 规划了 15 Cells，但包源网络失败；不当作联网验证。
- 联网 smoke `20260905T063318.637429Z-116700-c9d36da9` 越过旧 environment 投影缺口并安装成功，随后
  在 EnvironmentIdentity 唯一性检查崩溃。Process logs 显示 importlib.metadata 对 requests 返回两份
  相同 name/version/dependency graph。Adapter 已归并相同节点并拒绝冲突观测，public regression 已证明。
- 直接以绝对路径运行 `.venv/bin/pf` 的辅助 smoke/check 没有把 `ty` 加入 PATH，得到工具启动失败。
  有效实验改用上述 `uv run` 命令；这些辅助运行不代替 §3 的产品结果。

## 5. 结论边界

自引用已经成为真实 effective Cell surface，E003 的 target-as-harness 投影缺口已消失。Python 3.11 的
installed-graph mismatch 是后续独立观察到的未完成测量，不声称已定位根因。12 个搜索期构建失败也不构成
Rejection。此次没有得到 floor，不能将候选观测追认为完整兼容性结论。


## 6. 用户补充：自动跳过空 extra group

上述完整 search 运行期间，用户要求默认跳过空 extra group，并明确修复后不重复完整 search。
最终实现以声明 dependency array 是否非空过滤自动 `each/all` 探索；显式 custom surface 与 required extras
仍保留，marker 不活跃不算空组。三条既有命令的输入与结果保持 §2–3 原貌，不当作最终策略的完整实验。

最终代码通过实际 `ProjectLoader(pythons=UvAdapter(SubprocessRunner())).load(...)` 重新规划同一 requests
目录，仅调用 Python discovery，未创建验证 Attempt。结果为 CPython 3.10–3.14 × 两个 surfaces：

```text
[socks]
[socks, use_chardet_on_py3]
```

合计 10 Cells，自动跳过 `security = []`；7 个 external harness declarations、所有 Cell 的 PySocks
active declarations、无 requests source route 均已核对。最终 evaluation policy 为
`835b6acede321fcdd443cd0323a67e304c3abb51b7be039d0fb3d27cd68ba76a`，绑定新增固定
`extra_exploration = nonempty-declared-groups-only`。旧运行报告仍可离线读取，但不获得当前 policy 的 apply authority。

最终行为的证据是 public planning/configuration/policy tests 与此次 live planning；未再运行完整 search，
也不声称这 10 个 Cell 已取得新策略下的 smoke/check/search 终态。

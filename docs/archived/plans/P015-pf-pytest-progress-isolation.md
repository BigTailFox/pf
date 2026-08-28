# P015 — 嵌套 pytest 进度隔离

- **状态：** 已归档（已完成）
- **日期：** 2026-08-27
- **pytest witness：** [D013](../../designs/D013-pf-pytest-observer.md)
- **进程边界：** [D002](../../designs/D002-pf-implementation.md)

## 1. 问题与目标

direct serial pytest 的外层 progress snapshot 会在被测套件内部再次调用
`TestAdapter` 时停止。内层 adapter 未请求 progress，只从自己的 environment
overlay 删除 `PF_PYTEST_PROGRESS_DIR`；生产 `SubprocessRunner` 随后从父进程
environment 开始合并，导致内层 pytest 仍继承外层目录，并用内层 nonce 覆盖
外层 snapshot。外层 monitor 正确 fail closed，因此 CLI 冻结最后合法值，直到
Cell 完成时移除 live task。

本计划只修复 pytest 私有进程 environment 的 invocation 隔离。nonce 校验、
monitor 永久关闭、最后合法值冻结、20 Hz Rich refresh、TestOutcome 与报告契约
均保持不变。

## 2. Interface 与责任

`ProcessSpec` 增加 `environment_removals: tuple[str, ...] = ()`。它表达相对父进程
environment 的删除集合；`SubprocessRunner` 先删除这些名字，再应用
`environment` overlay。进程 environment 的物化仍完全封装在 ProcessRunner
module 内，调用方不修改全局 `os.environ`。

`TestAdapter` 拥有 direct-pytest 私有 profile，因此每次 direct pytest invocation
声明移除继承的 witness、nonce、progress 与 failure-detail 变量，再由当前
invocation 的 overlay 注入新的 witness/nonce 和可用的 UI telemetry 目录。Plugin
与 monitor 不学习嵌套调用，也不接受错误 nonce 后恢复。

## 3. 纵向 TDD 切面

1. **Process environment RED→GREEN：** 通过真实 `SubprocessRunner` 证明继承变量
   可以被删除，且同名显式 overlay 在删除后生效；实现 `ProcessSpec` 与生产 runner
   的最小 interface。
2. **Nested pytest RED→GREEN：** 通过公开 `TestAdapter.run` 启动两项外层 pytest，
   第二项内部再启动一个不请求 progress 的 pytest；修复前最终停在 `1/2`，修复后
   必须收到 `2/2` 且两层 outcome 均为 `TestPass`。
3. **Raw plugin launcher RED→GREEN：** full-suite probe 若发现绕过 TestAdapter 的
   pytest plugin launcher，先以其公开 qualification 路径锁定污染，再让 progress
   activation 与当前 witness nonce 显式配对；不得逐个调用点打补丁。
4. **契约与资格验证：** 更新 D002/D013，运行 process、pytest progress/witness、
   evaluation、terminal 聚焦测试，再运行 Ruff、ty、全量 pytest、build 与真实
   full-suite adapter progress probe。最后审计 staged diff 后提交本次文件。

## 4. 实施记录

- **诊断基线：** 完整 1261 项 adapter 路径在 6.416 秒停于 `389/1261`，pytest
  继续到 19.543 秒；两项最小复现在 1.317 秒内均通过但 progress 只到 `1/2`。
- **失效证据：** monitor 读取到 canonical `pf-pytest-progress-v1`，但文件中的
  run nonce 与外层 expected nonce 不同，且 `total=1` 来自内层 pytest。单变量
  清除继承目录后事件从 `[(1, 2)]` 恢复为 `[(1, 2), (2, 2)]`。
- **计划结论：** 修复 seam 位于 `ProcessSpec`/`SubprocessRunner` environment
  interface 与 `TestAdapter` 私有变量声明；不修改 plugin 计数或接收端校验。
- **Slice 1 RED：**
  `.venv/bin/pytest --no-testmon -q tests/test_process.py::TestSubprocessRunner::test_subprocess_runner_applies_environment_removals_before_overrides`
  失败；`ProcessSpec.environment_removals` 被 Pydantic 以 `extra_forbidden` 拒绝。
  该 RED 精确证明生产 process interface 尚不能表达“删除继承变量后再应用显式
  override”。
- **Slice 1 GREEN：** `ProcessSpec` 新增默认空的 `environment_removals`；生产
  `SubprocessRunner` 从父 environment 复制后先执行 removal，再应用显式 overlay。
  同一命令通过（`1 passed`），同时证明被删除名字可由当前 invocation 的 overlay
  重新赋值。
- **Slice 2 RED：**
  `.venv/bin/pytest --no-testmon -vv tests/test_pytest_witness_integration.py::TestPytestWitnessIntegration::test_test_adapter_progress_reaches_completion_across_nested_pytest`
  中两层 pytest 都通过，但外层最后值稳定为 `StageProgress(1, 2)`，未达到预期
  `StageProgress(2, 2)`。该测试通过公开 `TestAdapter.run` 复现真实污染链，没有
  mock plugin、monitor 或 process runner。
- **Slice 2 GREEN：** direct-pytest profile 对正常 injected run 与 bootstrap fallback
  都声明移除全部 pytest 私有 environment 名字；当前 invocation 的 overlay 再注入
  自己的 witness、nonce 及可用 UI 目录。相同嵌套集成命令通过（`1 passed`），最终
  收到 `StageProgress(2, 2)`；monitor 的 nonce fail-closed 代码未修改。
- **资格偏差 / Slice 3 输入：** 沙箱 full-suite adapter probe 已到达
  `1263/1263`，但联网重跑虽获得 `TestPass`，progress 停于 `500/1263`。第 501 项
  qualification case 直接用 `subprocess.run` 注入 standalone plugin，覆盖 witness
  nonce 但继承外层 progress 目录；这证明只修 `TestAdapter` 调用点不够深。新增
  Slice 3，以“只有当前 witness nonce 显式启用的 invocation 才能写 progress”收口
  plugin interface。
- **Slice 3 RED：**
  `.venv/bin/pytest --no-testmon -vv tests/test_pytest_witness_integration.py::TestPytestWitnessIntegration::test_test_adapter_progress_reaches_completion_across_qualification_pytest`
  中 qualification 的两项外层测试与 raw inner pytest 都通过，但合法外层值只停在
  `StageProgress(0, 2)`。第一次尝试因禁用 plugin autoload 命中 required-plugin
  bootstrap failure，不是目标症状；启用真实 repo plugin 后才取得上述正确 RED。
- **Slice 3 GREEN：** direct-pytest progress 增加 invocation-local
  `PF_PYTEST_PROGRESS_NONCE` activation；adapter 只在建立 monitor/目录时把它设为
  当前 witness nonce，standalone plugin 在 collection 与每次 commit 都要求两者匹配。
  qualification raw inner pytest 虽继承外层 progress 目录/token，但其新 witness nonce
  不匹配，因此不能覆盖外层 snapshot。相同集成命令通过（`1 passed`），plugin hook
  回归 `41 passed`。
- **聚焦验证：** process、plugin、progress monitor、witness integration/protocol/
  qualification、TestAdapter、schema、evaluation 与 terminal 共 `515 passed`；Ruff
  rules、ty 与 `git diff --check` 通过。
- **全量验证：** 默认只读 uv cache 的首次全量为 `1260 passed, 3 failed`；改用可写
  cache 后为 `1262 passed, 1 failed`，唯一失败的 Process Log 是沙箱拒绝从外部索引
  获取 `uv_build`。联网且显式使用官方 PyPI 的该 installed lifecycle 用例通过
  （`1 passed`）。最终联网 full-suite `TestAdapter` probe 为 `TestPass`，最后 progress
  为 `1264/1264`，同时覆盖普通嵌套 TestAdapter 与 raw qualification plugin launcher。
- **构建验证：** 官方 PyPI 环境下 `uv build` 成功生成 sdist 与 wheel。
- **格式偏差：** `ruff format --check` 仍会建议重排 touched legacy files 中与本修复
  无关的既有行；本次新增行已按 formatter 建议调整，未扩大提交做机械全文件格式化。
- **最终结论：** producer 只在当前 invocation 显式授权时写 progress，process module
  能精确删除继承 environment，consumer 的 bounded/nonce/freeze 契约保持不变。
  原始“386/389 后冻结直到瞬间完成”路径和后续 `500/1263` qualification 变体均已
  被提交级回归覆盖。

后续每个 RED/GREEN、验证结果、偏差与最终结论继续追加到本节。

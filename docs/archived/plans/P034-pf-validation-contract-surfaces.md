# P034 — PF validation contract surfaces 实施计划

- **状态：** 已完成并归档
- **日期：** 2026-09-05
- **依据：** [D028](../designs/D028-pf-validation-contract-surfaces.md)，用户已授权实现修订后的 Design
- **基线：** `3f79782`；开始时已有 D028 与 `docs/README.md` 导航改动

## 1. 目标与顺序

完整实现 D028；不增加兼容层，不改变搜索算法或 configured verifier authority。所有生产修改开始前
建立本 Plan。实现、验证、owner 归并与逐项审计完成后，D028/P034 与 R009 同步归档。

| 切片 | 接口与实现迁移 | 测试与证据槽 | 状态 |
| --- | --- | --- | --- |
| S1 | ProjectLoader 单次解析 group，内部自引用资格，按 target/Python 合成 effective Cells；不扩大 PackagePlan | public planning、marker、source、version、surface、workflow admission；补充自动跳过空 extra | 完成 |
| S2 | HarnessSelection → HarnessSatisfaction；baseline observations、graph ownership、角色适用与 ceiling；resolution/Attempt digest | harness、UvAdapter、EnvironmentFactory 正反向契约与 ownership 互换 | 完成 |
| S3 | ConfigLoader/schema 默认 any/pytest；删除缺 command 准入；policy.py 固定 normalization facts | config 分层与 policy identity，command/group admission | 完成 |
| S4 | report/apply closure 与 Failure/search regression；检查生成 Schema/examples | merge/update/update_path/apply/force；Schema round-trip、完整 regression | 完成 |
| S5 | README/README.zh、D001/D002/D005/D012/D014 吸收；D003/D004 核对；requests 新实验与归档索引 | dogfood、完整验证、链接和逐项验收 | 完成 |

## 2. 验收映射

| D028 AC | 切片与证明对象 | 证据 / 结论 |
| --- | --- | --- |
| 1 | S1 canonical self-reference / root-member-include / route 排除 | 通过：test_validation_surfaces loader union/root-member/include；requests live planning 7 external declarations |
| 2 | S1 extra 规范唯一匹配与 union、未知/歧义配置错误 | 通过：unknown/ambiguous extra、canonical alias、required-only union 参数化测试 |
| 3 | S1 三类 marker 变量与跨平台/Python 活跃性 | 通过：Linux/macOS/Windows 与 Python active/inactive；11 类 unsupported marker 测试 |
| 4 | S1 static/dynamic version 与 source 资格、Python discovery 时序 | 通过：版本/source 资格测试；public CheckCommandWorkflow 证明 discovery 后错误且未调用 snapshot/runner |
| 5 | S1 none/each/all/custom、去重、active declarations、Cell identity | 通过：none/each/all/custom、空组自动跳过/显式保留、inactive marker、active PySocks IDs 与 Cell IDs |
| 6 | S1/S5 requests 5 × 2 Cells / socks / 无自身 harness route | 通过：补充前实验为 15 Cells；最终 public planning/live Loader 复核 10 Cells、7 external declarations、全部 PySocks active、无 target route |
| 7 | S2 graph exact 与唯一 satisfaction owner | 通过：真实 UvAdapter recorded pylock overlap 与 EnvironmentFactory ownership 双向切换；唯一节点/exact |
| 8 | S2 五种角色、fixed/transitive 保留、双向 ownership 互换 | 通过：harness fixed/marker/lower/ceiling tests；Highest/Lowest/Exact environment 与 product suites |
| 9 | S2/S4 certified UNSAT → HARNESS_CONFLICT/REJECTED，投影失败 Indeterminate | 通过：test_environment_maps_certified_harness_unsat_before_installation、adapter 非法投影、search regression |
| 10 | S4 D003/D004 不变与 search/static regression | 通过：search/static/witness 算法实现无 diff，三个 Python 全套覆盖；状态机未扩展 |
| 11 | S3 artifact 默认与显式值、所有 verification commands | 通过：config 分层默认/override、candidate artifact 参数化、shared artifact environment tests |
| 12 | S3 command 默认/替换与 group existence；无自动发现 | 通过：config/Check 默认 command 正向测试、缺 group 准入；schema 非空 argv 与无 shell |
| 13 | S3/S4 固定 policy facts、显式默认值反例、merge/update/apply/force 隔离 | 通过：test_normalization_policy_isolates_reports_with_explicit_defaults、required-surface round-trip/authorization、policy preimage |
| 14 | S1–S4 public seam 测试矩阵与完整验证 | 通过：最终 Python 3.10/3.11/3.12 各 1613 passed；coverage 90.26%；补充 focused 207 passed |
| 15 | S5 owners/README、归档 R009/D028/P034、保留 E003 原文 | 通过：owner/README 与示例更新；E004 保存三命令终态及最终 planning；R009/D028/P034 已归档，链接已审计 |

## 3. 验证计划

- 每个切片先运行受影响 public seam 的 focused pytest（`--no-testmon`），再运行 Ruff/ty。
- 最终依次运行 Python 3.10/3.11/3.12 全套，避免并行 suite 干扰 observer qualification。
- 完整检查包含 coverage 门禁、build、生成 Schema/examples 检查、Markdown 链接与 `git diff --check`。
- 使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`；PyPI/build 网络失败与代码断言失败分开记录。
- requests 在 `expirements/requests` 中按 E003 §7 依次运行 smoke/check/search，保留完整 pytest contract。
  新实验另建 E004，记录命令、run-id、snapshot/policy、15 Cells、越过 environment 的情况及实际终态；
  即使未取得 floor 也如实记录原因，不回写 E003。

## 4. 行动、决策与证据

- 2026-09-05：读取当前工作树与 D028；用户的实现请求接受修订目标。建立 P034 后开始生产修改。
  现行实现仍为 HarnessSelection、默认 wheel/缺 command、自引用 direct harness；本次全部替换。
- S1–S4 第一轮实现已完成：ProjectLoader 归一化 self-reference，HarnessSatisfaction/observations 与
  current graph ceiling，默认 any/pytest 和固定 validation_contract_policy。测试经 public seams 迁移。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_validation_surfaces.py tests/test_project.py tests/test_harness.py tests/test_config.py tests/test_environment.py tests/test_uv_adapter.py tests/test_verification.py tests/test_check.py`：326 passed。
- 后续补充 ownership 互换与 digest 后，`tests/test_harness.py tests/test_environment.py`：57 passed。
  report/apply policy 隔离与 required-surface round-trip 加入后，`tests/test_authorization.py`：38 passed。
- `tests/test_validation_surfaces.py tests/test_check.py tests/test_candidates.py tests/test_search_workflow.py tests/test_report_artifacts.py`：84 passed。
- 初次全仓 pytest 收集了 ignored 的 requests clone，因未安装其依赖中止；PF 全套改为显式 `tests`。
  首次 `tests` suite：1594 passed / 4 failed；其中旧 artifact/default command 断言和 generated examples
  已更新，真实安装用例因沙箱网络失败，联网单独重跑 `tests/test_end_to_end.py`：1 passed。
- `ty check` 暴露 3 个基线已有测试诊断（CLI monkeypatch 方法赋值、process Popen 转发签名），做了
  最小测试 typing 修复；当前 `.venv/bin/ty check` 与 `.venv/bin/ruff check src tests` 均通过。
  `scripts/generate_report_schema.py` 已重生成两个示例，Schema 文件未变化。
- requests clone 实际位置为 `experiments/requests`，commit 仍为 E003 的 `dae7ef63`；保留 E003 原文，
  后续 E004 记录目录拼写差异。首次沙箱 smoke（run `20260905T062957.688120Z-2-8d93e5b3`）确认
  15 Cells 后以 15 SOURCE_FAILURE 退出 4；联网 smoke 已越过原有 environment 投影并完成安装。
- Dogfood 发现执行闭包内的既有缺口：联网 run `20260905T063318.637429Z-116700-c9d36da9` 的安装
  metadata 返回两份相同 requests 节点，EnvironmentIdentity 唯一性验证崩溃。原始 process logs 直接
  证明重复观测；使用 `tests/test_uv_adapter.py -k 'canonical_installed_graph or graph_inspection_rejects'`
  构建最小反馈：修复前 3 failed / 5 passed，修复后 8 passed。Adapter 归并相同 canonical graph node，
  对同名不同版本/依赖图返回 ToolFailure；不放宽 graph 唯一性或 project exact 不变量。
- Owner/README 已接收目标规则；三版本/coverage/requests 终态见下列最终记录。

## 5. 空组补充前的验证记录

| 命令 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short --cov=pf --cov-report=term-missing tests` | Python 3.10：1606 passed，34.88s；90.26% coverage，达到 90% 门禁 |
| `env UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.11 --group test pytest --no-testmon -q --tb=short tests` | 1606 passed，29.98s |
| `env UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.12 --group test pytest --no-testmon -q --tb=short tests` | 1606 passed，31.01s |
| `.venv/bin/ty check` / `.venv/bin/ruff check src tests` | 全部通过 |
| `.venv/bin/python scripts/generate_report_schema.py --check` | 通过，Schema 1 未扩形，两个示例 identity 已重生成 |
| `env UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` | 成功生成 wheel 与 sdist |
| `git diff --check` + 本次修改 Markdown 本地链接/code fences 检查 | 通过；归档后再核对最终链接 |

上述三版本依次执行；联网安装/构建已实际验证。随后仅补强两个既有测试：public workflow admission
（42 个 surface tests 通过）与真实 UvAdapter overlap（76 个 adapter tests 通过），未改变生产行为。
清理了 13 个文件的纯格式 diff，并以 AST 相等复证；未削减功能测试或产品逻辑。

Requests 有效运行命令、run-id、snapshot、policy、失败分布与范围见 [E004](../../experiments/E004-requests-validation-surfaces.md)。
Smoke 为 12 PASS / 3 py3.11 installed-graph Indeterminate；check 为 12 verifier Rejection / 3 capture
Indeterminate。Search 已退出 4，report reader 验证通过，12 runtime-search build Indeterminate / 3 baseline
Indeterminate，6 projections 无 floor。E004 保存 generation、全部计数与代表 Failure ID；不声称全矩阵 PASS
或完整 floor。这些三命令结果来自空组补充前的 15-Cell 策略，不追认为最终 10-Cell 实测。

## 6. 实施中补充：自动跳过空 extra group

- 用户要求 extra 策略默认跳过空组，修复后不重复完整 search 实验；先更新 D028/P034，再修改生产代码。
- 自动 `each/all` 只展开声明 dependency array 非空的 extra；`none` 仍保留 required base；显式 custom
  与 self-reference required 空组保留。Marker 不活跃不等于声明空组。继续使用同一 ProjectLoader seam。
- 顺序：public planning 回归 → 单处 surface 生成修改与固定 policy fact → D001/D002/D014/README
  和生成示例 → focused tests/ty/Ruff/schema → requests live planning 10 Cells → 验收归档。
- AC5/6/13/14/15 追加上述证据；已启动的 15-Cell search 已正常结束并保存为补充前实验，未重跑。
- 最终补充实现只在 ProjectLoader 的自动 surface 展开使用非空声明组；policy.py 增加固定
  `extra_exploration` fact，D014 与生成示例同步更新。没有新增参数、配置开关或 wire 字段。
- `env UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_validation_surfaces.py tests/test_project.py tests/test_environment.py tests/test_authorization.py`：207 passed，3.27s。
- 实际 Loader 使用 `UvAdapter(SubprocessRunner())` 完成 Python discovery：10 Cells（5 Python × 2 surfaces）、
  7 external declarations、PySocks 在全部 Cell 活跃、无 requests source route；最终 policy 为
  `835b6acede321fcdd443cd0323a67e304c3abb51b7be039d0fb3d27cd68ba76a`。未创建验证 Attempt。
- E003 仅修复 R009 归档链接，实验正文与运行事实保持不变；其两条历史 `expirements/requests` clone/artifact
  链接原已失效，保留原路径以免把历史报告指向本次覆盖后的新报告。归档链接检查单独列出这两项。


## 7. 最终验收

补充空 extra 策略后，重新依次执行 §5 的三条相同全套命令；没有重跑 requests 完整 search：

| 验证 | 最终结果 |
| --- | --- |
| Python 3.10 全套 + coverage | 1613 passed，34.19s；90.26%，满足 90% 门禁 |
| Python 3.11 全套 | 1613 passed，28.37s |
| Python 3.12 全套 | 1613 passed，30.55s |
| `env UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` | wheel 与 sdist 均成功 |
| `.venv/bin/ty check`、`.venv/bin/ruff check src tests` | 全部通过 |
| `.venv/bin/python scripts/generate_report_schema.py --check` | 通过，Schema 1 形状不变、示例已更新 |
| Markdown 本地链接/code fences 与 `git diff --check` | 通过；单列 §6 的两条原有历史 clone 路径 |

AC1–15 已逐项闭合。稳定规则由 D001/D002/D005/D012/D014 接管，README 两种语言同步，D003/D004
行为保持。R009/D028/P034 在本次完成变更中一起归档。Requests 实验的 Indeterminate 是 E004 如实
记录的验证结论；它不阻止本迁移验收，也不授权 floor/apply。最终 10-Cell 策略只取得 planning 与测试
证据，按用户要求不重复完整 search。上述验收完成时尚未创建 git commit。

## 8. 提交记录

- 用户随后授权提交；`7b45c6a` 保存实现、public seam 测试与生成示例。
- Owner 文档、E004 实验记录与 R009/D028/P034 归档作为独立文档提交一起交付。
- 提交前重新核对既有三版本测试及构建结果，并通过 staged whitespace、Markdown 链接和生成物检查；
  没有重复完整 search，未推送远端。

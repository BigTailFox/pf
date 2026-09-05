# P035 — 条件 resolution 节点投影实施计划

- **状态：** 已完成，归档
- **日期：** 2026-09-05
- **依据：** [D029](../designs/D029-pf-conditional-resolution-projection.md)
- **基线：** `8dce32e`，工作区干净；用户要求先设计再修复

## 1. 有序切片与验收映射

| 切片 | 修改与 ownership | AC | 证据槽 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | uv_lock 实际 version/target 投影，active 节点/引用闭合 | 1–2 | parser target/version/invalid/duplicate/reference tests | 完成 |
| S2 | ResolutionContext actual interpreter；EnvironmentFactory 生命周期；UvAdapter argv/constraints/harness | 3–4 | public adapter/factory tests；失败时序和资源清理 | 完成 |
| S3 | policy fixed fact、identities、examples/report/apply | 5 | context/cache identity、policy/report regression、schema check | 完成 |
| S4 | E004 原日志回放、真实 uv fixture、requests py3.11 单 Cell smoke | 6 | exact commands/run-id/结果；不重跑完整 search | 完成 |
| S5 | 三版本/coverage/build、owner 吸收、E004 追加、D029/P035 归档 | 7–8 | 最终测试与逐项验收表、链接与 whitespace | 完成 |

## 2. 实施约束

Design/Plan 在生产修改前建立；本次修复既有 active graph 不变量，不改变 build failure 或搜索契约。
所有内部接口直接替换，保留 v1，不引入 legacy aliases。遇到额外问题先核对是否属于本闭包，记录偏差。
测试通过 public parser/adapter/factory seam；真实实验只覆盖所需目标，不重跑完整 search。

## 3. 行动与证据

- 建立 D029/P035：核对当前生命周期、context/request identity、uv 参数、pylock 标准和 E004 三组日志。
- 基线证据：native pylock 条件节点没有投影；解析使用 minor，空环境与实际 patch 在解析之后才取得。
- S1/S2：实现 actual interpreter/target active graph 投影，create/inspect 提前；inactive references、严格 graph
  equality、context/request/Attempt/cache 绑定均经 public seam 检查。初轮 focused 暴露旧时序断言与测试 context
  迁移遗漏，已按目标接口修正；未放宽生产图检查。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_uv_lock.py tests/test_uv_adapter.py tests/test_environment.py tests/test_resolution.py`：213 passed（0.51s）。
- `.venv/bin/python /tmp/pf-d029-replay.py`：E004 0024/0053、0044/0080、0050/0086 全部匹配，分别32/32/33个依赖。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python /tmp/pf-d029-native-fixture.py`：真实 Python 3.11.15 + uv sync，
  两个条件 wheel 节点只有一个活跃；active graph 与实际安装包名/版本一致，native 文件未改写。
- requests 单 Cell smoke 首次沙箱 run `20260905T075728.831026Z-478-4a06b9a1` 在包源访问失败；已分开记录，
  不作修复失败或成功证据。联网 run `20260905T075749.820462Z-276394-e53849a8` 返回 HighestVersionPass，
  Python 3.11.15 的完整 pytest 为619 passed/15 skipped/1 xpassed；未重跑完整 search。
- S3：固定 policy fact 已增加，Schema/examples 已重生成；公共 Schema 未变化。
- S5：D002/D012/D014 已吸收稳定规则，E004 §10 追加原日志回放/真实 fixture/单 Cell smoke；D001 的
  生命周期只引用 D012，没有相反时序，核对后无需复制细节。
- 初次 Python 3.10 全套：1640 passed/3 failed；两项是 check lifecycle 的旧 resolve-before-create 断言，
  另一项是真实 installed CLI 的沙箱运行失败。迁移断言后联网全套通过，没有调整产品判定。
- 补充 inactive required harness 不能伪造 satisfaction 的 public adapter 用例；`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_check.py tests/test_uv_adapter.py`：90 passed（0.41s）。
- 最终 diff 去除仅由 formatter 造成的既有代码换行变化；Ruff/ty 复查通过。


## 4. 最终验证

除 build 与静态检查外，三版本全套在允许包源访问的环境执行。日志均位于 `/tmp/pf-d029-*`。

| 准确命令 | 结果 |
| --- | --- |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short --cov=pf --cov-report=term-missing tests` | 1644 passed，34.63s；coverage 90.25%，达到90%门禁 |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.11 --group test pytest --no-testmon -q --tb=short tests` | 1644 passed，28.91s |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.12 --group test pytest --no-testmon -q --tb=short tests` | 1644 passed，31.95s |
| `.venv/bin/ruff check src tests scripts` | 通过 |
| `.venv/bin/ty check` | 通过 |
| `.venv/bin/python scripts/generate_report_schema.py --check` | 通过；公共 Schema 1 未变化，两个示例已更新 |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` | wheel 与 sdist 成功 |
| `git diff --check` 与 Markdown 本地链接检查 | 通过（最终归档后的本地文件链接检查） |

## 5. 逐项验收审计

| D029 AC | 已取得证据与结论 |
| --- | --- |
| 1 | public parser tests 覆盖 actual patch、五个平台/architecture、implementation、unsupported/invalid markers；支持范围 fail closed |
| 2 | public parser tests 覆盖 inactive nodes/edges、互斥同名、active duplicate/reference/compatibility；真实 uv fixture 保留 native |
| 3 | two-pylock adapter test 检查两个 exact executable/full-version argv、无 inactive constraint、PROJECT_GRAPH/EXTERNAL_HARNESS satisfaction；缺失 active harness 仍 Indeterminate |
| 4 | public factory tests 检查 create/inspect/project/environment/install 顺序、同一 executable、Attempt identity、准备失败清理；既有 installed graph drift tests 保留 |
| 5 | 同一 factory 在 actual patch 改变后取得新 context/request/Attempt identity，相同 patch 命中缓存；policy preimage 与 report/apply 全套通过；Schema v1不扩展 |
| 6 | E004三组32/32/33个依赖回放一致；Python3.11.15真实uv fixture通过；requests py3.11+socks HighestVersionPass，未运行完整search |
| 7 | focused、Ruff、ty、三版本、coverage、build、生成物及文档检查见§4 |
| 8 | D002/D012/D014接管稳定规则，D001引用无冲突；E004追加§10；D029/P035同步归档 |

未扩展 build failure、first-probe、floor/predecessor 或 apply 契约。真实 requests 只覆盖一个当前 Cell，
不声称历史15 Cells或最终10 Cells全部重验。

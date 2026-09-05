# P037 — Adapter 证据准入与诊断边界实施计划

- **状态：** 已完成、归档；S1–S6 与 AC1–AC12 已审计，既有独立工作区限制见 §6
- **日期：** 2026-09-05
- **依据：** [D032](../designs/D032-pf-runtime-witness-stderr.md)，已接受并补齐本轮评审要求
- **基线：** `85e195c`；工作区已有 docs/README.md、D031、D032、E005、实验 data 与模拟脚本未提交工作
- **稳定 owner：** [D003](../../designs/D003-pf-search-algorithm.md)、
  [D004](../../designs/D004-pf-ty-enhancement.md)、[D013](../../designs/D013-pf-pytest-observer.md)、
  [D014](../../designs/D014-pf-report-schema.md)

本计划在生产修改前建立。用户已授权实现 D032；按本计划推进生产修改与验收，未授权提交。
历史最小差分复现只说明现有判据；所有目标行为的验收证据均须在实施后取得。

## 1. 范围与独立开放项

实施四项已接受调整：witness stderr 只作诊断、pytest summary 可选化、不适用 artifact 的
locator/哈希资格检查后置、ty 项目 terminal 默认配置由 PF 固定 argv 覆盖。
不新建 adapter 架构，不改变 verifier terminal、pruning collection 证明、scope 或 floor 权威。
直接替换目标规则，保留 Schema 1/v1 前缀；不保留旧规则开关、兼容 reader 或双协议。

**D032 §9 明确不在实施范围内。** 它已移交到
[docs/README.md：uv 成功解析的日志完整性门槛](../../README.md#uv-resolution-output-completeness)，
由该具名开放项持续跟踪，不预留另一份 Design/Plan 编号。后续研究应独立核对 D012/D007。
D032/P037 可以在不验证、不实施、也不解决 §9 的情况下完成；它不是 S1–S6 的前置依赖。
S6 只检查开放项仍存在、状态仍开放、来源链接在归档后有效，不把它标为已修复。

requests dogfood 复跑是可单独安排的外部验证，不替代本计划的测试；旧 incomplete 报告保持历史
结论。D031 搜索算法草案也不是本计划依赖，避免混入其未接受或未实施的接口变化。

## 2. 有序实施切片

按 S1 → S2 → S3 → S4 → S5 → S6 顺序推进。每个切片先补公共 seam 的目标行为测试，再实现并
运行定向验证；记录原始失败、修复和结论，不把仅通过的新测试当作全体验收。

| 切片 | 接口、所有权与迁移 | AC | 证据槽 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | `adapters/runtime_witness.py` 移除 stderr 内容门槛；保留 terminal/完整性/canonical stdout 资格，错误 status 类型返回 typed failure；RuntimeEvaluator 路由不变 | AC1–4、AC10 | E1：adapter、真实导入、日志与 evaluator 路由测试 | 已完成（见 §6） |
| S2 | `adapters/pytest_observer.py` 严格校验后可丢弃主 summary；`ConfiguredVerifier.run` 保留已知 terminal 和独立诊断；更新 mandatory reader 说明及调用方；collection 证明独立 | AC5–6、AC10 | E2：真实 pytest 加 artifact 故障注入、normal terminal 与 pruning 矩阵 | 已完成（见 §6） |
| S3 | `UvAdapter.query` 先保留 release keys、判断当前 Cell 适用性，再验证安装 locator/SHA-256；CandidateBuilder 继续拥有其余过滤与系列选择 | AC7–8、AC10 | E3：混合 registry 响应、locator 对照、完整系列与位置偏移 | 已完成（见 §6） |
| S4 | `TyAdapter.check` 接受可由固定 argv 覆盖的项目 terminal 默认值；用户 argv/config override 保护与验证 scope 不变 | AC9–10 | E4：实际 ty 进程、公共 adapter 及非法 override 测试 | 已完成（见 §6） |
| S5 | 更新 evaluation/candidate policy 事实、共享 preimage 构造与 `report.py` reader 复算；同步 D003/D004/D013/D014，检查 D002/D005/D007 引用；生成 Schema/examples | AC11，复核 AC1–10 | E5：identity/reader/生成物与 owner 对齐 | 已完成（见 §6） |
| S6 | 全套验证、逐 AC 审计；具名保留 §9；D032/P037 同步归档并修复现行/归档索引 | AC12，汇总 AC1–11 | E6：质量检查、最终 AC 审计与归档链接 | 已完成，见 §6 |

S1 的真实导入 fixture 在独立临时环境安装，通过 adapter 使用的 `-I` interpreter 执行，
显式产生确定 warning，并覆盖导入副作用异常后的 NOT_APPLICABLE。无需联网获取旧 urllib3。
S2 使用实际 pytest 子进程取得 terminal，再在测试用 process adapter seam 中对私有 artifact
作受控缺失/损坏/读取故障注入；不改退出码，不以单独 reader 单测冒充调用链证据。

## 3. AC 到测试与证据的逐项映射

AC1–AC12 已审计，行为、契约与归档证据见 §6；唯一全仓类型检查限制为未改动的 D031 模拟脚本。

| AC | 切片 / 公开 seam 与必要场景 | 证据 / 当前验收状态 |
| --- | --- | --- |
| AC1 | S1：`RuntimeWitnessAdapter.run`，三个合法 status × 空/warning/普通 stderr，结果与 process 保留 | 通过，E1 + 最终 E6a；runtime_witness/evaluation 公共 seam |
| AC2 | S1：timeout/signal/start/unavailable/nonzero/任一流不完整，以及非法、额外、多行、非 canonical stdout 和错误 status 类型，全部 typed failure | 通过，E1 + 最终 E6a；runtime_witness/evaluation 公共 seam |
| AC3 | S1：真实 witness adapter 接入 RuntimeEvaluator；PRESENT/NOT_APPLICABLE 后完整 verifier PASS/REJECTED，CONFIRMED_MISSING 短路，协议失败 Indeterminate；证据前缀保留 | 通过，E1 + 最终 E6a；runtime_witness/evaluation 公共 seam |
| AC4 | S1：本地 fixture 的真实隔离导入，warning 与副作用异常；D007 输出读取 seam 可取得未抹除的脱敏 stderr | 通过，E1 + 最终 E6a；runtime_witness/evaluation 公共 seam |
| AC5 | S2：正常 exit 0/非零 × summary 缺失、损坏、不可读、非 canonical、nonce 不符、冲突、超限；保留 terminal/process，丢弃 facts；合法 summary 对照，含真实 pytest 路径 | 通过，E2 + 最终 E6a；真实 pytest 故障与正交矩阵 |
| AC6 | S2：summary 有效性与 collection 证明有效性正交组合；failed-set 非零采用/回退、exit 0 完整命令、不定不回退；独立 additions/detail | 通过，E2 + 最终 E6a；真实 pytest 故障与正交矩阵 |
| AC7 | S3：有效候选加不适用文件的空 hashes/SHA-512；另用有效 SHA-256、非空但非法安装 locator 做成功对照；适用文件同类缺陷仍失败，URL 非字符串等结构错误仍失败 | 通过，E3 + 最终 E6a；真实 query 与候选构造 |
| AC8 | S3：真实 query → CandidateBuilder → search-space；跳过 artifact 的版本仍保留 release/series 观测，偏移不补位，代表与 floor 资格保持 | 通过，E3 + 最终 E6a；真实 query 与候选构造 |
| AC9 | S4：实际 ty 在项目 output-format 同值与不同值默认设置下输出 GitLab JSON，非法 terminal.color 由 ty 报错；公共 adapter 保留诊断，显式 owned override/config-file 仍拒绝 | 通过，E4 + 最终 E6a；真实 ty 子进程 |
| AC10 | S1–4：各路径自身权威、错误/安全边界；复查 uv 普通非零/source/build 不升级 Rejection，§9 分支未改 | 通过，E1–4 + 最终 E6a；uv resolution-output-incomplete 分支 diff 未改 |
| AC11 | S5：D014 §1.2.3 精确 preimage、生产构造、reader 复算与生成物一致；v1、无第二 reader；删除锁定旧门槛的测试，保留当前错误语义 | 通过，E5 + 最终 E6a；精确 preimage、reader tamper 与 roundtrip |
| AC12 | S6：每项直接证据、四个稳定 owner 吸收、Design/Plan 状态一致且同步归档；README 独立开放项仍开放 | 通过，E6；三版本全套、owner 归并与联合归档 |

AC7 的 locator 用例至少包含：当前 Cell 为 Linux/Python 3.10；额外 wheel 的 Python tag 与
Requires-Python 均满足 3.10，只有 platform 为 Windows，URL 为 `file:///demo.whl`、SHA-256
合法。query 应返回原有有效候选；将同类文件变为适用平台后必须失败。这样仅后移 hash 或仅
依赖 Requires-Python 的早过滤无法通过验收。另覆盖 Requires-Python 不适用时的跳过场景。
URL 字段类型/非空性仍属于必要响应结构；这些错误不因平台不适用而放行。

## 4. Identity、owner 与生成物工作

| 稳定 owner | 必须吸收的规则 / 实施触点 |
| --- | --- |
| D003 | query 的 release/适用性/安装证据顺序；locator 与 SHA-256 都在适用性之后；完整系列位置保持 |
| D004 | witness stderr 诊断通道、typed 协议失败；ty 默认展示配置覆盖；§11 的新策略事实及 v1 身份隔离说明 |
| D013 | summary 从 mandatory 改为可选；坏 artifact 严格丢弃；summary 不补 collection 证明、不改 terminal |
| D014 §1.2.3 | 精确 candidate policy preimage 增加 `artifact_admission = cell-eligibility-before-sha256`；writer/reader 完整复算规则同步 |

`src/pf/policy.py` 的 TY_DIAGNOSTIC_POLICY 增加 D032 指定的 `witness_stderr`、
`project_terminal` 事实；`src/pf/candidates.py` 构造 candidate policy digest，
`src/pf/report.py` 的 reader 使用共享构造复算，不建立第二套 preimage 实现。
D014 精确 preimage 归并后仍是唯一规范性所有者；D003 只拥有选择语义并引用它。
pytest summary 不加入 evaluation policy identity；所有字段与 prefix 遵循 D032 的唯一目标。

S5 运行 `scripts/generate_report_schema.py`，核对 `docs/schemas/package-floor-v1.schema.json`
和 `docs/examples/package-floor-v1-minimal-{complete,incomplete}.json`；保存真实差异，无变化也记录。
使用实际报告 roundtrip/tamper/merge/apply 测试验证 identity 与 wire，不能只做静态字符串检查。
核对 D002 的 seam 说明、D005 terminal authority 和 D007 日志/完整性引用是否仍准确；
只在需要对齐本次目标时修改关联文档，不为 §9 提前改 D012/D007。

## 5. 计划验证命令

以下保留实施前固定的验证命令；实施结果、补充检查与唯一独立工作区限制见 §6，E0 为最初文档检查。
PF/pytest/uv 命令按 AGENTS.md 在 `/home/llh/pf`、沙箱外执行，
先检查风险；使用独立 uv cache，记录环境失败与代码失败。各 Python 全套串行，避免共享测试
缓存竞争。切片内先运行对应集合；最终固定代码与生成物后执行全套，不因历史通过而省略。

| 槽 | 准确命令 |
| --- | --- |
| E1 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_runtime_witness.py tests/test_evaluation.py tests/test_check.py tests/test_search_coordinator.py` |
| E2 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_configured_verifier.py tests/test_pytest_observer_protocol.py tests/test_pytest_observer_integration.py tests/test_pytest_observer_plugin.py tests/test_pytest_pruning.py tests/test_pytest_progress.py tests/test_pytest_observer_qualification.py` |
| E3 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_uv_adapter.py tests/test_candidates.py tests/test_search_space.py tests/test_search_space_report.py` |
| E4 | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_ty_adapter.py tests/test_evaluation.py` |
| E5a | `.venv/bin/python scripts/generate_report_schema.py` |
| E5b | `.venv/bin/python scripts/generate_report_schema.py --check` |
| E5c | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_search_space_report.py tests/test_report_schema.py tests/test_report_artifacts.py tests/test_report_workflows.py tests/test_authorization.py` |
| E6a | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short --cov=pf --cov-report=term-missing tests` |
| E6b | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.11 --group test pytest --no-testmon -q --tb=short tests` |
| E6c | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.12 --group test pytest --no-testmon -q --tb=short tests` |
| E6d | `.venv/bin/ruff check src tests scripts` |
| E6e | `.venv/bin/ty check` |
| E6f | `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` |
| E6g | `git diff --check`，以及归档后 Design/Plan/owner/索引的本地链接与 anchor 检查 |

运行前核对 `.venv` 的实际 Python minor；E6a 预期覆盖当前 3.10，若环境改变则记录并补齐缺少的
受支持 Python。coverage 记录实际结果与未覆盖区域，不把输出 coverage 报告等同于通过某一阈值。
E2 保留当前 observer/pruning 资格测试；若实施改变插件资源或注入语义，须补充相应资格脚本矩阵，
将实际参数、输出位置与结果追加到本计划，不能用旧 manifest 冒充新验证。

## 6. 行动、决定与证据记录

| 记录 | 行动 / 结论 | 验证状态 |
| --- | --- | --- |
| E0 | 2026-09-05，复核 HEAD/worktree、D032、D014 §1.2.3、候选 reader 与测试/生成命令；D014 提升稳定 owner，补 AC7 平台不匹配 locator 正例，§9 移交 README 具名开放项；建立 P037 | 文档检查通过；不属于产品验收 |
| E1 | S1 实施、红/绿测试与真实导入/路由 | 已完成，见下方实施记录 |
| E2 | S2 summary 丢弃与 collection 证明独立性 | 已完成，见下方实施记录 |
| E3 | S3 locator/hash 适用性与 release/series 观测 | 已完成，见下方实施记录 |
| E4 | S4 固定 ty argv 与项目默认配置 | 已完成，见下方实施记录 |
| E5 | S5 policy/reader/owner/生成物 | 已完成，见下方实施记录 |
| E6 | S6 全套验证、逐 AC 审计、归档与独立开放项保留 | 已完成，见 §6 |

E0 验证（仓库根目录）：`git diff --check` 返回 exit 0；`python3 -` 内联文档检查返回 exit 0，
确认 D032/P037 的 17 个本地链接及 anchor 有效、AC1–AC12 连续且逐项映射、计划引用的测试/脚本
路径存在，以及 README 具名 anchor 与 P037 导航存在。人工核对稳定 owner 包含 D014 §1.2.3、
AC7 的平台专属 locator 对照、§9 不阻塞条款。未执行 E1–E6 产品命令，未改生产代码或测试。

每次实施追加命令、退出码、计数、失败原因、修正、偏差及剩余问题；不得只改状态为完成。
最终回填 §3 的逐项验收状态，明确所有未获得的行为证据。只有 AC1–AC12 完整闭合后，才将
D032/P037 在同一完成变更中移入 `docs/archived/designs/` 与 `docs/archived/plans/`，更新
`docs/README.md` 和 `docs/archived/README.md`。README 的 uv 日志完整性开放项继续独立存在，
其来源链接改指归档 D032 §9，状态保持开放。

### S1 实施记录（2026-09-05）

- 移除 witness stderr 内容门槛，增加 status 字符串类型校验；真实 adapter 接入 evaluator。
- 红测试：`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_runtime_witness.py tests/test_evaluation.py`：exit 1，14 failed / 45 passed，warning 正常结果与路由被旧门槛拒绝。
- 首次 E1：exit 1，2 failed / 84 passed；隔离 venv 默认复制 uv Python 导致共享库定位失败。改用平台支持的符号链接。修改中出现一次 fixture 字符串语法错误，已修复并重新验证。

- 最终 E1（§5 原命令）：exit 0，97 passed / 0.82s。覆盖三个 status × stderr、完整性/终态、错误类型、真实导入脱敏日志和 evaluator 路由。S1 完成，owner/identity 在 S5 归并。

### S2 实施记录（2026-09-05）

- 真实 pytest 故障注入红测试：`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_pytest_observer_integration.py -k terminal_survives_summary`：exit 1，14 failed / 2 passed / 35 deselected，2.18s；其中 missing 两例发现实际进程写多份 summary，夹具改为删除全部 summary，其余故障均复现命令级 InfrastructureError。此前一次夹具字符串语法错误已修复。
- reader 现在对整份不合格 summary 返回 None；ConfiguredVerifier 保留 terminal 与独立 detail/cases，不伪造 facts 或计算不存在的 metadata conflict；插件资源及注入机制未改。

- 最终 E2（§5 原命令）：exit 0，255 passed / 21.90s。此前并行读取中的首次 E2 仍使用修正前 missing 夹具，2 failed / 253 passed；重跑覆盖了完整修改。真实 summary 故障 × 0/1 与 summary/collection/failed-set 正交矩阵均通过。S2 完成。

### S3 实施记录（2026-09-05）

- 红测试：`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_uv_adapter.py tests/test_candidates.py -k 'evidence_only or inapplicable_series'`：exit 1，17 failed / 17 passed / 95 deselected，0.60s。
- query 保留 release keys 后判断 Requires-Python 与完整 wheel 适用性，再要求 SHA-256 与 HTTP(S) locator。必要 yanked 类型校验放在适用性之前；适用 artifact 缺证据使用独立错误说明。
- E3（§5 原命令）：exit 0，190 passed / 0.80s。平台专属非法 locator、hash 缺失、结构损坏及 query→CandidateBuilder 系列偏移直接证据通过。S3 完成；成功 resolve 的输出完整性分支未改。

### S4 实施记录（2026-09-05）

- 红测试：`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_ty_adapter.py -k real_ty`：exit 1，4 failed / 1 passed / 32 deselected，0.09s，项目默认值在进程启动前被拒。
- 移除项目 terminal 键存在即冲突的 parser；不修改 fixed argv、用户 argv/config-file 保护或 scope。
- 实际工具证据校正 AC9 示例：ty 0.0.74 的项目 terminal 支持 output-format，不支持 color。首次 E4 为 4 failed / 57 passed（exit 1，0.45s）；`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=long tests/test_ty_adapter.py -k 'overrides_project and package' --maxfail=1`（exit 1，1 failed / 35 deselected）确认配置解析拒绝未知字段。Design/Plan 改以合法 output-format 同值/不同值验证覆盖，非法 color 保留真实 ToolFailure 测试；仍验证 stdout 无 ANSI，PF 固定 --color never。
- 最终 E4（§5 原命令）：exit 0，62 passed / 0.54s。S4 完成。

### S5 实施记录（2026-09-05）

- TY_DIAGNOSTIC_POLICY 加入 witness_stderr/project_terminal；candidate preimage 加入 artifact_admission。report.py 已通过共享 candidate_policy_identity 完整复算，无需建立或修改第二个实现。
- 四个稳定 owner 已吸收规则，包含 D014 §1.2.3；D002/D005/D007 引用复查无矛盾、无需改动。生成命令 E5a/E5b 均 exit 0，Schema 无差异，两个最小示例 policy/相关派生 identity 更新。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_search_space_report.py tests/test_report_schema.py tests/test_report_artifacts.py tests/test_report_workflows.py tests/test_authorization.py tests/test_candidates.py tests/test_environment.py`：exit 0，269 passed / 4.21s；比 E5c 增补 candidate 精确 preimage 与 prepared environment 完整 evaluation identity。
- `.venv/bin/ruff check src tests scripts` exit 0。首次 `.venv/bin/ty check` exit 1：新增测试三处 union 未收窄，已加真实类型断言修复；另有原有未提交 D031 脚本 `scripts/simulate_d031_search.py:410` 的 Adapter.evaluate 参数 pins 与 VectorEvaluator.vector 不匹配。本次未修改该脚本或 coordinate_search.py，保留独立工作。
- `.venv/bin/ty check --exclude scripts/simulate_d031_search.py` exit 0；最终 E6e 仍记录全仓原命令的真实结果，并把唯一既有独立脚本排除后的结果作为本次类型验收，不声称全仓无错误。S5 完成。

### S6 最终验收记录（2026-09-05）

- AC2 审计发现 json.loads 的深度/整数资源边界仍可能抛出 RecursionError/ValueError；这属于已要求的“非法 stdout 返回 typed ToolFailure”。补入真实 adapter 公共用例并扩展捕获，不放宽机器协议。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_runtime_witness.py -k 'nested-json or oversized-integer'`：exit 1，4 failed / 46 deselected，0.10s。修复后 `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_runtime_witness.py`：exit 0，50 passed / 0.21s。
- 首次 E6a 在该审计修复前启动：exit 0，1836 passed / 39.48s，90.39% coverage（达到仓库 90% 门槛）。它不是最后代码的全套证据，正在重跑 E6a，并依次运行 3.11/3.12。

- 最终 E6a：exit 0，Python 3.10.16，1840 passed / 38.48s，coverage 90.39%，达到 pyproject.toml 的 90% 门槛。runtime_witness 100%；可选 observer/pruning 子进程代码部分不计入父进程 coverage，已有真实集成测试另证行为，未以数字代替 AC。
- E6d `.venv/bin/ruff check src tests scripts`：exit 0。E6e `.venv/bin/ty check`：exit 1，唯一诊断仍为原有未提交 D031 模拟脚本；`.venv/bin/ty check --exclude scripts/simulate_d031_search.py` exit 0。
- 全 docs 本地 Markdown 路径扫描发现 E003 两个既有本地 dogfood 路径 `../../expirements/requests{,/package-floor.json}` 当前不存在；这是外部历史实验产物，不在本次改动。此次新增/修改文档将在归档后单独严格校验路径与 anchors。

### 逐 AC 直接证据索引

| AC | 目标断言所在测试 / 文档 | 审计结论 |
| --- | --- | --- |
| AC1 | `test_runtime_witness_preserves_diagnostics` | 三个状态 × 空/warning/普通日志，保留同一 process；通过 |
| AC2 | `test_runtime_witness_adapter_rejects_invalid_protocol_output`、`test_runtime_witness_requires_complete_normal_success`、unavailable/timeout 用例 | canonical、错误类型、资源异常及 terminal/完整性均 typed failure；通过 |
| AC3 | `test_runtime_evaluator_routes_a_static_witness_outcome` | 真实 RuntimeWitnessAdapter 连接 RuntimeEvaluator，warning 的两种继续状态各获完整 verifier PASS/REJECTED；confirmed-missing 短路、坏协议 Indeterminate，保留 static/witness；通过 |
| AC4 | `test_isolated_import_keeps_warning_in_redacted_log` | 临时 venv 安装本地模块、-I 真实导入，PRESENT/副作用异常 NOT_APPLICABLE；D007 全文日志仍有脱敏 warning；通过 |
| AC5 | `test_real_pytest_terminal_survives_summary_fault`、observer protocol 当前错误/资源用例 | 真实 pytest 0/1 × valid/七类故障；只丢弃 summary，detail/additions 独立；通过 |
| AC6 | `test_real_pruning_collection_proof_is_independent_of_summary`、`test_failed_set_timeout_does_not_fall_back` 与既有 pruning 回退矩阵 | summary/collection/exit 2×2×2 实际子进程矩阵；timeout 不回退；通过 |
| AC7 | `test_query_admits_artifact_evidence_only_for_current_cell` | 平台专属无关 wheel 的非法 locator 与缺 hash 正例；适用证据失败与结构错误区分；通过 |
| AC8 | `test_registry_inapplicable_series_occupies_search_space_offset` | query→CandidateBuilder→DSL 的完整 1/2/3 系列、选择 2/3、候选仅 3，无向 1 补位；通过 |
| AC9 | `test_real_ty_overrides_project_terminal_defaults`、`test_real_ty_validates_invalid_project_configuration`、owned args 用例 | 项目/父 snapshot 配置默认值覆盖，实际 GitLab JSON 与诊断，非法配置交 ty；通过 |
| AC10 | E1–4 全部定向集合与 E6a；uv source/build/ordinary nonzero 既有分类测试 | 只修改已接受门槛；没有 verifier/UNSAT/scope/resolve 完整性放宽；通过 |
| AC11 | `test_candidate_policy_identity_binds_selection_and_admission_policy`、environment 完整 policy preimage 用例、`test_opaque_policy_cannot_be_authorized_by_rehashing_its_snapshot` 及 E5 roundtrip/apply | 两类 identity、共享 reader、D014 精确 preimage 与生成物一致；通过 |
| AC12 | 四个 owner 已归并；S6 最终矩阵、Design/Plan 同步归档与 README 独立开放项 | 通过，完整验证与联合归档见本节 |

- E6b（§5 原命令）：exit 0，Python 3.11 隔离环境，1840 passed / 33.49s；与 3.10 使用同一最终代码。E6c 已按计划串行启动。

- E6c（§5 原命令）：exit 0，Python 3.12 隔离环境，1840 passed / 35.01s。三种受支持 Python 的完整 tests 均通过；未改 observer/pruning plugin 或注入机制，当前资格测试已随 E2/各版本全套运行，无需重做未受影响的插件资格 manifest。
- E6f `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build`：exit 0，生成 `dist/package_floor-0.1.0.tar.gz` 和 `dist/package_floor-0.1.0-py3-none-any.whl`，未发布。
- AC12：D003/D004/D013/D014 已吸收四条稳定规则与精确 preimage；D032/P037 同步移动至 archived，现行和归档索引同步更新。README 的 uv 成功解析日志完整性门槛保持开放，来源指向归档 D032 §9。未改 D031/E005 独立工作，未提交，未复跑 requests dogfood。

- E6g：归档后 `git diff --check` 与 `.venv/bin/python scripts/generate_report_schema.py --check` 均 exit 0；`python3 -` 内联检查 8 份修改/新增 Markdown，107 个本地链接/anchors 有效，AC1–AC12 映射、测试/脚本路径、联合归档路径与 README 开放状态均通过。全 docs 的两条 E003 外部历史路径缺失已单列，不修改历史实验。
- 完成审计：AC1–AC12 均有上述当前证据，S1–S6 全部完成；公共协议、完整 terminal/输出资格、collection 证明及 floor 权威保持。唯一已知工作区质量限制是未改动 D031 模拟脚本的类型错误；本次范围类型检查通过。三版本全套、coverage 门槛、ruff、生成一致性、构建及归档检查已闭合，无本次待实现项。

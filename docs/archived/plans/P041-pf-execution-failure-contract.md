# P041 — PF 统一执行失败契约实施

- 状态：已完成并归档；AC1–AC13 全部通过，稳定规则已归并 owner
- 日期：2026-09-06
- 规范：[D036](../designs/D036-pf-execution-failure-contract.md)，用户已要求实现
- 起点：`d06c3d1d8533f54174c1f3498e674ce0792bbbca`；已有 D036、docs/README、E007 文档改动保留。

## 1. 有序实施切片

| 切片 | 接口与 ownership 迁移 | AC / 测试与证据槽 |
| --- | --- | --- |
| S1 | evaluation schema 的通用 ExecutionTerminal、封闭 attribution/fact union；共享纯分类规则；严格 terminal、stage 及 binding 验证 | AC2/3/4：公共类型与分类矩阵，非法组合拒绝；证据 E1 |
| S2 | UvOperations/Adapter 的 ResolutionFailure、InstallFailure 与辅助 operation failure；Factory 产生 binding、校验 context/envelope、延后 plan digest 提交；PrepareFailure 原样携带事实 | AC1/2/3/4：adapter qualification、resolution/environment 公共接口测试；证据 E2 |
| S3 | FailurePolicy prepare 入口、FailureRecord v3、ConfiguredVerifier 共享规则、search/workflow/baseline/check 消费新接口，保留 excluded 操作协议 | AC5/6/7：verifier、search coordinator、baseline/check/cache 测试；证据 E3 |
| S4 | Report Schema 1 新 authority 与 failure_policy identity、reader 语义复证、required-nullable、policy/generation/cache/apply、Journal/Diagnosis Index/sidecar | AC8/9/10：write/read、重新哈希后的非法语义、identity、merge/update/apply force 测试；生成 schema/examples；证据 E4 |
| S5 | diagnose 与卡片新增 cause/依据；删除当前 BUILD_FAILURE 枚举/展示/测试期待，保留 UNSAT build 排除与历史 manifest | AC11/13：smoke/check/search/diagnose、无本机日志、退出码；证据 E5 |
| S6 | 受控旧式 sdist 真实 resolve/install 构建失败及多候选搜索；追加本次固定 profile qualification 证据，不改历史结果 | AC12/3：真实 uv/PF 集成与 fixture 回放；证据 E6 |
| S7 | D001–D008、D012–D014 按 D036 §10 接收稳定规则；README/README.zh/CONTEXT、导航和生成物同步；逐 AC 审计后同步归档 D036/P041 | AC13 与全部 AC：全套门禁、文档检查、最终验收矩阵；证据 E7 |

S1 → S2 → S3 → S4 → S5 → S6 → S7。每片同步更新所影响的 fixtures/tests，不保留兼容 alias 或双读。
Operation facts 不保存自由文本；运行期 diagnostics 继续通过 sidecar 关联。成功路径完整输出要求不放宽。
uv context 准入在 Attempt 前抛 ConfigurationError；不补造 Attempt。未建模异常保持 InfrastructureError。

## 2. 验收矩阵

| AC | 完成所需权威证据 | 当前状态 |
| --- | --- | --- |
| AC1 | 四个 R/I stage 普通非零经 Factory/Policy 拒绝；仅实际已取得计划；无 Proposal | 通过：E2/E3、最终审计 |
| AC2 | R/I/A/V terminal 穷尽矩阵、timeout 优先；Q/excluded 边界；前置 uv 准入配置失败 | 通过：E1/E2/E3、最终审计 |
| AC3 | 两种真实 UNSAT fixture 的 typed facts；runtime context equality；所有既有排除形状 | 通过：E1/E2/E6、最终审计 |
| AC4 | 全部 15 类 fact 的 stage/terminal/cause；坏成功产物、无进程与图失败穿透 | 通过：E1/E2/E4、最终审计中的 producer 边界 |
| AC5 | configured verifier 全部 terminal、pytest telemetry、failed-set 与完整 PASS 资格 | 通过：E3、最终审计 |
| AC6 | prepare Reject 搜索继续并形成边界；structured Indeterminate 终止；baseline/declaration 终止 | 通过：E3/E6、最终审计 |
| AC7 | 完整向量/context 缓存不重跑；predecessor、非单调/可观察冲突；无 region/failed-set；final 完整 PASS | 通过：E3/E6、最终审计 |
| AC8 | 全 authority、branch、Attempt-only 与 final 的报告往返和 JSON null | 通过：E4/E6、最终审计 |
| AC9 | reader 对所有保存事实复证，重新哈希仍拒绝非法语义；无 context/log 的合法报告可读 | 通过：E4、最终审计 |
| AC10 | identity 采用事实而非 diagnostics；冻结 policy/generation/cache/apply/merge/update 边界 | 通过：E4、最终审计 |
| AC11 | 三命令 Journal/Index/card/diagnose/退出码；fallback 不宣称 UNSAT 或单版本根因 | 通过：E5、最终审计 |
| AC12 | 真实旧式 sdist resolve/install 失败与多候选搜索可达完整 PASS；新 qualification 记录 | 通过：E6 两个真实回放 |
| AC13 | owner/README/CONTEXT/schema/examples/tests 全迁移；删除不可达 cause；验收后归档 | 通过：E7、最终门禁与同步归档 |

## 3. 验证命令与证据记录

CLI/测试/真实 uv 在仓库根目录检查风险后沙箱外执行，使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`。
每次记录精确命令、实际结果与结论；失败不得记作通过。计划门禁：

- 分片公共接口 pytest，`--no-testmon -q --tb=short`。
- `.venv/bin/ruff check src tests scripts` 与 `.venv/bin/ty check`。
- `.venv/bin/python scripts/generate_report_schema.py --check`。
- Python 3.10 全套与 coverage ≥90%；Python 3.11、3.12 全套顺序执行。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build`。
- Markdown 本地链接与 `git diff --check`。

### E0 — 起点检查

- `git status --short`：仅 docs/README、E007 已修改，D036 未跟踪，无生产修改。
- 已读取 D036 完整契约、当前 failure/resolution/schema 接口与 D005 owner。当前 ResolutionUnsat/ResolutionIndeterminate、ToolFailure prepare 和 v2 identity 均需迁移。
- 本 Plan 在生产修改前建立。历史测试计数不作为本次验收证据。

### E1 — 共享执行事实与分类基础

- 已将通用 terminal 名称改为 ExecutionTerminal，无 VerifierTerminal alias；NormalExit 使用非负 strict integer，signal 保持正 strict integer。`execution_terminal` 优先保存 timeout，不受清理后 exit 0 或诊断截断影响。
- evaluation schema 新增封闭 OperationRequestBinding、两种 UNSAT typed facts/固定 profile attribution、15 类 StructuredOperationFact、ExecutionFailure/StructuredOperationFailure，以及共享 terminal/operation 分类与 portable Attempt/plan timing 校验。
- ConfiguredVerifier 已消费共享 terminal extraction/classification；failed-set/full 调度未改。旧 prepare/FailureRecord 路径尚待 S2–S4 迁移，新增 RESOLUTION_FAILED/INSTALLATION_FAILED 暂未进入真实 prepare 输出；BUILD_FAILURE 在 S2/S5 删除。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_contract.py tests/test_configured_verifier.py`：110 passed（0.49s）。
- 初次静态检查发现移除 import 时遗漏 failed-set 分支仍使用 VerifierIndeterminate，已修复；无产品语义变更。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_contract.py tests/test_configured_verifier.py tests/test_pytest_pruning.py tests/test_evaluation.py tests/test_failure.py`：234 passed（5.55s）。
- 扩充 binding 测试后 ty 发现异质 dict 的 **kwargs 推断不成立，改成显式类型可推断的参数传递。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_contract.py`：最终 103 passed（0.07s）。测试涵盖 R/I/A/V terminal 矩阵、异常组合拒绝、固定 credential、全部 15 fact 的示例与错误 terminal、required-nullable 和 branch/plan timing、跨 Attempt 凭据拒绝。
- `.venv/bin/ruff check src tests scripts && .venv/bin/ty check && git diff --check`：最终全部通过。
- 结论：S1 的基础分类与 verifier 接入已有直接证据；AC2/3/4/5 的端到端要求仍待后续接口迁移、真实 qualification 与报告验证。没有运行全套验收或声称实现完成。

### E2/E3 — prepare 接口与消费链路迁移

- 删除 ResolutionUnsat/ResolutionIndeterminate，使用带 request/context envelope 的 ResolutionFailure；InstallFailure 不再接收 cause。辅助 create/interpreter/graph 使用 OperationFailureResult；ToolFailure 只保留在 excluded 协议。
- UvOperations 与 Adapter 的 resolve/install 显式接收 Factory 创建的 OperationRequestBinding。ResolutionFailure 校验 typed UNSAT 与运行时 context 的 tool version/protocol/profile 相等；Factory 校验返回 stage/request/context、UNSAT binding 与 install plan envelope。
- Adapter 先归一化 terminal，再决定失败事实或成功检查。普通非零采用父操作 fallback；完整既有 UNSAT matcher 输出形成 typed attribution；可选日志读取失败采用 Unattributed。正常成功的坏 native plan/output/interpreter/graph 使用结构化 fact；PF 操作输入文件 I/O 失败为 environment-access-failed/null。
- Factory 在 source/artifact/project-preservation 全部检查通过后才提交 plan digest；installed graph 与 proposal vector 检查使用各自 Q stage、terminal=null。PrepareFailure 原样携带 OperationFailure 与 excluded process sidecar。未建模 prepare 异常清理资源后转 InfrastructureError。
- FailurePolicy.record_prepare 接收整个 PrepareFailure；baseline/check/search 已改为此入口。无 Proposal 的普通 prepare rejection 可以继续搜索；结构化 source 外因仍终止。共享 fixture 和 baseline/check/search_coordinator 对应测试已迁移。
- 为使 S2 的公共链路可测试，本轮提前完成依赖的 S3 FailureRecord v3 和 S4 authority/identity/null/sidecar 基础；没有扩大 Design，S4 的完整反篡改矩阵和 S5/S6/S7 仍待闭合。

### E4/E5 — 报告、策略与展示基础

- FailureRecord v3 增加 execution/operation-structured authority，并复用 operation 分类与 portable binding 检查；旧 authority 不得用于 prepare stage，test 只接受 configured-verifier；verifier reader 也复用共享 terminal 分类。
- FailurePolicy identity 为 failure-execution-v3，evaluation policy 吸收 D036 固定 execution_outcome_policy；Schema 1 identity 新增 required failure_policy 并进入 generation preimage。
- 新 nullable binding 与 structured terminal 显式保留 JSON null；reader 的 null path 规则和生成 schema marker 同步。已有完整/不完整 examples 与 JSON Schema 已重生成，--check 通过。
- baseline/check/search 的运行期 process sidecar 支持新 authority，不把诊断正文放进 FailureRecord identity；sidecar 校验其 terminal 与 authority 相等。
- 删除生产 cause enum 和展示映射中的 BUILD_FAILURE，保留 uv_diagnostics 的 build 排除短语。新增两个 fallback cause 文案；diagnose 展示 terminal、qualified attribution/fallback/structured fact。

### 本轮精确验证

- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_contract.py tests/test_prepare_execution.py tests/test_configured_verifier.py`：161 passed（0.45s）；加入报告往返后 169 passed（0.47s）。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_prepare_execution.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py`：77 passed（0.66s）。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_contract.py tests/test_prepare_execution.py tests/test_configured_verifier.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py`：213 passed（0.89s）；最终共享 verifier reader 清理后同命令复核通过。
- `.venv/bin/ruff check src tests/test_execution_contract.py tests/test_prepare_execution.py tests/evaluation_fixtures.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py`：通过。
- `.venv/bin/ty check src tests/test_execution_contract.py tests/test_prepare_execution.py tests/evaluation_fixtures.py tests/test_baseline.py tests/test_check.py tests/test_search_coordinator.py`：通过；新增报告测试的 union scope/outcome 类型收窄问题已修复。
- `.venv/bin/python scripts/generate_report_schema.py`、`.venv/bin/python scripts/generate_report_schema.py --check`、`git diff --check`：通过。

### E2–E5 — 目标契约回归迁移与实际缺口修复

- Adapter/Factory fixtures 已改为显式 OperationRequestBinding、ResolutionFailure、PrepareFailure 和 typed attribution；没有增加旧类型 alias。failure/report/schema/terminal/diagnose/explain fixtures 改用各 stage 的实际 authority，pre-Attempt planning 保持独立协议。
- 全套测试先暴露共享旧报告 fixture 导致的大量错误，再逐步完成迁移。两个仅测试日志省略的 Cell-scope fixture 改用 candidate-discovery，不伪造 configured-verifier Attempt。
- 修复实际缺口：Factory 拒绝不属于目标返回 union 的 create outcome；结果卡在新 authority 没有嵌入 process 时使用匹配 primary failure 的运行期 sidecar；baseline rejection 的最终 presentation 也保留该 sidecar。
- 进一步检查发现 uv version 准入仅看 exit_code，可能接受 timeout 清理后的 0，现改用共享 terminal；ToolSuccess/InterpreterSuccess/GraphSuccess/ResolutionPlan 要求 NormalExit(0)，已有 InstalledResolution 规则保持。新增公共构造/Factory 测试覆盖非零、超时、signal 和 start failure。
- 当前 workspace qualification runner 改从 FailurePolicy.record_prepare 读取结果；受控 unmanaged workspace 普通 resolve nonzero 现在是 REJECTED/RESOLUTION_FAILED。历史 manifest 保留原样，此项不扩大 workspace 支持范围。
- scripts/qualify_uv.py 仅迁移 build 排除诊断期待为 TOOL_FAILURE，其 diagnostic classifier 不是执行结果 authority；真实策略资格证据仍需独立完成，不能以此代替 AC12。

精确验证（pytest 和 uv 均在仓库根目录沙箱外运行）：

- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-migration-suite.log 2>&1`：首次修复 collection 后实际 46 failed、1948 passed、123 errors（54.91s），错误集中于旧报告 fixture；没有记作通过。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_uv_adapter.py tests/test_environment.py tests/test_failure.py tests/test_terminal.py tests/test_report_schema.py tests/test_uv_diagnostics.py > /tmp/pf-d036-migrated-final.log 2>&1`：471 passed（1.75s）。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-migration-suite2.log 2>&1`：25 failed、2087 passed（45.98s）；后续完成 optional group、resolution、schemas、report artifact、search workflow、diagnose/explain 与 workspace qualification 迁移。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_uv.py --uv-bin .venv/bin/uv --expected-version 0.12.5 --output /tmp/pf-d036-uv-diagnostic-replay.json`：exit 0，uv 0.12.5/profile v1，13/13 diagnostic cases expected。结果暂存 /tmp，仅为诊断 profile 回放，不是新增执行契约 qualification 交付。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-migration-suite3.log 2>&1`：1 failed、2112 passed（46.14s）。剩余为 candidate-discovery 展示标签实际空格而非连字符，已修正语义断言。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_contract.py tests/test_prepare_execution.py tests/test_explain_terminal.py tests/test_resolution.py tests/test_uv_adapter.py`：326 passed（0.78s）。随后增加 ResolutionPlan 三种坏 terminal 与 baseline rejection sidecar 断言。
- `.venv/bin/ruff check src tests scripts`、`.venv/bin/ty check`、`.venv/bin/python scripts/generate_report_schema.py --check`、`git diff --check`：通过。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-migration-suite4.log 2>&1`：2132 passed（45.36s）。这是当前默认解释器全套回归通过，不替代三版本/coverage/真实 qualification 与逐项 AC 审计。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon --cov --cov-report=term-missing -q --tb=short tests > /tmp/pf-d036-coverage.log 2>&1`：Python 3.10.16，2132 passed（50.94s），coverage 90.34%，90% 门禁通过。
- 新增离线 reader 20 类重新哈希攻击测试：先证明独立的 v3 preimage 实现与合法报告 ID 一致，再同时替换 evidence ID 和全部 failure refs，覆盖 cause/disposition/stage/plan timing、terminal、固定 tool/version/protocol/profile、completeness 与跨 Attempt binding。合法控制报告先 write/read，无本机 process sidecar；篡改报告仍拒绝。新增字段存在性断言避免错误拼写仅被 extra-forbid 拒绝；context digest 属于 Attempt 而非 attribution，相关 reader 场景仍需后续闭合。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_prepare_execution.py`：68 passed（0.53s）。该测试晚于上述全量 coverage，需最终门禁重跑时纳入。
- 上述 reader 测试字段审计后去除放错 authority 位置的 context mutation，改为实际 protocol/profile 字段；同命令复核 67 passed。context 空串在 Attempt 入口已有模型检查，但不把错误字段测试算作 reader context 验收。

### E6 — 真实 legacy sdist 与多候选搜索

- 新增 `scripts/qualify_execution_failures.py`：回环 HTTPS registry 提供版本 1 sdist、版本 2/3 wheel，实际执行 UvAdapter、EnvironmentFactory、TyAdapter、ConfiguredVerifier、SearchCoordinator 和 ReportStore。sdist 中保留 Python-2 setup.py，由无外部构建依赖的 in-tree backend 执行；install 场景附静态 Metadata-Version 2.2，resolve 场景不附。它是受控旧式 setup 失败，不宣称复测 Jinja2 或 setuptools 默认 legacy backend。
- 首次设施回放依次发现 HTTP 不符合 PF registry 准入、TLS 证书 CA 约束不适用、缺 HEAD、测试项目未配 backend/缺 editable hook；全部在资格验证设施修复，没有改生产准入或将这些意外失败算作目标 sdist 证据。
- `PATH=/home/llh/pf/.venv/bin:$PATH UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_execution_failures.py --output tests/execution_qualification/2026-09-06-uv-0.12.5-v1.json`：exit 0。两个场景均 baseline=3、final=2、predecessor=1；失败分别为 resolve-project/RESOLUTION_FAILED、install-project/INSTALLATION_FAILED，均 NormalExit(1)/Unattributed。失败时仅 install 场景有已验证 project plan。final 的 Proposal 与 baseline 不同，实际完整 verifier NormalExit(0)，报告 write/read 通过。实际进程数分别为 18/19。
- fixture archive/wheel hash 可复算；TLS 私钥只在自动清理的临时目录，证书仅通过当前 replay 的 SSL_CERT_FILE 信任，退出恢复环境。没有关闭 TLS 验证或修改全局信任。适用范围及重跑方式见 `tests/execution_qualification/README.md`。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_qualification.py`：3 passed（2.58s），含 dated manifest 语义及 artifact hashes 检查、两个真实独立回放。
- `.venv/bin/ruff check scripts/qualify_execution_failures.py tests/test_execution_qualification.py`、`.venv/bin/ty check scripts/qualify_execution_failures.py tests/test_execution_qualification.py`：通过。
- 增强资格断言并同命令更新 dated evidence：每场景两次真实完整 verifier（baseline 3 与 final 2），后一次实际在 sdist 拒绝之后；版本 1 不进入任何 static region。3 个 qualification tests 再次通过。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python scripts/qualify_uv.py --uv-bin .venv/bin/uv --expected-version 0.12.5 --output tests/execution_qualification/2026-09-06-uv-diagnostics.json`：exit 0，13/13 expected。新记录完整 stdout/stderr，不改历史 manifest；其中 `pf_classification` 是诊断排除/UNSAT matcher 结果，不是父操作执行契约 disposition。
- `tests/test_prepare_execution.py` 对全部 13 个真实输出，按 project/environment 分支与完整/截断 envelope 回放 UvAdapter → Factory → Policy。两类合格 UNSAT 得到 typed facts 与对应 conflict cause 并报告往返；其余与不完整 envelope 均 Unattributed，hash fixture 保持其实际 install stage，不借作 resolve 终态。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_prepare_execution.py`：119 passed（0.73s）。

### E7 — 跨解释器门禁与 owner 归并启动

- 新建 `/tmp/pf-d036-gates-C7FD2D` 隔离 3.11/3.12 环境，保留仓库现有 `.venv`；依赖使用 --frozen --group test。以下两版全套按顺序执行，没有并行 full pytest。
- `UV_PROJECT_ENVIRONMENT=/tmp/pf-d036-gates-C7FD2D/py311 UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.11 --group test pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-python311.log 2>&1`：2207 passed（47.26s）。
- `UV_PROJECT_ENVIRONMENT=/tmp/pf-d036-gates-C7FD2D/py312 UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.12 --group test pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-python312.log 2>&1`：1 failed、2206 passed（47.64s）。失败是第二个资格回放换证书后 Python 3.12 urllib 缓存 SSLContext；读取该解释器标准库实现确认缓存点，并非产品构建失败。
- 设施改为同一新进程的两个场景共享一个临时证书，pytest 用子进程运行完整 qualification；不复写全局 urllib opener，也不关闭证书验证。3.12 专项 3 passed（2.66s）。
- `UV_PROJECT_ENVIRONMENT=/tmp/pf-d036-gates-C7FD2D/py312 UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.12 --group test pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-python312-final.log 2>&1`：2207 passed（47.80s）。设施变化后 3.10/3.11 最终全套仍须复核。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build --out-dir /tmp/pf-d036-gates-C7FD2D/dist`：exit 0，生成 package_floor-0.2.0.tar.gz 和 package_floor-0.2.0-py3-none-any.whl；临时 out-dir 避免覆盖现有构建产物。
- Owner 归并已启动：D005 接收共享执行/结构化表、封闭凭据、FailureRecord v3、plan/binding/opaque context 规则、diagnose 语义与有限 nondeterminism seam；D012 接收中性 failure interface、准入、binding/context 验证与真实 qualification；D014 接收完整 execution policy preimage、failure_policy generation、required-nullable、reader/merge 边界。其余 owner/README/CONTEXT 尚未完成，不归档也不声称 AC13 完成。

### E4/E7 — 剩余 owner 与结构化报告证据

- D001/README/README.zh 接收精确向量与误拒绝取舍、完整 final PASS、无重试/复现承诺；D002 接收失败事实 ownership、实际失败阶段 Attempt 与 sidecar；D003 接收 prepare rejection 的边界/region/FailedCaseSet 限制和四个真实 cache/conflict seam；D004/D007/D013 明确 ty/witness/pytest diagnostics 与通用执行规则的边界；D006/D008 接收 authority-derived 展示、本机 sidecar 和三命令角色规则。移除现行 owner 的旧类型/版本/不可达 cause 表述；历史 Experiment/manifest 不改写。
- 使用 domain-modeling 技能更新 CONTEXT：Rejection 不承诺重复性或单依赖根因，Cause 不预设由 Adapter 分类，Execution Terminal/Attribution/Operation Failure 只保留领域定义；未将分类表或 wire 细节塞入 glossary。
- 新增 Factory envelope 测试覆盖不同 request、合法但不同 ResolutionContext、错误返回 stage、跨 Attempt UNSAT binding 与 install plan；全部在预期操作形成 request-invariant/Indeterminate，而非采纳普通非零拒绝，plan 时序保持。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_prepare_execution.py`：124 passed（0.97s）。
- 新增全部 15 fact 的 PrepareFailure → FailurePolicy → ReportStore write/read，并验证 required-nullable authority；每类在重算 Failure ID、替换全部 refs 后仍拒绝伪造 cause 和 test stage。独立公共 wire examples 抽到 `tests/execution_fixtures.py`，不从生产 rule table 推导测试期待。这是 portable fact 链路证据，不冒充所有直接检查 producer 的真实端到端观察。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_contract.py tests/test_prepare_execution.py`：254 passed（0.97s）。
- `.venv/bin/ruff check src tests scripts`、`.venv/bin/ty check`、`git diff --check`：通过。owner/README/CONTEXT/P041/qualification README 的本地 Markdown 目标存在性检查通过；anchor 最终检查仍随归档处理。
- `tests/test_execution_run.py` 新增三命令 × 两个 project resolve/install stage × 普通非零/timeout，共 12 个联动场景：Factory → Policy → VerificationRunner → 实际 Journal/Diagnosis Index，live/final card，search report，DiagnoseWorkflow 有/无 process locator，以及 1/4/0 退出码。测试使用合法 root/SourcePlan 与真实持久化，不把 sidecar 嵌回 authority。
- 联动测试实际发现 check 的 declaration-capture rejection 最终摘要误称 declared lower bounds incompatible，违反 D008 既有规则。先加语义断言得到 2 failed，再改 final summary 按 capture outcome 显示 baseline capture did not pass；分类、Role 和退出码不改。D006 同步该稳定文案。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_execution_run.py tests/test_terminal.py tests/test_verification.py tests/test_diagnose.py`：210 passed（1.40s）；全仓 Ruff/ty 与 diff check 通过。

### 上一阶段待审计清单（以下事项由最终审计闭合）

- 已执行全套回归并完成已发现的目标 fixture 迁移，默认解释器 2132 tests 全通过；尚未做完整 AC 审计。当前 src/tests/scripts 搜索只保留 uv_diagnostics 的 BUILD_FAILURE 排除短语常量及历史 manifest。
- 真实旧式 sdist resolve/install、多候选搜索与两类 UNSAT 的实际 diagnostic fixture → Adapter typed attribution 回放已有 E6 证据；producer context equality 等完整 AC3 项目仍需最终逐项审计；历史 manifest 不改写。
- S4 已有 Attempt-only 报告往返、20 类执行 authority 重新哈希，以及 15 类 structured authority 往返/重新哈希拒绝证据；仍需 Attempt context/identity/refs 与 policy/cache/apply/merge/update 的专项验收审计。sidecar 三命令 baseline/capture 联动已有直接证据，probe/其他角色的现有证据仍需按 AC11 审计。
- 环境/source/request/evidence-conflict 全观察路径和全部 15 fact 的 end-to-end 覆盖仍需审计；当前公共测试证明的 I/O 外因、坏成功产物、四类 R/I fallback、auxiliary 和 timeout 不等于全部 AC4 完成。
- Python 3.10 较早全套/coverage、3.11 与最终 3.12 全套和真实构建已有证据；新增测试与设施修改后的最终三版本/coverage 仍须复核。指定 owner/README/CONTEXT 已归并，仍须审计完整性；全部 AC 审计与归档尚未完成。目标仍为完成 D036 全部 AC1–AC13。

### 最终逐项验收审计

- AC1/2：`test_prepare_execution` 的四个 R/I stage、全部 A 异常 terminal、timeout 清理后 0、Attempt-only 分支，结合 `test_execution_contract` 的共享分类矩阵，证明 cause、authority 和 plan 时序。Q/excluded stage 在公共分类入口拒绝；ty/witness/planning 的独立回归保持。额外发现版本输出读取 OSError 泄漏，新增公共 Factory 测试先失败，再由 Adapter 将不可得版本输出归入准入 ToolFailure，Factory 在 Attempt 前抛 ConfigurationError。
- AC3：E6 的 13 类真实输出按两 branch/完整性回放；两种固定 code 产生 typed facts 并报告往返，其余含 build 排除保持 Unattributed。`test_resolution` 校验固定 context profile；`test_factory_closes_mismatched_failure_envelopes_under_expected_request` 验证实际合法但不同 context、request/stage/binding/install plan。不接受未注册 credential，也不通过放松 context 构造来伪造 mismatch。
- AC4：15 类独立 wire examples 覆盖合法分类/terminal 和报告往返/重算 ID 后拒绝非法 stage/cause。实际 producer 的成功坏 output/plan、interpreter/graph 观测、环境输入 I/O、request envelope、source/artifact policy、installed graph/vector 检查分别由 `test_prepare_execution`、`test_environment`、`test_uv_adapter` 公开入口覆盖。检查通过前不提交 plan digest；Q 的 terminal 为 null。`source-access-failed`、`artifact-invalid`、`evidence-conflict` 是仅在直接观察相应事实时才可用的封闭契约；当前 uv adapter 不直接读取 source artifact 或执行额外 hash 校验，也没有双 authority 观察入口，因此不凭文本/缺失失败产物制造这些 producer。其分类/reader 证据不冒称真实 I/O 集成，也不新增检查扩大本 Design 范围。
- AC5：`test_configured_verifier` 覆盖任意正常非零、正常 0（包括不完整诊断）、timeout 优先、signal/start/unavailable 和 pytest/通用命令；`test_pytest_pruning` 保留 failed-set 只能拒绝、完整 PASS 资格。ty/witness 与 observer 回归不借用 prepare 兜底。
- AC6/7：`test_prepare_rejection_continues_within_configured_space` 与 `test_search_preserves_exact_prepare_failure_and_emits_one_diagnostic` 对照 Reject 继续到 2、source structured Indeterminate 停止且只执行一次。E6 证明坏版本 1 后真实完整验证 2，不把 1 写入 region。`test_environment_factory_resolves_identical_inputs_only_once`、`test_actual_patch_binds_resolutions_attempt_and_cache` 与跨 Slice full-result cache 测试覆盖复用/context 隔离；`test_search` 的 predecessor 重验、`test_evaluation_cache` 的 observable conflicts 及 schemas 的直接同 Slice counterexample 覆盖既有搜索限制。实现审计确认 prepare/full cache 每个 evaluator 独立，完整向量为 key；prepare failure 在 static/failed-set 生成前返回，缓存命中没有再次执行或复现承诺。
- AC8/9：E4 全 fact/四 R/I/异常 terminal/harness/project-only/Attempt-only 与 E6 final 报告共同覆盖；required-nullable 在 schema 与 write/read 中保留。20 类 execution 重新哈希攻击和每个 structured fact 的 stage/cause 重新哈希攻击仍拒绝，普通 reader fixtures 补充 graph/Attempt/proposal/failure identity 与 refs。新增非空 opaque context 漂移与已有空串一起拒绝；合法报告无 context preimage 和本机 process 也能读取。reader 使用共享分类和 portable binding，不宣称解开 opaque digest。
- AC10：`test_failure_record_identity_ignores_captured_process_output` 明确同 execution facts 下日志/耗时/完整性变化不改 ID；独立 wire hash 控制与语义篡改测试锁定采用事实。`test_environment` 独立 policy preimage 包括整个固定 execution 对象。`test_evaluation_policy_isolates_reports_with_explicit_defaults` 新增 rules/structured_facts/attribution_profiles 三项变化，逐项证明 generation 隔离、merge/update 拒绝、apply force false/true 均不可绕过，update_path 明确替换 generation。cache 的 policy 隔离经 Attempt/Proposal identity 与每次 search 独立 evaluator 实现；不增加旧策略双读或把 merge 冲突变为 NONDETERMINISTIC。
- AC11：E5 新增 12 个三命令持久化/展示联动场景；`test_verification` 的 search closed roles 与 `test_diagnose_role_impact_matches_the_offline_contract` 补充 probe/check 及有/无日志角色语义。primary process sidecar 与 authority terminal 匹配，Journal 与 report 共用 FailureRecord；正常 fallback 退出 1、异常 4、diagnose 0，check capture 不误称 lower bounds 已证明不兼容。
- AC12：新增 dated manifests 和可独立重跑设施的两种真实 legacy sdist 场景满足受控集成要求。未将 E007 历史构建失败改写为新结果，也未声称 MkDocs/Jinja2 全搜索成功；可选 dogfood 不作为本次必需门禁。
- AC13：D001–D008、D012–D014、README/README.zh、CONTEXT 与生成 Schema/examples 已接收目标规则；当前生产 cause/wire/展示删除 BUILD_FAILURE，uv diagnostics build 排除与历史 manifest 保持。最终全量门禁和同步归档记录见下节。

审计新增验证：

- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_prepare_execution.py -k unavailable_uv_version`：1 failed、139 deselected；直接暴露版本输出 OSError，随后修复准入路径。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_prepare_execution.py tests/test_authorization.py tests/test_report_schema.py tests/test_evaluation_cache.py tests/test_search.py tests/test_search_coordinator.py`：388 passed（4.62s）。
- `.venv/bin/ruff check src tests scripts`、`.venv/bin/ty check`、`.venv/bin/python scripts/generate_report_schema.py --check`、`git diff --check`：通过。

### 最终门禁与同步归档

全部命令在 `/home/llh/pf` 执行；pytest/uv 经风险检查后在沙箱外运行，三个 full suites 顺序执行：

- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon --cov --cov-report=term-missing -q --tb=short tests > /tmp/pf-d036-final310.log 2>&1`：Python 3.10，2244 passed（55.34s）；coverage 90.46%，通过 90% 门禁。
- `UV_PROJECT_ENVIRONMENT=/tmp/pf-d036-gates-C7FD2D/py311 UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.11 --group test pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-final311.log 2>&1`：2244 passed（46.86s）。
- `UV_PROJECT_ENVIRONMENT=/tmp/pf-d036-gates-C7FD2D/py312 UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --frozen --python 3.12 --group test pytest --no-testmon -q --tb=short tests > /tmp/pf-d036-final312.log 2>&1`：2244 passed（48.29s）。
- 三版全套均包含当前受控 sdist resolve/install 的真实资格回放，不仅验证保存的 manifest。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build --out-dir /tmp/pf-d036-gates-C7FD2D/final-dist`：wheel/sdist 构建成功，未覆盖现有产物。
- `.venv/bin/ruff check src tests scripts`、`.venv/bin/ty check`、`.venv/bin/python scripts/generate_report_schema.py --check`：全部通过。
- 本地 Markdown 链接及锚点检查在归档前覆盖 19 files / 192 targets，全部通过；归档后覆盖 20 files / 209 targets，0 errors。归档后 Ruff、ty、schema `--check`、`git diff --check` 再次通过。
- D036/P041 同步移入 `docs/archived`，修复 owner/来源/实现链接，更新工程和归档索引。E007 只更新后续状态与链接，原实验数据/结论不改；原工作区文档改动保留。未提交或推送。

结论：S1–S7 与 AC1–AC13 已完成。没有新增重试、文本 build cause、兼容 alias、source 支持或无限 nondeterminism 检测；后续可选 E007 dogfood 和 C003 成功日志放宽不属于本次交付。

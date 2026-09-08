# PF 测试

本目录验证 PF 自身的接口行为。默认 pytest 收集范围是 `tests/`；`experiments/` 内第三方项目的
测试由对应实验运行。行为依据见 [D002 的模块与测试 seam](../docs/designs/D002-pf-implementation.md)，
契约演进遵循 [AGENTS.md](../AGENTS.md)。

## 组织与断言

按现行契约分三层，不要用资格回放代替公开缝，也不要把纯 schema 断言绑在真实 uv/ty 上。

| 层 | 默认门禁 | 内容 |
| --- | --- | --- |
| 单元 | 是 | schema、纯函数、`evaluation_assembly` / `ScriptedStaticRequests` |
| 公开缝 | 是 | 少量真实 prepare/ty/CLI，证明 adapter 与 Run cache 的正向路径 |
| 资格 | 否，`qualification` 标记 | `scripts/qualify_*.py` 与其余真实矩阵回放 |

- 用 `Test<Subject><Aspect>` 划分功能域，测试方法命名为 `test_<interface>_<outcome>`。
- 同一接口结果路径的不同输入用 `pytest.mark.parametrize`，用语义化 `ids` 区分边界和故障。
  有先后依赖的状态迁移、缓存复用和报告生命周期保留为连续场景。昂贵真实进程矩阵只在资格层展开。
- 从公开返回值、异常字段、事件、输出协议或持久化产物验证行为。插件测试调用 pytest hooks 后
  检查协议文件；替身响应请求中的环境配置，不复制被测代码的命令识别逻辑。
- 精确断言 PF 拥有的协议、退出码、身份、安全边界和规定的视觉规则。依赖库的显示顺序和提示
  文案用结构化语义验证；例如比较 `SpecifierSet`，而非其格式化字符串。
- 合并前核对接口与结果路径。不同层级有各自职责：schema 准入、adapter 协议消费和真实进程
  透明性不能相互替代。检查合并前后的已覆盖源码行和分支集合，而不只比较覆盖率百分比。
- 对真实进程、安装、嵌套 pytest、xdist 保留集成证据；普通 CLI 输入矩阵走公开入口。
  完成等待使用输出事件与有上限的等待，避免以固定睡眠推断线程已经完成。
- 负向测试只覆盖现行契约要求的错误或安全行为，不枚举已删除的旧语法。

## 验证

从仓库根目录执行；运行环境要求见 [AGENTS.md](../AGENTS.md#run-environment)。
`pyproject.toml` 的默认 `addopts` 含 `--testmon` 与 `-m "not qualification"`。
`PATH` 需包含仓库 `.venv/bin`，以便真实 ty/uv 公开缝能解析到工具。

```sh
# 默认门禁：单元 + 公开缝，不含资格回放
uv run pytest --no-testmon -q --durations=30
uv run ruff check tests
uv run ty check
git diff --check

# 资格回放（真实 uv/ty/observer/CLI 脚本）
uv run pytest --no-testmon -q -m qualification

# 3.10 覆盖率门禁（含资格；与 CI 一致，fail_under=90）
uv run pytest --no-testmon --cov=pf --cov-report=term-missing -m ""
```

`--no-testmon` 确保本轮全量执行。日常增量执行仍可使用默认 testmon。

## 2026-09-06 整理记录

基线依赖与源码对应 `772dbb7`；本轮修改测试、测试收集配置和本页。

| 治理项 | 保留的行为证据 |
| --- | --- |
| observer 私有状态断言 | hooks → summary/detail/progress；包括非法 nodeid、失效后继续收到报告、数量边界和 xdist 角色 |
| 重复 verifier/schema/进程用例 | 参数矩阵覆盖正常退出、超时、信号、启动失败、非法终止事实及终端尺寸来源 |
| 重复 observer 资格回放 | 完整回放一次，同时检查外层 pytest 进度不被嵌套资格运行覆盖；原 E001 的两个测试入口合并至 `TestPytestObserverQualificationRunner.test_run_replays_current_profile_with_isolated_nested_progress` |
| CLI 配置错误矩阵 | `main()` 的退出码、错误语义和无副作用；安装级入口测试继续保留 |
| 项目安装路径中的命令循环 | smoke/check/search/minimize × 缺失/空测试组独立参数项；安装与 verifier 实际运行，search/minimize 分别验证报告 |
| 安装日志中的内部命令计数 | 由 `TestOptionalGroupPreparation.test_empty_harness_all_roles_and_cache` 验证 prepare、安装与缓存职责；端到端检查 verifier 和报告结果 |

当时的全量命令为 `uv run pytest tests --no-testmon -q --durations=30 --cov=pf`。当前默认门禁
已排除 `qualification`；含资格的对照用 `uv run pytest --no-testmon -q -m ""`。

| 指标 | 治理前 | 治理后 |
| --- | --- | --- |
| 测试方法/函数 | 1168 | 1140 |
| TestClass | 124 | 132 |
| 类外测试 | 22 | 0 |
| 参数展开后的通过项 | 2246 | 2249 |
| 含覆盖率的完整运行 | 58.59 秒 | 50.81 秒 |
| 语句与分支综合覆盖率 | 90.46% | 90.47% |

展开后的项数略增来自命令循环展开与重复完成通知场景，函数减少并不要求参数项减少。
基线已覆盖的源码行与分支均保留。耗时是同机单次运行对照，受安装缓存与调度影响；未降低
覆盖率门槛或跳过慢测。Ruff、ty 与 whitespace 检查通过。

### pruning 插件覆盖补充

同日新增 `TestPytestPruningSelection`，通过 `pytest_cmdline_main` 的 pre-yield 收集边界验证
参数替换、invocation nonce、不可用请求与不支持的 Config。19 个参数项与已有 pruning 集成测试
共 62 项通过（5.07 秒）；`src/pf/_pytest_pruning.py` 语句覆盖 29/29，分支覆盖 6/6，均为 100%，
达到至少 85% 的目标。Ruff、ty 与 whitespace 检查通过。

```sh
uv run pytest tests/test_pytest_pruning_plugin.py tests/test_pytest_pruning.py --no-testmon -q --cov=pf._pytest_pruning --cov-branch --cov-fail-under=85 --cov-report=term-missing --cov-report=json:/tmp/pf-pruning-coverage.json
```

85% 仅用于此命令的模块专项门槛；仓库全量覆盖率门槛仍为 90%。分支比例另从 coverage JSON 的
`covered_branches / num_branches` 核对，避免把语句与分支的综合覆盖率误报为分支覆盖率。

### 函数总耗时与并行运行

同日另测完整套件，关闭 testmon、不启用 coverage。按 JUnit 的 `classname` 与去掉参数后缀的
方法名分组，累加全部参数项的时间；包含 setup/call/teardown，共享 fixture 计入实际承担它的
参数项一次。以下按优化前总耗时排序，另列出优化后进入前五的报告生命周期测试。

| 测试函数 | 参数项 | 优化前 | 优化后 |
| --- | --- | --- | --- |
| `TestInstalledCli.test_cli_verifies_project_only_environment` | 8 | 7.350 秒 | 7.117 秒 |
| `TestPytestObserverQualificationRunner.test_run_replays_current_profile_with_isolated_nested_progress` | 1 | 4.077 秒 | 1.270 秒 |
| `TestExecutionFailureQualification.test_replay_searches_to_full_pass_after_execution_rejection` | 2 | 2.671 秒 | 2.020 秒 |
| `TestPytestObserverSummaryFaults.test_run_preserves_terminal_under_summary_fault` | 16 | 1.871 秒 | 1.872 秒 |
| `TestPruningCollectionAuthority.test_run_uses_collection_proof_independently_of_summary` | 8 | 1.628 秒 | 1.630 秒 |
| `TestInstalledCli.test_installed_module_cli_completes_report_lifecycle` | 1 | 1.597 秒 | 1.635 秒 |

- `qualify_pytest_observer.py` 将 12 个隔离场景分到至多 4 个线程执行。每个场景仍运行真实
  reference/injected 进程对，组内保持串行；`executor.map` 保持结果顺序，既有 manifest 摘要
  检查仍通过。
- `qualify_execution_failures.py` 的本地服务器使用 0.01 秒轮询，使 shutdown 不必等待默认
  轮询周期。当前单次 profile 中两次 shutdown 合计 0.007 秒；37 次 PF 子进程运行合计约
  1.418 秒，是回放的主要成本。
- 安装矩阵保留 4 个命令 × 2 种测试组；summary 与 pruning 矩阵保留全部真实进程场景。
  这些未修改函数的时间差属于本次运行波动。
- 4 worker 运行暴露 merge 派发测试对长临时路径换行的耦合。派发用例继续精确断言
  `MergeRequest` 路径和成功输出；完整展示规则由
  `test_merge_success_preserves_every_input_path_and_one_final` 验证。

串行全量 **42.12 → 38.40 秒**，4 worker 全量 **11.72 秒**；三个 JUnit 的测试项集合完全
一致，均 2268 项通过。Ruff、ty、whitespace 检查通过。以上是同机、已有缓存的单次对照，
并行收益依赖可用 CPU 与 I/O；默认 pytest 配置未添加 worker 数。

```sh
# 串行测量；排名应汇总参数项，不能只看 --durations 的单项排名。
uv run pytest --no-testmon -q --durations=40 --junitxml=/tmp/pf-runtime.xml

# 本机已验证的并行全量命令。
uv run pytest --no-testmon -n 4 -q
```

## 2026-09-08 运行时间整理

D038/P042 之后，用户同款 `pytest tests/`（Python 3.10.16，默认 testmon，无 coverage）
从约 40 秒涨到 **2412 passed / 28 deselected in 266.77s**；同机无 testmon 全量曾到
**2601 passed in 288.61s**。膨胀来自真实 uv/ty/CLI 被乘进参数矩阵，以及资格脚本进入默认收集。

本轮按单元 / 公开缝 / 资格分层，不删 D038 正向语义：

- 默认门禁排除 `qualification`；资格回放仍可通过 `-m qualification` 或 `-m ""` 运行。
- Run cache、journal 多观察、报告 reader 等改为 `ScriptedStaticRequests` / `evaluation_assembly`。
- 真实 prepare/ty 矩阵只留一条公开缝，其余组合标资格。
- `tests/test_static_comparison.py` 用脚本化公开缝覆盖 slice/global 比较。

| 指标 | 治理前（用户同款） | 默认门禁（无 cov） | 含资格覆盖率门禁 |
| --- | --- | --- | --- |
| 命令 | `pytest tests/` | `pytest tests/ --no-testmon` | `pytest --no-testmon --cov=pf -m ""` |
| 通过项 | 2412（testmon）/ 2601 全量 | 2557 | 2579 |
| 排除项 | 28（testmon） | 22（`qualification`） | 0 |
| 墙钟 | 266.77s / 288.61s | **85.66s** | 320.27s（含 coverage） |
| 覆盖率 | 未测 | 默认收集约 89.8% | **90.01%** |

Ruff、ty 与 `git diff --check` 通过。日常 `pytest tests/` 走默认门禁；CI 3.10 使用含资格的覆盖率命令。

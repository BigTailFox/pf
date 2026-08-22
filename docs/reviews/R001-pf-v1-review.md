# R001 — PF v1 仓库评审

- **状态：** 快照
- **日期：** 2026-08-22
- **性质：** 非规范性评审；不定义命令、算法、Schema 或模块接口
- **对照：** 当时 `main` / `d1b8614`（`feat: implement D006, D007, D008`），远程 `origin` 为 `git@github.com:BigTailFox/pf.git`
- **契约所有者：** [D001](../designs/D001-pf.md)–[D008](../designs/D008-pf-verification-run.md)；实现结构以 [D002](../designs/D002-pf-implementation.md) 为准

本文记录一次对照现行契约与源码的架构评审，回答「v1 落地之后下一步优化什么」。它不取代 D001–D008，也不把 D001 §10 的非目标改写成待办。行数与测试计数来自同日对 `src/pf`、`tests` 的统计。

## 1. 结论

D001–D008 已全部落地。当前杠杆不在再加命令，而在加深已有模块、收回泄漏的内部 seam、补上测试面和工程门禁。

仓库约 13,473 行源码、16,523 行测试。`failure.py`、`ProjectLoader.load`、`CoordinateSearch.minimize`、`EvaluationCache` 已经是小接口、深实现。税主要在三个过宽文件：`workflow.py`（1175）、`search.py`（1097）、`terminal.py`（1870，对应测试 2938 行）。

D002 规定文件变长不是拆分条件，只在已经存在两个可独立描述、测试和演进的深模块时再拆。下文按这条原则取舍，不按行数机械切文件。

## 2. 模块健康

| 模块 | 行数 | 判断 | 建议 |
| --- | ---: | --- | --- |
| `terminal.py` | 1870 | Presenter 过宽：每个命令一个 `render_*`，外加进度消费与诊断折叠 | 后置；先把 `explain` / `diagnose` 挪到同包私有模块，对外仍 `render_X(result) -> exit_code` |
| `workflow.py` | 1175 | 七个命令叠在一起；Check / Smoke / Search 复制同一条验证运行编排 | 抽出验证运行编排，三个命令变薄 |
| `search.py` | 1097 | `CoordinateSearch` 与 `SearchCoordinator` 已是两个模块，挤在一个文件里 | 按已有边界拆；`minimize` 改为局部状态，接口可重入 |
| `schemas/evaluation.py` / `schemas/report.py` | 941 / 903 | Schema 把 complete 授权压在 validator 里，形状对 | 不优先拆 |
| `runlog.py` | 914 | 深，但没有独立测试面 | 以 store interface 为表面补 `test_runlog.py` |
| `project.py` | 771 | 深：`load(root, package_selection) -> ProjectPlan` | 优先不动 |
| `report.py` | 658 | Builder 与 Store 在 complete/incomplete 上重叠；Store 调用 Builder 私有方法 | 收回 `_failure_reason` / `_cell_key` |
| `environment.py` | 449 | `prepare()` 深；受管向量的 TOML 改写与 `ProjectEditor` 同类 | 后置 |
| `cli.py` | 405 | composition root 合格；`diagnose` 单独构造了不带 `pythons=uv` 的 `ProjectLoader` | 复用同一个 `projects` |
| `evaluation.py` | 325 | 深 | `FullEvaluator` 构造与别处 Protocol 对齐 |
| `candidates.py` | 243 | 深 | 优先不动 |
| `failure.py` | 53 | 深：`classify(...)` 一个 seam | 让更多调用方只走这里 |

## 3. 建议顺序

| 优先级 | 项 | 类别 |
| --- | --- | --- |
| P0 | 立 CI 与全量测试门禁 | 基建 |
| P0 | 统一 Cell identity 与 Failure 提取 | 架构 |
| P0 | 抽出验证运行编排 | 架构 |
| P1 | 把 Evaluation → FailureRecord 收进 FailurePolicy 附近 | 架构 |
| P1 | 加厚 `CoordinateSearch` 表驱动测试 | 测试 |
| P1 | 按已有边界拆 `search.py` | 架构 |
| P1 | 补 editor / apply 授权测试 | 测试 |
| P1 | 给 `RunLogStore` 独立测试面 | 测试 |
| P1 | 收紧 Protocol，去掉 `hasattr` 探测 | 架构 |
| P2 | 收窄 `TerminalPresenter` 内部视图 | 架构 |
| P2 | Journal 改为 cell 完成后再写 | 架构 |
| P2 | 产品范围继续守住 D001 §10 | 产品 |

从门禁和 cell identity 开始。这两步不改产品语义，但会让后面每一项都更便宜。

## 4. 分项

### 4.1 立 CI 与全量测试门禁

仓库是 git 仓库，`main` 跟踪 `origin/main`。缺的是可重复门禁：没有 `.github/` workflow、没有落地的 ruff / mypy / pyright 配置、没有 LICENSE / CHANGELOG。`.gitignore` 里有 `.ruff_cache/`，像是打算用 Ruff 但未写入 `pyproject.toml`。

`[tool.pytest.ini_options].addopts` 默认 `["--testmon"]`。本地增量快，全量回归若忘关会假绿。`[tool.pf].test-command` 反而写了 `--no-testmon`，说明作者知道这一点。

覆盖率 `fail_under = 90` 只在开发者机器上有意义。

**下一步：** 在已有 GitHub 远程上加最小 workflow：`uv sync --group test && uv run pytest --no-testmon`。Ruff 先开错误级。类型检查从 `pf.failure` / `pf.candidates` 这类深小模块开始，不要先喂 `terminal.py`。

### 4.2 统一 Cell identity 与 Failure 提取

至少五套 cell key、三套 failure 提取。有的含 `package`，有的是 `"|"` 拼接。合并、进度、诊断只要一套算错就会静默丢 cell。

| 位置 | 形状 |
| --- | --- |
| `workflow.py` `_cell_identity` | `(package, target, python, extras)` 四元组 |
| `scheduling.py` `_cell_key` | 同四元组 |
| `terminal.py` `_cell_key` | 同四元组 |
| `report.py` Builder / Store 各一份 `_cell_key` | `target\|python\|extras` 字符串，无 package |
| `schemas/report.py` `_cell_key` | 又一种四元组 |
| `workflow.py` Search / Diagnose 各一份 `_failure_records` | 与 `schemas/report.py` `_failure_records_for_result` 重复 |
| `ReportStore.update` | 调用 `PackageReportBuilder._failure_reason` |

**下一步：** identity 收成 `Cell` 或 `schemas/project.py` 旁的一个纯函数；failure 提取放进 report schema 旁的单一函数；删除 Store 对 Builder 私有方法的跨模块调用。

### 4.3 抽出验证运行编排

`CheckCommandWorkflow.run`（约 434–498）与 `SmokeCommandWorkflow.run`（约 585–651）结构平行：load → snapshot → `selected_host_cells` → `_JournalGate` → `scheduler.run` → 再写一遍 journal → `gate.raise_if_failed`。Search 同一骨架再加 report persist。

D008 的统一运行模型在代码里是复制出来的，不是一个接口。改 journal 或宿主 cell 规则要改三处。

**下一步：** 先写 `run_verification(command, cells, task_factory) -> outcomes` 的测试，再让三个 workflow 变薄。不要按行数把七个命令切成七个文件。

### 4.4 把 Evaluation → FailureRecord 收进 FailurePolicy 附近

D002 规定失败分类只有一个所有者。Adapter cause 已经走 `FailurePolicy`，但「从哪种 Evaluation 取出 cause / stage / process」复制了两遍：

- `CompatibilityChecker._evaluation_outcome`（`workflow.py`）
- `_ProposalRunner._static_evidence` / `_full_evidence`（`search.py`）

`STATIC_REGRESSION` / `TEST_FAILURE` 分支各写一份。新加一种 Evaluation 类型会漏改。

`workflow.py` 与 `search.py` 还各自声明几乎同签名的 `CheckEnvironmentOperations` / `SearchEnvironmentOperations` 等 Protocol。这是假 seam：没有第二个真实 adapter。

**下一步：** 一个函数，两个调用方；共用 `EnvironmentOperations` / `StaticOperations` / `FullOperations`。

### 4.5 加厚 CoordinateSearch 表驱动测试

`CoordinateSearch.minimize` 是仓库里最干净的算法模块：只吃冻结向量和 `VectorEvaluator`。`tests/test_search.py` 只有 6 个用例，覆盖不了 hint、`small_threshold`、`NON_MONOTONIC`、`start_is_known_pass`、空切片的组合。Coordinator 测试替代不了「给定假 evaluator 的向量序列」。

**下一步：** 纯函数式 fixture evaluator，不碰文件系统。

### 4.6 按已有边界拆 search.py

D002 的拆分条件已经满足：`CoordinateSearch`（约 79–356）零依赖 coordinator；`SearchCoordinator.search` 是另一套生命周期。

`SearchCoordinator` 把 `coordinate_search` 参数降级成只偷 `small_threshold`（约 831–833），传入的实例不被使用。`HighestVersionVerifier` 缺省时在构造函数里新建，和 `cli.build_context` 已有装配不一致。`CoordinateSearch.minimize` 把 evaluator 写到 `self` 上，接口上看不出来不可重入。

**下一步：** 先移动文件，不改行为；`HighestVersionVerifier` 改为必注入；`minimize` 的可变状态改为局部变量。

### 4.7 补 editor / apply 授权测试

`ProjectEditor` 只有 2 个测试（注释保留且幂等、workspace 批量 apply），却在 apply 时现场 `PackageReportBuilder().project(...)` 复核投影，并与 `environment.py` 分享 TOML 改写知识。apply 是唯一写用户 `pyproject.toml` 的路径。

**下一步：** 投影复核走注入的 Builder 实例；补漂移、不完整报告、recovery journal 的表驱动测试。

### 4.8 给 RunLogStore 独立测试面

`runlog.py` 914 行（journal、diagnosis-index、脱敏、Windows ACL）没有 `test_runlog.py`。行为寄生在 `test_process` / `test_diagnose` 上。`windows_runlog.py` 在非 Windows 跳过。

D007 / D008 的本机调查入口是产品差异点。改存储布局容易漏。

**下一步：** 以 `write_journal` / `replace_associations` / `lookup` 为唯一测试面，用临时目录测原子写和 index。不测 ctypes 细节。

### 4.9 收紧 Protocol，去掉 hasattr 探测

`persist_verification_journal` 在已声明 `JournalStore` 之后仍 `hasattr(logs, "write_journal")`。缺方法的假对象会 silently no-op，journal 丢失不会失败。`DiagnoseCommandWorkflow` 对 `read_latest_journal` / `lookup_run` 再做 `getattr` / `hasattr`。

`cli.build_context` 里 `diagnose_workflow` 单独 `ProjectLoader()`，不共享带 `pythons=uv` 的 loader。

**下一步：** 让 `RunLogStore` 满足一个完整 Protocol；`build_context` 复用同一个 `projects`。

### 4.10 收窄 TerminalPresenter 内部视图

1870 行实现 + 2938 行测试。D002 要求只有 `terminal.py` 创建业务 Rich renderable，这条约束保留。但对维护者，接口已经和实现一样宽。

**下一步：** 不要先重写视觉。先把 `render_explain` / `render_diagnose` 挪到同包私有模块，测试跟走。

### 4.11 Journal 改为 cell 完成后再写

`_JournalGate._persist` 每次失败都把整个 journal 重写。跨运行 Evaluation cache 是 D001 非目标，不要做。单次运行内的 O(n) 写盘是合法优化。

**下一步：** 延迟到 cell 完成或运行结束写一次。D008 允许本机工件。

### 4.12 产品范围继续守住 D001 §10

上界搜索、attribution、static-only、跨机 check、部分 apply、`--json` 都写在非目标里。工程债（4.1–4.9）比新能力更能降低后续产品成本。

若做产品，用户痛点更可能是：多宿主 merge 工作流、日志膨胀、GNU/musl 同 marker 投影失败、`project.dynamic` 元数据项目用不了。这些是保守失败面，不是算法野心。

文档上把 D001 §10 保持为拒绝清单，避免计划文档把非目标写成待办。

## 5. 已经很好、不必先动

- `failure.py`、`candidates.py`、`ProjectLoader.load`、`CoordinateSearch.minimize`、`EvaluationCache` 已经是深模块。
- `PackageFloorReportV1.validate_completion_authority` 把 complete-report 不变量压在 Schema 里。
- `cli.build_context` 作为唯一 composition root 符合 D002。
- Schema / terminal / process / project 测试护栏强。
- 设计文档所有权清楚，不要再写一份平行架构说明。

## 6. 不要当下一版产品做

D001 §10 的非目标不是半截实现：

- 传递依赖最小化或直接依赖笛卡尔积搜索
- 非单调区间细化、version hole 认证或自动 `!=`
- 不兼容上界发现
- failure attribution、partial tests 或测试用例选择
- 没有完整测试的 static-only floor
- flaky 自动重试
- `diagnose` 隐式重放或自动修复
- 跨运行 Proposal / Evaluation 环境缓存
- 非宿主平台执行
- 无法表示或不完整证据的部分 apply

其它 v1 边界同样保持：不支持 PyPy / free-threaded / debug；受管 marker 仅 `python_version` / `sys_platform` / `platform_machine`；不自动删除 `.pf/logs`；无 `--verbose` / `--json` / 本地化。

`ProcessSpec.summary_limit` 仍在 Schema 里。D007 说生产路径作废、只许测试注入。这是小债务，不是产品缺口。

## 7. 测试缺口摘要

| 源模块 | 主要测试 | 判断 |
| --- | --- | --- |
| `schemas/*` / `terminal.py` / `adapters/process.py` / `project.py` / `cli.py` | 厚 | 护栏扎实，terminal 维护贵 |
| Check / Diagnose / SearchCoordinator | 中 | 状态机仍有边角 |
| `CoordinateSearch` | `test_search.py` 6 个用例 | 薄 |
| `evaluation.py` | 6 + cache 3 | 薄 |
| `candidates.py` / `snapshot.py` | 5 / 4 | 薄 |
| `scheduling.py` | 3，含 `time.sleep` | 薄 |
| `editor.py` | 2 | 很薄 |
| `runlog.py` | 无 `test_runlog.py` | 缺口 |
| 端到端 | 2，真跑 `python -m pf`，`timeout=60` | 冒烟 |

全库几乎没有 `@pytest.mark.skip` / `xfail` / network。唯一 skip：`tests/test_windows_runlog.py` 在非 Windows。e2e 依赖本机 uv/ty，不是录制网络。

## 8. 勘误

初稿曾根据会话元数据写「工作区不是 git 仓库」。核验后作废：`/home/llh/pf` 是 git 仓库，`main` 跟踪 `origin/main`。门禁建议改为在已有远程上加 workflow，不必再初始化仓库。

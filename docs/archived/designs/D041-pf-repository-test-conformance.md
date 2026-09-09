# D041 — PF 仓库测试符合性整改

- **状态：** 已完成并归档；2026-09-09 通过 AC1–AC11 验收，稳定规则已由现行 owner 接管；实施与证据见 [P044](../plans/P044-pf-repository-test-conformance.md)
- **日期：** 2026-09-09
- **类型：** 已归档临时工程 Design；不再承担现行规范
- **目标 owner：** [D002](../../designs/D002-pf-implementation.md) §11、[tests/README.md](../../../tests/README.md)
- **验收标准：** [§8](#8-验收标准)
- **实施计划：** [P044](../plans/P044-pf-repository-test-conformance.md)
- **来源：** 2026-09-09 对照现行 `tests/README.md` 与 D002 §11 的仓库测试审查；同日独立 blind review（未读本文）交叉验证主要问题清单，并补 R1–R8
- **关联：** [D006](../../designs/D006-pf-cli-enhancement.md) 视觉规则不变，文案实现偏移仍见 [R010](../../reviews/R010-pf-engineering-document-audit.md) §2.1；[D012](../../designs/D012-pf-harness-relaxation.md) / [D013](../../designs/D013-pf-pytest-observer.md) 资格矩阵仍在资格车道；[D039](D039-pf-static-evaluation-module.md) 改静态公开表面，与本文件正交；车道调度已由归档 [D040](D040-pf-test-lanes.md) 吸收，本文件不重开车道

本文保存已完成的仓库测试符合性整改。用户要求实施后经 P044 规划与验收，稳定规则已归并 [D002](../../designs/D002-pf-implementation.md) §11 与 `tests/README.md`。正文保留迁移时的目标与理由，不再承担现行规范。

本文消除当时测试规范内部的种类张力，并把已偏离 D002 §11 / `tests/README.md` 的用例收回公开缝。D040 的三条消费者与 marker 集合保持不变。

## 1. 结论

D040 已经把日常、自举与 CI 的收集范围分开。当前缺口不在调度，而在**种类被拿去藏慢测**、以及部分用例不走公开缝：

- `qualification` 表头是 `qualify_*.py` 回放，正文却允许「昂贵真实进程矩阵」把产品 CLI / 真实 prepare+ty 的其余组合标成资格，从而退出 PR 门禁。资格矩阵是凭据刷新，不是第四条回归套件。独立审查按该句字面把嵌套 pytest 矩阵标成「应下沉资格」；本文方向相反。同一句被读成相反规则，故必须改写，不能留作矩阵分层依据。
- `e2e` 与 `process` 的边界按「是否多命令」划分，不如按「是否真实执行产品 CLI」清楚。`pyproject.toml` 的 e2e marker 描述仍写 `multi-command product path`，与 §3 不一致；保持不变的是 marker **名**，不是这段描述。
- 进程内用例直构 `PreparedEnvironment`、写入 `CliContext` 私有字段、进口 `_secure_runlog`、替身按 argv 识别 `ty check`，并把 schema 往返绑在真实 uv/ty 上。
- 负向用例数量大，但现行 owner 几乎只规定 fail-closed **行为**，不要求按字段组合枚举；同一规则在 schema 构造、CLI、真实进程上重复出现。

目标是一次说清四种产品证据各证明什么，按该分类改标记、拆断言、收回公开缝，并删掉过时/重复/非契约负向。不新增 marker，不改自举 `C`，不降低覆盖率门槛。

## 2. 范围与非目标

**接受并吸收后替换：**

| 目标 | 替换内容 |
| --- | --- |
| `tests/README.md` 种类表「资格」行、验证命令中的 `-m qualification`，以及「昂贵真实进程矩阵只在资格层展开」 | 改为 §3 / §3.3：资格只覆盖 qualify 脚本与 D012/D013 工具矩阵；矩阵仅在凭据变化时重跑；产品真实进程的额外维度回到进程内公开缝或 `process` 代表项 |
| `tests/README.md` 种类表「e2e」行 | 改为 §3：真实子进程执行产品命令（smoke/check/search/minimize/apply/explain/diagnose/merge） |
| D002 §11 资格句与公开缝句 | 明确资格不是产品 process 的溢流车道，也不是定期回归；公开缝禁令补上 `CliContext` 私有字段、`_secure_runlog` 产品进口、以及不 patch 产品私有函数（§4） |
| D002 `CliContext` composition（若 Plan 选用公开可选 workflow 参数） | 该参数是 D002 interface 变更，吸收时改 D002 正文（不仅 §11），不是测试专用 API |
| `pyproject.toml` `tool.pytest.ini_options.markers` 的 e2e / qualification **描述字符串** | 随 §3 / §3.3 更新；不改 marker 名 |
| `tests/README.md` 负向句与等待句 | 负向改为 §5.1；等待句改为 §5：完成等待与并发窗口不用固定睡眠 |

**保持不变：** D001 命令、退出码与 `test-command` 产品语义；`pyproject.toml` 的 **marker 名**集合（process/e2e/qualification/infra）、日常 `addopts` 表达式、自举数组与 CI `-m` 表达式；coverage `fail_under = 90` 的 canonical 并集规则；`e2e ⇒ process` 与 `SubprocessRunner` / spawn helper 守卫；D003 搜索；D012/D013 矩阵范围。marker **描述文本**不是这项「保持不变」的范围。

**与 D039：** D039 吸收后静态产品测试改走 Evaluator 表面。本文件先按现行 `StaticEvaluator.lookup/collect/compare` 与 `EnvironmentFactory.prepare` 整改；两份都吸收时，§11 同时保留 D039 表面句与本文件的种类/公开缝句。

**本文件不覆盖：** 重跑 `pf search` 或替换入库 `package-floor.json`；R006/R008；R010 §2.2 Journal Role；新增测试运行器或 xdist 默认值；为测试专用增加产品 API；沿 `tests/` 做 AST 漏标扫描。

## 3. 种类、矩阵与消费者

种类仍是 pytest marker。一条测试只标它所**证明**的种类。PR 仍是日常 ∪ `process` ∪ `e2e`（`-m "not qualification"`）。

| 种类 | Marker | 证明什么 | 典型去向 |
| --- | --- | --- | --- |
| 进程内产品 | 无 | 公开 module 的接口结果；schema/identity；CLI `create_app` / `main()`；recording / scripted adapter | 输入矩阵、schema 准入、比较、配置/调用错误 |
| 基建 | `infra` | 插件 hook、文档不变式、车道漏标安全网；必须直接驱动 `_secure_runlog` adapter 协议时也可标 | 不进自举 `C` |
| 真实进程公开缝 | `process` | 真实 uv / ty / nested pytest；`python -m pf` / 安装入口的 **help、调用错误、adapter 协议代表项** | 经 `TyAdapter` / `UvAdapter` / `ConfiguredVerifier` / spawn helper；不含产品命令路径 |
| 产品 CLI | `e2e`（且必须 `process`） | 真实子进程执行了 `smoke` / `check` / `search` / `minimize` / `apply` / `explain` / `diagnose` / `merge` 的产品路径（含该命令的产品级失败） | `-m e2e` 即全部真实产品 CLI；日常与自举仍排除 |
| 资格 | `qualification` | `scripts/qualify_*.py` 的工具协议 / 版本矩阵；committed manifest 由未标记测试对照现行 protocol 常量 | 仅在 §3.3 的凭据变化时重跑脚本；不进日常 / PR；不用于产品 Environment/Static/CLI 组合 |

`e2e` 仍是 `process` 的子集。帮助、未知 option、非法 duration 等**调用表面**走进程内 `create_app`；若必须证明入口子进程（`--help` 列宽、console script 与 `python -m pf` 一致），标 `process`、不标 `e2e`。配置错误（未知 `--package`）同样走 `create_app`，不因命令名叫 `check` 就升为 e2e。

输入矩阵规则：

- 同一公开接口的不同输入，在**进程内**用 `parametrize` 展开（含分辨率、测试组、产品命令名）。
- 真实进程每种 adapter 协议保留代表项；产品命令真实子进程按 §3 标 `e2e`，每种命令**至多**一条代表项，不要求八个命令各有一条。其余维度用 scripted/recording 覆盖。
- 有先后依赖的缓存复用和多命令生命周期保留为连续场景，标 `e2e`。

### 3.1 其余组合：耗时与是否全进进程内矩阵

2026-09-09 本机、已有 uv 缓存、`--no-testmon`，只跑当前误标为 `qualification` 的 13 条产品组合：**13 passed in 81.48s**。该墙钟只说明「不要把这 13 条原样改标进 PR」，**不是** S2/AC 的交付证据。交付以 CI PR 门禁 `pytest --no-testmon -m "not qualification"` 相对整改前的 collect 规模与实测墙钟增量为准。

| 组 | 项数 | 墙钟 | 证明什么 |
| --- | --- | --- | --- |
| `TestRealStaticRequestQualification` | 4 | 约 21s（3.5–6.4s/项） | lowest-direct / exact-vector / external-stub / relocation，真实 uv+ty |
| `TestNonemptyStaticPreparationQualification` | 3 | 约 32s（4.6–16.2s/项） | 非空 harness 下其余分辨率；`exact-vector`+secondary 最贵，循环多次真实 prepare |
| `test_other_commands_verify_project_only_environment` | 6 | 约 28s（3.0–5.9s/项） | 真实 `pf smoke`/`check`/`minimize` × 缺失/空测试组 |

这 13 条贵，是因为每条都做真实 uv resolve/install 和/或完整 CLI，不是因为组合语义本身贵。`evaluation_assembly` / `ScriptedUv` / `ScriptedStaticRequests` 已能区分 highest / lowest-direct / exact-vector；`test_static_comparison.py`、`test_environment.py`、`test_smoke.py`、`test_check.py` 已覆盖对应公开缝。

因此：

- **合适全量加入进程内矩阵**的，是分辨率、测试组、命令名、admission/比较这些语义维度。改写后是毫秒级 `parametrize`，不是把这 13 条真实进程再跑一遍。
- **不合适**把这 13 条原样改标为 `process`/`e2e` 留在 PR 上（本机串行约 +81s，仅作诊断；门禁增量见 S2）。
- **真实代表项**维持少量：已有 highest 的 `TestRealStaticRequest`、`TestNonemptyStaticPreparation`；CLI 已有 search 与 search→explain→apply。Plan 只在 scripted 无法证明的协议上加代表项（候选：真实 ty extra-paths 冻结、relocation 缓存各一条 `process`；`pf check` 若进程内 workflow 不能代替 composition root，至多一条 `e2e`）。smoke/minimize 的真实重复删除。

已知偏离（Plan 首切片必须逐条给出去向：改标 / 拆到进程内 / 删除重复）：

| 现状 | 目标 |
| --- | --- |
| `test_end_to_end.py` 真实 `pf search` / 多命令生命周期 | 保持 `e2e`（已符合 §3） |
| 同文件 `smoke`/`check`/`minimize` 标 `qualification` | 命令×测试组矩阵进进程内 workflow；删除真实重复，或至多留一条 `pf check` `e2e` |
| `TestApplyAuthorizationDriftAndCliRoundTrip` 等已 `process` 的 apply/explain | 补标 `e2e`（已在 PR 内，只改种类） |
| `TestRealStaticRequestQualification`、`TestNonemptyStaticPreparationQualification` | 语义矩阵拆到未标记公开缝；真实项不按分辨率展开；stub/relocation 至多各留一条 `process` |
| `test_static_journal.py` 12 条 reader 拒绝（`invalid-static-evidence` 9 + `undecodable` 2 + `unsupported-contract` 1）绑 `actual_static_journal` | 拒绝收回未标记，用冻结 journal 文本；同 fixture 的 persist / producer-log 索引留 `process` |
| `test_unknown_package_is_a_configuration_error` 标 `process` | 未标记，走 `create_app` |
| `python -m pf --help` / 非法 option 标 `process` | 保持 `process`，不标 `e2e` |

### 3.2 进程 / e2e / 资格：重复效果与精简

2026-09-09 `--no-testmon --collect-only`：`process and not e2e and not qualification` **176**；`e2e` **4**（全在 `tests/test_end_to_end.py`）；`qualification` **22**。

三条车道证明的不是同一事实，因此不能互相替代：

| 车道 | 应证明 | 不能用来代替 |
| --- | --- | --- |
| 资格 | `qualify_*.py` 的工具协议 / 版本矩阵与 committed manifest | 产品 Environment、Static、CLI 组合 |
| 进程 | 真实 uv / ty / nested pytest / `SubprocessRunner` / 入口 `--help` | 输入矩阵、reader 拒绝、schema 往返 |
| e2e（⊂ 进程） | 真实子进程执行了产品命令 | adapter 协议本身、资格矩阵 |

重复效果来自两类偏离：同一产品路径标错车道；同一协议已有进程内矩阵，却再做真实展开。

**跨车道重复（与 §3.1 同一批）：**

| 现状 | 效果重复于 | 处理 |
| --- | --- | --- |
| 13 条产品组合仅标 `qualification` | 已有 highest `process` 代表项 + 进程内 workflow / 比较矩阵 + 4 条 search e2e | 语义进进程内；真实侧不按分辨率/命令展开 |
| `test_authorization.py` 6 条 `run_pf_cli("apply"\|"explain")` 只标 `process` | 产品 CLI 路径，按 §3 应同时 `e2e`；与生命周期 e2e 的 apply 不同（预写报告 vs search 产物） | 补标 `e2e`；force/member-version 维度能由 `TestApplyAuthorizer` 覆盖的不再真实展开 |
| `test_unknown_package_is_a_configuration_error` | 进程内 CLI 配置错误矩阵 | 收回 `create_app` |
| e2e `search` × 缺失/空测试组 | `_verify_project_only_cli` 同一装配；组是否存在是规划事实 | 只留一条真实 `search`；另一组进进程内 |
| `scripts/measure_d038_guidance.py` 标资格 | `tests/test_static_guidance.py` 已在公开 evaluator 缝比较 guided/mechanical；该脚本本身也只驱动 fixtures，不跑真实 uv/ty | 退出资格；有缺口则并入未标记 guidance 测试，否则删除回放 |
| `qualify_static_guidance.py --mode controlled` | 真实 prepare+ty+verifier 与 journal persist，重叠 `TestStaticJournal` / `TestRealStaticRequest` 的 process 代表项 | 资格只保留「guidance 资格 profile 仍 PASS」一条；不另开第二套真实 prepare |

**资格里应保留、与 process/e2e 不重复的**是凭据本身，不是一套常跑的 pytest：`qualify_uv.py`、`qualify_uv_workspace_sources.py`、`qualify_execution_failures.py`、`qualify_pytest_observer.py`（及 D013 的 pruning 脚本）。日常只跑这些文件里**未标记**的 manifest 对照（protocol / profile / case 名仍与代码常量一致）。`pytest -m qualification` 若仍包装脚本回放，只作为 §3.3 刷新时的可收集入口，不是卫生套件；Plan 也可改为直接调用脚本、让该 marker 下不再挂昂贵回放。

D013 还要求 `scripts/qualify_pytest_pruning.py` 的版本/xdist 矩阵，仓库 **没有** pytest 回放；`tests/test_pytest_pruning.py` 的 21 条 `process` 正在补偿。Plan 要么在凭据刷新作业里跑该脚本并收缩 process 代表项，要么保持少量当前-pytest process 并把该脚本留在资格作业外；不要两边都厚。本文件不把「现在补上 pruning 全矩阵」写成新验收，以免把 D013 矩阵灌进 PR。

**进程车道内部可收回进程内的（仍标 `process` 就会在 PR 上付真实进程成本）：**

| 簇 | 约项数 | 为何重复或不必要 | 留下的真实代表项 |
| --- | --- | --- | --- |
| `test_static_journal.py` reader 变异 | 12 条拒绝（9+2+1）；同 fixture 另 2 条 persist/index | fixture 已付真实 uv+ty；之后只改 JSON。与 §4「schema 不绑真实 uv/ty」同一规则 | persist / 字节稳定 / producer log 索引留在 process；拒绝收回未标记，用冻结 journal 文本 |
| `TestRealTySearchPaths` / `TestRealTyGlobalIgnores` / `TestRealTyConfigurationEquivalence` / 非法 project config / terminal defaults | 约 8 | 路径/ignore/config 解析器已有未标记测试；若干手写 `ty check` argv | extra-paths 冻结、global-ignore 冻结、非法 config 各至多一条，经 `TyAdapter` |
| `TestPytestObserverSummaryFaults` 等 | 50 中约 16 条 summary fault × exit | 嵌套 pytest 写出协议文件后再破坏；破坏矩阵可用预制 artifact + recording | pass / fail / xdist / nested / 不改写 interrupt 与 usage 各一条 |
| `TestFailedCasePruning` lastfailed 三旗 + `TestPruningCollectionAuthority` 2×2×2 | 21 中约 11 | `--lf/--ff/--sw` 与 collection/summary 资格正交组合；recording 已覆盖 artifact 失败回退 | 原命令 additions、failed-set 跳过原命令、动态 collection 回退、xdist 无 controller 各一条 |
| `test_static_inputs.py` 整文件 `process` | 1 | `with_dependency = False` 使依赖分支死代码；capture 协议可在 highest prepare 代表项上断言 | 并入已有 prepare 代表项或留一条；删除死分支 |
| `test_static_report.py` 真实 search persist + 字节稳定 | 2 | schema validate 与 compare replay 已有 scripted 报告测试 | 一条「真实 search 写入新 scope 且 roundtrip」 |
| `test_cli.py` 三条 help | 3 | 命令列表与 120 列可同一 `--help` 子进程；console script 对 `python -m pf` 是另一入口协议 | 合并列表+列宽；保留 console script 一条 |
| `test_process.py` | 56 | `SubprocessRunner`/redact/`killpg` **就是**进程缝，与资格不重复。timeout / interrupt / 孙进程可酌情合并变体，不是跨车道重复 | 保留安全目录、超时杀组、中断、redact 代表项；不把 RunLogStore 纯 JSON 拒绝继续绑在真实子进程上（若某条只改索引文件） |

**e2e 扩集后的预期规模（AC1）：** 当前 `-m e2e` 仅 4 项。整改后预期：

| 去向 | 项数 |
| --- | --- |
| 必留：start-failed `search`、一条 project-only `search`、search→explain→apply | 3（现 4 项去掉一组 search） |
| 改标并入：`test_authorization.py` 已 `process` 的 `run_pf_cli` apply/explain | 当前 collect 6；§3.2 允许收缩 force/member-version 后 ≤6 |
| 可选：至多一条 `pf check` | 0 或 1 |
| 排除 | §3.1 的 13 条资格产品组合；`--help`/调用错误 |

上界 **3+6+1=10**。交付证据是 `pytest --collect-only -m e2e` 的 nodeid 集合对照该表，不是「e2e 变多即合格」。

### 3.3 资格是凭据刷新

资格矩阵证明「这套工具版本 + protocol/profile 仍认证这些 case」。它不是产品回归，也不替代 PR 上针对**当前 pinned** uv/ty/pytest 的少量 `process` 代表项。

日常与 PR 只核对照 committed manifest 是否仍匹配代码里的 protocol / profile / case 名（未标记测试）。重跑 `scripts/qualify_*.py`（或等价的 `-m qualification` 回放）只在凭据变化时：

| 触发 | 重跑什么 |
| --- | --- |
| 更换或扩展受支持的 uv 版本 | `qualify_uv.py`、workspace sources、execution-failure 证据；并更新 D012 allowlist / profile / classifier 测试 |
| 更换或扩展 observer/pruning 所覆盖的 pytest 版本，或改 observer 协议 | `qualify_pytest_observer.py`；pruning 用 `qualify_pytest_pruning.py` |
| 增加或改写矩阵 case、认证为 UNSAT 的范围、diagnostic profile | 对应脚本 + manifest |
| 改 ExecutionPolicy 中由资格凭据绑定的字段 | 受影响的 uv/execution 资格 |

不触发全矩阵重跑的：普通产品 PR、日常 pytest、「好久没跑资格了」。CI 保持 `-m "not qualification"`，本文件不增加定时资格 job。

**ty：** 仓库没有 `ty × 版本 × case` 资格矩阵（现行是 pinned ty 上的 `process` 代表项 + recording 解码）。更换 pinned ty 时跑那些代表项（已在 PR 的 process 车道），不在本文件发明 `qualify_ty.py`。若以后增加 ty 诊断矩阵，同样只在该凭据变化时重跑。

D012 已写「更换 uv 版本必须更新精确 allowlist、profile、qualification evidence」。吸收后 `tests/README.md` 的验证命令把 `-m qualification` 标成凭据刷新，不再与 process/e2e 并列成定期套件。

## 4. 公开缝

所有车道继续走 D002 §11 的公开表面。吸收后 §11 在现有三句禁令之外写明：

- 不直接构造 `PreparedEnvironment` 成功值；relocation 经 `EnvironmentFactory.prepare` 或现行公开 relocation 入口。
- 不写入 `CliContext._check_workflow` 一类私有字段。进程内 CLI 经 `create_app` 与公开 property。若当前构造器无法注入替身 workflow，Plan 二选一：**property 级替换**（仍只走公开 property，不写 `_` 字段）；或给 `CliContext` 增加**生产可用**的可选 workflow 参数。后者是 D002 的 `CliContext` composition / interface 变更，吸收时必须改 D002 正文（不仅 §11）。二者都不是测试专用 API。
- 产品测试不进口 `pf._secure_runlog`。安全目录行为经 `RunLogStore` 与 `pf.windows_runlog`。必须直接驱动 POSIX/Windows adapter 协议的用例标 `infra`。
- 产品测试不 `monkeypatch` / patch `pf` 包内私有函数（名以 `_` 开头或仅模块内可见）。现行偏离：`tests/test_pytest_progress.py` 三处 patch `pf.adapters.pytest_progress._read_progress`。进度协议经 `PytestProgressMonitor` 已监视的公开文件/artifact 驱动，或经该类型的公开构造入口；不把私有读函数当成测试缝。
- 真实 ty 进程测试经 `TyAdapter.observe`（及其公开装配），不在测试里手写 `ty check` argv。`decode_process` 的纯解码矩阵用 recording 的 `ProcessResult`。
- 替身响应请求里已经分类好的环境/配置/outcome。包装真实 `SubprocessRunner` 的 recording 不按 argv 识别 `ty check`。当被测物本身是 adapter 且 seam 就是 `ProcessSpec` 时，runner 替身可以按 spec 返回预制结果。

`CoordinateSearch` 与产品编排器仍用生产实现；下层按车道替换。

## 5. 断言

`tests/README.md` 断言惯例作如下收紧，不另开产品规则：

- 纯 schema / admission / 比较断言只出现在未标记车道。`process` 只断言真实进程协议、退出、缓存/关闭与持久化产物中由该协议产生的字段。
- specifier 与 marker 比较 `SpecifierSet` / `PortableMarker` / `Version in specifier`，不比较库的格式化字符串。
- 视觉断言跟现行 D006。`NO_PASS_IN_SEARCH_SPACE` 不得再锁「fully evaluated / 完整评估」。该项与 R010 §2.1 的代码修复同一 Plan 交付；不把错误文案继续写成期望。
- 完成等待与并发重叠窗口都不用固定睡眠。现行偏离：`tests/test_evaluation.py` 两处、`tests/test_check.py` 一处用 `time.sleep(0.05)` 制造并发窗口。改用 `Event` / barrier 或许可队列上的可等待同步。被测子进程 argv 里的 `sleep`（超时/杀组负载）以及 `Event.wait(timeout=…)` 不是这条违规。
- `parametrize` 的 **ids 判定**：参数值本身已是可读语义（命令名、`--lf`、字符串 case id）时，pytest 自动 id 可保留；**元组、布尔、整数代码**必须显式 `ids=`。现行反例：`TestPruningCollectionAuthority` 三层布尔展开为 `[False-False-False]`。本整改结束前按此规则补齐现有缺口，不只约束新增用例。

### 5.1 过时、重复与负向

现行 owner 把 fail closed 写成**产品行为**（D014 reader 步骤、D006 调用错误格式、D001 未知配置键、D007 不安全目录）。它们不要求「每个非法 JSON 字段一条测试」，也不要求 schema 构造、`create_app` 与真实 `pf` 子进程各测一遍同一拒绝。

负向测试**必须**留下的，是现行契约的错误或安全规则在**一个**公开缝上的代表项：

| 保留 | 例子 |
| --- | --- |
| D014 reader 每一步的一类失败 | 非 v1、额外字段、未知/重复 ref、不可达、identity 漂移、merge 冲突 |
| D006 规定的调用/配置错误形态 | 未知 option、非法 duration 文案、diagnose Usage、配置错误无 Usage |
| D001/D012 准入与安全 | `uv run` 前缀、空 test-command、互斥 managed/unmanaged、不安全路径、凭证绑定 |
| 车道安全网 | 未标记进入 `SubprocessRunner.run` / `run_pf_cli` |

**删或合并**（Plan 首切片按条给出）：

| 种类 | 现状 | 处理 |
| --- | --- | --- |
| 过时语法枚举 | `test_config` 里 `package = {}` 与 `surprise = true` 同属未知键；`package` 不是现行 `[tool.pf]` 键 | 只留一条未知键 |
| 同规则多缝 | `SearchRequest`/`CheckRequest`/`SmokeRequest` 三套非法 scheduling；CLI `--ty-jobs nope` 已覆盖 D006 调用错误，Request Schema 在 D006 是 defense-in-depth | 命令 Request 合成一个 `parametrize`；CLI 保留一条调用错误 |
| 同规则多层 | `test_schemas` 直构 `RequirementDeclaration` 拒非规范名，`test_report_schema` 再拒 Cell/PackageIdentity；reader 漂移测试已覆盖 identity 闭合 | 领域 schema 各类型一条安全代表项；wire 漂移留在 `ReportStore.read` |
| 真实进程重复 | §3.1 的 13 条产品组合；`smoke`/`check`/`minimize` × 测试组；`tests/test_static_journal.py` 12 条 reader 拒绝绑真实 uv/ty fixture；§3.2 的多余真实 ty / observer summary-fault 矩阵 / pruning 正交组合 / 双组 search e2e / D038 measure 资格回放 | 语义与 reader 拒绝进进程内（journal 用冻结文本）；真实侧只留代表项 |
| 固定睡眠 | `test_evaluation.py`：`test_ty_permits_bound_concurrent_public_static_evaluations`、`test_test_permits_bound_concurrent_public_runtime_evaluations`；`test_check.py`：`test_check_workflow_runs_host_cells_in_parallel`。各一处 `sleep(0.05)` | 改为事件或有上限的 wait；不删并发上限这条产品断言 |
| 视觉锁死 | `test_terminal` 把 Rich 画布空格/折行写成期望；D006 只固定 PF 拥有的文案、通道与图标规则，「实际换行由终端宽度决定」 | 精确断言固定文案与语义字段；宽度只保留「必要字段仍可读」类，不锁库排版 |
| 迁移名 | `test_read_rejects_a_legacy_report_missing_apply_identity` | 现行 D014：缺 `pyproject_identities` / `source_plan` 必须拒绝。保留行为，去掉 legacy 叙事 |

`test_report_schema` 里大量 `read_rejects_*` **不是**一律可删：它们多数对应 D014 §2–§3 的不同 identity/ref/可达性规则。精简时按规则去重，不按「负向就删」。

过时正向重复同样删：进程内 workflow 已证明的命令路径，不再保留第二条真实 CLI；qualification 回放不代替、也不复制公开缝。

## 6. 组织惯例

- 产品测试方法写在 `Test<Subject><Aspect>` 内。`test_static_comparison.py` 的模块级 `test_scripted_pair_*` 收回类中。
- 方法名保持 `test_<interface>_<outcome>`。
- `tests/history.md` 仍只是注明日期的记录，不因本文件改写历史计数。

## 7. 建议切片

实施顺序与去向表见 [P044](../plans/P044-pf-repository-test-conformance.md)。本文件不重复切片证据。

## 8. 验收标准

| AC | 必须成立的目标 | 公开 seam / 证据 |
| --- | --- | --- |
| AC1 | 无产品 CLI/Environment/Static 用例仅标 `qualification`；凡真实子进程执行产品命令的用例同时标 `e2e` 与 `process`；`--help`/调用错误不标 `e2e`；昂贵 `qualify_*.py` 回放仅标 `qualification` 且不进 PR；未标记测试对照 committed manifest 与现行 protocol 常量；`-m e2e` 的 nodeid 集合符合 §3.2 预期规模表（上界 10，排除 13 条资格产品组合） | Plan 去向表；`pytest --collect-only -m qualification` 与 `-m e2e` 的 nodeid 集合 |
| AC2 | 测试树不出现 `PreparedEnvironment(` 成功值直构；产品测试不进口 `pf._secure_runlog`；不赋值 `CliContext._*_workflow`；产品测试不 patch `pf` 包内私有函数（含 `_read_progress`） | 测试源码扫描 + 公开 CLI/runlog/进度测试仍通过 |
| AC3 | 包装真实 runner 的 recording 不以 argv 识别 ty；真实 ty 进程测试经 `TyAdapter` | `tests/test_static_request.py` 与 `tests/test_ty_adapter.py` 的 process 项 |
| AC4 | schema 往返、admission、slice/global 比较均有未标记公开缝覆盖；`test_static_journal.py` 的 12 条 reader 拒绝不再绑真实 uv/ty fixture；对应真实 uv/ty 用例不再重复这些断言 | `test_static_comparison.py` / journal 未标记类；原 Qualification 类去向 |
| AC5 | `parametrize` 符合 §5 ids 判定（自动 id 已语义化可保留；元组/布尔/整数代码必须显式 `ids=`）；无模块级 `test_*`；specifier 断言不依赖 `str(specifier)` 的库格式 | 测试源码；失败项 id 可读，无 `[False-False-False]` 类 nodeid |
| AC6 | 日常 / 自举 / `process and not qualification` / `qualification` 四次 `--collect-only` 与 §3 一致；自举仍排除 infra/process/e2e/qualification；addopts / 自举数组 / CI `-m` 表达式与 marker **名**集合未改；e2e/qualification marker **描述**符合 §3 / §3.3 | nodeid 集合；`pyproject.toml` |
| AC7 | `NO_PASS_IN_SEARCH_SPACE` 展示与测试符合现行 D006；R010 §2.1 关闭或明确仍开放的仅剩项 | terminal/explain 测试；D006 正文不改规则 |
| AC8 | 吸收后 D002 §11 含 §3 种类句、§3.3 刷新句与 §4 公开缝句；若选用公开可选 workflow 参数则 D002 `CliContext` composition 同步更新；`tests/README.md` 种类表、矩阵句、负向句、等待惯例与验证命令中的资格行与 §3、§3.3、§5 一致 | owner 正文 |
| AC9 | D001 正文无因本文件产生的改写；自举 `C` argv 不变 | `D001` diff 在本变更范围内为空；`tool.pf.test-command` |
| AC10 | 合并前后已覆盖源码行与分支集合不丢；canonical 覆盖率门槛仍为 90 | 覆盖率报告；不只百分比 |
| AC11 | 留下的每条负向测试都能指出一条现行 owner 的 fail-closed 规则；§5.1 / §3.2 表中的过时键、同规则多缝、跨车道产品重复、进程内可收回的真实展开、`test_static_journal.py` 12 条拒绝、3 处固定睡眠与视觉锁死已删除或合并 | Plan 去向表；`pytest.raises` / 拒绝用例与 owner 对照 |

停止条件（任一成立则未交付）：继续用 `qualification` 藏产品真实进程组合；把 13 条其余真实组合原样改标进 PR；把资格全矩阵排进 PR 或写成定期卫生套件；产品命令真实子进程不标 `e2e`，或把 `--help`/调用错误标成 `e2e`；`-m e2e` 超过 §3.2 上界或把 13 条资格组合改标进来；直构 `PreparedEnvironment`、写入 `CliContext` 私有字段或 patch 产品私有函数仍作为惯例；schema 断言仍绑在真实 uv/ty 上（含 `test_static_journal.py` 12 条拒绝）；为整改新增测试专用产品 API；改自举 `C` 或降低 `fail_under`；只改标记不拆缝；只改测试不修 R010 §2.1 却声称视觉断言已符合 D006；用固定睡眠推断完成或制造并发窗口；为凑覆盖率保留第二条相同拒绝，或把 D014 reader 的不同 identity 规则当成重复删掉。

## 9. 明确不做

- 第四种产品分类名或给近两千项进程内测试加正标记。
- 把资格矩阵排进 PR、定时 CI 或「每周卫生」；本文件不为 ty 发明版本矩阵。
- 把 nested pytest 代表项因「昂贵」整批下沉 `qualification`（现行 README 歧义句的误读）。
- 把 recording 测试改称为已资格化的 uv/ty 协议证据。
- 用全局 `subprocess`/`Popen` 补丁扩大漏标门禁。
- 把 AGENTS.md 写成种类表副本。
- 重开 D040 的消费者/命令讨论，或把旧 `package-floor.json` 写成新 `C` 的 floor。
- 把 D014 各条 identity/ref/可达性拒绝当成「负向过多」一次性删掉。

## 10. 接受状态

已完成并归档。2026-09-09 接受全文（含 §3.2 精简、§3.3 凭据刷新与 R1–R8）。实施按 [P044](../plans/P044-pf-repository-test-conformance.md) 完成；稳定规则已吸收进 D002 与 `tests/README.md`。

# R001 — PF v1 仓库评审

- **状态：** 快照，已按独立复核与实施前终审修订
- **日期：** 2026-08-22
- **性质：** 非规范性评审；不定义命令、算法、Schema 或模块接口
- **对照：** 当时 `main` / `d1b8614`（`feat: implement D006, D007, D008`），远程 `origin` 为 `git@github.com:BigTailFox/pf.git`
- **契约所有者：** [D001](../designs/D001-pf.md)–[D008](../designs/D008-pf-verification-run.md)；整改与重构以 [D009](../designs/D009-pf-v1-refactor.md) 为准

本文记录一次对照现行契约与源码的架构评审，回答「v1 主路径落地之后下一步修什么、再优化什么」。它不取代 D001–D008，也不把 D001 §10 的非目标改写成待办。行数与测试计数来自同日对 `src/pf`、`tests` 的统计。

初次评审之后，又启动了一轮不注入初次发现的独立仓库 review。独立 review 重新读取源码、契约和测试，并单独运行全量测试、`ty check src` 与构建。本文已把经源码交叉核验的结果合并进同一快照。

## 1. 结论

D001–D008 的命令、主要状态机和展示主路径已经落地，但不能再写成「已全部落地」。现行实现至少有五个高风险契约缺口：流式脱敏可在 Process Log / Output Cache / 终端末行留下跨分块明文；CandidateSnapshot 的 artifact/hash 没有约束实际 probe 安装；complete report 没把 `final_vector` 绑定到 PASS Proposal；apply 写后校验失败不回滚；workspace 重名包会在 workflow 中静默覆盖或混合。

流式缺口打在本机诊断和终端，不进入 `package-floor.json` 的 stdout/stderr 正文。公共报告另有残留凭据面：`FailureDetail.message` 和未剥离 query 的 artifact locator。

这些是当前契约的实现缺口，不是新产品范围。它们优先于模块加深、文件拆分和新工程能力。

仓库约 13,473 行源码、16,523 行测试。全量 `pytest --no-testmon` 为 522 个用例，`ty check src` 与 sdist/wheel 构建通过。现有门禁能证明主路径稳定，但没有覆盖上述跨 seam 的保密性、证据绑定、恢复和 workspace identity。

架构税仍集中在 `workflow.py`、`search.py`、`terminal.py`，但修复顺序必须是：先收紧证据和写入授权，再加深模块，最后处理展示内部结构与 I/O 优化。

## 2. 高风险契约缺口

### 2.1 流式脱敏跨分块泄露

`SubprocessRunner._redact_stream` 保留 overlap 后，把已确定前缀和 pending 尾部分别交给 `SecretRedactor.redact`。若 secret 或 URL userinfo 横跨两段的切点，两次调用都看不到完整值。同一份脱敏文本同时进入 Output Cache、Process Log，以及 live 卡片的末 3 行。

生产 `cli.build_context` 构造 `SubprocessRunner(listener=..., logs=...)`，不注入 credential literals；正常装配主要依赖 URL userinfo 正则，不能覆盖工具输出中的裸 token/password。overlap 在无 secrets 时默认 256 字节。

`.pf/` 在 `.gitignore` 里，也不进源码快照，但不是用完即删的临时目录：v1 不自动删除运行日志，`diagnose` 会打开这些文件，CI 和工作区拷贝常会带走它。同一条流还进终端，所以不能因为「本机文件」省略脱敏。

`package-floor.json` 的 `ProcessResult.stdout` / `stderr` 标了 `exclude=True`，公共报告不含进程输出正文。流式 overlap 缺口不构成 floor 报告泄漏。

**影响：** 违反 D001 / D007 对日志和终端的凭据承诺。

**下一步：** 流式脱敏必须在任意 byte 分块下与一次性脱敏等价。composition root 只注入本次运行实际装入的 `RegistryAccess` literals 和 `ProcessSpec.environment` 值，不扫描环境变量。secret 不进入 Schema、日志或 identity。测试覆盖 64 KiB 临界点前后、UTF-8、多 secret、URL userinfo，以及 cache / log / listener / 终端四个观察面。

### 2.2 Candidate artifact 与实际安装脱节

`CandidateBuilder` 为每个版本选择并冻结一个 `AvailableArtifact`，保存 filename、kind、locator 和 hash。Probe 只把受管声明改为 `==version`，随后执行普通 `uv pip install --editable`；安装命令没有消费已选 artifact、hash 或 distribution kind。

**影响：** 报告可声称候选来自某个 wheel/sdist，实际验证同版本的另一个 artifact；索引变化、私有索引或同版本多构件会破坏可重复性和证据含义。

**下一步：** exact-vector probe 必须从 CandidateSnapshot 唯一映射到 artifact selection，并按 locator + hash 安装，禁止 resolver 改选同版本其他构件；安装后核验实际图与 selection。公共报告不需要新增第二套候选模型，但 CellResult validator 必须验证 vector、candidate 和 PASS Proposal 的闭环。

### 2.3 Complete report 未绑定真实 PASS vector

`CellSuccess` 会检查 final Proposal ID 出现在 observation 中，却没有要求：

- `final_vector == final_evaluation.proposal.managed_vector`；
- `final_vector` 等于 terminal coordinate search 的成功向量；
- 对应 observation 的 vector / Attempt 与 final Evaluation 完全一致。

`PackageFloorReportV1.validate_completion_authority` 随后把可独立修改的 `final_vector` 当作已验证 floor，并只检查 projection floor 与它相等。

**影响：** 修改 report 的 `final_vector` 和 projection floors 后，可以保留另一份真实 PASS Evaluation 并通过 Schema，从而让 apply 写入未经验证的版本。

**下一步：** 在 `CellSuccess` 一次性绑定 terminal search、final vector、ProbePass Attempt、Proposal managed vector 与 final Evaluation；complete validator 只消费这个已经闭合的结果。新增只改 vector、只改 observation、只改 Proposal、只改 projection 的防篡改矩阵。

### 2.4 Apply 失败不回滚

`ProjectEditor.apply` 先把目标 TOML 原子替换，再重新解析并调用 `ProjectLoader`。写后校验失败时只抛 `ConfigurationError`，没有恢复 backup。下一次 `_recover` 只要当前 digest 等于 original 或 target，就直接把 journal 标成 `COMMITTED`；目标内容不会重新验证，也不会回滚。

`apply_many` 逐包调用 `apply`，后续包失败时前面已经提交的包不会回滚，workspace 可进入半应用状态。

**影响：** CLI 报告失败但用户元数据已改变；重试还可能把未经确认的 target 当作已提交。

**下一步：** `apply_many` 成为 workspace 级事务 interface：先为全部文件计算和验证目标，再统一写 recovery journal 和 backup；任一写后校验失败恢复全部已替换文件并 `fsync`。重启恢复遇到 target digest 时保守回滚，不能直接提交。

### 2.5 Workspace 重名包

`ProjectLoader._discover_packages` 按 path 去重，但不拒绝两个成员使用相同 canonical distribution name。Check 随后建立 `{package.name: package}`，后者覆盖前者；Search 按 package name 聚合 cell results，也会把同名成员证据混在一起。

**影响：** 一个成员可能没按自己的配置或源码验证，却得到另一个成员的结果或报告。

**下一步：** package discovery 在 selection 和 planning 前拒绝 canonical name 重复，并列出全部冲突路径；所有 workflow 继续把 package name 当唯一 key，但不再负责兜底检测。

## 3. 中风险实现缺口

### 3.1 离线读取命令仍会做完整项目规划

`explain` 使用带 `pythons=uv` 的完整 `ProjectLoader`。未显式配置 Python minor 时，单纯定位现有报告也会启动 `uv python list` 并写 Process Log。现行 `diagnose` 用单独的 `ProjectLoader()` 避开了外部进程；若把它改成复用同一个 `projects`，会直接违反 D001 / D008 的严格离线读取契约。

**下一步：** 抽出只负责 workspace/member discovery、selection、canonical name 唯一性和 report path 的 `ProjectDiscovery`。ProjectLoader 在它之上做 config/cell planning；Explain / Diagnose 只依赖 discovery，不依赖 Python provider、uv 或 SnapshotBuilder。

### 3.2 多包 Journal 顶层 policy identity 不成立

Verification Journal 可以包含多个 package，但 `persist_verification_journal` 只写 `packages[0].config` 的 policy identity。D001 允许 root override 和成员 `[tool.pf]`，同一次 workspace 运行可以有多套有效策略。

**下一步：** Journal 保存按 canonical package 排序的 `(package, evaluation_policy_identity)`，不再用单个顶层 identity 代表整个运行。FailureRecord scope 仍保留自身 identity。

### 3.3 私有 registry 的运行时凭据丢失

ProjectLoader 正确地从可移植 SourceIdentity 中删除 URL userinfo/query，但 Candidate query 随后直接对公开 locator 发起 HTTP 请求，没有可单独注入的运行时 credential，也不复用 uv 的认证能力。

**影响：** baseline 安装可通过 uv 登录私有索引，候选发现却对同一来源返回 401。

**下一步：** 明确区分可序列化 `SourceIdentity` 与进程内 `RegistryAccess`；credential 只交给 registry adapter 和 redactor，不进入报告、日志或 identity。

### 3.4 Registry adapter 对畸形 JSON fail open 到 traceback

`Content-Length` 直接 `int()`；`files` 元素、`hashes` 和 `requires-python` 没有在 adapter seam 完整验证。例如 `files=[null]` 会抛 `AttributeError`，非法长度会抛 `ValueError`，没有包装成 InfrastructureError / Indeterminate。

**下一步：** 在 registry adapter seam 验证完整 PEP 691 输入形状，把 `ValueError`、`TypeError`、`AttributeError` 和解析错误统一转为受控 infrastructure failure。

### 3.5 静态 PASS 环境生命周期过长

`_ProposalRunner` 在静态 PASS 后保留 PreparedEnvironment，直到整个 cell 搜索结束；大量候选和并行 jobs 会同时保留源码副本、venv、inode 和文件句柄。

**下一步：** 静态阶段只缓存 Evaluation；非最终环境立即关闭。确定 final vector 后重新 prepare 一次做 full evaluation。该策略允许重复环境准备，但不重复相同 context 的完整测试。

### 3.6 公共报告的残留凭据面

`package-floor.json` 不保存 stdout/stderr 正文，但下列 portable 字段仍可能带出凭据：

- `FailureDetail.message`：部分路径写入 `process.diagnostic()`（含缓存里的 stderr）或 `str(HTTPError)`；
- `AvailableArtifact.locator`：PEP 691 的 `url` 经 `urljoin` 后直接保存，没有像 `SourceIdentity` 那样去掉 userinfo/query；私有索引的 query token 会进候选快照；
- `ProcessResult.start_error`：会进报告，创建时已经过 redactor。

`SourceIdentity.locator` 和带 userinfo 的 PEP 508 直接 URL 已经在声明阶段处理，不是这条缺口。

**下一步：** 进入 Schema dump 的 detail 不含输出正文和 credential；artifact locator 与 `SourceIdentity` 使用同一套公开 URL 规则。这与流式 overlap 是不同 seam，不要用 `_redact_stream` 去扫报告。

## 4. 模块设计评审

### 4.1 Cell identity 与排序是两件事

现行代码至少有五套 cell key、三套 FailureRecord 提取；收成单一所有者仍然正确。但 `terminal.py` 的 key 顺序是 `(package, python, target, extras)`，Scheduler 的规范排序是 `(package, target, python, extras)`。前者同时承担 map identity 和诊断排序。

**下一步：** `cell_identity(cell)` 只用于 equality / lookup / dedup，不把它机械复用为展示或调度排序；需要排序的模块使用对应契约所有的显式 order key。`failure_records_for_result` 继续升为报告、search journal、diagnose 的单一入口。

### 4.2 验证运行编排值得加深，但不能做成参数搬运

Check / Smoke / Search 都有 Journal gate + Scheduler + final Journal 的共同循环。这个 locality 值得收进一个模块。

原提案把 scheduler、events、logs、packages、snapshot、tasks、jobs、deadline 和 journal callback 全部放进 `run_verification`，interface 几乎和 implementation 一样宽。删除该 wrapper 后，复杂度只回到少量调用，不满足深模块的删除测试。

**下一步：** 生产 composition root 构造一个 `VerificationRunner(scheduler, events, logs)`；调用方只交一个 `VerificationRun`，每个 task 自己提供执行与 outcome → journal entry 投影。Runner 隐藏 gate、并发、完成时写入、最终写入和基础设施错误。

### 4.3 Protocol 应按调用方保持窄 interface

Check static 只需要 `capture`，Search static 需要 `capture + evaluate`，FullEvaluator 只需要 static `evaluate`。把三者合成一个宽 Protocol 会迫使调用方和测试 adapter 知道无关方法。

**下一步：** 保留 consumer-owned 的窄 Protocol；只有方法集合与语义都相同时才复用。`FullEvaluator` 依赖窄的 `StaticEvaluateOperations`，不绑具体 `StaticEvaluator`，也不要求 `capture`。

### 4.4 CoordinateSearch 拆分成立，验收必须覆盖真重入

`CoordinateSearch` 与 `SearchCoordinator` 已是两个可独立描述、测试和演进的模块，按已有 seam 分文件成立。`minimize` 的 evaluator/cache/observations 必须改成调用局部状态，以便一个注入实例被并发 cell 共用。

「同实例连续调用两次」不足以验收：现行代码每次入口重置实例字段，本来就会通过。测试必须包含 evaluator 在外层 `minimize` 中嵌套调用同实例，以及 barrier 控制的双线程交错。

### 4.5 ProjectEditor 不需要注入无状态 Builder

`PackageReportBuilder.project` 已是同一个纯投影所有者。把同一个无状态 Builder 实例同时注入 Search 和 Editor 不会新增一致性保证，只会增加一个假 seam。现有 editor 测试已覆盖篡改 projected requirements 后不写回，只是与幂等断言混在同一测试。

**下一步：** 保持 projection 单一实现；把现有防篡改断言拆成独立测试，再补 complete authority、recovery 和 workspace transaction 测试。

### 4.6 RunLogStore 需要独立测试面

`runlog.py` 的 journal、diagnosis index、原子写、脱敏和权限行为主要寄生在 process / diagnose 测试上。新增 `test_runlog.py` 仍有价值，测试面是 `write_journal` / `read_latest_journal` / `replace_associations` / `lookup` / `lookup_run`，安全原语细节留在平台测试。

## 5. 建议顺序

| 优先级 | 项 | 完成标准 |
| --- | --- | --- |
| P0 | 流式脱敏 | 任意分块与一次性脱敏等价；观察面是 cache / log / 终端，不是报告正文 |
| P0 | Report authority | final vector、ProbePass、Proposal、Evaluation、projection 闭环 |
| P0 | Apply transaction | 单包与 workspace 写后失败均自动回滚 |
| P0 | Workspace package identity | canonical name 重复在 discovery 阶段失败 |
| P0 | Candidate artifact binding | exact-vector 安装精确 artifact + hash |
| P0 | 全量工程门禁 | Python 3.10–3.12；Ruff、ty、pytest、build |
| P1 | 离线 ProjectDiscovery | Explain / Diagnose 不启动工具、不联网、不写日志 |
| P1 | Journal package policies | 多包运行不再伪装成第一包 policy |
| P1 | Registry adapter | 私有认证可用；畸形响应保守失败 |
| P1 | 报告 portable 凭据 | FailureDetail 不含输出正文；artifact locator 为公开 URL |
| P1 | Cell identity / Failure 提取 | equality 与 order 分离；单一提取入口 |
| P1 | VerificationRunner | 三工作流共享深 interface，不复制 gate/schedule/journal |
| P1 | FailurePolicy 输入 | Evaluation → classify 的机械映射只有一个实现 |
| P1 | CoordinateSearch | 真重入、并发安全、表驱动算法测试、按 seam 分文件 |
| P1 | RunLogStore 测试 | 独立覆盖 journal 与 index interface |
| P2 | 静态 PASS 环境释放 | 不随候选数长期保留 PreparedEnvironment |
| P2 | TerminalPresenter 内部视图 | 包内拆 explain / diagnose，不改视觉契约 |
| P2 | Journal 写入时机 | 每个 cell 完成时至多一次，最终再写完整 Journal |

## 6. 已经很好、不必先动

- `FailurePolicy.classify`、`EvaluationCache`、Candidate filtering 和 SourceSnapshot 的核心形状仍然清晰；修复应加深它们的证据闭环，不重写算法。
- D003 的 CoordinateSearch probe 顺序与状态模型没有在本次 review 中发现契约偏差。
- `PackageFloorReportV1` 已有大量 complete/incomplete 不变量；问题是 final evidence 链缺一段，不是整个 Schema 需要拆散。
- `cli.build_context` 作为生产 composition root 的方向正确，但离线 discovery 与 credential/redactor 装配需要修正。
- D001 §10 的非目标继续保持；以上整改都属于既有承诺，不引入上界搜索、attribution、static-only floor、跨运行 cache 或非宿主执行。

## 7. 测试与验证

当前验证结果：

- `uv run pytest --no-testmon`：522 passed；
- `uv run ty check src`：通过；
- `uv build`：sdist 与 wheel 构建成功；
- `git diff --check`：通过。

现有薄面：

| 面 | 当前判断 |
| --- | --- |
| 流式 redaction | 缺跨 chunk / URL userinfo / production secret tests；观察面是 cache/log/终端 |
| 报告 portable 凭据 | FailureDetail 可抄 diagnostic()；artifact locator 未剥离 query |
| Report authority | 缺 final vector 与 PASS Proposal 防篡改矩阵 |
| ProjectEditor | 2 个测试；缺 rollback / restart recovery / workspace atomicity |
| Project discovery | 缺 canonical duplicate package names |
| Candidate install | 缺 snapshot artifact 与实际安装一致性 |
| Registry adapter | 缺认证私有索引和畸形 PEP 691 矩阵 |
| CoordinateSearch | 6 个直接算法用例；缺 hint/threshold/non-monotonic/真重入组合 |
| RunLogStore | 无独立 `test_runlog.py` |
| Scheduling | 3 个测试，含真实 `time.sleep` |
| 端到端 | 2 个本机冒烟；不替代上述 seam 测试 |

## 8. 勘误

- 初稿「D001–D008 已全部落地」改为「命令与主要状态机已落地，但仍有契约缺口」。
- `terminal.py._cell_key` 不是与 Scheduler 相同顺序的四元组；它是 package/python/target/extra，并同时参与诊断排序。
- `tests/test_windows_runlog.py` 的条件 skip 发生在 Windows，用来跳过“拒绝非 Windows”这一测试；不是在非 Windows skip 整个文件。
- `ProjectEditor` 现有首个测试已经覆盖 unauthorized projected requirement 不写回；待补的是 final evidence、rollback 和 workspace transaction。
- `/home/llh/pf` 是 git 仓库，`main` 跟踪 `origin/main`；无需初始化仓库。
- 初稿把流式脱敏写成「日志、终端和报告」同一缺口。终审后：`package-floor.json` 不含 stdout/stderr 正文；跨分块明文出现在 Output Cache、Process Log 和 live 卡片。报告残留凭据面是 `FailureDetail.message` 与未剥离 query 的 artifact locator，见 §3.6。
- `.pf/` 是 gitignore 的本机诊断目录，v1 不自动删除；不是省略日志脱敏的理由。

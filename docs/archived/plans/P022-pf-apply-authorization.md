# P022 — D016 PF Apply 授权实施记录

- **状态：** 已完成
- **开始日期：** 2026-08-29
- **完成日期：** 2026-08-29
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D016](../designs/D016-pf-apply-authorization.md)
- **实施基线：** `3b78fe3`（`feat(cli): align verification output grids`）

本文先于生产代码变更建立 D016 的实现切面、依赖顺序、测试矩阵和证据槽位。每次实质行动后在 §7 记录“行动、目标、结论、证据”；完成标准来自 D016 §10，本文不新增、缩小或替换设计契约。

## 1. 目标与边界

本轮完整实现 D016：

- 从当前 `EffectiveConfig.platform` 与报告最终 Cell roots 推导 `PlatformDeclaration`、`ApplySelector`、`ObservedSearchSuccess` 与 `ApplyScope`；
- 让 `PackageReportBuilder.project` 按 dependency group 和 Cell 意图重生成完整 requirement 组，支持声明矩阵、平台 scoped projection、libc selector 合并与 De Morgan 补集；
- 用 `PyprojectIdentity` 分离 owned pyproject 的 remainder 与 dependency arrays，并把类型保真的 canonical TOML identity 纳入 SourceSnapshot/Schema 1；
- 新增 `ApplyAuthorizer`，统一验证 package/policy/source/dependency/platform/projection，且只允许 `--force` waiver source-layer drift；
- 把 `ProjectEditor` 收窄为 authorized edit 的事务执行器，在 prepare、snapshot 和 raw bytes 三个时点 fail closed，并保留 workspace rollback；
- 让 CLI、minimize、explain 和终端展示使用结构化授权事实，满足 stdout/stderr、单一 final summary 与退出码契约；
- 完成真实 TOML round-trip、公有 CLI、跨 generation 增量 apply、workspace all-or-nothing 资格；
- 把 D016 决策分别归并到 D001、D002、D006、D014，再归档 D016 与本 Plan。

不增加手工 platform/cell 选择，不改变搜索、候选或 runtime authority，不在 report wire 保存 apply-time waiver/scope/history，不跨 generation merge/rebase evidence，不改变 SourceSnapshot 路径成员集合，也不为 Schema 1 旧报告增加宽松迁移。

## 2. 基线事实与差距

| 范围 | `3b78fe3` 当前事实 | D016 目标状态 |
| --- | --- | --- |
| snapshot/wire | 所有路径均为 `SnapshotEntry`；digest 使用 `pf:snapshot:v1`；Schema 1 无 owned pyproject identity | owned pyproject 使用 `PyprojectIdentity`；普通 entry 与 pyproject identity 共同进入新 snapshot digest；缺字段 reader fail closed |
| report projection | builder 按单个 declaration 投影；缺任一 active Cell 时 projection 为空；不同 floor 只按完整 target marker 展开 | builder 按 dependency group 重生成整组；apply-time 可选择已授权 selector；未授权 selector 保持 original；libc 同 selector；补集为已授权 selector 的 De Morgan |
| apply authorization | workflow 只比较 package/policy；editor 要求 complete report、完整 source identity 相等，并直接读取 report 重推 projection | workflow 先得到 frozen workspace authorization；authorizer 独占报告/当前 plan/snapshot 判定；默认允许安全 scoped；force 只 waiver source drift |
| dependency drift | editor 按 `RequirementDeclaration.raw` 在数组中定位，允许 projection 已存在的简单 no-op | normalized group multiset 区分 WRITABLE/NOOP/DRIFTED；固定、marker、source、extras、specifier 与 group 增删受保护；数组无语义重排不漂移 |
| transaction | prepare 后只做一次完整 snapshot 比较；替换前没有 raw compare-and-swap | prepare 复核 authorized semantics 与 expected snapshot；每个目标替换前按 prepared raw digest CAS；异常仍全 workspace rollback |
| CLI/presentation | `pf apply [package]` 无 force；成功只返回 `ProjectEditResult`；授权错误沿通用错误路径 | `ApplyRequest.force`；结构化 scope/preserved/waiver facts；source waiver 走 stderr warning，成功单一 final summary；退出 2/3/1 符合设计 |
| generation | apply 修改 snapshot 后旧 report 因 source drift 失效，但无 dependency-array 特判或顺序 scoped 重投影 | 合法 apply/NOOP 不算 source waiver；下一 search 创建新 generation；旧补集被整组重投影替换且不 merge 旧 evidence |

现有 `ProjectLoader` 已拥有 canonical package/source/config/cell 语义，`ReportStore` 已验证 Schema refs 与 generation，恢复日志与原子替换可复用。新增 seam 应放在这些 owner 之间，不把授权条件继续堆入 editor 或 terminal。

## 3. 实现模型与切面

### 3.1 Snapshot/wire identity

在 `schemas/project.py` 建立 `PyprojectIdentity` 与新版 `source_snapshot_digest`。`SnapshotBuilder.build(root, owned_pyprojects=...)` 保持同一遍安全遍历和 staging；遇到 owned regular pyproject 时解析 TOML，生成 type-tagged canonical identity，并在 snapshot 中以 pyproject identity 取代该文件的普通 blob entry。staged bytes 仍完整保留供搜索使用。

canonical encoder 只由 snapshot 模块拥有：table key 排序、array 保序，字符串/bool/int/float/datetime/date/time 类型分离；finite float 用 `hex()` 且保留 `-0.0`，NaN 归一化。remainder 和 dependency arrays 使用 D016 指定前缀；完整 snapshot 使用新的预映像前缀并绑定普通 entries 与 pyproject identities。

`ProjectPlan` 暴露 owned pyproject paths；search、apply authorization、editor 事务检查均把同一集合交给 builder。Schema 1 wire 要求 `pyproject_identities`，reader 对缺失字段失败，不做旧格式 fallback。

### 3.2 Cell-to-requirement projection

把 report builder 的 projection public seam 提升为 dependency-group projection。以 `(pyproject_path, location, optional group, canonical name)` 聚合 declarations，按 `(python_minor, extra_surface, ApplySelector)` 收集成功 floor。`DECLARED_MATRIX` 重投影全部声明 Cell；`PLATFORM_SCOPED` 接受 selected selectors，并为 selected selector 生成正选条件、为其 De Morgan 补集保留 original 的有效约束。

投影过程保留 declaration marker partition、requested extras、source、非 lower-bound specifier、fixed/unmanaged declarations；同 selector gnu/musl 的唯一 floor可外推，不同 floor fail closed。结果在全部报告 TargetCells 上重求值，验证 selected Cell 的 exact floor、preserved Cell 的 original 有效约束与同名 declaration 不重叠。Report wire 的 complete projection仍按全矩阵构建；incomplete report 的 apply-time projection只能由 authorizer显式请求。

### 3.3 Apply authorization

新增 `authorization.py`，保存 strict/frozen records：`ApplyMode`、`ApplyScope`、`DependencyState`、`ApplySelector`、`AuthorizedProjectEdit`、`AuthorizedPackageApply`、`ApplyPresentationFacts` 与 `AuthorizedWorkspaceApply`。`ApplyAuthorizer.authorize` 按以下顺序 fail closed：

1. report reader 已验证的 identity、package/policy/declaration/source-plan/target triple 一致性；
2. final roots 的 ObservedSearchSuccess、PlatformComplete、MissingSelector/MissingCellWithinSelector 与 non-monotonic blocker；
3. builder 生成且重求值通过的 intended group maps；
4. current/original/intended normalized dependency state，只接受 WRITABLE/NOOP；
5. 未选中 owned pyproject dependency arrays 精确相等，以及 package/target/source/policy 等独立结构化 identity；
6. source-layer expected snapshot；仅 force 可以记录 `SOURCE_SNAPSHOT_DRIFT`，并只保留有界脱敏 changed-path facts；
7. 所有 package 通过后一次性返回 workspace authorization。

没有 applicable floor 使用 `NoApplicableFloorError`（CLI 2）；有 floor 但授权失败使用新的 apply authorization error（CLI 3）。`--force` 不参与 scope/projection选择。

### 3.4 Authorized transaction

`ProjectEditor.apply_many` 改为只接收 `AuthorizedWorkspaceApply`。prepare 时重新构造当前 group map，与 `expected_pyproject_identity` 和 authorized group replacements 比较；不读取 report、不重推 scope。写前重建 source-layer snapshot并匹配 authorization 的 `expected_snapshot`；prepared edit保存原始 bytes digest，每次原子替换前立即 CAS。写后重新解析并验证完整 intended semantics；任何 package blocker、write/validation/recovery failure沿现有 journal 做 all-or-nothing rollback。

### 3.5 Workflow、CLI 与展示

`ApplyCommandWorkflow` 执行 load project → read reports → build current owned snapshot → authorize → transaction；`ApplyRequest` 增加 strict bool `force`。`minimize` 继续构造默认 apply request。workflow返回包含 edit结果和 `presentation_facts` 的公有 outcome，TerminalPresenter只做措辞/通道/退出码映射。

`explain` 只依据 report intrinsic facts描述 eligible/blocked 与 MissingSelector，并明确 apply 会复核当前声明；不读取当前树。force waiver 实际使用时输出有界 evidence/preserved/waived facts到 stderr且只渲染一个 warning final；无 waiver成功走 stdout。

## 4. 实施顺序

### 切片 001 — PyprojectIdentity 与 Schema 1 snapshot

1. 为 canonical TOML 类型、字段缺失、排版等价、owned/unowned pyproject和路径成员不变写测试；
2. 建 schema、digest与 `ProjectPlan.owned_pyproject_paths`；
3. 迁移 SnapshotBuilder以及 search/report fixtures的 build调用；
4. 更新 report wire/read/merge/generation验证与 schema/example生成物；
5. 运行 snapshot、schema、report、project和workflow回归。

### 切片 002 — selector/group projection

1. 为 omitted/single/multi、libc合并/冲突、multi-minor、optional/marker、不同 floor、De Morgan补集写 projection测试；
2. 建 group key、requirement semantics、selector 与 scope schemas/纯函数；
3. 把 `PackageReportBuilder.project` 迁移为整组 projection，保留 complete report wire语义；
4. 用 Cell重求值锁定 selected/preserved等价与禁止重叠；
5. 运行 project、report、report-schema和projection回归。

### 切片 003 — ApplyAuthorizer

1. 为平台判定、证据根、WRITABLE/NOOP/DRIFTED、source waiver优先级和 workspace package隔离写测试；
2. 建 strict授权 records 与错误分类；
3. 实现结构化 identity、dependency group multiset和 source-layer差异；
4. 调用 builder生成 apply-time intended edits，并一次性形成 workspace authorization；
5. 运行 authorization、snapshot、report和workspace反例矩阵。

### 切片 004 — ProjectEditor authorized transaction

1. 迁移 editor public seam 与 fixtures，删除 report internals/raw declaration授权逻辑；
2. 实现 semantic prepare、expected snapshot重检、raw CAS和整组 replacement；
3. 保持并扩展 recovery journal、write validation与多 package rollback；
4. 验证 authorize后 source drift、prepared后 raw drift、NOOP与重复apply；
5. 运行 editor、secure runlog和report workflow回归。

### 切片 005 — Public workflow/CLI/presentation 与增量 round-trip

1. 串联 project/report/snapshot/authorizer/editor，增加 `--force`；
2. 实现结构化 apply outcome和TTY/non-TTY单 summary、stdout/stderr与退出码；
3. 调整 minimize默认授权与explain离线条件式措辞；
4. 建真实临时workspace qualification：omitted/single、多selector scoped、force drift、不可force drift、顺序Linux→Windows generation；
5. 运行 cli、terminal、explain、end-to-end与真实 TOML round-trip回归。

### 切片 006 — 契约归并、验收审计与门禁

1. 按 D016 §9 更新 D001、D002、D006、D014及schema/example生成物；
2. 对 D016 §10 每个资格场景逐项回填直接实现/测试证据；
3. 执行 `ruff`、`ty`、Python 3.10–3.12全量 pytest（显式 `--no-testmon`）、coverage和build；
4. 检查 `git diff --check`、文档链接/状态/唯一所有者、工作树范围；
5. 将 D016/P022移入归档并更新索引，复跑文档与定向门禁。

## 5. 验收与测试矩阵

| D016 §10 资格 | 切片 | 主要测试位置 | 直接证据目标 |
| --- | --- | --- | --- |
| OMITTED/SINGLE无平台 marker；triple mismatch阻止 | 002、003、005 | `test_projection.py`, 新 `test_authorization.py`, `test_end_to_end.py` | public workflow与 round-trip 输出 |
| MULTI complete/scoped；gnu+musl selector；libc冲突 | 002、003 | `test_projection.py`, `test_authorization.py` | scope、selector与重求值事实 |
| Missing Cell/non-success/non-monotonic阻止；历史 rejection不误判 | 003、005 | `test_authorization.py`, `test_report_workflows.py` | final root分类与 CLI exit |
| 合法变更/NOOP不需 force；新 generation | 001、003、005 | `test_snapshot.py`, `test_authorization.py`, `test_end_to_end.py` | dependency-array排除与generation变化 |
| source/remainder可force；policy/package/source plan不可force | 001、003、005 | `test_authorization.py`, `test_cli.py` | identity优先级与 waiver facts |
| dependency语义漂移/重排/未选中package隔离 | 002、003 | `test_authorization.py`, `test_project.py` | normalized group multiset正反例 |
| canonical datetime/date/time/float稳定；旧wire拒绝 | 001 | `test_snapshot.py`, `test_report_schema.py` | exact digest与reader fail closed |
| marker/optional/multi-minor/floor/补集重求值 | 002 | `test_projection.py`, `test_report.py` | Cell级 intended/observed equality |
| 顺序scoped apply；旧补集删除；不跨generation merge | 002、005 | `test_end_to_end.py`, `test_report_artifacts.py` | 两次真实 TOML/report round-trip |
| 三平台补集不出现missing selector字面量 | 002、005 | `test_projection.py`, `test_end_to_end.py` | De Morgan字符串与语义 |
| merge只接受同 dependency identity generation | 001、005 | `test_report_artifacts.py`, `test_report_workflows.py` | merge identity正反例 |
| authorize/snapshot/raw CAS时序 | 003、004 | `test_authorization.py`, `test_editor.py` | 三个并发漂移见证 |
| 多package blocker/write/recovery all-or-nothing | 003、004 | `test_editor.py`, `test_report_workflows.py` | 无预写与rollback状态 |
| TTY/non-TTY单summary、通道、退出码 | 005 | `test_cli.py`, `test_terminal.py`, `test_end_to_end.py` | 公有CLI facts，不只helper |

每个切片先跑最窄测试，再跑相邻 owner；最终使用 `UV_CACHE_DIR=/tmp/pf-uv-cache` 并显式 `--no-testmon`。pytest通过、coverage gate与网络/环境限制分别记录，任何窄测试都不能替代 §10 逐项资格审计。

## 6. 变更控制

- Plan建立前工作树已有用户提供的 D016与`docs/README.md`修订；它们是本轮权威设计输入，保留且不覆盖；
- D016细化了已提交旧草案，生产实现以当前工作树版本为准；如实现证据推翻语义，先记录矛盾并请求设计决定，不暗中缩窄；
- `ReportStore`不根据force改变result/projection；`ProjectEditor`不保留读取report或重做authorization的后门；
- 不以host平台猜scope，不以CLI exit code或空projection代替 final root验证；
- 测试优先锁定公有 schema/workflow/CLI与语义，不依赖私有helper、完整易变输出或排序偶然性；
- 归档前必须确认D016每条资格已有直接证据，不能用全量绿色代替缺失场景。

## 7. 过程记录

### 基线审计与 Plan

- **状态：** 已完成
- **行动：** 核对当前工作树、D016全文及D001/D002/D006/D014链接，阅读snapshot、report projection、project loader、workflow、editor、CLI/terminal与对应测试入口，在生产代码修改前建立本Plan。
- **目标：** 把D016 §10全部资格映射到依赖有序切面和直接证据，识别可复用owner与必须替换的旧授权逻辑。
- **结论：** 现有ReportStore reader/generation、ProjectLoader结构化plan、SnapshotBuilder安全遍历和ProjectEditor recovery/atomic write可复用；snapshot wire、按declaration complete-only projection、editor内授权、raw字符串定位与无force CLI均需迁移。实现不能缩成editor加flag或partial projection helper。
- **证据：** `git status --short`在计划前仅显示`docs/README.md`、D016修改；`git log -5 --oneline`基线为`3b78fe3`；D016 §383–411列出public CLI与真实 TOML round-trip资格；源码定位为`src/pf/snapshot.py`、`src/pf/report.py:328`/`:1377`、`src/pf/editor.py:35`、`src/pf/workflow.py:1005`、`src/pf/cli.py:230`、`src/pf/terminal/__init__.py:982`。

### 切片 001 — PyprojectIdentity 与 Schema 1 snapshot

- **状态：** 已完成
- **行动：** 新增 `PyprojectIdentity` 与新版完整 snapshot digest；`SnapshotBuilder` 对 owned pyproject 保留路径/kind/mode entry，但用类型标记后的 parsed TOML 分别计算 remainder/dependency-array digest；`ProjectDiscovery` 收集 root、全部 workspace candidate（含未选中/排除 member）和递归 in-tree path package metadata；Check/Smoke/Search 都从 `ProjectPlan` 传入同一 owned path 集。Schema 1 wire/reader、生成脚本、JSON Schema与最小示例同步迁移。
- **目标：** 建立所有后续authorization可复用的结构化source/dependency identity。
- **结论：** dependency arrays变化只改变对应digest，scripts/其它TOML变化只改变remainder digest；field missing与present-empty不同；table布局/注释不入identity，array保序，datetime/date/time、inf/nan与`-0.0`均类型稳定。普通source路径成员集合不变，owned pyproject不再用raw blob digest。旧wire缺`pyproject_identities`因required field fail closed。
- **证据：** 首次 `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon -q tests/test_snapshot.py tests/test_report_schema.py` 在sandbox内因无法取`uv-build`而未执行；受控网络重跑为`150 passed`。加入project/workflow/artifact覆盖后，snapshot/project/report schema/artifacts/report workflow/search/check/smoke聚焦集合为`254 passed in 0.99s`，随后`generate_report_schema.py --check`成功；相关源码 `ruff check`通过。`ty check`只发现既有editor测试override需补新keyword signature，已同步修正。

### 切片 002 — selector/group projection

- **状态：** 已完成
- **行动：** 将旧的单 declaration complete-only 投影收窄为 report wire 用的 `project_declaration`，新增 public `PackageReportBuilder.project` 按 `DependencyGroupKey` 重生成整组 requirement；建立 `ApplySelector`、selected/preserved selector输入、同 selector libc floor 合并、selected selector正条件与 De Morgan补集，并在全部TargetCells上重求值。测试覆盖omitted、scoped multi、三平台补集、gnu/musl继承与冲突、已有marker、多Python minor、optional group和不可表示重叠。
- **目标：** 由同一owner生成并复证declared/scoped整组requirements。
- **结论：** `project()`现在是Cell意图到PEP 508组投影的唯一owner；DECLARED_MATRIX不生成平台marker，PLATFORM_SCOPED只否定已授权selector，未授权Cell保持original有效约束；同selector不同observed floor与重求值重叠均fail closed。report complete projection继续保持Schema 1既有语义。
- **证据：** `tests/test_projection.py`新增11个直接语义场景并全部通过；projection/report/editor相邻owner聚焦回归曾为`177 passed`，随后纳入snapshot、authorization、CLI/terminal后的D016聚焦集合为`376 passed in 3.22s`；相关`ruff check`与`ty check`通过。

### 切片 003 — ApplyAuthorizer

- **状态：** 已完成
- **行动：** 新建strict/frozen apply schemas、`ApplyAuthorizationError`与`ApplyAuthorizer`；按workspace报告选择、policy/source plan/requires-python、final root平台完整性、group projection、current/original/intended依赖语义、未选中owned pyproject identity和source-layer drift顺序授权；force只记录`SOURCE_SNAPSHOT_DRIFT`及最多8条规范相对路径。根据真实端到端反例，删除“完整CellSuccess但零受管依赖仍因floor计数为0失败”的过严二次判定，使其成为安全NOOP。
- **目标：** 在任何写入前冻结workspace级授权并区分不可waiver identity与source drift。
- **结论：** MissingSelector只改变自动scope；partial/non-success root、libc冲突、dependency/policy/package/source-plan和未选中package漂移均不可force。合法dependency状态迁移与NOOP排除在source waiver之外；source/remainder漂移只有显式force才产生有界展示事实。没有final CellSuccess仍返回exit 2，完整空依赖report可NOOP。
- **证据：** `tests/test_authorization.py`最终31项全部通过，含omitted/single/multi scoped、libc、partial/non-success/non-monotonic/no-success、历史rejection、force优先级、dependency语义/重排、NOOP、policy/requires-python/test-command/source-plan、unselected workspace与两项真实CLI round-trip；authorization/report-schema聚焦曾为`153 passed`，扩展schema/artifact后为`173 passed`；生产与测试`ruff`/`ty`通过。

### 切片 004 — ProjectEditor authorized transaction

- **状态：** 已完成
- **行动：** 把editor seam改为只接收`AuthorizedWorkspaceApply`，删除report读取、scope推导、projection和search-time raw定位；prepare验证expected `PyprojectIdentity`并渲染整组replacement，事务前重建expected snapshot，journal前做全目标raw preflight CAS且每次replace前再次CAS，写后复核TOML、mode/remainder和完整group requirements；原子写保留文件mode。原有基于editor内授权的测试迁至authorizer，`tests/test_editor.py`改写为事务职责测试。
- **目标：** 只执行authorized edits并在snapshot/raw两个并发窗口fail closed。
- **结论：** ProjectEditor不再拥有report internals或授权后门；authorize后source变化、prepare前semantic变化、prepared后仅排版raw变化均在覆盖前失败。多文件后续写异常仍按journal回滚，恢复异常先停止，重复authorized apply幂等。
- **证据：** `tests/test_editor.py`10个事务测试全部通过，直接覆盖comment保留、NOOP、snapshot drift、semantic drift、raw CAS、整组replacement、multi workspace、后写失败rollback、恢复失败与禁止重新load project；随后纳入`376 passed` D016聚焦回归，`ruff`/`ty`通过。

### 切片 005 — Public workflow/CLI/presentation与增量round-trip

- **状态：** 已完成
- **行动：** 串联ProjectLoader→owned snapshot→ReportStore→ApplyAuthorizer→ProjectEditor并返回`ApplyCommandResult`；增加`apply --force`，让minimize无条件复用默认授权而不以incomplete状态预先拦截合法MissingSelector；终端按facts输出scoped/preserved单summary，source waiver只走stderr warning并显示observed cells、最多8条路径；usage错误统一exit 1。explain改为offline eligible/blocked条件式措辞与passed Cell计数。新增Linux→Windows新generation真实TOML/CLI顺序apply与force source drift CLI测试，并修复完整空依赖end-to-end lifecycle。
- **目标：** 让D016行为通过public CLI与真实TOML/report generation可观察。
- **结论：** 无waiver成功只写stdout；实际source waiver只写stderr且exit 0；TTY/non-TTY均只有一个final summary。顺序scoped apply从更新后的dependency-array创建新generation，第二次整组投影移除旧Linux补集、保留Linux floor并写Windows floor。explain不再声称离线报告本身授权当前apply。
- **证据：** workflow/CLI首轮聚焦`44 passed`，explain/terminal聚焦`124 passed`，D016相邻owner聚焦`376 passed`；补齐public exit/scoped explain和资格反例后，authorization/CLI/explain/terminal聚焦`194 passed in 3.94s`。受限网络首次`test_end_to_end.py`在uv-build下载处以SOURCE_FAILURE停止；受控网络复跑暴露并修复空依赖NOOP后，sandbox缓存复跑`tests/test_end_to_end.py tests/test_authorization.py`通过。真实CLI顺序apply确认新generation、旧补集删除和selected/preserved事实，force CLI确认stderr warning与单一final。

### 切片 006 — 契约归并、验收审计与门禁

- **状态：** 已完成
- **行动：** 将产品授权/退出码归并D001，将`ApplyAuthorizer`、authorized transaction与Snapshot/Projection owners归并D002，将force/scoped/waiver/explain通道和措辞归并D006，将PyprojectIdentity、source-plan generation、apply-time projection与同generation merge归并D014；重新生成并核对Schema/examples。逐项审计D016 §10，补上single/multi complete、platform mismatch、non-success/non-monotonic、历史candidate rejection、固定/extras/marker/specifier/unmanaged drift、8-path bound与public exit场景。完成D016/P022状态、链接和索引后移入归档。
- **目标：** 逐项证明D016资格并完成唯一所有者归并与归档。
- **结论：** D001/D002/D006/D014分别成为唯一现行owner，D016只保留决策历史，P022保留实施证据；没有遗留并行授权文档或editor/report授权后门。所有D016资格均有语义测试或public CLI/真实TOML事务见证，环境下载失败与代码结果分开记录。
- **证据：** Python 3.10最终全量与coverage均为`1365 passed`，branch coverage`91.16%`（门禁90%）；Python 3.11隔离环境`1365 passed in 23.88s`，Python 3.12隔离环境`1365 passed in 25.59s`。两次首次sandbox依赖下载失败均在受控网络补齐后通过。`ruff check src/pf tests scripts`与全库`ty check`通过；`generate_report_schema.py --check`成功；`uv build`成功生成sdist/wheel且两项新增module均存在于工件；相关文档本地链接、归档状态、`git diff --check`均通过。

## 8. D016 §10 资格审计

| 资格 | 直接结论与证据 |
| --- | --- |
| OMITTED/SINGLE与triple mismatch | `test_omitted_platform_uses_declared_matrix_without_marker`、`test_explicit_single_platform_uses_declared_matrix_without_marker`证明无marker；`test_current_platform_mismatch_is_never_waived`覆盖default/force阻止 |
| MULTI complete/scoped与libc | `test_complete_multi_selector_report_uses_declared_matrix`、`test_multi_platform_missing_selector_is_default_platform_scoped`、libc继承/冲突测试；`test_group_projection_complement_only_negates_selected_selectors`证明三平台补集不出现missing selector字面量 |
| Missing/failed/non-monotonic roots与历史rejection | partial Cell、indeterminate和non-monotonic参数case均不可force；无final success返回`NoApplicableFloorError`；经`ReportStore` round-trip的历史`ProbeRejection`不阻止final `CellSuccess` |
| 合法apply、NOOP与新generation | editor comment-preserving apply/reauthorization测试与`test_exact_projected_dependency_state_is_an_unforced_noop`；真实Linux→Windows CLI round-trip断言snapshot/report generation变化 |
| force边界 | source 10-path drift只展示前8条，scripts/dependency-groups remainder与普通source可force；test-command、requires-python/platform policy、SourcePlan和dependency drift均不可force |
| dependency语义 | extras、marker、fixed pin、保留specifier参数矩阵及unmanaged group变化均DRIFTED；数组无语义重排WRITABLE；未选中workspace package变化default/force均阻止 |
| canonical identity与旧wire | snapshot测试覆盖datetime/date/time、inf/nan、`-0.0`、字段presence、排版等价；report reader参数case拒绝缺`pyproject_identities`或`source_plan` |
| group projection | projection测试覆盖existing marker、optional group、多minor/different floor、libc、De Morgan与Cell重求值；不可表示重叠/不同libc floor失败 |
| 增量scoped与merge | 真实CLI顺序apply确认Linux floor保留、Windows floor写入、旧Linux补集删除；report merge测试只接受相同generation，dependency-array变化创建新generation |
| 事务与workspace | editor分别见证authorize后snapshot drift、prepare语义漂移、raw CAS、multi-member write、later-write rollback、recovery blocker与幂等；authorizer在返回workspace grant前验证全部packages |
| CLI/TTY | public CLI测试覆盖default/force request、scoped stdout、waiver stderr、单summary、exit 0/1/2/3与TTY/non-TTY；end-to-end从installed module完成search→explain→diagnose→empty-dependency apply NOOP |

审计未用“全量绿色”替代上述直接见证；全量、coverage、跨Python、schema与build仅作为组合门禁。

# P023 — D017 PF 单 target 与 workspace 直接依赖实施记录

- **状态：** 已完成并归档
- **开始日期：** 2026-08-29
- **完成日期：** 2026-08-29
- **性质：** 非规范性实施计划与过程记录
- **设计来源：** [D017](../designs/D017-pf-single-target-workspace-dependencies.md)
- **实施基线：** `40e7d53`（`fix(snapshot): exclude uv lock from source snapshot`）

本文先于生产代码变更建立 D017 的实现顺序、interface/ownership 迁移、测试矩阵与证据槽位。每次
实质行动后在 §7 记录行动、目标、结论、命令与结果；完成标准完全来自 D017 §11，本文不新增、
缩小或替换设计契约。

## 1. 目标与边界

本轮完整实现 D017：

- 将全部 package-scoped CLI 收敛为可选长选项 `--package PACKAGE`，一次 invocation 恰好选择一个
  installable root 或 workspace member；删除路径 selector、隐式 all-packages 与配置式 package
  selection；
- 将 `ProjectPlan`、`VerificationRun`、workflow/report/apply authorization 与终端结果迁移为单
  target，同时保留 workspace 范围的 owned pyproject snapshot/CAS；
- 由 `ProjectLoader` 为 target 的每条直接 dependency 建立 development/search 双 source route，
  使受管 workspace-backed direct dependency 在 smoke 使用本地 member、在 check/search 使用唯一
  registry route；
- 将 `ResolutionSourceMode` 与逐 dependency route 绑定到 candidate、Attempt、resolution、
  Evaluation、report generation identity；让 `UvAdapter` 仅在 SEARCH mode 生成排序去重的重复
  `--no-sources-package` 参数并拒绝外部 source-selection 注入；
- 复证 project/environment/install graph 中受管 workspace coordinate 的 registry
  version/source/artifact，保持未受管 workspace/path source，并将 leakage/mismatch fail closed 为
  Indeterminate；
- 用新的 Schema 1 唯一 wire 形状替换 `SourcePlan.identities`，同步 JSON Schema、示例、reader、
  writer 与 merge/generation 检查，不保留旧 reader 或 fixture；
- apply 只编辑 target requirement 且保留 `[tool.uv.sources]`，由 loader 记录 workspace member 静态/
  动态版本，并在授权阶段对不满足 intended requirement 或动态版本 fail closed；
- 将稳定规则归并到 D001/D002/D003/D005/D006/D008/D012/D014，随后把 D017 与本 Plan 一并归档。

不增加 workspace 批处理、短选项、任意目录向上发现、transitive PF coordinate、全局
`--no-sources`、source table 改写、registry 失败 local fallback、动态版本求值、旧 CLI/report
兼容层，也不改变 D003 搜索算法、D005 disposition authority 或 D012 two-resolution/one-install。

## 2. 基线事实与差距

| 范围 | `40e7d53` 当前事实 | D017 目标状态 |
| --- | --- | --- |
| CLI/selection | package 是可选位置参数，可按 name/path/pyproject 选择；省略时按 `packages`/`exclude-packages` 展开多个 package | 只有 `--package`；省略选择 installable root；显式值只按 canonical distribution name 唯一选择；一次一个 target |
| config/discovery | root 配置可以筛选 package，package patch `path` 可以增加 candidate；discovery 返回 tuple | 遗留 selection 字段立即配置失败；workspace discovery 独占 identity/path；`select` 返回单个 `PackageLocation` |
| domain/workflow | `ProjectPlan.packages`、`VerificationRun.packages`、多 package loop、command aggregation | `ProjectPlan.target`、`VerificationRun.package + source_mode`、单 report update/lookup、单数 command outcome |
| dependency source | declaration 只有 `source`；所有 workspace/path/git/url 都 fixed/unmanaged；`SourcePlan` 是去重 source 集 | 每条 declaration 有 development/search route；workspace-backed searchable declaration共享 managed 规则；SourcePlan identity保留逐 dependency route与mode |
| uv/resolution | compile 不区分 source mode；adapter 环境可能继承 source-selection 变量；graph 只按现有 plan 做一般闭合 | SEARCH 对受管 workspace coordinate 两次 compile 使用相同逐 package suppression；禁止全局 suppression/外部注入；精确复证 registry artifact |
| candidate/report | candidate 使用 declaration.source；report generation保存 `SourcePlan.identities` | candidate 使用 route.search source；mode/routes进入 candidate、Attempt、Evaluation、generation与唯一 Schema 1 wire |
| apply | workspace authorizer要求报告数等于 `project.packages`，返回 `package_applies`；无 workspace member version类型 | grant 保持 workspace CAS但只含 `package_apply`；只写 target；静态版本满足 intended，动态版本给出规定的离线错误与恢复动作 |
| presentation | help/建议命令与 search completion 含多 package 语义 | package-scoped help/建议只用 `--package`；一次 invocation 一个 final summary；Cell active/pinned dependency计数保留 |

现有 `ProjectDiscovery` 的 workspace glob、安全路径和 owned pyproject 递归发现，`ProjectLoader` 的
registry policy/config/cell/harness owner，D012 的 `UvAdapter`/native plan 资格结构，以及 D016 的
workspace snapshot/authorization/editor CAS 可复用。必须直接替换 tuple interface 和旧 wire，不能在
其上增加单 target facade 或兼容 alias。

## 3. Interface 与 ownership 迁移

### 3.1 Selector、planning 与 config

新增 strict `TargetSelector`（root 或 canonical workspace package）并在 CLI value conversion 中只
接受 distribution-name 形状。`ProjectDiscovery.select(root, selector)` 读取既有 uv workspace，先
验证全部 canonical identity 唯一，再返回一个 installable location；省略 selector 只选 root，root
无 `[project]` 时列出稳定候选并失败。`owned_pyproject_paths` 继续覆盖 root、workspace member 与
递归 in-tree path package。

`ConfigLoader` 和 discovery 都 fail closed 拒绝 `packages`、`exclude-packages` 与 package patch
`path`；root patch、已选 package patch与 member-local `[tool.pf]` 的既有合并优先级不变。Plan seam
替换为 `ProjectLoader.load(root, selector) -> ProjectPlan(target=..., owned_pyproject_paths=...)`。

### 3.2 Dependency routes 与 member version

在 project schema 建立 `DependencySourceRoute`、`ResolutionSourceMode`、
`StaticWorkspaceMemberVersion`/`DynamicWorkspaceMemberVersion`。loader 按 declaration name 保存：

- 普通 registry、fixed 与 unmanaged source两侧相同；
- 满足 searchable语法且由单一无 marker workspace source 指向 member 的 declaration，development
  为 workspace、search 为现行唯一默认 registry；managed selector只在 searchable集合上运算；
- managed workspace route不合格立即配置失败；workspace membership本身不产生 declaration；
- member PEP 621 version类型和值只由 loader读取并写入 route，下游不重新读 TOML。

`RequirementDeclaration.source` 被 route-aware public seam替代；`PackagePlan.source_routes` 为完整
逐 dependency route。mode 投影函数是 candidates/harness/environment 的唯一有效 source入口，
`SourcePlan` identity 由 ordered routes + mode生成而非去重 identity 集。

### 3.3 Verification、uv 与 evidence closure

`VerificationRun` 只携带一个 package 和一个 mode：smoke 为 DEVELOPMENT，check/search 为 SEARCH。
source mode 贯穿 baseline、coordinator、environment factory、resolution context、Proposal/Evaluation与
report builder。`UvAdapter` 从 mode/routes生成 suppression names，对 project/environment 两次
compile使用相同 argv；从子进程环境移除 uv source-selection注入。installation只消费已验证native
plan，不重新选择 source。

CandidateBuilder 对每个 managed declaration使用 registry `search_source`。resolution/inspection
新增 workspace-managed selection断言：请求 version、registry locator/hash必须闭合且不得出现
workspace/path/editable；failure 用 D017 §9 的 scope/cause/stage/digests形成 Indeterminate。
未受管 workspace/path仍按 development identity留在 graph，member/transitive只在 uv graph中出现。

### 3.4 单数 workflow、report 与 apply

Check/Smoke/Search workflow 删除 package loop；report store一次只读写 target report；explain与diagnose
使用 discovery选择同一 target。`AuthorizedWorkspaceApply` 替换为单数 `package_apply`，authorizer只
接收 target report，但继续验证全部 owned pyproject identity与raw snapshot CAS；editor只写单个
target dependency arrays。

report wire用 mode + ordered dependency routes替换 `SourcePlan.identities`，并把该 identity纳入
generation、CandidateSnapshot、Attempt与Evaluation闭合验证。Schema 1 reader/writer/schema/example
原地替换，不读旧布局。

apply authorizer在生成 intended requirements后逐 route检查 workspace member version：静态值必须是
PEP 440且满足 intended requirement；动态值在任何编辑前产生不可 force waiver 的 configuration/
authorization error。CLI只在 stderr输出规定原因和恢复动作，exit 3且无 Usage。

## 4. 实施顺序

### 切片 001 — 单 target selector、config 与 CLI grammar

1. 为 root默认、显式root/member、non-package root、未知/重复canonical name建立真实workspace测试；
2. 建 `TargetSelector` 与 `ProjectDiscovery.select`，保留独立 owned-path discovery；
3. 删除配置 selection/path语义并建立遗留字段配置错误；
4. 将 ProjectPlan/Loader与 package-scoped request改为单 target/selector；
5. 将CLI七个命令改为keyword `--package`，验证grammar exit 1 + Usage与project error exit 3无Usage。

### 切片 002 — Source route schema 与 loader ownership

1. 建 route/mode/member-version strict schemas与canonical digest；
2. 重构 loader source/index/workspace metadata，使 declaration kind/managed基于search route资格；
3. 迁移 declaration/harness/source plan consumers到mode projection；
4. 测试普通registry、fixed/path/git/url、workspace managed/unmanaged、optional、marker、多source、
   无/歧义/不安全registry、静态/动态member version与membership非declaration；
5. 运行project/config/schema/candidate/harness聚焦回归。

### 切片 003 — VerificationRun 与单 target workflow

1. 把 VerificationRun、Check/Smoke/Search与cell matrix调用收敛为单 package；
2. smoke传DEVELOPMENT，check/search传SEARCH，并在每个Attempt保留mode identity；
3. 删除多report写入、completed-package聚合与多package授权读取；
4. explain/diagnose/apply/minimize只定位target report，建议命令全部生成`--package`；
5. 迁移terminal/public workflow测试，确认一个command outcome与一个final summary。

### 切片 004 — UvAdapter逐 package suppression资格与 source闭合

1. 先在固定`uv==0.12.5`建立root/member/equal precedence与一个/多个suppression资格fixture；
2. 让两次compile从同一route/mode投影重复的排序去重`--no-sources-package`；
3. 清除外部source-selection环境变量，断言无全局`--no-sources`且不编辑Proposal source tables；
4. 扩展native plan/install/inspection复证managed registry version/source/artifact并保留unmanaged source；
5. 对 candidate/source/resolve/inspect失败建立D017 §9完整Indeterminate evidence测试。

### 切片 005 — Candidate/search/report wire identity

1. CandidateBuilder只消费每条managed route的registry search source；
2. source mode/routes进入Slice、CandidateSnapshot、Attempt、Evaluation与report generation预映像；
3. 用新唯一SourcePlan wire迁移Pydantic models、ReportStore reader/writer/merge与全部fixture；
4. 重新生成JSON Schema和complete/incomplete examples，显式拒绝旧布局；
5. 测试workspace member当前版本不成为candidate/baseline，transitive/member dependency不成为coordinate、
   boundary或projection。

### 切片 006 — 单 target apply 与 member version授权

1. 将authorization/editor seam改为单数`package_apply`并保持workspace owned path/snapshot/raw CAS；
2. 只投影/写入target dependency arrays，保持source tables和所有member metadata不变；
3. 验证source route/report generation/owned pyproject drift不可force；
4. 对静态member version满足/不满足与动态version建立authorization及public CLI测试；
5. 证明动态member仍可smoke/check/search，apply在任何编辑前exit 3、stderr、无Usage且给出完整动作。

### 切片 007 — 真实资格、契约归并与归档

1. 运行固定uv资格矩阵、真实临时workspace CLI与发布artifact测试；
2. 按D017 §11表逐项归并D001/D002/D003/D005/D006/D008/D012/D014；
3. 重新生成并检查Schema/examples，删除旧批处理fixture/文案/兼容测试；
4. 将D017/P023状态改为已归档并移入`docs/archived/`，更新两个文档索引；
5. 逐项审计§11全部10项，再运行ruff、ty、Python 3.10–3.12全量pytest、coverage、build、链接与diff门禁。

## 5. D017 §11 验收与证据矩阵

| 验收项 | 切片 | 主要测试位置 | 直接证据目标 |
| --- | --- | --- | --- |
| 1. 七个命令只有`--package`且单target | 001、003 | `test_cli.py`, `test_*_workflow.py`, `test_terminal.py` | public help/合法CLI request、单数workflow与summary |
| 2. root/member/未知/重复选择与exit分层 | 001 | `test_project.py`, `test_cli.py`, `test_end_to_end.py` | 真实workspace discovery；1+Usage对比3+no Usage |
| 3. 单target plan/run/report/grant，workspace CAS | 001、003、006 | `test_project.py`, `test_verification.py`, `test_report_workflows.py`, `test_authorization.py`, `test_editor.py` | strict model与public workflow单数，未选owned文件漂移阻止 |
| 4. 遗留selection字段失败 | 001 | `test_config.py`, `test_project.py`, `test_cli.py` | 三字段稳定错误/恢复建议，package patch只影响已选target config |
| 5. workspace managed统一规则与mode闭合 | 002–005 | `test_project.py`, `test_candidates.py`, `test_check.py`, `test_search_workflow.py`, `test_report_schema.py` | smoke local；check/search registry artifact；route/mode identity |
| 6. fixed/unmanaged保留与fail-closed source/leakage | 002、004、005 | `test_environment.py`, `test_uv_adapter.py`, `test_failure.py`, `test_search_coordinator.py` | 完整Indeterminate scope/cause/stage/authority/digests，无Rejection |
| 7. member/transitive不成为PF coordinate | 002、004、005 | `test_project.py`, `test_candidates.py`, `test_search.py`, `test_projection.py` | candidate/boundary/projection name集合只来自target direct declarations |
| 8. apply target-only、drift与动态version | 006 | `test_authorization.py`, `test_editor.py`, `test_cli.py`, `test_end_to_end.py` | source table/member bytes不变；静态/动态与规定CLI错误 |
| 9. uv suppression全资格与环境封闭 | 004 | `test_uv_adapter.py`, `test_uv_qualification.py`, `tests/uv_qualification/*` | 固定uv真实root/member/precedence/多suppression/two resolves/install/inspect |
| 10. 建议命令、真实workspace/artifact与唯一wire | 003、005、007 | `test_cli.py`, `test_terminal.py`, `test_end_to_end.py`, `test_report_artifacts.py`, `test_report_schema.py` | 全部新命令文案、build工件、旧布局不可读、schema/examples一致 |

每个切片先运行最窄语义测试，再运行相邻owner；最终命令使用
`UV_CACHE_DIR=/tmp/pf-uv-cache`与显式`--no-testmon`。pytest pass、coverage gate、build/网络/
平台资格分别记录，不以窄测试或exit code代替§11直接语义证据。

## 6. 变更控制

- Plan建立前工作树已有用户提供的`AGENTS.md`、D017与`docs/README.md`修订；它们是本轮输入，
  保留且不覆盖；
- 当前HEAD比`origin/main`多`40e7d53`，其uv.lock snapshot排除是实施基线，不回退；
- PF pre-release按D017直接替换旧interface/wire；不加alias、兼容property、dual read/write或旧grammar
  rejection测试；
- `ProjectLoader`独占source route与member version；candidate/harness/environment不得重新读uv source
  table，authorizer不得重新读member TOML推导version；
- `UvAdapter`独占source suppression argv；调用者不得传suppression name，生产代码不得编辑source
  table或使用全局`--no-sources`；
- 测试优先锁定public schema/workflow/CLI和语义集合，不依赖private helper、完整易变输出或旧接口；
- 完成前逐条复核D017 §11，不用全量绿色代替缺失资格；归档必须与owner Design归并同一变更完成。

## 7. 过程记录

### 基线审计与 Plan

- **状态：** 已完成
- **行动：** 核对当前工作树、D017全文、文档索引与最近实施记录；定位ProjectDiscovery/ConfigLoader/
  ProjectLoader、project schemas、七个CLI handler、VerificationRun、三条package workflow、report/
  authorization/editor、UvAdapter/environment/candidate及相邻测试；在生产代码修改前建立本Plan。
- **目标：** 把D017 §11十项验收映射到依赖有序的interface迁移和直接证据，确认可复用owner与必须删除的
  旧batch/source/wire语义。
- **结论：** 选择与单target模型必须先于route/mode传播；uv投影必须在loader产出规范route之后实施；
  report wire与apply依赖稳定的mode/route identity。现有workspace discovery安全边界、owned snapshot、
  resolver two-stage/native plan和authorized transaction可复用，但tuple interfaces与去重SourcePlan不可保留。
- **证据：** `git status --short --branch`显示HEAD `40e7d53`、既有`docs/README.md`修改及未跟踪
  `AGENTS.md`/D017；`rg`确认当前`ProjectPlan.packages`、`VerificationRun.packages`、
  `AuthorizedWorkspaceApply.package_applies`、CLI位置参数、配置selection/path与
  `SourcePlan.identities`均仍存在；D017 §11列出10项验收与8份现行owner归并目标。

### 切片 001 — 单 target selector、config 与 CLI grammar

- **状态：** 已完成
- **行动：** 建立`RootPackage | WorkspacePackage` strict selector；将
  `ProjectDiscovery.select`改为返回唯一`PackageLocation`，`ProjectPlan`改为单数`target`；删除
  config中的package selection/path字段；七个package-scoped Cyclopts command只保留可选
  `--package`。
- **结论：** 省略selector只选择installable root，显式值只匹配canonical distribution name；
  workspace discovery继续独占candidate identity/path安全。grammar错误与合法形状的planning错误保持
  `1 + Usage`和`3 + no Usage`的不同authority。
- **证据：** `tests/test_project.py` 70项通过；`tests/test_cli.py`、`tests/test_config.py`进入最终全量矩阵。
  真实命令`pf smoke --help`只显示`--package/--jobs`；`pf smoke demo`退出1并显示Usage；
  `pf smoke --package missing`退出3、列出`pf`候选且无Usage。

### 切片 002 — Source route schema 与 loader ownership

- **状态：** 已完成
- **行动：** 增加`ResolutionSourceMode`、`DependencySourceRoute`、静态/动态workspace member version、
  `SourcePlan(mode, routes)`及其identity；loader为每条target direct declaration建立完整route，按
  development/search route统一决定managed，严格验证source table、index与registry URL，并移除
  declaration/harness上的旧单一source seam。
- **结论：** 普通registry与fixed/unmanaged route两侧相同；合格的受管workspace direct dependency
  才使用workspace→registry双route。workspace membership和member transitive dependency不产生PF
  declaration；member version metadata只由loader读取一次。
- **证据：** `tests/test_project.py`覆盖root/member source precedence、ordinary/fixed/managed/unmanaged、
  optional/marker、membership非declaration、member transitive、静态/动态version、歧义与unsafe registry；
  `tests/test_candidates.py`与`tests/test_harness.py`验证消费者只用mode投影。全部进入1382项全量矩阵。

### 切片 003 — VerificationRun 与单 target workflow

- **状态：** 已完成
- **行动：** 将`VerificationRun`改为`package + source_mode`，让smoke固定DEVELOPMENT、check/search固定
  SEARCH；删除workflow/report store的package loops、命令级多报告聚合及多package terminal outcome；
  explain/diagnose/apply/minimize只定位所选target。
- **结论：** 一次invocation从planning、Cell调度、report update到final summary始终只有一个target；
  D006的active/pinned packages仍是Cell direct dependency计数，不是workspace target计数。
- **证据：** `tests/test_verification.py` 11项、`tests/test_report_workflows.py` 6项及
  `tests/test_smoke.py`/`test_check.py`/`test_search_workflow.py`全部通过；terminal聚焦回归108项通过。

### 切片 004 — UvAdapter逐 package suppression资格与 source闭合

- **状态：** 已完成
- **行动：** `UvAdapter`从SourcePlan唯一投影排序去重的重复`--no-sources-package`，两次compile使用
  相同集合，移除全部外部uv source/index环境注入，禁止全局`--no-sources`；EnvironmentFactory在
  Proposal前复证受管coordinate source/artifact，保持project graph在environment plan中的精确嵌入，
  并为leakage/mismatch建立structured Indeterminate evidence与plan digest投影。
- **结论：** highest/lowest registry plan以等价registry locator及非空artifact alternatives闭合；
  exact candidate通过其artifact URL materialize时以version/filename/locator/hash闭合。固定uv的默认
  index在pylock中省略locator，因此只把该形状规范等价为PyPI默认，不伪造selected artifact。
- **资格与偏差：** 新增`scripts/qualify_uv_workspace_sources.py`和固定manifest。root、member及两处
  等价source declaration三种场景均通过；每场景执行highest与exact两个Attempt、4次compile、2次
  install，两个managed names的suppression始终相同，candidate和exact artifact闭合，本地in-tree path
  source及source table bytes保留。实验同时确认uv 0.12.5不能在任一逐包suppression存在时解析另一条
  未suppressed的`{workspace=true}` source；依D017 §5.1该mixed class不资格化，生产路径保留原route并以
  `TOOL_FAILURE@resolve-project`/Indeterminate fail closed，不增加额外suppression、改写或fallback。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/qualify_uv_workspace_sources.py`
  返回`all_passed=true`；`tests/test_uv_workspace_qualification.py`本地复跑负场景；
  `tests/test_environment.py` 28项通过并含highest/exact正闭合和structured失败公共seam。

### 切片 005 — Candidate/search/report wire identity

- **状态：** 已完成
- **行动：** CandidateBuilder只查询route的registry search source；Attempt、CandidateSnapshot、
  evaluation与report generation都绑定同一SourcePlan identity；Schema 1 inputs改为完整mode/routes，
  reader要求SEARCH并复证declaration、candidate和Attempt closure；同步生成JSON Schema与complete/
  incomplete examples，旧`identities`布局不再可读。
- **结论：** workspace member HEAD/version不是baseline sentinel或candidate；exact selected candidate
  evidence与runtime plan digest保持一条authority链。Reader/writer只有最新Schema 1形状，无兼容层。
- **证据：** `tests/test_report_schema.py` 140项、`tests/test_projection.py` 11项及candidate/search/report
  workflow测试通过；`scripts/generate_report_schema.py --check`无输出且退出0；两个example同时在全量
  artifact/schema测试中通过。

### 切片 006 — 单 target apply 与 member version授权

- **状态：** 已完成
- **行动：** `AuthorizedWorkspaceApply`改为单数`package_apply`，authorizer只消费target report但继续
  冻结全部owned pyproject identities/raw bytes；editor只写target dependency arrays并保留source table
  和member metadata。授权使用loader保存的member version判断静态满足关系，动态version在任何编辑前
  给出规定离线原因/action，force不可waive。
- **结论：** 单target不是缩小CAS范围；未选member drift、source route/report generation/declaration
  drift继续fail closed。合法静态member写回只改变target requirement。
- **证据：** `tests/test_authorization.py` 35项通过；public CLI矩阵证明静态2.5成功、静态1.5退出3且不编辑，
  动态version在force false/true下均`3 + stderr + no Usage`，包含dependency/member/intended requirement、
  offline限制和恢复动作且不建议force；`tests/test_editor.py` 10项通过。

### 切片 007 — 真实资格、契约归并与归档

- **状态：** 已完成
- **行动：** 将稳定规则归并到D001/D002/D003/D005/D006/D008/D012/D014；修正README命令表面；
  生成并检查Schema/examples；增加固定uv workspace资格manifest；完成Python 3.10–3.12、coverage、
  build、真实installed CLI和文档/whitespace门禁；D017/P023状态改为归档并同步索引。
- **结论：** 现行规则均有唯一owner，D017只保留迁移理由，P023只保留实施证据。没有batch、旧selector、
  旧SourcePlan wire、alias或dual-read/write遗留。
- **证据：** 见§9完整命令记录；`uv build`生成`pf-0.1.0.tar.gz`与
  `pf-0.1.0-py3-none-any.whl`，wheel/sdist均包含最新生产模块和`pf`入口。

## 8. D017 §11 最终审计

| §11 | 最终状态 | 直接证据与结论 |
| --- | --- | --- |
| 1 | 通过 | 七个request/handler/help只有`--package`；public CLI正向root/member测试；workflow与summary单数 |
| 2 | 通过 | 真实workspace覆盖root/non-package root/member/未知/重复canonical name；实跑exit 1+Usage与3+no Usage |
| 3 | 通过 | `ProjectPlan.target`、`VerificationRun.package/source_mode`、单report update、单`package_apply`；workspace CAS测试通过 |
| 4 | 通过 | 三个遗留config字段均立即`ConfigurationError`；package patch只影响已选target配置 |
| 5 | 通过 | loader route矩阵、DEVELOPMENT smoke、SEARCH check/search、Candidate/Attempt/report identity与真实registry artifact资格闭合 |
| 6 | 通过 | fixed/unmanaged route保持development identity；path保留已真实资格化；uv 0.12.5 mixed unmanaged workspace class按§5.1形成完整Indeterminate，无local fallback/Rejection |
| 7 | 通过 | composite workspace测试证明member/transitive name不进入declaration、CandidateSnapshot、coordinate、boundary或projection |
| 8 | 通过 | target-only editor、全workspace drift/CAS、source table/member byte preservation、静态/动态version public CLI矩阵均通过 |
| 9 | 通过 | 固定uv三场景manifest覆盖root/member/equal precedence、两个suppression、candidate、highest/exact、two resolutions/one install、artifact/graph/source-table；外部env与全局flag负断言通过 |
| 10 | 通过 | 所有建议命令使用`--package`；真实installed CLI、生成物、唯一wire、Python矩阵、coverage与build全部通过 |

终审另行确认selector必须是非空canonical distribution name、local workspace route不得漂移到另一
member、diagnose同时闭合package name与pyproject path、structured failure detail不得在evaluation
投影时丢失，并让reader从CandidateSnapshot重新计算exact Attempt的selected artifact摘要；相应正向与
篡改测试均进入最终矩阵。

审计没有遗留待办。唯一qualification限制是固定uv对mixed managed-suppressed/unmanaged-workspace
source的已证明不支持；这依D017 §5.1和D012现行owner明确为不可用class并fail closed，不是兼容性
Rejection或隐式语义降级。

## 9. 最终验证记录

| 命令 | 结果 |
| --- | --- |
| `ruff check src tests scripts` | 通过 |
| `ty check src` | 通过 |
| `UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/generate_report_schema.py --check` | 通过，无生成漂移 |
| `UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/qualify_uv_workspace_sources.py` | `all_passed=true`，3个正场景+1个fail-closed场景 |
| `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --cov=pf --cov-report=term-missing` | Python 3.10：1382 passed；coverage 90.71%，超过90% gate |
| `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 --group test pytest --no-testmon` | 1382 passed |
| `UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.12 --group test pytest --no-testmon` | 1382 passed |
| `UV_CACHE_DIR=/tmp/pf-uv-cache uv build` | sdist与wheel构建成功，工件目录检查通过 |
| Markdown相对链接检查、`python -m compileall -q src tests scripts`、`git diff --check` | 通过 |

首次在受限沙箱内运行Python 3.10全量时，installed-CLI witness因构建本地fixture所需的`uv_build`
无法访问PyPI而得到1项环境失败；进程日志明确为`Operation not permitted`。在受控联网条件下该用例
独立通过，随后同条件全量1382项通过；未据此修改生产语义或弱化测试。

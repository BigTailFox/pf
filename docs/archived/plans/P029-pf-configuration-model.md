# P029 — D023 PF 配置模型收敛实施计划

- **状态：** 已完成、已归档
- **开始日期：** 2026-09-04
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D023](../designs/D023-pf-configuration-model.md)
- **评审来源：** [R006](../../reviews/R006-pf-cli-system-review.md) §2.1
- **实施基线：** `d6b8a40`（`docs: record resolved R006 CLI findings`）
- **实现提交：** `43f9ee4`（`feat: implement D023 configuration model`）

本文在生产代码修改前建立 D023 的实施顺序、interface/ownership 迁移、测试矩阵与证据槽。每次实质行动后
在 §8 记录行动、结论、精确命令与结果；完成标准只来自 D023 §12，不以局部绿色、collection、单一 Python
版本、Schema 无结构 diff 或静态扫描替代验收。

## 1. 目标与边界

本轮完整实现 D023：

- 以扁平、职责前缀化的 `[tool.pf]` 唯一公开配置替换现有字段，内部由 strict/frozen nested
  `EffectiveConfig` 按 target、search、resolution、ty、test、scheduling 分组；
- 持久层只保留 root default → member-local，删除集中 package patch；scalar、list、AoT 分别按 D023
  定义替换，CLI optional scheduling override 在 project load 后一次解析；
- 让 ProjectLoader 独占 target 收窄、extra surface 展开、test group admission 和 named dependency search
  policy 的项目语义绑定；让 CandidateBuilder、resolution、evaluator 与 workflow 只消费已绑定 facts；
- 统一 smoke/check/search 的 registry artifact admission，删除 PF resolver prerelease override，并使 source
  snapshot 中 uv project configuration 进入 resolution context identity；
- 分开 Cell、ty 与 configured verifier 三个并发上限，保持 stage timeout 是 evaluation identity、纯调度值
  不是 policy identity；
- 在同一完成变更中更新 owner 文档、README/R006、v1 identity examples 与必要生成物，归档 D023/P029。

不改变 project dependency/floor 语义、D003 coordinate descent、apply projection、package-floor wire shape、
Schema/identity version；不增加 compatibility alias、dual reader、自动迁移、环境变量配置、第二套 config
wrapper/provider/repository、test splitting、pytest-xdist 或 ty worker 注入。

## 2. 基线事实与目标差距

| 切面 | `d6b8a40` 当前事实 | D023 目标 |
| --- | --- | --- |
| config schema | 单层 `EffectiveConfig` 保存 `python/platform/extras/release_granularity/distribution/allow_prereleases/jobs` | nested consumer schemas；只保存新字段与 normalized facts |
| persistent merge | root → centralized package patch → member；root target 会经过同一三层循环 | root → member；root target 只应用 root 一次；`tool.pf.package` 非法 |
| list/AoT | 一般字段最后写入覆盖；无 `dep` AoT；旧 `search-space` 可为 dependency requirement 列表 | list/AoT whole replacement；global search defaults + complete per-dependency policies |
| project binding | ProjectLoader 展开 target/extra，dependency search/default/prerelease facts仍混在 config/declaration | ProjectLoader 绑定每个 managed searchable direct dependency 的完整 named policy |
| candidate | hash 整份 config；全局 space/granularity/prerelease/distribution | hash named policy + artifact；按 dependency policy 过滤和采样 |
| resolution | declaration prerelease 被提升到 PF `allow`，uv adapter可传 prerelease override；artifact主要由 search 使用 | prerelease完全委托 snapshot内 uv config；artifact统一约束全部 registry resolution |
| evaluation | ty/test timeout 与 args/command从 flat config读取；无阶段执行 pool | 分组 config；ty/test invocation pools分别限流且不修改外部 argv |
| scheduling/CLI | `jobs` 在 config 无消费者；CLI省略立即变成`auto` | `max-cells/ty-jobs/test-jobs` optional CLI override一次解析为 RunLimits |
| admission | test command/group检查依赖现有 cell/project path | 四个 verification-producing command在 snapshot/process前统一 fail closed |
| identity/wire | evaluation hash除 jobs外的整份 config；candidate hash整份 config；resolution context保存PF prerelease推断 | 精确 v1 preimage；scheduling排除；uv project-config input identity加入 context；wire形状不变 |

## 3. Interface 与 ownership 迁移

1. 在 `pf.schemas.config` 建立 `TargetConfig`、dependency-selection tagged union、`ExtraConfig`、
   `SearchPolicy`、`DependencySearchPolicy`、`SearchConfig`、`ResolutionConfig`、`TyConfig`、`TestConfig`、
   `SchedulingConfig`、nested `EffectiveConfig` 与 invocation-local `RunLimits`；duration 只保存秒或 `None`，
   persistent scheduling 只保存 `auto | PositiveInt`。
2. `ConfigLoader` 独占 raw key/type/canonical duplicate 校验、root/member merge、dependency-selection variant
   replacement、list/AoT whole replacement、default/canonicalization、dep inheritance、space/step 组合及
   package namespace prohibition；root target不重复读取同一 observation。
3. `ProjectLoader` 消费 normalized target/extra/test facts，验证 explicit Python 是 requires-python 的子集，
   合并 root/member 同名 dependency group，并把每个最终 dep entry绑定到 managed searchable direct
   dependency；`PackagePlan` 输出覆盖全部 managed searchable dependency 的 sorted `NamedSearchPolicy`。
4. `CandidateBuilder` 只消费 `NamedSearchPolicy` 与 `ResolutionConfig.artifact`；candidate policy identity只
   hash这两个事实，候选 filtering/sampling使用每个 dependency的 space/step/prereleases。
5. project/environment resolution 与 CandidateBuilder 共用 artifact admission；删除 declaration/config到
   `--prerelease` / `--prerelease-package` 的 PF 推断和传递。`ResolutionContext` 绑定 exact uv version及
   snapshot内 uv project-configuration input identity。
6. Static/Runtime evaluators消费 `TyConfig`/`TestConfig`；在 evaluator process seam 注入 invocation-scoped
   ty/test permit pools，一个 stage process持有一个permit，permit值不进入argv或policy identity。
7. CLI requests保留三个 scheduling flags 的 omitted/explicit差异；workflow在project load后将其与
   `SchedulingConfig` 一次解析为正整数 `RunLimits`，Runner/Scheduler/evaluator不理解TOML或CLI omission。
8. 四个 verification workflows在 snapshot/process前共同校验 non-empty test command及存在的 test group；
   read/apply/merge workflows不承担该 admission。
9. `pf:policy:v1`、`pf:candidate-policy:v1`、`pf:resolution-context:v1` 在版本不变的前提下直接替换 canonical
   preimage；package-floor schema不新增raw config字段，受影响 examples digest按目标算法重建。

## 4. 实施顺序

### 切片 001 — Nested schema 与两层 ConfigLoader

1. 先以 ConfigLoader public seam覆盖完整新TOML、默认值、root/member/root-target precedence、scalar/list/
   AoT replacement、variant replacement、canonical duplicate、strict types及unknown/package namespace；
2. 建立 nested config schemas与raw layer parsing；删除旧字段、集中 package patch和旧 dependency-list
   `search-space` reader，不保留alias；
3. 实现global `search-*` + `dep[]`完整policy继承及每项space/step组合验证；
4. 保持ConfigLoader只做config语义，不在此读取declaration/source或验证managed searchable资格。

### 切片 002 — Project target、extra、group 与 named dependency policy

1. 迁移 ProjectLoader 到 nested config；用 public PackagePlan/Cell断言 explicit Python不能扩大
   requires-python、platform只建立Cell、policy+custom extra规范并集与unknown extra失败；
2. 保持managed/unmanaged现行ownership并生成覆盖全部managed searchable direct dependencies的canonical
   sorted `dependency_search_policies`；最终存活override的unknown/unmanaged/fixed/source资格在此失败；
3. 证明root/member同名test group组合、空group合法以及command/group admission facts可由PackagePlan取得；
4. 更新test fixtures与`package-floor.json`到唯一新配置，不加入旧字段兼容测试。

### 切片 003 — Candidate policy、artifact 与 prerelease delegation

1. CandidateBuilder按dependency取得bound policy，分别应用specifier/space/highest/artifact/prerelease/yanked
   过滤和major/minor/patch采样；`any`同版本优先wheel；
2. candidate policy v1只hash named policy + artifact，加入精确identity正反测试；
3. 把artifact admission贯穿highest/lowest/exact project plan及environment resolution，固定非registry source
   不被改写；artifact不一致不得形成PASS；
4. 删除PF `allow_prereleases`、declaration prerelease提升和全部uv prerelease参数；用adapter public request记录
   证明project/exact prerelease仅服从materialized uv配置。

### 切片 004 — Resolution context 与 source config identity

1. 盘点uv会消费的root/member project-configuration inputs，在SourceSnapshot/SourcePlan既有边界形成稳定
   identity而不由ConfigLoader解析、合并或复制uv配置；
2. `ResolutionContext`删除PF prerelease fact，加入exact uv version与本次snapshot的uv project-config input
   identity；保持timeout/search/scheduling不进入context；
3. 把context用于两阶段resolution、Attempt/report校验与apply drift authority，证明uv配置变化开启新generation
   且不能作为普通remainder waiver；
4. 保持wire字段名与v1 prefix不变，只重建受影响digest fixtures/examples。

### 切片 005 — Evaluation pools、RunLimits 与 CLI/workflow admission

1. 建立resolved `RunLimits(max_cells, ty_jobs, test_jobs, max_duration)`和一次`auto`解析；CLI四个验证命令
   暴露新flags并保持omitted/explicit，删除`--jobs`；minimize复用search limits；
2. Runner/Scheduler只消费resolved max_cells；在Static/Runtime evaluator process seam建立独立ty/test
   invocation pools并以并发barrier证明上限和相互独立；
3. 证明三个limit有消费者、stage limits不进入argv、等待permit仍占Cell slot，resolution/install/witness不
   借用stage pool；
4. 四个verification-producing command在snapshot/process前统一执行test-command/test-group admission，
   包括empty-host；证明explain/apply/diagnose/merge不作该直接检查。

### 切片 006 — Identity、current-contract测试与公开文档

1. evaluation policy v1精确hashresolution artifact/timeout、ty args/timeout、test command/cwd/timeout及既有
   tool/diagnostic/verifier/failure facts；排除target/extra/group/search/scheduling；
2. SourceSnapshot remainder继续覆盖raw `[tool.pf]`；报告与Schema不序列化raw key；更新identity reader tests；
3. 删除只证明旧config/CLI语法消失的临时测试、旧fixture/helper与parallel merge/default逻辑；保留当前
   contract public-seam正向测试和required error/safety negatives；
4. 更新README用户配置/CLI示例和R006 jobs项状态，扫描全部公开旧字段与旧CLI名称。

### 切片 007 — Owner归并、生成物、全量验收与同步归档

1. 按D023 §11把稳定规则归并D001/D002/D003/D004/D006/D008/D012/D014；核对非目标owner无漂移；
2. 重建并复证受影响examples digest，运行Schema生成no-drift并确认Schema/prefix仍为v1；
3. 运行§6全部focused、Ruff、ty、3.10 coverage/full、顺序3.11/3.12 full、build、docs links/diff与
   deletion/ownership scans，回填精确结果和环境限制；
4. 按§5逐条审计D023 §12，缺少直接证据即继续实施；
5. 将R006 jobs项标为已解决，D023/P029标记完成并同时移入`docs/archived`，复查相对links与唯一owner。

## 5. Acceptance / evidence matrix

| D023 §12 | 实施切片 | 直接证据 | 状态 |
| --- | --- | --- | --- |
| AC1 root→member、replacement与explicit CLI | 001、005 | ConfigLoader root/member scalar/list/AoT tests；CLI omitted/explicit request与RunLimits tests | 已通过 |
| AC2 root一次、package namespace删除与local ownership | 001、006 | root observation identity、root/member unknown namespace；ProjectDiscovery旧parallel reader删除 | 已通过 |
| AC3 target Python收窄与platform Cell | 001、002 | requires-python拒绝扩大、显式platform Cell与省略推断测试 | 已通过 |
| AC4 policy+custom extra规范并集 | 001、002 | surface内/custom间/policy重叠去重及unknown extra测试 | 已通过 |
| AC5 managed/unmanaged variant整体替换 | 001、002 | persistent layer variant replacement与declaration ownership测试 | 已通过 |
| AC6 global/dep policy、AoT替换与资格 | 001、002、003 | inherit/clear/replace、duplicate、unknown/fixed/unmanaged binding及candidate测试 | 已通过 |
| AC7 search prerelease与uv delegation | 003、004 | explicit specifier内prerelease candidate测试；uv argv/env removal与context测试 | 已通过 |
| AC8 shared artifact admission | 003 | Candidate/UvAdapter/project+environment resolution测试；mismatch不能PASS | 已通过 |
| AC9 group组合与nested evaluator config | 002、005 | root/member同名group provenance；Static/Runtime public seam参数测试 | 已通过 |
| AC10 四命令early admission及其他命令豁免 | 002、005 | check/smoke/search workflow共同准入、empty-host pre-snapshot与minimize复用；read/apply测试 | 已通过 |
| AC11 三层调度消费者与identity排除 | 005、006 | Scheduler consumer、ty/test barrier pool、CLI override、argv与policy equality测试 | 已通过 |
| AC12 ConfigLoader/ProjectLoader唯一ownership | 001–006 | raw `pf` reader扫描只命中ConfigLoader；named binding只在ProjectLoader；无parallel default | 已通过 |
| AC13 wire/四类v1 identity/timeout/scheduling/examples | 003、004、006、007 | exact preimage/reader测试、apply uv drift复核、Schema no-drift与生成示例复证 | 已通过 |
| AC14 clean replacement与public current-contract tests | 001–006 | old symbol/key/flag/compat扫描为空；历史语法测试删除；三版本full suite | 已通过 |
| AC15 owners、README、R006、Plan证据与归档 | 006、007 | D001/D002/D003/D004/D006/D008/D012/D014、README、R006、链接/归档审计 | 已通过 |

## 6. 验证命令与证据槽

所有pytest使用`UV_CACHE_DIR=/tmp/pf-uv-cache`；显式full regression使用`--no-testmon`，三个Python版本在
同一工作树顺序执行。最终命令可按实际测试ownership调整文件清单，但不能缩小D023验收范围。

| Gate | 计划命令 | 结果 |
| --- | --- | --- |
| focused config/project | `uv run --python 3.10 pytest --no-testmon tests/test_config.py tests/test_project.py tests/test_candidates.py -q` | 通过：`131 passed in 0.19s` |
| focused resolution/evaluation | `uv run --python 3.10 pytest --no-testmon tests/test_uv_adapter.py tests/test_resolution.py tests/test_environment.py tests/test_evaluation.py tests/test_static_transition.py tests/test_check.py tests/test_smoke.py tests/test_search.py -q` | 通过：`203 passed in 0.93s` |
| focused scheduling/composition | `uv run --python 3.10 pytest --no-testmon tests/test_scheduling.py tests/test_verification.py tests/test_cli.py tests/test_search_workflow.py tests/test_authorization.py tests/test_report.py tests/test_report_schema.py -q` | 通过：`289 passed in 4.86s` |
| Ruff | `uv run --python 3.12 ruff check .` | 通过：`All checks passed!` |
| ty | `uv run --python 3.12 ty check` | 通过：`All checks passed!` |
| 3.10 full + coverage | `uv run --python 3.10 pytest --no-testmon --cov=pf --cov-report=term-missing -q` | 通过：`1481 passed in 28.83s`；`90.52% >= 90%` |
| 3.11 full | `uv run --python 3.11 pytest --no-testmon -q` | 通过：`1481 passed in 24.15s` |
| 3.12 full | `uv run --python 3.12 pytest --no-testmon -q` | 通过：`1481 passed in 25.26s` |
| build | `uv build` | 通过：构建`pf-0.1.0.tar.gz`与`pf-0.1.0-py3-none-any.whl` |
| generated | `uv run --python 3.12 python scripts/generate_report_schema.py --check`；`git diff --exit-code -- docs/schemas` | 通过：Schema无漂移；两个examples已由generator重建并经report schema tests读取 |
| docs links | `python3`遍历根目录及`docs/**/*.md`的Markdown相对链接并逐个`Path.exists()` | 通过：`Markdown relative links: OK` |
| diff | `git diff --check`；`git status --short`；`git diff --stat` | 通过：无whitespace error；scope只含D023实现/owner/测试/生成物 |
| deletion/ownership | `rg`扫描旧config属性/flag/prerelease resolver、raw `get("pf")`与三limit consumers | 通过：无旧属性/`--jobs`；raw reader只命中`src/pf/config.py`；三个limit均有执行consumer |

## 7. 决策、偏差与停止条件

- 2026-09-04：用户要求实现D023，视为接受该Design完整目标并授权实施；按AGENTS要求先建立本Plan，再编辑
  production code。
- 无未解决偏差。任何package-floor wire shape/version、dependency/floor语义、D003 coordinate descent、apply
  projection或新配置来源变化都必须先修订并重新接受D023，不能在本Plan或实现中暗改。
- 若实现证明shared artifact admission与uv能力、project-config identity或evaluator pool边界不能满足D023，
  先在本节与§8记录可复现证据并请求决定，不以compatibility layer或缩水语义绕过。

## 8. 行动、结论与证据日志

### 2026-09-04 — 接受与建立实施基线

- 行动：核对HEAD/worktree、D023、R006、文档治理、P027/P028格式、ConfigLoader/schema、ProjectLoader、
  CandidateBuilder、policy、CLI/workflow/scheduling/evaluation/resolution及相关测试关键字。
- 命令：`git status --short --branch`；`git log -8 --oneline --decorate`；针对上述Design、Plan、源码、测试与
  owner文档的`sed` / `rg` / `nl`静态读取。
- 结果：HEAD与Design基线均为`d6b8a40`；worktree只有用户提供的D023及同轨`docs/README.md`索引改动。
  生产仍有三层central package patch、flat EffectiveConfig、全局dependency requirement search-space、
  PF allow-prereleases、整config policy hash及无消费者config jobs；D023 AC1–AC15均有实际差距。
- 结论：D023已接受，且本Plan已在任何production修改前建立。下一步执行切片001，先用ConfigLoader public
  tests锁定新target contract，再原地替换schema/loader。

### 2026-09-04 — 切片 001：nested config 与两层 merge

- RED：将`tests/test_config.py`改为D023 public seam后，focused collection以
  `ImportError: cannot import name 'parse_scheduling_limit'`失败，证明旧loader没有目标interface。
- 实现：建立target/search/resolution/ty/test/scheduling nested frozen schemas、dependency-selection union与
  `RunLimits`；ConfigLoader只读取root/member两层，root target只读一次，删除central package namespace，
  实现variant/list/AoT整体替换、global/dep policy继承、canonical duplicate/strict type/default/组合校验。
- GREEN：`UV_CACHE_DIR=/tmp/pf-uv-cache uv run --python 3.10 pytest --no-testmon tests/test_config.py -q`
  → `28 passed in 0.04s`。
- 结论：AC1/AC2/AC5/AC6/AC12的config-only部分已有直接证据；ProjectLoader资格绑定与CLI override仍待后续
  切片，当前不能标记对应AC通过。下一步迁移PackagePlan与ProjectLoader consumers。

### 2026-09-04 — 切片 002：ProjectLoader binding

- 实现：ProjectLoader消费nested target/extra/test配置，拒绝扩大`requires-python`，将policy与custom extra
  surfaces取规范并集，组合root/member同名test group，并为全部managed searchable direct dependencies建立
  sorted `NamedSearchPolicy`；unknown/fixed/unmanaged override在此按最终配置失败。
- 测试：增加root/member group provenance、完整policy coverage、资格错误、extra去重与target Cell public tests；
  `PackagePlan.search_policy_for`成为CandidateBuilder唯一查询seam。
- 结论：AC3/AC4/AC5/AC6/AC9的project部分闭合，ConfigLoader不读取declaration/source事实。

### 2026-09-04 — 切片 003/004：candidate、artifact、prerelease与resolution identity

- 实现：CandidateBuilder按named policy应用space/step/prerelease与shared artifact，并将candidate policy v1缩到
  named policy + artifact；UvAdapter删除PF prerelease参数，按artifact投影binary flags并清除相关用户环境；
  EnvironmentFactory对project/environment registry selected artifact二次fail closed。
- 实现：SourceSnapshot从冻结root/target pyproject inputs计算`pf:uv-project-configuration:v1`；ResolutionContext
  删除PF prerelease fact并绑定该摘要。Apply在source waiver前复算比较；target dependency arrays先投影到
  report digest，因为其original/projected/no-op已由独立结构授权，其余uv project input漂移不可force。
- 审计修正：显式PEP 440 dep space最初使用`version in SpecifierSet`，会再次隐式排除policy允许的prerelease；
  改为`contains(..., prereleases=policy.prereleases)`并加入`>=0,<2`内`1.0rc1`见证测试。
- 结论：AC7/AC8/AC13的resolver/candidate/apply identity部分闭合，prefix与wire字段未变。

### 2026-09-04 — 切片 005：RunLimits、阶段pool与early admission

- 实现：四个CLI命令暴露`max-cells/ty-jobs/test-jobs` optional override；workflow在project load后一次解析
  positive `RunLimits`。Runner只把max-cells交给Scheduler，并配置composition root与evaluators共享的
  ty/test `BoundedSemaphore`；permit只包围真实ty/configured verifier调用，不包围witness/resolution/install。
- 实现：check/smoke/search workflow在snapshot前共同执行test command/group准入；search empty-host仍准入，
  minimize原样复用SearchRequest，read/apply/diagnose/merge保持豁免。
- 测试：public evaluator barrier记录分别证明ty/test上限与独立性；CLI证明omitted `None`和显式`auto|N`；
  process fakes证明stage limits不进入argv，empty-host fake证明snapshot尚未构造。
- 结论：AC10/AC11闭合。

### 2026-09-04 — 切片 006：identity、clean replacement与公开表面

- 实现：evaluation policy v1只绑定resolution artifact/timeout、ty args/timeout、test command/cwd/timeout及
  既有tool/diagnostic/verifier/failure facts；test group、target/search/scheduling排除。更新CLI help、README和
  R006 jobs处理状态。
- 删除：移除ProjectDiscovery对旧package-selection配置的parallel reader及历史语法测试；全部旧flat config
  schema、PF prerelease resolver字段、`--jobs`和test fixture旧名原地删除，不保留alias或dual branch。
- 生成：运行`scripts/generate_report_schema.py`重建两个最小examples；Schema本身无结构diff，v1 reader与
  identity测试复证新digests。
- 结论：AC12/AC14及AC13余项闭合。

### 2026-09-04 — 切片 007：owners、验证与验收审计

- 归并：D001/D002/D003/D004/D006/D008/D012/D014接管稳定配置、binding、candidate、evaluator、CLI、Run、
  uv与wire/identity规则；根README同步命令，R006 §2.1标为已解决。
- 中间修正：首轮离线full为`1479 passed, 3 failed`，三个失败均是迁移测试TOML漏`f`前缀；ty另报loader
  中间态类型收窄和fixture artifact类型两项。逐项最小修正后离线full为`1482 passed`；随后按clean
  replacement删除6个历史参数化case并增加current-contract tests，最终计数为1481。
- 最终验证：§6所有focused/static/full/build/generated/link/diff/deletion gates均通过；联网权限只用于三个
  end-to-end isolated-install测试的package index访问，不存在被掩盖的代码失败。
- AC审计：逐条复核D023 §12 AC1–AC15，全部具有直接实现、public-seam测试或静态ownership证据；无未解决
  偏差。稳定规则已归并，D023/P029可以同步归档。

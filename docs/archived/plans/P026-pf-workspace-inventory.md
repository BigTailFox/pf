# P026 — D020 PF WorkspaceInventory 模块深化实施计划

- **状态：** 已完成并归档
- **开始日期：** 2026-09-03
- **完成日期：** 2026-09-03
- **性质：** 非规范性实施计划、过程与证据记录
- **设计来源：** [D020](../designs/D020-pf-workspace-inventory.md)
- **评审来源：** [R005](../reviews/R005-pf-module-depth-review.md) §4、§11.2
- **实施基线：** `c1aff33`（`chore: hidden some cache directories`）

本文在生产代码修改前建立 D020 的实施顺序、interface/ownership 迁移、测试矩阵与证据槽。
每次实质行动后在 §7 记录行动、结论、精确命令与结果；完成标准只来自 D020 §12，不以局部绿色
测试、collection 或静态扫描替代验收。

## 1. 目标与边界

本轮完整实现 D020：

- 让 `ProjectDiscovery.inventory(root, selector)` 构造单次 planning invocation 的唯一、不可变
  `WorkspaceInventory`，让 target、member facts 与 owned paths 来自同一批 observations；
- 让 offline `select` 与 online `inventory` 复用一个 package catalog，但保持 offline discovery 轻量；
- 让 `ConfigLoader` 消费 root/target observations，让 `ProjectLoader` 消费一个 inventory，删除 planning
  阶段的重复 filesystem read、旧 workspace traversal 与 identity drift 补丁；
- 保持 `ProjectLoader.load`、`ProjectPlan`、SourcePlan、SnapshotBuilder、authorization、editor、report、
  CLI 与 Schema caller-facing contract 不变；
- 完成后把稳定 interface、owned-path 与 ownership 规则归并到 D002，核对 D001/D008/D014，更新 R005
  与索引，并将 D020/P026 同时归档。

不建立 filesystem port、跨 invocation cache、digest/wire/lifecycle、任意 document/member collection，
不扩大 offline error surface，不替代 SourceSnapshot/PyprojectIdentity/CAS，也不实施 R005 其它候选。

## 2. 基线事实与目标差距

| 切面 | `c1aff33` 当前事实 | D020 目标 |
| --- | --- | --- |
| discovery | `select` 与 `owned_pyproject_paths` 各自 glob/read；`_candidate_paths` 还为 identity 重读 member | `select`/`inventory` 共享 catalog；每个 inventory path 一份 observation/parse |
| planning | `ProjectLoader` 组合 `select`、target/root `_read`、`_workspace_members`、owned paths | 一个 inventory 提供 selected location、root/target observations、member point query 与 owned paths |
| config | `ConfigLoader.load(root, package)` 自行读取 root/target filesystem | `load(root_observation, target_observation)` 只合并/校验 observation documents |
| consistency | selection 后重读 target 并以 identity mismatch 补丁侦测局部 drift | selection 与 target planning 使用同一 immutable observation，旧竞态不可表示 |
| owned paths | workspace candidates 与 recursive path metadata 由独立 traversal 读取 | root、非排除 installable members 与 path closure 按 D020 §4 投影并排序去重 |
| evidence | SnapshotBuilder 在 planning 后再次读取 owned paths | 该执行证据观察有意保留；inventory 不携带 bytes/digest/identity |

## 3. Interface 与 ownership 迁移

1. `PyprojectObservation` 固定 canonical absolute path 与 recursively immutable TOML mapping；
   `WorkspaceMemberFact` 固定 canonical name、root-relative locator 与 static/dynamic version；两者是
   planning-internal immutable values，不进入 Pydantic Schema 或 workflow。
2. `WorkspaceInventory` 仅提供 `target`、`root_observation`、`target_observation`、排序唯一的
   `owned_pyproject_paths` 与 `workspace_member_for(canonical_name)`；构造后不访问 filesystem。
3. `ProjectDiscovery._catalog` 成为 `select` / `inventory` 唯一 catalog implementation，独占 root
   resolve、workspace glob/exclude、observation read/parse、installable identity、canonical uniqueness
   与 selector；selector 失败时不继续 planning-only validation。
4. `ProjectDiscovery.inventory` 在 selected catalog 上补齐 member version facts与 recursive path closure；
   path-only metadata 只贡献 owned paths，不成为 selector/member fact。
5. `ConfigLoader.load` 原地替换为 observation interface；继续独占三层 PF config merge/validation，
   root target 保持 root config → matching override → root package config。
6. `ProjectLoader.load` caller-facing interface 不变；每次只调用一次 `inventory`。ProjectLoader 继续独占
   declaration、Cell、source route、member-version attachment 与 harness planning。
7. 删除 `owned_pyproject_paths` public method、ProjectLoader/ConfigLoader `_read`、`_workspace_members`、
   identity 二次比较及其 obsolete test；SnapshotBuilder、authorization、editor/report interface 不变。

## 4. 实施顺序

### 切片 001 — Immutable observation、共享 catalog 与轻量 select

1. 在 `test_project.py` 先锁定 `select` 的 root/member、virtual root、include/exclude、unknown、duplicate、
   invalid-present name 与 legacy selection 行为；补未知 selector 先于 planning-only invalid metadata；
2. 实现递归冻结的 `PyprojectObservation`、private catalog 与 catalog selection；
3. 让 `select` 只消费 catalog，不读取 member version、recursive path/config/declaration facts；
4. 用 public state 断言 observation document 递归不可变，不断言 private helper 或 syscall 次数。

### 切片 002 — WorkspaceInventory member facts 与 owned-path closure

1. 先增加 inventory target/root-target observation、static/dynamic member point query、稳定排序测试；
2. 增加 root、未选中、excluded/path-reachable、path-only、missing、escape、cycle/duplicate closure 矩阵；
3. 实现 inventory 扩展阶段并复用 catalog observations；每个新 path 只 read/parse 一次；
4. 增加构造后磁盘 mutation witness，证明当前 inventory facts/documents 冻结且下一次构造才观察变化。

### 切片 003 — ConfigLoader observation seam 与 ProjectLoader 单 inventory

1. 把 `test_config.py` 改为直接构造 observations，覆盖三层 merge、默认值、validation、root-target
   same-path 语义与 recursive immutability；
2. 原地替换 `ConfigLoader.load` interface并删除 filesystem read；
3. 让 ProjectLoader 从同一 inventory observation planning target，并以 member point query 附加 version；
4. 增加 discovery mutation witness：inventory 返回前修改磁盘，最终 ProjectPlan 全部保留旧 target
   identity、declaration、config 与 member version；
5. 删除 loader `_read`、`_workspace_members`、identity comparison 与旧 drift test。

### 切片 004 — Workflow、snapshot/authorization 回归与旧 interface 清除

1. 证明 explain/diagnose 仍只调用 `select`，对未选中 planning-only version/config/path 错误不失败，
   且不创建 ProjectLoader、SnapshotBuilder、uv、environment 或 process 能力；
2. 锁定 online selector → inventory validation → config/target planning 的新错误顺序；
3. 回归 SnapshotBuilder、authorization、editor、CLI/report workflow 的 owned paths、source drift、
   dependency-array drift、PyprojectIdentity 与 raw CAS；
4. 扫描并删除旧 method、旧 read/traversal 与 compatibility/dual-read 痕迹。

### 切片 005 — Owner 归并、全量证据、验收与归档

1. 将 module/interface/ownership、offline/online seam 与 owned-path 精确规则归并 D002；核对 D001、
   D008、D014，仅在现行契约确实受影响时修订；
2. 更新 R005 与文档索引，运行 focused、Ruff、ty、3.10 coverage/full suite、顺序 3.11/3.12 full suite、
   build、生成物 no-drift、links、diff 和静态旧路径扫描；
3. 按 §5 逐项审计 D020 §12，任何缺证据项继续实施；
4. 将 D020/P026 标记完成并同时移入 `docs/archived/designs` / `docs/archived/plans`。

## 5. D020 §12 验收与证据矩阵

| 验收项 | 切片 | 主要 public 测试/检查 | 直接证据目标 |
| --- | --- | --- | --- |
| 1. inventory 是 online 唯一入口；load interface 不变 | 002–003 | `test_project.py` + production scan | 每 load 一次 inventory；caller 仍传 root/selector |
| 2. select/inventory 共享 catalog且 selection 语义不变 | 001–002 | discovery selection matrix | typed error、候选与 selector 优先；唯一 catalog owner |
| 3. 每 path 单 observation/parse；窄且递归不可变 interface | 001–003 | freeze/mutation witnesses + interface scan | 无 raw bytes、arbitrary documents/members、digest/wire/cache/lifecycle |
| 4. root/unselected/excluded/path-only owned rules | 002 | owned-path matrix | 稳定排序、精确 inclusion/exclusion |
| 5. member version inventory 唯一产生；route/apply 不变 | 002–004 | project/authorization tests + scan | static/dynamic facts；path-only 不校验；loader 不重读 |
| 6. recursive path closure 安全语义 | 002 | path cycle/duplicate/missing/escape tests | root containment 先于 existence；typed error |
| 7. ConfigLoader observation seam；ProjectLoader ownership | 003 | `test_config.py`, `test_project.py` | 无 filesystem I/O；三层 merge/root target；planning mutation witness |
| 8. 旧 interfaces/read/traversal/drift 全删除 | 003–004 | production/test `rg` | 无 alias、adapter、fallback 或 dual read |
| 9. offline 轻量、online 新错误时序 | 001、004 | explain/diagnose/project workflow tests | offline failure surface/capability 不扩大；selector 优先 |
| 10. Schema/report/snapshot evidence 不变 | 004 | snapshot/report/authorization/editor/CLI tests + generated artifacts | 无 inventory/raw TOML/identity wire；SnapshotBuilder authority 保留 |
| 11. public seam 测试策略 | 001–004 | focused matrix + test scan | mutation/static evidence；不 patch syscall/private helper/volatile full text |
| 12. Plan evidence、门禁、owner 归并与归档 | 005 | §7–§8 ledger/audit | 精确结果；D002/R005/index/archive 一致 |

## 6. 变更控制与验证命令

- Plan 建立时 HEAD 为 `c1aff33`；工作树内 `docs/README.md`、R005 与 D020 是本轮 Design 范围，未发现
  其它修改；实施不得覆盖无关后续修改；
- PF 尚未发布，目标 internal interface 原地替换；临时共存只允许作为未提交步骤，交付时不得保留
  alias、compatibility adapter、fallback、dual read 或旧行为测试；
- tests 断言 public behavior、typed error 与稳定语义片段；只在 root-target observation 与 mutation
  witness 所需处断言对象/快照一致性，不断言 private helper、syscall 次数或完整 parser/OS 文案；
- 默认 uv cache 不可写时使用 `UV_CACHE_DIR=/tmp/pf-uv-cache`；Python 版本 full suites 在同一工作树
  顺序执行，coverage、build、网络或 sandbox 限制与代码失败分别记录；
- 计划命令如下，实施时记录精确结果，不预填“通过”：

```text
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_project.py tests/test_config.py tests/test_snapshot.py tests/test_report_workflows.py tests/test_diagnose.py tests/test_authorization.py tests/test_editor.py tests/test_cli.py -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/generate_report_schema.py --check
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests
UV_CACHE_DIR=/tmp/pf-uv-cache uv run ty check src
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --cov=pf --cov-report=term-missing -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.11 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv run --isolated --python 3.12 --group test pytest --no-testmon -q
UV_CACHE_DIR=/tmp/pf-uv-cache uv build
git diff --check
```

## 7. 过程与证据记录

### 2026-09-03 — Design 接受与 Plan 建立

- **状态：** 已完成
- **行动：** 将用户“实现 D020”的指令落实为 D020 已接受、待实施；读取 D020 §2–§12、当前
  discovery/config/project seams、相关 tests 与前序 P025 证据格式；建立五个有序切片和十二项验收矩阵，
  同步 R005 与 docs index。Plan 落盘前未修改 production code。
- **结论：** 当前契约足以实施，无需修改 CLI、Schema、snapshot authority 或 offline failure surface；
  `WorkspaceInventory` 是 local-substitutable planning module，不增加 port/adapter。
- **当前证据：** `git rev-parse --short HEAD` → `c1aff33`；`ProjectDiscovery` 仍暴露 `select` 与
  `owned_pyproject_paths` 两条 traversal，`ProjectLoader` 仍有 target/root `_read`、`_workspace_members` 与
  identity comparison，`ConfigLoader.load(root, package)` 仍读取 filesystem。尚未修改 production code，
  尚未运行行为测试。

### 切片 001 — Immutable observation、共享 catalog 与轻量 select

- **状态：** 已完成
- **行动：** 先增加 inventory import/freeze/selector-order tests 并运行红灯；再实现 recursive-freeze
  `PyprojectObservation`、`PackageLocation` 与共享 `_PackageCatalog`，让 `select` 只返回 catalog selection。
- **结论：** root 与每个现有非排除 candidate 由 catalog 各观察一次；root target 复用同一 observation；
  unknown selector 在 version/path/config/declaration validation 前失败，offline `select` 不展开这些 facts。
- **证据：** 首次 `pytest --no-testmon tests/test_project.py tests/test_config.py -q` 在 collection 期因缺少
  `PyprojectObservation` 产生 2 errors；实现后 discovery/inventory targeted run 的 20 个相关 tests 通过，
  3 个失败均为尚未迁移的 ProjectLoader 旧 `owned_pyproject_paths` 调用，而非 catalog 语义失败。

### 切片 002 — WorkspaceInventory member facts 与 owned-path closure

- **状态：** 已完成
- **行动：** 实现 frozen `WorkspaceMemberFact` / `WorkspaceInventory`、canonical point query、static/dynamic
  version facts 与 root/workspace/path observation closure；补 root、unselected、excluded path-reachable、
  path-only invalid version、missing、cycle/duplicate、escape、root-exclude 与 filesystem mutation tests。
- **结论：** 只有 installable、非排除 workspace packages 产生 member facts；root 始终 owned；path-only
  metadata 只贡献 closure。Containment 在 existence 前验证，构造后 query 不访问 filesystem。
- **证据：** `tests/test_project.py::TestProjectDiscoveryInventory` 全部通过；八模块 focused matrix 中相关
  inventory/project/snapshot assertions 通过。冻结测试证明 nested mapping/sequence 不可修改，连续两次
  inventory 只在第二次观察磁盘变化。

### 切片 003 — ConfigLoader observation seam 与 ProjectLoader 单 inventory

- **状态：** 已完成
- **行动：** 将 `ConfigLoader.load` 原地替换为 root/target observations，迁移 config tests 并增加不存在
  于磁盘的 observation 直接测试与 root-target override precedence；让 ProjectLoader 调用一次 inventory，
  将 documents/member point query 传给既有 planning logic，删除 loader read、workspace traversal 与 identity
  comparison；增加 inventory 返回后修改 root/member 的 integration witness。最终验收审计又删除
  `ProjectLoader.load` 的前置 root resolve，改由 root observation 提供 canonical root，确保 catalog 独占
  invocation 的 root canonicalization。
- **结论：** ConfigLoader 与 ProjectLoader 不再 open planning metadata；declaration/Cell/source/harness 仍由
  ProjectLoader 生成，member version 由 inventory point query 提供；caller-facing `load(root, selector)` 与
  `ProjectPlan` 不变。
- **证据：** `pytest --no-testmon tests/test_project.py tests/test_config.py -q` → `112 passed in 0.17s`；
  mutation witness 同时锁定旧 name、config、declaration 与 workspace version。

### 切片 004 — Workflow、snapshot/authorization 回归与旧 interface 清除

- **状态：** 已完成
- **行动：** 增加 explain/diagnose 对 unselected invalid version 与 escaping path metadata 的 offline tests，
  增加 online inventory-before-target-planning error-order test；回归 snapshot/report/authorization/editor/CLI；
  扫描旧 method/read/traversal/drift 路径。
- **结论：** explain/diagnose 仍仅调用 `select`；online selector 成功后 inventory validation 先于 target
  planning。SnapshotBuilder/authorization/editor/report interface 未改，执行证据第二次读取仍保留。
- **证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon tests/test_project.py
  tests/test_config.py tests/test_snapshot.py tests/test_report_workflows.py tests/test_diagnose.py
  tests/test_authorization.py tests/test_editor.py tests/test_cli.py -q` → final rerun `265 passed in 4.74s`；
  `ruff check src tests` 与 `ty check src` 均为 `All checks passed!`；production/test `rg` 对
  `owned_pyproject_paths(`、identity drift、`_workspace_members`、ProjectLoader/ConfigLoader `_read` 与旧
  ConfigLoader 调用均无命中，`ProjectDiscovery.select` 只在 explain/diagnose 两个 offline workflow 命中。

### 切片 005 — Owner 归并、全量证据、验收与归档

- **状态：** 已完成
- **行动：** 将 inventory、observation、ConfigLoader/ProjectLoader seam、offline/online boundary 与精确
  owned-path 规则归并到 D002；核对 D001 的 snapshot 产品范围、D008 的 Run/snapshot lifecycle 与 D014
  的 SourceSnapshot wire，三者均未被本次 internal planning 迁移改变，故不修订；更新 R005、现行/归档
  索引，并将 D020/P026 同时归档。
- **结论：** D020 的稳定规则已有唯一现行 owner；R005 因 Verification Run、评价 seam、result-card 与
  SearchCoordinator test surface 仍开放而不归档。没有新增 Schema/report/generated artifact，也没有扩大
  inventory authority 到 planning invocation 之外。
- **质量证据：** 设计指定八模块 focused matrix → `265 passed in 4.74s`；
  `UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/generate_report_schema.py --check` → exit 0、无漂移；
  `UV_CACHE_DIR=/tmp/pf-uv-cache uv run ruff check src tests` 与同环境 `uv run ty check src` →
  `All checks passed!`；受控联网环境的 Python 3.10 coverage/full suite → `1446 passed in 29.15s`、
  `90.83%`，达到 `fail_under = 90`；随后顺序 Python 3.11 full suite → `1446 passed in 24.65s`，
  Python 3.12 full suite → `1446 passed in 26.02s`。
- **构建与静态证据：** `UV_CACHE_DIR=/tmp/pf-uv-cache uv build` 成功生成 sdist/wheel；`git diff --check`
  → exit 0、无输出；全仓 Markdown relative-link audit → `checked=326 missing=0`；旧 method/read/traversal/
  identity-drift scan 无命中，production 中 `.inventory(` 仅 `ProjectLoader` 一处，`.select(` 仅 explain/
  diagnose 两处。
- **环境记录：** sandbox 内首次 3.10 full suite 为 `1445 passed, 1 failed in 32.98s`；唯一失败是 E2E
  临时项目下载 `uv_build` 时网络被拒，Process Log 明确为 `Operation not permitted`。相同代码随后在受控
  联网环境通过 coverage/full suite，未把外部网络失败归为代码失败或弱化测试。

## 8. 最终验收审计

| D020 §12 | 最终证据 | 结论 |
| --- | --- | --- |
| 1 | `ProjectLoader.load` mutation/integration test；production 仅一处 `inventory` 调用；load/ProjectPlan interface 未变 | 通过 |
| 2 | root/member/virtual/include/exclude/unknown/duplicate/invalid-name/legacy selection tests；共享 `_catalog` 静态审计 | 通过 |
| 3 | recursive freeze、filesystem mutation 与 loader mutation witnesses；public fields/production scan 无 raw bytes/documents collection/digest/wire/cache/lifecycle | 通过 |
| 4 | inventory owned-path matrix锁定 root、unselected、excluded path-reachable 与 path-only inclusion | 通过 |
| 5 | static/dynamic/path-only inventory tests、ProjectLoader route tests与 authorization regression；loader workspace traversal 已删除 | 通过 |
| 6 | recursive cycle/duplicate/missing/escape tests；containment 在 existence 前形成 typed error | 通过 |
| 7 | ConfigLoader 直接 observation tests、root-target precedence、ProjectLoader mutation witness；ConfigLoader 无 filesystem import/read | 通过 |
| 8 | 旧 `owned_pyproject_paths` method、loader/config read、`_workspace_members`、identity comparison 与旧 drift test 的 `rg` 无命中 | 通过 |
| 9 | explain/diagnose planning-only metadata tests；unknown selector 优先与 online inventory-before-planning tests；production select 仅两个 offline workflow | 通过 |
| 10 | snapshot/report/authorization/editor/CLI focused regression、3.10–3.12 full suites与生成物 no-drift；ProjectPlan/Schema/report 无新增字段 | 通过 |
| 11 | discovery/inventory、ConfigLoader、ProjectLoader 与 workflow public-seam tests；mutation/static evidence；无 syscall/private-helper patch | 通过 |
| 12 | 本 ledger、focused/Ruff/ty/coverage/三版本/build/generator/links/diff/scan 精确结果；D002/R005/index/同归档闭合 | 通过 |

未决项：无。实施没有改变 D020 的 CLI、Schema、snapshot authority、offline failure surface 或非目标
module；sandbox 网络限制已由相同代码的受控联网全量门禁复核。

# D043 — 静态 subject v2、报告去 intern 与 Run 内 ty-cache

- **状态：** 已完成并归档；2026-09-09 通过 AC1–AC14 验收，稳定规则已由现行 owner 接管；实施与证据见 [P045](../plans/P045-pf-static-subject-v2.md)
- **日期：** 2026-09-09
- **性质：** 已归档临时迁移 Design；不再承担现行规范
- **目标 owner：** [D004](../../designs/D004-pf-ty-enhancement.md)、[D003](../../designs/D003-pf-search-algorithm.md)、[D014](../../designs/D014-pf-report-schema.md)、[D008](../../designs/D008-pf-verification-run.md)、[D012](../../designs/D012-pf-harness-relaxation.md)、[D001](../../designs/D001-pf.md)、[D002](../../designs/D002-pf-implementation.md)；吸收时同步 [CONTEXT.md](../../../CONTEXT.md) 词汇，并修订 [D039](D039-pf-static-evaluation-module.md) 的离线 admission / AC4 / AC10
- **验收标准：** [§12](#12-验收标准)
- **实施计划：** [P045](../plans/P045-pf-static-subject-v2.md)
- **来源：** [I002](../investigations/I002-pf-self-search-py310-static-collection.md)；对照 [R008 2026-09-09](../../reviews/R008-pf-search-performance-review.md)
- **关联：** [D007](../../designs/D007-pf-process-output.md) 仍只拥有 Process Log；ty-cache 不是 Process Log。[D006](../../designs/D006-pf-cli-enhancement.md) diagnose 不再展示静态材料。静态仍无 compatibility disposition。D039 与本文**不是**完全正交，见 §11。

本文保存已完成的静态 subject v2 迁移。I002 已证实的墙钟机制当时收成一份目标契约；稳定规则已归并现行 owner。正文保留迁移时的目标与理由，不再承担现行规范。实施与证据见 [P045](../plans/P045-pf-static-subject-v2.md)。

## 1. 结论

现行 `static-subject-v1` 把 snapshot、venv、解释器可执行文件、stdlib（及可选 libpython）整棵树编进 identity，并用 `unclosed-symlink` 要求这些树在已登记 root 内闭合。I002 上只有 3.10 的 `S_hi` 采集成功，于是打开整格静态 guidance 并 intern 数万文件；3.11/3.12 整格 `NO_HINT(anchor-unavailable)`。墙钟差来自「成功打开的静态工作量」，不是 pytest，也不是 `json.dumps`。

目标契约：

1. **身份**改为 `static-subject-v2`：绑定快照 digest、Cell、解释器 implementation/abi、**resolution projection**（解析给出的 artifact **集合**，不是安装后文件树）、以及拆分后的观测策略身份。同一 artifact 输入集合的安装结果视为可互换。不扫 venv/stdlib/ty 可执行文件树。
2. **fail-closed** 跟着新身份。宿主用户配置与快照外分析根为 `undeclared-analysis-root`。不跟随未知 symlink。
3. **`S_slice`** 对已有直接 PASS 的 `U` 经 **prepare-only 重建** 采集；`S_hi` 只约束 GLOBAL。
4. **公开报告**删除五张静态 intern 表，不新增静态审计表。每条公开 `ProbeObservation` 增加 **required-nullable** `selection_reason`（`dependency=None` 时为 `null`），以复现 guidance 是否改了搜索轨迹。
5. **`pf diagnose FAILURE_ID` 不展示静态材料**（报告命中与 Journal 回退皆然）。ty-cache 只服务 Run 内静态 lookup，不服务 Failure diagnose。
6. **Check** 与 D001/D004 对齐：highest **prepare** 成功后，无论静态 capture 以何种 `StaticContentUnavailable` 结束，都继续 lowest-direct。D008 §3.2 现行「无合法 `S_hi` 不得 declaration」废止。

ty-cache 落盘完整 `TyFactDocument`。三层身份分开：`TyObservationPolicy.identity`、`cache_identity`、`GuidancePolicy.identity`（D014 `guidance_policy_identity` 等于后者）。`TyCheckKey = (StaticSubject.identity, cache_identity)`。

预发布：干净替换，无兼容层、无双读。

## 2. 范围与非目标

**接受并吸收后替换：**

| 目标 | 替换内容 |
| --- | --- |
| D004 §2、§6、§10、§11、§12 | v2 subject / 拆分观测身份；废止六组文件输入作为 identity；§6 的安装树隔离不再约束 TyCheckKey；§10 公共报告不保存 TyCheck/比较；admission 按 §7 |
| D003 不变量 1、`open_static_slice`、§5、§11 | slice 独立采集与 `reprepare`；§11 去掉静态 intern 义务；公开观察增加 `selection_reason`（§8.1） |
| D014 §1.1、§1.3、§1.4、§3、§5 | guidance 按 §4（`guidance_policy_identity` = `GuidancePolicy.identity`）；删除五张 intern 表；`ProbeObservation.selection_reason` 按 §8.1（`dependency=None` 时 null）；merge/update 不再处理 static scopes |
| D008 §3.2、§4、§7、§8、§9、§11 | Check 与 D001 对齐；Journal **名称保持 `verification-journal-v3`，旧 intern 形状非法**；删除 report-side 静态 association；diagnose 不读 ty-cache |
| D012 §6 | `reprepare`；安装复证仍是 name/version |
| D001 §5、§6 | Check 行保持「任意静态 unavailable 仍 lowest-direct」；diagnose 行删除「展示与 Failure 关联的静态材料」；报告不是 ty-cache |
| D002 §7、§9 | `RunLogStore` 拥有 ty-cache；编排器不读文件树采集器 |
| D006 diagnose | 不渲染静态 association |
| D039 §4.1、AC4、AC10 | 报告侧无 intern scopes；AC10 排除本文字段与 identity 字节变化 |
| CONTEXT | Journal / ty-cache / 公开报告三分；diagnose 不审计静态 |

**保持不变：** D001 命令与退出码；D003 两阶段搜索、静态无 compatibility disposition、`evaluate(start)` 不重跑已有 baseline；D004 多重集减法、lookup 只读 / collect 占 permit、同 key 单次 ty、`V_hi` 空增量不重跑、**capture ty 失败仍验证 lowest-direct**；D005 Failure identity；D007 Process Log；D008 host Cell、报告命中 Failure 后不读 Journal；D013；64 MiB 上限；`.pf/` 排除出 SourceSnapshot。

**本文件不覆盖：** 跨 Run cache；git 管理的 ty-cache；另开 `.pf/<run-id>/`；跟随未知 symlink；D039 搬家本身；从报告再删中间 oracle Attempt；R008 的 hints / single-flight / materialize / xdist。

## 3. StaticSubject v2 与 resolution projection

`projection = static-subject-v2`。Identity 域 `pf:static-subject:v2`。`StaticSubject.identity` 就是 §3.1 整份预像的 digest，**不能再取子集**。ty-cache 中的 `document.subject` 必须恰好是这份记录，禁止未定义的额外字段。

### 3.1 Canonical preimage

```text
{
  "projection": "static-subject-v2",
  "source_snapshot_digest": "<64 hex>",
  "cell": { "package", "python_minor", "target", "extra_surface" },
  "interpreter": { "implementation", "abi" },
  "resolution_projection": [ ResolutionBinding, ... ]
}
```

`resolution_projection` 按 canonical name 排序。`interpreter.python_minor` 必须等于 `cell.python_minor`，不重复写入。不进入预像：补丁号、venv 路径、process environment、图 edges、request/plan/graph digest、attempt/proposal id、安装后 RECORD / 文件树。

### 3.2 ResolutionBinding

取自该 Proposal 实际使用的 plan（有 harness 则为 environment，否则 project）中的 `ResolutionPackage`。现行 pylock 里，**普通 registry 包的 `selected_artifact` 为 `None`**，只填 `available_artifacts`；只有 direct archive 才设 selected。投影必须能表示这种 plan，否则含传递 registry 依赖的环境会整格 `unavailable`。

```text
{
  "name": "<canonical>",
  "version": "<normalized> | null",
  "source": SourceIdentity,
  "artifact":
      { "kind": "selected", "content_hash": "sha256:<64 hex>" }
    | { "kind": "available-set", "digest": "<64 hex>" }
    | { "kind": "source-tree" }
}
```

`available-set` digest：

```text
sha256(
  "pf:resolution-artifacts:v1\0"
  + canonical_identity_json(
      sorted unique [(kind, filename, content_hash), ...]
    )
)
```

只哈希 `kind` / `filename` / `content_hash`，不含 locator。同一 artifact 输入集合视为可互换。

| 情况 | `artifact` | 不能形成时 |
| --- | --- | --- |
| 已有 `selected_artifact.content_hash`（direct archive 等） | `selected` | hash 非法 |
| registry / url 且 `selected_artifact is None`，`available_artifacts` 非空且每项有 hash | `available-set` | 任一项缺 hash → `resolution-artifact-unbound` |
| path / workspace / 项目自身 | `source-tree`；隔离靠 snapshot digest + `source.locator` | locator 缺失或越出快照 |
| git | `source-tree`；`source.commit` 必填 | 无 commit → `resolution-artifact-unbound` |
| registry/url 且 available 与 selected 皆空 | — | `resolution-artifact-unbound` |

D004 §6「相同 sdist 的不同安装内容相互隔离」**不再**约束 TyCheckKey。隔离停在 artifact 输入集合。

AC1 必须含正向用例：含普通 registry 传递依赖、且 `selected_artifact is None` 的真实 plan，能形成 v2 subject 并启动 ty（其它条件满足时）。

### 3.3 谁证明什么

| 层 | 证明 | 静态缓存不要求 |
| --- | --- | --- |
| `inspect_environment` | 已安装 name/version 与 plan graph 一致 | 文件字节、RECORD |
| plan `available_artifacts` / `selected_artifact` | 解析给出的 artifact 输入集合 | 装完后的树是否与另一次构建逐文件相同 |
| 静态层 | 把 §3.2 编进 `StaticSubject.identity` | 再读安装元数据 |

### 3.4 TyCheckKey 与 subject

```text
TyCheckKey = (StaticSubject.identity, TyObservationPolicy.cache_identity)
```

`StaticSubject.identity` 是 §3.1 的完整 digest。只有观测策略使用「完整 generation 文档 + cache 子集」（§4）。

## 4. TyObservationPolicy v2

### 4.1 字面量与 wire 形状

升版：`rules = ty-observation-v2`，`analysis_scope = explicit-static-subject-v2`。

保持：`owned_options`、`output_format`、`diagnostic_identity`、`process_environment`、`unavailable`、`observation` 的现行字面量。

**删除**嵌套 `config: TyConfig`。`args` 与 `timeout_seconds` 升到观测策略顶层（`timeout_seconds` 仍为 required-nullable）。删除 `tool_content`、`executable`。

新增：`snapshot_ty_config`（§4.3）；`host_config = reject-undeclared-v1`；`tool_version` 见 §4.2。

### 4.2 `tool_version` 与可执行文件一致

身份使用已安装发行版元数据，**不**把 `ty --version` 原文写入 digest：

```text
tool_version =
    { "kind": "distribution", "name": "ty", "version": "<PEP 440>" }
  | { "kind": "unavailable" }
```

`version` 来自宿主 `importlib.metadata.version("ty")`。元数据不可得时写 `unavailable`，仍能形成 `TyObservationPolicy.identity` 与 `GuidancePolicy.identity`。

运行期资格（不进入 identity）：

- **元数据 available：** 规范解析 `ty --version`，必须与 metadata version 相等；不等或探测失败 → 该次 collect unavailable，不启动 ty。
- **元数据 unavailable：** 不启动静态 collect。报告身份用 `tool_version.kind=unavailable`。

search / check 在静态 unavailable 时继续 verifier。一次 Run 至多读一次元数据、至多一次 `--version`。

### 4.3 `snapshot_ty_config`

```text
snapshot_ty_config =
    { "kind": "materialized", "digest": "<64 hex>" }
  | { "kind": "unavailable", "reason": "configuration-..." | "undeclared-analysis-root" }
```

`materialized` 才允许启动 ty。`digest` 是 PF 确定性物化、经 `--config-file` 交给 ty 的那份文件的精确 UTF-8 字节：

```text
sha256("pf:ty-config:v2\0" + effective_config_bytes)
```

物化规则（必须确定）：

1. 输入仅限 SourceSnapshot：**快照内 `TY_CONFIG_FILE`（若设置）为最高优先级**；否则快照内 `ty.toml` 与 `pyproject.toml` 的 `[tool.ty]`，按现行项目内优先级合并。指向快照外的 `TY_CONFIG_FILE` 见 §5.2，不物化。
2. 不读、不合并宿主用户配置。发现宿主用户配置 → `snapshot_ty_config.kind=unavailable`，detail `undeclared-analysis-root`。
3. **effective TOML 只含上述合并结果。** PF owned CLI overrides（`adapter-cli-overrides`）**不**写入该文件，继续只经 argv 传递，并由观测策略字面量绑定。
4. 写出一份 effective TOML。同一合并表经固定 tomlkit 路径必须得到相同字节。
5. `digest` 只哈希这份已写出、实际传给 `--config-file` 的字节。

`unavailable` 仍进入完整观测预像，因而仍能形成 generation / guidance identity。Check/Search 继续 verifier。

该字段同时进入 `TyObservationPolicy.identity`、`cache_identity` 与 §7 admission。

### 4.4 三层身份（不得写成同一个 digest）

完整观测预像（`timeout_seconds` 为 required-nullable；`snapshot_ty_config` 为 §4.3 union）：

```text
{
  "rules": "ty-observation-v2",
  "tool_version": { "kind": "distribution", "name": "ty", "version": "0.0.0" },
  "args": [],
  "timeout_seconds": null,
  "owned_options": "adapter-cli-overrides",
  "output_format": "gitlab",
  "diagnostic_identity": "snapshot-path-line-column-code+external-namespace-path-code",
  "analysis_scope": "explicit-static-subject-v2",
  "process_environment": "explicit-complete-v1",
  "unavailable": "typed-ty-process-and-protocol-v1",
  "observation": "run-first-observation-v1",
  "snapshot_ty_config": { "kind": "materialized", "digest": "<64 hex>" },
  "host_config": "reject-undeclared-v1"
}
```

cache 子集 = 同一预像去掉 `timeout_seconds`。

```text
TyObservationPolicy.identity
  = sha256("pf:ty-observation-policy:v2\0" + complete observation preimage)

TyObservationPolicy.cache_identity
  = sha256("pf:ty-observation-cache:v2\0" + cache subset)

GuidancePolicy.identity
  = sha256("pf:guidance-policy:v1\0" + complete GuidancePolicy preimage)

D014 guidance_policy_identity
  = GuidancePolicy.identity
```

`GuidancePolicy` 完整预像：

```text
{
  "rules": "static-guidance-v1",
  "observation": <§4.4 完整观测预像>,
  "observation_identity": "<TyObservationPolicy.identity>",
  "comparison": "multiset-subtraction",
  "fingerprint": "scoped-comparison-identity-multiset-v1",
  "anchors": "global-diagnostic-and-slice-anchor-v1",
  "bisection": "slice-local-static-bisection-v1",
  "authority": "advisory-v1",
  "unavailable": "no-hint-fallback-v1"
}
```

改变 subtraction / anchor / hint / fallback 规则必须改变 `guidance_policy_identity`。禁止把 `GuidancePolicy.identity` 写成观测 digest。

`TyCheckFact.observation_policy_identity` 绑定 `TyObservationPolicy.identity`。`TyCheckCache.lookup` 使用 `cache_identity`。ty-cache 落盘完整观测预像（含 timeout 与 `snapshot_ty_config` union）。

Run 内 timeout 是常量。以后收紧或放宽 cache 只改派生函数。禁止跨 Run cache。

教学 golden：锁定 observation identity、cache identity、GuidancePolicy identity 三组字节；另锁 `tool_version.kind=unavailable` 与 `snapshot_ty_config.kind=unavailable` 仍能算出三层 digest。

## 5. 采集、宿主配置、revalidate、unavailable

### 5.1 三条 census 必须同时停止

| 现行路径 | 目标 |
| --- | --- |
| `StaticContentCollector.collect` 对 snapshot / venv / stdlib / 解释器 / ty 可执行文件 | 构造 subject 不再走采集器 |
| inspect `-c` 枚举 `distribution.files` | 只核对 interpreter identity + 诊断前缀。name/version 复用 D012 |
| `revalidate` 再 `collect` 整树 | 见 §5.3 |

内存 subject / 观测策略不得携带 content manifest。不读 RECORD 编身份。

### 5.2 宿主 ty 配置：发现即拒绝

只承认快照内配置。下列任一出现 → `snapshot_ty_config.kind=unavailable`（detail `undeclared-analysis-root`），不启动 ty：

- 宿主 `ty/ty.toml` 存在（XDG / APPDATA / `HOME/.config`，只探测存在性）
- `TY_CONFIG_FILE` 指向快照外（**快照内 `TY_CONFIG_FILE` 合法，且为物化最高优先级**，见 §4.3）
- 向父目录走查离开 snapshot 副本
- argv / 快照内配置引用快照外且不是已冻结 extra root

### 5.3 决策表

`detail` 联合加入 `undeclared-analysis-root`、`resolution-artifact-unbound`。

| 情况 | detail | 启动 ty | 报告 identity |
| --- | --- | --- | --- |
| lease 关闭 / 已 `tested` / `inputs_valid=false` / 诊断前缀消失 | `content-changed` | 否 | 不受影响 |
| inspect name/version ≠ plan | `installed-input-mismatch` | 否 | 不受影响 |
| registry/url 无 selected 且 available 空或缺 hash | `resolution-artifact-unbound` | 否 | 不受影响 |
| 宿主配置或快照外分析根 | `undeclared-analysis-root` | 否 | `snapshot_ty_config.kind=unavailable` |
| extra root symlink 逃逸 | `unclosed-symlink` | 否 | 不受影响 |
| 元数据 available 且 `--version` 失败或与 metadata 不等 | 该次 collect unavailable | 否 | `tool_version` 仍为 distribution |
| 发行版元数据不可得 | 不启动 collect | 否 | `tool_version.kind=unavailable` |
| 快照内配置无法物化 | `configuration-*` | 否 | `snapshot_ty_config.kind=unavailable` |
| 同 TyCheckKey 的 cached unavailable | 返回缓存 | — | 不受影响 |

命令开始时固定一份完整 `TyObservationPolicy` 与 `GuidancePolicy`（`tool_version` / `snapshot_ty_config` 均可为 unavailable union）。其后任何静态失败不得让「无法构造 `guidance_policy_identity`」。

## 6. `S_hi`、`S_slice`、Check 与环境

1. Highest capture 在 verifier 之前 `collect_prepared`。失败则 `S_hi = UNAVAILABLE`，不以空诊断代替。
2. Runtime PASS 后环境 `tested` 并 close。不得在该对象上再 collect，不得长期保留全部 PASS 环境。
3. `open_static_slice`：上端已有直接 runtime PASS。lookup 命中则不再 prepare。未命中则 `reprepare` 后 collect。失败则该坐标 `NO_HINT`，不改写 `S_hi`，不产生 Failure ID。
4. `record_pass` 不要求静态 consumer。`evaluate(start)` 不重新 prepare/static/runtime。
5. **Check（吸收 D001 §5 + D008 §3.2）：** 步骤 1 highest **prepare** 失败 → 不进入 declaration。步骤 1 prepare 成功后，无论静态 capture 以何种 `StaticContentUnavailable` 结束（含 `resolution-artifact-unbound`、`undeclared-analysis-root`、配置物化失败、inspect unavailable、tool 资格失败），**都**继续 lowest-direct；GLOBAL 比较为 UNAVAILABLE。不得把结果说成「declared lower bounds failed because baseline missing」。这与现行 `CompatibilityChecker` 及 D004 §9 一致，纠正 D008 正文。

```text
reprepare(proposal, snapshot, source_plan) -> PreparedEnvironment | StaticContentUnavailable
```

首次 `prepare` 成功时，**同一个 Run 的 `EnvironmentFactory`** 按 `proposal_id` 留存私有、不可变的
`ReprepareRecipe`：原 `PackagePlan`、resolution kind / selection / harness baseline、已经接受的
project / environment `ResolutionPlan` 原生安装载荷及其 digest，以及 snapshot / source-plan
identity。它不进入 Proposal、报告、Journal 或任何 identity；关闭 `PreparedEnvironment` 不删除
recipe，Run / factory 结束时统一释放。

`reprepare` 必须从 recipe 重建：先核对传入 Proposal、snapshot、source plan 与 recipe 的 identity
和 plan digest，再重新 materialize snapshot、创建环境、安装**缓存的既有 plan**并 inspect；
不得重新 resolve。recipe 缺失、digest 不一致、安装或 inspect 失败均映射为
`StaticContentUnavailable`，由调用方按 `NO_HINT` 处理。

复用已有 Attempt/Proposal，不分配新 Attempt，不调用 verifier，不记录新的 resolve / oracle
Attempt，失败不写入该向量的 oracle `prepare_failures`。

## 7. Comparison admission

| 现行项 | v2 |
| --- | --- |
| snapshot / content 树 | `source_snapshot_digest` |
| package mappings | snapshot + path/git locator |
| source plan | preparation 上 identity 相等 |
| Cell / interpreter | subject 内字段 |
| analysis layout / process context | 删除 |
| configuration 树 | 两端 `snapshot_ty_config` 相等（含 unavailable union）+ `host_config` |
| harness | 保留 `admit_harness_relation` |
| observation policy | 两端 `cache_identity` 相等 |
| GLOBAL / SLICE | 同前：`S_hi` 状态；anchor PASS、窗口、固定坐标 |

不同 `TyCheckKey` 不得 COMPARED。

## 8. 公开报告

删除 `static_contents` / `static_subjects` / `static_facts` / `static_comparisons` / `static_scopes`。不新增 `static_audits`。旧 intern 报告字段校验失败即停；`update_path` 当缺席。

### 8.1 搜索轨迹：`selection_reason`

公开 `ProbeObservation` 不全是坐标 probe：baseline 起点与 final confirmation 的 `dependency=None`。同一 vector/evidence 可能被多次消费但只保存一条观察。

Schema 1 / 领域记录增加 **required-nullable** `selection_reason`：

```text
dependency is None:
    selection_reason = null          # baseline 起点、final confirmation

dependency is not None:
    mechanical-lowest
  | mechanical-midpoint
  | history
  | static-suspect
  | static-clean-neighbor
  | direct-existing                  # 本 Slice 已有直接证据的复用
  | external-hint                    # CoordinateSearch hints= 入口
  | current-upper                    # 当前 PASS 上端认证
```

现行运行期 `SearchProbeRequest.selection_reason` 的 `mechanical` 拆成 `mechanical-lowest` / `mechanical-midpoint`。该请求仍只表示坐标 probe，因此继续 required 非空。公开 `ProbeObservation` / `ProbeObservationV1` 覆盖 baseline 与 final，因此按上式可空。

去重后保存**首次进入该 Slice observation 集合的原因**，不是最近一次消费原因。

删除 static audit 后，reader **只验证字面量合法**（与 `dependency` 的 null 规则），**不能**离线证明 `static-suspect` 来自合法 `StaticHint`。它是生成轨迹，不是 authority，不能改变 PASS / boundary / Failure。

不保存 `static_search_ref`、hint 端点、omission、TyCheck、窗口标量。

D003 §5 / §11：CellSuccess 包括这些观察上的 `selection_reason`；不包括静态 intern。D014 `guidance_policy_identity` = `GuidancePolicy.identity`。

对 `package-floor.json` 的增量：删数十 MB intern；每条观察多一个可空字面量。

## 9. Journal 与 ty-cache

预发布原地替换：Journal **名称保持 `verification-journal-v3`**。下列旧形状一律非法，reader 不得 resolve、inflate 或忽略后继续：

- 顶层 `static_contents` / `static_subjects` / `static_facts` / `static_comparisons`
- 顶层 `static_scopes`（无论 intern 引用还是嵌入完整 observation）
- 任何 fact / comparison 成员携带完整 `observation` / `context` 载荷，或 `observation_identity` 指向文档级 intern 表

磁盘字段仍为 `schema`；内存 `VerificationJournal` 仍为 `schema_version`。无法解码或不支持的 contract 仍是 `JournalReadError(reason="unsupported-journal-contract")`；旧 intern 或非法 membership 为 `invalid-static-evidence`。

### 9.1 Journal 外层

```text
schema = verification-journal-v3
run_id
command = smoke | check | search
source_snapshot_digest
package_policies[]          # 按 package 唯一、升序
  package
  execution_policy_identity
entries[]                   # 按 (package, cell canonical, failure_id) 升序
  package
  Cell
  Role
  Attempt?                  # CellFailureScope 时省略
  FailureRecord
static_membership[]         # required，可空；按 Cell canonical 升序
```

轻量 membership 精确字段：

```text
static_membership[]:
  cell: { package, python_minor, target, extra_surface }
  highest:
      { "kind": "collected",
        "subject_identity": "<64 hex>",
        "cache_identity": "<64 hex>",
        "fact_identity": "<64 hex>" }
    | { "kind": "uncollected",
        "detail": "<StaticContentUnavailable detail>" }
```

`collected` 的三个 identity 必须能在同 Run 的 ty-cache 中按 `TyCheckKey` 找到对应 `document`，且 `document.fact.identity = fact_identity`。本表只供 **Run 内重建**，**不**供 diagnose，不按 Failure ID 挂 fact，不提供 selection static association。

这项跨文件闭合由 **writer admission** 和显式的 Run 内静态 audit reader 验证。供 Failure
diagnose 使用的普通 Journal decode 只验证 Journal 自身结构、字面量、排序和唯一性，**不得**打开
ty-cache，也不得因 ty-cache 缺失或损坏而使一个本来合法的 `FailureRecord` 不可读。

Cell canonical 排序：`(package, python_minor, target, extra_surface)`，字符串按 Unicode code point。

### 9.2 `pf-ty-cache-v1` 外层

路径：`.pf/logs/<run-id>/ty-cache.json`。完整外层：

```text
{
  "schema": "pf-ty-cache-v1",
  "run_id": "<与 Journal.run_id 相同>",
  "entries": [
    {
      "subject_identity": "<64 hex>",
      "cache_identity": "<64 hex>",
      "document": TyFactDocument
    }
  ]
}
```

`entries` 按 `(subject_identity, cache_identity)` 升序、唯一。`document.subject` = §3.1 完整 `StaticSubject`，且 `document.subject.identity = subject_identity`。`document.observation_policy` = §4.4 完整 generation 预像，且其 `cache_identity` 等于条目 `cache_identity`。`document.fact.observation_policy_identity` = `TyObservationPolicy.identity`。同 key 不同 payload、缺字段、或 `run_id` 与目录/Journal 不一致 → fail closed。

只保存 Run 内原始静态事实，供 lookup / collect 去重。不绑定 Failure ID，**不是** Failure 诊断输入，不进入报告。

### 9.3 事务

先原子写完整 ty-cache snapshot，再写 Journal（`static_membership` 若引用 fact，该 fact 必须已在
ty-cache），最后才更新 latest / diagnose index：

- ty-cache 写失败：本次 persist 不写 Journal、不更新 latest；经现有 RunLog persistence failure
  边界返回。更早已经提交的合法 Journal 不受影响。
- ty-cache 成功而 Journal 写失败：允许留下无引用 sidecar；不更新 latest。重试按 canonical
  snapshot 原子覆盖，不从该 sidecar 恢复 oracle 状态。
- Journal 成功而 latest 更新失败：Journal 仍可按 run-id 读取，但不得把未提交的 latest 位置宣称为
  `diagnose_available`。

`diagnose_available` 只要求 Journal + Failure 的 **verifier** Process Log association，不要求
ty-cache。删除 Index 的静态/Failure 交叉映射与 report-side 静态 association。

## 10. Diagnose

`pf diagnose FAILURE_ID` 只查 Failure 权威：

```text
1. 报告命中 → 只展示报告 Failure；不读 Journal；不读 ty-cache；不渲染静态
2. 否则 latest Journal 命中 → 只展示 Journal Failure；不读 ty-cache；不渲染静态
3. 都没有 → D001 配置错误
```

ty-cache 只用于 Run 静态审计。删除一切「Journal 回退展示辅助静态材料」的措辞。D001 §5 diagnose 行改为不展示静态材料。

## 11. 与 D039

报告侧不再 inflate 静态表。比较重放只在测试夹具或 Run ty-cache 的 `TyFactDocument` 上。AC10 不涵盖五表删除、`selection_reason` 与 v2 identity 字节。先吸收本文，再搬家。

## 12. 验收标准

| AC | 必须成立的目标 | 公开 seam / 证据 |
| --- | --- | --- |
| AC1 | `resolution_projection` 对 `selected_artifact is None` 的普通 registry（含传递依赖）形成 `available-set` 并得到 v2 subject；不读 RECORD | 真实 plan 正向测试；D004 |
| AC2 | subject / 运行期对象不含文件树；inspect 不访问 `distribution.files` | key 预像；扫描 |
| AC3 | 宿主配置与快照外根 → `undeclared-analysis-root` | capture 测试 |
| AC4 | Run-owned recipe 驱动 `reprepare`（不 resolve / Attempt / verifier）+ 独立 `S_slice`；highest prepare 成功后任意 `StaticContentUnavailable` 仍 lowest-direct | Search / Check 公开测试 |
| AC5 | 报告无五张静态表；`selection_reason` 在 `dependency=None` 时为 `null`，坐标观察为 §8.1 枚举；`--check` 通过 | 生成投影；D003/D014 |
| AC6 | 旧 intern 报告短路径失败；`update_path` 当缺席 | reader 测试 |
| AC7 | ty-cache 外层为 `pf-ty-cache-v1`（`schema` / `run_id` / `entries`）；条目为完整 document；`TyCheckKey = (subject.identity, cache_identity)`；fact 绑 generation identity；Journal 名称保持 v3 且拒绝旧 intern；§9.3 三种部分失败不产生悬空 latest / membership | RunLog / collect / journal reader 测试 |
| AC8 | diagnose 两条路径都不读 ty-cache、不展示静态；Journal 回退在 ty-cache 缺失/损坏时仍能读取合法 Failure | diagnose 测试；D001/D006/D008 |
| AC9 | `tool_version` 为 distribution \| unavailable 时都能形成三层 identity；metadata available 时 `--version` 必须相等否则不启动 ty；metadata unavailable 时不 collect | golden + Check/Search |
| AC10 | §7 admission；不同 key 不得 COMPARED | compare 测试 |
| AC11 | §5.3 决策表；`snapshot_ty_config` 为 materialized \| unavailable union；仅 materialized 的 digest 哈希 `--config-file` 字节；unavailable 仍形成 generation / guidance | unavailable / 配置测试 |
| AC12 | 静态不分配 Failure ID | 扫描 |
| AC13 | D039 按 §11 修订 | D039 diff |
| AC14 | D001 §5、D008 §3.2/§8–§9 与本文一致 | owner 正文 |

停止条件：普通 registry plan 无法形成 subject；`--version` 失败或 metadata unavailable 导致无法写报告；metadata 与 PATH 中 ty 版本不一致仍启动 collect；`reprepare` 重新 resolve、分配 Attempt 或缺失 Run-owned recipe；diagnose 读取/依赖 ty-cache 或仍展示静态；cache / Journal 部分失败提交了悬空 membership / latest；`StaticSubject.identity` 再取子集；`GuidancePolicy.identity` 写成观测 digest；配置无法物化时无法形成 policy；缓存绑 RECORD；公开报告再 intern 静态表；`selection_reason` 无法表示 baseline/final；Check 因任意静态 unavailable 跳过 lowest-direct；Journal reader 仍接受旧 intern；静态授权 PASS/boundary。

## 13. 建议切片与证据槽

| 顺序 | 工作 | AC |
| --- | --- | --- |
| S1 | resolution_projection（含 available-set）、三层 identity、配置 union、admission | AC1–AC3、AC9–AC11 |
| S2 | Run-owned recipe、`reprepare`、slice、Check 与 D008 对齐 | AC4 |
| S3 | 删五表；`selection_reason`；生成投影 | AC5、AC6 |
| S4 | `pf-ty-cache-v1` 外层、Journal v3 拒绝旧 intern、writer/audit 与 diagnose reader 分离、部分失败事务、diagnose 彻底去静态 | AC7、AC8、AC12 |
| S5 | 全部目标 owner / D039 / CONTEXT 吸收并归档本文、Plan 与 I002 | AC13、AC14 |

S1–S4 是一次不可拆分合并的 contract-cutover；不得为中间态建立「v2 payload + 旧 intern
表」codec。Plan 证据槽（非硬门禁）：venv/stdlib/tool 目录 walk = 0；inspect 不读 `files`；
40-fact persist 墙钟；新报告体积与 `ReportStore.read`；3.10 卡片与收尾分段（对照 I002）。

## 14. 明确不做

- 跨 Run cache；把 ty-cache 纳入快照或 git。
- 跟随未知 symlink；宿主 ty 配置读进 digest。
- 用安装后文件树隔离同一 artifact 集合。
- 让 `diagnose FAILURE_ID` 展示或依赖静态材料。
- 用本文件关闭 R006/R008/R010 其它项，或实施 D039 搬家。

## 15. 接受状态

已完成并归档。2026-09-09 接受并实施 AC1–AC14；稳定规则已归并 D001/D003/D004/D008/D012/D014/D002/D006 与 CONTEXT，D039 AC4/AC10 已按 §11 修订。正文保留迁移时的目标与理由，不再承担现行规范。

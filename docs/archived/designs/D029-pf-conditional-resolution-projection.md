# D029 — PF 条件 resolution 节点投影

- **状态：** 已完成，归档（临时迁移规则已由稳定 owner 接管）
- **日期：** 2026-09-05
- **基线：** `8dce32e`；开始时工作区干净
- **来源：** [E004 §7](../../experiments/E004-requests-validation-surfaces.md#7-后续诊断python-311-的条件节点投影缺口)
- **实施：** [P035](../plans/P035-pf-conditional-resolution-projection.md)
- **稳定 owner：** [D002](../../designs/D002-pf-implementation.md)、[D012](../../designs/D012-pf-harness-relaxation.md)、[D014](../../designs/D014-pf-report-schema.md)

本文限定为修复实际解释器下的条件图投影。用户已授权该方向与实施顺序；不改变 build failure disposition、
搜索算法或 floor/apply 资格。稳定规则已归并到 owner，D029/P035 同步归档；本文件只保存迁移决策，不再承担现行契约。

## 1. 问题与依据

E004 中 uv 在 Python 3.11.15 正确跳过 marker 为 `python_full_version <= '3.11'` 的 tomli，PF 却将其
纳入预期图，导致 smoke/check/search 的 py3.11 Cell 在安装图复证失败。现行流程还在解析后才观察真实
interpreter，解析只传 Python minor，因此仅在最后比较处忽略缺项无法闭合 project constraints、harness
ownership 与 resolution identity。

[pylock 安装规则](https://packaging.python.org/en/latest/specifications/pylock-toml/#installation) 要求先求
package marker，再校验生效节点的 Python compatibility 和唯一性。[uv Python 版本设置](https://docs.astral.sh/uv/reference/settings/#python-version)
允许 patch；省略 patch 表示最低 patch，并不等于最后实际安装的 interpreter。

## 2. 生命周期与身份

EnvironmentFactory 保持唯一 lifecycle owner，顺序变为：

1. 建立 Run context、可用于准备失败的初始 Attempt；物化隔离源码。
2. 创建空 venv 并 inspect interpreter，验证 CPython 与 Cell minor 一致；此时没有安装依赖。
3. 用已观察的 InterpreterIdentity 完成 ResolutionContext 与 Attempt identity。
4. project resolve → active project graph → constraints / harness normalization → environment resolve。
5. exact project inclusion 与 artifact/source 资格 → 安装 native plan → 安装图复证 → Proposal。

`ResolutionContext.interpreter` 保存完整 InterpreterIdentity，进入 context digest；`None` 只表示步骤 2
以前的准备失败上下文，不能提交给 resolver。该状态不是旧接口兼容模式。准备失败仍具有真实 Attempt，
不伪造 interpreter；创建/检查失败不会发起 resolution，temporary resources 始终清理。

UvAdapter 的两种 resolve 方法接收实际 interpreter path，传给 uv `--python`，并以 context 的完整版本
设置 `--python-version`；两次解析和安装使用同一 executable。临时路径不进入语义 identity。
Context validation 拒绝不匹配 Cell minor、非 CPython 或不含完整 patch 的 interpreter identity。

## 3. Native lock 到 active graph

`parse_uv_pylock(content, *, python_version, target, source_root, lock_root)` 接收实际完整版本和 exact target；
不再用 minor 假造 `.0`。uv_lock 是 native 条件投影的唯一 owner；不新建 public module/service。

可求值变量为 `python_version`、`python_full_version`、`implementation_name`、`implementation_version`、
`platform_python_implementation`、`sys_platform`、`os_name`、`platform_system`、`platform_machine`。
版本/implementation 来自已资格化 CPython，platform 值由 Cell target 推导。不读取运行 PF 的 host marker
默认值补缺；platform release/version、extra/extras/dependency_groups 等超出此 single-use profile 的变量
fail closed，包含在逻辑短路的另一分支时也不猜测。非空 multi-use groups/extras/environments 保持不支持。

投影顺序：

1. 校验 native 格式、实际版本覆盖及 markers 的语法/变量资格；求每个 package 的活跃性。
2. 仅生效节点参与 source/artifact/package requires-python 验证。False marker 节点不产生 pin、constraint、
   direct harness satisfaction 或安装预期；不改写 native lock 让无效包变成有效包。
3. 在生效节点集合上检查 canonical name 唯一；互斥同名节点可选出一个，同时活跃的同名节点报错。
4. 对 active package 的可选 dependencies references 按 native 字段匹配原始节点；只引用 inactive 节点的
   边从 active graph 去除。未知引用或活跃歧义报错，不能靠过滤掩盖缺失节点。保持 active name graph 闭合。
5. 返回规范排序的 active ResolutionPackages；marker 原文语义可保留作 provenance。Native content/digest
   仍保存 uv 输出及既有路径归一化结果，交给 uv 安装。

EnvironmentFactory 的 exact inclusion 与 installed name/version equality 继续严格成立；不增加“忽略缺包”
例外。Source/artifact 与 graph ownership 检查消费同一 active graph。

## 4. 证据与版本

Context、request cache key、resolution digest 和 Attempt context digest 绑定实际 interpreter。固定
`validation_contract_policy.resolution_projection = actual-interpreter-target-active-pylock` 进入 evaluation-policy
preimage，使旧投影证据不能取得当前 merge/apply authority。沿用现行不同 generation update_path 替换。
不增加公共 report wire 字段，不升级 Schema 1 或 v1 prefixes；重生成 examples 并检查 schema。

## 5. 验收标准

1. Parser 覆盖 3.11.0 与 3.11.15 条件差异、Linux/macOS/Windows 与 architecture、implementation markers；
   unsupported/invalid markers 与实际版本不覆盖均 fail closed，无 host 值回退。
2. inactive 节点和相应依赖边消失，互斥同名节点唯一选择；active 重复、未知引用及 active requires-python
   不满足仍失败。Native content 未被 active filtering 改写。
3. public UvAdapter tests 证明 exact Python path/version argv、active project constraints、external harness
   satisfaction、失败分类；不只测 parser helper。
4. public EnvironmentFactory tests 证明 create/inspect 在两次解析前，context/Attempt 绑定实际版本，两次
   resolve/install interpreter 相同；创建/检查失败无 resolution 且清理；真正安装图漂移继续失败。
5. Interpreter 改变影响 context/request identity；evaluation policy 隔离、Schema/examples、report/apply tests
   保持正确。所有 role 共用修复后的 prepare 路径。
6. E004 的三份 py3.11 lock/metadata 离线回放全部 active name/version 相等；用小型真实 uv fixture 验证
   conditional node projection，并对 requests py3.11 至少一个 effective Cell 做针对性 smoke 验证。
   不重跑完整 search，不把原 E004 的 15-Cell 实验追认为新运行。
7. 完成 focused tests、Ruff、ty、三 Python 全套、coverage 门禁、build、生成物/链接/whitespace 检查。
8. D002/D012/D014 吸收稳定规则，必要的 D001 生命周期措辞对齐；E004 追加修复证据；D029/P035 同步归档。

## 6. 非目标与风险

不改变构建失败的分类、候选范围、first-probe 策略、pytest authority 或 partial-floor 授权。提前创建空
venv 增加失败解析前的准备成本，但避免解释器选择和 metadata/marker 环境不一致；不增加第二次安装。
Native profile 不支持的条件维度继续明确失败，不能借该修复声称任意 universal lock 均可投影。

## 7. 完成记录

8 项 AC 均已验收，逐项证据见 P035；E004 §10 保存三组回放、真实 uv fixture 与 py3.11+socks
针对性 smoke PASS。Python 3.10/3.11/3.12 各1644 passed，coverage90.25%，Ruff/ty、build和生成物通过。
没有重复完整 search，未改变构建失败的证据资格，也未产生新的 floor。

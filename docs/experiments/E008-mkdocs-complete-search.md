# E008 — MkDocs smoke/check/search 完整实验与 witness 证据偏差

- **状态：** 已完成（运行数据整理与证据分析；witness 问题未修复）
- **日期：** 2026-09-06（Asia/Shanghai；run-id 使用 UTC）
- **性质：** 非规范性 dogfood 实验事实，不定义新契约
- **前序：** [E007](E007-mkdocs-baseline-and-build-failures.md) 的实验准备与构建失败调查
- **目标：** `experiments/mkdocs`，MkDocs `1.6.1`，上游 commit
  `2862536793b3c67d9d83c33e0dd6d50a791928f8` 加本地实验配置
- **PF：** report generator `0.2.0`；failure policy `failure-execution-v3`
- **整理时 PF HEAD：** `8a232e92c0555731b52a5412052a50afec53c6d6`；报告没有运行时 PF Git commit，不能用整理时 HEAD 代替
- **契约入口：** [D003](../designs/D003-pf-search-algorithm.md)、
  [D004](../designs/D004-pf-ty-enhancement.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)、
  [D008](../designs/D008-pf-verification-run.md)、[D014](../designs/D014-pf-report-schema.md)

本轮 smoke **5/5 PASS**；check **2 PASS、3 witness REJECTED**；search **5/5 SUCCESS、complete**，
形成 14 个受管依赖的可表示投影。每个最终组合都有完整 725 项 unittest 的 PASS 日志。搜索成功卡片
仍展示最后拒绝的 Markdown predecessor，不能把卡片上的被探测版本当成最终 floor。

关键发现是 **witness 检查了错误的成员**：诊断指向 `UnescapeTreeprocessor`，实际 plan 却检查
`markdown.treeprocessors.treeprocessors`。此外，MkDocs 对原诊断位置已有 `AttributeError` 回退。
因此本轮证明了 PF 能完成该矩阵并找到完整测试通过的组合，但没有可靠证明 Python 3.10–3.12 的
原声明下界不兼容，也没有可靠证明其 Markdown floor 必须提高到 3.4.4。

## 1. 输入、配置和运行身份

用户按 `pf smoke → pf check → pf search → pf explain` 执行；日志 root 为
`/home/llh/pf/experiments/mkdocs`。本次整理只读取现有报告、Journal、process logs 和源码，
没有重跑这三个实验命令，没有 apply，也没有修改 PF 或 MkDocs 的实现、测试、依赖声明。

实际范围为 CPython **3.8、3.9、3.10、3.11、3.12** × `x86_64-unknown-linux-gnu` × `i18n`。
终端显示 5 Cells、14 active packages、0 pinned；3.10–3.12 的向量有 13 个依赖，3.8–3.9 因
`importlib-metadata` marker 多一个。14 是本矩阵的受管依赖并集，不是每个 Cell 的向量长度。

沿用 E007 的准备：

```toml
[dependency-groups]
pf-unit = ["mkdocs[i18n]"]

[tool.pf]
pythons = ["3.10", "3.11", "3.12", "3.8", "3.9"]
test-group = "pf-unit"
extra-policy = "none"
test-command = ["python", "-m", "unittest", "discover", "-s", "mkdocs", "-p", "*tests.py"]
```

`mkdocs[i18n]` 自引用形成 required surface；项目声明保留实验上界
`pathspec >=0.11.1,<=0.12.1`。该上界用于避免 E007 的测试前提问题，不是本轮搜索发现的上界。
`min-versions` 不在本轮矩阵内，其 `==` 声明仍会出现在 explain 的 requirement inventory 中，
显示 `fixed · not managed`；这与当前 0 pinned 并不冲突。Windows-only colorama 在此没有适用 floor。

搜索使用 `registry-series-slice-v1`：artifact `any`、排除 prerelease、resolution `minor`；
有声明下界使用 `majors[declaration-1:]`，无下界默认 `majors[baseline-2:]`，本轮受管依赖均有下界。
每个 minor 的合格精确 release 代表进入冻结候选集；没有执行 patch refine。
完整配置留存于 [configuration.toml](data/E008/configuration.toml)，逐 Cell 候选代表见
[search-spaces.json](data/E008/search-spaces.json)。配置文件是整理时副本，运行事实以报告输入、
Journal 身份和实际 argv 为准。

| 命令 | run-id | 用户终端结果 |
| --- | --- | --- |
| smoke | `20260906T134727.794924Z-1981921-bab846e8` | Smoke passed · 5 cells |
| check | `20260906T134751.403165Z-1983047-f49c33f2` | Check failed · declared lower bounds are incompatible · 5 cells |
| search | `20260906T135016.816969Z-1985424-0e216e2c` | Search complete · package-floor.json |
| explain | 用户未提供独立 run-id | complete；report evidence is eligible for apply；14 managed dependencies |

用户没有附 shell `$?`，本文不反推这些原始命令的退出码。三次运行的 Journal 确认相同 source snapshot
与 evaluation policy；search 使用本次自己的最高版本 baseline，并非复用 smoke 的 PASS。

```text
source snapshot:   4c3be6e0310545f6fb975effced8b7312bad8811e524ab626092915e175644be
evaluation policy: 1ef95705455bc6d384caa6134badd2c04eba866f2e29df5d8c948d86002d4e01
report generation: 2a2366faedbb773fb7507c8872bd0d3eabac7f3660616f7fd37096938c954090
```

## 2. smoke/check：正向执行证据与负向判定分开看

下表时间来自用户终端的单 Cell 卡片，不是整条命令的精确 wall time。

| Python | smoke | check | search | check 的实际终止阶段 |
| --- | --- | --- | --- | --- |
| 3.8 | PASS，14s | PASS，13s | SUCCESS，6m30s | 完整 unittest |
| 3.9 | PASS，14s | PASS，13s | SUCCESS，6m39s | 完整 unittest |
| 3.10 | PASS，12s | REJECTED，4s | SUCCESS，6m28s | witness；未运行该 lowest-direct 组合的 unittest |
| 3.11 | PASS，11s | REJECTED，4s | SUCCESS，6m20s | witness；未运行该 lowest-direct 组合的 unittest |
| 3.12 | PASS，13s | REJECTED，3s | SUCCESS，6m28s | witness；未运行该 lowest-direct 组合的 unittest |

smoke 5 个 configured verifier 均运行 725 tests、正常退出 0；3.8 skipped=6，其余 skipped=4。
check 的 3.8/3.9 同样运行 725 tests 并通过，Markdown 实际安装 **3.3.6**，PyYAML 实际安装 **5.1**。
三次失败 check 的 Markdown 也为 3.3.6，失败 ID 分别为：

- 3.10：`failure-3429301f9f233dd2`
- 3.11：`failure-23e5e1577a80c758`
- 3.12：`failure-272d7403cf4b7c6a`

这些 FailureRecord 为 `witness / RUNTIME_INTERFACE_MISSING / REJECTED`。
终端的“声明下界不兼容”是 PF 本次给出的结论，不能替代对 witness 是否正确的审查，详见第 4 节。

## 3. search 最终 floors 与证据规模

下表以 `final_proposal_ref → Proposal.managed_vector → PASS Evaluation` 和 report projections 为准。
所有 Cell 均完成 3 sweeps。每个最终组合的 verifier 都运行了 725 tests 并正常退出 0；3.8 skipped=6，
其余 skipped=4。固定日志编号见 [search-summary.json](data/E008/search-summary.json)。

| 依赖 | 原声明下界 | Python 3.8–3.9 floor | Python 3.10–3.12 floor |
| --- | --- | --- | --- |
| Babel（i18n） | 2.9.0 | 1.0 | 2.7.0 |
| click | 7.0 | 7.0 | 7.0 |
| ghp-import | 1.0 | 0.6.0 | 0.6.0 |
| importlib-metadata | 4.4，Python <3.10 | 4.4.0 | 不适用 |
| Jinja2 | 2.11.1 | 2.9.6 | 2.10.3 |
| Markdown | 3.3.6 | 3.3.7 | 3.4.4（predecessor witness 有缺陷） |
| MarkupSafe | 2.0.1 | 1.1.1 | 1.1.1 |
| mergedeep | 1.3.4 | 1.3.4 | 1.3.4 |
| mkdocs-get-deps | 0.2.0 | 0.1.0 | 0.1.0 |
| packaging | 20.5 | 19.0 | 19.0 |
| pathspec | 0.11.1，另有 <=0.12.1 | 0.10.3 | 0.10.3 |
| PyYAML | 5.1 | 5.1.2 | 5.1.2 |
| pyyaml-env-tag | 0.1 | 0.1 | 0.1 |
| watchdog | 2.0 | 1.0.2 | 1.0.2 |

八个依赖的已验证代表低于原声明下界，四个不变（4.4/4.4.0 视作同版本），两个数值提高。
这表达当前源码、环境和 oracle 的执行结果，不证明上游应放宽支持政策，也不证明各依赖任意组合兼容。
Babel、Jinja2、Markdown 生成按 Python minor 分组的 marker；pathspec 保留 `<=0.12.1`；
importlib-metadata 保留 `<3.10`。本轮没有验证其他平台、Python minors 或额外 surfaces。

两个容易误读的地方：

1. 3.8–3.9 的 `Markdown=3.3.6` 和 `PyYAML=5.1` 已在 check 通过。search 提到 3.3.7/5.1.2，
   是 minor 代表粒度导致；不能声称较早 patch 已被拒绝。3.10–3.12 的 3.4.4 同样不是“最早可用 patch”。
2. 成功卡片 `search completed at [markdown=3.3.7][3.3.7~3.4.4#2]` 展示结束时的探测边界，
   对应最终 floor **3.4.4**；3.8–3.9 的最后 predecessor 为 **3.2.2**，最终 floor 为 **3.3.7**。
   卡片的残留错误摘要是拒绝证据的摘要，不是最终组合失败，也不是完整搜索只做了两次探测。

报告证据规模如下；201 个 FailureRecords 是中间 Attempt 的拒绝，不是 201 个失败 Cell。

| 证据 | 数量 |
| --- | ---: |
| Attempts | 308 |
| Proposals / static evaluations | 231 / 231 |
| terminal evaluations | 226 |
| PASS / verifier rejected / runtime interface missing | 102 / 88 / 36 |
| resolution graphs | 135 |
| FailureRecords | 201 |
| resolve-project：RESOLUTION_CONFLICT / RESOLUTION_FAILED | 42 / 35 |
| test：VERIFIER_EXITED_NONZERO | 88 |
| witness：RUNTIME_INTERFACE_MISSING | 36 |

所有报告 FailureRecords 的 disposition 都是 REJECTED，没有 INDETERMINATE。
231 次 Proposal 准备使用 `environment_plan_digest = null`，与该 self-reference group 没有剩余外部
harness 依赖一致；日志仍有 231 次项目安装，不能把空 external harness 理解成跳过项目安装。

## 4. 主要发现：witness 的目标与拒绝权限

### 4.1 已确认：成员恢复错误影响了真实判定

MkDocs 的冻结源码 [rendering.py.txt](data/E008/rendering.py.txt) 第 15 行是：

```python
_unescape = markdown.treeprocessors.UnescapeTreeprocessor().unescape
```

用户终端和 ty diagnostic 都指向 `UnescapeTreeprocessor`，但 check 的五个探针实际执行同一目标：

```json
{"module":"markdown.treeprocessors","operation":"has-member","owner":"markdown.treeprocessors","symbol_or_member":"treeprocessors"}
```

它询问的是 `markdown.treeprocessors.treeprocessors`，不是原代码所访问的成员。
check 的 3.10–3.12 返回 `CONFIRMED_MISSING` 后立即拒绝；3.8–3.9 返回 `NOT_APPLICABLE`，
继续完整 unittest 并通过。真实 argv、协议 stdout 和终态已保存于 [diagnostics.txt](data/E008/diagnostics.txt)。

search 最终三个 Markdown predecessor 也保留同一错误 plan：

| Python | 被拒绝 predecessor | Failure ID |
| --- | --- | --- |
| 3.10 | 3.3.7 | `failure-edf6d29148a777e3` |
| 3.11 | 3.3.7 | `failure-012c874a7ee4cfd1` |
| 3.12 | 3.3.7 | `failure-90e320ccfe21792b` |

当前 [StaticTransitionClassifier](../../src/pf/static_transition.py) 的 `_member_target` 将无 alias 的
`import markdown.treeprocessors` 存成 `markdown → markdown.treeprocessors`，覆盖前面的
`import markdown` 映射；随后选中内层 `markdown.treeprocessors` AST Attribute，将 `treeprocessors`
再次作为 member。源码与冻结 plan 相互印证这一错误路径。D004 要求唯一恢复精确目标，复合/多义位置
应降级 general；本例的强分类没有满足该要求。

因此，这不是单纯诊断措辞不准确，而是错误目标的负向证据进入了 check disposition 和搜索边界。
报告 reader 能校验结构、引用和身份一致性，不能替代对 AST 目标语义的正确性验证。

### 4.2 独立风险：可选接口不等于必需接口

即使纠正 member 名称，原源码仍显式捕获 `AttributeError`：

```python
try:
    _unescape = markdown.treeprocessors.UnescapeTreeprocessor().unescape
except AttributeError:
    _unescape = lambda s: s
```

这表示项目有意支持缺失接口时的回退。孤立 `getattr` 确认属性不存在，不能独自证明项目无法运行。
本轮 3.8–3.9 的完整 check PASS 是该原声明组合可执行的直接证据；3.10–3.12 尚未绕过 witness
运行同一组合的完整 verifier，本文不宣称它们已经通过。

跨 Python 的 Markdown floor 分组至少受到 witness 路由差异影响，不能直接当作 MkDocs 的真实
Python 兼容分界。当前 witness harness 用 `AttributeError.obj/name` 精确归因；不同解释器上的异常
信息差异是进一步核查方向。本轮冻结了不同返回值，没有另做异常属性的解释器矩阵复现。

本次只记录发现。后续应分别处理精确 AST 目标恢复和受保护访问的负向证据资格，并在修复后重跑
check/search；不能在本文中手工改写已生成报告的 floor 或失败事实。

### 4.3 其他边界有不同的实际依据

并非所有 Python 分组都来自同一问题。最终 predecessor 的代表 process logs 显示：

| 代表 Cell / predecessor | 实际 verifier 症状 |
| --- | --- |
| 3.11 / Babel 2.6.0 | localization 的 `Translations` 缺失及 fallback 相关断言失败；725 tests，2 failures、2 errors |
| 3.11 / Jinja2 2.9.6 | 从 `collections` 导入 `Mapping` 失败，引发 discovery/import errors |
| 3.9 / Jinja2 2.8.1 | 模板没有 `tojson` filter，构建相关测试失败 |
| 3.9 / Markdown 3.2.2 | `markdown.htmlparser` 导入失败 |

这些是完整 configured verifier 的非零结果，与“孤立成员探针拒绝”应分别评估。
Babel 2.6.0 的更深导入原因未做单变量复现，不仅凭 fallback 症状断定具体依赖根因。

## 5. PF 的表现与性能观察

### 5.1 已取得的能力证据

与 E007 原始 21-Cell incomplete search 相比，本轮在明确的 5-Cell 配置下完成了报告与投影。
这是 prepared configuration、generic unittest、执行失败拒绝和完整最终向量验证共同工作的真实项目证据；
两次矩阵、配置及策略不同，不能据此声称算法在同一工作负载上提速。

本轮仍遇到 Jinja2 2.0 的 Python 2 构建语法失败，代表 `process-0614.log` 保留正常 exit 1 和
`SyntaxError: multiple exception types must be parenthesized`。报告使用 `failure-execution-v3`，
该类 prepare 失败不再令整个 Cell 以 Indeterminate 停止，搜索继续到可用组合。这为
[D036/P041](../archived/designs/D036-pf-execution-failure-contract.md) 的执行拒绝方向提供补充 dogfood 证据。
但 35 个 `RESOLUTION_FAILED` 没有细分成可靠 build attribution，不能统称为已确认构建不兼容。

### 5.2 成本主要在完整测试，尚无受控优化对照

5 个 search Cell 卡片耗时 380–399 秒，均值 389 秒，最大差 19 秒。卡片相加为 1,945 秒，
不是整条并行 search 的 wall time；用户输出没有精确命令起止时间、CPU/RSS 或冷/热缓存对照。

从 1,922 份 process logs 的 `duration_seconds` 汇总得到：

| 子进程类别 | 次数 | 累计秒数 |
| --- | ---: | ---: |
| unittest | 190 | 1,486.01 |
| 项目安装（uv pip sync） | 231 | 100.30 |
| 项目解析（uv pip compile） | 308 | 94.16 |
| ty | 231 | 29.48 |
| witness | 112 | 5.48 |
| 其他进程 | 850 | 24.47 |

unittest 占可见子进程累计时间约 **85.4%**。这不是 wall-time 占比或理论可节省时间；并发重叠、
Python 内部工作、registry 请求和调度开销不由该表完整表达。
190 次均使用原完整 discovery argv，没有 `-f`，也没有 pytest observer/pruning；102 次 PASS，88 次非零。
PASS 每次都必须运行完整配置命令。失败路径是否能由标准 unittest fail-fast 节省成本，适合后续受控比较；
本轮没有性能对照，不能量化收益，更没有树搜索优于现有算法的证据。

### 5.3 展示与结果解释

smoke/check/search 的阶段分离使基线可执行、声明组合判定和搜索结果可以独立审查；完整 report、
FailureRecord、witness plan 和 process log 也让本次错误目标能够被追踪。
同时有两个用户体验问题值得后续评估：成功 search 卡片保留 predecessor 错误、`[baseline]` 向量与
末尾候选容易混读；explain 重复展开各 Python 的大量相似候选及未选中的 min-versions 声明，主结论难以定位。
这些是本次输出的可读性观察，不是已接受的界面改动。

`pf explain` 的 `report evidence is eligible for apply` 是离线报告资格结论，且明确提示
`current project was not inspected`；不代表完成当前源码/配置授权检查，不代表实际 apply，也不消除
witness 语义缺陷。最终 PASS 组合的正向证据成立，部分“更低候选必须拒绝”的负向证据需要修复后重新取得。

## 6. 固定证据、复核与局限

- [search-summary.json](data/E008/search-summary.json)：报告 SHA、身份、声明/投影、计数、baseline/final
  Proposals、最终 PASS、每项 predecessor FailureRecord 与关联 Evaluation。
- [search-spaces.json](data/E008/search-spaces.json)：本次冻结的逐 Cell minor 代表、candidate/policy/inventory
  identity 与最高版本选择；不随以后 registry 变化重算。
- [process-evidence.json](data/E008/process-evidence.json)：三次 Journal 身份、失败记录摘要、所有 unittest/witness
  进程的原始文件 SHA、实际安装版本、Python、协议结果、测试摘要与按进程类别汇总的耗时。
- [diagnostics.txt](data/E008/diagnostics.txt)：五个 check witness、两个 check PASS、五个 final-vector PASS、
  代表 predecessor 测试失败和 Jinja2 2.0 构建失败日志；冗长重复 traceback 中段明确标为摘录省略。
- [configuration.toml](data/E008/configuration.toml)、[rendering.py.txt](data/E008/rendering.py.txt)：整理时配置
  与关键源码副本；rendering 文件 SHA 与报告 source snapshot 条目一致。

本地原始报告为 `experiments/mkdocs/package-floor.json`，14,208,342 bytes，SHA-256：

```text
dc835610ab704da1c9eb1ef3e3cd8c6707ff30211cbe5476cbe67c9ce02b366e
```

原始日志目录为 `experiments/mkdocs/.pf/logs/<run-id>/`。本轮保存的摘要不替代完整报告的离线授权；
可变报告和临时日志后续可能覆盖/清理。用户终端输出已按命令、Cell、时间和 floors 归纳在本文，
没有声称附件是整份终端逐字转录。

执行以下只读 public reader 复核（PF 仓库根、沙箱外、无 registry 查询）：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from pf.report import ReportStore
r = ReportStore().read(Path('experiments/mkdocs/package-floor.json'))
print(r.report_generation_id, r.result.status, len(r.cell_results))
PY
```

结果为本篇列出的 generation、`complete`、`5`，退出 0。另核对 final vectors 与实际安装版本及
完整 PASS 日志、三份 Journal identity、候选代表和投影一致性、JSON/TOML 可解析、文档相对链接及
`git diff --check`。这些是整理验证，不是重跑实验或修复 witness 的行为验收。

结论只覆盖本次 Linux target、五个 Python minors、i18n、当前源码/测试契约与冻结候选空间；
不覆盖 MkDocs 上游独立 integration 入口、未选 extra、其他平台、未探测 patch 或完整版本区间。

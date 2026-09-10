# R010 — PF 工程文档与实现一致性审计

- **状态：** 开放
- **日期：** 2026-09-06
- **性质：** 非规范性 Review；记录核对结果、问题与建议，不授权生产实施
- **基准：** 审计开始于 `30c5d7d`；期间出现独立 `8a232e9` 提交及 Pydantic 下界工作区变更，均保留
- **范围：** 11 份现行 owner Design、文档治理/导航、README/CONTEXT、开放 Review/Concept、7 份实验、生成投影与资格入口
- **排除：** 本轮开始前的 72 个归档文件只检查完整性/链接，不修改历史内容；未修改生产代码或测试
- **后续契约入口：** [工程文档索引](../README.md)；各行为的唯一 owner 由该索引定位

## 2026-09-10 状态核对

§2.2 Journal Role/冲突 admission 已按现行 D008 在 Journal decode 与公开 read seam 关闭；
§2.1 已于 2026-09-09 关闭。§2 不再有开放实现偏移。
PEP 508 机械规范化仍按 §4 原交接：不单独抽模块、不合并 owner；邻近改动才可抽 private helper。
假想 Protocol 无新增整改（`FailureLogAssociations` 已删除）。
[R011](../archived/reviews/R011-pf-architecture-review.md) 已归档。
现行开放项只剩 §4 的 ty × Python / 真实 host 发布资格，以及 targeted-runtime-contract floor
（若推进须先固定 argv 与产物）。

## 2026-09-08 状态核对

文档治理标准已写入 [工程文档索引](../README.md) 与 [AGENTS.md](../../AGENTS.md)。
§1、§3、§7 保留本 Review 的历史整改记录，不再作为现行工作项。
现行开放项只有 §2 实现偏移与 §4 工程事项。本轮不启动那些实现，也不重写 owner 正文。
R007 已从现行 `docs/reviews/` 移除；入链改为[归档正文](../archived/reviews/R007-pf-current-improvement-priorities.md)。
§3 核对入口里的 `static_transition.py` / `runtime_witness.py` 是 2026-09-06 快照，D038 已删除这些模块。

## 1. 结论与处理边界

现行文档的主要问题是迁移后旧句未清理、消费方重复定义 owner 规则，以及历史 Review 被当作当前流程说明。
本轮修订已有目标契约的残留和文档归属，不改变搜索、失败、授权或 wire 语义。明确的实现偏移有两项，见 §2；
没有把实现缺陷吸收成正常契约。未发现的路径不等于已证明没有偏移；本次是 owner 与公开 seam 的针对性审计，
不是全量运行资格、所有平台验证或性能实验。

已完成的治理调整：

- docs/README 只拥有分类、生命周期、单一权威和导航；实施门槛引用 AGENTS.md，避免复制两套流程。
- 区分当前行为、已接受目标与历史证据；允许明确来源的使用摘要和生成投影，但不得形成第二套规范。
- 增加文档/代码冲突分流、固定实验产物、静态与运行证据区别、开放项交接和归档冻结规则。
- 索引删除逐次迁移的长摘要与验收计数。R007 已归档，开放项逐项交接见 §4。
- 已完成实验继续在 experiments 保留固定证据；没有把 D036 前的失败处置或 E002 的旧计数改写成新结果。

## 2. 实现偏移

### 2.1 P2 NO PASS 文案夸大已验证范围

**触发与影响：** search 或 explain 展示 `NO_PASS_IN_SEARCH_SPACE` 时，
[terminal](../../src/pf/terminal/__init__.py) 的 `_SEARCH_COMPLETION_REASONS` 写出
`The configured search space was fully evaluated`。这会使用户误以为所有候选或版本组合均已验证。

**应有契约：** [D001 §1](../designs/D001-pf.md#1-结果承诺) 与
[D003 §7、§9](../designs/D003-pf-search-algorithm.md#7-一维定界) 只承诺冻结空间中的坐标搜索，
不承诺穷举、未观察 hole 或笛卡尔积。空候选也能在 candidate-discovery 产生同一 reason。
D006 的旧“完整评估”描述是消费方越界，本轮已修正；代码及断言仍待修复。

**当前证据：** 通过 `CoordinateSearch.minimize`，99 个候选、空间外 baseline 100、仅 baseline PASS，
得到 `NO_PASS_IN_SEARCH_SPACE` 时只探测 8 个空间内候选：1、50、75、87、93、96、98、99。
这证明“完整评估”不能按逐候选/逐组合验证理解；算法行为本身符合 D003。
[现有搜索测试](../../tests/test_search.py)覆盖虚拟 sentinel；
[coordinator 测试](../../tests/test_search_coordinator.py)覆盖空候选只有 highest preparation；
[terminal 测试](../../tests/test_terminal.py)反而把该过度措辞锁定为期望。

**代码状态（2026-09-09）：** P044/D041 已把 terminal 文案改为有限结论
「No applicable floor was found in the configured search space under PF's search rules.」
reason 与退出码不变。本项关闭；§2 仅剩 §2.2。

### 2.2 P2 Journal reader 接受 Role 错配与冲突条目

**触发与影响：** 本机 latest Journal 中，同一有效 exact-vector Attempt 的 Role 被改为 baseline，
或同一 Failure ID 同时存在两条不同 Role 的 entry，`RunLogStore.read_latest_journal` 仍接受。
`DiagnoseCommandWorkflow.run` 顺序选择第一个命中项，可能给 probe failure 展示错误的 baseline impact；
冲突记录的顺序影响结果。影响范围是本机诊断，未发现由此扩大 report/apply authority。

**应有契约：** [D008 §2、§4、§7](../designs/D008-pf-verification-run.md#2-attempt-request-与-role)
固定 command/Role/request 关系，并要求同 Failure ID 不同 portable entry fail closed。
Role 不进入 Failure ID 不意味着 reader 可以忽略 Role 的闭合。

**当前代码：** [VerificationJournalEntry/VerificationJournal](../../src/pf/schemas/evaluation.py)
验证 Cell/Attempt、package policy 与 snapshot，但不验证 command/Role/request 组合或 entry 冲突；
[RunLogStore.read_journal](../../src/pf/runlog.py)直接采用该 model；
[DiagnoseCommandWorkflow](../../src/pf/workflow.py)读取首个匹配 entry。

**复现：** 从合法 search Journal 出发，只把 exact-vector entry 的 Role 改为 baseline，reader 返回
`request=exact-vector role=baseline`；追加原始 probe entry 后，reader 返回 2 entries、1 distinct Failure ID。
没有修改 Attempt 或 failure authority、没有绕过 Failure ID 复算。

**修复归属与验收：** D008/RunLogStore 的 Journal admission，保持 D005 Role-independent identity。
从公开 read seam 覆盖三命令合法 Role、错误 Role/request、同 ID 冲突 entry 与确定读取；
无效 Journal 不得向 diagnose 提供错误 impact。既有历史 reader 分支须明确其适用验证边界，
不能用本次修复无意引入另一套 failure identity。

**代码状态（2026-09-10）：** Journal decode 拒绝 command/Role/request 错配、非 search-probe 的
Cell-scoped entry，以及同 Failure ID 重复条目。`RunLogStore.read_journal` 映射为
`JournalReadError(reason="unsupported-journal-contract")`；diagnose 不得从错配 Journal 展示
impact。本项关闭；§2 已全部关闭。

## 3. 已修复的文档偏移与权威重复

| 所有者 | 原问题与本轮修复 | 当前实现/测试核对入口 |
| --- | --- | --- |
| D001 产品/配置/候选 | 删除 D036 前“空间内 build failure 停止”；吸收原散在 D003 的 artifact admission；merge 细则引用 D014，工具资格引用 D012 | `config.py`、`project.py`、`markers.py`、`candidates.py`、`adapters/uv.py`；config/marker/optional-group tests |
| D002 模块结构 | 补 markers.py 布局；删除重复 Run 字段/时序、prepare 阶段与算法复用细则，改指 D008/D012/D003；保留接口与模块责任 | `cli.py`、`project_discovery.py`、`environment.py`、`search.py`、`verification.py`、`workflow.py` |
| D003 算法 | 候选规则回归 D001、identity 回归 D014、产品依赖/测试约束回归 D002；保留 Slice/region/定界/终止与冲突 seam | `coordinate_search.py`、`search.py`；公开算法测试与 §2.1 复现 |
| D004 静态/witness | 命令序列改引 D008；删除历史变体清单；明确静态策略是 `ty_diagnostic_policy` 子对象，字段 `policy` 与代码一致 | `static_transition.py`、`adapters/ty.py`、`adapters/runtime_witness.py`、`policy.py`；ty/witness tests |
| D005 失败 | diagnose 参数回归 D001；重复观察/cache 冲突表引用 D003，保持执行事实/分类/身份唯一归属 | `failure.py`、`schemas/evaluation.py`；execution-contract/prepare-execution tests |
| D006 展示 | 删除第二份退出码表和选项语义；更新 verifier 示例；收回 NO_PASS 完整评估承诺并记录代码偏移；澄清 check capture rejection/indeterminate summary | `terminal`、`cli.py`；execution-run/CLI/terminal tests |
| D007 输出 | 澄清只有适用 authority 才把 ProcessResult facts 带入 Failure ID，identity 规则引用 D005 | `adapters/process.py`、`runlog.py`；runlog tests |
| D008 Run/Journal | 磁盘字段实际是 `schema`，内存才是 `schema_version`；Role 与冲突验证的实现缺口留在 §2.2 | `verification.py`、`runlog.py`、`schemas/evaluation.py`；execution-run/runlog tests |
| D012 准备/harness | 删除“只有已认证无解可拒绝”；Role 表引用 D008；安装图坏观察实际是 `graph-observation-invalid`；HARNESS_CONFLICT 可来自 original baseline harness，不只 relaxed | `environment.py`、`resolution.py`、`harness.py`、`adapters/uv_lock.py`；prepare-execution/optional-group tests |
| D013 observer | 复核 summary/cases 可选性与 verifier authority 分离；仅更新核对日期，未发现需改动的行为契约 | `adapters/test_command.py`、`adapters/pytest_observer.py`；configured-verifier tests |
| D014 wire | “两个 nullable 字段”改为引用 §1 全部路径；分类/绑定验证委托 D005；静态 policy 子对象指向 D004 | `report.py`、`schemas/report.py`、`policy.py`；schema generator/report-schema/authorization tests |

另修复 E001 的可变根报告链接：固定为 `git show 0bc8550:package-floor.json`，已核对其 generation/snapshot
与实验记录一致。E004 的 D012 旧章节锚点改为当前位置；两者均不改变历史运行事实。
R008 已于 2026-09-08 按现行 D003/D038 全文重评，不再把 09-04 的 region 流程当现行算法。
原文「每个 Slice 从最早候选开始」「总是两次解析/venv sync」不是现状。E002 的耗时与计数保持历史口径。
坏 existing report 已由 D014 update_path 当作缺席处理，撤销基于“晚失败”的 preflight 候选。

## 4. R007 开放项交接

[R007 归档](../archived/reviews/R007-pf-current-improvement-priorities.md)保留历史证据；
其 host-partial、CI coverage、composition、中断和报告路径整改均已完成，不再在现行索引复述。

| 原事项 | 接收者与本轮状态 |
| --- | --- |
| verifier 成本、region/hints、single-flight、materialize、xdist | R008 继续拥有。2026-09-08 已按现行 D003/D038 全文重评：region 免 pytest 候选撤销；开放项为 hints 接线、single-flight、materialize、xdist 与当前 HEAD 分阶段基线。E002/E005/E006 不能外推为现行墙钟收益 |
| report preflight | R008 已撤销旧晚失败理由；未来预警属于新的产品判断 |
| 非 TTY 活动、terminal-private result-card | R006 继续拥有；不因本轮文档整理启动实现 |
| E001 artifact 链接漂移 | 本轮已修复，见 §3 |
| targeted-runtime-contract floor | 本 Review 接收；未找到独立的该标签实验。E001 full-repository floor 与后续根报告不能自动充当 runtime-only floor；若推进先固定验证 argv 与产物 |
| ty × Python / 真实 host 发布资格 | 本 Review 接收；当前 CI 是 Ubuntu × Python 3.10/3.11/3.12，ty exact pin 与 synthetic tests 不等于动态诊断矩阵。发布支持范围与真实 macOS/Windows 资格仍待明确 |
| 假想 Protocol | `FailureLogAssociations` 已删除，其他 seam 不能仅凭一个生产实现删除；无新增独立整改 |
| PEP 508 机械规范化局部重复 | 本 Review 接收为低优先级相邻改动建议；`report.py` / `authorization.py` 仍各有规范化，但必须保留独立重求值的授权检查，不据此合并两个 owner |

此处接管剩余状态，不复刻 R007 的完整证据或再维护一个跨 Review 总优先级清单。
C001–C003 仍为开放构想；未因 D033/D036 已完成而关闭独立证据缺口。

## 5. 文档归并与重构建议

以下保留首轮建议；用户随后要求完成可进行的文档重构，落实情况与新的 owner 划分见 §7。

1. 保留 11 个 owner 的职责划分。D005 的分类、D008 的 Role/运行、D014 的 wire/reader 相互引用即可；
   合并成一份大文档会降低问题定位能力，本轮已先删除重复规范。
2. 后续若候选 DSL 继续扩展，可把 D001 §4 的完整候选策略独立为长期 owner，再让 D001 只保留产品摘要。
   拆分必须一次迁移 admission、采样、baseline selection 与 reader 的引用，不能新增平行说明。当前无需立即拆。
3. D006 的视觉样式、折行与固定文案可在未来按阅读需求移到同 owner 的附录，主文保留通道、状态与信息层级。
   这不要求新增展示模块，也不应趁整理改变用户界面。
4. R006/R008 保留原证据与带日期的状态核对；只有开放项全部解决或明确接收后再归档。
   不合并两个 Review；R007 已完成交接，因此本轮可归档。README 双语用作来源明确的用户摘要，
   默认值示例若继续扩大，再考虑从 ConfigLoader 生成可核验片段。

## 6. 验证与局限

所有 PF 验证均按 AGENTS.md 在仓库根、沙箱外运行。未执行网络 dogfood、全量三版本套件、真实 host
qualification 或 build 资格；本次测试通过不表示 §2 的偏移已修复。

- `UV_CACHE_DIR=/tmp/pf-uv-cache uv run python scripts/generate_report_schema.py --check`：通过。
- 下列现有测试：929 passed in 11.25s；Python 3.10.16、Pydantic 2.13.4；未冒充最低依赖矩阵。

```sh
UV_CACHE_DIR=/tmp/pf-uv-cache uv run pytest --no-testmon --no-cov -q \
  tests/test_config.py tests/test_marker_projection.py tests/test_optional_test_group.py \
  tests/test_search.py tests/test_prepare_execution.py tests/test_execution_contract.py \
  tests/test_runtime_witness.py tests/test_ty_adapter.py tests/test_execution_run.py \
  tests/test_runlog.py tests/test_report_schema.py tests/test_authorization.py \
  tests/test_configured_verifier.py tests/test_cli.py tests/test_terminal.py
```

独立复现使用仓库已有 fixture 构造事实，并从 CoordinateSearch/RunLogStore 的公开 seam 观察结果；
没有替换产品 evaluator 或读取其私有状态。可从仓库根执行以下代码：

```python
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_search import snapshot_versions, probe_attempt, probe_pass
from pf.coordinate_search import CoordinateSearch
from pf.schemas.project import VersionPin
from pf.schemas.report import ProbeRejection
from pf.schemas.evaluation import (
    AttemptFailureScope, FailureRecord, NormalExit, VerificationJournal,
    VerificationJournalEntry, VerificationPackagePolicy,
)
from pf.runlog import RunLogStore

class OnlyBaselinePasses:
    def __init__(self):
        self.visited = []
    def evaluate(self, vector):
        version = vector[0].version
        self.visited.append(version)
        if version == "100":
            return probe_pass(vector, version)
        return ProbeRejection(attempt=probe_attempt(vector),
                              failure_id="failure-" + version,
                              cause="RESOLUTION_CONFLICT")

evaluator = OnlyBaselinePasses()
result = CoordinateSearch(small_threshold=2).minimize(
    start=(VersionPin(name="a", version="100"),),
    candidates=(snapshot_versions("a", tuple(str(i) for i in range(1, 100))),),
    evaluator=evaluator,
)
print(result.status, len(set(evaluator.visited) - {"100"}))
attempt = probe_attempt((VersionPin(name="a", version="1"),))
failure = FailureRecord.from_verifier(
    scope=AttemptFailureScope(attempt=attempt), disposition="REJECTED",
    cause="VERIFIER_EXITED_NONZERO", stage="test", terminal=NormalExit(exit_code=1),
)
entry = VerificationJournalEntry(package=attempt.identity.cell.package,
    cell=attempt.identity.cell, role="probe", attempt=attempt, failure=failure)
with tempfile.TemporaryDirectory(prefix="pf-r010-") as directory:
    logs = RunLogStore(root=Path(directory), run_id="audit")
    try:
        journal = VerificationJournal(run_id="audit", command="search",
            source_snapshot_digest=attempt.identity.source_snapshot_digest,
            package_policies=(VerificationPackagePolicy(package=entry.package,
                evaluation_policy_identity=attempt.identity.evaluation_policy_identity),),
            entries=(entry,))
        path = logs.write_journal(journal)
        document = json.loads(path.read_text())
        document["entries"][0]["role"] = "baseline"
        path.write_text(json.dumps(document))
        loaded = logs.read_latest_journal(entry.package)
        print(loaded.entries[0].attempt.identity.requested_resolution,
              loaded.entries[0].role)
        document["entries"].append(journal.model_dump(mode="json")["entries"][0])
        path.write_text(json.dumps(document))
        loaded = logs.read_latest_journal(entry.package)
        print(len(loaded.entries), len({e.failure.failure_id for e in loaded.entries}))
    finally:
        logs.close()
```

实际输出：`NO_PASS_IN_SEARCH_SPACE 8`、`exact-vector baseline`、`2 1`。
文档检查完成：`python /tmp/pf-docs-check.py`（本轮一次性检查脚本）遍历 105 份 Markdown，
检查 751 条本地链接/锚点（现行区域 304、归档区域 447），0 失效；对 `30c5d7d` 的
`docs/archived/` 72 个既有文件逐字节比较，0 变更。`git diff --check` 通过。
独立复现先以 `/tmp/pf-r010-probes.py` 执行，再从本文 Python 代码块提取为
`/tmp/pf-r010-documented-probes.py` 执行，输出与上列一致；临时脚本不作为新的产品工具交付。

最后复核保留了去重前的必要约束：Loader 单次 requirement 解析、真实 SearchCoordinator 测试、
static classification 的公开入口、完整/终态环境关闭分别留在 D002/D003；smoke 的 TyCheck 复用
与无 witness 时序移交 D008。D001 混合 reason 的退出码说明也明确先按 D008 聚合，
避免将 Baseline Rejection/Indeterminate 与远端缺 Cell 的组合误读为统一退出 2。

## 7. 文档重构交付（2026-09-06）

用户要求继续完成当前可进行的文档整理与重构后，落实以下迁移。此节更新 §5 的建议状态；
§3、§6 保留首轮审计范围和当时验证记录。现行长期 owner 由 11 份变为 12 份，D006 附录仍属于 D006。

| 原位置 | 交付位置与唯一归属 |
| --- | --- |
| D001 §4 候选规则及 §7 搜索默认表 | [D037](../designs/D037-pf-candidate-search-policy.md) §1–5 独占 registry 准入、DSL、anchor、默认/逐依赖策略、采样与 baseline artifact 选择域；D001 §4 保留导航及验证边界 |
| 候选章节内的通用规则 | 统一 artifact policy、root/member dep AoT 替换、数值退出码分别保留在 D001 §4、§7、§8；uv prerelease resolution 与 context identity 只在 D012 §6 定义，D037 链接消费 |
| D006 help、通用视觉细则和卡片示例 | [D006 视觉附录](../designs/appendices/D006-visual-specification.md) A.1–A.7；主文保留通道、状态、信息层级、命令事实来源和各章节入口 |
| 双语 README 完整默认值与 DSL 说明 | 收缩为同一份可执行配置示例；完整通用配置链接 D001，候选与搜索策略链接 D037 |
| 索引及消费方引用 | [文档规则与所有权表](../README.md) 补充纯文档拆分和规范性附录规则；D002/D003/D012/D014 及 README 同步指向 D037，wire/reader 仍由 D014 拥有 |

已完成 §5 的候选拆分和视觉附录建议；README 通过缩小示例范围消除平行默认表，当前无需新增生成器。
D005/D008/D014 继续分担各自边界，R006/R008 保留开放项与历史证据；没有进一步应立即执行的归并或归档。
§2 的两项实现偏移与 §4 的工程事项仍开放，本次文档迁移不关闭这些问题，也不新增产品行为。

2026-09-10：§2 已关闭；PEP 508 不单独实施；假想 Protocol 无新增整改。§4 剩余
ty × Python / 真实 host 资格与 targeted-runtime-contract floor。

迁移核对：D006 的 13 个搬迁片段在规范化相对链接与空白后全部保留；混合段落拆开的状态与样式规则
逐项核对仍在主文/附录。D001 原候选章节的 16 个段落中，12 个保持原文；其余 4 个分别拆分统一
artifact policy、引用通用配置合并、引用退出码、消除 D012 resolver 重复定义，规则均保留在对应 owner。
原 D001 §4 与 D006 章节锚点保留，历史入链不需要改写归档。

本次验证：

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /tmp/pf-doc-examples-check.py`：从两份 README
  提取 TOML，通过公开 `ConfigLoader.load`，核对 patch 采样、4 Cell 并发、pytest 命令与双语配置等价；
  在仓库根、沙箱外执行，通过。脚本仅用于本次检查，不作为产品工具交付。
- `python /tmp/pf-docs-check.py`：遍历 107 份 Markdown，检查 785 条本地链接/锚点
  （现行 338、归档 447），无失效链接。
- 对重构开始时文件 SHA-256 快照比较：73 个已有归档（含首轮归档的 R007）全部未变；
  源码、测试、schema/example 生成物及其他非 Markdown 文件全部未变，保留已有 pyproject/lock 改动。
- `git diff --check`：通过。本次只迁移文档并校验配置示例，未重跑 §6 的 929 项测试，
  不将静态检查记为行为修复或新的运行资格。

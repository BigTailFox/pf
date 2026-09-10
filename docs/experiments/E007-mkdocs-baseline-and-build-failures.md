# E007 — MkDocs 基线失败诊断与实验准备

- **状态：** 已完成
- **日期：** 2026-09-06（Asia/Shanghai；run-id 使用 UTC）
- **性质：** 非规范性 dogfood 实验事实；不定义失败分类或搜索算法的新契约
- **证据位置：** [data/E007/](data/E007/)（报告摘要、smoke 证据、诊断与配置记录）。
- **目标：** `experiments/mkdocs`，MkDocs `1.6.1`，上游 commit
  `2862536793b3c67d9d83c33e0dd6d50a791928f8`（2025-10-20）加本地实验配置
- **PF：** 原始报告 generator 为 `0.2.0`；报告不记录 PF Git commit，未据此反推运行时提交
- **整理时 PF HEAD：** `5af2a04d73299a9029ad78c0be780737db2e5398`
- **契约入口：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)

用户先执行 `pf search`，21 个 Cell 均未形成成功终态：13 个最高版本基线被 configured verifier
拒绝，另 8 个 Cell 通过基线后在 Jinja2 2.0 构建时停止。诊断确认了三个不同原因：单元测试所需
Babel 未进入 no-extra 环境、pathspec 新版本改变前导空格处理、Jinja2 2.0 必经构建脚本使用 Python 2
语法。完成实验配置后，`pf smoke` 的 5 个 i18n Cell 全部通过，每个运行 725 个单元测试。

本次没有重跑配置调整后的 search，没有执行 check 或 apply，没有修改 PF、MkDocs 生产实现或测试。
实验临时增加的 pathspec 上界不代表 MkDocs 对所有用户都必须限制该依赖。

## 1. 原始输入与实际时序

上游 `project.requires-python = ">=3.8"`，声明 `pathspec >=0.11.1`、`Jinja2 >=2.11.1`；
optional dependencies 包含 `i18n = ["babel >=2.9.0"]` 和维护最低依赖组合的 `min-versions`。
用户此前增加的 PF 配置只有：

```toml
[tool.pf]
test-command = ["python", "-m", "unittest", "discover", "-s", "mkdocs", "-p", "*tests.py"]
```

此次 search 自动规划 CPython 3.8–3.14 × `x86_64-unknown-linux-gnu` ×
`no-extra` / `i18n` / `min-versions`，共 21 Cells。终端显示 14 active packages、14 pinned；
这项显示不是“所有 Cell 的每个依赖都固定且不参与搜索”的证据，实际向量与候选以报告为准。

搜索没有显式限定 space，按有下界依赖的条件默认 `majors[declaration-1:]` 选择范围，以默认
`minor` 粒度采样。因此 Jinja2 的搜索可以进入声明下界之前的 2.0；声明 `>=2.11.1` 不等于
搜索也以 2.11.1 为最低候选。

实际顺序为：用户 search → 读取报告和失败日志 → 临时环境单变量复现 → 实验配置调整 → smoke →
补查 YAML 输入与上游 CI。推荐的准备流程是先 smoke，但本次不能倒写成“原始配置先通过了 smoke”。

| 运行 | run-id | shell 退出码 | 结果 |
| --- | --- | --- | --- |
| 用户原始 search | `20260905T215946.368564Z-1436881-b755d6da` | 未独立捕获 | 13 BASELINE_REJECTION、8 CELL_INDETERMINATE；报告 incomplete |
| 调整配置后的 smoke | `20260906T042427.389320Z-1480180-8df3bb32` | 0 | 5 Cells PASS |

配置准备时另有一次 smoke 在输入校验阶段退出 3：Python 列表未按 PF 要求的字符串顺序排列。
修正列表后才执行表中的有效 smoke；该输入错误不计入测试失败或 Cell 兼容性结果。

## 2. 原始 search：三类症状与实际 disposition

| Cell 集合 | 数量 | 基线观察 | 最终结果 |
| --- | ---: | --- | --- |
| Python 3.8 / no-extra | 1 | 缺少 Babel；2 failures、2 errors | BASELINE_REJECTION |
| Python 3.9–3.14 / no-extra | 6 | 缺少 Babel，加草稿排除测试的两个 subtest 失败；4 failures、2 errors | BASELINE_REJECTION |
| Python 3.9–3.14 / i18n | 6 | 草稿排除测试的两个 subtest 失败；2 failures | BASELINE_REJECTION |
| Python 3.8 / i18n | 1 | 基线通过，pathspec 为 0.12.1 | 搜索到 Jinja2 2.0 时 CELL_INDETERMINATE |
| Python 3.8–3.14 / min-versions | 7 | 基线通过，pathspec 为 0.11.1 | 搜索到 Jinja2 2.0 时 CELL_INDETERMINATE |

原始报告 `result.reasons` 为 `BASELINE_REJECTION`、`INDETERMINATE`、
`UNREPRESENTABLE_PROJECTION`。它保存 154 Attempts、34 Proposals、34 static evaluations、
33 evaluations、22 resolution graphs 和 138 FailureRecords；中间探针拒绝不能计成额外失败 Cell。

### 2.1 Babel：测试前提未转入 PF

上游 `[tool.hatch.envs.test]` 声明 `features = ["i18n"]`，完整单元测试预期 Babel 存在。
PF 的 no-extra Cell 则没有这一 requirement，`mkdocs.localization` 进入无 Babel 的 fallback。
失败包括 `Translations` 属性缺失，以及实际安装 `NoBabelExtension` 而测试期望 `jinja2.ext.i18n`。

这是实验只复制测试命令、没有复制测试前提造成的差异；不能通过在用户当前 venv 中手动安装 Babel
补齐 PF 的隔离环境。Babel 应经显式测试契约进入实际 Cell surface。

### 2.2 pathspec：基线已被拒绝，没有误判成 Indeterminate

代表失败 `failure-9afe7a00b0b085a9`（Python 3.11 / i18n）为：

```text
cause:       VERIFIER_EXITED_NONZERO
disposition: REJECTED
stage:       test
terminal:    normal-exit, exit_code=1
```

对应 `test_draft_docs_with_comments_from_user_guide` 的两个 subtest。日志显示草稿文件仍被生成，
预期的 preview-only 日志未出现；不是安装失败或导入即崩溃。

当时 D003 的基线必须先获得完整 PASS。Baseline Rejection 会终止 Cell，已有通过锚点后的 Probe
Rejection 才能继续参与下界定位。最高版本失败后向下寻找首个 PASS 属于另一项算法能力，本次没有实现。

### 2.3 Jinja2：Python 2 构建脚本导致停止

代表失败 `failure-2a0d57da8f366248`（Python 3.11 / min-versions）为：

```text
cause:       BUILD_FAILURE
disposition: INDETERMINATE
stage:       resolve-project
summary:     resolution-build-failure
```

uv 获取 Jinja2 2.0 sdist 的构建需求时，`setuptools.build_meta:__legacy__` 执行 `setup.py`，
在 `except CCompilerError, x:` 处出现 `SyntaxError: multiple exception types must be parenthesized`。
7 个终止构建失败指向这一 `except` 写法；Python 3.8–3.9 的提示为 `invalid syntax`。
Python 3.14 / min-versions 则指向同一脚本的 `print '*' * width`，提示缺少括号。
8 个均为 Python 2 语法导致的构建脚本解析失败，但不是完全相同的错误位置或文案。
独立 Python 3.11 安装同一旧版本复现了上述 `except` 错误。

当时 D005 不允许普通 build failure 形成 Rejection，因此该处置符合当时契约。诊断认为这里存在
可讨论的构建负向证据问题，但没有将历史 INDETERMINATE 改写为 REJECTED。

## 3. 单变量复现与正常用户路径

临时目录为 `/tmp/pf-mkdocs-diagnosis`。复制 MkDocs 源码，创建 Python 3.11.15 venv，按原始
Python 3.11 / i18n 报告的 resolution graph 安装精确版本，并以 editable 方式安装源码副本。
冻结依赖清单见 [baseline-py311.txt](data/E007/baseline-py311.txt)。

从 PF 仓库根启动的主要命令如下；诊断环境安装和测试均在沙箱外执行：

```bash
.venv/bin/uv venv --python 3.11 /tmp/pf-mkdocs-diagnosis/venv
.venv/bin/uv pip install --python /tmp/pf-mkdocs-diagnosis/venv/bin/python --no-deps \
  -r /tmp/pf-mkdocs-diagnosis/baseline.txt -e /tmp/pf-mkdocs-diagnosis/source
/tmp/pf-mkdocs-diagnosis/venv/bin/python -m unittest \
  mkdocs.tests.build_tests.BuildTests.test_draft_docs_with_comments_from_user_guide \
  mkdocs.tests.localization_tests
```

| 实验变化 | 已观察结果 |
| --- | --- |
| 原报告组合：Babel 2.18.0、pathspec 1.1.1 | 9 tests，2 failures，均为草稿测试 subtest |
| 只将 pathspec 改为 0.12.1 | 同样 9 tests，OK |
| 再只移除 Babel，运行 localization_tests | 8 tests，2 failures、2 errors；匹配 no-extra 症状 |
| 恢复 Babel 2.18.0，保留 pathspec 0.12.1，执行完整 discovery | 725 tests，OK，skipped=4 |
| 向独立临时 target 安装 `jinja2==2.0` | build 正常非零退出，复现上述 Python 2 语法错误 |

Jinja2 独立复现命令是：

```bash
.venv/bin/uv pip install --python /tmp/pf-mkdocs-diagnosis/venv/bin/python \
  --target /tmp/pf-mkdocs-diagnosis/jinja2-build --no-deps jinja2==2.0
```

普通 `uv pip compile --no-deps` 曾成功返回 `jinja2==2.0`，但没有证明实际构建成功；以随后实际
`pip install --target` 的失败作为构建复现证据。完整单测使用副本为 cwd，保持原 discovery 参数。
这些小实验的命令与结果来自本次会话记录，未声称保留其全部原始 stdout/stderr；原 PF 搜索的代表
完整 process log 另存于 [diagnostics.txt](data/E007/diagnostics.txt)。

随后通过 MkDocs `PathSpec().run_validation()` 核对输入。原测试直接传入 Python 三引号字符串，
其共同缩进仍在字符串中；正常 `draft_docs: |` YAML block 在解析时会移除共同缩进。

| 实际传给 pathspec 的模式 | 0.12.1 是否匹配 `other_unpublished.md` | 1.1.1 是否匹配 |
| --- | --- | --- |
| `*_unpublished.md` | 是 | 是 |
| 四个前导空格加 `*_unpublished.md` | 是 | 否 |

实测正常 YAML block 经 MkDocs 配置接口可以匹配；带缩进 Python 字符串不能匹配。因此最初笼统的
“MkDocs 与 pathspec 不兼容”应收窄为“带前导空格模式的行为变化触发了现有测试失败”。默认安装不运行
这些测试，常规 YAML 输入也不因这一例而失败；其他保留额外前导空格的输入仍可能暴露行为差异。
测试是否应 dedent，或 MkDocs 是否应承担配置归一化，需要依据项目承诺决定。

## 4. 实验准备调整与 smoke 证据

本次获准完成的配置调整为：

```toml
[dependency-groups]
pf-unit = ["mkdocs[i18n]"]

[tool.pf]
pythons = ["3.10", "3.11", "3.12", "3.8", "3.9"]
test-group = "pf-unit"
extra-policy = "none"
test-command = ["python", "-m", "unittest", "discover", "-s", "mkdocs", "-p", "*tests.py"]
```

同时将 base dependency 改为 `pathspec >=0.11.1,<=0.12.1`，并加实验原因注释。
精确改动见 [configuration.patch](data/E007/configuration.patch)，它以本次准备前的用户工作树为基准，
不包含用户此前对上游 pyproject 的格式或其他修改；使用零上下文 diff，复用时需 `git apply --unidiff-zero`。

自引用 `mkdocs[i18n]` 形成 required surface，Babel 成为项目依赖并参与搜索；`extra-policy = "none"`
不取消 required i18n，只停止自动探索额外的 `min-versions`。Python 范围对齐该 checkout 的上游
CPython CI 范围；这不声称 3.13–3.14 不受项目支持。数组的显示顺序遵循当时配置校验的字符串排序。

pathspec 上界只用于这一实验继续取得可用基线。它不是已验证 floor、不是所有用户都必需的上界，
也不是已接受的上游修复。单改 search-space 不会改变最高版本基线的解析，所以本次调整的是依赖声明。
Jinja2 搜索范围未调整，后续 search 仍可能遇到旧构建脚本。

有效 smoke 的实际入口如下（shell cwd 为 PF 仓库根；进程内切到目标目录）：

```bash
.venv/bin/python - <<'PY'
import os, sys
from pf.cli import main
os.chdir('/home/llh/pf/experiments/mkdocs')
sys.argv = ['pf', 'smoke']
main()
PY
```

| Python | interpreter | 单测结果 | Process Log |
| --- | --- | --- | --- |
| 3.8 | 3.8.20 | 725 tests，OK，skipped=6 | process-0030.log |
| 3.9 | 3.9.25 | 725 tests，OK，skipped=4 | process-0023.log |
| 3.10 | 3.10.16 | 725 tests，OK，skipped=4 | process-0018.log |
| 3.11 | 3.11.15 | 725 tests，OK，skipped=4 | process-0033.log |
| 3.12 | 3.12.3 | 725 tests，OK，skipped=4 | process-0037.log |

5 个 verifier 进程均正常退出 0，终端明确给出 `Smoke passed · 5 cells`，CLI 退出 0；Journal
无失败条目。成功结论同时依赖命令终态与实际测试日志，不单凭空 Journal 推断。每个 Cell 均安装
pathspec 0.12.1，完整 installed versions、解释器和日志摘要见
[smoke-evidence.json](data/E007/smoke-evidence.json)。

## 5. 上游已有问题记录

2026-09-06 查询了公开页面和 GitHub API，取得以下历史证据：

- pathspec [1.0.0 变更记录](https://python-path-specification.readthedocs.io/en/latest/changes.html)
  日期为 2026-01-05，明确将“不再移除前导空格”列为修复；1.1.1 发布于 2026-04-26。
  本次实测对照是 0.12.1 / 1.1.1，不把变更记录当作本地逐版本回归测试。
- MkDocs [PR #4152](https://github.com/mkdocs/mkdocs/pull/4152)，2026-06-25 创建：作者报告在
  upstream master 复现同样失败，建议对测试 fixture dedent，使其与文档 YAML block 一致。
- MkDocs [PR #4159](https://github.com/mkdocs/mkdocs/pull/4159)，2026-07-01 创建：提出在配置解析
  前对多行 pathspec 字符串 dedent，并报告现有 draft-docs 回归测试在 CI 失败。
- 查询时上述两个 PR 均 `state=open, merged=false`；不能将它们描述为上游已修复。
- [2026-07-11 定时 CI](https://github.com/mkdocs/mkdocs/actions/runs/29143356504) 的
  `head_sha` 与本实验 checkout 完全相同，整体 `conclusion=failure`；Python 3.11 和 3.12 的
  Ubuntu 测试 job 均失败，Python 3.8 的三个平台 job 均成功。本次只取得任务结果，没有取得
  该 CI 的完整日志，不能逐项断言各 job 都因 pathspec 失败。

PR 的修复方向和失败说明是作者陈述；本地对缩进输入的复现提供独立支持。以上不证明维护者已选择某个
修复方案，也不把所查询的 7 月运行称为 9 月最新 CI。上游存在测试和修复提案，因此不能归因成
“上游 CI 没有覆盖这个测试”。

## 6. 准备责任与后续契约方向

| 事项 | 本轮确定的处理范围 |
| --- | --- |
| 测试命令、必需 extra、Python 和 surface 范围 | 用户明确声明；PF 安装并展示实际配置，不自动猜测 Hatch 意图 |
| 构建翻译文件 | 项目已有 Hatch build hook 和构建依赖声明；构建环境的 Babel 不代替测试环境的 Babel |
| coverage、Hatch、lint 工具 | 当前直接运行 unittest 不需要额外加入；按实际验证契约选择 |
| 集成测试 | 本实验未覆盖；若加入，需另外声明文档插件并运行上游 integration 入口 |
| 文档依赖清单 | 上游 requirements-docs.txt 仍固定 `mkdocs==1.5.3`，不能整份直接用于当前源码实验；应提取需要的插件声明 |
| 编译器、系统库、网络和权限 | 用户提供实验前提；PF 应忠实执行并保留诊断 |
| 准备信息展示 | 可讨论显示 effective test group、required extras、Python 来源和完整命令；本轮未实施 |

用户确认先 smoke 有助于将“当前源码与验证契约能否通过”从下界搜索中分离。这里发现的是基线测试
漂移或实现缺陷的信号，而不是由 PF 搜索降低依赖后才引入的 pathspec 失败。

### 6.1 已确认方向：可靠归因优先，执行契约兜底

后续讨论中，用户认可将 resolve、build、install 与 test 收敛到统一的工程执行契约：严格确认
PASS，使用可信执行失败拒绝当前 Attempt；只有无法可靠完成或观察执行时，才是 Indeterminate。
这里记录的是用户已确认的后续设计方向，不是现行 D005 的替代契约；讨论时尚未形成规范性 Design、
Plan 或实现。后续目标契约由 [D036](../archived/designs/D036-pf-execution-failure-contract.md) 接收并已实施，
验收见 [P041](../archived/plans/P041-pf-execution-failure-contract.md)。第 2 节历史运行的 disposition 保持不变。

| 可取得的执行事实 | 确认的方向 |
| --- | --- |
| 阶段成功，必要产物与一致性检查通过 | 继续后续阶段；完整验证通过后才能形成 PASS |
| 有可靠的阶段归因证据 | 按证据 dispatch；例如明确的下载失败为 Indeterminate，明确的依赖冲突为 Reject |
| 缺少足够归因证据，但有效请求下验证操作正常非零退出 | Reject 当前 Attempt，无需先证明具体依赖是根因或失败必然可重复 |
| 超时、信号终止、无法启动或终态不可得 | Indeterminate，不能用 Reject 兜底 |
| PF 自身异常、产物损坏或安装图与计划不符 | 基础设施失败或 Indeterminate，不作为候选拒绝证据 |

“正常非零退出”指进程意义上的正常终态，不表示操作成功。它不能被统一标成依赖冲突或理论 UNSAT；
cause 应保留实际能确认的构建失败、解析操作失败或未细分原因。Rejection 表达“这次 Attempt
没有通过所配置的执行契约”，不表达“这个依赖组合在所有环境中都不可能通过”。

准确归因是通用规则之上的改进，不是允许搜索继续的前提。特化 dispatch 围绕稳定的阶段接口、工具
协议和结构化事实建立，只细化能够可靠区分的情况；其余回到统一规则。不按包名、错误文本或单个
案例不断增加必要识别分支，也不维护实用与严格两套默认语义。报告保留实际采用的证据和原因，归因
本身不作绝对正确的承诺。

后端可能把网络、权限或内部异常包装成普通非零退出；拿不到可靠外因证据时，兜底规则接受误拒绝的
可能。反复失败不能代替归因：缺少编译器或权限也可能稳定失败。此取舍应在各验证阶段保持一致，
不能对测试采用执行结果、对构建却要求逐案证明根因。本次 Jinja2 probe 在该方向下可以根据可信的
构建失败终态被拒绝，而不必专门识别 Python 2 的 `except` 或 `print`；这是预期方向，尚未实测实现。

### 6.2 结果承诺与搜索边界

正向证据保持严格：返回的最终精确向量必须实际解析、安装，完成必要一致性检查并通过完整 verifier。
负向证据服务于搜索，只否定当前 Attempt，不自动升级成单个版本或整个区间不兼容的证明。

PF 尽力降低已验证向量，不承诺全局最优或搜索完备性。误拒绝可能使下界偏高、遗漏可行组合，甚至
导致本次没有结果；最终 PASS 也不证明所有高于下界的任意组合都兼容。应承诺的是记录的源码、环境、
解析策略和验证契约下，返回的精确依赖向量已经通过验证。

最高版本基线失败后是否继续寻找可用锚点，仍是独立的算法问题；统一 Rejection 语义不会自动改变
Baseline Rejection 的终止规则。后续 [D036](../archived/designs/D036-pf-execution-failure-contract.md)
接收阶段事实、dispatch、证据与报告身份等细节，实施验收由 P041 记录；本报告只承载实验事实及
上述方向共识。

## 7. 固定证据与结论边界

- [search-summary.json](data/E007/search-summary.json)：原始 report 身份、SHA-256、计数及全部
  21 Cells 的 baseline vector、interpreter、终止 Attempt/FailureRecord 和日志路径。
- [diagnostics.txt](data/E007/diagnostics.txt)：原 search 中 no-extra、i18n 与两个 Jinja2 build
  代表失败的完整、已脱敏 process log；包含 Python 3.11 / 3.14 不同语法错误，路径保留历史值。
- [baseline-py311.txt](data/E007/baseline-py311.txt)：临时复现所用原 Python 3.11 / i18n 精确依赖。
- [configuration.patch](data/E007/configuration.patch)：本轮实验配置改动。
- [smoke-evidence.json](data/E007/smoke-evidence.json)：有效 smoke 的实际退出码、终端结论、Journal、
  5 个解释器、已安装版本、单测退出码/摘要及源日志 SHA-256。

原 search generation 为 `8b8ffb8b88e4032ae2d48373b0202946b84d12a63c3c714f24d72b48593fc4be`，
source snapshot 为 `d2801169a14a363512675c227f83ed7246489a74a7d4e05f1679a67e271f7580`。
调整配置后的 smoke snapshot 为 `4c3be6e0310545f6fb975effced8b7312bad8811e524ab626092915e175644be`；
两次运行保存的 evaluation policy 均为
`e2580e7a6a2324e1179f9758dff2c8ccc23d6c73d30d44aa1b3c26d917c1a112`。
相同 policy 不消除 snapshot、Cell 与配置差异，不能把后来的 smoke PASS 回填进原 search。

原始报告与 `.pf/logs/<run-id>/` 仍是目标目录下的本机产物，可能被后续运行覆盖或清理；本文保存的
摘要不替代完整 PF 报告、reader 验证或 apply 授权。原报告 SHA-256 为
`aaba4c41dece44cb6c3761e05ee1d2029bbb76a3767e2f827cc1d36a1481dfbf`。

本次成立的结论限于上述源码、依赖组合、Linux target 与单元测试契约：原 search 没有 verified floor，
调整后的 5 Cells smoke 全部通过。完整集成测试、调整后的 search、其他平台、新基线寻找算法及新的
build-failure disposition 均不在本次完成证据之内。

整理时执行了以下只读 reader 复核（PF 仓库根目录，沙箱外）：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from pf.report import ReportStore
r = ReportStore().read(Path('experiments/mkdocs/package-floor.json'))
print(r.report_generation_id, r.result.status, len(r.cell_results))
PY
```

结果为上述 generation、`incomplete`、`21`。另核对两个 JSON 摘要的 Cell 数、终止 cause、
5 个 smoke verifier 退出码/725 tests 摘要及 pathspec 版本；E007 与索引的 79 个本地链接目标、
Markdown 解析和 `git diff --check` 均通过。整理过程中没有重新执行产品实验。

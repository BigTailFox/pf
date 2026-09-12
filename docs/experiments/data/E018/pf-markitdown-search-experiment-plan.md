# E018 — MarkItDown all 搜索实验计划

- **状态：** 已完成
- **日期：** 2026-09-12
- **性质：** 非规范性实验协议与执行计划；不定义新契约，不构成对 MarkItDown 上游声明的修改建议
- **证据位置：** [.](.)
- **报告：** [../../E018-markitdown-complete-search.md](../../E018-markitdown-complete-search.md)
- **契约入口：** [D001](../../../designs/D001-pf.md)、[D003](../../../designs/D003-pf-search-algorithm.md)、
  [D005](../../../designs/D005-pf-failure-and-diagnose.md)、[D008](../../../designs/D008-pf-verification-run.md)、
  [D012](../../../designs/D012-pf-harness-relaxation.md)、[D013](../../../designs/D013-pf-pytest-observer.md)、
  [D014](../../../designs/D014-pf-report-schema.md)、[D037](../../../designs/D037-pf-candidate-search-policy.md)
- **对照：** [E012](../../E012-flask-complete-search.md)、[E013](../../E013-requests-complete-search.md)、
  [E014](../../E014-mkdocs-complete-search.md)、[E017](../../E017-xarray-core-complete-search.md)；
  [E016](../../E016-pf-linear-search-control.md) 不在本轮执行
- **候选调查：** [paper/candidate-markitdown.md](../../../../paper/candidate-markitdown.md)

本计划固定 MarkItDown **`packages/markitdown`** 上的 PF 完整搜索。只选该可安装成员，不搜索
`markitdown-mcp`、`markitdown-ocr` 或仓库根。extra 只跑命名 extra **`all`** 一组 surface。
不为凑 PASS 裁剪上游测试、不加人工上界、不改 PF 契约。
`check` 在声明下界失败是观测，不是准入否决。最高 baseline 失败时，仅允许对实验克隆做
**最小化本地修复**后再继续；修复必须写入证据，不得回写上游建议。

不使用 `extra-policy = "all"`。该政策会展开每个非空 selectable extra 再加并集。
2026-09-12 用户撤回 none surface：上游 suite 按 Hatch `features = ["all"]` 编写，
`no-extra` 在收集 `test_pptx_svg.py` 时因缺 `lxml` 失败。不为此给 none 打测试补丁，
也不搜索 required base。

## 1. 冻结身份

| 项 | 冻结值 |
| --- | --- |
| 目标树 | `experiments/markitdown/packages/markitdown` |
| 上游 tag | `v0.1.7`（annotated tag；peeled commit 如下） |
| 上游 commit | `fd239d5d2be43d9b68329730206b9312c7d5a388` |
| 声明 | `requires-python = ">=3.10"`；base 六项 + extra `all` 十五项 |
| 固定坐标 | `magika~=0.6.1`、`mammoth~=1.11.0`、`youtube-transcript-api~=1.0.0`（`~=` 按 D001 不搜索） |
| 拟搜索坐标 | 18（base 5 + extra `all` 中可搜索 13） |
| 上游 CI lane | Ubuntu `cd packages/markitdown; hatch test`，Hatch `features = ["all"]`，Python 3.10–3.12 |
| PF | generator `0.4.0`；执行时记录实际 HEAD |
| 工具 | uv `0.12.5`、ty `0.0.74`（D001 发行固定） |
| 宿主 | Linux、`x86_64-unknown-linux-gnu` |
| 解释器（阶段 1） | CPython 3.10 |
| 解释器（阶段 2+） | CPython 3.10 / 3.11 / 3.12（与 v0.1.7 CI 对齐；不扩 3.13/PyPy） |
| 允许的本地改动 | `pf-test` dependency group、`[tool.pf]`；smoke 直接失败时的最小化测试/harness 补丁 |

不使用浮动 `main`。当前克隆若仍在 `main`，先 checkout 上述 tag。
不把 Hatch 当作 oracle 内的二次 resolver。论文口径是 **packages/markitdown × extra all**，
不是 monorepo 全包、required base，或全部 named extras。

## 2. 配置

目标没有 `dev`/`test` group。省略 `test-group` 会得到空 harness，pytest 不会进入环境。
必须新建独立 group。仓库根没有 `[project]`，cwd 必须是成员目录，才能把
`packages/markitdown` 当作可安装 root，避免误选 MCP/OCR。

自引用 `markitdown[all]` 把 `all` 并入 required base `R`。`extra-policy = "none"` 只保留
`R`，得到单一 Cell `all`。这与 [E014](../../E014-mkdocs-complete-search.md) 的
`pf-unit = ["mkdocs[i18n]"]` 相同，不是 `extra-surfaces = [["all"]]`：后者仍会留下 empty surface。

阶段 1：

```toml
[dependency-groups]
pf-test = [
  "markitdown[all]",
  "pytest",
  "openai",
]

[tool.pf]
pythons = ["3.10"]
test-group = "pf-test"
extra-policy = "none"
test-command = ["pytest"]
resolve-artifact = "any"
test-timeout = "2h"
test-jobs = 1
search-space = "all"
search-resolution = "minor"
```

阶段 2 把 `pythons` 改为 `["3.10", "3.11", "3.12"]`。
阶段 4 把搜索改为 `search-space = "minors[declaration]"`、`search-resolution = "patch"`。

| 项 | 取值 | 原因 |
| --- | --- | --- |
| harness | 自引用 `markitdown[all]` + `pytest` + `openai` | 自引用进入 required surface，不是搜索坐标；`openai` 对应 Hatch `hatch-test` |
| 不进入 harness | mypy、coverage、fpdf2、ffmpeg、exiftool、真实 Azure/OpenAI 凭据 | 不启用未在 CI 固定的可选工具；缺 fpdf2/exiftool 的 skip 记入 oracle |
| extra | 自引用 `all` + `extra-policy = "none"` | 单 Cell；不展开 pptx/docx/…，不保留 no-extra |
| `test-command` | `pytest` | 与 Hatch 最终执行的完整 suite 等价；不传 `-k` / `--ignore` / `-n` |
| `resolve-artifact` | `any` | 本地源码快照必须可构建；wheel-only 会禁用 target sdist |
| `test-jobs` | `1` | `test_doc_rlink` 使用固定 `/tmp/test_rlink.txt` |
| `test-timeout` | `2h` | 进入 ExecutionPolicy；all extra 首次安装可能远超 30 分钟默认 |
| 宿主环境 | `GITHUB_ACTIONS=true`，不设 `OPENAI_API_KEY` | 复现上游 CI：跳过真实远程 URL / speech / LLM |
| search 时限 | `--max-duration 12h` | 超时写 incomplete，不改 oracle |

验证契约是成员目录完整 pytest collection（`tests/`）。sdist 只打包 `src/markitdown`，
因此必须从源码快照跑测试，不能用已发布 sdist 冒充完整资产。
`search-prereleases` 保持默认 `false`。`azure-ai-contentunderstanding>=1.2.0b1` 的预发布
下界只影响 resolver/baseline，不自动进入候选快照；缺稳定版时按实际 resolve/candidate
事实分类，不改契约。

矩阵预期：CPython **3.10** × `x86_64-unknown-linux-gnu` × `all`，**1 Cell**、
18 个受管直接依赖、3 个 pinned。

### 2.1 配置偏差（2026-09-12）：撤回 none

第一轮曾用 `extra-surfaces = [["all"]]` 同时准入 none 与 all。
`pf smoke` run-id `20260912T055455.638407Z-234544-47c10a3e`：
`all` PASS（307 passed / 33 skipped）；`no-extra` 收集 `test_pptx_svg.py` 因
`ModuleNotFoundError: lxml` 被拒绝。证据：[admission/smoke-1/](admission/smoke-1/)。
用户随后要求不再搜索 none。已改为自引用 `markitdown[all]`，不给 none 打测试补丁。
后续 smoke/check/search 属于新配置、新快照，不回写第一轮 none 失败计数。

### 2.2 配置偏差（2026-09-12）：contentunderstanding 预发布

第一次 `all` × `minor` search（run-id `20260912T060058.106115Z-253753-228ef278`）
在默认 `search-prereleases = false` 下把 search baseline 选成稳定版
`azure-ai-contentunderstanding==1.1.0`。`test_cu_converter.py` 在
`UserAgentPolicy() takes no arguments` 处失败。Smoke 的 highest 安装的是
`1.2.0b3`（声明 `>=1.2.0b1`）。证据：[all-minor/](all-minor/)。
该轮 **incomplete** / `SEARCH_FAILED`，0 次搜索观察，不回写。

已只对该坐标打开 `search-prereleases = true`，其余坐标保持默认 false。
这让候选域包含声明下界所要求的预发布，不扩大其他坐标。之后的 smoke/search
是新快照，不与 §2.1 的 all-only 准入混写。

```toml
[[tool.pf.dep]]
name = "azure-ai-contentunderstanding"
search-prereleases = true
```

## 3. 执行流程

由 PF 仓库根、沙箱外执行。`cwd=experiments/markitdown/packages/markitdown`，
`PATH` 前缀 `.venv/bin`，`UV_CACHE_DIR=/tmp/pf-uv-cache`，`GITHUB_ACTIONS=true`。
记录脚本为 `docs/experiments/data/E018/run_pf.py`。
阶段 1 使用 `test-jobs=1`。不对 pytest 加 `-n`。命令显式 `--package` 仅在 cwd 不是成员根时使用；
本轮 cwd 已是成员根，省略 selector。

| 阶段 | 命令 | 通过条件 | 失败分流 |
| --- | --- | --- | --- |
| 0 冻结 | checkout `v0.1.7`；只写实验配置 | 除实验配置外工作树干净 | 先停，不跑 PF |
| 1 准入 smoke | 仅 3.10 × `all`，连续两次 `pf smoke --test-jobs 1` | 两次 PASS，collection/skip 集合一致 | 配置/插件：改 harness；wheel/ABI：记录并分类；最高向量 verifier 失败：做**最小化**本地修复后重做本阶段，写入 `local-patch/`，不加上界、不删测试、不跳过失败 nodeid |
| 1b check | 同配置 `pf check` | 完成即可；REJECTED 保留为观测 | INDETERMINATE 或 oracle 不稳定：不进入 search |
| 1c 独立性 | 若有 Rejection，抽样失败 nodeid 单独重放 | 重放不互相污染 | `/tmp/test_rlink.txt` 污染或单 nodeid 不可复现则停 search |
| 2 扩 Cell | `pythons` 扩到 3.10–3.12，再 smoke/check | 各 Cell 最高版本 PASS | 某一 Python 无适用 wheel 则记录并从矩阵移除；本轮可在 3.10 稳定后决定是否扩，不假装全矩阵成功 |
| 3 粗搜 | `all` × `minor`：`pf search --max-duration 12h` → `pf apply` | complete 或原样留下 incomplete | `NO_PASS_IN_SEARCH_SPACE` 按 E014 解释为可达性，不当成联合空间无解 |
| 4 细化 | `minors[declaration]` × `patch` 再 search；成功才 apply | 记录 complete/incomplete | 同阶段 3 |
| 5 闭环 | apply 后再 `pf check` | 记录通过或失败 | 不回写上游声明；apply diff 不等于已验证 |

门控：阶段 1 的最高 baseline 未稳定 PASS，不得进入阶段 3。
用户已授权 smoke 直接失败时做最小化修复；修复后的源码快照是新身份，后续运行不回写修复前计数。
不为候选扩 PF 契约。本轮不执行 E016 线性对照。

### 3.1 执行结果（2026-09-12）

阶段 0 完成。阶段 1 第一次 smoke 在旧的 none+all 配置下：all PASS，none REJECTED。
已按 §2.1 改配置。单 Cell `all` 两次 smoke PASS（307/33）。原声明 check 因
`markdownify==0.2.0` 构建 `execfile` 被拒绝。第一次 all×minor search 在默认
不含预发布时以 `contentunderstanding==1.1.0` 失败（incomplete）。按 §2.2 打开该
坐标预发布后重做 smoke 与 search：all×minor **complete** 并 apply；patch refine
**incomplete**（`NO_PASS_IN_SEARCH_SPACE`）；apply 后 check 在联合 lowest-direct
上选出 `pandas==2.2.2` × `numpy==2.2.6` × `openpyxl==3.0.10`（2.2.2 是去掉
`numpy<2` 的第一条 pandas），xlsx 因 openpyxl 3.0.10 失败。未扩 3.11/3.12。
机制见正式报告 §10。

## 4. 观测与产物

证据根目录：`docs/experiments/data/E018/`。
可变 `experiments/markitdown/packages/markitdown/package-floor.json` 只作当次
`ReportStore.read`，不作历史入链。

每条命令写入：

- `{cmd}.txt`、`{cmd}.meta.json`（argv、cwd、起止、墙钟、退出码、`UV_CACHE_DIR`、`GITHUB_ACTIONS`）
- 该阶段 `configuration.toml`
- Journal、`search-summary.json`（经 reader，不手改）
- source snapshot digest、execution/guidance/search policy digest
- PF HEAD、uv/ty、实际 CPython patch、平台 triple

MarkItDown 额外记录：

| 观测 | 何时 | 用途 |
| --- | --- | --- |
| passed / skipped / xfailed / 总数 | 每次 smoke/check/baseline | skip 变化即 oracle 变化 |
| skip 原因直方图（remote / llm / speech / exiftool / fpdf2） | 第一次稳定 smoke | 证明沿用 CI 范围，不是裁套件 |
| 两次 smoke 墙钟与 skip 差 | 阶段 1 | 稳定性 |
| 受管坐标清单、fixed `~=` 三项 | 配置冻结后 | 18 个坐标 ≠ 18 项行为均被覆盖 |
| 候选快照与无 wheel / yanked / 预发布过滤 | 第一次 search | D037 域边界；尤其 contentunderstanding |
| 失败分类：resolve / install / ABI-unavailable / REJECTED / INDETERMINATE | 每个非 PASS | 安装失败 ≠ API 下界 |
| 代表 predecessor 的 `pf diagnose` | search 后 | 摘要不替代 Process Log |
| apply diff 与 apply 后 check | 阶段 3–5 | 维护闭环 |
| 本地补丁 NOTE 与差分 | 仅当 smoke 需要最小化修复 | 后续事实绑定含补丁快照 |

阶段 3/4 的 incomplete 原样归档。不把 CI `--lf`、xdist 墙钟或 skip 变少后的 PASS 写成成功证据。
不把跳过的远程 URL / speech / 真实云服务 floor 宣称为这些功能的真实兼容下界。
不把已放弃的 none surface 写成已搜索结论。

## 5. 可主张与不可主张

完成后报告可以主张：在记录的 Cell、`any` artifact、完整 configured pytest，以及
`GITHUB_ACTIONS=true` 的 CI 范围内，PF 对 `packages/markitdown` 的 extra `all`
执行了 smoke/check/search（及实际发生的 apply/闭环）。

不可以主张：required base / none surface 兼容、MCP/OCR 包兼容、全部 named extras
各自兼容、`>= floor` 的所有组合可用、云服务/语音/远程 URL 真实兼容、
安装失败等于 API 不兼容、本轮证明了 E016 的 hole 率，或这些 floor 是
MarkItDown 项目固有下界。

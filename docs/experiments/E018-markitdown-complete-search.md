# E018 — MarkItDown extra all 搜索

- **状态：** 已完成
- **日期：** 2026-09-12
- **性质：** 非规范性 dogfood 实验事实，不定义新契约，不构成对 MarkItDown 上游声明的修改建议
- **证据位置：** [data/E018/](data/E018/)
- **计划：** [data/E018/pf-markitdown-search-experiment-plan.md](data/E018/pf-markitdown-search-experiment-plan.md)
- **目标：** `experiments/markitdown/packages/markitdown`，MarkItDown `v0.1.7`，上游 commit
  `fd239d5d2be43d9b68329730206b9312c7d5a388` 加本地 `pf-test` 与 `[tool.pf]`
- **PF：** generator `0.4.0`；HEAD `2af5bfeb4e3b3cfd9f4d35ba1f6bb774118dee3a`
- **契约入口：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)、
  [D037](../designs/D037-pf-candidate-search-policy.md)

本轮只搜索 monorepo 成员 `packages/markitdown`。extra 只跑命名 extra **`all`**。
对照 [E017](E017-xarray-core-complete-search.md) 的报告组织：先写配置与准入，
再写 check/search/apply；配置变更追加在后，不回写旧计数。

第一轮曾同时准入 none 与 all。`no-extra` 收集失败后，用户撤回 none。
§4 保留该轮事实；§5 起是单 Cell `all` 的运行。第一次 all×minor 因预发布过滤
incomplete；打开 `azure-ai-contentunderstanding` 的 `search-prereleases` 后
all×minor complete 并 apply。patch refine incomplete。apply 后 check 失败。
未扩 3.11/3.12。

## 1. 兼容性结论与实验配置

MarkItDown `v0.1.7` 是 hatchling 单包，位于 monorepo 成员目录，`requires-python = ">=3.10"`。
base 六个 runtime 坐标，`all` 再加十五个。`magika~=0.6.1`、`mammoth~=1.11.0`、
`youtube-transcript-api~=1.0.0` 按 D001 固定。验证契约是成员目录完整 pytest，对应上游
`hatch test` 的 CI lane，并用 `GITHUB_ACTIONS=true` 跳过真实远程 URL / speech / LLM。

| 观察 | 对 PF 的影响 | 本轮处理 |
| --- | --- | --- |
| 仓库根无 `[project]`；另有 MCP/OCR 成员 | cwd 若在仓库根，省略 selector 无法选 root | cwd 固定为 `packages/markitdown` |
| 无 `dev`/`test` group | 省略 `test-group` 得到空 harness | 新建 `pf-test` |
| 默认 `extra-policy = each` | 会展开 pptx/docx/… 多个 singleton | 自引用 `markitdown[all]` + `extra-policy = "none"` |
| 上游 suite 依赖 extra `all` | none 收集期导入 `lxml`/`pptx` | 用户撤回 none；不打 none 测试补丁 |
| `test_doc_rlink` 使用 `/tmp/test_rlink.txt` | 多 Cell 并发可能竞争 | `test-jobs = 1` |
| Hatch `hatch-test` 额外安装 `openai` | 无库时 LLM 测试 skip | harness 含 `openai`，不提供 API key |
| 多数直接依赖无下界 | `lowest-direct` 会落到无法构建的旧 sdist | 原声明 check REJECTED 保留为观测 |
| `azure-ai-contentunderstanding>=1.2.0b1` | 默认候选不含预发布，search baseline 变成 1.1.0 | 只对该坐标 `search-prereleases = true` |

最终准入配置（副本：[prerelease/configuration.toml](data/E018/prerelease/configuration.toml)）：

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

[[tool.pf.dep]]
name = "azure-ai-contentunderstanding"
search-prereleases = true
```

矩阵：CPython **3.10.16** × `x86_64-unknown-linux-gnu` × `all`，**1 Cell、18 个受管直接依赖、3 pinned**。

## 2. 已执行计划

1. 冻结 `v0.1.7`。第一轮写 `extra-surfaces = [["all"]]`（none + all）。
2. 仓库根、沙箱外、`cwd=experiments/markitdown/packages/markitdown`，`PATH` 前缀 `.venv/bin`，
   `UV_CACHE_DIR=/tmp/pf-uv-cache`，`GITHUB_ACTIONS=true`。
3. 第一次 `pf smoke`：all PASS，none 收集失败。用户撤回 none。
4. 改为自引用 `markitdown[all]`。两次 smoke PASS。原声明 check REJECTED。
5. 第一次 all×minor search incomplete（稳定版 CU baseline 失败）。
6. 对该坐标打开 `search-prereleases`。smoke 再 PASS。all×minor search complete 并 apply。
7. patch refine incomplete。apply 后 check REJECTED。

记录脚本：[run_pf.py](data/E018/run_pf.py)。

## 3. 运行总表

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| smoke-1 `extra-surfaces=[["all"]]` | `20260912T055455.638407Z-234544-47c10a3e` | 1 | 54.25s | `all` PASS（307/33）；`no-extra` 收集缺 `lxml` |
| smoke-2 自引用 `all` | `20260912T055751.936905Z-240091-b9dcc10c` | 0 | 42.87s | PASS；340 collected / 307 passed / 33 skipped |
| smoke-3 | `20260912T055918.129725Z-247037-708925db` | 0 | 49.81s | PASS；同一 passed/skipped |
| check 原声明 | `20260912T060036.819241Z-252969-f19d2ad0` | 1 | 3.55s | `lowest-direct`：`markdownify==0.2.0` 构建 `execfile` |
| search all×minor（无预发布） | `20260912T060058.106115Z-253753-228ef278` | 2 | 94.75s | incomplete；CU 1.1.0 失败；0 观察 |
| smoke 预发布配置 | `20260912T060406.670982Z-261762-76dd9f2a` | 0 | 47.75s | PASS |
| search all×minor（CU 预发布） | `20260912T060501.429407Z-265737-09b84b8e` | 0 | 1498.61s | complete；3 sweep、152 观察 |
| apply all×minor | （无 Verification Run） | 0 | 0.66s | 写回 minor 投影 |
| search patch refine | `20260912T063121.371726Z-359057-667e32d9` | 2 | 415.97s | incomplete；`NO_PASS_IN_SEARCH_SPACE` |
| check apply 后 | `20260912T063853.654264Z-390926-be629222` | 1 | 10.71s | 联合 lowest-direct：pandas 2.2.2 × openpyxl 3.0.10 |

预发布后 smoke 与 complete search 共享 snapshot
`c75426842e2aca1c89adb47a34cf5410d579a55cd9224929bd3048ea82f49d37`。
patch refine 因声明与搜索配置变化而使用
`996fd18f50c1a8e358f781d3e3f2424113fb03020e77fb18e7efce43d3337815`。
历史入链的 complete 报告是
[prerelease/all-minor/search-summary.json](data/E018/prerelease/all-minor/search-summary.json)。
可变 `package-floor.json` 现为 incomplete refine，不作历史证据。

## 4. smoke-1：none 收集失败（已放弃该 surface）

终端见 [admission/smoke-1/smoke.txt](data/E018/admission/smoke-1/smoke.txt)。
选中 2 Cell。`[all]` PASS：340 collected，307 passed，33 skipped。
`[no-extra]` `VERIFIER_EXITED_NONZERO`：收集 `tests/test_pptx_svg.py` 时
`from lxml import etree` 失败。诊断 `failure-ec103297b914356d`。
按用户决定停止 none，不补测试。

## 5. smoke-2 / smoke-3：单 Cell all 最高 baseline 通过

自引用后只选 1 Cell，`extra surfaces: all`。
终端见 [admission/smoke-2/smoke.txt](data/E018/admission/smoke-2/smoke.txt)、
[admission/smoke-3/smoke.txt](data/E018/admission/smoke-3/smoke.txt)。

pytest 9.1.1 收集 340 项：**307 passed、33 skipped、1 warning**（pydub 找不到 ffmpeg）。
skip 来自远程 URL 与 `test_pdf_memory` 缺 fpdf2。两次 skip 集合一致。
最高向量含 `azure-ai-contentunderstanding==1.2.0b3`。

## 6. 原声明 check：lowest-direct 无法解析

终端见 [admission/check/check.txt](data/E018/admission/check/check.txt)。
`failure-a11fbc967428e17d`，`RESOLUTION_FAILED` @ `resolve-project`。
uv lowest-direct 尝试构建 `markdownify==0.2.0`，setup.py 调用 `execfile`。
证据：[admission/check/process-0010.log](data/E018/admission/check/process-0010.log)。
这不是最高 baseline 失败，不否决 search。

## 7. 第一次 all×minor：稳定候选域没有 PASS anchor

终端见 [all-minor/search.txt](data/E018/all-minor/search.txt)。
默认 `search-prereleases = false` 把 search baseline 选成
`azure-ai-contentunderstanding==1.1.0`。`test_cu_converter.py` 在
`UserAgentPolicy() takes no arguments` 处失败。
`SEARCH_FAILED`，0 次搜索观察。摘要：
[all-minor/search-summary.json](data/E018/all-minor/search-summary.json)。

Smoke 的 highest 是 `1.2.0b3`。候选域排除预发布后，稳定最高 1.1.0 不是 PASS anchor。
按计划这是预发布下界的域边界观测。随后只对该坐标打开预发布，不扩大其他坐标。

## 8. 预发布后 all×minor search 与 apply

run-id `20260912T060501.429407Z-265737-09b84b8e`，退出 0，墙钟 1498.61s，
`package-floor.json` **complete**。摘要：
[prerelease/all-minor/search-summary.json](data/E018/prerelease/all-minor/search-summary.json)。

1 Cell，3 sweep，152 次观察（64 ProbePass / 88 ProbeRejection）。
失败 cause：`VERIFIER_EXITED_NONZERO` 43、`RESOLUTION_CONFLICT` 33、
`RESOLUTION_FAILED` 10、`INSTALLATION_FAILED` 2。18 个候选快照。
最高 baseline：`beautifulsoup4==4.15.0`、`requests==2.34.2`、`pandas==2.3.3`、
`azure-ai-contentunderstanding==1.2.0b3` 等。

终端卡片上的版本是 predecessor / 当前向量，不是 floor。
`search-resolution = minor` 的 floor 是该 minor 系列代表，不是「该 minor 全部 patch 已验」。

| 依赖 | 原声明 | floor | predecessor | 相对原声明 |
| --- | --- | --- | --- | --- |
| beautifulsoup4 | （无） | `4.9.3` | `4.8.2`（`RESOLUTION_CONFLICT`） | 发现下界 |
| requests | （无） | `2.21.0` | `2.20.1` | 发现下界 |
| markdownify | （无） | `0.14.1` | `0.13.1`（CLI vector 断言） | 发现下界 |
| charset-normalizer | （无） | `2.0.12` | `1.4.1` | 发现下界 |
| defusedxml | （无） | `0.7.1` | `0.6.0` | 发现下界 |
| python-pptx | （无） | `0.6.23` | `0.5.8` | 发现下界 |
| pandas | （无） | `2.1.4` | `2.0.3`（`numpy.dtype size changed`） | 发现下界 |
| openpyxl | （无） | `3.0.10` | `2.6.4` | 发现下界 |
| xlrd | （无） | `2.0.2` | `1.2.0` | 发现下界 |
| lxml | （无） | `4.6.5` | `4.5.2` | 发现下界 |
| pdfminer.six | `>=20251230` | `20260107` | `20251230` | 抬到当前 minor 代表 |
| pdfplumber | `>=0.11.9` | `0.11.10` | `0.10.4` | 抬到当前 minor 代表 |
| olefile | （无） | `0.47` | `0.46` | 发现下界 |
| pydub | （无） | `0.1.1` | 无 | 发现下界 |
| SpeechRecognition | （无） | `1.0.4` | 无 | 发现下界 |
| azure-ai-documentintelligence | （无） | `1.0.2` | 无 | 发现下界 |
| azure-ai-contentunderstanding | `>=1.2.0b1` | `1.2.0b3` | `1.1.0`（CU `UserAgentPolicy`） | 抬到预发布最高 |
| azure-identity | （无） | `1.0.1` | 无 | 发现下界 |

代表诊断：
[diagnose-3d21969fb1147fcb.txt](data/E018/prerelease/all-minor/diagnose-3d21969fb1147fcb.txt)
（CU 1.1.0）、
[diagnose-4e17f73e396b1148.txt](data/E018/prerelease/all-minor/diagnose-4e17f73e396b1148.txt)
（markdownify 0.13.1）、
[diagnose-f914e7c856f2e4a3.txt](data/E018/prerelease/all-minor/diagnose-f914e7c856f2e4a3.txt)
（pandas 2.0.3，numpy ABI）。pandas predecessor 是导入期 ABI 失败，不得写成 API 语义下界。

apply 退出 0，写回 base 与 extra `all` 的 `>= floor`。
差分：[prerelease/all-minor/apply.diff](data/E018/prerelease/all-minor/apply.diff)。
singleton extras（`pptx` / `docx` / …）未在本 Cell 激活，声明保持原样。
这是实验克隆上的授权编辑。

## 9. patch refine：无域内可表示投影

将空间改为 `minors[declaration]` × `patch` 后再 search。
run-id `20260912T063121.371726Z-359057-667e32d9`，退出 2，墙钟 415.97s，
**incomplete**，`SEARCH_FAILED` / 无可表示投影。未 apply。

诊断 `failure-bcb1098a37ebc047`：精确向量把若干坐标降到声明 minor 内低于
all×minor floor 的 patch（如 `beautifulsoup4==4.9.0`、`charset-normalizer==2.0.0`、
`openpyxl==3.0.10`），CLI xlsx vector 失败。与 [E014](E014-mkdocs-complete-search.md)
相同：`NO_PASS_IN_SEARCH_SPACE` 不是「联合空间无解」，all×minor 的 exact floor
向量仍然成立。证据：[prerelease/patch-refine/](data/E018/prerelease/patch-refine/)。

## 10. apply 后 check

对 all×minor 写回的声明再 `pf check`：
run-id `20260912T063853.654264Z-390926-be629222`，退出 1，墙钟 10.71s。
xlsx CLI 失败：`Pandas requires version '3.1.0' or newer of 'openpyxl'`。
证据：[prerelease/post-apply-check/process-0014.log](data/E018/prerelease/post-apply-check/process-0014.log)。

search 的 final exact 向量是 `pandas==2.1.4` + `openpyxl==3.0.10`（及其余 floor 钉死）。
该组合过了 uv solve 与 oracle。apply 按 D001 写成 `>=version`，不是 `==version`。
`pf check` 不回放报告精确向量，只对当前声明重新做 `lowest-direct`。

安装图不是 `pandas==2.1.4`。`uv pip sync` 实际装了 `pandas==2.2.2`、
`numpy==2.2.6`、`openpyxl==3.0.10`。
证据：[prerelease/post-apply-check/process-0012.log](data/E018/prerelease/post-apply-check/process-0012.log)。
`2.2.2` 仍满足 `>=2.1.4`；`3.0.10` 仍是 floor。pandas 2.2.2 运行时要求
`openpyxl>=3.1.0`，与未上移的 openpyxl floor 冲突。

这不是「lowest-direct 忽略了 2.1.4 floor」，也不是「3.0.10 从未通过」。
事后对照 PyPI `Requires-Dist`（非本轮 PF 记录）：

| 发行 | CPython 3.10 上的 numpy 约束 |
| --- | --- |
| pandas 2.1.4 … 2.2.1 | `numpy>=1.22.4,<2` |
| pandas 2.2.2 | `numpy>=1.22.4`（去掉 `<2`） |

`2.2.2` 是第一条允许 numpy 2 的 pandas。`magika~=0.6.1` 按 D001 固定，其传递链
`onnxruntime` 只声明 `numpy>=…`、无上界。uv `lowest-direct` 对直接依赖取最低、
对传递依赖仍取最高，因此 numpy 落到 `2.2.6`。`pandas==2.1.4` 在 `numpy>=2` 下
UNSAT，求解器把直接依赖抬到仍满足 `>=2.1.4` 的 `2.2.2`。

search exact 把 `pandas==2.1.4` 钉死，等于强制 `numpy<2`，magika/onnxruntime
只能选 numpy 1.x。那是另一道 SAT 题，所以 exact 可以通过。check 打开 `>=` 后，
传递依赖的 highest 把 2.1.4–2.2.1 挤出可行域。本轮没有再跑 uv 求解器追踪；
机制由安装图与公开元数据对照得出。

apply diff 不等于已验证的联合最低向量。D001：floor 不是直接依赖笛卡尔积的
全局最小值。

## 11. 可主张的边界

可以主张：在 **CPython 3.10、extra `all`、`GITHUB_ACTIONS=true`、完整 configured pytest**
下，PF 完成了 smoke、原声明 check（REJECTED）、第一次无预发布 search（incomplete）、
预发布后 all×minor search（complete）、apply，以及 apply 后 check（REJECTED）。
多数无下界坐标得到了 minor 代表 floor；pdfminer / pdfplumber / contentunderstanding
需要抬到当前合格代表。none surface 未搜索。本次 check 失败的安装图是
`pandas==2.2.2` × `numpy==2.2.6` × `openpyxl==3.0.10`；`2.2.2` 是公开元数据上
第一条去掉 `numpy<2` 的 pandas。这解释了为何 exact `2.1.4` 通过而
`lowest-direct` 不复放它。

不可以主张：required base / 各 named extra 各自兼容、MCP/OCR 兼容、
`>= floor` 的所有组合可用、云服务/语音/远程 URL 真实兼容、pandas 2.0.3 的 ABI
失败等于 API 不兼容、patch refine 证明 4.9 / 3.0 系列无解、uv 在所有图上都会
先钉死最高 numpy，或这些 floor 是 MarkItDown 项目固有下界。工作树停在
all×minor apply 后的声明，外加未采用的 patch-refine 配置；可变
`package-floor.json` 现为 incomplete refine 报告。

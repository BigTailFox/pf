# E017 — xarray core 准入失败（最高 baseline 未通过）

- **状态：** 已完成
- **日期：** 2026-09-12
- **性质：** 非规范性 dogfood 实验事实，不定义新契约，不构成对 xarray 上游声明的修改建议
- **证据位置：** [data/E017/](data/E017/)（第一轮准入失败、本地补丁、补丁后 smoke/check/search/apply）
- **计划：** [data/E017/pf-xarray-core-search-experiment-plan.md](data/E017/pf-xarray-core-search-experiment-plan.md)
- **目标：** `experiments/xarray`，xarray `v2026.07.0`，上游 commit
  `0238035a646a04a6d2b603cc5cee5cbefa304e23` 加本地 `pf-core` 与 `[tool.pf]`
- **PF：** generator `0.4.0`；HEAD `72912785f381ed41bd84da599b13a26ec2a09343`
- **契约入口：** [D001](../designs/D001-pf.md)、[D003](../designs/D003-pf-search-algorithm.md)、
  [D005](../designs/D005-pf-failure-and-diagnose.md)、[D008](../designs/D008-pf-verification-run.md)、
  [D012](../designs/D012-pf-harness-relaxation.md)、[D014](../designs/D014-pf-report-schema.md)、
  [D037](../designs/D037-pf-candidate-search-policy.md)

第一轮按计划完成了冻结与阶段 1 准入，**没有进入 check/search/apply**。
最高版本 `pf smoke` 被 configured pytest 拒绝。按当时计划，这是否决 search 的条件；
没有给声明加人工上界，也没有删除或跳过失败测试。

2026-09-12 用户授权对 `test_repr` 做本地金串补丁后再搜索。补丁见
[data/E017/local-patch/NOTE.md](data/E017/local-patch/NOTE.md)。
下文 §1–§8 保留第一轮事实；补丁后运行追加在后面，不回写旧计数。

对照 [E012](E012-flask-complete-search.md)–[E014](E014-mkdocs-complete-search.md)：
那些案例有通过的最高 baseline。E017 记录的是 **core 源码快照与当前最高 pandas 的 oracle 不闭合**，
不是搜索算法结论。

## 1. 兼容性结论与实验配置

xarray `v2026.07.0` 是 setuptools 单包，`requires-python = ">=3.11"`，三个 runtime 坐标
`numpy>=1.26`、`packaging>=24.2`、`pandas>=2.2`。验证契约是上游完整 pytest
（`testpaths = ["xarray/tests", "properties"]`），对应 pixi `test-py311-bare-minimum`。
只搜索 required base；`extra-policy = "none"`。论文口径仍是 **core / upstream bare-minimum**。

| 观察 | 对 PF 的影响 | 本轮处理 |
| --- | --- | --- |
| `dev` 含 `xarray[complete,types]` | 省略 `test-group` 会把可选闭包拉进 harness | 新建 `pf-core` |
| `resolve-artifact = "wheel"` | uv `--only-binary :all:` 连本地源码快照也不能构建 | 改为 `any`（计划 §2.1） |
| 本机 CPython 无 IANA 时区 | 收集 `test_pandas_to_xarray.py` 因 `US/Pacific` 失败 | harness 加入 `tzdata` |
| 最高 pandas 3.0.5 的 Dataset repr | `TestDataset.test_repr` 期待 category `36B`，实际 `32B` | 记录 REJECTED，停止 search |

加入的最终准入配置（副本：[admission/configuration.toml](data/E017/admission/configuration.toml)）：

```toml
[dependency-groups]
pf-core = [
  "pytest",
  "pytest-asyncio",
  "pytest-env",
  "pytest-mypy-plugins>=4.0.0",
  "pytest-timeout",
  "hypothesis",
  "pytz",
  "tzdata",
]

[tool.pf]
pythons = ["3.11"]
test-group = "pf-core"
extra-policy = "none"
test-command = ["pytest", "--timeout", "180"]
resolve-artifact = "any"
test-timeout = "2h"
search-space = "all"
search-resolution = "minor"
```

矩阵：CPython **3.11.15** × `x86_64-unknown-linux-gnu` × `no-extra`，**1 Cell、3 个受管直接依赖、0 pinned**。

## 2. 已执行计划

1. 冻结 `v2026.07.0`，只写 `pf-core` 与 `[tool.pf]`。
2. 仓库根、沙箱外、`cwd=experiments/xarray`，`PATH` 前缀 `.venv/bin`，`UV_CACHE_DIR=/tmp/pf-uv-cache`。
3. 三次 `pf smoke --test-jobs 1`：wheel 策略失败、缺 `tzdata` 失败、最高 verifier 失败。
4. 在 PF 外用同一最高向量复现 `test_repr`；`origin/main`（`8de862c2`）同样失败。
5. 按计划停止。未跑 `pf check` / `pf search` / `pf apply`。

记录脚本：[run_pf.py](data/E017/run_pf.py)。

## 3. 运行总表

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| smoke-1 `resolve-artifact=wheel` | `20260912T050713.649383Z-204303-8eb7f92e` | 1 | 0.83s | `RESOLUTION_FAILED`；本地 xarray sdist 被禁用 |
| smoke-2 `any`，无 `tzdata` | `20260912T050802.214872Z-205432-aa1900a0` | 1 | 11.73s | 收集 `test_pandas_to_xarray.py`：`ZoneInfoNotFoundError` |
| smoke-3 加入 `tzdata` | `20260912T050849.931948Z-206661-2565179b` | 1 | 19.66s | `VERIFIER_EXITED_NONZERO`：`test_repr` |

smoke-3 安装的最高向量（[process-0007-install.log](data/E017/admission/smoke-3/process-0007-install.log)）：

```text
numpy==2.4.6  packaging==26.3  pandas==3.0.5  xarray==9999 (source snapshot)
```

pytest 9.1.1 收集约 16731 项。`--maxfail=1` 停在 `test_repr` 时：
**1893 passed、5214 skipped、4 xfailed、3 xpassed**。
大量 skip 来自缺失的可选后端，符合 core / bare-minimum，不是事后 `-k`。

## 4. smoke-1：wheel 策略与源码 target 冲突

终端见 [admission/smoke-1/smoke.txt](data/E017/admission/smoke-1/smoke.txt)。
`resolve-project` 使用 `--only-binary :all:`，stderr：
`Building source distributions for xarray is disabled`。
D001 的 artifact policy 同时约束 project 解析与 Candidate，因此 **不能** 在必须从快照构建的 target 上使用 `wheel`。
已改为 `any`。这不是 numpy/pandas API 结论。

## 5. smoke-2：harness 缺时区数据

终端见 [admission/smoke-2/smoke.txt](data/E017/admission/smoke-2/smoke.txt)。
收集期 `date_range(..., tz="US/Pacific")` 需要 `tzdata`。
这是环境/harness 缺口，不是搜索坐标。加入 `tzdata` 后收集通过。

## 6. smoke-3：最高 baseline 被 verifier 拒绝

终端见 [admission/smoke-3/smoke.txt](data/E017/admission/smoke-3/smoke.txt)。
失败 nodeid：`xarray/tests/test_dataset.py::TestDataset::test_repr`。
Process Log：[admission/smoke-3/process-0009.log](data/E017/admission/smoke-3/process-0009.log)。

测试按 `Version(pd.__version__) >= Version("3.0.0dev0")` 期待 category **36B**；
当前 `pandas==3.0.5` 下 Dataset repr 为 **32B**。
同一断言在 PF 外、`v2026.07.0` 与 `origin/main` `8de862c2` 上复现
（[repr-mismatch.txt](data/E017/admission/smoke-3/repr-mismatch.txt)）。

因此：

- 这不是 PF 配置或 observer 伪影。
- 这不是安装/ABI 失败。
- 不能解释成「声明下界过低」；最高向量没有完整 oracle PASS。
- 不能用给 pandas 加上界或删除 `test_repr` 换取 search。
- 更新到当时 `main` 也不能解除该门控。

## 7. 未执行的阶段

阶段 1b–5（check、扩 Cell、all×minor、patch refine、apply 后 check）均未执行。
没有 `package-floor.json` 历史证据，也没有 floor 投影。

## 8. 可主张与不可主张

可以主张：在记录的 Cell、`any` artifact 与完整 configured pytest 下，
xarray `v2026.07.0` core 的最高直接向量 **没有** 通过 PF smoke；失败点是
`test_repr` 的 pandas 3 category nbytes 金串。PF 按现行契约拒绝 baseline，未开始搜索。

不可以主张：xarray 声明下界错误或正确、三个坐标的条件最小向量、
wheel 域下界、全部 extras/后端兼容，或 PF 搜索在该项目上成功/失败。
后续若要重试，需要一份 **自身测试已与当时最高 pandas/numpy 对齐** 的源码快照，
再重新做阶段 1；那是新运行，不能回写本报告。

## 9. 本地补丁后继续（2026-09-12）

用户授权修复最高 baseline 后再搜索。补丁说明：[local-patch/NOTE.md](data/E017/local-patch/NOTE.md)。
差分：[test_repr-category-nbytes.diff](data/E017/local-patch/test_repr-category-nbytes.diff)、
[requires-scipy-tree-index-rename.diff](data/E017/local-patch/requires-scipy-tree-index-rename.diff)。

1. `test_repr`：不再按 pandas 版本写死 category `36B`/`32B`，改用
   `render_human_readable_nbytes(data["var4"].nbytes)`。
2. `test_tree_index_rename`：补上同文件已有的 `@requires_scipy`（bare-minimum 不含 scipy）。

这是实验克隆补丁，不是上游建议。后续事实绑定快照
`11bcbf1563ff1ba12935c88bb3d0a5e53741ccc737348487391ea159f0895157`（含补丁与 `pf-core`）。
本轮只跑 CPython 3.11，未扩 3.12/3.13。

配置副本：[patched/configuration.toml](data/E017/patched/configuration.toml)。

## 10. 补丁后准入与 check

| 命令 | run-id | 退出码 | 墙钟 | 结果 |
| --- | --- | ---: | ---: | --- |
| smoke-1（仅 test_repr 补丁） | `20260912T051550.717882Z-213331-0a3d8294` | 1 | 37s | `test_tree_index_rename` 缺 scipy |
| smoke-2 | `20260912T051716.663380Z-214608-226f3475` | 0 | 78.28s | PASS；6789 passed / 9933 skipped |
| smoke-3 | `20260912T051846.686280Z-215535-e5bcdbb9` | 0 | ~92s | PASS；同一 passed/skipped |
| check 原声明 | `20260912T052024.829863Z-216961-b4e6c707` | 1 | ~10s | 声明 lowest-direct REJECTED |

两次稳定 smoke 的 skip 集合一致。check 在导入 `conftest` 时，pandas 2.2 的
「Pyarrow will become a required dependency」`DeprecationWarning` 被
`error:::xarray.*` 当成来自 xarray 的错误（stacklevel 指向导入方）。
这是声明下界失配观测，不是 search 否决。证据：[patched/check/](data/E017/patched/check/)。

## 11. all×minor search 与 apply

`search-space = "all"` × `minor`，`--max-duration 12h`。
run-id `20260912T052104.062668Z-218220-e6bf3827`，退出 0，墙钟 713.76s，
`package-floor.json` **complete**。摘要：[patched/all-minor/search-summary.json](data/E017/patched/all-minor/search-summary.json)。

1 Cell，3 sweep，29 次观察（13 ProbePass / 16 ProbeRejection）。
失败 cause：`RESOLUTION_FAILED` 7、`RESOLUTION_CONFLICT` 4、`VERIFIER_EXITED_NONZERO` 5。
最高 baseline：`numpy==2.4.6`、`packaging==26.3`、`pandas==3.0.5`。

| 依赖 | 原声明 | floor | predecessor | 相对原声明 |
| --- | --- | --- | --- | --- |
| numpy | `>=1.26` | `1.25.2` | `1.24.4`（收集 `test_coding_strings.py`：numpy AttributeError） | 降到声明之前的 minor 代表 |
| packaging | `>=24.2` | `14.0` | 无 | 降到远低于声明 |
| pandas | `>=2.2` | `2.2.3` | `2.1.4`（收集 `test_groupby.py`：`Invalid frequency: ME`） | 抬到当前 minor 的合格 patch |

终端卡片上的版本是 predecessor / 当前向量，不是 floor。
`1.25.2` 是 `search-resolution = minor` 的 1.25 系列代表，不是「1.25 全部 patch 已验」。

apply 退出 0，写回
`dependencies = ["numpy>=1.25.2", "packaging>=14.0", "pandas>=2.2.3"]`。
差分：[patched/all-minor/apply.diff](data/E017/patched/all-minor/apply.diff)。
这是实验克隆上的授权编辑。

## 12. patch refine：无域内 PASS

将空间改为 `minors[declaration]` × `patch` 后再 search。
run-id `20260912T053402.036285Z-226064-00bc36fb`，退出 2，墙钟 101.45s，
**incomplete**，`SEARCH_FAILED` / 无可表示投影。未 apply。

诊断 `failure-b274f94743f063f7`：精确向量
`numpy==1.25.2, packaging==26.3, pandas==3.0.5` 在 `resolve-project` 为
`RESOLUTION_CONFLICT`（pandas 3.0.5 与 numpy 1.25.2 传递冲突）。
1.25.2 是缩窄后的域内候选，最高 pandas 是空间外 baseline sentinel。
与 [E014](E014-mkdocs-complete-search.md) 相同：`NO_PASS_IN_SEARCH_SPACE` 不是「联合空间无解」，
all×minor 的 `1.25.2 + 14.0 + 2.2.3` PASS 仍然成立。

## 13. apply 后 check

对 all×minor 写回的声明再 `pf check`：
run-id `20260912T053611.051572Z-227568-dd3ed6cb`，退出 0，墙钟 79.89s，
`check passed at [declaration][lowest-direct]`。
pytest：6770 passed / 9952 skipped（与最高 smoke 的 6789/9933 不同，因为向量是
pandas 2.2.3 不是 3.0.5；覆盖变化已记录）。

## 14. 补丁后可主张的边界

可以主张：在 **含上述两处本地测试补丁** 的 `v2026.07.0` core、CPython 3.11、
完整 configured pytest 下，PF 完成了 smoke、原声明 check（REJECTED）、
all×minor search（complete）、apply，以及 apply 后 check（PASS）。
numpy / packaging 的声明下界在该 oracle 下可以降低；pandas 需要抬到 `2.2.3`。

不可以主张：未打补丁的上游 tag 已通过最高 baseline、floor 覆盖全部 extras/后端、
`>= floor` 的所有组合可用、patch refine 证明 1.25 系列无解，或这些 floor 是
xarray 项目固有下界。工作树停在 all×minor apply 后的声明，外加未采用的
patch-refine 配置；可变 `package-floor.json` 现为 incomplete refine 报告，
历史入链只使用 [patched/all-minor/search-summary.json](data/E017/patched/all-minor/search-summary.json)。


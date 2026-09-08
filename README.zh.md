# PF — Package Floor

[English](README.md) | 简体中文

> 找出 Python 包经过验证的直接依赖下界。

## 它做什么

PF 在隔离环境中发现候选版本，尝试以当前声明所允许的最高版本环境的 `ty` 诊断作为静态基线，再运行项目的完整测试命令。兼容性结论只来自该动态证据；`ty` 可以改变后续探测顺序，但不能排除候选。它记录可解释、经过验证的精确依赖向量。

搜索单位是一个可独立安装的包和一个兼容性 cell：精确 uv target triple、CPython minor、extra 兼容面。在冻结的候选快照中，PF 返回经过完整测试的坐标最小向量。它不声称得到依赖笛卡尔积的全局最小值，也不证明未探测版本或其他组合兼容。产品契约以 [D001](docs/designs/D001-pf.md) 为准。

对已准入的解析与安装请求，未归因的正常非零退出会拒绝当前 Attempt，包括构建 backend 内部失败；这不证明依赖冲突或可重复失败。后端也可能用这种退出表示网络、权限问题，因此误拒绝可能抬高报告下界、遗漏可行向量或导致无结果。超时、异常终态以及 PF 直接观察的外因或一致性失败仍为 Indeterminate。最终结果仍须实际解析、安装、图检查和完整 verifier PASS；PF 不增加自动重试，也不以一次观察承诺可复现性。

## 安装

```bash
uv tool install package-floor
```

也可以 `pip install package-floor`。命令行入口是 `pf`。从仓库开发时用 `uv run pf`。

## 快速开始

目标项目需要静态 `project.dependencies`（以及用到的 optional-dependencies）。测试 dependency group 可省略：未配置 `test-group` 时，在 workspace root 与所选 member 中依次查找 `dev`、`test`，都不存在时按空 group 执行。显式名称只选择该 group，不存在时同样按空 group 执行。默认测试命令为 `pytest`；PF 不自动安装它。例如，为项目提供测试工具：

```toml
[dependency-groups]
test = ["pytest"]
```

然后：

```bash
pf smoke
pf search
pf apply
```

`smoke` 在当前声明允许的最新版本上做一次 fresh install 检查。`search` 写出 `package-floor.json`。授权通过后，`apply` 按该报告更新项目的依赖下界。

## 命令

| 命令 | 作用 |
| --- | --- |
| `pf smoke` | 以允许的最新版本做 fresh install、尝试捕获 `ty` 基线并跑完整测试。缺少 `ty` 基线仍进入 verifier。不搜索、不写报告。 |
| `pf check` | 验证项目已声明的下界。不搜索、不写报告。 |
| `pf search` | 寻找经过验证的 floor，并写出 `package-floor.json`。从不编辑项目元数据。 |
| `pf explain` | 读取报告，展示 floor、覆盖面与 apply 阻碍。 |
| `pf apply` | 在授权通过后按报告编辑项目元数据。`--force` 只豁免 source-layer drift。 |
| `pf minimize` | 先 `search`，再执行默认 `apply`。 |
| `pf diagnose FAILURE_ID` | 解释一条已记录的拒绝或不确定结果。离线，不重放。 |
| `pf merge REPORT ... --output PATH` | 合并不同宿主上生成的兼容报告。 |

常见流程：`pf smoke` → `pf search` → `pf explain` → `pf apply`。需要一步搜索并应用时用 `pf minimize`。

## 使用要求

- 省略 `--package` 时选择可安装的 workspace root。显式值必须是某个 workspace member 的规范 distribution name，不能是路径。
- 每个进程只执行与当前宿主匹配的 target。其他宿主用 `pf merge` 合并。本宿主全部成功且只缺其他宿主时，`pf search` 退出 0 并写出 incomplete report，便于 CI 收集 artifact。
- `search` 只写 `package-floor.json`。`apply` 不重新解析依赖，也不再运行 `ty` 或测试。

## 配置

配置从 workspace root 的 `[tool.pf]` 合并到所选 member 自己的 `[tool.pf]`，CLI 显式值只覆盖本次运行。例如：

```toml
[tool.pf]
test-command = ["pytest"]
search-resolution = "patch"
max-cells = 4

[tool.pf.search-space-defaults]
with-lower-bound = "majors[declaration-1:]"
without-lower-bound = "majors[baseline-2:]"
```

这是配置示例，不是完整默认值表。Group、Cell、并发、timeout 与层级合并见
[D001 配置](docs/designs/D001-pf.md#7-配置)；space、条件默认、逐依赖覆盖和精确 baseline artifact 见
[D037 候选与搜索策略](docs/designs/D037-pf-candidate-search-policy.md)。
`search-resolution` 控制所选空间内的采样，最终 floor 仍是精确版本。

test group 中的 `requests[socks]` 这类自引用，让 `socks` 成为每个 Cell 的必需 surface；
extra 探索叠加在该必需 surface 上。没有活跃外部测试依赖的 Cell 直接安装 project plan，仍运行配置的 verifier。
更换测试命令或 harness 会改变验证契约，也可能改变 floor；完整边界见
[D001 验证契约](docs/designs/D001-pf.md#4-候选与验证边界)。

## 锁定的工具版本

发行版精确固定 uv `0.12.5` 与 ty `0.0.74`。resolver protocol 只接受该 uv 版本，其他版本会 fail closed。升级任一工具都必须先重新资格化，再改 pin。

## 文档

- [D001 — 产品与命令契约](docs/designs/D001-pf.md)：floor、命令、配置、报告与退出码
- [工程文档索引](docs/README.md)：契约所有权与文档布局

## 许可证

Apache License 2.0。见 [LICENSE](LICENSE)。

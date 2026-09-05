# PF — Package Floor

[English](README.md) | 简体中文

> 找出 Python 包经过验证的直接依赖下界。

## 它做什么

PF 在隔离环境中发现候选版本，以当前声明所允许的最高版本环境的 `ty` 诊断作为静态基线，再运行项目的完整测试命令，为每个受管直接依赖生成可解释、可复现的下界。

搜索单位是一个可独立安装的包和一个兼容性 cell：精确 uv target triple、CPython minor、extra 兼容面。在冻结的候选快照中，PF 返回经过完整测试的坐标最小向量。它不声称得到依赖笛卡尔积的全局最小值，也不证明未探测版本或其他组合兼容。产品契约以 [D001](docs/designs/D001-pf.md) 为准。

## 安装

```bash
uv tool install package-floor
```

也可以 `pip install package-floor`。命令行入口是 `pf`。从仓库开发时用 `uv run pf`。

## 快速开始

目标项目需要静态 `project.dependencies`（以及用到的 optional-dependencies），以及名为 `test` 的 dependency group。省略 `test-group` 即使用该名称；group 本身可为空。省略测试命令即运行 `pytest`。例如，为项目提供测试工具：

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
| `pf smoke` | 以允许的最新版本做 fresh install、捕获 `ty` 基线并跑完整测试。不搜索、不写报告。 |
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

持久配置只合并两层：workspace root 的 `[tool.pf]`，再到所选 member 自己的 `[tool.pf]`。CLI 显式值只覆盖本次运行。未知 key 会失败。下面除 `pythons`、`platforms` 外都是省略时的默认值；这两项按项目与宿主推断。省略 `test-group` 即使用名为 `test` 的 dependency group。

```toml
[tool.pf]
test-command = ["pytest"]          # 默认 argv；显式值整体替换，不能以 "uv run" 开头
# pythons = ["3.10", "3.11", "3.12"]  # CPython minor；省略则按 requires-python 推断
# platforms = ["x86_64-unknown-linux-gnu"]  # uv target triple；省略则用当前宿主
extra-policy = "each"              # none | each | all
extra-surfaces = []                # 额外 extra 组合，例如 [["docs", "check"]]
# search-space = "all"             # 显式覆盖；省略时使用下方条件默认表
search-resolution = "minor"        # major | minor | patch
search-prereleases = false
resolve-artifact = "any"         # wheel | sdist | any
# managed-deps = ["rich"]          # 与 unmanaged-deps 互斥
# unmanaged-deps = ["build"]       # 两者都省略则管理全部可搜索直接依赖
test-group = "test"                # 省略即用名为 test 的 group；该 group 可为空
test-cwd = "package"               # package | root
ty-args = []
max-cells = "auto"                 # auto 或正整数；Cell 并发
ty-jobs = "auto"                   # ty 进程并发
test-jobs = "auto"                 # verifier 并发
resolve-timeout = "10m"
ty-timeout = "10m"
test-timeout = "30m"               # 三个 timeout 都可设 "none"

# [[tool.pf.dep]]
# name = "rich"                    # 规范 distribution name
# search-space = "majors[baseline]" # 或 minors[...] / 一段 PEP 440 specifier
# search-resolution = "minor"
# search-prereleases = false

[tool.pf.search-space-defaults]
with-lower-bound = "majors[declaration-1:]"
without-lower-bound = "majors[baseline-2:]"
```

所有 space 都可搭配 `major`、`minor` 或 `patch` resolution。`baseline` 锚定已验证的最高版本，`declaration` 锚定各 Cell 活跃直接声明的最强下界。偏移沿 registry 已存在系列移动，切片左闭右开；例如 `majors[baseline-2:]` 在 baseline cap 与候选过滤约束下，包含 baseline major 及前两个已有 major。被过滤的系列仍占位置。

窄 space 可以排除已验证的 baseline 版本。PF 会另外冻结该 baseline 的精确 artifact，使多依赖 probe 仍可复现，但不会把该版本加入搜索候选、窗口、边界或 floor。

显式 space 优先于条件默认，逐依赖 space 优先于全局 space。默认表两项必填，按完整对象替换继承；`without-lower-bound` 不得引用 declaration。`[[tool.pf.dep]]` 也整表替换；member 省略 `dep` 才继承 root 表，`dep = []` 则清空。缺声明下界前提在搜索前退出 3；registry anchor/scope 无法求值退出 2，报告保持原状。完整规则见 [D001](docs/designs/D001-pf.md)。

test group 中对当前项目的自引用 extras 是每个 Cell 的必需 surface。例如 `requests[socks]` 让所有 Cell 包含 `socks`；extra-policy 只自动探索其余依赖列表非空的 extras，`none` 也保留必需 extras。空组默认跳过，显式 `extra-surfaces` 和自引用要求仍可包含空组。Floor 相对于配置的验证契约成立；更换测试命令或 harness 可能改变结果。

## 锁定的工具版本

发行版精确固定 uv `0.12.5` 与 ty `0.0.74`。resolver protocol 只接受该 uv 版本，其他版本会 fail closed。升级任一工具都必须先重新资格化，再改 pin。

## 文档

- [D001 — 产品与命令契约](docs/designs/D001-pf.md)：floor、命令、配置、报告与退出码
- [工程文档索引](docs/README.md)：契约所有权与文档布局

## 许可证

Apache License 2.0。见 [LICENSE](LICENSE)。

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

目标项目需要静态 `project.dependencies`（以及用到的 optional-dependencies）、一个 `test` dependency group（可为空），以及 `[tool.pf]` 测试命令：

```toml
[tool.pf]
test-command = ["pytest"]
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

持久配置只合并两层：workspace root 的 `[tool.pf]`，再到所选 member 自己的 `[tool.pf]`。CLI 显式值只覆盖本次运行。

`max-cells`、`ty-jobs` 和 `test-jobs` 分别限制 Cell、`ty` 和 verifier 并发。完整字段、默认值与退出码见 [D001](docs/designs/D001-pf.md)。

## 锁定的工具版本

发行版精确固定 uv `0.12.5` 与 ty `0.0.74`。resolver protocol 只接受该 uv 版本，其他版本会 fail closed。升级任一工具都必须先重新资格化，再改 pin。

## 文档

- [D001 — 产品与命令契约](docs/designs/D001-pf.md)：floor、命令、配置、报告与退出码
- [工程文档索引](docs/README.md)：契约所有权与文档布局

## 许可证

Apache License 2.0。见 [LICENSE](LICENSE)。

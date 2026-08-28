# PF — Package Floor

> 找出 Python 包在给定环境中经过验证的直接依赖下界。

PF 在隔离环境中发现候选版本，以最高允许版本环境的 `ty` 诊断作为静态基线，再运行项目完整测试，为直接依赖生成可解释、可复现的精确下界。

## 当前能力

PF v1 以一个可独立安装的包和一个兼容性 cell 为搜索单位：

```text
(精确 uv target triple, CPython minor, extra 兼容面)
```

在冻结的候选快照中，PF 返回经过完整测试的坐标最小向量。它不声称得到依赖笛卡尔积的全局最小值，也不证明未探测版本或其他组合兼容。完整产品边界以 [D001 产品契约](docs/designs/D001-pf.md) 为准。

## 命令契约

```text
pf smoke [package] [--jobs auto|N]
pf check [package] [--jobs auto|N]
pf search [package] [--jobs auto|N] [--max-duration DURATION]
pf apply [package]
pf minimize [package] [--jobs auto|N] [--max-duration DURATION]
pf explain [package]
pf diagnose [package] [--failure FAILURE_ID]
pf merge REPORT [REPORT ...] --output PATH
```

开发环境可直接运行：

```text
uv run pf --help
uv run pf smoke
uv run pf check
uv run pf search
uv run pf explain
uv run pf apply
```

项目至少需要静态 `project.dependencies` / `project.optional-dependencies`、一个 `test` dependency group（可为空）以及 `[tool.pf].test-command`。每个进程只执行与当前宿主精确匹配的 target；其他宿主生成的报告使用 `pf merge` 合并。

PF 的发行依赖精确固定为已验证的 uv `0.12.5` 与 ty `0.0.74`。当前 resolver protocol 只支持 uv `0.12.5`；其他 uv 版本会 fail closed，不沿用未经 qualification 的诊断 parser。升级任一工具都必须先复跑对应验证，再更新精确版本。

`search` 只写 `package-floor.json`。`apply` 只消费完整、未漂移且可表示的报告，不重新解析依赖、不运行 `ty` 或测试。

`smoke` 在当前声明约束内建立尽可能新的 fresh install，捕获一次 `ty` 静态基线并运行完整测试。静态诊断本身不是兼容性失败；普通 Cell 摘要只展示最终结果，详细进程记录写入 `.pf/logs/`，失败时通过 `pf diagnose` 查看。

`diagnose` 只读、离线地解释报告或最近一次验证运行中的 Rejection / Indeterminate；它不访问网络、不创建环境，也不隐式重放失败。

## 文档

- [工程文档索引](docs/README.md)：契约所有权、状态词和文档布局
- [D001 — 产品与命令契约](docs/designs/D001-pf.md)：floor、命令、配置、报告与退出码

D001–D008、D012–D014 是现行契约；D009–D011 的实施决策与 D015 的 verifier terminal authority 已归并到这些所有者。Plan、Review 与 Investigation 只记录历史执行或观察，不承担现行契约。

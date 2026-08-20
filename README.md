# PF — Package Floor

> 找出 Python 包在给定环境中经过验证的直接依赖下界。

PF 在隔离环境中发现候选版本，以最高允许版本环境的 `ty` 诊断作为静态基线，再运行项目完整测试，为直接依赖生成可解释、可复现的精确下界。

## 当前能力

PF v1 以一个可独立安装的包和一个兼容性 cell 为搜索单位：

```text
(精确 uv target triple, CPython minor, extra 兼容面)
```

在冻结的候选快照中，PF 返回经过完整测试的坐标最小向量。它不声称得到依赖笛卡尔积的全局最小值，也不证明未探测版本或其他组合兼容。完整产品边界以 [D001 产品契约](docs/designs/D001-pf.md) 为准。

## 命令

```text
pf check [package] [--jobs auto|N]
pf search [package] [--jobs auto|N] [--max-duration DURATION]
pf apply [package]
pf minimize [package] [--jobs auto|N] [--max-duration DURATION]
pf explain [package]
pf merge REPORT... --output PATH
```

开发环境可直接运行：

```text
uv run pf --help
uv run pf check
uv run pf search
uv run pf explain
uv run pf apply
```

项目至少需要静态 `project.dependencies` / `project.optional-dependencies`、一个 `test` dependency group（可为空）以及 `[tool.pf].test-command`。每个进程只执行与当前宿主精确匹配的 target；其他宿主生成的报告使用 `pf merge` 合并。

`search` 只写 `package-floor.json`。`apply` 只消费完整、未漂移且可表示的报告，不重新解析依赖、不运行 `ty` 或测试。

## 文档

- [工程文档索引与契约所有权](docs/README.md)
- [D001 — 产品与命令契约](docs/designs/D001-pf.md)
- [D002 — 实现结构](docs/designs/D002-pf-implementation.md)
- [D003 — 搜索算法](docs/designs/D003-pf-search-algorithm.md)
- [D004 — `ty` 增量静态证据](docs/designs/D004-pf-ty-enhancement.md)
- [P001 — v1 实施记录](plans/P001-pf-v1.md)
- [P002 — D004 实施记录](plans/P002-pf-ty-enhancement.md)

PF v1 与 `increment-v1` 静态证据策略均已实现。P001/P002 记录历史 TDD 和验证证据，不承担现行契约。

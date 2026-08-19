# PF — Package Floor

> 找出 Python 包在给定环境中经过验证的直接依赖下界。

PF 通过候选版本发现、相对最高版本诊断基线的 `ty` 增量静态检查和项目完整测试，为直接依赖生成可解释、可复现的精确下界。

## v1 承诺

PF 的搜索单位是一个可独立安装的包，以及一个确定的兼容性 cell：

```text
(精确目标平台, CPython minor, extra 兼容面)
```

在指定 index、候选策略和 `search-space` 内，PF 返回一个经过完整测试的坐标最小向量：固定其他直接依赖后，没有任何单个受管依赖还能继续降低。

该结果不是依赖笛卡尔积的全局最小值，也不证明未测试的中间版本或其他依赖组合兼容。v1 假设每个一维搜索切片只有一个连续通过区间；观察到非单调结果时会停止并禁止 apply。

v1 只发现下界，不搜索或生成不兼容上界。用户已有的上界和排除项保持不变。

## 搜索流程

`pf search` 对每个 cell 执行：

1. 在隔离环境按最高允许版本解析当前声明，得到 `V_hi`，并捕获该环境的 `ty` 诊断基线 `S_hi`。
2. 完整测试 `V_hi`；项目既有 typing 错误不构成 baseline 失败。
3. 使用相对 `S_hi` 的 `ty` 增量检查对候选做廉价静态坐标定界，得到猜测向量 `V_static`。
4. 对 `V_static` 运行一次完整测试；通过时走 fast path。
5. 若测试失败，从 baseline 开始运行完整测试驱动的坐标搜索，直到达到不动点。

`pf check` 同样先捕获 `S_hi`，再对 `lowest-direct` 解析向量做增量静态检查和完整测试。

每个 Proposal 都代表一个精确依赖向量。只有完整测试通过的向量才能成为最终证据。

## 命令

```text
pf check [package]       验证当前声明
pf search [package]      搜索并写入 package-floor.json
pf apply [package]       将完整、可表示的报告写回 pyproject.toml
pf minimize [package]    依次执行 search 和 apply
pf explain [package]     查看搜索证据
pf merge REPORT...       合并不同宿主平台生成的报告
```

开发环境可直接运行：

```text
uv run pf --help
uv run pf check
uv run pf search
uv run pf explain
uv run pf apply
```

项目至少需要静态 `project.dependencies`/`project.optional-dependencies`、一个
`test` dependency group（可为空）以及 `[tool.pf].test-command`。搜索只执行与
当前宿主精确匹配的 target；其余 cell 保留为不完整覆盖，供对应宿主运行后用
`pf merge ... --output ...` 合并。

`search` 不修改项目元数据。`apply` 不重新解析、不运行 `ty` 或测试；证据缺失、过期、非单调、不确定或无法安全表示时，它会保守失败。

## 安全边界

- 搜索只移动受管的直接 registry 依赖。
- path、workspace、Git、URL 和固定版本依赖参与评估，但不参与搜索。
- baseline 无法完整测试通过时立即停止；项目既有 `ty` 诊断保留为 `S_hi`，不单独构成失败。PF 不负责修复本来就失败的测试。
- 基础设施错误、超时和不可解析状态不会被当作版本不兼容。
- minor/major 粒度使用系列中当前最新的合格 patch 作为样本，但报告和 apply 始终保留实际验证的精确版本。
- 没有完整测试时，v1 不生成可 apply 的 floor。

## 后续方向

任意非单调空间搜索、上界发现、依赖交互、failure attribution、partial tests、static-only 模式、flaky 重试和跨运行缓存均不属于 v1，统一记录在 D001/D003 的“后续工作”。

## 项目状态

PF v1 已按以下设计实现。源码测试覆盖 CLI、项目/来源规划、隔离评估、搜索算法、
版本化报告、跨 cell 投影、merge、恢复日志和幂等 apply；详细 TDD 过程与验证证据
记录在 [P001](plans/P001-pf-v1.md)。

- [D001 — 产品契约](docs/designs/D001-pf.md)
- [D002 — 实现设计](docs/designs/D002-pf-implementation.md)
- [D003 — 搜索算法](docs/designs/D003-pf-search-algorithm.md)
- [D004 — ty 增量静态检查](docs/designs/D004-pf-ty-enhancement.md)

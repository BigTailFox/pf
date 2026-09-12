# R012 — PF 资格化待办

- **状态：** 开放
- **日期：** 2026-09-12
- **性质：** 非规范性 Review；跟踪资格与证据留存缺口，不扩大支持承诺、不授权实现或资格运行
- **基准：** `05dbf604812e37bafdf96bd6247a1665bc3e83c7`；本次静态核对，未重跑资格
- **来源：** [R010 §4](../archived/reviews/R010-pf-engineering-document-audit.md#4-r007-开放项交接)
- **owner：** [D004](../designs/D004-pf-ty-enhancement.md)、[D007](../designs/D007-pf-process-output.md)、[D012](../designs/D012-pf-harness-relaxation.md)、[测试规范](../../tests/README.md)

| Issue（2026-09-12 核对） | 状态 | 证据与去向 |
| --- | --- | --- |
| Q1 ty pin × Python minor × diagnostic case 矩阵 | 开放 | exact pin、synthetic tests 和局部真实工具运行不能证明完整矩阵；范围见 §1 |
| Q2 真实 Host 发布资格与支持范围 | 开放 | CI 仍为 Ubuntu × 3.10/3.11/3.12；E010 是有限 Windows dogfood，不是发布资格；见 §2 |
| Q3 targeted-runtime-contract 尚无独立 floor 实验 | 已解决 | E015 已执行 check 与两轮 complete search；只关闭“未执行实验”的缺口 |
| Q4 E015 完整 search 报告未留存 | 开放 | E015 §8 明确只剩摘要，不能离线复核完整报告；见 §3 |

## 1. ty 诊断矩阵

现有 exact pin 与 [ty adapter 测试](../../tests/test_ty_adapter.py) 不等于覆盖受支持 Python minor
及诊断种类的固定动态矩阵。工具协议资格、真实进程公开 seam 与产品 Host 路径按测试规范分别记录。
后续刷新 ty 凭据时，先明确 pin、Python、诊断类别、正常/异常退出和输出完整性范围，保存命令、
环境、原始输出与判定。以现行 D004 的 TyCheck/Unavailable 为准，不沿用 R007 的 runtime witness 项。
这里只记录尚未闭合的矩阵范围，不推断 ty 存在缺陷，也不新建资格标准。

## 2. 真实 Host 资格

[CI](../../.github/workflows/ci.yml) 当前只声明 Ubuntu × Python 3.10/3.11/3.12。
注入 Darwin/Windows Cell、PAL 分支测试、单机 process/e2e 或跨 OS coverage 并集，都不自动证明
真实 Host 发布资格。[E010](../experiments/E010-windows-native-search-cleanup.md) 提供原生 Windows
的进程/清理调查；其 dirty worktree search 在 baseline 被拒绝，未进入坐标搜索，不能作为发布 PASS。

关闭前需明确拟支持 Host 与资格范围，在真实宿主保存固定源码、工具/解释器版本、argv、产物、
退出结果及局限，逐项对照现行 owner。若需要新增支持承诺或改行为契约，先走 Design。
本待办不把“尚无完整资格”解释为“所有平台功能均失败”。

## 3. 自举 floor 与完整产物

[E015](../experiments/E015-pf-self-bootstrap-complete-search.md) 已保存 targeted-runtime-contract argv、
源码基准、Linux 三 Python minor 的 check 和两轮 complete search 记录，替代 R010 的“未找到实验”状态。
其 [§8](../experiments/E015-pf-self-bootstrap-complete-search.md#8-固定证据与局限) 明确根报告恢复后
只留摘要；摘要中的 generation/hash 不能重建完整报告，不足以重新执行 reader/apply 离线复核。

若需关闭 Q4，应找回与记录 hash 一致的完整报告，或在新固定输入上重跑并保存完整产物与资格边界。
后者是新证据，不能改写 E015 的运行事实。E015 的 apply 漂移退出不代表 floor 已写入产品声明。

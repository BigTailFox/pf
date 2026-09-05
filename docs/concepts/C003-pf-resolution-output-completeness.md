# C003 — PF 成功解析的日志完整性门槛

- **状态：** 开放构想；已核对当前分支，完整场景与放宽依据待验证
- **日期：** 2026-09-05
- **性质：** 非规范性 Concept，不定义新的解析准入规则，不授权生产实施
- **来源：** [D032 §9](../archived/designs/D032-pf-runtime-witness-stderr.md#9-待验证uv-成功解析的日志完整性门槛)
  的独立开放项；从工程索引抽出集中跟踪，D032/P037 已完成
- **相关 owner：** [D012](../designs/D012-pf-harness-relaxation.md)、
  [D007](../designs/D007-pf-process-output.md)、[D005](../designs/D005-pf-failure-and-diagnose.md)
- **本轮静态核对：** `6271752`；[UvAdapter](../../src/pf/adapters/uv.py) 的 resolve 路径

## 1. 构想与当前事实

评估 uv 成功解析的准入能否由正常 terminal 与完整可信的 native lock artifact 共同决定，
让仅用于诊断的 stdout/stderr 完整性不再成为成功解析的额外门槛。

当前实现仍在成功退出后先检查 stdout/stderr 完整性；任一流不完整，返回
`resolution-output-incomplete`，不会继续读取并校验 `pylock.toml`。本轮只静态确认了这条分支，
没有复现“正常退出、锁文件完整合法、只有诊断日志不完整”的完整 adapter 路径。

D007 的 stream complete 指磁盘 Process Log 正文是否完整保存，tail/cache 没覆盖全文不构成
incomplete。日志正文写入失败、磁盘耗尽或完成前中止才会使其不完整。因此不能预设日志缺失
一定与 lock artifact 或 terminal 的可靠性无关，也不能把当前拒绝直接认定为产品缺陷。

## 2. 需要调查与验证的内容

1. 通过公开 resolve seam 固定精确正常 terminal 和合法锁文件，仅改变 stdout/stderr 完整性，
   复现结果差异；查清实际产生 incomplete 的运行条件。
2. 分别确认 native lock 读取、digest、语法与图验证、上下文归属的证据来源，判断诊断日志缺失
   是否可能伴随文件写入或 terminal 不可靠。
3. 对正常退出但 lock 缺失、截断、非法或上下文不匹配的情况，说明独立 lock 校验能否可靠拒绝。
4. 确定哪些日志只用于诊断，哪些输出仍参与权威解析或分类；成功与失败路径分别评审。

调查过程可以形成 Investigation，受控运行的结果保存为 Experiment；它们提供事实依据，
本 Concept 保存待决问题及后续去向。

## 3. 进入 Design 的条件

完整路径复现成立，且证据分析足以说明独立的 terminal/lock 校验能维持现有 authority 后，
再决定是否另建 Design。若成立，由新 Design 明确 D012/D007 的准入、诊断和完整性边界，
接受后建立独立 Plan 与验收标准。

非零退出的 UNSAT classifier 继续要求完整诊断与已资格化 profile，不能随成功路径一起放宽。
该项不属于 D032/P037 的完成条件；若无法证明日志失败与权威证据可以分离，则保留现行门槛。

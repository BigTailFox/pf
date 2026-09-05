# P039 — Portable dependency marker 投影实施计划

- **状态：** 已完成，归档
- **日期：** 2026-09-06
- **依据：** [D034](../designs/D034-pf-dependency-marker-projection.md)，用户明确要求实现
- **起始工作区：** D034 未跟踪、docs/README.md 已修改；保留既有内容，不提交

## 1. 有序实施切片

| 切片 | Interface / ownership 与交付物 | D034 AC | 测试与证据槽 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | pf.markers：具名不可变 target facts、Cell-free PortableMarker.parse、evaluate、独立 contextual 求值、结构化错误 | 1、4 | test_markers 全矩阵、host independence、完整资格、错误/context/extra；module coverage 100% | 已实现 |
| S2 | Loader admission/用途独占，managed 与 self-reference 预资格、activation、provenance；harness contextual | 2、3、4 | test_marker_projection、test_project、test_validation_surfaces、test_harness | 已实现 |
| S3 | report/authorization 使用 ownership 选入口、完整 matrix 等价；terminal canonical 具名 selector | 4、5 | test_projection、test_marker_projection、test_authorization、test_report_schema | 已实现 |
| S4 | uv_lock 复用 target facts，保留 actual profile；固定 policy fact，重生成 examples | 6、7 | test_uv_lock/uv_adapter/environment、policy 隔离；Schema 未变、examples 已生成 | 已实现 |
| S5 | 最小真实 fixture smoke/check/search；MkDocs load 缺口复核；全门禁、owner 吸收、归档 | 7、8 | host active idna fixture 三命令成功；MkDocs marker 准入成功；全门禁与 owner 归档 | 完成 |

## 2. 实施约束与文档交付

只支持指定五字段；fixed/unmanaged 与 external harness 的非 portable 上下文行为明确保留，不能
作为 portable 失败 fallback。所有调用点一次替换，不保留 marker_platform/marker_applies 兼容别名。
异常由语义 owner 提供结构化信息，由消费者映射用途/定位及既有错误类别。公开测试与调用方穿过
同一 interface，不引入测试专用 port。PF CLI/测试从 repo root 在沙箱外运行；实验仅写明确的临时 fixture
或运行产物，不修改 MkDocs 独立配置。owner D001/D002/D012/D014 吸收后同步归档 D034/P039，更新
docs/README.md 与 archived/README.md。Schema 1 不扩形，examples 用现有 generator 重生成。

## 3. 行动、决策与偏差

- 2026-09-06：重新核对 D034 全文、当前调用点与 owner 规则，接受用户实施授权；先建立本 Plan。
- 当前证据确认：Loader 两处重复正则资格、project marker host 默认环境、uv_lock 重复平台别名、
  terminal `.values()` 隐含二元 selector；按 S1–S4 消除这些知识重复。
- S1–S4：替换所有旧 project marker 消费；资格使用 packaging AST 的 Variable 节点发现，封装在
  marker module 内；有界缓存不暴露 mutable parser。所有五字段覆盖，不引入额外 Cell/wire 事实。
- 检查发现原 ApplyAuthorizer 跳过纯 preserved group，无法发现其 contextual activation 与冻结 IDs
  不一致；按 D034 完整 group 要求改为所有 group 复证。测试覆盖混合/纯 preserved 和 force=False/True。
- 测试迁移修正旧 os_name/platform_system 拒绝断言、policy preimage 期望；首轮新增 fixture 的平台
  排序、Rich 换行断言、报告属性名与 ResolutionContext.cell 访问已修正，未放宽生产判定。
- D001/D002/D012/D014 已吸收稳定产品、interface、actual/contextual 时点与 policy/wire 规则。
- 最终 reader 用例发现通用报告错误 sanitizer 会把末尾非 ID 的变量名替换为 `<invalid-id>`；错误现以
  安全 declaration ID 收尾，保留 module 提供的 reason/变量。未放宽 sanitizer 或打印 wire 原文。
- 无产品范围偏差。MkDocs 独立 test-group 缺失仍按 D034 非目标保留，不修改其实验配置。

## 4. 验证命令与结果

以下记录精确命令、结果与失败归因。门禁：focused public-seam tests；Ruff、ty；Python
3.10–3.12 全套（禁用 testmon）；3.10 coverage ≥90%；uv build；generate_report_schema.py --check；
Markdown 相对链接与 git diff --check。静态验证不代替行为证据。

### 4.1 切片验证与 dogfood

- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_project.py tests/test_report.py tests/test_authorization.py tests/test_uv_lock.py tests/test_environment.py tests/test_uv_adapter.py tests/test_harness.py`：首次 386 passed / 2 failed；失败为旧错误文案与 policy preimage 期望，已迁移。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_markers.py tests/test_marker_projection.py tests/test_validation_surfaces.py tests/test_projection.py tests/test_authorization.py tests/test_environment.py`：204 passed（6.15s）。
- `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_environment.py tests/test_marker_projection.py tests/test_authorization.py tests/test_end_to_end.py`：127 passed（10.06s）。
- 最终 focused：`UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short tests/test_markers.py tests/test_marker_projection.py tests/test_project.py tests/test_validation_surfaces.py tests/test_projection.py tests/test_report.py tests/test_report_schema.py tests/test_authorization.py tests/test_harness.py tests/test_environment.py tests/test_uv_lock.py tests/test_uv_adapter.py tests/test_end_to_end.py`：672 passed（11.28s）。
- `test_active_platform_system_dependency_completes_smoke_check_search` 创建最小 uv_build package，
  `idna>=3.10,<=3.10; platform_system == '<host system>'` 确实是 active managed declaration。通过
  `sys.executable -m pf smoke/check/search` 各运行真实 CLI（timeout=120），三者 exit 0，逐 invocation
  process log 证明 verifier import/assert 已运行；search reader 返回 complete 与精确 floor 3.10，保留原 marker。
- MkDocs：repo-root `.venv/bin/python -B` 只读调用
  `ProjectLoader().load(root=Path('experiments/mkdocs').resolve())`，结果 `mkdocs` / 3 Cells，两个 colorama
  声明分别 managed/preserved，均保留 `platform_system == "Windows"`，不再在 marker admission 失败。
  初次辅助脚本误用 CliContext constructor 得到 TypeError，无产品执行；随后使用真实 subprocess CLI：

```sh
UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/python -B - <<'PY'
from pathlib import Path
import subprocess
import sys
result = subprocess.run(
    [sys.executable, '-m', 'pf', 'smoke'],
    cwd=Path('experiments/mkdocs').resolve(),
    capture_output=True, text=True, timeout=60,
)
print('exit', result.returncode)
print(result.stdout)
print(result.stderr)
PY
```

结果：run `20260905T204927.893061Z-1280796-8f59e486`，exit 3，
`test dependency group is required: test`。这是 load 成功后的独立完整 verifier contract 配置 gate；
未创建 snapshot/Attempt/resolver/verifier，不宣称 MkDocs tests PASS。不在 D034 内替用户选择测试依赖。

### 4.2 全门禁记录

初轮 Python 3.10 全套 1934 passed（44.45s），coverage 90.28%；Python 3.11 全套 1934 passed
（38.04s）。随后补充 harness 错误定位与 reader/terminal 用例，以下最终表以最新代码为准。

| 精确命令 | 最终结果 |
| --- | --- |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/pytest --no-testmon -q --tb=short --cov=pf --cov-report=term-missing tests` | Python 3.10.16：1937 passed，44.20s；coverage 90.35% |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.11 --group test pytest --no-testmon -q --tb=short tests` | 1937 passed，38.91s |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv run --locked --isolated --python 3.12 --group test pytest --no-testmon -q --tb=short tests` | 1937 passed，39.96s |
| `.venv/bin/ruff check src tests scripts` | 通过 |
| `.venv/bin/ty check` | 通过 |
| `.venv/bin/python scripts/generate_report_schema.py` / `--check` | 已重生成并通过；Schema 无 diff，examples 仅 digest/ref 变化 |
| `UV_CACHE_DIR=/tmp/pf-uv-cache .venv/bin/uv build` | wheel 与 sdist 成功 |
| Markdown 本地链接与 `git diff --check` | 归档后 95 个 Markdown、691 条本地链接、0 缺失；空白通过 |

链接检查的精确命令（只读；不检查远程链接和 heading anchors）：

```sh
perl -MFile::Find -MFile::Basename=dirname -MFile::Spec -e '
my @files = ("README.md", "README.zh.md", "CONTEXT.md");
find(sub { push @files, $File::Find::name if -f $_ && /\.md$/ }, "docs");
my ($links, $failed) = (0, 0);
for my $file (@files) {
  open my $fh, "<", $file or die $!; local $/; my $text = <$fh>;
  $text =~ s/```.*?```//sg;
  while ($text =~ /\[[^\]]*\]\(([^)]+)\)/g) {
    my $dest = $1; $dest =~ s/^<|>$//g; $dest =~ s/\s+"[^"]*"$//;
    next if $dest =~ /^(?:[a-z]+:|#)/i;
    $dest =~ s/#.*$//; $dest =~ s/%20/ /g;
    next unless length $dest;
    $links++; my $target = File::Spec->catfile(dirname($file), $dest);
    if (!-e $target) { print "$file -> $dest\n"; $failed++; }
  }
}
print scalar(@files), " Markdown files; $links local links; $failed missing targets\n";
exit($failed ? 1 : 0);
'
```

## 5. 最终逐项验收

| D034 AC | 必需证据 | 结论 |
| --- | --- | --- |
| 1 | test_markers：GNU/musl/Darwin/Windows × 两种 architecture，五字段 host 扰动、大小写/逻辑与字符串值；九个非 portable 变量拒绝；D001 明确 future scope | 通过 |
| 2 | test_marker_projection：base/optional active IDs、alias overlap、CandidateBuilder 坐标/anchor；test_project/test_candidates 现行回归 | 通过 |
| 3 | required surface 跨宿主一致；三用途×九字段 inactive 表达式预资格；三命令×三用途非法比较 exit 3、provenance、无 building snapshot/process；test_validation_surfaces 保留 specifier/source/dynamic 规则 | 通过 |
| 4 | public parse/evaluate/contextual/facts、immutability 与全错误；Loader 无变量表/AST/host knowledge；harness 公共 activation/provenance；report/authorization/terminal/uv_lock 均按具名字段迁移，旧 exported helpers 消失 | 通过 |
| 5 | POSIX 跨 Linux/Darwin、Windows architecture floor、原 marker 与 canonical selector；mixed/pure preserved contextual drift 与 force 阻止；test_projection/test_authorization 覆盖 scoped/libc/完整 multiset、reader/terminal | 通过 |
| 6 | policy_fact 参数用例覆盖 generation/merge/update_path 整体替换和 force apply 隔离；reader 资格错误保留安全 declaration ID；Schema 无 diff，examples generator --check 通过 | 通过 |
| 7 | 四 target 的 public EnvironmentFactory + native parser 活跃 graph/managed vector 一致，actual patch/implementation 独立；既有 uv graph/disposition 全套；真实 active marker smoke/check/search 均 exit 0；MkDocs 的独立配置限制见 §4.1 | 通过 |
| 8 | §4 全套三版本均 1937 passed、coverage 90.35%、Ruff/ty/build/生成物与归档文档检查；D001/D002/D012/D014 接管，D034/P039 同步归档 | 通过 |

# E017 本地补丁（用户授权：修复最高 baseline 后再搜索）

- **日期：** 2026-09-12
- **上游：** xarray `v2026.07.0` (`0238035a646a04a6d2b603cc5cee5cbefa304e23`)
- **不是** 对上游的修改建议，也不改 PF 契约
- **后续运行必须标明「含这些补丁的快照」**，不能与未打补丁的 tag 混用

## 1. `test_repr` category nbytes

- **文件：** `experiments/xarray/xarray/tests/test_dataset.py`
- **差分：** [test_repr-category-nbytes.diff](test_repr-category-nbytes.diff)
- **原因：** `TestDataset.test_repr` 用 `pandas >= 3.0.0dev0` 写死 category `36B`；`pandas==3.0.5` 的 Dataset repr 是 `32B`。
- **改动：** 用 `render_human_readable_nbytes(data["var4"].nbytes)` 填入期望串，与 `Dataset.repr` 同一格式化函数。去掉不再使用的 `Version` 导入。
- **边界：** 该测试不再把 category nbytes 当作独立兼容性信号。

## 2. `test_tree_index_rename` 缺少 scipy skip

- **文件：** `experiments/xarray/xarray/tests/test_nd_point_index.py`
- **差分：** [requires-scipy-tree-index-rename.diff](requires-scipy-tree-index-rename.diff)
- **原因：** 补丁后 smoke 在 `test_tree_index_rename` 因 `scipy` 缺失失败。同文件其余 `NDPointIndex` 用例已有 `@requires_scipy`；上游 `test-py311-bare-minimum` 不含 scipy。
- **改动：** 给该测试补上已有的 `@requires_scipy`。不把 scipy 加入 harness，也不删除测试。
- **边界：** 无 scipy 时该用例 skip，有 scipy 时仍执行。

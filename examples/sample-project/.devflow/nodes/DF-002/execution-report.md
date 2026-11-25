# DF-002 执行报告（v2 · CR-001 返工）

- 节点：账户与流水数据结构 + 原子存储
- 执行者：code-agent（演示执行）
- 开始时间：2026-11-25T14:10:00+08:00
- 结束时间：2026-11-25T14:14:30+08:00
- 结果：success
- v1 报告已归档：`history/execution-report.v1-20261125T1410.md`

## 1. 做了什么

1. `LedgerEntry` 新增 `frozen_delta: int = 0`，表示本条流水对 frozen 的影响（带符号）。
2. 校验规则从"amount 不能为 0"改为"amount 与 frozen_delta 不能同时为 0"。
3. `from_dict` 用 `data.get("frozen_delta", 0)` 读取，v1 历史流水不需要迁移。
4. **新字段追加在字段列表末尾**：v1 的调用方（`tests/test_queries.py`）用位置参数构造，
   插在中间会导致参数静默错位。
5. 新增 6 条测试，覆盖往返、纯冻结流水、双零拒绝、历史数据兼容、位置参数兼容、非零 frozen。

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| src/points/models.py | LedgerEntry 加 frozen_delta，放宽 amount 校验 | REQ-009 需要记录冻结变动 |
| tests/test_store.py | 新增 LedgerFrozenDeltaTest（6 条） | 覆盖新字段与兼容性 |

`store.py` 未改动：文件结构不变，新字段只是一个键。

## 3. 关键实现说明

- 字段顺序：`frozen_delta` 放在 `note` 之后。这是本次最容易踩的坑——`test_queries.py` 里有
  `LedgerEntry("e1", "u1", "checkin", 10, "c1", "2026-11-20T09:00:00", "")` 这种位置参数写法，
  新字段插在 `amount` 后面会让 `"c1"` 变成 `frozen_delta`，`_as_int` 直接抛 TypeError。
- 双零校验放在 `__post_init__`，与原有 `_as_int` 守卫一致。
- 没有写迁移脚本：`from_dict` 默认值已足够，且写回时会自动补上字段。

## 4. 测试执行

```bash
python -m unittest discover -s tests -v
```

- 退出码：0
- 全量 57 条通过（v1 原有 51 条 + 本节点新增 6 条）
- 输出摘要：`Ran 57 tests in 2.229s` / `OK`

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
| frozen_delta 默认 0，往返完整 | 是 | test_frozen_delta_roundtrip |
| amount=0 仅当 frozen_delta≠0 允许 | 是 | test_zero_amount_allowed_with_frozen_delta / test_both_zero_rejected |
| 能读 v1 历史流水 | 是 | test_legacy_row_without_frozen_delta |
| Account.frozen 非 0 时 available 正确 | 是 | test_account_with_frozen |
| 原子写与坏文件处理不变 | 是 | v1 的 5 条 StoreTest 仍通过 |

## 6. 假设与偏差

- 偏差：`frozen_delta` 放在字段末尾而非紧随 `amount`。原因见上，属于**兼容性优先**的取舍。

## 7. 遗留问题与风险

- 如果将来把字段插到中间，所有位置参数调用都会错位。已在代码注释里写明"新字段只能追加在末尾"。

## 8. 交付物

- [x] src/points/models.py
- [x] tests/test_store.py
- [x] 旧报告归档到 history/

## 9. 机器可读证据

```devflow-execution
{
  "node_id": "DF-002",
  "result": "success",
  "modified_files": [
    "src/points/models.py",
    "tests/test_store.py"
  ],
  "acceptance": [
    {"criterion": "LedgerEntry 新增 frozen_delta，默认 0，往返完整", "met": true, "evidence": "test_frozen_delta_roundtrip"},
    {"criterion": "amount=0 仅在 frozen_delta≠0 时允许", "met": true, "evidence": "test_zero_amount_allowed_with_frozen_delta, test_both_zero_rejected"},
    {"criterion": "from_dict 能读取 v1 历史流水", "met": true, "evidence": "test_legacy_row_without_frozen_delta"},
    {"criterion": "Account.frozen 非 0 时 available 正确", "met": true, "evidence": "test_account_with_frozen"},
    {"criterion": "原子写与坏文件处理保持不变", "met": true, "evidence": "v1 StoreTest 5 条仍通过"}
  ],
  "assumptions": ["frozen_delta 追加在字段末尾以保证位置参数兼容"],
  "leftovers": []
}
```

# DF-008 执行报告

- 节点：积分冻结 / 解冻服务（CR-001 新增）
- 执行者：code-agent（演示执行）
- 开始时间：2026-11-25T14:15:00+08:00
- 结束时间：2026-11-25T14:20:10+08:00
- 结果：success

## 1. 做了什么

1. 新增 `FreezeService`：`freeze` / `unfreeze` / `frozen_amount`。
2. 冻结只改 `frozen`，不动 `balance`；`available` 自动下降，兑换立即受约束。
3. 冻结/解冻各写一条流水：`amount=0`、`frozen_delta=±amount`。
4. 账户与流水一次落盘（同 `WalletService._apply` 的做法）。
5. 13 条测试：三值关系、可用不足无副作用、部分/全额解冻、超额解冻、单号幂等、两条对账、跨模块兑换约束。

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| src/points/freeze.py | 新建 | 冻结/解冻写路径 |
| tests/test_freeze.py | 新建 | 13 条用例 |

## 3. 关键实现说明

- `_validate()` 统一校验 amount 与 source_id，避免三个方法各写一遍。
- 解冻超额用 `InvalidArgument` 而不是 `InsufficientBalance`：这是调用方参数错误，不是余额不足。
- `frozen_delta` 在 `_entry()` 里显式传，因为 `amount=0` 时必须靠它通过"双零"校验。
- 没有改 `service.py`：`redeem` 已经用 `available`，跨模块用例验证了这一点确实成立。

## 4. 测试执行

```bash
python -m unittest discover -s tests -p "test_freeze.py" -v
python -m unittest discover -s tests -v
```

- 退出码：0 / 0
- 本节点 13 条通过；全量 70 条通过
- 输出摘要：`Ran 13 tests in 0.421s OK`；`Ran 70 tests in 2.378s OK`

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
| 冻结后 balance 不变、available 减少 | 是 | test_freeze_moves_available_to_frozen |
| 可用不足抛 InsufficientBalance 且无副作用 | 是 | test_freeze_insufficient_has_no_side_effect |
| 支持部分解冻，超过 frozen 拒绝 | 是 | test_partial_unfreeze / test_unfreeze_more_than_frozen_rejected |
| 同一 source_id 重复抛 DuplicateRequest | 是 | test_duplicate_freeze_rejected / test_duplicate_unfreeze_rejected |
| 流水 amount=0、frozen_delta=±amount | 是 | test_freeze_ledger_entry_shape |
| sum(frozen_delta) == frozen | 是 | test_reconciliation |

## 6. 假设与偏差

- 假设：冻结不设过期时间（CR-001 已确认本期不做）。
- 假设：解冻不需要审批流，运营直接调用。

## 7. 遗留问题与风险

- 冻结长期不处理会让积分永久不可用，依赖运营流程。已写入 DF-007 的文档待办。

## 8. 交付物

- [x] src/points/freeze.py
- [x] tests/test_freeze.py

## 9. 机器可读证据

```devflow-execution
{
  "node_id": "DF-008",
  "result": "success",
  "modified_files": [
    "src/points/freeze.py",
    "tests/test_freeze.py"
  ],
  "acceptance": [
    {"criterion": "freeze：balance 不变、frozen 增加、available 减少", "met": true, "evidence": "test_freeze_moves_available_to_frozen"},
    {"criterion": "freeze 可用不足抛 InsufficientBalance 且无副作用", "met": true, "evidence": "test_freeze_insufficient_has_no_side_effect"},
    {"criterion": "unfreeze 支持部分解冻，超过 frozen 抛 InvalidArgument", "met": true, "evidence": "test_partial_unfreeze, test_unfreeze_more_than_frozen_rejected"},
    {"criterion": "同一 source_id 重复冻结/解冻抛 DuplicateRequest", "met": true, "evidence": "test_duplicate_freeze_rejected, test_duplicate_unfreeze_rejected"},
    {"criterion": "冻结/解冻流水 amount=0、frozen_delta=±amount", "met": true, "evidence": "test_freeze_ledger_entry_shape"},
    {"criterion": "sum(frozen_delta) == frozen", "met": true, "evidence": "test_reconciliation"}
  ],
  "assumptions": ["冻结不设过期", "解冻无需审批流"],
  "leftovers": ["冻结长期不处理需要运营流程兜底"]
}
```

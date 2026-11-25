# DF-004 执行报告

- 节点：账户服务：开户 / 签到 / 奖励 / 扣减 / 余额
- 执行者：code-agent（演示执行，与 DF-005 同属并行组 A，本次由单个 Agent 顺序完成）
- 开始时间：2026-11-25T13:48:00+08:00
- 结束时间：2026-11-25T13:53:00+08:00
- 结果：success

## 1. 做了什么

1. `WalletService(store, clock)`：`clock` 可注入，签到日期与流水时间都走它。
2. `open_account` 幂等；`check_in` 按 `user_id + 自然日` 幂等；`reward_order` 按 `source_id` 幂等。
3. `redeem` 先校验余额再写入，抛 `InsufficientBalance` 前**没有任何副作用**。
4. `_apply()` 把"余额变更 + 流水追加"合成**一次写盘**，避免两次写之间崩溃导致对不上账。
5. 所有写方法整体包在 `store.lock()` 内（读 → 校验 → 改 → 写），为 DF-006 的并发验证打基础。

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| src/points/service.py | 新建 | 核心业务写路径 |
| tests/test_service.py | 新建 | 17 条用例 |

## 3. 关键实现说明

- 积分换算 `amount_cents // 100`：整数除法天然向下取整，避免 `round()` 的银行家舍入。
- `amount_cents` / `amount` 显式拒绝 `bool`（`True` 是 `int` 的子类，不拦会变成 1 分）。
- 流水 `amount` 带符号：入账为正、扣减为负，`sum(ledger.amount) == balance` 可直接对账。
- `_apply()` 没有复用 `AccountRepo.save + LedgerRepo.append`，因为那会产生两次写盘。这是**有意偏离**，已在代码注释说明。

## 4. 测试执行

```bash
python -m unittest discover -s tests -p "test_service.py" -v
python -m unittest discover -s tests -v
```

- 退出码：0 / 0
- 本节点 17 条全部通过；全量 47 条通过
- 输出摘要：`Ran 17 tests in 0.322s OK`；`Ran 47 tests in 1.650s OK`

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
| 重复开户幂等 | 是 | test_idempotent |
| 当天首次签到 +10，第二次不变 | 是 | test_first_check_in_credits_ten / test_same_day_second_check_in_does_nothing |
| 1999 分 → 19 分，同一单号只入账一次 | 是 | test_rounding_down / test_duplicate_order_not_credited_twice |
| 余额不足抛 InsufficientBalance 且无副作用 | 是 | test_insufficient_has_no_side_effect |
| balance 返回三值且 available = balance - frozen | 是 | test_shape_and_math |

## 6. 假设与偏差

- 假设：签到不自动开户，账户不存在时抛 `AccountNotFound`（需求未要求自动开户）。
- 偏差：`_apply` 直接操作 store 而非复用仓储写方法，理由见上。

## 7. 遗留问题与风险

- 并发正确性依赖"读-改-写全程持锁"，本节点只做了实现，未做并发验证（属于 DF-006）。
- `frozen` 恒为 0，`available` 的计算已按未来冻结能力预留。

## 8. 交付物

- [x] src/points/service.py
- [x] tests/test_service.py

## 9. 机器可读证据

```devflow-execution
{
  "node_id": "DF-004",
  "result": "success",
  "modified_files": [
    "src/points/service.py",
    "tests/test_service.py"
  ],
  "acceptance": [
    {"criterion": "同一 user_id 重复开户幂等", "met": true, "evidence": "test_idempotent"},
    {"criterion": "当天首次签到 +10，第二次签到不变且不新增流水", "met": true, "evidence": "test_first_check_in_credits_ten, test_same_day_second_check_in_does_nothing"},
    {"criterion": "订单奖励 1999 分 → 19 积分；同一 source_id 只入账一次", "met": true, "evidence": "test_rounding_down, test_duplicate_order_not_credited_twice"},
    {"criterion": "余额不足抛 InsufficientBalance 且余额与流水不变", "met": true, "evidence": "test_insufficient_has_no_side_effect"},
    {"criterion": "balance 返回 balance/frozen/available，available = balance - frozen", "met": true, "evidence": "test_shape_and_math"}
  ],
  "assumptions": ["签到不自动开户", "_apply 直接操作 store 以保证一次写盘"],
  "leftovers": ["并发正确性由 DF-006 验证"]
}
```

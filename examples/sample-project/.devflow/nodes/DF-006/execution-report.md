# DF-006 执行报告（v2 · CR-001 返工）

- 节点：端到端链路与并发验证
- 执行者：code-agent（演示执行）
- 开始时间：2026-11-25T14:21:00+08:00
- 结束时间：2026-11-25T14:25:40+08:00
- 结果：success
- v1 报告已归档：`history/execution-report.20261125T1348.md`

## 1. 做了什么

1. `test_integration.py` 新增 `FreezeFlowTest`（3 条）：
   - 冻结 8 → 兑换 22 被拒 → 兑换 21 成功 → 解冻 8 → 兑换 8 成功，最终三值归零；
   - 两条对账公式（`sum(amount)==balance`、`sum(frozen_delta)==frozen`）；
   - 流水里确实出现 freeze / unfreeze 各一条。
2. `test_concurrency.py` 新增 `ConcurrentFreezeTest`（1 条）：
   - 20 线程各冻结 1 分、可用 10 → 恰好 10 成功、10 次 `InsufficientBalance`；
   - `frozen == balance == 10`、`available == 0`，且任意时刻 `frozen <= balance`；
   - 流水 11 条，两条对账成立。
3. 原有 70 条测试全部保留并通过。

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| tests/test_integration.py | 新增 FreezeFlowTest | 冻结链路端到端 |
| tests/test_concurrency.py | 新增 ConcurrentFreezeTest | 并发不超冻 |

## 3. 关键实现说明

- 冻结后兑换的断言用的是 `available` 边界值（21 成功、22 失败），刚好卡在临界点上。
- 并发冻结用例同样用 `Barrier` 同步起跑；统计成功次数而不是只看最终余额，
  否则"锁失效但恰好没超冻"会被漏掉。
- 未修改 `src/`。测试一次通过，说明 DF-002 / DF-008 的实现是自洽的。

## 4. 测试执行

```bash
python -m unittest discover -s tests -v
```

- 退出码：0
- 全量 74 条通过（v1 的 70 条 + 本节点新增 4 条）
- 输出摘要：`Ran 74 tests in 2.786s` / `OK`

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
| 端到端含冻结/解冻，两条对账成立 | 是 | test_freeze_blocks_then_unfreeze_restores / test_two_reconciliations |
| 冻结后可用不足时兑换被拒 | 是 | test_freeze_blocks_then_unfreeze_restores |
| 20 线程各冻结 1 分恰好 10 次成功，frozen 不超 balance | 是 | test_no_over_freeze |
| 全量测试全绿 | 是 | Ran 74 tests / OK |

## 6. 假设与偏差

- 假设：冻结与扣减的并发竞争不在本期验证范围内（两者都受同一把锁保护，理论上等价）。
  如果后续要做，可以加一个"边冻结边兑换"的混合用例。

## 7. 遗留问题与风险

- 多进程部署下锁失效的问题依然存在（v1 已记录），本次不改变结论。

## 8. 交付物

- [x] tests/test_integration.py
- [x] tests/test_concurrency.py
- [x] 旧报告归档到 history/

## 9. 机器可读证据

```devflow-execution
{
  "node_id": "DF-006",
  "result": "success",
  "modified_files": [
    "tests/test_integration.py",
    "tests/test_concurrency.py"
  ],
  "acceptance": [
    {"criterion": "端到端链路含冻结/解冻，两条对账公式一致", "met": true, "evidence": "test_freeze_blocks_then_unfreeze_restores, test_two_reconciliations"},
    {"criterion": "冻结后可用额度不足时兑换被拒绝", "met": true, "evidence": "test_freeze_blocks_then_unfreeze_restores"},
    {"criterion": "20 线程各冻结 1 分恰好 10 次成功，frozen 不超过 balance", "met": true, "evidence": "test_no_over_freeze"},
    {"criterion": "全量测试全绿", "met": true, "evidence": "Ran 74 tests in 2.786s, OK"}
  ],
  "assumptions": ["冻结与兑换的混合竞争未单独验证"],
  "leftovers": ["多进程部署锁失效问题仍然存在"]
}
```

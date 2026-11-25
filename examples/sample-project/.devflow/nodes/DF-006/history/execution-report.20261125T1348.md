# DF-006 执行报告

- 节点：端到端链路与并发验证
- 执行者：code-agent（演示执行）
- 开始时间：2026-11-25T13:54:00+08:00
- 结束时间：2026-11-25T13:56:40+08:00
- 结果：success

## 1. 做了什么

1. `test_integration.py`：真实存储跑完整链路（开户 → 签到 → 1999 分订单奖励 → 兑换 5 分 → 查流水 → 查余额），并做余额/流水对账。
2. `test_integration.py`：重复调用签到与订单奖励，验证幂等（余额不涨、流水不增）。
3. `test_concurrency.py`：20 线程 + `Barrier` 同时扣 1 分，断言恰好 10 次成功、10 次 `InsufficientBalance`。
4. `test_concurrency.py`：余额为 0 时并发扣减，断言全部失败且余额保持 0。

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| tests/test_integration.py | 新建 | 端到端 + 幂等 |
| tests/test_concurrency.py | 新建 | 并发不超扣 |

## 3. 关键实现说明

- 并发用例用 `threading.Barrier(20)` 保证同时起跑，避免"其实串行执行也通过"的假阳性。
- 每个线程用不同 `source_id`，避免撞上 `DuplicateRequest` 干扰统计。
- 断言"任何时刻余额都不为负"用的是成功分支里读到的 `balance` 快照。
- 未修改 `src/`。测试一次通过，未发现实现缺陷。

## 4. 测试执行

```bash
python -m unittest discover -s tests -v
```

- 退出码：0
- 全量 51 条通过（本节点新增 4 条）
- 输出摘要：`Ran 51 tests in 1.903s` / `OK`

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
| 端到端链路跑通，余额与流水一致 | 是 | test_full_chain |
| 20 线程各扣 1 分、余额 10 → 恰好 10 次成功、余额 0 | 是 | test_no_overspend |
| 全量测试全绿 | 是 | Ran 51 tests / OK |

## 6. 假设与偏差

- 假设：并发保护是进程内锁，多进程/多实例部署不在本期范围（已在 DF-007 的部署文档写明）。

## 7. 遗留问题与风险

- 并发用例在单进程内验证。多进程部署时 `threading.RLock` 无效，需要文件锁或数据库——这是**已知限制**，不是缺陷。

## 8. 交付物

- [x] tests/test_integration.py
- [x] tests/test_concurrency.py

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
    {"criterion": "端到端链路一条用例跑通，余额与流水累计值一致", "met": true, "evidence": "test_full_chain"},
    {"criterion": "20 线程各扣 1 分、余额 10 分 → 恰好 10 次成功、余额 0、无负余额", "met": true, "evidence": "test_no_overspend"},
    {"criterion": "全量测试全绿", "met": true, "evidence": "Ran 51 tests in 1.903s, OK"}
  ],
  "assumptions": ["并发保护限于单进程"],
  "leftovers": ["多实例部署需要换锁实现，已记入部署文档"]
}
```

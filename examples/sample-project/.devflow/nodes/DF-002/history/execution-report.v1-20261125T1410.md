# DF-002 执行报告

- 节点：账户与流水数据结构 + 原子存储
- 执行者：code-agent（演示执行）
- 开始时间：2026-11-25T13:42:00+08:00
- 结束时间：2026-11-25T13:44:10+08:00
- 结果：success

## 1. 做了什么

1. `models.py` 定义 `Account` 与 `LedgerEntry`，字段名与 JSON 一致，提供 `to_dict` / `from_dict`。
2. 加 `_as_int()` 守卫：拒绝 `float` 与 `bool`，避免 `1.5` 被静默截断。
3. `Account.available` 作为只读 property，`frozen` 本期恒 0。
4. `store.py` 实现 `JsonStore`：进程内 `RLock` + `read` / `write`。
5. 写入路径：`<file>.tmp` → `flush` + `os.fsync` → `os.replace`，中断不会留下半截文件。
6. 文件不存在返回空结构；JSON 损坏抛 `StorageError`，**不静默清空**（数据安全优先）。

## 2. 修改文件

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| src/points/models.py | 新建 | 数据结构与序列化 |
| src/points/store.py | 新建 | 原子存储 + 锁 |
| tests/test_store.py | 新建 | 10 条用例覆盖结构与存储 |

## 3. 关键实现说明

- `StorageError` 定义在 `store.py` 内并继承 `PointsError`：`errors.py` 属于 DF-001 的范围，本次不改。
- `read()` 返回的是新对象（每次都 `json.loads`），调用方改了不会影响"缓存"——本期没有缓存。
- `JsonStore.lock()` 暴露给业务层，是为了让 DF-004 能把"读-校验-写"整体放进同一把锁。

## 4. 测试执行

```bash
python -m unittest discover -s tests -v
```

- 退出码：0
- 本节点新增 10 条，全量 15 条全部通过
- 输出摘要：`Ran 15 tests in 0.066s` / `OK`

## 5. 验收标准对照

| 验收标准 | 是否满足 | 证据 |
| --- | --- | --- |
| Account 字段齐全且积分为 int | 是 | test_account_available / test_account_rejects_float |
| LedgerEntry 字段齐全、amount 带符号 | 是 | test_ledger_amount_signed / test_ledger_rejects_zero |
| 原子写、文件不存在返回空结构 | 是 | test_write_creates_parent_and_roundtrips / test_read_missing_file_returns_empty |
| 落盘文件可被 json.load 解析 | 是 | test_written_file_is_valid_json |

## 6. 假设与偏差

- 假设：数据量在单文件可承受范围内（本期 < 1 万条流水），不做分片。
- 偏差：`StorageError` 放在 `store.py` 而非 `errors.py`，原因见上。

## 7. 遗留问题与风险

- 单文件全量读写，流水到十万级会变慢。已记入 `docs/DEPLOY.md` 的迁移说明，本期不优化。

## 8. 交付物

- [x] src/points/models.py
- [x] src/points/store.py
- [x] tests/test_store.py

## 9. 机器可读证据

```devflow-execution
{
  "node_id": "DF-002",
  "result": "success",
  "modified_files": [
    "src/points/models.py",
    "src/points/store.py",
    "tests/test_store.py"
  ],
  "acceptance": [
    {"criterion": "Account 含 user_id/balance/frozen/version/created_at，积分为 int", "met": true, "evidence": "test_account_available, test_account_rejects_float"},
    {"criterion": "LedgerEntry 字段齐全，amount 为带符号整数", "met": true, "evidence": "test_ledger_amount_signed"},
    {"criterion": "写入使用临时文件 + os.replace，文件不存在返回空结构", "met": true, "evidence": "test_no_tmp_file_left, test_read_missing_file_returns_empty"},
    {"criterion": "落盘文件可被 json.load 解析", "met": true, "evidence": "test_written_file_is_valid_json"}
  ],
  "assumptions": ["单文件存储，本期数据量小于 1 万条", "StorageError 定义在 store.py"],
  "leftovers": ["十万级流水需要换存储，已记入部署文档"]
}
```

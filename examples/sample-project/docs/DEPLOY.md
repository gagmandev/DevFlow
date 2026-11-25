# 部署与配置

## 1. 环境

- Python 3.11 及以上
- 无第三方依赖
- 磁盘：数据文件所在目录需要可写

## 2. 数据文件

默认路径由调用方传入，示例用 `data/points.json`：

```python
from points.store import JsonStore
store = JsonStore("data/points.json")
```

- 文件不存在时自动视为空数据，首次写入时创建。
- 写入是原子的：先写 `points.json.tmp`，`fsync` 后 `os.replace`。
- 目录不存在会自动创建。

文件结构：

```json
{
  "accounts": {"u1": {"user_id": "u1", "balance": 24, "frozen": 8, "version": 6, "created_at": "..."}},
  "ledger": [
    {"entry_id": "...", "user_id": "u1", "type": "checkin", "amount": 10, "frozen_delta": 0, "source_id": "checkin:2026-11-20", "created_at": "...", "note": "每日签到"},
    {"entry_id": "...", "user_id": "u1", "type": "freeze", "amount": 0, "frozen_delta": 8, "source_id": "refund-1", "created_at": "...", "note": "冻结 refund-1"}
  ]
}
```

- `amount`：对 `balance` 的影响，带符号。
- `frozen_delta`：对 `frozen` 的影响，带符号。普通流水为 0；纯冻结流水 `amount=0`。
- v1 的历史流水没有 `frozen_delta` 字段，读取时按 0 处理，**不需要迁移脚本**。

## 2.1 对账

两条公式必须同时成立：

```python
account = wallet.balance("u1")
ledger = store.read()["ledger"]
assert sum(e["amount"] for e in ledger) == account["balance"]
assert sum(e["frozen_delta"] for e in ledger) == account["frozen"]
```

对不上时先看是不是有人绕过服务层直接改了 JSON 文件。

## 3. 部署形态

**只支持单进程部署。**

并发保护用的是 `threading.RLock`（进程内），多个进程同时写同一个数据文件会互相覆盖。
如果要多实例部署，必须先把锁换成文件锁或数据库行锁——`store.py` 是唯一需要改的地方，
`service.py` 的业务逻辑不用动。

## 4. 备份与恢复

```bash
# 备份
cp data/points.json backup/points-$(date +%Y%m%d-%H%M).json

# 恢复：先停写入，再覆盖
cp backup/points-20261125-1400.json data/points.json
```

恢复前务必停止所有写入进程，否则会和原子写抢同一个文件。

## 5. 升级到数据库

要改的只有两处：

1. `store.py`：`read()` / `write()` 换成 SQL 实现；
2. `repository.py`：把 JSON 结构的读写换成 SQL 语句。

`service.py` / `queries.py` 依赖的是仓储接口，不用改。

注意：换成数据库后，`service._apply()` 里的"余额 + 流水一次写盘"要改成同一个事务。

## 6. 配置项

| 配置 | 说明 | 是否必填 | 默认值 |
| --- | --- | --- | --- |
| 数据文件路径 | 传给 `JsonStore` | 是 | 无 |
| 签到积分 | `service.CHECKIN_POINTS` | 否 | 10 |
| 元/积分换算 | `service.CENTS_PER_POINT` | 否 | 100 |

> 不要在代码或配置文件里写密钥、令牌。本服务目前不需要任何凭据。

## 7. 健康检查

本服务是库形态，没有监听端口。调用方可用一次读操作探活：

```python
JsonStore("data/points.json").read()
```

抛 `StorageError` 说明数据文件损坏或不可读，需要人工介入（不要自动清空）。

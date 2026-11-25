# 积分账户服务（points）

会员体系的积分底座：开户、签到、订单奖励、兑换扣减、流水查询。
纯 Python 标准库实现，数据落在本地 JSON 文件里。

## 本期范围

**做**：账户、余额、积分流水、入账/扣减/查询、并发不超扣、退款积分冻结/解冻。

**不做**：积分商城页面、支付、HTTP 接口、运营后台 UI、积分有效期、冻结自动过期。

## 目录

```
src/points/
├── __init__.py      包入口，版本号
├── errors.py        领域异常（统一 code）
├── models.py        Account / LedgerEntry 数据结构
├── store.py         JSON 原子存储 + 进程内锁
├── repository.py    账户仓储 / 流水仓储
├── service.py       核心业务：开户、签到、奖励、扣减、余额
├── freeze.py        冻结 / 解冻（退款场景）
└── queries.py       流水查询（用户视图 / 运营视图）
tests/               unittest 用例（74 条）
```

## 环境

- Python 3.11+
- 无第三方依赖，不需要 `pip install`

## 运行测试

```bash
python -m unittest discover -s tests -v
```

## 用起来

```python
import sys
sys.path.insert(0, "src")

from points.service import WalletService
from points.store import JsonStore
from points.queries import LedgerQuery
from points.freeze import FreezeService

store = JsonStore("data/points.json")
wallet = WalletService(store)
freezer = FreezeService(store)
query = LedgerQuery(store)

wallet.open_account("u1")            # 开户（幂等）
wallet.check_in("u1")                # 签到 +10
wallet.reward_order("u1", 1999, "order-1")   # 19.99 元 → 19 分
wallet.redeem("u1", 5, "redeem-1")   # 兑换扣 5 分

print(wallet.balance("u1"))          # {'balance': 24, 'frozen': 0, 'available': 24, ...}

# 退款：冻结 8 分，余额不变，可用减少
freezer.freeze("u1", 8, "refund-1")
print(wallet.balance("u1"))          # {'balance': 24, 'frozen': 8, 'available': 16, ...}

freezer.unfreeze("u1", 8, "unfreeze-1")      # 运营确认后解冻
print(query.by_user("u1")["total"])  # 5（含 freeze / unfreeze 流水）
```

## 业务规则速查

| 规则 | 说明 |
| --- | --- |
| 1 元 = 1 积分 | 按实付金额（分）整除 100，向下取整 |
| 签到 | 每自然日首次 +10，当天重复签到不重复入账 |
| 订单奖励 | 同一订单号只入账一次 |
| 兑换 | 可用积分不足直接拒绝，无副作用 |
| 冻结 | 不改变 balance，只把额度挪到 frozen；冻结期间不可用 |
| 解冻 | 支持部分解冻；解冻金额不能超过已冻结 |
| 余额 | `available = balance - frozen`，不允许为负 |
| 对账 | `sum(amount) == balance`、`sum(frozen_delta) == frozen` |
| 积分类型 | 全程 `int`，不出现 float |

## 已知限制

- **并发保护是进程内锁**，只适用于单进程部署。多实例部署会失效，需要换文件锁或数据库。
- 数据全量加载到内存，十万级流水会明显变慢。
- 运营查询没有鉴权，调用方负责。
- **冻结没有过期机制**：长期不解冻会让这部分积分永久不可用，靠运营流程兜底。

## 变更记录

- `CR-001`：退款场景新增积分冻结/解冻（需求 v1 → v2）。见 `.devflow/changes/CR-001/`。

## 更多

- 部署与配置：[`docs/DEPLOY.md`](docs/DEPLOY.md)
- 需求基准：`.devflow/requirements/confirmed-requirements-v1.md`
- 变更记录：`.devflow/changes/`

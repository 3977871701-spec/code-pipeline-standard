# 高可用订单服务（Standard）

## 项目简介

这是一个高可用的内存订单服务，属于代码流水线（Code Pipeline）的 **Standard 难度** 级别。服务提供订单的创建（Create）、查询（Get）、取消（Cancel）、列表（List）四大核心能力，同时满足以下非功能性要求：

- **幂等性**：同一 `order_id` 的重复创建/取消请求只生效一次，参数一致时返回已有结果，参数不一致时抛出冲突异常
- **线程安全**：使用 `threading.RLock` 保护所有仓储访问，多线程并发调用不会破坏数据一致性
- **完整错误处理**：结构化异常层级，每种异常携带错误码（`code`），参数非法、状态非法、并发冲突均有明确异常类型
- **不可变实体**：`Order` 使用 `frozen=True` dataclass，状态变更通过 `dataclasses.replace` 创建新对象

测试报告显示：41 个测试用例全部通过，行覆盖率 96.2%，并发测试连续 5 次无 flaky。

## 功能特性

### 订单管理
- **创建订单** `create_order`：创建新订单（状态 PENDING），支持幂等重试
- **查询订单** `get_order`：按 `order_id` 查询订单详情
- **取消订单** `cancel_order`：将订单状态设为 CANCELLED（幂等，重复取消不报错）
- **订单列表** `list_orders`：按状态过滤，按创建时间升序排列

### 幂等设计
- **创建幂等**：同 `order_id` 同参数 → 返回已有订单（`create_hits += 1`）；同 `order_id` 不同参数 → 抛 `OrderDuplicateError`
- **取消幂等**：对已取消订单重复取消 → 直接返回当前订单（`cancel_hits += 1`）
- **浮点容差**：金额比较使用 `1e-9` 容差

### 状态机

```
PENDING ──cancel──▶ CANCELLED
PENDING ──confirm─▶ CONFIRMED（本版本未开放）
CONFIRMED ──cancel─▶ CANCELLED
CANCELLED ──✗──▶ （终态，禁止任何变更）
```

### 可观测性
- **调用统计** `stats()`：返回 `create_calls`、`create_hits`、`cancel_calls`、`cancel_hits` 四个计数器的防御性副本
- **时钟注入**：构造函数支持注入 `clock` 函数，便于测试时间相关行为

### 异常体系

| 异常类 | 错误码 | 触发场景 |
|--------|--------|----------|
| `OrderValidationError` | `EMPTY_ORDER_ID` | `order_id` 为空 |
| `OrderValidationError` | `INVALID_ORDER_ID` | `order_id` 含非法字符或超长 |
| `OrderValidationError` | `INVALID_ARG` | `user_id` 为空 / items 格式错误 |
| `OrderValidationError` | `EMPTY_ITEMS` | `items` 为空 |
| `OrderValidationError` | `NEGATIVE_AMOUNT` | `amount` 为负数/NaN/Inf |
| `OrderNotFoundError` | `NOT_FOUND` | 查询/取消不存在的订单 |
| `OrderDuplicateError` | `DUPLICATE_ID_PARAM_MISMATCH` | 同 id 不同参数重复创建 |

## 技术栈

| 技术 | 说明 |
|------|------|
| **语言** | Python 3.11+ |
| **依赖** | 无第三方依赖，仅使用标准库 |
| **并发原语** | `threading.RLock`（可重入锁） |
| **数据结构** | `dataclasses`（frozen=True）、`enum.Enum` |
| **测试框架** | `unittest` + `threading.Barrier` |
| **测试规模** | 41 个用例（单测 + 并发），行覆盖率 96.2% |

### 项目结构

```
standard/
├── requirement.md              # 需求文档
├── SPEC.md                     # 详细技术规格说明（332 行）
├── dev-plan.md                 # 开发计划
├── order_service.py            # 业务实现（285 行）
├── test_order_service.py       # 单元 + 并发测试（719 行）
├── test_report.md              # 测试报告
├── test_report.html            # 测试报告（HTML 版）
├── parser.py                   # CSV 解析模块（附赠）
├── test_parser.py              # CSV 解析测试（附赠）
├── stats.py                    # 数值统计模块（附赠）
├── pivot.py                    # 数据透视引擎（附赠）
└── README.md                   # 本文件
```

> 注：`parser.py`、`stats.py`、`pivot.py` 及其测试为同一 Code Pipeline 标准难度的另一道题（CSV 数据透视工具），与订单服务并列存放。

## 使用方法

### 运行测试

```bash
cd /Users/xylei/code-pipeline-projects/standard

# 运行订单服务测试（41 个用例）
python3 -m unittest test_order_service.py -v

# 运行 CSV 解析测试
python3 -m pytest test_parser.py -v
```

### 作为模块调用

```python
from order_service import OrderService, OrderStatus, OrderDuplicateError, OrderNotFoundError

# 创建服务实例
svc = OrderService()

# 创建订单
order = svc.create_order(
    order_id="ORD-001",
    user_id="user-1",
    items=[("sku-1", 2, 9.99)],
    amount=19.98,
)
print(order.status)  # OrderStatus.PENDING

# 查询订单
fetched = svc.get_order("ORD-001")

# 取消订单
cancelled = svc.cancel_order("ORD-001")
print(cancelled.status)  # OrderStatus.CANCELLED

# 幂等：重复取消不报错
cancelled_again = svc.cancel_order("ORD-001")

# 查看统计
print(svc.stats())
# {'create_calls': 1, 'create_hits': 0, 'cancel_calls': 1, 'cancel_hits': 1}

# 列表查询
orders = svc.list_orders(status=OrderStatus.CANCELLED)
```

### CSV 透视工具使用

```bash
cd /Users/xylei/code-pipeline-projects/standard
python3 pivot.py data.csv --index Region --columns Product --values Sales --agg sum
```

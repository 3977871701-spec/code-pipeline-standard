# SPEC：高可用订单服务（standard）

> 本规范为 **dev** 角色唯一执行入口，描述文件、模块、接口、行为、错误与并发契约，dev 拿到后无需追问。

---

## 1. 交付物

| 文件 | 角色 | 预估行数 |
|------|------|----------|
| `order_service.py` | 业务实现 | 110-130 |
| `test_order_service.py` | 单元 + 并发测试 | 70-90 |

不引入第三方依赖；仅使用 Python 标准库 + `pytest`。

---

## 2. 数据模型

### 2.1 `OrderStatus` (enum.Enum)

```python
class OrderStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
```

### 2.2 `Order` (dataclass, frozen=True)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `order_id` | `str` | 非空、长度 ≤ 64、仅 `[A-Za-z0-9_-]` | 业务主键 |
| `user_id` | `str` | 非空 | 用户标识 |
| `items` | `Tuple[Tuple[str, int, float], ...]` | 至少 1 项 | `(sku, qty, price)` 三元组 |
| `amount` | `float` | ≥ 0 | 订单总金额 |
| `status` | `OrderStatus` | — | 当前状态 |
| `created_at` | `float` | — | `time.time()` 秒级时间戳 |
| `updated_at` | `float` | — | 状态变更时间戳 |
| `version` | `int` | 初始 1，每次状态转移 +1 | 乐观锁版本号 |

`frozen=True`：状态变更通过 `dataclasses.replace(order, status=..., updated_at=..., version=...)` 整体替换实现。

---

## 3. 异常体系

所有异常继承自基类 `OrderError`，基类定义：

```python
class OrderError(Exception):
    code: str = "ORDER_ERROR"
    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
```

| 异常类 | 错误码 | 触发场景 |
|--------|--------|----------|
| `OrderValidationError` | `INVALID_ARG` / `EMPTY_ORDER_ID` / `INVALID_ORDER_ID` / `EMPTY_ITEMS` / `NEGATIVE_AMOUNT` | 参数校验失败 |
| `OrderNotFoundError` | `NOT_FOUND` | `order_id` 不存在 |
| `OrderStateError` | `INVALID_STATE` | 状态机非法转移 |
| `OrderDuplicateError` | `DUPLICATE_ID_PARAM_MISMATCH` | 同 `order_id` 已存在但参数不一致 |

`code` 通过 `self.code` 访问，`message` 通过 `str(exc)` 或 `exc.message` 访问。

---

## 4. 服务接口

### 4.1 `class OrderService`

```python
class OrderService:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None
```

构造函数：可选注入时钟（便于测试时间相关行为），默认 `time.time`。

#### 4.1.1 `create_order`

```python
def create_order(
    self,
    *,
    order_id: str,
    user_id: str,
    items: Sequence[Tuple[str, int, float]],
    amount: float,
) -> Order
```

**参数约束**：
- `order_id`：非空字符串，匹配正则 `^[A-Za-z0-9_-]{1,64}$`。
- `user_id`：非空字符串。
- `items`：非空序列，每个元素是 `(sku, qty, price)`，且 `qty > 0`、`price ≥ 0`。
- `amount`：≥ 0 的有限浮点数。

**行为**：
1. 校验参数，失败抛 `OrderValidationError`。
2. 加锁后：
   - 若 `order_id` 已存在：
     - 比对 `(user_id, items, amount)` 是否与已存在订单完全一致。
     - **一致** → 计数器 `create_hits += 1`，返回已存在 `Order`（**幂等命中**）。
     - **不一致** → 抛 `OrderDuplicateError`。
   - 若不存在 → 新建 `Order(status=PENDING, version=1, created_at=now, updated_at=now)`，写入仓储，计数器 `create_calls += 1`，返回新订单。
3. 解锁，返回。

**返回**：`Order` 实例（`status == PENDING`，首次创建时；幂等命中时为当前态）。

**可能抛出的异常**：`OrderValidationError`、`OrderDuplicateError`。

#### 4.1.2 `get_order`

```python
def get_order(self, order_id: str) -> Order
```

**行为**：加锁后查询。存在则返回 `Order`，不存在抛 `OrderNotFoundError`。

**异常**：`OrderNotFoundError`。

#### 4.1.3 `cancel_order`

```python
def cancel_order(self, order_id: str) -> Order
```

**行为**：
1. 加锁后查询。
2. 不存在 → 抛 `OrderNotFoundError`。
3. 存在且 `status == CANCELLED` → 计数器 `cancel_hits += 1`，**直接返回当前订单**（**幂等**）。
4. 存在且 `status in (PENDING, CONFIRMED)` → 更新为 `CANCELLED`，`version += 1`，`updated_at = now`，计数器 `cancel_calls += 1`，返回新 `Order`。
5. （无可达其他分支，`status` 终态仅 `CANCELLED`）。

**返回**：取消后的 `Order`（`status == CANCELLED`）。

**异常**：`OrderNotFoundError`。

#### 4.1.4 `list_orders`

```python
def list_orders(
    self,
    *,
    status: OrderStatus | None = None,
) -> List[Order]
```

**行为**：加锁后返回 `status` 过滤后的订单列表（无过滤返回全量）。按 `created_at` 升序。

**返回**：`List[Order]`，可能为空。

**异常**：无（空过滤参数合法）。

#### 4.1.5 `stats`

```python
def stats(self) -> Dict[str, int]
```

**返回**：`{"create_calls": int, "create_hits": int, "cancel_calls": int, "cancel_hits": int}` 的**副本**。

**语义**：
- `create_calls`：实际新建订单的次数。
- `create_hits`：幂等命中（参数一致复用）的次数。
- `cancel_calls`：实际执行取消的次数。
- `cancel_hits`：对已取消订单重复取消的次数。

---

## 5. 仓储层契约（仅 `OrderService` 内部使用）

`OrderService` 内部维护 `self._orders: Dict[str, Order]`，所有读写均在 `self._lock: threading.RLock` 内进行。

**为什么用 `RLock` 而非 `Lock`**：`create_order` 命中已有订单后，需调用 `get_order` 拿回最新对象；用 `RLock` 允许同一线程重入，避免自死锁。

**锁范围**：所有公开方法整段加锁；不使用 `with self._lock:` 之外的临界区访问 `_orders`。

---

## 6. 幂等设计详细规则

| 场景 | 首次行为 | 重复行为 |
|------|----------|----------|
| `create_order` 同 `order_id` 同参数 | 新建 PENDING | 返回已存在订单，`create_hits += 1` |
| `create_order` 同 `order_id` 不同参数 | 新建 PENDING | 抛 `OrderDuplicateError("DUPLICATE_ID_PARAM_MISMATCH")` |
| `cancel_order` 对 PENDING | → CANCELLED | 命中已取消，幂等返回，`cancel_hits += 1` |
| `cancel_order` 对 CANCELLED | → CANCELLED | 幂等返回，`cancel_hits += 1` |
| `cancel_order` 对 CONFIRMED | → CANCELLED | 命中已取消，幂等返回，`cancel_hits += 1` |
| `get_order` 不存在 | 抛 `OrderNotFoundError` | 抛 `OrderNotFoundError`（非幂等场景） |

**比较函数（私有）**：

```python
def _same_payload(self, a: Order, user_id: str, items: Sequence, amount: float) -> bool:
    return (
        a.user_id == user_id
        and tuple(a.items) == tuple((s, int(q), float(p)) for s, q, p in items)
        and abs(a.amount - amount) < 1e-9
    )
```

浮点金额使用 `1e-9` 容差比较。

---

## 7. 线程安全方案

1. **互斥原语**：`threading.RLock`，字段名 `_lock`。
2. **加锁位置**：`create_order`、`get_order`、`cancel_order`、`list_orders`、`stats`（`stats` 读取 `_stats` 字典时也需短暂加锁）。
3. **不可变对象**：`Order` 为 `frozen=True` dataclass，状态变更通过 `replace` 创建新对象，外部调用方拿到的是不可变引用，杜绝读改写竞态。
4. **不变量**：`self._orders[order_id].order_id == order_id`；`status == CANCELLED ⇒ version ≥ 1`。
5. **禁止**：
   - 直接暴露 `self._orders` 或 `self._stats` 引用给外部（必须返回副本/新对象）。
   - 在锁内调用可能阻塞的外部 I/O（本服务为纯内存，不涉及）。

---

## 8. 错误处理规则

| 输入 | 结果 |
|------|------|
| `order_id=""` | `OrderValidationError("EMPTY_ORDER_ID", "order_id must not be empty")` |
| `order_id="a b"` | `OrderValidationError("INVALID_ORDER_ID", "order_id contains invalid characters")` |
| `order_id` 长度 > 64 | `OrderValidationError("INVALID_ORDER_ID", "order_id too long")` |
| `user_id=""` | `OrderValidationError("INVALID_ARG", "user_id must not be empty")` |
| `items=[]` | `OrderValidationError("EMPTY_ITEMS", "items must not be empty")` |
| `items` 中存在 `qty <= 0` 或 `price < 0` | `OrderValidationError("INVALID_ARG", "item qty/price out of range")` |
| `amount < 0` 或 `math.isnan(amount)` | `OrderValidationError("NEGATIVE_AMOUNT", ...)` |
| `get_order("unknown")` | `OrderNotFoundError("NOT_FOUND", "order not found: unknown")` |
| `cancel_order("unknown")` | `OrderNotFoundError("NOT_FOUND", ...)` |
| `create_order` 同 id 不同参数 | `OrderDuplicateError("DUPLICATE_ID_PARAM_MISMATCH", ...)` |

**异常消息格式**：`f"{人类可读描述}: {具体值}"`，便于日志追踪。

---

## 9. 测试规范

### 9.1 测试文件结构

```python
# test_order_service.py
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from order_service import (
    OrderService, Order, OrderStatus,
    OrderError, OrderValidationError, OrderNotFoundError,
    OrderStateError, OrderDuplicateError,
)

@pytest.fixture
def fresh_service():
    return OrderService()

# ... 用例 ...
```

### 9.2 必须覆盖的用例清单

| # | 用例 ID | 场景 | 断言 |
|---|---------|------|------|
| 1 | `test_create_then_get` | 创建后查询 | `get_order(order_id).status == PENDING` |
| 2 | `test_create_invalid_order_id` | `order_id=""` / `"a b"` / 65 字符 | 抛 `OrderValidationError`，`code` 符合规则 |
| 3 | `test_create_invalid_items` | `items=[]`、负数 qty | 抛 `OrderValidationError` |
| 4 | `test_create_negative_amount` | `amount=-1.0` | 抛 `OrderValidationError("NEGATIVE_AMOUNT")` |
| 5 | `test_get_unknown` | 不存在 id | 抛 `OrderNotFoundError` |
| 6 | `test_cancel_pending` | PENDING → CANCELLED | 状态变化，`version += 1` |
| 7 | `test_cancel_already_cancelled_idempotent` | 重复 cancel | 两次都返回 `status=CANCELLED`，第二次 `cancel_hits == 1` |
| 8 | `test_create_idempotent_same_params` | 同 id 同参数重复 create | 两次返回等价 `Order`，`create_hits == 1` |
| 9 | `test_create_duplicate_different_params` | 同 id 不同参数 | 抛 `OrderDuplicateError` |
| 10 | `test_list_filter_by_status` | 混合订单列表 | 过滤后数量正确 |
| 11 | `test_concurrent_create_same_id` | 20 线程同时 `create_order` 同 id 同参数 | 最终 `len(orders)==1`，`create_calls==1`，`create_hits==19`，所有返回的 `order_id` 相等且 `user_id/amount` 一致 |
| 12 | `test_concurrent_cancel` | 10 线程同时 `cancel_order` | 最终 `cancel_calls==1`，`cancel_hits==9`，订单终态 `CANCELLED` |
| 13 | `test_concurrent_create_different_ids` | 50 线程各创建不同 id | 全部成功，订单数 == 50 |

### 9.3 并发测试模板

```python
def test_concurrent_create_same_id(fresh_service):
    svc = fresh_service
    order_id = "ORD-1"
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()  # 同步起跑
        return svc.create_order(
            order_id=order_id,
            user_id="u1",
            items=[("sku", 2, 9.99)],
            amount=19.98,
        )

    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(lambda _: worker(), range(20)))

    # 一致性
    assert all(r.order_id == order_id for r in results)
    # 幂等
    s = svc.stats()
    assert s["create_calls"] == 1
    assert s["create_hits"] == 19
```

### 9.4 覆盖率目标

- `order_service.py` 行覆盖 ≥ 90%。
- 分支覆盖：`if existing: ... else: ...` 两条均需执行。
- 异常分支：每种异常至少 1 个用例。

---

## 10. 实现提示（给 dev 的开发顺序）

1. **先异常，再实体**：定义 `OrderError` 家族与 `OrderStatus`、`Order`。
2. **再服务，最后测试**：直接实现 `OrderService`（不引入中间层类以保持 2 文件约束）；所有仓储细节作为 `OrderService` 的私有方法 `_validate_*`、`_upsert_locked` 等。
3. **测试驱动**：建议先写用例 1-10（单测），再写用例 11-13（并发），并发用例多跑几次确认稳定。

---

## 11. 验收标准

- [ ] `pytest -q` 全部通过，无 flaky。
- [ ] `pytest --cov=order_service --cov-report=term-missing` 覆盖率 ≥ 90%。
- [ ] 用例 11 / 12 在本地连续运行 5 次无失败。
- [ ] 代码无 `print`，无未捕获的 `except:`。
- [ ] 所有公共方法有类型注解。
- [ ] 满足 2 文件约束：`order_service.py` + `test_order_service.py`。

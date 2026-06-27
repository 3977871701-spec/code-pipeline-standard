"""High-availability in-memory order service.

Implements SPEC.md:
- Idempotent create / cancel operations.
- Thread-safe via a re-entrant lock guarding all repository access.
- Structured exception hierarchy with error codes.
- Immutable ``Order`` entity mutated only through ``dataclasses.replace``.
"""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class OrderError(Exception):
    """Base class for all order-related errors."""

    code: str = "ORDER_ERROR"

    def __init__(self, message: str, *, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class OrderValidationError(OrderError):
    """Raised when caller-provided parameters fail validation."""


class OrderNotFoundError(OrderError):
    """Raised when an order_id does not exist in the repository."""

    code: str = "NOT_FOUND"


class OrderStateError(OrderError):
    """Raised when a state-machine transition is not permitted."""

    code: str = "INVALID_STATE"


class OrderDuplicateError(OrderError):
    """Raised when the same order_id is reused with a different payload."""

    code: str = "DUPLICATE_ID_PARAM_MISMATCH"


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FLOAT_TOL = 1e-9


@dataclass(frozen=True)
class Order:
    """Immutable order record. State changes use ``dataclasses.replace``."""

    order_id: str
    user_id: str
    items: Tuple[Tuple[str, int, float], ...]
    amount: float
    status: OrderStatus
    created_at: float
    updated_at: float
    version: int = 1


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OrderService:
    """In-memory, thread-safe, idempotent order service."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._orders: Dict[str, Order] = {}
        self._lock: threading.RLock = threading.RLock()
        self._stats: Dict[str, int] = {
            "create_calls": 0,
            "create_hits": 0,
            "cancel_calls": 0,
            "cancel_hits": 0,
        }

    # -- public API ---------------------------------------------------------

    def create_order(
        self,
        *,
        order_id: str,
        user_id: str,
        items: Sequence[Tuple[str, int, float]],
        amount: float,
    ) -> Order:
        """Create a new order, or return the existing one for an idempotent retry."""
        self._validate_order_id(order_id)
        self._validate_user_id(user_id)
        self._validate_items(items)
        self._validate_amount(amount)

        norm_items: Tuple[Tuple[str, int, float], ...] = tuple(
            (str(sku), int(qty), float(price)) for sku, qty, price in items
        )

        with self._lock:
            existing = self._orders.get(order_id)
            if existing is not None:
                if self._same_payload(existing, user_id, norm_items, amount):
                    self._stats["create_hits"] += 1
                    return existing
                raise OrderDuplicateError(
                    f"order_id already exists with different payload: {order_id}",
                    code="DUPLICATE_ID_PARAM_MISMATCH",
                )

            now = self._clock()
            order = Order(
                order_id=order_id,
                user_id=user_id,
                items=norm_items,
                amount=float(amount),
                status=OrderStatus.PENDING,
                created_at=now,
                updated_at=now,
                version=1,
            )
            self._orders[order_id] = order
            self._stats["create_calls"] += 1
            return order

    def get_order(self, order_id: str) -> Order:
        """Return the order for ``order_id`` or raise :class:`OrderNotFoundError`."""
        if not isinstance(order_id, str) or not order_id:
            raise OrderValidationError(
                f"order_id must be a non-empty string: {order_id!r}",
                code="INVALID_ARG",
            )
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise OrderNotFoundError(
                    f"order not found: {order_id}", code="NOT_FOUND"
                )
            return order

    def cancel_order(self, order_id: str) -> Order:
        """Cancel an order. Idempotent on already-cancelled orders."""
        if not isinstance(order_id, str) or not order_id:
            raise OrderValidationError(
                f"order_id must be a non-empty string: {order_id!r}",
                code="INVALID_ARG",
            )
        with self._lock:
            current = self._orders.get(order_id)
            if current is None:
                raise OrderNotFoundError(
                    f"order not found: {order_id}", code="NOT_FOUND"
                )
            if current.status == OrderStatus.CANCELLED:
                self._stats["cancel_hits"] += 1
                return current
            cancelled = replace(
                current,
                status=OrderStatus.CANCELLED,
                updated_at=self._clock(),
                version=current.version + 1,
            )
            self._orders[order_id] = cancelled
            self._stats["cancel_calls"] += 1
            return cancelled

    def list_orders(
        self, *, status: Optional[OrderStatus] = None
    ) -> List[Order]:
        """Return all orders filtered by ``status``, sorted by ``created_at`` asc."""
        with self._lock:
            snapshot = list(self._orders.values())
        if status is not None:
            snapshot = [o for o in snapshot if o.status == status]
        snapshot.sort(key=lambda o: o.created_at)
        return snapshot

    def stats(self) -> Dict[str, int]:
        """Return a defensive copy of the internal counters."""
        with self._lock:
            return dict(self._stats)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _validate_order_id(order_id: str) -> None:
        if not isinstance(order_id, str) or order_id == "":
            raise OrderValidationError(
                f"order_id must not be empty: {order_id!r}", code="EMPTY_ORDER_ID"
            )
        if len(order_id) > 64 or not _ORDER_ID_RE.match(order_id):
            raise OrderValidationError(
                f"order_id contains invalid characters: {order_id!r}",
                code="INVALID_ORDER_ID",
            )

    @staticmethod
    def _validate_user_id(user_id: str) -> None:
        if not isinstance(user_id, str) or user_id == "":
            raise OrderValidationError(
                f"user_id must not be empty: {user_id!r}", code="INVALID_ARG"
            )

    @staticmethod
    def _validate_items(items: Sequence[Tuple[str, int, float]]) -> None:
        if not isinstance(items, (list, tuple)) or len(items) == 0:
            raise OrderValidationError(
                f"items must not be empty: {items!r}", code="EMPTY_ITEMS"
            )
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise OrderValidationError(
                    f"item qty/price out of range: {item!r}", code="INVALID_ARG"
                )
            sku, qty, price = item
            if not isinstance(sku, str) or sku == "":
                raise OrderValidationError(
                    f"item sku must be a non-empty string: {item!r}",
                    code="INVALID_ARG",
                )
            try:
                qty_i, price_f = int(qty), float(price)
            except (TypeError, ValueError):
                raise OrderValidationError(
                    f"item qty/price out of range: {item!r}", code="INVALID_ARG"
                )
            if qty_i <= 0 or price_f < 0 or math.isnan(price_f):
                raise OrderValidationError(
                    f"item qty/price out of range: {item!r}", code="INVALID_ARG"
                )

    @staticmethod
    def _validate_amount(amount: float) -> None:
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise OrderValidationError(
                f"amount must be a non-negative finite number: {amount!r}",
                code="NEGATIVE_AMOUNT",
            )
        v = float(amount)
        if math.isnan(v) or math.isinf(v) or v < 0:
            raise OrderValidationError(
                f"amount must be a non-negative finite number: {amount!r}",
                code="NEGATIVE_AMOUNT",
            )

    @staticmethod
    def _same_payload(
        order: Order,
        user_id: str,
        items: Tuple[Tuple[str, int, float], ...],
        amount: float,
    ) -> bool:
        return (
            order.user_id == user_id
            and tuple(order.items) == tuple(items)
            and abs(order.amount - float(amount)) < _FLOAT_TOL
        )

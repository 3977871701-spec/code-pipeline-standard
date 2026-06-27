"""Tests for the in-memory order service.

Covers:
- Normal create / get / cancel flow.
- Validation error paths (every error code).
- Idempotency on repeated create / cancel.
- Boundary conditions (empty params, illegal state transitions, missing ids).
- Concurrency: real race conditions via threading + barrier (>=10 threads).

Uses only the standard library (unittest + threading).
"""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List
from unittest import TestCase, main

from order_service import (
    Order,
    OrderDuplicateError,
    OrderError,
    OrderNotFoundError,
    OrderService,
    OrderStatus,
    OrderValidationError,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_service() -> OrderService:
    """A fresh OrderService with a deterministic, monotonic clock."""
    state = {"now": 1_000_000.0}

    def clock() -> float:
        state["now"] += 0.001
        return state["now"]

    return OrderService(clock=clock)


def _items() -> list:
    return [("sku-1", 2, 9.99)]


# ---------------------------------------------------------------------------
# 1. Normal flow
# ---------------------------------------------------------------------------


class CreateGetFlowTests(TestCase):
    def test_create_then_get(self) -> None:
        svc = _make_service()
        order = svc.create_order(
            order_id="ORD-1",
            user_id="user-1",
            items=_items(),
            amount=19.98,
        )
        self.assertIsInstance(order, Order)
        self.assertEqual(order.order_id, "ORD-1")
        self.assertEqual(order.user_id, "user-1")
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(order.version, 1)

        fetched = svc.get_order("ORD-1")
        self.assertEqual(fetched, order)
        self.assertEqual(fetched.status, OrderStatus.PENDING)

    def test_create_multiple_orders_returns_list(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="A", user_id="u1", items=[("s", 1, 1.0)], amount=1.0
        )
        svc.create_order(
            order_id="B", user_id="u1", items=[("s", 1, 1.0)], amount=1.0
        )
        svc.create_order(
            order_id="C", user_id="u2", items=[("s", 1, 1.0)], amount=1.0
        )
        ids = {o.order_id for o in svc.list_orders()}
        self.assertEqual(ids, {"A", "B", "C"})

    def test_create_zero_amount_is_valid(self) -> None:
        """Boundary: amount == 0 is allowed (e.g. free / promotional)."""
        svc = _make_service()
        order = svc.create_order(
            order_id="ORD-ZERO",
            user_id="u1",
            items=[("s", 1, 0.0)],
            amount=0.0,
        )
        self.assertEqual(order.amount, 0.0)

    def test_create_order_id_max_length_is_valid(self) -> None:
        """Boundary: order_id of exactly 64 chars is allowed."""
        svc = _make_service()
        long_id = "a" + "b" * 63
        order = svc.create_order(
            order_id=long_id, user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        self.assertEqual(order.order_id, long_id)


# ---------------------------------------------------------------------------
# 2. Validation: invalid order_id
# ---------------------------------------------------------------------------


class InvalidOrderIdTests(TestCase):
    def setUp(self) -> None:
        self.svc = _make_service()

    def _create(self, order_id: str) -> None:
        self.svc.create_order(
            order_id=order_id,
            user_id="u1",
            items=[("s", 1, 1.0)],
            amount=1.0,
        )

    def test_empty_order_id(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self._create("")
        self.assertEqual(ctx.exception.code, "EMPTY_ORDER_ID")

    def test_order_id_with_spaces(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self._create("a b")
        self.assertEqual(ctx.exception.code, "INVALID_ORDER_ID")

    def test_order_id_too_long(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self._create("x" * 65)
        self.assertEqual(ctx.exception.code, "INVALID_ORDER_ID")

    def test_order_id_with_slash(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self._create("ord/1")
        self.assertEqual(ctx.exception.code, "INVALID_ORDER_ID")


# ---------------------------------------------------------------------------
# 3. Validation: invalid user_id
# ---------------------------------------------------------------------------


class InvalidUserIdTests(TestCase):
    def test_empty_user_id(self) -> None:
        svc = _make_service()
        with self.assertRaises(OrderValidationError) as ctx:
            svc.create_order(
                order_id="ORD-1",
                user_id="",
                items=[("s", 1, 1.0)],
                amount=1.0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_ARG")


# ---------------------------------------------------------------------------
# 4. Validation: invalid items
# ---------------------------------------------------------------------------


class InvalidItemsTests(TestCase):
    def setUp(self) -> None:
        self.svc = _make_service()

    def test_empty_items(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1", user_id="u1", items=[], amount=0.0
            )
        self.assertEqual(ctx.exception.code, "EMPTY_ITEMS")

    def test_negative_qty(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", -1, 1.0)],
                amount=1.0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_ARG")

    def test_zero_qty(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", 0, 1.0)],
                amount=1.0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_ARG")

    def test_negative_price(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", 1, -0.5)],
                amount=1.0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_ARG")

    def test_item_wrong_shape(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", 1)],  # missing price
                amount=1.0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_ARG")

    def test_empty_sku(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("", 1, 1.0)],
                amount=1.0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_ARG")


# ---------------------------------------------------------------------------
# 5. Validation: invalid amount
# ---------------------------------------------------------------------------


class InvalidAmountTests(TestCase):
    def setUp(self) -> None:
        self.svc = _make_service()

    def test_negative_amount(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", 1, 1.0)],
                amount=-1.0,
            )
        self.assertEqual(ctx.exception.code, "NEGATIVE_AMOUNT")

    def test_nan_amount(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", 1, 1.0)],
                amount=float("nan"),
            )
        self.assertEqual(ctx.exception.code, "NEGATIVE_AMOUNT")

    def test_inf_amount(self) -> None:
        with self.assertRaises(OrderValidationError) as ctx:
            self.svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", 1, 1.0)],
                amount=float("inf"),
            )
        self.assertEqual(ctx.exception.code, "NEGATIVE_AMOUNT")


# ---------------------------------------------------------------------------
# 6. get_order error paths
# ---------------------------------------------------------------------------


class GetOrderErrorTests(TestCase):
    def test_unknown_order_id(self) -> None:
        svc = _make_service()
        with self.assertRaises(OrderNotFoundError) as ctx:
            svc.get_order("missing")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_empty_order_id(self) -> None:
        svc = _make_service()
        with self.assertRaises(OrderValidationError):
            svc.get_order("")


# ---------------------------------------------------------------------------
# 7. cancel_order
# ---------------------------------------------------------------------------


class CancelOrderTests(TestCase):
    def test_cancel_pending(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="ORD-1",
            user_id="u1",
            items=[("s", 1, 1.0)],
            amount=1.0,
        )
        before = svc.get_order("ORD-1")
        self.assertEqual(before.status, OrderStatus.PENDING)
        self.assertEqual(before.version, 1)

        cancelled = svc.cancel_order("ORD-1")
        self.assertEqual(cancelled.status, OrderStatus.CANCELLED)
        self.assertEqual(cancelled.version, before.version + 1)
        self.assertGreaterEqual(cancelled.updated_at, before.updated_at)

        persisted = svc.get_order("ORD-1")
        self.assertEqual(persisted.status, OrderStatus.CANCELLED)
        self.assertEqual(persisted.version, before.version + 1)

        stats = svc.stats()
        self.assertEqual(stats["cancel_calls"], 1)
        self.assertEqual(stats["cancel_hits"], 0)

    def test_cancel_unknown(self) -> None:
        svc = _make_service()
        with self.assertRaises(OrderNotFoundError) as ctx:
            svc.cancel_order("nope")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_cancel_invalid_order_id(self) -> None:
        svc = _make_service()
        with self.assertRaises(OrderValidationError):
            svc.cancel_order("")

    def test_cancel_already_cancelled_is_idempotent(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="ORD-1",
            user_id="u1",
            items=[("s", 1, 1.0)],
            amount=1.0,
        )
        first = svc.cancel_order("ORD-1")
        second = svc.cancel_order("ORD-1")
        self.assertEqual(first.status, OrderStatus.CANCELLED)
        self.assertEqual(second.status, OrderStatus.CANCELLED)
        # Idempotent: no version bump on the second call.
        self.assertEqual(second.version, first.version)
        stats = svc.stats()
        self.assertEqual(stats["cancel_calls"], 1)
        self.assertEqual(stats["cancel_hits"], 1)


# ---------------------------------------------------------------------------
# 8. Idempotency on create
# ---------------------------------------------------------------------------


class CreateIdempotencyTests(TestCase):
    def test_same_params_returns_existing(self) -> None:
        svc = _make_service()
        params = dict(
            order_id="ORD-1",
            user_id="u1",
            items=[("sku", 2, 9.99)],
            amount=19.98,
        )
        first = svc.create_order(**params)
        second = svc.create_order(**params)
        self.assertEqual(first, second)
        self.assertEqual(first.status, OrderStatus.PENDING)
        self.assertEqual(first.version, 1)
        stats = svc.stats()
        self.assertEqual(stats["create_calls"], 1)
        self.assertEqual(stats["create_hits"], 1)

    def test_amount_within_tolerance_treated_as_equal(self) -> None:
        svc = _make_service()
        first = svc.create_order(
            order_id="ORD-1",
            user_id="u1",
            items=[("s", 1, 1.0)],
            amount=1.0,
        )
        second = svc.create_order(
            order_id="ORD-1",
            user_id="u1",
            items=[("s", 1, 1.0)],
            amount=1.0 + 1e-12,  # within 1e-9 tolerance
        )
        self.assertEqual(first, second)
        stats = svc.stats()
        self.assertEqual(stats["create_calls"], 1)
        self.assertEqual(stats["create_hits"], 1)

    def test_different_amount_raises_duplicate(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="ORD-1", user_id="u1", items=[("s", 1, 1.0)], amount=1.0
        )
        with self.assertRaises(OrderDuplicateError) as ctx:
            svc.create_order(
                order_id="ORD-1",
                user_id="u1",
                items=[("s", 1, 1.0)],
                amount=99.0,
            )
        self.assertEqual(ctx.exception.code, "DUPLICATE_ID_PARAM_MISMATCH")

    def test_different_user_raises_duplicate(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="ORD-1",
            user_id="alice",
            items=[("s", 1, 1.0)],
            amount=1.0,
        )
        with self.assertRaises(OrderDuplicateError):
            svc.create_order(
                order_id="ORD-1",
                user_id="bob",
                items=[("s", 1, 1.0)],
                amount=1.0,
            )

    def test_different_items_raises_duplicate(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="ORD-1", user_id="u1", items=[("s", 1, 1.0)], amount=1.0
        )
        with self.assertRaises(OrderDuplicateError):
            svc.create_order(
                order_id="ORD-1", user_id="u1", items=[("s", 2, 1.0)], amount=1.0
            )


# ---------------------------------------------------------------------------
# 9. list_orders
# ---------------------------------------------------------------------------


class ListOrdersTests(TestCase):
    def test_filter_by_status(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="A", user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        svc.create_order(
            order_id="B", user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        svc.create_order(
            order_id="C", user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        svc.cancel_order("A")
        svc.cancel_order("B")

        pending = svc.list_orders(status=OrderStatus.PENDING)
        cancelled = svc.list_orders(status=OrderStatus.CANCELLED)
        all_orders = svc.list_orders()

        self.assertEqual({o.order_id for o in pending}, {"C"})
        self.assertEqual({o.order_id for o in cancelled}, {"A", "B"})
        self.assertEqual({o.order_id for o in all_orders}, {"A", "B", "C"})
        # Ordering: created_at ascending.
        self.assertEqual([o.order_id for o in all_orders], ["A", "B", "C"])

    def test_returns_independent_list(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="A", user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        snapshot = svc.list_orders()
        snapshot.clear()
        # The internal storage must not be affected.
        self.assertEqual(len(svc.list_orders()), 1)


# ---------------------------------------------------------------------------
# 10. Stats
# ---------------------------------------------------------------------------


class StatsTests(TestCase):
    def test_initial_stats(self) -> None:
        svc = _make_service()
        self.assertEqual(
            svc.stats(),
            {
                "create_calls": 0,
                "create_hits": 0,
                "cancel_calls": 0,
                "cancel_hits": 0,
            },
        )

    def test_stats_is_defensive_copy(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="A", user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        snapshot = svc.stats()
        snapshot["create_calls"] = 999
        self.assertEqual(svc.stats()["create_calls"], 1)


# ---------------------------------------------------------------------------
# 11. Concurrency
# ---------------------------------------------------------------------------


class ConcurrentCreateSameIdTests(TestCase):
    def test_twenty_threads_same_id_same_params(self) -> None:
        """20 threads concurrently create the same order with the same params.

        The service must guarantee:
        - exactly one Order stored
        - create_calls == 1, create_hits == 19
        - all returned Order references share the same identity-equivalent state
        """
        svc = _make_service()
        order_id = "ORD-CONC-1"
        n = 20
        barrier = threading.Barrier(n)
        results: List[Order] = []
        errors: List[BaseException] = []
        results_lock = threading.Lock()

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                order = svc.create_order(
                    order_id=order_id,
                    user_id="u1",
                    items=[("sku", 2, 9.99)],
                    amount=19.98,
                )
            except BaseException as e:  # noqa: BLE001
                with results_lock:
                    errors.append(e)
                return
            with results_lock:
                results.append(order)

        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(worker) for _ in range(n)]
            for f in futs:
                f.result(timeout=10)

        self.assertFalse(errors, f"unexpected errors: {errors!r}")
        self.assertEqual(len(results), n)
        for r in results:
            self.assertEqual(r.order_id, order_id)
            self.assertEqual(r.user_id, "u1")
            self.assertAlmostEqual(r.amount, 19.98, places=9)
            self.assertEqual(r.status, OrderStatus.PENDING)

        all_orders = svc.list_orders()
        self.assertEqual(len(all_orders), 1)
        self.assertEqual(all_orders[0].order_id, order_id)

        stats = svc.stats()
        self.assertEqual(stats["create_calls"], 1)
        self.assertEqual(stats["create_hits"], n - 1)


class ConcurrentCancelTests(TestCase):
    def test_ten_threads_cancel_same_order(self) -> None:
        svc = _make_service()
        svc.create_order(
            order_id="ORD-CONC-CXL",
            user_id="u1",
            items=[("sku", 1, 1.0)],
            amount=1.0,
        )
        n = 10
        barrier = threading.Barrier(n)
        results: List[Order] = []
        results_lock = threading.Lock()
        errors: List[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                cancelled = svc.cancel_order("ORD-CONC-CXL")
            except BaseException as e:  # noqa: BLE001
                with results_lock:
                    errors.append(e)
                return
            with results_lock:
                results.append(cancelled)

        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(worker) for _ in range(n)]
            for f in futs:
                f.result(timeout=10)

        self.assertFalse(errors, f"unexpected errors: {errors!r}")
        self.assertEqual(len(results), n)
        for r in results:
            self.assertEqual(r.status, OrderStatus.CANCELLED)

        stats = svc.stats()
        self.assertEqual(stats["cancel_calls"], 1)
        self.assertEqual(stats["cancel_hits"], n - 1)

        final = svc.get_order("ORD-CONC-CXL")
        self.assertEqual(final.status, OrderStatus.CANCELLED)
        self.assertEqual(final.version, 2)


class ConcurrentCreateDifferentIdsTests(TestCase):
    def test_fifty_threads_distinct_ids(self) -> None:
        svc = _make_service()
        n = 50
        barrier = threading.Barrier(n)
        results: List[Order] = []
        results_lock = threading.Lock()
        errors: List[BaseException] = []

        def worker(i: int) -> None:
            try:
                barrier.wait(timeout=5)
                order = svc.create_order(
                    order_id=f"ORD-{i:04d}",
                    user_id=f"u{i}",
                    items=[("sku", 1, float(i))],
                    amount=float(i),
                )
            except BaseException as e:  # noqa: BLE001
                with results_lock:
                    errors.append(e)
                return
            with results_lock:
                results.append(order)

        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(worker, i) for i in range(n)]
            for f in futs:
                f.result(timeout=10)

        self.assertFalse(errors, f"unexpected errors: {errors!r}")
        self.assertEqual(len(results), n)
        self.assertEqual(
            {r.order_id for r in results}, {f"ORD-{i:04d}" for i in range(n)}
        )

        all_orders = svc.list_orders()
        self.assertEqual(len(all_orders), n)
        stats = svc.stats()
        self.assertEqual(stats["create_calls"], n)
        self.assertEqual(stats["create_hits"], 0)


# ---------------------------------------------------------------------------
# 12. Exception hierarchy and codes
# ---------------------------------------------------------------------------


class ExceptionHierarchyTests(TestCase):
    def test_inheritance(self) -> None:
        self.assertTrue(issubclass(OrderValidationError, OrderError))
        self.assertTrue(issubclass(OrderNotFoundError, OrderError))
        self.assertTrue(issubclass(OrderDuplicateError, OrderError))
        self.assertTrue(issubclass(OrderError, Exception))

    def test_message_and_code(self) -> None:
        try:
            raise OrderValidationError("boom", code="X")
        except OrderError as e:
            self.assertEqual(e.message, "boom")
            self.assertEqual(e.code, "X")
            self.assertEqual(str(e), "boom")

    def test_default_code(self) -> None:
        # OrderError default code.
        with self.assertRaises(OrderError) as ctx:
            raise OrderError("plain")
        self.assertEqual(ctx.exception.code, "ORDER_ERROR")

    def test_default_code_when_code_kwarg_omitted(self) -> None:
        # Branch in OrderError.__init__ when `code` is not provided at all.
        err = OrderError("plain")
        self.assertEqual(err.message, "plain")
        self.assertEqual(err.code, "ORDER_ERROR")


# ---------------------------------------------------------------------------
# 13. Deterministic clock injection
# ---------------------------------------------------------------------------


class ClockInjectionTests(TestCase):
    def test_deterministic_clock(self) -> None:
        counter = {"n": 0}

        def clock() -> float:
            counter["n"] += 1
            return 1_000_000.0 + counter["n"] * 1.0

        svc = OrderService(clock=clock)
        a = svc.create_order(
            order_id="A", user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        self.assertEqual(a.created_at, 1_000_001.0)
        self.assertEqual(a.updated_at, 1_000_001.0)

        # Idempotent re-create must NOT advance the clock.
        b = svc.create_order(
            order_id="A", user_id="u", items=[("s", 1, 1.0)], amount=1.0
        )
        self.assertEqual(b, a)

        c = svc.cancel_order("A")
        self.assertEqual(c.updated_at, 1_000_002.0)
        self.assertEqual(c.created_at, 1_000_001.0)
        self.assertEqual(c.version, 2)


if __name__ == "__main__":
    main(verbosity=2)

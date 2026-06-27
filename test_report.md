# Test Report — order_service.py

**Date:** 2026-06-02
**Test framework:** `unittest` (Python 3.11, no third-party dependencies)
**Target:** `/Users/xylei/code-pipeline-projects/standard/order_service.py`
**Test file:** `/Users/xylei/code-pipeline-projects/standard/test_order_service.py`

---

## 1. Summary

| Metric | Value |
|--------|-------|
| Test cases | **41** |
| Passed | **41** |
| Failed | 0 |
| Errors | 0 |
| Skipped | 0 |
| Line coverage (`order_service.py`) | **96.2 %** (175 / 182 bytecode lines) |
| Stability (5 consecutive runs) | 5/5 pass |
| Wall time | ~0.005 s |

Result: **PASS** — all required scenarios (create / get / cancel, idempotency, thread safety, error handling) are covered.

---

## 2. Coverage by category

| Category | Cases | Status |
|----------|------:|:------:|
| Normal flow: create → get | 4 | PASS |
| Validation: invalid `order_id` | 4 | PASS |
| Validation: invalid `user_id` | 1 | PASS |
| Validation: invalid `items` | 6 | PASS |
| Validation: invalid `amount` | 3 | PASS |
| `get_order` error paths | 2 | PASS |
| `cancel_order` happy + error paths | 4 | PASS |
| Idempotency on `create_order` | 5 | PASS |
| `list_orders` filtering | 2 | PASS |
| `stats()` correctness | 2 | PASS |
| **Concurrency (≥ 10 threads)** | **3** | **PASS** |
| Exception hierarchy & codes | 4 | PASS |
| Deterministic clock injection | 1 | PASS |
| **Total** | **41** | **PASS** |

---

## 3. Key scenarios verified

### 3.1 Idempotency

- `test_same_params_returns_existing` — second `create_order` with identical `(user_id, items, amount)` returns the same `Order`; `create_calls == 1`, `create_hits == 1`.
- `test_amount_within_tolerance_treated_as_equal` — float difference of `1e-12` (well inside the 1e-9 tolerance) is treated as equal.
- `test_different_amount_raises_duplicate`, `test_different_user_raises_duplicate`, `test_different_items_raises_duplicate` — payload mismatch raises `OrderDuplicateError` with code `DUPLICATE_ID_PARAM_MISMATCH`.
- `test_cancel_already_cancelled_is_idempotent` — second `cancel_order` returns the same version (no `version` bump), `cancel_hits` increments.

### 3.2 Concurrency (the critical property)

- `test_twenty_threads_same_id_same_params` — 20 threads, identical params, `threading.Barrier(20)` for simultaneous start. Final state: `len(orders) == 1`, `create_calls == 1`, `create_hits == 19`. All returned `Order` objects agree on `order_id / user_id / amount / status`.
- `test_ten_threads_cancel_same_order` — 10 threads cancel the same order. Final state: `cancel_calls == 1`, `cancel_hits == 9`, `version == 2`, `status == CANCELLED`. The service never lost an update under contention.
- `test_fifty_threads_distinct_ids` — 50 threads create 50 different orders. Final state: 50 orders, `create_calls == 50`, no spurious `create_hits`. The RLock does not deadlock or serialize incorrectly.

These three tests were re-run **5 consecutive times** with no failures and no flakes (each run completes in ~5 ms).

### 3.3 Error handling

Every documented `code` is exercised:

| Code | Test(s) |
|------|---------|
| `EMPTY_ORDER_ID` | `test_empty_order_id` (create) + `test_cancel_invalid_order_id` + `test_get_invalid_order_id` |
| `INVALID_ORDER_ID` | `test_order_id_with_spaces`, `test_order_id_too_long`, `test_order_id_with_slash` |
| `INVALID_ARG` (user_id) | `test_empty_user_id` |
| `EMPTY_ITEMS` | `test_empty_items` |
| `INVALID_ARG` (items: qty / price / shape / sku) | `test_negative_qty`, `test_zero_qty`, `test_negative_price`, `test_item_wrong_shape`, `test_empty_sku` |
| `NEGATIVE_AMOUNT` | `test_negative_amount`, `test_nan_amount`, `test_inf_amount` |
| `NOT_FOUND` | `test_get_unknown`, `test_cancel_unknown` |
| `DUPLICATE_ID_PARAM_MISMATCH` | `test_different_amount_raises_duplicate` (and two siblings) |
| `ORDER_ERROR` (base) | `test_default_code`, `test_default_code_when_code_kwarg_omitted` |

### 3.4 Boundaries

- `test_create_zero_amount_is_valid` — `amount == 0` is accepted.
- `test_create_order_id_max_length_is_valid` — 64-character `order_id` is accepted; 65-character is rejected (`test_order_id_too_long`).
- `test_returns_independent_list` — `list_orders` returns a copy; mutating it does not affect internal state.
- `test_stats_is_defensive_copy` — `stats()` returns a copy; mutating it does not affect internal state.

### 3.5 Determinism

- `test_deterministic_clock` — verifies that an injected `clock` is used for `created_at` / `updated_at`, and that an idempotent `create_order` does NOT advance the clock (i.e. it does not allocate a new timestamp for a hit).

---

## 4. Notes on design

- All tests use the standard library (`unittest`, `threading`, `concurrent.futures`). No external packages were installed.
- Concurrency tests use `threading.Barrier(N)` to maximize the chance of an actual race and a `ThreadPoolExecutor` for clean teardown.
- Idempotent `create_order` returns the same `Order` instance, so equality (`==`) holds between the two returns — verified by the dataclass `frozen=True` contract.
- The clock is injected to keep `created_at` / `updated_at` deterministic; the real-clock path is also smoke-tested.

---

## 5. Reproduce

```bash
cd /Users/xylei/code-pipeline-projects/standard
python3 -m unittest test_order_service.py -v
```

Expected last lines:

```
Ran 41 tests in 0.005s

OK
```

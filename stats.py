"""Numeric aggregation helpers.

Pure functions for summing, averaging, counting, finding minima and maxima
over a list of values. Strings are coerced to ``float`` automatically.
"""

from __future__ import annotations

from typing import Iterable, Sequence


AGG_FUNCTIONS = {"sum", "avg", "count", "min", "max"}


class StatsError(Exception):
    """Raised when an aggregation cannot be computed.

    Attributes:
        index: Index of the offending value when applicable.
        value: The offending value when applicable.
    """

    def __init__(
        self,
        message: str,
        index: int | None = None,
        value: object = None,
    ) -> None:
        self.index = index
        self.value = value
        super().__init__(message)


def _to_float(value: object, index: int) -> float:
    """Coerce a value to ``float``, raising :class:`StatsError` on failure.

    ``None`` and empty strings are treated as missing values and raise
    :class:`StatsError` to make callers fail loudly.
    """
    if value is None:
        raise StatsError(
            f"Cannot convert None to float at index {index}",
            index=index,
            value=value,
        )
    if isinstance(value, str):
        if value == "":
            raise StatsError(
                f"Cannot convert '' to float at index {index}",
                index=index,
                value=value,
            )
        try:
            return float(value)
        except ValueError as exc:
            raise StatsError(
                f"Cannot convert {value!r} to float at index {index}",
                index=index,
                value=value,
            ) from exc
    if isinstance(value, bool):
        # bool is a subclass of int; preserve semantics explicitly.
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    raise StatsError(
        f"Cannot convert {value!r} to float at index {index}",
        index=index,
        value=value,
    )


def _coerce_all(values: Sequence[object]) -> list[float]:
    result: list[float] = []
    for i, v in enumerate(values):
        result.append(_to_float(v, i))
    return result


def sum_(values: list[float | str]) -> float:
    """Return the sum of ``values`` as ``float``."""
    nums = _coerce_all(values)
    return float(sum(nums))


def avg(values: list[float | str]) -> float:
    """Return the arithmetic mean of ``values``.

    Raises:
        StatsError: If ``values`` is empty.
    """
    if len(values) == 0:
        raise StatsError("Cannot compute average of empty sequence")
    nums = _coerce_all(values)
    return float(sum(nums) / len(nums))


def count(values: Iterable[object]) -> int:
    """Count non-empty values (``None`` and ``''`` are excluded)."""
    total = 0
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and v == "":
            continue
        total += 1
    return total


def min_val(values: list[float | str]) -> float:
    """Return the minimum of ``values``.

    Raises:
        StatsError: If ``values`` is empty.
    """
    if len(values) == 0:
        raise StatsError("Cannot compute min of empty sequence")
    nums = _coerce_all(values)
    return float(min(nums))


def max_val(values: list[float | str]) -> float:
    """Return the maximum of ``values``.

    Raises:
        StatsError: If ``values`` is empty.
    """
    if len(values) == 0:
        raise StatsError("Cannot compute max of empty sequence")
    nums = _coerce_all(values)
    return float(max(nums))


def aggregate(values: list, func_name: str) -> float | int:
    """Dispatch a numeric aggregation by name.

    Args:
        values: Input list of numbers or numeric strings.
        func_name: One of ``sum``, ``avg``, ``count``, ``min``, ``max``.

    Raises:
        ValueError: When ``func_name`` is not a known function.
        StatsError: When the underlying aggregation fails.
    """
    if func_name not in AGG_FUNCTIONS:
        raise ValueError(
            f"Unknown aggregation function: {func_name!r}. "
            f"Expected one of {sorted(AGG_FUNCTIONS)}"
        )
    if func_name == "sum":
        return sum_(values)
    if func_name == "avg":
        return avg(values)
    if func_name == "count":
        return count(values)
    if func_name == "min":
        return min_val(values)
    if func_name == "max":
        return max_val(values)
    # Defensive: should be unreachable due to the membership check above.
    raise ValueError(f"Unknown aggregation function: {func_name!r}")

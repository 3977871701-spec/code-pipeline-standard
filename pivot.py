"""Pivot engine and command line entry point.

Composes :mod:`parser` and :mod:`stats` to build a pivot table from a
CSV file. Run as a script for the CLI:

    python pivot.py data.csv --index A --columns B --values C
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

try:  # pragma: no cover - import shim
    from . import stats
    from .parser import CSVParseError, parse_csv_file
except ImportError:  # pragma: no cover - script mode
    import stats  # type: ignore[no-redef]
    from parser import CSVParseError, parse_csv_file  # type: ignore[no-redef]


class PivotError(Exception):
    """Base class for pivot operation errors."""


class ColumnNotFoundError(PivotError):
    """Raised when a requested column is missing from the dataset."""

    def __init__(self, column: str, available: Iterable[str]) -> None:
        self.column = column
        self.available = list(available)
        super().__init__(
            f"Column {column!r} not found. Available: {self.available}"
        )


class PivotEngine:
    """Build a pivot table from parsed CSV rows.

    Args:
        rows: Parsed rows as produced by :func:`parser.parse_csv_file`.
        index_col: Column whose values form row groups.
        columns_col: Column whose values form column groups.
        value_col: Column whose values are aggregated.
        agg_func: One of ``sum``/``avg``/``count``/``min``/``max``.
        fill_value: Value used to fill missing combinations. ``None``
            leaves them missing from the result.
    """

    def __init__(
        self,
        rows: list[dict[str, str]],
        index_col: str,
        columns_col: str,
        value_col: str,
        agg_func: str = "sum",
        fill_value: float | None = 0.0,
    ) -> None:
        if agg_func not in stats.AGG_FUNCTIONS:
            raise ValueError(
                f"Unknown aggregation function: {agg_func!r}. "
                f"Expected one of {sorted(stats.AGG_FUNCTIONS)}"
            )
        distinct = {index_col, columns_col, value_col}
        if len(distinct) < 3:
            raise PivotError(
                "index_col, columns_col and value_col must be different"
            )
        self.rows = rows
        self.index_col = index_col
        self.columns_col = columns_col
        self.value_col = value_col
        self.agg_func = agg_func
        self.fill_value = fill_value
        self._validate_columns()

    def _validate_columns(self) -> None:
        if not self.rows:
            return
        available = list(self.rows[0].keys())
        for col in (self.index_col, self.columns_col, self.value_col):
            if col not in available:
                raise ColumnNotFoundError(col, available)

    @staticmethod
    def _stringify_key(value: str) -> str:
        # Preserve user-facing values; empty keys become a sentinel so they
        # are still representable as table cells.
        return value if value != "" else "<empty>"

    def pivot(self) -> dict[str, dict[str, float | int]]:
        """Compute the pivot table.

        Returns:
            Nested dict ``{index_value: {column_value: aggregated}}``.
            Missing combinations are filled with ``fill_value`` when it is
            not ``None``.
        """
        result: dict[str, dict[str, float | int]] = {}
        if not self.rows:
            return result
        # Group raw values per (index, column) combination.
        buckets: dict[tuple[str, str], list[str]] = {}
        index_keys: dict[tuple[str, str], str] = {}
        column_keys: dict[tuple[str, str], str] = {}
        for row in self.rows:
            i_val = row.get(self.index_col, "")
            c_val = row.get(self.columns_col, "")
            v_val = row.get(self.value_col, "")
            i_key = self._stringify_key(i_val)
            c_key = self._stringify_key(c_val)
            pair = (i_key, c_key)
            buckets.setdefault(pair, []).append(v_val)
            index_keys.setdefault(pair, i_key)
            column_keys.setdefault(pair, c_key)
        for (i_key, c_key), values in buckets.items():
            agg = stats.aggregate(values, self.agg_func)
            result.setdefault(i_key, {})[c_key] = agg
        if self.fill_value is not None and result:
            # Ensure all index rows have the full set of columns filled.
            all_columns = sorted(
                {c_key for cols in result.values() for c_key in cols}
            )
            for i_key in list(result.keys()):
                row = result[i_key]
                for c_key in all_columns:
                    if c_key not in row:
                        row[c_key] = self.fill_value
        return result

    def to_table(
        self,
    ) -> tuple[list[str], list[list[Any]]]:
        """Render the pivot as ``(header, rows)`` for printing.

        Returns:
            A tuple ``(column_names, data_rows)``. The first column name
            is always the index column label.
        """
        pivoted = self.pivot()
        if not pivoted:
            return [self.index_col], []
        all_columns = sorted(
            {c_key for cols in pivoted.values() for c_key in cols}
        )
        header = [self.index_col] + all_columns
        index_keys = sorted(pivoted.keys())
        data_rows: list[list[Any]] = []
        for i_key in index_keys:
            row: list[Any] = [i_key]
            for c_key in all_columns:
                row.append(pivoted[i_key].get(c_key, self.fill_value))
            data_rows.append(row)
        return header, data_rows


def _format_table(header: list[str], rows: list[list[Any]]) -> str:
    """Format a header and rows as a left-aligned text table."""
    all_rows = [header] + [list(r) for r in rows]
    if not all_rows:
        return ""
    width = max(len(str(cell)) for row in all_rows for cell in row)
    lines: list[str] = []
    for i, row in enumerate(all_rows):
        line = "  ".join(str(cell).ljust(width) for cell in row)
        lines.append(line)
        if i == 0 and rows:
            lines.append("  ".join("-" * width for _ in row))
    return "\n".join(lines)


def run_pivot(
    csv_path: str | Path,
    index_col: str,
    columns_col: str,
    value_col: str,
    agg_func: str = "sum",
    fill_value: float | None = 0.0,
) -> int:
    """CLI helper: load a CSV, run the pivot, print the table.

    Returns:
        ``0`` on success, ``1`` on any handled error.
    """
    try:
        rows = parse_csv_file(csv_path)
        engine = PivotEngine(
            rows=rows,
            index_col=index_col,
            columns_col=columns_col,
            value_col=value_col,
            agg_func=agg_func,
            fill_value=fill_value,
        )
        header, table = engine.to_table()
    except FileNotFoundError as exc:
        print(f"Error: file '{csv_path}' not found", file=sys.stderr)
        return 1
    except PermissionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except CSVParseError as exc:
        line = exc.line_number
        if line:
            print(
                f"Error: parse failed at line {line}: {exc}",
                file=sys.stderr,
            )
        else:
            print(f"Error: parse failed: {exc}", file=sys.stderr)
        return 1
    except ColumnNotFoundError as exc:
        available = ", ".join(exc.available) if exc.available else ""
        print(
            f"Error: column '{exc.column}' not found. Available: [{available}]",
            file=sys.stderr,
        )
        return 1
    except stats.StatsError as exc:
        print(f"Error: aggregation failed: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        msg = str(exc)
        if "Unknown aggregation" in msg:
            print(f"Error: unknown aggregation '{agg_func}'", file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    except PivotError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not table:
        print("(no data)")
        return 0
    print(_format_table(header, table))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pivot",
        description="Build a pivot table from a CSV file.",
    )
    parser.add_argument("csv_file", help="Path to the input CSV file.")
    parser.add_argument(
        "--index",
        required=True,
        help="Column whose values form row groups.",
    )
    parser.add_argument(
        "--columns",
        required=True,
        help="Column whose values form column groups.",
    )
    parser.add_argument(
        "--values",
        required=True,
        help="Column whose values are aggregated.",
    )
    parser.add_argument(
        "--agg",
        default="sum",
        choices=sorted(stats.AGG_FUNCTIONS),
        help="Aggregation function (default: sum).",
    )
    parser.add_argument(
        "--fill",
        type=float,
        default=0.0,
        help="Fill value for missing combinations (default: 0.0).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse command line arguments and dispatch to :func:`run_pivot`."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_pivot(
        csv_path=args.csv_file,
        index_col=args.index,
        columns_col=args.columns,
        value_col=args.values,
        agg_func=args.agg,
        fill_value=args.fill,
    )


if __name__ == "__main__":
    sys.exit(main())

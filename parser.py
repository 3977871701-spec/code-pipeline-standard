"""CSV parser module.

Parses CSV text into ``list[dict[str, str]]``. Supports quoted fields,
escaped quotes (``""``), embedded newlines inside quoted fields, and
mixed line endings (``\n`` / ``\r\n``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


class CSVParseError(Exception):
    """Raised when CSV parsing fails.

    Attributes:
        line_number: 1-based line number where the error occurred. ``0`` for
            header-level errors.
        column_name: Optional column context.
        raw_line: Optional raw line content that triggered the error.
    """

    def __init__(
        self,
        message: str,
        line_number: int = 0,
        column_name: str | None = None,
        raw_line: str | None = None,
    ) -> None:
        self.line_number = line_number
        self.column_name = column_name
        self.raw_line = raw_line
        super().__init__(message)


def _split_records(text: str) -> list[str]:
    """Split CSV text into logical records, respecting quoted newlines.

    Returns a list of raw record strings (one per line in the original
    text, but records containing quoted newlines are merged).
    """
    records: list[str] = []
    buf: list[str] = []
    in_quotes = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            if in_quotes and i + 1 < n and text[i + 1] == '"':
                # Escaped quote inside quoted field.
                buf.append('"')
                buf.append('"')
                i += 2
                continue
            in_quotes = not in_quotes
            buf.append(ch)
            i += 1
            continue
        if not in_quotes and ch in ("\n", "\r"):
            # End of record.
            record = "".join(buf)
            # Drop trailing newline characters to compute a clean record.
            records.append(record)
            buf = []
            # Handle \r\n as a single line break.
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 2
            else:
                i += 1
            continue
        buf.append(ch)
        i += 1
    if buf or records:
        # Trailing content without newline still counts as a record.
        records.append("".join(buf))
    return records


def _parse_record(
    record: str,
    header: list[str],
    line_number: int,
) -> list[str]:
    """Parse a single record string into a list of field strings."""
    fields: list[str] = []
    cur: list[str] = []
    in_quotes = False
    i = 0
    n = len(record)
    while i < n:
        ch = record[i]
        if ch == '"':
            if in_quotes and i + 1 < n and record[i + 1] == '"':
                cur.append('"')
                i += 2
                continue
            in_quotes = not in_quotes
            i += 1
            continue
        if ch == "," and not in_quotes:
            fields.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if in_quotes:
        raise CSVParseError(
            f"Unclosed quote on line {line_number}",
            line_number=line_number,
            raw_line=record,
        )
    fields.append("".join(cur))
    return fields


def _normalize_newlines(text: str) -> str:
    # Normalize \r\n and bare \r to \n for consistent downstream handling.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _build_header(
    records: Iterable[str],
) -> tuple[list[str], list[str], int]:
    """Consume the first non-empty record as the header.

    Returns ``(header, remaining_records_as_list, header_line_number)``.
    """
    iterator = iter(records)
    for record in iterator:
        stripped = record
        if stripped == "":
            continue
        first = _parse_record(stripped, [], 1)
        header = [h.strip() for h in first]
        if not header or any(h == "" for h in header):
            raise CSVParseError(
                "CSV header is missing or contains empty column name",
                line_number=0,
                raw_line=stripped,
            )
        return header, list(iterator), 1
    raise CSVParseError(
        "CSV header is missing or contains empty column name",
        line_number=0,
    )


def parse_csv(text: str) -> list[dict[str, str]]:
    """Parse CSV text into a list of dictionaries keyed by header.

    Args:
        text: Full CSV text (UTF-8).

    Returns:
        List of dicts, one per data row. Values are raw string fields.

    Raises:
        CSVParseError: When the header is empty, a quote is unclosed, or
            a row's field count does not match the header.
    """
    if text is None:
        raise CSVParseError("CSV text is None", line_number=0)
    if text == "":
        return []
    normalized = _normalize_newlines(text)
    records = _split_records(normalized)
    if not records:
        return []
    header, rest, _ = _build_header(records)
    rows: list[dict[str, str]] = []
    for offset, record in enumerate(rest, start=2):
        if record == "":
            # Skip empty trailing lines silently.
            continue
        fields = _parse_record(record, header, offset)
        if len(fields) != len(header):
            raise CSVParseError(
                (
                    f"Field count mismatch on line {offset}: "
                    f"expected {len(header)}, got {len(fields)}"
                ),
                line_number=offset,
                raw_line=record,
            )
        rows.append({header[i]: fields[i] for i in range(len(header))})
    return rows


def parse_csv_file(path: str | Path) -> list[dict[str, str]]:
    """Read a file and parse its CSV content.

    Args:
        path: Filesystem path to a UTF-8 encoded CSV file.

    Returns:
        List of dict rows.

    Raises:
        FileNotFoundError: When the path does not exist.
        PermissionError: When the file cannot be read.
        CSVParseError: When parsing fails (including encoding errors).
    """
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}") from None
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}") from None
    except UnicodeDecodeError as exc:
        raise CSVParseError(
            f"Encoding error in {file_path}: {exc.reason}",
            line_number=0,
        ) from exc
    return parse_csv(raw)

"""Unit tests for parser module."""

from __future__ import annotations

import pytest

from parser import CSVParseError, parse_csv, parse_csv_file


class TestParseCsvNormal:
    """Tests for normal CSV parsing."""

    def test_simple_csv(self) -> None:
        text = "a,b,c\n1,2,3\n4,5,6\n"
        rows = parse_csv(text)
        assert rows == [
            {"a": "1", "b": "2", "c": "3"},
            {"a": "4", "b": "5", "c": "6"},
        ]

    def test_single_row(self) -> None:
        text = "name,age\nAlice,30\n"
        rows = parse_csv(text)
        assert rows == [{"name": "Alice", "age": "30"}]

    def test_empty_text_returns_empty_list(self) -> None:
        assert parse_csv("") == []

    def test_only_header_no_rows(self) -> None:
        text = "a,b,c\n"
        assert parse_csv(text) == []

    def test_header_whitespace_stripped(self) -> None:
        text = "  a  ,  b  ,  c  \n1,2,3\n"
        rows = parse_csv(text)
        assert rows == [{"a": "1", "b": "2", "c": "3"}]

    def test_values_keep_surrounding_whitespace(self) -> None:
        # Note: only header is stripped, values retain whitespace.
        text = "a,b\n  hello  ,world\n"
        rows = parse_csv(text)
        assert rows == [{"a": "  hello  ", "b": "world"}]

    def test_mixed_line_endings(self) -> None:
        text = "a,b\r\n1,2\r\n3,4\n5,6"
        rows = parse_csv(text)
        assert rows == [
            {"a": "1", "b": "2"},
            {"a": "3", "b": "4"},
            {"a": "5", "b": "6"},
        ]

    def test_bare_cr_line_endings(self) -> None:
        text = "a,b\r1,2\r3,4"
        rows = parse_csv(text)
        assert rows == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


class TestParseCsvQuoting:
    """Tests for quoted fields and quote escaping."""

    def test_quoted_field_with_comma(self) -> None:
        text = 'name,note\n"Smith, John","hello"\n'
        rows = parse_csv(text)
        assert rows == [
            {"name": "Smith, John", "note": "hello"},
        ]

    def test_escaped_double_quote(self) -> None:
        text = 'a,b\n"He said ""hi""",ok\n'
        rows = parse_csv(text)
        assert rows == [{"a": 'He said "hi"', "b": "ok"}]

    def test_empty_quoted_field(self) -> None:
        text = 'a,b\n"",x\n'
        rows = parse_csv(text)
        assert rows == [{"a": "", "b": "x"}]

    def test_quoted_field_with_special_chars(self) -> None:
        text = 'a,b\n"line1\nline2",x\n'
        rows = parse_csv(text)
        assert rows == [{"a": "line1\nline2", "b": "x"}]

    def test_multiline_quoted_field(self) -> None:
        text = 'name,bio\n"Alice","She said\nhi there"\n"Bob","plain"\n'
        rows = parse_csv(text)
        assert rows == [
            {"name": "Alice", "bio": "She said\nhi there"},
            {"name": "Bob", "bio": "plain"},
        ]


class TestParseCsvEmptyFields:
    """Tests for empty/unfilled fields."""

    def test_trailing_empty_field(self) -> None:
        text = "a,b,c\n1,2,\n"
        rows = parse_csv(text)
        assert rows == [{"a": "1", "b": "2", "c": ""}]

    def test_leading_empty_field(self) -> None:
        text = "a,b,c\n,2,3\n"
        rows = parse_csv(text)
        assert rows == [{"a": "", "b": "2", "c": "3"}]

    def test_multiple_empty_fields(self) -> None:
        text = "a,b,c\n,,\n1,,\n"
        rows = parse_csv(text)
        assert rows == [
            {"a": "", "b": "", "c": ""},
            {"a": "1", "b": "", "c": ""},
        ]

    def test_empty_trailing_line_is_skipped(self) -> None:
        text = "a,b\n1,2\n\n"
        rows = parse_csv(text)
        assert rows == [{"a": "1", "b": "2"}]


class TestParseCsvErrors:
    """Tests for parse error scenarios."""

    def test_field_count_mismatch_raises(self) -> None:
        text = "a,b,c\n1,2\n"
        with pytest.raises(CSVParseError) as exc:
            parse_csv(text)
        assert exc.value.line_number == 2
        assert "Field count mismatch" in str(exc.value)

    def test_unclosed_quote_raises(self) -> None:
        text = 'a,b\n"unterminated,1\n'
        with pytest.raises(CSVParseError) as exc:
            parse_csv(text)
        assert "Unclosed quote" in str(exc.value)

    def test_empty_header_column_raises(self) -> None:
        text = "a,,c\n1,2,3\n"
        with pytest.raises(CSVParseError) as exc:
            parse_csv(text)
        assert exc.value.line_number == 0

    def test_no_header_at_all_raises(self) -> None:
        with pytest.raises(CSVParseError):
            parse_csv("\n\n\n")

    def test_none_text_raises(self) -> None:
        with pytest.raises(CSVParseError):
            parse_csv(None)  # type: ignore[arg-type]


class TestParseCsvFile:
    """Tests for the file-based parse_csv_file helper."""

    def test_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_csv_file(tmp_path / "missing.csv")

    def test_round_trip(self, tmp_path) -> None:
        path = tmp_path / "data.csv"
        path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        rows = parse_csv_file(path)
        assert rows == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]

    def test_string_path(self, tmp_path) -> None:
        path = tmp_path / "data.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        rows = parse_csv_file(str(path))
        assert rows == [{"a": "1", "b": "2"}]

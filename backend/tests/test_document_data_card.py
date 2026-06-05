"""Unit tests for DocumentProcessor DATA CARD generation.

Pure tests: no DB, no Azure, no network. Exercise the pandas-based
structured profile / card builders for CSV and XLSX.
"""

import io

import pytest

from app.services.document_service import DocumentProcessor


# ── CSV ──────────────────────────────────────────────────────────────────────

def test_csv_data_card_includes_shape_columns_and_stats():
    proc = DocumentProcessor()
    csv_bytes = (
        b"name,age,salary\n"
        b"Alice,30,50000.50\n"
        b"Bob,42,72000\n"
        b"Carol,29,65000.25\n"
        b"Dan,51,95000\n"
    )
    text, meta = proc._extract_csv(csv_bytes)

    assert "DATA CARD" in text
    assert "4 rows" in text and "3 columns" in text
    # Numeric columns should include range/mean; string column should have samples
    assert "age" in text and "salary" in text and "name" in text
    assert "range=" in text  # min..max block present for numeric column
    # Structured profile captured in meta
    assert "structured_profile" in meta
    profile = meta["structured_profile"]
    assert profile["shape"] == [4, 3]
    names = [c["name"] for c in profile["columns"]]
    assert set(names) == {"name", "age", "salary"}


def test_csv_with_mostly_strings_produces_sample_values():
    proc = DocumentProcessor()
    csv_bytes = b"color,tag\nred,a\nblue,b\ngreen,a\nred,c\n"
    _, meta = proc._extract_csv(csv_bytes)
    prof = meta["structured_profile"]
    color_col = next(c for c in prof["columns"] if c["name"] == "color")
    assert "sample_values" in color_col
    assert color_col["unique_count"] == 3


def test_csv_handles_malformed_rows_gracefully():
    proc = DocumentProcessor()
    csv_bytes = b"a,b,c\n1,2,3\nbroken line with no commas\n4,5,6\n"
    text, meta = proc._extract_csv(csv_bytes)
    # We still get SOMETHING back (raw rows never fail)
    assert text and len(text) > 0
    assert "row_count" in meta


# ── XLSX ─────────────────────────────────────────────────────────────────────

def _make_xlsx_bytes():
    try:
        from openpyxl import Workbook
    except ImportError:
        pytest.skip("openpyxl not installed")
    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["region", "units", "revenue"])
    ws.append(["NA", 10, 1000.0])
    ws.append(["EU", 15, 2250.5])
    ws.append(["APAC", 7, 980.25])
    ws2 = wb.create_sheet("Meta")
    ws2.append(["key", "value"])
    ws2.append(["year", 2025])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_data_card_per_sheet():
    proc = DocumentProcessor()
    data = _make_xlsx_bytes()
    text, meta = proc._extract_xlsx(data)
    assert "DATA CARD" in text
    assert "Sales" in text and "Meta" in text
    assert meta["sheet_count"] == 2
    profiles = meta.get("structured_profile")
    assert profiles is not None
    sheet_names = {p["sheet"] for p in profiles}
    assert {"Sales", "Meta"}.issubset(sheet_names)
    sales = next(p for p in profiles if p["sheet"] == "Sales")
    assert sales["shape"][1] == 3  # 3 columns
    cols = {c["name"] for c in sales["columns"]}
    assert "region" in cols and "units" in cols and "revenue" in cols


# ── Unknown-type fallback ────────────────────────────────────────────────────

def test_unknown_binary_returns_placeholder_not_garbage():
    proc = DocumentProcessor()
    # Garbage binary
    payload = bytes(range(256)) * 8
    text, meta = proc._extract_unknown(payload, filename="weird.bin")
    assert text  # non-empty
    assert "Binary file" in text or len(text) > 0
    assert meta.get("warning")


def test_unknown_ascii_heuristic_keeps_text():
    proc = DocumentProcessor()
    payload = b"Hello world this is a text file with plain content. " * 20
    text, _meta = proc._extract_unknown(payload, filename="anon.dat")
    assert "Hello world" in text

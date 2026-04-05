"""indexing/excel_parser.py
--------------------------
Parse Word-style Excel documents into clean text for RAG ingestion.

A "Word-style" Excel file uses merged cells and indent columns as a document
outline, with actual data tables embedded inside.

Detection strategy
~~~~~~~~~~~~~~~~~~
1. Build a merge-map so every cell knows its logical span.
2. Classify each row as: SECTION / INDENT / TABLE_HDR / TABLE_BODY / EMPTY / MIXED.
3. Group consecutive TABLE_HDR / TABLE_BODY rows into table regions.
4. Walk rows sequentially:
   - Rows inside a region  → collect as table, render on region end.
   - Rows outside          → emit as section heading or paragraph text.

Public API
~~~~~~~~~~
    convert_workbook(wb, row_sentences, skip_boilerplate, extra_boilerplate_keys)
        -> str

    Called by ExcelReader with an already-open openpyxl Workbook so the file
    is not loaded twice.  row_sentences=False → Markdown tables (Solr fulltext).
    row_sentences=True  → one sentence per row, forward-filled (Qdrant embeddings).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from openpyxl.worksheet.worksheet import Worksheet


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class _MergeSpan:
    min_row: int
    max_row: int
    min_col: int
    max_col: int


@dataclass
class _TableRegion:
    min_row: int
    max_row: int
    min_col: int
    max_col: int


# Row type constants (private)
_EMPTY = "EMPTY"
_SECTION = "SECTION"
_INDENT = "INDENT"
_TABLE_HDR = "TABLE_HDR"
_TABLE_BODY = "TABLE_BODY"
_MIXED = "MIXED"


# ---------------------------------------------------------------------------
# Merge map
# ---------------------------------------------------------------------------


def _build_merge_map(ws: Worksheet) -> Dict[Tuple[int, int], _MergeSpan]:
    merge_map: Dict[Tuple[int, int], _MergeSpan] = {}
    for rng in ws.merged_cells.ranges:
        span = _MergeSpan(rng.min_row, rng.max_row, rng.min_col, rng.max_col)
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                merge_map[(r, c)] = span
    return merge_map


# ---------------------------------------------------------------------------
# Cell-level helpers
# ---------------------------------------------------------------------------


def _has_border(cell) -> bool:
    bd = cell.border
    if bd is None:
        return False
    for side in (bd.left, bd.right, bd.top, bd.bottom):
        if side and side.border_style and side.border_style != "none":
            return True
    return False


def _has_fill(cell) -> bool:
    fill = cell.fill
    if fill is None or fill.fill_type in (None, "none"):
        return False
    if fill.fill_type == "solid":
        fg = fill.fgColor
        if fg is None:
            return False
        if fg.type == "rgb" and fg.rgb.upper() in ("FFFFFFFF", "00000000", "00FFFFFF"):
            return False
        return True
    return fill.fill_type != "none"


def _is_bold(cell) -> bool:
    font = cell.font
    return bool(font and font.bold)


def _indent(cell) -> int:
    al = cell.alignment
    return al.indent if al and al.indent else 0


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------


def _classify_row(
    ws: Worksheet,
    row_idx: int,
    merge_map: Dict[Tuple[int, int], _MergeSpan],
    total_cols: int,
) -> str:
    non_empty = [c for c in ws[row_idx] if c.value is not None and str(c.value).strip()]
    if not non_empty:
        return _EMPTY

    # Full-width merge → section header
    spans_in_row: Set[Tuple] = set()
    for c_idx in range(1, total_cols + 1):
        span = merge_map.get((row_idx, c_idx))
        if span and span.min_row == row_idx:
            spans_in_row.add((span.min_row, span.min_col, span.max_row, span.max_col))
    for (_, mc, _, xc) in spans_in_row:
        if (xc - mc + 1) >= max(2, total_cols * 0.6):
            return _SECTION

    col_indices = [c.column for c in non_empty]
    max_col_with_data = max(col_indices)

    if max_col_with_data <= 2:
        first_cell = ws.cell(row=row_idx, column=col_indices[0])
        if _indent(first_cell) >= 1 or max_col_with_data == 1:
            if not all(_has_border(ws.cell(row=row_idx, column=c)) for c in col_indices):
                return _INDENT

    bold_count = sum(1 for c in non_empty if _is_bold(c))
    fill_count = sum(1 for c in non_empty if _has_fill(c))
    border_count = sum(1 for c in non_empty if _has_border(c))
    distinct_cols = len(set(c.column for c in non_empty))

    if distinct_cols < 2:
        if bold_count or fill_count:
            return _SECTION
        return _INDENT

    header_score = (bold_count / len(non_empty)) + (fill_count / len(non_empty))
    if header_score >= 0.5:
        return _TABLE_HDR
    if border_count >= 2 and distinct_cols >= 2:
        return _TABLE_BODY
    if distinct_cols >= 3:
        return _TABLE_BODY
    return _MIXED


# ---------------------------------------------------------------------------
# Column footprint
# ---------------------------------------------------------------------------


def _row_footprint(
    ws: Worksheet,
    row_idx: int,
    merge_map: Dict[Tuple[int, int], _MergeSpan],
) -> Tuple[int, int]:
    occupied: List[int] = []
    for cell in ws[row_idx]:
        if cell.value is not None and str(cell.value).strip():
            span = merge_map.get((row_idx, cell.column))
            if span:
                occupied.extend([span.min_col, span.max_col])
            else:
                occupied.append(cell.column)
        elif _has_border(cell):
            occupied.append(cell.column)
    if not occupied:
        return (0, 0)
    return (min(occupied), max(occupied))


# ---------------------------------------------------------------------------
# Table region detection
# ---------------------------------------------------------------------------


def _detect_regions(ws: Worksheet) -> List[_TableRegion]:
    if ws.max_row is None or ws.max_column is None:
        return []

    total_rows = ws.max_row
    total_cols = ws.max_column
    merge_map = _build_merge_map(ws)

    row_types: Dict[int, str] = {
        r: _classify_row(ws, r, merge_map, total_cols) for r in range(1, total_rows + 1)
    }

    # Group consecutive table rows
    candidates: List[Tuple[int, int]] = []
    in_table = False
    table_start = 0
    consecutive_empty = 0

    for r in range(1, total_rows + 2):
        rtype = row_types.get(r, _EMPTY)
        if not in_table:
            if rtype in (_TABLE_HDR, _TABLE_BODY):
                in_table = True
                table_start = r
                consecutive_empty = 0
        else:
            if rtype == _EMPTY:
                consecutive_empty += 1
                if consecutive_empty > 1:
                    table_end = r - consecutive_empty
                    if table_end >= table_start:
                        candidates.append((table_start, table_end))
                    in_table = False
                    consecutive_empty = 0
            elif rtype in (_TABLE_HDR, _TABLE_BODY):
                consecutive_empty = 0
            else:
                table_end = r - 1 - consecutive_empty
                if table_end >= table_start:
                    candidates.append((table_start, table_end))
                in_table = False
                consecutive_empty = 0

    # Determine exact column bounds per candidate
    regions: List[_TableRegion] = []
    for (rstart, rend) in candidates:
        if rend - rstart < 1:
            continue
        all_min: List[int] = []
        all_max: List[int] = []
        for r in range(rstart, rend + 1):
            fp = _row_footprint(ws, r, merge_map)
            if fp != (0, 0):
                all_min.append(fp[0])
                all_max.append(fp[1])
        if not all_min:
            continue
        min_col, max_col = min(all_min), max(all_max)
        if max_col - min_col < 1:
            continue
        regions.append(_TableRegion(min_row=rstart, max_row=rend, min_col=min_col, max_col=max_col))

    return regions


# ---------------------------------------------------------------------------
# Row value extraction
# ---------------------------------------------------------------------------


def _cell_str(cell) -> str:
    v = cell.value
    if v is None:
        return ""
    return str(v).replace("\n", " ").replace("\r", "").strip()


def _row_values(
    ws: Worksheet,
    row_idx: int,
    min_col: int,
    max_col: int,
    merge_map: Dict,
) -> List[str]:
    """Extract string values — top-left of merge emits value, continuations emit ''."""
    out: List[str] = []
    c = min_col
    while c <= max_col:
        span = merge_map.get((row_idx, c))
        if span:
            if span.min_row == row_idx and span.min_col == c:
                out.append(_cell_str(ws.cell(row=row_idx, column=c)))
                c = span.max_col + 1
            else:
                out.append("")
                c += 1
        else:
            out.append(_cell_str(ws.cell(row=row_idx, column=c)))
            c += 1
    return out


def _row_ffill(
    ws: Worksheet,
    row_idx: int,
    min_col: int,
    max_col: int,
    merge_map: Dict,
) -> List[str]:
    """Fixed-width extraction with vertical forward-fill for merged cells."""
    out: List[str] = []
    for c in range(min_col, max_col + 1):
        span = merge_map.get((row_idx, c))
        if span:
            if span.min_row == row_idx and span.min_col == c:
                out.append(_cell_str(ws.cell(row=row_idx, column=c)))
            elif span.min_col == c:
                out.append(_cell_str(ws.cell(row=span.min_row, column=span.min_col)))
            else:
                out.append("")
        else:
            out.append(_cell_str(ws.cell(row=row_idx, column=c)))
    return out


def _first_value(ws: Worksheet, row_idx: int) -> str:
    for cell in ws[row_idx]:
        v = _cell_str(cell)
        if v:
            return v
    return ""


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _trim_cols(rows: List[List[str]]) -> List[List[str]]:
    if not rows:
        return rows
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]

    min_used = ncols
    for row in rows:
        for i, v in enumerate(row):
            if v:
                min_used = min(min_used, i)
                break

    max_used = 0
    for row in rows:
        for i in range(len(row) - 1, -1, -1):
            if row[i]:
                max_used = max(max_used, i)
                break

    if min_used > max_used:
        return []
    return [row[min_used: max_used + 1] for row in rows if any(row[min_used: max_used + 1])]


def _to_markdown(rows: List[List[str]]) -> str:
    rows = _trim_cols(rows)
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    widths = [max(max(len(rows[ri][ci]) for ri in range(len(rows))), 1) for ci in range(ncols)]

    def fmt(r: List[str]) -> str:
        return "| " + " | ".join(r[i].ljust(widths[i]) for i in range(ncols)) + " |"

    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    return "\n".join([fmt(rows[0]), sep] + [fmt(r) for r in rows[1:]])


def _to_sentences(rows: List[List[str]]) -> str:
    rows = _trim_cols(rows)
    if len(rows) < 2:
        return ""
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    headers = rows[0]
    sentences: List[str] = []
    for row in rows[1:]:
        parts = [f"{h}: {v}" if h else v for h, v in zip(headers, row) if v]
        if parts:
            sentences.append(" / ".join(parts))
    return "\n".join(sentences)


# ---------------------------------------------------------------------------
# Boilerplate detection
# ---------------------------------------------------------------------------

_DEFAULT_BOILERPLATE_KEYS = {"PJ名", "システム名"}


def _auto_detect_boilerplate(wb) -> set:
    """Values that appear in the first 5 rows of 3+ sheets → document header boilerplate."""
    sheet_seen: Dict = {}
    for ws in wb.worksheets:
        if not ws.max_row:
            continue
        merge_map = _build_merge_map(ws)
        total_cols = ws.max_column or 1
        for r in range(1, min(6, ws.max_row + 1)):
            vals = _row_values(ws, r, 1, total_cols, merge_map)
            sig = frozenset(v for v in vals if v)
            if not sig:
                continue
            sheet_seen.setdefault(sig, set()).add(ws.title)

    keys: set = set()
    for sig, sheets in sheet_seen.items():
        if len(sheets) >= 3:
            keys.update(sig)
    return keys or _DEFAULT_BOILERPLATE_KEYS


def _is_boilerplate(vals: List[str], keys: set) -> bool:
    return any(v in keys for v in vals)


# ---------------------------------------------------------------------------
# Sheet → text
# ---------------------------------------------------------------------------


def _sheet_to_text(
    ws: Worksheet,
    boilerplate_keys: set,
    skip_boilerplate: bool,
    row_sentences: bool,
) -> str:
    if not ws.max_row or not ws.max_column:
        return ""

    merge_map = _build_merge_map(ws)
    total_cols = ws.max_column
    regions = _detect_regions(ws)

    row_to_region: Dict[int, _TableRegion] = {}
    for region in regions:
        for r in range(region.min_row, region.max_row + 1):
            row_to_region[r] = region

    lines: List[str] = []
    active_region: Optional[_TableRegion] = None
    table_accum: List[List[str]] = []

    def flush() -> None:
        nonlocal active_region, table_accum
        if table_accum:
            filtered = (
                [row for row in table_accum if not _is_boilerplate(row, boilerplate_keys)]
                if skip_boilerplate
                else table_accum
            )
            if filtered:
                rendered = _to_sentences(filtered) if row_sentences else _to_markdown(filtered)
                if rendered:
                    lines.append(rendered)
                    lines.append("")
        active_region = None
        table_accum = []

    for r in range(1, ws.max_row + 1):
        region = row_to_region.get(r)

        if region is not None:
            if active_region is not region:
                flush()
                active_region = region
            vals = (
                _row_ffill(ws, r, region.min_col, region.max_col, merge_map)
                if row_sentences
                else _row_values(ws, r, region.min_col, region.max_col, merge_map)
            )
            table_accum.append(vals)
            if r == region.max_row:
                flush()
        else:
            if active_region is not None:
                flush()

            vals = _row_values(ws, r, 1, total_cols, merge_map)
            if skip_boilerplate and _is_boilerplate(vals, boilerplate_keys):
                continue

            rtype = _classify_row(ws, r, merge_map, total_cols)
            if rtype == _EMPTY:
                continue
            elif rtype == _SECTION:
                val = _first_value(ws, r)
                if val:
                    lines.append(f"### {val}")
                    lines.append("")
            else:
                val = " ".join(v for v in vals if v)
                if val:
                    lines.append(val)

    flush()
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert_workbook(
    wb,
    row_sentences: bool = False,
    skip_boilerplate: bool = True,
    extra_boilerplate_keys: List[str] = None,
) -> str:
    """Convert an openpyxl Workbook to plain text for RAG ingestion.

    Args:
        wb: openpyxl Workbook (opened with data_only=True).
        row_sentences: True  → one sentence per table row, forward-filled merged
                               cells. Optimal for dense-vector (Qdrant) embeddings.
                       False → Markdown tables. Optimal for Solr fulltext search.
        skip_boilerplate: Remove repeated document-header rows auto-detected
                          across sheets.
        extra_boilerplate_keys: Additional cell values to treat as boilerplate.

    Returns:
        Full document text across all sheets, with ``[Sheet: name]`` separators
        and ``### Section`` markers for heading-aware downstream chunking.
    """
    if skip_boilerplate:
        keys = _auto_detect_boilerplate(wb)
        if extra_boilerplate_keys:
            keys = keys | set(extra_boilerplate_keys)
    else:
        keys = set()

    parts: List[str] = []
    for ws in wb.worksheets:
        text = _sheet_to_text(ws, boilerplate_keys=keys, skip_boilerplate=skip_boilerplate, row_sentences=row_sentences)
        if text:
            parts.extend([f"[Sheet: {ws.title}]", "", text, ""])

    return "\n".join(parts).strip()

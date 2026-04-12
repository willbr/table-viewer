# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A fast CSV viewer desktop application built with Tkinter and pandas. Designed to handle large CSV files (100k+ rows) using virtualized rendering, pool-based canvas management, and two-stage loading.

## Running

```bash
uv run main.py                        # launch empty
uv run main.py path/to/file.csv       # open a file directly
uv run main.py --debug-colors         # debug mode
```

Uses `uv` for dependency management. Python 3.13+ required. Single dependency: pandas. Optional: `tkinterdnd2` for drag-and-drop file opening.

## Architecture

The entire application is a single file (`main.py`) containing `CSVViewer` (main app) and `FilterDialog` (custom filter UI).

**Two-stage CSV loading** (`_load_csv_data`): Loads 200 rows for instant preview, then streams the rest in 50k-row chunks. Encoding is auto-detected (utf-8-sig, latin-1 fallback). After full load, `_optimize_dtypes` converts low-cardinality strings to `pd.Categorical` and downcasts numeric columns.

**Pool-based canvas rendering** (`redraw_canvas`): Pre-allocates canvas items (text, lines, rects) in `_rebuild_pool`, then updates them via `itemconfig`/`coords` instead of deleting and recreating per frame. Pool grows automatically when the viewport resizes.

**Multi-canvas layout**: Four synchronized canvases in a grid — row number gutter, frozen columns, main data area, plus matching headers. Vertical scroll syncs gutter + frozen + main. Horizontal scroll only affects main area + header.

**Column offsets** (`_col_offsets`): Prefix-sum array of column widths, recomputed via `_update_col_offsets()` on any width change. Used for O(log n) column hit-testing with `bisect`.

**Format cache** (`_get_formatted_data`): Pre-formats visible cell values (number formatting, NaN handling) and caches by `(start_row, end_row, _data_version)`. `_data_version` increments on load/sort/filter. Horizontal scrolling reuses the cache.

**Sorting and filtering** run on background threads with results marshalled to the main thread via `self.after(0, ...)`. Filter supports regex and column-specific search via `FilterDialog`.

**Frozen columns**: Right-click header to freeze/unfreeze. Frozen columns render in a separate canvas that doesn't scroll horizontally. `_update_frozen_layout()` shows/hides the frozen canvases via grid.

**Text clipping**: `_clip_text` uses average character width estimate for fast truncation with Unicode ellipsis, avoiding per-cell `font.measure()` calls.

**Config persistence**: Window geometry saved to `~/.csv-viewer.json` on close, restored on open.

## Test Data

`generate_test_csv.py` creates test CSV files of various sizes (1k-1M rows). These are gitignored.

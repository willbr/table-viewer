# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A fast CSV viewer desktop application built with Tkinter and pandas. Designed to handle large CSV files (100k+ rows) using virtualized rendering and two-stage loading.

## Running

```bash
uv run main.py                        # launch empty
uv run main.py path/to/file.csv       # open a file directly
uv run main.py --debug-colors         # debug mode
```

Uses `uv` for dependency management. Python 3.13+ required. Single dependency: pandas.

## Architecture

The entire application is a single file (`main.py`) containing one class: `CSVViewer(tk.Tk)`.

**Two-stage CSV loading** (`_load_csv_data`): First loads 200 rows for an instant preview, then streams the rest in 50k-row chunks via `pd.read_csv` with chunked iteration. All file I/O runs on background threads; UI updates are marshalled back via `self.after(0, ...)`.

**Virtualized canvas rendering** (`redraw_canvas`): Only draws rows/columns visible in the viewport. The canvas is manually managed (not a Treeview) — text items, grid lines, and selection rectangles are created each frame via `canvas.create_text`/`create_line`/`create_rectangle`.

**Column type detection** (`_detect_column_types`): Infers int/float/datetime/text from pandas dtypes. Numeric columns get right-aligned monospace font with comma formatting; text columns get left-aligned proportional font.

**Sorting and filtering** run on background threads (`_perform_sort`, `_perform_filter`) to keep the UI responsive. Filter searches all columns using vectorized pandas string operations.

**Column resizing**: Drag column borders in the header. Double-click a border to auto-fit. State machine in `_on_header_press`/`_on_header_drag`/`_on_header_release` distinguishes resize drags from sort clicks from double-click auto-fit.

**Theme**: Detects macOS dark mode via `defaults read -g AppleInterfaceStyle` at startup; falls back to light theme.

## Test Data

`generate_test_csv.py` creates test CSV files of various sizes (1k–1M rows). These are gitignored.

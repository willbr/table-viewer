# Table Viewer

A fast desktop viewer for CSV and SQLite files. Opens large files (1M+ rows) instantly with two-stage loading and fixed-position virtualized rendering.

## Usage

```
uv run main.py                     # launch empty
uv run main.py data.csv            # open a CSV file
uv run main.py database.db         # open a SQLite file
```

Or `make run` to launch.

## Features

- **CSV and SQLite** — auto-detects format from extension. SQLite files show tabs to switch tables.
- **Fast loading** — preview appears instantly, full data loads in the background.
- **Virtualized rendering** — fixed-position canvas pool. Scrolling only updates text content, no item creation or movement.
- **Sort** — click column headers. Numeric, text, and datetime sorting.
- **Filter** — Cmd/Ctrl+F. Supports regex and column-specific search.
- **Keyboard navigation** — arrow keys, Page Up/Down, Home/End.
- **Column resize** — drag column borders. Double-click to auto-fit.
- **Export** — save the current filtered view to a new CSV.
- **Dark mode** — auto-detects macOS dark mode.

## Requirements

- Python 3.13+
- pandas

## Test data

```
python scripts/generate_test_csv.py test.csv 100000
python scripts/generate_test_sqlite.py test.db 100000
```

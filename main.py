import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkFont
import threading
import bisect
import sys
import os
import subprocess
import time
import json
import pandas as pd

# Optional drag-and-drop support
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _TkBase = TkinterDnD.Tk
except ImportError:
    _TkBase = tk.Tk

# High DPI support for Windows
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

CONFIG_PATH = os.path.expanduser("~/.csv-viewer.json")


class FilterDialog(tk.Toplevel):
    """Custom filter dialog with column selection and regex support."""

    def __init__(self, parent, columns):
        super().__init__(parent)
        self.title("Find/Filter")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None

        # Center on parent
        self.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 80}")

        tk.Label(self, text="Search:").grid(row=0, column=0, padx=8, pady=(10, 4), sticky="w")
        self.entry = tk.Entry(self, width=36)
        self.entry.grid(row=0, column=1, columnspan=2, padx=8, pady=(10, 4), sticky="ew")
        self.entry.focus_set()

        tk.Label(self, text="In column:").grid(row=1, column=0, padx=8, pady=4, sticky="w")
        self.column_var = tk.StringVar(value="All columns")
        combo = ttk.Combobox(self, textvariable=self.column_var,
                             values=["All columns"] + list(columns), state="readonly")
        combo.grid(row=1, column=1, columnspan=2, padx=8, pady=4, sticky="ew")

        self.regex_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="Regular expression", variable=self.regex_var).grid(
            row=2, column=0, columnspan=3, padx=8, pady=4, sticky="w")

        btn_frame = tk.Frame(self)
        btn_frame.grid(row=3, column=0, columnspan=3, pady=(4, 10))
        tk.Button(btn_frame, text="Filter", width=10, command=self._ok).pack(side="left", padx=4)
        tk.Button(btn_frame, text="Cancel", width=10, command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _ok(self):
        term = self.entry.get()
        if term:
            col = self.column_var.get()
            self.result = (term, None if col == "All columns" else col, self.regex_var.get())
        self.destroy()


class CSVViewer(_TkBase):
    """
    A fast CSV viewer with virtualized pool-based canvas rendering,
    frozen columns, row gutter, keyboard navigation, and more.
    """

    def __init__(self, debug_colors=False):
        super().__init__()
        self.title("Fast CSV Viewer")
        self.modifier = "Command" if sys.platform == "darwin" else "Control"
        self.debug_colors = debug_colors

        self._setup_theme_colors()

        # Fonts
        self.default_font = tkFont.Font(
            family="Segoe UI" if os.name == "nt" else "Helvetica", size=10)
        self.mono_font = tkFont.Font(
            family="Consolas" if os.name == "nt" else "Courier New", size=10)
        self.header_font = tkFont.Font(
            family="Segoe UI" if os.name == "nt" else "Helvetica", size=10, weight="bold")

        # Precompute average char widths for clipping
        self._default_char_width = self.default_font.measure("x")
        self._mono_char_width = self.mono_font.measure("0")

        # --- Data state ---
        self.file_path = None
        self.original_data = pd.DataFrame()
        self.view_df = pd.DataFrame()
        self.column_names = []
        self.sort_info = {"col_index": None, "ascending": True}
        self.col_alignments = {}
        self.col_types = {}
        self.selected_cell = {"row": None, "col": None}

        # --- Virtual grid ---
        self.row_height = 25
        self.col_widths = {}
        self._col_offsets = [0]  # prefix-sum of col widths
        self.total_rows = 0
        self.features_ready = False
        self._redraw_job = None
        self._data_version = 0
        self._format_cache = {"start": -1, "end": -1, "ver": -1, "data": []}

        # --- Threads ---
        self.sort_thread = None
        self.filter_thread = None
        self.auto_fit_thread = None

        # --- Column resizing state ---
        self.resizing_col_index = None
        self.resize_start_x = 0
        self.initial_col_width = 0
        self.potential_sort_click = False
        self.last_press_time = 0
        self.last_press_col = None
        self._resize_header_source = None

        # --- Frozen columns ---
        self.frozen_count = 0

        # --- Gutter ---
        self._gutter_width = 50

        # --- Pool (main canvas) ---
        self._pool_n_rows = 0
        self._pool_n_cols = 0
        self._pool_stripes = []
        self._pool_sel = None
        self._pool_vlines = []
        self._pool_hlines = []
        self._pool_texts = []

        # --- Window geometry ---
        self._load_config()

        self.config(bg=self.BACKGROUND_COLOR)
        self._create_menu()
        self._create_widgets()
        self._setup_dnd()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================
    # Config persistence
    # ================================================================

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            geo = cfg.get("geometry")
            if geo:
                self.geometry(geo)
                return
        except Exception:
            pass
        # Default geometry
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = int(sw * 0.75), int(sh * 0.8)
        x, y = (sw - w) // 2, (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _save_config(self):
        try:
            cfg = {"geometry": self.geometry()}
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f)
        except Exception:
            pass

    # ================================================================
    # Theme
    # ================================================================

    def _setup_theme_colors(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        is_dark = False
        if sys.platform == "darwin":
            try:
                r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                   capture_output=True, text=True)
                if r.stdout.strip() == "Dark":
                    is_dark = True
            except Exception:
                pass

        if is_dark:
            self.BACKGROUND_COLOR = "#2e2e2e"
            self.FOREGROUND_COLOR = "#dcdcdc"
            self.SELECTION_COLOR = "#4a6fa5"
            self.GRID_LINE_COLOR = "#4a4a4a"
            self.HEADER_BG = "#404040"
            self.HEADER_FG = "#ffffff"
            self.STRIPE_COLOR = "#353535"
            self.GUTTER_BG = "#383838"
        else:
            self.BACKGROUND_COLOR = "#ffffff"
            self.FOREGROUND_COLOR = "#000000"
            self.SELECTION_COLOR = "#a6d1ff"
            self.GRID_LINE_COLOR = "#e0e0e0"
            self.HEADER_BG = "#e1e1e1"
            self.HEADER_FG = "#000000"
            self.STRIPE_COLOR = "#f7f7f7"
            self.GUTTER_BG = "#f0f0f0"

    # ================================================================
    # Menu
    # ================================================================

    def _create_menu(self):
        menu_bar = tk.Menu(self)
        self.config(menu=menu_bar)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Open...", command=self.open_file,
                              accelerator=f"{self.modifier}+O")
        self.export_menu_index = 1
        file_menu.add_command(label="Export View...", command=self.export_filtered,
                              state="disabled")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menu_bar.add_cascade(label="File", menu=file_menu)
        self.file_menu = file_menu

        self.edit_menu = tk.Menu(menu_bar, tearoff=0)
        self.edit_menu.add_command(label="Find/Filter...", command=self.find_data,
                                   accelerator=f"{self.modifier}+F", state="disabled")
        self.edit_menu.add_command(label="Clear Filter", command=self.clear_filter,
                                   accelerator="Esc", state="disabled")
        self.edit_menu.add_command(label="Go to Row...", command=self.go_to_line,
                                   accelerator=f"{self.modifier}+G")
        self.edit_menu.add_separator()
        self.edit_menu.add_command(label="Copy Cell", command=self.copy_selection,
                                   accelerator=f"{self.modifier}+C")
        menu_bar.add_cascade(label="Edit", menu=self.edit_menu)

        view_menu = tk.Menu(menu_bar, tearoff=0)
        view_menu.add_command(label="Freeze Columns...", command=self._freeze_dialog,
                              state="disabled")
        view_menu.add_command(label="Unfreeze All", command=self._unfreeze_columns,
                              state="disabled")
        menu_bar.add_cascade(label="View", menu=view_menu)
        self.view_menu = view_menu

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)

        # Key bindings
        self.bind(f"<{self.modifier}-o>", lambda e: self.open_file())
        self.bind(f"<{self.modifier}-f>", lambda e: self.find_data())
        self.bind(f"<{self.modifier}-g>", lambda e: self.go_to_line())
        self.bind(f"<{self.modifier}-c>", lambda e: self.copy_selection())
        self.bind("<Escape>", lambda e: self.clear_filter())

        # Keyboard navigation
        for key in ("Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"):
            self.bind(f"<{key}>", self._on_key_nav)
        self.bind("<Tab>", self._on_key_nav)
        self.bind("<Shift-Tab>", self._on_key_nav)

    # ================================================================
    # Widgets
    # ================================================================

    def _create_widgets(self):
        # Layout:
        #          col0            col1           col2         col3
        # row0  [gutter_hdr]   [frozen_hdr]   [main_hdr]
        # row1  [gutter_cvs]   [frozen_cvs]   [main_cvs]    [vsb]
        # row2                                 [hsb]
        # row3  [status_bar spanning all]

        main_frame = tk.Frame(self, bg=self.BACKGROUND_COLOR)
        main_frame.pack(fill="both", expand=True)
        main_frame.grid_rowconfigure(1, weight=1)
        main_frame.grid_columnconfigure(2, weight=1)

        # --- Row 0: Headers ---
        self.gutter_header = tk.Canvas(main_frame, height=30, bd=0,
                                        highlightthickness=0, bg=self.HEADER_BG,
                                        width=self._gutter_width)
        self.gutter_header.grid(row=0, column=0, sticky="nsew")

        self.frozen_header = tk.Canvas(main_frame, height=30, bd=0,
                                        highlightthickness=0, bg=self.HEADER_BG,
                                        width=0)
        # Hidden initially (no frozen cols)

        self.header_canvas = tk.Canvas(main_frame, height=30, bd=0,
                                        highlightthickness=0, bg=self.HEADER_BG)
        self.header_canvas.grid(row=0, column=2, sticky="ew")

        # --- Row 1: Data canvases ---
        self.gutter_canvas = tk.Canvas(main_frame, bg=self.GUTTER_BG,
                                        highlightthickness=0, bd=0,
                                        width=self._gutter_width)
        self.gutter_canvas.grid(row=1, column=0, sticky="ns")

        self.frozen_canvas = tk.Canvas(main_frame, bg=self.BACKGROUND_COLOR,
                                        highlightthickness=0, bd=0, width=0)
        # Hidden initially

        self.canvas = tk.Canvas(main_frame, bg=self.BACKGROUND_COLOR,
                                 highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=2, sticky="nsew")

        # Scrollbars
        self.vsb = ttk.Scrollbar(main_frame, orient="vertical", command=self.on_vscroll)
        self.hsb = ttk.Scrollbar(main_frame, orient="horizontal", command=self.on_hscroll)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        self.vsb.grid(row=1, column=3, sticky="ns")
        self.hsb.grid(row=2, column=2, sticky="ew")

        # --- Row 3: Status bar ---
        self.status_bar = ttk.Label(self, text="Ready", anchor=tk.W,
                                     padding=(5, 2), relief=tk.SUNKEN)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # --- Bindings ---
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mousewheel on all data canvases
        for cvs in (self.canvas, self.gutter_canvas, self.frozen_canvas):
            cvs.bind("<MouseWheel>", self._on_mousewheel)
            cvs.bind("<Button-4>", self._on_mousewheel)
            cvs.bind("<Button-5>", self._on_mousewheel)

        # Cell click on data canvases
        self.canvas.bind("<Button-1>", self._on_cell_click)
        self.frozen_canvas.bind("<Button-1>", self._on_cell_click)

        # Header: resize / sort / right-click
        for hdr in (self.header_canvas, self.frozen_header):
            hdr.bind("<Motion>", self._on_header_motion)
            hdr.bind("<ButtonPress-1>", self._on_header_press)
            hdr.bind("<B1-Motion>", self._on_header_drag)
            hdr.bind("<ButtonRelease-1>", self._on_header_release)
            if sys.platform == "darwin":
                hdr.bind("<Button-2>", self._on_header_right_click)
                hdr.bind("<Control-Button-1>", self._on_header_right_click)
            else:
                hdr.bind("<Button-3>", self._on_header_right_click)

    # ================================================================
    # Drag-and-drop
    # ================================================================

    def _setup_dnd(self):
        if not hasattr(self, "drop_target_register"):
            return
        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        path = event.data.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        if os.path.isfile(path):
            self.load_file_from_path(path)

    # ================================================================
    # Scrolling
    # ================================================================

    def _schedule_redraw(self):
        if self._redraw_job:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(8, self._perform_redraw)

    def _perform_redraw(self):
        self._redraw_job = None
        self._redraw_all()

    def _redraw_all(self):
        self._redraw_gutter_header()
        self._redraw_frozen_header()
        self._redraw_main_header()
        self._redraw_gutter()
        self._redraw_frozen_data()
        self.redraw_canvas()

    def on_vscroll(self, *args):
        self.canvas.yview(*args)
        self.gutter_canvas.yview(*args)
        if self.frozen_count > 0:
            self.frozen_canvas.yview(*args)
        self._schedule_redraw()

    def on_hscroll(self, *args):
        self.canvas.xview(*args)
        self.header_canvas.xview(*args)
        self._schedule_redraw()

    def _on_mousewheel(self, event):
        if sys.platform == "darwin":
            delta = -int(event.delta)
        elif event.num == 5 or event.delta < 0:
            delta = 3
        elif event.num == 4 or event.delta > 0:
            delta = -3
        else:
            return
        self.canvas.yview_scroll(delta, "units")
        self.gutter_canvas.yview_scroll(delta, "units")
        if self.frozen_count > 0:
            self.frozen_canvas.yview_scroll(delta, "units")
        self._schedule_redraw()

    def _on_canvas_configure(self, event=None):
        self._schedule_redraw()

    # ================================================================
    # Data loading
    # ================================================================

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.load_file_from_path(path)

    def load_file_from_path(self, file_path):
        if not os.path.exists(file_path):
            messagebox.showerror("Error", f"File not found:\n{file_path}")
            return
        self._reset_state()
        self.file_path = file_path
        self.title(f"Fast CSV Viewer - {os.path.basename(file_path)}")
        self.status_bar.config(text=f"Opening {file_path}...")
        threading.Thread(target=self._load_csv_data, daemon=True).start()

    def _reset_state(self):
        self.original_data = pd.DataFrame()
        self.view_df = pd.DataFrame()
        self.column_names = []
        self.sort_info = {"col_index": None, "ascending": True}
        self.features_ready = False
        self.frozen_count = 0
        self.selected_cell = {"row": None, "col": None}
        self._data_version += 1
        self._pool_n_rows = 0
        self._pool_n_cols = 0
        self.last_press_time = 0
        self.last_press_col = None
        self.edit_menu.entryconfig("Find/Filter...", state="disabled")
        self.edit_menu.entryconfig("Clear Filter", state="disabled")
        self.file_menu.entryconfig(self.export_menu_index, state="disabled")
        self.view_menu.entryconfig("Freeze Columns...", state="disabled")
        self.view_menu.entryconfig("Unfreeze All", state="disabled")
        self.canvas.delete("all")
        self.header_canvas.delete("all")
        self.gutter_canvas.delete("all")
        self.gutter_header.delete("all")
        self.frozen_canvas.delete("all")
        self.frozen_header.delete("all")
        self._update_frozen_layout()

    def _detect_encoding(self):
        try:
            with open(self.file_path, "r", encoding="utf-8-sig") as f:
                f.read(8192)
            return "utf-8-sig"
        except UnicodeDecodeError:
            return "latin-1"

    def _load_csv_data(self):
        INITIAL_ROWS = 200
        CHUNK_SIZE = 50000

        try:
            encoding = self._detect_encoding()

            # Stage 1: fast preview
            preview = pd.read_csv(self.file_path, nrows=INITIAL_ROWS,
                                   encoding=encoding, on_bad_lines="skip", engine="c")
            col_names = preview.columns.tolist()
            self._detect_column_types(preview)
            self._estimate_col_widths(preview)
            self._update_col_offsets()

            def _apply_preview():
                self.column_names = col_names
                self.original_data = preview
                self.view_df = preview
                self.total_rows = len(preview)
                self._data_version += 1
                self._update_gutter_width()
                self.setup_display()
                self.status_bar.config(text="Loaded preview. Reading full file...")
            self.after(0, _apply_preview)

            # Stage 2: full load
            reader = pd.read_csv(self.file_path, chunksize=CHUNK_SIZE, iterator=True,
                                  low_memory=False, encoding=encoding,
                                  on_bad_lines="skip", engine="c", header=0,
                                  skiprows=range(1, INITIAL_ROWS + 1))
            chunks = [preview]
            for chunk in reader:
                chunks.append(chunk)
            full_data = pd.concat(chunks, ignore_index=True)
            full_data = self._optimize_dtypes(full_data)

            self.after(0, lambda: self._finalize_load(full_data))

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to read file: {e}"))

    def _finalize_load(self, full_data):
        self.original_data = full_data
        self.view_df = full_data
        self.total_rows = len(full_data)
        self._data_version += 1
        self._estimate_col_widths(full_data)
        self._update_col_offsets()
        self._update_gutter_width()
        self.features_ready = True
        self.edit_menu.entryconfig("Find/Filter...", state="normal")
        self.edit_menu.entryconfig("Clear Filter", state="normal")
        self.file_menu.entryconfig(self.export_menu_index, state="normal")
        self.view_menu.entryconfig("Freeze Columns...", state="normal")
        self.setup_display()
        self.status_bar.config(text=f"Ready. {self.total_rows:,} rows.")

    # ================================================================
    # Dtype optimization
    # ================================================================

    def _optimize_dtypes(self, df):
        """Convert low-cardinality strings to categorical, downcast numerics."""
        for col in df.columns:
            dtype = df[col].dtype
            if dtype == "object":
                sample = df[col].iloc[:10000] if len(df) > 10000 else df[col]
                if sample.nunique() / max(len(sample), 1) < 0.5:
                    df[col] = df[col].astype("category")
            elif pd.api.types.is_integer_dtype(dtype):
                df[col] = pd.to_numeric(df[col], downcast="integer")
            elif pd.api.types.is_float_dtype(dtype):
                df[col] = pd.to_numeric(df[col], downcast="float")
        return df

    # ================================================================
    # Column types, widths, offsets
    # ================================================================

    def _detect_column_types(self, df):
        self.col_alignments = {}
        self.col_types = {}
        for i, col_name in enumerate(df.columns):
            dtype = df[col_name].dtype
            if hasattr(dtype, "categories"):
                # categorical — check underlying type
                cat_dtype = df[col_name].cat.categories.dtype
                if pd.api.types.is_integer_dtype(cat_dtype):
                    col_type = "int"
                elif pd.api.types.is_float_dtype(cat_dtype):
                    col_type = "float"
                else:
                    col_type = "text"
            elif pd.api.types.is_integer_dtype(dtype):
                col_type = "int"
            elif pd.api.types.is_float_dtype(dtype):
                col_type = "float"
            elif pd.api.types.is_datetime64_any_dtype(dtype):
                col_type = "datetime"
            else:
                col_type = "text"
            self.col_types[i] = col_type
            self.col_alignments[i] = "e" if col_type in ("int", "float") else "w"

    def _estimate_col_widths(self, df):
        self.col_widths = {}
        sample = df.iloc[:200]
        for i, col_name in enumerate(df.columns):
            header_w = self.header_font.measure(col_name) + 30
            col_type = self.col_types.get(i, "text")
            font = self.mono_font if col_type in ("int", "float", "datetime") else self.default_font
            col_data = sample[col_name]
            if col_data.empty:
                max_data_w = 0
            else:
                strs = col_data.astype(str)
                longest = strs.loc[strs.str.len().idxmax()]
                if col_type == "int":
                    try:
                        longest = f"{int(float(longest)):,}"
                    except (ValueError, TypeError):
                        pass
                elif col_type == "float":
                    try:
                        longest = f"{float(longest):,.2f}"
                    except (ValueError, TypeError):
                        pass
                max_data_w = font.measure(longest) + 15
            self.col_widths[i] = min(max(header_w, max_data_w, 50), 400)

    def _update_col_offsets(self):
        """Recompute prefix-sum of column widths."""
        offsets = [0]
        for i in range(len(self.col_widths)):
            offsets.append(offsets[-1] + self.col_widths.get(i, 100))
        self._col_offsets = offsets

    def _update_gutter_width(self):
        if self.total_rows > 0:
            max_str = f"{self.total_rows:,}"
        else:
            max_str = "999"
        self._gutter_width = max(50, self.mono_font.measure(max_str) + 20)
        self.gutter_header.config(width=self._gutter_width)
        self.gutter_canvas.config(width=self._gutter_width)

    # ================================================================
    # Cell formatting helpers
    # ================================================================

    def _format_cell(self, col_idx, val):
        if pd.isna(val):
            return ""
        col_type = self.col_types.get(col_idx, "text")
        if col_type == "int":
            try:
                return f"{int(val):,}"
            except (ValueError, TypeError):
                return str(val)
        elif col_type == "float":
            try:
                return f"{float(val):,.2f}"
            except (ValueError, TypeError):
                return str(val)
        return str(val)

    def _get_font(self, col_idx):
        col_type = self.col_types.get(col_idx, "text")
        return self.mono_font if col_type in ("int", "float", "datetime") else self.default_font

    def _get_char_width(self, col_idx):
        col_type = self.col_types.get(col_idx, "text")
        return self._mono_char_width if col_type in ("int", "float", "datetime") else self._default_char_width

    def _clip_text(self, text, max_width, avg_char_w):
        if len(text) * avg_char_w <= max_width:
            return text
        max_chars = max(0, int(max_width / avg_char_w) - 1)
        if max_chars <= 0:
            return ""
        return text[:max_chars] + "\u2026"

    def _get_formatted_data(self, start_row, end_row):
        """Pre-format visible data with caching."""
        c = self._format_cache
        if c["start"] == start_row and c["end"] == end_row and c["ver"] == self._data_version:
            return c["data"]
        n_cols = len(self.column_names)
        result = []
        for row_vals in self.view_df.iloc[start_row:end_row].values:
            row = []
            for ci in range(n_cols):
                row.append(self._format_cell(ci, row_vals[ci]))
            result.append(row)
        self._format_cache = {"start": start_row, "end": end_row,
                              "ver": self._data_version, "data": result}
        return result

    # ================================================================
    # Display setup
    # ================================================================

    def setup_display(self):
        self._update_scrollregions()
        self._redraw_all()
        self.deiconify()
        self.lift()

    def _update_scrollregions(self):
        total_h = self.total_rows * self.row_height
        frozen_w = self._col_offsets[self.frozen_count] if self.frozen_count <= len(self._col_offsets) - 1 else 0
        main_w = self._col_offsets[-1] - frozen_w if self._col_offsets else 0

        self.canvas.configure(scrollregion=(0, 0, main_w, total_h))
        self.header_canvas.configure(scrollregion=(0, 0, main_w, 30))
        self.gutter_canvas.configure(scrollregion=(0, 0, self._gutter_width, total_h))
        if self.frozen_count > 0:
            self.frozen_canvas.configure(scrollregion=(0, 0, frozen_w, total_h))
            self.frozen_header.configure(scrollregion=(0, 0, frozen_w, 30))

    # ================================================================
    # Pool management (main canvas)
    # ================================================================

    def _rebuild_pool(self, n_rows, n_cols):
        """Rebuild the item pool for the main data canvas."""
        self.canvas.delete("all")
        self._pool_n_rows = n_rows
        self._pool_n_cols = n_cols

        # Layer 1: row stripe backgrounds
        self._pool_stripes = []
        for _ in range(n_rows):
            rid = self.canvas.create_rectangle(0, 0, 0, 0, fill="", outline="", state="hidden")
            self._pool_stripes.append(rid)

        # Layer 2: selection highlight
        self._pool_sel = self.canvas.create_rectangle(
            0, 0, 0, 0, fill=self.SELECTION_COLOR, outline="", state="hidden")

        # Layer 3: grid lines
        self._pool_vlines = []
        self._pool_hlines = []
        for r in range(n_rows):
            row_vl = []
            for c in range(n_cols):
                vid = self.canvas.create_line(0, 0, 0, 0, fill=self.GRID_LINE_COLOR, state="hidden")
                row_vl.append(vid)
            self._pool_vlines.append(row_vl)
            hid = self.canvas.create_line(0, 0, 0, 0, fill=self.GRID_LINE_COLOR, state="hidden")
            self._pool_hlines.append(hid)

        # Layer 4: text (on top)
        self._pool_texts = []
        for r in range(n_rows):
            row_t = []
            for c in range(n_cols):
                tid = self.canvas.create_text(0, 0, text="", anchor="w",
                                               font=self.default_font,
                                               fill=self.FOREGROUND_COLOR, state="hidden")
                row_t.append(tid)
            self._pool_texts.append(row_t)

    # ================================================================
    # Rendering: main canvas (pool-based)
    # ================================================================

    def redraw_canvas(self, event=None):
        if not self.column_names:
            return

        canvas_h = self.canvas.winfo_height()
        if canvas_h <= 1:
            return

        main_cols = len(self.column_names) - self.frozen_count
        needed_rows = int(canvas_h / self.row_height) + 3
        needed_cols = main_cols

        if needed_rows > self._pool_n_rows or needed_cols > self._pool_n_cols:
            self._rebuild_pool(max(needed_rows, self._pool_n_rows),
                               max(needed_cols, self._pool_n_cols))

        if self.total_rows == 0:
            # Hide everything
            for r in range(self._pool_n_rows):
                self.canvas.itemconfig(self._pool_stripes[r], state="hidden")
                self.canvas.itemconfig(self._pool_hlines[r], state="hidden")
                for c in range(self._pool_n_cols):
                    self.canvas.itemconfig(self._pool_texts[r][c], state="hidden")
                    self.canvas.itemconfig(self._pool_vlines[r][c], state="hidden")
            self.canvas.itemconfig(self._pool_sel, state="hidden")
            return

        y_top, y_bottom = self.canvas.yview()
        start_row = max(0, int(y_top * self.total_rows))
        end_row = min(self.total_rows, int(y_bottom * self.total_rows) + 2)

        # Horizontal visibility
        x_left_frac, x_right_frac = self.canvas.xview()
        frozen_offset = self._col_offsets[self.frozen_count] if self.frozen_count < len(self._col_offsets) else 0
        main_total_w = self._col_offsets[-1] - frozen_offset
        if main_total_w <= 0:
            main_total_w = 1
        x_left_px = x_left_frac * main_total_w
        x_right_px = x_right_frac * main_total_w + 20  # small buffer

        # Pre-format visible data
        formatted = self._get_formatted_data(start_row, end_row)
        visible_data_rows = end_row - start_row

        sel_row = self.selected_cell["row"]
        sel_col = self.selected_cell["col"]
        sel_drawn = False

        for pr in range(self._pool_n_rows):
            data_row = start_row + pr

            if pr >= visible_data_rows or data_row >= self.total_rows:
                # Hide this pool row
                self.canvas.itemconfig(self._pool_stripes[pr], state="hidden")
                self.canvas.itemconfig(self._pool_hlines[pr], state="hidden")
                for pc in range(self._pool_n_cols):
                    self.canvas.itemconfig(self._pool_texts[pr][pc], state="hidden")
                    self.canvas.itemconfig(self._pool_vlines[pr][pc], state="hidden")
                continue

            y = data_row * self.row_height

            # Stripe
            if data_row % 2 == 0:
                self.canvas.coords(self._pool_stripes[pr], 0, y, main_total_w, y + self.row_height)
                self.canvas.itemconfig(self._pool_stripes[pr], fill=self.STRIPE_COLOR, state="normal")
            else:
                self.canvas.itemconfig(self._pool_stripes[pr], state="hidden")

            # Horizontal line
            self.canvas.coords(self._pool_hlines[pr], 0, y + self.row_height,
                               main_total_w, y + self.row_height)
            self.canvas.itemconfig(self._pool_hlines[pr], state="normal")

            # Cells
            row_data = formatted[pr]
            for pc in range(self._pool_n_cols):
                global_col = self.frozen_count + pc
                if global_col >= len(self.column_names):
                    self.canvas.itemconfig(self._pool_texts[pr][pc], state="hidden")
                    self.canvas.itemconfig(self._pool_vlines[pr][pc], state="hidden")
                    continue

                col_x = self._col_offsets[global_col] - frozen_offset
                col_w = self.col_widths.get(global_col, 100)
                col_right = col_x + col_w

                # Visibility check
                if col_right < x_left_px or col_x > x_right_px:
                    self.canvas.itemconfig(self._pool_texts[pr][pc], state="hidden")
                    self.canvas.itemconfig(self._pool_vlines[pr][pc], state="hidden")
                    continue

                # Text
                align = self.col_alignments.get(global_col, "w")
                font = self._get_font(global_col)
                char_w = self._get_char_width(global_col)
                display = self._clip_text(row_data[global_col], col_w - 10, char_w)

                if align == "e":
                    tx = col_x + col_w - 5
                    anchor = "e"
                else:
                    tx = col_x + 5
                    anchor = "w"

                self.canvas.coords(self._pool_texts[pr][pc], tx, y + self.row_height / 2)
                self.canvas.itemconfig(self._pool_texts[pr][pc],
                                        text=display, anchor=anchor, font=font, state="normal")

                # Vertical line
                self.canvas.coords(self._pool_vlines[pr][pc],
                                    col_right, y, col_right, y + self.row_height)
                self.canvas.itemconfig(self._pool_vlines[pr][pc], state="normal")

            # Selection (in main canvas area)
            if data_row == sel_row and sel_col is not None and sel_col >= self.frozen_count:
                sc_x = self._col_offsets[sel_col] - frozen_offset
                sc_w = self.col_widths.get(sel_col, 100)
                self.canvas.coords(self._pool_sel, sc_x, y, sc_x + sc_w, y + self.row_height)
                self.canvas.itemconfig(self._pool_sel, state="normal")
                self.canvas.tag_raise(self._pool_sel)
                # Raise text above selection
                for pc in range(self._pool_n_cols):
                    self.canvas.tag_raise(self._pool_texts[pr][pc])
                sel_drawn = True

        if not sel_drawn:
            self.canvas.itemconfig(self._pool_sel, state="hidden")

    # ================================================================
    # Rendering: headers
    # ================================================================

    def _redraw_gutter_header(self):
        self.gutter_header.delete("all")
        h = self.gutter_header.winfo_height()
        self.gutter_header.create_text(self._gutter_width - 8, h / 2,
                                        text="#", anchor="e",
                                        font=self.header_font, fill=self.HEADER_FG)
        # Right border
        self.gutter_header.create_line(self._gutter_width - 1, 0,
                                        self._gutter_width - 1, h, fill=self.GRID_LINE_COLOR)

    def _redraw_frozen_header(self):
        self.frozen_header.delete("all")
        if self.frozen_count == 0 or not self.column_names:
            return
        h = self.frozen_header.winfo_height()
        for i in range(self.frozen_count):
            col_x = self._col_offsets[i]
            col_w = self.col_widths.get(i, 100)
            text = self.column_names[i]
            if self.sort_info["col_index"] == i:
                text += " \u25b2" if self.sort_info["ascending"] else " \u25bc"

            align = self.col_alignments.get(i, "w")
            tx = col_x + (col_w - 10 if align == "e" else 10)
            anchor = "e" if align == "e" else "w"

            self.frozen_header.create_text(tx, h / 2, text=text, anchor=anchor,
                                            font=self.header_font, fill=self.HEADER_FG)
            line_x = col_x + col_w
            self.frozen_header.create_line(line_x, 4, line_x, h - 4, fill=self.GRID_LINE_COLOR)

    def _redraw_main_header(self):
        self.header_canvas.delete("all")
        if not self.column_names:
            return

        h = self.header_canvas.winfo_height()
        x_left = self.header_canvas.xview()[0]
        frozen_offset = self._col_offsets[self.frozen_count] if self.frozen_count < len(self._col_offsets) else 0
        main_w = self._col_offsets[-1] - frozen_offset
        x_off = -int(x_left * main_w)
        canvas_w = self.header_canvas.winfo_width()

        for i in range(self.frozen_count, len(self.column_names)):
            col_x = self._col_offsets[i] - frozen_offset
            col_w = self.col_widths.get(i, 100)

            draw_x = col_x + x_off
            if draw_x + col_w < 0 or draw_x > canvas_w:
                continue

            text = self.column_names[i]
            if self.sort_info["col_index"] == i:
                text += " \u25b2" if self.sort_info["ascending"] else " \u25bc"

            align = self.col_alignments.get(i, "w")
            tx = col_x + (col_w - 10 if align == "e" else 10)
            anchor = "e" if align == "e" else "w"

            self.header_canvas.create_text(tx, h / 2, text=text, anchor=anchor,
                                            font=self.header_font, fill=self.HEADER_FG)
            line_x = col_x + col_w
            self.header_canvas.create_line(line_x, 4, line_x, h - 4, fill=self.GRID_LINE_COLOR)

    # ================================================================
    # Rendering: gutter (row numbers)
    # ================================================================

    def _redraw_gutter(self):
        self.gutter_canvas.delete("all")
        if self.view_df.empty or self.total_rows == 0:
            return

        y_top = self.gutter_canvas.yview()[0]
        start_row = max(0, int(y_top * self.total_rows))
        visible = int(self.gutter_canvas.winfo_height() / self.row_height) + 2

        gw = self._gutter_width

        for i in range(min(visible, self.total_rows - start_row)):
            data_row = start_row + i
            if data_row >= len(self.view_df):
                break
            y = data_row * self.row_height

            # Stripe
            if data_row % 2 == 0:
                self.gutter_canvas.create_rectangle(0, y, gw, y + self.row_height,
                                                     fill=self.STRIPE_COLOR, outline="")

            # Row number (1-based original position)
            row_num = self.view_df.index[data_row] + 1
            self.gutter_canvas.create_text(gw - 8, y + self.row_height / 2,
                                            text=f"{row_num:,}", anchor="e",
                                            font=self.mono_font, fill=self.FOREGROUND_COLOR)

            # Bottom line
            self.gutter_canvas.create_line(0, y + self.row_height, gw, y + self.row_height,
                                            fill=self.GRID_LINE_COLOR)

        # Right border
        self.gutter_canvas.create_line(gw - 1, 0, gw - 1,
                                        self.total_rows * self.row_height, fill=self.GRID_LINE_COLOR)

    # ================================================================
    # Rendering: frozen data
    # ================================================================

    def _redraw_frozen_data(self):
        self.frozen_canvas.delete("all")
        if self.frozen_count == 0 or self.view_df.empty:
            return

        y_top = self.frozen_canvas.yview()[0]
        start_row = max(0, int(y_top * self.total_rows))
        visible = int(self.frozen_canvas.winfo_height() / self.row_height) + 2

        frozen_w = self._col_offsets[self.frozen_count]
        formatted = self._get_formatted_data(
            start_row, min(start_row + visible, self.total_rows))

        sel_row = self.selected_cell["row"]
        sel_col = self.selected_cell["col"]

        for i in range(min(visible, len(formatted))):
            data_row = start_row + i
            if data_row >= self.total_rows:
                break
            y = data_row * self.row_height

            # Stripe
            if data_row % 2 == 0:
                self.frozen_canvas.create_rectangle(0, y, frozen_w, y + self.row_height,
                                                     fill=self.STRIPE_COLOR, outline="")

            # Selection
            if data_row == sel_row and sel_col is not None and sel_col < self.frozen_count:
                sx = self._col_offsets[sel_col]
                sw = self.col_widths.get(sel_col, 100)
                self.frozen_canvas.create_rectangle(sx, y, sx + sw, y + self.row_height,
                                                     fill=self.SELECTION_COLOR, outline="")

            # Cells
            for ci in range(self.frozen_count):
                col_x = self._col_offsets[ci]
                col_w = self.col_widths.get(ci, 100)
                align = self.col_alignments.get(ci, "w")
                font = self._get_font(ci)
                char_w = self._get_char_width(ci)

                display = self._clip_text(formatted[i][ci], col_w - 10, char_w)
                tx = col_x + (col_w - 5 if align == "e" else 5)
                anchor = "e" if align == "e" else "w"

                self.frozen_canvas.create_text(tx, y + self.row_height / 2,
                                                text=display, anchor=anchor,
                                                font=font, fill=self.FOREGROUND_COLOR)
                # Vertical line
                self.frozen_canvas.create_line(col_x + col_w, y, col_x + col_w,
                                                y + self.row_height, fill=self.GRID_LINE_COLOR)

            # Horizontal line
            self.frozen_canvas.create_line(0, y + self.row_height, frozen_w,
                                            y + self.row_height, fill=self.GRID_LINE_COLOR)

    # ================================================================
    # Sorting
    # ================================================================

    def sort_by_column(self, col_index):
        if not self.features_ready:
            return
        if self.sort_thread and self.sort_thread.is_alive():
            return
        if self.sort_info["col_index"] == col_index:
            self.sort_info["ascending"] = not self.sort_info["ascending"]
        else:
            self.sort_info["col_index"] = col_index
            self.sort_info["ascending"] = True
        self.status_bar.config(text=f"Sorting by '{self.column_names[col_index]}'...")
        self.sort_thread = threading.Thread(target=self._perform_sort, daemon=True)
        self.sort_thread.start()

    def _perform_sort(self):
        col_index = self.sort_info["col_index"]
        ascending = self.sort_info["ascending"]
        col_name = self.column_names[col_index]
        try:
            col_type = self.col_types.get(col_index, "text")
            df = self.view_df
            if col_type == "datetime":
                sort_key = pd.to_datetime(df[col_name], errors="coerce")
            elif col_type in ("int", "float"):
                sort_key = pd.to_numeric(df[col_name], errors="coerce")
            else:
                sort_key = df[col_name]
            sorted_df = df.iloc[sort_key.argsort(kind="mergesort")]
            if not ascending:
                sorted_df = sorted_df.iloc[::-1]

            def _apply():
                self.view_df = sorted_df
                self._data_version += 1
                self.setup_display()
                self.status_bar.config(text="Sort complete.")
            self.after(0, _apply)
        except Exception as e:
            self.after(0, lambda err=e: messagebox.showerror("Sort Error", str(err)))

    # ================================================================
    # Filtering
    # ================================================================

    def find_data(self):
        if not self.features_ready:
            return
        if self.filter_thread and self.filter_thread.is_alive():
            return
        dlg = FilterDialog(self, self.column_names)
        if dlg.result:
            term, column, use_regex = dlg.result
            self.status_bar.config(text=f"Filtering for '{term}'...")
            self.filter_thread = threading.Thread(
                target=self._perform_filter, args=(term, column, use_regex), daemon=True)
            self.filter_thread.start()

    def _filter_by_column(self, col_index):
        """Open filter dialog pre-set to a specific column."""
        if not self.features_ready:
            return
        if self.filter_thread and self.filter_thread.is_alive():
            return
        dlg = FilterDialog(self, self.column_names)
        # The dialog already ran; if user wanted column-specific, they chose it.
        # For right-click, we could pre-select, but FilterDialog blocks.
        # Instead, just open the dialog — user can pick the column.
        if dlg.result:
            term, column, use_regex = dlg.result
            if column is None:
                column = self.column_names[col_index]
            self.status_bar.config(text=f"Filtering for '{term}'...")
            self.filter_thread = threading.Thread(
                target=self._perform_filter, args=(term, column, use_regex), daemon=True)
            self.filter_thread.start()

    def _perform_filter(self, search_term, column, use_regex):
        t_start = time.perf_counter()
        try:
            if column:
                cols = [column]
            else:
                cols = list(self.original_data.columns)

            mask = pd.Series(False, index=self.original_data.index)
            for col in cols:
                series = self.original_data[col]
                if pd.api.types.is_string_dtype(series) or series.dtype == "object" or hasattr(series.dtype, "categories"):
                    col_matches = series.astype(str).str.contains(
                        search_term, case=False, na=False, regex=use_regex)
                else:
                    col_matches = series.astype(str).str.contains(
                        search_term, case=False, na=False, regex=use_regex)
                mask |= col_matches

            filtered = self.original_data[mask]
            t_end = time.perf_counter()
            count = len(filtered)

            def _apply():
                self.view_df = filtered
                self.total_rows = count
                self.selected_cell = {"row": None, "col": None}
                self._data_version += 1
                self._update_gutter_width()
                self.setup_display()
                self.status_bar.config(text=f"Found {count:,} matches in {t_end - t_start:.3f}s")
            self.after(0, _apply)
        except Exception as e:
            self.after(0, lambda err=e: self.status_bar.config(text=f"Filter error: {err}"))

    def clear_filter(self):
        if not self.features_ready:
            return
        self.view_df = self.original_data
        self.total_rows = len(self.original_data)
        self.selected_cell = {"row": None, "col": None}
        self._data_version += 1
        self._update_gutter_width()
        self.canvas.yview_moveto(0)
        self.gutter_canvas.yview_moveto(0)
        if self.frozen_count > 0:
            self.frozen_canvas.yview_moveto(0)
        self.status_bar.config(text="Filter cleared.")
        self.setup_display()

    # ================================================================
    # Keyboard navigation
    # ================================================================

    def _on_key_nav(self, event):
        if self.view_df.empty:
            return "break"

        row = self.selected_cell.get("row")
        col = self.selected_cell.get("col")
        n_cols = len(self.column_names)
        max_row = self.total_rows - 1

        if row is None:
            row, col = 0, 0

        key = event.keysym
        if key == "Up":
            row = max(0, row - 1)
        elif key == "Down":
            row = min(max_row, row + 1)
        elif key == "Left":
            col = max(0, col - 1)
        elif key == "Right":
            col = min(n_cols - 1, col + 1)
        elif key == "Prior":  # Page Up
            page = max(1, int(self.canvas.winfo_height() / self.row_height) - 1)
            row = max(0, row - page)
        elif key == "Next":  # Page Down
            page = max(1, int(self.canvas.winfo_height() / self.row_height) - 1)
            row = min(max_row, row + page)
        elif key == "Home":
            row = 0
        elif key == "End":
            row = max_row
        elif key == "Tab":
            if event.state & 0x1:  # Shift
                col = max(0, col - 1)
            else:
                col = min(n_cols - 1, col + 1)
        else:
            return

        self.selected_cell = {"row": row, "col": col}
        self._ensure_visible(row, col)
        self._schedule_redraw()
        return "break"

    def _ensure_visible(self, row, col):
        """Scroll to make the given cell visible."""
        if self.total_rows <= 0:
            return

        # Vertical
        y_top, y_bottom = self.canvas.yview()
        row_frac = row / self.total_rows
        visible_frac = y_bottom - y_top
        row_size_frac = 1.0 / self.total_rows if self.total_rows > 0 else 0

        if row_frac < y_top:
            target = row_frac
            self.canvas.yview_moveto(target)
            self.gutter_canvas.yview_moveto(target)
            if self.frozen_count > 0:
                self.frozen_canvas.yview_moveto(target)
        elif row_frac + row_size_frac > y_bottom:
            target = max(0, row_frac + row_size_frac - visible_frac)
            self.canvas.yview_moveto(target)
            self.gutter_canvas.yview_moveto(target)
            if self.frozen_count > 0:
                self.frozen_canvas.yview_moveto(target)

        # Horizontal (only for non-frozen columns)
        if col >= self.frozen_count:
            frozen_offset = self._col_offsets[self.frozen_count]
            main_w = self._col_offsets[-1] - frozen_offset
            if main_w <= 0:
                return
            x_left, x_right = self.canvas.xview()
            col_left = (self._col_offsets[col] - frozen_offset) / main_w
            col_right = (self._col_offsets[col + 1] - frozen_offset) / main_w
            vis_w = x_right - x_left

            if col_left < x_left:
                self.canvas.xview_moveto(col_left)
                self.header_canvas.xview_moveto(col_left)
            elif col_right > x_right:
                self.canvas.xview_moveto(max(0, col_right - vis_w))
                self.header_canvas.xview_moveto(max(0, col_right - vis_w))

    def go_to_line(self):
        if self.original_data.empty:
            return
        from tkinter import simpledialog
        line_num = simpledialog.askinteger("Go to Row",
                                            f"Row (1 - {self.total_rows}):",
                                            parent=self, minvalue=1, maxvalue=self.total_rows)
        if line_num:
            frac = max(0.0, min((line_num - 1) / max(self.total_rows, 1), 1.0 - 1e-9))
            self.canvas.yview_moveto(frac)
            self.gutter_canvas.yview_moveto(frac)
            if self.frozen_count > 0:
                self.frozen_canvas.yview_moveto(frac)
            self.selected_cell = {"row": line_num - 1, "col": 0}
            self._schedule_redraw()

    # ================================================================
    # Selection & clipboard
    # ================================================================

    def _on_cell_click(self, event):
        canvas = event.widget
        canvas_y = canvas.canvasy(event.y)
        row = int(canvas_y // self.row_height)

        col = None
        if canvas == self.canvas:
            canvas_x = canvas.canvasx(event.x)
            frozen_offset = self._col_offsets[self.frozen_count] if self.frozen_count < len(self._col_offsets) else 0
            global_x = canvas_x + frozen_offset
            idx = bisect.bisect_right(self._col_offsets, global_x) - 1
            if self.frozen_count <= idx < len(self.column_names):
                col = idx
        elif canvas == self.frozen_canvas:
            canvas_x = canvas.canvasx(event.x)
            idx = bisect.bisect_right(self._col_offsets, canvas_x) - 1
            if 0 <= idx < self.frozen_count:
                col = idx

        if row < len(self.view_df) and col is not None:
            self.selected_cell = {"row": row, "col": col}
        else:
            self.selected_cell = {"row": None, "col": None}
        self._schedule_redraw()

    def copy_selection(self):
        sel = self.selected_cell
        if sel["row"] is not None and sel["col"] is not None:
            try:
                value = self.view_df.iloc[sel["row"], sel["col"]]
                self.clipboard_clear()
                self.clipboard_append(str(value))
                self.status_bar.config(text="Copied to clipboard.")
            except IndexError:
                pass

    # ================================================================
    # Column resizing
    # ================================================================

    def _header_canvas_x_to_global(self, hdr, event_x):
        """Convert header canvas event x to global column offset."""
        cx = hdr.canvasx(event_x)
        if hdr == self.frozen_header:
            return cx
        else:
            frozen_offset = self._col_offsets[self.frozen_count] if self.frozen_count < len(self._col_offsets) else 0
            return cx + frozen_offset

    def _get_resize_col(self, global_x):
        """Return column index whose right border is near global_x, or None."""
        tolerance = 5
        for i in range(len(self.column_names)):
            border = self._col_offsets[i + 1] if i + 1 < len(self._col_offsets) else self._col_offsets[-1]
            if abs(global_x - border) < tolerance:
                return i
        return None

    def _get_col_at(self, global_x):
        """Return column index at global_x."""
        idx = bisect.bisect_right(self._col_offsets, global_x) - 1
        if 0 <= idx < len(self.column_names):
            return idx
        return None

    def _on_header_motion(self, event):
        if self.resizing_col_index is not None:
            return
        hdr = event.widget
        gx = self._header_canvas_x_to_global(hdr, event.x)
        col = self._get_resize_col(gx)
        hdr.config(cursor="sb_h_double_arrow" if col is not None else "")

    def _on_header_press(self, event):
        hdr = event.widget
        gx = self._header_canvas_x_to_global(hdr, event.x)
        col_to_resize = self._get_resize_col(gx)
        current_time = time.time()

        # Double-click auto-fit
        if (col_to_resize is not None and col_to_resize == self.last_press_col
                and (current_time - self.last_press_time) < 0.3):
            self.auto_fit_column(col_to_resize)
            self.last_press_col = None
            return

        self.last_press_time = current_time
        self.last_press_col = col_to_resize
        self.resizing_col_index = None
        self.potential_sort_click = False
        self._resize_header_source = hdr

        if col_to_resize is not None:
            self.resizing_col_index = col_to_resize
            self.resize_start_x = event.x
            self.initial_col_width = self.col_widths.get(col_to_resize, 100)
        else:
            self.potential_sort_click = True
            self._potential_sort_gx = gx

    def _on_header_drag(self, event):
        if self.resizing_col_index is None:
            return
        self.potential_sort_click = False
        new_w = max(30, self.initial_col_width + (event.x - self.resize_start_x))
        self.col_widths[self.resizing_col_index] = new_w
        self._update_col_offsets()
        self._update_scrollregions()
        self._schedule_redraw()

    def _on_header_release(self, event):
        if self.resizing_col_index is not None:
            self.resizing_col_index = None
            hdr = event.widget
            hdr.config(cursor="")
            # Force pool rebuild since col count might need adjustment
            self._pool_n_cols = 0
            self._perform_redraw()
        elif self.potential_sort_click:
            col = self._get_col_at(self._potential_sort_gx)
            if col is not None:
                self.sort_by_column(col)
        self.potential_sort_click = False

    def _on_header_right_click(self, event):
        if not self.column_names:
            return
        hdr = event.widget
        gx = self._header_canvas_x_to_global(hdr, event.x)
        col = self._get_col_at(gx)
        if col is None:
            return

        menu = tk.Menu(self, tearoff=0)
        col_name = self.column_names[col]

        if col >= self.frozen_count:
            menu.add_command(label=f"Freeze up to '{col_name}'",
                             command=lambda c=col: self._freeze_up_to(c))
        if self.frozen_count > 0:
            menu.add_command(label="Unfreeze all columns",
                             command=self._unfreeze_columns)

        menu.add_separator()
        menu.add_command(label=f"Auto-fit '{col_name}'",
                         command=lambda c=col: self.auto_fit_column(c))

        if self.features_ready:
            menu.add_separator()
            menu.add_command(label=f"Filter by '{col_name}'...",
                             command=lambda c=col: self._filter_by_column(c))

        menu.tk_popup(event.x_root, event.y_root)

    def auto_fit_column(self, col_index):
        if not self.features_ready:
            return
        if self.auto_fit_thread and self.auto_fit_thread.is_alive():
            return
        self.status_bar.config(text="Auto-fitting...")
        self.auto_fit_thread = threading.Thread(
            target=self._perform_auto_fit, args=(col_index,), daemon=True)
        self.auto_fit_thread.start()

    def _perform_auto_fit(self, col_index):
        try:
            col_name = self.column_names[col_index]
            font = self._get_font(col_index)
            header_w = self.header_font.measure(col_name) + 30
            s = self.original_data[col_name].astype(str)
            if s.empty:
                max_data_w = 0
            else:
                val = s.loc[s.str.len().idxmax()]
                max_data_w = font.measure(val) + 15
            new_w = min(max(header_w, max_data_w, 50), 500)

            def _apply():
                self.col_widths[col_index] = new_w
                self._update_col_offsets()
                self._update_scrollregions()
                self._update_frozen_layout()
                self._pool_n_cols = 0  # force rebuild
                self._perform_redraw()
                self.status_bar.config(text="Auto-fit complete.")
            self.after(0, _apply)
        except Exception as e:
            self.after(0, lambda: self.status_bar.config(text=f"Auto-fit failed: {e}"))

    # ================================================================
    # Frozen columns
    # ================================================================

    def _freeze_up_to(self, col_index):
        self.frozen_count = col_index + 1
        self._update_frozen_layout()
        self._update_scrollregions()
        self._pool_n_cols = 0  # force pool rebuild
        self._data_version += 1  # invalidate format cache row range
        self.view_menu.entryconfig("Unfreeze All", state="normal")
        self._perform_redraw()

    def _unfreeze_columns(self):
        self.frozen_count = 0
        self._update_frozen_layout()
        self._update_scrollregions()
        self._pool_n_cols = 0
        self._data_version += 1
        self.view_menu.entryconfig("Unfreeze All", state="disabled")
        self._perform_redraw()

    def _freeze_dialog(self):
        if not self.features_ready:
            return
        from tkinter import simpledialog
        n = simpledialog.askinteger("Freeze Columns",
                                     f"Freeze first N columns (0-{len(self.column_names)}):",
                                     parent=self, minvalue=0,
                                     maxvalue=len(self.column_names))
        if n is not None:
            if n == 0:
                self._unfreeze_columns()
            else:
                self._freeze_up_to(n - 1)

    def _update_frozen_layout(self):
        """Show/hide frozen canvases based on frozen_count."""
        if self.frozen_count > 0:
            frozen_w = self._col_offsets[self.frozen_count] if self.frozen_count < len(self._col_offsets) else 0
            self.frozen_header.config(width=frozen_w)
            self.frozen_canvas.config(width=frozen_w)
            self.frozen_header.grid(row=0, column=1, sticky="nsew")
            self.frozen_canvas.grid(row=1, column=1, sticky="ns")
        else:
            self.frozen_header.grid_forget()
            self.frozen_canvas.grid_forget()

    # ================================================================
    # Export
    # ================================================================

    def export_filtered(self):
        if self.view_df.empty:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            try:
                self.view_df.to_csv(path, index=False)
                self.status_bar.config(
                    text=f"Exported {len(self.view_df):,} rows to {os.path.basename(path)}")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))

    # ================================================================
    # Misc
    # ================================================================

    def show_about(self):
        messagebox.showinfo("About",
                            "Fast CSV Viewer\n"
                            "Optimized for large files.\n\n"
                            "Keyboard: Arrow keys, PgUp/PgDn, Home/End\n"
                            "Double-click column border to auto-fit\n"
                            "Right-click header for options")

    def _on_close(self):
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    debug_mode = False
    file_to_open = None

    args = sys.argv[1:]
    for arg in args:
        if arg == "--debug-colors":
            debug_mode = True
        elif not arg.startswith("-"):
            file_to_open = arg

    app = CSVViewer(debug_colors=debug_mode)
    if file_to_open:
        app.load_file_from_path(file_to_open)
    app.mainloop()

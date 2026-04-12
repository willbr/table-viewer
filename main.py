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

# High DPI support for Windows
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

CONFIG_PATH = os.path.expanduser("~/.csv-viewer.json")


class FilterDialog(tk.Toplevel):
    """Filter dialog with column selection and regex support."""

    def __init__(self, parent, columns):
        super().__init__(parent)
        self.title("Find/Filter")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.geometry(f"+{parent.winfo_rootx() + 80}+{parent.winfo_rooty() + 80}")

        tk.Label(self, text="Search:").grid(row=0, column=0, padx=8, pady=(10, 4), sticky="w")
        self.entry = tk.Entry(self, width=36)
        self.entry.grid(row=0, column=1, columnspan=2, padx=8, pady=(10, 4), sticky="ew")
        self.entry.focus_set()

        tk.Label(self, text="In column:").grid(row=1, column=0, padx=8, pady=4, sticky="w")
        self.column_var = tk.StringVar(value="All columns")
        ttk.Combobox(self, textvariable=self.column_var,
                     values=["All columns"] + list(columns), state="readonly").grid(
            row=1, column=1, columnspan=2, padx=8, pady=4, sticky="ew")

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


class CSVViewer(tk.Tk):
    """A fast CSV viewer with virtualized canvas rendering and two-stage loading."""

    def __init__(self):
        super().__init__()
        self.title("Fast CSV Viewer")
        self.modifier = "Command" if sys.platform == "darwin" else "Control"

        self._setup_theme_colors()

        # Fonts
        self.default_font = tkFont.Font(
            family="Segoe UI" if os.name == "nt" else "Helvetica", size=10)
        self.mono_font = tkFont.Font(
            family="Consolas" if os.name == "nt" else "Courier New", size=10)
        self.header_font = tkFont.Font(
            family="Segoe UI" if os.name == "nt" else "Helvetica", size=10, weight="bold")
        self._default_char_width = self.default_font.measure("x")
        self._mono_char_width = self.mono_font.measure("0")

        # Data
        self.file_path = None
        self.original_data = pd.DataFrame()
        self.view_df = pd.DataFrame()
        self.column_names = []
        self.sort_info = {"col_index": None, "ascending": True}
        self.col_alignments = {}
        self.col_types = {}
        self.selected_cell = {"row": None, "col": None}

        # Virtual grid
        self.row_height = 25
        self.col_widths = {}
        self._col_offsets = [0]
        self.total_rows = 0
        self.features_ready = False
        self._redraw_job = None
        self._gutter_width = 50

        # Threads
        self.sort_thread = None
        self.filter_thread = None
        self.auto_fit_thread = None

        # Column resizing state
        self.resizing_col_index = None
        self.resize_start_x = 0
        self.initial_col_width = 0
        self.potential_sort_click = False
        self.last_press_time = 0
        self.last_press_col = None

        self._load_config()
        self.config(bg=self.BACKGROUND_COLOR)
        self._create_menu()
        self._create_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================
    # Config
    # ================================================================

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r") as f:
                geo = json.load(f).get("geometry")
            if geo:
                self.geometry(geo)
                return
        except Exception:
            pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(sw * 0.75), int(sh * 0.8)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _save_config(self):
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump({"geometry": self.geometry()}, f)
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
                is_dark = r.stdout.strip() == "Dark"
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

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)

        self.bind(f"<{self.modifier}-o>", lambda e: self.open_file())
        self.bind(f"<{self.modifier}-f>", lambda e: self.find_data())
        self.bind(f"<{self.modifier}-g>", lambda e: self.go_to_line())
        self.bind(f"<{self.modifier}-c>", lambda e: self.copy_selection())
        self.bind("<Escape>", lambda e: self.clear_filter())

        for key in ("Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"):
            self.bind(f"<{key}>", self._on_key_nav)
        self.bind("<Tab>", self._on_key_nav)
        self.bind("<Shift-Tab>", self._on_key_nav)

    # ================================================================
    # Widgets
    # ================================================================

    def _create_widgets(self):
        main_frame = tk.Frame(self, bg=self.BACKGROUND_COLOR)
        main_frame.pack(fill="both", expand=True)
        main_frame.grid_rowconfigure(1, weight=1)
        main_frame.grid_columnconfigure(1, weight=1)

        # Gutter header
        self.gutter_header = tk.Canvas(main_frame, height=30, bd=0,
                                        highlightthickness=0, bg=self.HEADER_BG,
                                        width=self._gutter_width)
        self.gutter_header.grid(row=0, column=0, sticky="nsew")

        # Main header
        self.header_canvas = tk.Canvas(main_frame, height=30, bd=0,
                                        highlightthickness=0, bg=self.HEADER_BG)
        self.header_canvas.grid(row=0, column=1, sticky="ew")

        # Gutter data
        self.gutter_canvas = tk.Canvas(main_frame, bg=self.GUTTER_BG,
                                        highlightthickness=0, bd=0,
                                        width=self._gutter_width)
        self.gutter_canvas.grid(row=1, column=0, sticky="ns")

        # Main data
        self.canvas = tk.Canvas(main_frame, bg=self.BACKGROUND_COLOR,
                                 highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=1, sticky="nsew")

        # Scrollbars
        self.vsb = ttk.Scrollbar(main_frame, orient="vertical", command=self.on_vscroll)
        self.hsb = ttk.Scrollbar(main_frame, orient="horizontal", command=self.on_hscroll)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        self.vsb.grid(row=1, column=2, sticky="ns")
        self.hsb.grid(row=2, column=1, sticky="ew")

        # Status bar
        self.status_bar = ttk.Label(self, text="Ready", anchor=tk.W,
                                     padding=(5, 2), relief=tk.SUNKEN)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Bindings
        self.canvas.bind("<Configure>", lambda e: self._schedule_redraw())
        self.canvas.bind("<Button-1>", self._on_cell_click)

        for cvs in (self.canvas, self.gutter_canvas):
            cvs.bind("<MouseWheel>", self._on_mousewheel)
            cvs.bind("<Button-4>", self._on_mousewheel)
            cvs.bind("<Button-5>", self._on_mousewheel)

        self.header_canvas.bind("<Motion>", self._on_header_motion)
        self.header_canvas.bind("<ButtonPress-1>", self._on_header_press)
        self.header_canvas.bind("<B1-Motion>", self._on_header_drag)
        self.header_canvas.bind("<ButtonRelease-1>", self._on_header_release)
        if sys.platform == "darwin":
            self.header_canvas.bind("<Button-2>", self._on_header_right_click)
            self.header_canvas.bind("<Control-Button-1>", self._on_header_right_click)
        else:
            self.header_canvas.bind("<Button-3>", self._on_header_right_click)

    # ================================================================
    # Scrolling
    # ================================================================

    def _schedule_redraw(self):
        if self._redraw_job:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(8, self._perform_redraw)

    def _perform_redraw(self):
        self._redraw_job = None
        self._redraw_header()
        self._redraw_gutter()
        self.redraw_canvas()

    def on_vscroll(self, *args):
        self.canvas.yview(*args)
        self.gutter_canvas.yview(*args)
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
        self.selected_cell = {"row": None, "col": None}
        self.last_press_time = 0
        self.last_press_col = None
        self.edit_menu.entryconfig("Find/Filter...", state="disabled")
        self.edit_menu.entryconfig("Clear Filter", state="disabled")
        self.file_menu.entryconfig(1, state="disabled")
        self.canvas.delete("all")
        self.header_canvas.delete("all")
        self.gutter_canvas.delete("all")
        self.gutter_header.delete("all")

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
                self._update_gutter_width()
                self.setup_display()
                self.status_bar.config(text="Loaded preview. Reading full file...")
            self.after(0, _apply_preview)

            reader = pd.read_csv(self.file_path, chunksize=CHUNK_SIZE, iterator=True,
                                  low_memory=False, encoding=encoding,
                                  on_bad_lines="skip", engine="c", header=0,
                                  skiprows=range(1, INITIAL_ROWS + 1))
            chunks = [preview]
            for chunk in reader:
                chunks.append(chunk)
            full_data = pd.concat(chunks, ignore_index=True)
            self.after(0, lambda: self._finalize_load(full_data))

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to read file: {e}"))

    def _finalize_load(self, full_data):
        self.original_data = full_data
        self.view_df = full_data
        self.total_rows = len(full_data)
        self._estimate_col_widths(full_data)
        self._update_col_offsets()
        self._update_gutter_width()
        self.features_ready = True
        self.edit_menu.entryconfig("Find/Filter...", state="normal")
        self.edit_menu.entryconfig("Clear Filter", state="normal")
        self.file_menu.entryconfig(1, state="normal")
        self.setup_display()
        self.status_bar.config(text=f"Ready. {self.total_rows:,} rows.")

    # ================================================================
    # Column types, widths, offsets
    # ================================================================

    def _detect_column_types(self, df):
        self.col_alignments = {}
        self.col_types = {}
        for i, col_name in enumerate(df.columns):
            dtype = df[col_name].dtype
            if pd.api.types.is_integer_dtype(dtype):
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
        offsets = [0]
        for i in range(len(self.col_widths)):
            offsets.append(offsets[-1] + self.col_widths.get(i, 100))
        self._col_offsets = offsets

    def _update_gutter_width(self):
        max_str = f"{self.total_rows:,}" if self.total_rows > 0 else "999"
        self._gutter_width = max(50, self.mono_font.measure(max_str) + 20)
        self.gutter_header.config(width=self._gutter_width)
        self.gutter_canvas.config(width=self._gutter_width)

    # ================================================================
    # Formatting
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

    def _clip_text(self, text, col_idx, col_w):
        char_w = self._mono_char_width if self.col_types.get(col_idx, "text") in ("int", "float", "datetime") else self._default_char_width
        if len(text) * char_w <= col_w - 10:
            return text
        max_chars = max(0, int((col_w - 10) / char_w) - 1)
        return text[:max_chars] + "\u2026" if max_chars > 0 else ""

    # ================================================================
    # Display
    # ================================================================

    def setup_display(self):
        total_h = self.total_rows * self.row_height
        total_w = self._col_offsets[-1] if self._col_offsets else 0
        self.canvas.configure(scrollregion=(0, 0, total_w, total_h))
        self.header_canvas.configure(scrollregion=(0, 0, total_w, 30))
        self.gutter_canvas.configure(scrollregion=(0, 0, self._gutter_width, total_h))
        self._perform_redraw()
        self.deiconify()
        self.lift()

    # ================================================================
    # Rendering
    # ================================================================

    def _redraw_header(self):
        self.header_canvas.delete("all")
        if not self.column_names:
            return

        h = self.header_canvas.winfo_height()
        x_left = self.header_canvas.xview()[0]
        total_w = self._col_offsets[-1]
        x_off = -int(x_left * total_w)
        canvas_w = self.header_canvas.winfo_width()

        for i in range(len(self.column_names)):
            col_x = self._col_offsets[i]
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

        # Gutter header (static content, cheap to redraw)
        self.gutter_header.delete("all")
        self.gutter_header.create_text(self._gutter_width - 8, h / 2, text="#",
                                        anchor="e", font=self.header_font, fill=self.HEADER_FG)
        self.gutter_header.create_line(self._gutter_width - 1, 0,
                                        self._gutter_width - 1, h, fill=self.GRID_LINE_COLOR)

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

            if data_row % 2 == 0:
                self.gutter_canvas.create_rectangle(0, y, gw, y + self.row_height,
                                                     fill=self.STRIPE_COLOR, outline="")

            row_num = self.view_df.index[data_row] + 1
            self.gutter_canvas.create_text(gw - 8, y + self.row_height / 2,
                                            text=f"{row_num:,}", anchor="e",
                                            font=self.mono_font, fill=self.FOREGROUND_COLOR)
            self.gutter_canvas.create_line(0, y + self.row_height, gw, y + self.row_height,
                                            fill=self.GRID_LINE_COLOR)

    def redraw_canvas(self, event=None):
        self.canvas.delete("all")
        if not self.column_names or self.total_rows == 0:
            return

        canvas_w = self.canvas.winfo_width()
        if canvas_w <= 1:
            return

        y_top, y_bottom = self.canvas.yview()
        start_row = max(0, int(y_top * self.total_rows))
        end_row = min(self.total_rows, int(y_bottom * self.total_rows) + 2)

        x_left_frac, x_right_frac = self.canvas.xview()
        total_w = self._col_offsets[-1]
        if total_w <= 0:
            return
        x_left_px = x_left_frac * total_w
        x_right_px = x_right_frac * total_w + 20

        sel_row = self.selected_cell["row"]
        sel_col = self.selected_cell["col"]

        visible_slice = self.view_df.iloc[start_row:end_row]

        for i, row_vals in enumerate(visible_slice.values):
            data_row = start_row + i
            y = data_row * self.row_height

            # Row stripe
            if data_row % 2 == 0:
                self.canvas.create_rectangle(0, y, total_w, y + self.row_height,
                                              fill=self.STRIPE_COLOR, outline="")

            # Selection
            if data_row == sel_row and sel_col is not None:
                sx = self._col_offsets[sel_col]
                sw = self.col_widths.get(sel_col, 100)
                self.canvas.create_rectangle(sx, y, sx + sw, y + self.row_height,
                                              fill=self.SELECTION_COLOR, outline="")

            # Cells
            for col_idx in range(len(self.column_names)):
                col_x = self._col_offsets[col_idx]
                col_w = self.col_widths.get(col_idx, 100)
                col_right = col_x + col_w

                if col_right < x_left_px or col_x > x_right_px:
                    continue

                display = self._clip_text(
                    self._format_cell(col_idx, row_vals[col_idx]), col_idx, col_w)

                align = self.col_alignments.get(col_idx, "w")
                if align == "e":
                    tx, anchor = col_x + col_w - 5, "e"
                else:
                    tx, anchor = col_x + 5, "w"

                self.canvas.create_text(tx, y + self.row_height / 2,
                                         text=display, anchor=anchor,
                                         font=self._get_font(col_idx),
                                         fill=self.FOREGROUND_COLOR)

                self.canvas.create_line(col_right, y, col_right, y + self.row_height,
                                         fill=self.GRID_LINE_COLOR)

            self.canvas.create_line(0, y + self.row_height, total_w, y + self.row_height,
                                     fill=self.GRID_LINE_COLOR)

    # ================================================================
    # Sorting
    # ================================================================

    def sort_by_column(self, col_index):
        if not self.features_ready or (self.sort_thread and self.sort_thread.is_alive()):
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
        ci = self.sort_info["col_index"]
        asc = self.sort_info["ascending"]
        col_name = self.column_names[ci]
        try:
            col_type = self.col_types.get(ci, "text")
            df = self.view_df
            if col_type == "datetime":
                key = pd.to_datetime(df[col_name], errors="coerce")
            elif col_type in ("int", "float"):
                key = pd.to_numeric(df[col_name], errors="coerce")
            else:
                key = df[col_name]
            sorted_df = df.iloc[key.argsort(kind="mergesort")]
            if not asc:
                sorted_df = sorted_df.iloc[::-1]

            def _apply():
                self.view_df = sorted_df
                self.setup_display()
                self.status_bar.config(text="Sort complete.")
            self.after(0, _apply)
        except Exception as e:
            self.after(0, lambda err=e: messagebox.showerror("Sort Error", str(err)))

    # ================================================================
    # Filtering
    # ================================================================

    def find_data(self):
        if not self.features_ready or (self.filter_thread and self.filter_thread.is_alive()):
            return
        dlg = FilterDialog(self, self.column_names)
        if dlg.result:
            term, column, use_regex = dlg.result
            self.status_bar.config(text=f"Filtering for '{term}'...")
            self.filter_thread = threading.Thread(
                target=self._perform_filter, args=(term, column, use_regex), daemon=True)
            self.filter_thread.start()

    def _perform_filter(self, search_term, column, use_regex):
        t_start = time.perf_counter()
        try:
            cols = [column] if column else list(self.original_data.columns)
            mask = pd.Series(False, index=self.original_data.index)
            for col in cols:
                mask |= self.original_data[col].astype(str).str.contains(
                    search_term, case=False, na=False, regex=use_regex)

            filtered = self.original_data[mask]
            t_end = time.perf_counter()
            count = len(filtered)

            def _apply():
                self.view_df = filtered
                self.total_rows = count
                self.selected_cell = {"row": None, "col": None}
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
        self._update_gutter_width()
        self.canvas.yview_moveto(0)
        self.gutter_canvas.yview_moveto(0)
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
        elif key == "Prior":
            page = max(1, int(self.canvas.winfo_height() / self.row_height) - 1)
            row = max(0, row - page)
        elif key == "Next":
            page = max(1, int(self.canvas.winfo_height() / self.row_height) - 1)
            row = min(max_row, row + page)
        elif key == "Home":
            row = 0
        elif key == "End":
            row = max_row
        elif key == "Tab":
            if event.state & 0x1:
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
        if self.total_rows <= 0:
            return

        y_top, y_bottom = self.canvas.yview()
        row_frac = row / self.total_rows
        visible_frac = y_bottom - y_top
        row_size = 1.0 / self.total_rows

        if row_frac < y_top:
            target = row_frac
            self.canvas.yview_moveto(target)
            self.gutter_canvas.yview_moveto(target)
        elif row_frac + row_size > y_bottom:
            target = max(0, row_frac + row_size - visible_frac)
            self.canvas.yview_moveto(target)
            self.gutter_canvas.yview_moveto(target)

        total_w = self._col_offsets[-1]
        if total_w <= 0:
            return
        x_left, x_right = self.canvas.xview()
        col_left = self._col_offsets[col] / total_w
        col_right = self._col_offsets[col + 1] / total_w
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
            self.selected_cell = {"row": line_num - 1, "col": 0}
            self._schedule_redraw()

    # ================================================================
    # Selection & clipboard
    # ================================================================

    def _on_cell_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        row = int(canvas_y // self.row_height)
        col_idx = bisect.bisect_right(self._col_offsets, canvas_x) - 1

        if row < len(self.view_df) and 0 <= col_idx < len(self.column_names):
            self.selected_cell = {"row": row, "col": col_idx}
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

    def _get_resize_col(self, global_x):
        tolerance = 5
        idx = bisect.bisect_right(self._col_offsets, global_x + tolerance)
        if 1 <= idx < len(self._col_offsets):
            border = self._col_offsets[idx]
            if abs(global_x - border) < tolerance:
                return idx - 1
        idx = bisect.bisect_right(self._col_offsets, global_x - tolerance)
        if 1 <= idx < len(self._col_offsets):
            border = self._col_offsets[idx]
            if abs(global_x - border) < tolerance:
                return idx - 1
        return None

    def _on_header_motion(self, event):
        if self.resizing_col_index is not None:
            return
        cx = self.header_canvas.canvasx(event.x)
        col = self._get_resize_col(cx)
        self.header_canvas.config(cursor="sb_h_double_arrow" if col is not None else "")

    def _on_header_press(self, event):
        cx = self.header_canvas.canvasx(event.x)
        col_to_resize = self._get_resize_col(cx)
        current_time = time.time()

        if (col_to_resize is not None and col_to_resize == self.last_press_col
                and (current_time - self.last_press_time) < 0.3):
            self.auto_fit_column(col_to_resize)
            self.last_press_col = None
            return

        self.last_press_time = current_time
        self.last_press_col = col_to_resize
        self.resizing_col_index = None
        self.potential_sort_click = False

        if col_to_resize is not None:
            self.resizing_col_index = col_to_resize
            self.resize_start_x = event.x
            self.initial_col_width = self.col_widths.get(col_to_resize, 100)
        else:
            self.potential_sort_click = True
            self._potential_sort_x = cx

    def _on_header_drag(self, event):
        if self.resizing_col_index is None:
            return
        self.potential_sort_click = False
        new_w = max(30, self.initial_col_width + (event.x - self.resize_start_x))
        self.col_widths[self.resizing_col_index] = new_w
        self._update_col_offsets()
        total_w = self._col_offsets[-1]
        total_h = self.total_rows * self.row_height
        self.canvas.configure(scrollregion=(0, 0, total_w, total_h))
        self.header_canvas.configure(scrollregion=(0, 0, total_w, 30))
        self._schedule_redraw()

    def _on_header_release(self, event):
        if self.resizing_col_index is not None:
            self.resizing_col_index = None
            self.header_canvas.config(cursor="")
            self._perform_redraw()
        elif self.potential_sort_click:
            idx = bisect.bisect_right(self._col_offsets, self._potential_sort_x) - 1
            if 0 <= idx < len(self.column_names):
                self.sort_by_column(idx)
        self.potential_sort_click = False

    def _on_header_right_click(self, event):
        if not self.column_names:
            return
        cx = self.header_canvas.canvasx(event.x)
        idx = bisect.bisect_right(self._col_offsets, cx) - 1
        if not (0 <= idx < len(self.column_names)):
            return

        menu = tk.Menu(self, tearoff=0)
        col_name = self.column_names[idx]
        menu.add_command(label=f"Auto-fit '{col_name}'",
                         command=lambda c=idx: self.auto_fit_column(c))
        if self.features_ready:
            menu.add_separator()
            menu.add_command(label=f"Filter by '{col_name}'...",
                             command=lambda c=idx: self._find_in_column(c))
        menu.tk_popup(event.x_root, event.y_root)

    def _find_in_column(self, col_index):
        """Open filter dialog with column pre-selected."""
        if not self.features_ready or (self.filter_thread and self.filter_thread.is_alive()):
            return
        dlg = FilterDialog(self, self.column_names)
        if dlg.result:
            term, column, use_regex = dlg.result
            # If user didn't pick a specific column, use the one they right-clicked
            if column is None:
                column = self.column_names[col_index]
            self.status_bar.config(text=f"Filtering for '{term}'...")
            self.filter_thread = threading.Thread(
                target=self._perform_filter, args=(term, column, use_regex), daemon=True)
            self.filter_thread.start()

    def auto_fit_column(self, col_index):
        if not self.features_ready or (self.auto_fit_thread and self.auto_fit_thread.is_alive()):
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
                max_data_w = font.measure(s.loc[s.str.len().idxmax()]) + 15
            new_w = min(max(header_w, max_data_w, 50), 500)

            def _apply():
                self.col_widths[col_index] = new_w
                self._update_col_offsets()
                self.setup_display()
                self.status_bar.config(text="Auto-fit complete.")
            self.after(0, _apply)
        except Exception as e:
            self.after(0, lambda: self.status_bar.config(text=f"Auto-fit failed: {e}"))

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
        messagebox.showinfo("About", "Fast CSV Viewer\nOptimized for large files.")

    def _on_close(self):
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    file_to_open = None
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            file_to_open = arg

    app = CSVViewer()
    if file_to_open:
        app.load_file_from_path(file_to_open)
    app.mainloop()

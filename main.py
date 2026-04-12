import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, font as tkFont
import threading
import sys
import os
import subprocess
import time
import pandas as pd

# --- 1. High DPI Support for Windows ---
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

class CSVViewer(tk.Tk):
    """
    A fast and simple CSV viewer application built with Tkinter.
    It uses a virtualized canvas and two-stage loading with pandas to display large CSV files.
    """
    def __init__(self, debug_colors=False):
        super().__init__()
        self.title("Fast CSV Viewer")
        
        # Calculate screen dimensions and set window size
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        
        window_width = int(screen_width * 0.75)
        window_height = int(screen_height * 0.8)
        
        x_cordinate = int((screen_width / 2) - (window_width / 2))
        y_cordinate = int((screen_height / 2) - (window_height / 2))
        
        self.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")

        self.modifier = "Command" if sys.platform == "darwin" else "Control"
        self.debug_colors = debug_colors

        # --- Setup Theme and Colors ---
        self._setup_theme_colors()

        # Fonts
        self.default_font = tkFont.Font(family="Segoe UI" if os.name == 'nt' else "Helvetica", size=10)
        self.mono_font = tkFont.Font(family="Consolas" if os.name == 'nt' else "Courier New", size=10)
        self.header_font = tkFont.Font(family="Segoe UI" if os.name == 'nt' else "Helvetica", size=10, weight="bold")
        
        self.file_path = None
        self.original_data = pd.DataFrame()
        self.view_df = pd.DataFrame()
        
        self.column_names = []
        self.sort_info = {'col_index': None, 'ascending': True}
        self.col_alignments = {}
        self.col_types = {}
        self.selected_cell = {'row': None, 'col': None}

        # Virtual grid settings
        self.row_height = 25
        self.col_widths = {}
        self.total_rows = 0
        self.features_ready = False
        self._redraw_job = None
        self.sort_thread = None
        self.filter_thread = None
        self.auto_fit_thread = None

        # --- Column Resizing State ---
        self.resizing_col_index = None
        self.resize_start_x = 0
        self.initial_col_width = 0
        self.potential_sort_click = False
        self.last_press_time = 0
        self.last_press_col = None

        self.config(bg=self.BACKGROUND_COLOR)
        self._create_menu()
        self._create_widgets()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _setup_theme_colors(self):
        """Sets up theme-aware colors, with a special check for macOS dark mode."""
        style = ttk.Style(self)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        is_dark_mode = False
        if sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                    capture_output=True, text=True
                )
                if result.stdout.strip() == 'Dark':
                    is_dark_mode = True
            except Exception:
                pass

        if is_dark_mode:
            self.BACKGROUND_COLOR = "#2e2e2e"
            self.FOREGROUND_COLOR = "#dcdcdc"
            self.SELECTION_COLOR = "#4a6fa5"
            self.GRID_LINE_COLOR = "#4a4a4a"
            self.HEADER_BG = "#404040"
            self.HEADER_FG = "#ffffff"
        else:
            self.BACKGROUND_COLOR = "#ffffff"
            self.FOREGROUND_COLOR = "#000000"
            self.SELECTION_COLOR = "#a6d1ff"
            self.GRID_LINE_COLOR = "#e0e0e0"
            self.HEADER_BG = "#e1e1e1"
            self.HEADER_FG = "#000000"

    def _create_menu(self):
        menu_bar = tk.Menu(self)
        self.config(menu=menu_bar)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Open...", command=self.open_file, accelerator=f"{self.modifier}+O")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menu_bar.add_cascade(label="File", menu=file_menu)
        
        self.edit_menu = tk.Menu(menu_bar, tearoff=0)
        self.edit_menu.add_command(label="Find/Filter...", command=self.find_data, accelerator=f"{self.modifier}+F", state="disabled")
        self.edit_menu.add_command(label="Clear Filter", command=self.clear_filter, accelerator="Esc", state="disabled")
        self.edit_menu.add_command(label="Go to Line...", command=self.go_to_line, accelerator=f"{self.modifier}+G")
        self.edit_menu.add_separator()
        self.edit_menu.add_command(label="Copy Cell", command=self.copy_selection, accelerator=f"{self.modifier}+C")
        menu_bar.add_cascade(label="Edit", menu=self.edit_menu)

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)
        
        self.bind(f"<{self.modifier}-o>", lambda event: self.open_file())
        self.bind(f"<{self.modifier}-f>", lambda event: self.find_data())
        self.bind(f"<{self.modifier}-g>", lambda event: self.go_to_line())
        self.bind(f"<{self.modifier}-c>", lambda event: self.copy_selection())
        self.bind("<Escape>", lambda event: self.clear_filter())

    def _create_widgets(self):
        main_frame = tk.Frame(self, bg=self.BACKGROUND_COLOR)
        main_frame.pack(fill="both", expand=True, padx=0, pady=0)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # Header Canvas
        self.header_canvas = tk.Canvas(main_frame, height=30, bd=0, highlightthickness=0, bg=self.HEADER_BG)
        self.header_canvas.grid(row=0, column=0, sticky="ew")
        
        # Bindings for Column Resizing/Sorting
        self.header_canvas.bind("<Motion>", self._on_header_motion)
        self.header_canvas.bind("<ButtonPress-1>", self._on_header_press)
        self.header_canvas.bind("<B1-Motion>", self._on_header_drag)
        self.header_canvas.bind("<ButtonRelease-1>", self._on_header_release)
        
        # Main Data Canvas
        self.canvas = tk.Canvas(main_frame, bg=self.BACKGROUND_COLOR, highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")

        # Scrollbars
        self.vsb = ttk.Scrollbar(main_frame, orient="vertical", command=self.on_vscroll)
        self.hsb = ttk.Scrollbar(main_frame, orient="horizontal", command=self.on_hscroll)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        self.vsb.grid(row=1, column=1, sticky="ns")
        self.hsb.grid(row=2, column=0, sticky="ew")
        
        # Status Bar
        self.status_bar = ttk.Label(self, text="Ready", anchor=tk.W, padding=(5, 2), relief=tk.SUNKEN)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas.bind("<Configure>", self.redraw_canvas)
        # Use specific binding to avoid conflicts
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel) # Linux
        self.canvas.bind("<Button-5>", self._on_mousewheel) # Linux
        self.canvas.bind("<Button-1>", self._on_cell_click)

    def _schedule_redraw(self):
        if self._redraw_job:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(10, self._perform_redraw)

    def _perform_redraw(self):
        self._redraw_job = None
        self._redraw_header()
        self.redraw_canvas()

    def on_vscroll(self, *args):
        self.canvas.yview(*args)
        self._schedule_redraw()

    def on_hscroll(self, *args):
        self.canvas.xview(*args)
        self.header_canvas.xview(*args)
        self._schedule_redraw()

    def _on_mousewheel(self, event):
        if sys.platform == "darwin":
            self.canvas.yview_scroll(-int(event.delta), "units")
        elif event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-3, "units")
        self._schedule_redraw()

    def open_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if file_path: self.load_file_from_path(file_path)

    def load_file_from_path(self, file_path):
        if not os.path.exists(file_path):
            messagebox.showerror("Error", f"File not found:\n{file_path}")
            return

        self._reset_state()
        self.file_path = file_path
        self.title(f"Fast CSV Viewer - {os.path.basename(self.file_path)}")
        self.status_bar.config(text=f"Opening {self.file_path}...")
        
        thread = threading.Thread(target=self._load_csv_data, daemon=True)
        thread.start()

    def _reset_state(self):
        self.original_data = pd.DataFrame()
        self.view_df = pd.DataFrame()
        self.column_names = []
        self.sort_info = {'col_index': None, 'ascending': True}
        self.features_ready = False
        self.edit_menu.entryconfig("Find/Filter...", state="disabled")
        self.edit_menu.entryconfig("Clear Filter", state="disabled")
        self.last_press_time = 0
        self.last_press_col = None
        self.canvas.delete("all")
        self.header_canvas.delete("all")

    def _read_csv(self, **kwargs):
        """Read CSV with automatic encoding fallback."""
        try:
            return pd.read_csv(self.file_path, encoding='utf-8', **kwargs)
        except UnicodeDecodeError:
            return pd.read_csv(self.file_path, encoding='latin-1', **kwargs)

    def _load_csv_data(self):
        INITIAL_LOAD_ROWS = 200
        CHUNK_SIZE = 50000

        try:
            # Stage 1: Fast initial read
            initial_chunk_df = self._read_csv(
                nrows=INITIAL_LOAD_ROWS, on_bad_lines='skip', engine='c'
            )

            column_names = initial_chunk_df.columns.tolist()
            self._detect_column_types(initial_chunk_df)
            self._estimate_col_widths(initial_chunk_df)

            # Marshal state to main thread
            def _apply_preview():
                self.column_names = column_names
                self.original_data = initial_chunk_df
                self.view_df = self.original_data
                self.total_rows = len(self.view_df)
                self.setup_display()
                self.status_bar.config(text="Loaded preview. Reading full file...")
            self.after(0, _apply_preview)

            # Stage 2: Load rest
            reader = self._read_csv(
                chunksize=CHUNK_SIZE, iterator=True,
                low_memory=False, on_bad_lines='skip',
                engine='c', header=0, skiprows=range(1, INITIAL_LOAD_ROWS + 1)
            )

            chunk_dfs = [initial_chunk_df]
            for chunk_df in reader:
                chunk_dfs.append(chunk_df)

            full_data = pd.concat(chunk_dfs, ignore_index=True)
            self.after(0, lambda: self._finalize_load(full_data))

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to read file: {e}"))

    def _finalize_load(self, full_data):
        self.original_data = full_data
        self.view_df = self.original_data
        self.total_rows = len(self.original_data)
        self._estimate_col_widths(self.original_data)
        self.features_ready = True
        self.edit_menu.entryconfig("Find/Filter...", state="normal")
        self.edit_menu.entryconfig("Clear Filter", state="normal")
        self.setup_display()
        self.status_bar.config(text=f"Ready. {self.total_rows:,} rows.")

    def _estimate_col_widths(self, df):
        self.col_widths = {}
        df_sample = df.iloc[:100] 

        for i, col_name in enumerate(self.column_names):
            header_width = self.header_font.measure(col_name) + 30
            col_type = self.col_types.get(i, 'text')
            font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font
            
            col_data = df_sample[col_name]
            if col_data.empty:
                max_data_width = 0
            else:
                str_series = col_data.astype(str)
                longest_str_index = str_series.str.len().idxmax()
                value_to_measure = str_series.loc[longest_str_index]
                
                # Format check for accurate measurement
                if col_type == 'int':
                    try: value_to_measure = f"{int(float(value_to_measure)):,}"
                    except (ValueError, TypeError): pass
                elif col_type == 'float':
                    try: value_to_measure = f"{float(value_to_measure):,.2f}"
                    except (ValueError, TypeError): pass

                max_data_width = font_to_use.measure(value_to_measure) + 15

            self.col_widths[i] = min(max(header_width, max_data_width, 50), 400)

    def _detect_column_types(self, df):
        self.col_alignments = {}
        self.col_types = {}
        
        # Simple heuristic using pandas types
        for i, col_name in enumerate(df.columns):
            dtype = df[col_name].dtype
            col_type = 'text'
            if pd.api.types.is_integer_dtype(dtype): col_type = 'int'
            elif pd.api.types.is_float_dtype(dtype): col_type = 'float'
            elif pd.api.types.is_datetime64_any_dtype(dtype): col_type = 'datetime'
            
            self.col_types[i] = col_type
            self.col_alignments[i] = 'e' if col_type in ['int', 'float'] else 'w'

    def setup_display(self):
        self._redraw_header()
        self.update_scrollregion()
        self.redraw_canvas()
        self.deiconify()
        self.lift()

    def update_scrollregion(self):
        total_width = sum(self.col_widths.values())
        total_height = self.total_rows * self.row_height
        self.canvas.configure(scrollregion=(0, 0, total_width, total_height))
        self.header_canvas.configure(scrollregion=(0, 0, total_width, self.header_canvas.winfo_height()))

    def _redraw_header(self):
        self.header_canvas.delete("all")
        x_left = self.header_canvas.xview()[0]
        scroll_width = sum(self.col_widths.values())
        x_offset = -int(x_left * scroll_width)
        current_x = 0
        
        canvas_height = self.header_canvas.winfo_height()

        for i, col_name in enumerate(self.column_names):
            col_width = self.col_widths.get(i, 100)
            
            # Only draw if visible horizontally
            if (current_x + col_width + x_offset > 0) and (current_x + x_offset < self.header_canvas.winfo_width()):
                text = col_name
                if self.sort_info['col_index'] == i:
                    text += ' ▲' if self.sort_info['ascending'] else ' ▼'
                
                align = self.col_alignments.get(i, 'w')
                text_x = current_x + x_offset + (col_width - 10 if align == 'e' else 10)
                anchor = 'e' if align == 'e' else 'w'

                self.header_canvas.create_text(text_x, canvas_height / 2,
                    text=text, anchor=anchor, font=self.header_font, fill=self.HEADER_FG)
                
                # Divider line
                line_x = current_x + col_width + x_offset
                self.header_canvas.create_line(line_x, 4, line_x, canvas_height - 4, fill=self.GRID_LINE_COLOR)

            current_x += col_width

    def redraw_canvas(self, event=None):
        self.canvas.delete("all")
        if self.view_df.empty: return

        y_top, y_bottom = self.canvas.yview()
        x_left, _ = self.canvas.xview()
        
        start_row = int(y_top * self.total_rows)
        end_row = int(y_bottom * self.total_rows) + 2

        scrollregion_str = self.canvas.cget("scrollregion")
        if not scrollregion_str: return
        scroll_width = int(scrollregion_str.split(' ')[2])
        x_offset = -int(x_left * scroll_width)
        
        # Optimize: Get slice and values
        visible_df_slice = self.view_df.iloc[start_row:min(end_row, len(self.view_df))]
        visible_rows_list = visible_df_slice.fillna('').values.tolist()
        
        for i, row_data in enumerate(visible_rows_list):
            row_idx = start_row + i
            y = row_idx * self.row_height
            
            # 1. Draw Selection
            if row_idx == self.selected_cell['row']:
                col_to_highlight = self.selected_cell['col']
                highlight_x = sum(self.col_widths.get(j, 100) for j in range(col_to_highlight))
                highlight_width = self.col_widths.get(col_to_highlight, 100)
                self.canvas.create_rectangle(highlight_x + x_offset, y,
                    highlight_x + highlight_width + x_offset, y + self.row_height,
                    fill=self.SELECTION_COLOR, outline="")

            # 2. Draw Text
            current_x = 0
            canvas_width = self.canvas.winfo_width()
            for col_idx, cell_value in enumerate(row_data):
                col_width = self.col_widths.get(col_idx, 100)
                col_right = current_x + col_width + x_offset
                col_left = current_x + x_offset

                # Visibility Check
                if col_right > 0 and col_left < canvas_width:
                    align = self.col_alignments.get(col_idx, 'w')
                    col_type = self.col_types.get(col_idx, 'text')
                    font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font

                    display_value = str(cell_value)
                    if col_type == 'int':
                        try: display_value = f"{int(cell_value):,}"
                        except (ValueError, TypeError): pass
                    elif col_type == 'float':
                        try: display_value = f"{float(cell_value):,.2f}"
                        except (ValueError, TypeError): pass

                    text_x = current_x + x_offset + (col_width - 5 if align == 'e' else 5)
                    anchor = 'e' if align == 'e' else 'w'

                    self.canvas.create_text(text_x, y + self.row_height / 2,
                        anchor=anchor, text=display_value, fill=self.FOREGROUND_COLOR, font=font_to_use)

                    # Grid line (only for visible columns)
                    self.canvas.create_line(col_right, y, col_right, y + self.row_height,
                                            fill=self.GRID_LINE_COLOR)

                current_x += col_width
            
            # Horizontal Grid Line (Bottom of row)
            self.canvas.create_line(0, y + self.row_height, scroll_width, y + self.row_height, fill=self.GRID_LINE_COLOR)

    def sort_by_column(self, col_index):
        if not self.features_ready: return
        if self.sort_thread and self.sort_thread.is_alive(): return

        if self.sort_info['col_index'] == col_index:
            self.sort_info['ascending'] = not self.sort_info['ascending']
        else:
            self.sort_info['col_index'] = col_index
            self.sort_info['ascending'] = True

        self.status_bar.config(text=f"Sorting by column '{self.column_names[col_index]}'...")
        self.sort_thread = threading.Thread(target=self._perform_sort, daemon=True)
        self.sort_thread.start()

    def _perform_sort(self):
        col_index = self.sort_info['col_index']
        ascending = self.sort_info['ascending']
        col_name = self.column_names[col_index]

        try:
            col_type = self.col_types.get(col_index, 'text')
            df = self.view_df

            if col_type == 'datetime':
                sort_key = pd.to_datetime(df[col_name], errors='coerce')
            elif col_type in ['int', 'float']:
                sort_key = pd.to_numeric(df[col_name], errors='coerce')
            else:
                sort_key = df[col_name]

            sorted_df = df.iloc[sort_key.argsort(kind='mergesort')]
            if not ascending:
                sorted_df = sorted_df.iloc[::-1]

            def _apply_sort():
                self.view_df = sorted_df
                self.setup_display()
                self.status_bar.config(text="Sort complete.")
            self.after(0, _apply_sort)
        except Exception as e:
            self.after(0, lambda err=e: messagebox.showerror("Sort Error", f"{err}"))

    def find_data(self):
        if not self.features_ready: return
        if self.filter_thread and self.filter_thread.is_alive(): return
        
        search_term = simpledialog.askstring("Find/Filter", "Enter text to find:", parent=self)
        if search_term:
            self.status_bar.config(text=f"Filtering for '{search_term}'...")
            self.filter_thread = threading.Thread(target=self._perform_filter, args=(search_term,), daemon=True)
            self.filter_thread.start()

    def _perform_filter(self, search_term):
        """
        Optimized filtering:
        1. Avoids converting the entire DataFrame to string at once (saves RAM).
        2. Uses vectorized column operations instead of row-wise 'apply' (saves CPU).
        """
        t_start = time.perf_counter()
        
        # Initialize a mask of all False (no matches yet)
        mask = pd.Series(False, index=self.original_data.index)
        
        # Iterate over columns to build the mask incrementally
        for col in self.original_data.columns:
            series = self.original_data[col]
            
            # specific optimization for Object/String columns
            if pd.api.types.is_string_dtype(series) or series.dtype == 'object':
                # 'na=False' treats NaN as not matching, preventing errors
                col_matches = series.str.contains(search_term, case=False, na=False)
            else:
                # Only convert non-string columns to string temporarily for this comparison
                col_matches = series.astype(str).str.contains(search_term, case=False, na=False)
            
            # Combine with existing matches using logical OR
            mask |= col_matches

        filtered_df = self.original_data[mask]
        t_end = time.perf_counter()
        match_count = len(filtered_df)

        def _apply_filter():
            self.view_df = filtered_df
            self.total_rows = match_count
            self.selected_cell = {'row': None, 'col': None}
            self.setup_display()
            self.status_bar.config(text=f"Found {match_count:,} matches in {t_end - t_start:.4f}s")
        self.after(0, _apply_filter)

    def clear_filter(self):
        if not self.features_ready: return
        self.view_df = self.original_data
        self.total_rows = len(self.original_data)
        self.selected_cell = {'row': None, 'col': None}
        self.status_bar.config(text="Filter cleared.")
        self.canvas.yview_moveto(0)
        self.setup_display()

    def go_to_line(self):
        if self.original_data.empty: return
        line_num = simpledialog.askinteger("Go to Line", f"Row (1 - {self.total_rows}):", parent=self, minvalue=1, maxvalue=self.total_rows)
        if line_num:
            fraction = max(0.0, min((line_num - 1) / max(self.total_rows, 1), 1.0 - 1e-9))
            self.canvas.yview_moveto(fraction)
            self.redraw_canvas()

    def copy_selection(self):
        sel = self.selected_cell
        if sel['row'] is not None and sel['col'] is not None:
            try:
                value = self.view_df.iloc[sel['row'], sel['col']]
                self.clipboard_clear()
                self.clipboard_append(str(value))
                self.status_bar.config(text=f"Copied to clipboard.")
            except IndexError:
                pass

    def _get_resize_col(self, canvas_x):
        current_x = 0
        tolerance = 5
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            if abs(canvas_x - (current_x + col_width)) < tolerance:
                return i
            current_x += col_width
        return None

    def _on_header_motion(self, event):
        if self.resizing_col_index is not None: return
        canvas_x = self.header_canvas.canvasx(event.x)
        col = self._get_resize_col(canvas_x)
        self.header_canvas.config(cursor="sb_h_double_arrow" if col is not None else "")

    def _on_header_press(self, event):
        canvas_x = self.header_canvas.canvasx(event.x)
        col_to_resize = self._get_resize_col(canvas_x)
        current_time = time.time()
        
        # Double click check for auto-fit
        if col_to_resize is not None and col_to_resize == self.last_press_col and (current_time - self.last_press_time) < 0.3:
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
            self.potential_sort_x = canvas_x

    def _on_header_drag(self, event):
        if self.resizing_col_index is None: return
        self.potential_sort_click = False
        new_width = max(20, self.initial_col_width + (event.x - self.resize_start_x))
        self.col_widths[self.resizing_col_index] = new_width
        self.update_scrollregion()
        self._schedule_redraw()

    def _on_header_release(self, event):
        if self.resizing_col_index is not None:
            self.resizing_col_index = None
            self.header_canvas.config(cursor="")
            self._perform_redraw()
        elif self.potential_sort_click:
            self._on_header_click_logic(self.potential_sort_x)
        self.potential_sort_click = False

    def _on_header_click_logic(self, canvas_x):
        current_x = 0
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            if current_x <= canvas_x < current_x + col_width:
                self.sort_by_column(i)
                break
            current_x += col_width

    def auto_fit_column(self, col_index):
        if not self.features_ready: return
        if self.auto_fit_thread and self.auto_fit_thread.is_alive(): return
        
        self.status_bar.config(text=f"Auto-fitting...")
        self.auto_fit_thread = threading.Thread(target=self._perform_auto_fit, args=(col_index,), daemon=True)
        self.auto_fit_thread.start()

    def _perform_auto_fit(self, col_index):
        try:
            col_name = self.column_names[col_index]
            col_type = self.col_types.get(col_index, 'text')
            font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font

            header_width = self.header_font.measure(col_name) + 30

            s = self.original_data[col_name].astype(str)
            if s.empty: max_data_width = 0
            else:
                val = s.loc[s.str.len().idxmax()]
                max_data_width = font_to_use.measure(val) + 15

            self.col_widths[col_index] = min(max(header_width, max_data_width, 50), 500)
            self.after(0, self._finalize_auto_fit)
        except Exception as e:
            self.after(0, lambda: self.status_bar.config(text=f"Auto-fit failed: {e}"))

    def _finalize_auto_fit(self):
        self.update_scrollregion()
        self._perform_redraw()
        self.status_bar.config(text="Auto-fit complete.")

    def _on_cell_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        row = int(canvas_y // self.row_height)
        
        current_x = 0
        col = None
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            if current_x <= canvas_x < current_x + col_width:
                col = i
                break
            current_x += col_width

        if row < len(self.view_df) and col is not None:
            self.selected_cell = {'row': row, 'col': col}
        else:
            self.selected_cell = {'row': None, 'col': None}
        self.redraw_canvas()

    def show_about(self):
        messagebox.showinfo("About", "Fast CSV Viewer\nOptimized for large files.")

if __name__ == "__main__":
    debug_mode = False
    file_to_open = None
    
    args = sys.argv[1:]
    for arg in args:
        if arg == '--debug-colors': debug_mode = True
        elif not arg.startswith('-'): file_to_open = arg
            
    app = CSVViewer(debug_colors=debug_mode)
    if file_to_open: app.load_file_from_path(file_to_open)
    app.mainloop()

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, font as tkFont
import csv
import threading
import sys
import os
import subprocess
import time
import pandas as pd
from datetime import datetime

class CSVViewer(tk.Tk):
    """
    A fast and simple CSV viewer application built with Tkinter.
    It uses a virtualized canvas and two-stage loading with pandas to display large CSV files.
    """
    def __init__(self):
        super().__init__()
        self.title("Fast CSV Viewer")
        self.geometry("800x600")

        self.modifier = "Command" if sys.platform == "darwin" else "Control"

        # --- Setup Theme and Colors ---
        self._setup_theme_colors()

        # Fonts
        self.default_font = tkFont.Font(family="Helvetica", size=12)
        self.mono_font = tkFont.Font(family="Courier New", size=12)
        self.header_font = tkFont.Font(family="Helvetica", size=12, weight="bold")
        
        # Common date and datetime formats to check against
        self.DATETIME_FORMATS = [
            '%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%Y-%m-%d %H:%M',
            '%m/%d/%Y %I:%M %p', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d',
            '%m/%d/%Y', '%m-%d-%Y', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y'
        ]

        self.file_path = None
        self.original_data = pd.DataFrame() # Holds the original, unfiltered data as a DataFrame
        self.view_df = pd.DataFrame() # Holds the current view (sorted or filtered) of the data
        
        self.column_names = []
        self.sort_info = {'col_index': None, 'ascending': True}
        self.col_alignments = {}
        self.col_types = {}
        self.selected_cell = {'row': None, 'col': None}

        # Virtual grid settings
        self.row_height = 20
        self.col_widths = {}
        self.total_rows = 0
        self.features_ready = False # Flag for when sorting/filtering is available
        self._redraw_job = None # For debouncing redraw calls
        self.sort_thread = None # To manage sort thread
        self.filter_thread = None # To manage filter thread

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
            print("Clam theme not available, using default.")

        is_dark_mode = False
        if sys.platform == "darwin":
            try:
                cmd = 'defaults read -g AppleInterfaceStyle'
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, shell=True)
                output, _ = p.communicate()
                if output.strip().decode() == 'Dark':
                    is_dark_mode = True
            except Exception as e:
                print(f"Could not detect dark mode on macOS: {e}")

        if is_dark_mode:
            self.BACKGROUND_COLOR = "#2e2e2e"
            self.FOREGROUND_COLOR = "#dcdcdc"
            self.SELECTION_COLOR = "#5a5a5a"
            self.GRID_LINE_COLOR = "#4a4a4a"
        else:
            self.BACKGROUND_COLOR = style.lookup('TFrame', 'background')
            self.FOREGROUND_COLOR = style.lookup('TLabel', 'foreground')
            self.SELECTION_COLOR = style.lookup('TEntry', 'selectbackground')
            self.GRID_LINE_COLOR = style.lookup('TLabel', 'foreground', ('disabled',))

    def _create_menu(self):
        """Creates the menu bar for the application."""
        menu_bar = tk.Menu(self)
        self.config(menu=menu_bar)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Open...", command=self.open_file, accelerator=f"{self.modifier}+O")
        file_menu.add_command(label="Close Window", command=self.destroy, accelerator=f"{self.modifier}+W")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menu_bar.add_cascade(label="File", menu=file_menu)
        
        self.edit_menu = tk.Menu(menu_bar, tearoff=0)
        self.edit_menu.add_command(label="Find/Filter...", command=self.find_data, accelerator=f"{self.modifier}+F", state="disabled")
        self.edit_menu.add_command(label="Clear Filter", command=self.clear_filter, accelerator="Esc", state="disabled")
        self.edit_menu.add_command(label="Go to Line...", command=self.go_to_line, accelerator=f"{self.modifier}+G")
        self.edit_menu.add_separator()
        self.edit_menu.add_command(label="Copy", command=self.copy_selection, accelerator=f"{self.modifier}+C")
        menu_bar.add_cascade(label="Edit", menu=self.edit_menu)

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)
        
        self.bind(f"<{self.modifier}-o>", lambda event: self.open_file())
        self.bind(f"<{self.modifier}-w>", lambda event: self.destroy())
        self.bind(f"<{self.modifier}-f>", lambda event: self.find_data())
        self.bind(f"<{self.modifier}-g>", lambda event: self.go_to_line())
        self.bind(f"<{self.modifier}-c>", lambda event: self.copy_selection())
        self.bind("<Escape>", lambda event: self.clear_filter())

    def _create_widgets(self):
        """Creates the main widgets for the application."""
        main_frame = tk.Frame(self, bg=self.BACKGROUND_COLOR)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)

        self.header_canvas = tk.Canvas(main_frame, height=25, bd=0, highlightthickness=0, bg=self.BACKGROUND_COLOR)
        self.header_canvas.grid(row=0, column=0, sticky="ew")
        self.header_canvas.bind("<Button-1>", self._on_header_click)
        
        self.canvas = tk.Canvas(main_frame, bg=self.BACKGROUND_COLOR, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(main_frame, orient="vertical", command=self.on_vscroll)
        hsb = ttk.Scrollbar(main_frame, orient="horizontal", command=self.on_hscroll)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")
        
        ttk.Separator(self, orient='horizontal').pack(side=tk.BOTTOM, fill='x', padx=5)

        self.status_bar = ttk.Label(self, text="Ready", anchor=tk.W, padding=(5, 2))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas.bind("<Configure>", self.redraw_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-1>", self._on_cell_click)

    def _schedule_redraw(self):
        """Schedules a redraw, debouncing rapid calls to prevent redundant drawing."""
        if self._redraw_job:
            self.after_cancel(self._redraw_job)
        # Use a minimal delay to allow multiple scroll events in one event loop
        # cycle to be handled by a single redraw.
        self._redraw_job = self.after(1, self._perform_redraw)

    def _perform_redraw(self):
        """Executes the actual redraw of the canvas and header."""
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
        if event.num == 5 or event.delta < 0: self.canvas.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0: self.canvas.yview_scroll(-3, "units")
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
        """Resets the application state for a new file."""
        self.original_data = pd.DataFrame()
        self.view_df = pd.DataFrame()
        self.column_names = []
        self.sort_info = {'col_index': None, 'ascending': True}
        self.features_ready = False
        self.edit_menu.entryconfig("Find/Filter...", state="disabled")
        self.edit_menu.entryconfig("Clear Filter", state="disabled")

    def _load_csv_data(self):
        """Loads data in two stages using pandas for speed and robustness."""
        INITIAL_LOAD_ROWS = 2000
        CHUNK_SIZE = 10000 
        try:
            # --- Stage 1: Fast initial read using nrows for instant display ---
            initial_chunk_df = pd.read_csv(
                self.file_path,
                nrows=INITIAL_LOAD_ROWS,
                encoding='utf-8',
                on_bad_lines='skip',
                engine='c'
            )
            
            self.column_names = initial_chunk_df.columns.tolist()
            self.original_data = initial_chunk_df
            self.view_df = self.original_data
            
            self.total_rows = len(self.view_df)

            self._detect_column_types(initial_chunk_df)
            self._estimate_col_widths(self.view_df) # Pass the DataFrame directly
            self.after(0, self.setup_display)
            self.after(0, lambda: self.status_bar.config(text=f"Showing first {self.total_rows:,} rows. Loading full file..."))
            
            # --- Stage 2: Create a new iterator to load the rest of the file ---
            reader = pd.read_csv(
                self.file_path, 
                chunksize=CHUNK_SIZE, 
                iterator=True, 
                low_memory=False,
                encoding='utf-8', 
                on_bad_lines='skip',
                engine='c',
                header=0,
                skiprows=range(1, INITIAL_LOAD_ROWS + 1)
            )
            
            chunk_dfs = [self.original_data]
            for chunk_df in reader:
                chunk_dfs.append(chunk_df)
                time.sleep(0.001) # Yield to the UI thread
            
            self.original_data = pd.concat(chunk_dfs, ignore_index=True)

            self.after(0, self._finalize_load)

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to read file: {e}"))

    def _finalize_load(self):
        """Called after the full file is loaded and features are enabled."""
        self.total_rows = len(self.original_data)
        self.view_df = self.original_data
        
        # Enable features
        self.features_ready = True
        self.edit_menu.entryconfig("Find/Filter...", state="normal")
        self.edit_menu.entryconfig("Clear Filter", state="normal")
        
        self.setup_display()
        self.status_bar.config(text=f"Ready. {self.total_rows:,} rows.")
        print("Full data loaded. Features enabled.")

    def _estimate_col_widths(self, df):
        """Estimate column widths based on header and first 100 rows of a DataFrame."""
        self.col_widths = {}
        for i, col_name in enumerate(self.column_names):
            header_width = self.header_font.measure(col_name) + 30
            max_data_width = 0
            col_type = self.col_types.get(i, 'text')
            font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font
            
            # Use itertuples for memory-efficient iteration over the first 100 rows
            for row in df.iloc[:100].itertuples(index=False, name=None):
                if i < len(row):
                    cell_value = str(row[i]) if pd.notna(row[i]) else ""
                    cell_width = font_to_use.measure(cell_value) + 15
                    if cell_width > max_data_width: max_data_width = cell_width
            self.col_widths[i] = max(header_width, max_data_width, 50)

    def _detect_column_types(self, df):
        """Uses pandas dtypes for robust and fast type detection."""
        self.col_alignments = {}
        self.col_types = {}
        
        df_converted = df.copy()
        for col_name in df_converted.columns:
            # Attempt to convert to numeric first.
            numeric_col = pd.to_numeric(df[col_name], errors='coerce')
            
            # If at least one value is numeric, we'll treat it as numeric.
            if numeric_col.notna().any():
                df_converted[col_name] = numeric_col
            else:
                # If no values are numeric, try converting to datetime.
                datetime_col = pd.to_datetime(df[col_name], errors='coerce')
                # Only if at least one value is a valid datetime, we treat it as datetime.
                if datetime_col.notna().any():
                    df_converted[col_name] = datetime_col
                # Otherwise, it remains as the original 'object' type, which we'll treat as text.
        
        for i, col_name in enumerate(df.columns):
            dtype = df_converted[col_name].dtype
            col_type = 'text'
            if pd.api.types.is_integer_dtype(dtype):
                col_type = 'int'
            elif pd.api.types.is_float_dtype(dtype):
                col_type = 'float'
            elif pd.api.types.is_datetime64_any_dtype(dtype):
                col_type = 'datetime'
            
            self.col_types[i] = col_type
            if col_type in ['int', 'float', 'datetime']:
                self.col_alignments[i] = 'e'
            else:
                self.col_alignments[i] = 'w'

    def setup_display(self):
        self._redraw_header()
        total_width = sum(self.col_widths.values())
        total_height = self.total_rows * self.row_height
        self.canvas.configure(scrollregion=(0, 0, total_width, total_height))
        self.header_canvas.configure(scrollregion=(0, 0, total_width, self.header_canvas.winfo_height()))
        self.redraw_canvas()

    def _redraw_header(self):
        self.header_canvas.delete("all")
        x_left = self.header_canvas.xview()[0]
        scroll_width = sum(self.col_widths.values())
        x_offset = -int(x_left * scroll_width)
        current_x = 0
        for i, col_name in enumerate(self.column_names):
            col_width = self.col_widths.get(i, 100)
            text = col_name
            if self.sort_info['col_index'] == i:
                text += ' ▲' if self.sort_info['ascending'] else ' ▼'
            
            align = self.col_alignments.get(i, 'w')
            padding = 10
            if align == 'e':
                anchor, text_x = 'e', current_x + x_offset + col_width - padding
            else:
                anchor, text_x = 'w', current_x + x_offset + padding

            self.header_canvas.create_text(text_x, self.header_canvas.winfo_height()/2,
                text=text, anchor=anchor, font=self.header_font, fill=self.FOREGROUND_COLOR)
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

        # Get the slice of the DataFrame to draw and convert ONLY that to a list
        visible_df_slice = self.view_df.iloc[start_row:min(end_row, len(self.view_df))]
        visible_rows_list = visible_df_slice.fillna('').values.tolist()
        
        for i, row_data in enumerate(visible_rows_list):
            row_idx = start_row + i # The actual index in the dataframe/view
            y = row_idx * self.row_height
            
            # The Fix: Draw each line individually to prevent the zig-zag effect.
            self.canvas.create_line(0, y + self.row_height, scroll_width, y + self.row_height, fill=self.GRID_LINE_COLOR)

            current_x = 0
            if row_idx == self.selected_cell['row']:
                 col_to_highlight = self.selected_cell['col']
                 highlight_x = sum(self.col_widths.get(j, 100) for j in range(col_to_highlight))
                 highlight_width = self.col_widths.get(col_to_highlight, 100)
                 self.canvas.create_rectangle(highlight_x + x_offset, y,
                    highlight_x + highlight_width + x_offset, y + self.row_height,
                    fill=self.SELECTION_COLOR, outline="")

            for col_idx, cell_value in enumerate(row_data):
                col_width = self.col_widths.get(col_idx, 100)
                if (current_x + col_width + x_offset > 0) and (current_x + x_offset < self.canvas.winfo_width()):
                    align = self.col_alignments.get(col_idx, 'w')
                    col_type = self.col_types.get(col_idx, 'text')
                    font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font
                    
                    display_value = str(cell_value)
                    if col_type == 'float':
                        try: display_value = f"{float(str(cell_value).replace(',', '')):.2f}"
                        except (ValueError, TypeError): pass

                    padding = 5
                    if align == 'e':
                        anchor, text_x = 'e', current_x + x_offset + col_width - padding
                    else:
                        anchor, text_x = 'w', current_x + x_offset + padding

                    self.canvas.create_text(text_x, y + self.row_height / 2,
                        anchor=anchor, text=display_value, fill=self.FOREGROUND_COLOR, font=font_to_use)
                current_x += col_width

    def sort_by_column(self, col_index):
        if not self.features_ready:
            self.status_bar.config(text="Please wait for data to load before sorting.")
            return
            
        # --- Thread Safety Guard ---
        if self.sort_thread and self.sort_thread.is_alive():
            self.status_bar.config(text="A sort is already in progress...")
            return
        # -------------------------

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
            # Use pandas for high-performance sorting
            col_type = self.col_types.get(col_index, 'text')
            temp_df = self.view_df.copy()

            # Convert column to appropriate type for sorting
            if col_type == 'datetime':
                temp_df[col_name] = pd.to_datetime(temp_df[col_name], errors='coerce')
            elif col_type in ['int', 'float']:
                temp_df[col_name] = pd.to_numeric(temp_df[col_name], errors='coerce')
            
            sorted_df = temp_df.sort_values(by=col_name, ascending=ascending, na_position='first')
            
            self.view_df = sorted_df

            self.after(0, self.setup_display)
            self.after(0, lambda: self.status_bar.config(text="Sort complete."))
        except Exception as e:
            self.after(0, lambda err=e: messagebox.showerror("Sort Error", f"An error occurred: {err}"))

    def find_data(self):
        if not self.features_ready:
             self.status_bar.config(text="Please wait for data to load before filtering.")
             return
        
        # --- Thread Safety Guard ---
        if self.filter_thread and self.filter_thread.is_alive():
            self.status_bar.config(text="A filter is already in progress...")
            return
        # -------------------------
        
        search_term = simpledialog.askstring("Find/Filter", "Enter text to find:", parent=self)
        if search_term:
            self.status_bar.config(text=f"Filtering for '{search_term}'...")
            self.filter_thread = threading.Thread(target=self._perform_filter, args=(search_term,), daemon=True) # Pass original case
            self.filter_thread.start()

    def _perform_filter(self, search_term):
        """Performs filtering using pandas for efficiency."""
        # This optimized approach converts each column to string once, which is much faster.
        mask = self.original_data.apply(
            lambda col: col.astype(str).str.contains(search_term, case=False, na=False)
        ).any(axis=1)
        
        self.view_df = self.original_data[mask]
        self.total_rows = len(self.view_df)
        
        self.selected_cell = {'row': None, 'col': None}
        self.sort_info = {'col_index': None, 'ascending': True}
        
        self.after(0, self.setup_display)
        self.after(0, lambda: self.status_bar.config(text=f"Found {self.total_rows:,} matching rows."))

    def clear_filter(self):
        if not self.features_ready: return
        self.view_df = self.original_data
        self.total_rows = len(self.original_data)
        
        self.selected_cell = {'row': None, 'col': None}
        self.sort_info = {'col_index': None, 'ascending': True}
        self.status_bar.config(text="Filter cleared.")

        self.canvas.yview_moveto(0)
        self.setup_display()

    def go_to_line(self):
        if self.original_data.empty: return
        
        max_rows = self.total_rows
        line_num = simpledialog.askinteger("Go to Line", 
            f"Enter line number (1 - {max_rows}):",
            parent=self, minvalue=1, maxvalue=max_rows)
        if line_num:
            fraction = (line_num - 1) / self.total_rows if self.total_rows > 0 else 0
            self.canvas.yview_moveto(fraction)
            self.redraw_canvas()

    def copy_selection(self):
        sel = self.selected_cell
        if sel['row'] is not None and sel['col'] is not None:
            try:
                value = self.view_df.iloc[sel['row'], sel['col']]
                self.clipboard_clear()
                self.clipboard_append(str(value))
                self.status_bar.config(text=f"Copied '{value}' to clipboard.")
            except IndexError:
                self.status_bar.config(text="Cannot copy cell, data out of sync.")

    def _on_header_click(self, event):
        canvas_x = self.header_canvas.canvasx(event.x)
        current_x, col = 0, None
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            if current_x <= canvas_x < current_x + col_width:
                col = i
                break
            current_x += col_width
        if col is not None: self.sort_by_column(col)

    def _on_cell_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)

        row = int(canvas_y // self.row_height)
        
        current_x, col = 0, None
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
        messagebox.showinfo("About Fast CSV Viewer",
            "A simple, fast CSV viewer for large files.\n\nBuilt with Python and Tkinter.")

if __name__ == "__main__":
    app = CSVViewer()
    if len(sys.argv) > 1:
        app.after(100, lambda: app.load_file_from_path(sys.argv[1]))
    app.mainloop()











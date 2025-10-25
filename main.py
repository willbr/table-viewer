import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, font as tkFont
import threading
import sys
import os
import subprocess
import time
import pandas as pd

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
        
        window_width = int(screen_width * 0.6)
        window_height = int(screen_height * 0.8)
        
        # Calculate position for centering
        x_cordinate = int((screen_width / 2) - (window_width / 2))
        y_cordinate = int((screen_height / 2) - (window_height / 2))
        
        self.geometry(f"{window_width}x{window_height}+{x_cordinate}+{y_cordinate}")

        self.modifier = "Command" if sys.platform == "darwin" else "Control"
        
        self.debug_colors = debug_colors # Store the CLI flag

        # --- Debug Colors ---
        self.DEBUG_COLOR_START = '#FFCCCC' # Light Red
        self.DEBUG_COLOR_STAGE1 = '#FFFFCC' # Light Yellow
        # --- End Debug Colors ---

        # --- Setup Theme and Colors ---
        self._setup_theme_colors()

        # Fonts
        self.default_font = tkFont.Font(family="Helvetica", size=12)
        self.mono_font = tkFont.Font(family="Courier New", size=12)
        self.header_font = tkFont.Font(family="Helvetica", size=12, weight="bold")
        
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
        
        # --- Column Resizing State ---
        self.resizing_col_index = None
        self.resize_start_x = 0
        self.initial_col_width = 0
        self.potential_sort_click = False # Differentiates a click from a drag
        self.last_press_time = 0
        self.last_press_col = None
        self.auto_fit_thread = None # To manage auto-fit thread

        self.config(bg=self.BACKGROUND_COLOR)
        self._create_menu()
        self._create_widgets()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        # Start hidden, we will show it once data is ready
        # self.withdraw() # <-- REVERTED: Removing this line

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

        # DEBUG: Use start color if debugging, else normal
        bg_color = self.DEBUG_COLOR_START if self.debug_colors else self.BACKGROUND_COLOR
        self.header_canvas = tk.Canvas(main_frame, height=25, bd=0, highlightthickness=0, bg=bg_color)
        self.header_canvas.grid(row=0, column=0, sticky="ew")
        
        # --- Bindings for Column Resizing and Sorting ---
        self.header_canvas.bind("<Motion>", self._on_header_motion)
        self.header_canvas.bind("<ButtonPress-1>", self._on_header_press)
        self.header_canvas.bind("<B1-Motion>", self._on_header_drag)
        self.header_canvas.bind("<ButtonRelease-1>", self._on_header_release)
        
        # DEBUG: Use start color if debugging, else normal
        self.canvas = tk.Canvas(main_frame, bg=bg_color, highlightthickness=0)
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
        self._redraw_job = self.after(0, self._perform_redraw)

    def _perform_redraw(self):
        """Executes the actual redraw of the canvas and header."""
        self._redraw_job = None
        self._redraw_header()
        self.redraw_canvas()

    def _set_debug_colors(self, color):
        """(Main Thread) Sets the background color of canvases for debugging."""
        try:
            self.canvas.config(bg=color)
            self.header_canvas.config(bg=color)
        except tk.TclError as e:
            # This can happen if the window is closed during load
            print(f"Failed to set debug color (window likely closed): {e}")

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
        self.status_bar.config(text=f"Opening {self.file_path}....")
        self.update_idletasks()
        # self.deiconify() # <-- This is redundant, mainloop shows the window.
        # The window will be shown in setup_display() after Stage 1 load.

    def _reset_state(self):
        """Resets the application state for a new file."""
        
        # DEBUG: Reset background to start color or normal
        if self.debug_colors:
            self._set_debug_colors(self.DEBUG_COLOR_START)
        else:
            self._set_debug_colors(self.BACKGROUND_COLOR)
        
        self.original_data = pd.DataFrame()
        self.view_df = pd.DataFrame()
        self.column_names = []
        self.sort_info = {'col_index': None, 'ascending': True}
        self.features_ready = False
        self.edit_menu.entryconfig("Find/Filter...", state="disabled")
        self.edit_menu.entryconfig("Clear Filter", state="disabled")
        self.last_press_time = 0
        self.last_press_col = None
        self.auto_fit_thread = None

    def _load_csv_data(self):
        """Loads data in two stages using pandas for speed and robustness."""
        INITIAL_LOAD_ROWS = 200
        CHUNK_SIZE = 50000 
        self.after(0, lambda: self.status_bar.config(text=f"loading csv data"))
        try:
            # --- PROFILING: Mark start time ---
            t_start_full = time.perf_counter()

            # --- Stage 1: Fast initial read using nrows for instant display ---
            initial_chunk_df = pd.read_csv(
                self.file_path,
                nrows=INITIAL_LOAD_ROWS,
                encoding='utf-8',
                on_bad_lines='skip',
                engine='c'
            )
            #self.after(0, lambda: self.status_bar.config(text=f"initial chunk read"))
            
            # --- PROFILING: Mark chunk load time ---
            t_end_chunk = time.perf_counter()
            chunk_load_time = t_end_chunk - t_start_full
            print(f"--- STAGE 1 (Initial Chunk) loaded in: {chunk_load_time:.4f} seconds ---")
            
            self.column_names = initial_chunk_df.columns.tolist()
            #self.after(0, lambda: self.status_bar.config(text=f"col names"))
            self.original_data = initial_chunk_df
            self.view_df = self.original_data
            
            self.total_rows = len(self.view_df)

            #self.after(0, lambda: self.status_bar.config(text=f"detect col types"))
            self._detect_column_types(initial_chunk_df)

            #self.after(0, lambda: self.status_bar.config(text=f"estimate widths"))
            self._estimate_col_widths(self.view_df) # Pass the DataFrame directly

            # DEBUG: Set background to yellow for Stage 1 display
            if self.debug_colors:
                self.after(0, self._set_debug_colors, self.DEBUG_COLOR_STAGE1)

            #self.after(0, lambda: self.status_bar.config(text=f"setup display"))
            self.after(0, self.setup_display)

            time.sleep(0.01) # Yield to the UI thread
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

            # --- PROFILING: Mark full load time ---
            t_end_full = time.perf_counter()
            full_load_time = t_end_full - t_start_full
            print(f"--- STAGE 2 (Full File) loaded in: {full_load_time:.4f} seconds ---")

            self.after(0, self._finalize_load)

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to read file: {e}"))

    def _finalize_load(self):
        """Called after the full file is loaded and features are enabled."""
        
        # DEBUG: Set background back to normal
        self._set_debug_colors(self.BACKGROUND_COLOR)
        
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
        t_start = time.perf_counter() # --- PROFILING START ---
        
        self.col_widths = {}
        # Limit to the first 100 rows
        df_sample = df.iloc[:100] 

        for i, col_name in enumerate(self.column_names):
            header_width = self.header_font.measure(col_name) + 30
            
            col_type = self.col_types.get(i, 'text')
            font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font
            
            # --- Vectorized Optimization ---
            # 1. Get the column's sample data
            col_data = df_sample[col_name]
            
            if col_data.empty:
                max_data_width = 0
            else:
                # 2. Convert all to string ONCE
                str_series = col_data.astype(str)
                
                # 3. Find the index of the longest string
                longest_str_index = str_series.str.len().idxmax()
                
                # 4. Get the actual longest string
                value_to_measure = str_series.loc[longest_str_index]
                
                # --- FIX: Apply the same formatting as redraw_canvas ---
                if col_type == 'int':
                    try: value_to_measure = f"{int(float(value_to_measure)):,}"
                    except (ValueError, TypeError): pass # Keep original string if conversion fails
                elif col_type == 'float':
                    try: value_to_measure = f"{float(value_to_measure):,.2f}"
                    except (ValueError, TypeError): pass # Keep original string
                # --- END FIX ---

                # 5. Measure only ONCE
                max_data_width = font_to_use.measure(value_to_measure) + 15
            # --- End Optimization ---

            self.col_widths[i] = max(header_width, max_data_width, 50)

        t_end = time.perf_counter() # --- PROFILING END ---
        print(f"--- _estimate_col_widths (Vectorized) executed in: {t_end - t_start:.4f} seconds ---")

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
        #self.after(0, lambda: self.status_bar.config(text=f"setup display"))
        self._redraw_header()
        #self.after(0, lambda: self.status_bar.config(text=f"redraw header"))
        self.update_scrollregion()
        self.redraw_canvas()
        #self.after(0, lambda: self.status_bar.config(text=f"redraw canvas"))
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.focus_force()
        #self.after(0, lambda: self.status_bar.config(text=f"end setup display"))

    def update_scrollregion(self):
        """Recalculates and sets the scrollregion for header and data canvases."""
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
        
        canvas_height = self.header_canvas.winfo_height() # Get canvas height

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

            self.header_canvas.create_text(text_x, canvas_height / 2,
                text=text, anchor=anchor, font=self.header_font, fill=self.FOREGROUND_COLOR)
            
            # --- ADD VERTICAL LINE ---
            # Draw a divider line at the end of this column
            line_x = current_x + col_width + x_offset
            # Add a small padding from top and bottom
            self.header_canvas.create_line(line_x, 4, line_x, canvas_height - 4, fill=self.GRID_LINE_COLOR)
            # --- END ADD ---

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
                    if col_type == 'int':
                        try: display_value = f"{int(cell_value):,}"
                        except (ValueError, TypeError): pass
                    elif col_type == 'float':
                        try: display_value = f"{float(cell_value):,.2f}"
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
            
            # --- MODIFICATION: Trigger auto-fit after sort ---
            self.after(0, lambda: self.status_bar.config(text="Sort complete. Auto-fitting column..."))
            self.after(0, self.auto_fit_column, col_index)
            # --- END MODIFICATION ---
            
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
        t_start_full = time.perf_counter()

        # This optimized approach converts each column to string once, which is much faster.
        mask = self.original_data.astype(str).apply(lambda s: s.str.contains(search_term, case=False, na=False)).any(axis=1)
        
        self.view_df = self.original_data[mask]
        self.total_rows = len(self.view_df)
        
        self.selected_cell = {'row': None, 'col': None}
        self.sort_info = {'col_index': None, 'ascending': True}
        
        t_end_full = time.perf_counter()
        filter_time = t_end_full - t_start_full
        self.after(0, self.setup_display)
        self.after(0, lambda: self.status_bar.config(text=f"Found {self.total_rows:,} matching rows. {filter_time:.4f} seconds"))

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

    def _get_resize_col(self, canvas_x):
        """Checks if the mouse x-coordinate is over a column divider."""
        current_x = 0
        tolerance = 5 # Pixels
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            divider_x = current_x + col_width
            if abs(canvas_x - divider_x) < tolerance:
                return i # Return the index of the column to resize
            current_x += col_width
        return None

    def _on_header_motion(self, event):
        """Changes the cursor if hovering over a column divider."""
        if self.resizing_col_index is not None:
            return # Already resizing
            
        canvas_x = self.header_canvas.canvasx(event.x)
        col_to_resize = self._get_resize_col(canvas_x)
        
        if col_to_resize is not None:
            self.header_canvas.config(cursor="sb_h_double_arrow")
        else:
            self.header_canvas.config(cursor="")

    def _on_header_press(self, event):
        """Starts a column resize, auto-fit, or flags a potential sort click."""
        canvas_x = self.header_canvas.canvasx(event.x)
        col_to_resize = self._get_resize_col(canvas_x)
        
        current_time = time.time()
        time_diff = current_time - self.last_press_time
        
        # --- Check for double-click auto-fit ---
        if col_to_resize is not None and col_to_resize == self.last_press_col and time_diff < 0.3: # 0.3 sec threshold
            if self.auto_fit_thread and self.auto_fit_thread.is_alive():
                return # Already auto-fitting
            
            self.auto_fit_column(col_to_resize)
            
            # Reset state
            self.last_press_time = 0
            self.last_press_col = None
            self.potential_sort_click = False
            self.resizing_col_index = None
            return # Handled as auto-fit
        
        # --- Not a double-click, record press time & col ---
        self.last_press_time = current_time
        self.last_press_col = col_to_resize # Will be None if not on a divider

        # --- Original press logic ---
        self.resizing_col_index = None
        self.potential_sort_click = False
        
        if col_to_resize is not None:
            # This is a *single* press on a divider, init resize
            self.resizing_col_index = col_to_resize
            self.resize_start_x = event.x
            self.initial_col_width = self.col_widths.get(col_to_resize, 100)
        else:
            # This is a press in the middle of a header, init sort
            self.potential_sort_click = True
            self.potential_sort_x = canvas_x

    def _on_header_drag(self, event):
        """Handles the drag motion to resize a column."""
        if self.resizing_col_index is None:
            return
            
        # It's a drag, so definitely not a sort click
        self.potential_sort_click = False 
        
        delta_x = event.x - self.resize_start_x
        new_width = self.initial_col_width + delta_x
        new_width = max(20, new_width) # Set a minimum width
        
        self.col_widths[self.resizing_col_index] = new_width
        
        self.update_scrollregion()
        self._schedule_redraw() # Debounced redraw

    def _on_header_release(self, event):
        """Finishes a resize or triggers a sort if it was a click."""
        if self.resizing_col_index is not None:
            # Finalize resize
            self.resizing_col_index = None
            self.header_canvas.config(cursor="")
            self._perform_redraw() # Do one final, non-debounced redraw
            
        elif self.potential_sort_click:
            # It was a click (press and release), so trigger the sort
            self._on_header_click_logic(self.potential_sort_x)

        self.potential_sort_click = False

    def _on_header_click_logic(self, canvas_x):
        """Finds which column was clicked and triggers a sort."""
        current_x, col = 0, None
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            if current_x <= canvas_x < current_x + col_width:
                col = i
                break
            current_x += col_width
        if col is not None: self.sort_by_column(col)

    def auto_fit_column(self, col_index):
        """Starts a background thread to auto-fit a column."""
        if not self.features_ready:
            self.status_bar.config(text="Please wait for data to load before auto-fitting.")
            return

        if self.auto_fit_thread and self.auto_fit_thread.is_alive():
            self.status_bar.config(text="Auto-fit already in progress...")
            return
            
        col_name = self.column_names[col_index]
        self.status_bar.config(text=f"Auto-fitting column '{col_name}'...")
        self.auto_fit_thread = threading.Thread(target=self._perform_auto_fit, args=(col_index,), daemon=True)
        self.auto_fit_thread.start()

    def _perform_auto_fit(self, col_index):
        """(Threaded) Calculates the optimal width for a column based on all data."""
        try:
            col_name = self.column_names[col_index]
            col_type = self.col_types.get(col_index, 'text')
            font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font
            
            # 1. Get header width
            header_width = self.header_font.measure(col_name) + 30 # Padding + sort arrow

            # 2. Get max data width from original_data
            # This is the most expensive operation
            s = self.original_data[col_name].astype(str)
            
            if s.empty:
                max_data_width = 0
            else:
                # Find the string with the max character length (fast proxy)
                # and measure just that one string (slower, but only done once)
                value_to_measure = s.loc[s.str.len().idxmax()]
                
                # --- FIX: Apply the same formatting as redraw_canvas ---
                if col_type == 'int':
                    try: value_to_measure = f"{int(float(value_to_measure)):,}"
                    except (ValueError, TypeError): pass # Keep original string if conversion fails
                elif col_type == 'float':
                    try: value_to_measure = f"{float(value_to_measure):,.2f}"
                    except (ValueError, TypeError): pass # Keep original string
                # --- END FIX ---
                
                max_data_width = font_to_use.measure(value_to_measure) + 15 # Cell padding

            # 3. Determine new width
            new_width = int(max(header_width, max_data_width, 50)) # Min width of 50
            self.col_widths[col_index] = new_width
            
            # 4. Schedule UI update
            self.after(0, self._finalize_auto_fit)
        except Exception as e:
            print(f"Error during auto-fit: {e}")
            self.after(0, lambda: self.status_bar.config(text=f"Auto-fit failed: {e}"))

    def _finalize_auto_fit(self):
        """(Main Thread) Updates UI after auto-fit calculation is done."""
        self.update_scrollregion()
        self._perform_redraw()
        self.status_bar.config(text="Auto-fit complete.")

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
    debug_mode = False
    file_to_open = None
    
    # Parse CLI args
    args = sys.argv[1:]
    for arg in args:
        if arg == '--debug-colors':
            debug_mode = True
        elif not arg.startswith('-'):
            file_to_open = arg
            
    app = CSVViewer(debug_colors=debug_mode) # Pass the flag
    
    if file_to_open:
        app.load_file_from_path(file_to_open)
    
    app.mainloop()




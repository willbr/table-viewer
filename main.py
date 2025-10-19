import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, font as tkFont
import csv
import threading
import sys
import os
import subprocess
from datetime import datetime

class CSVViewer(tk.Tk):
    """
    A fast and simple CSV viewer application built with Tkinter.
    It uses a virtualized canvas to efficiently display large CSV files,
    and includes features like sorting, filtering, and go-to-line.
    """
    def __init__(self):
        super().__init__()
        self.title("Fast CSV Viewer")
        self.geometry("800x600")

        self.modifier = "Command" if sys.platform == "darwin" else "Control"

        # --- Setup Theme and Colors ---
        self._setup_theme_colors()

        # Fonts
        self.default_font = tkFont.Font(family="Helvetica", size=9)
        self.mono_font = tkFont.Font(family="Courier New", size=9)
        self.header_font = tkFont.Font(family="Helvetica", size=10, weight="bold")
        
        # Common date and datetime formats to check against
        self.DATETIME_FORMATS = [
            '%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%Y-%m-%d %H:%M',
            '%m/%d/%Y %I:%M %p', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d',
            '%m/%d/%Y', '%m-%d-%Y', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y'
        ]

        self.file_path = None
        self.original_data = [] # Always holds the original, unfiltered data
        self.csv_data = []      # Holds the data to be displayed (can be sorted/filtered)
        self.column_names = []
        self.sort_info = {'col_index': None, 'ascending': True}
        self.col_alignments = {}
        self.col_types = {}
        self.selected_cell = {'row': None, 'col': None}

        # Virtual grid settings
        self.row_height = 20
        self.col_widths = {}
        self.total_rows = 0
        self.total_cols = 0

        self.config(bg=self.BACKGROUND_COLOR) # Set root window background
        self._create_menu()
        self._create_widgets()

    def _setup_theme_colors(self):
        """Sets up theme-aware colors, with a special check for macOS dark mode."""
        style = ttk.Style(self)
        try:
            style.theme_use('clam')
        except tk.TclError:
            print("Clam theme not available, using default.")

        # --- Theme-aware UI Colors ---
        is_dark_mode = False
        if sys.platform == "darwin":
            try:
                # Use subprocess to query macOS for its interface style for robustness
                cmd = 'defaults read -g AppleInterfaceStyle'
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, shell=True)
                output, _ = p.communicate()
                # The command returns 'Dark' if in dark mode
                if output.strip().decode() == 'Dark':
                    is_dark_mode = True
            except Exception as e:
                # Fallback if the command fails
                print(f"Could not detect dark mode on macOS: {e}")
                pass

        if is_dark_mode:
            # Manually define colors for a consistent dark theme
            self.BACKGROUND_COLOR = "#2e2e2e"
            self.FOREGROUND_COLOR = "#dcdcdc"
            self.SELECTION_COLOR = "#5a5a5a"
            self.GRID_LINE_COLOR = "#4a4a4a"
        else:
            # Use theme-provided colors for light mode or other OSes
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
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)
        menu_bar.add_cascade(label="File", menu=file_menu)
        
        edit_menu = tk.Menu(menu_bar, tearoff=0)
        edit_menu.add_command(label="Find/Filter...", command=self.find_data, accelerator=f"{self.modifier}+F")
        edit_menu.add_command(label="Clear Filter", command=self.clear_filter, accelerator="Esc")
        edit_menu.add_command(label="Go to Line...", command=self.go_to_line, accelerator=f"{self.modifier}+G")
        edit_menu.add_separator()
        edit_menu.add_command(label="Copy", command=self.copy_selection, accelerator=f"{self.modifier}+C")
        menu_bar.add_cascade(label="Edit", menu=edit_menu)

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)
        
        # Bind shortcuts
        self.bind(f"<{self.modifier}-o>", lambda event: self.open_file())
        self.bind(f"<{self.modifier}-f>", lambda event: self.find_data())
        self.bind(f"<{self.modifier}-g>", lambda event: self.go_to_line())
        self.bind(f"<{self.modifier}-c>", lambda event: self.copy_selection())
        self.bind("<Escape>", lambda event: self.clear_filter())

    def _create_widgets(self):
        """Creates the main widgets, replacing Treeview with a Canvas."""
        # Main frame
        main_frame = tk.Frame(self, bg=self.BACKGROUND_COLOR)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # Header canvas (for horizontal scrolling headers)
        self.header_canvas = tk.Canvas(main_frame, height=25, bd=0, highlightthickness=0, bg=self.BACKGROUND_COLOR)
        self.header_canvas.grid(row=0, column=0, sticky="ew")
        self.header_canvas.bind("<Button-1>", self._on_header_click)
        
        # Canvas for CSV data
        self.canvas = tk.Canvas(main_frame, bg=self.BACKGROUND_COLOR, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")

        # Scrollbars - using ttk for a flatter, theme-aware appearance
        vsb = ttk.Scrollbar(main_frame, orient="vertical", command=self.on_vscroll)
        hsb = ttk.Scrollbar(main_frame, orient="horizontal", command=self.on_hscroll)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")
        
        # Separator before status bar
        ttk.Separator(self, orient='horizontal').pack(side=tk.BOTTOM, fill='x', padx=5)

        # Status Bar - use ttk.Label for better theme integration
        self.status_bar = ttk.Label(self, text="Ready", anchor=tk.W, padding=(5, 2))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Bind events
        self.canvas.bind("<Configure>", self.redraw_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-1>", self._on_cell_click)

    def on_vscroll(self, *args):
        """Handle vertical scrolling and redraw."""
        self.canvas.yview(*args)
        self.redraw_canvas()

    def on_hscroll(self, *args):
        """Handle horizontal scrolling for both canvas and header."""
        self.canvas.xview(*args)
        self.header_canvas.xview(*args)
        self.redraw_canvas()
        self._redraw_header()

    def _on_mousewheel(self, event):
        """
        Handle mouse wheel scrolling in a cross-platform way.
        Normalizes the delta to provide consistent scroll speed.
        """
        if event.num == 5 or event.delta < 0:
            # Scroll down
            self.canvas.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            # Scroll up
            self.canvas.yview_scroll(-3, "units")
        
        self.redraw_canvas()

    def open_file(self):
        """Opens a file dialog to select a CSV file."""
        file_path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if file_path:
            self.load_file_from_path(file_path)

    def load_file_from_path(self, file_path):
        """Initiates loading a file from a given path."""
        if not os.path.exists(file_path):
            messagebox.showerror("Error", f"File not found:\n{file_path}")
            return

        self.file_path = file_path
        self.title(f"Fast CSV Viewer - {os.path.basename(self.file_path)}")
        self.status_bar.config(text=f"Loading {self.file_path}...")
        self.sort_info = {'col_index': None, 'ascending': True} # Reset sort
        self.selected_cell = {'row': None, 'col': None} # Reset selection

        # Use a thread to load the file to prevent the UI from freezing
        thread = threading.Thread(target=self._load_csv_data, daemon=True)
        thread.start()

    def _load_csv_data(self):
        """Loads data from the CSV file in a separate thread."""
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f)
                self.column_names = next(reader, None)
                if self.column_names:
                    self.original_data = list(reader)
                    self.csv_data = self.original_data[:]
                    self._detect_column_types()
                    self._estimate_col_widths()

            self.after(0, self.setup_display)
            self.after(0, lambda: self.status_bar.config(text=f"Loaded {len(self.original_data):,} rows from {os.path.basename(self.file_path)}"))

        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to read file:\n{e}"))
            self.after(0, lambda: self.status_bar.config(text="Error loading file."))

    def _estimate_col_widths(self):
        """Estimate column widths based on header and first few rows."""
        self.col_widths = {}
        row_count = len(self.csv_data)
        for i, col_name in enumerate(self.column_names):
            header_width = self.header_font.measure(col_name) + 30 # Padding for sort indicator
            
            max_data_width = 0
            col_type = self.col_types.get(i, 'text')
            font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font
            
            for row_idx in range(min(100, row_count)):
                if i < len(self.csv_data[row_idx]):
                    cell_width = font_to_use.measure(self.csv_data[row_idx][i]) + 15 # Padding
                    if cell_width > max_data_width:
                        max_data_width = cell_width
            
            self.col_widths[i] = max(header_width, max_data_width, 50)

    def _detect_column_types(self):
        """
        Analyzes the first 100 rows to determine column types (text, int, float, datetime)
        and sets their alignment accordingly.
        """
        self.col_alignments = {}
        self.col_types = {}
        if not self.csv_data:
            return

        num_rows_to_check = min(100, len(self.csv_data))
        if num_rows_to_check == 0:
            for i in range(len(self.column_names)):
                self.col_alignments[i] = 'w'
                self.col_types[i] = 'text'
            return

        for col_idx in range(len(self.column_names)):
            # Determine the most likely type by checking all sample rows
            is_int, is_float, is_datetime = True, True, True
            for row_idx in range(num_rows_to_check):
                try:
                    cell_value = self.csv_data[row_idx][col_idx].strip()
                    if cell_value:
                        if is_int:
                            try: int(cell_value.replace(',', ''))
                            except ValueError: is_int = False
                        if is_float:
                            try: float(cell_value.replace(',', ''))
                            except ValueError: is_float = False
                        if is_datetime:
                            if not self._is_datetime(cell_value): is_datetime = False
                except IndexError:
                    continue # Skip ragged rows

            col_type = 'text'
            if is_int: col_type = 'int'
            elif is_float: col_type = 'float'
            elif is_datetime: col_type = 'datetime'
            
            self.col_types[col_idx] = col_type
            if col_type in ['int', 'float', 'datetime']:
                self.col_alignments[col_idx] = 'e'
            else:
                self.col_alignments[col_idx] = 'w'

    def _is_datetime(self, s):
        """Helper to check if a string matches common date/datetime formats."""
        for fmt in self.DATETIME_FORMATS:
            try:
                datetime.strptime(s, fmt)
                return True
            except (ValueError, TypeError):
                pass
        return False

    def setup_display(self):
        """Set up the header and canvas scroll region."""
        self._redraw_header()

        self.total_rows = len(self.csv_data)
        total_width = sum(self.col_widths.values())
        total_height = self.total_rows * self.row_height
        self.canvas.configure(scrollregion=(0, 0, total_width, total_height))
        self.header_canvas.configure(scrollregion=(0, 0, total_width, self.header_canvas.winfo_height()))
        self.redraw_canvas()

    def _redraw_header(self):
        """Draws the column headers directly on the header canvas."""
        self.header_canvas.delete("all")
        x_left = self.header_canvas.xview()[0]
        scroll_width = sum(self.col_widths.values())
        x_offset = -int(x_left * scroll_width)

        current_x = 0
        for i, col_name in enumerate(self.column_names):
            col_width = self.col_widths.get(i, 100)
            
            # Draw header text
            text = col_name
            if self.sort_info['col_index'] == i:
                text += ' ▲' if self.sort_info['ascending'] else ' ▼'
            
            align = self.col_alignments.get(i, 'w')
            padding = 10 # Padding from the edge of the column

            if align == 'e':
                anchor = 'e'
                # Position text at the right edge of the column, minus padding
                text_x = current_x + x_offset + col_width - padding
            else:  # Default to 'w'
                anchor = 'w'
                # Position text at the left edge of the column, plus padding
                text_x = current_x + x_offset + padding

            self.header_canvas.create_text(
                text_x, self.header_canvas.winfo_height()/2,
                text=text, anchor=anchor, font=self.header_font,
                fill=self.FOREGROUND_COLOR
            )
            current_x += col_width

    def redraw_canvas(self, event=None):
        """Redraws the visible part of the canvas."""
        self.canvas.delete("all")
        
        if not self.csv_data:
            return

        y_top = self.canvas.yview()[0]
        y_bottom = self.canvas.yview()[1]
        x_left = self.canvas.xview()[0]

        start_row = int(y_top * self.total_rows)
        end_row = int(y_bottom * self.total_rows) + 2

        scrollregion_str = self.canvas.cget("scrollregion")
        if not scrollregion_str: return
        scroll_width = int(scrollregion_str.split(' ')[2])
        x_offset = -int(x_left * scroll_width)

        for row_idx in range(start_row, min(end_row, self.total_rows)):
            y = row_idx * self.row_height
            
            # Draw a thin grid line instead of alternating row colors
            self.canvas.create_line(0, y + self.row_height, scroll_width, y + self.row_height, fill=self.GRID_LINE_COLOR)

            current_x = 0
            row_data = self.csv_data[row_idx]

            # Highlight selected cell with a subtle color
            if row_idx == self.selected_cell['row']:
                 col_to_highlight = self.selected_cell['col']
                 highlight_x = sum(self.col_widths.get(i, 100) for i in range(col_to_highlight))
                 highlight_width = self.col_widths.get(col_to_highlight, 100)
                 self.canvas.create_rectangle(
                    highlight_x + x_offset, y,
                    highlight_x + highlight_width + x_offset, y + self.row_height,
                    fill=self.SELECTION_COLOR,
                    outline=""
                )

            for col_idx, cell_value in enumerate(row_data):
                col_width = self.col_widths.get(col_idx, 100)
                
                if (current_x + col_width + x_offset > 0) and (current_x + x_offset < self.canvas.winfo_width()):
                    align = self.col_alignments.get(col_idx, 'w')
                    col_type = self.col_types.get(col_idx, 'text')
                    font_to_use = self.mono_font if col_type in ['int', 'float', 'datetime'] else self.default_font
                    
                    display_value = cell_value
                    if col_type == 'float':
                        try:
                            # Format to 2 decimal places if possible
                            display_value = f"{float(cell_value.replace(',', '')):.2f}"
                        except (ValueError, TypeError):
                            pass  # Keep original value if conversion fails

                    padding = 5
                    if align == 'e':
                        anchor = 'e'
                        text_x = current_x + x_offset + col_width - padding
                    else:  # Default to 'w'
                        anchor = 'w'
                        text_x = current_x + x_offset + padding

                    self.canvas.create_text(
                        text_x,
                        y + self.row_height / 2,
                        anchor=anchor,
                        text=display_value,
                        fill=self.FOREGROUND_COLOR,
                        font=font_to_use
                    )
                current_x += col_width

    def sort_by_column(self, col_index):
        """Sorts the data by the selected column."""
        if self.sort_info['col_index'] == col_index:
            self.sort_info['ascending'] = not self.sort_info['ascending']
        else:
            self.sort_info['col_index'] = col_index
            self.sort_info['ascending'] = True

        self.status_bar.config(text=f"Sorting by column '{self.column_names[col_index]}'...")
        
        thread = threading.Thread(target=self._perform_sort, daemon=True)
        thread.start()

    def _perform_sort(self):
        """The actual sorting logic, run in a background thread."""
        col_index = self.sort_info['col_index']
        ascending = self.sort_info['ascending']
        col_type = self.col_types.get(col_index, 'text')
        
        try:
            # Create a more intelligent sort key based on detected column type
            def sort_key(row):
                try:
                    val = row[col_index]
                    if not val: return datetime.min if col_type == 'datetime' else ""

                    if col_type == 'int': return int(val.replace(',', ''))
                    if col_type == 'float': return float(val.replace(',', ''))
                    if col_type == 'datetime':
                        for fmt in self.DATETIME_FORMATS:
                            try: return datetime.strptime(val, fmt)
                            except (ValueError, TypeError): pass
                        return datetime.min # Fallback for un-parsable dates
                    
                    return val.lower() # Case-insensitive text sort
                except (ValueError, IndexError):
                    return "" # Handle conversion errors or ragged rows

            self.csv_data.sort(key=sort_key, reverse=not ascending)
            self.after(0, self.setup_display)
            self.after(0, lambda: self.status_bar.config(text="Sort complete."))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Sort Error", f"An error occurred while sorting:\n{e}"))

    def find_data(self):
        """Filters the data based on user input."""
        if not self.original_data:
            return
        
        search_term = simpledialog.askstring("Find/Filter", "Enter text to find:", parent=self)
        if search_term:
            self.status_bar.config(text=f"Filtering for '{search_term}'...")
            thread = threading.Thread(
                target=self._perform_filter, args=(search_term.lower(),), daemon=True
            )
            thread.start()

    def _perform_filter(self, search_term):
        """The actual filtering logic, run in a background thread."""
        filtered_data = [
            row for row in self.original_data 
            if any(search_term in str(cell).lower() for cell in row)
        ]
        self.csv_data = filtered_data
        self.selected_cell = {'row': None, 'col': None} # Reset selection
        self.sort_info = {'col_index': None, 'ascending': True} # Reset sort
        
        self.after(0, self.setup_display)
        self.after(0, lambda: self.status_bar.config(text=f"Found {len(self.csv_data):,} matching rows."))


    def clear_filter(self):
        """Resets the view to show the original, unfiltered data."""
        if not self.original_data:
            return
        self.csv_data = self.original_data[:]
        self.selected_cell = {'row': None, 'col': None} # Reset selection
        self.sort_info = {'col_index': None, 'ascending': True} # Reset sort
        self.status_bar.config(text="Filter cleared.")
        self.setup_display()

    def go_to_line(self):
        """Jumps the view to a specific line number."""
        if not self.csv_data:
            return
            
        line_num = simpledialog.askinteger(
            "Go to Line", 
            f"Enter line number (1 - {self.total_rows}):",
            parent=self, minvalue=1, maxvalue=self.total_rows
        )
        if line_num:
            fraction = (line_num - 1) / self.total_rows if self.total_rows > 0 else 0
            self.canvas.yview_moveto(fraction)
            self.redraw_canvas()

    def copy_selection(self):
        """Copies the content of the selected cell to the clipboard."""
        sel = self.selected_cell
        if sel['row'] is not None and sel['col'] is not None:
            try:
                value = self.csv_data[sel['row']][sel['col']]
                self.clipboard_clear()
                self.clipboard_append(value)
                self.status_bar.config(text=f"Copied '{value}' to clipboard.")
            except IndexError:
                self.status_bar.config(text="Cannot copy cell, data out of sync.")

    def _on_header_click(self, event):
        """Handles clicks on the header canvas to trigger sorting."""
        canvas_x = self.header_canvas.canvasx(event.x)
        
        current_x = 0
        col = None
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            if current_x <= canvas_x < current_x + col_width:
                col = i
                break
            current_x += col_width
        
        if col is not None:
            self.sort_by_column(col)

    def _on_cell_click(self, event):
        """Handles clicks on the canvas to select a cell."""
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)

        row = int(canvas_y // self.row_height)
        
        current_x = 0
        col = None
        # Find column by iterating through widths
        for i in range(len(self.column_names)):
            col_width = self.col_widths.get(i, 100)
            if current_x <= canvas_x < current_x + col_width:
                col = i
                break
            current_x += col_width

        if row < len(self.csv_data) and col is not None:
            self.selected_cell = {'row': row, 'col': col}
        else:
            # Deselect if clicking outside data area
            self.selected_cell = {'row': None, 'col': None}
        
        self.redraw_canvas()

    def show_about(self):
        """Shows the about dialog."""
        messagebox.showinfo(
            "About Fast CSV Viewer",
            "A simple, fast CSV viewer for large files.\n\nBuilt with Python and Tkinter."
        )


if __name__ == "__main__":
    app = CSVViewer()

    # Check for command-line argument for a file to open
    if len(sys.argv) > 1:
        file_to_open = sys.argv[1]
        # Schedule the file loading to occur shortly after the mainloop starts
        # This allows the GUI to appear before the file loading begins.
        app.after(100, lambda: app.load_file_from_path(file_to_open))

    app.mainloop()



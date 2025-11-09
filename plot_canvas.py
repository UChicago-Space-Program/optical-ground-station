from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
import pandas as pd  # Ensure pandas is imported
import matplotlib.dates as mdates
from PyQt6 import QtCore, QtGui  # Import QtCore and QtGui for key events and modifiers

# Import constant
from constants import ARCHIVE_SOURCE_PREFIX

class PlotCanvas(FigureCanvas):
    """A custom Matplotlib canvas widget for plotting data with interactive zoom/pan."""
    def __init__(self, parent=None, width=5, height=3.5, dpi=100, plot_title='Plot', bg_color='#F0F0F0', data_key="None", main_window=None):
        fig = Figure(figsize=(width, height), dpi=dpi)
        fig.patch.set_facecolor(bg_color)
        # Adjust bottom more to prevent date labels overlapping title/xlabel
        fig.subplots_adjust(left=0.15, right=0.9, bottom=0.25, top=0.88)
        self.axes = fig.add_subplot(111)
        self.axes.set_facecolor(bg_color)
        super().__init__(fig)
        self.setParent(parent)
        self._plot_title = plot_title
        self.data_key = data_key
        self.main_window = main_window
        self.current_archive_path = None  # Store archive path used for current data
        self._initial_plot_done = False  # Flag to track if initial plot fitting is done

        # Enable keyboard interaction and focus on hover
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self.plot()  # Initial plot call

    def set_data_key(self, data_key, archive_path=None):
        """Sets the data source key and optional archive path, triggers a replot."""
        self.data_key = data_key
        self.current_archive_path = archive_path  # Store path used for this key
        self._initial_plot_done = False  # Reset flag to allow autofit on new data
        self.plot()

    def set_plot_title(self, title):
        """Sets the base plot title."""
        self._plot_title = title
        # Re-apply title immediately if axes exist
        if hasattr(self, 'axes'):
            self._apply_plot_decorations()  # Update title shown on plot
            self.draw_idle()  # Redraw needed elements

    def plot(self):
        """Clears and redraws the plot based on data from the main window using data_key and archive_path."""
        # Store current limits if they exist AND if initial plot was done
        # to restore zoom/pan state, unless it's the very first plot for this data key
        limits_exist = False
        if self._initial_plot_done and hasattr(self.axes, 'get_xlim'):
            try:
                xlim = self.axes.get_xlim()
                ylim = self.axes.get_ylim()
                # Check if limits are valid (not default 0,1)
                if xlim != (0.0, 1.0) or ylim != (0.0, 1.0):
                    limits_exist = True
            except AttributeError:
                limits_exist = False  # Should not happen if _initial_plot_done is True

        self.axes.clear()
        bg_color = self.figure.patch.get_facecolor()
        self.axes.set_facecolor(bg_color)

        data = []
        timestamps = []
        if self.main_window:
            timestamps, data = self.main_window.get_plot_data(self.data_key, archive_base_path=self.current_archive_path)

        is_archive_source = self.data_key.startswith(ARCHIVE_SOURCE_PREFIX)
        plotted_something = False
        valid_indices = []

        if timestamps and data:
            # Ensure data and timestamps are numpy arrays for easier handling
            timestamps_dt = pd.to_datetime(timestamps)  # Convert to datetime objects if not already
            data_np = np.array(data, dtype=float)  # Convert data to float, forcing errors to NaN

            # Filter out NaN/NaT values
            valid_mask = pd.notna(timestamps_dt) & pd.notna(data_np)
            x_plot = mdates.date2num(timestamps_dt[valid_mask])  # Convert valid datetimes to Matplotlib format
            y_plot = data_np[valid_mask]

            if len(x_plot) > 0:
                self.axes.plot(x_plot, y_plot, marker='.', linestyle='-', markersize=3)  # Smaller markers
                self.axes.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d\n%H:%M:%S'))
                self.figure.autofmt_xdate(rotation=0, ha='center')  # Adjust rotation and alignment

                # Restore previous limits if they existed and data was plotted
                if limits_exist:
                    self.axes.set_xlim(xlim)
                    self.axes.set_ylim(ylim)
                else:
                    # Autoscale only if limits weren't restored (initial plot or no previous zoom)
                    self.axes.autoscale_view()
                    self._initial_plot_done = True  # Mark initial fit as done

                plotted_something = True

            else:
                self.axes.text(0.5, 0.5, 'No valid data points', horizontalalignment='center', verticalalignment='center', transform=self.axes.transAxes)
                self._initial_plot_done = False  # No data, reset flag
        elif self.data_key != "None":
            self.axes.text(0.5, 0.5, 'No data loaded or available', horizontalalignment='center', verticalalignment='center', transform=self.axes.transAxes)
            self._initial_plot_done = False  # No data, reset flag

        # Apply titles, labels, grid etc.
        self._apply_plot_decorations()

        self.draw()  # Update the canvas display

    def _apply_plot_decorations(self):
        """Helper method to apply titles, labels, grid, etc."""
        is_archive_source = self.data_key.startswith(ARCHIVE_SOURCE_PREFIX)
        title = self._plot_title
        source_name = self.data_key
        if is_archive_source:
            # Try to extract a cleaner name if possible (e.g., from path)
            try:
                base_name = os.path.basename(self.current_archive_path) if self.current_archive_path else "Unknown Archive"
                data_part = source_name[len(ARCHIVE_SOURCE_PREFIX):]
                title = f"{title}: {data_part} ({base_name})"
            except Exception:  # Fallback
                title = f"{title}: {source_name} (Archive)"

        elif self.data_key != "None":
            title = f"{title}: {source_name} (Live)"

        self.axes.set_title(title, fontsize=9, wrap=True)  # Smaller font, allow wrapping
        self.axes.set_xlabel('Time', fontsize=8)
        self.axes.set_ylabel('Value', fontsize=8)
        self.axes.tick_params(axis='both', which='major', labelsize=7)
        self.axes.grid(True, linestyle='--', alpha=0.6)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        """Handles key press events for zooming and panning."""
        key = event.key()
        modifiers = event.modifiers()

        try:
            xlim = self.axes.get_xlim()
            ylim = self.axes.get_ylim()
        except AttributeError:
            return  # No axes to work with

        x_range = xlim[1] - xlim[0]
        y_range = ylim[1] - ylim[0]
        x_center = xlim[0] + x_range / 2
        y_center = ylim[0] + y_range / 2

        zoom_factor = 0.1  # Zoom in/out by 10%
        pan_factor = 0.1  # Pan by 10% of the current view

        redraw = False  # Flag to redraw at the end

        if modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
            if key == QtCore.Qt.Key.Key_Up:
                # X-axis Zoom in
                new_x_range = x_range * (1 - zoom_factor)
                self.axes.set_xlim(x_center - new_x_range / 2, x_center + new_x_range / 2)
                redraw = True
            elif key == QtCore.Qt.Key.Key_Down:
                # X-axis Zoom out
                new_x_range = x_range * (1 + zoom_factor)
                self.axes.set_xlim(x_center - new_x_range / 2, x_center + new_x_range / 2)
                redraw = True

        elif modifiers == QtCore.Qt.KeyboardModifier.NoModifier:  # No modifiers pressed
            if key == QtCore.Qt.Key.Key_Up:
                # Zoom in (both axes)
                new_x_range = x_range * (1 - zoom_factor)
                new_y_range = y_range * (1 - zoom_factor)
                self.axes.set_xlim(x_center - new_x_range / 2, x_center + new_x_range / 2)
                self.axes.set_ylim(y_center - new_y_range / 2, y_center + new_y_range / 2)
                redraw = True
            elif key == QtCore.Qt.Key.Key_Down:
                # Zoom out (both axes)
                new_x_range = x_range * (1 + zoom_factor)
                new_y_range = y_range * (1 + zoom_factor)
                self.axes.set_xlim(x_center - new_x_range / 2, x_center + new_x_range / 2)
                self.axes.set_ylim(y_center - new_y_range / 2, y_center + new_y_range / 2)
                redraw = True
            elif key == QtCore.Qt.Key.Key_Left:
                # Pan left
                pan_amount = x_range * pan_factor
                self.axes.set_xlim(xlim[0] - pan_amount, xlim[1] - pan_amount)
                redraw = True
            elif key == QtCore.Qt.Key.Key_Right:
                # Pan right
                pan_amount = x_range * pan_factor
                self.axes.set_xlim(xlim[0] + pan_amount, xlim[1] + pan_amount)
                redraw = True

        if redraw:
            self._initial_plot_done = True  # User interaction implies we are past initial auto-fit
            self.draw()
        else:
            # If no action was taken by this handler, pass it up
            super().keyPressEvent(event)

    def enterEvent(self, event: QtGui.QEnterEvent):
        """Grab keyboard focus when the mouse enters the widget."""
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        """Clear keyboard focus when the mouse leaves the widget."""
        self.clearFocus()
        super().leaveEvent(event)
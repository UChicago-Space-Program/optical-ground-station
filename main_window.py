from PyQt6.QtWidgets import (QMainWindow, QVBoxLayout, QPushButton, QWidget,
                             QStackedWidget, QLabel, QHBoxLayout, QSizePolicy,
                             QFileDialog)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette, QFont
import numpy as np
from datetime import datetime
from collections import deque
import csv
import os
import pandas as pd
import shutil

# Import page widgets
from telemetry_page import TelemetryPage
from data_plotter_page import DataPlotterPage
from command_center_page import CommandCenterPage
from camera_control_page import CameraControlPage
from constants import MAX_DATA_POINTS, MAX_TABLE_ROWS, LIVE_DATA_SOURCES, ARCHIVE_SOURCE_PREFIX


class MainWindow(QMainWindow):
    # --- Constants for button symbols ---
    POWER_SYMBOL_ON = "\u23FB"  # Power symbol
    POWER_SYMBOL_OFF = "\u23FB"  # Can be the same or different if desired
    RECORD_SYMBOL_START = "\u25B6"  # Play symbol ▶️
    RECORD_SYMBOL_STOP = "\u25A0"  # Stop symbol ⏹️
    # --- End Constants ---

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PULSE-A Interface")
        self.setGeometry(100, 100, 1200, 800)

        # --- State Variables ---
        self.is_recording = False  # Track recording state
        self.recording_start_time = None  # To store the start time of recording
        # --- End State Variables ---

        # --- Data Storage (Live Data) ---
        self.timestamps = deque(maxlen=MAX_DATA_POINTS)
        self.data_storage = {source: deque(maxlen=MAX_DATA_POINTS) for source in LIVE_DATA_SOURCES if source != "None"}
        # --- End Data Storage ---

        # --- CSV Logging/Loading Setup ---
        self.base_dir = os.path.dirname(__file__)  # Get directory of main_window.py
        self.data_dir = os.path.join(self.base_dir, 'data')
        self.archive_dir = os.path.join(self.base_dir, 'archive')  # Define archive base directory
        self._csv_file_cache = {}  # Cache for loaded CSV data
        # --- End CSV Logging Setup ---

        # --- Ensure data and archive directories exist on startup ---
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            os.makedirs(self.archive_dir, exist_ok=True)  # Also create base archive dir
        except OSError as e:
            print(f"Warning: Could not create data/archive directory on startup: {e}")
        # --- End directory check ---

        # Main container widget and layout
        main_container = QWidget()
        main_layout = QVBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top Menu Row
        top_menu = QWidget()
        top_menu.setStyleSheet("background-color: lightgray;")
        top_menu.setFixedHeight(50)
        top_menu_layout = QHBoxLayout(top_menu)
        top_menu_layout.setContentsMargins(10, 5, 10, 5)

        label = QLabel("PULSE-A Ground Control")
        label.setStyleSheet("font-size: 20pt; font-weight: bold;")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_menu_layout.addWidget(label)
        top_menu_layout.addStretch(1)

        # --- Record Button ---
        self.record_button = QPushButton(self.RECORD_SYMBOL_START)
        self.record_button.setCheckable(True)
        self.record_button.setChecked(self.is_recording)
        self.record_button.setFixedWidth(50)
        self.record_button.setFixedHeight(35)
        record_font = self.record_button.font()
        record_font.setPointSize(16)  # Adjust size as needed
        self.record_button.setFont(record_font)
        # Initial style (not recording)
        self.record_button.setStyleSheet("""
            QPushButton { background-color: gray; color: white; border: 1px solid darkgray; border-radius: 5px; font-weight: bold; }
            QPushButton:hover { border: 2px solid black; }
            QPushButton:checked { background-color: red; border: 1px solid darkred; } /* Style when checked (recording) */
        """)
        self.record_button.toggled.connect(self.handle_record_toggle)
        top_menu_layout.addWidget(self.record_button)  # Add before power button
        # --- End Record Button ---

        # On/Off Button (Data Retrieval)
        self.on_off_button = QPushButton(self.POWER_SYMBOL_OFF)
        self.on_off_button.setCheckable(True)
        self.on_off_button.setChecked(False)
        self.on_off_button.setFixedWidth(50)
        self.on_off_button.setFixedHeight(35)
        power_font = self.on_off_button.font()
        power_font.setPointSize(16)
        self.on_off_button.setFont(power_font)
        self.on_off_button.setStyleSheet("""
            QPushButton { background-color: red; color: white; border: 1px solid darkred; border-radius: 17px; font-weight: bold; padding: 0px; }
            QPushButton:checked { background-color: lightgreen; border: 1px solid darkgreen; color: black; }
            QPushButton:hover { border: 2px solid black; }
        """)
        self.on_off_button.toggled.connect(self.handle_on_off_toggle)
        top_menu_layout.addWidget(self.on_off_button)
        main_layout.addWidget(top_menu)

        # Content Area Layout (Sidebar + Pages)
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Sidebar
        sidebar_container = QWidget()
        sidebar_container.setStyleSheet("background-color: lightgray;")
        sidebar_container.setFixedWidth(170)
        sidebar_layout = QVBoxLayout(sidebar_container)
        sidebar_layout.setContentsMargins(10, 0, 10, 0)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.button_names = ["Telemetry Viewer", "Data Plotter", "Telescope Controls", "Command Center"]
        self.buttons = [QPushButton(name) for name in self.button_names]
        for button in self.buttons:
            button.clicked.connect(self.change_page)
            sidebar_layout.addWidget(button)

        # Pages
        self.pages = QStackedWidget()
        # Instantiate page widgets
        self.telemetry_page = TelemetryPage()
        self.data_plotter_page = DataPlotterPage(main_window=self)
        self.camera_control_page = CameraControlPage(main_window=self)
        self.command_center_page = CommandCenterPage()
        # Add instances to stacked widget
        self.pages.addWidget(self.telemetry_page)
        self.pages.addWidget(self.data_plotter_page)
        self.pages.addWidget(self.camera_control_page)
        self.pages.addWidget(self.command_center_page)

        content_layout.addWidget(sidebar_container)
        content_layout.addWidget(self.pages)
        main_layout.addLayout(content_layout)
        self.setCentralWidget(main_container)

        # Timer for data retrieval
        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.update_incoming_data)

    # --- Handler for the DATA RETRIEVAL toggle button ---
    def handle_on_off_toggle(self, checked):
        if checked:
            print("System Data Retrieval ON")
            self.telemetry_timer.start(1000)
        else:
            print("System Data Retrieval OFF")
            self.telemetry_timer.stop()
            self.telemetry_page.reset_status()

    # --- Handler for the RECORD toggle button ---
    def handle_record_toggle(self, checked):
        # Store previous state before updating self.is_recording
        was_recording = self.is_recording
        self.is_recording = checked

        if checked:
            # --- Start Recording ---
            if not was_recording:  # Only log start and set time if we weren't already recording
                print("Recording STARTED")
                self.recording_start_time = datetime.now()  # Store start time
                self.record_button.setText(self.RECORD_SYMBOL_STOP)
                self.record_button.setStyleSheet("""
                    QPushButton { background-color: red; color: white; border: 1px solid darkred; border-radius: 5px; font-weight: bold; }
                    QPushButton:hover { border: 2px solid black; }
                    QPushButton:checked { background-color: red; border: 1px solid darkred; }
                """)
                try:
                    os.makedirs(self.data_dir, exist_ok=True)
                    print(f"Ensured data directory exists: {self.data_dir}")
                except OSError as e:
                    print(f"Error creating data directory {self.data_dir}: {e}")
                    self.record_button.setChecked(False)  # Revert button state
                    self.is_recording = False  # Revert internal state
                    self.recording_start_time = None  # Clear start time
            # --- End Start Recording ---
        else:
            # --- Stop Recording ---
            if was_recording:  # Only stop and archive if we were actually recording
                print("Recording STOPPED")
                self.record_button.setText(self.RECORD_SYMBOL_START)
                self.record_button.setStyleSheet("""
                    QPushButton { background-color: gray; color: white; border: 1px solid darkgray; border-radius: 5px; font-weight: bold; }
                    QPushButton:hover { border: 2px solid black; }
                    QPushButton:checked { background-color: red; border: 1px solid darkred; }
                """)
                # --- Archive data ---
                self.archive_data()
                # --- End Archive data ---
            # --- End Stop Recording ---

    # --- Method to archive data ---
    def archive_data(self):
        """Moves files from data_dir to a timestamped archive directory."""
        if self.recording_start_time is None:
            print("Archiving skipped: No recording start time found.")
            return

        end_time = datetime.now()
        # Format timestamps for directory name (YYYYMMDDTHHMMSS)
        start_str = self.recording_start_time.strftime("%Y%m%dT%H%M%S")
        end_str = end_time.strftime("%Y%m%dT%H%M%S")
        archive_sub_dir_name = f"{start_str}-{end_str}"
        target_archive_path = os.path.join(self.archive_dir, archive_sub_dir_name)

        print(f"Archiving data to: {target_archive_path}")

        try:
            # Ensure the specific archive subdirectory exists
            os.makedirs(target_archive_path, exist_ok=True)

            # List files in the data directory
            files_to_move = [f for f in os.listdir(self.data_dir) if os.path.isfile(os.path.join(self.data_dir, f))]

            if not files_to_move:
                print("No data files found in data directory to archive.")
                # Optionally remove the empty archive subdir: os.rmdir(target_archive_path)
            else:
                for filename in files_to_move:
                    source_path = os.path.join(self.data_dir, filename)
                    destination_path = os.path.join(target_archive_path, filename)
                    try:
                        print(f"Moving {filename} to archive...")
                        shutil.move(source_path, destination_path)
                    except Exception as move_error:
                        print(f"Error moving file {filename}: {move_error}")
                print("Archiving complete.")

        except OSError as e:
            print(f"Error creating/accessing archive directory {target_archive_path}: {e}")
        except Exception as e:
            print(f"An unexpected error occurred during archiving: {e}")
        finally:
            # Reset start time regardless of success/failure to prevent re-archiving same session
            self.recording_start_time = None

    # --- Generic placeholder page creation ---
    def create_placeholder_page(self, text):
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(text, alignment=Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 18px;")
        layout.addWidget(label)
        return page

    # --- Function to handle incoming data and update UI ---
    def update_incoming_data(self):
        # This function now runs whenever the timer is active (System ON)
        # It only logs data if self.is_recording is also True

        if not self.telemetry_timer.isActive():
            return  # Should not happen if called by timer, but good practice

        is_connected = np.random.rand() > 0.1
        now = datetime.now()
        now_str_display = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        now_str_csv = now.isoformat()

        if is_connected:
            status_text = "Status: Connected"
            status_color = "green"
            last_msg = f"Last Message: {now_str_display}"

            param = np.random.choice([s for s in LIVE_DATA_SOURCES if s != "None"])
            value = np.random.rand() * 100
            value_str = f"{value:.3f}"

            # Update Telemetry Page Display
            self.telemetry_page.add_telemetry_row(now_str_display, param, value_str, MAX_TABLE_ROWS)

            # --- Log to CSV ONLY if recording is active ---
            if self.is_recording:
                try:
                    if param != "None":
                        file_path = os.path.join(self.data_dir, f"{param}.csv")
                        file_exists = os.path.exists(file_path)

                        with open(file_path, 'a', newline='', encoding='utf-8') as csvfile:
                            csv_writer = csv.writer(csvfile)
                            if not file_exists or os.path.getsize(file_path) == 0:
                                csv_writer.writerow(['timestamp', 'value'])
                            csv_writer.writerow([now_str_csv, value])

                except IOError as e:
                    print(f"Error writing to CSV {file_path}: {e}")
                except Exception as e:
                    print(f"An unexpected error occurred during CSV logging: {e}")
            # --- End Log to CSV ---

            # --- Store Live Data for Plotting ---
            self.timestamps.append(now)
            for source, storage_deque in self.data_storage.items():
                if source == param:
                    storage_deque.append(value)
                else:
                    if source in LIVE_DATA_SOURCES and source != "None":
                        storage_deque.append(np.nan)
            # --- End Data Storage ---

            # --- Update Data Plotter Page (only if in Live mode) ---
            self.data_plotter_page.update_live_plots()
            # --- End Update ---

        else:
            status_text = "Status: Disconnected"
            status_color = "red"
            last_msg = self.telemetry_page.last_message_label.text()

        # Update status display
        self.telemetry_page.update_status(status_text, status_color, last_msg)

    # --- Method to get list of available CSV files ---
    def get_csv_files(self):
        """Returns a list of CSV filenames found in the data directory."""
        csv_files = []
        if not os.path.isdir(self.data_dir):
            return []
        try:
            for filename in os.listdir(self.data_dir):
                if filename.lower().endswith('.csv'):
                    csv_files.append(filename)
        except OSError as e:
            print(f"Error reading data directory {self.data_dir}: {e}")
        return sorted(csv_files)

    # --- Method to get list of available ARCHIVE directories ---
    def get_archive_dirs(self):
        """Returns a list of subdirectory names within the archive directory."""
        archive_dirs = []
        if not os.path.isdir(self.archive_dir):
            print(f"Archive directory not found: {self.archive_dir}")
            return []
        try:
            for item in os.listdir(self.archive_dir):
                if os.path.isdir(os.path.join(self.archive_dir, item)):
                    archive_dirs.append(item)
        except OSError as e:
            print(f"Error reading archive directory {self.archive_dir}: {e}")
        return sorted(archive_dirs)

    # --- Method to get list of CSV files within a specific archive directory ---
    def get_csvs_in_archive_dir(self, dir_path):
        """Returns a list of CSV filenames found in the given directory path."""
        csv_files = []
        if not os.path.isdir(dir_path):
            print(f"Target directory not found: {dir_path}")
            return []
        try:
            for filename in os.listdir(dir_path):
                if filename.lower().endswith('.csv') and os.path.isfile(os.path.join(dir_path, filename)):
                    csv_files.append(filename)
        except OSError as e:
            print(f"Error reading directory {dir_path}: {e}")
        return sorted(csv_files)

    # --- Method to load data from a CSV file ---
    def load_csv_data(self, full_file_path):
        """Loads timestamp and value data from a CSV file using pandas. Caches results."""
        # Use full_file_path as the cache key
        if full_file_path in self._csv_file_cache:
            return self._csv_file_cache[full_file_path]

        timestamps = []
        values = []
        try:
            df = pd.read_csv(full_file_path)
            if 'timestamp' not in df.columns or 'value' not in df.columns:
                print(f"Error: CSV file {os.path.basename(full_file_path)} missing 'timestamp' or 'value' column.")
                self._csv_file_cache[full_file_path] = ([], [])  # Cache error state
                return [], []

            timestamps = pd.to_datetime(df['timestamp'], errors='coerce', infer_datetime_format=True).tolist()
            values = pd.to_numeric(df['value'], errors='coerce').tolist()

            valid_indices = [i for i, ts in enumerate(timestamps) if pd.notna(ts) and pd.notna(values[i])]  # Check both
            timestamps = [timestamps[i] for i in valid_indices]
            values = [values[i] for i in valid_indices]

            self._csv_file_cache[full_file_path] = (timestamps, values)

        except FileNotFoundError:
            print(f"Error: CSV file not found: {full_file_path}")
            self._csv_file_cache[full_file_path] = ([], [])
        except pd.errors.EmptyDataError:
            print(f"Warning: CSV file is empty: {full_file_path}")
            self._csv_file_cache[full_file_path] = ([], [])
        except Exception as e:
            print(f"Error loading CSV file {full_file_path}: {e}")
            self._csv_file_cache[full_file_path] = ([], [])

        return list(timestamps), list(values)

    # --- Method to clear the CSV cache ---
    def clear_csv_cache(self):
        print("Clearing CSV data cache.")
        self._csv_file_cache.clear()

    # --- Method to get all available sources for plot dropdowns ---
    def get_available_plot_sources(self):
        """Combines live sources and discovered CSV files for plot options."""
        live_sources = list(LIVE_DATA_SOURCES)  # Make a copy
        csv_sources = [f"{CSV_SOURCE_PREFIX}{fname}" for fname in self.get_csv_files()]
        return live_sources + csv_sources

    # --- Method to get data for plots (handles live and archive) ---
    def get_plot_data(self, data_key, archive_base_path=None):
        """Returns timestamps and data for a given key (live or archive)."""
        if data_key.startswith(ARCHIVE_SOURCE_PREFIX):
            if archive_base_path is None:
                print(f"Error: Archive path not provided for key {data_key}")
                return [], []
            csv_filename = data_key[len(ARCHIVE_SOURCE_PREFIX):]
            full_path = os.path.join(archive_base_path, csv_filename)
            return self.load_csv_data(full_path)
        elif data_key == "None" or data_key not in self.data_storage:
            return [], []  # Return empty for None or invalid live key
        else:
            return list(self.timestamps), list(self.data_storage[data_key])

    def change_page(self):
        sender = self.sender()
        try:
            index = self.button_names.index(sender.text())
            self.pages.setCurrentIndex(index)
        except ValueError:
            print(f"Error: Button '{sender.text()}' not found in expected names.")

    # --- Override closeEvent ---
    def closeEvent(self, event):
        """Handles the main window closing."""
        print("Close event triggered.")
        if self.is_recording:
            print("Recording is active. Archiving data before closing...")
            self.archive_data()
            self.is_recording = False

        event.accept()
    # --- End override closeEvent ---
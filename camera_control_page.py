"""
Camera Control Page for PULSE-A Interface.

Provides GUI controls for camera operations including:
- Parameter inputs (exposure, gain, number of images)
- Start/Stop acquisition controls
- Status indicator
- CSV log viewer
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QSpinBox, QPushButton, QTextEdit,
                             QGroupBox, QFormLayout, QFileDialog, QProgressBar,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QSplitter, QFrame, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor
import sys
import os
import csv

# Add Camera Control directory to path for imports
CAMERA_CONTROL_DIR = os.path.join(os.path.dirname(__file__), '..', 'Camera Control')
if CAMERA_CONTROL_DIR not in sys.path:
    sys.path.insert(0, CAMERA_CONTROL_DIR)


class CameraWorker(QThread):
    """Worker thread to run camera operations without blocking UI."""
    progress = pyqtSignal(int, int, str)  # (current_image, total_images, message)
    image_taken = pyqtSignal(int, float, bool)  # (image_num, time, acquired)
    finished = pyqtSignal(list, list)  # (time_log, acq_log)
    error = pyqtSignal(str)

    def __init__(self, exp, gain, n, label, folder, cal=None):
        super().__init__()
        self.exp = exp
        self.gain = gain
        self.n = n
        self.label = label
        self.folder = folder
        self.cal = cal
        self._is_running = True

    def stop(self):
        """Request the worker to stop."""
        self._is_running = False

    def run(self):
        """Execute the camera acquisition loop."""
        time_log = []
        acq_log = []

        # Determine calibration settings
        cal_bool = False
        biaspath, darkpath, thres = None, None, 30
        if self.cal is not None:
            biaspath, darkpath, thres = self.cal
            cal_bool = True

        try:
            # Try to import camera modules
            from cameraconnect import camera
            from camerapicture import takepic
            from acqClean import threshold

            for i in range(self.n):
                if not self._is_running:
                    self.progress.emit(i, self.n, "Acquisition stopped by user.")
                    break

                self.progress.emit(i + 1, self.n, f"Capturing image {i + 1} of {self.n}...")

                i_label = f"{self.label}{i}"
                log_time = takepic(self.exp, self.gain, i_label, camera, self.folder, talk=False)
                time_log.append(log_time)

                # Check for acquisition if calibration data provided
                if cal_bool and biaspath and darkpath:
                    acq = threshold(i_label, biaspath, darkpath, thres, cal_bool)
                else:
                    acq = False  # No calibration, can't determine acquisition
                acq_log.append(acq)

                self.image_taken.emit(i, log_time, acq)

            self.finished.emit(time_log, acq_log)

        except ImportError as e:
            self.error.emit(f"Failed to import camera modules: {e}\n\nRunning in simulation mode...")
            # Simulation mode for testing without hardware
            self._run_simulation(time_log, acq_log)

        except Exception as e:
            self.error.emit(f"Camera error: {e}")

    def _run_simulation(self, time_log, acq_log):
        """Run a simulated acquisition for testing without camera hardware."""
        import time
        import random

        for i in range(self.n):
            if not self._is_running:
                self.progress.emit(i, self.n, "Simulation stopped by user.")
                break

            self.progress.emit(i + 1, self.n, f"[SIM] Capturing image {i + 1} of {self.n}...")
            time.sleep(0.2)  # Simulate capture time

            log_time = time.time()
            acq = random.choice([True, False])  # Random acquisition result

            time_log.append(log_time)
            acq_log.append(acq)

            self.image_taken.emit(i, log_time, acq)

        self.finished.emit(time_log, acq_log)


class CameraControlPage(QWidget):
    """Widget for controlling the tracking camera."""

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.worker = None
        self.time_log = []
        self.acq_log = []
        self.setup_ui()

    def setup_ui(self):
        """Set up the page UI layout."""
        # Main horizontal splitter: Left (CSV viewer) | Right (status + controls)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # === LEFT PANE: CSV Log Viewer ===
        left_pane = self._create_csv_viewer_pane()
        splitter.addWidget(left_pane)

        # === RIGHT PANE: Status (top) + Controls (bottom) ===
        right_pane = QWidget()
        right_layout = QVBoxLayout(right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # Top right: Status indicator
        status_widget = self._create_status_pane()
        right_layout.addWidget(status_widget, stretch=1)

        # Bottom right: Controls
        controls_widget = self._create_controls_pane()
        right_layout.addWidget(controls_widget, stretch=1)

        splitter.addWidget(right_pane)

        # Set initial splitter sizes (50% left, 50% right)
        splitter.setSizes([500, 500])

        main_layout.addWidget(splitter)

    def _create_csv_viewer_pane(self):
        """Create the left pane with CSV log viewer."""
        group = QGroupBox("Acquisition Log Viewer")
        layout = QVBoxLayout(group)

        # Toolbar for CSV operations
        toolbar = QHBoxLayout()

        self.load_csv_button = QPushButton("Load CSV...")
        self.load_csv_button.clicked.connect(self.load_csv_file)
        toolbar.addWidget(self.load_csv_button)

        self.clear_log_button = QPushButton("Clear")
        self.clear_log_button.clicked.connect(self.clear_log_table)
        toolbar.addWidget(self.clear_log_button)

        self.export_csv_button = QPushButton("Export...")
        self.export_csv_button.clicked.connect(self.export_csv_file)
        toolbar.addWidget(self.export_csv_button)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # CSV Table (row numbers are automatic via vertical header)
        self.log_table = QTableWidget()
        self.log_table.setColumnCount(2)
        self.log_table.setHorizontalHeaderLabels(["Timestamp", "Acquired"])
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.log_table.verticalHeader().setVisible(True)  # Show row numbers
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.log_table)

        # Summary label
        self.summary_label = QLabel("No data loaded.")
        self.summary_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self.summary_label)

        return group

    def _create_status_pane(self):
        """Create the top-right status indicator pane."""
        group = QGroupBox("Acquisition Status")
        layout = QVBoxLayout(group)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Large status indicator
        self.status_label = QLabel("IDLE")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_font = QFont()
        status_font.setPointSize(28)
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: #2d2d2d;
                color: #888888;
                border: 3px solid #555555;
                border-radius: 10px;
                padding: 20px;
                min-height: 60px;
            }
        """)
        layout.addWidget(self.status_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m images")
        layout.addWidget(self.progress_bar)

        # Status message
        self.status_message = QLabel("Ready to start acquisition.")
        self.status_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_message.setWordWrap(True)
        self.status_message.setStyleSheet("color: gray;")
        layout.addWidget(self.status_message)

        return group

    def _create_controls_pane(self):
        """Create the bottom-right controls pane."""
        group = QGroupBox("Camera Controls")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                padding-top: 12px;
                margin-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(12)

        # Common style for input widgets
        input_style = """
            QSpinBox, QLineEdit {
                padding: 8px 12px;
                border: 1px solid #555555;
                border-radius: 6px;
                background-color: #2d2d2d;
                color: white;
                font-size: 13px;
                min-height: 20px;
            }
            QSpinBox:focus, QLineEdit:focus {
                border: 2px solid #2196f3;
            }
            QSpinBox:disabled, QLineEdit:disabled {
                background-color: #1a1a1a;
                color: #666666;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 20px;
                border: none;
                background-color: #404040;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #505050;
            }
        """

        label_style = """
            QLabel {
                font-size: 13px;
                font-weight: 500;
                color: #cccccc;
                padding-right: 8px;
            }
        """

        # Form layout for inputs
        form_layout = QFormLayout()
        form_layout.setSpacing(14)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # Exposure input (microseconds)
        self.exposure_input = QSpinBox()
        self.exposure_input.setRange(1, 100000000)  # 1 µs to 100 seconds
        self.exposure_input.setValue(100000)  # Default: 100ms
        self.exposure_input.setSuffix(" µs")
        self.exposure_input.setToolTip("Exposure time in microseconds (10⁻⁶ seconds)")
        self.exposure_input.setStyleSheet(input_style)
        self.exposure_input.setMinimumWidth(160)
        exposure_label = QLabel("Exposure:")
        exposure_label.setStyleSheet(label_style)
        form_layout.addRow(exposure_label, self.exposure_input)

        # Gain input
        self.gain_input = QSpinBox()
        self.gain_input.setRange(0, 600)
        self.gain_input.setValue(140)  # Default from operations.py
        self.gain_input.setToolTip("Camera gain (counts per electron)")
        self.gain_input.setStyleSheet(input_style)
        self.gain_input.setMinimumWidth(160)
        gain_label = QLabel("Gain:")
        gain_label.setStyleSheet(label_style)
        form_layout.addRow(gain_label, self.gain_input)

        # Number of images
        self.num_images_input = QSpinBox()
        self.num_images_input.setRange(1, 100000)
        self.num_images_input.setValue(10)  # Reasonable default for testing
        self.num_images_input.setToolTip("Number of images to capture")
        self.num_images_input.setStyleSheet(input_style)
        self.num_images_input.setMinimumWidth(160)
        num_label = QLabel("Number of Images:")
        num_label.setStyleSheet(label_style)
        form_layout.addRow(num_label, self.num_images_input)

        # Image label prefix
        self.label_input = QLineEdit("image_")
        self.label_input.setToolTip("Prefix for saved FITS image filenames")
        self.label_input.setStyleSheet(input_style)
        self.label_input.setMinimumWidth(160)
        label_label = QLabel("Image Label:")
        label_label.setStyleSheet(label_style)
        form_layout.addRow(label_label, self.label_input)

        # Output folder
        folder_widget = QWidget()
        folder_layout = QHBoxLayout(folder_widget)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(8)
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Select output folder...")
        self.folder_input.setToolTip("Folder where FITS images will be saved")
        self.folder_input.setStyleSheet(input_style)
        self.folder_button = QPushButton("Browse...")
        self.folder_button.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                border: 1px solid #555555;
                border-radius: 6px;
                background-color: #404040;
                color: white;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #505050;
                border-color: #666666;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #555555;
            }
        """)
        self.folder_button.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_input, stretch=1)
        folder_layout.addWidget(self.folder_button)
        folder_label = QLabel("Output Folder:")
        folder_label.setStyleSheet(label_style)
        form_layout.addRow(folder_label, folder_widget)

        layout.addLayout(form_layout)

        # Spacer
        layout.addStretch()

        # Control buttons
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("▶  Start Acquisition")
        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: white;
                font-weight: bold;
                padding: 12px 24px;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
            QPushButton:disabled {
                background-color: #666666;
            }
        """)
        self.start_button.clicked.connect(self.start_acquisition)
        button_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("■  Stop")
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: #c62828;
                color: white;
                font-weight: bold;
                padding: 12px 24px;
                border-radius: 6px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
            QPushButton:disabled {
                background-color: #666666;
            }
        """)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_acquisition)
        button_layout.addWidget(self.stop_button)

        layout.addLayout(button_layout)

        return group

    # === Slot Methods ===

    def browse_folder(self):
        """Open dialog to select output folder."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder",
            os.path.expanduser("~")
        )
        if folder:
            self.folder_input.setText(folder)

    def start_acquisition(self):
        """Start the camera acquisition process."""
        # Validate inputs
        if not self.folder_input.text().strip():
            self.status_message.setText("Error: Please select an output folder.")
            self.status_message.setStyleSheet("color: red;")
            return

        if not self.label_input.text().strip():
            self.status_message.setText("Error: Please enter an image label.")
            self.status_message.setStyleSheet("color: red;")
            return

        # Clear previous log
        self.clear_log_table()
        self.time_log = []
        self.acq_log = []

        # Get parameters
        exp = self.exposure_input.value()
        gain = self.gain_input.value()
        n = self.num_images_input.value()
        label = self.label_input.text().strip()
        folder = self.folder_input.text().strip()

        # Update UI state
        self._set_running_state(True)
        self.progress_bar.setMaximum(n)
        self.progress_bar.setValue(0)

        # Create and start worker thread
        self.worker = CameraWorker(exp, gain, n, label, folder)
        self.worker.progress.connect(self._on_progress)
        self.worker.image_taken.connect(self._on_image_taken)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def stop_acquisition(self):
        """Stop the current acquisition."""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.status_message.setText("Stopping acquisition...")
            self.status_message.setStyleSheet("color: orange;")

    def _set_running_state(self, is_running):
        """Update UI to reflect running/stopped state."""
        self.start_button.setEnabled(not is_running)
        self.stop_button.setEnabled(is_running)
        self.exposure_input.setEnabled(not is_running)
        self.gain_input.setEnabled(not is_running)
        self.num_images_input.setEnabled(not is_running)
        self.label_input.setEnabled(not is_running)
        self.folder_input.setEnabled(not is_running)
        self.folder_button.setEnabled(not is_running)

        if is_running:
            self.status_label.setText("RUNNING")
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: #1b5e20;
                    color: #4caf50;
                    border: 3px solid #4caf50;
                    border-radius: 10px;
                    padding: 20px;
                    min-height: 60px;
                }
            """)
        else:
            self.status_label.setText("COMPLETED")
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: #0d47a1;
                    color: #2196f3;
                    border: 3px solid #2196f3;
                    border-radius: 10px;
                    padding: 20px;
                    min-height: 60px;
                }
            """)

    def _on_progress(self, current, total, message):
        """Handle progress updates from worker."""
        self.progress_bar.setValue(current)
        self.status_message.setText(message)
        self.status_message.setStyleSheet("color: white;")

    def _on_image_taken(self, image_num, timestamp, acquired):
        """Handle single image completion - add row to log table."""
        self.time_log.append(timestamp)
        self.acq_log.append(acquired)

        row = self.log_table.rowCount()
        self.log_table.insertRow(row)

        # Timestamp
        import datetime
        ts_str = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self.log_table.setItem(row, 0, QTableWidgetItem(ts_str))

        # Acquired status
        acq_item = QTableWidgetItem("YES" if acquired else "NO")
        acq_item.setForeground(QColor("#4caf50" if acquired else "#f44336"))
        self.log_table.setItem(row, 1, acq_item)

        # Scroll to bottom
        self.log_table.scrollToBottom()

        # Update summary
        total = len(self.acq_log)
        acquired_count = sum(self.acq_log)
        self.summary_label.setText(f"{total} images captured, {acquired_count} targets acquired.")
        self.summary_label.setStyleSheet("color: white;")

    def _on_finished(self, time_log, acq_log):
        """Handle acquisition completion."""
        self._set_running_state(False)

        total = len(acq_log)
        acquired_count = sum(acq_log)

        self.status_message.setText(
            f"Acquisition complete! {total} images captured, {acquired_count} targets acquired."
        )
        self.status_message.setStyleSheet("color: #4caf50;")

        # Auto-save log
        self._auto_save_log()

    def _on_error(self, error_msg):
        """Handle errors from worker."""
        self.status_message.setText(error_msg)
        self.status_message.setStyleSheet("color: orange;")
        # Don't stop - simulation mode continues

    def _auto_save_log(self):
        """Automatically save the log to CSV after acquisition."""
        if not self.time_log or not self.folder_input.text().strip():
            return

        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = self.folder_input.text().strip()

        # Save time log
        time_path = os.path.join(folder, f"timeLog_{timestamp}.csv")
        acq_path = os.path.join(folder, f"acqLog_{timestamp}.csv")

        try:
            os.makedirs(folder, exist_ok=True)

            with open(time_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp'])
                for t in self.time_log:
                    writer.writerow([t])

            with open(acq_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['acquired'])
                for a in self.acq_log:
                    writer.writerow([a])

            print(f"Logs saved to: {time_path}, {acq_path}")

        except Exception as e:
            print(f"Error saving logs: {e}")

    # === CSV Viewer Methods ===

    def load_csv_file(self):
        """Load a CSV file into the log viewer."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load CSV Log",
            CAMERA_CONTROL_DIR,
            "CSV Files (*.csv);;All Files (*)"
        )
        if file_path:
            self._load_csv(file_path)

    def _load_csv(self, file_path):
        """Parse and display a CSV file."""
        self.clear_log_table()

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader, None)

                if not headers:
                    self.summary_label.setText("Error: Empty CSV file.")
                    return

                # Adjust table columns based on CSV
                self.log_table.setColumnCount(len(headers))
                self.log_table.setHorizontalHeaderLabels(headers)

                for row_data in reader:
                    row = self.log_table.rowCount()
                    self.log_table.insertRow(row)
                    for col, value in enumerate(row_data):
                        self.log_table.setItem(row, col, QTableWidgetItem(value))

            self.summary_label.setText(f"Loaded: {os.path.basename(file_path)} ({self.log_table.rowCount()} rows)")
            self.summary_label.setStyleSheet("color: white;")

        except Exception as e:
            self.summary_label.setText(f"Error loading CSV: {e}")
            self.summary_label.setStyleSheet("color: red;")

    def clear_log_table(self):
        """Clear all rows from the log table."""
        self.log_table.setRowCount(0)
        # Reset to default columns
        self.log_table.setColumnCount(2)
        self.log_table.setHorizontalHeaderLabels(["Timestamp", "Acquired"])
        self.summary_label.setText("No data loaded.")
        self.summary_label.setStyleSheet("color: gray; font-style: italic;")

    def export_csv_file(self):
        """Export current log table to CSV."""
        if self.log_table.rowCount() == 0:
            self.summary_label.setText("Nothing to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Log to CSV",
            os.path.expanduser("~/acquisition_log.csv"),
            "CSV Files (*.csv)"
        )
        if file_path:
            try:
                with open(file_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    # Write headers
                    headers = [self.log_table.horizontalHeaderItem(i).text()
                               for i in range(self.log_table.columnCount())]
                    writer.writerow(headers)
                    # Write rows
                    for row in range(self.log_table.rowCount()):
                        row_data = [self.log_table.item(row, col).text() if self.log_table.item(row, col) else ""
                                    for col in range(self.log_table.columnCount())]
                        writer.writerow(row_data)

                self.summary_label.setText(f"Exported to: {os.path.basename(file_path)}")
                self.summary_label.setStyleSheet("color: #4caf50;")

            except Exception as e:
                self.summary_label.setText(f"Export error: {e}")
                self.summary_label.setStyleSheet("color: red;")

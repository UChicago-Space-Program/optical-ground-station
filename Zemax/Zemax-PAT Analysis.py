import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime
from matplotlib.collections import LineCollection

class ZemaxMeasurement():

    def __init__(self, path):
        self.path = path
        self.name = self.path.split("\\")[-1]
        self.degree = float(self.name.split("_")[2])

        with open(self.path, 'r') as f:
            file_content_split = f.read().splitlines()

        # Detect header
        header = file_content_split[0].split(',')
        start_index = 1 if "timestamp" in header[0].lower() else 0

        # Initialize lists
        self.t = []
        self.tiltX = []
        self.tiltY = []
        self.alpha = []
        self.beta = []
        self.collimator0_X = []
        self.collimator0_Y = []
        self.collimator0_centroid = []
        self.collimator1_X = []
        self.collimator1_Y = []
        self.collimator1_centroid = []
        self.trackingcameraX = []
        self.trackingcameraY = []
        self.trackingcameraCentroid = []

        # Find first valid timestamp for reference
        t0 = None
        for line in file_content_split[start_index:]:
            cols = line.strip().split(',')
            try:
                t0 = self._convert_timestamp_to_seconds(cols[0])
                break
            except:
                continue
        if t0 is None:
            raise ValueError("No valid timestamp found in file.")

        # Parse each row safely
        for i, line in enumerate(file_content_split[start_index:], start=start_index + 1):
            cols = line.strip().split(',')

            if len(cols) < 14:
                print(f"Skipping row {i}: not enough columns")
                continue

            try:
                # Try to convert all first
                t_val = self._convert_timestamp_to_seconds(cols[0]) - t0
                tiltX_val = float(cols[1])
                tiltY_val = float(cols[2])
                alpha_val = float(cols[3])
                beta_val = float(cols[4])
                c0x = float(cols[5])
                c0y = float(cols[6])
                c0cent = float(cols[7])
                c1x = float(cols[8])
                c1y = float(cols[9])
                c1cent = float(cols[10])
                tx = float(cols[11])
                ty = float(cols[12])
                tcent = float(cols[13])

            except ValueError:
                print(f"Skipping row {i}: one or more invalid values (e.g., 'N/A')")
                continue
            except Exception as e:
                print(f"Skipping row {i}: unexpected error: {e}")
                continue

            # Append ONLY if all succeeded
            self.t.append(t_val)
            self.tiltX.append(tiltX_val)
            self.tiltY.append(tiltY_val)
            self.alpha.append(alpha_val)
            self.beta.append(beta_val)
            self.collimator0_X.append(c0x)
            self.collimator0_Y.append(c0y)
            self.collimator0_centroid.append(c0cent)
            self.collimator1_X.append(c1x)
            self.collimator1_Y.append(c1y)
            self.collimator1_centroid.append(c1cent)
            self.trackingcameraX.append(tx)
            self.trackingcameraY.append(ty)
            self.trackingcameraCentroid.append(tcent)

    def _convert_timestamp_to_seconds(self, ts_str):
        """
        Converts timestamp of form '2014-172-00:00:00.100000000'
        to absolute seconds (float). 2014 = year, 172 = day of year.
        """
        year, doy, rest = ts_str.split('-')[0], ts_str.split('-')[1], '-'.join(ts_str.split('-')[2:])
        time_main, frac = (rest.split('.')[0], rest.split('.')[1]) if '.' in rest else (rest, '0')
        dt = datetime.strptime(f"{year}-{doy}-{time_main}", "%Y-%j-%H:%M:%S")
        seconds = (dt - datetime(dt.year, 1, 1)).total_seconds()
        return seconds + float(f"0.{frac}")

    def plot2Dtotime(self, x, y, t, title):
        """
        Plots the beam trajectory as a continuous line with color representing time.
        x, y, t must be lists or arrays of the same length.
        """
        # Convert to numpy arrays
        x = np.array(x)
        y = np.array(y)
        t = np.array(t)

        # Build line segments between consecutive points
        points = np.array([x, y]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)

        # Create the line collection
        lc = LineCollection(
            segments,
            cmap='plasma',
            norm=plt.Normalize(t.min(), t.max())
        )
        lc.set_array(t)
        lc.set_linewidth(2)

        # Plot
        fig, ax = plt.subplots(figsize=(7, 6))
        line = ax.add_collection(lc)

        # Autoscale the axes
        ax.autoscale()
        ax.set_aspect('equal', 'box')

        # Labels and title
        ax.set_xlabel("X position")
        ax.set_ylabel("Y position")
        ax.set_title("Beam Position Over Time")

        # Add colorbar for time
        cbar = fig.colorbar(line, ax=ax)
        cbar.set_label("Time (s)")

        ax.grid(True)
        plt.title(title)
        plt.show()

    def plot2D(self, x, t, title, ax=None):

        if ax is None:
            ax = plt.gca()
        x = np.array(x)
        t = np.array(t)
        line, = ax.plot(t, x, label=title)
        # Plot
        ax.set_xlabel("Time [s]")
        ax.set_ylabel("Detector centroid distance [mm]")
        return line

    def correlation(self, x, y, t, title):
        correlation_list = []
        for i in range(len(x)):
            correlation_list.append(x[i]/y[i])
        plt.plot(t, correlation_list)
        plt.xlabel("Time [s]")
        plt.ylabel("Ratio of centroids")
        plt.title(f"{title} at {self.degree} degrees pointing error", fontsize=12, fontweight='bold')


data_02 = ZemaxMeasurement(path=r"\\wsl.localhost\Ubuntu\home\jipa2004\optical-ground-station\Zemax\Zemax_PAT_0.2_Degree.csv")
data_005 = ZemaxMeasurement(path=r"\\wsl.localhost\Ubuntu\home\jipa2004\optical-ground-station\Zemax\Zemax_PAT_0.05_Degree.csv")

def plot_beam_paths(data):
    data.plot2Dtotime(data.collimator0_X, data.collimator0_Y, data.t, "Collimator 0")
    data.plot2Dtotime(data.collimator1_X, data.collimator1_Y, data.t, "Collimator 1")
    data.plot2Dtotime(data.trackingcameraX, data.trackingcameraY, data.t, "Tracking Camera")

def plot_centroids(data):
    fig, ax = plt.subplots(figsize=(8,5))

    h1 = data.plot2D(data.trackingcameraCentroid, data.t, "Tracking camera centroid", ax=ax)
    h2 = data.plot2D(data.collimator0_centroid, data.t, "Collimator 0 centroid", ax=ax)
    h3 = data.plot2D(data.collimator1_centroid, data.t, "Collimator 1 centroid", ax=ax)

    ax.set_title(f"Centroids at {data.degree} Degree Error", fontsize=12, fontweight='bold', loc='center')
    ax.legend(handles=[h1, h2, h3], loc="upper right", frameon=True)

    ax.grid(True)
    plt.tight_layout
    plt.show()

def plot_correlations(data):
    data.correlation(data.trackingcameraCentroid, data.collimator0_centroid, data.t, "Tracking camera and collimator 0 centroid correlation")
    plt.show()
    data.correlation(data.trackingcameraCentroid, data.collimator1_centroid, data.t, "Tracking camera and collimator 0 centroid correlation")
    plt.show()

plot_beam_paths(data_02)
plot_beam_paths(data_005)

plot_centroids(data_02)
plot_correlations(data_02)

plot_centroids(data_005)
plot_correlations(data_005)

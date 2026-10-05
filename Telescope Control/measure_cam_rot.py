"""
measure_camera_rotation.py
----------------------------
Measures the tracking camera's mechanical clocking angle (theta_mech)
and plate scale by observing sidereal drift of a star.

Uses the calibrated pointing model to identify the star's RA/Dec,
computes expected AltAz drift from ephemeris, captures tracking
camera frames, and compares measured pixel drift to expected sky drift.
"""

import time, csv, os
import numpy as np
import zwoasi as asi
import cv2
from contextlib import contextmanager
from datetime import datetime, timezone
from scipy.ndimage import center_of_mass, label, sum_labels
from astropy.coordinates import SkyCoord, AltAz, EarthLocation
from astropy.time import Time
from astropy.io import fits
import astropy.units as u

from OGS_control_methods import OGS_control
import nexstar_protocol as protocol
from camerapicture import takepic
# Camera open/close plumbing and the base profile list are reused from
# live_view.py so the preview behaves identically to the standalone tool.
from live_view import open_camera, close_camera, CAMERA_PROFILES, SAVE_DIR

# --- Config ---
SDK_PATH = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/libASICamera2.dylib.1.41'
CHICAGO = EarthLocation(lat=41.868, lon=-87.648)
PORT = '/dev/tty.PL2303G-USBtoUART110'
CAL_FILE = 'pointing_model_cal.csv'
U_TEL_ST = np.array([-3.00567405e-02, -5.12286421e-04, 9.99548063e-01])

IMAGE_BASE_DIR = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/Images'
TRACK_FOLDER = 'Drift'

TRACK_CAMERA_NAME = 'ZWO ASI585MM Pro'
EXPOSURE = 10000        # 10ms to prevent core saturation
GAIN = 250
N_FRAMES = 8           # frames to keep
INTERVAL = 1.0
DRIFT_LOG = 'drift_measurement.csv'

# Outlier rejection: max allowed frame-to-frame jump in pixels
MAX_JUMP_PX = 200

# --- Live view ---
# Two profiles for the tracking camera: a bright one for finding and framing
# the star, and one matching the drift capture settings so you can confirm the
# core is not saturated and the star has room to drift before committing.
ACQ_PROFILE_KEY = 'track'           # bright acquisition view (from live_view.py)
DRIFT_PROFILE_KEY = 'track_drift'   # mirrors EXPOSURE/GAIN below

PROFILES = [dict(p) for p in CAMERA_PROFILES]   # copy; never mutate live_view's
PROFILES.insert(1, {'key': DRIFT_PROFILE_KEY, 'name': TRACK_CAMERA_NAME,
                    'exposure': EXPOSURE, 'gain': GAIN})

DISPLAY_MAX = 900          # px; same downscale cap live_view.py uses
OPEN_SETTLE = 0.5          # s to wait before opening a camera, so the SDK does
                           # not return a handle it still considers closed


def init_cameras():
    """
    Initialise the SDK and return the list of connected camera names.

    No handle is opened here: the ZWO SDK will not allow two open handles on
    one device, so LiveView owns the handle and lends it to takepic() via
    LiveView.paused_on().
    """
    asi.init(SDK_PATH)
    cameras = asi.list_cameras()
    if not cameras:
        raise RuntimeError('No ZWO cameras found.')
    print(f"Connected cameras: {cameras}")
    if TRACK_CAMERA_NAME not in cameras:
        raise RuntimeError(f"'{TRACK_CAMERA_NAME}' not found in {cameras}")
    return cameras


class LiveView:
    """
    Live preview that behaves like live_view.py: one open camera handle at a
    time, 's' switches profile, 'w' saves a snapshot. The caller pumps the loop
    with show() and dispatches on the returned key, so extra keys can be
    layered on per phase.
    """

    def __init__(self, cameras, start_key):
        self.cameras = cameras
        self.frame = None
        self.idx = next((i for i, p in enumerate(PROFILES)
                         if p['key'] == start_key), 0)
        self._open(self.idx)

    def _open(self, idx, settle=OPEN_SETTLE):
        """
        Open the profile at idx.

        Settles first: the ZWO SDK can hand back a handle it still considers
        closed if a device is reopened too soon after being closed. Not retried
        on failure -- open_camera() can raise after the device is already open,
        and a retry would then hit a double-open on an orphaned handle.
        """
        time.sleep(settle)
        opened = open_camera(self.cameras, PROFILES[idx])
        if opened is None:
            raise RuntimeError(f"'{PROFILES[idx]['name']}' not found in "
                               f"{self.cameras}.")
        self.camera, self.info, self.width, self.height = opened
        self.idx = idx

    def show(self):
        """Grab and display one frame. Returns the key pressed (255 if none)."""
        self.frame = self.camera.capture_video_frame()
        display = self.frame
        if max(self.height, self.width) > DISPLAY_MAX:
            s = DISPLAY_MAX / max(self.height, self.width)
            display = cv2.resize(self.frame,
                                 (int(self.width * s), int(self.height * s)))
        cv2.imshow(f'Live View - {PROFILES[self.idx]["key"]} '
                   f'({self.info["Name"]})', display)
        return cv2.waitKey(1) & 0xFF

    def switch(self):
        """Cycle to the next profile that opens (same logic as live_view.py)."""
        for step in range(1, len(PROFILES) + 1):
            nxt = (self.idx + step) % len(PROFILES)
            print(f"Switching to '{PROFILES[nxt]['key']}'...")
            if PROFILES[nxt]['name'] == self.info['Name']:
                # Same physical device: reuse the handle. Closing and reopening
                # the same ZWO device back-to-back returns a handle the SDK
                # still considers closed (capture then fails "Camera closed").
                # This mirrors paused_on's same-device handling.
                try:
                    self.camera.stop_video_capture()
                except Exception:
                    pass
                self.idx = nxt
                self._apply_profile()
                self.camera.start_video_capture()
                print(f"Exposure: {PROFILES[nxt]['exposure']} us  "
                      f"Gain: {PROFILES[nxt]['gain']}")
                return True
            # Different device: safe to close and open.
            close_camera(self.camera, self.info)
            try:
                self._open(nxt)
                return True
            except RuntimeError:
                continue
        print('No other camera available to switch to.')
        return False

    def snapshot(self):
        """Save the current preview frame as FITS, as live_view.py's 'w' does."""
        os.makedirs(SAVE_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        path = os.path.join(
            SAVE_DIR, f'snap_{self.info["Name"].replace(" ", "_")}_{ts}.fits')
        fits.writeto(path, self.frame, overwrite=True)
        print(f"  Snapshot saved: {path}")

    def _apply_profile(self):
        """Re-apply the current profile's controls (takepic overwrites them)."""
        p = PROFILES[self.idx]
        self.camera.set_control_value(
            asi.ASI_BANDWIDTHOVERLOAD,
            self.camera.get_controls()['BandWidth']['MinValue'])
        self.camera.set_control_value(asi.ASI_EXPOSURE, p['exposure'])
        self.camera.set_control_value(asi.ASI_GAIN, p['gain'])
        self.camera.set_image_type(asi.ASI_IMG_RAW8)

    @contextmanager
    def paused_on(self, camera_name):
        """
        Pause the preview and yield a handle for camera_name in snapshot mode,
        ready for takepic().

        Dispatches on the camera NAME, not the profile index, because several
        profiles can share one device (ACQ and DRIFT are both the tracking
        camera). If the wanted device is already open the handle is reused --
        it is NOT closed and reopened, because the ZWO SDK does not reliably
        return a working handle from a back-to-back close/reopen of the same
        device (get_controls then fails with "Camera closed").
        """
        swap = (self.info['Name'] != camera_name)
        was = self.idx
        print('  [live view paused]')

        if swap:
            close_camera(self.camera, self.info)
            self._open(next(i for i, p in enumerate(PROFILES)
                            if p['name'] == camera_name))
        # Video mode must be off for takepic()'s snapshot capture.
        try:
            self.camera.stop_video_capture()
        except Exception:
            pass

        try:
            yield self.camera
        finally:
            if swap:
                close_camera(self.camera, self.info)
                self._open(was)          # reopens and restarts video
            else:
                self._apply_profile()    # undo takepic's exposure/gain
                self.camera.start_video_capture()
            print('  [live view resumed]')

    def close(self):
        try:
            self.camera.stop_video_capture()
        except Exception:
            pass
        try:
            self.camera.close()
        except Exception:
            pass
        cv2.destroyAllWindows()
        print('Live view stopped.')


def acquire_star(lv):
    """
    Preview loop for framing the star before the drift run.

    Returns True to proceed with the measurement, False to abort.
    """
    print("\n" + "-"*60)
    print("STAR ACQUISITION")
    print()
    print("Centre a star in the tracking camera, offset toward the edge the")
    print("drift comes FROM so it has room to cross the frame.")
    print()
    print("Live view keys (window must have focus):")
    print(f"  s   switch profile ({'/'.join(p['key'] for p in PROFILES)})")
    print(f"      '{DRIFT_PROFILE_KEY}' shows the actual capture settings")
    print(f"      ({EXPOSURE} us, gain {GAIN}) -- check the core is not")
    print("      saturated there before starting")
    print("  w   save a snapshot of the current preview")
    print("  r   start the drift measurement")
    print("  q   abort")
    print("-"*60)

    while True:
        key = lv.show()
        if key == ord('q'):
            print("Aborted.")
            return False
        elif key == ord('s'):
            if not lv.switch():
                return False
        elif key == ord('w'):
            lv.snapshot()
        elif key == ord('r'):
            return True


def capture_drift(lv):
    """
    Run the drift capture with the preview paused for the whole sequence.

    The preview is paused once around the entire run rather than between
    frames: toggling video mode N_FRAMES times would add jitter to the
    per-frame timestamps and to INTERVAL, which the fit depends on.

    Returns (centroids, times).
    """
    centroids, times = [], []

    with lv.paused_on(TRACK_CAMERA_NAME) as camera:
        for i in range(N_FRAMES):
            # Log time right before shutter opens
            t_start = Time.now()

            # Capture and save using the centralized function
            prefix = f'drift_{i:03d}'
            saved_path = takepic(EXPOSURE, GAIN, prefix, camera,
                                 TRACK_FOLDER, IMAGE_BASE_DIR, talk=False)

            # Log time right after the image data clears the USB bus
            t_end = Time.now()

            # Calculate the true midpoint of the exposure/readout window
            t = t_start + (t_end - t_start) / 2

            # Load the saved FITS file to calculate the centroid
            if saved_path and os.path.exists(saved_path):
                frame = fits.getdata(saved_path)
                c = find_brightest(frame)
            else:
                c = None

            if c is None:
                print(f"  [{i}] no source — skipped")
                continue

            centroids.append(c)
            times.append(t)

            dt = (t - times[0]).sec if len(times) > 1 else 0
            print(f"  [{i}] x={c[0]:.1f}  y={c[1]:.1f}  "
                  f"dt={dt:.2f}s  saved: {os.path.basename(saved_path)}")

            time.sleep(INTERVAL)

    return centroids, times


def find_brightest(image):
    # Calculate background median and noise
    median = np.median(image)
    std = np.std(image)
    
    # 5-sigma detection threshold
    thresh = median + (5 * std)
    
    labeled, n = label(image > thresh)
    if n == 0:
        return None
    # Per-label flux in a single vectorized pass. The previous
    # [image[labeled == i].sum() for i in range(1, n+1)] scanned the whole
    # 8.3 MP frame once per blob; with ~1500 noise blobs that was ~6 s/frame.
    idx = np.arange(1, n + 1)
    fluxes = sum_labels(image, labeled, index=idx)
    best = idx[np.argmax(fluxes)]
    cy, cx = center_of_mass(image, labels=labeled, index=best)
    return cx, cy


def compute_drift_rate(star_radec, t0, location, dt=10.0):
    """GREAT-CIRCLE drift rate (arcsec/s) as the camera sees it.

    The azimuth COORDINATE rate must be foreshortened by cos(alt) to give
    the angular (cross-elevation) motion in the focal plane. Omitting this
    made theta_mech and the drift plate scale depend on where you pointed
    (bug found 2026-08-09: drift scale read 0.262 vs optics 0.2136).
    """
    t1 = t0 + dt * u.second
    p0 = star_radec.transform_to(AltAz(obstime=t0, location=location))
    p1 = star_radec.transform_to(AltAz(obstime=t1, location=location))
    daz = ((p1.az.deg - p0.az.deg + 180) % 360 - 180) * 3600
    dalt = (p1.alt.deg - p0.alt.deg) * 3600
    daz_gc = daz * np.cos(np.radians(p0.alt.deg))
    return daz_gc / dt, dalt / dt


def reject_outliers(centroids, times):
    """Remove points with frame-to-frame jumps exceeding MAX_JUMP_PX."""
    good = [0]
    for i in range(1, len(centroids)):
        dx = centroids[i][0] - centroids[good[-1]][0]
        dy = centroids[i][1] - centroids[good[-1]][1]
        if np.sqrt(dx**2 + dy**2) < MAX_JUMP_PX:
            good.append(i)
        else:
            print(f"  Rejecting frame {i}: jump={np.sqrt(dx**2+dy**2):.0f} px")
    return [centroids[i] for i in good], [times[i] for i in good]


def main():
    # --- Telescope ---
    ctrl = OGS_control(port=PORT, location=CHICAGO,
                       file_start_time=time.time())
    ctrl.fit_pointing_model(CAL_FILE)
    ctrl.u_tel_st = U_TEL_ST

    # --- Camera + star acquisition ---
    # The live view runs first so the star can be framed. The encoder read
    # below therefore happens AFTER any repositioning, which matters: the star
    # is identified from the current pointing, so reading it before acquisition
    # would identify the wrong patch of sky.
    cameras = init_cameras()
    lv = LiveView(cameras, ACQ_PROFILE_KEY)
    try:
        if not acquire_star(lv):
            lv.close()
            return

        az, alt = ctrl.get_azm_alt()
        t0 = Time.now()
        star = SkyCoord(az=az*u.deg, alt=alt*u.deg,
                        frame=AltAz(obstime=t0, location=CHICAGO)
                        ).transform_to('icrs')
        print(f"\nStar: RA={star.ra.deg:.4f}  Dec={star.dec.deg:+.4f}")
        print(f"      Az={az:.4f}  Alt={alt:.4f}")

        daz_rate, dalt_rate = compute_drift_rate(star, t0, CHICAGO)
        sky_drift_angle = np.degrees(np.arctan2(dalt_rate, daz_rate))
        sky_drift_rate = np.sqrt(daz_rate**2 + dalt_rate**2)
        print(f"\nExpected sidereal drift:")
        print(f"  dAz/dt={daz_rate:+.3f}  dAlt/dt={dalt_rate:+.3f} arcsec/s")
        print(f"  Rate={sky_drift_rate:.3f} arcsec/s  "
              f"Angle={sky_drift_angle:.2f} deg")

        # Disable mount tracking
        print("\nDisabling mount tracking...")
        ctrl.nexstar_command_and_read_until(protocol.TRACKING_RATE_OFF, b'#')

        # Force a hard 10-second wait for the mechanical deceleration ramp to
        # finish. The preview keeps running through this so the star can be
        # watched settling.
        print("Waiting 10 seconds for mount to mechanically settle...")
        t_settle = time.time() + 10
        while time.time() < t_settle:
            lv.show()

        print(f"\nCapturing {N_FRAMES} discrete frames...")
        centroids, times = capture_drift(lv)
    finally:
        lv.close()

    if len(centroids) < 5:
        print("Not enough centroids.")
        return

    # --- Outlier rejection ---
    centroids, times = reject_outliers(centroids, times)
    if len(centroids) < 5:
        print("Not enough points after outlier rejection.")
        return

    # --- Fit ---
    centroids = np.array(centroids)
    t_sec = np.array([(t - times[0]).sec for t in times])
    x, y = centroids[:, 0], centroids[:, 1]

    px = np.polyfit(t_sec, x, 1)
    py = np.polyfit(t_sec, y, 1)
    dx_dt, dy_dt = px[0], py[0]

    pixel_drift_angle = np.degrees(np.arctan2(dy_dt, dx_dt))
    pixel_drift_rate = np.sqrt(dx_dt**2 + dy_dt**2)

    x_fit = np.polyval(px, t_sec)
    y_fit = np.polyval(py, t_sec)
    residuals = np.sqrt((x - x_fit)**2 + (y - y_fit)**2)
    rms = np.sqrt(np.mean(residuals**2))

    # One drift direction CANNOT distinguish a pure rotation from a rotation
    # composed with a mirror flip (parity). The empirically required
    # P_SIGN_ALT = -1 in the tracking loops says this chain likely contains a
    # flip, in which case the rotation-only theta varies with pointing as
    # (C - 2*sky_angle) -- exactly the observed inconsistency. Report both
    # hypotheses; the one that repeats across stars with DIFFERENT drift
    # angles is the true geometry.
    theta_mech = (pixel_drift_angle - sky_drift_angle + 180) % 360 - 180
    pixel_angle_flip = np.degrees(np.arctan2(-dy_dt, dx_dt))
    theta_mech_flip = (pixel_angle_flip - sky_drift_angle + 180) % 360 - 180

    plate_scale_drift = (sky_drift_rate / pixel_drift_rate
                         if pixel_drift_rate > 0 else float('nan'))
    plate_scale_optics = 206265 * 0.0029 / 2800

    # --- Save CSV ---
    with open(DRIFT_LOG, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['star_ra', star.ra.deg, 'star_dec', star.dec.deg,
                     'az', az, 'alt', alt])
        w.writerow(['frame', 't_sec', 'x_px', 'y_px',
                     'x_fit', 'y_fit', 'residual_px'])
        for i in range(len(centroids)):
            w.writerow([i, f'{t_sec[i]:.3f}',
                        f'{x[i]:.2f}', f'{y[i]:.2f}',
                        f'{x_fit[i]:.2f}', f'{y_fit[i]:.2f}',
                        f'{residuals[i]:.2f}'])

    # --- Results ---
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(f"\nStar: RA={star.ra.deg:.4f}  Dec={star.dec.deg:+.4f}")
    print(f"      Az={az:.4f}  Alt={alt:.4f}")
    print(f"\nSky drift: {sky_drift_rate:.3f} arcsec/s  "
          f"at {sky_drift_angle:.2f} deg")
    print(f"\nPixel drift ({len(centroids)} frames, {t_sec[-1]:.1f}s):")
    print(f"  dx/dt={dx_dt:.4f} px/s  dy/dt={dy_dt:.4f} px/s")
    print(f"  {pixel_drift_rate:.4f} px/s  at {pixel_drift_angle:.2f} deg")
    print(f"  Fit RMS = {rms:.2f} px")
    print(f"\ntheta_mech (no flip)   = {theta_mech:.2f} deg")
    print(f"theta_mech (with flip) = {theta_mech_flip:.2f} deg")
    print("-> repeat on a second star with a different drift direction;")
    print("   the value that REPEATS is the real geometry.")
    print(f"Plate scale (drift)  = {plate_scale_drift:.4f} arcsec/px")
    print(f"Plate scale (optics) = {plate_scale_optics:.4f} arcsec/px")
    print(f"\nData saved to {DRIFT_LOG}")
    print(f"\nFor Main_controller.py:")
    # Two-star test 2026-08-09 confirmed the chain HAS a mirror flip. In the
    # tracking code's convention (rotation + P_SIGN_ALT = -1) the value to
    # use is MINUS the with-flip angle: diag(1,-1)R(-t) = R(t)diag(1,-1).
    print(f"  THETA_MECH  = {-theta_mech_flip:.2f}   (flip-aware; "
          f"requires P_SIGN_ALT = -1)")
    print(f"  PLATE_SCALE = {plate_scale_optics:.4f}")


if __name__ == '__main__':
    main()
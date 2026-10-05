import time
import os
import numpy as np
import cv2
from contextlib import contextmanager
from datetime import datetime, timezone
from astropy.coordinates import EarthLocation
from astropy.time import Time
from OGS_control_methods import OGS_control
from camerapicture import takepic
from platesolveAUTO import auto_solve
# Preview plumbing is reused wholesale from live_view.py so there is one
# source of truth for camera opening/closing and the profile definitions.
from live_view import (open_camera, close_camera, CAMERA_PROFILES, SAVE_DIR)

# --- Constants ---
CHICAGO = EarthLocation(lat=41.868, lon=-87.648)

# --- Config ---
PORT = '/dev/tty.PL2303G-USBtoUART110'
CAL_FILE = 'pointing_model_cal.csv'
BORESIGHT_FILE = 'u_tel_st.txt'     # persisted telescope boresight in ST frame

# Star tracker camera settings.
# Taken from the star tracker entry in live_view.CAMERA_PROFILES, so the
# preview and the plate-solve captures always use identical exposure/gain.
# To change them, edit live_view.CAMERA_PROFILES -- not here.
ST_PROFILE_KEY = 'st'      # key of the star tracker in live_view.CAMERA_PROFILES
_ST_PROFILE = next(p for p in CAMERA_PROFILES if p['key'] == ST_PROFILE_KEY)
ST_EXPOSURE = _ST_PROFILE['exposure']    # microseconds
ST_GAIN = _ST_PROFILE['gain']
IMAGE_BASE_DIR = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/Images'
IMAGE_FOLDER = 'ST'

# Telescope boresight pixel in star tracker image.
# Measured once via terrestrial alignment (see measure_tel_pixel below).
# Set to None to skip inter-camera alignment.
TEL_PIXEL = None           # e.g. (437.2, 512.8) once measured

# ZWO ASI SDK path
SDK_PATH = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/libASICamera2.dylib.1.41'
ST_CAMERA_NAME = 'ZWO ASI120MM Mini'

# --- Live view ---
# Profile the preview opens on for each phase. Terrestrial alignment needs the
# TRACKING camera (that is where the target gets centred); the calibration
# points need the star tracker (that is what gets plate solved).
ALIGN_PROFILE_KEY = 'track'
CAL_PROFILE_KEY = 'st'
DISPLAY_MAX = 900          # px; same downscale cap live_view.py uses
OPEN_SETTLE = 0.5          # s to wait before opening a camera, so the SDK does
                           # not return a handle it still considers closed

# The preview uses live_view.CAMERA_PROFILES as-is, and ST_EXPOSURE/ST_GAIN
# above are read out of that same list, so preview and capture cannot drift.
PROFILES = CAMERA_PROFILES


# =================================================================
# Camera init
# =================================================================

def init_cameras():
    """
    Initialise the SDK and return the list of connected camera names.

    Note: no camera handle is opened here. The ZWO SDK will not allow two
    open handles on one device, so LiveView owns the handle and lends it to
    takepic() via LiveView.paused_on_star_tracker().
    """
    import zwoasi as asi
    asi.init(SDK_PATH)
    cameras = asi.list_cameras()
    if not cameras:
        raise RuntimeError('No ZWO cameras found.')
    print(f'Connected cameras: {cameras}')
    if ST_CAMERA_NAME not in cameras:
        raise RuntimeError(f'Star tracker "{ST_CAMERA_NAME}" not found. '
                           f'Connected cameras: {cameras}')
    return cameras


# =================================================================
# Live view  (preview loop reused from live_view.py)
# =================================================================

class LiveView:
    """
    Live preview that behaves like live_view.py: one open camera handle at a
    time, 's' switches profile, 'w' saves a snapshot. The caller pumps the
    loop by calling show() and dispatches on the returned key, so extra keys
    ('r' to record, 'c' to capture) can be layered on per phase.
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
        closed if a device is reopened too soon after being closed. This is not
        retried on failure -- open_camera() can raise after the device is
        already open, and a retry would then hit a double-open on an orphaned
        handle we no longer have a reference to.
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
        cv2.imshow(f'Live View - {self.info["Name"]}', display)
        return cv2.waitKey(1) & 0xFF

    def switch(self):
        """Cycle to the next profile that opens (same logic as live_view.py)."""
        close_camera(self.camera, self.info)
        for step in range(1, len(PROFILES) + 1):
            nxt = (self.idx + step) % len(PROFILES)
            print(f"Switching to '{PROFILES[nxt]['key']}'...")
            try:
                self._open(nxt)
                return True
            except RuntimeError:
                continue
        print('No other camera available to switch to.')
        return False

    def snapshot(self):
        """Save the current preview frame as FITS, as live_view.py's 'w' does."""
        from astropy.io import fits
        os.makedirs(SAVE_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        path = os.path.join(
            SAVE_DIR, f'snap_{self.info["Name"].replace(" ", "_")}_{ts}.fits')
        fits.writeto(path, self.frame, overwrite=True)
        print(f'  Snapshot saved: {path}')

    def _apply_profile(self):
        """
        Re-apply the current profile's controls. Needed after takepic(), which
        overwrites exposure/gain on the shared handle (and may have used a
        gain_override).
        """
        import zwoasi as asi
        p = PROFILES[self.idx]
        self.camera.set_control_value(
            asi.ASI_BANDWIDTHOVERLOAD,
            self.camera.get_controls()['BandWidth']['MinValue'])
        self.camera.set_control_value(asi.ASI_EXPOSURE, p['exposure'])
        self.camera.set_control_value(asi.ASI_GAIN, p['gain'])
        self.camera.set_image_type(asi.ASI_IMG_RAW8)

    @contextmanager
    def paused_on_star_tracker(self):
        """
        Pause the preview and yield a star tracker handle usable by takepic().

        If the star tracker is already the live camera -- the normal case during
        calibration -- the handle is reused: video capture is stopped, the
        handle is lent out, then the profile controls are reapplied and video
        restarted. It is NOT closed and reopened, because the ZWO SDK does not
        reliably return a working handle from a back-to-back close/reopen of the
        same device (get_controls then fails with "Camera closed").

        Only when the preview is on a different camera is that camera closed and
        the star tracker opened for the duration, since the SDK will not allow
        two open handles on one device.
        """
        st_idx = next(i for i, p in enumerate(PROFILES)
                      if p['key'] == ST_PROFILE_KEY)
        swap = (self.idx != st_idx)
        was = self.idx
        print('  [live view paused]')

        if swap:
            close_camera(self.camera, self.info)
            self._open(st_idx)
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


# =================================================================
# Automated plate solve: capture + solve
# =================================================================

def capture_and_solve(camera, image_name, tel_pixel=None, gain_override=None):
    """
    Take a star tracker image, plate solve it, return results.

    Returns dict with boresight_ra, boresight_dec (and tel_ra,
    tel_dec if tel_pixel provided), plus 't_exp' -- the EXPOSURE MIDPOINT of
    the frame that was solved. Returns None on failure.

    t_exp is bracketed around takepic() rather than read before it: takepic
    spends time configuring the camera and settling before the shutter opens,
    and the solved RA/Dec belongs to the exposure, not to the setup.
    """
    gain = gain_override if gain_override is not None else ST_GAIN
    t_start = Time.now()
    image_path = takepic(ST_EXPOSURE, gain, image_name, camera,
                         IMAGE_FOLDER, IMAGE_BASE_DIR, talk=False)
    t_exp = t_start + (Time.now() - t_start) / 2
    if gain_override is not None:
        print(f'  (using gain={gain})')
    print(f'  Plate solving {image_path}...')
    result = auto_solve(image_path, tel_pixel=tel_pixel)
    if result is None:
        print('  Plate solve FAILED.')
    else:
        result['t_exp'] = t_exp
        print(f'  Solved: RA={result["boresight_ra"]:.6f}  '
              f'Dec={result["boresight_dec"]:+.6f}')
        if 'tel_ra' in result:
            print(f'  Tel boresight: RA={result["tel_ra"]:.6f}  '
                  f'Dec={result["tel_dec"]:+.6f}')
    return result


# =================================================================
# Terrestrial boresight alignment (one-time measurement)
# =================================================================

def measure_tel_pixel(cameras):
    """
    One-time measurement of the telescope boresight pixel location
    in the star tracker image, using a distant terrestrial target.

    Runs the live view on the TRACKING camera so the target can be centred
    interactively. The preview pauses while the star tracker image is taken
    and resumes afterwards.

    Procedure:
      1. Point telescope at a distant light / identifiable target
      2. Center the target in the tracking camera (live view)
      3. Press 'c' -- a star tracker image is taken
      4. Identify the target in the ST image, enter its pixel coords

    Returns (pixel_x, pixel_y) or None if aborted.
    """
    print('\n' + '-'*60)
    print('TERRESTRIAL BORESIGHT ALIGNMENT')
    print()
    print('This measures where the telescope boresight falls in')
    print('the star tracker image. Only needs to be done once.')
    print()
    print('Steps:')
    print('  1. Point at a distant light (cell tower, building, etc.)')
    print('  2. Center it in the TELESCOPE tracking camera using the')
    print('     live view window.')
    print('  3. Press c  -- a star tracker image will be taken.')
    print('  4. Enter the pixel coordinates of the target in the')
    print('     star tracker image.')
    print()
    print('Live view keys (window must have focus):')
    print('  c   capture the star tracker image')
    print('  s   switch camera (track <-> star tracker)')
    print('  w   save a snapshot of the current preview')
    print('  q   skip alignment')
    print('-'*60)

    lv = LiveView(cameras, ALIGN_PROFILE_KEY)
    image_path = None
    try:
        while True:
            key = lv.show()
            if key == ord('q'):
                print('Skipped.')
                return None
            elif key == ord('s'):
                if not lv.switch():
                    return None
            elif key == ord('w'):
                lv.snapshot()
            elif key == ord('c'):
                with lv.paused_on_star_tracker() as st_camera:
                    image_path = takepic(ST_EXPOSURE, ST_GAIN,
                                         'boresight_alignment', st_camera,
                                         IMAGE_FOLDER, IMAGE_BASE_DIR,
                                         talk=False)
                print(f'  Star tracker image saved: {image_path}')
                print('  Open the image and find the target pixel '
                      'coordinates.')
                break
    finally:
        lv.close()

    try:
        px = float(input('  Target pixel X: ').strip())
        py = float(input('  Target pixel Y: ').strip())
        confirm = input(f'  Confirm pixel ({px:.1f}, {py:.1f})? (y/n): ').strip().lower()
        if confirm == 'y':
            print(f'  Telescope boresight pixel: ({px:.1f}, {py:.1f})')
            return (px, py)
        else:
            print('  Aborted.')
            return None
    except ValueError:
        print('  Invalid input. Aborted.')
        return None


# =================================================================
# One calibration point
# =================================================================

def record_point(controller, lv, tel_pixel):
    """
    Record one calibration point: encoders, then a paused star tracker
    capture + plate solve, then the solved RA/Dec back into the CSV.

    Returns (success, tel_measurement) where tel_measurement is
    (tel_ra, tel_dec, solve_time) or None.
    """
    point_id = controller.record_raw_position(CAL_FILE)
    if point_id is None:
        print('  Encoder read failed. Try again.')
        return False, None

    # Capture + plate solve (with retry). The preview is paused for the whole
    # retry sequence, including the terminal gain prompt.
    gain_override = None
    result = None
    with lv.paused_on_star_tracker() as st_camera:
        while True:
            result = capture_and_solve(st_camera, f'cal_{point_id:03d}',
                                       tel_pixel=tel_pixel,
                                       gain_override=gain_override)
            if result is not None:
                break
            cmd = input('  Plate solve failed. '
                        'r=retry, g=retry with more gain, s=skip: '
                        ).strip().lower()
            if cmd == 'g':
                try:
                    gain_override = int(input(
                        f'  New gain (current={ST_GAIN}): ').strip())
                except ValueError:
                    print('  Invalid number, using default.')
                    gain_override = None
            elif cmd != 'r':
                break

    if result is None:
        print(f'  Point {point_id} skipped.')
        return False, None

    # The row's timestamp is overwritten with the exposure midpoint. The mount
    # is parked, so the encoder values are valid whenever they were read, but
    # the solved RA/Dec belongs to the instant the shutter was open -- and
    # fit_pointing_model converts RA/Dec to ENU using this timestamp.
    solve_time = result['t_exp']
    ra, dec = result['boresight_ra'], result['boresight_dec']
    if not controller.record_true_position(point_id, ra, dec, CAL_FILE,
                                          timestamp=solve_time):
        print(f'  Failed to write point {point_id}.')
        return False, None
    print(f'  Point {point_id} complete.  (t_exp={solve_time.iso})')

    tel_meas = None
    if 'tel_ra' in result:
        tel_meas = (result['tel_ra'], result['tel_dec'], solve_time)
    return True, tel_meas


# =================================================================
# Main
# =================================================================

def main():
    controller = OGS_control(port=PORT, location=CHICAGO,
                             file_start_time=time.time())
    controller.set_time_to_now()

    cameras = init_cameras()

    # Determine tel_pixel
    tel_pixel = TEL_PIXEL
    if tel_pixel is None:
        print('\nNo telescope boresight pixel configured (TEL_PIXEL = None).')
        cmd = input('Run terrestrial alignment? (y/n): ').strip().lower()
        if cmd == 'y':
            tel_pixel = measure_tel_pixel(cameras)

    if tel_pixel is not None:
        print(f'\nUsing telescope boresight pixel: ({tel_pixel[0]:.1f}, {tel_pixel[1]:.1f})')
        print('Every plate solve will also update inter-camera alignment.')
    else:
        print('\nNo telescope boresight pixel set.')
        print('Calibration will fit the 8-DOF model only.')

    print('\n' + '='*60)
    print('POINTING MODEL CALIBRATION  (Riesing Ch. 3)')
    print()
    print('Workflow at each position:')
    print('  1. Slew to a position, let the mount settle.')
    print('  2. Press r in the live view to record + plate solve.')
    print()
    print('Live view keys (window must have focus):')
    print('  r   record a new point (preview pauses for the capture)')
    print('  s   switch camera (star tracker <-> track)')
    print('  w   save a snapshot of the current preview')
    print('  q   fit the model and exit')
    print()
    print(f'Calibration data -> {CAL_FILE}')
    print('='*60)

    n_points = 0
    tel_boresight_measurements = []     # collect (ra, dec, time) per image

    lv = LiveView(cameras, CAL_PROFILE_KEY)
    try:
        while True:
            key = lv.show()
            if key == ord('q'):
                break
            elif key == ord('s'):
                if not lv.switch():
                    break
            elif key == ord('w'):
                lv.snapshot()
            elif key == ord('r'):
                ok, tel_meas = record_point(controller, lv, tel_pixel)
                if ok:
                    n_points += 1
                    print(f'  ({n_points} total)')
                if tel_meas is not None:
                    tel_boresight_measurements.append(tel_meas)
    finally:
        lv.close()

    # Fit
    if n_points < 3:
        print(f'\nNeed >= 3 complete points (have {n_points}). '
              f'Exiting without fit.')
        return

    print(f'\nFitting model from {n_points} point(s) in {CAL_FILE}...')
    try:
        controller.fit_pointing_model(CAL_FILE)
    except Exception as e:
        print(f'Fit failed: {e}')
        return

    # Apply telescope boresight from plate-solve data
    if tel_boresight_measurements:
        # Use the most recent measurement (taken closest to current
        # encoder position, so the rotation chain is most accurate)
        tel_ra, tel_dec, tel_time = tel_boresight_measurements[-1]
        print(f'\nApplying telescope boresight from plate solve:')
        print(f'  RA={tel_ra:.6f}  Dec={tel_dec:+.6f}')
        controller.set_tel_boresight_from_pixel(tel_ra, tel_dec, tel_time)

        # Persist the computed u_tel_st so the star test uses this measured
        # value instead of a hardcoded one.
        np.savetxt(BORESIGHT_FILE, controller.u_tel_st)
        print(f'  Saved telescope boresight -> {BORESIGHT_FILE}  '
              f'u_tel_st={controller.u_tel_st}')

        # Print consistency of all measurements
        if len(tel_boresight_measurements) > 1:
            print(f'\nTelescope boresight consistency '
                  f'({len(tel_boresight_measurements)} measurements):')
            for i, (r, d, t) in enumerate(tel_boresight_measurements):
                print(f'  [{i}] RA={r:.6f}  Dec={d:+.6f}')

    ts = datetime.now().strftime('%Y-%m-%d_%H-%M')
    print(f'\nCalibration complete at {ts}.')
    print('get_azm_alt() returns telescope boresight position.')
    print('goto commands point the telescope at the target.')
    if tel_pixel is not None:
        print(f'\nTo reuse this boresight pixel, set in config:')
        print(f'  TEL_PIXEL = ({tel_pixel[0]:.1f}, {tel_pixel[1]:.1f})')


if __name__ == '__main__':
    main()
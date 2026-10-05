"""
Main_controller_sat_pid_slim.py
-------------------------------
Point-and-track a satellite from its TLE. Satellite counterpart of
Main_controller_star_pid_slim.py: same structure, same helpers, different
physics. The two files deliberately differ ONLY where the regimes differ:

  star                          satellite
  ----                          ---------
  feedforward ~25 "/s           feedforward 1000-7000 "/s
  P only                        P + I + D (see below)
  secant read once              secant per tick (alt changes 40 deg in a pass)
  0.25 s tick                   0.10 s tick, sleep compensated for loop work
  takeup always +az             takeup arrives in the pass's az direction

Why PID here when the star file is P-only: the TLE's rate error (mostly
along-track) is tens of "/s. P alone turns that into a standing offset of
(rate error)/KP -- easily 60+ arcsec, a quarter of the FOV half-height. The
INTEGRATOR absorbs it. The DERIVATIVE damps the correction when the offset is
changing quickly; KD is small and can be zeroed without harm.

I and D update only when a NEW camera frame arrives (every queue message is a
real detection -- lost-target frames push nothing). Integrating a stale error
at the 10 Hz tick rate would wind up ~10x too fast.

Flow
----
1. Find the next pass that rises through MIN_ALT with enough lead to prep.
2. Idle (mount untouched) until PREP_LEAD_S before the crossing.
3. Corrective slew to the crossing point, arriving in the direction the pass
   will move; plate-solve refinement, parked, same as the star file.
4. Hold. At the crossing time the satellite enters the frame and the loop
   turns on:  cmd_rate = TLE_feedforward + PID(camera offset).

Process split (required on macOS): MAIN owns the ASI585 + OpenCV, CHILD owns
the serial mount + ASI120 star tracker. Mount unaligned: disable_tracking()
once, every command an explicit rate.
"""

import os
import queue
import time
import datetime
import csv
import numpy as np
from multiprocessing import Process, Queue, Event

from astropy.coordinates import (SkyCoord, AltAz, EarthLocation, TEME,
                                 CartesianRepresentation)
from astropy.time import Time
import astropy.units as u
from astropy.io import fits
from scipy.ndimage import center_of_mass, find_objects, label, sum_labels
from scipy.interpolate import CubicSpline
from sgp4.api import Satrec

from OGS_control_methods import OGS_control, forward_telescope, MAX_TUBE_ALT
from camerapicture import takepic
from platesolveAUTO import auto_solve
import nexstar_protocol as protocol

# =================================================================
# Config
# =================================================================
CHICAGO = EarthLocation(lat=41.868, lon=-87.648)
PORT = '/dev/tty.PL2303G-USBtoUART110'
CAL_SOURCE = 'pointing_model_cal.csv'
BORESIGHT_FILE = 'u_tel_st.txt'

# --- Target TLE: paste the exact two lines, or leave None and provide
# target.tle (optional name line + 2 TLE lines) beside this script. ----------
TLE_NAME  = 'STARLINK-5320 (NORAD 55289)'
TLE_LINE1 = '1 55289U 23010W   26220.54595807  .00000244  00000+0  28246-4 0  9995'
TLE_LINE2 = '2 55289  70.0008   1.6573 0002382 272.3873  87.7014 14.98331201196011'
TLE_FILE = 'target.tle'
MAX_TLE_AGE_D = 2.0          # refuse a TLE older than this (prediction error
                             # would exceed the acquisition FOV)

# --- Pass geometry / timing -------------------------------------------------
MIN_ALT = 35.0               # deg; sit-and-wait altitude, where the loop fires
SEARCH_HORIZON_H = 24.0      # hours ahead to search for a pass
PREP_LEAD_S =  200       # start slew + refinement this long before crossing
TRACK_DURATION_S = 240.0     # tracking time after the satellite enters; the
                             # 25400 pass is sunlit from the 30 deg crossing
                             # (03:39:05) until ~03:44, so 240 s uses the arc
SCHEDULE_TSTEP = 0.10        # control interval (s); the sat moves ~0.5 deg/s
SAT_SPLINE_STEP = 0.5        # ephemeris sampling for the spline (s)

# Azimuth approach: back off AZ_TAKEUP and arrive moving in the SAME direction
# the pass will track, so the first tracking commands do not reverse through
# the gear dead zone (~0.13 deg) right at acquisition. Keep >= 1.0 deg (small
# legs are in the lossy regime -- POINTING_INVESTIGATION.md section 5).
AZ_TAKEUP = 1.5              # deg; 0 disables

# --- Tracking-loop altitude limits ------------------------------------------
# The guards in OGS_control_methods protect GOTOS only; the tracking loop sends
# rate commands straight to the mount, so it needs its own. Two independent
# stops:
#   1. EPHEMERIS stop: end the track when the satellite descends back through
#      MIN_ALT -- the same altitude we intercepted it at. Below that it is in
#      the murk, and following it drives the tube toward the horizon for no
#      scientific return. Costs nothing (the spline already knows).
#   2. HARDWARE guard: read the encoders periodically and abort if the model
#      boresight leaves [ALT_GUARD_MIN, ALT_GUARD_MAX]. This is the backstop
#      against a runaway loop -- a zenith crossing snapped a camera cable on
#      2026-08-09, and there is no lower limit anywhere else in the codebase.
STOP_BELOW_MIN_ALT = True    # ephemeris stop at MIN_ALT on the way down
ALT_GUARD_MIN = 12.0         # deg; hardware floor (tube toward the fork/base)
ALT_GUARD_MAX = MAX_TUBE_ALT  # deg; hardware ceiling (85, the cable-break limit)
GUARD_CHECK_S = 1.0          # seconds between encoder guard checks; 0 disables

# --- Plate-solve refinement (identical to the star file) --------------------
CORR_MAX_ITER = 6
CORR_THRESHOLD = 40.0        # arcsec
CORR_BIAS_LIMIT = 12000.0     # arcsec
CORR_MIN_LEFT = 15.0         # s
CAPTURE_CONFIG_S = 0.15

ST_CAMERA_NAME = 'ZWO ASI120MM Mini'
ST_EXPOSURE = 500000
ST_GAIN = 75

# --- Star-tracker recorder (3rd process; "did we barely miss it?") ----------
# The ST field is 2.2 x 1.7 deg vs the tracking camera's 0.23 x 0.13, so if the
# target passed just OUTSIDE the tracking camera it will still be on the ST as
# a streak. Runs in its own process so the 10 Hz control loop is never blocked
# by a 0.2 s exposure. Starts at track start (refinement has closed the ST by
# then) and stops at track end. Files carry a wall-clock timestamp, so a streak
# plus its time gives the TLE time bias directly.
ST_RECORD = True             # False disables the recorder entirely
ST_REC_INTERVAL_S = 1.0      # seconds between ST frames
ST_REC_EXPOSURE = 200000     # us; 0.2 s -> a 1500 "/s target streaks ~48 px
ST_REC_GAIN = 300            # higher than the plate-solve gain (short exposure)
ST_REC_OPEN_SETTLE_S = 2.0   # the ZWO SDK needs a pause between close and open

# --- ST coarse feedback (from the 2026-08-09 03:38 pass frames) -------------
# During a track the SATELLITE is the compact source on the ST (the mount
# moves with it) while stars are ~40 px streaks flowing at the mount rate
# (~220 px/frame). Detection = compact blob whose frame-to-frame motion is
# SLOW (the 03:38 sat moved ~11 px/frame; streak fragments jump ~220).
# The ST pixel->sky mapping was solved from the two star-flow directions
# before/after culmination (same two-vector method as the tracking-camera
# theta): FLIP y, then rotate. sky = R(-ST_THETA) @ [dx, -dy] * ST_ARCSEC_PX.
# The two flows agreed to 11 deg; treat ST_THETA as +/-6 deg, fine for a
# coarse loop whose only job is to drive the target into the ASI585 FOV.
# The child uses ST offsets ONLY when the tracking camera has no fresh lock.
ST_FEEDBACK = True           # False: recorder saves frames but never steers
ST_THETA = 277.5             # deg, measured 2026-08-09 (see above)
ST_ARCSEC_PX = 6.22          # ST plate scale
ST_KP = 0.3                  # [1/s] coarse P gain on ST offsets (no I, no D)
ST_CORR_CLAMP = 800.0        # max ST correction ("/s) -- bounds a wrong sign
ST_MATCH_PX = 120.0          # track gate: candidate must be this close to the
                             # prediction from the previous detection
ST_FRESH_S = 3.0             # ST offset older than this is not used
CAM_FRESH_S = 1.5            # if the ASI585 delivered within this, it wins
ST_FOLDER = 'ST'
IMAGE_BASE_DIR = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/Images'
TEL_PIXEL = (594, 496)       # re-measured 2026-08-09 ~03:55 (post-bump).
                             # Used by BOTH plate-solve refinement and the ST
                             # coarse feedback -- keep in sync with star file.

# --- PID gains (correction added to the feedforward, arcsec/s) --------------
KP = 0.5                     # [1/s]
KI = 0.05                    # [1/s^2]  raised for satellites: any residual
                             # rate error is ~10% of a huge feedforward
                             # (~100 "/s). At 0.01 the integrator needed ~50 s
                             # to absorb that -- most of the pass.
I_CLAMP = 150.0               # max |I contribution| per axis ("/s)
KD = 0.01                    # [-] on the measured offset drift; 0 disables
D_CLAMP = 15.0               # max |D contribution| per axis ("/s)
P_SIGN_AZ = +1.0             # camera-axis signs, verified on sky 2026-07-16
P_SIGN_ALT = -1.0
APPLY_SECANT = True          # divide az correction by cos(alt), per tick

# --- Mount rate-transmission correction (measured 2026-08-09) ---------------
# Three rate_calibration.py runs at two sky regions: the drive delivers LESS
# sky rate than commanded (encoder always exact -> mechanical loss downstream
# of the encoder). Tables are measured (commanded, delivered) pairs, symmetric
# in sign, anchored at 0 and extrapolated to the slew limit. To DELIVER rate d
# we command the interpolated inverse of delivered(commanded). Set
# APPLY_TRANSMISSION = False to send raw rates again.
APPLY_TRANSMISSION = False   # OFF: the table was measured FROM REST, so it
                             # over-boosts sustained motion ~2.5x. On-sky
                             # 2026-08-09 02:09 it cut blind drift 15->7 "/s
                             # but degraded steady state 1.5"->8.3" rms and
                             # drove the integrators negative. Re-derive from
                             # sustained-motion data before switching on.
_AZ_CMD = np.array([0., 10., 25., 50., 100., 300., 1000., 3000., 10800.])
_AZ_DEL = np.array([0., 1.0, 7.5, 17., 36., 135., 900., 2940., 10584.])
_ALT_CMD = np.array([0., 10., 25., 50., 100., 300., 1000., 10800.])
_ALT_DEL = np.array([0., 3.5, 17., 42., 89., 288., 1000., 10800.])


def transmission_correct(rate, deliv, cmd):
    """Commanded axis rate ("/s) that DELIVERS `rate`, per the measured curve."""
    return float(np.sign(rate) * np.interp(abs(rate), deliv, cmd))

# --- Tracking camera + logging ----------------------------------------------
SDK_PATH = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/libASICamera2.dylib.1.41'
CAM_SAVE_DIR = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/Images/Passes'
LOG_DIR = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/Sat_pid_logs'
CAM_EXPOSURE = 30000         # us; raised 20->30 ms under cloud 2026-08-09.
                             # A TRACKED target is a point source, so exposure
                             # buys signal linearly; the cost is smear if the
                             # loop is lagging (30 ms x 0.5 deg/s of POINTING
                             # ERROR = 54" = 250 px, so keep the loop locked).
CAM_GAIN = 550               # raised 450->550. With sky at 13/255 ADU there is
                             # plenty of headroom, and in RAW8 more gain mostly
                             # buys better use of the 8-bit range rather than
                             # true SNR. If the SDK rejects 550, drop to 500.
THETA_MECH  = 108.0          # 2026-08-09 three-star drift fit (-107.9 +/- 0.5
                             # with-flip); the chain has a mirror flip -- valid
                             # ONLY with P_SIGN_ALT = -1
PLATE_SCALE = 0.2177         # drift-measured (0.219 +/- 2% over 3 stars); SCT
                             # focus shifts FL, nominal-FL 0.2136 was 2% off
CAM_WIDTH, CAM_HEIGHT = 3840, 2160

TRACK_WINDOW = 'Satellite PID Tracking   [q = stop]'


# =================================================================
# TLE + ephemeris
# =================================================================

def load_tle():
    """(name, line1, line2) from the config constants or TLE_FILE."""
    if TLE_LINE1 and TLE_LINE2:
        return TLE_NAME, TLE_LINE1.strip(), TLE_LINE2.strip()
    if os.path.exists(TLE_FILE):
        lines = [ln.strip() for ln in open(TLE_FILE) if ln.strip()]
        if len(lines) >= 3 and lines[1].startswith('1 '):
            return lines[0], lines[1], lines[2]
        if len(lines) >= 2 and lines[0].startswith('1 '):
            return TLE_NAME, lines[0], lines[1]
    raise ValueError(f"No TLE: set TLE_LINE1/2 or provide {TLE_FILE}.")


def tle_age_days(satrec):
    """Age of the TLE epoch in days (positive = in the past)."""
    now_jd = 2440587.5 + time.time() / 86400.0
    return now_jd - (satrec.jdsatepoch + satrec.jdsatepochF)


def sat_altaz(satrec, t_unix, location):
    """Topocentric (az, alt) deg for one or many UNIX times.
    sgp4 -> TEME -> astropy AltAz; two-part JD keeps sub-second sampling
    exact. Failed sgp4 steps come back NaN."""
    t_unix = np.atleast_1d(np.asarray(t_unix, dtype=float))
    t = Time(t_unix, format='unix', scale='utc')
    e, r, _ = satrec.sgp4_array(np.ascontiguousarray(t.jd1),
                                np.ascontiguousarray(t.jd2))
    teme = TEME(CartesianRepresentation(r[:, 0] * u.km, r[:, 1] * u.km,
                                        r[:, 2] * u.km), obstime=t)
    aa = teme.transform_to(AltAz(obstime=t, location=location))
    az = np.asarray(aa.az.deg, dtype=float)
    alt = np.asarray(aa.alt.deg, dtype=float)
    az[np.asarray(e) != 0] = np.nan
    alt[np.asarray(e) != 0] = np.nan
    return az, alt


def find_next_rise_crossing(satrec, location, alt_deg, t_now,
                            horizon_h, min_lead_s, coarse_step=15.0):
    """Next time the satellite RISES through alt_deg, at least min_lead_s
    away. Returns (t_cross_unix, az_at_cross, culmination_alt) or None."""
    ts = np.arange(t_now, t_now + horizon_h * 3600.0, coarse_step)
    _, alt = sat_altaz(satrec, ts, location)
    a = np.nan_to_num(alt, nan=-90.0)
    rising = np.where((a[:-1] < alt_deg) & (a[1:] >= alt_deg))[0]
    for i in rising:
        if ts[i + 1] < t_now + min_lead_s:
            continue
        lo, hi = ts[i], ts[i + 1]          # bisect the crossing to ~0.05 s
        while hi - lo > 0.05:
            mid = 0.5 * (lo + hi)
            _, am = sat_altaz(satrec, mid, location)
            lo, hi = (mid, hi) if am[0] < alt_deg else (lo, mid)
        t_cross = 0.5 * (lo + hi)
        az_c, _ = sat_altaz(satrec, t_cross, location)
        j = i + 1
        while j < len(ts) and a[j] >= alt_deg:
            j += 1
        return float(t_cross), float(az_c[0]), float(np.nanmax(alt[i:j + 1]))
    return None


def build_sat_altaz_spline(satrec, t0, t1, location,
                           step=SAT_SPLINE_STEP, margin=2.0):
    """(pos_spline, rate_spline) of the satellite's AltAz over [t0, t1].
    pos_spline(t) -> [az_deg (unwrapped), alt_deg]; rate is deg/s."""
    ts = np.arange(t0 - margin, t1 + margin + step, step)
    az, alt = sat_altaz(satrec, ts, location)
    az = np.rad2deg(np.unwrap(np.deg2rad(az)))
    pos = CubicSpline(ts, np.stack([az, alt], axis=1))
    return pos, pos.derivative()


# =================================================================
# Helpers (identical to the star file)
# =================================================================

def wrap180(deg):
    """Wrap an angular difference in degrees to (-180, 180]."""
    return ((deg + 180.0) % 360.0) - 180.0


MAX_BLOB_PX = 60             # identity gate: during a track, stars streak
                             # ~rate*exposure (>100 px at 0.3 deg/s, 20 ms);
                             # the tracked satellite is the only COMPACT source
MIN_BLOB_PX = 4              # reject HOT PIXELS. At 2800 mm a real point
                             # source spreads over several pixels; a 1-2 px
                             # spike is sensor noise. On an empty (clouded)
                             # frame a hot pixel is the brightest thing there
                             # is, and the loop will happily track it
                             # (observed on-sky 2026-08-09 03:20).

def find_brightest(image):
    """Intensity-weighted centroid (cx, cy) of the brightest compact source,
    or None.

    Two size gates, both necessary:
      MIN_BLOB_PX rejects hot pixels (too small to be optical),
      MAX_BLOB_PX rejects star streaks (too long to be the tracked target).
    """
    labeled, n = label(image > np.median(image) + 5 * np.std(image))
    if n == 0:
        return None
    idx = np.arange(1, n + 1)
    fluxes = sum_labels(image, labeled, index=idx)
    sizes = np.bincount(labeled.ravel())[1:]
    boxes = find_objects(labeled)
    ok = [i for i in idx
          if sizes[i - 1] >= MIN_BLOB_PX
          and max(boxes[i - 1][0].stop - boxes[i - 1][0].start,
                  boxes[i - 1][1].stop - boxes[i - 1][1].start) <= MAX_BLOB_PX]
    if not ok:
        return None
    best = ok[int(np.argmax(fluxes[np.array(ok) - 1]))]
    cy, cx = center_of_mass(image, labels=labeled, index=best)
    return cx, cy


def approach(controller, az, alt, az_dir=+1.0):
    """Corrective goto to (az, alt), FINAL LEG moving in the az_dir direction
    (+1 = increasing az). Pass the track's az direction so the gear stays
    loaded the way the pass will push it."""
    if AZ_TAKEUP > 0:
        controller.go_to_azm_alt_corrective((az - az_dir * AZ_TAKEUP) % 360,
                                            alt)
    return controller.go_to_azm_alt_corrective(az % 360, alt)


def timed_takepic(exposure, gain, name, camera, folder):
    """takepic() plus the astropy Time of the exposure midpoint."""
    t_start = Time.now()
    path = takepic(exposure, gain, name, camera, folder, IMAGE_BASE_DIR,
                   talk=False)
    return path, t_start + (CAPTURE_CONFIG_S + exposure / 2e6) * u.second


def connect_st_camera():
    """Open the ASI120 star tracker in this process, or None."""
    import zwoasi as asi
    try:
        asi.init(SDK_PATH)
    except Exception:
        pass
    cams = asi.list_cameras()
    cid = next((i for i, n in enumerate(cams) if n == ST_CAMERA_NAME), None)
    if cid is None:
        print(f"[REFINE] '{ST_CAMERA_NAME}' not found; skipping refinement.")
        return None
    return asi.Camera(cid)


def refine_pointing(controller, st_camera, P_az, P_alt, t_deadline,
                    az_dir=+1.0):
    """Drive the telescope boresight onto the fixed sky point (P_az, P_alt)
    with plate solves, mount PARKED between slews. Same loop as the star file.
    Returns (iterations, final_sep_arcsec, bias_tuple, converged)."""
    cal, u_tel_st = controller.cal, controller.u_tel_st
    sep, bias = float('nan'), (0.0, 0.0)
    for it in range(1, CORR_MAX_ITER + 1):
        if time.time() > t_deadline - CORR_MIN_LEFT:
            print(f"  [refine] out of time before iter {it}; holding.")
            return it - 1, sep, bias, False

        path, t_exp = timed_takepic(ST_EXPOSURE, ST_GAIN,
                                    f'satpid_corr_{it}', st_camera, ST_FOLDER)
        enc_az, enc_alt = controller.get_raw_azm_alt()
        if enc_az is None:
            print("  [refine] encoder read failed.")
            return it, sep, bias, False

        sol = auto_solve(path, tel_pixel=TEL_PIXEL)
        if sol is None or 'tel_ra' not in sol:
            print(f"  [refine iter {it}] plate solve failed -- re-imaging.")
            continue

        act = SkyCoord(sol['tel_ra'] * u.deg, sol['tel_dec'] * u.deg,
                       frame='icrs').transform_to(
                           AltAz(obstime=t_exp, location=CHICAGO))
        pred_az, pred_alt = forward_telescope(
            enc_az, enc_alt, cal['q_mnt_enu'], cal['q_st_gim'],
            cal['theta_np'], cal['a_d'], u_tel_st)
        bias = (wrap180(act.az.deg - pred_az), act.alt.deg - pred_alt)

        e_az, e_alt = wrap180(P_az - act.az.deg), P_alt - act.alt.deg
        sep = np.hypot(e_az * np.cos(np.radians(P_alt)), e_alt) * 3600
        print(f"  [refine iter {it}] sep={sep:8.1f}\"  "
              f"bias=({bias[0]*3600:+.0f}\", {bias[1]*3600:+.0f}\")")

        if sep < CORR_THRESHOLD:
            return it, sep, bias, True
        if max(abs(bias[0]), abs(bias[1])) * 3600 > CORR_BIAS_LIMIT:
            print(f"  [refine] ABORT: |bias| > {CORR_BIAS_LIMIT:.0f}\".")
            return it, sep, bias, False

        approach(controller, (P_az - bias[0]) % 360, P_alt - bias[1], az_dir)

    print(f"  [refine] no convergence in {CORR_MAX_ITER} iters "
          f"(sep = {sep:.1f}\").")
    return CORR_MAX_ITER, sep, bias, False


# =================================================================
# Child process: slew + refine + hold + track (owns the serial mount)
# =================================================================

def slew_and_track_main(offset_queue, tle_l1, tle_l2,
                        t_track_start, t_track_end, run_id, abort_event,
                        st_queue=None):
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"sat_pid_log_{run_id}.csv")

    satrec = Satrec.twoline2rv(tle_l1, tle_l2)
    controller = OGS_control(port=PORT, location=CHICAGO,
                             file_start_time=time.time())
    controller.fit_pointing_model(CAL_SOURCE)
    controller.u_tel_st = np.loadtxt(BORESIGHT_FILE)
    controller.disable_tracking()

    pos_spline, rate_spline = build_sat_altaz_spline(
        satrec, t_track_start, t_track_end, CHICAGO)

    # Direction the pass moves in azimuth at track start: the approach and
    # every refinement re-slew arrive moving this way (gear pre-loaded for
    # the track; no dead-zone reversal at acquisition).
    az_dir = 1.0 if rate_spline(t_track_start)[0] >= 0 else -1.0

    tgt = pos_spline(t_track_start)
    tgt_az, tgt_alt = float(tgt[0]) % 360.0, float(tgt[1])
    print(f"[SLEW] -> crossing point  Az={tgt_az:.3f} Alt={tgt_alt:+.3f}  "
          f"(arriving in {'+' if az_dir > 0 else '-'}az, matching the pass)")
    approach(controller, tgt_az, tgt_alt, az_dir)

    if CORR_MAX_ITER > 0:
        st_camera = connect_st_camera()
        if st_camera is not None:
            iters, sep, bias, ok = refine_pointing(
                controller, st_camera, tgt_az, tgt_alt, t_track_start, az_dir)
            print(f"[REFINE] {iters} iter(s), sep {sep:.0f}\", ok={ok}.")
            try:
                st_camera.close()
            except Exception:
                pass

    # --- Hold parked until the crossing time ---
    print("[SLEW] Holding until the satellite arrives.")
    while not abort_event.is_set() and time.time() < t_track_start:
        time.sleep(0.05)
    print(f"[TRACK] ON.  rate = feedforward + PID  "
          f"(Kp={KP} Ki={KI} Kd={KD})")

    # --- PID state. I and D advance only on fresh camera frames. ---
    cam_offset = np.array([0.0, 0.0])   # latest [daz, dalt] arcsec
    cam_t = None                        # its arrival time
    i_az = i_alt = 0.0                  # integrator ("/s)
    vel = np.array([0.0, 0.0])          # offset drift between frames ("/s)
    st_offset = np.array([0.0, 0.0])    # coarse offset from the star tracker
    st_t = None
    log_rows = []
    guard_t = 0.0                       # last encoder guard check
    stop_reason = 'duration'
    try:
        while time.time() < t_track_end and not abort_event.is_set():
            tick = time.time()

            # STOP 1 (ephemeris): the satellite has set back through MIN_ALT.
            sat_alt = float(pos_spline(np.clip(tick, t_track_start - 2,
                                               t_track_end + 2))[1])
            if STOP_BELOW_MIN_ALT and sat_alt < MIN_ALT:
                stop_reason = f'satellite descended below {MIN_ALT:.0f} deg'
                print(f"\n[TRACK] {stop_reason} -- stopping.")
                break

            # STOP 2 (hardware): where is the tube actually pointing?
            if GUARD_CHECK_S > 0 and tick - guard_t >= GUARD_CHECK_S:
                guard_t = tick
                g_az, g_alt = controller.get_raw_azm_alt()
                if g_az is not None and controller.cal is not None:
                    _, tube_alt = forward_telescope(
                        g_az, g_alt, controller.cal['q_mnt_enu'],
                        controller.cal['q_st_gim'], controller.cal['theta_np'],
                        controller.cal['a_d'], controller.u_tel_st)
                    if not (ALT_GUARD_MIN <= tube_alt <= ALT_GUARD_MAX):
                        stop_reason = (f'ALT GUARD: tube at {tube_alt:.1f} deg '
                                       f'(band {ALT_GUARD_MIN:.0f}-'
                                       f'{ALT_GUARD_MAX:.0f})')
                        print(f"\n[TRACK] {stop_reason} -- ABORTING.")
                        break

            # Drain the queue; every message is a real detection.
            try:
                while True:
                    msg = offset_queue.get_nowait()
                    new = np.array([msg['daz'], msg['dalt']])
                    if cam_t is not None and 0.02 < msg['t'] - cam_t < 2.0:
                        dt = msg['t'] - cam_t
                        vel = (new - cam_offset) / dt
                        i_az = float(np.clip(i_az + KI * new[0] * dt,
                                             -I_CLAMP, I_CLAMP))
                        i_alt = float(np.clip(i_alt + KI * new[1] * dt,
                                              -I_CLAMP, I_CLAMP))
                    cam_offset, cam_t = new, msg['t']
            except queue.Empty:
                pass

            # Drain the ST coarse queue (satellite offset seen by the wide
            # star tracker; pushed only after the track gate accepts it).
            if st_queue is not None:
                try:
                    while True:
                        msg = st_queue.get_nowait()
                        st_offset = np.array([msg['daz'], msg['dalt']])
                        st_t = msg['t']
                except queue.Empty:
                    pass

            # Feedforward from the ephemeris spline, at this instant.
            now = np.clip(tick, t_track_start - 2, t_track_end + 2)
            r = rate_spline(now)
            ff_az, ff_alt = 3600.0 * float(r[0]), 3600.0 * float(r[1])

            # Secant per tick: alt sweeps tens of degrees during a pass.
            cos_alt = max(np.cos(np.radians(float(pos_spline(now)[1]))),
                          0.05) if APPLY_SECANT else 1.0

            # COARSE/FINE arbitration: the ASI585 (fine) wins whenever it has
            # a fresh lock; otherwise a fresh ST detection steers coarsely
            # (P-only, clamped) to drive the target INTO the fine FOV.
            cam_fresh = cam_t is not None and tick - cam_t < CAM_FRESH_S
            st_fresh = st_t is not None and tick - st_t < ST_FRESH_S
            src = 'CAM'
            if cam_fresh or not st_fresh:
                d_az = float(np.clip(KD * vel[0], -D_CLAMP, D_CLAMP))
                d_alt = float(np.clip(KD * vel[1], -D_CLAMP, D_CLAMP))
                corr_az = (P_SIGN_AZ * (KP * cam_offset[0] + i_az + d_az)
                           / cos_alt)
                corr_alt = P_SIGN_ALT * (KP * cam_offset[1] + i_alt + d_alt)
            else:
                src = 'ST '
                d_az = d_alt = 0.0
                corr_az = float(np.clip(ST_KP * st_offset[0],
                                        -ST_CORR_CLAMP, ST_CORR_CLAMP)) / cos_alt
                corr_alt = float(np.clip(ST_KP * st_offset[1],
                                         -ST_CORR_CLAMP, ST_CORR_CLAMP))

            # Desired DELIVERED rates, then invert the measured transmission
            # so the mount actually delivers them.
            des_az = ff_az + corr_az
            des_alt = ff_alt + corr_alt
            if APPLY_TRANSMISSION:
                az_r = transmission_correct(des_az, _AZ_DEL, _AZ_CMD)
                al_r = transmission_correct(des_alt, _ALT_DEL, _ALT_CMD)
            else:
                az_r, al_r = des_az, des_alt
            az_r = float(np.clip(az_r, -protocol.max_slew_rate,
                                 protocol.max_slew_rate))
            al_r = float(np.clip(al_r, -protocol.max_slew_rate,
                                 protocol.max_slew_rate))
            controller.nexstar_command_and_read_until(
                protocol.slewAZM_var(az_r), b"#")
            controller.nexstar_command_and_read_until(
                protocol.slewALT_var(al_r), b"#")

            t_plus = tick - t_track_start
            print(f"  T+{t_plus:5.1f}s [{src}] ff=({ff_az:+8.1f},{ff_alt:+8.1f})\"/s "
                  f"cam=({cam_offset[0]:+6.1f},{cam_offset[1]:+6.1f})\"  "
                  f"st=({st_offset[0]:+7.0f},{st_offset[1]:+7.0f})\"  "
                  f"cmd=({az_r:+8.1f},{al_r:+8.1f})\"/s")
            log_rows.append({
                't_plus': f"{t_plus:.3f}", 't_wall': f"{tick:.3f}",
                'src': src.strip(),
                'ff_az': f"{ff_az:.3f}", 'ff_alt': f"{ff_alt:.3f}",
                'cam_daz': f"{cam_offset[0]:.3f}",
                'cam_dalt': f"{cam_offset[1]:.3f}",
                'st_daz': f"{st_offset[0]:.1f}", 'st_dalt': f"{st_offset[1]:.1f}",
                'i_az': f"{i_az:.3f}", 'i_alt': f"{i_alt:.3f}",
                'd_az': f"{d_az:.3f}", 'd_alt': f"{d_alt:.3f}",
                'des_az': f"{des_az:.3f}", 'des_alt': f"{des_alt:.3f}",
                'cmd_az': f"{az_r:.3f}", 'cmd_alt': f"{al_r:.3f}",
            })

            # Sleep whatever is left of the tick after the serial round trips.
            time.sleep(max(0.0, SCHEDULE_TSTEP - (time.time() - tick)))
    finally:
        controller.stop()
        if log_rows:
            with open(log_path, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(log_rows[0].keys()))
                w.writeheader()
                w.writerows(log_rows)
            print(f"[TRACK] Logged {len(log_rows)} steps to {log_path}.")
        print(f"[TRACK] Done ({stop_reason}). Mount stopped.")


# =================================================================
# Third process: star-tracker recorder (wide-field "near miss" camera)
# =================================================================

def find_compact_st(image):
    """Compact-source candidates on an ST frame: [(cx, cy, flux), ...].

    Compact = >=5 px, <=14 px across, aspect <=2. Star streaks (40+ px, thin)
    and hot pixels (1-2 px) both fail; broken streak FRAGMENTS can pass, which
    is why the caller also applies the slow-motion track gate.
    """
    med = np.median(image)
    sd = max(float(np.std(image[:200, :200])), 0.5)
    lab, n = label(image > med + 5 * sd)
    if n == 0:
        return []
    idx = np.arange(1, n + 1)
    sizes = np.bincount(lab.ravel())[1:]
    fluxes = sum_labels(image - med, lab, index=idx)
    boxes = find_objects(lab)
    out = []
    for i in idx:
        s = sizes[i - 1]
        if s < 5:
            continue
        h = boxes[i - 1][0].stop - boxes[i - 1][0].start
        w = boxes[i - 1][1].stop - boxes[i - 1][1].start
        if max(h, w) > 14 or max(h, w) / max(min(h, w), 1) > 2.0:
            continue
        cy, cx = center_of_mass(image, labels=lab, index=i)
        out.append((float(cx), float(cy), float(fluxes[i - 1])))
    return out


def st_pixel_to_sky(dx, dy):
    """ST pixel offset from TEL_PIXEL -> (daz_greatcircle, dalt) arcsec.

    Measured mapping (2026-08-09): flip y, rotate by -ST_THETA, scale.
    """
    th = np.radians(ST_THETA)
    vx, vy = dx, -dy
    daz = (np.cos(th) * vx + np.sin(th) * vy) * ST_ARCSEC_PX
    dalt = (-np.sin(th) * vx + np.cos(th) * vy) * ST_ARCSEC_PX
    return float(daz), float(dalt)


def st_recorder_main(t_track_start, t_track_end, run_id, abort_event,
                     st_queue=None):
    """Save one ST frame per ST_REC_INTERVAL_S for the whole track, and (if
    ST_FEEDBACK) push coarse sky offsets of the detected satellite.

    Detection is two-stage: find_compact_st() proposes candidates, then a
    track gate accepts only sources moving SLOWLY frame-to-frame (the
    satellite signature during a track). Two consecutive consistent sightings
    are required before the first push, so a lone streak fragment can never
    steer the mount. Every failure is swallowed: worst case this degrades to
    the passive recorder.
    """
    try:
        # Wait for the track to begin: refinement owns the ST until then.
        while not abort_event.is_set() and time.time() < t_track_start:
            time.sleep(0.2)
        if abort_event.is_set():
            return
        time.sleep(ST_REC_OPEN_SETTLE_S)   # SDK dislikes close-then-reopen

        import zwoasi as asi
        try:
            asi.init(SDK_PATH)
        except Exception:
            pass
        cams = asi.list_cameras()
        cid = next((i for i, n in enumerate(cams) if n == ST_CAMERA_NAME), None)
        if cid is None:
            print(f"[ST-REC] '{ST_CAMERA_NAME}' not available; no recording.")
            return
        camera = asi.Camera(cid)
        print(f"[ST-REC] recording every {ST_REC_INTERVAL_S:.1f} s "
              f"({ST_REC_EXPOSURE/1000:.0f} ms, gain {ST_REC_GAIN})")

        i = 0
        last = None                 # (t, cx, cy, vx, vy) of accepted track
        pending = None              # first sighting awaiting confirmation
        n_push = 0
        try:
            while time.time() < t_track_end and not abort_event.is_set():
                t0 = time.time()
                stamp = datetime.datetime.fromtimestamp(t0)
                try:
                    path = takepic(ST_REC_EXPOSURE, ST_REC_GAIN,
                                   f"strec_{run_id}_{i:04d}_{stamp:%H%M%S_%f}",
                                   camera, ST_FOLDER, IMAGE_BASE_DIR,
                                   talk=False)
                    i += 1
                except Exception as e:
                    print(f"[ST-REC] frame {i} failed: {e}")
                    path = None

                if path and ST_FEEDBACK and st_queue is not None:
                    try:
                        t_mid = t0 + CAPTURE_CONFIG_S + ST_REC_EXPOSURE / 2e6
                        img = fits.getdata(path).astype(float)
                        cands = find_compact_st(img)
                        got = None
                        if last is not None:
                            # predict from the running track, gate on distance
                            tl, lx, ly, vx, vy = last
                            dt = t_mid - tl
                            px, py = lx + vx * dt, ly + vy * dt
                            close = [c for c in cands
                                     if np.hypot(c[0]-px, c[1]-py) < ST_MATCH_PX]
                            if close:
                                got = max(close, key=lambda c: c[2])
                                vx = (got[0]-lx)/dt if dt > 0 else vx
                                vy = (got[1]-ly)/dt if dt > 0 else vy
                                last = (t_mid, got[0], got[1], vx, vy)
                            else:
                                if t_mid - tl > 4.0:
                                    last = None     # track lost
                        if last is None and got is None and cands:
                            # bootstrap: need two consecutive SLOW sightings
                            best = max(cands, key=lambda c: c[2])
                            if pending is not None:
                                pt, bx, by = pending
                                dt = t_mid - pt
                                # 8 s window: under cloud, sightings can be
                                # sparse (the 03:38 sat showed at 7 s spacing)
                                if 0 < dt < 8.0 and np.hypot(
                                        best[0]-bx, best[1]-by) < ST_MATCH_PX:
                                    vx, vy = (best[0]-bx)/dt, (best[1]-by)/dt
                                    last = (t_mid, best[0], best[1], vx, vy)
                                    got = best
                                    print(f"[ST-REC] TARGET ACQUIRED at "
                                          f"({best[0]:.0f},{best[1]:.0f}) px")
                            pending = (t_mid, best[0], best[1])
                        if got is not None:
                            daz, dalt = st_pixel_to_sky(got[0]-TEL_PIXEL[0],
                                                        got[1]-TEL_PIXEL[1])
                            st_queue.put({'t': t_mid, 'daz': daz,
                                          'dalt': dalt})
                            n_push += 1
                    except Exception as e:
                        print(f"[ST-REC] detect failed: {e}")

                time.sleep(max(0.0, ST_REC_INTERVAL_S - (time.time() - t0)))
        finally:
            try:
                camera.close()
            except Exception:
                pass
            print(f"[ST-REC] {i} frames saved to {ST_FOLDER}/ "
                  f"(prefix strec_{run_id}_), {n_push} offsets pushed")
    except Exception as e:
        print(f"[ST-REC] recorder aborted: {e}")


# =================================================================
# Main process: tracking camera + display (owns the ASI585 + OpenCV)
# =================================================================

def camera_track_display(offset_queue, frame_dir, t_track_start, t_track_end,
                         abort_event):
    """Capture frames, push centroid offsets, show a live view. 'q' aborts.
    A frame with no detection pushes NOTHING: the child keeps correcting on
    the last real measurement (graceful coasting through dropouts)."""
    import zwoasi as asi
    import cv2

    cam_id = next((i for i, n in enumerate(asi.list_cameras())
                   if 'ASI585MM' in n), None)
    if cam_id is None:
        print("[CAM] ASI585MM Pro not found!")
        abort_event.set()
        return
    camera = asi.Camera(cam_id)
    try:
        camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD,
                                 camera.get_controls()['BandWidth']['MinValue'])
    except Exception:
        pass
    camera.set_control_value(asi.ASI_EXPOSURE, CAM_EXPOSURE)
    camera.set_control_value(asi.ASI_GAIN, CAM_GAIN)
    camera.set_image_type(asi.ASI_IMG_RAW8)

    th = np.radians(THETA_MECH)
    cos_t, sin_t = np.cos(th), np.sin(th)
    scale = 900 / max(CAM_WIDTH, CAM_HEIGHT)
    disp_w, disp_h = int(CAM_WIDTH * scale), int(CAM_HEIGHT * scale)
    cv2.namedWindow(TRACK_WINDOW)

    frame_idx = 0
    try:
        while time.time() < t_track_end + 1.0 and not abort_event.is_set():
            try:
                frame = camera.capture()
            except Exception as e:
                print(f"[CAM] Error: {e}")
                continue

            # Centroid -> push FIRST (control latency), save the frame after.
            # Push only during the track: before it, any detection is a field
            # star, and acting on it at PID-on would slew at a star.
            c = find_brightest(frame)
            if c is not None:
                cx, cy = c                       # always unpack (display uses it)
                if time.time() >= t_track_start:
                    dx, dy = cx - CAM_WIDTH / 2, cy - CAM_HEIGHT / 2
                    offset_queue.put({
                        't': time.time(),
                        'daz': (dx * cos_t + dy * sin_t) * PLATE_SCALE,
                        'dalt': (-dx * sin_t + dy * cos_t) * PLATE_SCALE})
            fits.writeto(os.path.join(
                frame_dir, f"satpid_frame_{frame_idx:04d}.fits"),
                frame, overwrite=True)
            frame_idx += 1

            disp = cv2.cvtColor(cv2.resize(frame, (disp_w, disp_h)),
                                cv2.COLOR_GRAY2BGR)
            cv2.drawMarker(disp, (disp_w // 2, disp_h // 2), (0, 255, 0),
                           cv2.MARKER_CROSS, 34, 1)
            if c is not None:
                cv2.circle(disp, (int(cx * scale), int(cy * scale)),
                           18, (0, 180, 255), 2)
            phase = ("HOLD" if time.time() < t_track_start else
                     "TRACKING" if time.time() <= t_track_end else "DONE")
            cv2.putText(disp, phase, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 0), 2)
            cv2.imshow(TRACK_WINDOW, disp)
            if (cv2.waitKey(1) & 0xFF) == ord('q'):
                abort_event.set()
    finally:
        try:
            camera.close()
        except Exception:
            pass
        cv2.destroyAllWindows()
        for _ in range(5):
            cv2.waitKey(1)


# =================================================================
# Entry point
# =================================================================

def main():
    os.makedirs(CAM_SAVE_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    name, l1, l2 = load_tle()
    satrec = Satrec.twoline2rv(l1, l2)
    age = tle_age_days(satrec)
    print(f"Target: {name}  (NORAD {satrec.satnum}, TLE age {age:.1f} d)")
    if age > MAX_TLE_AGE_D:
        print(f"REFUSING: TLE is {age:.1f} days old (limit {MAX_TLE_AGE_D}). "
              f"A stale TLE misses the acquisition FOV. Get a fresh one.")
        return

    t_now = time.time()
    hit = find_next_rise_crossing(satrec, CHICAGO, MIN_ALT, t_now,
                                  SEARCH_HORIZON_H, PREP_LEAD_S)
    if hit is None:
        print(f"No pass above {MIN_ALT:.0f} deg with {PREP_LEAD_S:.0f} s lead "
              f"in the next {SEARCH_HORIZON_H:.0f} h.")
        return
    t_cross, az_cross, culm = hit
    when = datetime.datetime.fromtimestamp(t_cross).strftime('%H:%M:%S')
    print(f"Next pass: rises through {MIN_ALT:.0f} deg at {when} local "
          f"(in {(t_cross - t_now)/60:.1f} min), Az={az_cross:.1f}, "
          f"culminates ~{culm:.0f} deg.")

    # Idle (mount untouched) until the prep window opens.
    try:
        while time.time() < t_cross - PREP_LEAD_S:
            left = t_cross - PREP_LEAD_S - time.time()
            print(f"  idle until prep: T-{left/60:6.1f} min", end='\r')
            time.sleep(min(5.0, max(left, 0.1)))
    except KeyboardInterrupt:
        print("\nCancelled during idle wait.")
        return
    print(f"\n[MAIN] Prep window open (T-{PREP_LEAD_S:.0f}s).")

    import zwoasi as asi
    try:
        asi.init(SDK_PATH)
    except Exception as ex:
        print(f"[MAIN] ASI init note: {ex}")

    t_track_start, t_track_end = t_cross, t_cross + TRACK_DURATION_S
    run_id = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    frame_dir = os.path.join(CAM_SAVE_DIR, f"satpid_{run_id}")
    os.makedirs(frame_dir, exist_ok=True)

    offset_queue = Queue()
    st_queue = Queue()
    abort_event = Event()
    p = Process(target=slew_and_track_main,
                args=(offset_queue, l1, l2,
                      t_track_start, t_track_end, run_id, abort_event,
                      st_queue))
    p.start()
    rec = None
    if ST_RECORD:
        rec = Process(target=st_recorder_main,
                      args=(t_track_start, t_track_end, run_id, abort_event,
                            st_queue))
        rec.start()
    try:
        camera_track_display(offset_queue, frame_dir,
                             t_track_start, t_track_end, abort_event)
    except KeyboardInterrupt:
        print("\nInterrupted. Stopping...")
    finally:
        abort_event.set()
        for proc in (p, rec):
            if proc is None:
                continue
            proc.join(timeout=8)
            if proc.is_alive():
                proc.terminate()
                proc.join()
        print("All processes stopped.")


if __name__ == '__main__':
    main()

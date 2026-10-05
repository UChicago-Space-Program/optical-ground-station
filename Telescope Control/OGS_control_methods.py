# OGS_control_methods_riesing.py
# ------------------------------------------------------------------
# Telescope control using the quaternion/rotation-chain pointing model
# from Riesing (2018) Ch. 3.
#
# Setup (identical to PorTeL):
#   - CPC1100 alt-az mount with incremental encoders
#   - Off-axis star tracker plate-solving in J2K (RA/Dec)
#   - Telescope boresight at a known offset from the star tracker
#
# Rotation chain (Riesing Eq. 3.1, section 3.3):
#
#   J2K --> ENU --> MNT --> GIM --> ST --> OBS
#       astropy  q_ME    gim(psi,alpha,theta_NP)  q_SG   vd(a_d)
#
# Eight calibration DOF:
#   q_mnt_enu  (3)  mount base orientation in local horizontal
#   theta_np   (1)  gimbal axis non-perpendicularity
#   q_st_gim   (3)  star tracker orientation relative to gimbals
#   a_d        (1)  vertical deflection coefficient
#
# Plus one pre-measured vector:
#   u_tel_st   (2)  telescope boresight direction in ST frame
#                   (from the inter-camera alignment, Riesing section 5.1.1)
# ------------------------------------------------------------------

import os, serial, time, csv
import numpy as np
from scipy.spatial.transform import Rotation as R
from scipy.optimize import least_squares, minimize
from astropy.time import Time
from astropy.coordinates import SkyCoord, AltAz, EarthLocation
import astropy.units as u
import nexstar_protocol as protocol


# =====================================================================
# Rotation helpers  (scipy Rotation: scalar-last [x,y,z,w])
# =====================================================================

def _qmul(r1, r2):
    return r1 * r2

def _qinv(r):
    return r.inv()

def _dcm(r):
    return r.as_matrix()

def _from_dcm(m):
    return R.from_matrix(m)

def _rot_x(rad):
    return R.from_rotvec([rad, 0, 0])

def _rot_y(rad):
    return R.from_rotvec([0, rad, 0])

def _rot_z(rad):
    return R.from_rotvec([0, 0, rad])


# =====================================================================
# Coordinate helpers
# =====================================================================

def altaz_to_enu(az_deg, alt_deg):
    """AltAz (degrees) -> unit vector in ENU  [X=E, Y=N, Z=Up]."""
    az  = np.radians(az_deg)
    alt = np.radians(alt_deg)
    return np.array([np.cos(alt)*np.sin(az),
                     np.cos(alt)*np.cos(az),
                     np.sin(alt)])

def enu_to_altaz(v):
    """Unit vector in ENU -> (az_deg, alt_deg)."""
    alt = np.degrees(np.arcsin(np.clip(v[2], -1, 1)))
    az  = np.degrees(np.arctan2(v[0], v[1])) % 360
    return az, alt

def j2k_to_enu(ra_deg, dec_deg, obstime, location):
    """Convert J2K RA/Dec to an ENU unit vector via astropy."""
    sc = SkyCoord(ra=ra_deg*u.deg, dec=dec_deg*u.deg, frame='icrs')
    aa = sc.transform_to(AltAz(obstime=obstime, location=location))
    return altaz_to_enu(aa.az.deg, aa.alt.deg)


def apply_vertical_deflection(u_enu, a_d, invert=False):
    """
    Vertical deflection as a proper SO(3) rotation (replaces the old
    scalar 'alt -= a_d*cos(alt)' offset).

    The deflection tips the line of sight within the local vertical plane:
    it changes altitude while leaving azimuth fixed.  A rotation of
    magnitude a_d*cos(alt) about the horizontal cross-elevation axis
    [cos(az), -sin(az), 0] does exactly that -- that axis is the normal of
    the pointing's own vertical plane, so rotating about it moves the
    vector purely in altitude.  This is numerically identical to the old
    additive form but keeps the entire pointing chain in SO(3).

    a_d is in degrees.  invert=False lowers the apparent altitude (forward
    model); invert=True raises it (used by the pointing inverse).
    """
    az, alt = enu_to_altaz(u_enu)
    delta = np.radians(a_d * np.cos(np.radians(alt)))          # deflection angle (rad)
    axis  = np.array([np.cos(np.radians(az)), -np.sin(np.radians(az)), 0.0])
    sign  = +1.0 if invert else -1.0                           # +raise (inverse) / -lower (forward)
    return R.from_rotvec(sign * delta * axis).apply(u_enu)


# =====================================================================
# GIM <- MNT rotation   (Riesing Eq. 3.2-3.5)
# =====================================================================

def gim_q_mnt(psi, alpha, theta_np):
    """
    MNT -> GIM rotation from encoder azimuth psi, altitude alpha (radians),
    and non-perpendicularity angle theta_NP (radians).

    Eq. 3.2:  q_NP (x) q_alt (x) q_NP^-1 (x) q_azi
    q_azi  = rotation about Y by psi     (Eq. 3.3)
    q_alt  = rotation about X by alpha   (Eq. 3.4)
    q_NP   = rotation about Z by -theta_NP (Eq. 3.5)
    """
    q_azi = _rot_y(psi)
    q_alt = _rot_x(alpha)
    q_np  = _rot_z(-theta_np)
    return _qmul(_qmul(_qmul(q_np, q_alt), _qinv(q_np)), q_azi)


# =====================================================================
# Forward model
# =====================================================================

def forward_star_tracker(enc_az, enc_alt, q_mnt_enu, q_st_gim, theta_np, a_d):
    """
    Given encoder readings, predict where the STAR TRACKER boresight
    points in the sky.  Returns (az_deg, alt_deg).

    Chain:  [0,0,1]_ST  ->  GIM  ->  MNT  ->  ENU   (+vert. deflection)
    """
    u_st_boresight = np.array([0.0, 0.0, 1.0])

    R_sg  = _dcm(q_st_gim)          # ST <- GIM
    R_gm  = _dcm(gim_q_mnt(np.radians(enc_az), np.radians(enc_alt), theta_np))
    R_me  = _dcm(q_mnt_enu)         # MNT <- ENU

    u_gim = R_sg.T @ u_st_boresight
    u_mnt = R_gm.T @ u_gim
    u_enu = R_me.T @ u_mnt

    # Vertical deflection as a proper SO(3) rotation (was alt -= a_d*cos(alt))
    u_enu = apply_vertical_deflection(u_enu, a_d)
    return enu_to_altaz(u_enu)


def forward_telescope(enc_az, enc_alt, q_mnt_enu, q_st_gim, theta_np, a_d,
                      u_tel_st):
    """
    Given encoder readings, predict where the TELESCOPE boresight
    points in the sky.  Returns (az_deg, alt_deg).

    Same chain as forward_star_tracker but starts from u_tel_st
    (the telescope boresight direction in the ST frame) instead of
    [0,0,1].
    """
    R_sg  = _dcm(q_st_gim)
    R_gm  = _dcm(gim_q_mnt(np.radians(enc_az), np.radians(enc_alt), theta_np))
    R_me  = _dcm(q_mnt_enu)

    u_gim = R_sg.T @ u_tel_st
    u_mnt = R_gm.T @ u_gim
    u_enu = R_me.T @ u_mnt

    # Vertical deflection as a proper SO(3) rotation (was alt -= a_d*cos(alt))
    u_enu = apply_vertical_deflection(u_enu, a_d)
    return enu_to_altaz(u_enu)


# =====================================================================
# Inverse model -- point the TELESCOPE at a target  (Riesing section 3.5)
# =====================================================================

# Hard tube-altitude ceiling. The inverse solve, the goto target check, and
# the in-slew guard all enforce it: the tube must NEVER cross zenith (a
# zenith-crossing slew snapped a tracking-camera cable, 2026-08-09). Slews to
# the far side of the sky go around in azimuth, never over the top.
MAX_TUBE_ALT = 85.0


def inverse_telescope(target_az, target_alt, q_mnt_enu, q_st_gim,
                      theta_np, a_d, u_tel_st, alpha_max_deg=None):
    """
    Find encoder angles (psi, alpha) that point the TELESCOPE boresight
    at a sky target given in AltAz.

    alpha_max_deg: ceiling on the returned encoder altitude. Callers with
    serial access (see _compute_encoder_target) pass the MEASURED ceiling
    (encoder zero offset + MAX_TUBE_ALT) so the constraint is physical
    despite the arbitrary power-up zero of the incremental encoders.
    Defaults to +90 encoder degrees when unknown.

    The problem (Riesing Eq. 3.34-3.35): find psi, alpha such that
        R_GIM_MNT(psi,alpha,theta_NP) . b  =  u_tel_gim
    where
        b         = R_MNT_ENU . u_target_ENU      (target in MNT frame)
        u_tel_gim = R_ST_GIM^T . u_tel_st          (tel boresight in GIM)

    Returns (enc_az_deg, enc_alt_deg).
    """
    # Un-apply the vertical deflection: invert the forward SO(3) deflection
    # so we solve the pure-rotation chain for the pre-deflection direction
    # (was corr_alt = target_alt + a_d*cos(alt), i.e. point slightly higher)
    u_target_enu = altaz_to_enu(target_az, target_alt)
    u_target_enu = apply_vertical_deflection(u_target_enu, a_d, invert=True)

    b = _dcm(q_mnt_enu) @ u_target_enu            # target in MNT
    u_tel_gim = _dcm(q_st_gim).T @ u_tel_st       # tel boresight in GIM

    # Solve numerically: minimise |R_GM(psi,alpha,theta_NP) . b - u_tel_gim|^2.
    # The pointing is TWO-FOLD AMBIGUOUS: (psi, alpha) and (psi+180, 180-alpha)
    # reach the same sky direction, and the second branch drives the tube over
    # the top. The grid search and a heavy penalty confine the solve to
    # alpha <= alpha_max_deg so the over-the-top branch cannot be returned.
    lim = np.radians(alpha_max_deg if alpha_max_deg is not None else 90.0)

    def cost_raw(x):
        R_gm = _dcm(gim_q_mnt(x[0], x[1], theta_np))
        return np.sum((R_gm @ b - u_tel_gim)**2)

    def cost(x):
        pen = max(0.0, x[1] - lim)**2 + max(0.0, -np.pi/2 - x[1])**2
        return cost_raw(x) + 10.0 * pen

    # Coarse grid search for a good starting point, below the ceiling only
    best_c, best_x0 = 1e9, [0.0, 0.0]
    for psi in np.linspace(0, 2*np.pi, 36, endpoint=False):
        for alpha in np.linspace(-np.pi/2, lim, 18):
            c = cost([psi, alpha])
            if c < best_c:
                best_c, best_x0 = c, [psi, alpha]

    res = minimize(cost, best_x0, method='Nelder-Mead',
                   options={'xatol': 1e-12, 'fatol': 1e-16, 'maxiter': 5000})
    if cost_raw(res.x) > 1e-8:
        raise RuntimeError(
            f"inverse_telescope: no solution with encoder alt <= "
            f"{np.degrees(lim):.1f} deg (residual {cost_raw(res.x):.2e}) -- "
            f"target unreachable under the altitude ceiling")

    return (np.degrees(res.x[0]) % 360,
            float(np.clip(np.degrees(res.x[1]), -90, np.degrees(lim))))


# =====================================================================
# Wahba's problem  (used by coarse calibration)
# =====================================================================

def _wahba(vecs_a, vecs_b):
    """Find R minimising Sum|v_a - R.v_b|^2 via SVD."""
    B = vecs_a.T @ vecs_b
    U, _, Vt = np.linalg.svd(B)
    d = np.linalg.det(U) * np.linalg.det(Vt)
    return _from_dcm(U @ np.diag([1, 1, d]) @ Vt)


# =====================================================================
# Calibration
# =====================================================================

def _coarse_calibrate(enc_az, enc_alt, u_true_enu):
    """
    Coarse estimate of q_mnt_enu and q_st_gim.
    Assumes theta_NP = 0, a_d = 0, star-tracker boresight = [0,0,1]_ST.

    Each observation says: the star tracker boresight [0,0,1], mapped
    through ST->GIM->MNT->ENU, should equal the measured u_true_enu.
    With q_st_gim = I  (absorbed into q_mnt_enu for now):
        u_mnt = R_GIM_MNT(psi,alpha,0)^T . [0,0,1]
    and we need R_MNT_ENU^T . u_mnt = u_true_enu.

    Solve Wahba's problem for R_MNT_ENU, then again for R_ST_GIM.
    """
    n = len(enc_az)
    u_mnt_arr = np.zeros((n, 3))
    for i in range(n):
        R_gm = _dcm(gim_q_mnt(np.radians(enc_az[i]), np.radians(enc_alt[i]), 0.0))
        u_mnt_arr[i] = R_gm.T @ np.array([0, 0, 1.0])

    # Wahba:  u_mnt = R_ME . u_enu   ->  solve for R_ME
    q_me = _wahba(u_mnt_arr, u_true_enu)

    # Now refine q_st_gim.  For each obs:
    #   u_gim_i = R_GM . R_ME . u_enu_i
    # We want:  R_SG . u_gim_i ~ [0,0,1]
    # Wahba:  [0,0,1] = R_SG . u_gim   ->  solve for R_SG
    R_me = _dcm(q_me)
    u_bor = np.tile([0, 0, 1.0], (n, 1))
    u_gim_arr = np.zeros((n, 3))
    for i in range(n):
        R_gm = _dcm(gim_q_mnt(np.radians(enc_az[i]), np.radians(enc_alt[i]), 0.0))
        u_gim_arr[i] = R_gm @ R_me @ u_true_enu[i]

    q_sg = _wahba(u_bor, u_gim_arr)
    return q_me, q_sg


def _fine_calibrate(enc_az, enc_alt, u_true_enu,
                    q_me_init, q_sg_init,
                    theta_np0=0.0, a_d0=0.0):
    """
    Refine all 8 DOF via nonlinear least squares.

    State x[0:3] = Rodrigues perturbation to q_mnt_enu
          x[3:6] = Rodrigues perturbation to q_st_gim
          x[6]   = theta_np   (radians)
          x[7]   = a_d        (degrees of deflection at zenith)
    """
    n = len(enc_az)

    def residuals(x):
        q_me = _qmul(R.from_rotvec(x[0:3]), q_me_init)
        q_sg = _qmul(R.from_rotvec(x[3:6]), q_sg_init)
        tnp, ad = x[6], x[7]
        res = np.zeros(3 * n)
        for i in range(n):
            p_az, p_alt = forward_star_tracker(
                enc_az[i], enc_alt[i], q_me, q_sg, tnp, ad)
            u_p = altaz_to_enu(p_az, p_alt)
            res[3*i:3*i+3] = (u_p - u_true_enu[i]) * 206265
        return res

    x0 = np.zeros(8)
    x0[6], x0[7] = theta_np0, a_d0
    result = least_squares(residuals, x0, method='lm',
                           ftol=1e-12, xtol=1e-12, max_nfev=500)

    q_me = _qmul(R.from_rotvec(result.x[0:3]), q_me_init)
    q_sg = _qmul(R.from_rotvec(result.x[3:6]), q_sg_init)
    rms = np.sqrt(np.mean(result.fun**2))
    return q_me, q_sg, result.x[6], result.x[7], rms


# =====================================================================
# Schedule_gen  (unchanged)
# =====================================================================

class Schedule_gen():
    def __init__(self, t0, tf, t_step, function):
        step_num = np.floor((tf - t0) / t_step)
        self.time_array = t0 + t_step * np.arange(step_num)
        self.time_step = t_step
        self.evaluate_position = function
        self.tf = tf

    def get_deriv_slope(self):
        int_pos_array = np.append(self.time_array, self.tf)
        int_positions = self.evaluate_position(int_pos_array)
        deltas = int_positions[1:] - int_positions[:-1]
        deltas[:-1] = np.copy(deltas[:-1]) / self.time_step
        deltas[-1] = deltas[-1] / (self.tf - self.time_array[-1])
        return deltas

    def put_together(self):
        sched = dict()
        sched["time_step"] = self.time_step
        sched["time_array"] = self.time_array
        sched["int_pos_time_array"] = np.append(self.time_array, self.tf)
        sched["int_positions"] = self.evaluate_position(sched["int_pos_time_array"])
        sched["slew_rates"] = self.get_deriv_slope()
        sched["func"] = self.evaluate_position
        return sched


# =====================================================================
# Main controller class
# =====================================================================

class OGS_control:
    def __init__(self, port, location, file_start_time, verbose=True):
        self.ser = serial.Serial(port=port, baudrate=9600, timeout=1)
        self.location = location          # astropy EarthLocation
        self.file_start_time = file_start_time
        self.verbose = verbose
        self.record_state_time = 0.05
        self.sleep_time = 0.1
        self.slew_rates = list((0, 0))
        self.current_cmd_rates = [0, 0]
        self.history = []

        # Pointing model state (None = uncalibrated)
        self.cal = None

        # Telescope boresight direction in the star-tracker frame.
        # Set via inter-camera alignment (Riesing section 5.1.1):
        #   center a target in the telescope detector, plate-solve
        #   with the star tracker, record the target's pixel position,
        #   convert to a unit vector in the ST frame.
        # Default: aligned with star-tracker boresight.
        self.u_tel_st = np.array([0.0, 0.0, 1.0])

        self.log(f"Connected to telescope on {port}")

    # =================================================================
    # Utility
    # =================================================================

    def set_time_to_now(self):
        import datetime
        now = datetime.datetime.now()
        li = time.localtime()
        gmt_off = 256 + int(li.tm_gmtoff/3600) if li.tm_gmtoff < 0 else int(li.tm_gmtoff/3600)
        payload = bytes([now.hour, now.minute, now.second,
                         now.month, now.day, now.year % 100,
                         gmt_off, li.tm_isdst])
        cmd = b'H' + payload
        print(f"Setting time. Command: {cmd}")
        resp = self.nexstar_command_and_read_until(cmd, stop_char=b'#')
        print(f"Time set response: {resp}")

    def hex_to_dec(self, s):
        return int(s, 16)

    def dec_to_hex(self, s):
        return hex(int(s))[2:].upper().zfill(8)

    def log(self, msg):
        if self.verbose:
            print(f"[LOG {time.strftime('%H:%M:%S')}]: {msg}")

    def save_log(self, filename):
        with open(filename, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=self.history[0].keys())
            w.writeheader(); w.writerows(self.history)
        print(f"Pass data saved to {filename}")

    def nexstar_command_and_read_until(self, command, stop_char=b'#'):
        self.ser.write(command)
        return self.ser.read_until(stop_char)

    # =================================================================
    # Inter-camera alignment  (Riesing section 5.1.1)
    # =================================================================

    def set_tel_boresight(self, ra_deg, dec_deg, timestamp=None):
        """
        Record the telescope boresight direction in the ST frame.

        Procedure: center a target (star or distant light) in the
        telescope detector, then plate-solve a star-tracker image.
        Pass the plate-solved (RA, Dec) of the centred target here.

        If the pointing model is already calibrated, this computes
        u_tel_st from the full chain.  Otherwise it stores the J2K
        direction for later use after calibration.
        """
        if timestamp is None:
            timestamp = Time.now()
        if self.cal is None:
            self.log("Model not yet calibrated -- storing boresight J2K "
                     "for use after calibration.")
            self._boresight_j2k = (ra_deg, dec_deg, timestamp)
            return

        # Convert J2K -> ENU -> MNT -> GIM -> ST
        u_enu = j2k_to_enu(ra_deg, dec_deg, timestamp, self.location)
        enc_az, enc_alt = self.get_raw_azm_alt()
        R_sg = _dcm(self.cal['q_st_gim'])
        R_gm = _dcm(gim_q_mnt(np.radians(enc_az), np.radians(enc_alt),
                               self.cal['theta_np']))
        R_me = _dcm(self.cal['q_mnt_enu'])
        u_mnt = R_me @ u_enu
        u_gim = R_gm @ u_mnt
        u_st  = R_sg @ u_gim
        self.u_tel_st = u_st / np.linalg.norm(u_st)
        sep = np.degrees(np.arccos(np.clip(np.dot(self.u_tel_st,
                                                   [0, 0, 1]), -1, 1)))
        self.log(f"Telescope boresight set: u_tel_st = "
                 f"[{self.u_tel_st[0]:.6f}, {self.u_tel_st[1]:.6f}, "
                 f"{self.u_tel_st[2]:.6f}]  "
                 f"({sep:.4f} deg from star-tracker boresight)")

    def set_tel_boresight_from_pixel(self, tel_ra, tel_dec, timestamp=None):
        """
        Set u_tel_st from the telescope boresight RA/Dec obtained via
        WCS lookup of the known telescope pixel in a plate-solved
        star-tracker image.

        Same rotation chain as set_tel_boresight, but called with
        coordinates derived from the star-tracker WCS rather than
        from a manually identified target.
        """
        self.set_tel_boresight(tel_ra, tel_dec, timestamp)

    # =================================================================
    # Motion control  (unchanged)
    # =================================================================

    def stop(self):
        self.nexstar_command_and_read_until(protocol.slewAZM_STOP)
        self.nexstar_command_and_read_until(protocol.slewALT_STOP)
        self.log("Slew stopped.")

    def set_tracking_rate(self, rate_type='sidereal'):
        command_map = {
            'off': protocol.TRACKING_RATE_OFF, 'lunar': protocol.TRACKING_RATE_LUNAR,
            'solar': protocol.TRACKING_RATE_SOLAR, 'sidereal': protocol.TRACKING_RATE_SIDEREAL,
            'king': protocol.TRACKING_RATE_KING,
            0: protocol.TRACKING_RATE_OFF, 1: protocol.TRACKING_RATE_LUNAR,
            2: protocol.TRACKING_RATE_SOLAR, 3: protocol.TRACKING_RATE_SIDEREAL,
            4: protocol.TRACKING_RATE_KING}
        if rate_type not in command_map:
            raise ValueError(f"Invalid tracking rate: {rate_type}.")
        resp = self.nexstar_command_and_read_until(command_map[rate_type], b'#')
        names = {'off': 'Off', 'lunar': 'Lunar', 'solar': 'Solar',
                 'sidereal': 'Sidereal', 'king': 'King',
                 0: 'Off', 1: 'Lunar', 2: 'Solar', 3: 'Sidereal', 4: 'King'}
        self.log(f"Tracking rate set to: {names.get(rate_type, '?')}")
        return resp

    def enable_sidereal_tracking(self):
        return self.set_tracking_rate('sidereal')

    def disable_tracking(self):
        return self.set_tracking_rate('off')

    # =================================================================
    # Position reading
    # =================================================================

    def get_raw_azm_alt(self):
        """ 
        Raw encoder readings in degrees.  Returns (None, None) on failure.
        """
        out = self.nexstar_command_and_read_until(
            protocol.getAZM_ALT_PRECISE, b"#").decode()
        try:
            azm_s, alt_s = out.replace("#", "").split(",")
            azm = 360 * self.hex_to_dec(azm_s) / protocol.ROT_PRECISE
            alt = 360 * self.hex_to_dec(alt_s) / protocol.ROT_PRECISE
        except Exception:
            print("Error parsing raw azm/alt:", out)
            return None, None
        azm %= 360
        if alt > 180: alt -= 360
        return azm, alt

    def get_azm_alt(self):
        """
        Corrected TELESCOPE boresight position in true sky AltAz.
        Uses the full Riesing chain with u_tel_st.
        Falls back to raw encoder values if uncalibrated.
        """
        azm, alt = self.get_raw_azm_alt()
        if self.cal is not None:
            return forward_telescope(azm, alt,
                                     self.cal['q_mnt_enu'],
                                     self.cal['q_st_gim'],
                                     self.cal['theta_np'],
                                     self.cal['a_d'],
                                     self.u_tel_st)
        return azm, alt

    def get_st_azm_alt(self):
        """Star-tracker boresight position in true sky AltAz."""
        azm, alt = self.get_raw_azm_alt()
        if self.cal is not None:
            return forward_star_tracker(azm, alt,
                                        self.cal['q_mnt_enu'],
                                        self.cal['q_st_gim'],
                                        self.cal['theta_np'],
                                        self.cal['a_d'])
        return azm, alt

    # =================================================================
    # Goto
    # =================================================================

    def _compute_encoder_target(self, target_az, target_alt):
        """Inverse model: sky target -> encoder angles for the TELESCOPE.

        Altitude ceiling, three layers (a zenith crossing snapped a camera
        cable, 2026-08-09):
          1. Sky target clamped to MAX_TUBE_ALT.
          2. The inverse solve is bounded to the below-zenith branch using a
             ceiling MEASURED now: encoder zero offset (current encoder alt
             minus current model sky alt) + MAX_TUBE_ALT. This is physical
             even though the incremental encoders power up at arbitrary zero.
             NOTE: assumes the tube is currently on the normal (unflipped)
             side of zenith -- true whenever it starts below MAX_TUBE_ALT.
          3. Invariant: the solution's encoder-alt move must match the sky-alt
             move (the physical alt axis is 1:1); a flipped-branch solution
             violates this by ~2x(90 - alt) and is refused.
        (Layer 4 lives in go_to_azm_alt_corrective: an in-slew guard stops
        the mount if the model boresight ever exceeds the ceiling.)
        """
        if target_alt > MAX_TUBE_ALT:
            self.log(f"ALT CEILING: sky target alt {target_alt:.2f} clamped "
                     f"to {MAX_TUBE_ALT} deg")
            target_alt = MAX_TUBE_ALT
        if self.cal is None:
            return target_az, target_alt

        cur_az, cur_alt = self.get_raw_azm_alt()
        if cur_az is None:
            raise RuntimeError("_compute_encoder_target: encoder read failed;"
                               " refusing to slew without a measured ceiling")
        _, sky_alt_now = forward_telescope(
            cur_az, cur_alt, self.cal['q_mnt_enu'], self.cal['q_st_gim'],
            self.cal['theta_np'], self.cal['a_d'], self.u_tel_st)
        offset = cur_alt - sky_alt_now          # encoder-alt zero offset

        # The ceiling must never exceed the pole: an unbounded value here lets
        # the solver return the over-the-top branch (bug found 2026-08-09).
        alpha_max = float(np.clip(offset + MAX_TUBE_ALT, 10.0, 89.0))

        enc_az, enc_alt = inverse_telescope(
            target_az, target_alt,
            self.cal['q_mnt_enu'], self.cal['q_st_gim'],
            self.cal['theta_np'], self.cal['a_d'], self.u_tel_st,
            alpha_max_deg=alpha_max)

        # ROUND-TRIP VERIFICATION -- the catch-all. Push the solution back
        # through the forward model: it must reproduce the requested sky
        # target. This catches a flipped branch, a degenerate model fit, a
        # bad boresight vector, and solver non-convergence in one test,
        # without trusting any of them individually.
        chk_az, chk_alt = forward_telescope(
            enc_az, enc_alt, self.cal['q_mnt_enu'], self.cal['q_st_gim'],
            self.cal['theta_np'], self.cal['a_d'], self.u_tel_st)
        err = np.hypot(((chk_az - target_az + 180) % 360 - 180)
                       * np.cos(np.radians(target_alt)), chk_alt - target_alt)
        if err > 0.5:
            raise RuntimeError(
                f"POINTING CHECK FAILED: enc=({enc_az:.2f},{enc_alt:.2f}) maps "
                f"back to sky=({chk_az:.2f},{chk_alt:.2f}), requested "
                f"({target_az:.2f},{target_alt:.2f}) -- {err:.2f} deg error. "
                f"Refusing to slew (bad model, boresight, or solve).")

        d_enc, d_sky = enc_alt - cur_alt, target_alt - sky_alt_now
        if abs(d_enc - d_sky) > 10.0:
            raise RuntimeError(
                f"ALT CEILING: inverse solution moves encoder alt "
                f"{d_enc:+.1f} deg for a {d_sky:+.1f} deg sky move -- "
                f"flipped (over-the-top) branch, refusing to slew")
        self.log(f"  goto check OK: sky({target_az:.2f},{target_alt:.2f}) -> "
                 f"enc({enc_az:.2f},{enc_alt:.2f})  round-trip err "
                 f"{err*3600:.0f}\"")
        return enc_az, enc_alt

    def go_to_azm_alt_corrective(self, target_az, target_alt, prec=5):
        """Proportional corrective slew in encoder space."""
        safe_limit = protocol.max_slew_rate * 0.8
        enc_az, enc_alt = self._compute_encoder_target(target_az, target_alt)
        self.log(f"Slewing to sky=({target_az:.2f}, {target_alt:.2f}) "
                 f"enc_target=({enc_az:.2f}, {enc_alt:.2f})")
        while True:
            cur_az, cur_alt = self.get_raw_azm_alt()
            if cur_az is None:
                self.stop()
                self.log("ALT GUARD: encoder read failed mid-slew -- stopped.")
                raise RuntimeError("Encoder read failed during corrective slew")
            # In-slew guard: if the model says the tube is above the ceiling,
            # something upstream is wrong -- stop immediately, ask questions
            # later. Independent of how the target was computed.
            if self.cal is not None:
                _, guard_alt = forward_telescope(
                    cur_az, cur_alt, self.cal['q_mnt_enu'],
                    self.cal['q_st_gim'], self.cal['theta_np'],
                    self.cal['a_d'], self.u_tel_st)
                if guard_alt > MAX_TUBE_ALT + 1.0:
                    self.stop()
                    self.log(f"ALT GUARD TRIPPED: boresight at "
                             f"{guard_alt:.1f} deg -- slew aborted.")
                    raise RuntimeError(
                        f"Altitude guard tripped at {guard_alt:.1f} deg")
            err_az  = (((enc_az - cur_az + 180) % 360) - 180) * 3600
            err_alt = (enc_alt - cur_alt) * 3600
            if abs(err_az) < prec and abs(err_alt) < prec:
                break
            cmd_az  = np.clip(err_az,  -safe_limit, safe_limit)
            cmd_alt = np.clip(err_alt, -safe_limit, safe_limit)
            mx = max(abs(cmd_az), abs(cmd_alt))
            if mx > safe_limit:
                s = safe_limit / mx; cmd_az *= s; cmd_alt *= s
            self.nexstar_command_and_read_until(protocol.slewAZM_var(cmd_az))
            self.nexstar_command_and_read_until(protocol.slewALT_var(cmd_alt))
            time.sleep(0.5)
        self.stop()
        self.log("Corrective goto complete.")
        return enc_az, enc_alt

    def goto_azm_alt(self, azm_deg, alt_deg, wait=True):
        """NexStar precise goto with pointing-model inverse applied."""
        if not (0 <= azm_deg < 360):
            raise ValueError(f"Az must be in [0,360), got {azm_deg}")
        if not (-90 <= alt_deg <= MAX_TUBE_ALT):
            raise ValueError(f"Alt must be in [-90,{MAX_TUBE_ALT}] "
                             f"(tube altitude ceiling), got {alt_deg}")
        cmd_az, cmd_alt = self._compute_encoder_target(azm_deg, alt_deg)
        azm_n = cmd_az % 360
        alt_n = cmd_alt if cmd_alt >= 0 else cmd_alt + 360.0
        azm_hex = self.dec_to_hex(round((azm_n / 360) * protocol.ROT_PRECISE))
        alt_hex = self.dec_to_hex(round((alt_n / 360) * protocol.ROT_PRECISE))
        self.disable_tracking()
        self.log(f"Goto sky=({azm_deg:.4f},{alt_deg:.4f}) -> "
                 f"enc=({cmd_az:.4f},{cmd_alt:.4f})")
        self.nexstar_command_and_read_until(
            protocol.gotoAZM_ALT(azm_hex, alt_hex), b"#")
        if wait:
            time.sleep(1.0)
            while True:
                r = self.nexstar_command_and_read_until(
                    protocol.isGotoInProgress(), b"#")
                rs = r.decode(errors='ignore').strip().replace('#', '')
                try:
                    if int(rs) == 0: break
                except ValueError: pass
                time.sleep(0.5)
        self.log("AZM/ALT GOTO complete.")

    # =================================================================
    # Calibration CSV  (stores J2K RA/Dec from plate solver)
    # =================================================================

    _CAL_FIELDS = ['point_id', 'timestamp',
                   'raw_az', 'raw_alt',
                   'solved_ra', 'solved_dec']

    def record_raw_position(self, cal_file):
        """Read encoders, append a row to cal CSV.  Returns point_id."""
        raw_az, raw_alt = self.get_raw_azm_alt()
        if raw_az is None:
            print("Encoder read failed -- not recorded.")
            return None
        print("Reading is:", raw_az, raw_alt)
        write_header = not os.path.exists(cal_file)
        point_id = 0
        if not write_header:
            with open(cal_file, 'r') as f:
                point_id = sum(1 for _ in csv.DictReader(f))
        ts = Time.now().iso
        with open(cal_file, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=self._CAL_FIELDS)
            if write_header: w.writeheader()
            w.writerow({'point_id': point_id, 'timestamp': ts,
                        'raw_az': raw_az, 'raw_alt': raw_alt,
                        'solved_ra': '', 'solved_dec': ''})
        self.log(f"Raw position recorded: id={point_id}  "
                 f"raw=({raw_az:.4f},{raw_alt:.4f})  t={ts}")
        return point_id

    def record_true_position(self, point_id, ra_deg, dec_deg, cal_file,
                            timestamp=None):
        """
        Update a calibration row with plate-solved J2K coordinates.

        Parameters
        ----------
        point_id  : int     from record_raw_position
        ra_deg    : float   plate-solved RA  (J2K/ICRS, degrees)
        dec_deg   : float   plate-solved Dec (J2K/ICRS, degrees)
        cal_file  : str     path to calibration CSV
        timestamp : Time    optional; overwrite the row's timestamp with the
                            EXPOSURE MIDPOINT of the plate-solved image.
                            record_raw_position stamps the encoder-read time,
                            but the solved RA/Dec belongs to the moment the
                            shutter was open -- and the fit converts RA/Dec to
                            ENU using this timestamp, so a few seconds of gap
                            injects tens of arcsec of error. Safe to omit when
                            the encoder read and exposure are simultaneous.
        """
        if not os.path.exists(cal_file):
            print(f"Cal file not found: {cal_file}"); return False
        with open(cal_file, 'r') as f:
            rows = list(csv.DictReader(f))
        ok = False
        for row in rows:
            if int(row['point_id']) == point_id:
                row['solved_ra']  = ra_deg
                row['solved_dec'] = dec_deg
                if timestamp is not None:
                    row['timestamp'] = timestamp.iso
                ok = True; break
        if not ok:
            print(f"Point {point_id} not found."); return False
        with open(cal_file, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=self._CAL_FIELDS)
            w.writeheader(); w.writerows(rows)
        self.log(f"Solved position recorded: id={point_id}  "
                 f"RA={ra_deg:.6f}  Dec={dec_deg:.6f}")
        return True

    # =================================================================
    # Pointing model fit
    # =================================================================

    def fit_pointing_model(self, cal_file):
        """
        Fit the Riesing 8-DOF pointing model from calibration data.

        Each complete row supplies:
          - encoder (psi, alpha) at the time of observation
          - plate-solved (RA, Dec) in J2K  -> converted to ENU via
            the row timestamp and self.location

        Phase 1: Coarse calibration (Wahba's problem)
        Phase 2: Fine calibration   (Levenberg-Marquardt NLS)

        Requires >= 3 complete observations.  Sets self.cal, after
        which get_azm_alt() returns the telescope boresight position
        and goto methods point the telescope.
        """
        if not os.path.exists(cal_file):
            raise FileNotFoundError(cal_file)

        with open(cal_file, 'r') as f:
            complete = [r for r in csv.DictReader(f)
                        if r['solved_ra'] != '' and r['solved_dec'] != '']
        n = len(complete)
        if n < 3:
            raise RuntimeError(f"Need >= 3 complete points, have {n}.")

        enc_az  = [float(r['raw_az'])  for r in complete]
        enc_alt = [float(r['raw_alt']) for r in complete]
        ra      = [float(r['solved_ra'])  for r in complete]
        dec     = [float(r['solved_dec']) for r in complete]
        times   = Time([r['timestamp'] for r in complete])

        # J2K -> ENU for each observation
        u_true_enu = np.zeros((n, 3))
        print(f"\nConverting {n} plate-solved J2K positions to ENU:")
        for i in range(n):
            u_true_enu[i] = j2k_to_enu(ra[i], dec[i], times[i], self.location)
            az_t, alt_t = enu_to_altaz(u_true_enu[i])
            print(f"  [{i:2d}] RA={ra[i]:9.4f} Dec={dec[i]:+8.4f}  "
                  f"->  Az={az_t:7.2f} Alt={alt_t:+6.2f}")

        # Phase 1
        print(f"\nPhase 1: Coarse calibration ({n} points)...")
        q_me, q_sg = _coarse_calibrate(enc_az, enc_alt, u_true_enu)
        errs = []
        for i in range(n):
            p_az, p_alt = forward_star_tracker(
                enc_az[i], enc_alt[i], q_me, q_sg, 0, 0)
            u_p = altaz_to_enu(p_az, p_alt)
            errs.append(np.linalg.norm(u_p - u_true_enu[i]) * 206265)
        print(f"  Coarse RMS: {np.sqrt(np.mean(np.array(errs)**2)):.0f}\"")

        # Phase 2
        print(f"Phase 2: Fine calibration (8 DOF)...")
        q_me_f, q_sg_f, tnp, ad, rms = _fine_calibrate(
            enc_az, enc_alt, u_true_enu, q_me, q_sg)

        self.cal = {'q_mnt_enu': q_me_f, 'q_st_gim': q_sg_f,
                    'theta_np': tnp, 'a_d': ad}

        # Summary
        euler_me = q_me_f.as_euler('ZYX', degrees=True)
        euler_sg = q_sg_f.as_euler('ZYX', degrees=True)
        print("\n" + "="*60)
        print("Riesing pointing model fitted.")
        print(f"  theta_NP = {np.degrees(tnp):+.4f} deg ({tnp*206265:+.1f}\")")
        print(f"  a_d      = {ad*3600:+.1f}\"")
        print(f"  MNT->ENU Euler(Z,Y,X) = ({euler_me[0]:+.3f}, "
              f"{euler_me[1]:+.3f}, {euler_me[2]:+.3f}) deg")
        print(f"  ST->GIM  Euler(Z,Y,X) = ({euler_sg[0]:+.3f}, "
              f"{euler_sg[1]:+.3f}, {euler_sg[2]:+.3f}) deg")
        print(f"  Star-tracker forward RMS = {rms:.1f}\"")
        print("="*60)

        # Per-point residuals
        print("\nPer-point residuals (star-tracker forward):")
        for i in range(n):
            p_az, p_alt = forward_star_tracker(
                enc_az[i], enc_alt[i], q_me_f, q_sg_f, tnp, ad)
            az_t, alt_t = enu_to_altaz(u_true_enu[i])
            daz  = ((p_az - az_t + 180) % 360 - 180) * 3600
            dalt = (p_alt - alt_t) * 3600
            print(f"  [{i:2d}] daz={daz:+8.1f}\"  dalt={dalt:+7.1f}\"")

        # If a boresight J2K was stored before calibration, apply it now
        if hasattr(self, '_boresight_j2k'):
            ra_b, dec_b, t_b = self._boresight_j2k
            self.log("Applying stored inter-camera alignment...")
            self.set_tel_boresight(ra_b, dec_b, t_b)
            del self._boresight_j2k

    # =================================================================
    # State recording and tracking loop  (unchanged)
    # =================================================================

    def record_state(self, measure_position=True, input_az=None, input_alt=None):
        t = time.time() - self.file_start_time
        if measure_position:
            az, alt = self.get_azm_alt()
        else:
            az, alt = input_az, input_alt
        self.history.append({'time': t, 'az': az, 'alt': alt,
                             'cmd_rate_az': self.current_cmd_rates[1],
                             'cmd_rate_alt': self.current_cmd_rates[0]})

    def measure_runtime_of_get_coords(self):
        ts = []
        for _ in range(10):
            t0 = time.time(); self.get_azm_alt(); ts.append(time.time()-t0)
        self.record_state_time = np.mean(ts)

    def follow_schedule(self, schedule, PID_gains):
        self.func = schedule["func"]
        self.proportional_gain = PID_gains[0]
        self.integral_gain = PID_gains[1]
        self.derivative_gain = PID_gains[2]
        self.log("Commencing Tracking...")
        self.history = []
        for idx, target_time in enumerate(schedule["time_array"]):
            while (time.time()-self.file_start_time+self.record_state_time) < target_time:
                if (time.time()-self.file_start_time+2*self.record_state_time+self.sleep_time) < target_time:
                    self.record_state(); time.sleep(self.sleep_time)
            intended = schedule["int_positions"][idx]
            cur_az, cur_alt = self.get_azm_alt()
            #err = np.array([(intended[0]-cur_az)*3600, (intended[1]-cur_alt)*3600])
            err=np.array([0,0])
            ctrl = self.proportional_gain * err
            slr = 3600 * schedule["slew_rates"][idx]
            az_r = np.clip(slr[0]+ctrl[0], -protocol.max_slew_rate, protocol.max_slew_rate)
            al_r = np.clip(slr[1]+ctrl[1], -protocol.max_slew_rate, protocol.max_slew_rate)
            self.nexstar_command_and_read_until(protocol.slewAZM_var(az_r), b"#")
            self.nexstar_command_and_read_until(protocol.slewALT_var(al_r), b"#")
            self.current_cmd_rates = [al_r, az_r]
            self.record_state(measure_position=False, input_az=cur_az, input_alt=cur_alt)
            self.log(f"CMD at T={target_time:.2f}s | Err: {err[0]:.1f}\", {err[1]:.1f}\"")

    def follow_schedule_with_camera(self, schedule, PID_gains, offset_queue):
        """
        Runs the tracking loop with camera feedback routed through the
        inverse Riesing pointing model to correct for secant projection
        and mechanical distortions.
        """
        import queue as queue_module

        self.func = schedule["func"]
        self.proportional_gain = PID_gains[0]
        self.integral_gain = PID_gains[1]
        self.derivative_gain = PID_gains[2]
        self.log("Commencing Tracking (with camera feedback)...")
        self.history = []

        cam_offset = np.array([0.0, 0.0])   # most recent [daz, dalt] arcsec

        for idx, target_time in enumerate(schedule["time_array"]):
            # Timing/wait loop to hit the exact schedule mark
            while (time.time()-self.file_start_time+self.record_state_time) < target_time:
                if (time.time()-self.file_start_time+2*self.record_state_time+self.sleep_time) < target_time:
                    self.record_state()
                    time.sleep(self.sleep_time)

            # Drain queue, keep most recent offset
            try:
                while True:
                    msg = offset_queue.get_nowait()
                    cam_offset = np.array([msg['daz'], msg['dalt']])
            except queue_module.Empty:
                pass

            intended_sky = schedule["int_positions"][idx]
            
            # Map the camera's great-circle offset back to coordinate spherical degrees
            # (Secant correction for altitude)
            cam_coord_az_offset = (cam_offset[0] / 3600.0) / np.cos(np.radians(intended_sky[1]))
            cam_coord_alt_offset = (cam_offset[1] / 3600.0)

            # Create the "true" target in the sky by combining ephemeris and camera feedback
            target_sky_az = intended_sky[0] + cam_coord_az_offset
            target_sky_alt = intended_sky[1] + cam_coord_alt_offset

            # Pass target through inverse Riesing model to get required mechanical encoders
            enc_target_az, enc_target_alt = self._compute_encoder_target(target_sky_az, target_sky_alt)

            # Compare to current RAW encoders to get true mechanical error in motor-space
            cur_enc_az, cur_enc_alt = self.get_raw_azm_alt()
            
            err_az = (((enc_target_az - cur_enc_az + 180) % 360) - 180) * 3600
            err_alt = (enc_target_alt - cur_enc_alt) * 3600
            err = np.array([err_az, err_alt])

            # PID control operating purely in mechanical encoder space
            ctrl = self.proportional_gain * err
            slr = 3600 * schedule["slew_rates"][idx]
            az_r = np.clip(slr[0]+ctrl[0], -protocol.max_slew_rate, protocol.max_slew_rate)
            al_r = np.clip(slr[1]+ctrl[1], -protocol.max_slew_rate, protocol.max_slew_rate)
            
            self.nexstar_command_and_read_until(protocol.slewAZM_var(az_r), b"#")
            self.nexstar_command_and_read_until(protocol.slewALT_var(al_r), b"#")
            self.current_cmd_rates = [al_r, az_r]
            
            self.record_state(measure_position=False, input_az=cur_enc_az, input_alt=cur_enc_alt)
            self.log(f"CMD at T={target_time:.2f}s | Err: {err[0]:.1f}\", {err[1]:.1f}\" "
                     f"| Cam: {cam_offset[0]:.1f}\", {cam_offset[1]:.1f}\"")
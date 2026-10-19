"""
FSM closed-loop tracking for APDs, sequential Zemax (ZOS-API).
Calibrates by finding where the spot naturally lands at field (0,0),
uses that as the centroid target, then PIDs to hold it there.
"""

import os
import winreg
import clr
import numpy as np
import pandas as pd
from datetime import datetime

# ════════════════════════════════════════════════════════════════════
CONFIG = {
    "zmx_files": {
        "apd1": r"C:\Users\PulseA\Desktop\Ground Station\APD_1_seq2.zmx",
        "apd2": r"C:\Users\PulseA\Desktop\Ground Station\APD_2_seq.zmx",
    },
    "csv_in":  r"C:\Users\PulseA\Downloads\take2_alpha_beta.csv",
    "out_dir": r"C:\Users\PulseA\Desktop\Ground Station\ZOS-API",

    "fsm_surface":    6,
    "fsm_return_cb":  8,

    "tilt_limits_deg": (np.array([-46.5, -1.5]),
                        np.array([-43.5,  1.5])),
    "max_step_deg":    0.5,

    "kp": 0.7,
    "ki": 0.1,
    "kd": 0.1,

    "calib_delta_deg":  0.1,

    # No active_radius filter during PID — the PID itself keeps the
    # spot on detector. Only log NaN when zero rays land at all.
}
# ════════════════════════════════════════════════════════════════════


class PID2D:
    def __init__(self, kp, ki, kd):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.integ  = np.zeros(2)
        self.prev_e = None

    def reset_history(self):
        self.prev_e = None

    def update(self, e, dt=1.0):
        self.integ += e * dt
        d = np.zeros(2) if self.prev_e is None else (e - self.prev_e) / dt
        self.prev_e = e
        return self.kp * e + self.ki * self.integ + self.kd * d


class ZemaxFSM:
    def __init__(self, the_system, ZOSAPI, surface_number, limits_deg):
        self.lo, self.hi = limits_deg
        Col = ZOSAPI.Editors.LDE.SurfaceColumn

        surf     = the_system.LDE.GetSurfaceAt(surface_number)
        surf_aft = the_system.LDE.GetSurfaceAt(surface_number + 2)

        self.cell_tx     = surf.GetSurfaceCell(Col.Par3)
        self.cell_ty     = surf.GetSurfaceCell(Col.Par4)
        self.cell_tx_aft = surf_aft.GetSurfaceCell(Col.Par3)
        self.cell_ty_aft = surf_aft.GetSurfaceCell(Col.Par4)

        print(f"[FSM] surface {surface_number}: "
              f"TiltX={self.cell_tx.DoubleValue:+.4f}, "
              f"TiltY={self.cell_ty.DoubleValue:+.4f}")

    def get_angles(self):
        return np.array([self.cell_tx.DoubleValue, self.cell_ty.DoubleValue])

    def set_angles(self, angles_deg):
        a = np.clip(np.asarray(angles_deg, dtype=float), self.lo, self.hi)
        self.cell_tx.DoubleValue     = float(a[0])
        self.cell_ty.DoubleValue     = float(a[1])
        self.cell_tx_aft.DoubleValue = -90.0 - float(a[0])
        self.cell_ty_aft.DoubleValue = -float(a[1])

        rb = self.get_angles()
        if not np.allclose(rb, a, atol=1e-9):
            raise RuntimeError(
                f"[FSM] write failed: commanded {a}, readback {rb}.")
        return a


class ZemaxDetector:
    """Returns None only when zero rays land (rms==0 and c==(0,0)).
    No radius filter — the PID loop is responsible for keeping the
    spot on the detector."""

    def __init__(self, the_system, ZOSAPI):
        self.sys  = the_system
        self.spot = the_system.Analyses.New_Analysis(
            ZOSAPI.Analysis.AnalysisIDM.StandardSpot)
        s = self.spot.GetSettings()
        s.ReferTo = ZOSAPI.Analysis.Settings.Spot.Reference.Centroid

    def read_centroid(self):
        self.sys.UpdateStatus()
        self.spot.ApplyAndWaitForCompletion()
        sd = self.spot.GetResults().SpotData

        c   = np.array([sd.GetReferenceCoordinate_X_For(1, 1),
                        sd.GetReferenceCoordinate_Y_For(1, 1)])
        rms = sd.GetRMSSpotSizeFor(1, 1)

        if rms == 0.0 and c[0] == 0.0 and c[1] == 0.0:
            return None
        return c

    def close(self):
        self.spot.Close()


def ensure_return_cb_fixed(the_system, ZOSAPI, cb_surface):
    surf  = the_system.LDE.GetSurfaceAt(cb_surface)
    Col   = ZOSAPI.Editors.LDE.SurfaceColumn
    cell  = surf.GetSurfaceCell(Col.Par3)
    val   = cell.DoubleValue
    solve = cell.GetSolveData()
    stype = str(solve.Type) if solve is not None else "None"
    print(f"[CB{cb_surface}] TiltX = {val:+.4f}, solve type = {stype}")
    if "Fixed" not in stype and "None" not in stype:
        print(f"[CB{cb_surface}] removing solve, fixing value at {val:+.4f}")
        cell.MakeSolveFixed()
        cell.DoubleValue = val
        print(f"[CB{cb_surface}] solve now = {cell.GetSolveData().Type}")


def calibrate(fsm, detector, delta_deg):
    """
    Calibrate at field (0,0) with nominal FSM.
    The natural landing point of the spot becomes the target —
    we don't force it to (0,0) since the APD centre may be offset.
    Returns (Jinv, target) where target is the centroid at (0,0).
    """
    a0 = fsm.get_angles()
    c0 = detector.read_centroid()
    print(f"[CAL] start angles={a0}, centroid={c0}")
    if c0 is None:
        raise RuntimeError(
            "[CAL] no rays at field (0,0) with nominal FSM — "
            "check that the system traces on-axis.")

    # The natural centroid position at (0,0) becomes our lock target
    target = c0.copy()
    print(f"[CAL] lock target set to: {target}")

    J = np.zeros((2, 2))
    for axis in range(2):
        a = a0.copy()
        a[axis] += delta_deg
        fsm.set_angles(a)
        c = detector.read_centroid()
        print(f"[CAL] poke axis {axis}: angles={fsm.get_angles()}, "
              f"centroid={c}")
        if c is None:
            raise RuntimeError(
                f"[CAL] poke on axis {axis} lost the beam — "
                f"reduce calib_delta_deg.")
        J[:, axis] = (c - c0) / delta_deg
        fsm.set_angles(a0)

    cond = np.linalg.cond(J)
    print(f"[CAL] Jacobian:\n{J}\n[CAL] condition number = {cond:.3e}")
    if cond > 1e3:
        raise RuntimeError("[CAL] Jacobian ill-conditioned.")

    return np.linalg.inv(J), target


def connect():
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r'Software\Zemax', 0, winreg.KEY_READ)
    zemax_dir, _ = winreg.QueryValueEx(key, 'ZemaxRoot')
    winreg.CloseKey(key)
    clr.AddReference(os.path.join(
        zemax_dir, r'ZOS-API\Libraries\ZOSAPI_NetHelper.dll'))
    import ZOSAPI_NetHelper
    ZOSAPI_NetHelper.ZOSAPI_Initializer.Initialize()
    clr.AddReference('ZOSAPI_Interfaces')
    clr.AddReference('ZOSAPI')
    import ZOSAPI
    conn = ZOSAPI.ZOSAPI_Connection()
    app  = conn.CreateNewApplication()
    return app, ZOSAPI


def run_apd(label, zmx_path, df, cfg, the_system, ZOSAPI):
    print(f"\n{'═'*60}")
    print(f"[RUN] {label}: {zmx_path}")
    the_system.LoadFile(zmx_path, False)
    the_system.SystemData.Fields.Type = ZOSAPI.SystemData.FieldType.Angle
    field1 = the_system.SystemData.Fields.GetField(1)

    ensure_return_cb_fixed(the_system, ZOSAPI, cfg["fsm_return_cb"])

    fsm = ZemaxFSM(the_system, ZOSAPI, cfg["fsm_surface"],
                   cfg["tilt_limits_deg"])
    det = ZemaxDetector(the_system, ZOSAPI)

    K       = len(df)
    field_x = df["field_x_transmission"].to_numpy()
    field_y = df["field_y_transmission"].to_numpy()

    # ── Calibrate at (0,0) — target is the natural landing point ──
    field1.X, field1.Y = 0.0, 0.0
    Jinv, target = calibrate(fsm, det, cfg["calib_delta_deg"])
    print(f"[{label}] PID target (APD centroid lock point): {target}")

    pid   = PID2D(cfg["kp"], cfg["ki"], cfg["kd"])
    angle = fsm.get_angles()
    max_step = cfg["max_step_deg"]

    cols = ['field_x_deg', 'field_y_deg',
            'xC_pre', 'yC_pre', 'tilt_x_deg', 'tilt_y_deg',
            'xC_post', 'yC_post', 'resid', 'beacon']
    rows   = []
    n_lost = 0

    for k in range(K):
        field1.X = float(field_x[k])
        field1.Y = float(field_y[k])

        c_pre = det.read_centroid()

        if c_pre is None:
            # Zero rays — hold FSM, log LOST
            n_lost += 1
            pid.reset_history()
            rows.append([field_x[k], field_y[k],
                         np.nan, np.nan,
                         angle[0], angle[1],
                         np.nan, np.nan, np.nan,
                         'LOST'])
            print(f"  {label} k={k:3d}  "
                  f"field=({field_x[k]:+.4f},{field_y[k]:+.4f})  "
                  f"BEACON LOST")
            continue

        # PID steers toward the calibrated lock target
        e_fsm  = Jinv @ (target - c_pre)
        step   = np.clip(pid.update(e_fsm), -max_step, max_step)
        angle  = fsm.set_angles(angle + step)

        c_post = det.read_centroid()
        if c_post is None:
            xpost, ypost, resid = np.nan, np.nan, np.nan
        else:
            xpost, ypost = c_post
            resid = float(np.linalg.norm(target - c_post))

        rows.append([field_x[k], field_y[k],
                     c_pre[0], c_pre[1],
                     angle[0], angle[1],
                     xpost, ypost, resid,
                     'OK'])

        print(f"  {label} k={k:3d}  "
              f"field=({field_x[k]:+.4f},{field_y[k]:+.4f})  "
              f"pre=({c_pre[0]:+.5f},{c_pre[1]:+.5f})  "
              f"tilt=({angle[0]:+.4f},{angle[1]:+.4f})  "
              f"resid={resid:.6f}")

    det.close()
    print(f"[{label}] {K - n_lost}/{K} tracked, {n_lost} lost.")
    return pd.DataFrame(rows, columns=cols)


def main(cfg):
    df = pd.read_csv(cfg["csv_in"])

    app, ZOSAPI = connect()
    the_system  = app.PrimarySystem

    os.makedirs(cfg["out_dir"], exist_ok=True)
    out_file = os.path.join(
        cfg["out_dir"],
        f'apd_pid_track_{datetime.now().strftime("%Y%m%d_%H%M")}.csv')

    all_results = {}
    for label, zmx_path in cfg["zmx_files"].items():
        all_results[label] = run_apd(label, zmx_path, df, cfg,
                                     the_system, ZOSAPI)

    app.CloseApplication()

    base = pd.DataFrame({
        "field_x_transmission": df["field_x_transmission"].to_numpy(),
        "field_y_transmission": df["field_y_transmission"].to_numpy(),
    })
    for label, res in all_results.items():
        for col in res.columns:
            base[f"{col}_{label}"] = res[col].to_numpy()

    base.to_csv(out_file, index=False)
    print(f"\nDone. CSV written to: {out_file}")


if __name__ == "__main__":
    main(CONFIG)
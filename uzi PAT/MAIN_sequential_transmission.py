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
    "csv_field": r"C:\Users\PulseA\Downloads\a_b_final.csv",
    "csv_tilts": r"C:\Users\PulseA\Desktop\Ground Station\ZOS-API\fsm_track_20260614_2333.csv",
    "out_dir":   r"C:\Users\PulseA\Desktop\Ground Station\ZOS-API",

    "fsm_surface":      6,
    "fsm_return_cb":    8,
    "config_index":     1,
    "active_radius_mm": 0.1,
}
# ════════════════════════════════════════════════════════════════════


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


class ZemaxFSM:
    def __init__(self, the_system, ZOSAPI, fsm_surface):
        Col = ZOSAPI.Editors.LDE.SurfaceColumn

        surf     = the_system.LDE.GetSurfaceAt(fsm_surface)
        surf_aft = the_system.LDE.GetSurfaceAt(fsm_surface + 2)

        self.cell_tx     = surf.GetSurfaceCell(Col.Par3)
        self.cell_ty     = surf.GetSurfaceCell(Col.Par4)
        self.cell_tx_aft = surf_aft.GetSurfaceCell(Col.Par3)
        self.cell_ty_aft = surf_aft.GetSurfaceCell(Col.Par4)

        print(f"[FSM] surface {fsm_surface}: "
              f"TiltX={self.cell_tx.DoubleValue:+.4f}, "
              f"TiltY={self.cell_ty.DoubleValue:+.4f}")

    def set_angles(self, tilt_x, tilt_y):
        tx, ty = float(tilt_x), float(tilt_y)
        self.cell_tx.DoubleValue     = tx
        self.cell_ty.DoubleValue     = ty
        self.cell_tx_aft.DoubleValue = -90.0 - tx
        self.cell_ty_aft.DoubleValue = -ty


class ZemaxDetector:
    def __init__(self, the_system, ZOSAPI, config_index):
        self.sys  = the_system
        self.spot = the_system.Analyses.New_Analysis(
            ZOSAPI.Analysis.AnalysisIDM.StandardSpot)
        s = self.spot.GetSettings()
        s.ReferTo = ZOSAPI.Analysis.Settings.Spot.Reference.Centroid
        the_system.MCE.SetCurrentConfiguration(config_index)

    def read_centroid(self, active_radius_mm):
        self.sys.UpdateStatus()
        self.spot.ApplyAndWaitForCompletion()
        sd = self.spot.GetResults().SpotData

        c   = np.array([sd.GetReferenceCoordinate_X_For(1, 1),
                        sd.GetReferenceCoordinate_Y_For(1, 1)])
        rms = sd.GetRMSSpotSizeFor(1, 1)

        if rms == 0.0 and c[0] == 0.0 and c[1] == 0.0:
            return None

        if np.sqrt(c[0]**2 + c[1]**2) > active_radius_mm:
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


def main(cfg):
    df_field = pd.read_csv(cfg["csv_field"])
    df_tilts = pd.read_csv(cfg["csv_tilts"])

    assert len(df_field) == len(df_tilts), (
        f"Row count mismatch: field CSV has {len(df_field)} rows, "
        f"tilt CSV has {len(df_tilts)} rows.")

    K          = len(df_field)
    field_x    = df_field["field_x_transmission"].to_numpy()
    field_y    = df_field["field_y_transmission"].to_numpy()
    tilt_x_arr = df_tilts["tilt_x_deg"].to_numpy()
    tilt_y_arr = df_tilts["tilt_y_deg"].to_numpy()

    app, ZOSAPI = connect()
    the_system  = app.PrimarySystem

    os.makedirs(cfg["out_dir"], exist_ok=True)
    out_file = os.path.join(
        cfg["out_dir"],
        f'apd_seq_data_{datetime.now().strftime("%Y%m%d_%H%M")}.csv')

    results = {}

    for label, zmx_path in cfg["zmx_files"].items():
        print(f"\n── Tracing {label}: {zmx_path}")

        the_system.LoadFile(zmx_path, False)
        the_system.SystemData.Fields.Type = ZOSAPI.SystemData.FieldType.Angle
        field1 = the_system.SystemData.Fields.GetField(1)

        ensure_return_cb_fixed(the_system, ZOSAPI, cfg["fsm_return_cb"])
        fsm = ZemaxFSM(the_system, ZOSAPI, cfg["fsm_surface"])
        det = ZemaxDetector(the_system, ZOSAPI, cfg["config_index"])

        xc_arr = np.full(K, np.nan)
        yc_arr = np.full(K, np.nan)

        for k in range(K):
            field1.X = float(field_x[k])
            field1.Y = float(field_y[k])
            fsm.set_angles(tilt_x_arr[k], tilt_y_arr[k])

            c = det.read_centroid(cfg["active_radius_mm"])

            if c is None:
                print(f"  {label} k={k:3d}  "
                      f"field=({field_x[k]:+.4f}°, {field_y[k]:+.4f}°)  "
                      f"tilt=({tilt_x_arr[k]:+.4f}°, {tilt_y_arr[k]:+.4f}°)  "
                      f"OFF DETECTOR")
            else:
                xc_arr[k], yc_arr[k] = c
                print(f"  {label} k={k:3d}  "
                      f"field=({field_x[k]:+.4f}°, {field_y[k]:+.4f}°)  "
                      f"tilt=({tilt_x_arr[k]:+.4f}°, {tilt_y_arr[k]:+.4f}°)  "
                      f"xC={xc_arr[k]:+.6f}  yC={yc_arr[k]:+.6f}")

        det.close()
        results[label] = (xc_arr, yc_arr)

    app.CloseApplication()

    out = pd.DataFrame({
        "field_x_transmission": field_x,
        "field_y_transmission": field_y,
        "tilt_x_deg":           tilt_x_arr,
        "tilt_y_deg":           tilt_y_arr,
    })
    for label, (xc_arr, yc_arr) in results.items():
        out[f"xC_{label}"] = xc_arr
        out[f"yC_{label}"] = yc_arr

    out.to_csv(out_file, index=False)
    print(f"\nDone. CSV written to: {out_file}")


if __name__ == "__main__":
    main(CONFIG)
"""
Mock FSM closed-loop tracking for singular APD, sequential ZEMAX (ZOS-API)
Calibrates by finding where the spot naturally lands at field (0, 0),
uses that as a centroid target, then PIDs to hold it there.
"""

import os
import winreg # to find where zemax is installed 
import clr
import numpy as np
import pandas as pd
from datetime import datetime
from FSM_LOOP_APD import PID2D, ZemaxFSM, ZemaxDetector


################
CONFIG = {
    'zmx_files': {
        'fsm': r'C:\Users\PulseA\Desktop\Ground Station\uzi\source_fsm_apd.zmx'
        # if running this on my pc at home, change path to respective path
        # 'fsm': r:'C:\Users\uzi\UCSP\PAT\source_fsm_apd.zmx'
    },
    "csv_in":  r"C:\Users\PulseA\Downloads\take2_alpha_beta.csv",
    "out_dir": r"C:\Users\PulseA\Desktop\Ground Station\ZOS-API",

    'fsm_surface': 3, 
    'fsm_return_cb': 0, # change this as well

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

'''
Issue is that it tilts to mechanical limit each time, not correcting just going
to mechanical limit.
'''
# the coordinate breaks are dummy surfaces that only reposition axes, no aperature



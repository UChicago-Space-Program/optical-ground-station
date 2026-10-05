from datetime import datetime
import pickle
import numpy as np
from astropy.coordinates import EarthLocation, get_body, GCRS
from astropy.time import Time
from scipy.interpolate import CubicSpline
from OGS_control_methods import OGS_control,Schedule_gen


#--- Constants ---
CHICAGO = EarthLocation(lat=41.868, lon=-87.648)
format_string = "%H:%M:%S/%d:%m:%Y" #Format for datetime parsing

time_start_abs_str="20:14:00/12:12:2026" #UTC time string for start of pass
time_start_abs= datetime.strptime(time_start_abs_str, format_string).timestamp()
time_end_abs_str="20:20/':00/12:12:2026" \
"" #UTC time string for end of pass
time_end_abs=datetime.strptime(time_end_abs_str, format_string).timestamp()


# --- CONFIG ---
TARGET = 'jupiter'
PICKLE_FILE = 'jupiter_pass.pickle'
PID_gains=[1.5,0,0]
PORT='/dev/tty.PL2303G-USBtoUART110'


# --- DATA PREPARATION ---
with open(PICKLE_FILE, 'rb') as f:
    data = pickle.load(f)

t_sec = data['time_delta'] * 86400 #Put times seconds from days
file_time_start=data['zeropoint'] # Unix time of start of pass file, corresponds to the real time of time_delta=0
path = CubicSpline(t_sec, np.stack((data['azm'], data['alt']), axis=1)) #Create path function
#Before interpolating, get the relative times in seconds
t0_relative=time_start_abs-file_time_start
tf_relative=time_end_abs-file_time_start
# Create the schedule Object
builder = Schedule_gen(t0=t0_relative, tf=tf_relative, t_step=1.0, function=path)
schedule_dict = builder.put_together()

# --- CONTROLLER INITIALIZATION AND EXECUTION ---
def main():
    #Create controller object
    controller = OGS_control(port=PORT, location=CHICAGO, file_start_time=data['zeropoint'])
    #
    controller.set_time_to_now()
    #Align the RA/DEC poiting model on the telescope
    print(f"Alignment: Centering {TARGET}...")
    input("Press Enter when target is centered to Sync and Start Tracking.")

    # Get postion of the target at current time
    obs_time_now = Time.now()
    target_coords = get_body(TARGET, obs_time_now)
    frame_apparent = GCRS(obstime=obs_time_now)
    target_apparent_frame=target_coords.transform_to(frame_apparent)
    target_ra_t0 = target_apparent_frame.ra.degree
    target_dec_t0 = target_apparent_frame.dec.degree
    #Now sync to this position

    controller.align_ra_dec(target_ra_t0, target_dec_t0)
    print(f"Syncing to RA: {target_ra_t0:.4f}, DEC: {target_dec_t0:.4f}")

    #Now, follow the schedule with PID control
    controller.follow_schedule(schedule_dict, PID_gains)
    
    #When done, save the log
    timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"{TARGET}_pass_{timestamp_str}.csv"

    controller.save_log(filename)
    print(f"Log saved as: {filename}")



#Execute it 
if __name__ == "__main__":
    main()
#This bit of code will contain the handling of the serial connection, math for astronomical calculations and the loop to control the telescope. Another file will run this code.


import serial
import time
import csv
import numpy as np
from astropy.time import Time
from astropy.coordinates import SkyCoord, AltAz
import astropy.units as u
import nexstar_protocol as protocol

class Schedule_gen():
    def __init__(self,t0,tf,t_step,function):
        #Make time array
        step_num=np.floor((tf-t0)/t_step)
        self.time_array=t0+t_step*np.arange(step_num)
        self.time_step=t_step
        self.evaluate_position=function
        self.tf=tf



    def get_deriv_slope(self):
        '''
        Gets derivates by just calculationg a slope between points, super simple. 
        '''
        int_pos_array=np.append(self.time_array,self.tf)
        int_positions=self.evaluate_position(int_pos_array)
        deltas=int_positions[1:]-int_positions[:-1]
        deltas[:-1]=np.copy(deltas[:-1])/self.time_step
        deltas[-1]=deltas[-1]/(self.tf-self.time_array[-1])
        return(deltas)
    def put_together(self):
        sched=dict()
        sched["time_step"]=self.time_step
        sched["time_array"]=self.time_array
        sched["int_pos_time_array"]=np.append(self.time_array,self.tf) 
        sched["int_positions"]=self.evaluate_position(sched["int_pos_time_array"])
        sched["slew_rates"]= self.get_deriv_slope()
        sched["func"]=self.evaluate_position
        return(sched)

class OGS_control:
    def __init__(self, port, location, file_start_time, verbose=True):
        # Initialize hardware connection
        self.ser = serial.Serial(port=port, baudrate=9600, timeout=1)
        self.location = location
        self.file_start_time = file_start_time
        self.verbose = verbose
        #Get the runtime of measuring the position
        #self.measure_runtime_of_get_coords()
        self.record_state_time = 0.05
        self.sleep_time = 0.1 #This is the amount of time the telescope sleeps between position checks while waiting for the next scheduled time, should be 
        #shorter than the step time - (the time to record the state of the telescope + the inconsistency is time.sleep) 
        self.slew_rates = list((0,0)) # [ALT, AZM]
        self.current_cmd_rates = [0,0] #This will track what slew rates are currently being commanded/executed
        self.log(f"Connected to telescope on {port}")
        self.history = [] #This is what we log things to, this will record the state of the telescope at each scheduled time interval or when we are waiting
    def set_time_to_now(self):
        """
        Sets the telescope's time to the computer's current local time.
        """
        import datetime
        import time

        now = datetime.datetime.now()
        local_time_info = time.localtime()
        
        # Get local time zone offset from UTC (in seconds), *including* DST
        # tm_gmtoff is the offset in seconds west of UTC
        gmt_offset_seconds = local_time_info.tm_gmtoff
        gmt_offset_hours = gmt_offset_seconds / 3600.0 # e.g., -5.0 for CDT, -6.0 for CST
      
        # W is the offset from GMT. If negative, use 256-zone [cite: 76]
        if gmt_offset_hours < 0:
            gmt_offset_byte = 256 + int(gmt_offset_hours) # e.g., 256 - 6 = 250
        else:
            gmt_offset_byte = int(gmt_offset_hours)
            
        # X is 1 for DST, 0 for Standard Time [cite: 77]
        # local_time_info.tm_isdst will be 1 if DST is active, 0 if not
        dst_byte = local_time_info.tm_isdst

        # V is the year (century assumed as 20) [cite: 75]
        year_byte = now.year % 100

        # Construct the 8-byte payload [cite: 69]
        payload = bytes([
            now.hour,           # Q: hour (24 clock) [cite: 70]
            now.minute,         # R: minutes [cite: 71]
            now.second,         # S: seconds [cite: 72]
            now.month,          # T: month [cite: 73]
            now.day,            # U: day [cite: 74]
            year_byte,          # V: year (e.g., 25 for 2025) [cite: 75]
            gmt_offset_byte,    # W: offset from GMT [cite: 76]
            dst_byte            # X: 1 for DST, 0 for Standard [cite: 77]
        ])

        # PC Command is "H" & chr(Q)...chr(X)
        command = b'H' + payload

        print(f"Setting time. Command: {command}")
        response = self.nexstar_command_and_read_until(command, stop_char=b'#')
        print(f"Time set response: {response}")
    #Two methods to go between hex and decimal numbers
    def hex_to_dec(self,s):
        i = int(s, 16)
        return(i)
    #def dec_to_hex(self,s):
    #    i = hex(s)
    #    return(i)
    def dec_to_hex(self, s):
    # 1. hex(int(s)) -> '0x4da99b30'
    # 2. [2:] -> '4da99b30' (strips the 0x)
    # 3. .upper() -> '4DA99B30' (NexStar prefers uppercase, though lowercase often works)
    # 4. .zfill(8) -> ensures it is exactly 8 characters by adding leading zeros
        return hex(int(s))[2:].upper().zfill(8)
    def log(self, message):
        """Toggleable debug printer. """
        if self.verbose:
            print(f"[LOG {time.strftime('%H:%M:%S')}]: {message}")

    def save_log(self,filename):
        """Saves a log messages to a file.This includes snapshots of all states of the telescope at each scheduled time interval."""
        with open(filename, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.history[0].keys())
            writer.writeheader()
            writer.writerows(self.history)
        print(f"Pass data saved to {filename}")

    def nexstar_command_and_read_until(self, command,stop_char=b'#'):
        """
        Sends a command to the telescope and reads the telescope output until a specified stop character is encountered.
        
        :param command: Command to send to the telescope in bytes, usually as an a variable imported from the nexstar_protocol.py file
        :param stop_char: Character to wait for in the telescope's response before stopping the read. Default is b'#'.
        """
        self.ser.write(command)
        return self.ser.read_until(stop_char) 

    def stop(self):
        """
        Stops all slewing in both coordinates.
        """
        self.nexstar_command_and_read_until(protocol.SLEW_AZM_STOP)
        self.nexstar_command_and_read_until(protocol.SLEW_ALT_STOP)
        self.log("Slew stopped.")

    def align_ra_dec(self,actual_ra,actual_dec):  
        '''
        This bit of code aligns the telescope's internal pointing model to the specified RA and Dec coordinates.
        The input coordinates should be in decimal degrees.
        '''
        #Max rotation, maximum decimal number stotred in 4 hex numbers, represents one full rotation
        ra_hex=self.dec_to_hex(round((actual_ra/360)*protocol.ROT_PRECISE))
        dec_hex=self.dec_to_hex(round((actual_dec/360)*protocol.ROT_PRECISE))
        self.nexstar_command_and_read_until(protocol.sync_precise_RA_DEC(ra_hex,dec_hex) ,b"#")
        self.log(f"Sent {(ra_hex)} + ',' + {(dec_hex)}")
        self.log(f"Aligned current position to ra={actual_ra*(24/360)} hours and dec={actual_dec}")

    def get_radec_degrees(self):
        '''
        This bit of code gets the current pointing angular coordinates from the telescope, according to its own pointing model.  The telescope will return coordinates that 
        represent angles greater than 360. Since the code is currently written to only work with angles between 0-360, so we have the output 
        go through a mod 360 to get it in that range. 
        '''
        #Gets the bytestring from the telescope
        out_bstring=self.nexstar_command_and_read_until(protocol.getRA_DEC_PRECISE ,b"#")

        #Turn it into a regular string
        out_string=out_bstring.decode()
        #Split the string into RA and Dec hex strings
        try:
            ra_s,dec_s=(out_string.replace("#","")).split(",")
        except:
            print("Error here",out_string)
        #Convert the hex strings into decimal degrees
        ra,dec=360*self.hex_to_dec(ra_s)/protocol.ROT_PRECISE , 360*self.hex_to_dec(dec_s)/protocol.ROT_PRECISE
        #Put return angle between 0 and 360 degrees
       
        
        ra= ra % 360
        if dec >90 and dec < 180:
            print("Not good dec value from telescope:",dec)
        elif dec >180:
            dec=dec-360
        
      
        return(ra,dec)


    def get_azm_alt(self):
        """
        This bit of code gets azm and alt by first getting the RA and DEc form the telescope according to its own pointing model,
        and then converts those into azm and alt using astropy. 
        """
        #Get RA and Dec from telescope pointing model
        ra, dec = self.get_radec_degrees()

        #Now create a skyccord object to turn these into alt and azm
        pointing_icrs = SkyCoord(ra=ra*u.deg, dec=dec*u.deg, frame='icrs')
        #Use the current time and location to get the altaz coordinates
        current_time_utc = Time.now()
        OGS_frame = AltAz(obstime=current_time_utc, location=self.location)
        pointing_model_altaz = pointing_icrs.transform_to(OGS_frame)

        return pointing_model_altaz.az.degree, pointing_model_altaz.alt.degree
    
    #For recording the state of the telescope whenever we want, create a record_State function
    def record_state(self,measure_position=True,input_az=None,input_alt=None):
        """Greedy measurement: Captures the state as fast as serial allows. Only input positions if measure_position is False."""
        current_relative_time = time.time() - self.file_start_time
        if measure_position:
            az, alt = self.get_azm_alt()
        else:
            az,alt=input_az,input_alt
        self.history.append({
            'time': current_relative_time,
            'az': az, 'alt': alt,
            'cmd_rate_az': self.current_cmd_rates[1],
            'cmd_rate_alt': self.current_cmd_rates[0]
        })

    def measure_runtime_of_get_coords(self):
        """
        This function measures how long it takes to get the azm and alt coordinates from the telescope
        """
        N=10
        time_list=list()
        for a in np.arange(N):
            iter_start_time=time.time()
            (azm_s,alt_z)=self.get_azmalt()
            dt=time.time()-iter_start_time
            time_list.append(dt)
        self.record_state_time=np.mean(time_list)
    def follow_schedule(self,schedule,PID_gains):

        """
        Takes in a schedule dict as built up by the Schedule_gen class and follows said schedule, doing measured waits at the time intervals
        and returning the recorded positions.

        self.proportional_gain: Multiplied with the error angle to give a corrective adjustment. Set to 0 for no correction.
        self.integral_gain: Multiplied with the integral of error angles to give a corrective adjustment. Set to 0 for no correction.
        self.derivative_gain: Multiplied by the slope of the error to give a corrective adjustment. Set to 0 for no correction.
        """
        self.func=schedule["func"]
        #
        self.proportional_gain=PID_gains[0]
        self.integral_gain=PID_gains[1]
        self.derivative_gain=PID_gains[2]
    
        #
        self.log("Commencing Tracking...")
        self.history = []
        
        for Index,target_time in enumerate(schedule["time_array"]):
            #Wait until the scheduled time, LHS is real_time-file_start_time 
            while (time.time()-self.file_start_time+self.record_state_time)<target_time:
                #Record the state of the telecope if there is still time before the next scheduled time
                if (time.time()-self.file_start_time+2*self.record_state_time+self.sleep_time)<target_time:
                    self.record_state() 
                    time.sleep(self.sleep_time) #Sleep for a short bit to avoid busy-waiting
                #if there is not enough time to record the state, just busy-wait
                else:
                    pass
            #By this point, we are at exactly the scheduled time, so calculate the error and figure out the correct signal to send out
            #Calculatinng error
            intended_angle=schedule["int_positions"][Index]
            cur_az, cur_alt = self.get_azm_alt()
            # Calculate PID correction
            err = np.array([(intended_angle[0] - cur_az) * 3600, (intended_angle[1] - cur_alt) * 3600])
            #Compute control signal 
            control_signal=self.proportional_gain*err
            intended_slr= 3600*schedule["slew_rates"][Index]

            #Check if the slew rate + correction is greater than max slew rate
            if abs(intended_slr[0]+control_signal[0])<protocol.max_slew_rate:
                az_rate=intended_slr[0]+control_signal[0]
            else:
                #If it is, then just go at maximum slew rate
                sig=int(np.sign(intended_slr[0]+control_signal[0]))
                az_rate=protocol.max_slew_rate*sig
            #Same thing but for altitude 
            if abs(intended_slr[1]+control_signal[1])<protocol.max_slew_rate:
                alt_rate=intended_slr[1]+control_signal[1]
            else:
                sig=int(np.sign(intended_slr[1]+control_signal[1]))
                alt_rate=protocol.max_slew_rate*sig

            #Send out the slew commands
            self.nexstar_command_and_read_until(protocol.slewAZM_var(az_rate),b"#")
            self.nexstar_command_and_read_until(protocol.slewALT_var(alt_rate),b"#")
            # Update internal state and record the state of the telescope
            self.current_cmd_rates = [alt_rate, az_rate]
            self.record_state(measure_position=False,input_az=cur_az,input_alt=cur_alt,)
            self.log(f"CMD issued at T={target_time:.2f}s | Err: {err[0]:.1f}\", {err[1]:.1f}\"")
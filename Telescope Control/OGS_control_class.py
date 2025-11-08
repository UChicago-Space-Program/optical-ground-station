import nexstar
import serial
import time
import numpy as np
import matplotlib.pyplot as plt
#from scipy.differentiate import derivative
import numdifftools as nd
max_slew_rate=5*60*60
def two_comp_derivative(func, t_array):
  f1=lambda t: func(t)[:,0]
  f2=lambda t: func(t)[:,1]
  df1=nd.Derivative(f1,n=1)
  df2=nd.Derivative(f2,n=1)
  return np.stack((df1(t_array), df2(t_array)),axis=1)
def deg_to_arcsec(deg):
    arcsec=(60**2)*deg
    return(arcsec)

class Schedule_gen():
    def __init__(self,t0,tf,t_step,function):
        #Make time array
        step_num=np.floor((tf-t0)/t_step)
        self.time_array=t0+t_step*np.arange(step_num)
        self.time_step=t_step
        self.evaluate_position=function
        self.tf=tf
        self.t0=t0
    
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
        sched["time_array"]=self.time_array-self.t0
        sched["int_pos_time_array"]=np.append(self.time_array,self.tf) #Removed a -self.t0 here, come back to this is everyhting breaks
        sched["int_positions"]=self.evaluate_position(sched["int_pos_time_array"])
        sched["slew_rates"]= self.get_deriv_slope()
        sched["func"]=self.evaluate_position
        return(sched)

class OGS_control:
    def __init__(self,name,t0=0):
        self.t0=t0
        self.path_name=name
        self.global_start_time=time.time()
        self.data_interval=0.1
        self.internal_zero_aligned=False
        #Initialize slew rates object
        self.slew_rates=list((0,0))
        #Initialize the serial connection
        self.ser = serial.Serial(
        port='/dev/tty.PL2303G-USBtoUART110',
        baudrate=9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=1)
        
        self.get_azm_alt_runtime=self.measure_runtime_of_get_coords()
        #test connection
        #print('connected:', self.ser.name)

    #Two methods to go between hex and decimal numbers
    def hex_to_dec(self,s):
        i = int(s, 16)
        return(i)
    def dec_to_hex(self,s):
        i = hex(s)
        return(i)
    #Reads serial data from telescope, this is a read until command, so it will wait until the stop character is sent
    # by the telescope. In this case, we usually wait for a # to be sent. (Note that it has to be # in bytes, so b'#')
    def nexstarComm_read_until(self,command,stop_char):
        self.ser.write(command)
        s = self.ser.read_until(stop_char)
        return(s)
    
        
    #Stops slewing
    def Stop(self):
        self.nexstarComm_read_until(nexstar.slewAZM_STOP,b'#')
        self.nexstarComm_read_until(nexstar.slewALT_STOP,b'#')
    
    def get_azmalt_degrees(self):
        '''
        This bit of code gets the current pointing angular coordinates from the telescope. The telescope will return coordinates that 
        represent angles greater than 360. Since the code is currently written to only work with angles between 0-360, so we have the output 
        go through a mod 360 to get it in that range. 
        '''
        #Max rotation, maximum decimal number stotred in 4 hex numbers, represents one full rotation
        self.rot=65535 #Unprecise
        self.rot=4294967296 #Precise

        #Gets the bytestring non precise version
        #out_bstring=self.nexstarComm_read_until(nexstar.getAZM_ALT ,b"#")
        #Or the precise version getAZM_ALT_PRECISE
        out_bstring=self.nexstarComm_read_until(nexstar.getAZM_ALT_PRECISE ,b"#")
        #print(out_bstring)
        #Turn it into a regular string
        out_string=out_bstring.decode()
        try:
            az_s,alt_s=out_string.split(",")
        except:
            print("Error here",out_string)
        alt_s,az_s=alt_s.replace("#","") , az_s.replace("#","")
        alt,azm=360*self.hex_to_dec(alt_s)/self.rot , 360*self.hex_to_dec(az_s)/self.rot
        #Put returned angle between 0 and 360 degrees
        
        alt= alt % 360
        azm= azm % 360
        if not self.internal_zero_aligned:
            return(azm % 360,alt % 360)
        else:
            return((azm-self.zero_azm)% 360,(alt-self.zero_alt)% 360)
    
    def get_radec_degrees(self):
        '''
        This bit of code gets the current pointing angular coordinates from the telescope. The telescope will return coordinates that 
        represent angles greater than 360. Since the code is currently written to only work with angles between 0-360, so we have the output 
        go through a mod 360 to get it in that range. 
        '''
        #Gets the bytestring
        out_bstring=self.nexstarComm_read_until(nexstar.getRA_DEC ,b"#")
        print(f"received:{out_bstring}")
        #Turn it into a regular string
        out_string=out_bstring.decode()
        try:
            ra_s,dec_s=out_string.split(",")
        except:
            print("Error here",out_string)
        ra_s,dec_s=ra_s.replace("#","") , dec_s.replace("#","")
        ra,dec=360*self.hex_to_dec(ra_s)/self.rot , 360*self.hex_to_dec(dec_s)/self.rot
        #Put return angle between 0 and 360 degrees
       
        
        ra= ra % 360
        dec= dec % 360
      
        return(ra,dec)
    
    def is_align_complete(self):
        """
        Checks if alignment is complete on the telescope.
        """
        out_bstring=self.nexstarComm_read_until(nexstar.isAlignmentComplete ,b"#")
        print(out_bstring)
    def horizon_north_align(self,alt_offset=0):
        '''
        The way this command is meant to be used is that the telescope should be pointing roughly at plevel and northbound. At
        which point this command should be run, which will align the telescope. Its a quick an easy way to get rough alignment for testing of 
        tracking for the OGS. 
        '''
      
        self.zero_azm,self.zero_alt=self.get_azmalt_degrees()
        self.zero_alt-=alt_offset
        print(self.zero_azm,self.zero_alt)
        self.internal_zero_aligned=True
        print("Alignment attempted")
    
    def align_azm_alt(self,actual_azm,actual_alt):  
        '''
        '''  
        cur_azm,cur_alt=self.get_azmalt_degrees()
        self.zero_azm=cur_azm-actual_azm
        self.zero_alt=cur_alt-actual_alt
        #print(self.zero_azm,self.zero_alt)
        self.internal_zero_aligned=True
        print("Alignment attempted")


    def align_ra_dec(self,actual_ra,actual_dec):  
        '''
        '''
        #Max rotation, maximum decimal number stotred in 4 hex numbers, represents one full rotation
        ra_hex=self.dec_to_hex(round((actual_ra/360)*self.rot))
        dec_hex=self.dec_to_hex(round((actual_dec/360)*self.rot))
        self.nexstarComm_read_until(nexstar.sync_RA_DEC(ra_hex,dec_hex) ,b"#")
        print(f"Sent {(ra_hex)} + ',' + {(dec_hex)}")
        print(f"Algined current position to ra={actual_ra*(24/360)} hours and dec={actual_dec}")
              
    def measure_runtime_of_get_coords(self):
        N=10
        time_list=list()
        for a in np.arange(N):
            iter_start_time=time.time()
            (azm_s,alt_z)=self.get_azmalt_degrees()
            dt=time.time()-iter_start_time
            time_list.append(dt)
        #plt.plot(np.arange(len(time_list)),time_list)
        #plt.show()
      
        return(np.average(time_list))
    def measure_runtime_of_sending_slew_rates(self):
        N=100
        time_list=list()
        alt_slr=np.linspace(0,7200,N)
        azm_slr=np.linspace(0,-7200,N)
        slr_list=np.stack((alt_slr,azm_slr),axis=1)
        
        for a in np.arange(N):
            iter_start_time=time.time()
            alt_slr,az_slr=slr_list[a]
            self.var_AZM_slew(az_slr)
            self.var_ALT_slew(alt_slr)
            dt=time.time()-iter_start_time
            time_list.append(dt)
        self.Stop()
        plt.plot(np.arange(len(time_list)),time_list,'bs')
        plt.show()
        return(np.average(time_list))

    def measure(self,azm_alt_list):

        (azm_s,alt_z)=self.get_azmalt_degrees()
        #print(f"Measured: {azm_s},{alt_z}")
        dt=time.time()-self.global_start_time
        azm_alt_list.append([dt,azm_s,alt_z,self.slew_rates[0],self.slew_rates[1]])
        return(azm_alt_list)
        """
        These two functions are defined as below so that when we send new slew rates, we also update self.slew_rates,
        which lets me append to the data list the commanded slew rates
        """
    def var_AZM_slew(self,rate):
        self.nexstarComm_read_until(nexstar.slewAZM_var(rate),b'#')
        self.slew_rates[1]=rate
    def var_ALT_slew(self,rate):
        self.nexstarComm_read_until(nexstar.slewALT_var(rate),b'#')
        self.slew_rates[0]=rate
    #This might be flipped, watch out!
    def Go_to_AZM_ALT_sat(self,azm,alt):

        alt_frac=alt/360
        azm_frac=azm/360
        alt_out=hex(round(self.rot*alt_frac))
        azm_out=hex(round(self.rot*azm_frac))
        self.nexstarComm_read_until(nexstar.gotoAZM_ALT(azm_out,alt_out),b'#')

    def Go_To_azm_alt(self,target_azmalt,prec=10):
        """
        Go to function that uses only structure from this file. 
        """
        maxrate=2*3600/3600
        #Assume a=4deg/s^2
        a=4.  #deg/s^2
        #determne max angle of accelerating domain
        theta_accel=0.5*((maxrate/3600)**2)/a
        accel_domain_time=(maxrate/3600)/a
        #Get angular difference
        (cur_azm,cur_alt)=self.get_azmalt_degrees()
        curazmalt=np.array([cur_azm,cur_alt])
        difs=target_azmalt-curazmalt
        argmax=np.argmax(difs)
        #Figure out if we are inside or outside accelerating domain in both dimensions
        accel_domain= abs(difs)<theta_accel
        #Get times to stop in each dimension
        slew_time=np.zeros(2)
        slew_time[accel_domain]=np.sqrt(np.abs((2*difs[accel_domain])/a))
        #We use 2*theta_accel to account for movement while decelerating
        slew_time[~accel_domain]=accel_domain_time + (np.abs(difs[~accel_domain])-2*theta_accel)/maxrate

        #Actually slew!
        #Setup altaz list
        m_azm_alt_list=list()
         
        #Get initial time
        goto_start_time=time.time()
        self.var_AZM_slew(maxrate*3600*np.sign(difs[0]))
        self.var_ALT_slew(maxrate*3600*np.sign(difs[1]))
        self.slew_rates=np.array([maxrate,maxrate])
        first=True
 
        while time.time()-goto_start_time<slew_time.max():
            if first:
                bool_arr=time.time()-goto_start_time<slew_time
                first=False
            nbool_arr=time.time()-goto_start_time<slew_time
            #Detect if we reached any stop times
            if np.array_equiv(nbool_arr,bool_arr):
                self.measure(m_azm_alt_list)
                continue
            #If we have, send new slew rates
            int_arr=nbool_arr.astype(int)
            self.var_AZM_slew(int_arr[0]*maxrate*3600*np.sign(difs[0]))
            self.var_ALT_slew(int_arr[1]*maxrate*3600*np.sign(difs[1]))
            self.slew_rates=3600*np.sign(difs)*int_arr*np.array([maxrate,maxrate])
            bool_arr=nbool_arr
        self.Stop()
        self.slew_rates=np.array([0,0])
        #Wait for things to settle down
        wait_time_start=time.time()
        wait_time=2
        while time.time()<wait_time_start+wait_time:
            self.measure(m_azm_alt_list)
        #Now, calibrate
        #We should be completely inside the acceleration domain here
        #Get difs
        (cur_azm,cur_alt)=self.get_azmalt_degrees()
        curazmalt=np.array([cur_azm,cur_alt])
        difs=target_azmalt-curazmalt
        while difs.max()>prec:
            #Get difs
            (cur_azm,cur_alt)=self.get_azmalt_degrees()
            curazmalt=np.array([cur_azm,cur_alt])
            difs=target_azmalt-curazmalt #in degrees
            self.var_AZM_slew(difs[0]*3600)
            self.var_ALT_slew(difs[1]*3600)
            self.slew_rates=difs*3600
            self.measure(m_azm_alt_list)
        
        return(m_azm_alt_list)
            


    def follow_schedule(self,schedule):
        self.global_start_time=time.time()
        #Initialoze error integral and previous error integral variables, used to calculate Intergral and Derivative portions of PID
        self.error_integral=0
        self.previous_error=0
        """
        Takes in a schedule dict as built up by the Schedule_gen class and follows said schedule, doing measured waits at the time intervals
        and returning the recorded positions.

        self.proportional_gain: Multiplied with the error angle to give a corrective adjustment. Set to 0 for no correction.
        self.integral_gain: Multiplied with the integral of error angles to give a corrective adjustment. Set to 0 for no correction.
        self.derivative_gain: Multiplied by the slope of the error to give a corrective adjustment. Set to 0 for no correction.
        """

        #Setup altaz list
        m_azm_alt_list=list()
        for time_ind in np.arange(len(schedule["time_array"])):
            
            if time_ind!=0:
                dv=schedule["slew_rates"][time_ind]-schedule["slew_rates"][time_ind-1]
                #print('dv:',dv)
            while (time.time()-self.global_start_time)+self.get_azm_alt_runtime+0.05 <schedule["time_array"][time_ind]:
                self.measure(m_azm_alt_list)
                #Little bit of code introduced to prevent 100% CPU utilization
                time.sleep(0.05)
                #
                pass
            
            #Set the slew rates
            dt=time.time()-self.global_start_time
            az_slr,alt_slr,=deg_to_arcsec(schedule["slew_rates"][time_ind])
            #add slew rate correction 
            #if False:
            if len(m_azm_alt_list)>0:
                cur_time,cur_azm,cur_alt=m_azm_alt_list[-1][0],m_azm_alt_list[-1][1],m_azm_alt_list[-1][2]
                #Compute the angular error in each coordinate
                azm_err=deg_to_arcsec(self.func(cur_time+self.t0)[0]-cur_azm)
                alt_err=deg_to_arcsec(self.func(cur_time+self.t0)[1]-cur_alt)
                #Compute control signal 
                control_signal_azm=self.proportional_gain*azm_err
                control_signal_alt=self.proportional_gain*alt_err
                print("Measured",cur_azm,cur_alt)
                print("Control signal in each coordinate",control_signal_azm,control_signal_alt)

            else:
                control_signal_azm=0
                control_signal_alt=0
            #Check if the slew rate + correction is greater than max slew rate
            if abs(az_slr+control_signal_azm)<max_slew_rate:
                self.var_AZM_slew(az_slr+control_signal_azm)
            else:
                #If it is, then just go at maximum slew rate
                sig=int(np.sign(az_slr+control_signal_azm))
                self.var_AZM_slew(max_slew_rate*sig)
            #Same thing but for altitude 
            if abs(alt_slr+control_signal_alt)<max_slew_rate:
                self.var_ALT_slew(alt_slr+control_signal_alt)
            else:
                sig=int(np.sign(az_slr+control_signal_alt))
                self.var_ALT_slew(max_slew_rate*sig)
            #Wait and save to list
        self.measure(m_azm_alt_list)
         
        return(m_azm_alt_list,schedule)
    def do_schedule_and_plot(self,schedule,c_factor):
        
        self.time_step=schedule["time_step"]
        self.proportional_gain=c_factor
        self.func=schedule["func"]
        
        data_list,schedule=self.follow_schedule(schedule)
        self.Stop()
        data_arr=np.array(data_list)
        print(data_list)

        #Plot path actually taken vs path intended
        times=self.t0+data_arr[:,0]
        azm_measured=data_arr[:,1]
        altitude_measured=data_arr[:,2]
        #
        fig,ax=plt.subplots(2,2,figsize=(12,8))
        fig.suptitle(f"Proportional Gain={self.proportional_gain}")
        #Azimuth
        ax[0,1].plot(times,azm_measured,'bs',label="Azimuth measured")
        ax[0,1].plot(schedule["int_pos_time_array"],schedule["int_positions"][:,0],'r',label="Azimuth intended")
        ax[0,1].legend()
        #Altitude
        ax[0,0].plot(times,altitude_measured,'bs',label="Altitude measured")
        ax[0,0].plot(schedule["int_pos_time_array"],schedule["int_positions"][:,1],'r',label="Altitude intended")
        ax[0,0].legend()
        #Labes
        ax[0,0].set_xlabel("Seconds")
        ax[0,1].set_xlabel("Seconds")
        ax[0,0].set_ylabel("Degrees")
        ax[0,1].set_ylabel("Degrees")
        ax[0,0].set_title("Altitude")
        ax[0,1].set_title("Azimuth")
        #Errors
        err_times=data_arr[:,0]
        err_alt=data_arr[:,2]-self.func(err_times+self.t0)[:,1]
        err_azm=data_arr[:,1]-self.func(err_times+self.t0)[:,0]
        ax[1,0].plot(err_times,err_alt,'r',label="Altitude error measured")
        RMS_alt=np.sqrt((1/len(err_times))*np.sum((err_alt)**2))
        ax[1,0].legend()
        #Azimuth
        ax[1,1].plot(err_times,err_azm,'r',label="Azimuth error measured")
        RMS_azm=np.sqrt((1/len(err_times))*np.sum((err_azm)**2))
        ax[1,1].legend()
        ax[1,0].set_xlabel("Seconds")
        ax[1,1].set_xlabel("Seconds")
        ax[1,0].set_ylabel("Degrees")
        ax[1,1].set_ylabel("Degrees")
        ax[1,0].set_title(f"Altitude error, RMS={RMS_alt} deg")
        ax[1,1].set_title(f"Azimuth error, RMS={RMS_azm} deg ")




        plt.tight_layout()
        plt.show()



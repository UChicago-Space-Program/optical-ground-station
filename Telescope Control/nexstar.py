

import serial
import serial.tools.list_ports
from datetime import datetime
import time






# control the mount
#need to search /dev
#likely not the same ID on pi
ser = serial.Serial(
    #need to search /dev for the correct port
    
    port='/dev/tty.PL2303G-USBtoUART110',
    baudrate=9600,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=1

)


#test connection
print('connected:', ser.name)

#class Nexstar(EventDispatcher):
class Nexstar():
    def __init__(self, **kwargs):
        self.register_event_type('on_test')
        super(Nexstar, self).__init__(**kwargs)

    def timeGetter():#will be used to update via setTime push if system vs. scanner time drifts beyond a set delta
    #     today=str(datetime.today())
        # d=str(date.time())
        t=str(datetime.now())
        # print("NOW:  ",t)
        x= t.split()
        dateNow= x[0]
        d=str(dateNow.split())
        # print(len(d))
        yearNow=d[4:6]
        # print("yearNow", yearNow)
        monthNow=d[7:9]
        # print("monthNow",monthNow)
        dayNow=d[10:12]
        # print("dayNow", dayNow)
        y= x[1]
        z=y.split(".")
        timeNow= z[0]
        v=str(timeNow.split())
        # print(len(v))
        hourNow=v[2:4]
        # print("hourNow", hourNow)
        minNow=v[5:7]
        # print("minNow",minNow)
        secNow=v[8:10]
        # print("secNow", secNow)

        return (hourNow,minNow,secNow,monthNow,dayNow,yearNow)

    #print(timeGetter())





    #GOTO Commands
    #need to figure out coordinate encoding to hex to pass into this
    #65536/360
    #65536/90

def gotoRA_DEC(RA,DEC):
    raDec=bytes('R'+ (RA) + ',' + (DEC),'utf-8')
    print("raDec inside gotoRA_DEC  :",  raDec)
    # time.sleep(4)
    return raDec

def gotoAZM_ALT(AZM,ALT):
    #Not that the order here is swithced
    azmAlt=bytes('B'+ (AZM) + ',' + (ALT),'utf-8')
    print("azmAlt inside gotoAZM_ALT  :",  AZM, ALT)
    # time.sleep(4)
    return azmAlt
def sync_RA_DEC(RA,DEC):
    align_command=bytes('S' + (RA) + ',' + (DEC),'utf-8')
    return align_command
    
#Get Position Commands
getRA_DEC = bytes('E','utf-8')
getRA_DEC_PRECISE = bytes('e','utf-8')
#This returns 
getAZM_ALT = bytes('Z','utf-8')
getAZM_ALT_PRECISE = bytes('z','utf-8')

#Tracking Commands
getTrackingMode = bytes('t','utf-8')

#Time/Location Commands
# Location:
# N 40.64600° W 78.09062°
# N40º 30’ 46”  W78º 05’ 24”
# Format= ABCDEFGH
# A=40
# B=30
# C=46
# D=0
# E=78
# F=5
# G=24
# H=1

#getLocation = bytes('w','utf-8')
#print("getLocation",getLocation)
# b"('\x00\x00K\x1a\x00\x01#"
# b'(\x1e.\x00N\x05\x18\x01#'
#setLocation = bytes('W'+ chr(40)+chr(30)+chr(46)+chr(0)+chr(78)+chr(5)+chr(24)+chr(1), 'utf-8')
#print("setLocation",setLocation)


#getTime = bytes('h','utf-8')

#H=Nexstar.timeGetter()
#print("H",H[0],H[1],H[2],H[3],H[4],H[5])
# Q=hour
# R=min
# S=sec
# T=month
# U=day
# V=year
# W=251 (offset from GMT. 256 - zone   -5UTC = 251)
# X=1 for Daylight Savings, 0 for Standard Time

#setTime = bytes('H'+
#                chr(int(H[0]))+
#                chr(int(H[1]))+
#                chr(int(H[2]))+
#                chr(int(H[3]))+
#                chr(int(H[4]))+
#                chr(int(H[5]))+
#                chr(251)+
#                chr(1),
#                'utf-8')
# print("setTime",setTime)
# print(nexstarComm(getTime))
# print("setTime set",nexstarComm(setTime))
# time.sleep(4)



#Miscellaneous Commands
getVersion = bytes('V', 'utf-8')
getDeviceVersion_AZ_RA_Motor = bytes('P'+ chr(1)+chr(16)+chr(254)+chr(0)+chr(0)+chr(0)+chr(2), 'utf-8')
getDeviceVersion_ALT_DEC_Motor = bytes('P'+ chr(1)+chr(17)+chr(254)+chr(0)+chr(0)+chr(0)+chr(2), 'utf-8')
getDeviceVersion_GPS = bytes('P'+ chr(1)+chr(176)+chr(254)+chr(0)+chr(0)+chr(0)+chr(2), 'utf-8')
getDeviceVersion_RTC = bytes('P'+ chr(1)+chr(178)+chr(254)+chr(0)+chr(0)+chr(0)+chr(2), 'utf-8')
getModel = bytes('m', 'utf-8')
# echoCheck = bytes('K'+chr(x), 'utf-8')
isAlignmentComplete = bytes('J', 'utf-8')
isGotoInProgress = bytes('L', 'utf-8')
cancelGoto = bytes('M', 'utf-8')


#Slewing Commands
#speed set in [4] (0-9)
slewAZM_Positive = bytes('P'+ chr(2)+chr(16)+chr(36)+chr(9)+chr(0)+chr(0)+chr(0), 'utf-8')
slewAZM_STOP = bytes('P'+ chr(2)+chr(16)+chr(36)+chr(0)+chr(0)+chr(0)+chr(0), 'utf-8')
slewAZM_Negative = bytes('P'+ chr(2)+chr(16)+chr(37)+chr(9)+chr(0)+chr(0)+chr(0), 'utf-8')

slewALT_Positive = bytes('P'+ chr(2)+chr(17)+chr(36)+chr(9)+chr(0)+chr(0)+chr(0), 'utf-8')
slewALT_STOP = bytes('P'+ chr(2)+chr(17)+chr(37)+chr(0)+chr(0)+chr(0)+chr(0), 'utf-8')
slewALT_Negative = bytes('P'+ chr(2)+chr(17)+chr(37)+chr(9)+chr(0)+chr(0)+chr(0), 'utf-8')

slewAZM_PositiveOne = bytes('P'+ chr(2)+chr(16)+chr(36)+chr(1)+chr(0)+chr(0)+chr(0), 'utf-8')
slewAZM_NegativeOne = bytes('P'+ chr(2)+chr(16)+chr(37)+chr(1)+chr(0)+chr(0)+chr(0), 'utf-8')

slewALT_PositiveOne = bytes('P'+ chr(2)+chr(17)+chr(36)+chr(1)+chr(0)+chr(0)+chr(0), 'utf-8')
slewALT_NegativeOne = bytes('P'+ chr(2)+chr(17)+chr(37)+chr(1)+chr(0)+chr(0)+chr(0), 'utf-8')


slewAZM_PositiveFive = bytes('P'+ chr(2)+chr(16)+chr(36)+chr(6)+chr(0)+chr(0)+chr(0), 'utf-8')
slewAZM_NegativeFive = bytes('P'+ chr(2)+chr(16)+chr(37)+chr(6)+chr(0)+chr(0)+chr(0), 'utf-8')

slewALT_PositiveFive = bytes('P'+ chr(2)+chr(17)+chr(36)+chr(6)+chr(0)+chr(0)+chr(0), 'utf-8')
slewALT_NegativeFive = bytes('P'+ chr(2)+chr(17)+chr(37)+chr(6)+chr(0)+chr(0)+chr(0), 'utf-8')

#Variable slewing commands
import numpy as np
def slewAZM_var(arcsec_rate):
    rate_mag=abs(arcsec_rate)
    trackRateHigh = int(np.floor((rate_mag * 4)/(256)))
    trackRateLow = int(np.floor((rate_mag * 4)%256))
    #If rate is negative, send command for slewing in the negative direction
    if arcsec_rate<0:
        #Negative slew direction
        dir=7
    else:
        #Positive slew direction
        dir=6
    return bytes('P'+chr(3)+chr(16)+chr(dir)+chr(trackRateHigh)+chr(trackRateLow)+chr(0)+chr(0),'utf-8')
def slewALT_var(arcsec_rate):
    rate_mag=abs(arcsec_rate)
    trackRateHigh = int(np.floor((rate_mag * 4)/(256)))
    trackRateLow = int(np.floor((rate_mag * 4)%256))
    #If rate is negative, send command for slewing in the negative direction
    if arcsec_rate<0:
        #Negative slew direction
        dir=7
    else:
        #Positive slew direction
        dir=6
    return bytes('P'+chr(3)+chr(17)+chr(dir)+chr(trackRateHigh)+chr(trackRateLow)+chr(0)+chr(0),'utf-8')


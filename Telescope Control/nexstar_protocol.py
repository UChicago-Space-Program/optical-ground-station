#This is the file that outlines the bit-string definitions for commands for the Celestron Nexstar protocol.
#It also includes some helper functions to convert from human readable formats to the bit-string formats needed for the commands.

import numpy as np

#
# ---   CONSTANTS  ---
#

# 32-bit precision divisor for 'precise' commands
ROT_PRECISE = 4294967296
#Max slewing rate in arcsec/sec
max_slew_rate=5*60*60
#Get Position Commands
getRA_DEC = bytes('E','utf-8')
getRA_DEC_PRECISE = bytes('e','utf-8')
getAZM_ALT = bytes('Z','utf-8')
getAZM_ALT_PRECISE = bytes('z','utf-8')

#Stop Commands
slewAZM_STOP = bytes('P'+ chr(2)+chr(16)+chr(36)+chr(0)+chr(0)+chr(0)+chr(0), 'utf-8')
slewALT_STOP = bytes('P'+ chr(2)+chr(17)+chr(37)+chr(0)+chr(0)+chr(0)+chr(0), 'utf-8')



#
# --- FUNCTIONS  ---
#

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


#GOTO Commands
#need to figure out coordinate encoding to hex to pass into this
#65536/360

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

#Sync Commands
def sync_RA_DEC(RA,DEC):
    align_command=bytes('S' + (RA) + ',' + (DEC),'utf-8')
    return align_command
def sync_precise_RA_DEC(RA,DEC):
    align_command=bytes('s' + (RA) + ',' + (DEC),'utf-8')
    return align_command
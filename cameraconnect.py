"""
The purpose of this code is to connect to a ZWO ASI camera.

This code was compiled by Ashley Ashiku, telescope engineer, for the PULSE-A project. This code utilizes Python wrappers written by Steve Marple, 2017 
(search python-zwoasi on GitHub) around C commands for ZWO ASI cameras from the software development kit.

Last updated July 9th, 2025

Correspondence/Questions:
aashiku@uchicago.edu
"""

# Imports needed 
import zwoasi as asi
import sys
import os
from astropy.io import fits
import time

# My attempt to suppress the weird error message "WARNING:root:ASI SDK library not found"
# It didn't work, so ignore this:
#import logging
#logging.getLogger().setLevel(logging.CRITICAL)

# Load ZWOASI SDK, the library of C commands that you downloaded. Replace the filepath to suite your own computer!
sdk_filename = './ASI_linux_mac_SDK_V1.37/lib/mac/libASICamera2.dylib'
asi.init(sdk_filename)

# Manually plug the ZWO camera into your own laptop using a USB connector and any converters necessary.

# This returns the number of connected cameras
print(f"Number of connected cameras: {asi.get_num_cameras()}")

# This lists the cameras connected and automatically connects to the first, since we are only connecting one
num_cameras = asi.get_num_cameras()
if num_cameras == 0:
    print('No cameras found')
    sys.exit(0)

cameras_found = asi.list_cameras()  # Models names of the connected cameras

if num_cameras == 1:
    camera_id = 0
    print('Found one camera: %s' % cameras_found[0])
else:
    print('Found %d cameras' % num_cameras)
    for n in range(num_cameras):
        print('    %d: %s' % (n, cameras_found[n]))
    # TO DO: allow user to select a camera
    camera_id = 0
    print('Using #%d: %s' % (camera_id, cameras_found[camera_id]))

# Establishing which camera we're using
camera = asi.Camera(camera_id)

# You're now connected to the ZWO camera! Next, we're printing out various camera properties so you see what we're working with
print("Getting camera property.")
cam_prop = camera.get_camera_property()

# I am most interested in gain and pixel size, so that's what I printed out specifically. You can specify any specific properties you want, or
# alternatively, just print all of them with:
#print(cam_prop)
print(f"Gain: {cam_prop['ElecPerADU']:2f}")
print(f"Pixel size: {cam_prop['PixelSize']}")

# You can also change certain settings if you please. I wrote a function in camerapicture.py to do that, so no need to do so now
# But use the following code if you want to now:
camera.set_control_value(asi.ASI_EXPOSURE, 15000000) # Here, keep in mind that the exposure time is input in microseconds (10 ^ -6 seconds)



# Great, that's it! Ignore everything below, this is code I was messing around with.


"""
print('Capturing a single 8-bit mono image')

    # where to save it
save_dir = "/Users/ashleyashiku/Desktop/PULSE-A/cameratest/"
os.makedirs(save_dir, exist_ok=True)

camera.set_image_type(asi.ASI_IMG_RAW8)
image = camera.capture()
print(f'image taken. trust me on it. ')

# specify image name
imagename =  "bigtest16"+ ".fits"
filename = os.path.join(save_dir,imagename)
fits.writeto(filename, image, overwrite=True)

print(f"image should now be saved in {filename}. go look!")
"""
#camera.stop_video_capture()

#print('Enabling video mode')

#time.sleep(5)
#camera.stop_video_capture()
#print("ended cam")
#chat = camera.get_video_data()

#if __name__ == "__main__":
  #  camconnect()
  #  image()


#print(asi._get_camera_property)

# getting all camera controls (from demo code)
"""
print('')
print('Camera controls:')
controls = camera.get_controls()
for cn in sorted(controls.keys()):
    print('    %s:' % cn)
    for k in sorted(controls[cn].keys()):
        print('        %s: %s' % (k, repr(controls[cn][k])))
"""



def exposureconvert(mine, status = False):
    """
    Silly little function to know exposure times. Camera works with exposure times in microseconds (10^-6). Default is
    you enter your time in seconds and function spits out the value in microseconds for you to enter. If status = True, 
    it will do the backwards conversion. Enter in the camera's exposure time and it will convert to seconds for you.
    """
    if status:
        return mine/1000000
    else:
        return mine*1000000
    

"""
Note for future: can clean up this function with a def if main, run main function thing.
"""
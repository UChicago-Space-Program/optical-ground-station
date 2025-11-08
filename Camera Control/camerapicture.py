"""
The purpose of this code is to take images.

This code was written and compiled by Ashley Ashiku, telescope engineer, for the PULSE-A project. This code utilizes Python wrappers written by Steve Marple, 2017 
(search python-zwoasi on GitHub) around C commands for ZWO ASI cameras from the software development kit.

Last updated July 9th, 2025

Correspondence/Questions:
aashiku@uchicago.edu
"""
# imports needed
import zwoasi as asi
import sys
import os
from astropy.io import fits
from cameraconnect import camera

# Defining the function we'll use to take images and their helper functions!
# YOU WILL NEED to redefine the filepath.

def takepic(exposure, gain, name, camera):
    """"
    This main function will get the image taken for you with the specific exposure time and gain you specify.

    Inputs:
        exposure: int, exposure time (micro seconds, 10^-6 seconds)
        gain: float, camera gain
        name: name of the camera file you want to save
        camera: name of the camera you're using
    """
    imagesetting(exposure, gain, camera)
    eightimage(name, camera)


def imagesetting(exposure, gain, camera):
    """
    This helper function will set the exposure time and gain of the camera, as well as print out the new values so you know it worked.

    Inputs:
        exposure: int, exposure time, in MICROSECONDS (10e-6)
        gain: int, number of counts per electron

    """
    print("Changing exposure time.")
    camera.set_control_value(asi.ASI_EXPOSURE, exposure)
    print("-----")
    print("Successfully set exposure time.")
    camera.set_control_value(asi.ASI_GAIN, gain)
    print("-----")
    print("Successfully set gain value.")
    settings = camera.get_control_values()
    print(f"Exposure time is now {settings['Exposure']} microseconds, {settings['Exposure']/1000000} seconds.")
    print(f"Gain is now {settings['Gain']}.")

def eightimage(name, camera):
    """
    This helper function will take in a file name, take the image, and, most importantly, save it as FITS file.

    Inputs:
        name: string, name of fits file you are saving
        camera: name of the camera you're using
    """
    print('Capturing a single 8-bit mono image...')

    # Filepath, CHANGE THIS!
    save_dir = "/Users/ashleyashiku/Desktop/PULSE-A/cameratest/"
    os.makedirs(save_dir, exist_ok=True)

    camera.set_image_type(asi.ASI_IMG_RAW8)
    image = camera.capture()
    print(f'image taken. trust me on it. ')

    # dpecify image name
    imagename = name + ".fits"
    filename = os.path.join(save_dir,imagename)
    fits.writeto(filename, image, overwrite=True)

    print(f"image should now be saved in {filename}. go look!")

# Now, let's actually use this and take an 8-bit mono image!

#cam_1 = camconnect() # In all honesty, I don't remember what this is here. And can't test or retry it without a camera.
takepic(500000, 56, "101925-sat", camera=camera)


# Ignore my code scraps below!

#print("Changing exposure time.")
#camera.set_control_value(asi.ASI_EXPOSURE, 10000000)
#camera.set_control_value(asi.ASI_GAIN, 4)
#settings = camera.get_control_values()
#print(f"Exposure time is now {settings['Exposure']} microseconds, {settings['Exposure']/1000000} seconds.")
#print(f"Gain is now {settings['Gain']}.")
#get_camera_info = asi.ASISetControlValue(0, ASI_EXPOSURE, 10000, ASI_FALSE)
#print(get_camera_info)
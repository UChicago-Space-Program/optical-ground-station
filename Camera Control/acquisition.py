"""
The purpose of this file is to make a rough draft of the camera acquisition code.

I'm going to write it without classes this time, and not assuming real-time pictures. 
But I'll edit and make future drafts. This is just for practice.

Structure:
1. Access camera logs
2. Calibrate images
3. Turn images into useable counts
4. Identify all brightest points
5. Figure out clusters 
6. Redundancy check for Jupiter/Moon
7. Determine RA&dec
8. Log RA&dec
9. Check differences between RA&decs between objects. Take sigmas between to find outliers

Pixel dimensions: (2160, 3840)
"""
import numpy as np
from astropy.io import fits
from scipy import stats
from scipy.stats import norm
from astropy.wcs import WCS
import glob

# save log of all current images
imagepath = "/Users/ashleyashiku/Desktop/PULSE-A/sunday"
allimagepath = "/Users/ashleyashiku/Desktop/PULSE-A/sunday/*.fits"
imagelog = sorted(glob.glob(allimagepath))

#dark = fits.open(imagepath + '/dark.fits')
# PLACEHOLDER FOR BIAS IMAGE
biaspath = fits.open('/Users/ashleyashiku/Desktop/PULSE-A/cameratest/101925-sat.fits')
#testdata = test[0].data
threshold = 30 #change threshold


def threshold(imagepath, threshold = 30, cal = False):
    """
    The purpose of this function is to check whether a signal above a certain threshold was detected. 
    This includes a very simple calibration (bias subtraction)
    Input:
        imagepath: imagepath
        threshold: threshold counts
        cal: whether to subtract calibration (bias) frame
    """
    image = fits.open(imagepath)
    imagedata = image[0].data
    countvalues = imagedata.flatten()
    
   
    if cal:
        bias = fits.open(biaspath)
        biasdata = image[0].data
        biasvalues = imagedata.flatten()
        countvalues = countvalues - biasvalues

    if np.max(countvalues) > threshold:
        return True
    else:
        return False

acqResult = []
for i in range(len(imagelog)):
    result = threshold(imagelog[i])
    acqResult.append(result)

""" 

r1 = testdata[0:1079, 0:1279]
r2 = testdata[0:719, 1280:2559]
r3 = testdata[0:719, 2560:3839]
r4 = testdata[720:2169, 0:1270]
r5 = testdata[720:2169, 1280:2559]
r6 = testdata[720:2169, 2560:3839]

"""

#regions = [r1, r2, r3, r4, r5, r6]
#regionBol = [False] * len(regions)
regionBol = []

def simple(image):
    """
    NEED TO PLATE SOLVE
    
    The purpose of this very simple acquisition function is to find the pixel of max brightness in the image. Then, list the RA&dec of that pixel in a 
    log. 

    Inputs:
        image = image in data version
    """
    imagedata = image[0].data
    briP = np.unravel_index(np.argmax(imagedata, axis=None), imagedata.shape)
    brightPixel = np.zeros(2, dtype = 'int')
    for i in range(2):
        op = briP[i]
        brightPixel[i] = int(op)

    # have coordinates of brightest pixel, convert to RA and dec

    w = WCS(image[0].header)
    briRd = w.pixel_to_world(brightPixel[0], brightPixel[1])
    return briRd




#def acquire(regions):
#    """
 #   The purpose of this function is to determine whether the target has been acquired. First, split 
  #  the image into 6 regions and check whether there are any points over the threshold. 
   # Inputs:
 #       image: fits image
 #   """
    # First, check if any regions have spotted something over the threshold
 #   for i, region in enumerate(regions):
 #      if np.max(region) > threshold:
  #          regionBol[i] = True
  #      else:
  #          regionBol[i] = False

  #  for i in range(len(regions)):
  #      if regionBol[i]: message="Potential Target."
  #      else: message = "-"
  #      print(f"Region {i+1}: {message}")

   # return regionBol

def regionAcquire(imagedata, regions, regionBol):
    all_pixelBool = []
    for i, region in enumerate(regions):
        if regionBol[i]==True:
            all_pixelBool.extend(regionAcquireHelper(imagedata, region)) # perform function
    return all_pixelBool

def regionAcquireHelper(imagedata, region):
    ylen, xlen = region.shape
    pixelLog = []
    pixelBool = [False]*(xlen*ylen)
    pixelBool = np.array(pixelBool)
    pixelBool = pixelBool.reshape(xlen, ylen)
    print(pixelBool.shape)
    for x in range(xlen):
        for y in range(ylen):
            pixOP = imagedata[x][y]
            if pixOP > threshold:
                pixelBool[x][y] = True

    return pixelBool



"""
regionBol = acquire(regions)
all_pixelBool = regionAcquire(testdata, regions, regionBol)
all_pixelBool = list(all_pixelBool)
print(sum(sum(all_pixelBool)))
print(f"{2160*3840} total pixels. {sum(sum(all_pixelBool))} detected, or {sum(sum(all_pixelBool))/(2160*3840) )*100} percent")

    # Now, you have regionBol, which, with Trues, tells you which regions to look for

"""

# cycle thru all images
# for i, image in imagelog:
# subtract dark

# identify brightest spots function


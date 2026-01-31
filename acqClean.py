import numpy as np
from astropy.io import fits
import csv

def imageRead(imagepath):
    """
    Helper function for translating a fits image into counts.
    Input: pathname to fits image
    Output: array of counts (ints)
    """
    image = fits.open(imagepath)
    imagedata = image[0].data
    countvalues = imagedata.flatten()
    return countvalues

def threshold(imagepath, biaspath, darkpath, threshold = 30, cal = False):
    """
    The purpose of this function is to check whether a signal above a certain threshold was detected. 
    This includes a very simple calibration (bias subtraction)
    Input:
        imagepath: imagepath
        threshold: threshold counts
        cal: whether to subtract calibration (bias) frame
    """
    # reading image
    imageCount = imageRead(imagepath)
    biasCount = imageRead(biaspath)
    darkCount = imageRead(darkpath)

    # calibrating image
    if cal:
       counts = imageCount - biasCount - darkCount
    
    else:
        counts = imageCount

    if np.max(counts) > threshold:
        return True
    else:
        return False        

def logger(header, data, name):
    """
    The purpose of this function is to save times as csv file.
    """
    for i in range(len(header)):
        headOp = header[i]
        dataOp = data[i]
        nameOp = name[i]
        with open(nameOp+'.csv', 'w', newline='', encoding='utf8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headOp)
            for t in dataOp:
                writer.writerow([t])
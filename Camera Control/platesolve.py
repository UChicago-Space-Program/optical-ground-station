# file for taking an image, retrieving the solved coordinates from ASTAP, and sending it to the telescope
from astropy.io import fits
from twirl import find_peaks
import numpy as np
import pandas as pd
from astroquery.astrometry_net import AstrometryNet
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from camerapicture import *

def platesolve(imagepath, starcam = True):
    """ Currently hardcoded for calibration files. Star = true, tracking = false"""
    image = fits.open('/Users/ashleyashiku/Desktop/PULSE-A/' + imagepath + '.fits')  
    rawdata = image[0].data

    dark_F = fits.open('/Users/ashleyashiku/Desktop/PULSE-A/starcamera/DARKFRAME.fits')
    dark = dark_F[0].data
    bias_F = fits.open('/Users/ashleyashiku/Desktop/PULSE-A/starcamera/BIASFRAME.fits')
    bias = bias_F[0].data
    flat_F = fits.open('/Users/ashleyashiku/Desktop/PULSE-A/starcamera/FLATFRAME3.fits')
    flat = flat_F[0].data

    data = (rawdata - bias - (dark - bias))/flat
    # pull out peaks
    xy = find_peaks(data)[0:10]
    x = np.array(xy[:,0])
    y = np.array(xy[:,1])

    rX = []
    rY = []
    for i in range(len(xy)):
        rX.append(int(np.round(x[i])))
        rY.append(int(np.round(y[i])))

    flux_val = data[rY, rX]
    sources = np.array([x, y, flux_val])
    source_list = pd.DataFrame(sources.T, columns = ['x', 'y', 'flux_val'])
    source_list.sort_values('flux_val', ascending = True)

    # pass into platesolving
    ast = AstrometryNet()
    ast.api_key = 'cgiokceqsveywcin'

    if starcam:
        width = 1280        # star camera image width, pixels
        height = 960       # star camera image height, pixels
    else:
        width = 2048        # tracking camera image width, pixels
        height = 2064       # tracking camera image height, pixels 

    wcs_header = ast.solve_from_source_list(source_list['x'], source_list['y'], width, height, solve_timeout=120)

    # find image center pixel
    frame = np.array([width, height])
    centerP = frame/2
    center = WCS(wcs_header).pixel_to_world(centerP[0], centerP[1])
    centerICRS = center.transform_to('icrs') # transform to ICRS coordinate system

    dec = center.dec.value
    ra = center.ra.value

    return ra, dec


def usePlatesolve(exptime, imagename):
    """
    input:
        exptime = exposure time, (int), micro seconds
        imagename = name of image to be saved, (string)
    """
    takepic(exptime, 20, imagename, camera, "starcamera", talk=True)
    ra, dec =  platesolve(imagename)

    return ra, dec
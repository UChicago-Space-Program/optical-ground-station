import numpy as np
import matplotlib
from astropy.io import fits

# define pathname to the image you are trying to open
image = fits.open('/Users/ashleyashiku/Desktop/PULSE-A/cameratest/101825-room.fits')

# open and define the header and actual "image" data
header = image[0].header
imagedata = image[0].data

# position matrices
positionx = np.zeros_like(imagedata, dtype=np.int32)   
j = 0 # start at origin
k = 0
for i in range(int(3840)):
    positionx[:,int(j)] = k
    k+=1
    j+=1
    
positiony = np.zeros_like(imagedata, dtype=np.int32)   
j = 0 # start at origin
k = 0
for i in range(int(2160)):
    positiony[int(j),:] = k
    k+=1
    j+=1


# centroid function
def centroid(images):
    cenX = []
    cenY = []
    for image in images:
        imagedata = fits.getdata(image)

        mask = imagedata<250
        imagedata[mask] = 0

        x_mul = imagedata*positionx
        sum_xmul = np.sum(x_mul)
        weight_x = sum_xmul/(np.sum(imagedata))

        y_mul = imagedata*positiony
        sum_ymul = np.sum(y_mul)
        weight_y = sum_ymul/(np.sum(imagedata))

        cenX.append(weight_x)
        cenY.append(weight_y)
    cenZip = zip(cenX, cenY)
    cenZip = list(cenZip)
    cenZip = np.array(cenZip)
    return cenZip

center = (1530, 1920)
def offset(centroids):
    diff = []
    for i in centroids:
        diffOp = np.abs(i - center)
        diff.append(diffOp)
    return diff

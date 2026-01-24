from cameraconnect import camera
from camerapicture import *
from acqClean import *
from dataAnalysis import *
import csv

def operate(exp, gain, n, label, folder, cal=None, talk=False):
    """
    The purpose of this function is to operate the tracking camera.
    Inputs:
        exp = exposure time, measured in e-9 seconds
        gain = camera gain
        n = int, number of images
        label = string, label for all fits images
        folder = string, name of folder saving images to
        cal = tuple, (biaspath (str), darkpath(str), threshold(int)) for calibration
    """
    calBool = False
    if cal is not None:
        biaspath, darkpath, thres = cal
        calBool = True
    time_log = []
    acq_log = []

    for i in range(n):
        print(f"IMAGE {i}")
        iString = str(i)
        iLabel = label+iString # individual file name
        logTime = takepic(exp, gain, iLabel, camera, folder, talk=False)
        print("--")
        time_log.append(logTime)

        # check for acquisition
        acq = threshold(iLabel, biaspath, darkpath, thres, calBool)
        if acq:
            print("***TARGET ACQUIRED***")
        acq_log.append(acq)

    return time_log, acq_log

timeList, acqList = operate(100000, 140, 3800)
head = ('times', 'acquired')
data = (timeList, acqList)
name = ('timeLog01.18.26', 'acqLog01.18.26')
logger(head, data, name)



# next steps: integrate csv file logging into main function


#header = "times"
#with open('timelogTesJUPYTER.csv', 'w', newline='', encoding='utf8') as csvfile:
 #   writer = csv.writer(csvfile)
  #  writer.writerow(header)
  #  for t in logged_time:
  #      writer.writerow([t])
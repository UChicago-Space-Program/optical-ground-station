from Pol_Measurement_Class import Pol_Measurement
from MuellerMatrixComputation import*
import numpy as np
import matplotlib as plt
import os
import math
from Data_analysis_fiber_experiment import*

def create_file_paths_non_recursive(dir_path: str) -> list[str]:
    """
    :param dir_path: path of directory as a string
    :return: returns file paths inside of the directory
    """
    if not os.path.exists(dir_path) or not os.path.isdir(dir_path):
        raise ValueError("The provided path is not a valid directory.")
    else:
        file_paths = [os.path.join(dir_path, file_name) for file_name in os.listdir(dir_path) if
                      os.path.isfile(os.path.join(dir_path, file_name))]
        return file_paths

def create_file_paths_recursive(dir_path: str, file_paths: list) -> list[str]:
    """
    :param dir_path: path of directory as a string
    :return: returns file paths inside of the directory. Works for a directory of directories
    """
    if os.path.isfile(dir_path):
        file_paths.append(dir_path)
        return file_paths
    elif os.path.isdir(dir_path):
        for file_name in os.listdir(dir_path):
            file_paths = create_file_paths_recursive(os.path.join(dir_path, file_name), file_paths)
        return file_paths

def create_measurement_objects(file_paths: list[str]):
    """
    :param file_paths: list of file paths as strings
    :return: returns a list of polarization measurement objects
    """
    measurement_list = []
    for path in file_paths:
        path_str_lst = path.split("\\")
        str_name = path_str_lst[-1]
        measurement_list.append(Pol_Measurement(str_name, path))
    return measurement_list

def split_measured_objects(measured_objs):
    initial_stokes_objs = []
    final_stokes_objs = []
    for i in range(len(measured_objs)):
        if i == 0:
            calibration_object = measured_objs[i]
        elif 0 < i <= 10:
            final_stokes_objs.append(measured_objs[i])
        elif 20 >= i > 10:
            initial_stokes_objs.append(measured_objs[i])
    return calibration_object, initial_stokes_objs, final_stokes_objs

def calibrated_stdev(self: Pol_Measurement, param: str, calibration_obj: Pol_Measurement):
    """
    :param param: String in self.data_keys.
    :return:
    """
    if param not in self.data_keys:
        print(f"ERROR: Please input one of the following data keys: {self.data_keys}")
    else:
        data_list = self.data[param]
        if type(data_list[0]) == float:
            #perform stdev computation
            sum_val = 0
            for i in range(len(data_list)):
                sum_val += (data_list[i] - self.average(param)) ** 2
            return math.sqrt(sum_val / len(data_list))
        else:
            print("Cannot execute average on non-numerical values")

def calibrated_average(self: Pol_Measurement, param: str, calibration_obj: Pol_Measurement):
    """
    :param param: String in self.data_keys.
    :return:
    """
    if param not in self.data_keys:
        print(f"ERROR: Please input one of the following data keys: {self.data_keys}")
    else:
        data_list = self.data[param]
        if type(data_list[0]) == float:
            sum_val = 0
            for i in range(len(data_list)):
                sum_val += data_list[i] - calibration_obj.average(param)
            return sum_val / len(data_list)
        else:
            print("Cannot execute average on non-numerical values")

def create_avg_list_cal(measured_objects, param, cal_obj):
    avg_list = []
    for obj in measured_objects:
        avg_list.append(calibrated_average(obj, param, cal_obj))
    return avg_list

def create_stdev_list_cal(measured_objects, param, cal_obj):
    stdev_list = []
    for obj in measured_objects:
        stdev_list.append(calibrated_stdev(obj, param, cal_obj))
    return stdev_list

def plot_avg_with_stdev_calibrated(measured_objects, param, calibration_object):
    # Ignore the figure() argument if you want to show multiple graphs in a single window
    #plt.figure()
    average_param_lst = create_avg_list_cal(measured_objects, param, calibration_object)
    angle_lst = create_angle_list(measured_objects)
    stdev_lst = create_stdev_list_cal(measured_objects, param, calibration_object)
    plt.scatter(angle_lst, average_param_lst)
    # fmt = none adds errorbars without overriding scatter
    plt.errorbar(angle_lst, average_param_lst, yerr=stdev_lst, fmt='None', color='red')
    plt.xlabel("Angle [deg]")
    plt.ylabel(param)
    plt.title(f"Angle vs {param} - Calibrated")

def create_correlation_2objs_vs_angle(meas_obj_1, meas_obj_2, param_1, param_2):
    data_avg_lst = []
    for i in range(len(meas_obj_1)):
        obj_1 = meas_obj_1[i]
        obj_2 = meas_obj_2[i]
        data_1 = obj_1.data[param_1]
        data_2 = obj_2.data[param_2]
        data_diff = []
        data_avg = 0
        if len(data_1) == len(data_2):
            for i in range(len(data_1)):
                data_diff.append(data_1[i] - data_2[i])
                data_avg += data_diff[i]
        elif len(data_1) > len(data_2):
            for i in range(len(data_2)):
                data_diff.append(data_1[i] - data_2[i])
                data_avg += data_diff[i]
        else:
            for i in range(len(data_1)):
                data_diff.append(data_1[i] - data_2[i])
                data_avg += data_diff[i]
        data_avg = data_avg / len(data_diff)
        data_avg_lst.append(data_avg)
    angle_lst = create_angle_list(meas_obj_1)
    plt.scatter(angle_lst, data_avg_lst)
    plt.xlabel("Angle of QWP [deg]", fontsize=8)
    plt.ylabel(f"{param_1} - {param_2}", fontsize=8)
    plt.title(f"{param_1} - {param_2} to angle", fontsize=8)

measured_objects = create_measurement_objects(create_file_paths_recursive(r"C:\Users\juani\Personal\CubeSat\Polarimeter Data\Telescope Experiment\Nov 12 Data", []))

cal_obj, initial_objs, final_objs = split_measured_objects(measured_objects)

params = ["S 0 [mW]", "Normalized s 1 ", "Normalized s 2 ", "Normalized s 3 ", 'DOP[%] ', 'DOCP[%] ', 'Ellipticity[Â°] ', 'Phase Difference[Â°] ', 'Power[mW] ']
for param in params:
    """
    plot_avg_with_stdev([cal_obj], param)
    plt.show()
    plot_avg_with_stdev(initial_objs, param)
    plt.show()
    plot_avg_with_stdev(final_objs, param)
    plt.show()
    plot_avg_with_stdev_calibrated(initial_objs, param, cal_obj)
    plt.show()
    plot_avg_with_stdev_calibrated(final_objs, param, cal_obj)
    plt.show()
    """
    plot_avg_with_stdev(initial_objs, param)
    plt.show()
    plot_avg_with_stdev(final_objs, param)
    plt.show()
    create_correlation_2objs_vs_angle(initial_objs, final_objs, param, param)
    plt.show()



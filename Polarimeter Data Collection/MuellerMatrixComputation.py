import numpy as np
from Pol_Measurement_Class import Pol_Measurement

def make_mueller(A, B):
    """

    :param A: Input array of stokes vectors
    :param B: Output array of stokes vectors
    :return: Mueller matrix M
    """
    M = np.matmul(B, np.matmul(A.T, np.linalg.inv(np.matmul(A, A.T)))) # B*A^T*(A*A^T)^-1
    return M

def stokes_matrix(measurements: list[Pol_Measurement]):
    # Correcting length of measurements list
    if len(measurements) > 6:
        print(f"Measurements list has length {len(measurements)}. Deleting las {len(measurements)-6} items in list.")
        for i in range(len(measurements) - 6):
            del measurements[-1]
        print(f"Measurements list has length {len(measurements)}.")
    elif len(measurements) < 6:
        print(f"Measurements list has length {len(measurements)}. Not enough measurements to compute Mueller Matrix.")
        return None
    else:
        print("Measurements list has length 6")
    # Producing array of stokes:
    dict_stokes = {"S0": [], "S1": [], "S2": [], "S3": []}
    for i in range(len(measurements)):
        dict_stokes["S0"].append(measurements[i].average("S 0 [mW]"))
        dict_stokes["S1"].append(measurements[i].average("S 1 [mW]"))
        dict_stokes["S2"].append(measurements[i].average("S 2 [mW]"))
        dict_stokes["S3"].append(measurements[i].average("S 3 [mW]"))
    stokes_A = np.array([dict_stokes["S0"], dict_stokes["S1"], dict_stokes["S2"], dict_stokes["S3"]])
    if stokes_A.ndim == (6, 4):
        print("Array has size 6, 4")
        return stokes_A
    else:
        print(f"Array has size {stokes_A.ndim}. Something went wrong.")
        return None

def stokes_uncertainty_matrix(measurements: list[Pol_Measurement]):
    # Correcting length of measurements list
    if len(measurements) > 6:
        print(f"Measurements list has length {len(measurements)}. Deleting las {len(measurements)-6} items in list.")
        for i in range(len(measurements) - 6):
            del measurements[-1]
        print(f"Measurements list has length {len(measurements)}.")
    elif len(measurements) < 6:
        print(f"Measurements list has length {len(measurements)}. Not enough measurements to compute Mueller Matrix.")
        return None
    else:
        print("Measurements list has length 6")
    # Producing array of stokes:
    dict_stokes = {"S0": [], "S1": [], "S2": [], "S3": []}
    for i in range(len(measurements)):
        dict_stokes["S0"].append(measurements[i].stdev("S 0 [mW]"))
        dict_stokes["S1"].append(measurements[i].stdev("S 1 [mW]"))
        dict_stokes["S2"].append(measurements[i].stdev("S 2 [mW]"))
        dict_stokes["S3"].append(measurements[i].stdev("S 3 [mW]"))
    stokes_A = np.array([dict_stokes["S0"], dict_stokes["S1"], dict_stokes["S2"], dict_stokes["S3"]])
    if stokes_A.ndim == (6, 4):
        print("Array has size 6, 4")
        return stokes_A
    else:
        print(f"Array has size {stokes_A.ndim}. Something went wrong.")
        return None

def average_matrices(M_list: list[np.array]):
    M_final = np.empty((4, 6))
    for M in M_list:
        M_final += M
    print(f"Final Averaged Mueller Matrix: {M_final / len(M_list)}")
    return M_final / len(M_list)


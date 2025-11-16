from MuellerMatrixComputation import *
from Pol_Measurement_Class import Pol_Measurement
import numpy as np

A_mean = stokes_matrix(measurements_input)
B_mean = stokes_matrix(measurements_output)
A_std  = stokes_uncertainty_matrix(measurements_input)
B_std  = stokes_uncertainty_matrix(measurements_output)

def propagate_uncertainty(A_mean, A_std, B_mean, B_std, Nsamples=5000):
    M_samples = []
    for _ in range(Nsamples):
        A_s = np.random.normal(A_mean, A_std)   # 4×6
        B_s = np.random.normal(B_mean, B_std)   # 4×6
        M_s = make_mueller(A_s, B_s)            # MUST be 4×4
        M_samples.append(M_s)

    M_samples = np.array(M_samples)   # (Nsamples, 4, 4)

    return np.mean(M_samples, axis=0), np.std(M_samples, axis=0)


M_mean, M_std = propagate_uncertainty(A_mean, A_std, B_mean, B_std)

print("Mueller matrix:\n", M_mean)
print("Uncertainty:\n", M_std)

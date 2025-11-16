import numpy as np
import math

# add comment

def truncated_standard_normal():
    x = np.random.normal(0, 1)
    while abs(x) > 3:
        x = np.random.normal(0, 1)
    return x

#Define the random Mueller matrix function.
#Identity matrix
M_0 = np.array([
    [1, 0, 0, 0],
    [0 , 1, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, 1]
])

#Single pm fiber matrix
#M_12 = np.array([
   # [1, 0, 0, 0],
   # [0.627773 , -0.261825, 0.45438, 0.480219],
   # [-0.708459, -0.293815, -0.307685, -0.02616],
   # [1.12193, 0.989887, -0.191137, -0.015808]

#])
theta = 0.1
delta = 0.1
cos2t = np.cos(2*theta)
sin2t = np.sin(2*theta)
cosd = np.cos(delta)
sind = np.sin(delta)

#General linear retarder matrix
M_12 = np.array([
    [1, 0, 0, 0],
    [0, cos2t**2+sin2t**2*cosd, cos2t*sin2t*(1-sind), -sin2t*sind],
    [0, cos2t*sin2t*(1-cosd), cos2t**2*cosd+sin2t**2, cos2t*sind],
    [0, sin2t*sind, -cos2t*sind, cosd]
])

#Bit flip matrix
M_1 = np.array([
    [1, 0, 0, 0],
    [0, 1, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, -1]
])

#Interpolate between matrices
def interpolate_matrix(t, M0, M12, M1):
    """
    Piecewise linear interpolation:
    - t ∈ [-3,0] interpolates from M0 → M12
    - t ∈ [0,3] interpolates from M12 → M1
    """

    # Region 1: M0 → M12 on [-3, 0]
    if -3 <= t <= 0:
        s = (t + 3) / 3     # maps t=-3→0  and t=0→1
        return (1 - s) * M0 + s * M12

    # Region 2: M12 → M1 on [0, 3]
    elif 0 <= t <= 3:
        s = t / 3          # maps t=0→0  and t=3→1
        return (1 - s) * M12 + s * M1

    # Optional: clamp or raise error if t out of range
    elif t < -3:
        return M0.copy()
    else:  # t > 3
        return M1.copy()


#Randomly pick a value between from a normal distribution
#and assign a Mueller matrix. Run an input Stokes vector through the
#matrix, return the output

def random_matrix_func(vector_in:np.array):
    t = truncated_standard_normal()
    vector_out = interpolate_matrix(t, M_0, M_12, M_1)@vector_in
    return vector_out

#Define the right- and left-handed circular polarization states
R = np.array([1, 0, 0, 1]) #Right handed
L = np.array([1, 0, 0, -1]) #Left handed

#Send 0 to L and 1 to R.
def bit_conversion(x:np.array):
    mapping = {
        0: L,
        1 : R
    }
    return np.array([mapping[x] for x in x])

#Send array to bit string of 0, 1, 2.
def classify_arr(arr:np.array, res:float):
    conditions = [
        arr > res, #Value greater than res
        arr < -res #Value less than -res
    ]
    choices = [0, 1] #Corresponding outputs
    return np.select(conditions, choices, default = 2) #If neither is met, choose 2

#Run a string of bits through the simulated polarization algorithm
def polarization_func(input_bits:np.array, res:float):
    states_in = bit_conversion(input_bits) #Convert bits to Stokes vectors
    states_out = np.array([random_matrix_func(v) for v in states_in]) #Put each Stokes vector through a random matrix
    left_coeff = np.array([np.dot(L, v) for v in states_out])  #Take inner product of each state with \ket{L}
    right_coeff = np.array([np.dot(R, v) for v in states_out]) #Take inner product of each state with \ket{R}
    diff = np.abs(left_coeff) - np.abs(right_coeff) #Return |a|-|b|
    output_bits = classify_arr(diff, res) #Send to L or R depending on relative magnitude of inner product with each

    # Count ambiguous outputs
    num_twos = np.sum(output_bits == 2)

    # Create mask ignoring 2's
    valid_positions = output_bits != 2

    # Count flips specifically
    flips_0_to_1 = np.sum((input_bits[valid_positions] == 0) & (output_bits[valid_positions] == 1))
    flips_1_to_0 = np.sum((input_bits[valid_positions] == 1) & (output_bits[valid_positions] == 0))

    # Compute total error rate (%)
    total_errors = flips_0_to_1 + flips_1_to_0 + num_twos
    error_percentage = (total_errors / len(input_bits)) * 100.0

    return {
        "input_bits": input_bits,
        "output_bits": output_bits,
        "flips_0_to_1": flips_0_to_1,
        "flips_1_to_0": flips_1_to_0,
        "twos": num_twos,
        "error_percentage": error_percentage
    }




###Inputs
#Put your string of bits here
#bits = np.array([])

#Set resolution here
#res = 0.1

#Test
results = polarization_func(np.random.randint(0, 2, size=1000000), 0.1)

print("Input bits:      ", results["input_bits"])
print("Output bits:     ", results["output_bits"])
print("0→1 flips:       ", results["flips_0_to_1"])
print("1→0 flips:       ", results["flips_1_to_0"])
print("Ambiguous (2's):", results["twos"])
print("Error %:         ", results["error_percentage"])
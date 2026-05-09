import numpy as np


class PIDController:
    def __init__(self, kp, ki, kd):
        self.kp = kp
        self.ki = ki
        self.kd = kd

        self.integral = 0.0
        self.previous_error = 0.0

    def update(self, error, dt):
        """
        Given the current error and timestep dt, return PID correction
        """

        if dt <= 0:
            raise ValueError("dt must be positive")

        self.integral += error * dt

        derivative = (error - self.previous_error) / dt

        output = (
            self.kp * error
            + self.ki * self.integral
            + self.kd * derivative
        )

        self.previous_error = error

        return output

    def reset(self):
        self.integral = 0.0
        self.previous_error = 0.0


def calculate_pointing_error(
    x, y,
    x_centre, y_centre,
    plate_scale,
    camera_rotation=0.0
):
    """
    Converts pixel error into az/el angular error
    """

    dx_pix = x - x_centre
    dy_pix = y - y_centre

    dx_angle = dx_pix * plate_scale
    dy_angle = dy_pix * plate_scale

    theta = np.deg2rad(camera_rotation)

    error_az = dx_angle * np.cos(theta) - dy_angle * np.sin(theta)
    error_el = dx_angle * np.sin(theta) + dy_angle * np.cos(theta)

    return error_az, error_el


def pid_pointing_update(
    x, y,
    x_centre, y_centre,
    current_az, current_el,
    plate_scale,
    az_pid,
    el_pid,
    dt,
    camera_rotation=0.0
):
    """
    Uses PID control to compute the next az/el

    Returns:
        new_az, new_el, error_az, error_el, az_correction, el_correction
    """

    error_az, error_el = calculate_pointing_error(
        x=x,
        y=y,
        x_centre=x_centre,
        y_centre=y_centre,
        plate_scale=plate_scale,
        camera_rotation=camera_rotation
    )

    az_correction = az_pid.update(error_az, dt)
    el_correction = el_pid.update(error_el, dt)

    new_az = current_az + az_correction
    new_el = current_el + el_correction

    return new_az, new_el, error_az, error_el, az_correction, el_correction
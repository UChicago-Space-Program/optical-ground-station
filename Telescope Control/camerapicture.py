"""
camerapicture.py
-----------------
Image capture for ZWO ASI cameras.

Based on code by Ashley Ashiku (PULSE-A project), using python-zwoasi
wrappers (Steve Marple, 2017) around the ZWO ASI SDK.
"""

import zwoasi as asi
import time
import os
from datetime import datetime, timezone
from astropy.io import fits


def _camera_short_name(camera):
    """Short name string from the camera handle, for filenames."""
    try:
        info = camera.get_camera_property()
        return info['Name'].replace(' ', '_')
    except Exception:
        return 'cam'


def _utc_timestamp():
    """UTC timestamp string suitable for filenames."""
    return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


def takepic(exposure, gain, name, camera, folder, base_dir, talk=True):
    """
    Set exposure/gain, capture a single 8-bit mono image, save as FITS.

    Returns full path to the saved FITS file.
    """
    _configure_camera(exposure, gain, camera, talk)
    filepath = _capture_and_save(name, camera, exposure, folder, base_dir, talk)
    return filepath


def _configure_camera(exposure, gain, camera, talk=False):
    """Set bandwidth, exposure, gain, and image type."""
    # Minimize USB bandwidth — required for some sensors (e.g. ASI585MM Pro)
    # to produce valid frames in snapshot mode.
    bw_min = camera.get_controls()['BandWidth']['MinValue']
    camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, bw_min)
    camera.set_control_value(asi.ASI_EXPOSURE, exposure)
    camera.set_control_value(asi.ASI_GAIN, gain)
    camera.set_image_type(asi.ASI_IMG_RAW8)
    if talk:
        settings = camera.get_control_values()
        print(f"  Exposure: {settings['Exposure']} us "
              f"({settings['Exposure']/1e6:.3f} s)  "
              f"Gain: {settings['Gain']}  "
              f"BW: {settings['BandWidth']}")


def _capture_and_save(name, camera, exposure_us, folder, base_dir, talk=False):
    """
    Capture a single 8-bit mono image and save as FITS.
    Filename: {name}_{camera}_{UTC_timestamp}.fits
    """
    save_dir = os.path.join(base_dir, folder)
    os.makedirs(save_dir, exist_ok=True)

    cam_name = _camera_short_name(camera)
    ts = _utc_timestamp()
    filename = f"{name}_{cam_name}_{ts}.fits"
    filepath = os.path.join(save_dir, filename)

    # Wait for settings to take effect before capturing
    #time.sleep((exposure_us / 1e6) + 0.5)

    initial_sleep_sec = max(exposure_us / 1e6 - 0.1, 0.01)
    image = camera.capture(initial_sleep=initial_sleep_sec, poll=0.05)

    if talk:
        print(f"  Image: shape={image.shape} min={image.min()} "
              f"max={image.max()}")
    fits.writeto(filepath, image, overwrite=True)
    if talk:
        print(f"  Saved: {filepath}")
    return filepath
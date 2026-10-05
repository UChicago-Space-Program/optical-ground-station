"""
live_view.py
-------------
Live preview from a ZWO ASI camera using the ASI SDK video mode
and OpenCV for display.
  'q' = quit
  's' = switch camera (cycles between the configured profiles)
  'w' = save a snapshot (.fits)
"""

import zwoasi as asi
import cv2
import numpy as np
import os
import sys
from datetime import datetime, timezone

# --- Config ---
SDK_PATH = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/libASICamera2.dylib.1.41'

# Camera profiles to cycle through with 's'. Order = switch order.
CAMERA_PROFILES = [
    {'key': 'track', 'name': 'ZWO ASI585MM Pro',  'exposure': 200000, 'gain': 400},
    {'key': 'st',    'name': 'ZWO ASI120MM Mini', 'exposure': 200000, 'gain': 150},
]
START_PROFILE = 'track'   # which profile to open first

SAVE_DIR = '/Users/rodrigospinola/Desktop/Rodrigo/UChicago/Pulse-A/Telescope/Control Code/Pulse/Images/Snapshots'


def open_camera(cameras, profile):
    """Open and configure the camera for the given profile.

    Returns (camera, info, width, height) or None if the camera is not found.
    """
    camera_id = None
    for i, name in enumerate(cameras):
        if name == profile['name']:
            camera_id = i
            break
    if camera_id is None:
        print(f"'{profile['name']}' not found.")
        return None

    camera = asi.Camera(camera_id)
    info = camera.get_camera_property()
    width = info['MaxWidth']
    height = info['MaxHeight']
    print(f"Using: {info['Name']}  ({width}x{height})")

    camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD,
                             camera.get_controls()['BandWidth']['MinValue'])
    camera.set_control_value(asi.ASI_EXPOSURE, profile['exposure'])
    camera.set_control_value(asi.ASI_GAIN, profile['gain'])
    camera.set_image_type(asi.ASI_IMG_RAW8)

    camera.start_video_capture()
    print(f"Exposure: {profile['exposure']} us  Gain: {profile['gain']}")
    return camera, info, width, height


def close_camera(camera, info):
    """Stop capture and close the given camera's OpenCV window."""
    try:
        camera.stop_video_capture()
    except Exception:
        pass
    camera.close()
    cv2.destroyWindow(f'Live View - {info["Name"]}')


def main():
    asi.init(SDK_PATH)
    cameras = asi.list_cameras()
    if len(cameras) == 0:
        print("No cameras found.")
        sys.exit(1)

    print(f"Connected cameras: {cameras}")

    # Pick the starting profile index.
    profile_idx = next((i for i, p in enumerate(CAMERA_PROFILES)
                        if p['key'] == START_PROFILE), 0)

    opened = open_camera(cameras, CAMERA_PROFILES[profile_idx])
    if opened is None:
        sys.exit(1)
    camera, info, width, height = opened

    print("Live view started. 'q' = quit, 's' = switch camera, 'w' = save snapshot.")
    print("Adjust exposure and gain per profile in CAMERA_PROFILES if too dark/bright.")

    os.makedirs(SAVE_DIR, exist_ok=True)

    try:
        while True:
            frame = camera.capture_video_frame()

            # Display (resize if too large for screen)
            display = frame
            max_display = 900
            if max(height, width) > max_display:
                scale = max_display / max(height, width)
                display = cv2.resize(frame,
                                     (int(width * scale), int(height * scale)))

            cv2.imshow(f'Live View - {info["Name"]}', display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('s'):
                # Switch to the next camera profile.
                close_camera(camera, info)
                start_idx = profile_idx
                opened = None
                # Try each subsequent profile until one opens.
                for step in range(1, len(CAMERA_PROFILES) + 1):
                    next_idx = (start_idx + step) % len(CAMERA_PROFILES)
                    print(f"Switching to '{CAMERA_PROFILES[next_idx]['key']}'...")
                    opened = open_camera(cameras, CAMERA_PROFILES[next_idx])
                    if opened is not None:
                        profile_idx = next_idx
                        break
                if opened is None:
                    print("No other camera available to switch to. Exiting.")
                    return
                camera, info, width, height = opened
            elif key == ord('w'):
                ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                name = info['Name'].replace(' ', '_')
                path = os.path.join(SAVE_DIR, f'snap_{name}_{ts}.fits')
                from astropy.io import fits
                fits.writeto(path, frame, overwrite=True)
                print(f"  Snapshot saved: {path}")

    finally:
        try:
            camera.stop_video_capture()
        except Exception:
            pass
        cv2.destroyAllWindows()
        print("Live view stopped.")


if __name__ == '__main__':
    main()

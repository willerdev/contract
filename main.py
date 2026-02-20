import cv2
import numpy as np
import pyautogui
import math
import os
import sys
import ssl
import time
import urllib.request

# MediaPipe 0.10+ uses the Tasks API (no mp.solutions)
from mediapipe.tasks.python.core import base_options as base_options_lib
from mediapipe.tasks.python.vision import (
    HandLandmarker,
    HandLandmarkerOptions,
    HandLandmarksConnections,
)
from mediapipe.tasks.python.vision import drawing_utils as mp_drawing
from mediapipe.tasks.python.vision.core import vision_task_running_mode
from mediapipe import Image as MpImage, ImageFormat

# Screen size
screen_w, screen_h = pyautogui.size()

# Fixed (not in UI)
PROCESS_WIDTH, PROCESS_HEIGHT = 640, 480

# Live settings (updated by UI trackbars; defaults here)
settings = {
    "click_threshold_left": 40,
    "click_threshold_right": 40,
    "smoothing_alpha": 0.4,      # 0–1
    "dead_zone": 2,
    "margin": 0.08,              # 0–0.3
    "sensitivity": 1.0,          # 0.5–2
    "right_click_cooldown_ms": 200,
    "double_click_ms": 350,
    "show_camera": 1,            # 1=show, 0=hide
}
CAMERA_WINDOW = "Hand Mouse Control"
SETTINGS_WINDOW = "Air Mouse Settings"

# Download hand landmarker model if needed (MediaPipe 0.10+ requires a .task file)
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, "hand_landmarker.task")

if not os.path.exists(MODEL_PATH):
    print("Downloading hand_landmarker.model...")
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(MODEL_URL, context=ctx) as src:
            with open(MODEL_PATH, "wb") as dst:
                dst.write(src.read())
        print("Done.")
    except urllib.error.URLError as e:
        print(f"Download failed: {e}")
        print(f"Download the model manually and save as: {MODEL_PATH}")
        print(f"URL: {MODEL_URL}")
        print('Or run: curl -L -o', repr(MODEL_PATH), repr(MODEL_URL))
        sys.exit(1)

# Create hand landmarker (Tasks API)
base_options = base_options_lib.BaseOptions(model_asset_path=MODEL_PATH)
options = HandLandmarkerOptions(
    base_options=base_options,
    running_mode=vision_task_running_mode.VisionTaskRunningMode.IMAGE,
    num_hands=1,
)
hands = HandLandmarker.create_from_options(options)

# Camera
cap = cv2.VideoCapture(0)


def distance(p1, p2):
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def create_settings_window():
    """Create the settings window with trackbars (call once)."""
    cv2.namedWindow(SETTINGS_WINDOW)
    # (trackbar_name, key, max_int, scale: int -> value)
    # scale used when reading: value = getTrackbarPos() then settings[key] = scale(value)
    cv2.createTrackbar("Left click threshold", SETTINGS_WINDOW, 40, 80, lambda _: None)
    cv2.createTrackbar("Right click threshold", SETTINGS_WINDOW, 40, 80, lambda _: None)
    cv2.createTrackbar("Smoothing (0=smooth)", SETTINGS_WINDOW, 40, 100, lambda _: None)  # 0-100 -> 0.01-1
    cv2.createTrackbar("Dead zone (px)", SETTINGS_WINDOW, 2, 15, lambda _: None)
    cv2.createTrackbar("Margin (%)", SETTINGS_WINDOW, 8, 30, lambda _: None)  # 0-30 -> 0-0.30
    cv2.createTrackbar("Sensitivity (%)", SETTINGS_WINDOW, 50, 150, lambda _: None)  # 0-150 -> 0.5-2.0
    cv2.createTrackbar("Right click cooldown (ms)", SETTINGS_WINDOW, 200, 500, lambda _: None)
    cv2.createTrackbar("Double-click window (ms)", SETTINGS_WINDOW, 350, 600, lambda _: None)
    cv2.createTrackbar("Show camera (0=off 1=on)", SETTINGS_WINDOW, 1, 1, lambda _: None)


def read_settings_from_ui():
    """Read trackbar values into settings dict (call each frame)."""
    settings["click_threshold_left"] = cv2.getTrackbarPos("Left click threshold", SETTINGS_WINDOW)
    settings["click_threshold_right"] = cv2.getTrackbarPos("Right click threshold", SETTINGS_WINDOW)
    # Smoothing: 0-100 -> 0.01-1.0 (avoid 0 for stability)
    v = cv2.getTrackbarPos("Smoothing (0=smooth)", SETTINGS_WINDOW)
    settings["smoothing_alpha"] = max(0.01, v / 100.0)
    settings["dead_zone"] = cv2.getTrackbarPos("Dead zone (px)", SETTINGS_WINDOW)
    settings["margin"] = cv2.getTrackbarPos("Margin (%)", SETTINGS_WINDOW) / 100.0
    settings["sensitivity"] = 0.5 + cv2.getTrackbarPos("Sensitivity (%)", SETTINGS_WINDOW) / 100.0  # 0.5-2.0
    settings["right_click_cooldown_ms"] = cv2.getTrackbarPos("Right click cooldown (ms)", SETTINGS_WINDOW)
    settings["double_click_ms"] = cv2.getTrackbarPos("Double-click window (ms)", SETTINGS_WINDOW)
    settings["show_camera"] = cv2.getTrackbarPos("Show camera (0=off 1=on)", SETTINGS_WINDOW)


# State for smoothing and gestures
smoothed_x = screen_w / 2
smoothed_y = screen_h / 2
clicking = False
right_was_close = False
last_right_click_time = 0
last_left_release_time = 0
did_double_click = False  # so we don't mouseUp after a doubleClick

create_settings_window()

while True:
    read_settings_from_ui()
    show_cam = settings["show_camera"] == 1

    ret, frame = cap.read()
    frame = cv2.flip(frame, 1)

    # Resize for faster detection (hand tracking doesn't need full resolution)
    frame_small = cv2.resize(frame, (PROCESS_WIDTH, PROCESS_HEIGHT))
    rgb = cv2.cvtColor(frame_small, cv2.COLOR_BGR2RGB)
    h, w = PROCESS_HEIGHT, PROCESS_WIDTH

    # MediaPipe Tasks API: wrap numpy frame as Image and detect
    mp_image = MpImage(image_format=ImageFormat.SRGB, data=rgb)
    result = hands.detect(mp_image)

    margin_val = settings["margin"]
    sens = settings["sensitivity"]
    alpha = settings["smoothing_alpha"]
    dead = settings["dead_zone"]
    thresh_left = settings["click_threshold_left"]
    thresh_right = settings["click_threshold_right"]
    cooldown_ms = settings["right_click_cooldown_ms"]
    double_ms = settings["double_click_ms"]

    if result.hand_landmarks:
        for hand_landmarks in result.hand_landmarks:
            # hand_landmarks is list of NormalizedLandmark (x, y in [0, 1])
            landmarks = [
                (int(lm.x * w), int(lm.y * h)) for lm in hand_landmarks
            ]

            # Index finger tip (8), thumb tip (4), middle finger tip (12)
            index_x, index_y = landmarks[8]
            thumb_x, thumb_y = landmarks[4]
            middle_x, middle_y = landmarks[12]

            # Map to screen with margin (easier to reach corners)
            margin_w = int(w * margin_val)
            margin_h = int(h * margin_val)
            raw_x = np.interp(
                index_x,
                [margin_w, w - margin_w],
                [0, screen_w],
            )
            raw_y = np.interp(
                index_y,
                [margin_h, h - margin_h],
                [0, screen_h],
            )
            raw_x = np.clip(raw_x * sens, 0, screen_w)
            raw_y = np.clip(raw_y * sens, 0, screen_h)

            # Smooth cursor (EMA)
            smoothed_x = alpha * smoothed_x + (1 - alpha) * raw_x
            smoothed_y = alpha * smoothed_y + (1 - alpha) * raw_y

            # Dead zone: only move if change is above threshold
            cur_x, cur_y = pyautogui.position()
            if (
                abs(smoothed_x - cur_x) > dead
                or abs(smoothed_y - cur_y) > dead
            ):
                pyautogui.moveTo(smoothed_x, smoothed_y)

            # Distances for gestures
            dist_thumb_index = distance((thumb_x, thumb_y), (index_x, index_y))
            dist_index_middle = distance((index_x, index_y), (middle_x, middle_y))

            # LEFT CLICK HOLD (with double-click on quick second pinch)
            now_ms = time.perf_counter() * 1000
            if dist_thumb_index < thresh_left:
                if not clicking:
                    # Two quick pinches = double-click (no hold on second)
                    if (
                        last_left_release_time
                        and (now_ms - last_left_release_time) < double_ms
                    ):
                        pyautogui.doubleClick()
                        last_left_release_time = 0
                        did_double_click = True
                    else:
                        pyautogui.mouseDown()
                        did_double_click = False
                    clicking = True
            else:
                if clicking:
                    if not did_double_click:
                        pyautogui.mouseUp()
                        last_left_release_time = now_ms
                    did_double_click = False
                    clicking = False

            # RIGHT CLICK: only on transition (fingers just closed) + cooldown
            right_close = dist_index_middle < thresh_right
            if right_close and not right_was_close:
                if (now_ms - last_right_click_time) >= cooldown_ms:
                    pyautogui.rightClick()
                    last_right_click_time = now_ms
            right_was_close = right_close

            # Draw hand connections on full-size frame for display
            if show_cam:
                mp_drawing.draw_landmarks(
                    frame,
                    hand_landmarks,
                    HandLandmarksConnections.HAND_CONNECTIONS,
                )
    else:
        right_was_close = False

    # Show or hide camera window
    if show_cam:
        cv2.imshow(CAMERA_WINDOW, frame)
    else:
        try:
            cv2.destroyWindow(CAMERA_WINDOW)
        except cv2.error:
            pass

    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()

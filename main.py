import cv2                              # OpenCV -- handles webcam access, image display, drawing
import numpy as np                      # used to build blank placeholder images
import os                               # file path handling
import glob                             # lets us search for files matching a pattern, like "frame_*.jpg"
import time                             # timestamps, used for freshness/staleness checks and FPS math
from hand_tracker import HandTracker    # our own class from hand_tracker.py -- does the MediaPipe hand detection

from login import require_auth
require_auth()   # exits immediately if not logged in

# Absolute path to the folder this script lives in. Must match
# THIS_DIR in camera.py -- both scripts need to agree on exactly where
# frame files live, otherwise main.py would be looking in the wrong place.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# A "glob pattern" -- the * is a wildcard. This matches ANY file in
# this folder named like "frame_something.jpg", no matter what
# "something" (the client id) actually is. This is how we find however
# many friends are currently streaming, without knowing their IDs in advance.
FRAME_GLOB = os.path.join(THIS_DIR, "frame_*.jpg")

# ─────────────────────────────────────────────
# Ask the user, once, at startup, which video source to use.
# This is an explicit choice instead of silently guessing based on
# whether a file happens to exist -- it doesn't change for the rest of
# this run.
# ─────────────────────────────────────────────
print("Choose video source:")
print("  1. Developer mode (receive stream(s) over the network via camera.py + ngrok)")
print("  2. Local webcam")

mode_choice = input("Enter 1 or 2: ").strip()

while mode_choice not in ("1", "2"):
    mode_choice = input("Please enter 1 or 2: ").strip()

USE_NETWORK_MODE = (mode_choice == "1")

if USE_NETWORK_MODE:
    print()
    print("Developer mode selected. Before this will show anything, make sure:")
    print("  1. camera.py is running (in another terminal): py -3.12 camera.py")
    print("  2. if you're trying to see someone else's stream, they run camera.py, not you")
    print("  3. do ngrok http 5050 ")
    print("  4. run main.py")
    print("Ok bye")
   

print("👂 Listening for video source... Press 'q' on any video window to quit.")

cap = None               # local webcam object, only used in local mode
tracker = HandTracker()   # ONE shared tracker, loaded once, used by both modes -- no threads, no per-client trackers

# ── Bookkeeping for network mode (which files we've already shown, etc) ──
last_modified_times = {}      # client_id -> mtime of the last frame we displayed for them
last_update_timestamps = {}   # client_id -> real time we last saw a fresh frame from them
open_windows = set()          # every window title currently open on screen

# ── FPS tracking (shared by both modes) ──
fps_frame_counts = {}      # key -> frames counted since last recalculation
fps_last_check_times = {}  # key -> time we last recalculated FPS for this key
fps_current_values = {}    # key -> most recently calculated FPS value

# ─────────────────────────────────────────────
# Main loop -- runs continuously until 'q' is pressed.
#
# Both modes below end up doing the SAME final steps on whatever raw
# frame(s) they produced: run hand detection, draw the FPS counter,
# and show the window. That shared block sits at the BOTTOM of the
# loop, outside the if/else, written once -- not duplicated inside
# each branch.
# ─────────────────────────────────────────────
while True:
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break

    # frame_jobs collects one or more (window_name, frame, fps_key)
    # tuples to process this loop -- "one or more" because network
    # mode might have several friends connected at once, while local
    # mode only ever has one camera. Either way, everything in this
    # list gets run through the exact same detection/display step below.
    frame_jobs = []

    if USE_NETWORK_MODE:
        # ════════════════════════════════════════════════════
        # DEVELOPER MODE: find every frame_<id>.jpg file that
        # currently exists, and queue each one up to be processed.
        # ════════════════════════════════════════════════════

        active_paths = glob.glob(FRAME_GLOB)
        active_ids = set()

        for path in active_paths:
            filename = os.path.basename(path)
            client_id = filename[len("frame_"):-len(".jpg")]
            active_ids.add(client_id)
            window_name = f"Friend: {client_id}"

            try:
                current_modified_time = os.path.getmtime(path)
                is_fresh = current_modified_time != last_modified_times.get(client_id)

                if is_fresh:
                    frame = cv2.imread(path)
                    if frame is not None and frame.size > 0:
                        last_modified_times[client_id] = current_modified_time
                        last_update_timestamps[client_id] = time.time()
                        # Queue this frame up for detection + display below.
                        frame_jobs.append((window_name, frame, client_id))
                else:
                    # Not updated this exact loop, but recent enough
                    # (<=15s) -- nothing new to process, skip.
                    elapsed = time.time() - last_update_timestamps.get(client_id, 0)
                    if elapsed > 15:
                        # Gone stale -- close their window, forget them.
                        if window_name in open_windows:
                            cv2.destroyWindow(window_name)
                            open_windows.discard(window_name)
                        last_modified_times.pop(client_id, None)
                        last_update_timestamps.pop(client_id, None)
                        fps_frame_counts.pop(client_id, None)
                        fps_last_check_times.pop(client_id, None)
                        fps_current_values.pop(client_id, None)

            except Exception:
                continue

        # Close windows for any client whose file disappeared entirely
        # (they clicked Stop, or camera.py's watchdog cleaned them up).
        known_ids = set(last_modified_times.keys())
        vanished_ids = known_ids - active_ids
        for client_id in vanished_ids:
            window_name = f"Friend: {client_id}"
            if window_name in open_windows:
                cv2.destroyWindow(window_name)
                open_windows.discard(window_name)
            last_modified_times.pop(client_id, None)
            last_update_timestamps.pop(client_id, None)
            fps_frame_counts.pop(client_id, None)
            fps_last_check_times.pop(client_id, None)
            fps_current_values.pop(client_id, None)

        if not active_ids and not open_windows:
            # Nobody connected at all -- show one waiting placeholder.
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "WAITING FOR STREAM(S)...", (50, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)
            cv2.imshow("Webcam", placeholder)
            open_windows.add("Webcam")
        elif "Webcam" in open_windows and active_ids:
            cv2.destroyWindow("Webcam")
            open_windows.discard("Webcam")

        time.sleep(0.01)

    else:
        # ════════════════════════════════════════════════════
        # LOCAL WEBCAM MODE: read one frame from the local camera
        # and queue it up the exact same way network mode queues
        # up each friend's frame above.
        # ════════════════════════════════════════════════════

        if cap is None:
            cap = cv2.VideoCapture(0)

        ret, frame = cap.read()

        if not ret:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "LOCAL HARDWARE NOT FOUND", (80, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
            cv2.imshow("Webcam", frame)
            time.sleep(0.01)
            continue   # nothing real to detect on, skip the shared step below

        frame_jobs.append(("Webcam", frame, "local"))

    # ─────────────────────────────────────────────
    # SHARED STEP -- outside the if/else, written once.
    # Runs hand detection, draws the FPS counter, and displays the
    # window, for whatever frame(s) got queued up above -- whether
    # that's one local webcam frame, or several friends' frames in
    # network mode. This is the ONE place find_hands() gets called.
    # ─────────────────────────────────────────────
    for window_name, frame, fps_key in frame_jobs:
        frame = tracker.find_hands(frame)   # the actual hand-tracking logic
        # FPS calculation: count this frame, recalc once per second.
        rn = time.time()
        if fps_key not in fps_last_check_times:
            fps_last_check_times[fps_key] = rn
            fps_frame_counts[fps_key] = 0
            fps_current_values[fps_key] = 0.0

        fps_frame_counts[fps_key] += 1
        elapsed = rn - fps_last_check_times[fps_key]
        if elapsed >= 1.0:
            fps_current_values[fps_key] = fps_frame_counts[fps_key] / elapsed
            fps_frame_counts[fps_key] = 0
            fps_last_check_times[fps_key] = rn

        fps_text = f"FPS: {fps_current_values[fps_key]:.1f}"
        cv2.putText(frame, fps_text, (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)

        cv2.imshow(window_name, frame)
        open_windows.add(window_name)

# ── Cleanup ──
if cap is not None:
    cap.release()
cv2.destroyAllWindows()

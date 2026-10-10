"""
camera.py

A single Flask app, one port, one ngrok tunnel -- supporting MULTIPLE
simultaneous streamers instead of just one.

Each browser that opens this page generates its own random client ID
when the page loads. Frames get written to frame_<client_id>.jpg
instead of one shared shared_frame.jpg, so two (or more) friends
streaming at once don't overwrite each other.

  GET  /              -> serves the camera capture page
  POST /frame/<cid>   -> receives a JPEG frame from client <cid>
  POST /stop/<cid>    -> client <cid> clicked Stop, deletes their frame file

main.py scans this folder for any frame_*.jpg files and opens one
OpenCV window per active streamer, dynamically.
"""

import os                                      # file path handling
import re                                      # regex, used to validate client IDs
import time                                    # timestamps, for the watchdog
import base64                                  # decoding the image data the browser sends
import threading                               # runs the watchdog loop in the background

import cv2                                     # decodes/saves JPEG image data
import numpy as np                             # turns raw bytes into an array cv2 can decode
from flask import Flask, request, Response     # the actual web server framework
from flask_cors import CORS                    # allows the browser to call this server from a different origin

from login import require_auth
require_auth()   # exits immediately if not logged in

# ─────────────────────────────────────────────────────────
# 0. Config
# ─────────────────────────────────────────────────────────

# Absolute path to the folder this script lives in. Using an absolute
# path (not a relative one like "shared_frame.jpg") means frame files
# always land in the same place no matter what folder you happened to
# launch this script from -- this is what fixed the "lost the file"
# issue from earlier.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Every streamer's frame file is named frame_<client_id>.jpg.
# main.py looks for this exact same prefix/suffix pattern, so if you
# change these, you'd need to change main.py too.
FRAME_PREFIX = "frame_"
FRAME_SUFFIX = ".jpg"

# A client_id will get inserted directly into a filename AND a URL, so
# we restrict it to only safe characters (letters, numbers, dash,
# underscore), 1-64 of them. This blocks someone from sending a
# malicious client_id designed to break out of our folder (e.g. trying
# to use "../../something" to write files elsewhere on disk).
VALID_CLIENT_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# A simple shared password that the browser must send with every
# request, so random strangers who find your ngrok URL can't post fake
# frames or stop your friends' streams. Set via environment variable,
# or falls back to a default if you didn't set one.
SHARED_SECRET = os.environ.get("FRAME_SECRET", "change-me-please")

# Tracks the LAST TIME each client sent a frame: {client_id: timestamp}.
# The watchdog (further down) uses this to figure out which streamers
# have gone quiet and need cleaning up -- per-client, not all-or-nothing.
last_frame_time = {}

# Since Flask can handle multiple requests at the same time (different
# friends posting frames simultaneously), multiple threads could try to
# read/write last_frame_time at once. This Lock makes sure only one
# thread touches that dictionary at a time, avoiding rare data-corruption
# bugs that can happen when two threads edit a dict simultaneously.
last_frame_time_lock = threading.Lock()

app = Flask(__name__)   # create the actual Flask web application object
CORS(app)                # allow browsers from other origins (like your ngrok domain) to call this server's endpoints


def _check_auth(req):
    # Compares the X-Auth header on an incoming request against our
    # SHARED_SECRET. Returns True only if they match exactly.
    return req.headers.get("X-Auth") == SHARED_SECRET


def _frame_path(client_id):
    # Builds the full file path for a given client's frame file, e.g.
    # /your/folder/frame_alice123.jpg
    return os.path.join(THIS_DIR, f"{FRAME_PREFIX}{client_id}{FRAME_SUFFIX}")


# ─────────────────────────────────────────────────────────
# 1. The page each friend opens (HTML + embedded JavaScript)
# ─────────────────────────────────────────────────────────
# This entire block is a string containing a full webpage. Flask will
# serve this exact text whenever someone visits "/". The {{ and }} are
# NOT normal HTML/JS syntax -- they're doubled up because this string
# uses Python's .format() later, and in a Python format string, a
# literal { or } needs to be doubled to "escape" it (otherwise Python
# would think it's a placeholder like {shared_secret}).

PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>Sign Language Camera</title>
<style>
  /* Basic dark-themed page styling -- purely visual, no logic here */
  body {{ margin: 0; background: #0e1117; display: flex; flex-direction: column;
         align-items: center; font-family: sans-serif; color: #fafafa; padding: 16px; }}
  video {{ width: 640px; height: 480px; border-radius: 8px; background: #1a1a2e;
          display: block; transform: scaleX(-1); }}  /* scaleX(-1) mirrors the video like a real mirror */
  canvas {{ display: none; }}                          /* the canvas is just used internally to grab frames, never shown */
  #status {{ margin-top: 10px; font-size: 13px; color: #888; }}
  #clientid {{ margin-top: 4px; font-size: 11px; color: #555; }}
  button {{ margin-top: 14px; padding: 10px 28px; border-radius: 6px; border: none;
           font-size: 15px; cursor: pointer; font-weight: 600; }}
  #startBtn {{ background: #ff4b4b; color: white; }}
  #stopBtn  {{ background: #333; color: #ccc; display: none; }}  /* hidden until streaming starts */
</style>
</head>
<body>
  <h2>🎥 Camera Bridge</h2>
  <video id="video" autoplay playsinline muted></video>     <!-- shows the live camera preview -->
  <canvas id="canvas" width="640" height="480"></canvas>    <!-- invisible scratchpad used to grab still frames -->
  <div id="status">Click Start to begin streaming your camera.</div>
  <div id="clientid"></div>                                   <!-- shows this session's random ID, for debugging -->
  <div>
    <button id="startBtn" onclick="startStream()">▶ Start</button>
    <button id="stopBtn"  onclick="stopStream()">■ Stop</button>
  </div>

<script>
  // ---- JavaScript runs in the FRIEND'S BROWSER, not on your computer ----
  let stream = null;        // will hold the camera stream object once started
  let intervalId = null;     // the repeating timer that sends frames

  // Grab references to the HTML elements above so we can control them
  const video  = document.getElementById('video');
  const canvas = document.getElementById('canvas');
  const ctx    = canvas.getContext('2d');   // lets us draw the video frame onto the canvas
  const status = document.getElementById('status');

  // window.location.origin = "whatever URL this page was loaded from"
  // (e.g. your ngrok URL). This means the JS doesn't need a hardcoded
  // server address -- it just talks back to wherever it came from.
  const BASE = window.location.origin;

  // The shared password gets baked into the page itself by Python's
  // .format() down below, so this browser can prove it's allowed to
  // post frames.
  const SHARED_SECRET = '{shared_secret}';

  // Generates a random-ish unique ID for this browser session, e.g.
  // "k3j9x2p1mq7r3f8a". Combines a random number with the current
  // time so two people loading the page at the same moment still get
  // different IDs.
  function makeClientId() {{
    return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }}
  const CLIENT_ID = makeClientId();   // generated once, when the page loads
  document.getElementById('clientid').textContent = 'Session ID: ' + CLIENT_ID;

  async function startStream() {{
    try {{
      // Ask the browser for camera access. This is what triggers the
      // "Allow camera access?" popup. video:true, audio:false means we
      // only want video, no microphone.
      stream = await navigator.mediaDevices.getUserMedia({{ video: true, audio: false }});

      video.srcObject = stream;   // pipe the live camera feed into the <video> element so it's visible

      // Swap which button is showing
      document.getElementById('startBtn').style.display = 'none';
      document.getElementById('stopBtn').style.display  = 'inline-block';

      status.textContent = '🟢 Streaming -- sending frames to server...';

      // Call sendFrame() every 50 milliseconds = ~20 times per second (20fps)
      intervalId = setInterval(sendFrame, 50);
    }} catch (err) {{
      // Happens if there's no camera, or the user denies permission
      status.textContent = '❌ Camera error: ' + err.message;
    }}
  }}

  async function stopStream() {{
    clearInterval(intervalId);                              // stop sending frames
    if (stream) stream.getTracks().forEach(t => t.stop());  // actually release the camera hardware
    video.srcObject = null;                                  // clear the preview

    document.getElementById('startBtn').style.display = 'inline-block';
    document.getElementById('stopBtn').style.display  = 'none';
    status.textContent = 'Stopped.';

    // Tell the server "I'm done" so it deletes my frame file right away,
    // instead of waiting for the 5-second watchdog timeout.
    await fetch(BASE + '/stop/' + CLIENT_ID, {{
      method: 'POST',
      headers: {{
        'X-Auth': SHARED_SECRET,
        'ngrok-skip-browser-warning': 'true'   // bypasses ngrok's free-tier "are you a robot" interstitial page
      }}
    }}).catch(() => {{}});   // if this fails for any reason, just ignore it -- not critical
  }}

  function sendFrame() {{
    if (!video.videoWidth) return;   // camera hasn't actually produced a frame yet, skip this tick

    // Draw the CURRENT video frame onto the invisible canvas, at
    // exactly 640x480 pixels.
    ctx.drawImage(video, 0, 0, 640, 480);

    // Convert whatever's on the canvas into a JPEG image, encoded as a
    // base64 text string (this is what "data:image/jpeg;base64,...."
    // means -- it's an image file's bytes represented as text so it
    // can be put into a JSON request).
    const dataURL = canvas.toDataURL('image/jpeg', 0.5);   // SPEED FIX: lowered from 0.6 -- smaller file = faster upload over ngrok, modest quality tradeoff

    // Send that image to our own server's /frame/<CLIENT_ID> endpoint.
    fetch(BASE + '/frame/' + CLIENT_ID, {{
      method: 'POST',
      headers: {{
        'Content-Type': 'application/json',
        'X-Auth': SHARED_SECRET,
        'ngrok-skip-browser-warning': 'true'
      }},
      body: JSON.stringify({{ frame: dataURL }})   // wrap the image data in a JSON object
    }}).catch(() => {{
      status.textContent = '⚠️ Frame send failed -- is the server still running?';
    }});
  }}

  // If the person closes the tab or navigates away, try to tell the
  // server to stop/clean up. sendBeacon is a special browser feature
  // for "fire this request even as the page is closing" -- a normal
  // fetch() call might get cancelled mid-flight in that situation.
  window.addEventListener('beforeunload', () => {{
    navigator.sendBeacon(BASE + '/stop/' + CLIENT_ID);
  }});
</script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    # Whenever someone visits the site's root URL with a normal GET
    # request (i.e. just opening the link in a browser), fill in the
    # {shared_secret} placeholder inside PAGE_HTML with the real secret,
    # and send the resulting HTML back to them.
    html = PAGE_HTML.format(shared_secret=SHARED_SECRET)
    return Response(html, mimetype="text/html")


# ─────────────────────────────────────────────────────────
# 2. Per-client frame receiving + stop + watchdog
# ─────────────────────────────────────────────────────────

@app.route("/frame/<client_id>", methods=["POST"])
def receive_frame(client_id):
    # <client_id> in the route above means Flask automatically captures
    # whatever's in that part of the URL and passes it in as the
    # client_id argument to this function.

    if not _check_auth(request):
        # Wrong or missing password -- reject immediately, don't process anything.
        return {"status": "unauthorized"}, 401

    if not VALID_CLIENT_ID.match(client_id):
        # client_id contains characters we don't allow (could be an
        # attack attempt, or just a malformed request) -- reject it
        # before it ever touches the filesystem.
        return {"status": "invalid client id"}, 400

    try:
        # Parse the incoming JSON body, e.g. {"frame": "data:image/jpeg;base64,...."}
        data = request.get_json(force=True)

        # The string looks like "data:image/jpeg;base64,<actual base64 data>"
        # -- splitting on the comma and taking [1] discards the
        # "data:image/jpeg;base64," prefix and keeps just the real data.
        img_data = data["frame"].split(",")[1]

        # Convert the base64 TEXT back into actual binary image bytes.
        # SPEED FIX: the browser already gave us valid JPEG bytes, so we
        # write them straight to disk as-is -- no decode + re-encode
        # round trip. The old version did cv2.imdecode() then
        # cv2.imwrite() with a NEW quality setting, which both burns
        # CPU time and re-compresses an already-compressed image for
        # no benefit. This is the single biggest lag-reduction change here.
        img_bytes = base64.b64decode(img_data)

        if len(img_bytes) > 0:
            with open(_frame_path(client_id), "wb") as f:
                f.write(img_bytes)

            # Record "this client just sent a frame, right now" --
            # the watchdog uses this to detect who's gone silent.
            with last_frame_time_lock:
                last_frame_time[client_id] = time.time()

        return {"status": "ok"}, 200

    except Exception as e:
        # Anything went wrong (bad data, decode failure, etc) -- report
        # it back instead of crashing the whole server.
        return {"status": "error", "detail": str(e)}, 500


@app.route("/stop/<client_id>", methods=["POST"])
def stop_stream(client_id):
    if not _check_auth(request):
        return {"status": "unauthorized"}, 401
    if not VALID_CLIENT_ID.match(client_id):
        return {"status": "invalid client id"}, 400

    try:
        path = _frame_path(client_id)
        if os.path.exists(path):
            os.remove(path)   # delete this client's frame file -- main.py will notice it's gone and close their window
    except Exception:
        pass   # if deleting fails for some reason, don't crash -- just move on

    with last_frame_time_lock:
        last_frame_time.pop(client_id, None)   # stop tracking this client; .pop(key, None) means "remove if present, don't error if not"

    return {"status": "stopped"}, 200


def watchdog():
    """Runs forever in the background, in its own thread. Every 2
    seconds, checks if any client has gone quiet for more than 5
    seconds (tab closed, app crashed, wifi dropped, etc) and cleans up
    JUST their file, leaving everyone else's stream untouched."""
    while True:
        time.sleep(2)   # don't check constantly -- every 2 seconds is plenty

        now = time.time()

        with last_frame_time_lock:
            # Build a list of every client_id whose last frame was more
            # than 5 seconds ago.
            stale_clients = [
                cid for cid, t in last_frame_time.items()
                if now - t > 5
            ]
            # Stop tracking those clients now that we've identified them.
            for cid in stale_clients:
                del last_frame_time[cid]

        # Delete each stale client's frame file (done outside the lock,
        # since file deletion doesn't need to touch the shared dictionary).
        for cid in stale_clients:
            path = _frame_path(cid)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass



# This code now runs every time camera.py is run directly OR imported
# by something else (no more "only if run directly" guard). That gives
# flexibility to import this file elsewhere and have the server start
# automatically -- just be aware: if this file ever gets imported
# twice in the same running program, this will try to bind port 5050
# a second time and crash, since two things can't listen on the same
# port at once.

# Start the watchdog as a background "daemon" thread -- daemon=True
# means this thread won't prevent the program from exiting if you
# close the main script.
threading.Thread(target=watchdog, daemon=True).start()

# Print some helpful startup info to the terminal.
print(f"Frames will be written as: {os.path.join(THIS_DIR, FRAME_PREFIX + '<client_id>' + FRAME_SUFFIX)}")
print(f"Shared secret: {SHARED_SECRET}")
print("Run `ngrok http 5050` in another terminal, then send friends the ngrok URL.")
print("Each friend who opens the link gets their own stream automatically.")

# Actually start the Flask web server.
#   host="0.0.0.0" -> listen on all network interfaces (needed so
#                     ngrok can forward requests to it)
#   port=5050      -> the port number ngrok will tunnel into
#   debug=False    -> don't run in Flask's debug mode (debug mode
#                     can expose stack traces publicly -- not safe)
#   use_reloader=False -> don't auto-restart on file changes (avoids
#                          double-starting threads like the watchdog)
app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False)
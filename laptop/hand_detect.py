"""
Hand Detection using ESP32-CAM + MediaPipe on Laptop
=====================================================
- Fetches JPEG frames from ESP32-CAM /capture endpoint
- Runs MediaPipe Hands to detect presence of hand(s)
- Prints result to terminal
- Serves a simple web page at http://localhost:5000 showing
  live camera feed + detection status

Usage:
  1. Make sure ESP32-CAM is running and connected to Wi-Fi.
  2. Update ESP_IP below with the IP shown in serial monitor.
  3. pip install -r requirements.txt
  4. python hand_detect.py
  5. Open http://localhost:5000 in your browser.
"""

import cv2
import numpy as np
import requests
import mediapipe as mp
import threading
import time
import base64
from flask import Flask, Response, render_template_string

# ──────────────────────────────────────────────
# CONFIGURATION — update ESP_IP to match yours
# ──────────────────────────────────────────────
ESP_IP = "192.168.1.8"  # <-- change this to your ESP32-CAM IP
CAPTURE_URL = f"http://{ESP_IP}/capture"
LED_URL     = f"http://{ESP_IP}/led"  # triggers ESP onboard LED
POLL_INTERVAL = 0.3  # seconds between captures

# LED trigger behaviour
HAND_HOLDOFF    = 0.5   # hand must be visible this long before first trigger (s)
RETRIGGER_AFTER = 1.5   # re-trigger every this many seconds while hand stays visible (s)
LED_ON_DURATION = 2.0   # how long ESP keeps LED on per trigger (s)

# ──────────────────────────────────────────────
# MediaPipe Hands setup
# ──────────────────────────────────────────────
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils

# ──────────────────────────────────────────────
# Shared state (updated by background threads)
# ──────────────────────────────────────────────
latest_raw_frame  = None
latest_annotated  = None
hand_detected     = False
num_hands         = 0
frame_lock        = threading.Lock()
result_lock       = threading.Lock()
new_frame_event   = threading.Event()

# LED trigger state
hand_present_since = None   # time hand first became continuously visible
last_trigger_time  = None   # last time we sent /led to ESP
led_active_until   = 0.0    # used only for HUD display
led_state_lock     = threading.Lock()

def trigger_led():
    """Fire a non-blocking HTTP GET to ESP /led endpoint."""
    def _send():
        try:
            requests.get(LED_URL, timeout=1)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()

# ──────────────────────────────────────────────
# Thread 1: Capture — fetches frames as fast as possible
# ──────────────────────────────────────────────
def capture_loop():
    global latest_raw_frame
    session = requests.Session()
    while True:
        try:
            resp = session.get(CAPTURE_URL, timeout=5)
            if resp.status_code != 200:
                time.sleep(0.5)
                continue
            img_array = np.frombuffer(resp.content, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if frame is not None:
                with frame_lock:
                    latest_raw_frame = frame
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Capture: {e}")
            time.sleep(2)
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(0.5)

# ──────────────────────────────────────────────
# Thread 2: Detection — runs MediaPipe on latest frame
# ──────────────────────────────────────────────
def detection_loop():
    global latest_annotated, hand_detected, num_hands
    global hand_present_since, last_trigger_time, led_active_until
    hands_model = mp_hands.Hands(
        static_image_mode=False,       # stream mode = faster
        max_num_hands=2,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    while True:
        with frame_lock:
            frame = latest_raw_frame.copy() if latest_raw_frame is not None else None

        if frame is None:
            time.sleep(0.05)
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands_model.process(rgb)

        detected = results.multi_hand_landmarks is not None
        count = len(results.multi_hand_landmarks) if detected else 0

        # ── LED trigger logic ──────────────────────────────────────────
        now = time.time()
        with led_state_lock:
            if detected:
                if hand_present_since is None:
                    hand_present_since = now          # hand just appeared
                time_visible = now - hand_present_since
                if time_visible >= HAND_HOLDOFF:      # held for 0.5s?
                    if last_trigger_time is None or (now - last_trigger_time) >= RETRIGGER_AFTER:
                        last_trigger_time = now
                        led_active_until  = now + LED_ON_DURATION
                        trigger_led()
                        print(f"\n[LED] Triggered (hand visible {time_visible:.2f}s)")
            else:
                hand_present_since = None             # reset on no-hand
                last_trigger_time  = None
            led_on = now < led_active_until
        # ───────────────────────────────────────────────────────────────

        annotated = frame.copy()
        if detected:
            for hand_landmarks in results.multi_hand_landmarks:
                mp_drawing.draw_landmarks(
                    annotated, hand_landmarks, mp_hands.HAND_CONNECTIONS)

        label = f"HAND DETECTED ({count})" if detected else "NO  HAND"
        color = (0, 255, 0) if detected else (0, 0, 255)
        cv2.putText(annotated, label, (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

        # Show LED status and holdoff progress on HUD
        if detected and hand_present_since is not None:
            progress = min(time.time() - hand_present_since, HAND_HOLDOFF)
            pct = int(progress / HAND_HOLDOFF * 100)
            if pct < 100:
                cv2.putText(annotated, f"Hold... {pct}%", (10, 80),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
        if led_on:
            cv2.putText(annotated, "LED ON", (10, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

        _, buf = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])

        with result_lock:
            latest_annotated = buf.tobytes()
            hand_detected = detected
            num_hands = count
        new_frame_event.set()  # wake up gen_frames

        print(f"[{'HAND' if detected else '----'}] Hands: {count}   ", end='\r')

# ──────────────────────────────────────────────
# Flask web server
# ──────────────────────────────────────────────
app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>Hand Detection - ESP32-CAM</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #111; color: #eee;
      font-family: 'Segoe UI', sans-serif;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      min-height: 100vh;
    }
    h1 { margin: 20px 0 10px; font-size: 1.5em; }
    #status {
      font-size: 2em; font-weight: bold;
      padding: 10px 30px; border-radius: 10px;
      margin: 10px 0 20px;
      transition: all 0.3s;
    }
    .detected { background: #2e7d32; color: #fff; }
    .not-detected { background: #c62828; color: #fff; }
    img {
      max-width: 90vw; max-height: 70vh;
      border: 3px solid #333; border-radius: 8px;
    }
    .info { margin-top: 15px; color: #888; font-size: 0.9em; }
  </style>
</head>
<body>
  <h1>ESP32-CAM Hand Detection</h1>
  <div id="status" class="not-detected">NO HAND</div>
  <img id="feed" src="/video_feed" alt="Camera Feed">
  <p class="info">Powered by MediaPipe Hands + ESP32-CAM</p>
  <script>
    async function pollStatus() {
      try {
        const resp = await fetch('/status');
        const data = await resp.json();
        const el = document.getElementById('status');
        if (data.hand_detected) {
          el.textContent = 'HAND DETECTED (' + data.num_hands + ')';
          el.className = 'detected';
        } else {
          el.textContent = 'NO HAND';
          el.className = 'not-detected';
        }
      } catch(e) {}
      setTimeout(pollStatus, 500);
    }
    pollStatus();
  </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/status')
def status():
    with result_lock:
        return {'hand_detected': hand_detected, 'num_hands': num_hands}

def gen_frames():
    """Yield annotated JPEG frames as MJPEG stream."""
    while True:
        # Wait up to 2s for a new frame from detection_loop
        new_frame_event.wait(timeout=2.0)
        new_frame_event.clear()
        with result_lock:
            frame = latest_annotated
        if frame is None:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 50)
    print("  Hand Detection — ESP32-CAM + MediaPipe")
    print(f"  ESP Camera: http://{ESP_IP}/capture")
    print("  Web UI:     http://localhost:5000")
    print("=" * 50)
    print()

    # Start capture thread (fast, just fetches frames)
    t1 = threading.Thread(target=capture_loop, daemon=True)
    t1.start()

    # Start detection thread (MediaPipe, runs on latest frame)
    t2 = threading.Thread(target=detection_loop, daemon=True)
    t2.start()

    # Start Flask web server
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

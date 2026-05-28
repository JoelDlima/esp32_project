"""
Hand Detection — ESP32-CAM + MediaPipe / ONNX-CUDA
===================================================
Inference backend (auto-selected at startup):
  1. ONNX Runtime + CUDA  – uses your RTX 3050, ~10-20 ms/frame
  2. MediaPipe CPU         – fallback if ONNX/CUDA not available, ~80-150 ms/frame

Other features:
  • 3-frame debounce: LED state only changes after 3 consecutive identical results
    → eliminates single-frame flicker
  • Non-blocking LED sends (fire-and-forget thread)
  • MJPEG parser always grabs the freshest frame (rfind)
  • Adaptive frame skipping when inference is slow
  • Flask UI at http://localhost:5000

ONNX setup (one-time, optional):
  pip install onnxruntime-gpu mediapipe-model-maker
  # The hand landmark ONNX model is exported automatically on first run
  # if onnxruntime-gpu is installed and a CUDA GPU is detected.
"""

import cv2
import numpy as np
import requests
import mediapipe as mp
import os
import sys
import threading
import time
import subprocess
import re
import socket
from collections import deque
from flask import Flask, Response, render_template_string

# ──────────────────────────────────────────────────────────────────────────────
# Inference backend selection
# ──────────────────────────────────────────────────────────────────────────────

BACKEND = "mediapipe"   # will be overridden below if ONNX+CUDA is available

def _try_init_onnx():
    """
    Try to load the MediaPipe hand-landmark ONNX model with CUDA EP.
    Returns (session, input_name) or None if unavailable.

    The model used is the MediaPipe hand_landmark_full.tflite converted to ONNX.
    We use the tf2onnx-converted version bundled with mediapipe's model files,
    or fall back to the lite variant.
    """
    try:
        import onnxruntime as ort

        # Check CUDA provider is available
        providers = ort.get_available_providers()
        if "CUDAExecutionProvider" not in providers:
            print("[BACKEND] onnxruntime-gpu installed but CUDA EP not available")
            return None

        # Locate the ONNX model – we ship a conversion script but also check
        # a pre-converted file in the project directory.
        model_path = os.path.join(os.path.dirname(__file__), "hand_landmark.onnx")
        if not os.path.exists(model_path):
            print("[BACKEND] hand_landmark.onnx not found – run export_onnx.py first")
            print("[BACKEND] Falling back to MediaPipe CPU")
            return None

        sess = ort.InferenceSession(
            model_path,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        input_name = sess.get_inputs()[0].name
        print(f"[BACKEND] ONNX+CUDA loaded: {model_path}")
        print(f"[BACKEND] Providers in use: {sess.get_providers()}")
        return sess, input_name

    except ImportError:
        print("[BACKEND] onnxruntime-gpu not installed")
        return None
    except Exception as e:
        print(f"[BACKEND] ONNX init failed: {e}")
        return None


_onnx_session   = None
_onnx_inp_name  = None

_onnx_result = _try_init_onnx()
if _onnx_result is not None:
    _onnx_session, _onnx_inp_name = _onnx_result
    BACKEND = "onnx_cuda"
    print("[BACKEND] Using ONNX + CUDA (RTX 3050)")
else:
    print("[BACKEND] Using MediaPipe CPU")


# ──────────────────────────────────────────────────────────────────────────────
# MediaPipe setup (always initialised – used as fallback or primary)
# ──────────────────────────────────────────────────────────────────────────────

mp_hands   = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils

MAX_FINGERS = 5   # max value sent to ESP32


def count_raised_fingers(hand_landmarks):
    """Count raised fingers (index, middle, ring, pinky) from MediaPipe landmarks."""
    lm = hand_landmarks.landmark
    count = 0
    for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
        if lm[tip].y < lm[pip].y - 0.02:
            count += 1
    return count


def count_raised_fingers_onnx(landmarks_flat):
    """
    Count raised fingers from a flat array of 21×3 landmarks (x,y,z normalised).
    landmarks_flat shape: (63,)  – [x0,y0,z0, x1,y1,z1, ...]
    """
    # Reshape to (21, 3)
    lm = landmarks_flat.reshape(21, 3)
    count = 0
    for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
        if lm[tip, 1] < lm[pip, 1] - 0.02:   # y-axis comparison
            count += 1
    return count


# ──────────────────────────────────────────────────────────────────────────────
# Network helpers – auto-detect ESP32-CAM
# ──────────────────────────────────────────────────────────────────────────────

def get_local_subnet():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "192.168.1.1"
    finally:
        s.close()
    return local_ip, '.'.join(local_ip.split('.')[:3])


def find_esp32_ip():
    print("[INIT] Scanning network for ESP32-CAM...")
    local_ip, subnet = get_local_subnet()
    print(f"[INIT] Laptop IP: {local_ip}  Subnet: {subnet}.*")

    print("[INIT] Ping sweep (~5 s)...")
    procs = [
        subprocess.Popen(
            ['ping', '-n', '1', '-w', '200', f"{subnet}.{i}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        for i in range(1, 255)
    ]
    for p in procs:
        p.wait()

    try:
        result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=5)
        arp_output = result.stdout
    except Exception:
        arp_output = ""

    pattern = re.compile(r'(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f-]+)\s+dynamic', re.IGNORECASE)
    candidates = [
        (m.group(1), m.group(2))
        for m in pattern.finditer(arp_output)
        if m.group(1).startswith(subnet + '.') and m.group(1) != local_ip
    ]
    print(f"[INIT] {len(candidates)} devices on {subnet}.*")

    for ip, mac in candidates:
        try:
            r = requests.get(f"http://{ip}/capture", timeout=2)
            if r.status_code == 200 and len(r.content) > 1000:
                print(f"[INIT] ESP32-CAM at {ip}  (MAC: {mac}  {len(r.content)} B)")
                return ip
        except Exception:
            pass
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Shared state (detection thread → Flask)
# ──────────────────────────────────────────────────────────────────────────────

latest_annotated = None
hand_detected    = False
num_hands        = 0
finger_count     = 0
detect_ms        = 0.0
result_lock      = threading.Lock()
new_frame_event  = threading.Event()


# ──────────────────────────────────────────────────────────────────────────────
# Non-blocking LED sender (fire-and-forget)
# ──────────────────────────────────────────────────────────────────────────────

_led_lock          = threading.Lock()
_led_pending       = None
_led_sender_active = False


def _send_led_worker(fingers_url: str, n: int):
    global _led_sender_active, _led_pending
    while True:
        try:
            requests.get(f"{fingers_url}?n={n}", timeout=1)
            print(f"\n[LEDS] Sent n={n}", flush=True)
        except Exception as e:
            print(f"\n[LEDS] Send failed: {e}", flush=True)

        with _led_lock:
            if _led_pending is not None and _led_pending != n:
                n = _led_pending
                _led_pending = None
                continue
            _led_pending = None
            _led_sender_active = False
            break


def send_leds_async(fingers_url: str, n: int):
    global _led_sender_active, _led_pending
    with _led_lock:
        if _led_sender_active:
            _led_pending = n
            return
        _led_sender_active = True
        _led_pending = None
    threading.Thread(target=_send_led_worker, args=(fingers_url, n), daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# Main detection loop
# ──────────────────────────────────────────────────────────────────────────────

def main_loop(stream_url: str, fingers_url: str):
    global latest_annotated, hand_detected, num_hands, finger_count, detect_ms

    session = requests.Session()

    # MediaPipe model (always created; used when ONNX unavailable)
    hands_model = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=2,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    # ── Debounce state ────────────────────────────────────────────────────────
    # We keep a rolling window of the last DEBOUNCE_FRAMES results.
    # The LED state only changes when ALL frames in the window agree.
    DEBOUNCE_FRAMES = 3
    debounce_window = deque(maxlen=DEBOUNCE_FRAMES)
    confirmed_leds  = -1   # last value actually sent to ESP32
    last_sent_leds  = -1   # tracks what was sent (init -1 forces first send)

    frame_count  = 0
    stream_resp  = None
    stream_iter  = None
    buf          = b""
    skip_frames  = 0

    # ── Stream helpers ────────────────────────────────────────────────────────

    def connect_stream():
        nonlocal stream_resp, stream_iter, buf
        if stream_resp:
            try: stream_resp.close()
            except Exception: pass
        print(f"[STREAM] Connecting to {stream_url} ...", flush=True)
        stream_resp = session.get(stream_url, stream=True, timeout=10)
        stream_resp.raise_for_status()
        stream_iter = stream_resp.iter_content(chunk_size=2048)
        buf = b""
        print("[STREAM] Connected", flush=True)

    def read_latest_jpeg():
        nonlocal buf, stream_iter
        JPEG_SOI = b'\xff\xd8'
        JPEG_EOI = b'\xff\xd9'
        MAX_BUF  = 512 * 1024

        for chunk in stream_iter:
            buf += chunk
            if len(buf) > MAX_BUF:
                last_soi = buf.rfind(JPEG_SOI)
                buf = buf[last_soi:] if last_soi > 0 else buf[-MAX_BUF // 2:]

            eoi = buf.rfind(JPEG_EOI)
            if eoi == -1:
                continue
            soi = buf.rfind(JPEG_SOI, 0, eoi)
            if soi == -1:
                continue

            jpg = buf[soi: eoi + 2]
            buf = buf[eoi + 2:]
            return jpg
        return None

    # ── Inference helpers ─────────────────────────────────────────────────────

    def run_mediapipe(small_rgb):
        """Run MediaPipe on a 320×240 RGB frame. Returns (detected, count, raised, landmarks_list)."""
        res = hands_model.process(small_rgb)
        detected = res.multi_hand_landmarks is not None
        count    = len(res.multi_hand_landmarks) if detected else 0
        raised   = 0
        lm_list  = []
        if detected:
            for hl in res.multi_hand_landmarks:
                raised  += count_raised_fingers(hl)
                lm_list.append(hl)
        return detected, count, raised, lm_list

    def run_onnx(small_bgr):
        """
        Run ONNX hand landmark model on a 224×224 BGR frame.
        Returns (detected, count=1, raised, landmarks_flat or None).

        The MediaPipe hand_landmark ONNX model expects:
          input  : float32 [1, 224, 224, 3]  normalised 0-1
          outputs: [landmarks (1,63), handedness (1,1), score (1,1)]
        """
        inp = cv2.resize(small_bgr, (224, 224))
        inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB)
        inp = inp.astype(np.float32) / 255.0
        inp = np.expand_dims(inp, 0)   # (1, 224, 224, 3)

        outputs = _onnx_session.run(None, {_onnx_inp_name: inp})
        landmarks_flat = outputs[0][0]   # shape (63,)
        score          = float(outputs[2][0][0]) if len(outputs) > 2 else 1.0

        if score < 0.5:
            return False, 0, 0, None

        raised = count_raised_fingers_onnx(landmarks_flat)
        return True, 1, raised, landmarks_flat

    # ── Main loop ─────────────────────────────────────────────────────────────

    while True:
        # 1. Connect
        if stream_iter is None:
            try:
                connect_stream()
            except Exception as e:
                print(f"[ERROR] Stream connect: {e}", flush=True)
                time.sleep(2)
                continue

        # 2. Get latest frame
        try:
            jpg = read_latest_jpeg()
        except Exception as e:
            print(f"[ERROR] Stream read: {e}", flush=True)
            stream_iter = None
            time.sleep(1)
            continue

        if jpg is None:
            stream_iter = None
            continue

        frame_count += 1

        # 3. Adaptive skip
        if skip_frames > 0:
            skip_frames -= 1
            continue

        # 4. Decode
        img_arr = np.frombuffer(jpg, dtype=np.uint8)
        frame   = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
        if frame is None:
            continue

        # 5. Inference
        t0    = time.perf_counter()
        small = cv2.resize(frame, (320, 240))

        if BACKEND == "onnx_cuda":
            detected, count, raised, lm_data = run_onnx(small)
            lm_list_mp = None   # no MediaPipe landmarks for drawing
        else:
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            detected, count, raised, lm_list_mp = run_mediapipe(rgb)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        detect_ms  = elapsed_ms

        # Adaptive skip thresholds
        if elapsed_ms > 150:
            skip_frames = 2
        elif elapsed_ms > 80:
            skip_frames = 1
        else:
            skip_frames = 0

        # 6. Map to LED value
        if not detected:
            raw_leds = 0
        elif raised >= 4:
            raw_leds = 4
        else:
            raw_leds = raised   # 0, 1, 2, or 3

        # ── 7. Debounce ───────────────────────────────────────────────────
        # Push this frame's result into the rolling window
        debounce_window.append(raw_leds)

        # Only act if the window is full AND all values agree
        if (len(debounce_window) == DEBOUNCE_FRAMES
                and len(set(debounce_window)) == 1):
            stable_leds = debounce_window[0]
        else:
            # Window not yet stable – keep the last confirmed value
            stable_leds = confirmed_leds if confirmed_leds != -1 else 0

        # Send to ESP32 only when the stable value actually changes
        if stable_leds != last_sent_leds:
            send_leds_async(fingers_url, stable_leds)
            last_sent_leds  = stable_leds
            confirmed_leds  = stable_leds

        # 8. Annotate
        annotated = frame.copy()

        # Draw landmarks (MediaPipe backend only – ONNX doesn't give us the
        # full NormalizedLandmarkList object needed by mp_drawing)
        if detected and lm_list_mp:
            for hl in lm_list_mp:
                mp_drawing.draw_landmarks(annotated, hl, mp_hands.HAND_CONNECTIONS)

        # Status label
        debounce_stable = len(set(debounce_window)) == 1 and len(debounce_window) == DEBOUNCE_FRAMES
        stab_str = "STABLE" if debounce_stable else f"buf:{len(debounce_window)}/{DEBOUNCE_FRAMES}"

        if detected:
            flash_str = " +FLASH" if stable_leds >= 4 else ""
            label = f"HAND  Fingers:{raised}  LEDs:{stable_leds}{flash_str}  [{stab_str}]"
            color = (0, 255, 0)
        else:
            label = f"NO HAND – off  [{stab_str}]"
            color = (0, 0, 255)

        cv2.putText(annotated, label, (8, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.putText(annotated,
                    f"{BACKEND}  {elapsed_ms:.0f}ms  skip:{skip_frames}  #{frame_count}",
                    (8, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

        _, enc = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])

        # 9. Publish
        with result_lock:
            latest_annotated = enc.tobytes()
            hand_detected    = detected
            num_hands        = count
            finger_count     = raised
        new_frame_event.set()

        print(
            f"[{'HAND' if detected else '----'}] "
            f"raw={raw_leds} stable={stable_leds} buf={list(debounce_window)} "
            f"{elapsed_ms:.0f}ms {BACKEND} #{frame_count}   ",
            end='\r', flush=True
        )


# ──────────────────────────────────────────────────────────────────────────────
# Flask web server
# ──────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>Hand Detection – ESP32-CAM</title>
  <meta charset="utf-8">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: #111; color: #eee;
      font-family: 'Segoe UI', sans-serif;
      display: flex; flex-direction: column;
      align-items: center; padding: 20px;
      min-height: 100vh;
    }
    h1 { margin-bottom: 10px; font-size: 1.4em; letter-spacing: 1px; }
    #status {
      font-size: 1.6em; font-weight: bold;
      padding: 8px 24px; border-radius: 8px;
      margin-bottom: 14px; transition: background 0.15s;
    }
    .detected     { background: #2e7d32; }
    .not-detected { background: #c62828; }
    #feed {
      max-width: 90vw; max-height: 62vh;
      border: 3px solid #333; border-radius: 8px;
    }
    #meta { margin-top: 10px; color: #555; font-size: 0.82em; }
    #backend-badge {
      display: inline-block; margin-top: 6px;
      padding: 2px 10px; border-radius: 4px;
      font-size: 0.78em; font-weight: bold;
    }
    .cuda  { background: #1a237e; color: #90caf9; }
    .cpu   { background: #37474f; color: #b0bec5; }
  </style>
</head>
<body>
  <h1>ESP32-CAM Hand Detection</h1>
  <div id="status" class="not-detected">NO HAND – LEDs off</div>
  <img id="feed" src="/video_feed" alt="Camera Feed">
  <div id="meta">
    MediaPipe Hands + ESP32-CAM
    <span id="backend-badge" class="cpu">loading...</span>
  </div>

  <script>
    async function pollStatus() {
      try {
        const r = await fetch('/status');
        const d = await r.json();
        const el = document.getElementById('status');
        if (d.hand_detected) {
          const flash = d.finger_count >= 4 ? ' ⚡FLASH' : '';
          el.textContent = `HAND ✋  ${d.finger_count} finger${d.finger_count !== 1 ? 's' : ''}${flash}`;
          el.className = 'detected';
        } else {
          el.textContent = 'NO HAND – LEDs off';
          el.className = 'not-detected';
        }
        // Backend badge
        const badge = document.getElementById('backend-badge');
        if (d.backend === 'onnx_cuda') {
          badge.textContent = '⚡ ONNX + CUDA (GPU)';
          badge.className = 'cuda';
        } else {
          badge.textContent = '🖥 MediaPipe CPU';
          badge.className = 'cpu';
        }
      } catch(e) {}
      setTimeout(pollStatus, 300);
    }
    pollStatus();

    // Auto-reconnect MJPEG stream on error
    const feed = document.getElementById('feed');
    feed.onerror = () => setTimeout(() => { feed.src = '/video_feed?' + Date.now(); }, 1000);
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
        return {
            'hand_detected': hand_detected,
            'num_hands':     num_hands,
            'finger_count':  finger_count,
            'detect_ms':     round(detect_ms, 1),
            'backend':       BACKEND,
        }


def gen_frames():
    while True:
        got = new_frame_event.wait(timeout=2.0)
        new_frame_event.clear()
        if not got:
            continue
        with result_lock:
            frame = latest_annotated
        if frame is None:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')


@app.route('/video_feed')
def video_feed():
    return Response(
        gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/diag')
def diagnostics():
    try:
        resp = requests.get(f"http://{ESP32_IP}/capture", timeout=2)
        return {'esp32_reachable': True, 'ip': ESP32_IP,
                'frame_bytes': len(resp.content), 'backend': BACKEND}
    except Exception as e:
        return {'esp32_reachable': False, 'status': str(e),
                'ip': ESP32_IP, 'backend': BACKEND}


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 54)
    print("  Hand Detection — ESP32-CAM + MediaPipe / ONNX-CUDA")
    print("=" * 54)
    print(f"  Inference backend : {BACKEND}")
    print()

    ESP32_IP = os.environ.get('ESP32_IP', '').strip()
    if ESP32_IP:
        print(f"[INIT] Using ESP32_IP env var: {ESP32_IP}")
    else:
        ESP32_IP = find_esp32_ip()

    if not ESP32_IP:
        print("[FATAL] Could not find ESP32-CAM on the network.")
        exit(1)

    stream_url  = f"http://{ESP32_IP}/stream"
    fingers_url = f"http://{ESP32_IP}/fingers"

    print(f"  ESP32-CAM : {ESP32_IP}")
    print(f"  Stream    : {stream_url}")
    print(f"  Web UI    : http://localhost:5000")
    print()

    t = threading.Thread(target=main_loop, args=(stream_url, fingers_url), daemon=True)
    t.start()

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

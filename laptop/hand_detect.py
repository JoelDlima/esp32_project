"""
Hand Detection using ESP32-CAM + MediaPipe on Laptop
=====================================================
- Auto-detects ESP32-CAM on the local network
- Reads MJPEG stream from ESP32-CAM /stream endpoint
- Runs MediaPipe Hands to detect raised fingers
- Sends finger count to ESP32 /fingers?n=X  (non-blocking, background thread)
- Serves annotated live feed at http://localhost:5000

Key fixes vs previous version:
  1. LED requests are sent in a fire-and-forget background thread so they
     NEVER block the detection loop (was causing 10-second stalls).
  2. MJPEG parser always discards everything before the LAST complete JPEG
     in the buffer, so we always process the freshest frame.
  3. Frame skipping is adaptive: if detection takes >80 ms we skip the
     next frame; if it takes >150 ms we skip two frames.
  4. Output JPEG quality raised to 70 for a nicer browser view.
"""

import cv2
import numpy as np
import requests
import mediapipe as mp
import os
import threading
import time
import subprocess
import re
import socket
from flask import Flask, Response, render_template_string

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
    parts = local_ip.split('.')
    return local_ip, '.'.join(parts[:3])


def find_esp32_ip():
    print("[INIT] Scanning network for ESP32-CAM...")
    local_ip, subnet = get_local_subnet()
    print(f"[INIT] Laptop IP: {local_ip}  Subnet: {subnet}.*")

    # Ping sweep to populate ARP table
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
    print(f"[INIT] {len(candidates)} devices found on {subnet}.*")

    for ip, mac in candidates:
        try:
            r = requests.get(f"http://{ip}/capture", timeout=2)
            if r.status_code == 200 and len(r.content) > 1000:
                print(f"[INIT] ESP32-CAM at {ip}  (MAC: {mac}  frame: {len(r.content)} B)")
                return ip
        except Exception:
            pass
    return None


# ──────────────────────────────────────────────────────────────────────────────
# MediaPipe setup
# ──────────────────────────────────────────────────────────────────────────────

mp_hands    = mp.solutions.hands
mp_drawing  = mp.solutions.drawing_utils

# Maximum fingers we report to the ESP32 (0-5)
# 0        → all LEDs off
# 1        → LED1
# 2        → LED1 + LED2
# 3        → LED1 + LED2 + LED3
# 4 or 5   → LED1 + LED2 + LED3 + onboard flash
MAX_FINGERS = 5


def count_raised_fingers(hand_landmarks):
    """Count raised fingers (index, middle, ring, pinky)."""
    lm = hand_landmarks.landmark
    count = 0
    for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
        if lm[tip].y < lm[pip].y - 0.02:
            count += 1
    return count


# ──────────────────────────────────────────────────────────────────────────────
# Shared state (written by detection thread, read by Flask)
# ──────────────────────────────────────────────────────────────────────────────

latest_annotated = None
hand_detected    = False
num_hands        = 0
finger_count     = 0
result_lock      = threading.Lock()
new_frame_event  = threading.Event()


# ──────────────────────────────────────────────────────────────────────────────
# Non-blocking LED sender
# Fire-and-forget: spawns a tiny thread so the detection loop is never stalled.
# ──────────────────────────────────────────────────────────────────────────────

_led_lock          = threading.Lock()
_led_pending       = None   # value waiting to be sent
_led_sender_active = False  # is a sender thread already running?


def _send_led_worker(fingers_url: str, n: int):
    global _led_sender_active, _led_pending
    while True:
        try:
            requests.get(f"{fingers_url}?n={n}", timeout=1)
            print(f"\n[LEDS] Sent n={n}")
        except Exception as e:
            print(f"\n[LEDS] Send failed: {e}")

        # Check if a newer value arrived while we were sending
        with _led_lock:
            if _led_pending is not None and _led_pending != n:
                n = _led_pending
                _led_pending = None
                # loop again with the new value
                continue
            else:
                _led_pending = None
                _led_sender_active = False
                break


def send_leds_async(fingers_url: str, n: int):
    """Queue an LED update. If a send is already in flight, just update the
    pending value so the worker picks it up when it finishes."""
    global _led_sender_active, _led_pending
    with _led_lock:
        if _led_sender_active:
            _led_pending = n   # worker will pick this up
            return
        _led_sender_active = True
        _led_pending = None

    t = threading.Thread(target=_send_led_worker, args=(fingers_url, n), daemon=True)
    t.start()


# ──────────────────────────────────────────────────────────────────────────────
# Main detection loop
# ──────────────────────────────────────────────────────────────────────────────

def main_loop(stream_url: str, fingers_url: str):
    global latest_annotated, hand_detected, num_hands, finger_count

    session = requests.Session()
    hands_model = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=2,              # track up to 2 hands → up to 8 fingers
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    last_sent_leds = -1
    frame_count    = 0
    stream_resp    = None
    stream_iter    = None
    buf            = b""

    # ── helpers ──────────────────────────────────────────────────────────────

    def connect_stream():
        nonlocal stream_resp, stream_iter, buf
        if stream_resp is not None:
            try:
                stream_resp.close()
            except Exception:
                pass
        print(f"[STREAM] Connecting to {stream_url} ...", flush=True)
        stream_resp = session.get(stream_url, stream=True, timeout=10)
        stream_resp.raise_for_status()
        # Use a small chunk size so we get data quickly
        stream_iter = stream_resp.iter_content(chunk_size=2048)
        buf = b""
        print("[STREAM] Connected", flush=True)

    def read_latest_jpeg():
        """
        Read chunks from the MJPEG stream until we have at least one complete
        JPEG.  If multiple complete JPEGs are in the buffer we return the LAST
        one (i.e. the freshest frame) and discard everything before it.
        Returns raw JPEG bytes or None on error.
        """
        nonlocal buf, stream_iter

        JPEG_SOI = b'\xff\xd8'
        JPEG_EOI = b'\xff\xd9'
        MAX_BUF  = 512 * 1024   # 512 KB hard cap

        for chunk in stream_iter:
            buf += chunk

            # Hard cap: keep only the tail so we never accumulate stale frames
            if len(buf) > MAX_BUF:
                # Find the last SOI marker and keep from there
                last_soi = buf.rfind(JPEG_SOI)
                if last_soi > 0:
                    buf = buf[last_soi:]
                else:
                    buf = buf[-MAX_BUF // 2:]

            # Find the LAST complete JPEG in the buffer
            eoi = buf.rfind(JPEG_EOI)
            if eoi == -1:
                continue  # no complete frame yet

            # Walk backwards from eoi to find the matching SOI
            soi = buf.rfind(JPEG_SOI, 0, eoi)
            if soi == -1:
                continue

            jpg = buf[soi: eoi + 2]
            # Discard everything up to and including this frame
            buf = buf[eoi + 2:]
            return jpg

        return None  # stream_iter exhausted (shouldn't happen)

    # ── main loop ─────────────────────────────────────────────────────────────

    skip_frames = 0   # adaptive skip counter

    while True:
        # ── 1. Connect / reconnect ────────────────────────────────────────
        if stream_iter is None:
            try:
                connect_stream()
            except Exception as e:
                print(f"[ERROR] Stream connect: {e}", flush=True)
                time.sleep(2)
                continue

        # ── 2. Get latest JPEG ────────────────────────────────────────────
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

        # ── 3. Adaptive frame skip ────────────────────────────────────────
        if skip_frames > 0:
            skip_frames -= 1
            continue

        # ── 4. Decode ─────────────────────────────────────────────────────
        img_arr = np.frombuffer(jpg, dtype=np.uint8)
        frame   = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
        if frame is None:
            continue

        # ── 5. Resize + detect ────────────────────────────────────────────
        t0 = time.perf_counter()

        small = cv2.resize(frame, (320, 240))
        rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        res   = hands_model.process(rgb)

        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Adaptive skip: if inference was slow, skip upcoming frames
        if elapsed_ms > 150:
            skip_frames = 2
        elif elapsed_ms > 80:
            skip_frames = 1
        else:
            skip_frames = 0

        # ── 6. Count fingers ──────────────────────────────────────────────
        detected = res.multi_hand_landmarks is not None
        count    = len(res.multi_hand_landmarks) if detected else 0

        raised = 0
        if detected:
            for hl in res.multi_hand_landmarks:
                raised += count_raised_fingers(hl)

        # Map raised finger count → LED value sent to ESP32
        #   0        → 0  (all off)
        #   1        → 1  (LED1)
        #   2        → 2  (LED1+2)
        #   3        → 3  (LED1+2+3)
        #   4 or 5   → 4  (LED1+2+3 + flash)
        #   no hand  → 0  (all off)
        if not detected:
            leds = 0
        elif raised <= 0:
            leds = 0
        elif raised >= 4:
            leds = 4   # triggers flash on ESP32
        else:
            leds = raised  # 1, 2, or 3

        # ── 7. Send LED update (non-blocking) ─────────────────────────────
        if leds != last_sent_leds:
            send_leds_async(fingers_url, leds)
            last_sent_leds = leds

        # ── 8. Annotate frame ─────────────────────────────────────────────
        annotated = frame.copy()
        if detected:
            # Scale landmarks back to original frame size
            h, w = frame.shape[:2]
            sh, sw = small.shape[:2]
            for hl in res.multi_hand_landmarks:
                # Draw on the full-size frame by scaling landmark coords
                scaled_hl = type(hl)()
                scaled_hl.CopyFrom(hl)
                for lm in scaled_hl.landmark:
                    lm.x *= sw / w   # already normalised 0-1, no scaling needed
                    lm.y *= sh / h
                mp_drawing.draw_landmarks(annotated, hl, mp_hands.HAND_CONNECTIONS)

        label = f"HAND ({count})  Fingers: {raised}  LEDs: {leds}" + (" +FLASH" if leds >= 4 else "") if detected else "NO HAND – all off"
        color = (0, 255, 0) if detected else (0, 0, 255)
        cv2.putText(annotated, label, (10, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
        cv2.putText(annotated, f"Detect: {elapsed_ms:.0f}ms  Frame#{frame_count}", (10, 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

        # Encode at higher quality for a nicer browser view
        _, enc = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])

        # ── 9. Publish to Flask ───────────────────────────────────────────
        with result_lock:
            latest_annotated = enc.tobytes()
            hand_detected    = detected
            num_hands        = count
            finger_count     = raised
        new_frame_event.set()

        print(f"[{'HAND' if detected else '----'}] "
              f"hands={count} fingers={raised} leds={leds} "
              f"detect={elapsed_ms:.0f}ms skip={skip_frames} #{frame_count}   ",
              end='\r', flush=True)


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
      align-items: center; justify-content: flex-start;
      min-height: 100vh; padding: 20px;
    }
    h1 { margin-bottom: 12px; font-size: 1.4em; letter-spacing: 1px; }
    #status {
      font-size: 1.8em; font-weight: bold;
      padding: 8px 28px; border-radius: 8px;
      margin-bottom: 16px; transition: background 0.2s;
    }
    .detected     { background: #2e7d32; }
    .not-detected { background: #c62828; }
    #feed {
      max-width: 90vw; max-height: 65vh;
      border: 3px solid #333; border-radius: 8px;
      /* Use the MJPEG stream directly – browser handles it natively,
         no JS polling needed, no extra latency */
    }
    .info { margin-top: 12px; color: #666; font-size: 0.85em; }
  </style>
</head>
<body>
  <h1>ESP32-CAM Hand Detection</h1>
  <div id="status" class="not-detected">NO HAND</div>

  <!--
    The <img> tag pointing at /video_feed is a native MJPEG stream.
    The browser renders each frame as it arrives – no JS, no polling,
    no canvas tricks needed.  This is the lowest-latency approach.
  -->
  <img id="feed" src="/video_feed" alt="Camera Feed">

  <p class="info">MediaPipe Hands + ESP32-CAM &nbsp;|&nbsp; <span id="fps_info"></span></p>

  <script>
    // Poll /status every 300 ms just to update the status badge.
    // The video itself streams independently via the <img> MJPEG src.
    let lastFingers = -1;
    async function pollStatus() {
      try {
        const r = await fetch('/status');
        const d = await r.json();
        const el = document.getElementById('status');
        if (d.hand_detected) {
          const flash = d.finger_count >= 4 ? ' ⚡FLASH' : '';
          el.textContent = `HAND DETECTED  ✋ ${d.finger_count} finger${d.finger_count !== 1 ? 's' : ''}${flash}`;
          el.className = 'detected';
        } else {
          el.textContent = 'NO HAND – LEDs off';
          el.className = 'not-detected';
        }
      } catch(e) {}
      setTimeout(pollStatus, 300);
    }
    pollStatus();

    // Reconnect the MJPEG stream if it drops
    const feed = document.getElementById('feed');
    feed.onerror = () => {
      setTimeout(() => { feed.src = '/video_feed?' + Date.now(); }, 1000);
    };
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
        }


def gen_frames():
    """
    Yield annotated JPEG frames as a proper MJPEG stream.
    Waits for new_frame_event so we never spin-loop on CPU.
    """
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
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # disable nginx buffering if behind proxy
        }
    )


@app.route('/diag')
def diagnostics():
    try:
        resp = requests.get(f"http://{ESP32_IP}/capture", timeout=2)
        return {'esp32_reachable': True, 'status': 'OK', 'ip': ESP32_IP,
                'frame_bytes': len(resp.content)}
    except Exception as e:
        return {'esp32_reachable': False, 'status': str(e), 'ip': ESP32_IP}


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 52)
    print("  Hand Detection — ESP32-CAM + MediaPipe")
    print("=" * 52)

    ESP32_IP = os.environ.get('ESP32_IP', '').strip()
    if ESP32_IP:
        print(f"[INIT] Using ESP32_IP env var: {ESP32_IP}")
    else:
        ESP32_IP = find_esp32_ip()

    if not ESP32_IP:
        print("[FATAL] Could not find ESP32-CAM on the network.")
        print("        Make sure the ESP32 is powered on and on the same WiFi.")
        exit(1)

    stream_url  = f"http://{ESP32_IP}/stream"
    fingers_url = f"http://{ESP32_IP}/fingers"

    print(f"\n  ESP32-CAM : {ESP32_IP}")
    print(f"  Stream    : {stream_url}")
    print(f"  Fingers   : {fingers_url}")
    print(f"  Web UI    : http://localhost:5000\n")

    # Detection runs in a daemon thread; Flask runs in the main thread
    t = threading.Thread(target=main_loop, args=(stream_url, fingers_url), daemon=True)
    t.start()

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

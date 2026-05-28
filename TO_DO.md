# ESP32-CAM — To-Do & Setup Guide

Everything here is **pending** — none of these steps have been done yet.
Work through them in order when you get the ESP32 back.

---

## Step 1 — First USB Flash (required once, then never again)

This is the last time you need the USB-to-TTL adapter.
After this flash the board has OTA built in and all future updates go over WiFi.

**Put the ESP32 into bootloader mode:**
1. Bridge **IO0 → GND** (jumper wire or hold the BOOT button)
2. Press and release **RST**
3. Keep holding BOOT / IO0 bridge

**Run the upload:**
```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -e esp32dev --target upload --upload-port COM3
```

4. Release BOOT once you see `Writing at 0x00010000...`
5. Remove the IO0–GND bridge
6. Press RST — the board boots, connects to WiFi, and prints its IP to serial

**Verify it worked — open serial monitor:**
```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" device monitor --port COM3 --baud 115200
```

You should see:
```
[WIFI] IP: 10.183.227.27
[OTA]  Ready  hostname=esp32-cam  password=CHANGE_ME_OTA_PASSWORD
[READY] System ready!
```

The IP shown here is what you need for Step 2.

---

## Step 2 — Configure OTA IP in platformio.ini

Open `platformio.ini` and update the OTA environment with the IP from Step 1:

```ini
[env:esp32dev_ota]
upload_port = 10.183.227.27   ; ← replace with your actual IP
```

From now on, every firmware update is just:
```powershell
& "$env:USERPROFILE\.platformio\penv\Scripts\pio.exe" run -e esp32dev_ota --target upload
```

No USB, no BOOT button, no touching the hardware.
The OLED will show a progress bar during the update and reboot automatically when done.

**OTA password** is `CHANGE_ME_OTA_PASSWORD` (set in `main.cpp` as `OTA_PASSWORD`).
Change it if you're on a shared network.

---

## Step 3 — Run the Python hand detection

Nothing changed on the Python side. Just run:
```
start.bat
```

Or manually:
```powershell
laptop\venv\Scripts\python.exe laptop\hand_detect.py
```

Then open **http://localhost:5000** in your browser.

---

## Step 4 — Enable GPU inference (optional, one-time)

Your RTX 3050 can run hand detection at ~10–20 ms/frame instead of ~100–150 ms.
This is optional — the system works fine on CPU.

```powershell
cd laptop
venv\Scripts\activate
pip install onnxruntime-gpu tf2onnx tensorflow
python export_onnx.py
```

`export_onnx.py` downloads the MediaPipe hand landmark model, converts it to ONNX,
and verifies CUDA is active. After that `hand_detect.py` auto-detects the GPU —
you'll see **⚡ ONNX + CUDA (GPU)** in the browser badge instead of 🖥 MediaPipe CPU.

---

## What each feature does (already in the firmware/Python)

### Debounce — Python
A rolling window of the last 3 detection frames.
LED state only changes when all 3 frames agree on the same finger count.
Single bad frames are silently ignored — no more flickering.
The video overlay shows `buf:1/3 → buf:2/3 → STABLE` as it builds confidence.

### Rich OLED display — ESP32
Four rows of live info, updated every 500 ms:
- **Row 1:** Finger count + 5-block bar graph
- **Row 2:** Stream FPS
- **Row 3:** WiFi RSSI (signal strength) + uptime
- **Row 4:** Watchdog countdown — or OTA progress bar during an update

### Watchdog — ESP32
Every `/fingers` request from the laptop resets a 5-second timer.
If the timer expires (laptop crashed, WiFi dropped, Python stopped),
all LEDs turn off automatically.
OLED shows `WDG: FIRED` when it triggers.
Resets automatically as soon as the laptop sends the next request.

### OTA firmware updates — ESP32
`ArduinoOTA` runs in the background on the ESP32.
After the first USB flash, all future firmware updates go over WiFi.
During an OTA update the OLED shows a live progress bar.
LEDs are turned off automatically during the update for safety.
If the update fails, the previous firmware is preserved (ESP32 dual-partition rollback).

---

## Finger → LED mapping (reference)

| Fingers | LEDs on |
|:-------:|---------|
| 0 / no hand | All off |
| 1 | LED1 — GPIO12 |
| 2 | LED1 + LED2 — GPIO12, GPIO13 |
| 3 | LED1 + LED2 + LED3 — GPIO12, GPIO13, GPIO16 |
| 4 – 5 | All LEDs + onboard flash — GPIO4 |

---

## Wiring reminder

| Component | ESP32-CAM GPIO |
|-----------|---------------|
| OLED SDA | GPIO **15** |
| OLED SCL | GPIO **14** |
| LED 1 (1 finger) | GPIO **12** + 220 Ω resistor |
| LED 2 (2 fingers) | GPIO **13** + 220 Ω resistor |
| LED 3 (3 fingers) | GPIO **16** + 220 Ω resistor |
| Onboard flash (4-5 fingers) | GPIO **4** (built-in) |

Long leg (anode) → resistor → GPIO. Short leg (cathode) → GND.

# ESP32-CAM Hand Detection

Real-time hand detection system — ESP32-CAM streams video over Wi-Fi, a laptop runs MediaPipe to count raised fingers, and LEDs light up to match the count. Everything is automatic: the Python script finds the ESP32 on the network by itself.

![System Overview](https://img.shields.io/badge/ESP32--CAM-AI--Thinker-blue) ![Python](https://img.shields.io/badge/Python-3.10-green) ![MediaPipe](https://img.shields.io/badge/MediaPipe-0.10.21-orange) ![PlatformIO](https://img.shields.io/badge/PlatformIO-Arduino-purple)

---

## How It Works

```
┌──────────────────────────────────┐        ┌──────────────────────────────────────┐
│  ESP32-CAM                       │        │  Laptop (Python)                     │
│                                  │◄──────►│                                      │
│  • MJPEG stream  → /stream       │  WiFi  │  • Auto-detect ESP32 IP              │
│  • Single frame  → /capture      │        │  • Parse MJPEG, grab latest frame    │
│  • LED control   → /fingers?n=X  │        │  • MediaPipe hand + finger detection │
│  • OLED display (SSD1306 I2C)    │        │  • Send n= to ESP32 (async thread)   │
│  • 3 external LEDs + flash       │        │  • Flask UI at localhost:5000        │
└──────────────────────────────────┘        └──────────────────────────────────────┘
```

### Finger → LED Mapping

| Fingers shown | LEDs on |
|:---:|---|
| 0 / no hand | All off |
| 1 | LED 1 (GPIO12) |
| 2 | LED 1 + LED 2 (GPIO13) |
| 3 | LED 1 + LED 2 + LED 3 (GPIO16) |
| 4 – 5 | LED 1 + LED 2 + LED 3 + onboard flash (GPIO4) |

Closing your fist or moving out of frame turns everything off instantly.

---

## Hardware

### Required
- **AI-Thinker ESP32-CAM** module
- **USB-to-TTL adapter** (CH340 or CP2102) — for uploading firmware
- **SSD1306 OLED display** (0.96", 128×64, I2C) — shows status + finger count
- **3× LEDs** with **220–330 Ω resistors** in series
- Jumper wires, breadboard

### Wiring

#### OLED (I2C)
| OLED pin | ESP32-CAM GPIO |
|----------|---------------|
| SDA | GPIO **15** |
| SCL | GPIO **14** |
| VCC | 3.3 V |
| GND | GND |

> ⚠️ Pull-up resistors (4.7 kΩ) on SDA and SCL are required if your OLED module doesn't have them built in.

#### External LEDs
```
GPIO12 ──[220Ω]──[LED1 +]──[LED1 -]── GND   (1 finger)
GPIO13 ──[220Ω]──[LED2 +]──[LED2 -]── GND   (2 fingers)
GPIO16 ──[220Ω]──[LED3 +]──[LED3 -]── GND   (3 fingers)
```
Long leg (anode) → resistor → GPIO. Short leg (cathode) → GND.

#### Safe GPIO pins on AI-Thinker ESP32-CAM
| GPIO | Status | Notes |
|------|--------|-------|
| 4 | ✅ Output | Onboard white flash LED |
| 12 | ✅ Output | Safe after boot (strapping pin — avoid pull-ups at power-on) |
| 13 | ✅ Output | Free |
| 14 | ✅ I2C SCL | Free |
| 15 | ✅ I2C SDA | Free |
| 16 | ✅ Output | Free |
| 0 | ⚠️ Boot | Keep floating; button to GND for flash mode only |
| 2 | ⚠️ Strapping | Avoid external pull-ups |

---

## Software Setup

### 1. ESP32 Firmware

**Prerequisites:** VS Code + PlatformIO extension

```bash
# Clone the repo
git clone https://github.com/JoelDlima/esp32_project.git
cd esp32_project
```

Edit your Wi-Fi credentials in `src/main.cpp`:
```cpp
const char* ssid     = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";
```

Check your COM port in `platformio.ini`:
```ini
upload_port = COM4   ; change to match your USB-to-TTL adapter
```

**Upload:**
1. Wire USB-to-TTL: `5V→5V`, `GND→GND`, `TX→U0R`, `RX→U0T`
2. Bridge **IO0 → GND** (bootloader mode)
3. Press **RST**
4. Run upload:
   ```bash
   pio run --target upload
   ```
5. Remove IO0–GND bridge, press RST — ESP32 boots and connects to Wi-Fi

### 2. Python Environment (first time only)

```bash
cd laptop
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 3. Run

**Windows — one click:**
```
start.bat      # starts everything + opens browser
kill.bat       # stops everything
```

**Manual:**
```bash
laptop\venv\Scripts\python.exe laptop\hand_detect.py
```

Open **http://localhost:5000** in your browser.

---

## Project Structure

```
esp32_project/
├── src/
│   └── main.cpp              # ESP32 firmware (C++ / Arduino)
├── laptop/
│   ├── hand_detect.py        # Hand detection + Flask web UI
│   └── requirements.txt      # Python dependencies
├── oled_test/                # Standalone OLED wiring test sketch
├── platformio.ini            # PlatformIO build config
├── start.bat                 # Windows launch script
├── kill.bat                  # Windows stop script
├── ISSUES.md                 # Root-cause analysis of all bugs fixed
├── DIAGNOSTICS.md            # Serial output guide + component tests
└── INFO.md                   # Full system documentation
```

---

## API Endpoints (ESP32)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Simple HTML page with embedded stream |
| `/stream` | GET | MJPEG continuous video stream |
| `/capture` | GET | Single JPEG frame |
| `/fingers?n=0..5` | GET | Set LED count (0 = all off, 5 = all on + flash) |

Test LEDs directly from a browser:
```
http://<ESP32-IP>/fingers?n=1   → LED1 on
http://<ESP32-IP>/fingers?n=3   → LED1+2+3 on
http://<ESP32-IP>/fingers?n=0   → all off
```

---

## Performance

| Metric | Value |
|--------|-------|
| Stream resolution | 640×480 (VGA) |
| Stream frame rate | ~10–15 fps |
| Detection resolution | 320×240 (downscaled for speed) |
| MediaPipe inference | ~80–150 ms / frame |
| LED response latency | < 200 ms end-to-end |
| WiFi bandwidth | ~300–500 kbps |

**Key optimisations applied:**
- `CAMERA_GRAB_LATEST` — always captures the newest frame, never queues stale ones
- MJPEG parser uses `rfind` — always decodes the freshest frame in the buffer
- LED HTTP requests run in a fire-and-forget background thread — never block detection
- `server.handleClient()` called every frame — `/fingers` requests processed within ~100 ms
- Adaptive frame skipping — skips 1–2 frames when inference is slow to stay real-time

---

## Troubleshooting

### ESP32 won't upload
| Symptom | Fix |
|---------|-----|
| "Connecting..." hangs | Bridge IO0→GND, press RST, then run upload |
| Wrong COM port | Check Device Manager → Ports |
| `firmware.bin` locked | Close serial monitor / PlatformIO monitor first |

### OLED stays blank
| Symptom | Fix |
|---------|-----|
| No I2C device found in serial log | Check SDA→GPIO15, SCL→GPIO14, 3.3V, GND |
| Device found but init fails | Add 4.7 kΩ pull-ups on SDA and SCL |
| Wrong address | Try 0x3D instead of 0x3C in `main.cpp` |

> **Common mistake:** calling `Wire.setClock()` before `Wire.begin()` — always `begin()` first.

### LEDs don't light up
| Symptom | Fix |
|---------|-----|
| Nothing lights up | Check polarity: long leg → GPIO, short leg → GND |
| Very dim | Add 220–330 Ω resistor in series |
| Only onboard flash works | External LEDs need resistors; GPIO12 needs pull-down at boot |
| Wrong LED lights up | Verify pin numbers match your wiring (GPIO12=LED1, GPIO13=LED2) |

### Video feed laggy / freezing
| Symptom | Fix |
|---------|-----|
| Feed freezes after a few seconds | Restart `start.bat`; check WiFi signal |
| LEDs respond slowly | Make sure you're running the latest firmware (per-frame `handleClient`) |
| High latency | Normal for 2.4 GHz WiFi; keep ESP32 and laptop on same router |

### Python errors
| Error | Fix |
|-------|-----|
| `Could not find ESP32-CAM` | ESP32 must be on the same WiFi; close browser tabs to its IP |
| `mediapipe` import error | Use Python 3.10 exactly; `pip install mediapipe==0.10.21` |
| Port 5000 in use | Run `kill.bat` or `taskkill /IM python.exe /F` |

---

## Known Issues & Fixes

See **[ISSUES.md](ISSUES.md)** for a full root-cause breakdown of every bug encountered, with code examples and prevention tips for future ESP32-CAM projects. Covers:

- OLED blank (`Wire.setClock` order + swapped SDA/SCL)
- LEDs not working (strapping pin conflicts on GPIO2/GPIO12)
- Laggy stream (blocking HTTP calls, stale MJPEG buffer, TCP chunk size)
- LEDs staying on after hand removed (`last_sent_leds` init bug)

---

## Requirements

**Python** (`laptop/requirements.txt`):
```
opencv-python==4.9.0.80
mediapipe==0.10.21
requests>=2.31
flask>=3.0
numpy>=1.24,<2
```

**PlatformIO** (`platformio.ini`):
```ini
[env:esp32dev]
platform  = espressif32
board     = esp32dev
framework = arduino
monitor_speed = 115200
upload_speed  = 115200
upload_port   = COM4
lib_deps =
    adafruit/Adafruit SSD1306 @ ^2.5.10
    adafruit/Adafruit GFX Library @ ^1.11.9
```

---

## License

Open source — free for personal and educational use.

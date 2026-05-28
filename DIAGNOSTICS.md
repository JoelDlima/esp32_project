# ESP32-CAM Troubleshooting & Diagnostics

## Changes Made (2026-05-27)

### 1. **ESP32 Firmware Improvements**
- ✅ **Better OLED initialization**: 3 retry attempts with longer setup delays
- ✅ **I2C diagnostic logging**: Shows probe attempts and error messages
- ✅ **GPIO verification**: Logs all LED pin configuration during boot
- ✅ **Frame buffer robustness**: Tracks failed frame grabs and stops stream if too many failures
- ✅ **Stream handler logging**: Reports FPS, frame counts, client connections
- ✅ **Mode transition messages**: Clear boot → running state indicators

### 2. **Python Optimization** 
- ✅ Frame resizing: 640×480 → 320×240 for ~4x faster MediaPipe inference
- ✅ Frame skipping: Reduces jank when detection falls behind

---

## How to Read the Diagnostics

### ESP32 Serial Output (COM3 at 115200 baud)

**Expected boot sequence:**
```
========================================
[BOOT] ESP32-CAM Hand Detection Starting
========================================

[OLED] Initializing I2C on SDA=14 SCL=15
[OLED] Probing address 0x3C
[OLED] SUCCESS: Display initialized on first try
[OLED] Display initialized successfully
[OLED] Bootscreen displayed

[LEDS] Configuring GPIO outputs...
[LEDS] GPIO2 set to OUTPUT (1 finger)
[LEDS] GPIO13 set to OUTPUT (2 fingers)
[LEDS] GPIO12 set to OUTPUT (3 fingers)
[LEDS] All LEDs OFF at startup

[CAM] Configuring camera (AI-Thinker ESP32-CAM)...
[CAM] PSRAM found - using PSRAM for frame buffer
[CAM] Initializing camera...
[CAM] SUCCESS: Camera initialized

[WIFI] Starting Wi-Fi connection
[WIFI] SSID: YOUR_WIFI_SSID
[WIFI] Connecting..... CONNECTED!
[WIFI] SUCCESS! IP: 10.183.227.27
[WIFI] Stream: http://10.183.227.27/stream

========================================
[MODE] Transitioning to RUNNING MODE
========================================
[SERVER] Initializing HTTP handlers...
[SERVER]   GET /stream    -> MJPEG stream
[SERVER]   GET /capture   -> Single frame
[SERVER]   GET /fingers?n=0..3 -> LED control
[SERVER] WebServer started on port 80
[SERVER] SUCCESS: Camera server started
========================================
[READY] System ready for connections!
```

### What to Check If Something Goes Wrong

**If "OLED not found" message appears:**
- [ ] Check GPIO14 (SDA) and GPIO15 (SCL) are connected properly to OLED
- [ ] Verify OLED address is 0x3C (run I2C scanner if possible)
- [ ] Check for pull-up resistors (typically 4.7kΩ) on SDA/SCL lines
- [ ] Try different I2C address (0x3D?) if available

**If LEDs don't respond:**
- [ ] Check GPIO2, GPIO12, GPIO13 are not connected to camera
- [ ] Verify GPIO pins aren't pulled low permanently
- [ ] Test with `/fingers?n=1`, `/fingers?n=2`, `/fingers?n=3` directly from browser
- [ ] Look for "[LEDS] Set to X fingers" messages in serial output

**If video goes blank after initial load:**
- [ ] Look for "[STREAM]" messages in serial output
- [ ] If "ERROR: Too many failed frame grabs", camera buffer issue
- [ ] Check if "[STREAM] Client disconnected after 0 frames" - indicates immediate disconnect

---

## How to Monitor Serial Output

### Option 1: PlatformIO Monitor
```bash
platformio device monitor --port COM3 --baud 115200
```

### Option 2: Use VS Code PlatformIO Extension
- Click PlatformIO icon in left sidebar
- Select "Monitor" under current project

### Option 3: Manual USB Connection
- Open Arduino IDE
- Tools → Serial Monitor → COM3, 115200 baud

---

## Testing Each Component

###  Test LEDs
```
Browser: http://10.183.227.27/fingers?n=0  # All OFF
Browser: http://10.183.227.27/fingers?n=1  # GPIO2 ON
Browser: http://10.183.227.27/fingers?n=2  # GPIO2+13 ON
Browser: http://10.183.227.27/fingers?n=3  # GPIO2+13+12 ON
```
Watch serial for: `[LEDS] Set to X fingers: GPIO2=... GPIO13=... GPIO12=...`

### Test OLED
- Should display "Booting..." on first load
- Should display "WiFi..." + SSID during WiFi connection
- Should display "WiFi OK" + IP when connected
- Should display "Hand Detected" + finger count when receiving detection data

### Test Video Stream
```
Direct: http://10.183.227.27/stream      # Raw MJPEG stream
Flask:  http://localhost:5000/video_feed # Python-processed frames
```
Watch serial for: `[STREAM] Client connected` and `[STREAM] Sent X frames, FPS: Y`

---

## Next Steps

1. **Monitor serial output** to identify which component isn't initializing
2. **Look for ERROR messages** - these indicate the specific failure
3. **Provide error messages** from serial output for diagnosis
4. If OLED fails to init, provide the retry count and final status
5. If LEDs don't work, test with direct `/fingers?n=X` calls and check GPIO voltage

---

## Firmware Features
- **Robust OLED init**: 3 attempts with 100ms delays
- **I2C clock**: Set to 100kHz for compatibility
- **Frame buffer error tracking**: Stops stream after 10+ consecutive failed grabs
- **Connection logging**: Tracks client connections and disconnections
- **FPS monitoring**: Logs performance every 30 frames

Check the serial console for detailed diagnostics!

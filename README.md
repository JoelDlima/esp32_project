# ESP32-CAM Hand Detection Project

A real-time hand detection system using **ESP32-CAM** for video streaming and **MediaPipe** on a laptop for computer vision processing. The system detects hands in the video stream and triggers the ESP32-CAM's onboard LED when a hand is detected.

## Features

- **ESP32-CAM Video Streaming**: Captures and streams JPEG frames over Wi-Fi
- **Real-time Hand Detection**: Uses Google MediaPipe for accurate hand tracking
- **Web Interface**: Flask server provides live video feed with detection overlay
- **LED Trigger**: ESP32-CAM LED activates when hands are detected
- **Low Latency**: Optimized for real-time performance

## System Architecture

```
┌─────────────┐  Wi-Fi  ┌──────────────────────────┐
│ ESP32-CAM   │◄────────┤ Laptop (Python)          │
│ - Stream    │         │ - Hand Detection         │
│ - LED       │         │ - Flask Web Server       │
└─────────────┘         └──────────────────────────┘
```

## Hardware Requirements

- **ESP32-CAM** (AI-Thinker module recommended)
- FTDI Programmer or USB-to-TTL adapter (for initial upload)
- 5V power supply (USB or external)
- Computer with Wi-Fi

## Software Requirements

### ESP32-CAM (Firmware)
- [PlatformIO](https://platformio.org/) (recommended) or Arduino IDE
- ESP32 board support

### Laptop (Computer Vision)
- Python 3.8 or higher
- Libraries (see `laptop/requirements.txt`)

## Installation & Setup

### Part 1: ESP32-CAM Setup

#### Option A: Using PlatformIO (Recommended)

1. **Install PlatformIO**:
   - [VS Code](https://code.visualstudio.com/) + [PlatformIO IDE Extension](https://platformio.org/install/ide?install=vscode)
   - Or use PlatformIO CLI

2. **Configure Wi-Fi Credentials**:
   Edit `src/main.cpp` and update your Wi-Fi credentials:
   ```cpp
   const char* ssid = "YOUR_WIFI_SSID";
   const char* password = "YOUR_WIFI_PASSWORD";
   ```

3. **Upload to ESP32-CAM**:
   ```bash
   # Connect ESP32-CAM via FTDI/USB-TTL to your computer
   # Put ESP32-CAM in programming mode (connect GPIO0 to GND before power-on)
   pio run --target upload
   ```

4. **Monitor Serial Output**:
   ```bash
   pio device monitor -b 115200
   ```
   Note down the **IP address** displayed (e.g., `192.168.1.8`)

#### Option B: Using Arduino IDE

1. **Install ESP32 Board Support**:
   - Add `https://dl.espressif.com/dl/package_esp32_index.json` to board manager URLs
   - Install "esp32" by Espressif Systems

2. **Configure Settings**:
   - Board: "AI Thinker ESP32-CAM"
   - Upload Speed: 115200
   - Port: Select your FTDI/USB-TTL port

3. **Update Wi-Fi Credentials** in `esp32cam_test.ino` or `src/main.cpp`

4. **Upload** and note the IP address from Serial Monitor

### Part 2: Laptop Setup

1. **Navigate to laptop directory**:
   ```bash
   cd laptop
   ```

2. **Create virtual environment** (recommended):
   ```bash
   python -m venv venv
   
   # Windows
   venv\Scripts\activate
   
   # Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure ESP32-CAM IP**:
   Edit `laptop/hand_detect.py` and update the IP address:
   ```python
   ESP_IP = "192.168.1.8"  # Change to your ESP32-CAM IP
   ```

## Usage

### Running the System

1. **Power on ESP32-CAM** and ensure it's connected to Wi-Fi

2. **Start the hand detection server**:
   ```bash
   cd laptop
   python hand_detect.py
   ```

3. **Open web interface**:
   - Navigate to `http://localhost:5000` in your browser
   - You should see the live camera feed with hand detection overlay

### What to Expect

- **Green overlay**: Hand(s) detected
- **ESP32-CAM LED**: Lights up when hand is continuously detected
- **Console output**: Real-time detection status

### Detection Parameters

You can adjust these in `hand_detect.py`:

```python
POLL_INTERVAL = 0.3      # Seconds between frame captures
HAND_HOLDOFF = 0.5       # Time before first LED trigger
RETRIGGER_AFTER = 1.5    # Re-trigger interval
LED_ON_DURATION = 2.0    # How long LED stays on
```

## Project Structure

```
esp32cam_miniproject/
├── src/
│   ├── main.cpp           # ESP32-CAM firmware (PlatformIO)
│   └── main.cpp.bak       # Backup
├── laptop/
│   ├── hand_detect.py     # Hand detection + Flask server
│   └── requirements.txt   # Python dependencies
├── platformio.ini         # PlatformIO configuration
├── esp32cam_test.ino      # Arduino IDE sketch (alternative)
├── start.bat              # Windows batch helper
├── kill.bat               # Windows batch helper
└── README.md              # This file
```

## Troubleshooting

### ESP32-CAM Issues

**Camera fails to initialize:**
- Check power supply (ESP32-CAM needs stable 5V, min 500mA)
- Ensure camera ribbon cable is properly connected
- Try reducing camera resolution in code

**Can't upload code:**
- GPIO0 must be connected to GND during upload
- Disconnect GPIO0 after upload to run normally
- Check FTDI connections (TX→RX, RX→TX)
- Reset ESP32-CAM after upload

**WiFi won't connect:**
- Verify SSID and password
- Ensure 2.4GHz network (ESP32 doesn't support 5GHz)
- Check router firewall settings

### Laptop Issues

**Import errors:**
- Ensure virtual environment is activated
- Reinstall dependencies: `pip install -r requirements.txt --upgrade`

**Can't connect to ESP32-CAM:**
- Verify ESP32-CAM IP address
- Check both devices on same network
- Ping ESP32-CAM: `ping 192.168.1.x`

**Slow detection:**
- Reduce frame capture rate (increase `POLL_INTERVAL`)
- Close other applications
- Check MediaPipe compatibility with your system

**No web interface:**
- Ensure port 5000 is not in use
- Check firewall settings
- Try `http://127.0.0.1:5000` instead

## Technical Details

### ESP32-CAM Endpoints

- `http://<ESP_IP>/`: Root page with links
- `http://<ESP_IP>/capture`: Get single JPEG frame
- `http://<ESP_IP>/stream`: MJPEG stream
- `http://<ESP_IP>/led`: Trigger onboard LED

### Dependencies

**ESP32 (C++):**
- esp_camera library
- WiFi library
- WebServer library

**Python:**
- opencv-python: Image processing
- mediapipe: Hand detection ML model
- flask: Web server
- requests: HTTP client
- numpy: Array operations

## Performance

- **Frame rate**: ~3 FPS (adjustable)
- **Detection latency**: < 100ms
- **WiFi range**: Typical 2.4GHz range (10-50m indoors)

## Future Enhancements

- [ ] Gesture recognition (thumbs up, peace sign, etc.)
- [ ] Multiple camera support
- [ ] Mobile app interface
- [ ] Cloud deployment
- [ ] Recording and playback
- [ ] Face detection integration

## License

This project is open source and available for educational and personal use.

## Credits

- **ESP32-CAM**: Espressif Systems
- **MediaPipe**: Google
- **OpenCV**: Open Source Computer Vision Library

## Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.

---

**Enjoy building!** 🚀📷✋

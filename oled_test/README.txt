OLED TEST (SAFE WIRING)
=======================

Sketch:
- oled_test.ino

Recommended pin mapping:
- OLED SDA -> GPIO15
- OLED SCL -> GPIO14
- OLED VCC -> 3.3V
- OLED GND -> GND

Important:
- Avoid GPIO2 for I2C on ESP32-CAM (boot strap pin).
- Avoid GPIO4 for I2C on ESP32-CAM (flash LED circuit).
- If ESP32-CAM shows constant LED / boot issues, disconnect OLED and reboot first.

How to use:
1) Open oled_test.ino in Arduino IDE (or copy into PlatformIO src/main.cpp for quick test).
2) Install libraries:
   - Adafruit GFX Library
   - Adafruit SSD1306
3) Upload to ESP32-CAM.
4) Open Serial Monitor at 115200.
5) You should see I2C scan logs and OLED status.
6) OLED will show test text + uptime heartbeat.

/*
  ESP32-CAM OLED quick test (safe wiring)
  ---------------------------------------
  Recommended pins for ESP32-CAM:
    SDA -> GPIO15
    SCL -> GPIO14

  Why not GPIO2/GPIO4?
  - GPIO2 is a boot strap pin and external pull-ups can break boot.
  - GPIO4 is tied to the flash LED circuit on ESP32-CAM.

  Required libraries:
    - Adafruit GFX Library
    - Adafruit SSD1306
*/

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1

#define OLED_SDA 15
#define OLED_SCL 14

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

static uint8_t detectOledAddress() {
  uint8_t found = 0;
  Serial.println("[I2C] Scanning bus...");

  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission();
    if (err == 0) {
      Serial.print("[I2C] Found device at 0x");
      if (addr < 16) Serial.print('0');
      Serial.println(addr, HEX);
      if (addr == 0x3C || addr == 0x3D) {
        found = addr;
      }
    }
  }

  if (found == 0) {
    Serial.println("[I2C] No SSD1306 address (0x3C/0x3D) found.");
  }

  return found;
}

void drawBootScreen(uint8_t addr) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("ESP32-CAM OLED TEST");

  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  display.setCursor(0, 16);
  display.print("SDA: GPIO");
  display.println(OLED_SDA);

  display.setCursor(0, 26);
  display.print("SCL: GPIO");
  display.println(OLED_SCL);

  display.setCursor(0, 36);
  display.print("ADDR: 0x");
  if (addr < 16) display.print('0');
  display.println(addr, HEX);

  display.setCursor(0, 50);
  display.println("If text visible: PASS");
  display.display();
}

void drawHeartbeat(uint32_t secondsUp) {
  display.fillRect(0, 56, 128, 8, SSD1306_BLACK);
  display.setTextSize(1);
  display.setCursor(0, 56);
  display.print("UP: ");
  display.print(secondsUp);
  display.print("s");

  int x = (secondsUp % 16) * 8;
  display.fillCircle(x + 3, 60, 2, SSD1306_WHITE);
  display.display();
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("=== ESP32-CAM OLED SAFE-WIRING TEST ===");

  Wire.begin(OLED_SDA, OLED_SCL);
  Wire.setClock(100000);

  uint8_t oledAddr = detectOledAddress();
  if (oledAddr == 0) {
    Serial.println("[FAIL] OLED not detected. Check power, GND, SDA/SCL.");
    while (true) {
      delay(1000);
    }
  }

  if (!display.begin(SSD1306_SWITCHCAPVCC, oledAddr)) {
    Serial.println("[FAIL] SSD1306 init failed.");
    while (true) {
      delay(1000);
    }
  }

  Serial.println("[OK] OLED initialized.");

  display.clearDisplay();
  display.display();
  delay(100);

  drawBootScreen(oledAddr);
}

void loop() {
  static uint32_t lastTickMs = 0;
  uint32_t nowMs = millis();

  if (nowMs - lastTickMs >= 1000) {
    lastTickMs = nowMs;
    drawHeartbeat(nowMs / 1000);
  }
}

/*
  ESP32-CAM  –  Hand Detection → LED trigger
  ──────────────────────────────────────────
  • Camera streams MJPEG frames over Wi-Fi
  • Laptop runs MediaPipe hand detection
  • Laptop sends finger count to /fingers?n=0..3
  • LEDs on GPIO13/GPIO12/GPIO4 indicate 1/2/3 fingers
  • OLED SSD1306 on SDA=GPIO15, SCL=GPIO14

  PIN NOTES (AI-Thinker ESP32-CAM):
  - GPIO2  : Strapping pin + onboard flash LED → avoid for user LEDs
  - GPIO12 : Strapping pin (boot mode) → avoid; use GPIO4 instead
  - GPIO4  : Onboard flash LED (bright), but safe as digital output
  - GPIO13 : Free, safe for LED
  - GPIO14 : Free, safe for I2C SCL
  - GPIO15 : Free, safe for I2C SDA (also strapping but safe when LOW at boot)
*/

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ── OLED display (0.96", 128×64, SSD1306 I2C) ────────────────────────────────
// SDA=GPIO15, SCL=GPIO14  (matches oled_test wiring)
#define OLED_SDA   15
#define OLED_SCL   14
#define SCREEN_W  128
#define SCREEN_H   64
#define OLED_ADDR 0x3C
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

// ── Wi-Fi credentials ────────────────────────────────────────────────────────
const char* ssid     = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_SSIDdlima";

// ── LED outputs ──────────────────────────────────────────────────────────────
// Wiring (user confirmed): long leg → GPIO, short leg → GND
//   GPIO12 → LED1  (1 finger)
//   GPIO13 → LED2  (2 fingers)
//   GPIO16 → LED3  (3 fingers)   ← add a resistor ~220-330Ω in series!
//   GPIO4  → onboard white flash (4-5 fingers)
//
// GPIO12 note: safe as OUTPUT after boot. Only dangerous if pulled HIGH
//              externally DURING power-on reset. A series resistor (220Ω)
//              limits current enough that it won't affect the strapping level.
//
// Finger → LED mapping:
//   0        → all OFF   (no hand, closed fist)
//   1        → LED1
//   2        → LED1 + LED2
//   3        → LED1 + LED2 + LED3
//   4 or 5   → LED1 + LED2 + LED3 + onboard flash (GPIO4)
#define LED1_PIN  12   // external LED 1  – you wired long leg here
#define LED2_PIN  13   // external LED 2  – you wired long leg here
#define LED3_PIN  16   // external LED 3  – wire long leg here next
#define FLASH_PIN  4   // onboard white flash – 4-5 fingers bonus

bool oled_ok = false;
int  current_fingers = 0;

// ── Camera pin map – AI-Thinker ESP32-CAM ────────────────────────────────────
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

WebServer server(80);
void startCameraServer();

// ─────────────────────────────────────────────────────────────────────────────
// setLedCount – maps finger count to LED states
//   0        → all OFF
//   1        → LED1
//   2        → LED1 + LED2
//   3        → LED1 + LED2 + LED3
//   4 or 5   → LED1 + LED2 + LED3 + onboard flash (GPIO4)
//   no hand  → caller sends 0, so all OFF
void setLedCount(int count) {
  if (count < 0) count = 0;
  if (count > 5) count = 5;

  digitalWrite(LED1_PIN,  count >= 1 ? HIGH : LOW);
  digitalWrite(LED2_PIN,  count >= 2 ? HIGH : LOW);
  digitalWrite(LED3_PIN,  count >= 3 ? HIGH : LOW);
  digitalWrite(FLASH_PIN, count >= 4 ? HIGH : LOW);  // bonus: 4-5 fingers

  current_fingers = count;

  Serial.printf("[LEDS] fingers=%d  L1=%s L2=%s L3=%s FLASH=%s\n",
    count,
    count >= 1 ? "ON" : "off",
    count >= 2 ? "ON" : "off",
    count >= 3 ? "ON" : "off",
    count >= 4 ? "ON" : "off");
}

// ── OLED helper ───────────────────────────────────────────────────────────────
void oled_show(const String& line1, const String& line2 = "", const String& line3 = "") {
  if (!oled_ok) return;
  display.clearDisplay();

  // Title bar
  display.fillRect(0, 0, SCREEN_W, 14, WHITE);
  display.setTextColor(BLACK);
  display.setTextSize(1);
  display.setCursor(4, 3);
  display.print("ESP32-CAM");

  // Content lines
  display.setTextColor(WHITE);
  display.setCursor(0, 18);
  display.setTextSize(1);
  display.println(line1);
  if (line2.length()) display.println(line2);
  if (line3.length()) display.println(line3);

  display.display();
}

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n========================================");
  Serial.println("[BOOT] ESP32-CAM Hand Detection Starting");
  Serial.println("========================================\n");

  // ── OLED init ────────────────────────────────────────────────────────────
  // IMPORTANT: Wire.begin() MUST come before Wire.setClock()
  Serial.printf("[OLED] I2C init: SDA=GPIO%d  SCL=GPIO%d\n", OLED_SDA, OLED_SCL);
  Wire.begin(OLED_SDA, OLED_SCL);   // begin first
  Wire.setClock(100000);             // then set clock speed
  delay(200);                        // let bus stabilise

  // I2C scan – find what's actually on the bus
  Serial.println("[OLED] Scanning I2C bus...");
  bool found_any = false;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[OLED] Device found at 0x%02X\n", addr);
      found_any = true;
    }
  }
  if (!found_any) {
    Serial.println("[OLED] WARNING: No I2C devices found! Check wiring.");
  }

  // Try to init display (3 attempts)
  oled_ok = false;
  for (int retry = 0; retry < 3 && !oled_ok; retry++) {
    oled_ok = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
    if (!oled_ok) {
      Serial.printf("[OLED] Init attempt %d failed, retrying...\n", retry + 1);
      delay(150);
    }
  }

  if (!oled_ok) {
    Serial.println("[OLED] FAILED: Display not found at 0x3C");
    Serial.println("[OLED] Check: SDA→GPIO15, SCL→GPIO14, 3.3V, GND, pull-ups");
  } else {
    Serial.println("[OLED] SUCCESS: Display initialised");
    display.setFont(nullptr);
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(WHITE);
    display.setCursor(0, 0);
    display.println("Booting...");
    display.display();
    delay(100);
  }

  // ── LED outputs ──────────────────────────────────────────────────────────
  Serial.println("[LEDS] Configuring GPIO outputs...");
  pinMode(LED1_PIN,  OUTPUT);
  pinMode(LED2_PIN,  OUTPUT);
  pinMode(LED3_PIN,  OUTPUT);
  pinMode(FLASH_PIN, OUTPUT);
  setLedCount(0);
  Serial.printf("[LEDS] GPIO%d GPIO%d GPIO%d GPIO%d(flash) → all OFF\n",
    LED1_PIN, LED2_PIN, LED3_PIN, FLASH_PIN);

  // ── Camera config ────────────────────────────────────────────────────────
  Serial.println("[CAM] Configuring camera...");
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    Serial.println("[CAM] PSRAM found – high quality mode");
    config.frame_size   = FRAMESIZE_VGA;   // 640×480
    config.jpeg_quality = 12;              // better quality (lower = better)
    config.fb_count     = 2;
    config.fb_location  = CAMERA_FB_IN_PSRAM;
    config.grab_mode    = CAMERA_GRAB_LATEST;  // always get newest frame
  } else {
    Serial.println("[CAM] No PSRAM – DRAM mode");
    config.frame_size   = FRAMESIZE_QVGA;  // 320×240 – lighter on DRAM
    config.jpeg_quality = 15;
    config.fb_count     = 1;
    config.fb_location  = CAMERA_FB_IN_DRAM;
    config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAM] ERROR: init failed 0x%x\n", err);
    oled_show("Camera ERROR", String("0x") + String(err, HEX));
    return;
  }

  // Tune sensor for better streaming performance
  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    s->set_framesize(s, FRAMESIZE_VGA);
    s->set_quality(s, 12);
    s->set_brightness(s, 1);   // slight brightness boost
    s->set_saturation(s, 0);
    s->set_gainceiling(s, (gainceiling_t)2);
    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_aec2(s, 1);
    Serial.println("[CAM] Sensor tuned for streaming");
  }

  Serial.println("[CAM] SUCCESS: Camera initialised");

  // ── Wi-Fi ────────────────────────────────────────────────────────────────
  Serial.printf("[WIFI] Connecting to %s ...\n", ssid);
  oled_show("Connecting WiFi", String(ssid));
  WiFi.begin(ssid, password);
  int dots = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    dots++;
    if (dots % 10 == 0) oled_show("WiFi...", String(ssid));
  }
  Serial.println(" CONNECTED!");

  String ip = WiFi.localIP().toString();
  Serial.printf("[WIFI] IP: %s\n", ip.c_str());
  Serial.printf("[WIFI] Stream: http://%s/stream\n", ip.c_str());
  oled_show("WiFi  OK", ip, "/stream");

  startCameraServer();

  Serial.println("========================================");
  Serial.println("[READY] System ready!\n");
}

// ─────────────────────────────────────────────────────────────────────────────
void loop() {
  server.handleClient();
  // No delay here – keep the server as responsive as possible
}

// ── MJPEG stream handler ──────────────────────────────────────────────────────
// Streams frames as fast as the camera produces them.
// Uses chunked writes to avoid blocking the TCP stack.
void handle_jpg_stream(void) {
  WiFiClient client = server.client();
  Serial.println("[STREAM] Client connected");

  // Send MJPEG header
  client.print("HTTP/1.1 200 OK\r\n"
               "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
               "Cache-Control: no-cache\r\n"
               "Connection: close\r\n"
               "Access-Control-Allow-Origin: *\r\n"
               "\r\n");

  int  frame_count  = 0;
  int  fail_count   = 0;
  unsigned long fps_timer = millis();

  while (client.connected()) {
    // Grab the LATEST frame from the camera
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      fail_count++;
      if (fail_count > 5) {
        Serial.println("[STREAM] Too many failed grabs – stopping");
        break;
      }
      delay(10);
      continue;
    }
    fail_count = 0;

    if (fb->len == 0) {
      esp_camera_fb_return(fb);
      continue;
    }

    // Write boundary + headers
    char hdr[128];
    int hdr_len = snprintf(hdr, sizeof(hdr),
      "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
      (unsigned)fb->len);
    client.write((uint8_t*)hdr, hdr_len);

    // Write JPEG data in chunks so TCP doesn't stall
    const uint8_t* ptr = fb->buf;
    size_t remaining   = fb->len;
    while (remaining > 0 && client.connected()) {
      size_t chunk = remaining > 1460 ? 1460 : remaining;  // ~1 TCP segment
      client.write(ptr, chunk);
      ptr       += chunk;
      remaining -= chunk;
    }
    client.write((uint8_t*)"\r\n", 2);

    esp_camera_fb_return(fb);
    frame_count++;

    // Process any pending HTTP requests (e.g. /fingers) after EVERY frame.
    // This is what makes LED response feel instant – no 30-frame wait.
    server.handleClient();

    // Log FPS every 30 frames
    if (frame_count % 30 == 0) {
      unsigned long now = millis();
      float fps = 30000.0f / (float)(now - fps_timer);
      Serial.printf("[STREAM] %d frames  FPS: %.1f\n", frame_count, fps);
      fps_timer = now;
    }
  }

  Serial.printf("[STREAM] Client disconnected after %d frames\n", frame_count);
}

// ── Single-frame capture ──────────────────────────────────────────────────────
void handle_capture(void) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Capture failed");
    return;
  }
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Cache-Control", "no-cache");
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

// ── Finger / LED control ──────────────────────────────────────────────────────
void handle_fingers(void) {
  int n = server.hasArg("n") ? server.arg("n").toInt() : 0;
  if (n < 0) n = 0;
  if (n > 5) n = 5;
  Serial.printf("[FINGERS] n=%d\n", n);
  setLedCount(n);

  // OLED: show finger count and which LEDs are on
  String line1, line2, line3;
  if (n == 0) {
    line1 = "No hand / 0";
    line2 = "All LEDs OFF";
  } else {
    line1 = String(n) + " finger" + (n != 1 ? "s" : "");
    line2 = "LEDs 1-" + String(min(n, 3)) + " ON";
    if (n >= 4) line3 = "+ FLASH ON";
  }
  oled_show(line1, line2, line3);

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", "OK");
}

// ── Root page ─────────────────────────────────────────────────────────────────
void handle_root(void) {
  String ip = WiFi.localIP().toString();
  String html = "<html><head><title>ESP32-CAM</title></head><body style='background:#111;color:#eee;font-family:sans-serif;text-align:center'>";
  html += "<h2>ESP32-CAM Stream</h2>";
  html += "<img src='/stream' style='max-width:100%;border:2px solid #444'><br><br>";
  html += "<p>IP: " + ip + "</p>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

// ─────────────────────────────────────────────────────────────────────────────
void startCameraServer() {
  server.on("/",        HTTP_GET, handle_root);
  server.on("/stream",  HTTP_GET, handle_jpg_stream);
  server.on("/capture", HTTP_GET, handle_capture);
  server.on("/fingers", HTTP_GET, handle_fingers);
  server.begin();
  Serial.println("[SERVER] Routes: /  /stream  /capture  /fingers?n=0..3");
}

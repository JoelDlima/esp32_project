/*
  ESP32-CAM  –  Hand Detection → LED trigger
  ──────────────────────────────────────────
  • Camera streams MJPEG frames over Wi-Fi
  • Laptop runs hand detection (MediaPipe or ONNX/CUDA)
  • Laptop sends finger count to /fingers?n=0..5
  • LEDs on GPIO12/13/16 + onboard flash GPIO4
  • OLED SSD1306 on SDA=GPIO15, SCL=GPIO14

  Features:
  ┌─────────────────────────────────────────────────────┐
  │ • OTA updates: flash new firmware over WiFi,        │
  │   no USB adapter needed after first flash           │
  │   Password: "CHANGE_ME_OTA_PASSWORD"  (change before deploying)   │
  │ • Watchdog: if no /fingers request for 5 s,         │
  │   all LEDs turn off automatically                   │
  │ • Rich OLED layout:                                 │
  │   - Title bar: "ESP32-CAM"                          │
  │   - Row 1: finger count + bar graph                 │
  │   - Row 2: stream FPS                               │
  │   - Row 3: WiFi RSSI + uptime                       │
  │   - Row 4: watchdog countdown / OTA progress        │
  └─────────────────────────────────────────────────────┘

  PIN NOTES (AI-Thinker ESP32-CAM):
  - GPIO2  : Strapping pin + onboard flash LED → avoid for user LEDs
  - GPIO12 : Strapping pin (boot mode) → safe as OUTPUT after boot
  - GPIO4  : Onboard flash LED (bright), safe as digital output
  - GPIO13 : Free, safe for LED
  - GPIO14 : Free, safe for I2C SCL
  - GPIO15 : Free, safe for I2C SDA
*/

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoOTA.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ── OLED ─────────────────────────────────────────────────────────────────────
#define OLED_SDA   15
#define OLED_SCL   14
#define SCREEN_W  128
#define SCREEN_H   64
#define OLED_ADDR 0x3C
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

// ── Wi-Fi ─────────────────────────────────────────────────────────────────────
const char* ssid     = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_SSIDdlima";

// ── OTA ───────────────────────────────────────────────────────────────────────
// Change this password before deploying in a shared environment.
// Upload via OTA:  pio run --target upload  (with upload_protocol = espota in platformio.ini)
#define OTA_PASSWORD  "CHANGE_ME_OTA_PASSWORD"
#define OTA_HOSTNAME  "esp32-cam"

// ── LED pins ──────────────────────────────────────────────────────────────────
//   GPIO12 → LED1  (1 finger)
//   GPIO13 → LED2  (2 fingers)
//   GPIO16 → LED3  (3 fingers)
//   GPIO4  → onboard flash (4-5 fingers)
#define LED1_PIN  12
#define LED2_PIN  13
#define LED3_PIN  16
#define FLASH_PIN  4

// ── Watchdog ──────────────────────────────────────────────────────────────────
// If no /fingers request arrives within this many ms, turn all LEDs off.
#define WATCHDOG_MS  5000UL

// ── Camera pins (AI-Thinker) ──────────────────────────────────────────────────
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

// ── State ─────────────────────────────────────────────────────────────────────
bool          oled_ok         = false;
int           current_fingers = 0;
unsigned long last_fingers_ms = 0;   // millis() of last /fingers request
bool          watchdog_fired  = false;

// Stream FPS tracking (updated by stream handler, read by OLED)
volatile float stream_fps     = 0.0f;

// OTA progress (0 = idle, 1-100 = in progress, shown on OLED row 4)
volatile int   ota_progress   = -1;   // -1 = not active

WebServer server(80);
void startCameraServer();

// ─────────────────────────────────────────────────────────────────────────────
void setLedCount(int count) {
  if (count < 0) count = 0;
  if (count > 5) count = 5;

  digitalWrite(LED1_PIN,  count >= 1 ? HIGH : LOW);
  digitalWrite(LED2_PIN,  count >= 2 ? HIGH : LOW);
  digitalWrite(LED3_PIN,  count >= 3 ? HIGH : LOW);
  digitalWrite(FLASH_PIN, count >= 4 ? HIGH : LOW);

  current_fingers = count;

  Serial.printf("[LEDS] fingers=%d  L1=%s L2=%s L3=%s FLASH=%s\n",
    count,
    count >= 1 ? "ON" : "off",
    count >= 2 ? "ON" : "off",
    count >= 3 ? "ON" : "off",
    count >= 4 ? "ON" : "off");
}

// ── Rich OLED renderer ────────────────────────────────────────────────────────
//
//  ┌────────────────────────┐  y=0
//  │ ▌ESP32-CAM             │  title bar (filled, 13px tall)
//  ├────────────────────────┤  y=14
//  │ Fingers: 3  [███░░]    │  y=17  finger count + bar graph
//  │ FPS: 12.4              │  y=28
//  │ RSSI: -62 dBm  42s     │  y=38  RSSI + uptime
//  │ WDG: 3.2s              │  y=50  watchdog countdown (or "OK")
//  └────────────────────────┘
//
void oled_update() {
  if (!oled_ok) return;

  display.clearDisplay();

  // ── Title bar ──────────────────────────────────────────────────────────
  display.fillRect(0, 0, SCREEN_W, 13, WHITE);
  display.setTextColor(BLACK);
  display.setTextSize(1);
  display.setCursor(3, 3);
  display.print("ESP32-CAM");

  display.setTextColor(WHITE);

  // ── Row 1: finger count + bar graph ───────────────────────────────────
  display.setCursor(0, 17);
  display.print("Fingers:");
  display.print(current_fingers);

  // Bar graph: 5 blocks, each 10px wide × 7px tall, 2px gap
  // Filled blocks = current_fingers (capped at 5)
  int bar_x = 72;
  int bar_y = 16;
  int bar_w = 9;
  int bar_h = 8;
  int bar_gap = 2;
  for (int i = 0; i < 5; i++) {
    int bx = bar_x + i * (bar_w + bar_gap);
    if (i < current_fingers) {
      display.fillRect(bx, bar_y, bar_w, bar_h, WHITE);   // filled
    } else {
      display.drawRect(bx, bar_y, bar_w, bar_h, WHITE);   // outline
    }
  }

  // ── Row 2: stream FPS ─────────────────────────────────────────────────
  display.setCursor(0, 28);
  display.print("FPS: ");
  display.print(stream_fps, 1);

  // ── Row 3: RSSI + uptime ──────────────────────────────────────────────
  int32_t rssi = WiFi.RSSI();
  unsigned long uptime_s = millis() / 1000;
  display.setCursor(0, 38);
  display.print("RSSI:");
  display.print(rssi);
  display.print("dBm ");
  // Uptime: show as Xm Ys if >= 60s, else just Xs
  if (uptime_s >= 60) {
    display.print(uptime_s / 60);
    display.print("m");
    display.print(uptime_s % 60);
    display.print("s");
  } else {
    display.print(uptime_s);
    display.print("s");
  }

  // ── Row 4: watchdog status / OTA progress ────────────────────────────
  display.setCursor(0, 50);
  if (ota_progress >= 0) {
    // OTA in progress – show a progress bar
    display.print("OTA:");
    int bar_w = 90;
    int filled = (bar_w * ota_progress) / 100;
    display.drawRect(28, 51, bar_w, 7, WHITE);
    if (filled > 0) display.fillRect(28, 51, filled, 7, WHITE);
    display.setCursor(120, 50);
    display.print(ota_progress);
  } else if (watchdog_fired) {
    display.print("WDG: FIRED - LEDs off");
  } else {
    unsigned long now2 = millis();
    unsigned long elapsed2 = now2 - last_fingers_ms;
    if (elapsed2 < WATCHDOG_MS) {
      unsigned long remaining_ms = WATCHDOG_MS - elapsed2;
      display.print("WDG: ");
      display.print(remaining_ms / 1000);
      display.print(".");
      display.print((remaining_ms % 1000) / 100);
      display.print("s");
    } else {
      display.print("WDG: OK");
    }
  }

  display.display();
}

// ── Boot OLED screen ──────────────────────────────────────────────────────────
void oled_boot(const String& line1, const String& line2 = "", const String& line3 = "") {
  if (!oled_ok) return;
  display.clearDisplay();
  display.fillRect(0, 0, SCREEN_W, 13, WHITE);
  display.setTextColor(BLACK);
  display.setTextSize(1);
  display.setCursor(3, 3);
  display.print("ESP32-CAM");
  display.setTextColor(WHITE);
  display.setCursor(0, 17);
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

  // ── OLED ─────────────────────────────────────────────────────────────────
  Serial.printf("[OLED] I2C init: SDA=GPIO%d  SCL=GPIO%d\n", OLED_SDA, OLED_SCL);
  Wire.begin(OLED_SDA, OLED_SCL);
  Wire.setClock(100000);
  delay(200);

  Serial.println("[OLED] Scanning I2C bus...");
  bool found_any = false;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[OLED] Device at 0x%02X\n", addr);
      found_any = true;
    }
  }
  if (!found_any) Serial.println("[OLED] WARNING: No I2C devices found!");

  oled_ok = false;
  for (int retry = 0; retry < 3 && !oled_ok; retry++) {
    oled_ok = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
    if (!oled_ok) {
      Serial.printf("[OLED] Attempt %d failed\n", retry + 1);
      delay(150);
    }
  }

  if (!oled_ok) {
    Serial.println("[OLED] FAILED – check SDA→GPIO15, SCL→GPIO14, 3.3V, GND");
  } else {
    Serial.println("[OLED] OK");
    display.setFont(nullptr);
    oled_boot("Booting...");
  }

  // ── LEDs ─────────────────────────────────────────────────────────────────
  pinMode(LED1_PIN,  OUTPUT);
  pinMode(LED2_PIN,  OUTPUT);
  pinMode(LED3_PIN,  OUTPUT);
  pinMode(FLASH_PIN, OUTPUT);
  setLedCount(0);
  last_fingers_ms = millis();   // initialise watchdog clock
  Serial.printf("[LEDS] GPIO%d/%d/%d/%d(flash) → all OFF\n",
    LED1_PIN, LED2_PIN, LED3_PIN, FLASH_PIN);

  // ── Camera ───────────────────────────────────────────────────────────────
  Serial.println("[CAM] Configuring...");
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
    config.frame_size   = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count     = 2;
    config.fb_location  = CAMERA_FB_IN_PSRAM;
    config.grab_mode    = CAMERA_GRAB_LATEST;
    Serial.println("[CAM] PSRAM mode");
  } else {
    config.frame_size   = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count     = 1;
    config.fb_location  = CAMERA_FB_IN_DRAM;
    config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;
    Serial.println("[CAM] DRAM mode");
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CAM] ERROR 0x%x\n", err);
    oled_boot("Camera ERROR", String("0x") + String(err, HEX));
    return;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    s->set_framesize(s, FRAMESIZE_VGA);
    s->set_quality(s, 12);
    s->set_brightness(s, 1);
    s->set_saturation(s, 0);
    s->set_gainceiling(s, (gainceiling_t)2);
    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_aec2(s, 1);
  }
  Serial.println("[CAM] OK");

  // ── WiFi ─────────────────────────────────────────────────────────────────
  Serial.printf("[WIFI] Connecting to %s ...\n", ssid);
  oled_boot("Connecting WiFi", String(ssid));
  WiFi.begin(ssid, password);
  int dots = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (++dots % 10 == 0) oled_boot("WiFi...", String(ssid));
  }
  Serial.println(" CONNECTED!");

  String ip = WiFi.localIP().toString();
  Serial.printf("[WIFI] IP: %s\n", ip.c_str());
  oled_boot("WiFi OK", ip, "/stream");

  // ── OTA ──────────────────────────────────────────────────────────────────
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);

  ArduinoOTA.onStart([]() {
    String type = (ArduinoOTA.getCommand() == U_FLASH) ? "firmware" : "filesystem";
    Serial.println("[OTA] Starting update: " + type);
    ota_progress = 0;
    // Turn off LEDs during OTA – don't want them stuck on if update fails
    setLedCount(0);
    oled_boot("OTA Update", "Starting...");
  });

  ArduinoOTA.onEnd([]() {
    Serial.println("\n[OTA] Complete – rebooting");
    ota_progress = 100;
    oled_boot("OTA Done", "Rebooting...");
    delay(500);
  });

  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    int pct = (progress * 100) / total;
    ota_progress = pct;
    if (pct % 10 == 0) {
      Serial.printf("[OTA] %d%%\n", pct);
    }
  });

  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("[OTA] ERROR %u: ", error);
    if      (error == OTA_AUTH_ERROR)    Serial.println("Auth failed");
    else if (error == OTA_BEGIN_ERROR)   Serial.println("Begin failed");
    else if (error == OTA_CONNECT_ERROR) Serial.println("Connect failed");
    else if (error == OTA_RECEIVE_ERROR) Serial.println("Receive failed");
    else if (error == OTA_END_ERROR)     Serial.println("End failed");
    ota_progress = -1;
    oled_boot("OTA ERROR", "Check serial");
  });

  ArduinoOTA.begin();
  Serial.printf("[OTA] Ready  hostname=%s  password=%s\n", OTA_HOSTNAME, OTA_PASSWORD);
  Serial.printf("[OTA] Upload via: pio run --target upload  (espota protocol)\n");

  startCameraServer();

  // Seed the watchdog so it doesn't fire immediately on boot
  last_fingers_ms = millis();

  Serial.println("========================================");
  Serial.println("[READY] System ready!\n");
}

// ─────────────────────────────────────────────────────────────────────────────
// loop – runs watchdog check + OLED refresh every 500 ms
// ─────────────────────────────────────────────────────────────────────────────
void loop() {
  ArduinoOTA.handle();   // must be called every loop – listens for OTA upload
  server.handleClient();

  static unsigned long last_oled_ms = 0;
  unsigned long now = millis();

  // Update OLED every 500 ms (fast enough to feel live, slow enough not to
  // waste CPU that the stream handler needs)
  if (now - last_oled_ms >= 500) {
    last_oled_ms = now;

    // ── Watchdog check ──────────────────────────────────────────────────
    if (!watchdog_fired && (now - last_fingers_ms) >= WATCHDOG_MS) {
      Serial.println("[WDG] No /fingers for 5s – turning all LEDs off");
      setLedCount(0);
      watchdog_fired = true;
    }

    // ── Refresh OLED ────────────────────────────────────────────────────
    oled_update();
  }
}

// ── MJPEG stream handler ──────────────────────────────────────────────────────
void handle_jpg_stream(void) {
  WiFiClient client = server.client();
  Serial.println("[STREAM] Client connected");

  client.print("HTTP/1.1 200 OK\r\n"
               "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
               "Cache-Control: no-cache\r\n"
               "Connection: close\r\n"
               "Access-Control-Allow-Origin: *\r\n"
               "\r\n");

  int           frame_count = 0;
  int           fail_count  = 0;
  unsigned long fps_timer   = millis();

  while (client.connected()) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      if (++fail_count > 5) { Serial.println("[STREAM] Too many fails"); break; }
      delay(10);
      continue;
    }
    fail_count = 0;

    if (fb->len == 0) { esp_camera_fb_return(fb); continue; }

    char hdr[128];
    int hdr_len = snprintf(hdr, sizeof(hdr),
      "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
      (unsigned)fb->len);
    client.write((uint8_t*)hdr, hdr_len);

    const uint8_t* ptr = fb->buf;
    size_t remaining   = fb->len;
    while (remaining > 0 && client.connected()) {
      size_t chunk = remaining > 1460 ? 1460 : remaining;
      client.write(ptr, chunk);
      ptr += chunk; remaining -= chunk;
    }
    client.write((uint8_t*)"\r\n", 2);
    esp_camera_fb_return(fb);
    frame_count++;

    // Process pending HTTP requests (e.g. /fingers) after every frame
    server.handleClient();

    // Update FPS counter every 30 frames
    if (frame_count % 30 == 0) {
      unsigned long now = millis();
      stream_fps = 30000.0f / (float)(now - fps_timer);
      fps_timer  = now;
      Serial.printf("[STREAM] %d frames  FPS: %.1f\n", frame_count, stream_fps);
    }
  }

  Serial.printf("[STREAM] Disconnected after %d frames\n", frame_count);
}

// ── Single capture ────────────────────────────────────────────────────────────
void handle_capture(void) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) { server.send(500, "text/plain", "Capture failed"); return; }
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

  setLedCount(n);

  // Reset watchdog – laptop is alive
  last_fingers_ms = millis();
  watchdog_fired  = false;

  Serial.printf("[FINGERS] n=%d  watchdog reset\n", n);

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", "OK");
}

// ── Root page ─────────────────────────────────────────────────────────────────
void handle_root(void) {
  String ip   = WiFi.localIP().toString();
  String html = "<html><head><title>ESP32-CAM</title></head>"
                "<body style='background:#111;color:#eee;font-family:sans-serif;text-align:center'>"
                "<h2>ESP32-CAM Stream</h2>"
                "<img src='/stream' style='max-width:100%;border:2px solid #444'><br><br>"
                "<p>IP: " + ip + "</p>"
                "</body></html>";
  server.send(200, "text/html", html);
}

// ─────────────────────────────────────────────────────────────────────────────
void startCameraServer() {
  server.on("/",        HTTP_GET, handle_root);
  server.on("/stream",  HTTP_GET, handle_jpg_stream);
  server.on("/capture", HTTP_GET, handle_capture);
  server.on("/fingers", HTTP_GET, handle_fingers);
  server.begin();
  Serial.println("[SERVER] Routes: /  /stream  /capture  /fingers?n=0..5");
}

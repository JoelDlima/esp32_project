/*
ESP32-CAM Test Code
This code initializes the camera and starts a web server to stream video.
Connect to the ESP32-CAM's IP address from your laptop browser to view the video stream.
*/

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>

// Replace with your WiFi credentials
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// Camera pin definition for AI-Thinker ESP32-CAM
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

// Onboard red LED (GPIO 33, active LOW — LOW=ON, HIGH=OFF)
#define LED_PIN 33
unsigned long led_off_at = 0;  // millis() timestamp when LED should turn off

void startCameraServer();

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Force highest continuous resolution (B). If this fails, we'll revert to single-shot strategy.
  if (psramFound()) {
    config.frame_size = FRAMESIZE_SVGA;  // 640x480
    config.jpeg_quality = 3;            // 0=best quality, 63=worst
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size = FRAMESIZE_SVGA;  // 640x480
    config.jpeg_quality = 3;            // 0=best quality, 63=worst
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  // Camera init
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x", err);
    return;
  }

  // Connect to Wi-Fi
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("");
  Serial.println("WiFi connected");
  Serial.print("Camera Stream Ready! Go to: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":80");

  // LED setup
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);  // OFF (active LOW)

  // Start streaming web server
  startCameraServer();
}

void loop() {
  server.handleClient();
  // Auto-off: turn LED off when timer expires
  if (led_off_at > 0 && millis() >= led_off_at) {
    digitalWrite(LED_PIN, HIGH);  // OFF
    led_off_at = 0;
  }
  delay(1);
}

void handle_jpg_stream(void) {
  WiFiClient client = server.client();
  String response = "HTTP/1.1 200 OK\r\n";
  response += "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n";
  server.sendContent(response);

  while (1) {
    camera_fb_t * fb = NULL;
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed");
      return;
    }
    response = "--frame\r\n";
    response += "Content-Type: image/jpeg\r\n\r\n";
    server.sendContent(response);
    server.sendContent((const char*)fb->buf, fb->len);
    server.sendContent("\r\n");
    esp_camera_fb_return(fb);
    if (!client.connected()) break;
  }
}

void handle_led(void) {
  // (Re)start the 2-second on-timer
  led_off_at = millis() + 2000;
  digitalWrite(LED_PIN, LOW);  // ON
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", "LED ON");
}

void handle_jpg_capture(void) {
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

void startCameraServer() {
  server.on("/", HTTP_GET, []() {
    server.send(200, "text/html",
      "<html><head><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
      "<style>"
      "body{margin:0;background:#000;display:flex;justify-content:center;align-items:center;height:100vh;color:#fff;}"
      ".container{display:flex;flex-direction:column;align-items:center;}"
      "img{max-width:90vw;max-height:80vh;width:640px;height:auto;border:4px solid #222;border-radius:6px;}"
      "h1{font-family:Arial,Helvetica,sans-serif;font-size:18px;margin-bottom:12px;}"
      "</style></head><body><div class=\"container\"><h1>ESP32-CAM</h1><img src=\'/stream\' alt=\'stream\'></div></body></html>");
  });
  server.on("/stream", HTTP_GET, handle_jpg_stream);
  server.on("/capture", HTTP_GET, handle_jpg_capture);
  server.on("/led", HTTP_GET, handle_led);
  server.begin();
}

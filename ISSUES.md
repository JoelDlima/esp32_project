# ESP32-CAM Project — Issues Log & Prevention Guide

**Project:** Hand Detection with LED control + OLED display  
**Board:** AI-Thinker ESP32-CAM  
**Date resolved:** 2026-05-27

---

## Issue 1 — OLED display completely blank

### What was happening
The OLED never turned on. Serial output showed the init attempts failing silently.

### Root cause A — `Wire.setClock()` called before `Wire.begin()`
```cpp
// ❌ WRONG – setClock before begin is a no-op on ESP32 Arduino
Wire.setClock(100000);
Wire.begin(OLED_SDA, OLED_SCL);

// ✅ CORRECT – begin first, then set clock
Wire.begin(OLED_SDA, OLED_SCL);
Wire.setClock(100000);
```
On ESP32 Arduino core, `Wire.setClock()` has no effect until after `Wire.begin()` is called.
The bus was left running at the default (unpredictable) speed, causing the SSD1306 to miss ACKs.

### Root cause B — SDA and SCL pins were swapped in the defines
```cpp
// ❌ WRONG (was in main.cpp)
#define OLED_SDA  14
#define OLED_SCL  15

// ✅ CORRECT (matches physical wiring and oled_test.ino)
#define OLED_SDA  15
#define OLED_SCL  14
```
The oled_test sketch (which worked) used SDA=15, SCL=14. The main firmware had them reversed.
I2C is not symmetric — swapping SDA/SCL means the device never responds.

### How to prevent in future ESP32-CAM projects
1. Always call `Wire.begin(SDA, SCL)` **before** `Wire.setClock()`.
2. Add an I2C bus scan at boot (loop addr 1–127, `Wire.endTransmission() == 0`).
   This tells you immediately whether the device is physically present and at what address.
3. Cross-check pin numbers against a known-good test sketch before integrating.
4. Add a 100–200 ms delay after `Wire.begin()` before the first transaction — the SSD1306
   needs time to power up its charge pump.

---

## Issue 2 — LEDs not lighting up / ESP32 boot failures

### What was happening
LEDs wired to GPIO2 and GPIO12 either never turned on, or the ESP32 would fail to boot
intermittently (random resets, stuck in bootloader).

### Root cause — Strapping pins used as LED outputs

The AI-Thinker ESP32-CAM has several **strapping pins** that the chip samples at reset to
decide its boot mode. Driving them HIGH or LOW externally (via an LED circuit) can override
the internal pull resistors and cause the wrong boot mode to be selected.

| GPIO | Strapping role | Effect if driven HIGH at boot |
|------|---------------|-------------------------------|
| GPIO0 | Boot mode | Forces download mode (can't run firmware) |
| GPIO2 | Must be LOW or floating | Blocks serial download if HIGH |
| GPIO12 | Flash voltage select | HIGH = 1.8 V flash mode → chip won't boot with 3.3 V flash |
| GPIO15 | JTAG / UART log | Suppresses boot log if LOW |

GPIO12 is the most dangerous: pulling it HIGH at boot permanently selects 1.8 V flash
voltage. On boards with 3.3 V flash (all AI-Thinker modules), this causes an immediate
boot failure that looks like a random crash.

### Fix
Move LEDs to pins that are free from strapping duties:

| LED | Old pin | New pin | Notes |
|-----|---------|---------|-------|
| LED1 (1 finger) | GPIO2 | **GPIO13** | Free, safe |
| LED2 (2 fingers) | GPIO13 | **GPIO16** | Free, safe |
| LED3 (3 fingers) | GPIO12 | **GPIO12** | Safe *after* boot as OUTPUT |
| Flash (4-5 fingers) | — | **GPIO4** | Onboard white flash LED |

> GPIO12 is safe to use as a digital OUTPUT once the chip has already booted.
> The danger is only at the moment of reset. If your LED circuit has a pull-up resistor
> that holds GPIO12 HIGH during power-on, add a 10 kΩ pull-down to GND on that line.

### How to prevent in future ESP32-CAM projects
1. **Never connect external pull-ups or LEDs to GPIO0, GPIO2, GPIO12 without a pull-down.**
2. Keep GPIO0 completely free (or add a momentary button to GND for flashing only).
3. Preferred safe GPIO pins for user I/O on AI-Thinker ESP32-CAM:
   - **Outputs:** GPIO13, GPIO16, GPIO4 (flash), GPIO33
   - **Inputs:** GPIO34, GPIO35, GPIO36, GPIO39 (input-only, no pull resistors)
   - **I2C:** GPIO14 (SCL), GPIO15 (SDA)
4. Always check the AI-Thinker schematic before assigning pins.
   Reference: https://github.com/raphaelbs/esp32-cam-ai-thinker

---

## Issue 3 — Camera stream laggy / localhost feed stuttering

### What was happening
The browser feed at `localhost:5000` was updating very slowly (1–2 fps, sometimes freezing).
Hand detection was delayed by several seconds behind real movement.

### Root cause A — LED HTTP requests blocked the detection loop
```python
# ❌ WRONG – blocks the entire detection loop for up to 10 seconds
requests.get(f"{fingers_url}?n={leds_to_set}", timeout=10)
```
Every time the finger count changed, the Python script made a synchronous HTTP request to
the ESP32. If WiFi was slow or the ESP32 was busy streaming, this call could take 2–10 seconds.
During that time, no new frames were decoded and no new detections ran.

**Fix:** Fire-and-forget background thread for LED updates.
```python
# ✅ CORRECT – non-blocking, never stalls the detection loop
def send_leds_async(fingers_url, n):
    t = threading.Thread(target=_send_led_worker, args=(fingers_url, n), daemon=True)
    t.start()
```
A worker thread handles the HTTP call. If a new value arrives while a send is in flight,
it is queued and sent immediately after the current request finishes.

### Root cause B — MJPEG parser was processing stale frames
```python
# ❌ WRONG – finds the FIRST complete JPEG in the buffer
start_idx = stream_buffer.find(b'\xff\xd8')
end_idx   = stream_buffer.find(b'\xff\xd9')
```
If the buffer accumulated 3–4 frames (common when detection was slow), the code always
decoded the oldest one. The display was always showing frames that were several seconds old.

**Fix:** Use `rfind` to get the LAST complete JPEG — always the freshest frame.
```python
# ✅ CORRECT – finds the LAST (newest) complete JPEG
eoi = buf.rfind(b'\xff\xd9')
soi = buf.rfind(b'\xff\xd8', 0, eoi)
jpg = buf[soi : eoi + 2]
buf = buf[eoi + 2:]   # discard everything before this frame
```

### Root cause C — ESP32 stream handler used `server.sendContent()` for large payloads
The Arduino `WebServer::sendContent()` tries to write the entire JPEG in one call.
For a 20–30 KB frame this can stall the TCP stack on the ESP32, causing the client to
wait for the full buffer to flush before receiving anything.

**Fix:** Write JPEG data in 1460-byte chunks (one TCP segment at a time).
```cpp
// ✅ CORRECT – chunked write, keeps TCP pipeline full
while (remaining > 0 && client.connected()) {
    size_t chunk = remaining > 1460 ? 1460 : remaining;
    client.write(ptr, chunk);
    ptr += chunk; remaining -= chunk;
}
```

### How to prevent in future ESP32-CAM projects
1. **Never make synchronous HTTP calls inside a frame-processing loop.**
   Always use a background thread or async queue for outbound requests.
2. **Always grab the latest frame, not the oldest.**
   Use `rfind` on the MJPEG buffer, or set `CAMERA_GRAB_LATEST` in the camera config.
3. **Write large payloads in MTU-sized chunks** (~1460 bytes) on the ESP32 side.
4. Set `config.grab_mode = CAMERA_GRAB_LATEST` when PSRAM is available — this tells the
   camera driver to overwrite old frame buffers instead of queuing them.
5. Use `fb_count = 2` with PSRAM so the camera can capture the next frame while the
   current one is being transmitted.

---

## Issue 4 — LEDs stayed on when hand was removed

### What was happening
When the hand left the frame or fingers were lowered, the LEDs stayed in their last state.
The ESP32 only updated when it received a new `/fingers?n=X` request — if no hand was
detected, no request was sent.

### Root cause — Python only sent updates on *change*, not on *no-hand*
```python
# ❌ WRONG – if no hand, raised=0 and leds=0, but last_sent_leds was already 0
#            so the condition was False and nothing was sent on first no-hand frame
if leds != last_sent_leds:
    send_leds_async(fingers_url, leds)
```
The first time a hand disappeared, `leds` became 0 and `last_sent_leds` was already 0
from initialisation, so the update was skipped. LEDs stayed in whatever state they were in.

**Fix:** Initialise `last_sent_leds = -1` so the first frame (even with 0 fingers) always
triggers a send. The mapping also explicitly sets `leds = 0` when `not detected`.
```python
last_sent_leds = -1   # force a send on the very first frame

# In the loop:
if not detected:
    leds = 0          # explicit: no hand = all off
```

### How to prevent in future ESP32-CAM projects
1. Initialise `last_sent_leds = -1` (not 0) so the first detection result always fires.
2. Explicitly handle the "no hand" case — don't rely on `raised` being 0 naturally.
3. Consider adding a watchdog on the ESP32: if no `/fingers` request arrives for N seconds,
   turn all LEDs off automatically. This protects against Python crashes or WiFi drops.

---

## Quick Reference — Safe GPIO Pins for AI-Thinker ESP32-CAM

```
┌─────────┬──────────────────────────────────────────────────────┐
│  GPIO   │  Notes                                               │
├─────────┼──────────────────────────────────────────────────────┤
│  GPIO0  │ ⚠️  Boot/flash strapping – keep floating or button   │
│  GPIO1  │ UART TX – avoid unless serial not needed             │
│  GPIO2  │ ⚠️  Strapping pin – avoid pull-ups at boot           │
│  GPIO3  │ UART RX – avoid unless serial not needed             │
│  GPIO4  │ ✅ Onboard flash LED – safe digital output           │
│  GPIO5  │ Camera D0 – reserved                                 │
│  GPIO12 │ ⚠️  Strapping (flash voltage) – safe AFTER boot      │
│  GPIO13 │ ✅ Free – good for LEDs, buttons                     │
│  GPIO14 │ ✅ Free – good for I2C SCL                           │
│  GPIO15 │ ✅ Free – good for I2C SDA                           │
│  GPIO16 │ ✅ Free – good for LEDs, buttons                     │
│  GPIO18 │ Camera D3 – reserved                                 │
│  GPIO19 │ Camera D4 – reserved                                 │
│  GPIO21 │ Camera D5 – reserved                                 │
│  GPIO22 │ Camera PCLK – reserved                               │
│  GPIO23 │ Camera HREF – reserved                               │
│  GPIO25 │ Camera VSYNC – reserved                              │
│  GPIO26 │ Camera SIOD – reserved                               │
│  GPIO27 │ Camera SIOC – reserved                               │
│  GPIO32 │ Camera PWDN – reserved                               │
│  GPIO33 │ ✅ Free – good for LEDs, buttons                     │
│  GPIO34 │ ✅ Input only – good for sensors (no pull resistors) │
│  GPIO35 │ ✅ Input only – good for sensors                     │
│  GPIO36 │ ✅ Input only – good for sensors                     │
│  GPIO39 │ ✅ Input only – good for sensors                     │
└─────────┴──────────────────────────────────────────────────────┘
```

---

## Checklist for New ESP32-CAM Projects

- [ ] Call `Wire.begin(SDA, SCL)` **before** `Wire.setClock()`
- [ ] Add I2C bus scan at boot to verify device presence
- [ ] Avoid GPIO0, GPIO2, GPIO12 for external pull-ups or LEDs
- [ ] Use `CAMERA_GRAB_LATEST` + `fb_count=2` when PSRAM is available
- [ ] Never make blocking HTTP calls inside a frame-processing loop
- [ ] Use `rfind` (not `find`) when parsing MJPEG buffers to get the latest frame
- [ ] Write large TCP payloads in ≤1460-byte chunks on the ESP32
- [ ] Initialise LED state trackers to `-1` so the first frame always triggers an update
- [ ] Add a no-hand timeout on the ESP32 to auto-clear LEDs if Python crashes

# ESP32-S3 firmware (Phase 6 — not started)

Target: Waveshare ESP32-S3-Touch-LCD-4.3C (ESP-IDF 5.x + LVGL 8/9).

The firmware is **not written yet**. Everything it needs from the server exists and is tested:

* Protocol: [`docs/mabcat/WEBSOCKET_PROTOCOL.md`](../../docs/mabcat/WEBSOCKET_PROTOCOL.md)
* Hardware design notes (audio format, buffering, reconnection, LVGL screens, ear servos, memory budget):
  [`docs/mabcat/HARDWARE_ESP32.md`](../../docs/mabcat/HARDWARE_ESP32.md)
* Reference behaviour: `device_simulator.py` in this folder — the firmware should do exactly what it does.
* Visual reference for expressions: `mabixai/web-console/robot.js`.

Suggested component layout: `net/` (Wi-Fi, TLS WebSocket, reconnect/backoff, heartbeat) · `audio/` (I2S
capture → 16 kHz mono s16, PSRAM ring buffer, playback jitter buffer) · `ui/` (LVGL face, lesson, quiz,
image, drawing screens) · `servo/` (ear easing + calibration) · `cache/` (images, fixed phrases) ·
`config/` (device id + token in encrypted NVS).

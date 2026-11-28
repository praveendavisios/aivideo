# RoboCat hardware integration — Waveshare ESP32-S3-Touch-LCD-4.3C

Status: **protocol and design defined, firmware not yet written or tested on hardware.** The web
teaching console simulates the robot screen so the backend can be tested without the board.
`mabixai/esp32-firmware/device_simulator.py` is a reference client that behaves like the firmware.

## Division of work

| ESP32-S3 (device) | Backend (server / GPU PC) |
|---|---|
| Wi-Fi, TLS WebSocket client, device token | Authentication, consent, usage limits |
| Microphone capture, buffering, optional on-device VAD hint | VAD, segmentation, STT model routing |
| Speaker playback of streamed PCM | LLM teaching, curriculum RAG, TTS |
| LVGL UI: eyes, states, lesson text, images, quizzes, drawing | Lesson plans, quizzes, evaluation, progress |
| Servo control of the ears | Robot expression / ear commands |
| Cache of images and repeated audio | Media generation (images/video) |

No LLM, STT or API keys on the device. The device only holds its own revocable device token
(rotate it from the parent portal; store it in NVS with flash encryption enabled).

## Audio in (microphone → server)

* Capture at **16 kHz, 16-bit, mono** (mix the dual mics in the codec or average in firmware). If the codec
  only runs at 44.1/48 kHz, send that and declare it — the server resamples.
* Frames of **20 ms** from I2S, batched into **100 ms** WebSocket binary frames (3 200 bytes + 12-byte header).
  Bandwidth ≈ 33 kB/s while the microphone is open.
* Keep a **5 s ring buffer in PSRAM** (160 kB). On a Wi-Fi drop, keep capturing into the ring; after reconnect
  + `auth` + `audio_start`, resend unsent frames with continuing sequence numbers (the server drops anything
  it already has). Discard audio older than 5 s — stale questions confuse children more than a "say that again".
* Only stream while listening (after Mab Cat finishes speaking, or on touch/wake word). The server's VAD
  drops silence, but not streaming silence saves bandwidth and battery.
* Send `playback_started` / `playback_finished` around speaker output so the server can detect barge-in;
  on `stop_speaking`, stop the I2S output and flush the playback buffer immediately.
* Acoustic echo: the speaker will be picked up by the mics. Either mute capture while speaking (simplest), or
  keep capturing for barge-in and rely on the codec's AEC if available. Test both with real children.

## Audio out (TTS → speaker)

* `tts_audio_start` announces `sample_rate` (request 16 kHz in `session_start` → `tts_sample_rate`).
* Buffer ~300 ms before starting I2S playback to absorb Wi-Fi jitter; frames are ≤ 4 096 bytes (128 ms).
* Cache: hash `(text, voice)` → store PCM in flash/SD for fixed phrases (greetings, praise) to save bandwidth.

## Display (LVGL, 800 × 480)

Screens: Face (default) · Lesson card (title + `tts_sentence` captions) · Quiz (question + up to 4 touch
options → `student_answer` with `option_index`) · Image (from `media_ready`) · Drawing pad · Settings/Wi-Fi.

Expressions to implement (match `robot_expression.expression`): neutral, happy, excited, thinking, listening,
speaking (mouth animation while TTS plays), sleeping, concerned, surprised. The console's `robot.js` is the
visual reference.

## Ears (servos)

`ear_left` / `ear_right` are degrees **0 (flat back) … 90 (upright)**. Map to the servo's pulse range with
per-ear calibration; ease over ~300 ms; ignore commands while a movement is in progress if needed. Use a
separate 5 V supply for the servos (they brown-out the ESP32 if fed from its 3.3 V rail) and a PCA9685 or
LEDC PWM channels.

## Connection lifecycle

1. Connect Wi-Fi → open `wss://server/ws/mabcat/<device_id>` → `auth` with the device token.
2. `session_start` for the linked student (from `connected.student_id`).
3. `heartbeat` every 25 s. If no `heartbeat_ack` within 10 s, reconnect with exponential backoff
   (1, 2, 4 … 30 s) and show the "sleeping / no connection" face.
4. On `usage_limit_reached`: show a friendly "see you tomorrow" screen; do not auto-restart sessions.

## Memory budget (8 MB PSRAM)

Mic ring 160 kB · TTS jitter buffer 64 kB · LVGL frame buffers (800×480×2 B ×2) ≈ 1.5 MB · image cache 2–4 MB ·
TLS + WebSocket ~60 kB internal RAM.

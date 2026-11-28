# Mab Cat WebSocket protocol (v1.0)

Two endpoints, same framing and authentication:

| Endpoint | Purpose |
|---|---|
| `/ws/mabcat/{device_id}` | Teaching session: chat, lessons, quizzes, TTS audio, robot commands, optional mic audio |
| `/ws/mabcat/audio/{device_id}` | Dedicated real-time speech-to-text stream |

Use `wss://` in production (TLS terminated by the reverse proxy, see DEPLOYMENT.md).

## 1. Framing

* **Text frames** — one JSON object, `{"type": "...", ...}`, UTF-8.
* **Binary frames** — audio only, with a 12-byte header (all little-endian):

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 2 | magic | `"MC"` (0x4D 0x43) |
| 2 | 1 | version | `1` |
| 3 | 1 | kind | `1` = microphone (device → server), `2` = TTS (server → device) |
| 4 | 4 | stream_id | uint32 — your mic stream id, or the server's TTS `stream_id` |
| 8 | 4 | sequence | uint32, starts at 0, +1 per frame |
| 12 | … | payload | raw PCM in the declared format |

Mic payload: whatever `audio_start`/`session_start` declared (`pcm_s16le` | `pcm_s32le` | `pcm_f32le`, 8–48 kHz, 1–8 channels interleaved). Recommended for the ESP32-S3: **16 kHz, mono, pcm_s16le, 100 ms per frame (3 200 bytes)**. The server always converts explicitly (downmix → resample → 16 kHz mono float).
TTS payload: `pcm_s16le`, mono, at the rate announced in `tts_audio_start` (default 16 kHz; set `tts_sample_rate` in `session_start`). Frames are ≤ 4 096 bytes (128 ms at 16 kHz).

Clients that cannot send binary frames may send `{"type":"audio_chunk","sequence":n,"data":"<base64 PCM>"}`.

Sequence handling: a frame whose sequence ≤ the last one received is a duplicate/late frame and is dropped (counted in `dropped_chunks`); gaps are logged and tolerated.

## 2. Authentication (first message, within 10 s)

```json
{"type": "auth", "token": "mcd_…", "firmware": "1.0.3"}        // robot (device token from registration)
{"type": "auth", "user_token": "<login token>"}                  // web console; user must own the device
```
Tokens are never put in the URL (they would end up in proxy logs). Failure → `{"type":"error","code":"auth_failed"}` and close code 4401.
Success → `{"type":"connected","device_id":"mabcat-001","protocol":"1.0","heartbeat_s":30,"student_id":"stu_…"}`.

## 3. Teaching socket — client → server

| type | fields | notes |
|---|---|---|
| `heartbeat` | — | every ≤ 30 s; server closes after 3 × `heartbeat_s` silence |
| `session_start` | `student_id`, `session_id?`, `curriculum_id?`, `node_id?`, `language?`, `persona?`, `tts` (bool, default true), `tts_sample_rate` (16000) | needs parent learning consent and remaining daily time |
| `start_lesson` | `node_id` | node must be assigned to the student |
| `student_message` | `text`, `language?` | free question or answer; language auto-labelled (e.g. `ml-en`) |
| `student_answer` | `text` or (`option_index` + `options`) | touch or voice answer to the pending `quiz_question` |
| `audio_start` | `sample_rate`, `channels`, `audio_format`, `language` (`auto`/`en`/`ml`/`ml-en`…), `stream_id` | starts mic streaming on this socket; needs voice consent |
| *(binary kind 1)* | PCM | mic audio |
| `speech_ended` | — | client-side end of utterance (push-to-talk released) |
| `audio_stop` | — | ends mic streaming |
| `playback_started` / `playback_finished` | — | robot speaker state, enables barge-in detection |
| `interrupt` | — | stop current TTS (e.g. touch on screen) |
| `robot_expression` | `expression`, `ear_left` 0–90, `ear_right` 0–90, `duration_ms?` | test/remote command; validated and relayed to all screens of the device |
| `tts_request` | `text`, `language?` | speak arbitrary text (testing) |
| `session_end` | — | returns a learning summary |

## 4. Teaching socket — server → client

| type | fields |
|---|---|
| `session_started` | `session_id`, `student`, `next_lesson` |
| `teacher_response` | `session_id`, `text`, `emotion`, `engine` (`offline` / `llm:<provider>:<model>` / `system`), `sources` [{`filename`,`page`,`document_id`}], `in_curriculum?`, `audio_status` (`generating`/`disabled`), `lesson_complete?` |
| `quiz_question` | `question_index`, `total`, `text`, `input` (`voice_or_touch`), `options` (null or list) |
| `answer_evaluation` | `question_index`, `score` 0–1, `correct`, `evaluator` (`keyword-overlap`/`llm:<model>`/`ungraded`), `attempt` |
| `lesson_update` | `step` (`introduction`/`completed`), `node_id`, `title`, `plan_id`, `plan_generator`, `questions`, `objectives`, `score`, `next`, `revision` |
| `robot_expression` | `expression` ∈ neutral, happy, excited, thinking, listening, speaking, sleeping, concerned, surprised; `ear_left`, `ear_right` (degrees, 0 = flat, 90 = upright) |
| `tts_audio_start` | `stream_id`, `format` `pcm_s16le`, `sample_rate`, `channels` 1, `engine` |
| `tts_sentence` | `stream_id`, `text`, `language` (for captions on the screen) |
| *(binary kind 2)* | TTS PCM frames for `stream_id` |
| `tts_audio_end` | `stream_id`, `frames`, `interrupted?` — **match `stream_id`**: an interrupted older stream also ends with this message |
| `tts_error` | `stream_id`, `message` |
| `stop_speaking` | `reason: student_interrupted` — stop playback now (barge-in) |
| `student_transcript` | final voice transcript forwarded to the teacher: `text`, `language`, `provider` |
| `media_ready` | `kind` (`illustration`), `material_id`, `url` (GET with the user token; robots fetch via their owner's API) |
| `usage_limit_reached` | daily limit hit |
| `session_ended` | `summary` (answers, correct, accuracy, level, strengths, needs_practice, next) |
| `heartbeat_ack` | `server_time` |
| `error` | `code`, `message` — codes: `auth_failed`, `auth_timeout`, `consent_required`, `audio_consent_required`, `student_not_found`, `usage_limit_reached`, `no_session`, `node_not_found`, `bad_json`, `bad_message`, `bad_frame`, `bad_audio`, `audio_not_started`, `unknown_type`, `heartbeat_timeout`, `server_error` |

Mic events on the teaching socket use the same messages as the audio socket (`audio_ready` instead of `session_ready`).

## 5. Audio (STT) socket

Client → server: `auth`, `session_start` {`device_id`, `student_id`, `sample_rate`, `channels`, `audio_format`, `language`, `provider?`, `forward_to_teacher` (default true)}, binary kind-1 frames or `audio_chunk`, `speech_detected` (hint, ignored — server runs its own VAD), `speech_ended`, `playback_started`/`playback_finished`, `session_stop`, `heartbeat`.

Server → client:

```json
{"type":"session_ready","session_id":"aud_…","sample_rate_in":48000,"internal_format":"pcm_s16le 16 kHz mono","vad":"webrtc","end_of_utterance_ms":800}
{"type":"speech_detected","session_id":"aud_…","utterance_id":"aud_…-u1","t":0.18}
{"type":"partial_transcript","session_id":"aud_…","utterance_id":"aud_…-u1","text":"Can you explain photo","language":"en","is_final":false,"provider":"faster-whisper"}
{"type":"processing_status","session_id":"aud_…","utterance_id":"aud_…-u1","status":"transcribing","duration":2.64}
{"type":"language_detected","session_id":"aud_…","utterance_id":"aud_…-u1","language":"ml","source":"session|audio-lid|profile"}
{"type":"final_transcript","session_id":"aud_…","utterance_id":"aud_…-u1","text":"Photosynthesis എന്താണ്?","language":"ml-en",
 "provider_language":"ml","is_final":true,"provider":"indicconformer","model":"ai4bharat/indic-conformer-600m-multilingual",
 "confidence":null,"duration":2.1,"processing_ms":340,"rtf":0.16,
 "routing":{"reason":"route[ml]","language_source":"session","fallback_from":null},
 "code_switching":{"primary":"ml","languages":{"en":0.5,"ml":0.5},"mixed":true,"segments":[{"lang":"en","text":"Photosynthesis"},{"lang":"ml","text":"എന്താണ്"}]}}
{"type":"transcription_error","code":"no_provider|timeout|bad_input|stream_too_large|…","message":"…"}
{"type":"stop_speaking","reason":"student_interrupted"}
{"type":"session_stopped","utterances":2,"dropped_chunks":0,"seconds":12.4,"bytes":396800,"vad":"webrtc"}
```

Rules: one `final_transcript` per utterance id; `confidence` is `null` unless a provider reports a calibrated value (none of the current ones do); `language` is the label of the transcript itself (per-word script analysis), `provider_language` what the model used; transcripts are never translated.

When `forward_to_teacher` is true, each final transcript is sent to the AI teacher of the device's active teaching session, and the teacher's reply (and TTS) is delivered on the **teaching** socket.

## 6. Typical robot flow

```
open /ws/mabcat/mabcat-001 → auth → connected
session_start {student_id, tts_sample_rate:16000}        → session_started, robot_expression, teacher_response, tts_audio_start, [binary…], tts_audio_end
audio_start {16000, 1, pcm_s16le, language:"auto"}       → audio_ready
[binary kind 1, 100 ms each]                             → speech_detected → robot_expression(listening)
(child stops talking: 800 ms silence)                    → processing_status → final_transcript → student_transcript
                                                          → robot_expression(thinking) → teacher_response → tts_audio_start → [binary kind 2] → tts_audio_end
playback_started / playback_finished                      (so the server can detect barge-in)
child talks during playback                               → stop_speaking  (stop the speaker immediately)
```

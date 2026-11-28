"""RoboCat device simulator — a reference client that behaves like the ESP32 firmware.

  python mabixai/esp32-firmware/device_simulator.py --url ws://127.0.0.1:8000 --device mabcat-001 \
      --token mcd_… --student stu_… [--wav question.wav] [--say "Can you explain photosynthesis?"]

* authenticates with the device token, starts a session, prints teacher replies
* receives TTS frames (binary "MC" kind 2) and writes them to tts_<stream>.wav
* --wav streams a recording in real time as 100 ms pcm_s16le frames (like the microphone)
* --say sends a typed student message
Firmware developers can mirror this message flow 1:1.
"""
import argparse
import asyncio
import json
import struct
import time
import wave

import websockets

HDR = struct.Struct("<2sBBII")


def read_wav_16k_mono(path):
    with wave.open(path) as w:
        if w.getsampwidth() != 2:
            raise SystemExit("WAV must be 16-bit PCM")
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()


async def main(a):
    tts = {}
    async with websockets.connect(f"{a.url}/ws/mabcat/{a.device}", max_size=2 ** 22) as ws:
        await ws.send(json.dumps({"type": "auth", "token": a.token, "firmware": "simulator"}))
        hello = json.loads(await ws.recv())
        print("←", hello)
        if hello["type"] != "connected":
            return
        await ws.send(json.dumps({"type": "session_start", "student_id": a.student or hello.get("student_id"),
                                  "tts_sample_rate": 16000}))

        async def reader():
            while True:
                m = await ws.recv()
                if isinstance(m, bytes):
                    magic, ver, kind, sid, seq = HDR.unpack_from(m)
                    tts.setdefault(sid, bytearray()).extend(m[HDR.size:])
                    continue
                msg = json.loads(m)
                if msg["type"] in ("teacher_response", "quiz_question", "final_transcript", "error", "transcription_error",
                                   "robot_expression", "stop_speaking", "lesson_update"):
                    print("←", msg["type"], msg.get("text") or msg.get("expression") or msg.get("message") or msg.get("step") or "")
                if msg["type"] == "tts_audio_end" and msg["stream_id"] in tts:
                    path = f"tts_{msg['stream_id']}.wav"
                    with wave.open(path, "wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(tts.pop(msg["stream_id"])))
                    print(f"   (saved {path}{', interrupted' if msg.get('interrupted') else ''})")
        r = asyncio.create_task(reader())

        async def heartbeat():
            while True:
                await asyncio.sleep(25)
                await ws.send(json.dumps({"type": "heartbeat"}))
        hb = asyncio.create_task(heartbeat())
        await asyncio.sleep(2)
        if a.say:
            await ws.send(json.dumps({"type": "student_message", "text": a.say}))
        if a.wav:
            pcm, sr, ch = read_wav_16k_mono(a.wav)
            await ws.send(json.dumps({"type": "audio_start", "sample_rate": sr, "channels": ch, "audio_format": "pcm_s16le",
                                      "language": a.language, "stream_id": 1}))
            step = sr * ch * 2 // 10
            t0 = time.time()
            for seq, i in enumerate(range(0, len(pcm), step)):
                await ws.send(HDR.pack(b"MC", 1, 1, 1, seq) + pcm[i:i + step])
                await asyncio.sleep(max(0, t0 + (seq + 1) * 0.1 - time.time()))
            await ws.send(json.dumps({"type": "speech_ended"}))
        await asyncio.sleep(a.wait)
        hb.cancel(); r.cancel()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="ws://127.0.0.1:8000")
    p.add_argument("--device", default="mabcat-001")
    p.add_argument("--token", required=True)
    p.add_argument("--student")
    p.add_argument("--wav")
    p.add_argument("--say")
    p.add_argument("--language", default="auto")
    p.add_argument("--wait", type=float, default=20)
    asyncio.run(main(p.parse_args()))

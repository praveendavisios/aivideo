"""Wire format shared by the robot, the web console and the server.

JSON text frames carry control events. Binary frames carry audio:

  offset size  field
  0      2     magic  b"MC"
  2      1     version (1)
  3      1     kind   1 = microphone audio (device → server)
                      2 = TTS audio       (server → device)
  4      4     stream_id  uint32 little-endian (audio session / TTS stream)
  8      4     sequence   uint32 little-endian, starts at 0, +1 per frame
  12     …     payload: PCM in the format declared by session_start / tts_audio_start

Clients that cannot send binary frames may send
  {"type": "audio_chunk", "sequence": n, "data": "<base64 PCM>"}.
"""
import base64
import struct

MAGIC = b"MC"
VERSION = 1
KIND_MIC = 1
KIND_TTS = 2
HEADER = struct.Struct("<2sBBII")
PROTOCOL_VERSION = "1.0"


class FrameError(ValueError):
    pass


def pack(kind, stream_id, seq, payload: bytes):
    return HEADER.pack(MAGIC, VERSION, kind, stream_id & 0xFFFFFFFF, seq & 0xFFFFFFFF) + payload


def unpack(frame: bytes):
    if len(frame) < HEADER.size:
        raise FrameError("frame shorter than header")
    magic, ver, kind, stream_id, seq = HEADER.unpack_from(frame)
    if magic != MAGIC or ver != VERSION:
        raise FrameError("bad magic/version")
    return kind, stream_id, seq, frame[HEADER.size:]


def b64_audio(msg):
    try:
        return base64.b64decode(msg.get("data", ""), validate=True)
    except Exception as exc:
        raise FrameError("audio_chunk.data must be base64 PCM") from exc


EXPRESSIONS = {"neutral", "happy", "excited", "thinking", "listening", "speaking", "sleeping", "concerned", "surprised"}


def robot_command(msg):
    """Validate a robot_expression / ear command coming from a console."""
    exp = msg.get("expression", "neutral")
    if exp not in EXPRESSIONS:
        raise FrameError(f"unknown expression {exp}")
    out = {"type": "robot_expression", "expression": exp}
    for k in ("ear_left", "ear_right"):
        if k in msg:
            v = int(msg[k])
            if not 0 <= v <= 90:
                raise FrameError(f"{k} must be 0-90 degrees")
            out[k] = v
    if "duration_ms" in msg:
        out["duration_ms"] = max(0, min(10000, int(msg["duration_ms"])))
    return out

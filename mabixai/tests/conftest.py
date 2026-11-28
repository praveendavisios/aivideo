import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mabcat-test-")
os.environ.setdefault("MABCAT_DATA_DIR", os.path.join(_TMP, "mabcat"))
os.environ.setdefault("MABIX_DATA_DIR", os.path.join(_TMP, "studio"))
os.environ.setdefault("MABIX_DB_PATH", os.path.join(_TMP, "studio", "mabix.db"))
os.environ.setdefault("MABIX_LEGACY_VIDEO_DIR", os.path.join(_TMP, "videos"))
os.environ["MABIX_DISABLE_WORKER"] = "1"
os.environ["MABCAT_LLM_PROVIDER"] = "none"     # offline teaching unless a test fakes an LLM
os.environ["MABCAT_TTS_PROVIDERS"] = "espeak"
os.environ["MABCAT_VAD"] = "webrtc"
os.environ["MABCAT_EOU_MS"] = "600"

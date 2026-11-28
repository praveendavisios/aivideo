# MabixAI (video studio + RoboCat backend) — NVIDIA GPU image. CPU-only: see docs/mabcat/DEPLOYMENT.md
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models/huggingface
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv python3-pip ffmpeg espeak-ng curl \
      tesseract-ocr tesseract-ocr-eng tesseract-ocr-mal tesseract-ocr-hin tesseract-ocr-tam tesseract-ocr-kan \
    && rm -rf /var/lib/apt/lists/*
RUN python3.12 -m venv /venv
ENV PATH=/venv/bin:$PATH
WORKDIR /app
# PyTorch for RTX 50-series (sm_120) needs the CUDA 12.8 wheels
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
COPY requirements.txt requirements-mabcat.txt ./
RUN pip install -r requirements.txt -r requirements-mabcat.txt kokoro "piper-tts==1.2.0" silero-vad
COPY . .
RUN useradd -m -u 1000 mabix && mkdir -p /app/storage /models && chown -R mabix /app/storage /models
USER mabix
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD curl -fs http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "mabixai.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

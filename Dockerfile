# Build:  docker build -t yt-transcript .
# With Whisper speech-to-text for videos that have no captions (bigger image):
#         docker build --build-arg WHISPER=1 -t yt-transcript .
FROM python:3.11-slim

ARG WHISPER=0
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000

# ffmpeg helps yt-dlp with some audio formats; Node.js is the JavaScript runtime
# yt-dlp needs to unlock YouTube audio streams for the Whisper path.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY yt_transcript ./yt_transcript
RUN pip install . \
    && if [ "$WHISPER" = "1" ]; then pip install ".[whisper]"; fi

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

CMD ["yt-transcript", "serve"]

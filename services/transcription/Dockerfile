FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

WORKDIR /app

# Install Python + system deps
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install --yes --no-install-recommends \
        software-properties-common curl pciutils && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install --yes --no-install-recommends \
        python3.13-dev python3.13-venv

# Create venv and install WhisperLiveKit with GPU + diarization
RUN python3.13 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
RUN pip install --no-cache-dir \
    "whisperlivekit[cu129,diarization-sortformer]"

ENV LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

COPY services/transcription/startup.sh ./
RUN chmod 755 startup.sh

CMD ["./startup.sh"]

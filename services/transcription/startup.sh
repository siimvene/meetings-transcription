#!/bin/bash
set -eu -o pipefail

echo "=== GPU Info ==="
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv 2>/dev/null || echo "No GPU detected"

echo "=== Starting WhisperLiveKit ==="
exec wlk \
    --model "${WHISPER_MODEL:-large-v3-turbo}" \
    --backend "${WHISPER_BACKEND:-faster-whisper}" \
    --diarization \
    --host 0.0.0.0 \
    --port 8000

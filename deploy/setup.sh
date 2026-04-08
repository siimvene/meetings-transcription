#!/bin/bash
# Run on the B200 server manually, step by step.
# Assumes: Ubuntu 24.04, NVIDIA drivers already installed, Docker + NVIDIA Container Toolkit ready.
# (The B200 server already has all of this.)

set -eux -o pipefail

INSTALL_DIR="$HOME/meetings-transcription"

### Clone repo
# git clone <repo-url> "$INSTALL_DIR"
# cd "$INSTALL_DIR"

### Configure
cp config/env.example config/.env
echo ">>> Edit config/.env with your vLLM API key and settings"
echo ">>> Then run: docker compose up -d"

### Verify GPU is accessible from Docker
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi

### Build and start
# docker compose build
# docker compose up -d
# docker compose logs -f

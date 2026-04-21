#!/bin/bash
# First-time setup on the B200 server.
# Assumes: Ubuntu 24.04, NVIDIA drivers installed, Docker + NVIDIA Container Toolkit ready.

set -eux -o pipefail

### 1. Clone repo
INSTALL_DIR="$HOME/meetings-transcription"
if [ ! -d "$INSTALL_DIR" ]; then
    git clone https://github.com/siimvene/meetings-transcription.git "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

### 2. Verify GPU is accessible from Docker
echo "=== Verifying GPU access ==="
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi

### 3. Run deploy script (creates .env on first run, then exits for editing)
echo ""
echo "=== Running deploy script ==="
bash deploy/deploy.sh

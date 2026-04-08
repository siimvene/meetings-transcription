#!/bin/bash
# Download NMT model files for the TartuNLP septilang translation worker.
# Run this on the B200 server before starting docker compose.

set -eu -o pipefail

MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$MODELS_DIR"

echo "=== Downloading TartuNLP septilang model ==="
echo "Target: $MODELS_DIR/septilang/"

# Install git-xet for large file support (HuggingFace)
if ! command -v git-xet &>/dev/null; then
    echo "Installing git-xet..."
    curl -sSfL https://hf.co/git-xet/install.sh | sh
fi

# Clone the septilang model from HuggingFace
if [ -d "$MODELS_DIR/septilang" ]; then
    echo "septilang model already exists, skipping download"
else
    echo "Cloning from HuggingFace..."
    cd "$MODELS_DIR"
    git clone https://huggingface.co/tartuNLP/septilang
    echo "Done. Model files:"
    ls -lh "$MODELS_DIR/septilang/"
fi

echo ""
echo "=== Model download complete ==="
echo ""
echo "Expected structure:"
echo "  models/septilang/"
echo "    ├── modular_model.pt"
echo "    ├── dict.*.txt"
echo "    └── sp-model.*.model"
echo ""
echo "The translation-worker docker service mounts ./models:/app/models"

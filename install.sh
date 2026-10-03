#!/usr/bin/env bash
set -e
echo "============================================"
echo " NarrAsync - Setup"
echo "============================================"

if ! command -v python3 >/dev/null; then
  echo "[ERROR] python3 nahi mila. Pehle Python 3.10+ install karein."
  exit 1
fi
if ! command -v ffmpeg >/dev/null; then
  echo "[ERROR] ffmpeg nahi mila."
  echo "  Ubuntu/Debian: sudo apt install ffmpeg"
  echo "  macOS:         brew install ffmpeg"
  exit 1
fi

echo "[1/3] Virtual environment ban raha hai..."
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate

echo "[2/3] Libraries install ho rahi hain (2-4 minute lag sakte hain)..."
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "[3/3] Voice model download ho raha hai (sirf pehli baar, ~150MB)..."
python -c "from faster_whisper import WhisperModel; WhisperModel('base'); print('Model ready.')"

echo ""
echo "============================================"
echo " Setup mukammal! Ab ./start.sh chalayein."
echo "============================================"

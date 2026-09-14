#!/usr/bin/env bash
set -euo pipefail
cd /home/lenovo/nvidia-voice/nemotron-voice-agent
docker compose --profile multilingual-assistant/dgx-spark down

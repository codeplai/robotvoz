#!/usr/bin/env bash
# Estado y logs de los servicios
set -euo pipefail
REPO=/home/lenovo/nvidia-voice/nemotron-voice-agent
PROFILE=multilingual-assistant/dgx-spark
cd "$REPO"
echo "=== Contenedores ==="; docker compose --profile "$PROFILE" ps
echo; echo "=== GPU ==="; nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv
echo; echo "=== Salud app ==="; curl -sk https://localhost:7860/health || echo "app aun no responde"
echo; echo "=== Ultimos logs ==="; docker compose --profile "$PROFILE" logs --tail=30

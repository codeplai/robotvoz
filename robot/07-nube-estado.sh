#!/usr/bin/env bash
# Estado de la VERSION NUBE: contenedor, salud y servicios elegidos
set -euo pipefail
cd /home/lenovo/nvidia-voice/nemotron-voice-agent
echo "=== Contenedor ==="; docker compose --profile multilingual-assistant ps
echo; echo "=== Salud app ==="; curl -sk https://localhost:7860/health || echo "app aun no responde"
echo; echo "=== Servicios de la ultima sesion ==="
docker compose --profile multilingual-assistant logs --tail=400 | grep -E "ASR: server|TTS: server|LLM: model" | tail -3 || true
echo; echo "=== Ultimos logs ==="; docker compose --profile multilingual-assistant logs --tail=30

#!/usr/bin/env bash
# Arranca el agente de voz Nemotron en espanol sobre DGX Spark
set -euo pipefail
REPO=/home/lenovo/nvidia-voice/nemotron-voice-agent
PROFILE=multilingual-assistant/dgx-spark

cd "$REPO"
echo ">> Perfil: $PROFILE"
echo ">> Primera vez: descarga ~60-100 GB de modelos. Puede tardar 30-60 min."
docker compose --profile "$PROFILE" up -d
echo
docker compose --profile "$PROFILE" ps
echo
echo "UI:  https://$(hostname -I | awk '{print $1}'):7860"
echo "Logs: bash /home/lenovo/nvidia-voice/robot/03-estado.sh"

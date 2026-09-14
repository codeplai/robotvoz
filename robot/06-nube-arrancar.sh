#!/usr/bin/env bash
# Arranca la VERSION NUBE: solo el contenedor de la app; LLM, ASR y TTS se
# consumen por API de NVIDIA (no usa la GPU del Spark). Para antes la version
# local, porque las dos usan el puerto 7860.
set -euo pipefail
REPO=/home/lenovo/nvidia-voice/nemotron-voice-agent
cd "$REPO"
if docker compose --profile multilingual-assistant/dgx-spark ps -q | grep -q .; then
  echo ">> Parando la version local (DGX Spark) para liberar el 7860 y la GPU..."
  docker compose --profile multilingual-assistant/dgx-spark down
fi
echo ">> Perfil: multilingual-assistant (nube NVIDIA)"
docker compose --profile multilingual-assistant up -d
echo ">> Esperando a que la app responda..."
for i in $(seq 1 60); do
  curl -sfk https://localhost:7860/health >/dev/null && break
  sleep 2
done
curl -sfk https://localhost:7860/health >/dev/null && echo ">> Lista." || echo ">> Aun no responde: bash $(dirname "$0")/07-nube-estado.sh"
echo
echo "Movil: https://$(hostname -I | awk '{print $1}'):7860/movil/index.html"

#!/usr/bin/env bash
# Para la VERSION NUBE. Para volver a la local: bash 02-arrancar.sh
set -euo pipefail
cd /home/lenovo/nvidia-voice/nemotron-voice-agent
docker compose --profile multilingual-assistant down

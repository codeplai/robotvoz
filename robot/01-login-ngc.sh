#!/usr/bin/env bash
# Autentica Docker contra el registro de NVIDIA (nvcr.io) usando la clave del .env
set -euo pipefail
REPO=/home/lenovo/nvidia-voice/nemotron-voice-agent
set -a; source "$REPO/.env"; set +a

if [ -z "${NVIDIA_API_KEY:-}" ]; then
  echo "ERROR: NVIDIA_API_KEY vacia en $REPO/.env" >&2; exit 1
fi
if [ -z "${HF_TOKEN:-}" ]; then
  echo "AVISO: HF_TOKEN vacia. vLLM no podra descargar Nemotron 3 Nano NVFP4." >&2
fi

printf '%s' "$NVIDIA_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin
echo "Login nvcr.io correcto."

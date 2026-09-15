#!/usr/bin/env bash
# Arranca el agente de voz SIN DOCKER (host-native) en Linux.
# Equivalente Linux de 09-servidor-arrancar.ps1 (Windows), usando venv + pip
# + requirements.txt en vez de uv (por si el equipo destino no tiene uv).
#
# Requisitos, una sola vez:
#   cd nemotron-voice-agent
#   python3 -m venv .venv
#   .venv/bin/pip install -r requirements.txt
#   cd client && npm install && npm run build && cd ..
#   copiar/editar .env con las claves (ver seccion 13 del README para NVIDIA,
#   o seccion 14 para Azure)
#
# El servidor corre en primer plano: Ctrl+C para pararlo.
set -euo pipefail

PUERTO="${1:-7860}"
REPO="$(cd "$(dirname "$0")/../nemotron-voice-agent" && pwd)"
ENV_FILE="$REPO/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "No existe $ENV_FILE." >&2
  exit 1
fi

falta_clave() {
  ! grep -Eq "^$1=\S+" "$ENV_FILE"
}

SELECTION="$(sed -n 's/^EXAMPLE_SELECTION=//p' "$ENV_FILE" | tail -n1 | tr -d '\r')"

if [ "$SELECTION" = "multilingual-assistant-azure" ]; then
  faltan=()
  for k in AZURE_SPEECH_API_KEY AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT; do
    falta_clave "$k" && faltan+=("$k")
  done
  if [ "${#faltan[@]}" -gt 0 ]; then
    echo "Faltan estas claves en $ENV_FILE: ${faltan[*]}" >&2
    exit 1
  fi
elif falta_clave "NVIDIA_API_KEY"; then
  echo "Falta NVIDIA_API_KEY en $ENV_FILE. Consiguela en https://build.nvidia.com/ y pegala ahi (NVIDIA_API_KEY=nvapi-...)." >&2
  exit 1
fi

DIST="$REPO/client/dist"
if [ ! -d "$DIST" ]; then
  echo "No existe $DIST. Antes de la primera vez: cd nemotron-voice-agent/client && npm install && npm run build" >&2
  exit 1
fi

VENV_PY="$REPO/.venv/bin/python3"
if [ ! -x "$VENV_PY" ]; then
  echo "No existe $VENV_PY. Antes de la primera vez:" >&2
  echo "  cd nemotron-voice-agent && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# 'npm run build' borra y recrea client/dist/, asi que la interfaz movil
# (robot/web) se vuelve a copiar dentro en cada arranque. Es barato y evita
# que un rebuild del cliente deje /movil/index.html en 404.
ROBOT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOVIL="$DIST/movil"
mkdir -p "$MOVIL"
cp -r "$ROBOT_DIR"/web/* "$MOVIL"/
rm -f "$MOVIL/index.html.escritorio"

# multilingual_azure reutiliza los skills/prompts de multilingual (no los
# duplica a mano): se copian aqui en cada arranque para que examples_registry
# (que exige un prompts.yaml/tools.yaml literal junto al modulo del ejemplo,
# para /api/deployment) los encuentre. La fuente de verdad sigue siendo
# examples/multilingual/{prompts,tools}.yaml.
MULTILINGUAL_DIR="$REPO/src/examples/multilingual"
AZURE_DIR="$REPO/src/examples/multilingual_azure"
if [ -d "$AZURE_DIR" ]; then
  cp "$MULTILINGUAL_DIR/prompts.yaml" "$AZURE_DIR/prompts.yaml"
  cp "$MULTILINGUAL_DIR/tools.yaml" "$AZURE_DIR/tools.yaml"
fi

cd "$REPO"
echo ">> Arrancando host-native (sin Docker)."
echo ">> UI: https://localhost:$PUERTO  (certificado autofirmado, el navegador avisara una vez)"
echo ">> Movil (mismo origen): https://localhost:$PUERTO/movil/index.html"
echo ">> Ctrl+C para parar."
exec "$VENV_PY" src/server.py --host 0.0.0.0 --port "$PUERTO"

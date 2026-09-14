#!/usr/bin/env bash
# Sirve la interfaz de prueba en http://localhost:8080
set -euo pipefail
DIR="$(cd "$(dirname "$0")/web" && pwd)"
PUERTO="${1:-8080}"
IP=$(hostname -I | awk '{print $1}')
echo "AVISO: para usar el movil, NO uses este servidor."
echo "  El navegador bloquea el microfono sin https."
echo "  Usa:  https://$(hostname -I | awk '{print $1}'):7860/movil/index.html"
echo "  (misma pagina, servida por la app con TLS y en su mismo origen)"
echo
echo "Este servidor solo sirve para desarrollo en el propio Spark:"
echo "  en el Spark : http://localhost:$PUERTO"
echo "  en la red   : http://$IP:$PUERTO   (el microfono solo funciona en localhost)"
echo
cd "$DIR"
exec python3 -m http.server "$PUERTO" --bind 0.0.0.0

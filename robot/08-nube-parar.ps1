# Para la VERSION NUBE. Para volver a la local: .\02-arrancar (no existe en
# Windows todavia porque la local necesita el hardware del DGX Spark).
# Equivalente Windows de 08-nube-parar.sh
$ErrorActionPreference = "Stop"

$repo = Join-Path $PSScriptRoot "..\nemotron-voice-agent"
Push-Location $repo
try {
    docker compose --profile multilingual-assistant down
} finally {
    Pop-Location
}

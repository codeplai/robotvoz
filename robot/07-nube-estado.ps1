# Estado de la VERSION NUBE: contenedor, salud y servicios elegidos
# Equivalente Windows de 07-nube-estado.sh
$ErrorActionPreference = "Stop"

$repo = Join-Path $PSScriptRoot "..\nemotron-voice-agent"
Push-Location $repo
try {
    Write-Host "=== Contenedor ==="
    docker compose --profile multilingual-assistant ps

    Write-Host "`n=== Salud app ==="
    curl.exe -sk https://localhost:7860/health
    if ($LASTEXITCODE -ne 0) { Write-Host "app aun no responde" }

    Write-Host "`n=== Servicios de la ultima sesion ==="
    docker compose --profile multilingual-assistant logs --tail=400 |
        Select-String -Pattern "ASR: server|TTS: server|LLM: model" |
        Select-Object -Last 3

    Write-Host "`n=== Ultimos logs ==="
    docker compose --profile multilingual-assistant logs --tail=30
} finally {
    Pop-Location
}

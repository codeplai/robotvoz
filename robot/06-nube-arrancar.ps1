# Arranca la VERSION NUBE: solo el contenedor de la app; LLM, ASR y TTS se
# consumen por API de NVIDIA (no requiere GPU local). Para antes la version
# local si estuviera encendida, porque las dos usan el puerto 7860.
# Equivalente Windows de 06-nube-arrancar.sh
$ErrorActionPreference = "Stop"

$repo = Join-Path $PSScriptRoot "..\nemotron-voice-agent"
Push-Location $repo
try {
    $localRunning = docker compose --profile multilingual-assistant/dgx-spark ps -q
    if ($localRunning) {
        Write-Host ">> Parando la version local (DGX Spark) para liberar el 7860..."
        docker compose --profile multilingual-assistant/dgx-spark down
    }

    Write-Host ">> Perfil: multilingual-assistant (nube NVIDIA)"
    Write-Host ">> Si es la primera vez, esto construye la imagen (npm ci + npm build + apt); puede tardar varios minutos."
    docker compose --profile multilingual-assistant up -d

    Write-Host ">> Esperando a que la app responda..."
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        curl.exe -sfk https://localhost:7860/health *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if ($ready) {
        Write-Host ">> Lista."
    } else {
        Write-Host ">> Aun no responde: .\07-nube-estado.ps1"
    }

    $ip = (Get-NetIPConfiguration |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
        Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress
    Write-Host ""
    Write-Host "Movil: https://${ip}:7860/movil/index.html"
} finally {
    Pop-Location
}

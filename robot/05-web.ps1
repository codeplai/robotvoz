# Sirve la interfaz de prueba en http://localhost:8080
# Equivalente Windows de 05-web.sh
param([int]$Puerto = 8080)
$ErrorActionPreference = "Stop"

$dir = Join-Path $PSScriptRoot "web"
$ip = (Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress

Write-Host "AVISO: para usar el movil, NO uses este servidor."
Write-Host "  El navegador bloquea el microfono sin https."
Write-Host "  Usa:  https://${ip}:7860/movil/index.html"
Write-Host "  (misma pagina, servida por la app con TLS y en su mismo origen)"
Write-Host ""
Write-Host "Este servidor solo sirve para desarrollo local:"
Write-Host "  en este equipo : http://localhost:$Puerto"
Write-Host "  en la red      : http://${ip}:$Puerto   (el microfono solo funciona en localhost)"
Write-Host ""

Set-Location $dir
python -m http.server $Puerto --bind 0.0.0.0

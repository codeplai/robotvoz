# Autentica Docker contra el registro de NVIDIA (nvcr.io) usando la clave del .env
# Equivalente Windows de 01-login-ngc.sh
$ErrorActionPreference = "Stop"

$repo = Join-Path $PSScriptRoot "..\nemotron-voice-agent"
$envFile = Join-Path $repo ".env"

if (-not (Test-Path $envFile)) {
    Write-Error "No existe $envFile. Crealo con NVIDIA_API_KEY=... (ver seccion 13 del README)."
    exit 1
}

$envVars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $envVars[$matches[1]] = $matches[2].Trim('"').Trim("'")
    }
}

$nvidiaKey = $envVars["NVIDIA_API_KEY"]
if ([string]::IsNullOrWhiteSpace($nvidiaKey)) {
    Write-Error "NVIDIA_API_KEY vacia en $envFile"
    exit 1
}
if ([string]::IsNullOrWhiteSpace($envVars["HF_TOKEN"])) {
    Write-Warning "HF_TOKEN vacia. Solo hace falta para descargar modelos gated con vLLM; no afecta a la version nube."
}

$nvidiaKey | docker login nvcr.io -u '$oauthtoken' --password-stdin
Write-Host "Login nvcr.io correcto."

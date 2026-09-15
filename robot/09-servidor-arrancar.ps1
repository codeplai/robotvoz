# Arranca el agente de voz SIN DOCKER (host-native) en Windows.
# Version nube: LLM, ASR y TTS se consumen por API de NVIDIA con NVIDIA_API_KEY.
#
# Requisitos, una sola vez:
#   cd nemotron-voice-agent
#   python -m pip install --user uv
#   python -m uv sync --group dev
#   cd client; npm install; npm run build; cd ..
#   copiar/editar .env con NVIDIA_API_KEY=...
#
# El servidor corre en primer plano: Ctrl+C para pararlo.
param([int]$Puerto = 7860)
$ErrorActionPreference = "Stop"

$repo = Join-Path $PSScriptRoot "..\nemotron-voice-agent"
$envFile = Join-Path $repo ".env"

if (-not (Test-Path $envFile)) {
    Write-Error "No existe $envFile."
    exit 1
}

$selection = (Select-String -Path $envFile -Pattern '^EXAMPLE_SELECTION=(.+)$').Matches.Groups[1].Value.Trim()
if ($selection -eq "multilingual-assistant-azure") {
    $faltan = @("AZURE_SPEECH_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT") |
        Where-Object { -not (Select-String -Path $envFile -Pattern "^$_=\S+" -Quiet) }
    if ($faltan) {
        Write-Error "Faltan estas claves en ${envFile}: $($faltan -join ', ')"
        exit 1
    }
} elseif (-not (Select-String -Path $envFile -Pattern '^NVIDIA_API_KEY=\S+' -Quiet)) {
    Write-Error "Falta NVIDIA_API_KEY en $envFile. Consiguela en https://build.nvidia.com/ y pegala ahi (NVIDIA_API_KEY=nvapi-...)."
    exit 1
}

$dist = Join-Path $repo "client\dist"
if (-not (Test-Path $dist)) {
    Write-Error "No existe $dist. Antes de la primera vez: cd nemotron-voice-agent\client; npm install; npm run build"
    exit 1
}

# 'npm run build' borra y recrea client/dist/, asi que la interfaz movil
# (robot/web) se vuelve a copiar dentro en cada arranque. Es barato y evita
# que un rebuild del cliente deje /movil/index.html en 404.
$movil = Join-Path $dist "movil"
New-Item -ItemType Directory -Force -Path $movil | Out-Null
Copy-Item (Join-Path $PSScriptRoot "web\*") -Destination $movil -Recurse -Force -Exclude "index.html.escritorio"

# multilingual_azure reutiliza los skills/prompts de multilingual (no los
# duplica a mano): se copian aqui en cada arranque para que examples_registry
# (que exige un prompts.yaml/tools.yaml literal junto al modulo del ejemplo,
# para /api/deployment) los encuentre. La fuente de verdad sigue siendo
# examples/multilingual/{prompts,tools}.yaml.
$multilingualDir = Join-Path $repo "src\examples\multilingual"
$azureDir = Join-Path $repo "src\examples\multilingual_azure"
if (Test-Path $azureDir) {
    Copy-Item (Join-Path $multilingualDir "prompts.yaml") (Join-Path $azureDir "prompts.yaml") -Force
    Copy-Item (Join-Path $multilingualDir "tools.yaml") (Join-Path $azureDir "tools.yaml") -Force
}

Push-Location $repo
try {
    Write-Host ">> Arrancando host-native (sin Docker), perfil nube NVIDIA."
    Write-Host ">> UI: https://localhost:$Puerto  (certificado autofirmado, el navegador avisara una vez)"
    Write-Host ">> Movil (mismo origen): https://localhost:$Puerto/movil/index.html"
    Write-Host ">> Ctrl+C para parar."
    python -m uv run python src/server.py --host 0.0.0.0 --port $Puerto
} finally {
    Pop-Location
}

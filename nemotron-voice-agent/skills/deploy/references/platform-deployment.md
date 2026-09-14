# Platform Deployment Reference

Use from repository root after `deploy` picks a recipe profile.

## Common Setup

```bash
test -f .env || cp .env.example .env
export NGC_API_KEY="$NVIDIA_API_KEY"
echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin
```

Required `.env` keys:
- All recipes: `NVIDIA_API_KEY`
- Any recipe ending in `/dgx-spark` or `/jetson-thor`, plus `omni-assistant/workstation` and `omni-assistant-subagents/workstation` (local Omni vLLM downloads the model from HF on first run): `HF_TOKEN`

## Workstation

Recipes: `generic-assistant/workstation`, `multilingual-assistant/workstation`, `omni-assistant/workstation`, `frontend-backend-agent/workstation`.

Services depend on the recipe:
- `generic-assistant/workstation`: `generic-assistant`, `nvidia-llm`, `nemotron-asr-streaming-english`, `tts-service`
- `multilingual-assistant/workstation`: `multilingual-assistant`, `nvidia-llm`, `parakeet-rnnt-asr`, `tts-service`
- `omni-assistant/workstation`: `omni-assistant`, `nvidia-llm-vllm-omni`, `tts-service`
- `frontend-backend-agent/workstation`: `frontend-backend-agent`, `booking-server`, `nvidia-llm`, `nemotron-asr-streaming-english`, `tts-service`

Requires enough GPU VRAM for the selected local NIM services. Single-GPU hosts are valid when capacity is sufficient. Multi-GPU hosts may split speech sidecars and LLM across devices. For the user-facing VRAM, memory-knob, and device-placement matrix, see [VRAM & hardware support](../../../docs/how-to/configure-llm.md#vram--hardware-support).

```bash
nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader
docker compose --profile generic-assistant/workstation up -d
# or: docker compose --profile multilingual-assistant/workstation up -d
# or: docker compose --profile omni-assistant/workstation up -d
# or: docker compose --profile frontend-backend-agent/workstation up -d
```

## DGX Spark

Recipes: `generic-assistant/dgx-spark`, `multilingual-assistant/dgx-spark`, `omni-assistant/dgx-spark`.

Services depend on the recipe:
- `generic-assistant/dgx-spark`: `generic-assistant`, `nvidia-llm-vllm`, `nemotron-asr-streaming-english`, `tts-service`
- `multilingual-assistant/dgx-spark`: `multilingual-assistant`, `nvidia-llm-vllm`, `parakeet-rnnt-asr`, `tts-service`
- `omni-assistant/dgx-spark`: `omni-assistant`, `nvidia-llm-vllm-omni`, `tts-service`

```bash
free -h
docker compose --profile generic-assistant/dgx-spark up -d
# docker compose --profile multilingual-assistant/dgx-spark up -d
# docker compose --profile omni-assistant/dgx-spark up -d
```

## Jetson Thor

Recipes: `generic-assistant/jetson-thor` and `omni-assistant/jetson-thor`. Multilingual and Omni Assistant Subagents examples are not supported on Jetson today.

Services depend on the recipe:
- `generic-assistant/jetson-thor`: `generic-assistant`, `nvidia-llm-vllm`, `nemotron-speech` (Riva ASR + TTS).
- `omni-assistant/jetson-thor`: `omni-assistant`, `nvidia-llm-vllm-omni`, `nemotron-speech-tts` (Riva TTS only; Omni does its own ASR).

One-time Riva model setup, from the repo parent. Uses the Riva Speech Skills v2.26.0 L4T quickstart (NGC `nvidia/riva` org, 2.26.0 models by default):

```bash
cd ..
ngc registry resource download-version "nvidia/riva/riva_quickstart_arm64:2.26.0"
cd riva_quickstart_arm64_v2.26.0   # resource extracts into this dir
bash riva_init.sh && cd ../nemotron-voice-agent
```

Deploy:

```bash
sudo bash scripts/start-mps.sh
docker compose --profile generic-assistant/jetson-thor up -d
# Omni Assistant (local Omni vLLM + Riva TTS; requires HF_TOKEN):
docker compose --profile omni-assistant/jetson-thor up -d
```

Thor tuning `.env`:

```env
VLLM_MPS_THREAD_PCT=50
RIVA_MPS_THREAD_PCT=50
VLLM_CPUSET=0-3
RIVA_CPUSET=4-7
PIPECAT_CPUSET=8-11
```

If a service fails to start on low memory (e.g. `nvidia-llm-vllm` logs `Engine core initialization failed`), reclaim cached memory and retry:

```bash
free -h
sudo sync && sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
docker compose --profile generic-assistant/jetson-thor up -d
```

## TURN

Add `--profile turn` when clients connect from outside the host network.
The bundled coturn profile uses the `instrumentisto/coturn` image, which is
supported on x86_64 (`linux/amd64`) only. On arm64 hosts, such as Jetson Thor,
do not enable the bundled `turn` profile; set `TURN_URL`, `TURN_USERNAME`, and
`TURN_PASSWORD` for an externally hosted TURN server instead.

Before starting TURN, ensure `.env` contains TURN credentials. Coturn has
compose defaults, but the app only publishes ICE servers to clients when
`TURN_USERNAME` and `TURN_PASSWORD` are present in `.env`.

```bash
test -f .env || cp .env.example .env
grep -Eq '^TURN_USERNAME=.+$' .env || printf '\nTURN_USERNAME=turn-%s\n' "$(openssl rand -hex 4)" >> .env
grep -Eq '^TURN_PASSWORD=.+$' .env || printf 'TURN_PASSWORD=%s\n' "$(openssl rand -hex 24)" >> .env
```

Set `TURN_URL=turn:<turn-host-or-ip>:3478` when TURN runs on a different host,
or when the host derived from the incoming request is not reachable by clients.
Open UDP `3478` and UDP `49160-49200` from client networks.

```bash
docker compose --profile generic-assistant --profile turn up -d
docker compose --profile generic-assistant/workstation --profile turn up -d
```

Verify TURN with:

```bash
docker compose ps coturn
# HTTPS by default; if PIPELINE_TLS=false the HTTPS call fails and the HTTP one returns the config
curl -k https://localhost:${PIPELINE_APP_PORT:-7860}/api/ice-servers \
  || curl http://localhost:${PIPELINE_APP_PORT:-7860}/api/ice-servers
```

## Verify / Stop

```bash
docker compose ps
docker compose logs --tail 200 <service-name>
docker compose down
```

# Frontend/Backend Agent Deploy Reference

Use this reference from the `deploy` skill when deploying the cascaded Frontend/Backend Agent airline assistant.

## Profiles

Pin Docker Compose to one Frontend/Backend Agent recipe. The cloud recipe uses NVIDIA cloud ASR, a frontend LLM, a backend agent LLM, and TTS, plus the local booking-server sidecar. The workstation recipe runs local NIM ASR, TTS, and a shared frontend/backend LLM, plus the local booking-server sidecar.

```bash
docker compose --profile frontend-backend-agent up -d
docker compose --profile frontend-backend-agent/workstation up -d
```

| Recipe profile | App service | Sidecars |
| --- | --- | --- |
| `frontend-backend-agent` | `frontend-backend-agent` | `booking-server` |
| `frontend-backend-agent/workstation` | `frontend-backend-agent` | `booking-server`, `nvidia-llm`, `nemotron-asr-streaming-english`, `tts-service` |

Tear down with the same recipe used at `up` time.

```bash
docker compose --profile <recipe> down
```

## Verify

- App logs: `docker compose logs --tail 200 frontend-backend-agent`.
- Booking server logs: `docker compose logs --tail 200 booking-server`.
- Workstation local service logs: `docker compose logs --tail 200 nvidia-llm nemotron-asr-streaming-english tts-service`.

## Limits

Frontend/Backend Agent currently supports cloud-only and workstation recipes. Do not use DGX Spark or Jetson Thor profile names for this example unless matching compose recipes are added first.

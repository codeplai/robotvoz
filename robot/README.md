# Agente de voz Nemotron en español — DGX Spark

Stack 100% local en el DGX Spark (GB10, 119 GB unificados). Sin llamadas a la nube
durante la conversación.

| Componente | Modelo | Contenedor |
|---|---|---|
| ASR | Nemotron ASR Streaming Multilingual | `nemotron-asr-streaming-multilingual-dgx-spark` |
| LLM | Nemotron 3 Nano 30B A3B NVFP4 (vLLM) | `nvidia-llm-vllm` |
| TTS | Magpie TTS Multilingual, voz `es-US` | `tts-service` |
| App | Pipecat + FastAPI | `multilingual-assistant-dgx-spark` |

Idioma de sesión fijado a `es-US` en `examples_registry.yaml`. El ASR, la voz del
TTS y el LLM quedan clavados en español durante toda la conexión.

## Operación

```bash
bash 01-login-ngc.sh    # login en nvcr.io con la clave del .env
bash 02-arrancar.sh     # levanta los 4 servicios
bash 03-estado.sh       # estado, GPU, salud y logs
bash 04-parar.sh        # apaga todo
```

Interfaz web de prueba: `https://<ip-del-spark>:7860`
El certificado es autofirmado, el navegador avisará.

## API para el robot

Dos endpoints. La sesión se abre por HTTP y el audio va por WebSocket.

### 1. Abrir sesión

```
POST /api/session-config
Content-Type: application/json

{
  "pipeline_mode": "multilingual-assistant",
  "llm_id": "nemotron-nano",
  "asr_language_code": "es-US",
  "prompt_key": "multilingual_voice_assistant"
}
```

Respuesta: `{"session_id": "..."}`

### 2. Streaming de audio

```
WebSocket  wss://<ip>:7860/api/ws?session_id=<id>
```

Audio PCM de 16 bits, 16 kHz, mono, en los dos sentidos. Cada mensaje es un frame
protobuf de Pipecat (`Frame.audio`). El servidor devuelve frames de audio con la
respuesta hablada, y frames de texto con la transcripción.

Alternativa WebRTC: `POST /api/start` devuelve una URL para `/api/offer`. Mejor
para latencia sobre red, pero más complejo de implementar en el robot.

### Otros endpoints útiles

| Ruta | Uso |
|---|---|
| `GET /health` | Salud de la app |
| `GET /api/services` | Servicios ASR/LLM/TTS activos |
| `GET /api/tts-config` | Voces e idiomas disponibles |
| `GET /api/deployment` | Ejemplo y plataforma activos |

## Interfaz web de prueba

```bash
bash 05-web.sh          # sirve en http://localhost:8080
```

Muestra el estado de los cuatro servicios, medidor de microfono, transcripcion
de la conversacion y registro de eventos. Llama exactamente a los mismos dos
endpoints que usa el robot.

El microfono solo funciona en `http://localhost:8080` desde el propio Spark.
Desde otra maquina de la red el navegador lo bloquea por no ser contexto seguro.

El blueprint trae ademas su propia interfaz completa en el puerto 7860, con
selector de modelos y de voces.

## Cliente de ejemplo

`robot_voz.py` implementa las dos llamadas. El entorno ya esta preparado en
`venv/` con `websockets`, `protobuf` y `requests`.

```bash
./venv/bin/python robot_voz.py --wav pregunta.wav --out respuesta.wav
```

El wav de entrada debe ser PCM 16 bits, 16 kHz, mono.

## Sobre el codec protobuf

`frames.proto` y `frames_pb2.py` vienen de pipecat-ai 1.9.0, la misma version
que corre el servidor. El cliente web lleva el codec reimplementado a mano en
`web/protocolo.js` para no depender de librerias externas; se verifico byte a
byte contra el protobuf oficial en cinco tamanos de audio distintos.

## Cambiar el prompt del robot

El comportamiento del agente vive en
`../nemotron-voice-agent/src/examples/multilingual/prompts.yaml`.
Edita `multilingual_voice_assistant` y reinicia con `04-parar.sh` y `02-arrancar.sh`.

## Cambiar la voz

Las voces disponibles se descubren en caliente. Consulta `GET /api/tts-config`
y fija `tts_voice_id` en el cuerpo de `/api/session-config`.

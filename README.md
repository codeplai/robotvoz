# Agente de voz en español sobre DGX Spark

Robot conversacional con reconocimiento de voz, modelo de lenguaje y síntesis de
voz. Hay **dos versiones** con el mismo código, skills, seguridad e interfaz:

| Versión | Dónde corren los modelos | Arranque |
|---|---|---|
| Local | en el DGX Spark, sin llamadas a la nube durante la conversación | `bash 02-arrancar.sh` |
| Nube | APIs de NVIDIA (build.nvidia.com); el Spark solo corre la app | `bash 06-nube-arrancar.sh` |
| Azure Brasil Sur | **propuesta, sin implementar** (sección 14) | — |

Las dos usan el puerto 7860, así que solo puede estar una encendida. La versión
nube se documenta en la sección 13; la propuesta con Azure, en la 14.

Base: [blueprint Nemotron Voice Agent de NVIDIA](https://github.com/NVIDIA-AI-Blueprints/nemotron-voice-agent).

---

## 1. Hardware y entorno

| Elemento | Valor |
|---|---|
| Equipo | DGX Spark, GPU GB10 Blackwell |
| Memoria | 119 GB unificada CPU/GPU |
| CPU | 20 núcleos ARM Cortex-X925 / A725 |
| Sistema | Ubuntu 24.04, kernel 6.17 NVIDIA |
| Driver | 580.173.02, CUDA 13.0 |
| Docker | 29.2.1 con NVIDIA Container Toolkit 1.19.1 |

---

## 2. Arquitectura actual

```
  micrófono
      │  audio PCM 16 kHz mono
      ▼
  ┌─────────────────────────┐
  │ Nemotron ASR Streaming  │  reconocimiento multilingüe
  │ Multilingual (NIM)      │  contenedor: ...-asr-streaming-multilingual
  └───────────┬─────────────┘
              │  texto transcrito
              ▼
  ┌─────────────────────────┐
  │ Qwen3.5 4B (solo texto) │  modelo de lenguaje
  │ vLLM                    │  contenedor: nvidia-llm-vllm
  └───────────┬─────────────┘
              │  texto de respuesta
              ▼
  ┌─────────────────────────┐
  │ Magpie TTS Multilingual │  síntesis de voz, es-US
  │ (NIM)                   │  contenedor: ...-tts-service
  └───────────┬─────────────┘
              │  audio PCM 16 kHz mono
              ▼
           altavoz

  Orquestación: Pipecat + FastAPI (contenedor ...-multilingual-assistant-dgx-spark)
  Puerto 7860. Idioma de sesión fijado a es-US.
```

### Modelos en uso

| Función | Modelo | Tamaño |
|---|---|---|
| Reconocimiento | Nemotron ASR Streaming Multilingual | imagen 16.5 GB |
| Lenguaje | Qwen/Qwen3.5-4B | 9.3 GB |
| Síntesis | Magpie TTS Multilingual | imagen 24.3 GB |

### Voz en español

Voz por defecto: **`Magpie-Multilingual.ES-US.Diego.Happy`**, fijada en el
catálogo (sección dgxspark). La pipeline multilingüe la reasigna según el idioma
de sesión con `resolve_voice_for_language()`, así que es la preferencia inicial,
no una imposición.

12 voces en `es-US`: **Isabela** (base, Calm, Happy, Angry, Neutral, Sad) y
**Diego** (base, Calm, Happy, Angry, Neutral, PleasantSurprised).

### Velocidad del habla

Magpie **no tiene parámetro de velocidad**. Probado:

| Intento | Resultado |
|---|---|
| SSML `<prosody rate="130%">` | apenas acorta la frase, un 8% |
| `custom_configuration` con `speed`, `pace`, `rate`, `speaking_rate`, `length_scale`, `duration_scale` | error 500 con cualquier clave desconocida |

Lo que sí acelera es **evitar las variantes `.Calm`**. Misma frase:

| Variante | Duración |
|---|---|
| Diego.Neutral | 4.93 s |
| Diego | 5.34 s |
| Diego.Happy | 5.53 s |
| Diego.Calm | 7.20 s |

Entre Diego, Neutral y Happy la diferencia es pequeña y cambia según la frase.
Calm es consistentemente la más lenta. Por eso la voz pasó de `Isabela.Calm` a `Diego.Happy`, elegida tras comparar muestras.

Al probar con `curl`, usa `--form-string` para texto SSML. Con `-F`, un valor
que empieza por `<` se interpreta como "leer el contenido de un fichero".

### Latencia de síntesis, medida

| Frase | Audio generado | Tiempo de síntesis |
|---|---|---|
| "Sí." | 0.42 s | 0.068 s |
| "Claro, ahora mismo lo hago." | 2.65 s | 0.276 s |
| "De acuerdo, voy a moverme hacia la derecha y luego me detengo." | 5.43 s | 0.509 s |

Unas 10 veces más rápido que tiempo real. Holgado para un robot conversacional.

---

## 3. Estructura de ficheros

### Repositorios git

Hay **dos** repositorios, porque el blueprint de NVIDIA ya traía el suyo y
conservarlo permite seguir sus actualizaciones:

| Carpeta | Repositorio | Rama | Contenido |
|---|---|---|---|
| `/home/lenovo/nvidia-voice` | propio | `main` | este README, `robot/` (scripts, cliente, web) |
| `nemotron-voice-agent/` | clon de `NVIDIA-AI-Blueprints/nemotron-voice-agent` | **`robot-g1`** | todo el código del agente con nuestros cambios; `main` queda igual que NVIDIA |

No se versionan `.env` (claves), `nltk_data/`, `robot/venv/`, cachés ni las
copias `*.bak` / `*.antes-*` (en el blueprint se excluyen en
`.git/info/exclude`, sin tocar su `.gitignore`).

Ver los cambios respecto a NVIDIA: `cd nemotron-voice-agent && git diff main robot-g1 --stat`.
Traer una versión nueva del blueprint: `git fetch origin && git merge origin/main` estando en `robot-g1`.

```
/home/lenovo/nvidia-voice/
├── README.md                       este documento
├── nltk_data/                      datos del tokenizador NLTK (ver 5.3)
├── nemotron-voice-agent/           repositorio del blueprint
│   ├── .env                        credenciales y configuración
│   ├── docker-compose.override.yml NUESTROS ajustes (ver 4)
│   ├── buildkitd.toml              DNS para la construcción (ver 5.1)
│   ├── examples_registry.yaml      español por defecto
│   └── src/examples/multilingual/
│       └── services.local.yaml     catálogo de modelos
└── robot/
    ├── 01-login-ngc.sh             login en el registro de NVIDIA
    ├── 02-arrancar.sh              levanta los 4 servicios
    ├── 03-estado.sh                estado, GPU, salud y logs
    ├── 04-parar.sh                 apaga todo
    ├── 05-web.sh                   sirve la interfaz de prueba
    ├── robot_voz.py                cliente de ejemplo para el robot
    ├── frames.proto, frames_pb2.py esquema protobuf de Pipecat 1.9.0
    ├── venv/                       entorno del cliente
    └── web/                        interfaz de prueba
        ├── index.html
        ├── protocolo.js            códec protobuf sin dependencias
        └── captura.js              captura de micrófono
```

---

## 4. Cambios respecto al blueprint original

Todos los cambios propios viven en `docker-compose.override.yml`, que Docker
Compose fusiona automáticamente. El repositorio original queda casi intacto.

### 4.1 Español por defecto

`examples_registry.yaml`: el idioma de sesión pasó de `de-DE` a `es-US`.
Copia de respaldo en `examples_registry.yaml.bak`.

### 4.2 Modelo de lenguaje

Historial de cambios en `src/examples/multilingual/services.local.yaml`
(copia del original en `services.local.yaml.bak`):

| Modelo | Tamaño | Notas |
|---|---|---|
| Nemotron 3 Nano 30B A3B NVFP4 | 19.4 GB | el del blueprint; demasiada memoria |
| Nemotron Nano 9B v2 NVFP4 | 7.9 GB | paso intermedio |
| **Qwen3.5-4B** | **9.3 GB** | **actual**: 201 idiomas, 262K de contexto |

**Cómo desactivar el razonamiento cambia con cada modelo.** Es el detalle que
más fácil se pasa por alto, y en un robot es crítico: si el modelo razona en
voz alta, añade segundos de latencia y el sintetizador pronuncia su propio
razonamiento.

| Modelo | Mecanismo |
|---|---|
| Nemotron 3 Nano (v3) | `chat_template_kwargs.enable_thinking = false` |
| Nemotron Nano v2 | `/no_think` en el rol de sistema; **ignora** el parámetro |
| Qwen3.5 | `chat_template_kwargs.enable_thinking = false` |

Con Qwen3.5 el `system_prompt` del catálogo vuelve a ir vacío.

**Qwen3.5-4B es multimodal.** Su arquitectura es
`Qwen3_5ForConditionalGeneration` y carga un procesador de imágenes. La bandera
`--language-model-only` salta el codificador visual y su perfilado, liberando
esa memoria para la caché. Los pesos de visión se descargan igualmente porque
el repositorio es uno solo, pero no se cargan en memoria.

### 4.3 Banderas de vLLM

Respecto al comando del blueprint:

| Bandera | Cambio | Motivo |
|---|---|---|
| `--moe-backend` | eliminada | Qwen3.5-4B es denso, no MoE |
| `--mamba_ssm_cache_dtype` | eliminada | no es Mamba |
| `--trust-remote-code` | eliminada | Qwen no lo necesita |
| `--reasoning-parser` | nemotron_v3 → qwen3 | separa el bloque `<think>` |
| `--enable-auto-tool-choice` | eliminada | la multilingüe no usa herramientas |
| `--language-model-only` | añadida | salta el codificador de visión |
| `--max-num-seqs 64` | añadida | limita sesiones concurrentes |
| `--max-model-len` | 262144 → 4096 | un robot no necesita más |
| `--gpu-memory-utilization` | 0.35 → 0.28 | ver 5.4 |

### 4.4 TLS desactivado

`PIPELINE_TLS=false` en `.env`. El certificado autofirmado solo añadía fricción
en pruebas locales.

---

## 5. Problemas encontrados y sus soluciones

Esta sección es la más útil para quien herede el sistema. Todos estos problemas
costaron tiempo y ninguno era evidente.

> **Nota (12/09/2026): se cambió de red.** Los problemas 5.1, 5.2 y parte del
> 5.5 eran **específicos de la red de ESAN** y ya no se dan. Los parches se
> retiraron porque apuntaban a servidores que en la red nueva no existen, y
> mantenerlos habría dejado los contenedores sin resolución de nombres.
> Se conservan documentados porque el patrón se repite en redes corporativas
> y universitarias, y porque saber reconocer el síntoma ahorra horas.
>
> Comparativa medida en ambas redes:
>
> | Comprobación | Red ESAN | Red actual |
> |---|---|---|
> | Servidores DNS que responden | 3 de 8 | 3 de 3 |
> | Resolver un nombre inexistente | 12.008 ms | 2 ms |
> | `huggingface.co` desde contenedor | 4.100 ms | 3 ms |
> | CDN de ficheros de GitHub (185.199.x) | bloqueado | conecta |
>
> Lo que **sí se conserva** por ser útil en cualquier red: los datos de NLTK
> locales (evitan una descarga en el arranque) y los `extra_hosts` de la
> sección 5.5 (protegen del fallo de bloqueo del blueprint siempre).

### 5.1 DNS: solo 3 de 8 servidores responden (red ESAN)

**Síntoma:** `apt-get update` dentro de la construcción ignoraba todos los
índices con tiempos de espera de 56 a 84 segundos por cada uno. Fallaba unas
veces sí y otras no.

**Causa:** la red entrega 8 servidores DNS y solo responden tres:
`10.24.52.183`, `172.25.0.46` y `172.25.0.73`. La librería del sistema dentro
de un contenedor lee **únicamente los 3 primeros**, y Docker los ordena distinto
en cada arranque. Cuando tocaban tres muertos, nada resolvía. El host no lo
sufre porque usa `systemd-resolved`, que prueba los ocho con reintentos.

**Solución aplicada entonces (ya retirada):** fijar los 3 servidores buenos en
`buildkitd.toml` para la construcción y en `docker-compose.override.yml` para
la ejecución. Ambos ficheros conservan la explicación comentada y cómo
reintroducirlo.

Lo que **sí se mantiene** es crear el constructor con red del host, porque es
inofensivo en una red sana y evita una clase entera de problemas:

```bash
docker buildx create --name spark --driver docker-container \
  --driver-opt network=host --config buildkitd.toml --use
```

**Cómo diagnosticarlo si reaparece.** El síntoma es que las descargas y los
arranques se cuelgan sin error claro:

```bash
resolvectl status | grep -i "dns servers"      # cuáles reparte la red
for ns in <cada uno>; do nslookup huggingface.co $ns; done   # cuáles responden
```

Si solo responden algunos, añade `dns: [<los buenos>]` a los cuatro servicios.
La clave: **glibc dentro del contenedor usa solo los 3 primeros**, y Docker los
ordena distinto en cada arranque.

### 5.2 GitHub: el CDN de ficheros está bloqueado (red ESAN)

| Destino | Resultado |
|---|---|
| github.com por git, 140.82.x | Conecta |
| codeload.github.com | Conecta |
| CDN de ficheros, 185.199.x | **Bloqueado** |
| api.github.com | **Bloqueado** |

Por eso el clonado inicial funcionó mientras toda descarga de ficheros sueltos
fallaba. No era intermitencia: son rutas distintas y solo una está cortada.

### 5.3 NLTK colgaba el arranque de la aplicación

**Síntoma:** la aplicación nunca llegaba a escuchar en el 7860. Sin errores,
sin consumo de CPU, simplemente colgada.

**Causa:** Pipecat usa el tokenizador de frases de NLTK, cuyos datos
(`punkt_tab`) se descargan de `raw.githubusercontent.com`, bloqueado (ver 5.2).
La conexión quedaba en SYN_SENT para siempre. Se localizó volcando la traza del
proceso con `faulthandler`.

Apareció al cambiar de modelo porque Docker **recrea** los contenedores cuando
cambia la configuración, y se perdió la copia que la aplicación había
descargado en su primer arranque.

**Solución (se mantiene aunque GitHub ya no esté bloqueado, porque evita una
descarga en cada arranque):** traer los datos y montarlos:
```bash
git clone --depth 1 --branch gh-pages --filter=blob:none --sparse \
  https://github.com/nltk/nltk_data.git
cd nltk_data && git sparse-checkout set packages/tokenizers
unzip packages/tokenizers/punkt_tab.zip -d /home/lenovo/nvidia-voice/nltk_data/tokenizers/
```
El override monta `../nltk_data` en `/opt/nltk_data` y define `NLTK_DATA`.
Sobrevive a que Docker recree el contenedor.

### 5.4 Memoria de vLLM: dos restricciones opuestas

vLLM exige **a la vez**:

1. El presupuesto (`util × 119.6 GB`) debe **caber en la memoria libre** al
   arrancar. Con ASR y TTS cargados quedan unos 83 GB, así que el techo está
   sobre 0.69. Superarlo da:
   `ValueError: Free memory on device cuda:0 ... is less than desired`
2. El presupuesto **menos lo que ya ocupan otros procesos** (~36 GB) menos los
   pesos del modelo debe dejar sitio para la caché de atención. Quedarse corto da:
   `ValueError: No available memory for the cache blocks`

El 0.35 del blueprint falla por la segunda: asume que vLLM arranca solo.

**Consecuencia importante:** apilar varias instancias de vLLM en el Spark no
funciona. Una vez arrancada la primera, queda tan poca memoria libre que la
segunda no cumple la restricción 1. Para varios modelos a la vez conviene
llama.cpp, que no reserva un presupuesto global, o el modo de suspensión de vLLM.

**Fórmula medida en esta máquina.** No estimada, deducida de tres arranques:

| `--gpu-memory-utilization` | Presupuesto | Memoria del sistema | Caché |
|---|---|---|---|
| 0.50 | 59.8 GB | 98 GB usados, 1 libre | 1.757.769 tokens |
| 0.42 | 50.2 GB | 88 GB usados | 1.452.909 tokens |
| **0.28** | **33.5 GB** | **71 GB usados, 47 libres** | **846.116 tokens** |

De ahí: `memoria_usada ≈ 38 GB fijos + (util × 119.6 GB)`. Los 38 GB son el
ASR (10.4), el TTS (5.9), la aplicación y el sistema.

Un robot con 4096 de contexto y pocas sesiones necesita del orden de 20.000
tokens de caché, así que incluso 0.28 va sobrado.

**Trampa al diagnosticar:** `docker stats` **no** contabiliza la memoria de GPU
en esta plataforma unificada. Declaraba 20 GB mientras el sistema usaba 88.
Solo `free` dice la verdad aquí.

### 5.5 La aplicación respondía en lotes cada 36 segundos

**Síntoma:** `/health` tardaba 47 segundos. El contenedor se marcaba caído
porque la sonda espera 10. En los registros, las peticiones se atendían en
lotes a intervalos de 36.03 segundos exactos.

**Causa:** `/api/services` comprueba si cada servicio del catálogo está vivo con
`is_endpoint_reachable()`, que hace un `socket.create_connection()` **síncrono
dentro del bucle de eventos asíncrono**. El catálogo lista alternativas que este
perfil no despliega: `chatterbox-tts-service`, `magpie-zeroshot-tts-service` y
`parakeet-rnnt-asr`. En esta red, resolver un nombre inexistente tarda 12.008
segundos porque el resolutor de Docker reenvía a los DNS externos y estos nunca
contestan.

3 × 12.008 = **36.02 s**. El intervalo medido era 36.03 s.

El tiempo de espera de conexión del blueprint es de 2 segundos, pero nunca
llegaba a aplicarse: el bloqueo ocurría antes, en la resolución del nombre.

**Solución:** apuntar esos nombres a `127.0.0.1` con `extra_hosts`. El DNS sale
del fichero de hosts al instante y la conexión se rechaza de inmediato, así que
se marcan como no disponibles, que es la respuesta correcta.

**Resultado:** de 47 segundos a 2 milisegundos.

Es un fallo del blueprint, no de la instalación. Una llamada de red bloqueante
dentro de un manejador asíncrono es incorrecta en cualquier red; esta solo lo
hizo visible. En una red normal el DNS falla rápido y pasa desapercibido.

### 5.6 Saturación de red durante las descargas

Los 61 GB de imágenes de NVIDIA saturaban el enlace y hacían fallar `apt`
dentro de la construcción por tiempo de espera. No era la causa raíz, pero
agravaba el problema de DNS. Conviene no construir mientras se descargan
imágenes grandes.

---

## 6. Operación

```bash
cd /home/lenovo/nvidia-voice/robot

bash 01-login-ngc.sh    # login en nvcr.io con la clave del .env
bash 02-arrancar.sh     # levanta los 4 servicios
bash 03-estado.sh       # estado, GPU, salud y logs
bash 05-web.sh          # interfaz de prueba en el 8080
bash 04-parar.sh        # apaga todo

# Version nube (seccion 13): no usa la GPU ni descarga modelos
bash 06-nube-arrancar.sh   # para la local si esta encendida y arranca la nube
bash 07-nube-estado.sh     # contenedor, salud y servicios elegidos
bash 08-nube-parar.sh      # apaga la version nube
```

Primera vez: descarga unos 61 GB de imágenes más los pesos del modelo. Cuenta
entre 30 y 60 minutos según la red.

### Interfaces

| Dirección | Qué es |
|---|---|
| `http://localhost:8080` | interfaz de prueba propia, en español |
| `http://172.16.11.10:7860` | interfaz completa del blueprint |

El micrófono solo funciona en `localhost`. Desde otra máquina de la red el
navegador lo bloquea por no ser contexto seguro.

---

## 7. API para el robot

Dos llamadas. La sesión se abre por HTTP y el audio va por WebSocket.

**1. Abrir sesión**

```
POST /api/session-config
Content-Type: application/json

{
  "pipeline_mode": "multilingual-assistant",
  "asr_language_code": "es-US",
  "prompt_key": "skill_recepcion"
}
```

Respuesta: `{"session_id": "..."}`

**No envíes `llm_id`** salvo que sepas que existe en el catálogo actual. El
servidor usa su modelo por defecto. Con un `llm_id` que ya no existe (pasó al
renombrar `nemotron-nano` a `qwen35-4b`), la sesión se abre, pero al conectar
el WebSocket el servidor registra `Unknown built-in LLM selection` y **cierra
con código 1000 ("OK")**: el cliente no ve ningún error útil.

**2. Enviar `client-ready` nada más conectar (OBLIGATORIO)**

Primer mensaje por el WebSocket, como `Frame.message` del protobuf:

```json
{"label": "rtvi-ai", "type": "client-ready", "id": "<uuid>",
 "data": {"version": "2.0.0", "about": {"library": "mi-robot"}}}
```

**Sin él la sesión se queda muda, sin ningún error.** El servidor solo lanza el
saludo inicial al recibirlo, y hasta que el bot completa ese primer turno una
estrategia de silencio ignora toda la voz del usuario.

Lo engañoso: el reconocedor **sí** transcribe al usuario, pero esa transcripción
nunca llega al modelo. Prueba de punta a punta con el mismo audio:

| | Sin `client-ready` | Con `client-ready` |
|---|---|---|
| Saludo inicial | no | sí |
| Voz transcrita | sí | sí |
| Respuesta del robot | **nada** | sí, con voz |
| Audio recibido | 0 s | 14.3 s |

Además, **el audio enviado durante el saludo se descarta**. Un cliente que manda
una pregunta grabada debe esperar al primer `bot-stopped-speaking`. Con
micrófono en directo da igual: el usuario habla después de oír el saludo.

Latencia medida desde que el usuario deja de hablar hasta que el robot empieza
a responder: **1.0 s**.

**3. Streaming de audio**

```
WebSocket  ws://<ip>:7860/api/ws?session_id=<id>
```

Audio PCM de 16 bits, 16 kHz, mono, en ambos sentidos. Cada mensaje es un frame
protobuf de Pipecat.

### Mensajes que devuelve el servidor

Los textos NO viajan como frames de texto del protobuf, sino como mensajes RTVI
dentro de `MessageFrame`:

| Tipo | Contenido | Uso |
|---|---|---|
| `user-transcription` | `{text, final}` | lo que dice el usuario; parcial hasta `final: true` |
| `bot-tts-text` | `{text}` | lo que el bot pronuncia, sincronizado con el audio |
| `bot-llm-text` | `{text}` | texto del modelo; respaldo si no hay TTS text |
| `user-started-speaking` | — | el usuario empezó a hablar |
| `bot-started-speaking` | — | el bot empezó a hablar |
| `bot-stopped-speaking` | — | fin del turno del bot |

Además, `InterruptionFrame` (campo 5 del protobuf) indica que el usuario cortó
al bot: hay que descartar el audio pendiente de reproducir.

### Otros endpoints

| Ruta | Uso |
|---|---|
| `GET /health` | salud de la aplicación |
| `GET /api/services` | servicios ASR/LLM/TTS activos |
| `GET /api/tts-config` | voces e idiomas; **requiere parámetros del servicio** |
| `GET /api/deployment` | ejemplo y plataforma activos |

`/api/tts-config` sin parámetros devuelve listas vacías. Hay que pasarle
`server`, `model`, `voice_id`, `pipeline_mode` y `llm_id`.

### Cliente de ejemplo

```bash
cd /home/lenovo/nvidia-voice/robot
./venv/bin/python robot_voz.py --wav pregunta.wav --out respuesta.wav
```

El wav de entrada debe ser PCM 16 bits, 16 kHz, mono.

---

## 8. Interfaz web (móvil y escritorio)

### Dirección

```
https://<ip-del-spark>:7860/movil/index.html
```

El navegador avisará del certificado autofirmado: hay que aceptarlo una vez.

### Por qué esa dirección y no un servidor aparte

**Los navegadores bloquean el micrófono fuera de un contexto seguro.** En el
móvil, `http://<ip>:8080` no sirve: no hay micrófono. Hacen falta `https` o
`localhost`.

La solución es montar la página **dentro del directorio estático de la propia
app** (`robot/web` → `/app/client/dist/movil`), que la sirve en su mismo
origen. Eso resuelve tres cosas de una vez:

| Problema | Cómo lo resuelve el mismo origen |
|---|---|
| Dos certificados que aceptar | uno solo, el de la app |
| Origen cruzado (CORS) | no hay, es el mismo origen |
| Contenido mixto | la página va por `https` y el socket por `wss` |

Por eso `PIPELINE_TLS=true` volvió a activarse. Sin TLS la página carga pero el
micrófono no funciona en el móvil.

El servidor de `05-web.sh` (puerto 8080) sigue existiendo **solo para
desarrollo en el propio Spark**, donde `localhost` ya es contexto seguro.

### Si Chrome en Android niega el micrófono

Chrome puede negar `getUserMedia` en un origen cuyo **certificado está en
aviso**, aunque hayas pulsado "Continuar". El síntoma engaña: sale
`NotAllowedError` pese a haber concedido el permiso.

Salida, sin certificados de por medio: declarar el origen HTTP como seguro.

1. En Chrome, abrir `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
2. Añadir `http://<ip-del-spark>:8080`
3. Poner el flag en **Enabled** y reiniciar Chrome
4. Usar `http://<ip-del-spark>:8080/index.html` (servidor de `05-web.sh`)

La página detecta este fallo y muestra la instrucción en pantalla, así que no
hay que recordarla.

### La conversación se cortaba sola en Android

**Diagnóstico por los registros** (sesión real desde el móvil):

```
22:12:00  conexión, cinco turnos correctos con el modelo
22:13:19  Client disconnected            <- sin ningún error en el servidor
22:13:22  GET /movil/index.html          <- el teléfono RECARGÓ la página
22:13:26  sesión nueva, sin memoria de la anterior
```

Una recarga que corta la llamada apunta al **gesto de deslizar hacia abajo para
recargar** de Chrome, que se dispara al subir el dedo por la conversación para
releer. Estaba desactivado solo en `body`, y **Chrome para Android solo lo
respeta en el elemento raíz** (`html`). Corregido.

Protecciones añadidas:

| Riesgo | Protección |
|---|---|
| Deslizar para recargar | `overscroll-behavior-y: none` en `html` |
| Recargar o cerrar durante la llamada | Chrome pide confirmación (`beforeunload`) |
| Gesto de atrás al rozar el borde | ya no cuelga: se retiene y avisa |

Se probó también guardar el texto de la conversación en `sessionStorage` para
recuperarlo tras una recarga accidental. **Se retiró**: al recargar aparecía lo
ya hablado y parecía que las sesiones quedaban grabadas. Ahora **al recargar se
empieza siempre en blanco**, y la página borra los restos que dejó esa versión.

### El texto del robot salía duplicado

**Síntoma:** en la burbuja del saludo aparecía
`¡Buenos días! Soy el robot... ¿En qué puedo ayudarle hoy?¡Buenos días!Soy el robot...`

**No era eco del micrófono.** El eco se vería como una burbuja *del usuario*
(el micro transcribiendo la voz del robot), no duplicado dentro de la del robot.

**Causa:** el servidor manda el texto del bot por varios canales a la vez.
Capturado en el saludo:

| Canal | Primera llegada | Formato |
|---|---|---|
| `bot-llm-text` | 0.36 s | tokens del modelo, **con** espacios |
| `bot-transcription` | 0.60 s | frases del modelo |
| `bot-output` | 0.81 s | cada frase **dos veces** |
| `bot-tts-text` | 1.81 s | frases del sintetizador, **sin** espacio entre ellas |

La página usaba `bot-llm-text` como respaldo "si no llega `bot-tts-text`".
Como el del modelo llega antes, en el primer turno se pintaban ambos. La copia
con espacios era la del modelo y la pegada, la del sintetizador.

**Solución:** usar solo `bot-tts-text`, que va sincronizado con lo que suena, e
insertar un espacio entre frases salvo que el trozo empiece por un signo que va
pegado (`, . ; : ! ? )`). `bot-output` tampoco se usa: repite cada frase.

### Botón Limpiar

Arriba a la derecha. **Cuelga y deja la pantalla en blanco.** Cuelga a propósito:
el robot recuerda lo hablado mientras dura la sesión en el servidor, así que
borrar solo la pantalla dejaría al robot con memoria de algo que ya no se ve.

Si se reconecta con conversación aún en pantalla (colgar y volver a conectar
sin limpiar), aparece el separador *"Nueva sesión · el robot no recuerda lo
anterior"*.

### Botón Pausar

Aparece bajo **Colgar** mientras hay llamada. Sirve para ausentarse sin perder
la conversación.

**En pausa:** no se envía el micrófono, no suena el robot (si estaba hablando,
se calla), y el estado muestra cuánto tiempo lleva en pausa. **La sesión y la
memoria del robot siguen vivas en el servidor.** **Reanudar** continúa donde se
quedó.

Dos comprobaciones del servidor que condicionaron el diseño:

| Pieza | Comportamiento | Consecuencia |
|---|---|---|
| Reconocedor (`NvidiaSTTService`) | sin audio, envía silencio por su cuenta cada 5 s para mantener su canal gRPC | en pausa basta con dejar de enviar el micro |
| Pipeline (`PipelineWorker`) | cancela la sesión si pasan `PIPELINE_IDLE_TIMEOUT_SECS` sin `BotSpeakingFrame` ni `UserSpeakingFrame`; **el silencio no cuenta** | con el valor por defecto (600 s), **una pausa de más de 10 min borraba la conversación** |

Por eso `.env` fija `PIPELINE_IDLE_TIMEOUT_SECS=3600`: **una pausa puede durar
hasta 60 minutos**. Si el servidor cierra igualmente, la página lo avisa con el
motivo. Subirlo más es posible; el coste es que una pestaña abandonada mantiene
su sesión viva ese tiempo.

Límites a tener en cuenta:

- Si se pausa **mientras el robot habla**, el servidor termina de generar esa
  respuesta aunque no suene, y queda en su memoria como dicha.
- En el **móvil**, la pantalla se mantiene encendida también en pausa. Si se
  apaga, Chrome puede congelar la pestaña y cortar la conexión.

### Una sola conexión y una sola pestaña

Ante un aviso de "dos audios en uno" se revisó el servidor: **nunca hubo dos
sesiones solapadas** (cinco pipelines, cinco WebSocket, en fila, ninguna por
WebRTC) y cada respuesta se generó una sola vez. El servidor envió un único
audio. Aun así se blindaron las dos formas en que el navegador puede producir
voces mezcladas:

| Riesgo | Protección |
|---|---|
| Dos toques seguidos en Conectar crean dos sockets | se ignoran toques mientras conecta, y conectar cierra antes cualquier conexión previa |
| Un socket viejo sigue sonando o cierra el nuevo | cada intento lleva un número; los manejadores de intentos anteriores quedan inertes |
| Dos pestañas con la página conectadas | `BroadcastChannel`: si otra pestaña conecta, esta cuelga y avisa |

**Nota para diagnosticar registros tras un cuelgue del Spark:** `docker logs`
con ventanas grandes (`--tail 1500`, `--since 45m`) se detiene en el punto del
cuelgue y **no devuelve nada posterior**, probablemente por una línea cortada en
el fichero de registro. Ventanas pequeñas (`--tail 400`, `--tail 700`) sí leen
lo reciente. Si un registro "no muestra" una sesión que sabes que existió,
prueba con ventanas más pequeñas.

### Instalable como app

Lleva `manifest.json` e icono, así que Chrome ofrece **Añadir a pantalla de
inicio**. Se abre sin barra de direcciones, en vertical, como una app.

### Diseño

Pensado para Android primero, y se adapta a escritorio:

- **Botón grande abajo**, al alcance del pulgar, con área táctil de 60 px.
- **Conversación a pantalla completa**, con burbujas que aparecen con animación.
- Lo que dices sale en gris y cursiva mientras es provisional, y se fija al
  cerrar la frase.
- **Tres luces** arriba para reconocimiento, modelo y voz.
- **Detalles en panel aparte** (botón `⋯`), para no robar espacio a la
  conversación.
- Respeta las **zonas seguras** del móvil (muesca y barra inferior) con
  `env(safe-area-inset-*)`, y `viewport-fit=cover`.
- `maximum-scale=1` evita el zoom accidental al tocar los controles.
- **Aviso automático** si se abre sin `https`, con la dirección correcta.
- **La pantalla no se apaga** durante la conversación (Wake Lock). Sin esto,
  Android la apaga a los pocos segundos y suspende el audio a mitad de frase.
  El bloqueo se repone solo si cambias de app y vuelves.
- **El botón físico "atrás" cuelga la llamada** en lugar de salir de la página.
- Altura en `100dvh`, no `100vh`: la barra de direcciones de Chrome aparece y
  desaparece al desplazar, y con `100vh` el botón quedaba fuera de pantalla.
- `AudioContext.resume()` se llama dentro del gesto del usuario, requisito para
  que suene el audio.

### El audio se cortaba tras la primera frase

**Síntoma:** el robot decía "Buenos días" y luego silencio, aunque el servidor
generaba y enviaba todo el audio (los registros mostraban el saludo completo y
las respuestas siguientes).

**Causa:** en `reproducir()` la vista de 16 bits se creaba directamente sobre el
mensaje recibido: `new Int16Array(pcm.buffer, pcm.byteOffset, ...)`. Pipecat
incluye en cada frame un `id` y un nombre (`OutputAudioRawFrame#85`) cuya
longitud cambia frame a frame, así que el audio **puede empezar en una posición
impar** del mensaje. `Int16Array` exige desplazamiento par y lanza `RangeError`,
y ese trozo no suena.

Medido con 50 frames reales del saludo:

| | Frames que fallan |
|---|---|
| Código anterior | 15 de 50 (30%) |
| Código corregido | 0, con muestras idénticas a una lectura de referencia |

Por qué pasaba unas veces sí y otras no: la paridad depende de los contadores
de `id` y nombre, que vuelven a cero al reiniciar la app.

**Solución:** copiar el audio a un buffer propio antes de crear la vista
(`pcm.slice()`, que empieza en 0). Además, `ws.onmessage` ahora registra
cualquier error al procesar un frame en el panel de detalles, en vez de
perderlo en silencio.

El cliente Python `robot_voz.py` **no tiene este problema**: usa la librería
oficial de protobuf, que copia los bytes.

### Sobre el códec

Sin dependencias externas: el códec protobuf de Pipecat está reimplementado a
mano en `protocolo.js` y verificado byte a byte contra el oficial (Pipecat
1.9.0) en cinco tamaños de audio distintos.

La versión anterior, orientada a escritorio, quedó como
`robot/web/index.html.escritorio`.

---

## 9. Skills del robot

Un skill es un **prompt más la lista de herramientas que puede usar**. El robot
cambia de skill **por voz**, sin reconectar.

### Los tres skills

| Skill | Puede | No puede |
|---|---|---|
| `skill_recepcion` (inicial) | saludar, orientar, leer sensores | moverse |
| `skill_inspeccion` | mover, girar, detener, sensores, reportar incidencias | — |
| `skill_mantenimiento` | diagnóstico, leer sensores, detener | moverse (seguridad) |

### Decisión de diseño: el permiso vive en el código, no en el prompt

El esquema de herramientas se fija al construir la sesión y **no puede cambiar
a mitad de conversación**. Si solo se enviaran las del skill inicial, el robot
no podría moverse tras cambiar a inspección.

Por eso el modelo recibe la **unión** de todas las herramientas, y el permiso
real lo aplica `tool_handlers.con_permiso()` según el skill activo.

**Esto no es teórico.** En la prueba, estando en modo recepción y con un prompt
que decía explícitamente "NO te mueves en este modo", el modelo invocó `mover`
igualmente. Lo detuvo el manejador, no el prompt:

```
[RECEPCION] "avanza tres metros"
   -> invoca mover(adelante, 3)      <- el modelo lo intentó
   -> rechazado por el manejador
   -> "En este modo no puedo moverme. Para desplazarme, necesito pasar a
       modo inspección."
```

Para un robot con motores, la diferencia entre una instrucción en el prompt y
una comprobación en código es la diferencia entre una sugerencia y una garantía.

### Cómo cambia de skill

El usuario lo pide de viva voz, el modelo invoca `cambiar_skill(nombre)` y el
manejador **reescribe el mensaje de sistema del contexto en caliente**,
conservando el historial de la conversación.

### Fallo: el cambio de modo no tenía efecto

**Síntoma:** el robot confirmaba "he cambiado a modo inspección", pero al pedirle
avanzar respondía "no puedo avanzar, necesito estar en modo recepción": la
regla de recepción con los nombres cambiados.

**Diagnóstico:** los registros muestran el prompt de sistema de cada llamada al
modelo. Tras "cambiar" de modo, las seis llamadas siguientes seguían recibiendo
el prompt de **recepción**, aunque el manejador registraba "contexto reescrito:
True".

**Causa:** `PerTurnReminderProcessor` (el recordatorio de idioma de la pipeline
multilingüe) reenvía al modelo **una copia nueva del contexto en cada turno**.
Esa copia es la que llega a la herramienta en `params.context`. Reescribirla no
cambiaba el contexto real de la sesión.

**Solución:**

- `pipeline.py` entrega al estado del skill el `LLMContext` **compartido** de la
  sesión, el prompt vigente y una función que añade el bloque de idioma fijo.
- `cambiar_skill` reescribe ese contexto compartido. Localiza el prompt por su
  contenido exacto, no por posición, porque con modelos que usan una directiva
  en el rol de sistema (`/no_think`) el prompt va en el primer mensaje de usuario.
- El **bloque de idioma fijo se conserva**: antes se perdía al sustituir el
  prompt por el contenido crudo del skill.

**Verificado por voz, de punta a punta:**

| Turno | Resultado |
|---|---|
| "Por favor, entra en modo inspección" | `cambiar_skill(inspeccion)` y "Bien, estoy en modo inspección" |
| "Avanza un metro hacia adelante" | `mover(adelante, 1)` y "Avanzaré un metro hacia adelante" |
| "¿Qué modo tienes activo ahora?" | "Estoy en modo inspección" |

Llamadas al modelo 1 a 3 con prompt de recepción; 4 a 7 con el de inspección,
todas con el bloque de idioma.

**Lección:** una prueba de la herramienta llamando al modelo directamente, o del
manejador por separado, no detecta esto. Solo aparece a través de la pipeline
completa.

### Reglas añadidas a los tres modos

En una conversación real, el modelo dijo "necesito pasar a modo inspección,
déjame cambiar de modo" **sin invocar la herramienta**, y además decidió cambiar
de modo ante una pregunta de viaje. Se añadió a los tres skills:

- si el usuario lo pide, o si el propio robot dice que va a cambiar, **debe
  invocar `cambiar_skill` en esa misma respuesta**;
- las preguntas de información general se responden **en cualquier modo**;
  cambiar solo hace falta para usar una herramienta no permitida.

Es una instrucción al modelo, no una garantía: un modelo de 4B puede saltársela.

### El reconocedor se quedaba sordo tras ~30 s de micrófono

**Síntoma:** la conversación iba bien y, pasado cerca de un minuto, el robot
dejaba de responder. La interfaz mostraba "Escuchando…", pero no llegaba
ninguna transcripción. No había errores en ningún contenedor.

**Diagnóstico:**

| Sesión | Duración | Audio que recibió el servidor de voz |
|---|---|---|
| Real, desde el navegador | 452 s | 39.5 s |
| Real, desde el navegador | 88 s | 54.7 s |
| Reproducción, micro continuo | 95 s | **31.84 s** |
| Reproducción repetida | 95 s | **31.84 s** |

Con el micrófono abierto de forma continua, el NIM `nemotron-asr-streaming`
**deja de consumir audio tras unos 30 s acumulados en el mismo stream gRPC**.
No corta la conexión ni da error: simplemente no lee más. Pipecat no detecta
caída, así que no reconecta, y sigue encolando audio en una cola sin límite.
La detección de voz funciona aparte, por eso se veía "Escuchando…".

Las pruebas anteriores no lo detectaron porque duraban unos 30 s o enviaban
frases sueltas. La pausa de 60 s funcionó porque en pausa casi no se envía
audio: el tope es de **audio acumulado**, no de tiempo.

Se descartó que fuera el tamaño de los trozos del navegador: con trozos de 8 ms
y de 100 ms falla exactamente igual.

**Sospechoso:** la diarización *sortformer* del modelo, con caché de hablantes
de tamaño fijo que nunca se refresca (`spkcache_len: 160`,
`spkcache_refresh_rate: 0`). **Todos los perfiles del NIM para DGX Spark llevan
sortformer**, así que cambiar de perfil no es una salida.

**Solución:** `src/examples/multilingual/stt_reciclado.py` sustituye al
reconocedor de Pipecat y **recicla el stream gRPC antes del tope**:

| Condición | Acción |
|---|---|
| ≥ 20 s de audio, usuario callado y 1.5 s sin resultados | abre stream nuevo |
| ≥ 28 s de audio, en cualquier caso | abre stream nuevo |

Usa un iterador nuevo para el stream nuevo y cierra el viejo con su centinela,
de modo que este entrega los resultados pendientes. No usa el `_do_reconnect`
de Pipecat, que reutiliza el mismo iterador y dejaría dos streams leyendo de
la misma cola.

**Verificado** con la misma reproducción (95 s de micro continuo con ruido de
fondo y cuatro frases):

| Frase | Antes | Después |
|---|---|---|
| t = 8 s | 0.5 s | 0.5 s |
| t = 30 s | 0.2 s | 0.7 s |
| t = 52 s | **sin transcripción** | 0.6 s |
| t = 74 s | **sin transcripción** | 0.4 s |

Cuatro reciclados, todos con el usuario en silencio, sin errores. Ningún stream
volvió a acercarse al tope.

**Diagnóstico si reaparece:** las estadísticas del NIM registran el audio
recibido por stream:

```bash
docker logs --tail 300 nemotron-voice-agent-nemotron-asr-streaming-multilingual-dgx-spark-1 \
  | grep stats_builder | grep -oE 'audio_duration[^,]+'
```

Si alguno se queda clavado cerca de 31.84 s, el tope ha vuelto a alcanzarse.

### Turnos que el reconocedor devuelve vacíos

El reconocedor a veces detecta voz pero devuelve la transcripción vacía (el
blueprint lo documenta). Entonces no llega nada al modelo y el robot se queda
callado. La interfaz web detecta ese caso: si 3 s después de dejar de hablar no
hubo transcripción ni empezó a hablar el robot, muestra **"No te entendí,
repite"**.

### Skills de conversación

Además de los tres modos físicos, el robot tiene tres modos para **conversar**:

| Modo | Tema |
|---|---|
| `ia` (`skill_charla_ia`) | qué es la IA, cómo funciona, usos, límites, ética, impacto; habla en primera persona de cómo funciona él mismo sin exagerar |
| `educacion` (`skill_charla_educacion`) | aprendizaje, enseñanza, tecnología e IA en el aula, habilidades del futuro |
| `innovacion` (`skill_charla_innovacion`) | ideas, emprendimiento, transformación digital, experimentar y fracasar; ejemplos de Perú y Latinoamérica sin inventar datos |

Reglas comunes: turnos de dos o tres frases, sin listas ni emojis (se leen en
voz alta), preguntas abiertas para que la charla siga, adaptarse al
interlocutor y **no inventar cifras, estudios ni fechas**. En estos modos no
se puede andar. Cambiar de tema se hace por voz: "hablemos de educación".

Al entrar, `cambiar_skill` devuelve al modelo una `instruccion_entrada` para que
abra la conversación con una idea breve y una pregunta. El código le indica
además si cambió de postura de verdad: en una prueba, al pasar de IA a educación
ya sentado, decía "me he sentado" sin haberse movido.

### Postura: la decide el modelo, la seguridad la impone el código

**Decisión del usuario:** el código **no** fuerza la postura. Al entrar en un
modo de conversación el robot puede sentarse si lo ve oportuno (herramienta
`postura`), pero no es obligatorio, y tampoco se levanta solo al pasar a
inspección. El mecanismo `al_entrar` de `prompts.yaml` sigue disponible, sin uso.

Lo que **sí** impone el código, porque solo bloquea acciones y nunca mueve al
robot por su cuenta:

| Situación | Código |
|---|---|
| Sentado e intenta `mover` o `girar` | se deniega |
| Sentado e intenta levantarse | exige confirmación del usuario |

**Por qué hace falta.** En una prueba por voz, sentado en modo conversación y
ante "avanza un metro", el modelo cambió **por su cuenta** a inspección, se
levantó y anduvo sin preguntar, pese a que el prompt le ordenaba explicarlo
primero. Es la tercera vez en el proyecto que el modelo se salta una regla del
prompt.

**Qué cuenta como confirmación**, por orden:

1. **Orden explícita de levantarse** en la frase del usuario: "levántate",
   "puedes levantarte", "ponte de pie". "Avanza un metro" a secas **no** cuenta.
2. **Respuesta afirmativa** ("sí", "claro", "dale"...) a una pregunta del robot
   sobre levantarse o moverse.
3. Si se armó una confirmación, que **el usuario haya hablado después**,
   contado en el historial real de la conversación, dentro de 60 s.

En cualquier caso, si la respuesta es una negativa ("no", "mejor no",
"quédate sentado", "espera") **no se levanta**.

Los casos 1 y 2 existen porque el modelo suele preguntar en texto sin llamar a
ninguna herramienta. Solo con el caso 3, el "sí" del usuario llegaba antes de
armar y el robot **pedía confirmar dos veces**. Además, rearmar una
confirmación pendiente borraba que el usuario ya había respondido; ahora una
confirmación vigente no se rearma.

**Verificado por voz**, sentado en modo IA y pidiendo "avanza un metro":

| Respuesta del usuario | ¿Se levanta? | Confirmación en código |
|---|---|---|
| "Sí, confirmo, puedes levantarte" | sí | a la primera, por orden explícita |
| "Sí, claro" | sí | a la primera, por respuesta a la pregunta del robot |
| "No, mejor quédate sentado" | no | — |

Con "Sí, claro" el código confirmó a la primera, pero el modelo aún añadió de
palabra un "¿estás de acuerdo?" redundante. Es comportamiento del modelo, no
del código.

### Ficheros

| Fichero | Contenido |
|---|---|
| `src/examples/multilingual/prompts.yaml` | los skills y su `tools_available` |
| `src/examples/multilingual/tools.yaml` | las 7 herramientas en formato de función |
| `src/examples/multilingual/tool_handlers.py` | lógica, permisos y estado por sesión |
| `src/examples/multilingual/tools.py` | armado del esquema (unión de skills) |

Copias del original con sufijo `.bak`.

### Hardware: humanoide Unitree G1

El control vive en `src/examples/multilingual/g1_unitree.py`, que aísla el SDK
de Unitree del resto de la pipeline. Con `G1_ENABLED=false` responde simulado y
el agente de voz arranca igual.

**El SDK YA ESTÁ INSTALADO** en la imagen `nemotron-voice-agent:g1`, construida
a partir de `docker/Dockerfile.g1`. Para rehacerla:

```bash
cd nemotron-voice-agent
docker buildx build --builder default -f docker/Dockerfile.g1 \
  -t nemotron-voice-agent:g1 .
```

Usa el constructor `default`, **no** el `spark`: ese tiene driver
`docker-container` y no ve las imágenes locales, así que intenta descargar
`nemotron-voice-agent:latest` de un registro y falla.

Tres cosas que costaron un intento cada una y conviene no repetir:

| Problema | Causa | Solución en el Dockerfile |
|---|---|---|
| El paquete de PyPI no sirve | `unitree-sdk2` solo trae el **Go2** | se clona el repositorio de GitHub |
| No hay rueda de `cyclonedds` para aarch64 | el SDK fija `cyclonedds==0.10.2` | se compila CycloneDDS 0.10.x en `/opt/cyclonedds` |
| `python3 -m pip` no existe en la imagen | la imagen del blueprint no trae pip para el python del sistema | se usa `uv pip install` |

El SDK se instala en `/opt/g1-python`, **no** en el venv de la app: el servidor
arranca con `uv run`, que sincroniza `/app/.venv` desde `uv.lock` y borraría
cualquier paquete añadido a mano. Se expone con `PYTHONPATH`.

**Red.** El PC de a bordo del G1 está en `192.168.123.161`. La interfaz de este
equipo debe estar en la misma subred, por ejemplo `192.168.123.99`. El
contenedor necesitará `network_mode: host`, porque CycloneDDS usa multicast y
no atraviesa el puente de Docker.

**Configuración** (en `.env`):

```
G1_ENABLED=false      # true solo con SDK instalado y robot accesible
G1_IFACE=eth0         # interfaz conectada al robot
G1_VEL_LINEAL=0.3     # m/s al andar
G1_VEL_ANGULAR=0.5    # rad/s al girar
```

#### El G1 se controla por velocidad, no por posición

`LocoClient.SetVelocity(vx, vy, omega, duration)` es lo que hay. La capa
convierte distancia en tiempo: `duracion = metros / velocidad`. **La distancia
recorrida es aproximada**, no hay realimentación de posición. Trátala como una
orden de "anda hacia allá un rato", no como un desplazamiento exacto.

#### El audio encaja sin conversión

`AudioClient.PlayStream()` del G1 espera **PCM 16 bits, 16 kHz, mono**, que es
exactamente lo que produce Magpie TTS y lo que usa la pipeline. La voz sale por
el altavoz del robot sin remuestrear. `g1_unitree.reproducir_pcm()` ya trocea
en bloques de 96000 bytes (3 s), como el ejemplo del SDK.

El G1 también tiene `LedControl(R,G,B)`, útil para indicar visualmente si está
escuchando, pensando o hablando.

#### Pendiente: la red DDS (cuando llegue el robot)

**Esto es lo único que falta.** El SDK está instalado y la capa funciona, pero
CycloneDDS usa **multidifusión** y no atraviesa el puente de Docker: el robot
no puede responder a un contenedor detrás de la traducción de direcciones.

Dos caminos, ninguno probado todavía por no tener el robot:

1. **Red del host en el contenedor de la app.** Es lo más simple. Requiere
   `network_mode: host` y cambiar los extremos del catálogo, porque dejarían
   de resolverse por nombre de servicio:
   `tts-service:50051` → `localhost:50151`,
   `nemotron-asr-...:50052` → `localhost:50152`,
   `nvidia-llm-vllm:8000` → `localhost:18000`.
   (Los puertos publicados ya existen, ver `docker ps`.)

2. **Contenedor auxiliar** solo para el robot, con red del host, que exponga
   una API HTTP mínima. `g1_unitree.py` pasaría a ser un cliente HTTP. Más
   limpio y aísla el problema, pero hay que escribirlo.

**Recomendación: la opción 2**, porque deja el agente de voz intacto y evita
tocar el catálogo de servicios.

#### Por qué la capa comprueba el código de retorno

`LocoClient.Init()` **no verifica que el robot esté ahí**: solo prepara el
cliente. En la primera prueba, con el SDK instalado y sin robot, la capa
afirmaba estar conectada y daba por ejecutado un movimiento que nunca salió:

```
disponible=True   motivo=conectado por eth0
[ClientStub] send request error
mover -> {'simulado': False, ...}     <- mentira
```

Se corrigió con una sonda real (`GetFsmId()`) al arrancar y comprobando el
código de retorno de cada orden (0 = éxito). Ahora:

```
disponible=False
motivo=el SDK carga pero el robot no responde por eth0 (código 3102)
mover -> {'simulado': True, ...}      <- honesto
```

Importa porque el modelo confirma al usuario lo que la herramienta le devuelve.
Dar por bueno un movimiento que no ocurrió es peor que fallar.

#### Seguridad específica de un bípedo

| Herramienta | Qué hace | Riesgo |
|---|---|---|
| `detener` | `StopMove()` | ninguno, es la parada normal |
| `parada_emergencia` | `Damp()` | **el robot queda flácido y CAE si está de pie** |

`parada_emergencia` usa **confirmación en dos fases impuesta por el código**.
La primera llamada solo arma la confirmación; la segunda ejecuta, y solo si han
pasado al menos 2 segundos (para que el modelo no encadene dos llamadas en el
mismo turno) y menos de 60.

**Por qué no basta el prompt.** En la prueba, ante "haz una parada de
emergencia", el modelo invocó la herramienta con `confirmado: true` **por su
cuenta**, sin que nadie hubiera confirmado nada. Por eso el manejador **ignora
ese argumento a propósito** y lleva su propio estado.

Es el segundo caso en este proyecto donde el modelo se salta una regla escrita
en el prompt. Ver también la sección de permisos por skill. La conclusión es la
misma: **con un robot que se mueve, toda regla de seguridad va en código.**

### Conectar otro hardware

Las acciones físicas son **puntos de enganche**: registran la intención y
devuelven valores simulados. Cada una lleva un `TODO` donde va tu llamada real.

```python
async def mover(params: FunctionCallParams):
    ...
    logger.info(f"[ROBOT] mover {direccion} {metros} m")
    # TODO: aquí va la llamada real a tu controladora / ROS.
    await params.result_callback({"ok": True, ...})
```

Los **límites de seguridad ya son reales**, no simulados: `mover` valida que la
distancia esté entre 0.1 y 10 metros antes de llegar a tu hardware.

### Añadir un skill

1. Añadir la entrada en `prompts.yaml` con su `content` y `tools_available`.
2. Registrarla en `SKILLS` de `tool_handlers.py` (nombre hablado → clave).
3. Añadir el nombre al `enum` de `cambiar_skill` en `tools.yaml`.
4. Reiniciar la aplicación.

### Requisito en vLLM

Las herramientas necesitan estas banderas, ya puestas en el override:

```
--enable-auto-tool-choice --tool-call-parser qwen3_coder
```

`qwen3_coder` es el analizador de Qwen3.5. El Nemotron usaba `nemotron_json`:
si se cambia de modelo, hay que cambiar el analizador.

## 10. Personalización

**Prompt del robot:** `nemotron-voice-agent/src/examples/multilingual/prompts.yaml`.
Cada skill (`skill_recepcion`, `skill_inspeccion`, `skill_mantenimiento`) tiene
su propio `content`. El skill inicial se fija en `examples_registry.yaml`
(`prompt: [skill_recepcion]`) o por sesión con `prompt_key`. Ver sección 9.

**Voz:** consultar `GET /api/tts-config` y fijar `tts_voice_id` en el cuerpo de
`/api/session-config`. Por ejemplo `Magpie-Multilingual.ES-US.Diego.Happy`.

**Probar la síntesis sin pasar por la pipeline.** El NIM de Magpie expone una
API REST en el puerto 9000. Ojo: es `multipart/form-data`, no JSON, y los
campos se llaman `text`, `voice` y `language` (con JSON responde
`Bad Request, empty text input`, que despista):

```bash
curl -X POST http://localhost:9000/v1/audio/synthesize \
  -F 'text=Hola, soy tu robot asistente.' \
  -F 'voice=Magpie-Multilingual.ES-US.Diego' \
  -F 'language=es-US' \
  -F 'sample_rate_hz=16000' \
  -F 'encoding=LINEAR_PCM' \
  -o prueba.wav
```

Otros endpoints útiles del TTS: `/v1/audio/list_voices`,
`/v1/audio/synthesize_online` (streaming) y `/v1/health/ready`.

Tras cualquier cambio: `bash 04-parar.sh && bash 02-arrancar.sh`.

---

## 11. Trabajo pendiente

Versión local: ninguno bloqueante. Operativa de punta a punta en español.

Versión nube (sección 13):

- La capa gratuita de build.nvidia.com se satura ("Worker local total request
  limit reached (16/16)", "Service temporarily overloaded") y a veces responde
  muy lento (36 s) o con una llamada a herramienta sin argumentos. Hay
  reintentos, modelo de respaldo y aviso hablado, pero para el robot en
  producción hace falta un endpoint con capacidad garantizada o la versión
  local.
- Abierto: en 2 de 4 pruebas de voz se perdieron las primeras palabras de una
  frase ("¿La inteligencia artificial…" llegó como "artificial…"). El VAD vio
  la voz a tiempo y el stream del ASR no se estaba reciclando. Falta aislar si
  es el endpointing de Parakeet en la nube.

Se evaluó sustituir Magpie por Voxtral 4B TTS y se **descartó** por ahora: ver
sección 11.

---

## 12. Alternativas evaluadas y descartadas

Modelos verificados en Hugging Face:

| Modelo | Tamaño | Español |
|---|---|---|
| Qwen/Qwen3.5-35B-A3B (bf16) | 71.9 GB | sí, 201 idiomas |
| Qwen/Qwen3.5-35B-A3B-FP8 | 37.5 GB | sí |
| Qwen/Qwen3.5-35B-A3B-GPTQ-Int4 | 24.5 GB | sí |
| mistralai/Voxtral-4B-TTS-2603 | 8.0 GB | sí, 9 idiomas |

No existe Qwen3.6. El 3.5 tiene 35B totales con 3B activos y 262K de contexto.

**Por qué se descartó Voxtral TTS de momento:**

1. No se sirve con vLLM estándar, necesita
   [`vllm-omni`](https://github.com/vllm-project/vllm-omni), otro proyecto.
2. Pipecat habla con el sintetizador por gRPC contra Riva. Voxtral no es Riva,
   así que hace falta escribir un servicio de Pipecat nuevo. Es programación,
   no configuración.
3. La restricción de memoria de vLLM (ver 5.4) complica que ASR, Qwen y Voxtral
   convivan.

Magpie ya cubre el caso: 12 voces en español con variantes emocionales, y
síntesis 10 veces más rápida que tiempo real. Volver a Voxtral tendría sentido
si se necesitara clonación de voz o una expresividad que Magpie no dé.

---

## 13. Versión nube: solo APIs de NVIDIA

Misma app, skills, seguridad e interfaz móvil, pero sin modelos en el Spark:
el contenedor de la app llama por API a la nube de NVIDIA con `NVIDIA_API_KEY`.
Sirve en cualquier equipo con Docker; no necesita GPU.

```bash
cd /home/lenovo/nvidia-voice/robot
bash 06-nube-arrancar.sh   # para la local si esta encendida, arranca la nube
bash 07-nube-estado.sh
bash 08-nube-parar.sh      # y para volver a la local: bash 02-arrancar.sh
```

Móvil: `https://<ip>:7860/movil/index.html`, igual que la local.

### Servicios

| Función | Servicio | Endpoint |
|---|---|---|
| Reconocimiento | Parakeet 1.1B RNNT multilingüe | `grpc.nvcf.nvidia.com:443`, function-id `71203149-…` |
| Lenguaje | `nvidia/nemotron-3-super-120b-a12b`, respaldo `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | `https://integrate.api.nvidia.com/v1` |
| Síntesis | Magpie TTS multilingüe, voz `Magpie-Multilingual.ES-US.Diego.Happy` | `grpc.nvcf.nvidia.com:443`, function-id `877104f7-…` |

### Cómo está montado

| Pieza | Qué hace |
|---|---|
| perfil `multilingual-assistant` | del blueprint: arranca solo la app con `PLATFORM=cloud` |
| `docker-compose.override.yml` | le da a ese servicio la imagen `g1`, NLTK local, la web móvil y `LLM_MODELO_RESPALDO` |
| `examples_registry.nube.yaml` | copia del registro con los servicios de nube por defecto; se monta solo en este contenedor |
| `services.cloud.yaml` | voz Diego.Happy, entrada `nemotron-nano-omni` añadida y Super sin `repetition_penalty` |
| `llm_nube.py` | reintentos, modelo de respaldo, aviso hablado y limpieza de etiquetas |

Hace falta un registro aparte porque con `PLATFORM=cloud` no se carga el
catálogo local, y `qwen35-4b` no existe en el de nube: la app no arrancaría.

### Latencia medida (desde este Spark)

| Tramo | Valor |
|---|---|
| ASR: fin de voz → transcripción final | 0.75–1.1 s |
| LLM nano-omni: primer token | mediana 532 ms, p90 573 ms |
| LLM Super: primer token | mediana 644 ms, p90 885 ms |
| TTS: primer audio | ~230 ms en caliente (la primera petición tras un rato parado tardó 16.8 s) |
| **Fin de voz → primer audio del robot** | **mediana 2.4–3.1 s, rango 2.0–6.8 s** |

Los turnos lentos son los que encadenan herramienta y confirmación (dos
llamadas al LLM) o los que tuvieron que reintentar. El ASR de la nube no se
atasca a los 30 s como el NIM local: probado con un stream continuo de 45 s. El
reciclado de stream sigue activo por si acaso; es inocuo.

### Elección del modelo

Prueba con el esquema real de herramientas, 6 casos × 3 (cambios de modo,
`mover`, `girar`, charla), en streaming como la pipeline:

| Modelo | Aciertos | Fallos de servicio | Primer token | Charla |
|---|---|---|---|---|
| **nemotron-3-nano-omni** | 14/14 | 4/18 ResourceExhausted | 532 ms | 100–180 caracteres |
| nemotron-3-super | 14/16 | 2/18 overloaded | 644 ms | 320–530 caracteres |
| gpt-oss-20b | 15/18 | 0 | 4.7 s | — |

Descartados: nemotron-3.5-lightning (primer token de 9 a 46 s y cortes),
mistral-nemotron (HTTP 500), glm-5.3-flash, kimi-k3 y gemma-4-31b (sin
respuesta en 30 s), deepseek-v4-flash (18.8 s), nemotron-3-ultra (30 s).
Llama 3.3, Llama 4, Qwen3-Next y gpt-oss-120b aparecen en otras listas pero la
API devuelve 410 (retirados).

En cambios de modo desde cinco modos distintos (un solo turno), nano-omni
acertó 12/15 y Super 12/13; los fallos de ambos fueron "siéntate y hablemos de
IA", donde primero llaman a `postura`.

Hasta aquí nano-omni parecía mejor, y se probó por defecto. En las pruebas de
voz falló donde las pruebas de un turno no miraban: **con historial**, tras
haber cambiado de modo y haberse movido, dijo "ahora estoy en modo educación"
sin invocar `cambiar_skill`, y el robot siguió en inspección. Repetido por API
con ese mismo historial, 4 intentos por escenario:

| Escenario con historial | Super | nano-omni |
|---|---|---|
| recepción → inspección | 4/4 | 3/3 (+1 ResourceExhausted) |
| inspección → educación, tras `mover` | **4/4** | **0/4**, responde solo con texto |

**Decisión: Super por defecto, nano-omni de respaldo.** El respaldo solo entra
cuando Super está saturado, así que su fallo sistemático afecta a pocos turnos,
y a cambio esos turnos no se quedan mudos.

### Problemas encontrados y soluciones

**1. La app usaba otro modelo del que decía el registro.** La pipeline pedía
`load_service_entry("llm", "")`, que devuelve la primera entrada del catálogo.
En local coincidía con Qwen por casualidad; en nube cogía Lightning, el más
lento. Ahora toma el id por defecto de `examples_registry` para el ejemplo.

**2. La nube se satura y el robot se quedaba mudo.** "Service temporarily
overloaded" llega como evento de error dentro del stream SSE, así que los
reintentos del cliente openai no lo cubren; nano-omni además devuelve
ResourceExhausted (16/16). `NvidiaLLMNube` reintenta mientras no haya llegado
ningún trozo, alternando principal y respaldo (4 intentos). Si todos fallan,
el robot dice "Perdona, tengo la conexión saturada…" en vez de callar.

**3. El robot decía "tool call" en voz alta.** Tras varias herramientas, Super
escribía `<tool_call>`, `<answer>` o `</think>` como texto. El filtro de TTS
solo quitaba el `<`. Además quedaban en el historial y el modelo las imitaba.
Se limpian en el propio stream (antes de entrar al contexto) y otra vez por
frase antes del TTS, junto con narraciones como "(Procedo a llamar a la
herramienta mover)".

**4. Ninguna herramienta se ejecutaba.** El envoltorio del stream llamaba a
`close()`, pero NvidiaLLMService devuelve un generador asíncrono, que solo
tiene `aclose()`. La excepción saltaba al final de cada respuesta y Pipecat
ejecuta las herramientas después de consumir el stream: el modelo decía
"avanzando" y el robot no se movía.

**5. El modelo anunciaba el movimiento y no llamaba a `mover`.** La regla
"antes de moverte, di en voz alta lo que vas a hacer" hacía que respondiera
"voy a avanzar dos metros" y esperara. Con "invoca mover o girar en esa misma
respuesta, sin pedir confirmación" pasó de 0–1/3 a 3/3. Los límites siguen en
el código (distancia máxima, denegación si está sentado).

**6. Estilo impropio de voz.** Con los modelos de nube aparecieron listas con
guiones, "Soy Nemotron", reglas recitadas al usuario y observaciones inventadas
tras moverse ("superficie plana, sin obstáculos"). Se añadieron reglas a los
seis skills: sin listas, no recitar reglas ni nombrar el modelo, no narrar
herramientas y, en inspección, no inventar lo que ve.

**7. Argumento corrupto y confusión de modos.** Super mandó una vez
`skill: "recepcion>\nrecepcion"`; `cambiar_skill` ahora acepta el valor si
contiene un único modo válido. Nano-omni pasaba a `ia` cuando se pedía hablar de
educación; la descripción de la herramienta ahora da el tema de cada modo.

**8. Respuesta degradada ocasional.** En una prueba, Super tardó 36 s en
completar un stream que empezó en 1.1 s y terminó en `cambiar_skill({})`, sin
argumentos. No hubo error, así que no hay reintento que lo cubra. El manejador
ahora contesta al modelo con una instrucción explícita de volver a invocar la
herramienta con un modo válido; antes, con solo el error, el robot le decía al
usuario "debe especificar exactamente el nombre del modo".

**9. `repetition_penalty` hacía que fingiera las acciones.** El catálogo de
nube del blueprint manda `repetition_penalty: 1.05` a Super. En la prueba de
voz, con eso, el robot dijo "(Accionado con `mover`…)" y "ya he cambiado de
modo" sin haber invocado ninguna herramienta. Reproducido por API con el
contexto exacto de ese turno, sacado de los registros:

| Configuración | Llamada real a `cambiar_skill` | Solo texto ("cambio de modo activado") |
|---|---|---|
| `repetition_penalty: 1.05` | 3/4 | 1/4 |
| sin penalización | **4/4** | 0/4 |

Penalizar tokens repetidos estropea el formato de las llamadas, que repite
llaves, comillas y nombres. Se quitó de la entrada `nemotron-super`. Las
comparativas anteriores no lo enviaban, por eso no lo detectaron. Además, el
filtro de voz ahora quita los paréntesis que nombran herramientas y los
acentos graves.

**Nota de método.** Las primeras comparativas de modelos enviaban las
herramientas sin descripción ni parámetros (el script leía mal `tools.yaml`) y
llevaron a conclusiones erróneas. Las tablas de arriba son de la repetición con
el esquema real.

### Límites

- Capa gratuita: capacidad compartida y créditos limitados. Válida para
  desarrollo y demostraciones; para el robot en uso real, endpoint con
  capacidad garantizada o la versión local.
- La latencia incluye la ida y vuelta a los servidores de NVIDIA; es mayor que
  en la local, sobre todo en el reconocimiento. Además varía: la misma
  petición tarda 0.8 s o, de forma ocasional, más de 30 s.
- El audio y el texto de la conversación salen del equipo hacia NVIDIA.

### Ficheros

| Fichero | Cambio |
|---|---|
| `src/examples/multilingual/llm_nube.py` | nuevo |
| `src/examples/multilingual/pipeline.py` | defaults del registro, `NvidiaLLMNube`, filtro de etiquetas |
| `src/examples/multilingual/services.cloud.yaml` | Diego.Happy, `nemotron-nano-omni`, Super sin `repetition_penalty` |
| `src/examples/multilingual/prompts.yaml` | reglas de voz, inspección que actúa |
| `src/examples/multilingual/tools.yaml` | descripción de `cambiar_skill` |
| `src/examples/multilingual/tool_handlers.py` | tolerancia a argumento corrupto |
| `examples_registry.nube.yaml` | nuevo |
| `docker-compose.override.yml` | servicio `multilingual-assistant` |
| `robot/06-nube-arrancar.sh`, `07-nube-estado.sh`, `08-nube-parar.sh` | nuevos |

Los cambios de `pipeline.py`, `prompts.yaml`, `tools.yaml` y `tool_handlers.py`
también aplican a la versión local.

**Sobre el modelo de lenguaje**, se evaluó el Qwen3.5-35B-A3B que se pidió
inicialmente (71.9 GB en bf16, 37.5 en FP8, 24.5 en GPTQ Int4). Se optó por el
**Qwen3.5-4B** (9.3 GB) para dejar memoria libre a los modelos adicionales
previstos. No existe Qwen3.6.

---

## 14. Propuesta: Azure Brasil Sur (sin implementar)

Estado: **evaluada, pendiente de crear los recursos en Azure**. Nada de esta
sección está en el código todavía.

### Por qué se plantea

La nube gratuita de NVIDIA se satura (sección 13) y NVIDIA no vende una versión
de pago de esa misma API. Sus opciones de pago son la licencia NVIDIA AI
Enterprise (para correr los modelos en el propio Spark, prueba gratis de 90
días y luego unos 4 500 USD por GPU al año en la licencia general; el precio
para Spark no está publicado), proveedores asociados que solo sirven el LLM, o
DGX Cloud por hora de GPU.

### Red medida desde el Spark

Ida y vuelta hasta el servidor que procesa (petición sin clave, mediana de 5).
Mide la distancia, no la velocidad del modelo.

| Uso | Proveedor | ms |
|---|---|---|
| Voz | **Azure Brasil Sur** | **83** |
| LLM | NVIDIA | 86 |
| Región | AWS Virginia / São Paulo / Oregón | 87 / 91 / 143 |
| Voz | NVIDIA NVCF | 103 |
| LLM | Fireworks / OpenAI / Anthropic | 100 / 167 / 198 |
| Voz | Azure East US | 122 |
| Voz | Deepgram / AssemblyAI | 174 / 177 |
| Voz | ElevenLabs / Cartesia | 218 / 232 |
| LLM | Groq / Cerebras / Together | ~230 |
| LLM | Google Gemini | 975 (puede ser el trato a peticiones sin clave) |

Desglose de un turno de 3.3 s con la nube de NVIDIA: detectar fin de frase y
transcribir 0.9–1.2 s, primer token del LLM 0.6–0.9 s (hasta 2.8 s), segunda
llamada al LLM en turnos con acción 0.8–1.0 s, primer audio 0.24 s. La red
aporta unos 0.2 s por llamada; el resto es procesamiento y cola.

### Qué ofrece Brasil Sur

| Pieza | Disponible |
|---|---|
| Reconocimiento en tiempo real | sí; los datos se procesan solo en la región |
| Voces neuronales | sí; **es-PE Camila y Alex**, además de es-MX, es-CO, es-AR, es-CL y es-US |
| Voces HD | no en esta región |
| LLM | gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, gpt-5.4-mini/nano y otros |

Matiz del LLM: en Brasil Sur los modelos se despliegan como **Global
Standard**, que Microsoft puede procesar en otra región. Procesar solo en
Brasil exige capacidad reservada (Provisioned), mucho más cara.

Técnicamente es viable: Pipecat 1.5 trae `pipecat.services.azure` (stt, tts,
llm) y el SDK `azure-cognitiveservices-speech` 1.51.2 publica rueda para Linux
aarch64. Solo falta añadirlo a la imagen `g1`.

### Pasos

En portal.azure.com:

1. Cuenta con suscripción de pago.
2. Recurso **Speech** en **Brazil South**, plan **S1** (el gratuito tiene
   límites que pueden cortar una demo).
3. Recurso **Azure OpenAI / Foundry** en **Brazil South** con un despliegue
   **gpt-4.1-mini**, Global Standard.
4. En `nemotron-voice-agent/.env`:

   ```
   AZURE_SPEECH_API_KEY=...
   AZURE_SPEECH_REGION=brazilsouth
   AZURE_OPENAI_API_KEY=...
   AZURE_OPENAI_ENDPOINT=https://<recurso>.openai.azure.com/
   AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
   ```

Implementación prevista:

1. SDK de Azure Speech en `docker/Dockerfile.g1`.
2. Perfil `multilingual-assistant-azure`: reconocimiento es-PE, voz
   `es-PE-AlexNeural`, gpt-4.1-mini con los reintentos y el aviso de
   `llm_nube.py`, mismas herramientas y seguridad.
3. Enviar audio al reconocedor **solo cuando el detector de voz oye a alguien**
   (ver precios).
4. Scripts de arranque, estado y parada, y de cambio a la versión local.
5. Las mismas pruebas que con NVIDIA: 5 turnos por voz, cambios de modo con
   historial, `mover`, latencia por tramo.

### Precios

Precios oficiales de Azure para `brazilsouth`, pago por uso, consultados en la
API pública de precios (septiembre de 2026), en USD y sin impuestos. En Perú
probablemente aplique IGV a servicios digitales del extranjero.

| Servicio | Precio |
|---|---|
| Reconocimiento en tiempo real (S1) | **1.00 por hora de audio** |
| Voz neuronal (S1) | **15 por millón de caracteres** |
| gpt-4.1-mini, Global | 0.40 entrada / 0.10 entrada en caché / 1.60 salida, por millón de tokens |
| gpt-4.1-nano, Global | 0.10 / 0.025 / 0.40 |
| gpt-5.4-mini, Global | 0.75 / 0.075 / 4.50 |
| gpt-5.4-nano, Global | 0.20 / 0.02 / 1.25 |

Consumo medido en los registros de la versión nube: cada llamada al LLM usa
1 800–2 700 tokens de entrada y 10–120 de salida; los turnos con acción hacen
dos llamadas. Por turno: unos 3 100 tokens de entrada y 60 de salida, y unos
120 caracteres de voz.

| Coste por turno | USD |
|---|---|
| LLM gpt-4.1-mini | ~0.0013 |
| Voz | ~0.0018 |
| **Total sin reconocimiento** | **~0.003** |

**Lo que decide el precio es el reconocimiento.** Hoy el agente envía el
micrófono continuamente, silencios incluidos, y Azure lo cobra como horas de
audio aunque nadie hable. Enviarlo solo cuando hay voz reduce mucho el coste.

Estimación mensual (22 días, un turno por minuto de media):

| Uso | Micro continuo | Solo cuando hay voz (~20 % del tiempo) |
|---|---|---|
| Demos, 2 h/día | ~52 | **~17** |
| Recepción, 8 h/día | ~210 | **~68** |
| 24 h todos los días | ~800 | ~180 |

Son estimaciones: dependen de cuánto hable la gente y del largo de las
respuestas.

### Evento de una hora

| Concepto | Cálculo | USD |
|---|---|---|
| Reconocimiento | 1 h × 1.00 | 1.00 |
| LLM gpt-4.1-mini | ~120 turnos × 0.0013 | ~0.16 |
| Voz es-PE | ~120 turnos × 0.0018 | ~0.22 |
| **Total** | peor caso, micro abierto toda la hora | **~1.40** |

Las pruebas previas suman unos pocos dólares más. Con ese coste lo que importa
es que no falle:

- **Plan S1, no el gratuito**: sus límites pueden cortar al robot a mitad de
  demo, por un par de dólares de diferencia.
- **Versión local lista como plan B**: si cae el internet del lugar, Azure deja
  de funcionar y la local sigue. Cambiar es parar una y arrancar la otra.
- **Probar en la red del evento**: la latencia medida es desde la red actual; un
  wifi de evento saturado la empeora. Mejor cable o un router propio.
- **Ensayo completo el día anterior**: una hora seguida hablando, con cambios de
  modo y movimientos.

### Fuentes

- [Regiones de Azure Speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions)
- [Disponibilidad regional de modelos de Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability)
- [Idiomas y voces de Azure Speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support)
- [Voces de Azure en español](https://json2video.com/ai-voices/azure/languages/spanish/)
- [API pública de precios de Azure](https://prices.azure.com/api/retail/prices)
- [Foro de NVIDIA: límites de build.nvidia.com](https://forums.developer.nvidia.com/t/api-credit-rate-limit-increase-request-for-build-nvidia-com-free-tier/382934)
- [NVIDIA AI Enterprise—DGX Spark](https://www.nvidia.com/content/dam/en-zz/Solutions/dgx-spark/workstation-print-gtc26-nvaie-spark-solution-overview-5004550-r7.pdf)

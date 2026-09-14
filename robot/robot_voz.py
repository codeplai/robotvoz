#!/usr/bin/env python3
"""
Cliente de voz en espanol para el robot.

Habla con el agente Nemotron que corre en el DGX Spark:
  1. POST /api/session-config  -> abre sesion fijada a es-US
  2. WebSocket /api/ws         -> audio PCM 16 kHz mono en ambos sentidos

Uso:
    python3 robot_voz.py --wav pregunta.wav --out respuesta.wav
    python3 robot_voz.py --micro            # microfono y altavoz en vivo

Dependencias:
    pip install websockets protobuf requests
    pip install sounddevice        # solo para --micro
"""

from __future__ import annotations

import argparse
import functools
import asyncio
import json
import ssl
import sys
import uuid
import wave
from pathlib import Path

import requests
import websockets

sys.path.insert(0, str(Path(__file__).parent))
from frames_pb2 import Frame  # noqa: E402  (generado desde pipecat frames.proto)

print = functools.partial(print, flush=True)  # noqa: A001

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * CHANNELS * 2 * CHUNK_MS // 1000


def abrir_sesion(base_url: str, idioma: str) -> str:
    """Crea la configuracion de sesion y devuelve el session_id."""
    payload = {
        "pipeline_mode": "multilingual-assistant",
        # Sin llm_id a proposito: el servidor usa su modelo por defecto. Con un
        # llm_id que no existe en el catalogo, el servidor cierra el socket con
        # codigo 1000 ("OK") y el cliente no recibe ningun error util.
        "asr_language_code": idioma,
        "prompt_key": "skill_recepcion",
    }
    res = requests.post(f"{base_url}/api/session-config", json=payload, verify=False, timeout=60)
    res.raise_for_status()
    return res.json()["session_id"]


def _ssl_ctx(url: str):
    if not url.startswith("https"):
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def empaquetar(pcm: bytes) -> bytes:
    """PCM crudo -> frame protobuf de Pipecat."""
    f = Frame()
    f.audio.audio = pcm
    f.audio.sample_rate = SAMPLE_RATE
    f.audio.num_channels = CHANNELS
    return f.SerializeToString()


def mensaje_cliente_listo() -> bytes:
    """Mensaje RTVI "client-ready". OBLIGATORIO nada mas conectar.

    El servidor solo lanza el saludo inicial al recibirlo, y hasta que el bot
    completa ese primer turno ignora toda la voz del usuario. Sin el, la sesion
    se queda muda para siempre sin dar ningun error.
    """
    f = Frame()
    f.message.data = json.dumps(
        {
            "label": "rtvi-ai",
            "type": "client-ready",
            "id": str(uuid.uuid4()),
            "data": {"version": "2.0.0", "about": {"library": "robot-voz-python"}},
        }
    )
    return f.SerializeToString()


def desempaquetar(data: bytes):
    """Frame protobuf -> (tipo, contenido). Devuelve (None, None) si no interesa."""
    f = Frame()
    f.ParseFromString(data)
    cual = f.WhichOneof("frame")
    if cual == "audio":
        return "audio", f.audio.audio
    if cual == "text":
        return "texto", f.text.text
    if cual == "message":
        try:
            return "mensaje", json.loads(f.message.data)
        except Exception:
            return "mensaje", f.message.data
    return None, None


async def conversar(base_url: str, idioma: str, wav_in: Path | None, wav_out: Path | None):
    session_id = abrir_sesion(base_url, idioma)
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    uri = f"{ws_url}/api/ws?session_id={session_id}"
    print(f"[robot] sesion {session_id} idioma {idioma}")

    async with websockets.connect(uri, ssl=_ssl_ctx(base_url), max_size=None) as ws:
        print("[robot] conectado")
        await ws.send(mensaje_cliente_listo())
        print("[robot] client-ready enviado")

        # El servidor silencia la voz del usuario hasta que el bot termina su
        # saludo. Audio enviado antes se pierde, asi que se espera al primer
        # bot-stopped-speaking (con tope, por si el saludo esta desactivado).
        saludo_terminado = asyncio.Event()
        respuesta_terminada = asyncio.Event()
        turnos_bot = [0]

        async def enviar():
            if wav_in is None:
                return
            try:
                await asyncio.wait_for(saludo_terminado.wait(), timeout=30)
                print("[robot] saludo terminado, envio la pregunta")
            except asyncio.TimeoutError:
                print("[robot] sin saludo en 30 s, envio igualmente")
            with wave.open(str(wav_in), "rb") as w:
                assert w.getframerate() == SAMPLE_RATE, "el wav debe ser 16 kHz"
                assert w.getnchannels() == CHANNELS, "el wav debe ser mono"
                while chunk := w.readframes(SAMPLE_RATE * CHUNK_MS // 1000):
                    await ws.send(empaquetar(chunk))
                    await asyncio.sleep(CHUNK_MS / 1000)
            # silencio para que el detector de turno cierre la frase
            silencio = b"\x00" * CHUNK_BYTES
            for _ in range(50):
                await ws.send(empaquetar(silencio))
                await asyncio.sleep(CHUNK_MS / 1000)

        async def recibir():
            salida = None
            if wav_out:
                salida = wave.open(str(wav_out), "wb")
                salida.setnchannels(CHANNELS)
                salida.setsampwidth(2)
                salida.setframerate(SAMPLE_RATE)
            try:
                async for data in ws:
                    tipo, cont = desempaquetar(data)
                    if tipo == "audio" and salida:
                        salida.writeframes(cont)
                    elif tipo == "texto":
                        print(f"[bot] {cont}")
                    elif tipo == "mensaje":
                        m = cont if isinstance(cont, dict) else {}
                        if m.get("type") == "server-message" and isinstance(m.get("data"), dict):
                            m = m["data"]
                        t = m.get("type")
                        d = m.get("data") or {}
                        if t == "bot-stopped-speaking":
                            turnos_bot[0] += 1
                            saludo_terminado.set()
                            # 1.er turno = saludo, 2.o = respuesta a la pregunta
                            if turnos_bot[0] >= 2:
                                respuesta_terminada.set()
                        if t == "user-transcription" and d.get("final"):
                            print(f"[usuario] {d.get('text', '')}")
                        elif t == "bot-tts-text" and d.get("text"):
                            print(f"[bot] {d['text']}")
            finally:
                if salida:
                    salida.close()
                    print(f"[robot] respuesta guardada en {wav_out}")

        receptor = asyncio.create_task(recibir())
        await enviar()
        if wav_in is not None:
            try:
                await asyncio.wait_for(respuesta_terminada.wait(), timeout=60)
                print("[robot] respuesta completa")
            except asyncio.TimeoutError:
                print("[robot] sin respuesta completa en 60 s")
            receptor.cancel()
            try:
                await receptor
            except asyncio.CancelledError:
                pass
        else:
            await receptor


def main():
    p = argparse.ArgumentParser(description="Cliente de voz en espanol para el robot")
    p.add_argument("--url", default="https://localhost:7860", help="URL del agente")
    p.add_argument("--idioma", default="es-US", help="codigo de idioma de la sesion")
    p.add_argument("--wav", type=Path, help="wav de entrada, 16 kHz mono")
    p.add_argument("--out", type=Path, default=Path("respuesta.wav"), help="wav de salida")
    args = p.parse_args()

    requests.packages.urllib3.disable_warnings()
    try:
        asyncio.run(conversar(args.url, args.idioma, args.wav, args.out))
    except KeyboardInterrupt:
        print("\n[robot] fin")


if __name__ == "__main__":
    main()

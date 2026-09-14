# SPDX-License-Identifier: BSD-2-Clause
"""Reconocedor NVIDIA que recicla su stream gRPC antes de que se atasque.

Problema (medido en esta instalacion)
-------------------------------------
Con el micro abierto de forma continua, el NIM nemotron-asr-streaming
(perfil multi, diarizer:sortformer, el unico disponible para DGX Spark) deja
de consumir audio tras ~30 s acumulados en el MISMO stream. No hay error ni
caida: el servidor simplemente no lee mas, las estadisticas del NIM se quedan
en audio_duration=31.84 s (identico en dos reproducciones) y Pipecat sigue
encolando audio en silencio. La deteccion de voz sigue funcionando, pero ya no
llegan transcripciones: el robot "escucha" y no responde.

Sospechoso: la diarizacion sortformer, con cache de hablantes de tamaño fijo
y spkcache_refresh_rate=0 (nunca se refresca).

Solucion
--------
Abrir un stream nuevo antes del tope, en un momento en que el usuario no hable:
- a partir de UMBRAL_S de audio en el stream, si el usuario no esta hablando y
  hace al menos SILENCIO_S que no llega ningun resultado del reconocedor;
- a los MAXIMO_S, se recicla igualmente (mejor cortar una palabra que perder
  todas las siguientes).

El reciclado usa un iterador NUEVO para el stream nuevo y cierra el viejo con
su centinela, en vez del _do_reconnect de Pipecat, que reutiliza el mismo
iterador y dejaria dos streams leyendo de la misma cola. Al cerrarse, el stream
viejo entrega los resultados finales que tuviera pendientes.
"""

import time

from loguru import logger
from pipecat.frames.frames import VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.services.nvidia.stt import AudioChunkIterator, NvidiaSTTService

BYTES_POR_SEGUNDO = 16000 * 2  # PCM 16 bits, 16 kHz, mono
UMBRAL_S = 20.0
MAXIMO_S = 28.0
SILENCIO_S = 1.5


class NvidiaSTTServiceReciclado(NvidiaSTTService):
    """NvidiaSTTService que renueva el stream gRPC cada ~20 s de audio."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bytes_stream = 0
        self._usuario_habla = False
        self._ultimo_resultado = 0.0
        self._reciclados = 0
        self._vad_visto = False

    async def process_frame(self, frame, direction):
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._usuario_habla = True
            self._vad_visto = True
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._usuario_habla = False
            self._ultimo_resultado = max(self._ultimo_resultado, time.monotonic())
            self._vad_visto = True
        await super().process_frame(frame, direction)

    async def _handle_response(self, response):
        for result in response.results:
            if result and result.alternatives and result.alternatives[0].transcript:
                self._ultimo_resultado = time.monotonic()
                break
        await super()._handle_response(response)

    async def _reciclar_stream(self, motivo: str):
        viejo = self._audio_iterator
        if viejo is None or viejo.closed:
            return
        segundos = self._bytes_stream / BYTES_POR_SEGUNDO
        nuevo = AudioChunkIterator(self.get_event_loop())
        # Desde aqui el audio entrante va al iterador nuevo...
        self._audio_iterator = nuevo
        self._bytes_stream = 0
        # ...y un stream nuevo lo consume. _thread_task_handler lee
        # self._audio_iterator al arrancar, por eso se asigna antes.
        self._thread_task = self.create_task(self._thread_task_handler())
        # El viejo recibe su centinela, termina y entrega sus finales.
        await viejo.close()
        self._reciclados += 1
        logger.info(
            f"{self} stream ASR reciclado #{self._reciclados} tras {segundos:.1f} s de audio "
            f"({motivo}; VAD visto: {self._vad_visto})"
        )

    async def run_stt(self, audio: bytes):
        self._bytes_stream += len(audio)
        segundos = self._bytes_stream / BYTES_POR_SEGUNDO
        if segundos >= MAXIMO_S:
            await self._reciclar_stream("tope alcanzado")
        elif segundos >= UMBRAL_S and not self._usuario_habla:
            if time.monotonic() - self._ultimo_resultado >= SILENCIO_S:
                await self._reciclar_stream("usuario en silencio")
        async for frame in super().run_stt(audio):
            yield frame

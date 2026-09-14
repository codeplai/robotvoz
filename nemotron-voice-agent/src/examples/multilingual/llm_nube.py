"""LLM de la nube de NVIDIA con reintentos y limpieza de etiquetas de plantilla.

Dos problemas medidos con nvidia/nemotron-3-super-120b-a12b en
integrate.api.nvidia.com, en conversaciones reales por voz:

1. Errores transitorios. "Service temporarily overloaded" llega como evento de
   error DENTRO del stream SSE, no como estado HTTP, asi que los reintentos del
   cliente openai no lo cubren. Tambien hay HTTP 429 y 500 sueltos. Pipecat lo
   convierte en ErrorFrame y el turno se queda sin respuesta: el robot se calla.
   Aqui se reintenta mientras no haya llegado ningun trozo (reintentar despues
   repetiria frases ya dichas).

2. Etiquetas de plantilla como texto. Tras varias llamadas a herramientas el
   modelo escribe a veces <tool_call>, <answer> o </think> dentro del contenido.
   NemotronSpeechTextFilter solo quita el "<", asi que el robot decia
   "tool call" en voz alta; y como el texto queda en el historial, el modelo lo
   imitaba en los turnos siguientes. Se limpia en el propio stream, antes de que
   Pipecat lo agregue al contexto, y otra vez por frase antes del TTS.
"""

import asyncio
import contextlib
import re

from loguru import logger
from openai import APIConnectionError, APIError, APIStatusError
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk, Choice, ChoiceDelta
from pipecat.services.nvidia.llm import NvidiaLLMService
from pipecat.utils.text.base_text_filter import BaseTextFilter

# Intentos y espera previa a cada uno. Con modelo de respaldo se alterna
# principal, respaldo, principal, respaldo: la saturacion es por modelo
# ("Worker local total request limit reached (16/16)"), asi que saltar al otro
# sin esperar suele bastar. Sin respaldo, el principal cuatro veces.
INTENTOS = 4
ESPERAS_S = (0.0, 0.0, 0.6, 1.0)

# Si fallan todos los intentos, el robot lo dice en vez de quedarse mudo. Queda
# en el historial como respuesta del asistente, lo que es honesto con lo ocurrido.
AVISO_SIN_SERVICIO = "Perdona, tengo la conexión saturada y no te he podido responder. ¿Me lo repites?"

_MENSAJE_TRANSITORIO = re.compile(
    r"overload|temporar|unavailable|exhaust|capacity|rate.?limit|too many requests|timed? ?out|try again", re.I
)

# Etiquetas que el modelo filtra de su plantilla de chat.
ETIQUETAS = frozenset({"think", "answer", "tool_call", "tool_response", "function_call"})
# Bloques cuyo CONTENIDO tampoco se dice: razonamiento y llamadas en crudo.
BLOQUES_OCULTOS = frozenset({"think", "tool_call", "tool_response", "function_call"})

_NOMBRES = "|".join(sorted(ETIQUETAS))
_ETIQUETA_COMPLETA = re.compile(rf"<\s*/?\s*(?:{_NOMBRES})\s*>", re.I)
# Por si llega sin "<" (ya quitado por otro filtro): "tool_call>", "/answer>".
_ETIQUETA_SIN_APERTURA = re.compile(rf"(?<![\w<])/?(?:{_NOMBRES})>", re.I)
# Narracion interna que el modelo mete entre parentesis y no es para decirla:
# "(Procedo a llamar a la herramienta mover)", "(He usado `cambiar_skill` para...)".
_HERRAMIENTAS = (
    "cambiar_skill|listar_skills|mover|girar|detener|parada_emergencia|postura|gesto|"
    "leer_sensor|reportar_incidencia"
)
_NARRACION_HERRAMIENTA = re.compile(
    rf"\([^()]*(?:\bherramienta\b|`[^`]*`|\b(?:{_HERRAMIENTAS})\b)[^()]*\)\.?", re.I
)
# Nombre de herramienta suelto entre acentos graves fuera de parentesis.
_CODIGO_EN_LINEA = re.compile(r"`([^`]*)`")


def es_transitorio(exc: BaseException) -> bool:
    """True si merece la pena reintentar la peticion."""
    if isinstance(exc, APIConnectionError):  # incluye APITimeoutError
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 429) or exc.status_code >= 500
    if isinstance(exc, APIError):  # error dentro del stream SSE, sin estado HTTP
        return bool(_MENSAJE_TRANSITORIO.search(str(exc)))
    return False


class LimpiadorEtiquetas:
    """Quita etiquetas de plantilla de un texto que llega troceado token a token.

    Una etiqueta puede partirse entre trozos ("<tool" + "_call>"), asi que lo que
    empieza por "<" se retiene hasta ver ">" o descartar que sea etiqueta.
    """

    MAX_PENDIENTE = 24

    def __init__(self) -> None:
        self._pendiente = ""
        self._oculto: str | None = None

    def limpiar(self, texto: str) -> str:
        salida: list[str] = []
        for c in texto:
            if self._pendiente:
                if c == "<":
                    self._soltar(salida)
                    self._pendiente = "<"
                    continue
                self._pendiente += c
                if c == ">":
                    self._resolver(salida)
                elif c == "\n" or len(self._pendiente) > self.MAX_PENDIENTE:
                    self._soltar(salida)
            elif c == "<":
                self._pendiente = "<"
            elif self._oculto is None:
                salida.append(c)
        return "".join(salida)

    def terminar(self) -> str:
        """Fin de la respuesta: suelta lo retenido y reinicia el estado."""
        salida: list[str] = []
        self._soltar(salida)
        self._oculto = None
        return "".join(salida)

    def _soltar(self, salida: list[str]) -> None:
        if self._oculto is None:
            salida.append(self._pendiente)
        self._pendiente = ""

    def _resolver(self, salida: list[str]) -> None:
        m = re.fullmatch(r"<\s*(/?)\s*([A-Za-z_]+)\s*>", self._pendiente)
        nombre = m.group(2).lower() if m else ""
        if nombre not in ETIQUETAS:
            self._soltar(salida)  # "<5 metros>" o similar: texto normal
            return
        self._pendiente = ""
        if m.group(1) == "/":
            if self._oculto == nombre:
                self._oculto = None
        elif nombre == "answer":
            # <answer> es lo que se quiere decir, aunque venga dentro de un
            # <tool_call> que el modelo nunca cerro.
            self._oculto = None
        elif nombre in BLOQUES_OCULTOS:
            self._oculto = nombre


class _FlujoLimpio:
    """Envoltorio del AsyncStream de openai: reinyecta el primer trozo y limpia el texto."""

    def __init__(self, flujo, iterador, primero, limpiador: LimpiadorEtiquetas):
        self._flujo = flujo
        self._iterador = iterador
        self._primero = primero
        self._limpiador = limpiador

    def __aiter__(self):
        return self._generar()

    async def _generar(self):
        try:
            if self._primero is not None:
                yield self._limpiar(self._primero)
            async for trozo in self._iterador:
                yield self._limpiar(trozo)
        finally:
            if hasattr(self._iterador, "aclose"):
                await self._iterador.aclose()

    def _limpiar(self, trozo):
        for choice in trozo.choices or []:
            delta = choice.delta
            if delta is None:
                continue
            if delta.content:
                delta.content = self._limpiador.limpiar(delta.content)
            if choice.finish_reason:
                resto = self._limpiador.terminar()
                if resto:
                    delta.content = (delta.content or "") + resto
        return trozo

    async def close(self):
        await _cerrar(self._flujo)


async def _cerrar(flujo) -> None:
    """Cierra el stream sea cual sea su tipo.

    NvidiaLLMService no devuelve el AsyncStream de openai (que tiene close) sino
    un generador asincrono que lo envuelve (que solo tiene aclose). Llamar a
    close() ahi lanzaba una excepcion al final de CADA respuesta, y como Pipecat
    ejecuta las herramientas despues de consumir el stream, ninguna llamada a
    herramienta llegaba a ejecutarse: el modelo decia "avanzando" sin moverse.
    """
    if hasattr(flujo, "aclose"):
        await flujo.aclose()
    elif hasattr(flujo, "close"):
        await flujo.close()


class NvidiaLLMNube(NvidiaLLMService):
    """NvidiaLLMService con reintentos, modelo de respaldo y limpieza de etiquetas.

    Ante un fallo transitorio ANTES del primer trozo se reintenta segun
    INTENTOS/ESPERAS_S, alternando con el modelo de respaldo si lo hay
    (LLM_MODELO_RESPALDO). Si se agotan, se responde AVISO_SIN_SERVICIO.

    Medido en la nube gratuita: nano-omni devolvia ResourceExhausted en 4 de 18
    peticiones y Super "Service temporarily overloaded" en 2 de 18, y en una
    prueba de voz fallaron los dos seguidos en el mismo turno.
    """

    def __init__(self, *args, modelo_respaldo: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._modelo_respaldo = modelo_respaldo or None
        self._modelo_forzado: str | None = None

    def build_chat_completion_params(self, params_from_context) -> dict:
        params = super().build_chat_completion_params(params_from_context)
        if self._modelo_forzado:
            params["model"] = self._modelo_forzado
        return params

    def _modelo_del_intento(self, intento: int) -> str | None:
        """None = principal. Con respaldo, los intentos pares van al respaldo."""
        return self._modelo_respaldo if self._modelo_respaldo and intento % 2 == 0 else None

    async def get_chat_completions(self, context):
        ultimo: BaseException | None = None
        for intento in range(1, INTENTOS + 1):
            espera = ESPERAS_S[min(intento - 1, len(ESPERAS_S) - 1)]
            if intento > 1 and espera:
                await asyncio.sleep(espera)
            self._modelo_forzado = self._modelo_del_intento(intento)
            destino = self._modelo_forzado or "principal"
            flujo = None
            try:
                flujo = await super().get_chat_completions(context)
                iterador = flujo.__aiter__()
                try:
                    primero = await iterador.__anext__()
                except StopAsyncIteration:
                    primero = None
                if intento > 1:
                    logger.info(f"{self}: respuesta obtenida en el intento {intento}/{INTENTOS} ({destino})")
                return _FlujoLimpio(flujo, iterador, primero, LimpiadorEtiquetas())
            except Exception as exc:
                if flujo is not None:
                    with contextlib.suppress(Exception):
                        await _cerrar(flujo)
                if not es_transitorio(exc):
                    raise
                ultimo = exc
                logger.warning(f"{self}: fallo transitorio en el intento {intento}/{INTENTOS} ({destino}): {exc}")
            finally:
                self._modelo_forzado = None
        logger.error(f"{self}: {INTENTOS} intentos fallidos ({ultimo}); se responde con el aviso de servicio saturado")
        return _flujo_aviso(AVISO_SIN_SERVICIO)


def _flujo_aviso(texto: str):
    """Stream sintetico de un solo trozo, con el mismo tipo que devuelve openai."""
    trozo = ChatCompletionChunk(
        id="aviso-sin-servicio",
        object="chat.completion.chunk",
        created=0,
        model="aviso-local",
        choices=[Choice(index=0, delta=ChoiceDelta(role="assistant", content=texto), finish_reason="stop")],
    )

    async def generar():
        yield trozo

    g = generar()
    return _FlujoLimpio(g, g.__aiter__(), None, LimpiadorEtiquetas())


class FiltroEtiquetasModelo(BaseTextFilter):
    """Red de seguridad por frase antes del TTS (va ANTES de NemotronSpeechTextFilter)."""

    async def filter(self, text: str) -> str:
        text = _ETIQUETA_COMPLETA.sub("", text)
        text = _ETIQUETA_SIN_APERTURA.sub("", text)
        text = _NARRACION_HERRAMIENTA.sub("", text)
        return _CODIGO_EN_LINEA.sub(lambda m: m.group(1).replace("_", " "), text)

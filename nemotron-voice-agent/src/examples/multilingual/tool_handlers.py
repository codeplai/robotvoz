# SPDX-License-Identifier: BSD-2-Clause
"""Manejadores de herramientas del robot, con skills conmutables por voz.

Diseño
------
El esquema de herramientas que ve el modelo se fija al construir la pipeline y
no puede cambiar a mitad de sesión. Por eso se le ofrece la UNIÓN de todas las
herramientas de todos los skills, y el permiso real se aplica aquí: si el skill
activo no lista la herramienta en ``tools_available``, el manejador la rechaza
y se lo explica al modelo, que lo transmite al usuario.

El estado vive en una instancia de :class:`EstadoSkill` por sesión, creada en
``pipeline.py`` y capturada por clausura en los manejadores. Nada global, así
que dos sesiones simultáneas no se pisan.

Cambiar de skill reescribe el mensaje de sistema del contexto en caliente, sin
reconectar: se sustituye el primer mensaje de rol ``system`` por el contenido
del skill nuevo y se conserva el resto del historial.

Las acciones físicas hablan con el humanoide **Unitree G1** a través de
``g1_unitree.py``. Si el SDK no está instalado o ``G1_ENABLED`` no está activo,
esa capa responde en modo simulado y el agente de voz funciona igual.
"""

import re
import time
import unicodedata
from typing import Any, Callable

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

from examples.multilingual.g1_unitree import obtener_g1

# Nombre de skill (como lo dice el usuario) -> clave en prompts.yaml
SKILLS: dict[str, str] = {
    # Fisicos
    "recepcion": "skill_recepcion",
    "inspeccion": "skill_inspeccion",
    "mantenimiento": "skill_mantenimiento",
    # Conversacion, sentado
    "ia": "skill_charla_ia",
    "educacion": "skill_charla_educacion",
    "innovacion": "skill_charla_innovacion",
}

# Formas en que el modelo puede nombrar un modo aunque el enum pida el corto.
ALIAS: dict[str, str] = {
    "inteligencia artificial": "ia",
    "inteligencia_artificial": "ia",
}

# Postura en la que queda el robot tras cada accion de postura.
POSTURA_RESULTANTE: dict[str, str] = {
    "levantarse": "de_pie",
    "alto": "de_pie",
    "bajo": "de_pie",
    "sentarse": "sentado",
}


def _texto_mensaje(m) -> str:
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def _es_negativa(texto: str) -> bool:
    """Respuesta que NO autoriza a levantarse: "no", "mejor no", "quedate sentado"..."""
    t = _normalizar(texto)
    t = re.sub(r"[^a-z ]+", " ", t).strip()
    return bool(
        re.match(r"^(no|nunca|espera|mejor no|todavia no|aun no)\b", t)
        or re.search(r"\b(no te levantes|quedate sentado|sigue sentado|no te pares)\b", t)
    )


def _limpio(texto: str) -> str:
    t = _normalizar(texto)
    return re.sub(r"[^a-z ]+", " ", t).strip()


def _ordena_levantarse(texto: str) -> bool:
    """Orden EXPLICITA de ponerse de pie: "levantate", "puedes levantarte"...

    "Avanza un metro" a secas NO cuenta: fue justo el caso inseguro en que el
    modelo se levanto y anduvo sin preguntar.
    """
    t = _limpio(texto)
    if not t or _es_negativa(texto):
        return False
    return bool(re.search(
        r"\b(levantate|levantarte|levantese|levantarse|ponte de pie|ponerte de pie|parate|pararte|incorporate)\b", t
    ))


def _es_afirmativa(texto: str) -> bool:
    t = _limpio(texto)
    if not t or _es_negativa(texto):
        return False
    return bool(re.match(r"^(si|claro|dale|confirmo|adelante|de acuerdo|ok|okey|vale|por supuesto|hazlo|perfecto)\b", t))


def _pregunta_por_levantarse_o_moverse(texto: str) -> bool:
    t = _limpio(texto)
    return bool(re.search(r"(levant|de pie|mover|desplaz|avanz|andar|caminar|modo inspeccion)", t)) and "?" in texto


def _normalizar(nombre: str) -> str:
    """Minusculas y sin tildes: "Educación" -> "educacion"."""
    base = unicodedata.normalize("NFD", nombre.strip().lower())
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    return ALIAS.get(base, base)

# Herramientas de control que todo skill puede usar siempre.
SIEMPRE_PERMITIDAS = {"cambiar_skill", "listar_skills"}


# Ventana para confirmar una parada de emergencia. El primer intento solo
# arma la confirmación; el segundo debe llegar dentro de este margen.
EMERGENCIA_MIN_S = 2.0   # impide que el modelo encadene dos llamadas seguidas
EMERGENCIA_MAX_S = 60.0  # caduca si el usuario no responde

# Mismo patron para levantarse estando sentado.
LEVANTARSE_MIN_S = 2.0
LEVANTARSE_MAX_S = 60.0


class EstadoSkill:
    """Estado del skill activo para una sesión."""

    def __init__(self, clave_inicial: str, catalogo_prompts: dict):
        self.clave = clave_inicial
        self.catalogo = catalogo_prompts
        self.emergencia_armada_en: float | None = None
        self.levantarse_armado_en: float | None = None
        # Mensajes de usuario que habia en el contexto al armar: confirmar exige
        # que el usuario haya hablado DESPUES de que el robot preguntara.
        self.levantarse_armado_usuarios: int | None = None
        # Los fija pipeline.py al crear el contexto. Ver cambiar_skill().
        self.contexto = None            # LLMContext COMPARTIDO de la sesion
        self.prompt_actual: str | None = None
        self.renderizar = None          # añade el bloque de idioma fijo al prompt
        # "de_pie", "sentado", o None si se desconoce (al arrancar la sesion).
        self.postura_actual: str | None = None

    @property
    def nombre(self) -> str:
        for corto, clave in SKILLS.items():
            if clave == self.clave:
                return corto
        return self.clave

    def permitidas(self) -> set[str]:
        entrada = self.catalogo.get(self.clave) or {}
        return set(entrada.get("tools_available") or []) | SIEMPRE_PERMITIDAS

    def contenido(self, clave: str) -> str:
        entrada = self.catalogo.get(clave) or {}
        return str(entrada.get("content") or "")

    def entrada(self, clave: str) -> dict:
        e = self.catalogo.get(clave)
        return e if isinstance(e, dict) else {}


def _reescribir_prompt(context, anterior: str | None, nuevo: str) -> bool:
    """Sustituye el prompt vigente dentro del contexto y conserva el historial.

    Busca el mensaje cuyo contenido es exactamente el prompt vigente, porque su
    posicion depende del modelo: con system_prompt vacio (Qwen) va en el rol de
    sistema; con una directiva de control (p. ej. "/no_think" del Nemotron v2)
    va en el primer mensaje de usuario. Si no lo encuentra, usa el primer
    mensaje de sistema.
    """
    try:
        mensajes = list(context.get_messages())
    except Exception as exc:  # pragma: no cover
        logger.warning(f"No se pudo leer el contexto: {exc}")
        return False
    indice = None
    if anterior is not None:
        for i, m in enumerate(mensajes):
            if isinstance(m, dict) and m.get("content") == anterior:
                indice = i
                break
    if indice is None:
        for i, m in enumerate(mensajes):
            if isinstance(m, dict) and m.get("role") == "system":
                indice = i
                break
    if indice is None:
        mensajes.insert(0, {"role": "system", "content": nuevo})
    else:
        mensajes[indice] = {**mensajes[indice], "content": nuevo}
    try:
        context.set_messages(mensajes)
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning(f"No se pudo reescribir el contexto: {exc}")
        return False


def construir_manejadores(estado: EstadoSkill) -> dict[str, Callable]:
    """Devuelve los manejadores ligados a ``estado`` (uno por sesión)."""

    async def _denegar(params: FunctionCallParams, motivo: str):
        logger.info(f"[skill={estado.nombre}] '{params.function_name}' denegada: {motivo}")
        await params.result_callback({"permitido": False, "motivo": motivo})

    def con_permiso(fn):
        """Envuelve un manejador aplicando el permiso del skill activo."""

        async def envuelto(params: FunctionCallParams):
            if params.function_name not in estado.permitidas():
                return await _denegar(
                    params,
                    f"El modo '{estado.nombre}' no permite '{params.function_name}'. "
                    f"Cambia de modo primero con cambiar_skill.",
                )
            return await fn(params)

        return envuelto

    # --- control de skills -------------------------------------------------

    def _requiere_levantarse(clave: str) -> bool:
        return any(
            isinstance(a, dict) and str(a.get("postura", "")).lower() == "levantarse"
            for a in estado.entrada(clave).get("al_entrar") or []
        )

    def _usuarios_en_contexto() -> tuple[int | None, str]:
        """(numero de mensajes de usuario, texto del ultimo) en el contexto COMPARTIDO."""
        if estado.contexto is None:
            return None, ""
        try:
            mensajes = [m for m in estado.contexto.get_messages() if isinstance(m, dict) and m.get("role") == "user"]
        except Exception:  # pragma: no cover
            return None, ""
        return len(mensajes), (_texto_mensaje(mensajes[-1]) if mensajes else "")

    def _confirmado_en_este_turno() -> str | None:
        """Motivo si la frase actual del usuario YA autoriza levantarse, o None.

        Cubre los dos flujos naturales en que el robot pregunta en texto, sin
        llamar a ninguna herramienta, y el "si" llega ANTES de armar: el usuario
        da una orden explicita de levantarse, o responde afirmativamente a una
        pregunta del robot sobre levantarse o moverse.
        """
        if estado.contexto is None:
            return None
        try:
            mensajes = [m for m in estado.contexto.get_messages() if isinstance(m, dict)]
        except Exception:  # pragma: no cover
            return None
        i_user = next((i for i in range(len(mensajes) - 1, -1, -1) if mensajes[i].get("role") == "user"), None)
        if i_user is None:
            return None
        ultimo = _texto_mensaje(mensajes[i_user])
        if _ordena_levantarse(ultimo):
            return f"orden explicita del usuario: {ultimo!r}"
        if _es_afirmativa(ultimo):
            previo = next(
                (_texto_mensaje(m) for m in reversed(mensajes[:i_user])
                 if m.get("role") == "assistant" and _texto_mensaje(m).strip()),
                "",
            )
            if _pregunta_por_levantarse_o_moverse(previo):
                return f"el usuario respondio {ultimo!r} a {previo[-80:]!r}"
        return None

    def _armar_levantarse(que: str) -> None:
        """Arma la confirmacion en cuanto hay INTENCION de moverse estando sentado.

        Sin esto, el modelo preguntaba en texto sin llamar a ninguna herramienta;
        el "si" del usuario llegaba como PRIMERA llamada y el codigo volvia a
        pedir confirmacion: el usuario tenia que confirmar dos veces.
        """
        armado = estado.levantarse_armado_en
        if armado is not None and time.monotonic() - armado <= LEVANTARSE_MAX_S:
            # NO rearmar: rearmar actualizaba el recuento de mensajes al actual y
            # borraba que el usuario ya habia respondido, obligandole a confirmar
            # dos veces (visto en prueba: "si" -> cambio a inspeccion -> rearme).
            logger.info(f"[G1] levantarse ya estaba armado; se conserva ({que})")
            return
        estado.levantarse_armado_en = time.monotonic()
        estado.levantarse_armado_usuarios, _ = _usuarios_en_contexto()
        logger.warning(f"[G1] levantarse ARMADO ({que}); falta confirmacion del usuario")

    def _estado_confirmacion() -> str:
        """'sin_armar', 'esperando', 'confirmada' o 'rechazada'."""
        if _confirmado_en_este_turno():
            return "confirmada"
        armado = estado.levantarse_armado_en
        if armado is None or time.monotonic() - armado > LEVANTARSE_MAX_S:
            return "sin_armar"
        n, ultimo = _usuarios_en_contexto()
        if n is None or estado.levantarse_armado_usuarios is None or n <= estado.levantarse_armado_usuarios:
            return "esperando"
        return "rechazada" if _es_negativa(ultimo) else "confirmada"

    async def _confirmar_levantarse(params: FunctionCallParams, clave: str | None, que: str) -> bool:
        """Levantarse desde sentado exige confirmacion del usuario, en codigo.

        Motivo: en una prueba por voz, sentado y ante "avanza un metro", el modelo
        cambio por su cuenta a inspeccion, se levanto y anduvo sin preguntar,
        pese a que el prompt le ordenaba explicarlo primero.

        Confirmar exige que el usuario haya hablado DESPUES de armar (se cuenta en
        el historial real de la conversacion), dentro de LEVANTARSE_MAX_S, y que
        esa respuesta no sea una negativa. Asi el modelo no puede encadenar la
        pregunta y la accion sin pasar por el usuario. Devuelve True si se puede
        continuar.
        """
        if estado.postura_actual != "sentado":
            return True
        if clave is not None and not _requiere_levantarse(clave):
            return True
        motivo = _confirmado_en_este_turno()
        if motivo:
            estado.levantarse_armado_en = None
            logger.info(f"[G1] levantarse CONFIRMADO ({que}): {motivo}")
            return True
        ahora = time.monotonic()
        armado = estado.levantarse_armado_en
        if armado is None or ahora - armado > LEVANTARSE_MAX_S:
            _armar_levantarse(que)
            await params.result_callback(
                {
                    "ok": False,
                    "requiere_confirmacion": True,
                    "aviso": f"Estoy sentado. Para {que} tengo que ponerme de pie.",
                    "instruccion": "Explica al usuario que tienes que levantarte y preguntale si lo "
                    "confirma. Solo si responde que si, vuelve a llamar a esta herramienta.",
                }
            )
            return False
        n, ultimo = _usuarios_en_contexto()
        if n is None:
            # Sin contexto compartido no se pueden contar turnos: margen temporal.
            if ahora - armado < LEVANTARSE_MIN_S:
                await params.result_callback({"ok": False, "requiere_confirmacion": True,
                    "motivo": "Hay que preguntar al usuario y esperar su respuesta."})
                return False
        elif estado.levantarse_armado_usuarios is not None and n <= estado.levantarse_armado_usuarios:
            logger.warning("[G1] levantarse: el usuario aun no ha respondido desde que se pregunto")
            await params.result_callback({"ok": False, "requiere_confirmacion": True,
                "motivo": "El usuario aun no ha respondido. Preguntale si confirma y espera su respuesta."})
            return False
        if ultimo and _es_negativa(ultimo):
            estado.levantarse_armado_en = None
            logger.info(f"[G1] levantarse RECHAZADO por el usuario: {ultimo!r}")
            await params.result_callback({"ok": False, "motivo": "El usuario no lo ha confirmado. Sigues sentado."})
            return False
        estado.levantarse_armado_en = None
        logger.info(f"[G1] levantarse CONFIRMADO ({que}) tras respuesta del usuario: {ultimo!r}")
        return True

    async def _ejecutar_al_entrar(clave: str) -> list[dict]:
        """Acciones fisicas declaradas en prompts.yaml (al_entrar) para un modo.

        Se ejecutan en CODIGO al entrar, no se dejan al criterio del modelo: ya
        se vio que el modelo se salta reglas del prompt. Una postura solo se
        aplica si difiere de la ultima conocida, para no repetir "sentarse" al
        pasar de un tema de conversacion a otro.
        """
        hechas: list[dict] = []
        for accion in estado.entrada(clave).get("al_entrar") or []:
            if not isinstance(accion, dict):
                continue
            if "postura" in accion:
                objetivo = str(accion["postura"]).lower()
                resultante = POSTURA_RESULTANTE.get(objetivo)
                if resultante and estado.postura_actual == resultante:
                    hechas.append({"postura": objetivo, "omitida": f"ya estaba {resultante}"})
                    continue
                r = obtener_g1().postura(objetivo)
                if r.get("ok"):
                    estado.postura_actual = resultante
                hechas.append({"postura": objetivo, **r})
                logger.info(f"[G1] al entrar en {clave}: postura {objetivo} -> {r}")
            elif "gesto" in accion:
                cual = str(accion["gesto"]).lower()
                r = obtener_g1().gesto(cual)
                hechas.append({"gesto": cual, **r})
                logger.info(f"[G1] al entrar en {clave}: gesto {cual} -> {r}")
        return hechas

    async def cambiar_skill(params: FunctionCallParams):
        destino = _normalizar(str((params.arguments or {}).get("skill", "")))
        if destino not in SKILLS:
            # Argumento corrupto del modelo, visto con Nemotron Super en la nube:
            # "recepcion>\nrecepcion". Si dentro aparece UN solo modo valido, se usa.
            candidatos = {
                _normalizar(t) for t in re.findall(r"[a-zA-Z\u00c0-\u017f]+", destino)
            } & set(SKILLS)
            if len(candidatos) == 1:
                logger.warning(f"cambiar_skill: argumento {destino!r} interpretado como {next(iter(candidatos))!r}")
                destino = candidatos.pop()
        if destino not in SKILLS:
            # Instruccion dirigida al MODELO, no al usuario. Con solo el error, Super
            # respondio al usuario "debe especificar exactamente el nombre del modo"
            # tras mandar el mismo cambiar_skill con argumentos vacios.
            await params.result_callback(
                {
                    "ok": False,
                    "error": f"Modo desconocido o vacio: {destino!r}",
                    "disponibles": list(SKILLS),
                    "instruccion": (
                        "El fallo es tuyo, no del usuario: no se lo atribuyas. Vuelve a invocar "
                        "cambiar_skill ahora con el parametro skill igual a uno de los modos "
                        "disponibles, el que corresponda a lo que pidio el usuario."
                    ),
                }
            )
            return
        clave = SKILLS[destino]
        texto = estado.contenido(clave)
        if not texto:
            await params.result_callback({"ok": False, "error": f"El modo '{destino}' no tiene prompt definido"})
            return
        if not await _confirmar_levantarse(params, clave, f"pasar al modo {destino}"):
            return
        anterior = estado.nombre
        estado.clave = clave
        # OJO: hay que reescribir el contexto COMPARTIDO de la sesion, no
        # params.context. PerTurnReminderProcessor reenvia al modelo una COPIA
        # nueva del contexto en cada turno, y esa copia es la que llega aqui:
        # reescribirla no tenia efecto y el modelo seguia con el prompt inicial
        # (verificado en los registros: seis llamadas seguidas con el prompt de
        # recepcion tras "cambiar" a inspeccion).
        contenido = estado.renderizar(texto) if estado.renderizar else texto
        contexto = estado.contexto if estado.contexto is not None else params.context
        reescrito = _reescribir_prompt(contexto, estado.prompt_actual, contenido)
        if reescrito:
            estado.prompt_actual = contenido
        logger.info(
            f"Skill: {anterior} -> {destino} (contexto compartido: {estado.contexto is not None}, "
            f"reescrito: {reescrito})"
        )
        acciones = await _ejecutar_al_entrar(clave)
        aviso_postura = None
        if estado.postura_actual == "sentado" and "mover" in (estado.entrada(clave).get("tools_available") or []):
            _armar_levantarse(f"moverme en modo {destino}")
            conf = _estado_confirmacion()
            if conf == "confirmada":
                # El usuario ya dijo que si: no volver a preguntar. Se le indica al
                # modelo que se levante; no se levanta desde el codigo (el usuario
                # pidio no forzar la postura).
                aviso_postura = (
                    "El usuario YA ha confirmado que te levantes. No vuelvas a preguntar: llama "
                    "ahora a postura con accion levantarse y despues haz lo que pidio."
                )
            elif conf == "rechazada":
                aviso_postura = "El usuario ha dicho que no te levantes. Sigues sentado: no te muevas."
            else:
                aviso_postura = (
                    "Estas sentado: para moverte tendras que levantarte. Si el usuario quiere que "
                    "te muevas, preguntale si confirma que te levantes; cuando responda que si, "
                    "llama a postura con accion levantarse."
                )
        # El modelo no debe afirmar un cambio de postura que no ocurrio (dijo "me
        # he sentado" al pasar de un tema a otro ya estando sentado).
        hechas = {a.get("postura") for a in acciones if a.get("postura") and a.get("ok") and not a.get("omitida")}
        instruccion = estado.entrada(clave).get("instruccion_entrada") or "Confirma el cambio al usuario en una frase corta."
        if "sentarse" in hechas:
            instruccion += " Te acabas de sentar: puedes mencionarlo."
        elif "levantarse" in hechas:
            instruccion += " Te acabas de poner de pie: puedes mencionarlo."
        else:
            instruccion += " No has cambiado de postura: no digas que te has sentado ni levantado."
        await params.result_callback(
            {
                "ok": True,
                "modo_anterior": anterior,
                "modo_actual": destino,
                "herramientas": sorted(estado.permitidas()),
                "acciones_al_entrar": acciones,
                "postura": estado.postura_actual or "desconocida",
                "instruccion": instruccion,
                **({"aviso_postura": aviso_postura} if aviso_postura else {}),
            }
        )

    async def listar_skills(params: FunctionCallParams):
        await params.result_callback(
            {
                "disponibles": list(SKILLS),
                "activo": estado.nombre,
                "herramientas_activas": sorted(estado.permitidas()),
            }
        )

    # --- acciones físicas sobre el Unitree G1 -----------------------------

    async def mover(params: FunctionCallParams):
        if estado.postura_actual == "sentado":
            _armar_levantarse(f"{params.function_name}")
            await params.result_callback(
                {
                    "ok": False,
                    "motivo": "Estoy sentado y no puedo moverme asi.",
                    "instruccion": "Pregunta al usuario si confirma que te levantes. Cuando responda "
                    "que si, llama a postura con accion levantarse.",
                }
            )
            return
        a = params.arguments or {}
        direccion = str(a.get("direccion", "")).lower()
        try:
            metros = float(a.get("metros", 0))
        except (TypeError, ValueError):
            await params.result_callback({"ok": False, "error": "metros debe ser un numero"})
            return
        if not 0.1 <= metros <= 10:
            await params.result_callback({"ok": False, "error": "La distancia debe estar entre 0.1 y 10 metros"})
            return
        g1 = obtener_g1()
        # vx adelante/atras, vy lateral (positivo a la izquierda en el SDK)
        ejes = {
            "adelante": (g1.vel_lineal, 0.0),
            "atras": (-g1.vel_lineal, 0.0),
            "izquierda": (0.0, g1.vel_lineal),
            "derecha": (0.0, -g1.vel_lineal),
        }
        if direccion not in ejes:
            await params.result_callback({"ok": False, "error": f"Direccion desconocida: {direccion}"})
            return
        vx, vy = ejes[direccion]
        r = g1.mover(vx, vy, metros)
        logger.info(f"[G1] mover {direccion} {metros} m -> {r}")
        await params.result_callback({"ok": True, "direccion": direccion, "metros": metros, **r})

    async def girar(params: FunctionCallParams):
        if estado.postura_actual == "sentado":
            _armar_levantarse(f"{params.function_name}")
            await params.result_callback(
                {
                    "ok": False,
                    "motivo": "Estoy sentado y no puedo moverme asi.",
                    "instruccion": "Pregunta al usuario si confirma que te levantes. Cuando responda "
                    "que si, llama a postura con accion levantarse.",
                }
            )
            return
        try:
            grados = float((params.arguments or {}).get("grados", 0))
        except (TypeError, ValueError):
            await params.result_callback({"ok": False, "error": "grados debe ser un numero"})
            return
        if abs(grados) > 360:
            await params.result_callback({"ok": False, "error": "El giro no puede superar 360 grados"})
            return
        r = obtener_g1().girar(grados)
        logger.info(f"[G1] girar {grados} grados -> {r}")
        await params.result_callback({"ok": True, "grados": grados, **r})

    async def detener(params: FunctionCallParams):
        r = obtener_g1().detener()
        logger.info(f"[G1] detener -> {r}")
        await params.result_callback({"ok": True, "estado": "detenido", **r})

    async def parada_emergencia(params: FunctionCallParams):
        """Damp(): el robot queda flacido y SE CAE si esta de pie.

        Confirmacion en DOS FASES, impuesta aqui y no por el prompt. El
        argumento ``confirmado`` que manda el modelo se IGNORA a proposito:
        en pruebas, el modelo lo ponia a true por su cuenta sin que el usuario
        hubiera confirmado nada. Con un bipedo eso es una caida no autorizada.

        La primera llamada solo arma la confirmacion y devuelve el aviso. La
        segunda ejecuta, pero solo si han pasado al menos EMERGENCIA_MIN_S
        (para que el modelo no pueda encadenar dos llamadas en el mismo turno,
        sin pasar por el usuario) y menos de EMERGENCIA_MAX_S.
        """
        ahora = time.monotonic()
        armada = estado.emergencia_armada_en
        transcurrido = None if armada is None else ahora - armada

        if transcurrido is None or transcurrido > EMERGENCIA_MAX_S:
            estado.emergencia_armada_en = ahora
            logger.warning("[G1] parada de emergencia ARMADA; falta confirmacion del usuario")
            await params.result_callback(
                {
                    "ok": False,
                    "requiere_confirmacion": True,
                    "aviso": "La parada de emergencia deja el robot flacido y CAERA si esta de pie.",
                    "instruccion": "Pregunta al usuario en voz alta si confirma. Solo si responde que "
                    "si, vuelve a llamar a esta herramienta.",
                }
            )
            return

        if transcurrido < EMERGENCIA_MIN_S:
            logger.warning(f"[G1] segunda llamada demasiado rapida ({transcurrido:.1f}s); se rechaza")
            await params.result_callback(
                {
                    "ok": False,
                    "requiere_confirmacion": True,
                    "motivo": "Llamada demasiado seguida. Hay que preguntar al usuario primero.",
                }
            )
            return

        estado.emergencia_armada_en = None
        r = obtener_g1().parada_emergencia()
        logger.warning(f"[G1] PARADA DE EMERGENCIA EJECUTADA -> {r}")
        await params.result_callback({"ok": True, "estado": "flacido", **r})

    async def postura(params: FunctionCallParams):
        accion = str((params.arguments or {}).get("accion", "")).lower()
        if accion == "levantarse" and not await _confirmar_levantarse(params, None, "levantarme"):
            return
        r = obtener_g1().postura(accion)
        if r.get("ok") and accion in POSTURA_RESULTANTE:
            estado.postura_actual = POSTURA_RESULTANTE[accion]
        logger.info(f"[G1] postura {accion} -> {r}")
        await params.result_callback(r)

    async def gesto(params: FunctionCallParams):
        cual = str((params.arguments or {}).get("cual", "")).lower()
        r = obtener_g1().gesto(cual)
        logger.info(f"[G1] gesto {cual} -> {r}")
        await params.result_callback(r)

    async def leer_sensor(params: FunctionCallParams):
        sensor = str((params.arguments or {}).get("sensor", "")).lower()
        # TODO: leer del topico LowState del G1 por DDS. Valores simulados
        # mientras tanto; ver g1_unitree.py para el patron de conexion.
        simulados: dict[str, Any] = {
            "bateria": {"valor": 78, "unidad": "%"},
            "distancia": {"valor": 1.4, "unidad": "m"},
            "temperatura": {"valor": 41.2, "unidad": "C"},
            "inclinacion": {"valor": 0.8, "unidad": "grados"},
        }
        if sensor not in simulados:
            await params.result_callback({"ok": False, "error": f"Sensor desconocido: {sensor}"})
            return
        await params.result_callback({"ok": True, "sensor": sensor, **simulados[sensor], "simulado": True})

    async def reportar_incidencia(params: FunctionCallParams):
        a = params.arguments or {}
        desc = str(a.get("descripcion", "")).strip()
        grav = str(a.get("gravedad", "")).lower()
        if not desc:
            await params.result_callback({"ok": False, "error": "Falta la descripcion"})
            return
        logger.warning(f"[INCIDENCIA:{grav}] {desc}")
        await params.result_callback({"ok": True, "registrada": True, "gravedad": grav})

    return {
        "cambiar_skill": cambiar_skill,
        "listar_skills": listar_skills,
        "mover": con_permiso(mover),
        "girar": con_permiso(girar),
        "detener": con_permiso(detener),
        "parada_emergencia": con_permiso(parada_emergencia),
        "postura": con_permiso(postura),
        "gesto": con_permiso(gesto),
        "leer_sensor": con_permiso(leer_sensor),
        "reportar_incidencia": con_permiso(reportar_incidencia),
    }

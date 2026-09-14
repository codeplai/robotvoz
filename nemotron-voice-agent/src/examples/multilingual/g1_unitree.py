# SPDX-License-Identifier: BSD-2-Clause
"""Capa de control del humanoide Unitree G1.

Aísla el SDK de Unitree del resto de la pipeline. Si el SDK no está instalado
o el robot no responde, ``G1.disponible`` queda en False y los manejadores
siguen funcionando en modo simulado, así que el agente de voz arranca igual.

Puesta en marcha
----------------
1. Instalar el SDK (NO está en PyPI con soporte de G1; hay que clonarlo):

       git clone https://github.com/unitreerobotics/unitree_sdk2_python.git
       cd unitree_sdk2_python && pip3 install -e .

   Si se queja de cyclonedds, compilar CycloneDDS 0.10.x y exportar
   CYCLONEDDS_HOME antes de instalar.

2. Red: el PC de a bordo del G1 está en 192.168.123.161. Poner la interfaz de
   este equipo en la misma subred, por ejemplo 192.168.123.99.

3. Variables de entorno (ver docker-compose.override.yml):

       G1_ENABLED=true
       G1_IFACE=eth0          # interfaz conectada al robot
       G1_VEL_LINEAL=0.3      # m/s al andar
       G1_VEL_ANGULAR=0.5     # rad/s al girar

Seguridad
---------
- La distancia se convierte a tiempo: duracion = metros / velocidad. El G1 se
  controla por VELOCIDAD, no por posición, así que no hay garantía de recorrer
  la distancia exacta. Trátalo como aproximado.
- ``parada_emergencia()`` llama a Damp(), que deja el robot flácido. Úsalo solo
  si el robot está sujeto o sentado: si está de pie, SE CAE.
- ``detener()`` es la parada normal y segura: StopMove().
"""

import os
import threading
import time

from loguru import logger


def _bool_env(nombre: str, por_defecto: bool = False) -> bool:
    v = os.getenv(nombre)
    return por_defecto if v is None else v.strip().lower() in ("1", "true", "yes", "si", "sí")


def _float_env(nombre: str, por_defecto: float) -> float:
    try:
        return float(os.getenv(nombre, por_defecto))
    except (TypeError, ValueError):
        return por_defecto


def _resultado(code, **extra) -> dict:
    """Normaliza el codigo de retorno del SDK. 0 es exito.

    Importa no mentir aqui: dar por ejecutado un movimiento que el robot nunca
    recibio es peor que fallar, porque el modelo se lo confirmaria al usuario.
    """
    ok = code == 0
    r = {"ok": ok, "simulado": False, **extra}
    if not ok:
        r["error"] = f"El robot rechazo la orden (codigo {code})"
        logger.warning(f"G1: orden rechazada, codigo {code}, {extra}")
    return r


class G1:
    """Fachada sobre LocoClient y AudioClient del G1."""

    def __init__(self):
        self.disponible = False
        self.motivo = "no inicializado"
        self._loco = None
        self._audio = None
        self._lock = threading.Lock()
        self.vel_lineal = _float_env("G1_VEL_LINEAL", 0.3)
        self.vel_angular = _float_env("G1_VEL_ANGULAR", 0.5)

        if not _bool_env("G1_ENABLED", False):
            self.motivo = "G1_ENABLED no está activo; modo simulado"
            logger.info(f"G1: {self.motivo}")
            return

        iface = os.getenv("G1_IFACE", "eth0")
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient

            ChannelFactoryInitialize(0, iface)

            self._loco = LocoClient()
            self._loco.SetTimeout(10.0)
            self._loco.Init()

            self._audio = AudioClient()
            self._audio.SetTimeout(10.0)
            self._audio.Init()

            # Init() NO comprueba que el robot esté ahí: se limita a preparar el
            # cliente. Sin esta sonda, la capa afirmaba estar conectada sin robot
            # y daba por ejecutados movimientos que nunca salieron.
            code, _ = self._loco.GetFsmId()
            if code != 0:
                self.motivo = f"el SDK carga pero el robot no responde por {iface} (código {code}); modo simulado"
                logger.warning(f"G1: {self.motivo}")
                self._loco = self._audio = None
                return

            self.disponible = True
            self.motivo = f"conectado por {iface}"
            logger.info(f"G1: {self.motivo} (v={self.vel_lineal} m/s, w={self.vel_angular} rad/s)")
        except ImportError as exc:
            self.motivo = f"SDK de Unitree no instalado ({exc}); modo simulado"
            logger.warning(f"G1: {self.motivo}")
        except Exception as exc:
            self.motivo = f"no se pudo conectar por {iface}: {exc}; modo simulado"
            logger.warning(f"G1: {self.motivo}")

    # --- locomoción --------------------------------------------------------

    def mover(self, vx: float, vy: float, metros: float) -> dict:
        """Desplaza una distancia aproximada. El G1 va por velocidad, no por posición."""
        duracion = abs(metros) / max(self.vel_lineal, 0.01)
        if not self.disponible:
            return {"simulado": True, "vx": vx, "vy": vy, "duracion_s": round(duracion, 2)}
        with self._lock:
            code = self._loco.SetVelocity(vx, vy, 0.0, duracion)
        return _resultado(code, vx=vx, vy=vy, duracion_s=round(duracion, 2))

    def girar(self, grados: float) -> dict:
        import math

        rad = math.radians(abs(grados))
        duracion = rad / max(self.vel_angular, 0.01)
        # En el SDK, omega positivo gira a la izquierda (antihorario).
        omega = -self.vel_angular if grados > 0 else self.vel_angular
        if not self.disponible:
            return {"simulado": True, "grados": grados, "duracion_s": round(duracion, 2)}
        with self._lock:
            code = self._loco.SetVelocity(0.0, 0.0, omega, duracion)
        return _resultado(code, grados=grados, duracion_s=round(duracion, 2))

    def detener(self) -> dict:
        if not self.disponible:
            return {"simulado": True}
        with self._lock:
            code = self._loco.StopMove()
        return _resultado(code)

    def parada_emergencia(self) -> dict:
        """Damp(): deja el robot flácido. SE CAE si está de pie."""
        if not self.disponible:
            return {"simulado": True}
        with self._lock:
            code = self._loco.Damp()
        return _resultado(code)

    def postura(self, accion: str) -> dict:
        acciones = {
            "levantarse": "Squat2StandUp",
            "sentarse": "StandUp2Squat",
            "alto": "HighStand",
            "bajo": "LowStand",
        }
        metodo = acciones.get(accion)
        if metodo is None:
            return {"ok": False, "error": f"Postura desconocida: {accion}"}
        if not self.disponible:
            return {"ok": True, "simulado": True, "postura": accion}
        with self._lock:
            code = getattr(self._loco, metodo)()
        return _resultado(code, postura=accion)

    def gesto(self, cual: str) -> dict:
        if not self.disponible:
            return {"ok": True, "simulado": True, "gesto": cual}
        with self._lock:
            if cual == "saludar":
                code = self._loco.WaveHand(False)
            elif cual == "dar_la_mano":
                code = self._loco.ShakeHand()
            else:
                return {"ok": False, "error": f"Gesto desconocido: {cual}"}
        return _resultado(code, gesto=cual)

    # --- audio y luces -----------------------------------------------------

    def reproducir_pcm(self, pcm: bytes, nombre: str = "voz") -> dict:
        """Envía PCM 16 bits, 16 kHz, mono al altavoz del G1.

        Es exactamente el formato que produce Magpie TTS y el que usa la
        pipeline, así que no hace falta remuestrear.
        """
        if not self.disponible:
            return {"simulado": True, "bytes": len(pcm)}
        trozo = 96000  # 3 s a 16 kHz, como el ejemplo del SDK
        stream_id = str(int(time.time() * 1000))
        enviados = 0
        with self._lock:
            for off in range(0, len(pcm), trozo):
                self._audio.PlayStream(nombre, stream_id, pcm[off : off + trozo])
                enviados += 1
                time.sleep(1.0)
        return {"simulado": False, "bytes": len(pcm), "trozos": enviados}

    def parar_audio(self, nombre: str = "voz") -> dict:
        if not self.disponible:
            return {"simulado": True}
        with self._lock:
            self._audio.PlayStop(nombre)
        return {"simulado": False}

    def led(self, r: int, g: int, b: int) -> dict:
        if not self.disponible:
            return {"simulado": True, "rgb": [r, g, b]}
        with self._lock:
            self._audio.LedControl(int(r), int(g), int(b))
        return {"simulado": False, "rgb": [r, g, b]}


_instancia: G1 | None = None


def obtener_g1() -> G1:
    """Devuelve la instancia compartida, creándola en el primer uso."""
    global _instancia
    if _instancia is None:
        _instancia = G1()
    return _instancia

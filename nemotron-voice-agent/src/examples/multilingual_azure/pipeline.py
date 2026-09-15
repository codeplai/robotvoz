# SPDX-License-Identifier: BSD-2-Clause
"""Multilingual cascaded pipeline on Azure: Azure Speech STT -> Azure OpenAI LLM -> Azure Speech TTS.

Variante de ``examples.multilingual`` (NVIDIA) para la propuesta de la seccion 14
del README: reconocimiento y voz con Azure Speech, modelo con Azure OpenAI,
region Brazil South por latencia desde Sudamerica.

Los skills y herramientas del robot (tool_handlers.py, tools.py) NO se
duplican: viven en ``examples.multilingual`` y este modulo los reutiliza tal
cual via ``_SKILLS_MODULE_FILE``. ``prompts.yaml`` y ``tools.yaml`` SI tienen
una copia en esta carpeta, porque ``examples_registry.py`` exige un
``prompts.yaml`` literal junto al modulo del ejemplo para poder resolver
``/api/deployment`` (metadata del prompt por defecto que el cliente pide al
arrancar, antes de cualquier sesion). La copia la mantiene al dia
``robot/09-servidor-arrancar.ps1`` en cada arranque; la fuente de verdad sigue
siendo ``examples/multilingual/{prompts,tools}.yaml``.

A diferencia de la version NVIDIA, el idioma de sesion no se descubre en
caliente (no hay prewarm de catalogo ni selector de idioma): queda fijo por
variable de entorno (``AZURE_SESSION_LANGUAGE``), igual que el robot lo usa
siempre en un solo idioma.
"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame, TTSUpdateSettingsFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.runner.types import RunnerArguments
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.azure.stt import AzureSTTService, AzureSTTSettings
from pipecat.services.azure.tts import AzureTTSService, AzureTTSSettings
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

import examples_registry
from examples.multilingual.multilingual_processor import (
    FIXED_SESSION_GREETING_TRIGGER,
    FIXED_SESSION_LANGUAGE_ADDON_KEY,
    PerTurnReminderProcessor,
    build_reminder,
    describe_language,
)
from examples.multilingual.tool_handlers import SKILLS, EstadoSkill, construir_manejadores
from examples.multilingual.tools import build_tools_schema, herramientas_de_todos_los_skills
from examples.shared.audio_recorder import create_audio_recorder
from examples.shared.pipeline_utils import (
    apply_pinned_prompt_summary,
    build_context_messages,
    build_smart_turn_stop_strategies,
    build_user_mute_strategies,
    create_transport,
)
from tracing import IS_TRACING_ENABLED
from utils import (
    load_prompt_catalog,
    normalize_lang_code,
    parse_env_bool,
    parse_env_float,
    render_prompt_addon,
    resolve_prompt,
)

load_dotenv(override=True)
CHAT_HISTORY_RECENT_TURNS = int(os.getenv("CHAT_HISTORY_RECENT_TURNS", "10"))

# Prompts/tools/skills viven junto a examples.multilingual.pipeline; se apunta
# ahi directamente para no duplicar prompts.yaml / tools.yaml.
_SKILLS_MODULE_FILE = Path(__file__).resolve().parent.parent / "multilingual" / "pipeline.py"


def _session_language() -> str:
    return normalize_lang_code(os.getenv("AZURE_SESSION_LANGUAGE", "es-PE").strip() or "es-PE")


def _build_user_aggregator_params(welcome_enabled: bool) -> LLMUserAggregatorParams:
    """Use VAD-only turn starts so interim ASR text does not start a user turn."""
    if not parse_env_bool("USE_SILERO_VAD_TURN_DETECTION", default=False):
        return LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            user_mute_strategies=build_user_mute_strategies(welcome_enabled),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=build_smart_turn_stop_strategies(),
            ),
        )

    stop_secs = parse_env_float("SILERO_VAD_STOP_SECS", 0.5, min_value=0.0)
    return LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=stop_secs)),
        user_mute_strategies=build_user_mute_strategies(welcome_enabled),
        user_turn_strategies=UserTurnStrategies(
            start=[VADUserTurnStartStrategy()],
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.0)],
        ),
    )


async def bot(runner_args: RunnerArguments) -> None:
    """Build and run the multilingual Azure cascaded pipeline for a single session."""
    transport = create_transport(runner_args)
    body = runner_args.body if isinstance(runner_args.body, dict) else {}
    welcome_enabled = examples_registry.welcome_message_enabled(body.get("pipeline_mode", ""))
    prompt_key, base_system_content = resolve_prompt(
        _SKILLS_MODULE_FILE,
        body.get("prompt_content", ""),
        body.get("prompt_key", ""),
    )
    logger.info(f"Starting multilingual Azure cascaded pipeline (prompt={prompt_key})")

    session_language = _session_language()

    # --- ASR (Azure Speech) ---
    speech_key = os.getenv("AZURE_SPEECH_API_KEY", "").strip()
    speech_region = os.getenv("AZURE_SPEECH_REGION", "brazilsouth").strip()
    if not speech_key:
        raise RuntimeError("AZURE_SPEECH_API_KEY no esta configurada en .env")

    stt = AzureSTTService(
        api_key=speech_key,
        region=speech_region,
        sample_rate=16000,
        settings=AzureSTTSettings(language=session_language),
    )
    logger.info(f"ASR: Azure Speech region={speech_region}, language={session_language}")

    # --- LLM (Azure OpenAI, API v1) ---
    # La API v1 de Azure OpenAI (openai_endpoint termina en /openai/v1, la que
    # da el portal de Foundry) es compatible con el cliente OpenAI normal:
    # base_url + api_key + el nombre del DEPLOYMENT como "model". No hace
    # falta api_version ni el AzureLLMService de pipecat, que es para el
    # endpoint clasico (sin /openai/v1) con api_version mensual.
    openai_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    openai_deployment = body.get("model_id", "") or os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
    if not openai_key or not openai_endpoint:
        raise RuntimeError("AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT no configuradas en .env")

    system_prompt = body.get("system_prompt", "")
    # Modelos de razonamiento (gpt-5.x, o-series) piensan en segundo plano antes
    # de responder: medido con gpt-5-mini, "minimal" baja de 128 a 0 tokens de
    # razonamiento y de 1.47s a 0.15s de motor para un turno trivial. Mismo
    # problema que el enable_thinking de Nemotron (ver README, seccion 4.2),
    # critico en un robot. Vacio (deployment sin razonamiento, p.ej. gpt-4.1-mini)
    # desactiva el parametro, porque esos modelos lo rechazan con error 400.
    reasoning_effort = os.getenv("AZURE_OPENAI_REASONING_EFFORT", "minimal").strip()
    logger.info(
        f"LLM: Azure OpenAI deployment={openai_deployment}, endpoint={openai_endpoint}, "
        f"reasoning_effort={reasoning_effort or '(none)'}"
    )

    llm = OpenAILLMService(
        api_key=openai_key,
        base_url=openai_endpoint,
        settings=OpenAILLMService.Settings(
            model=openai_deployment,
            extra={"reasoning_effort": reasoning_effort} if reasoning_effort else {},
        ),
    )

    # --- Skills del robot (compartidos con la version NVIDIA) -------------
    estado_skill = EstadoSkill(
        prompt_key if prompt_key in SKILLS.values() else next(iter(SKILLS.values())),
        load_prompt_catalog(_SKILLS_MODULE_FILE),
    )
    manejadores = construir_manejadores(estado_skill)
    tools_schema, herramientas_registradas = build_tools_schema(
        _SKILLS_MODULE_FILE, herramientas_de_todos_los_skills(_SKILLS_MODULE_FILE), manejadores
    )
    if tools_schema is not None:
        for nombre in herramientas_registradas:
            llm.register_function(nombre, manejadores[nombre])
        logger.info(
            f"Skills activos: {list(SKILLS)} | inicial={estado_skill.nombre} | "
            f"herramientas={herramientas_registradas}"
        )
    else:
        logger.info("Sin herramientas: el robot no podra ejecutar acciones ni cambiar de skill por voz")

    # LLM auxiliar para el resumen de historial (ver apply_pinned_prompt_summary).
    # Corre en segundo plano tras cada turno, fuera de la ruta de voz: no hay
    # motivo para bajarle el razonamiento, y mas razonamiento da un resumen mas
    # fiel (mismo criterio que with_reasoning(..., True) en la version NVIDIA).
    summary_llm = OpenAILLMService(
        api_key=openai_key,
        base_url=openai_endpoint,
        model=openai_deployment,
    )

    # --- TTS (Azure Speech) ---
    tts_voice = body.get("tts_voice_id", "") or os.getenv("AZURE_TTS_VOICE", "es-PE-AlexNeural")
    tts = AzureTTSService(
        api_key=speech_key,
        region=speech_region,
        sample_rate=16000,
        settings=AzureTTSSettings(voice=tts_voice, language=session_language),
    )
    logger.info(f"TTS: Azure Speech voice={tts_voice}, language={session_language}")

    # --- Context ---
    prompt_catalog = load_prompt_catalog(_SKILLS_MODULE_FILE)
    base_system_content = render_prompt_addon(
        base_system_content,
        prompt_catalog,
        FIXED_SESSION_LANGUAGE_ADDON_KEY,
        {"fixed_language_name": describe_language(session_language)},
    )

    messages = build_context_messages(base_system_content, system_prompt)
    if tools_schema is not None:
        context = LLMContext(messages, tools=tools_schema, tool_choice="auto")
    else:
        context = LLMContext(messages)
    preserve_prompt_messages = len(messages)

    # El cambio de skill por voz reescribe este contexto compartido (ver
    # examples.multilingual.tool_handlers.cambiar_skill).
    estado_skill.contexto = context
    estado_skill.prompt_actual = base_system_content
    estado_skill.renderizar = lambda contenido: render_prompt_addon(
        contenido,
        prompt_catalog,
        FIXED_SESSION_LANGUAGE_ADDON_KEY,
        {"fixed_language_name": describe_language(session_language)},
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=_build_user_aggregator_params(welcome_enabled),
    )
    logger.info(
        f"Chat history summarization enabled: recent_turns={CHAT_HISTORY_RECENT_TURNS}, "
        f"preserve_prompt_messages={preserve_prompt_messages}"
    )

    reminder_processor = PerTurnReminderProcessor(build_reminder(session_language))

    audio_recorder = create_audio_recorder()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            reminder_processor,
            llm,
            tts,
            transport.output(),
            *([audio_recorder] if audio_recorder else []),
            assistant_aggregator,
        ]
    )

    latency_observer = UserBotLatencyObserver()
    summary_lock = asyncio.Lock()

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        async with summary_lock:
            await apply_pinned_prompt_summary(
                context=context,
                llm=summary_llm,
                preserve_prompt_messages=preserve_prompt_messages,
                recent_turns=CHAT_HISTORY_RECENT_TURNS,
                summary_system_prompt=system_prompt,
            )

    @latency_observer.event_handler("on_first_bot_speech_latency")
    async def on_first_bot_speech(observer, latency):
        logger.info(f"First bot speech latency: {latency:.3f}s")
        await task.queue_frame(
            RTVIServerMessageFrame(
                data={"type": "user-bot-latency", "latency": round(latency, 3), "first": True}
            )
        )

    @latency_observer.event_handler("on_latency_measured")
    async def on_latency(observer, latency):
        logger.info(f"User→Bot latency: {latency:.3f}s")
        await task.queue_frame(
            RTVIServerMessageFrame(
                data={"type": "user-bot-latency", "latency": round(latency, 3), "first": False}
            )
        )

    @latency_observer.event_handler("on_latency_breakdown")
    async def on_breakdown(observer, breakdown):
        events = breakdown.chronological_events()
        await task.queue_frame(
            RTVIServerMessageFrame(
                data={
                    "type": "latency-breakdown",
                    "vad_smart_turn": round(breakdown.user_turn_secs, 3)
                    if breakdown.user_turn_secs is not None
                    else None,
                    "events": events,
                }
            )
        )
        if events:
            logger.info(f"Latency breakdown: {' | '.join(events)}")

    task = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        observers=[latency_observer],
        enable_tracing=IS_TRACING_ENABLED,
    )

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        await task.queue_frame(
            RTVIServerMessageFrame(
                data={
                    "type": "user-turn-finalized",
                    "timestamp": getattr(message, "timestamp", None),
                    "transcript": getattr(message, "content", None),
                    "user_id": getattr(message, "user_id", None),
                }
            )
        )

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_connected(rtvi):
        logger.info("Client connected")
        if audio_recorder:
            await audio_recorder.start_recording()
        if not welcome_enabled:
            logger.info("Welcome message disabled; waiting for the user to speak first")
            return
        context.add_message({"role": "user", "content": FIXED_SESSION_GREETING_TRIGGER})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    @task.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, message):
        payload = message.data if isinstance(message.data, dict) else {}
        if message.type == "set-voice":
            voice_id = payload.get("voice_id", "")
            language = payload.get("language", "")
            if not voice_id:
                return
            settings_kwargs: dict = {"voice": voice_id}
            if language:
                settings_kwargs["language"] = normalize_lang_code(language)
            await task.queue_frame(
                TTSUpdateSettingsFrame(
                    delta=AzureTTSSettings(**settings_kwargs),
                    service=tts,
                )
            )
            logger.info(f"Voice switched → {voice_id}, language={settings_kwargs.get('language', '(unchanged)')}")

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(task)
    await runner.run()

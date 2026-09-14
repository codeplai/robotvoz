# SPDX-License-Identifier: BSD-2-Clause
"""Construccion del esquema de herramientas para la pipeline multilingue.

A diferencia del ejemplo generico, aqui el esquema contiene la UNION de las
herramientas de TODOS los skills, no solo las del skill inicial. El motivo es
que el esquema se fija al construir la pipeline y no puede cambiar a mitad de
sesion; si solo se enviaran las del skill inicial, el robot no podria ejecutar
nada tras cambiar de modo por voz.

El permiso real lo aplica tool_handlers.con_permiso() segun el skill activo.
"""

from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema

from examples.multilingual.tool_handlers import SKILLS
from utils import load_prompt_catalog, load_tools_catalog


def herramientas_de_todos_los_skills(module_file: str | Path) -> list[str]:
    """Union de tools_available de cada skill declarado en prompts.yaml."""
    catalogo = load_prompt_catalog(module_file)
    nombres: list[str] = []
    for clave in SKILLS.values():
        entrada = catalogo.get(clave)
        if not isinstance(entrada, dict):
            logger.warning(f"Skill '{clave}' ausente de prompts.yaml; se ignora")
            continue
        for n in entrada.get("tools_available") or []:
            if n not in nombres:
                nombres.append(n)
    return nombres


def build_tools_schema(
    module_file: str | Path, tool_names: list[str], handlers: dict
) -> tuple[ToolsSchema | None, list[str]]:
    """Arma el esquema con las entradas validas de tools.yaml.

    Una herramienta entra solo si (a) existe en el YAML, (b) su function.name
    coincide con la clave, y (c) hay un manejador registrado. Asi el modelo
    nunca ve una herramienta que la pipeline no pueda ejecutar.
    """
    if not tool_names:
        return None, []

    catalogo = load_tools_catalog(module_file)
    if not catalogo:
        logger.warning("No se encontro tools.yaml; herramientas desactivadas")
        return None, []

    entradas: list[dict] = []
    registradas: list[str] = []
    for nombre in tool_names:
        entrada = catalogo.get(nombre)
        if not isinstance(entrada, dict):
            logger.warning(f"Herramienta '{nombre}' ausente de tools.yaml; se omite")
            continue
        fn = entrada.get("function") if isinstance(entrada.get("function"), dict) else {}
        if fn.get("name") != nombre:
            logger.warning(f"Herramienta '{nombre}' con function.name={fn.get('name')!r} distinto; se omite")
            continue
        if nombre not in handlers:
            logger.warning(f"Herramienta '{nombre}' sin manejador; se omite")
            continue
        entradas.append(entrada)
        registradas.append(nombre)

    if not entradas:
        return None, []

    return ToolsSchema(standard_tools=[], custom_tools={AdapterType.OPENAI: entradas}), registradas

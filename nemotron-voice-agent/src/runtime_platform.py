# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: BSD-2-Clause

"""Manual runtime platform selection for local service catalogs."""

import os
from collections.abc import Mapping

_PLATFORM_ALIASES = {
    "cloud": "cloud",
    "workstation": "workstation",
    "dgxspark": "dgxspark",
    "jetsonthor": "jetson",
}


def normalize_runtime_platform(value: str | None) -> str:
    """Normalize ``PLATFORM`` to service catalog section names."""
    key = "".join(char for char in (value or "").lower() if char.isalnum())
    return _PLATFORM_ALIASES.get(key, "")


def configured_runtime_platform() -> str | None:
    """Return the manually configured local platform.

    ``None`` means unset. ``""`` means set but not recognized.
    """
    raw = os.getenv("PLATFORM")
    if raw is None or not raw.strip():
        return None
    return normalize_runtime_platform(raw)


def select_runtime_platform_catalog(data: Mapping[str, object]) -> dict | None:
    """Return the configured platform section, or ``None`` for legacy merging."""
    platform = configured_runtime_platform()
    if platform is None:
        return None
    if not platform or platform == "cloud":
        return {}
    section = data.get(platform)
    return dict(section) if isinstance(section, Mapping) else {}

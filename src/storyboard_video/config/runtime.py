from __future__ import annotations

import copy
import os


DEFAULT_TTS_PROVIDER_ORDER = ["edge_tts_wsl", "edge_tts"]


def build_runtime_tts_config(config: dict, *, os_name: str | None = None) -> dict:
    runtime_os = os_name or os.name
    preferred = copy.deepcopy(config)
    preferred.setdefault("tts", {})
    provider_order = list(preferred["tts"].get("provider_order", DEFAULT_TTS_PROVIDER_ORDER))
    if runtime_os != "nt":
        provider_order = [provider for provider in provider_order if str(provider).strip() != "edge_tts_wsl"]
        if "edge_tts" not in provider_order:
            provider_order.insert(0, "edge_tts")
    preferred["tts"]["provider_order"] = provider_order
    return preferred

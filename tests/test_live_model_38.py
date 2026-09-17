"""
Tests de la migración del frontend de voz a Gemini 3.8 Live (GA: 15-sep-2026).

Test-first: fijan que los defaults (dataclass, constructor) y la config real
(config.yaml) apunten al nuevo modelo, y que `connect()` lo entrega a la API.
Los 4 tests fallan contra el código anterior a la migración (rojo) y deben
quedar verdes tras actualizar los 3 strings del modelo.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.loader import load_config
from src.config.models import ProviderConfig
from src.providers.gemini import GeminiProvider

TARGET_MODEL = "gemini-3.8-live"


def test_provider_config_default_apunta_a_3_8_live():
    assert ProviderConfig().model == TARGET_MODEL


def test_gemini_provider_constructor_default_apunta_a_3_8(monkeypatch):
    # Interceptar genai.Client: su __init__ exige credenciales reales
    monkeypatch.setattr("src.providers.gemini.genai.Client", lambda *a, **k: MagicMock())
    provider = GeminiProvider()
    assert provider.model_name == TARGET_MODEL


def test_config_yaml_real_apunta_a_3_8_live():
    """La config que la app realmente lee no puede quedar desalineada de los defaults."""
    cfg = load_config("config.yaml")
    assert cfg.provider.model == TARGET_MODEL


def test_connect_entrega_el_modelo_a_la_api(monkeypatch):
    """Drift guard: el modelo configurado llega intacto a client.aio.live.connect."""
    capturado = {}

    class FakeCM:
        async def __aenter__(self): return MagicMock()
        async def __aexit__(self, *a): return False

    class FakeLive:
        def connect(self, model, config):
            capturado["model"] = model
            return FakeCM()

    monkeypatch.setattr(
        "src.providers.gemini.genai.Client",
        lambda *a, **k: SimpleNamespace(aio=SimpleNamespace(live=FakeLive())),
    )

    async def go():
        provider = GeminiProvider()
        async with provider.connect(system_prompt="s", tools=[]):
            pass

    import asyncio
    asyncio.run(go())
    assert capturado["model"] == TARGET_MODEL

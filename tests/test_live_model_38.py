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


# ---------------------------------------------------------------------------
# 3.8 Live requiere VAD del servidor (audio_stream_end es ignorado por el modelo)
# ---------------------------------------------------------------------------

import asyncio

from src.config.models import AtlasConfig, enforce_model_requirements


def _provider_falso(monkeypatch):
    capturado = {}

    class FakeCM:
        async def __aenter__(self): return MagicMock()
        async def __aexit__(self, *a): return False

    class FakeLive:
        def connect(self, model, config):
            capturado["model"] = model
            capturado["config"] = config
            return FakeCM()

    monkeypatch.setattr(
        "src.providers.gemini.genai.Client",
        lambda *a, **k: SimpleNamespace(aio=SimpleNamespace(live=FakeLive())),
    )
    return capturado


def test_enforce_requirements_fuerza_server_vad_en_3_8():
    cfg = AtlasConfig()
    assert cfg.provider.model == TARGET_MODEL
    assert cfg.voice.server_vad is False  # default de config
    enforce_model_requirements(cfg)
    assert cfg.voice.server_vad is True


def test_enforce_requirements_no_toca_modelos_anteriores():
    cfg = AtlasConfig()
    cfg.provider.model = "gemini-3.1-flash-live-preview"
    enforce_model_requirements(cfg)
    assert cfg.voice.server_vad is False


def test_provider_fuerza_server_vad_en_3_8(monkeypatch):
    monkeypatch.setattr("src.providers.gemini.genai.Client", lambda *a, **k: MagicMock())
    assert GeminiProvider().server_vad is True
    assert GeminiProvider(model_name="gemini-3.1-flash-live-preview").server_vad is False


def test_connect_3_8_envia_realtime_config_minimo(monkeypatch):
    """3.8: solo automatic_activity_detection; sin sensitivity/activity_handling
    (los defaults del servidor son el camino soportado, medido en vivo)."""
    capturado = _provider_falso(monkeypatch)

    async def go():
        provider = GeminiProvider()
        async with provider.connect(system_prompt="s", tools=[]):
            pass

    asyncio.run(go())
    ric = capturado["config"].realtime_input_config
    assert ric is not None
    assert ric.automatic_activity_detection.disabled is False
    assert ric.automatic_activity_detection.start_of_speech_sensitivity is None
    assert ric.activity_handling is None
    assert ric.turn_coverage is None


def test_connect_3_1_mantiene_config_detallada_con_server_vad(monkeypatch):
    capturado = _provider_falso(monkeypatch)

    async def go():
        provider = GeminiProvider(model_name="gemini-3.1-flash-live-preview", server_vad=True)
        async with provider.connect(system_prompt="s", tools=[]):
            pass

    asyncio.run(go())
    ric = capturado["config"].realtime_input_config
    assert ric.automatic_activity_detection.silence_duration_ms == 600
    assert ric.activity_handling is not None


# ---------------------------------------------------------------------------
# Docs oficiales gemini-3.8-live:
# https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live
# ---------------------------------------------------------------------------

class _FakeTool:
    name = "herramienta_prueba"
    description = "tool de prueba"
    parameters = {"type": "OBJECT", "properties": {"x": {"type": "STRING"}}}


def test_declaraciones_de_tools_son_blocking_en_3_8(monkeypatch):
    """Bug real: con el default NON_BLOCKING el modelo respondía de memoria
    ('Argentina 2022') mientras la búsqueda ya decía 'España 2026'. Con
    behavior=BLOCKING el modelo espera el resultado antes de hablar."""
    from google.genai import types
    capturado = _provider_falso(monkeypatch)

    async def go():
        provider = GeminiProvider()
        async with provider.connect(system_prompt="s", tools=[_FakeTool()]):
            pass

    asyncio.run(go())
    decls = capturado["config"].tools[0].function_declarations
    assert decls and decls[0].behavior == types.Behavior.BLOCKING


def test_affective_dialog_no_se_envia_en_3_8(monkeypatch):
    """La API de 3.8 retiró enable_affective_dialog: no debe enviarse aunque
    la config local lo pida."""
    capturado = _provider_falso(monkeypatch)

    async def go():
        provider = GeminiProvider(affective_dialog=True)
        async with provider.connect(system_prompt="s", tools=[]):
            pass

    asyncio.run(go())
    assert capturado["config"].enable_affective_dialog is not True


def test_connect_no_incluye_google_search_nativo(monkeypatch):
    """VERIFICADO contra la API en vivo: Tool(google_search) en la sesión Live
    hace que el servidor rechace la conexión con '1011 quota exceeded' a menos
    que el plan lo permita. Este test lo bloquea para que nadie lo reintroduzca."""
    capturado = _provider_falso(monkeypatch)

    async def go():
        provider = GeminiProvider()
        async with provider.connect(system_prompt="s", tools=[_FakeTool()]):
            pass

    asyncio.run(go())
    tools = capturado["config"].tools
    assert tools[0].function_declarations
    assert all(getattr(t, "google_search", None) is None for t in tools)


def test_connect_sin_temperatura_en_live(monkeypatch):
    """NUNCA fijar temperature en la sesión Live: medido con sonda A/B contra
    la API real (2026-09-19) — temperature=0.1 hace que gemini-3.8-live
    genere PCM de silencio puro (0% frames hablados) aunque la transcripción
    sea correcta: turnos de 110s de 'nada audible'. Con el default (~1.0) el
    audio siempre contiene voz. La defensa anti-alucinación vive en tools
    BLOCKING + override del FunctionResponse, no aquí."""
    capturado = _provider_falso(monkeypatch)

    async def go():
        provider = GeminiProvider()
        async with provider.connect(system_prompt="s", tools=[]):
            pass

    asyncio.run(go())
    assert capturado["config"].temperature is None

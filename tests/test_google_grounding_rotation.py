"""Tests para la rotación de modelos y bloqueo por cuota de Google Grounding."""
import pytest
from src.plugins.browser.engines.google_grounding import GoogleGroundingSearchEngine
from src.plugins.browser.engines.base import SearchResponse


class FakeQuotaError(Exception):
    """Simula un 429 RESOURCE_EXHAUSTED de google.genai."""
    def __init__(self):
        super().__init__("429 RESOURCE_EXHAUSTED. You exceeded your current quota")


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    # Aislar del estado real persistido en ~/.cache (no contaminar ni leer)
    monkeypatch.setattr(
        "src.plugins.browser.engines.google_grounding._STATE_PATH",
        str(tmp_path / "state.json")
    )
    return GoogleGroundingSearchEngine(timeout=1.0)


@pytest.mark.asyncio
async def test_bloqueo_individual_por_modelo_429(engine, monkeypatch):
    llamados = []

    async def fake_try(query, model, max_results):
        llamados.append(model)
        if model == engine.MODEL_CANDIDATES[0]:
            raise FakeQuotaError()
        return SearchResponse(query=query, answer="respuesta ok", success=True, engine_used="google_grounding")

    monkeypatch.setattr(engine, "_try_model", fake_try)

    # 1ª búsqueda: primer modelo da 429 -> se bloquea y rota al segundo
    res = await engine.search("hola")
    assert res.success is True
    assert engine.MODEL_CANDIDATES[0] in engine._model_blocked_until
    assert engine.model == engine.MODEL_CANDIDATES[1]  # promovido
    assert llamados == engine.MODEL_CANDIDATES[:2]

    # 2ª búsqueda: el modelo bloqueado NO se reintenta
    llamados.clear()
    res2 = await engine.search("otra")
    assert res2.success is True
    assert engine.MODEL_CANDIDATES[0] not in llamados


@pytest.mark.asyncio
async def test_todos_bloqueados_responde_instantaneamente(engine):
    # Simular que los 3 modelos ya están bloqueados
    import time
    future = time.time() + 900
    for m in engine.MODEL_CANDIDATES:
        engine._model_blocked_until[m] = future

    t0 = time.time()
    res = await engine.search("consulta cualquiera")
    elapsed = time.time() - t0

    assert res.success is False
    assert "Cuota" in res.error
    assert elapsed < 0.1  # circuito abierto, sin red


@pytest.mark.asyncio
async def test_timeout_bloquea_modelo_y_no_rota(engine, monkeypatch):
    """
    Un timeout casi siempre es la red lenta/caída, no el modelo. Política:
    cooldown corto (4 min) para ese modelo y NO seguir probando los demás
    (pagar 5s por modelo en la misma red no tiene sentido).
    """
    import asyncio as aio
    import time

    llamados = []

    async def fake_try(query, model, max_results):
        llamados.append(model)
        raise aio.TimeoutError()

    monkeypatch.setattr(engine, "_try_model", fake_try)
    res = await engine.search("clima")

    assert res.success is False
    assert llamados == [engine.model]  # NO rotó a los otros modelos
    assert engine._model_blocked_until[engine.model] > time.time() + 60


@pytest.mark.asyncio
async def test_bloqueo_por_timeout_se_salta_en_la_siguiente_busqueda(engine, monkeypatch):
    """La 2ª búsqueda tras un timeout no vuelve a pagar el modelo caído."""
    import asyncio as aio

    llamados = []

    async def fake_try(query, model, max_results):
        llamados.append(model)
        if model == engine.MODEL_CANDIDATES[0]:
            raise aio.TimeoutError()
        return SearchResponse(query=query, answer="ok", success=True, engine_used="google_grounding")

    monkeypatch.setattr(engine, "_try_model", fake_try)
    await engine.search("primera")          # modelo0 timeout → bloqueado
    llamados.clear()
    res = await engine.search("segunda")    # debe empezar por modelo1
    assert res.success is True
    assert engine.MODEL_CANDIDATES[0] not in llamados


@pytest.mark.asyncio
async def test_error_de_conexion_bloquea_y_no_rota(engine, monkeypatch):
    """Errores tipo DNS/socket/connect: mismo tratamiento que timeout."""

    async def fake_try(query, model, max_results):
        raise ConnectionError("DNS no resuelve generativelanguage.googleapis.com")

    monkeypatch.setattr(engine, "_try_model", fake_try)
    res = await engine.search("noticias")
    assert res.success is False
    assert engine.model in engine._model_blocked_until  # cooldown aplicado
    assert "conexión" in (res.error or "")


@pytest.mark.asyncio
async def test_cooldown_expira_y_reintenta(engine, monkeypatch):
    """Pasado el cooldown, el modelo vuelve a intentarse (recuperación)."""
    import time

    llamados = []
    modelo = engine.model
    # Bloqueo ya expirado (timestamp en el pasado)
    engine._model_blocked_until[modelo] = time.time() - 1

    async def fake_try(query, m, max_results):
        llamados.append(m)
        return SearchResponse(query=query, answer="ok", success=True, engine_used="google_grounding")

    monkeypatch.setattr(engine, "_try_model", fake_try)
    res = await engine.search("recuperación")
    assert res.success is True
    assert llamados == [modelo]


@pytest.mark.asyncio
async def test_estado_persiste_entre_reinicios(engine, monkeypatch, tmp_path):
    """El último modelo exitoso y los bloqueos 429 deben sobrevivir un reinicio."""
    import time
    state_file = tmp_path / "state.json"
    monkeypatch.setattr("src.plugins.browser.engines.google_grounding._STATE_PATH", str(state_file))

    # Simular éxito en el 2º modelo: queda promovido y persistido
    async def fake_try(query, model, max_results):
        if model == engine.MODEL_CANDIDATES[0]:
            raise FakeQuotaError()
        return SearchResponse(query=query, answer="ok", success=True, engine_used="google_grounding")

    monkeypatch.setattr(engine, "_try_model", fake_try)
    res = await engine.search("x")
    assert res.success is True
    assert engine.model == engine.MODEL_CANDIDATES[1]

    # Nueva instancia = reinicio de Atlas: debe heredar modelo y bloqueos
    engine2 = GoogleGroundingSearchEngine(timeout=1.0)
    assert engine2.model == engine.MODEL_CANDIDATES[1]
    assert engine.MODEL_CANDIDATES[0] in engine2._model_blocked_until

    # Los bloqueos expirados NO se restauran
    engine3 = GoogleGroundingSearchEngine(timeout=1.0)
    engine3._model_blocked_until = {engine.MODEL_CANDIDATES[2]: time.time() - 10}
    engine3._save_state()
    engine4 = GoogleGroundingSearchEngine(timeout=1.0)
    assert engine.MODEL_CANDIDATES[2] not in engine4._model_blocked_until

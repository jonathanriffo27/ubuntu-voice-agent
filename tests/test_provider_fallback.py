"""Tests del fallback en caliente de proveedor.

Contexto real: `gemini-3.8-live` empezó a devolver `1011 Internal error
encountered` en cuanto se enviaba un mensaje (backend saturado), dejando a
Atlas sin respuesta. El asistente debe cambiar a `provider.fallback_model`
solo en runtime (la config del primario nunca se sobreescribe) y volver al
primario cuando una sonda confirme que se recuperó.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brain.assistant import Assistant
from src.events.base import SystemNotification
from src.events.bus import EventBus
from src.providers.base import BaseProvider, ProviderSession, TextChunk
from src.tools.registry import ToolRegistry

ERROR_1011 = "1011 None. Internal error encountered."


class _SesionFake(ProviderSession):
    def __init__(self, error=None):
        self._error = error
        self.sent_texts = []
        self.cerrada = False
        self._closed = asyncio.Event()

    async def send_audio(self, data: bytes, sample_rate: int = 16000) -> None:
        pass

    async def send_video(self, data: bytes, mime_type: str = "image/jpeg") -> None:
        pass

    async def send_text(self, text: str, end_of_turn: bool = False) -> None:
        self.sent_texts.append((text, end_of_turn))

    async def end_turn(self) -> None:
        pass

    async def send_tool_response(self, responses) -> None:
        pass

    async def close(self) -> None:
        self.cerrada = True
        self._closed.set()

    async def receive(self):
        if self._error:
            raise ConnectionResetError(self._error)
        yield TextChunk(text="ok")
        await self._closed.wait()
        # Modela el WebSocket real: al cerrar, el receive termina con error.
        raise ConnectionResetError("1006 None. abnormal closure [internal]")


class _ProveedorFake(BaseProvider):
    def __init__(self, modelo, error=None):
        self.model_name = modelo
        self._error = error
        self.sesiones = []
        # El Assistant lo consulta para no contar fallos de sesiones abiertas
        # con handle de resumption caducado (señal falsa de backend caído).
        self._last_connect_used_handle = False

    @asynccontextmanager
    async def connect(self, system_prompt: str, tools: list):
        session = _SesionFake(self._error)
        self.sesiones.append(session)
        yield session


def _config(fallback_model="fake-fallback", fallback_after_failures=2, probe_interval=9999):
    return SimpleNamespace(
        provider=SimpleNamespace(
            model="gemini-3.8-live",
            fallback_model=fallback_model,
            fallback_after_failures=fallback_after_failures,
            fallback_probe_interval=probe_interval,
        ),
        voice=MagicMock(mode="wake_word"),
        ui=None,
        developer_agent=None,
        mcp_servers={},
    )


def _assistant(primario=None, factory=None, bus=None, **cfg):
    primario = primario or _ProveedorFake("gemini-3.8-live", error=ERROR_1011)
    return Assistant(
        provider=primario,
        registry=ToolRegistry(),
        config=_config(**cfg),
        event_bus=bus or EventBus(),
        provider_factory=factory,
    )


@pytest.mark.asyncio
async def test_dos_fallos_cortos_activan_el_fallback():
    eventos = []
    bus = EventBus()
    bus.subscribe_all(lambda e: eventos.append(e))
    creados = []

    def factory(modelo):
        proveedor = _ProveedorFake(modelo)
        creados.append(proveedor)
        return proveedor

    a = _assistant(primario=_ProveedorFake("gemini-3.8-live"), factory=factory, bus=bus)
    error = ConnectionResetError(ERROR_1011)

    a._track_provider_health(time.time(), error)
    assert a._fallback_active is False  # un fallo aún no alcanza el umbral
    assert a._unavailable_streak == 1

    a._track_provider_health(time.time(), error)
    assert a._fallback_active is True
    assert a._active_provider is a._fallback_provider
    assert creados[0].model_name == "fake-fallback"
    assert any(
        isinstance(e, SystemNotification) and "no está disponible" in e.message and e.kind == "model"
        for e in eventos
    )

    a._probe_task.cancel()


@pytest.mark.asyncio
async def test_1011_tras_sesion_larga_no_activa_el_fallback():
    """Un 1011 con la sesión ya establecida es el idle-timeout normal del
    servidor: no debe confundirse con el backend caído."""
    a = _assistant(factory=lambda m: _ProveedorFake(m))
    a._unavailable_streak = 1

    a._track_provider_health(time.time() - 60.0, ConnectionResetError(ERROR_1011))

    assert a._unavailable_streak == 0
    assert a._fallback_active is False


@pytest.mark.asyncio
async def test_fallo_corto_generico_no_cuenta_para_el_fallback():
    a = _assistant(factory=lambda m: _ProveedorFake(m))

    a._track_provider_health(time.time(), ConnectionError("network unreachable"))

    assert a._unavailable_streak == 0
    assert a._fallback_active is False


@pytest.mark.asyncio
async def test_exceptiongroup_con_1011_cuenta_como_no_disponible():
    """El bucle de sesión entrega ExceptionGroup: hay que mirar los subtipos,
    porque str(ExceptionGroup) no incluye el mensaje del 1011."""
    a = _assistant(factory=lambda m: _ProveedorFake(m))
    grupo = BaseExceptionGroup("unhandled errors in a TaskGroup", [ConnectionResetError(ERROR_1011)])

    a._track_provider_health(time.time(), grupo)

    assert a._unavailable_streak == 1


@pytest.mark.asyncio
async def test_fallos_del_fallback_no_re_activan_ni_cambian_proveedor():
    creados = []

    def factory(modelo):
        proveedor = _ProveedorFake(modelo)
        creados.append(proveedor)
        return proveedor

    a = _assistant(primario=_ProveedorFake("gemini-3.8-live"), factory=factory)
    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011))
    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011))
    fallback = a._fallback_provider

    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011))

    assert a._active_provider is fallback
    assert len(creados) == 1  # no se construyó otro proveedor
    a._probe_task.cancel()


@pytest.mark.asyncio
async def test_sin_fallback_configurado_no_cambia_nada():
    primario = _ProveedorFake("gemini-3.8-live")
    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m), fallback_model=None)

    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011))
    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011))

    assert a._fallback_active is False
    assert a._active_provider is primario


@pytest.mark.asyncio
async def test_sonda_exitosa_vuelve_al_primario():
    eventos = []
    bus = EventBus()
    bus.subscribe_all(lambda e: eventos.append(e))
    primario = _ProveedorFake("gemini-3.8-live")  # sano: responde a la sonda

    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m), bus=bus)
    a._fallback_active = True
    a._fallback_provider = _ProveedorFake("fake-fallback")
    a._active_provider = a._fallback_provider

    assert await a._probe_primary_once() is True

    await a._return_to_primary()

    assert a._fallback_active is False
    assert a._active_provider is primario
    assert any(
        isinstance(e, SystemNotification) and "disponible de nuevo" in e.message and e.kind == "model"
        for e in eventos
    )


@pytest.mark.asyncio
async def test_sonda_fallida_mantiene_el_fallback():
    primario = _ProveedorFake("gemini-3.8-live", error=ERROR_1011)
    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m, error=ERROR_1011))
    a._fallback_active = True
    a._active_provider = _ProveedorFake("fake-fallback")

    assert await a._probe_primary_once() is False
    assert a._fallback_active is True
    assert a._active_provider is not primario


@pytest.mark.asyncio
async def test_refresco_volitivo_no_cuenta_como_fallo():
    a = _assistant(factory=lambda m: _ProveedorFake(m))
    session = _SesionFake()
    a._active_session = session

    await a._force_session_refresh()

    assert session.cerrada is True
    assert a._provider_switch_requested is True

    a._track_provider_health(time.time(), ConnectionResetError("connection closed"))

    assert a._provider_switch_requested is False
    assert a._unavailable_streak == 0
    assert a._fallback_active is False


@pytest.mark.asyncio
async def test_run_async_cambia_al_fallback_tras_fallos_del_primario():
    """Integración: el loop real de sesiones debe usar `_active_provider`, que
    pasa al fallback tras 2 sesiones cortas muertas con 1011."""
    primario = _ProveedorFake("gemini-3.8-live", error=ERROR_1011)
    fallback = _ProveedorFake("fake-fallback")

    def factory(modelo):
        assert modelo == "fake-fallback"
        return fallback

    a = _assistant(primario=primario, factory=factory)
    # Hardware mockeado (mismo patrón que tests/test_reconnect.py)
    a.in_stream = MagicMock()
    a.out_stream = MagicMock()
    a.p = MagicMock()
    a.recorder = MagicMock()
    a.recorder.listen = AsyncMock()
    a.player = MagicMock()
    a.player.play = AsyncMock()

    with patch("pyaudio.PyAudio"), \
         patch("src.voice.recorder.AudioRecorder.load_wake_word", new_callable=AsyncMock), \
         patch("src.voice.recorder.AudioRecorder.calibrate", new_callable=AsyncMock):
        tarea = asyncio.create_task(a.run_async())
        try:
            for _ in range(500):
                if a._fallback_active:
                    break
                await asyncio.sleep(0.01)

            assert a._fallback_active is True
            assert a._active_provider is fallback
            assert fallback.sesiones, "el fallback debió abrir una sesión"
        finally:
            tarea.cancel()
            try:
                await tarea
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_get_voice_status_refleja_activo_y_respaldo():
    primario = _ProveedorFake("gemini-3.8-live")
    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m))

    status = a.get_voice_status()
    assert status["model"] == "gemini-3.8-live"
    assert status["fallback_model"] == "fake-fallback"
    assert status["active_model"] == "gemini-3.8-live"
    assert status["fallback_active"] is False

    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011))
    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011))

    status = a.get_voice_status()
    assert status["active_model"] == "fake-fallback"
    assert status["fallback_active"] is True
    a._probe_task.cancel()


@pytest.mark.asyncio
async def test_texto_sin_sesion_avisa_en_vez_de_ignorar(caplog):
    """Antes, un mensaje de HUD durante la reconexión se descartaba en silencio
    y parecía que Atlas no respondía a propósito."""
    eventos = []
    bus = EventBus()
    bus.subscribe_all(lambda e: eventos.append(e))
    a = _assistant(bus=bus)
    assert a._active_session is None

    with caplog.at_level("WARNING"):
        await a.send_text_message("¿hola?")

    assert any("descartado" in r.message for r in caplog.records)
    assert any(isinstance(e, SystemNotification) and "no enviado" in e.message for e in eventos)


@pytest.mark.asyncio
async def test_fallo_con_handle_caducado_no_cuenta_para_el_fallback():
    """Caso real observado con el micrófono en pausa: el server cierra la sesión
    idle (1011/1006), el reintento con handle falla al instante y el intento
    limpio siguiente conecta. Ese fallo NO es "backend no disponible"."""
    primario = _ProveedorFake("gemini-3.8-live")
    primario._last_connect_used_handle = True
    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m))

    a._track_provider_health(time.time(), ConnectionResetError(ERROR_1011), primario)

    assert a._unavailable_streak == 0
    assert a._fallback_active is False


@pytest.mark.asyncio
async def test_caida_real_activa_el_fallback_tras_dos_fallos_limpios():
    """En una caída real el intento con handle falla (no cuenta) y los intentos
    limpios consecutivos sí acumulan hasta el umbral."""
    primario = _ProveedorFake("gemini-3.8-live")
    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m))
    error = ConnectionResetError(ERROR_1011)

    primario._last_connect_used_handle = True
    a._track_provider_health(time.time(), error, primario)   # handle caducado
    assert a._unavailable_streak == 0

    primario._last_connect_used_handle = False
    a._track_provider_health(time.time(), error, primario)   # limpio 1/2
    assert a._fallback_active is False

    a._track_provider_health(time.time(), error, primario)   # limpio 2/2
    assert a._fallback_active is True
    a._probe_task.cancel()


@pytest.mark.asyncio
async def test_pausa_prolongada_suspende_y_reanuda_la_sesion():
    """El watcher cierra la sesión Live tras N segundos de mic en pausa y la
    marca para reconectar cuando el usuario reanuda."""
    primario = _ProveedorFake("gemini-3.8-live")
    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m))
    a.config.voice.pause_suspend_after = 0.05
    sesion = _SesionFake()
    a._active_session = sesion
    a.recorder = MagicMock()
    a.recorder.is_paused = True

    tarea = asyncio.create_task(a._pause_watcher_loop())
    try:
        for _ in range(200):
            if a._session_suspended:
                break
            await asyncio.sleep(0.01)
        assert a._session_suspended is True
        assert sesion.cerrada is True

        a.recorder.is_paused = False
        for _ in range(200):
            if not a._session_suspended:
                break
            await asyncio.sleep(0.01)
        assert a._session_suspended is False
    finally:
        tarea.cancel()
        try:
            await tarea
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_suspension_no_dispara_el_fallback():
    primario = _ProveedorFake("gemini-3.8-live")
    a = _assistant(primario=primario, factory=lambda m: _ProveedorFake(m))
    a._session_suspended = True

    a._track_provider_health(
        time.time(), ConnectionResetError("1006 None. abnormal closure [internal]"), primario
    )

    assert a._unavailable_streak == 0
    assert a._fallback_active is False


@pytest.mark.asyncio
async def test_run_async_suspende_y_reconecta_con_la_pausa():
    """Integración: pausar suspende la sesión y el loop no reconecta; al
    reanudar, se abre una sesión nueva."""
    primario = _ProveedorFake("gemini-3.8-live")

    def factory(modelo):
        return _ProveedorFake(modelo)

    a = _assistant(primario=primario, factory=factory)
    a.config.voice.pause_suspend_after = 0.05
    a.in_stream = MagicMock()
    a.out_stream = MagicMock()
    a.p = MagicMock()
    a.recorder = MagicMock()
    a.recorder.listen = AsyncMock()
    a.player = MagicMock()
    a.player.play = AsyncMock()

    with patch("pyaudio.PyAudio"), \
         patch("src.voice.recorder.AudioRecorder.load_wake_word", new_callable=AsyncMock), \
         patch("src.voice.recorder.AudioRecorder.calibrate", new_callable=AsyncMock):
        tarea = asyncio.create_task(a.run_async())
        try:
            for _ in range(300):
                if primario.sesiones:
                    break
                await asyncio.sleep(0.01)
            assert primario.sesiones, "debe conectar la sesión inicial"

            a.recorder.is_paused = True
            for _ in range(300):
                if a._session_suspended:
                    break
                await asyncio.sleep(0.01)
            assert a._session_suspended is True

            sesiones_antes = len(primario.sesiones)
            await asyncio.sleep(0.3)
            assert len(primario.sesiones) == sesiones_antes  # no reconecta en pausa

            a.recorder.is_paused = False
            for _ in range(400):
                if len(primario.sesiones) > sesiones_antes:
                    break
                await asyncio.sleep(0.01)
            assert len(primario.sesiones) > sesiones_antes, "debe reconectar al reanudar"
        finally:
            tarea.cancel()
            try:
                await tarea
            except asyncio.CancelledError:
                pass

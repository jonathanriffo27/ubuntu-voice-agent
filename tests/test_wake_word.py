import asyncio
import struct
import time
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

from src.voice.wake_word import WakeWordDetector
from src.voice.recorder import AudioRecorder, RecorderState
from src.events.bus import EventBus
from src.events.base import ConversationContext, WakeWordDetected, WakeWordStandby


class TestWakeWordDetector:
    def test_initialization_not_loaded(self):
        detector = WakeWordDetector()
        assert not detector.is_loaded

    def test_predict_without_load_returns_false(self):
        detector = WakeWordDetector()
        silence = struct.pack('h' * 1280, *([0] * 1280))
        detected, name, score = detector.predict(silence)
        assert detected is False
        assert score == 0.0

    def test_predict_silence_with_mock_model(self):
        detector = WakeWordDetector()
        detector._np = __import__("numpy")
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.001}
        detector._model = mock_model
        detector._model_name = "hey_jarvis"

        silence = struct.pack('h' * 1280, *([0] * 1280))
        detected, name, score = detector.predict(silence, threshold=0.35)
        assert detected is False
        assert name == "hey_jarvis"
        assert score == 0.001

    def test_predict_above_threshold_returns_true(self):
        detector = WakeWordDetector()
        detector._np = __import__("numpy")
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.88}
        detector._model = mock_model
        detector._model_name = "hey_jarvis"

        chunk = struct.pack('h' * 1280, *([500] * 1280))
        detected, name, score = detector.predict(chunk, threshold=0.35)
        assert detected is True
        assert name == "hey_jarvis"
        assert score == 0.88

    def test_buffer_accumulation_with_512_chunks(self):
        detector = WakeWordDetector()
        detector._np = __import__("numpy")
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.75}
        detector._model = mock_model
        detector._model_name = "hey_jarvis"

        chunk512 = struct.pack('h' * 512, *([100] * 512))

        # Chunks 1 y 2 (512 y 1024 muestras < 1280): no disparan inferencia aún
        d1, _, _ = detector.predict(chunk512, threshold=0.35)
        assert d1 is False
        assert not mock_model.predict.called

        d2, _, _ = detector.predict(chunk512, threshold=0.35)
        assert d2 is False
        assert not mock_model.predict.called

        # Chunk 3 (1536 muestras >= 1280): procesa exactamente 1280 y deja 256 en buffer
        d3, name, score = detector.predict(chunk512, threshold=0.35)
        assert d3 is True
        assert score == 0.75
        assert mock_model.predict.called
        # El remanente en buffer debe ser 256 muestras = 512 bytes (¡sin pérdidas!)
        assert len(detector._buffer) == 256 * 2

    def test_reset_clears_buffer_and_delegates(self):
        detector = WakeWordDetector()
        mock_model = MagicMock()
        detector._model = mock_model
        detector._buffer.extend(b"12345")
        detector.reset()
        assert len(detector._buffer) == 0
        assert mock_model.reset.called

    def test_load_sync_real_model(self):
        detector = WakeWordDetector()
        detector.load_sync("hey_jarvis")
        assert detector.is_loaded
        assert detector._model_name == "hey_jarvis"

    def test_load_sync_real_alexa_model(self):
        detector = WakeWordDetector()
        detector.load_sync("alexa")
        assert detector.is_loaded
        assert detector._model_name == "alexa"


class TestRecorderStateMachine:
    def _make_recorder(self, mode="wake_word"):
        mock_stream = MagicMock()
        mock_stream.is_active.return_value = True
        voice_config = MagicMock()
        voice_config.mode = mode
        voice_config.wake_word_threshold = 0.35
        voice_config.follow_up_timeout = 2.0
        voice_config.activation_sound = False
        return AudioRecorder(mock_stream, EventBus(), ConversationContext(), voice_config=voice_config)

    def test_starts_in_standby_when_wake_word_mode(self):
        rec = self._make_recorder(mode="wake_word")
        assert rec.state == RecorderState.STANDBY

    def test_starts_in_active_when_always_on(self):
        rec = self._make_recorder(mode="always_on")
        assert rec.state == RecorderState.ACTIVE

    def test_is_paused_property_compat(self):
        rec = self._make_recorder(mode="wake_word")
        assert rec.is_paused is False
        assert rec.state == RecorderState.STANDBY

        rec.is_paused = True
        assert rec.is_paused is True
        assert rec.state == RecorderState.MUTED

        rec.is_paused = False
        assert rec.is_paused is False
        assert rec.state == RecorderState.STANDBY

    def test_is_paused_compat_always_on(self):
        rec = self._make_recorder(mode="always_on")
        rec.is_paused = True
        assert rec.state == RecorderState.MUTED
        rec.is_paused = False
        assert rec.state == RecorderState.ACTIVE

    def test_enter_follow_up(self):
        rec = self._make_recorder(mode="wake_word")
        rec._state = RecorderState.ACTIVE
        rec.enter_follow_up()
        assert rec.state == RecorderState.FOLLOW_UP
        assert rec._follow_up_last_active > 0

    def test_enter_follow_up_ignored_if_muted(self):
        rec = self._make_recorder(mode="wake_word")
        rec._state = RecorderState.MUTED
        rec.enter_follow_up()
        assert rec.state == RecorderState.MUTED

    def test_pending_sleep_va_directo_a_standby(self):
        """Feature: tool 'entrar_en_espera' — tras un 'no, por ahora no, gracias'
        el turno de despedida cierra directo en STANDBY, sin abrir la ventana
        follow-up de 7s con el micrófono escuchando al ambiente."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))
        rec = AudioRecorder(MagicMock(), event_bus, ConversationContext(),
                            voice_config=self._wake_word_voice_config())
        rec._state = RecorderState.ACTIVE

        rec.request_sleep_after_turn()
        rec.enter_follow_up()

        assert rec.state == RecorderState.STANDBY
        assert rec.pending_sleep is False  # consumido
        assert any(isinstance(e, WakeWordStandby) for e in events)

    def test_turncomplete_duplicado_no_redespierta_tras_sleep_deliberado(self):
        """Gemini Live emite DOS TurnComplete por turno (generation_complete +
        turn_complete en gemini_session.py): el segundo no debe reabrir la
        ventana FOLLOW_UP justo después de un sueño deliberado."""
        event_bus = EventBus()
        rec = AudioRecorder(MagicMock(), event_bus, ConversationContext(),
                            voice_config=self._wake_word_voice_config())
        rec._state = RecorderState.ACTIVE

        rec.request_sleep_after_turn()
        rec.enter_follow_up()   # primer TurnComplete: duerme
        rec.enter_follow_up()   # TurnComplete duplicado: NO debe re-despertar

        assert rec.state == RecorderState.STANDBY

    def test_follow_up_desde_standby_sin_sleep_deliberado_sigue_abierto(self):
        """Control: un TurnComplete llegando a STANDBY sin cierre deliberado
        (ej: turno iniciado por teclado/HUD) sí abre la ventana follow-up."""
        event_bus = EventBus()
        rec = AudioRecorder(MagicMock(), event_bus, ConversationContext(),
                            voice_config=self._wake_word_voice_config())
        rec._state = RecorderState.STANDBY
        rec.waiting_for_model = True

        rec.enter_follow_up()

        assert rec.state == RecorderState.FOLLOW_UP
        assert rec.waiting_for_model is False

    @staticmethod
    def _wake_word_voice_config():
        vc = MagicMock()
        vc.mode = "wake_word"
        return vc


@pytest.mark.asyncio
class TestRecorderListenLoop:
    async def test_listen_wake_word_detection_and_activation(self):
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        mock_stream = MagicMock()
        silence_chunk = struct.pack('h' * 512, *([0] * 512))
        mock_stream.read.return_value = silence_chunk
        mock_stream.is_active.return_value = True

        voice_config = MagicMock()
        voice_config.mode = "wake_word"
        voice_config.wake_word_threshold = 0.35
        voice_config.follow_up_timeout = 5.0
        voice_config.activation_sound = False

        recorder = AudioRecorder(mock_stream, event_bus, ConversationContext(), voice_config=voice_config)

        # Mock WakeWordDetector
        mock_detector = MagicMock()
        mock_detector.predict.side_effect = [
            (False, "hey_jarvis", 0.05),
            (True, "hey_jarvis", 0.92)
        ]
        recorder.wake_detector = mock_detector

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()

        task = asyncio.create_task(recorder.listen(q_in, q_out))
        # Bombear hasta la detección (máx. 2s) en vez de un sleep fijo de 50ms:
        # bajo carga de la suite completa el event loop puede tardar más en
        # programar la tarea y el test fallaba en flake.
        deadline = time.monotonic() + 2.0
        while recorder.state != RecorderState.ACTIVE and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert recorder.state == RecorderState.ACTIVE
        ww_events = [e for e in events if isinstance(e, WakeWordDetected)]
        assert len(ww_events) == 1
        assert ww_events[0].wake_word == "hey_jarvis"
        assert ww_events[0].confidence == 0.92

    async def test_follow_up_timeout_returns_to_standby(self):
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        mock_stream = MagicMock()
        silence_chunk = struct.pack('h' * 512, *([0] * 512))
        mock_stream.read.return_value = silence_chunk
        mock_stream.is_active.return_value = True

        voice_config = MagicMock()
        voice_config.mode = "wake_word"
        voice_config.wake_word_threshold = 0.35
        voice_config.follow_up_timeout = 0.05
        voice_config.activation_sound = False

        recorder = AudioRecorder(mock_stream, event_bus, ConversationContext(), voice_config=voice_config)
        recorder._state = RecorderState.FOLLOW_UP
        recorder._follow_up_last_active = time.time() - 1.0

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()

        task = asyncio.create_task(recorder.listen(q_in, q_out))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert recorder.state == RecorderState.STANDBY
        standby_events = [e for e in events if isinstance(e, WakeWordStandby)]
        assert len(standby_events) >= 1

    async def test_active_timeout_without_speech_returns_to_standby(self):
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        mock_stream = MagicMock()
        silence_chunk = struct.pack('h' * 512, *([0] * 512))
        mock_stream.read.return_value = silence_chunk
        mock_stream.is_active.return_value = True

        voice_config = MagicMock()
        voice_config.mode = "wake_word"
        voice_config.wake_word_threshold = 0.35
        voice_config.follow_up_timeout = 0.05
        voice_config.activation_sound = False

        recorder = AudioRecorder(mock_stream, event_bus, ConversationContext(), voice_config=voice_config)
        recorder._state = RecorderState.ACTIVE
        recorder._active_started = time.time() - 1.0  # Tiempo expirado sin habla

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()

        task = asyncio.create_task(recorder.listen(q_in, q_out))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert recorder.state == RecorderState.STANDBY
        standby_events = [e for e in events if isinstance(e, WakeWordStandby)]
        assert len(standby_events) >= 1

    async def test_waiting_for_model_prevents_standby_timeout(self):
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        mock_stream = MagicMock()
        silence_chunk = struct.pack('h' * 512, *([0] * 512))
        mock_stream.read.return_value = silence_chunk
        mock_stream.is_active.return_value = True

        voice_config = MagicMock()
        voice_config.mode = "wake_word"
        voice_config.wake_word_threshold = 0.35
        voice_config.follow_up_timeout = 0.05
        voice_config.activation_sound = False

        recorder = AudioRecorder(mock_stream, event_bus, ConversationContext(), voice_config=voice_config)
        recorder._state = RecorderState.ACTIVE
        recorder.waiting_for_model = True
        recorder._active_started = time.time() - 1.0  # Expirado, pero esperando al modelo

        q_in = asyncio.Queue()
        q_out = asyncio.Queue()

        task = asyncio.create_task(recorder.listen(q_in, q_out))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # NO debe pasar a STANDBY porque está esperando la respuesta del modelo
        assert recorder.state == RecorderState.ACTIVE
        standby_events = [e for e in events if isinstance(e, WakeWordStandby)]
        assert len(standby_events) == 0

    async def test_enter_follow_up_from_standby(self):
        mock_stream = MagicMock()
        voice_config = MagicMock()
        voice_config.mode = "wake_word"
        recorder = AudioRecorder(mock_stream, EventBus(), ConversationContext(), voice_config=voice_config)
        recorder._state = RecorderState.STANDBY
        recorder.waiting_for_model = True

        recorder.enter_follow_up()
        assert recorder.state == RecorderState.FOLLOW_UP
        assert recorder.waiting_for_model is False
        assert recorder._follow_up_last_active > 0

    def _make_listen_recorder(self, event_bus):
        mock_stream = MagicMock()
        silence_chunk = struct.pack('h' * 512, *([0] * 512))
        mock_stream.read.return_value = silence_chunk
        mock_stream.is_active.return_value = True

        voice_config = MagicMock()
        voice_config.mode = "wake_word"
        voice_config.wake_word_threshold = 0.35
        voice_config.follow_up_timeout = 0.05
        voice_config.activation_sound = False
        voice_config.server_vad = True
        return AudioRecorder(mock_stream, event_bus, ConversationContext(), voice_config=voice_config)

    async def _pump_listen(self, recorder, player=None, seconds=0.05, until=None):
        """Corre el bucle listen en background.
        - Sin `until`: ventana fija de `seconds` (para tests que esperan que el
          estado NO cambie).
        - Con `until`: bombea hasta que el predicado se cumpla o se agote
          `seconds` (máx.). Evita flakes bajo carga de la suite completa, donde
          50ms de wall-clock no garantizan ni una iteración del bucle."""
        task = asyncio.create_task(recorder.listen(asyncio.Queue(), asyncio.Queue(), player))
        try:
            if until is None:
                await asyncio.sleep(seconds)
            else:
                deadline = time.monotonic() + seconds
                while not until() and time.monotonic() < deadline:
                    await asyncio.sleep(0.005)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_processing_tool_prevents_standby_timeout(self):
        """Bug real: con server_vad una búsqueda de 15s dormía el sistema a mitad
        de la tool porque el timeout de ACTIVE ignoraba processing_tool."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.ACTIVE
        recorder.processing_tool = True
        recorder._active_started = time.time() - 1.0  # Timeout ya expirado

        await self._pump_listen(recorder)

        assert recorder.state == RecorderState.ACTIVE  # sigue despierto durante la tool
        assert not any(isinstance(e, WakeWordStandby) for e in events)

    async def test_standby_ignora_wake_word_mientras_atlas_habla(self):
        """Bug real: la propia voz de Atlas entrando al micro disparaba 'alexa'
        con 95-99% de confianza al entrar en espera."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.STANDBY
        recorder.wake_detector = MagicMock()
        recorder.wake_detector.predict.return_value = (True, "alexa", 0.99)

        speaking_player = SimpleNamespace(is_speaking=True, last_speech_time=time.time())
        await self._pump_listen(recorder, player=speaking_player)

        assert recorder.state == RecorderState.STANDBY
        recorder.wake_detector.predict.assert_not_called()  # ni siquiera evalúa el eco
        assert not any(isinstance(e, WakeWordDetected) for e in events)

    async def test_follow_up_no_expira_durante_tool(self):
        """turn_complete puede llegar JUNTO a la function call: la ventana
        FOLLOW_UP no debe caducar mientras la herramienta sigue ejecutándose."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.FOLLOW_UP
        recorder.processing_tool = True
        recorder._follow_up_last_active = time.time() - 60.0  # expirada hace rato

        await self._pump_listen(recorder)

        assert recorder.state == RecorderState.FOLLOW_UP  # sigue esperando la tool
        assert not any(isinstance(e, WakeWordStandby) for e in events)

    async def test_standby_detecta_wake_word_con_atlas_en_silencio(self):
        """La guardia anti-eco no debe bloquear detecciones legítimas."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.STANDBY
        recorder.wake_detector = MagicMock()
        recorder.wake_detector.predict.return_value = (True, "alexa", 0.9)

        quiet_player = SimpleNamespace(is_speaking=False, last_speech_time=0.0)
        await self._pump_listen(
            recorder, player=quiet_player, seconds=2.0,
            until=lambda: recorder.state == RecorderState.ACTIVE,
        )

        assert recorder.state == RecorderState.ACTIVE
        assert any(isinstance(e, WakeWordDetected) for e in events)

    async def test_turn_in_flight_prevents_standby_en_pausa_de_respuesta(self):
        """Bug real: con server_vad, gemini-3.8 hace pausas de varios segundos
        ENTRE segmentos de una misma respuesta; sin turn_in_flight el timeout de
        ACTIVE dormía a Atlas a mitad de frase ("¿En qué te puedo..." → 💤 →
        "...asistir?" llegaba tras repetir la wake word)."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.ACTIVE
        recorder.turn_in_flight = True
        recorder._active_started = time.time() - 60.0  # "inactividad" expirada

        await self._pump_listen(recorder)

        assert recorder.state == RecorderState.ACTIVE  # sigue despierto a mitad de turno
        assert not any(isinstance(e, WakeWordStandby) for e in events)

    async def test_turn_in_flight_prevents_follow_up_expiry(self):
        """Un TurnComplete no debe cortar el seguimiento si el modelo sigue
        emitiendo eventos del mismo turno (variante FOLLOW_UP del bug anterior)."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.FOLLOW_UP
        recorder.turn_in_flight = True
        recorder._follow_up_last_active = time.time() - 60.0  # ventana expirada

        await self._pump_listen(recorder)

        assert recorder.state == RecorderState.FOLLOW_UP
        assert not any(isinstance(e, WakeWordStandby) for e in events)

    async def test_standby_grace_period_suprime_eco_del_chime_sleep(self):
        """Bug real: el chime "sleep" (pw-play por altavoces, fuera del tracking
        del player) auto-disparaba la wake word ~1s después de dormir (0.85)."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.STANDBY
        recorder._standby_entered = time.time()  # recién dormido (gracia activa)
        recorder.wake_detector = MagicMock()
        recorder.wake_detector.predict.return_value = (True, "alexa", 0.85)

        quiet_player = SimpleNamespace(is_speaking=False, last_speech_time=0.0)
        await self._pump_listen(recorder, player=quiet_player)

        assert recorder.state == RecorderState.STANDBY
        recorder.wake_detector.predict.assert_not_called()  # ni siquiera evalúa el chime
        assert not any(isinstance(e, WakeWordDetected) for e in events)

    async def test_follow_up_expira_normalmente_tras_turno_cerrado(self):
        """Control: con turn_in_flight limpio (modelo ya terminó), la ventana
        FOLLOW_UP sí debe expirar con normalidad."""
        event_bus = EventBus()
        events = []
        event_bus.subscribe_all(lambda e: events.append(e))

        recorder = self._make_listen_recorder(event_bus)
        recorder._state = RecorderState.FOLLOW_UP
        recorder.turn_in_flight = False
        recorder._follow_up_last_active = time.time() - 60.0

        await self._pump_listen(recorder)

        assert recorder.state == RecorderState.STANDBY
        assert any(isinstance(e, WakeWordStandby) for e in events)


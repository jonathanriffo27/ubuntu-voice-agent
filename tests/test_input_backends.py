"""Tests de los backends de inyección de input (ydotool / portal RemoteDesktop)."""
import os
from unittest.mock import patch

import pytest

from src.input.backends import (
    InputRouter,
    RemoteDesktopPortalBackend,
    YdotoolBackend,
    parse_key_combo,
)


class TestParseKeyCombo:
    def test_tecla_simple(self):
        assert parse_key_combo("enter") == [28]
        assert parse_key_combo("super") == [125]

    def test_combo_modificador(self):
        assert parse_key_combo("ctrl+v") == [29, 47]
        assert parse_key_combo("ctrl+alt+t") == [29, 56, 20]

    def test_sin_separador_mas_en_nombre(self):
        assert parse_key_combo("SUPER") == [125]
        assert parse_key_combo("  ctrl + v ") == [29, 47]

    def test_tecla_desconocida(self):
        assert parse_key_combo("hipertecla") is None
        assert parse_key_combo("ctrl+inventada") is None
        assert parse_key_combo("") is None


class TestYdotoolBackend:
    def _backend(self, socket_exists=True, ydotool_exists=True):
        b = YdotoolBackend(socket_path="/tmp/fake_socket")
        return b, socket_exists, ydotool_exists

    def test_disponible_solo_con_daemon_vivo(self):
        # Socket obsoleto + daemon muerto => NO disponible (falso positivo detectado
        # el 2026-09-14: el socket DGRAM de ydotoold no se puede sondear conectando).
        b = YdotoolBackend(socket_path="/tmp/socket_inexistente_xyz")
        with patch("src.input.backends.shutil.which", return_value="/usr/bin/ydotool"), \
             patch("src.input.backends.os.path.exists", return_value=True), \
             patch("src.input.backends.process_alive", return_value=False):
            assert not b.is_available()
        with patch("src.input.backends.shutil.which", return_value="/usr/bin/ydotool"), \
             patch("src.input.backends.os.path.exists", return_value=True), \
             patch("src.input.backends.process_alive", return_value=True):
            assert b.is_available()
        # Sin binario ydotool, nunca disponible aunque el daemon viva
        with patch("src.input.backends.shutil.which", return_value=None), \
             patch("src.input.backends.os.path.exists", return_value=True), \
             patch("src.input.backends.process_alive", return_value=True):
            assert not b.is_available()

    def test_click_construye_comandos_correctos(self, tmp_path):
        sock = tmp_path / "sock"
        sock.touch()
        b = YdotoolBackend(socket_path=str(sock))
        calls = []

        class R:
            returncode = 0

        with patch("src.input.backends.subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or R()):
            assert b.click(150, 200) is True

        assert calls[0][:3] == ["ydotool", "mousemove", "--absolute"]
        assert "-x" in calls[0] and "150" in calls[0] and "200" in calls[0]
        assert calls[1] == ["ydotool", "click", "0xC0"]

    def test_doble_click_derecho(self, tmp_path):
        sock = tmp_path / "sock"
        sock.touch()
        b = YdotoolBackend(socket_path=str(sock))
        calls = []

        class R:
            returncode = 0

        with patch("src.input.backends.subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or R()):
            assert b.click(10, 10, button="right", double=True) is True
        assert calls[1] == ["ydotool", "click", "-r", "2", "-D", "50", "0xC1"]

    def test_press_key_combo(self, tmp_path):
        sock = tmp_path / "sock"
        sock.touch()
        b = YdotoolBackend(socket_path=str(sock))
        calls = []

        class R:
            returncode = 0

        with patch("src.input.backends.subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or R()):
            assert b.press_key("ctrl+v") is True
        assert calls[0] == ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]

    def test_type_text_clipboard_fallo(self, tmp_path):
        sock = tmp_path / "sock"
        sock.touch()
        b = YdotoolBackend(socket_path=str(sock))
        with patch("src.input.backends.shutil.which", return_value=None):
            assert b.type_text("hola") is False

    def test_type_text_ok(self, tmp_path):
        sock = tmp_path / "sock"
        sock.touch()
        b = YdotoolBackend(socket_path=str(sock))
        calls = []

        class R:
            returncode = 0

        with patch("src.input.backends.shutil.which", return_value="/usr/bin/wl-copy"), \
             patch("src.input.backends.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or R()):
            assert b.type_text("hola ¿qué tal? 🎉") is True
        # 1) wl-copy 2) ydotool key ctrl+v
        assert calls[0][0] == "wl-copy"
        assert calls[1][:2] == ["ydotool", "key"]


class _FakeAdapter:
    """Adapter del portal que finge el intercambio CreateSession/Select/Start."""

    CANCEL = "__CANCEL__"

    def __init__(self, fail_at: str = None):
        self.calls = []
        self._req = 0
        self.fail_at = fail_at
        self._responses = {}

    def call(self, method, *args):
        self.calls.append((method, args))
        self._req += 1
        req_path = f"/org/freedesktop/portal/desktop/request/{self._req}"
        if self.fail_at == method:
            self._responses[req_path] = self.CANCEL
        elif method == "CreateSession":
            self._responses[req_path] = {"session_handle": f"/session/{self._req}"}
        elif method == "SelectDevices":
            self._responses[req_path] = {}
        elif method == "Start":
            self._responses[req_path] = {"restore_token": "tok123", "devices": 3}
        else:
            return ()  # Notify*: void -> tupla vacía = éxito
        return (req_path,)

    def wait_response(self, path, timeout):
        val = self._responses.get(path)
        # El contrato real: usuario cancela => None
        return None if val == self.CANCEL else val


class TestRemoteDesktopPortalBackend:
    def _backend(self, tmp_path, adapter=None):
        adapter = adapter or _FakeAdapter()
        return RemoteDesktopPortalBackend(dbus_adapter=adapter,
                                          token_store_path=str(tmp_path / "token")), adapter

    def test_ensure_session_y_persistencia_token(self, tmp_path):
        backend, adapter = self._backend(tmp_path)
        assert backend.ensure_session() is True
        assert backend._session_handle == "/session/1"
        # Token persistido con permisos 600
        token_file = tmp_path / "token"
        assert token_file.read_text() == "tok123"
        # Con token guardado, la segunda sesión pide persist_mode=2
        backend2, adapter2 = self._backend(tmp_path)
        assert backend2.ensure_session() is True
        select_call = [c for c in adapter2.calls if c[0] == "SelectDevices"][0]
        opts = select_call[1][1]
        assert opts.get("restore_token") == "tok123"
        assert opts.get("persist_mode") == ("u", 2)

    def test_ensure_session_falla_si_usuario_cancela(self, tmp_path):
        backend, _ = self._backend(tmp_path, _FakeAdapter(fail_at="Start"))
        assert backend.ensure_session() is False
        assert backend._session_handle is None

    def test_ensure_session_falla_en_create_session(self, tmp_path):
        backend, _ = self._backend(tmp_path, _FakeAdapter(fail_at="CreateSession"))
        assert backend.ensure_session() is False
        assert backend._session_handle is None

    def test_click_emite_motion_y_botones(self, tmp_path):
        backend, adapter = self._backend(tmp_path)
        assert backend.click(300, 400) is True
        methods = [m for m, _ in adapter.calls]
        assert "NotifyPointerMotionAbsolute" in methods
        assert methods.count("NotifyPointerButton") == 2  # press + release
        motion = [a for m, a in adapter.calls if m == "NotifyPointerMotionAbsolute"][0]
        assert motion[2] == ""            # stream vacío (input-only)
        assert motion[3] == 300.0 and motion[4] == 400.0

    def test_press_key_combo_ctrl_v(self, tmp_path):
        backend, adapter = self._backend(tmp_path)
        assert backend.press_key("ctrl+v") is True
        key_calls = [a for m, a in adapter.calls if m == "NotifyKeyboardKeycode"]
        # args: (session_handle, options, keycode, estado)
        # press: ctrl(29), v(47) ; release: v, ctrl
        assert [a[2] for a in key_calls] == [29, 47, 47, 29]
        assert all(a[3] == ("u", 1) for a in key_calls[:2])
        assert all(a[3] == ("u", 0) for a in key_calls[2:])

    def test_press_key_desconocida_sin_sesion(self, tmp_path):
        backend, _ = self._backend(tmp_path)
        backend.ensure_session()
        assert backend.press_key("tecla_inventada") is False


class _FakeBackendForRouter:
    def __init__(self, name, available=True, works=True):
        self.name = name
        self._available = available
        self._works = works
        self.clicks = []

    def is_available(self):
        return self._available

    def click(self, x, y, button="left", double=False):
        self.clicks.append((x, y))
        return self._works

    def press_key(self, combo):
        return self._works

    def type_text(self, text):
        return self._works


class TestInputRouter:
    def test_usa_primer_backend_disponible(self):
        b1 = _FakeBackendForRouter("b1")
        b2 = _FakeBackendForRouter("b2")
        router = InputRouter(backends=[b1, b2])
        ok, via = router.click(1, 2)
        assert ok and via == "b1"
        assert b1.clicks == [(1, 2)]
        assert b2.clicks == []

    def test_fallback_si_primero_falla(self):
        b1 = _FakeBackendForRouter("b1", works=False)
        b2 = _FakeBackendForRouter("b2")
        router = InputRouter(backends=[b1, b2])
        ok, via = router.click(1, 2)
        assert ok and via == "b2"

    def test_salta_backends_no_disponibles(self):
        b1 = _FakeBackendForRouter("b1", available=False)
        b2 = _FakeBackendForRouter("b2")
        router = InputRouter(backends=[b1, b2])
        ok, via = router.press_key("enter")
        assert ok and via == "b2"

    def test_ninguno_disponible(self):
        b1 = _FakeBackendForRouter("b1", available=False)
        router = InputRouter(backends=[b1])
        ok, via = router.click(1, 2)
        assert not ok and via == "none"

    def test_excepcion_en_backend_no_rompe_router(self):
        class Explosivo(_FakeBackendForRouter):
            def is_available(self):
                raise RuntimeError("boom")
        b1 = Explosivo("b1")
        b2 = _FakeBackendForRouter("b2")
        router = InputRouter(backends=[b1, b2])
        ok, via = router.click(5, 5)
        assert ok and via == "b2"

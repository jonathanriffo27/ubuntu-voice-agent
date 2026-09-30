"""Tests de la confirmación dura para comandos destructivos.

Bug real: con `sudo shutdown -h now` propuesto, una frase ambigua del usuario
("¿Quieres irte a la casa?") llegaba al modelo; la barrera final (que el modelo
llamara o no a `ejecutar_comando_confirmado`) no validaba la confirmación real.
Ahora el tool exige una confirmación explícita y reciente del usuario para
comandos destructivos, sin depender del juicio del modelo.
"""

import time
from types import SimpleNamespace

import pytest

from src.plugins.shell.tools import (
    CommandResult,
    CommandState,
    EjecutarComandoTool,
    _has_explicit_confirmation,
    _is_dangerous_command,
)


class _ExecutorFake:
    def __init__(self):
        self.commands = []

    async def run(self, command):
        self.commands.append(command)
        return CommandResult(exit_code=0, stdout="ok", stderr="", duration_ms=1)


def _ctx(utterance="", ts=0.0):
    cfg = SimpleNamespace(tools=SimpleNamespace(shell=SimpleNamespace(enabled=True)))
    return SimpleNamespace(
        config=cfg,
        last_user_utterance=utterance,
        last_user_utterance_time=ts,
    )


class TestDeteccionDePeligro:
    @pytest.mark.parametrize("cmd", [
        "sudo shutdown -h now",
        "shutdown -h now",
        "reboot",
        "systemctl poweroff",
        "rm -rf /tmp/x",
        "rm -fr carpeta",
        "mkfs.ext4 /dev/sdb1",
        "dd if=/dev/zero of=/dev/sda",
    ])
    def test_comandos_destructivos(self, cmd):
        assert _is_dangerous_command(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "rm archivo.txt",
        "systemctl status nginx",
        "cat /etc/hosts",
        "pip install requests",
    ])
    def test_comandos_normales(self, cmd):
        assert _is_dangerous_command(cmd) is False

    def test_confirmacion_explicita(self):
        ahora = time.time()
        assert _has_explicit_confirmation("sí, confirmo", ahora) is True
        assert _has_explicit_confirmation("apruebo", ahora) is True
        assert _has_explicit_confirmation("ejecuta", ahora) is True
        # "sí" pelado puede responder a otra pregunta: no alcanza
        assert _has_explicit_confirmation("sí", ahora) is False
        assert _has_explicit_confirmation("¿Quieres irte a la casa?", ahora) is False
        # confirmación vieja
        assert _has_explicit_confirmation("confirmo", ahora - 120) is False
        assert _has_explicit_confirmation("", ahora) is False


class TestConfirmacionDura:
    @pytest.mark.asyncio
    async def test_peligroso_sin_confirmacion_no_ejecuta_ni_drena(self):
        state = CommandState(timeout_seconds=60)
        state.propose("sudo shutdown -h now")
        executor = _ExecutorFake()
        tool = EjecutarComandoTool(executor=executor, state=state)

        resultado = await tool.execute(
            _ctx("¿Quieres irte a la casa?", time.time())
        )

        assert resultado.success is False
        assert "confirmación explícita" in resultado.content
        assert executor.commands == []
        # El comando sigue pendiente hasta su TTL (se puede confirmar después)
        assert state.list_pending() == ["sudo shutdown -h now"]

    @pytest.mark.asyncio
    async def test_peligroso_con_confirmo_ejecuta(self):
        state = CommandState(timeout_seconds=60)
        state.propose("sudo shutdown -h now")
        executor = _ExecutorFake()
        tool = EjecutarComandoTool(executor=executor, state=state)

        resultado = await tool.execute(_ctx("sí, confirmo, apágalo", time.time()))

        assert resultado.success is True
        assert executor.commands == ["sudo shutdown -h now"]
        assert state.list_pending() == []

    @pytest.mark.asyncio
    async def test_confirmacion_vencida_no_ejecuta(self):
        state = CommandState(timeout_seconds=60)
        state.propose("reboot")
        executor = _ExecutorFake()
        tool = EjecutarComandoTool(executor=executor, state=state)

        resultado = await tool.execute(_ctx("confirmo", time.time() - 120))

        assert resultado.success is False
        assert executor.commands == []

    @pytest.mark.asyncio
    async def test_comando_normal_mantiene_el_flujo_actual(self):
        """La confirmación explícita se exige solo para comandos destructivos:
        el resto conserva el flujo mediado por el modelo."""
        state = CommandState(timeout_seconds=60)
        state.propose("ls -la")
        executor = _ExecutorFake()
        tool = EjecutarComandoTool(executor=executor, state=state)

        resultado = await tool.execute(_ctx(""))

        assert resultado.success is True
        assert executor.commands == ["ls -la"]

    @pytest.mark.asyncio
    async def test_sin_comando_pendiente_devuelve_error(self):
        executor = _ExecutorFake()
        tool = EjecutarComandoTool(executor=executor, state=CommandState(timeout_seconds=60))

        resultado = await tool.execute(_ctx("confirmo", time.time()))

        assert resultado.success is False
        assert "no hay un comando pendiente" in resultado.content.lower()

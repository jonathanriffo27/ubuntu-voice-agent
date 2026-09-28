"""Tests del gestor de navegador CDP y ciclo de vida de procesos."""
import asyncio
import os
import signal
import sys
import tempfile
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.cdp.manager import BrowserManager, _setup_linux_pdeathsig


def test_regression_orphaned_browser_pdeathsig():
    """
    Bug real: Instancias headless de Brave/Chromium quedaban huérfanas en segundo plano
    consumiendo más de 1.5GB de RAM cuando Atlas se cerraba abruptamente (os._exit(0)
    o Ctrl+C) debido a start_new_session=True sin PR_SET_PDEATHSIG ni limpieza de grupo de procesos.
    """
    if sys.platform.startswith("linux"):
        # Verificar que la función helper de PR_SET_PDEATHSIG no genera excepciones
        _setup_linux_pdeathsig()


def test_cleanup_stale_headless_profiles():
    """Verifica que cleanup_stale_headless limpia directorios huérfanos en /tmp."""
    tmp = tempfile.gettempdir()
    dummy_dir = tempfile.mkdtemp(prefix="atlas-cdp-headless-test-", dir=tmp)
    assert os.path.exists(dummy_dir)

    # Renombrar a prefijo estándar
    target_dir = os.path.join(tmp, f"atlas-cdp-headless-dummy_{os.getpid()}")
    os.rename(dummy_dir, target_dir)
    assert os.path.exists(target_dir)

    cleaned = BrowserManager.cleanup_stale_headless()
    assert cleaned >= 1
    assert not os.path.exists(target_dir)


@pytest.mark.asyncio
async def test_shutdown_kills_process_group():
    """Verifica que shutdown utiliza SIGTERM/SIGKILL sobre el grupo de procesos."""
    mgr = BrowserManager(headless=True)
    mgr._owns_browser = True

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)
    mgr._proc = mock_proc

    with patch("os.getpgid", return_value=99999), \
         patch("os.killpg") as mock_killpg:
        await mgr.shutdown()
        mock_killpg.assert_called_with(99999, signal.SIGTERM)
        mock_proc.wait.assert_called_once()

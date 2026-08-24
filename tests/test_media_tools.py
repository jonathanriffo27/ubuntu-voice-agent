import pytest
from unittest.mock import patch, MagicMock
from src.plugins.media.tools import ControlarMusicaTool, ReproducirMusicaTool
from src.tools.base import ToolContext
from src.config.models import AtlasConfig


@pytest.mark.asyncio
async def test_controlar_musica_play_pause():
    tool = ControlarMusicaTool()
    ctx = ToolContext(config=AtlasConfig())

    with patch("src.plugins.media.tools._execute_mpris_command", return_value=(True, "ok")):
        res = await tool.execute(ctx, accion="play_pause")
        assert res.success is True
        assert "Play/Pausa" in res.content


@pytest.mark.asyncio
async def test_controlar_musica_que_suena():
    tool = ControlarMusicaTool()
    ctx = ToolContext(config=AtlasConfig())

    with patch("src.plugins.media.tools._get_spotify_metadata", return_value={"title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera"}):
        res = await tool.execute(ctx, accion="que_suena")
        assert res.success is True
        assert "Bohemian Rhapsody" in res.content
        assert "Queen" in res.content


@pytest.mark.asyncio
async def test_reproducir_musica():
    tool = ReproducirMusicaTool()
    ctx = ToolContext(config=AtlasConfig())

    with patch("src.plugins.media.tools._execute_mpris_command", return_value=(True, "ok")), \
         patch("subprocess.Popen") as mock_popen, \
         patch("subprocess.run") as mock_run:
        res = await tool.execute(ctx, busqueda="Duki")
        assert res.success is True
        assert "Duki" in res.content

import pytest
from unittest.mock import MagicMock

from src.plugins.workspace.tools import (
    ListarArchivosProyectoTool, LeerArchivoProyectoTool, PROJECT_ROOT,
)
from src.tools.base import ToolContext


def _ctx():
    return ToolContext(config=MagicMock())


@pytest.mark.asyncio
class TestListarArchivosProyecto:
    async def test_lista_raiz_muestra_estructura_real(self):
        result = await ListarArchivosProyectoTool().execute(_ctx())
        assert result.success
        assert "src/" in result.content
        assert "jarvis.py" in result.content
        assert "config.yaml" in result.content

    async def test_lista_raiz_oculta_directorios_internos(self):
        result = await ListarArchivosProyectoTool().execute(_ctx())
        assert result.success
        # Comparar con "/" final: ".github" es legítimo y contiene ".git" como subcadena.
        for oculto in (".git/", "venv/", "__pycache__/", ".worktrees/"):
            assert oculto not in result.content

    async def test_lista_subruta_relativa(self):
        result = await ListarArchivosProyectoTool().execute(_ctx(), subruta="src/voice")
        assert result.success
        assert "recorder.py" in result.content
        assert "player.py" in result.content

    async def test_rechaza_escape_fuera_del_proyecto(self):
        for subruta in ("../", "../../etc", "/etc/passwd"):
            result = await ListarArchivosProyectoTool().execute(_ctx(), subruta=subruta)
            assert not result.success, f"escape no bloqueado: {subruta}"

    async def test_subruta_inexistente(self):
        result = await ListarArchivosProyectoTool().execute(_ctx(), subruta="directorio/inexistente")
        assert not result.success


@pytest.mark.asyncio
class TestLeerArchivoProyecto:
    async def test_lee_archivo_real(self):
        result = await LeerArchivoProyectoTool().execute(_ctx(), ruta="config.yaml")
        assert result.success
        assert "provider:" in result.content

    async def test_rechaza_archivos_con_secretos(self):
        # Defensa en profundidad: aunque el CredentialBroker redacta el output,
        # el contenido de .env jamás debe entrar al contexto del modelo.
        result = await LeerArchivoProyectoTool().execute(_ctx(), ruta=".env")
        assert not result.success

    async def test_rechaza_escape_fuera_del_proyecto(self):
        result = await LeerArchivoProyectoTool().execute(_ctx(), ruta="../../etc/hosts")
        assert not result.success

    async def test_archivo_inexistente(self):
        result = await LeerArchivoProyectoTool().execute(_ctx(), ruta="no_existo.py")
        assert not result.success

    async def test_trunca_archivos_largos(self):
        # DEVELOPMENT_NOTES.md supera con creces el tope de lectura por voz.
        result = await LeerArchivoProyectoTool().execute(_ctx(), ruta="DEVELOPMENT_NOTES.md")
        assert result.success
        assert "truncado" in result.content
        # 8000 chars de contenido + cabecera + nota de truncamiento
        assert len(result.content) < 8300

    async def test_rechaza_binarios(self):
        import struct
        import tempfile
        import os
        binpath = PROJECT_ROOT / "_test_binario_tmp.bin"
        try:
            binpath.write_bytes(b"\x00\x01\x02\x03" * 64)
            result = await LeerArchivoProyectoTool().execute(_ctx(), ruta=binpath.name)
            assert not result.success
        finally:
            if binpath.exists():
                os.unlink(binpath)


def test_plugin_setup_registra_ambas_tools():
    from src.plugins.workspace import setup
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry()
    setup(registry, {})
    names = {t.name for t in registry.get_all_tools()}
    assert {"listar_archivos_proyecto", "leer_archivo_proyecto"} <= names

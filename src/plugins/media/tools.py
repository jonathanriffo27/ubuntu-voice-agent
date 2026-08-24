import asyncio
import re
import shutil
import subprocess
import time
import urllib.parse
from typing import Dict, Any, Optional
from src.tools.base import BaseTool, ToolContext, ToolResult
from src.utils.logging import get_logger

logger = get_logger("plugins.media")


def _execute_mpris_command(method: str, args: list = None, player: str = "spotify") -> tuple[bool, str]:
    """Ejecuta un comando D-Bus MPRIS para controlar reproductores multimedia en Linux."""
    cmd = [
        "dbus-send",
        "--print-reply",
        f"--dest=org.mpris.MediaPlayer2.{player}",
        "/org/mpris/MediaPlayer2",
        f"org.mpris.MediaPlayer2.Player.{method}"
    ] + (args or [])

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            return True, res.stdout
        return False, res.stderr
    except subprocess.TimeoutExpired:
        return False, "Timeout esperando respuesta de D-Bus."
    except Exception as e:
        return False, str(e)


async def _ensure_spotify_ready(timeout: float = 3.0) -> bool:
    """Verifica si Spotify está activo en D-Bus; si no, lo inicia y espera de forma asíncrona a que esté listo."""
    ok, _ = _execute_mpris_command("Pause", player="spotify")
    if ok:
        return True

    logger.info("Iniciando Spotify en segundo plano...")
    try:
        subprocess.Popen(
            ["spotify"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        logger.error(f"Error al lanzar Spotify: {e}")
        return False

    # Esperar activamente a que el servicio D-Bus aparezca
    start_t = time.time()
    while time.time() - start_t < timeout:
        await asyncio.sleep(0.3)
        ok, _ = _execute_mpris_command("Pause", player="spotify")
        if ok:
            logger.info("Spotify listo y conectado a D-Bus.")
            return True

    return False


def _get_spotify_metadata(player: str = "spotify") -> Dict[str, str]:
    """Obtiene la información de la canción actual desde D-Bus."""
    cmd = [
        "busctl",
        "--user",
        "get-property",
        f"org.mpris.MediaPlayer2.{player}",
        "/org/mpris/MediaPlayer2",
        "org.mpris.MediaPlayer2.Player",
        "Metadata"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        output = res.stdout

        title_m = re.search(r'"xesam:title"\s+s\s+"([^"]+)"', output)
        artist_m = re.search(r'"xesam:artist"\s+as\s+\d+\s+"([^"]+)"', output)
        album_m = re.search(r'"xesam:album"\s+s\s+"([^"]+)"', output)

        return {
            "title": title_m.group(1) if title_m else "Desconocido",
            "artist": artist_m.group(1) if artist_m else "Desconocido",
            "album": album_m.group(1) if album_m else "Desconocido"
        }
    except Exception:
        return {"title": "Desconocido", "artist": "Desconocido", "album": "Desconocido"}


class ControlarMusicaTool(BaseTool):
    """Controla la reproducción de música (Play, Pausa, Siguiente, Anterior, Estado) vía MPRIS D-Bus."""

    @property
    def name(self) -> str:
        return "controlar_musica"

    @property
    def description(self) -> str:
        return (
            "Controla la reproducción de música en Spotify o cualquier reproductor de Linux. "
            "Acciones soportadas: 'play_pause', 'play', 'pausar', 'siguiente', 'anterior', 'stop', 'que_suena'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "accion": {
                    "type": "STRING",
                    "enum": ["play_pause", "play", "pausar", "siguiente", "anterior", "stop", "que_suena"],
                    "description": "La acción multimedia a realizar (ej. 'play', 'pausar', 'siguiente', 'que_suena')."
                },
                "reproductor": {
                    "type": "STRING",
                    "description": "El nombre del reproductor (por defecto: 'spotify').",
                    "default": "spotify"
                }
            },
            "required": ["accion"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        accion = kwargs.get("accion", "play_pause").lower().strip()
        player = kwargs.get("reproductor", "spotify").lower().strip()

        # Mapeo a métodos estándar MPRIS
        method_map = {
            "play_pause": "PlayPause",
            "play": "Play",
            "pausar": "Pause",
            "siguiente": "Next",
            "anterior": "Previous",
            "stop": "Stop"
        }

        if accion == "que_suena":
            meta = _get_spotify_metadata(player)
            if meta["title"] != "Desconocido":
                return ToolResult(
                    success=True,
                    content=f"Actualmente sonando en Spotify: '{meta['title']}' de {meta['artist']} (Álbum: {meta['album']})."
                )
            return ToolResult(success=True, content="No hay ninguna canción reproduciéndose actualmente en Spotify.")

        mpris_method = method_map.get(accion, "PlayPause")
        ok, err = _execute_mpris_command(mpris_method, player=player)

        if not ok and player == "spotify":
            # Si Spotify no estaba abierto, iniciarlo y esperar de forma asíncrona
            ready = await _ensure_spotify_ready()
            if ready:
                ok, err = _execute_mpris_command(mpris_method, player=player)

        if not ok:
            return ToolResult(success=False, content=f"No se pudo ejecutar la acción '{accion}' en {player}: {err}")

        # Mensajes de éxito amigables
        mensajes_exito = {
            "play_pause": "Se alternó la reproducción (Play/Pausa) en Spotify.",
            "play": "Reproducción reanudada en Spotify.",
            "pausar": "Música pausada en Spotify.",
            "siguiente": "Pasando a la siguiente canción en Spotify.",
            "anterior": "Volviendo a la canción anterior en Spotify.",
            "stop": "Reproducción detenida en Spotify."
        }

        return ToolResult(success=True, content=mensajes_exito.get(accion, f"Acción '{accion}' ejecutada en {player}."))


class ReproducirMusicaTool(BaseTool):
    """Busca y reproduce una canción, artista, álbum o playlist en Spotify."""

    @property
    def name(self) -> str:
        return "reproducir_musica"

    @property
    def description(self) -> str:
        return (
            "Busca y reproduce una canción, artista, álbum, playlist o género en Spotify. "
            "Úsalo cuando el usuario te pida: 'pon música de X', 'reproduce Bohemian Rhapsody', 'pon una playlist de rock'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "busqueda": {
                    "type": "STRING",
                    "description": "El nombre de la canción, artista, álbum o playlist a buscar y reproducir (ej. 'Queen', 'Lofi beats', 'Coldplay')."
                }
            },
            "required": ["busqueda"]
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        busqueda = kwargs.get("busqueda", "").strip()
        if not busqueda:
            return ToolResult(success=False, content="Falta la búsqueda o nombre de la canción/artista.")

        # 1. Asegurar que Spotify esté abierto y respondiendo en D-Bus
        await _ensure_spotify_ready()

        try:
            # 2. Enviar URI de búsqueda a Spotify mediante D-Bus OpenUri
            uri = f"spotify:search:{urllib.parse.quote(busqueda)}"
            ok, err = _execute_mpris_command("OpenUri", args=[f"string:{uri}"], player="spotify")

            if not ok:
                # Fallback: abrir con xdg-open spotify:search:...
                subprocess.Popen(["xdg-open", f"spotify:search:{busqueda}"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                await asyncio.sleep(0.5)

            # 3. Enviar señal Play
            _execute_mpris_command("Play", player="spotify")

            # 4. Simular pulsación de Enter si ydotool está instalado para iniciar el primer resultado
            if shutil.which("ydotool"):
                try:
                    subprocess.run(["ydotool", "key", "28:1", "28:0"], capture_output=True)
                except Exception:
                    pass

            return ToolResult(
                success=True,
                content=f"Buscando y reproduciendo '{busqueda}' en Spotify."
            )
        except Exception as e:
            logger.error(f"Error al reproducir en Spotify: {e}")
            return ToolResult(success=False, content=f"Error al intentar reproducir en Spotify: {e}")

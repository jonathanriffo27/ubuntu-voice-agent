import re
from html import unescape
import httpx
from src.utils.logging import get_logger

logger = get_logger("plugins.browser.reader")


class WebPageReader:
    """
    Extractor y limpiador de páginas web.
    Convierte HTML en texto plano legible eliminando scripts, estilos, menús y publicidad.
    """

    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

    async def read_url(self, url: str, max_chars: int = 4000) -> str:
        """Descarga una URL y devuelve el texto limpio de su contenido principal."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=self.headers) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text

            # 1. Eliminar etiquetas no textuales o de estructura secundaria
            clean = re.sub(r'<(script|style|nav|header|footer|aside|noscript|svg)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
            # 2. Reemplazar saltos de bloque por nuevas líneas
            clean = re.sub(r'<(p|h[1-6]|li|tr|div|article|section)[^>]*>', '\n', clean, flags=re.IGNORECASE)
            # 3. Eliminar resto de etiquetas HTML
            clean = re.sub(r'<[^>]+>', ' ', clean)
            # 4. Desescapar entidades HTML
            clean = unescape(clean)
            # 5. Normalizar espacios en blanco repetidos
            clean = re.sub(r'[ \t]+', ' ', clean)
            clean = re.sub(r'\n\s*\n+', '\n\n', clean).strip()

            if len(clean) > max_chars:
                clean = clean[:max_chars] + "\n...[Contenido truncado]"

            return clean if clean else "No se pudo extraer texto legible de la página."

        except httpx.HTTPStatusError as e:
            return f"Error HTTP {e.response.status_code} al acceder a {url}."
        except Exception as e:
            logger.warning(f"Error leyendo página {url}: {e}")
            return f"Error al leer la página: {str(e)}"

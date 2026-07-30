import datetime
import os
import subprocess
import shutil
from typing import Dict, Any

from src.tools.base import BaseTool, ToolContext, ToolResult

def detect_user_location() -> str | None:
    _TZ_TO_COUNTRY = {
        "America/Santiago": "Chile", "America/Punta_Arenas": "Chile",
        "America/Argentina": "Argentina", "America/Buenos_Aires": "Argentina",
        "America/Sao_Paulo": "Brasil", "America/Fortaleza": "Brasil",
        "America/Mexico_City": "México", "America/Cancun": "México",
        "America/Bogota": "Colombia", "America/Lima": "Perú",
        "America/Caracas": "Venezuela", "America/Guayaquil": "Ecuador",
        "America/Montevideo": "Uruguay", "America/Asuncion": "Paraguay",
        "America/La_Paz": "Bolivia", "America/Panama": "Panamá",
        "America/Costa_Rica": "Costa Rica", "America/Havana": "Cuba",
        "America/Santo_Domingo": "República Dominicana",
        "Europe/Madrid": "España", "America/New_York": "Estados Unidos",
        "America/Los_Angeles": "Estados Unidos", "America/Chicago": "Estados Unidos",
    }
    tz = None
    try:
        with open('/etc/timezone') as f:
            tz = f.read().strip()
    except Exception:
        try:
            tz = subprocess.check_output(
                ['timedatectl', 'show', '-p', 'Timezone', '--value'],
                text=True
            ).strip()
        except Exception:
            pass
    if not tz:
        return None
    if tz in _TZ_TO_COUNTRY:
        return _TZ_TO_COUNTRY[tz]
    for prefix, country in _TZ_TO_COUNTRY.items():
        if tz.startswith(prefix.rsplit('/', 1)[0]):
            return country
    return tz

class EstadoSistemaTool(BaseTool):
    @property
    def name(self) -> str:
        return "obtener_estado_sistema"

    @property
    def description(self) -> str:
        return "Obtiene la hora actual."

    @property
    def parameters(self) -> Dict[str, Any]:
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        data = {
            "hora_actual": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "plataforma": os.uname().sysname,
            "usuario": os.getlogin(),
            "ubicacion": detect_user_location() or "desconocida"
        }
        return ToolResult(success=True, content=str(data), metadata=data)

class ImprimirConsolaTool(BaseTool):
    @property
    def name(self) -> str:
        return "imprimir_en_consola"

    @property
    def description(self) -> str:
        return "Imprime un texto, código, tabla o información detallada en la terminal para que el usuario pueda leerlo. Útil cuando la respuesta es muy larga o contiene formato que se pierde al hablar."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "texto": {"type": "STRING", "description": "El texto a imprimir"}
            }, 
            "required": ["texto"]
        }

    async def execute(self, context: ToolContext, texto: str = None) -> ToolResult:
        if not texto:
            return ToolResult(success=False, content="Falta el texto a imprimir.")
        print(f"\n[ATLAS DICE]:\n{texto}\n")
        return ToolResult(success=True, content="Texto impreso en la consola correctamente.")

class AbrirAplicacionTool(BaseTool):
    @property
    def name(self) -> str:
        return "abrir_aplicacion"

    @property
    def description(self) -> str:
        return "Abre una aplicación instalada en el sistema (ej. calculadora, terminal, code) o abre páginas web conocidas en el navegador (ej. gmail, youtube, whatsapp)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "nombre": {"type": "STRING", "description": "El nombre de la aplicación o sitio web a abrir"}
            }, 
            "required": ["nombre"]
        }

    async def execute(self, context: ToolContext, nombre: str = None) -> ToolResult:
        if not nombre:
            return ToolResult(success=False, content="Falta el nombre.")
            
        print(f"\n🚀 [ATLAS ABRIENDO APLICACIÓN]: {nombre}")
        nombre_lower = nombre.lower().strip()
        
        mapeos = {
            "gmail": "xdg-open 'https://mail.google.com'",
            "youtube": "xdg-open 'https://youtube.com'",
            "netflix": "xdg-open 'https://netflix.com'",
            "whatsapp": "xdg-open 'https://web.whatsapp.com'",
            "calculadora": "gnome-calculator",
            "terminal": "gnome-terminal",
            "navegador": "xdg-open 'https://google.com'",
            "archivos": "nautilus",
            "carpetas": "nautilus",
            "vscode": "code",
            "code": "code",
            "spotify": "spotify",
            "discord": "discord"
        }
        
        cmd = mapeos.get(nombre_lower)
        if not cmd:
            if shutil.which(nombre_lower):
                cmd = nombre_lower
            else:
                print(f"  ❌ No se encontró la aplicación '{nombre}'")
                return ToolResult(success=False, content=f"No conozco la aplicación '{nombre}' ni tengo un comando instalado con ese nombre.")
                
        try:
            subprocess.Popen(cmd, shell=True, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  ✅ Aplicación '{nombre}' lanzada correctamente.")
            return ToolResult(success=True, content=f"He abierto {nombre} en tu sistema.")
        except Exception as e:
            print(f"  ❌ Error al abrir {nombre}: {e}")
            return ToolResult(success=False, content=f"Falló al intentar abrir {nombre}: {e}")

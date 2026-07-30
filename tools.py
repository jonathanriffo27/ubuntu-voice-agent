import subprocess
import re
import datetime
import os
import time

# --- MÁQUINA DE ESTADOS Y SEGURIDAD ---
class CommandManager:
    def __init__(self):
        self.pending_command = None
        self.pending_timestamp = 0
        self.timeout_seconds = 30 # El comando expira en 30s

    def propose(self, command: str) -> str:
        self.pending_command = command
        self.pending_timestamp = time.time()
        return f"Comando '{command}' propuesto. Esperando confirmación hablada del usuario."

    def get_pending(self) -> str | None:
        if self.pending_command is None:
            return None
        
        # Verificar timeout
        if time.time() - self.pending_timestamp > self.timeout_seconds:
            self.pending_command = None
            return None
            
        return self.pending_command

    def clear(self):
        self.pending_command = None

cmd_manager = CommandManager()

# Lista negra dura (Regex base) para comandos destructivos evidentes
BLACKLIST_PATTERNS = [
    r"rm\s+-r?[fF]",          # rm -rf, rm -f
    r"mkfs",                  # Formatear
    r"dd\s+if=",              # Sobrescribir discos
    r">\s*/dev/sd[a-z]",      # Redirigir a discos físicos
    r"wget.*\|\s*bash",       # wget | bash
    r"curl.*\|\s*bash",       # curl | bash
    r":\(\)\{.*\}",           # Fork bomb
    r"sudo\s+(passwd|rm|su)"  # Escaladas de privilegios peligrosas
]

def _is_safe_command(cmd: str) -> bool:
    for pattern in BLACKLIST_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return False
    return True

def _redact_secrets(text: str) -> str:
    """Filtra posibles secretos del texto de salida antes de enviarlo al modelo."""
    # Buscar patrones tipo CLAVE=valor o "token": "valor"
    # Redacta todo lo que hay después del igual/dos_puntos que parezca un hash/clave larga
    redacted = re.sub(
        r"(?i)(api_key|password|secret|token|auth|cred)([\s=:\"]+)[A-Za-z0-9\-_]{16,}", 
        r"\1\2[REDACTADO]", 
        text
    )
    return redacted
import json

# --- MEMORIA PERSISTENTE ---
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_memory.json")
MAX_NOTAS = 20              # Máximo de notas generales
MAX_NOTA_LENGTH = 200       # Caracteres máximos por nota
MAX_PERFIL_CAMPOS = 10      # Máximo de campos en el perfil
MAX_PERFIL_VALOR_LENGTH = 100  # Caracteres máximos por valor de perfil

# Mapeo de zonas horarias a países (las más comunes de Latinoamérica y España)
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

def detect_user_location() -> str | None:
    """Detecta el país del usuario basándose en la zona horaria del sistema."""
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
    
    # Buscar coincidencia exacta o por prefijo
    if tz in _TZ_TO_COUNTRY:
        return _TZ_TO_COUNTRY[tz]
    for prefix, country in _TZ_TO_COUNTRY.items():
        if tz.startswith(prefix.rsplit('/', 1)[0]):
            return country
    return tz  # Fallback: devolver el nombre de la zona horaria

def cargar_memoria() -> dict:
    """Carga la memoria completa: perfil (permanente) + notas (rotativas)."""
    default = {"perfil": {}, "notas": []}
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Migración: formato antiguo solo tenía "notas" como lista
            if isinstance(data, dict):
                if "perfil" not in data:
                    data["perfil"] = {}
                if "notas" not in data:
                    data["notas"] = []
                return data
    except Exception:
        pass
    return default

def _guardar_memoria(datos: dict):
    """Escribe la estructura completa de memoria al archivo."""
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)

def guardar_perfil(campo: str, valor: str) -> dict:
    """
    Guarda un dato permanente del usuario (nombre, ocupación, intereses, etc.).
    Estos datos NUNCA se eliminan automáticamente. Usa campos cortos y descriptivos.
    """
    campo = campo.strip().lower()
    if not campo:
        return {"status": "error", "message": "El campo no puede estar vacío."}
    
    if len(valor) > MAX_PERFIL_VALOR_LENGTH:
        valor = valor[:MAX_PERFIL_VALOR_LENGTH] + "…"
    
    datos = cargar_memoria()
    perfil = datos["perfil"]
    
    if campo not in perfil and len(perfil) >= MAX_PERFIL_CAMPOS:
        return {"status": "error", "message": f"Perfil lleno ({MAX_PERFIL_CAMPOS} campos). Borra alguno antes de agregar otro."}
    
    perfil[campo] = valor
    try:
        _guardar_memoria(datos)
        print(f"\n💾 [PERFIL] {campo}: {valor}")
        return {"status": "success", "message": f"Perfil actualizado: {campo} = {valor}. Total: {len(perfil)}/{MAX_PERFIL_CAMPOS} campos."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def guardar_nota(nota: str) -> dict:
    """
    Guarda una nota general que Jarvis recordará en futuras sesiones.
    Para datos personales permanentes (nombre, ocupación), usa 'guardar_perfil' en su lugar.
    """
    if len(nota) > MAX_NOTA_LENGTH:
        nota = nota[:MAX_NOTA_LENGTH] + "…"
    
    datos = cargar_memoria()
    notas = datos["notas"]
    
    if nota in notas:
        return {"status": "success", "message": f"Esa nota ya existe. Total: {len(notas)}/{MAX_NOTAS}."}
    
    # FIFO: eliminar la más antigua si se alcanzó el límite
    eliminada = None
    if len(notas) >= MAX_NOTAS:
        eliminada = notas.pop(0)
    
    notas.append(nota)
    try:
        _guardar_memoria(datos)
        msg = f"Nota guardada ({len(notas)}/{MAX_NOTAS})."
        if eliminada:
            msg += f" Se eliminó la más antigua: '{eliminada[:50]}…'"
        print(f"\n💾 [MEMORIA] {msg}")
        return {"status": "success", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def borrar_nota(indice: int) -> dict:
    """
    Borra una nota general por su número (1-indexado).
    Usa esto cuando el usuario pida olvidar algo o cuando una nota ya no sea relevante.
    """
    datos = cargar_memoria()
    notas = datos["notas"]
    if not notas:
        return {"status": "error", "message": "No hay notas guardadas."}
    if indice < 1 or indice > len(notas):
        return {"status": "error", "message": f"Índice inválido. Hay {len(notas)} notas (1 a {len(notas)})."}
    
    eliminada = notas.pop(indice - 1)
    try:
        _guardar_memoria(datos)
        print(f"\n🗑️ [MEMORIA] Nota eliminada: {eliminada}")
        return {"status": "success", "message": f"Nota '{eliminada[:50]}' eliminada. Quedan {len(notas)}/{MAX_NOTAS}."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- HERRAMIENTAS EXPORTADAS ---

def obtener_estado_sistema() -> dict:
    """Obtiene información básica del sistema para Jarvis."""
    return {
        "status": "success",
        "hora_actual": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plataforma": os.uname().sysname,
        "usuario": os.getlogin(),
        "ubicacion": detect_user_location() or "desconocida"
    }

def proponer_comando(comando: str) -> dict:
    """
    El modelo llama a esto cuando quiere ejecutar algo en Bash. 
    NO ejecuta el comando. Lo guarda y espera que el usuario confirme por voz.
    """
    if not _is_safe_command(comando):
        return {"status": "error", "message": f"El comando '{comando}' está bloqueado por seguridad en código duro."}
    
    mensaje = cmd_manager.propose(comando)
    print(f"\n⚠️ [JARVIS PROPONE]: {comando}\n (Esperando confirmación...)")
    return {"status": "pending", "message": f"{mensaje} CRÍTICO: DETENTE AQUÍ. NO llames a ejecutar_comando_confirmado. Espera a escuchar la respuesta por voz del usuario."}

def ejecutar_comando_confirmado() -> dict:
    """
    El modelo llama a esto SOLAMENTE después de que el usuario haya dicho 'sí', 'procede', etc.
    Ejecuta el último comando propuesto si no ha expirado.
    """
    cmd = cmd_manager.get_pending()
    
    if not cmd:
        print("\n❌ [JARVIS ERROR]: Se intentó confirmar sin comando pendiente válido.")
        return {"status": "error", "message": "No hay ningún comando pendiente o el tiempo de confirmación expiró."}
    
    # Doble chequeo de seguridad por si acaso
    if not _is_safe_command(cmd):
        cmd_manager.clear()
        return {"status": "error", "message": "El comando pendiente viola las reglas de seguridad."}

    print(f"\n✅ [EJECUTANDO]: {cmd}")
    cmd_manager.clear() # Limpiar estado
    
    try:
        result = subprocess.run(cmd, shell=True, check=True, text=True, capture_output=True, timeout=15)
        output = result.stdout if result.stdout else "Comando ejecutado exitosamente sin salida."
        
        # Redactar secretos y truncar
        safe_output = _redact_secrets(output)
        
        # Mostrar el output al usuario en la terminal
        print(f"\n[SALIDA DEL COMANDO]\n{safe_output}")
        print("-" * 40)
        
        if len(safe_output) > 2000:
            safe_output = safe_output[:2000] + "\n...[TRUNCATED]"
            
        return {"status": "success", "output": safe_output}
        
    except subprocess.CalledProcessError as e:
        error_out = _redact_secrets(e.stderr)
        return {"status": "error", "codigo": e.returncode, "salida": error_out}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "El comando tardó demasiado y fue cancelado (>15s)."}

def imprimir_en_consola(texto: str) -> dict:
    """
    Imprime un mensaje en la consola de la terminal para que el usuario pueda leerlo.
    Útil para mostrar información larga, código, tablas o detalles que son difíciles de dictar por voz.
    """
    print(f"\n[JARVIS DICE]:\n{texto}\n")
    return {"status": "success", "message": "Texto impreso en la consola correctamente."}

def _extract_page_text(html_raw, query):
    """Extrae texto limpio y relevante de HTML crudo."""
    import re
    html = html_raw
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<nav[^>]*>.*?</nav>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<header[^>]*>.*?</header>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<!--.*?-->', ' ', html, flags=re.DOTALL)
    html = re.sub(r'<[^>]+>', ' ', html)
    html = re.sub(r'&[a-zA-Z]+;', ' ', html)
    html = re.sub(r'&#\d+;', ' ', html)
    html = re.sub(r'\s+', ' ', html).strip()
    
    # Buscar la ventana de texto más relevante según las palabras del query
    query_words = [w.lower() for w in query.split() if len(w) > 3]
    best_pos = 0
    best_score = 0
    for pos in range(0, min(len(html), 5000), 200):
        window = html[pos:pos+800].lower()
        score = sum(1 for w in query_words if w in window)
        if score > best_score:
            best_score = score
            best_pos = pos
    
    text = html[best_pos:best_pos+1500]
    
    # Descartar páginas que requieren JS
    skip_indicators = ['javascript is disabled', 'enable javascript', 'captcha', 'verify you are human']
    if any(ind in text.lower() for ind in skip_indicators):
        return None
    
    return text if len(text) > 100 else None


def buscar_en_internet(query: str) -> dict:
    """
    Realiza una búsqueda en internet usando la API de Tavily.
    Tavily está diseñado para IAs, proporciona respuestas directas y extrae el contenido clave de las páginas web automáticamente.
    """
    print(f"\n🔍 [JARVIS BUSCANDO EN INTERNET (TAVILY)]: {query}")
    try:
        import urllib.request
        import urllib.error
        import json
        
        url = 'https://api.tavily.com/search'
        data = {
            'api_key': os.environ.get('TAVILY_API_KEY', ''),
            'query': query,
            'search_depth': 'basic',
            'include_answer': True,
            'include_raw_content': False,
            'max_results': 3
        }
        
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
        
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            output = f"Resultados de búsqueda para: {query}\n\n"
            
            # Tavily usually provides a direct answer if 'include_answer' is True
            answer = result.get('answer')
            if answer:
                output += f"Respuesta Directa:\n{answer}\n\n"
                
            output += "Fuentes y Detalles Adicionales:\n"
            for i, r in enumerate(result.get('results', [])):
                title = r.get('title', 'Sin título')
                content = r.get('content', '')
                output += f"{i+1}. {title}\n   {content}\n"
            
            print(f"  ✅ [TAVILY] Búsqueda exitosa ({len(output)} chars)")
            
            # Redactar secretos por seguridad
            safe_output = _redact_secrets(output)
            if len(safe_output) > 2000:
                safe_output = safe_output[:2000] + "\n...[información truncada]"
                
            return {"result": safe_output}
            
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
        print(f"  ❌ [TAVILY] Error HTTP {e.code}: {err_msg[:100]}")
        return {"status": "error", "message": f"Fallo en la búsqueda de Tavily: HTTP {e.code}"}
    except Exception as e:
        print(f"  ❌ [TAVILY] Error: {e}")
        return {"status": "error", "message": f"Fallo en la búsqueda: {str(e)}"}



def abrir_aplicacion(nombre: str) -> dict:
    """
    Abre una aplicación de escritorio o una página web conocida (como gmail).
    """
    import subprocess
    import shutil
    
    print(f"\n🚀 [JARVIS ABRIENDO APLICACIÓN]: {nombre}")
    
    nombre_lower = nombre.lower().strip()
    
    # Mapeos predefinidos para cosas comunes web y apps locales
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
        # Si no está en el mapa, intentamos ver si existe el ejecutable literal
        if shutil.which(nombre_lower):
            cmd = nombre_lower
        else:
            print(f"  ❌ No se encontró la aplicación '{nombre}'")
            return {"status": "error", "message": f"No conozco la aplicación '{nombre}' ni tengo un comando instalado con ese nombre."}
            
    try:
        # Ejecutamos en segundo plano (detach) para que no bloquee a Jarvis
        subprocess.Popen(cmd, shell=True, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  ✅ Aplicación '{nombre}' lanzada correctamente.")
        return {"status": "success", "message": f"He abierto {nombre} en tu sistema."}
    except Exception as e:
        print(f"  ❌ Error al abrir {nombre}: {e}")
        return {"status": "error", "message": f"Falló al intentar abrir {nombre}: {e}"}

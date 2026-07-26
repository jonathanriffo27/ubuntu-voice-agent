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

# --- HERRAMIENTAS EXPORTADAS ---

def obtener_estado_sistema() -> dict:
    """Obtiene información básica del sistema para Jarvis."""
    return {
        "status": "success",
        "hora_actual": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plataforma": os.uname().sysname,
        "usuario": os.getlogin()
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
    return {"status": "pending", "message": mensaje}

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

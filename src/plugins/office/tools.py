import os
import pathlib
import subprocess
import shutil
import asyncio
from typing import Dict, Any, Optional
from src.tools.base import BaseTool, ToolContext, ToolResult
from src.utils.logging import get_logger

logger = get_logger("plugins.office")
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _resolver_ruta_docx(nombre: str, directorio_destino: str) -> str:
    """
    Genera la ruta final del documento sin doble extensión (.docx.docx) y
    SIN sobrescribir archivos existentes del usuario (añade sufijo _2, _3...).
    """
    base = nombre.strip()
    if base.lower().endswith(".docx"):
        base = base[:-5]
    if not base:
        base = "documento"

    ruta = os.path.join(directorio_destino, f"{base}.docx")
    contador = 2
    while os.path.exists(ruta):
        ruta = os.path.join(directorio_destino, f"{base}_{contador}.docx")
        contador += 1
    return ruta


async def _generate_content_resilient(prompt: str) -> str:
    """
    Genera contenido de texto de forma resiliente con fallback automático entre proveedores y modelos.
    1. Intenta primero vía CLIProxy (gemini-3.8-flash-high en el servidor Oracle), que tiene alta disponibilidad.
    2. Si falla CLIProxy, intenta vía google.genai rotando modelos (gemini-2.5-flash, gemini-2.0-flash, gemini-3.7-flash)
       para eludir errores 503 UNAVAILABLE de Google AI Studio.
    """
    # 1. Intentar con CLIProxyClient (servidor Oracle / túnel local)
    try:
        from src.agents.client import CLIProxyClient
        cli_client = CLIProxyClient(timeout=120.0)
        resp = await cli_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model="gemini-3.8-flash-high",
            temperature=0.3
        )
        choices = resp.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")
            if text and text.strip():
                return text.strip()
    except Exception as e_cliproxy:
        logger.warning(f"CLIProxy no disponible o falló ({e_cliproxy}). Usando Google GenAI como fallback...")

    # 2. Fallback a Google GenAI con modelos alternativos
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("No se pudo contactar CLIProxy y GEMINI_API_KEY no está configurada.")

    from google import genai
    client = genai.Client(api_key=api_key)

    # gemini-2.5-flash y gemini-2.0-flash son sumamente estables frente a sobrecargas de 3.7
    models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-3.7-flash"]
    last_err = None

    for model_name in models_to_try:
        try:
            logger.info(f"Intentando generar informe con Google GenAI [{model_name}]...")
            res = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if res and res.text:
                return res.text
        except Exception as e:
            last_err = e
            logger.warning(f"Google GenAI [{model_name}] falló ({e}). Probando siguiente...")
            await asyncio.sleep(1.5)

    raise last_err or RuntimeError("No se pudo generar contenido con ningún proveedor ni modelo disponible.")


def _get_ydotool_env() -> Dict[str, str]:
    env = os.environ.copy()
    uid = os.getuid()
    env["YDOTOOL_SOCKET"] = f"/run/user/{uid}/.ydotool_socket"
    return env


async def _emit_ydotool_keys(*key_combos: str) -> bool:
    env = _get_ydotool_env()
    args = ["ydotool", "key"]
    for combo in key_combos:
        args.extend(combo.split())
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"Error ejecutando ydotool key: {e}")
        return False


async def _copy_clipboard_async(text: str) -> bool:
    try:
        if shutil.which("wl-copy"):
            proc = await asyncio.create_subprocess_exec(
                "wl-copy",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate(input=text.encode("utf-8"))
            return proc.returncode == 0
        elif shutil.which("xclip"):
            proc = await asyncio.create_subprocess_exec(
                "xclip", "-selection", "clipboard",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate(input=text.encode("utf-8"))
            return proc.returncode == 0
    except Exception as e:
        logger.error(f"Error copiando al portapapeles: {e}")
    return False


async def _paste_clipboard_async() -> str:
    try:
        if shutil.which("wl-paste"):
            proc = await asyncio.create_subprocess_exec(
                "wl-paste",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await proc.communicate()
            return out.decode("utf-8", errors="replace")
        elif shutil.which("xclip"):
            proc = await asyncio.create_subprocess_exec(
                "xclip", "-selection", "clipboard", "-o",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await proc.communicate()
            return out.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Error leyendo del portapapeles: {e}")
    return ""


class CrearDocumentoOfficeTool(BaseTool):
    """
    Crea un documento de Word (.docx) con el título y contenido especificado
    y lo abre automáticamente en OnlyOffice.
    """
    
    @property
    def name(self) -> str:
        return "crear_documento_onlyoffice"
        
    @property
    def description(self) -> str:
        return "Crea un informe o documento de texto en formato DOCX y lo abre automáticamente en OnlyOffice. Úsalo cuando el usuario pida redactar un informe, reporte o documento formal."
        
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "titulo": {
                    "type": "STRING",
                    "description": "El título principal del documento"
                },
                "tema": {
                    "type": "STRING",
                    "description": "El tema detallado o las instrucciones sobre lo que debe tratar el informe. Explica bien el objetivo."
                },
                "nombre_archivo": {
                    "type": "STRING",
                    "description": "Nombre del archivo a guardar (sin la extensión .docx). Ej: 'informe_ventas'"
                }
            },
            "required": ["titulo", "tema", "nombre_archivo"]
        }
        
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        try:
            import docx
        except ImportError:
            return ToolResult(
                success=False,
                content="La librería python-docx no está instalada. Dile al usuario que instale las dependencias con 'pip install -r requirements.txt'."
            )
            
        titulo = kwargs.get("titulo", "Documento Sin Título")
        tema = kwargs.get("tema", kwargs.get("contenido", "Un tema general"))
        nombre_crudo = str(kwargs.get("nombre_archivo", "documento"))
        nombre = os.path.basename(nombre_crudo).strip()
        if not nombre or ".." in nombre:
            nombre = "documento"
            
        # Fast path para documentos en blanco
        tema_lower = tema.lower()
        if "blanco" in tema_lower or "vacío" in tema_lower or "vacio" in tema_lower:
            doc = docx.Document()
            doc.add_heading(titulo, 0)
            
            directorio_destino = os.path.expanduser("~/Documentos")
            if not os.path.exists(directorio_destino):
                directorio_destino = os.path.expanduser("~/Documents")
                if not os.path.exists(directorio_destino):
                    directorio_destino = os.path.expanduser("~")

            ruta_archivo = _resolver_ruta_docx(nombre, directorio_destino)

            try:
                doc.save(ruta_archivo)
            except Exception as e:
                return ToolResult(success=False, content=f"Error al guardar el documento: {e}")
                
            try:
                subprocess.Popen(["onlyoffice-desktopeditors", ruta_archivo], start_new_session=True)
            except Exception:
                pass
                
            return ToolResult(
                success=True, 
                content=f"Documento '{titulo}' creado exitosamente en {ruta_archivo} y abierto en OnlyOffice."
            )

        # Para documentos con contenido, lanzar en segundo plano con generador resiliente
        async def _background_task():
            try:
                prompt = (
                    f"El usuario necesita crear un documento con la siguiente instrucción o tema: {tema}. "
                    "Redacta el contenido basándote estrictamente en lo que pide. Si pide un informe largo, hazlo detallado. "
                    "Si pide algo breve, hazlo breve. Redáctalo en texto plano estructurado, separado por saltos de línea. "
                    "NO uses formato markdown (sin asteriscos para negritas ni hashtags)."
                )

                contenido_crudo = await _generate_content_resilient(prompt)
                contenido = contenido_crudo.replace("\\n", "\n").replace("\\r", "")

                doc = docx.Document()
                doc.add_heading(titulo, 0)
                
                for parrafo in contenido.split('\n'):
                    if parrafo.strip():
                        doc.add_paragraph(parrafo.strip())
                        
                directorio_destino = os.path.expanduser("~/Documentos")
                if not os.path.exists(directorio_destino):
                    directorio_destino = os.path.expanduser("~/Documents")
                    if not os.path.exists(directorio_destino):
                        directorio_destino = os.path.expanduser("~")

                ruta_archivo = _resolver_ruta_docx(nombre, directorio_destino)
                doc.save(ruta_archivo)
                subprocess.Popen(["onlyoffice-desktopeditors", ruta_archivo], start_new_session=True)
                print(f"\n[ATLAS DICE]:\n✅ ¡El informe '{titulo}' ha terminado de redactarse y lo acabo de abrir en tu pantalla!\n")
                if context.event_bus:
                    from src.events.base import SystemNotification, ConversationContext
                    context.event_bus.publish(SystemNotification(
                        context=context.conversation_context or ConversationContext(),
                        message=f"El informe '{titulo}' ha terminado de redactarse y lo acabo de abrir en tu pantalla. Notifícaselo brevemente al usuario."
                    ))
            except Exception as e:
                import traceback
                with open(_PROJECT_ROOT / "genai_error.txt", "w") as f:
                    f.write(f"Error en tarea de fondo: {e}\n")
                    f.write(traceback.format_exc())
                print(f"\n[ATLAS DICE]:\n❌ Hubo un error redactando el informe '{titulo}'. Revisar log.\n")
                if context.event_bus:
                    from src.events.base import SystemNotification, ConversationContext
                    context.event_bus.publish(SystemNotification(
                        context=context.conversation_context or ConversationContext(),
                        message=f"Hubo un error al redactar el informe '{titulo}'. Notifícaselo brevemente al usuario."
                    ))

        asyncio.create_task(_background_task())
        
        return ToolResult(
            success=True, 
            content="El informe se está redactando en segundo plano. Informa al usuario que ya empezaste a escribirlo de fondo, que tardará unos segundos, y que puede seguir conversando contigo de cualquier otro tema mientras tanto. Dile que el documento aparecerá solo en pantalla cuando acabe."
        )

class EscribirTextoOfficeTool(BaseTool):
    """
    Escribe o pega texto en tiempo real simulando acciones de teclado.
    Ideal para dictar contenido o modificar el documento activo en OnlyOffice.
    """
    
    @property
    def name(self) -> str:
        return "escribir_texto_tiempo_real"
        
    @property
    def description(self) -> str:
        return "Escribe, dicta o inserta texto en tiempo real en la ventana activa de OnlyOffice (o cualquier editor). Úsalo cuando el usuario pida agregar texto, dictar, o modificar el documento que ya está abierto."
        
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "texto": {
                    "type": "STRING",
                    "description": "El texto que se va a insertar o escribir en el documento."
                },
                "reemplazar_todo": {
                    "type": "BOOLEAN",
                    "description": "Si es true, borra todo el documento actual antes de pegar el nuevo texto (ideal para reemplazar el contenido entero). Si es false, simplemente inserta el texto donde esté el cursor."
                }
            },
            "required": ["texto"]
        }
        
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        texto = kwargs.get("texto", "").replace("\\n", "\n").replace("\\r", "")
        reemplazar = kwargs.get("reemplazar_todo", False)
        
        if not texto:
            return ToolResult(success=False, content="No hay texto para escribir.")
            
        try:
            # Copiar el texto al portapapeles usando wl-copy o xclip de forma asíncrona
            ok = await _copy_clipboard_async(texto)
            if not ok:
                raise Exception("No se pudo copiar el texto al portapapeles (wl-copy / xclip no disponibles).")
                
            await asyncio.sleep(0.4) # Pausa mayor para sincronizar portapapeles
            
            teclas = []
            if reemplazar:
                # Ctrl+A (Seleccionar todo) y Suprimir
                teclas.extend(["29:1 30:1 30:0 29:0", "111:1 111:0"])
                
            # Ctrl+V (Pegar)
            teclas.append("29:1 47:1 47:0 29:0")
            
            success = await _emit_ydotool_keys(*teclas)
            if not success:
                raise Exception("ydotool falló al enviar las pulsaciones de teclado.")
            
            return ToolResult(
                success=True, 
                content="Texto modificado/escrito en tiempo real correctamente en el documento activo."
            )
        except Exception as e:
            return ToolResult(success=False, content=f"Error al escribir en tiempo real con ydotool: {e}")

class ControlarTecladoOfficeTool(BaseTool):
    """
    Simula pulsaciones de teclas especiales (borrar, enter, deshacer)
    """
    
    @property
    def name(self) -> str:
        return "controlar_teclado_tiempo_real"
        
    @property
    def description(self) -> str:
        return "Presiona teclas especiales en el documento activo (OnlyOffice u otros). Úsalo cuando el usuario te pida: borrar texto, deshacer, hacer un salto de línea (enter) o seleccionar todo."
        
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "accion": {
                    "type": "STRING",
                    "enum": ["borrar", "enter", "deshacer", "seleccionar_todo", "suprimir", "arriba", "abajo", "izquierda", "derecha", "inicio", "fin", "buscar"],
                    "description": "La tecla o atajo a presionar."
                },
                "veces": {
                    "type": "INTEGER",
                    "description": "Número de veces a presionar (útil para borrar varias letras). Por defecto es 1.",
                    "default": 1
                }
            },
            "required": ["accion"]
        }
        
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        accion = kwargs.get("accion")
        veces = max(1, kwargs.get("veces", 1))
        
        # Mapeo de códigos de tecla para ydotool (códigos de kernel Linux)
        keycodes = {
            "borrar": "14:1 14:0",          # KEY_BACKSPACE
            "enter": "28:1 28:0",           # KEY_ENTER
            "suprimir": "111:1 111:0",      # KEY_DELETE
            "deshacer": "29:1 44:1 44:0 29:0", # KEY_LEFTCTRL + KEY_Z
            "seleccionar_todo": "29:1 30:1 30:0 29:0", # KEY_LEFTCTRL + KEY_A
            "arriba": "103:1 103:0",
            "abajo": "108:1 108:0",
            "izquierda": "105:1 105:0",
            "derecha": "106:1 106:0",
            "inicio": "102:1 102:0",
            "fin": "107:1 107:0",
            "buscar": "29:1 33:1 33:0 29:0" # KEY_LEFTCTRL + KEY_F
        }
        
        if accion not in keycodes:
            return ToolResult(success=False, content=f"Acción desconocida: {accion}")
            
        teclas_a_presionar = [keycodes[accion]] * veces
        
        try:
            success = await _emit_ydotool_keys(*teclas_a_presionar)
            if not success:
                raise Exception("ydotool falló al emitir teclas.")
                
            return ToolResult(
                success=True, 
                content=f"Acción '{accion}' simulada correctamente {veces} vez/veces."
            )
        except Exception as e:
            return ToolResult(success=False, content=f"Error al presionar teclas: {e}")

class EditarDocumentoInteligenteTool(BaseTool):
    """
    Herramienta avanzada de edición de texto basada en extracción JSON y reemplazo nativo UI.
    """
    @property
    def name(self) -> str:
        return "editar_documento_inteligente"
        
    @property
    def description(self) -> str:
        return "Lee el documento, extrae de forma ultra-rápida y económica qué texto cambiar (vía JSON), y usa el menú 'Buscar y Reemplazar' nativo de OnlyOffice (Ctrl+H) para conservar el 100% del formato. Úsalo para editar partes específicas del documento."
        
    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT",
            "properties": {
                "instruccion": {
                    "type": "STRING",
                    "description": "Qué es exactamente lo que el usuario quiere cambiar (ej. 'cambia el título a X', 'reemplaza el párrafo 2 con Y')."
                }
            },
            "required": ["instruccion"]
        }
        
    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        instruccion = kwargs.get("instruccion")
        if not instruccion:
            return ToolResult(success=False, content="Falta instrucción.")
            
        import os
        import asyncio
        import json
        
        # 1. Copiar todo de forma asíncrona para leer el estado actual
        await _emit_ydotool_keys("29:1 30:1 30:0 29:0") # Ctrl+A
        await asyncio.sleep(0.1)
        await _emit_ydotool_keys("29:1 46:1 46:0 29:0") # Ctrl+C
        await asyncio.sleep(0.4)
        
        texto_original = (await _paste_clipboard_async()).strip()
        
        if not texto_original:
            return ToolResult(success=False, content="El documento parece estar vacío o no se pudo copiar.")
            
        async def _background_edit():
            try:
                prompt = (
                    f"Documento actual:\n\n{texto_original}\n\nInstrucción: {instruccion}\n\n"
                    "Identifica EXACTAMENTE el fragmento literal antiguo que se debe buscar en el documento y redacta "
                    "el nuevo texto que lo va a reemplazar basándote en la instrucción.\n\n"
                    "ADVERTENCIA CRÍTICA: 'buscar' y 'reemplazar_con' deben ser ÚNICAMENTE la frase o el párrafo específico "
                    "que cambia, NUNCA el documento entero. Devuelve ÚNICAMENTE un JSON válido con dos claves: 'buscar' y 'reemplazar_con'."
                )

                raw_text = await _generate_content_resilient(prompt)
                raw_json = raw_text.replace("```json", "").replace("```", "").strip()
                cambios = json.loads(raw_json)
                
                buscar_texto = cambios.get("buscar", "")
                reemplazar_texto = cambios.get("reemplazar_con", "")
                
                # Debug log
                with open(_PROJECT_ROOT / "last_edit.json", "w") as f:
                    json.dump(cambios, f, indent=2)
                
                if not buscar_texto or not reemplazar_texto:
                    print("\n[ATLAS DICE]:\n❌ No pude extraer la instrucción de búsqueda y reemplazo.\n")
                    if context.event_bus:
                        from src.events.base import SystemNotification, ConversationContext
                        context.event_bus.publish(SystemNotification(
                            context=context.conversation_context or ConversationContext(),
                            message="No se pudo extraer el texto a buscar y reemplazar para la edición. Notifícaselo al usuario."
                        ))
                    return
                
                # 1.5. Deseleccionar el texto (Flecha Derecha = 106) para no borrar el doc por accidente
                await _emit_ydotool_keys("106:1 106:0")
                await asyncio.sleep(0.2)
                
                # 2. Abrir Buscar (Ctrl+F) (KEY_F = 33)
                await _emit_ydotool_keys("29:1 33:1 33:0 29:0")
                await asyncio.sleep(0.5)
                
                # 3. Pegar texto a buscar (el buscador auto-selecciona el texto si lo encuentra)
                await _copy_clipboard_async(buscar_texto)
                await asyncio.sleep(0.3)
                await _emit_ydotool_keys("29:1 47:1 47:0 29:0") # Ctrl+V
                await asyncio.sleep(0.4)
                
                # 4. Presionar Enter para asegurar que el foco salte al resultado en el texto
                await _emit_ydotool_keys("28:1 28:0")
                await asyncio.sleep(0.2)
                
                # 5. Presionar Escape para cerrar el buscador. El texto encontrado quedará resaltado en el editor.
                await _emit_ydotool_keys("1:1 1:0")
                await asyncio.sleep(0.2)
                
                # 6. Pegar el texto nuevo. Al estar resaltado el texto viejo, Ctrl+V lo sobrescribe conservando el formato.
                await _copy_clipboard_async(reemplazar_texto)
                await asyncio.sleep(0.3)
                await _emit_ydotool_keys("29:1 47:1 47:0 29:0") # Ctrl+V
                
                print(f"\n[ATLAS DICE]:\n✅ ¡Edición segura completada! He reemplazado el texto conservando todo el formato.\n")
                if context.event_bus:
                    from src.events.base import SystemNotification, ConversationContext
                    context.event_bus.publish(SystemNotification(
                        context=context.conversation_context or ConversationContext(),
                        message=f"La edición del documento ('{instruccion}') ha finalizado exitosamente en pantalla conservando el formato. Notifícaselo brevemente al usuario."
                    ))
            except Exception as e:
                import traceback
                with open(_PROJECT_ROOT / "genai_error.txt", "w") as f:
                    f.write(f"Error en editar_documento_inteligente:\n{e}\n")
                    f.write(traceback.format_exc())
                print(f"\n[ATLAS DICE]:\n❌ Hubo un error al editar. Revisar log.\n")
                if context.event_bus:
                    from src.events.base import SystemNotification, ConversationContext
                    context.event_bus.publish(SystemNotification(
                        context=context.conversation_context or ConversationContext(),
                        message=f"Hubo un error al intentar editar el documento: {e}. Notifícaselo al usuario."
                    ))
                
        asyncio.create_task(_background_edit())
        
        return ToolResult(
            success=True, 
            content="La edición súper rápida (vía JSON y Reemplazar) se está realizando en segundo plano. Informa al usuario que la modificación se hará conservando el formato visual, y que aparecerá lista en pantalla en un par de segundos."
        )

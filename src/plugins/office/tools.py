import os
import pathlib
import subprocess
from typing import Dict, Any
from src.tools.base import BaseTool, ToolContext, ToolResult

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]


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
                    
            ruta_archivo = os.path.join(directorio_destino, f"{nombre}.docx")
            
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

        # Para documentos con contenido, lanzar en segundo plano
        import asyncio
        async def _background_task():
            try:
                from google import genai
                client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
                prompt = f"El usuario necesita crear un documento con la siguiente instrucción o tema: {tema}. Redacta el contenido basándote estrictamente en lo que pide. Si pide un informe largo, hazlo detallado. Si pide algo breve, hazlo breve. Redáctalo en texto plano estructurado, separado por saltos de línea. NO uses formato markdown (sin asteriscos para negritas ni hashtags)."
                respuesta_llm = await client.aio.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=prompt
                )
                contenido = respuesta_llm.text.replace("\\n", "\n").replace("\\r", "")
                
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
                        
                ruta_archivo = os.path.join(directorio_destino, f"{nombre}.docx")
                doc.save(ruta_archivo)
                subprocess.Popen(["onlyoffice-desktopeditors", ruta_archivo], start_new_session=True)
                print(f"\n[ATLAS DICE]:\n✅ ¡El informe '{titulo}' ha terminado de redactarse y lo acabo de abrir en tu pantalla!\n")
            except Exception as e:
                import traceback
                with open(_PROJECT_ROOT / "genai_error.txt", "w") as f:
                    f.write(f"Error en tarea de fondo: {e}\n")
                    f.write(traceback.format_exc())
                print(f"\n[ATLAS DICE]:\n❌ Hubo un error redactando el informe '{titulo}'. Revisar log.\n")

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
            import subprocess
            import time
            
            # Copiar el texto al portapapeles usando wl-copy (Wayland)
            cp_proc = subprocess.run(["wl-copy"], input=texto, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if cp_proc.returncode != 0:
                raise Exception(f"wl-copy falló con código {cp_proc.returncode}")
                
            time.sleep(0.4) # Pausa mayor para asegurar que el portapapeles de Wayland se sincronice en todas las apps
            
            teclas = []
            if reemplazar:
                # Ctrl+A (Seleccionar todo) y Suprimir
                teclas.extend(["29:1", "30:1", "30:0", "29:0", "111:1", "111:0"])
                
            # Ctrl+V (Pegar)
            teclas.extend(["29:1", "47:1", "47:0", "29:0"])
            
            proceso = subprocess.run(
                ["ydotool", "key"] + teclas,
                capture_output=True,
                text=True
            )
            
            if proceso.returncode != 0:
                raise Exception(f"ydotool falló al pegar: {proceso.stderr}")
            
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
        # :1 = presionado, :0 = soltado
        keycodes = {
            "borrar": "14:1 14:0 ",          # KEY_BACKSPACE
            "enter": "28:1 28:0 ",           # KEY_ENTER
            "suprimir": "111:1 111:0 ",      # KEY_DELETE
            "deshacer": "29:1 44:1 44:0 29:0 ", # KEY_LEFTCTRL + KEY_Z
            "seleccionar_todo": "29:1 30:1 30:0 29:0 ", # KEY_LEFTCTRL + KEY_A
            "arriba": "103:1 103:0 ",
            "abajo": "108:1 108:0 ",
            "izquierda": "105:1 105:0 ",
            "derecha": "106:1 106:0 ",
            "inicio": "102:1 102:0 ",
            "fin": "107:1 107:0 ",
            "buscar": "29:1 33:1 33:0 29:0 " # KEY_LEFTCTRL + KEY_F
        }
        
        if accion not in keycodes:
            return ToolResult(success=False, content=f"Acción desconocida: {accion}")
            
        teclas_a_presionar = keycodes[accion] * veces
        
        try:
            import subprocess
            # ydotool key <keycodes>
            cmd = ["ydotool", "key"] + teclas_a_presionar.strip().split()
            proceso = subprocess.run(cmd, capture_output=True, text=True)
            
            if proceso.returncode != 0:
                raise Exception(f"ydotool falló: {proceso.stderr}")
                
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
            
        import subprocess
        import time
        import os
        import asyncio
        import json
        
        # 1. Copiar todo para leer el estado actual
        subprocess.run(["ydotool", "key", "29:1", "30:1", "30:0", "29:0"], capture_output=True) # Ctrl+A
        time.sleep(0.1)
        subprocess.run(["ydotool", "key", "29:1", "46:1", "46:0", "29:0"], capture_output=True) # Ctrl+C
        time.sleep(0.4)
        
        proc = subprocess.run(["wl-paste"], capture_output=True, text=True)
        texto_original = proc.stdout.strip()
        
        if not texto_original:
            return ToolResult(success=False, content="El documento parece estar vacío o no se pudo copiar.")
            
        async def _background_edit():
            try:
                from google import genai
                client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
                
                prompt = f"Documento actual:\n\n{texto_original}\n\nInstrucción: {instruccion}\n\nIdentifica EXACTAMENTE el fragmento literal antiguo que se debe buscar en el documento y redacta el nuevo texto que lo va a reemplazar basándote en la instrucción.\n\nADVERTENCIA CRÍTICA: 'buscar' y 'reemplazar_con' deben ser ÚNICAMENTE la frase o el párrafo específico que cambia, NUNCA el documento entero. Devuelve ÚNICAMENTE un JSON válido con dos claves: 'buscar' y 'reemplazar_con'."
                
                res = await client.aio.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=prompt
                )
                
                raw_json = res.text.replace("```json", "").replace("```", "").strip()
                cambios = json.loads(raw_json)
                
                buscar_texto = cambios.get("buscar", "")
                reemplazar_texto = cambios.get("reemplazar_con", "")
                
                # Debug log
                with open(_PROJECT_ROOT / "last_edit.json", "w") as f:
                    json.dump(cambios, f, indent=2)
                
                if not buscar_texto or not reemplazar_texto:
                    print("\n[ATLAS DICE]:\n❌ No pude extraer la instrucción de búsqueda y reemplazo.\n")
                    return
                
                # 1.5. Deseleccionar el texto (Flecha Derecha = 106) para no borrar el doc por accidente
                subprocess.run(["ydotool", "key", "106:1", "106:0"], capture_output=True)
                time.sleep(0.2)
                
                # 2. Abrir Buscar (Ctrl+F) (KEY_F = 33)
                subprocess.run(["ydotool", "key", "29:1", "33:1", "33:0", "29:0"], capture_output=True)
                time.sleep(0.5)
                
                # 3. Pegar texto a buscar (el buscador auto-selecciona el texto si lo encuentra)
                subprocess.run(["wl-copy"], input=buscar_texto, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.3)
                subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], capture_output=True) # Ctrl+V
                time.sleep(0.4)
                
                # 4. Presionar Enter para asegurar que el foco salte al resultado en el texto
                subprocess.run(["ydotool", "key", "28:1", "28:0"], capture_output=True)
                time.sleep(0.2)
                
                # 5. Presionar Escape para cerrar el buscador. El texto encontrado quedará resaltado en el editor.
                subprocess.run(["ydotool", "key", "1:1", "1:0"], capture_output=True)
                time.sleep(0.2)
                
                # 6. Pegar el texto nuevo. Al estar resaltado el texto viejo, Ctrl+V lo sobrescribe conservando el formato.
                subprocess.run(["wl-copy"], input=reemplazar_texto, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(0.3)
                subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], capture_output=True) # Ctrl+V
                
                print(f"\n[ATLAS DICE]:\n✅ ¡Edición segura completada! He reemplazado el texto conservando todo el formato.\n")
            except Exception as e:
                import traceback
                with open(_PROJECT_ROOT / "genai_error.txt", "w") as f:
                    f.write(f"Error en editar_documento_inteligente:\n{e}\n")
                    f.write(traceback.format_exc())
                print(f"\n[ATLAS DICE]:\n❌ Hubo un error al editar. Revisar log.\n")
                
        asyncio.create_task(_background_edit())
        
        return ToolResult(
            success=True, 
            content="La edición súper rápida (vía JSON y Reemplazar) se está realizando en segundo plano. Informa al usuario que la modificación se hará conservando el formato visual, y que aparecerá lista en pantalla en un par de segundos."
        )

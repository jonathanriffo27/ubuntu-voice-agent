import os
import subprocess
from typing import Dict, Any
from src.tools.base import BaseTool, ToolContext, ToolResult

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
                "contenido": {
                    "type": "STRING",
                    "description": "El texto principal del documento. Puede incluir múltiples párrafos separados por saltos de línea (\\n)."
                },
                "nombre_archivo": {
                    "type": "STRING",
                    "description": "Nombre del archivo a guardar (sin la extensión .docx). Ej: 'informe_ventas'"
                }
            },
            "required": ["titulo", "contenido", "nombre_archivo"]
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
        contenido = kwargs.get("contenido", "")
        nombre_crudo = str(kwargs.get("nombre_archivo", "documento"))
        nombre = os.path.basename(nombre_crudo).strip()
        if not nombre or ".." in nombre:
            nombre = "documento"
        
        # Crear documento
        doc = docx.Document()
        doc.add_heading(titulo, 0)
        
        for parrafo in contenido.split('\n'):
            if parrafo.strip():
                doc.add_paragraph(parrafo.strip())
                
        # Guardar en el Escritorio por defecto
        escritorio = os.path.expanduser("~/Escritorio")
        if not os.path.exists(escritorio):
            escritorio = os.path.expanduser("~/Desktop")
            if not os.path.exists(escritorio):
                escritorio = os.path.expanduser("~")
                
        ruta_archivo = os.path.join(escritorio, f"{nombre}.docx")
        
        try:
            doc.save(ruta_archivo)
        except Exception as e:
            return ToolResult(success=False, content=f"Error al guardar el documento: {e}")
            
        # Abrir con OnlyOffice
        try:
            # desktopeditors es el binario típico de OnlyOffice en Linux
            subprocess.Popen(["desktopeditors", ruta_archivo], start_new_session=True)
        except Exception as e:
            return ToolResult(
                success=True, 
                content=f"Documento creado en {ruta_archivo}, pero no pude abrir OnlyOffice automáticamente. Error: {e}"
            )
            
        return ToolResult(
            success=True, 
            content=f"Documento '{titulo}' creado exitosamente en {ruta_archivo} y abierto en OnlyOffice."
        )

from typing import Dict, Any
from src.tools.base import BaseTool, ToolContext, ToolResult
from src.knowledge.manager import KnowledgeManager
from src.events.base import KnowledgeUpdated

class GuardarNotaTool(BaseTool):
    def __init__(self, manager: KnowledgeManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "guardar_nota"

    @property
    def description(self) -> str:
        return "Guarda una nota general que recordarás en futuras sesiones. Para datos personales permanentes usa 'guardar_perfil'. Máximo 20 notas, las más antiguas rotan."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "nota": {"type": "STRING", "description": "La nota a guardar (máx 200 caracteres)"}
            }, 
            "required": ["nota"]
        }

    async def execute(self, context: ToolContext, nota: str = None) -> ToolResult:
        if not context.config.memory.enabled:
            return ToolResult(success=False, content="El subsistema de memoria está deshabilitado.")
            
        if not nota:
            return ToolResult(success=False, content="Falta la nota a guardar.")
            
        try:
            self.manager.save_note(nota)
        except Exception as e:
            return ToolResult(success=False, content=f"No pude guardar la nota, hubo un error de escritura: {e}")
        if context.event_bus and context.conversation_context:
            context.event_bus.publish(KnowledgeUpdated(
                context=context.conversation_context,
                action="note_added",
                details=f"Nota guardada: '{nota}'"
            ))
        return ToolResult(success=True, content=f"Nota guardada correctamente: '{nota}'")

class BorrarNotaTool(BaseTool):
    def __init__(self, manager: KnowledgeManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "borrar_nota"

    @property
    def description(self) -> str:
        return "Borra una nota general por su número. Úsalo cuando el usuario pida olvidar algo."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "indice": {"type": "INTEGER", "description": "Número de la nota a borrar (1-indexado)"}
            }, 
            "required": ["indice"]
        }

    async def execute(self, context: ToolContext, indice: int = None) -> ToolResult:
        if not context.config.memory.enabled:
            return ToolResult(success=False, content="El subsistema de memoria está deshabilitado.")
            
        if indice is None:
            return ToolResult(success=False, content="Falta el índice de la nota a borrar.")
            
        success = self.manager.delete_note(indice)
        if success:
            if context.event_bus and context.conversation_context:
                context.event_bus.publish(KnowledgeUpdated(
                    context=context.conversation_context,
                    action="note_deleted",
                    details=f"Nota #{indice} borrada."
                ))
            return ToolResult(success=True, content=f"Nota #{indice} borrada correctamente.")
        else:
            return ToolResult(success=False, content=f"No se pudo borrar la nota #{indice}. Asegúrate de que el índice sea válido.")

class GuardarPerfilTool(BaseTool):
    def __init__(self, manager: KnowledgeManager):
        self.manager = manager

    @property
    def name(self) -> str:
        return "guardar_perfil"

    @property
    def description(self) -> str:
        return "Guarda un dato PERMANENTE del usuario (ej. nombre, ocupación, intereses). Estos datos NUNCA se borran automáticamente. Máx 10 campos."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "OBJECT", 
            "properties": {
                "campo": {"type": "STRING", "description": "Nombre del campo (ej. nombre, ocupacion, intereses)"},
                "valor": {"type": "STRING", "description": "Valor del campo (máx 100 caracteres)"}
            }, 
            "required": ["campo", "valor"]
        }

    async def execute(self, context: ToolContext, campo: str = None, valor: str = None) -> ToolResult:
        if not context.config.memory.enabled:
            return ToolResult(success=False, content="El subsistema de memoria está deshabilitado.")
            
        if not campo or not valor:
            return ToolResult(success=False, content="Faltan parámetros 'campo' o 'valor'.")
            
        try:
            self.manager.save_profile(campo, valor)
        except Exception as e:
            return ToolResult(success=False, content=f"No pude guardar el perfil, hubo un error de escritura: {e}")
        if context.event_bus and context.conversation_context:
            context.event_bus.publish(KnowledgeUpdated(
                context=context.conversation_context,
                action="profile_updated",
                details=f"Perfil '{campo}' actualizado a '{valor}'."
            ))
        return ToolResult(success=True, content=f"Perfil actualizado: {campo} = {valor}")

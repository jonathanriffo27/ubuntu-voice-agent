import os
import sys
import tools
from src.providers.gemini import GeminiProvider
from src.brain.assistant import Assistant

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("❌ ERROR: Debes exportar GEMINI_API_KEY.")
    sys.exit(1)

# En Hito 3 esto se moverá al ToolRegistry
TOOL_FUNCTIONS = {
    "obtener_estado_sistema": tools.obtener_estado_sistema,
    "proponer_comando": tools.proponer_comando,
    "ejecutar_comando_confirmado": tools.ejecutar_comando_confirmado,
    "buscar_en_internet": tools.buscar_en_internet,
    "imprimir_en_consola": tools.imprimir_en_consola,
    "abrir_aplicacion": tools.abrir_aplicacion,
    "guardar_nota": tools.guardar_nota,
    "borrar_nota": tools.borrar_nota,
    "guardar_perfil": tools.guardar_perfil
}

agent_tools = [{"function_declarations": [
    {"name": "obtener_estado_sistema", "description": "Obtiene la hora actual."},
    {"name": "proponer_comando", "description": "Propone un comando de terminal.", "parameters": {"type": "OBJECT", "properties": {"comando": {"type": "STRING", "description": "El comando bash exacto"}}, "required": ["comando"]}},
    {"name": "ejecutar_comando_confirmado", "description": "Ejecuta el último comando de terminal propuesto."},
    {"name": "buscar_en_internet", "description": "Realiza una búsqueda en internet y devuelve un resumen de los resultados.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "La consulta a buscar en internet"}}, "required": ["query"]}},
    {"name": "imprimir_en_consola", "description": "Imprime un texto, código, tabla o información detallada en la terminal para que el usuario pueda leerlo. Útil cuando la respuesta es muy larga o contiene formato que se pierde al hablar.", "parameters": {"type": "OBJECT", "properties": {"texto": {"type": "STRING", "description": "El texto a imprimir"}}, "required": ["texto"]}},
    {"name": "abrir_aplicacion", "description": "Abre una aplicación instalada en el sistema (ej. calculadora, terminal, code) o abre páginas web conocidas en el navegador (ej. gmail, youtube, whatsapp).", "parameters": {"type": "OBJECT", "properties": {"nombre": {"type": "STRING", "description": "El nombre de la aplicación o sitio web a abrir"}}, "required": ["nombre"]}},
    {"name": "guardar_nota", "description": "Guarda una nota general que recordarás en futuras sesiones. Para datos personales permanentes usa 'guardar_perfil'. Máximo 20 notas, las más antiguas rotan.", "parameters": {"type": "OBJECT", "properties": {"nota": {"type": "STRING", "description": "La nota a guardar (máx 200 caracteres)"}}, "required": ["nota"]}},
    {"name": "borrar_nota", "description": "Borra una nota general por su número. Úsalo cuando el usuario pida olvidar algo.", "parameters": {"type": "OBJECT", "properties": {"indice": {"type": "INTEGER", "description": "Número de la nota a borrar (1-indexado)"}}, "required": ["indice"]}},
    {"name": "guardar_perfil", "description": "Guarda un dato PERMANENTE del usuario (ej. nombre, ocupación, intereses). Estos datos NUNCA se borran automáticamente. Máx 10 campos.", "parameters": {"type": "OBJECT", "properties": {"campo": {"type": "STRING", "description": "Nombre del campo (ej. nombre, ocupacion, intereses)"}, "valor": {"type": "STRING", "description": "Valor del campo (máx 100 caracteres)"}}, "required": ["campo", "valor"]}}
]}]

if __name__ == "__main__":
    provider = GeminiProvider(model_name="gemini-3.1-flash-live-preview")
    assistant = Assistant(provider=provider, agent_tools=agent_tools, tool_functions=TOOL_FUNCTIONS)
    assistant.run()

import os
import sys
from src.providers.gemini import GeminiProvider
from src.brain.assistant import Assistant
from src.tools.registry import ToolRegistry
from src.tools.legacy import get_legacy_tools

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("❌ ERROR: Debes exportar GEMINI_API_KEY.")
    sys.exit(1)

if __name__ == "__main__":
    # Inicializar el registro de herramientas
    registry = ToolRegistry()
    for tool in get_legacy_tools():
        registry.register(tool)

    # Inicializar el proveedor
    provider = GeminiProvider(model_name="gemini-3.1-flash-live-preview")
    
    # Inicializar el asistente
    assistant = Assistant(provider=provider, registry=registry)
    assistant.run()

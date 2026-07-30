import os
import sys
from src.providers.gemini import GeminiProvider
from src.brain.assistant import Assistant
from src.tools.registry import ToolRegistry
from src.tools.legacy import get_legacy_tools
from src.config.loader import load_config

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("❌ ERROR: Debes exportar GEMINI_API_KEY.")
    sys.exit(1)

if __name__ == "__main__":
    config = load_config("config.yaml")

    # Inicializar el registro de herramientas
    registry = ToolRegistry()
    for tool in get_legacy_tools():
        registry.register(tool)

    # Inicializar el proveedor
    if config.provider.type == "gemini":
        provider = GeminiProvider(
            model_name=config.provider.model, 
            voice_name=config.provider.voice
        )
    else:
        raise ValueError(f"Proveedor desconocido: {config.provider.type}")
    
    # Inicializar el asistente
    assistant = Assistant(provider=provider, registry=registry, config=config)
    assistant.run()

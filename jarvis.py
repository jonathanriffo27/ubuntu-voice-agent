import os
import sys
from src.providers.gemini import GeminiProvider
from src.brain.assistant import Assistant
from src.tools.registry import ToolRegistry
from src.knowledge.manager import KnowledgeManager
from src.knowledge.backends.json import JsonKnowledgeBackend
from src.config.loader import load_config
from src.events.bus import EventBus
from src.ui.cli import CLIInterface
from src.plugins.loader import discover_and_register_plugins

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("❌ ERROR: Debes exportar GEMINI_API_KEY.")
    sys.exit(1)

if __name__ == "__main__":
    config = load_config("config.yaml")

    # Inicializar el Event Bus y la UI
    event_bus = EventBus()
    ui = CLIInterface(event_bus)

    # Inicializar el registro de herramientas
    registry = ToolRegistry()
    
    # Inicializar Base de Conocimiento (Knowledge)
    knowledge_backend = JsonKnowledgeBackend("atlas_knowledge.json")
    knowledge_manager = KnowledgeManager(backend=knowledge_backend)

    # Cargar plugins dinámicamente
    dependencies = {
        "knowledge_manager": knowledge_manager
    }
    discover_and_register_plugins(registry, dependencies)

    # Inicializar el proveedor
    if config.provider.type == "gemini":
        provider = GeminiProvider(
            model_name=config.provider.model, 
            voice_name=config.provider.voice
        )
    else:
        raise ValueError(f"Proveedor desconocido: {config.provider.type}")
    
    assistant = Assistant(
        provider=provider, 
        registry=registry, 
        config=config, 
        knowledge_manager=knowledge_manager,
        event_bus=event_bus
    )
    assistant.run()

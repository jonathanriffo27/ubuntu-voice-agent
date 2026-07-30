import os
import sys
from src.providers.gemini import GeminiProvider
from src.brain.assistant import Assistant
from src.tools.registry import ToolRegistry
from src.tools.shell import BashExecutor, CommandState, ProponerComandoTool, EjecutarComandoTool
from src.tools.knowledge import GuardarNotaTool, BorrarNotaTool, GuardarPerfilTool
from src.tools.system import EstadoSistemaTool, ImprimirConsolaTool, AbrirAplicacionTool
from src.tools.browser import BuscarEnInternetTool
from src.knowledge.manager import KnowledgeManager
from src.knowledge.backends.json import JsonKnowledgeBackend
from src.config.loader import load_config
from src.events.bus import EventBus
from src.observability.logger import LoggingListener

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("❌ ERROR: Debes exportar GEMINI_API_KEY.")
    sys.exit(1)

if __name__ == "__main__":
    config = load_config("config.yaml")

    # Inicializar el Event Bus y los suscriptores (Observability)
    event_bus = EventBus()
    logger = LoggingListener(event_bus)

    # Inicializar el registro de herramientas
    registry = ToolRegistry()
    
    # Herramientas del Sistema y Navegador
    registry.register(EstadoSistemaTool())
    registry.register(ImprimirConsolaTool())
    registry.register(AbrirAplicacionTool())
    registry.register(BuscarEnInternetTool())
        
    # Inicializar herramientas nativas de shell
    shell_state = CommandState()
    shell_executor = BashExecutor()
    registry.register(ProponerComandoTool(state=shell_state))
    registry.register(EjecutarComandoTool(executor=shell_executor, state=shell_state))

    # Inicializar Base de Conocimiento (Knowledge)
    knowledge_backend = JsonKnowledgeBackend("atlas_knowledge.json")
    knowledge_manager = KnowledgeManager(backend=knowledge_backend)
    registry.register(GuardarNotaTool(manager=knowledge_manager))
    registry.register(BorrarNotaTool(manager=knowledge_manager))
    registry.register(GuardarPerfilTool(manager=knowledge_manager))

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

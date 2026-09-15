import os
import sys
from src.voice.alsa_mute import mute_alsa_logging

# Silenciar errores y advertencias de bajo nivel C de ALSA (underruns)
mute_alsa_logging()

from src.utils.logging import setup_logging, get_logger
from src.providers.gemini import GeminiProvider
from src.brain.assistant import Assistant
from src.tools.registry import ToolRegistry
from src.knowledge.manager import KnowledgeManager
from src.knowledge.backends.json import JsonKnowledgeBackend
from src.config.loader import load_config
from src.events.bus import EventBus
from src.ui.cli import CLIInterface
from src.ui.web_overlay import WebOverlayServer
from src.plugins.loader import discover_and_register_plugins, reload_plugins
from src.mcp.manager import MCPServerManager
from src.reminders.scheduler import AsyncReminderScheduler
from src.security.approval import ApprovalManager
from src.agents.client import CLIProxyClient
from src.agents.tools import AgentCodeTools
from src.agents.developer_agent import DeveloperAgent

from src.brain.trajectory import TrajectoryManager

# Inicializar logging estructurado (consola limpia, log completo a disco)
setup_logging(log_file="latest_session.log")
logger = get_logger("bootstrap")

# Cargar credenciales desde .env del proyecto (no pisa variables ya exportadas)
from src.config.loader import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    logger.critical(
        "Debes exportar la variable de entorno GEMINI_API_KEY "
        "(o definirla en el archivo .env del proyecto)."
    )
    sys.exit(1)

if __name__ == "__main__":
    config = load_config("config.yaml")

    # Inicializar el Event Bus y la UI de consola
    event_bus = EventBus()
    ui = CLIInterface(event_bus)

    # Inicializar el gestor de trayectoria y memoria de sesión persistente
    trajectory_manager = TrajectoryManager(event_bus=event_bus)

    # Inicializar la compuerta de aprobación humana (HITL)
    approval_manager = ApprovalManager(event_bus=event_bus)

    # Inicializar el motor de recordatorios
    reminder_scheduler = AsyncReminderScheduler(event_bus=event_bus)

    # Inicializar el servidor de HUD / Overlay Web con soporte HITL y Trajectory
    overlay_server = None
    if config.ui.overlay_enabled:
        overlay_server = WebOverlayServer(
            event_bus,
            port=config.ui.overlay_port,
            approval_manager=approval_manager,
            trajectory_manager=trajectory_manager,
            reminder_scheduler=reminder_scheduler
        )

    # Inicializar el registro de herramientas
    registry = ToolRegistry()

    # Inicializar Base de Conocimiento (Knowledge)
    knowledge_backend = JsonKnowledgeBackend("atlas_knowledge.json")
    knowledge_manager = KnowledgeManager(backend=knowledge_backend)

    # Dependencias base para plugins
    dependencies = {
        "knowledge_manager": knowledge_manager,
        "reminder_scheduler": reminder_scheduler,
        "approval_manager": approval_manager
    }

    # Inicializar el subagente desarrollador si está habilitado
    developer_agent = None
    if config.developer_agent.enabled:
        cli_client = CLIProxyClient(
            base_url=config.developer_agent.base_url,
            api_key=config.developer_agent.api_key
        )
        reload_cb = lambda: reload_plugins(registry, dependencies)
        agent_tools = AgentCodeTools(
            approval_manager=approval_manager,
            reload_callback=reload_cb
        )
        developer_agent = DeveloperAgent(
            client=cli_client,
            code_tools=agent_tools,
            event_bus=event_bus,
            model=config.developer_agent.model,
            max_iterations=config.developer_agent.max_iterations
        )
        dependencies["developer_agent"] = developer_agent
        dependencies["reload_callback"] = reload_cb

    # Cargar plugins locales dinámicamente
    discover_and_register_plugins(registry, dependencies)

    # Diagnóstico de la cadena de input GUI (Fase 0 - COMPUTER_USE_PLAN.md):
    # detecta y auto-repara ydotoold caído antes de que falle una automatización.
    try:
        from src.input.health import log_startup_report
        log_startup_report()
    except Exception as e:
        logger.debug(f"Health-check de input no disponible: {e}")

    # Gestor de servidores MCP (Model Context Protocol)
    mcp_manager = MCPServerManager()

    # Inicializar el proveedor LLM
    if config.provider.type == "gemini":
        provider = GeminiProvider(
            model_name=config.provider.model,
            voice_name=config.provider.voice,
            server_vad=getattr(config.voice, "server_vad", False),
            affective_dialog=getattr(config.voice, "affective_dialog", False)
        )
    else:
        logger.error(f"Proveedor desconocido en config: {config.provider.type}")
        raise ValueError(f"Proveedor desconocido: {config.provider.type}")

    assistant = Assistant(
        provider=provider,
        registry=registry,
        config=config,
        knowledge_manager=knowledge_manager,
        event_bus=event_bus,
        mcp_manager=mcp_manager,
        overlay_server=overlay_server,
        reminder_scheduler=reminder_scheduler,
        approval_manager=approval_manager,
        trajectory_manager=trajectory_manager
    )
    assistant.run()

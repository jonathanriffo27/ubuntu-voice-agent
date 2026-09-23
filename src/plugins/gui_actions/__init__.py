from src.tools.registry import ToolRegistry
from .tools import InteractuarGuiTool
from .background import OperarGuiTareaTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    approval = dependencies.get("approval_manager")
    gui_tool = InteractuarGuiTool(approval_manager=approval)
    registry.register(gui_tool)

    # Orquestador OODA (Fase 2): reutiliza el cliente LLM del DeveloperAgent
    dev_agent = dependencies.get("developer_agent")
    client = getattr(dev_agent, "client", None) if dev_agent else None
    model = getattr(dev_agent, "model", "gemini-3.8-flash-high") if dev_agent else "gemini-3.8-flash-high"
    registry.register(OperarGuiTareaTool(client=client, gui_tool=gui_tool, model=model))

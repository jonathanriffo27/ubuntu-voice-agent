from src.tools.registry import ToolRegistry
from .tools import DelegarTareaDesarrolloTool, AprobarAccionTool, RecargarPluginsTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    dev_agent = dependencies.get("developer_agent")
    approval_manager = dependencies.get("approval_manager")
    reload_cb = dependencies.get("reload_callback")

    if dev_agent:
        registry.register(DelegarTareaDesarrolloTool(dev_agent))
    if approval_manager:
        registry.register(AprobarAccionTool(approval_manager))
    if reload_cb:
        registry.register(RecargarPluginsTool(reload_cb))

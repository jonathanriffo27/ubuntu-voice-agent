from src.tools.registry import ToolRegistry
from .tools import CrearRecordatorioTool, ListarRecordatoriosTool, CancelarRecordatorioTool


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    scheduler = dependencies.get("reminder_scheduler")
    if scheduler:
        registry.register(CrearRecordatorioTool(scheduler))
        registry.register(ListarRecordatoriosTool(scheduler))
        registry.register(CancelarRecordatorioTool(scheduler))

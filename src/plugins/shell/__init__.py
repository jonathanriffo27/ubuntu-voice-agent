from src.tools.registry import ToolRegistry
from .tools import CommandState, BashExecutor, ProponerComandoTool, EjecutarComandoTool

def setup(registry: ToolRegistry, dependencies: dict):
    state = CommandState()
    executor = BashExecutor()
    registry.register(ProponerComandoTool(state=state))
    registry.register(EjecutarComandoTool(executor=executor, state=state))

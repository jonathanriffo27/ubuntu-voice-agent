from src.tools.registry import ToolRegistry
from .tools import (
    MultiEngineSearchManager,
    BuscarEnInternetTool,
    LeerPaginaWebTool,
    InvestigarEnProfundidadTool
)
from .engines.reader import WebPageReader
from .research import DeepResearchEngine
from src.agents.client import CLIProxyClient


def setup(registry: ToolRegistry, dependencies: dict) -> None:
    search_manager = MultiEngineSearchManager()
    reader = WebPageReader()

    cli_client = dependencies.get("cli_client")
    if not cli_client and "developer_agent" in dependencies:
        cli_client = dependencies["developer_agent"].client
    if not cli_client:
        cli_client = CLIProxyClient()

    research_engine = DeepResearchEngine(
        search_engine=search_manager,
        cli_client=cli_client,
        reader=reader
    )

    registry.register(BuscarEnInternetTool(search_manager))
    registry.register(LeerPaginaWebTool(reader))
    registry.register(InvestigarEnProfundidadTool(research_engine))

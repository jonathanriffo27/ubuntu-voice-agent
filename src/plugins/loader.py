import importlib
import pkgutil
import sys
from typing import Dict, Any, List
from src.tools.registry import ToolRegistry
import src.plugins
from src.utils.logging import get_logger

logger = get_logger("plugins.loader")


def discover_and_register_plugins(registry: ToolRegistry, dependencies: Dict[str, Any]) -> List[str]:
    """
    Descubre dinámicamente los plugins en src/plugins/ y los registra
    pasándoles las dependencias que necesiten.
    """
    loaded_plugins = []
    for _, module_name, is_pkg in pkgutil.iter_modules(src.plugins.__path__):
        if is_pkg:
            full_module_name = f"src.plugins.{module_name}"
            try:
                plugin_module = importlib.import_module(full_module_name)
                if hasattr(plugin_module, "setup"):
                    plugin_module.setup(registry, dependencies)
                    loaded_plugins.append(module_name)
            except Exception as e:
                logger.error(f"Error cargando plugin {module_name}: {e}")
    return loaded_plugins


def reload_plugins(registry: ToolRegistry, dependencies: Dict[str, Any]) -> int:
    """
    Recarga en caliente todos los plugins de Atlas:
    Invalida caches de importación, recarga módulos en sys.modules y re-registra las herramientas.
    """
    importlib.invalidate_caches()
    registry.clear()

    # Recargar módulos de plugins previamente cacheados
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("src.plugins."):
            try:
                importlib.reload(sys.modules[mod_name])
            except Exception:
                pass

    discover_and_register_plugins(registry, dependencies)
    tools = registry.get_all_tools()
    logger.info(f"🔄 Recarga en caliente completada: {len(tools)} herramientas activas en memoria.")
    return len(tools)

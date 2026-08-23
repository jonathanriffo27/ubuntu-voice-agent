import importlib
import pkgutil
from typing import Dict, Any
from src.tools.registry import ToolRegistry
import src.plugins

def discover_and_register_plugins(registry: ToolRegistry, dependencies: Dict[str, Any]):
    """
    Descubre dinámicamente los plugins en src/plugins/ y los registra
    pasándoles las dependencias que necesiten.
    """
    # Recorrer todos los submódulos en src.plugins
    for _, module_name, is_pkg in pkgutil.iter_modules(src.plugins.__path__):
        if is_pkg:
            full_module_name = f"src.plugins.{module_name}"
            try:
                plugin_module = importlib.import_module(full_module_name)
                
                # Cada plugin debe exponer una función setup(registry, dependencies)
                if hasattr(plugin_module, "setup"):
                    plugin_module.setup(registry, dependencies)
            except Exception as e:
                print(f"⚠️ Error cargando plugin {module_name}: {e}")

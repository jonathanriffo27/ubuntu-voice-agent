import os
import re
import yaml
from typing import Any
from .models import (
    AtlasConfig, ProviderConfig, VoiceConfig, MemoryConfig,
    VisionConfig, ToolsConfig, ShellToolConfig, UIConfig,
    DeveloperAgentConfig
)


def load_dotenv(path: str = ".env") -> int:
    """
    Carga variables de un archivo .env simple (KEY=VALUE) al entorno del proceso.
    - No sobrescribe variables ya exportadas en el entorno real (tienen precedencia).
    - Soporta 'export KEY=VALUE', comillas simples/dobles, comentarios y líneas vacías.
    Devuelve cuántas variables cargó.
    """
    if not os.path.exists(path):
        return 0

    loaded = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Quitar comillas envolventes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Ignorar placeholders de plantilla
            if not key or not value or value.startswith("tu_clave"):
                continue
            if key not in os.environ:  # el entorno real siempre manda
                os.environ[key] = value
                loaded += 1
    return loaded


def _expand_env_vars(obj: Any) -> Any:
    """
    Expande recursivamente variables de entorno del tipo ${VAR_NAME} o ${VAR_NAME:-default}.
    """
    if isinstance(obj, str):
        pattern = re.compile(r'\$\{([^}:]+)(?::-([^}]*))?\}')

        def replace_match(m):
            var_name = m.group(1)
            default_val = m.group(2) if m.group(2) is not None else ""
            return os.environ.get(var_name, default_val)

        return pattern.sub(replace_match, obj)
    elif isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(elem) for elem in obj]
    return obj


def load_config(path: str = "config.yaml") -> AtlasConfig:
    """Carga y parsea la configuración de Atlas con expansión de variables de entorno."""
    if not os.path.exists(path):
        return AtlasConfig()

    with open(path, 'r', encoding='utf-8') as f:
        raw_data = yaml.safe_load(f) or {}

    data = _expand_env_vars(raw_data)

    provider_data = data.get("provider", {})
    voice_data = data.get("voice", {})
    memory_data = data.get("memory", {})
    vision_data = data.get("vision", {})
    tools_data = data.get("tools", {})
    shell_data = tools_data.get("shell", {})
    ui_data = data.get("ui", {})
    dev_agent_data = data.get("developer_agent", {})
    mcp_servers = data.get("mcp_servers", {})

    return AtlasConfig(
        provider=ProviderConfig(**provider_data),
        voice=VoiceConfig(**voice_data),
        memory=MemoryConfig(**memory_data),
        vision=VisionConfig(**vision_data),
        tools=ToolsConfig(shell=ShellToolConfig(**shell_data)),
        ui=UIConfig(**ui_data),
        developer_agent=DeveloperAgentConfig(**dev_agent_data),
        mcp_servers=mcp_servers
    )

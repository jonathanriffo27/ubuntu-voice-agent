import os
import re
import yaml
from typing import Any
from .models import (
    AtlasConfig, ProviderConfig, VoiceConfig, MemoryConfig,
    VisionConfig, ToolsConfig, ShellToolConfig, UIConfig,
    DeveloperAgentConfig
)


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

import os
import yaml
from .models import (
    AtlasConfig, ProviderConfig, VoiceConfig, MemoryConfig,
    VisionConfig, ToolsConfig, ShellToolConfig, UIConfig,
    DeveloperAgentConfig
)


def load_config(path: str = "config.yaml") -> AtlasConfig:
    if not os.path.exists(path):
        return AtlasConfig()

    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

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

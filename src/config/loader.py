import os
import yaml
from .models import AtlasConfig, ProviderConfig, VoiceConfig, MemoryConfig, VisionConfig

def load_config(path: str = "config.yaml") -> AtlasConfig:
    if not os.path.exists(path):
        return AtlasConfig()  # retorna valores por defecto

    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    provider_data = data.get("provider", {})
    voice_data = data.get("voice", {})
    memory_data = data.get("memory", {})
    vision_data = data.get("vision", {})

    return AtlasConfig(
        provider=ProviderConfig(**provider_data),
        voice=VoiceConfig(**voice_data),
        memory=MemoryConfig(**memory_data),
        vision=VisionConfig(**vision_data)
    )

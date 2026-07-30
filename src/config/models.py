from dataclasses import dataclass, field

@dataclass
class ProviderConfig:
    type: str = "gemini"
    model: str = "gemini-3.1-flash-live-preview"
    voice: str = "Aoede"

@dataclass
class VoiceConfig:
    wake_word: str = "atlas"
    language: str = "es-CL"

@dataclass
class MemoryConfig:
    enabled: bool = True

@dataclass
class VisionConfig:
    enabled: bool = False

@dataclass
class ShellToolConfig:
    enabled: bool = True
    confirmation: str = "always"  # always, dangerous_only, never

@dataclass
class ToolsConfig:
    shell: ShellToolConfig = field(default_factory=ShellToolConfig)

@dataclass
class AtlasConfig:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)

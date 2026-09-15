from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class ProviderConfig:
    type: str = "gemini"
    model: str = "gemini-3.1-flash-live-preview"
    voice: str = "Aoede"


@dataclass
class VoiceConfig:
    mode: str = "wake_word"  # "wake_word" o "always_on"
    wake_word: str = "alexa"  # "alexa", "hey_jarvis", "hey_mycroft" o ruta .onnx
    wake_word_threshold: float = 0.35  # Sensibilidad optimizada para español (0.30 - 0.45)
    follow_up_timeout: float = 7.0  # Segundos post-respuesta antes de volver a standby
    activation_sound: bool = True  # Chime al detectar wake word
    language: str = "es-CL"
    server_vad: bool = False  # True: delega detección de fin de turno al VAD nativo de Gemini
    affective_dialog: bool = False  # True: voz con entonación afectiva/emocional nativa


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
class UIConfig:
    overlay_enabled: bool = True
    overlay_port: int = 7890
    auto_open_browser: bool = False


@dataclass
class DeveloperAgentConfig:
    enabled: bool = True
    provider: str = "cliproxy"
    base_url: str = "http://127.0.0.1:8317/v1"
    api_key: str = ""  # Debe inyectarse vía variable de entorno CLIPROXY_API_KEY
    model: str = "gemini-3.7-flash-high"
    temperature: float = 0.2
    max_iterations: int = 15


@dataclass
class AtlasConfig:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    developer_agent: DeveloperAgentConfig = field(default_factory=DeveloperAgentConfig)
    mcp_servers: Dict[str, Any] = field(default_factory=dict)

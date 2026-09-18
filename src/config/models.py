from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class ProviderConfig:
    type: str = "gemini"
    model: str = "gemini-3.8-live"
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
    # Loopback por defecto: el HUD expone trayectoria (correos), recordatorios
    # y aprobaciones HITL. Solo cambiar a 0.0.0.0 si el usuario sabe lo que hace.
    overlay_host: str = "127.0.0.1"
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


def enforce_model_requirements(config: AtlasConfig) -> AtlasConfig:
    """
    Ajustes obligatorios derivados del modelo de voz elegido.

    gemini-3.8-live IGNORA `audio_stream_end` (medido empíricamente y
    consistente con la documentación de la Live API: el flujo soportado es
    stream continuo + VAD del servidor). Sin forzar server_vad, los turnos
    de voz nunca se cierran y Atlas "no escucha". El recorder y el provider
    leen esta bandera desde config.voice, así que se fuerza aquí, una vez.
    """
    model = getattr(config.provider, "model", "") or ""
    if "gemini-3.8" in model and not config.voice.server_vad:
        config.voice.server_vad = True
    return config

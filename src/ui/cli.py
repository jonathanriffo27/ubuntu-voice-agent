import sys
from src.events.base import (
    Event, SessionStarted, SessionEnded, VoiceListeningStarted,
    VoiceListeningStopped, SpeechRecognized, ModelThinkingStarted, 
    ToolStarted, ToolSucceeded, ToolFailed, ResponseGenerated, 
    ErrorOccurred, KnowledgeUpdated
)
from src.events.bus import EventBus

# Secuencias ANSI para colores
C_RESET = "\033[0m"
C_BLUE = "\033[94m"
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"

class CLIInterface:
    """
    Una interfaz de terminal estilizada que escucha el Event Bus.
    """
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.bus.subscribe_all(self.handle_event)
        self._print_header()

    def _print_header(self):
        print(f"\n{C_CYAN}┌────────────────────────────────────────────────────────┐{C_RESET}")
        print(f"{C_CYAN}│ {C_BOLD}Atlas Runtime{C_RESET}{C_CYAN}                                          │{C_RESET}")
        print(f"{C_CYAN}├────────────────────────────────────────────────────────┤{C_RESET}")

    def handle_event(self, event: Event):
        if isinstance(event, SessionStarted):
            print(f"{C_CYAN}│{C_RESET} 🚀 {C_DIM}Sistema iniciado. Di 'Hey Atlas' para hablar.{C_RESET}")
            
        elif isinstance(event, VoiceListeningStarted):
            print(f"{C_CYAN}│{C_RESET} 🎤 {C_YELLOW}Escuchando...{C_RESET}")
            
        elif isinstance(event, SpeechRecognized):
            text = event.text if event.text else "[Audio enviado]"
            print(f"{C_CYAN}│{C_RESET} 👤 {C_BOLD}Usuario:{C_RESET} {text}")
            
        elif isinstance(event, ToolStarted):
            print(f"{C_CYAN}│{C_RESET} 🔧 {C_BLUE}Usando herramienta:{C_RESET} {event.tool_name}")
            # Mostrar args de forma resumida
            args_str = str(event.arguments)
            if len(args_str) > 60: args_str = args_str[:57] + "..."
            print(f"{C_CYAN}│{C_RESET}    {C_DIM}args: {args_str}{C_RESET}")
            
        elif isinstance(event, ToolSucceeded):
            res_str = str(event.result).replace('\n', ' ')
            if len(res_str) > 60: res_str = res_str[:57] + "..."
            print(f"{C_CYAN}│{C_RESET}    ✅ {C_GREEN}Completado:{C_RESET} {C_DIM}{res_str}{C_RESET}")
            
        elif isinstance(event, ToolFailed):
            print(f"{C_CYAN}│{C_RESET}    ❌ {C_RED}Fallo:{C_RESET} {event.error}")
            
        elif isinstance(event, KnowledgeUpdated):
            print(f"{C_CYAN}│{C_RESET} 💾 {C_YELLOW}Conocimiento:{C_RESET} {event.details}")
            
        elif isinstance(event, ResponseGenerated):
            # Imprimir respuesta con formato
            print(f"{C_CYAN}│{C_RESET} 🤖 {C_BOLD}Atlas:{C_RESET} {event.text}")
            
        elif isinstance(event, ErrorOccurred):
            print(f"{C_CYAN}│{C_RESET} ⚠️  {C_RED}Error [{event.source}]: {event.error}{C_RESET}")
            
        elif isinstance(event, SessionEnded):
            print(f"{C_CYAN}└────────────────────────────────────────────────────────┘{C_RESET}\n")

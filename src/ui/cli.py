import re
import sys
from typing import Optional
from src.events.base import (
    BaseEvent,
    SessionStarted,
    SessionEnded,
    AudioStreamStarted,
    AudioStreamChunk,
    AudioStreamEnded,
    AssistantTextChunk,
    SpeechRecognized,
    UserInterrupted,
    ListeningStarted,
    ListeningStopped,
    ModelThinkingStarted,
    ModelThinkingFinished,
    ToolExecuting,
    ToolSucceeded,
    ToolFailed,
    TaskDelegated,
    ApprovalRequested,
    ApprovalResolved,
    TaskCompleted,
    PluginsReloaded,
    ReminderCreated,
    ReminderTriggered,
    KnowledgeUpdated,
    ErrorOccurred,
    TurnCompleted,
    SessionReconnected,
    WakeWordDetected,
    WakeWordStandby,
)

# Códigos ANSI para diseño compacto y minimalista
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_PURPLE = "\033[35m"
C_BLUE = "\033[34m"
C_RED = "\033[31m"


class CLIInterface:
    """
    Renderizador de terminal compacto, limpio y estético para Atlas.
    Agrupa logs por prefijos limpios y da salida en tiempo real al habla del asistente.
    """

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._in_text_stream = False
        if self.event_bus:
            self.event_bus.subscribe_all(self.display_event)

    def _flush_stream(self):
        if self._in_text_stream:
            print()
            self._in_text_stream = False

    def display_event(self, event: BaseEvent) -> None:
        if isinstance(event, SessionStarted):
            self._flush_stream()
            print(f"\n{C_CYAN}╔══════════════════════════════════════════════════════════════╗{C_RESET}")
            print(f"{C_CYAN}║ {C_BOLD}⚡ ATLAS AI RUNTIME (Multi-Agent Auto-Evolution)             {C_RESET}{C_CYAN}║{C_RESET}")
            print(f"{C_CYAN}╚══════════════════════════════════════════════════════════════╝{C_RESET}")
            print(f"{C_CYAN}│{C_RESET} 🎙️  {C_BOLD}Voz:{C_RESET}        Live Preview (Wake Word 'Alexa' o [Tab] para Mute)")
            print(f"{C_CYAN}│{C_RESET} 🤖  {C_BOLD}Subagente:{C_RESET}  Gemini 3.7 Flash High (CLIProxy Oracle)")
            print(f"{C_CYAN}│{C_RESET} 🔍  {C_BOLD}Búsqueda:{C_RESET}   Google → Tavily → DuckDuckGo (Deep Research)")
            print(f"{C_CYAN}│{C_RESET} 🌐  {C_BOLD}Web HUD:{C_RESET}    http://localhost:7890 (Dashboard interactivo)")
            print(f"{C_CYAN}│{C_RESET} ⌨️  {C_BOLD}Terminal:{C_RESET}   Escribe prompts libremente o [Enter] para aprobar")
            print(f"{C_CYAN}│{C_RESET} 🚀  {C_GREEN}Sistema listo y en espera de activación ('Alexa')...{C_RESET}")
            print(f"{C_CYAN}├──────────────────────────────────────────────────────────────{C_RESET}")

        elif isinstance(event, SessionEnded):
            self._flush_stream()
            print(f"\n{C_CYAN}└─────────────────────────── [Sesión Finalizada] ──────────────{C_RESET}\n")

        elif isinstance(event, SessionReconnected):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} 🔄 {C_DIM}[Reconectado]{C_RESET} {C_GREEN}Conexión con Gemini restablecida.{C_RESET}")

        elif isinstance(event, WakeWordDetected):
            self._flush_stream()
            conf = int(event.confidence * 100)
            print(f"{C_CYAN}│{C_RESET} 🔔 {C_GREEN}[Wake Word]{C_RESET} ¡'{event.wake_word}' detectado! (Confianza: {conf}%)")
            print(f"{C_CYAN}│{C_RESET} 🎙️  {C_DIM}Escuchando consulta...{C_RESET}")

        elif isinstance(event, WakeWordStandby):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} 💤 {C_DIM}[En Espera] Di 'Alexa' para despertar.{C_RESET}")

        elif isinstance(event, ListeningStarted):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} {C_GREEN}●{C_RESET} {C_DIM}Escuchando...{C_RESET}", end="\r", flush=True)

        elif isinstance(event, ListeningStopped):
            # Limpiar línea de escuchando
            print("\r" + " " * 45 + "\r", end="", flush=True)

        elif isinstance(event, ModelThinkingStarted):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} {C_YELLOW}⏳{C_RESET} {C_DIM}Procesando respuesta...{C_RESET}", end="\r", flush=True)

        elif isinstance(event, ModelThinkingFinished):
            print("\r" + " " * 45 + "\r", end="", flush=True)

        elif isinstance(event, SpeechRecognized):
            self._flush_stream()
            print("\r" + " " * 45 + "\r", end="", flush=True)
            text_clean = event.text.strip()
            if text_clean and text_clean != "[Audio enviado]":
                print(f"{C_CYAN}│{C_RESET} 🎙️  {C_BOLD}Tú (voz):{C_RESET} {C_GREEN}{text_clean}{C_RESET}")

        elif isinstance(event, AssistantTextChunk):
            if not self._in_text_stream:
                print("\r" + " " * 45 + "\r", end="", flush=True)
                print(f"{C_CYAN}│{C_RESET} 🤖 {C_BOLD}Atlas:{C_RESET} ", end="", flush=True)
                self._in_text_stream = True
            print(event.text, end="", flush=True)

        elif isinstance(event, TurnCompleted):
            self._flush_stream()

        elif isinstance(event, UserInterrupted):
            if self._in_text_stream:
                print(f" {C_YELLOW}[interrumpido]{C_RESET}")
                self._in_text_stream = False
            else:
                print(f"{C_CYAN}│{C_RESET} {C_YELLOW}⚡ Interrupción de usuario{C_RESET}")

        elif isinstance(event, ToolExecuting):
            self._flush_stream()
            print("\r" + " " * 45 + "\r", end="", flush=True)
            args_clean = ", ".join(f"{k}='{v}'" for k, v in event.arguments.items())
            if len(args_clean) > 45:
                args_clean = args_clean[:42] + "..."
            print(f"{C_CYAN}│{C_RESET} ⚡ {C_BLUE}{event.tool_name}{C_RESET}{C_DIM}({args_clean}){C_RESET}")
            print(f"{C_CYAN}│{C_RESET}   {C_YELLOW}⏳{C_RESET} {C_DIM}Ejecutando acción...{C_RESET}", end="\r", flush=True)

        elif isinstance(event, ToolSucceeded):
            self._flush_stream()
            print("\r" + " " * 45 + "\r", end="", flush=True)
            res = event.result
            if isinstance(res, dict) and "result" in res:
                res_str = str(res["result"]).replace('\n', ' ')
            else:
                res_str = str(res).replace('\n', ' ')
            if len(res_str) > 100:
                res_str = res_str[:97] + "..."
            print(f"{C_CYAN}│{C_RESET}   {C_GREEN}└─ {res_str}{C_RESET}")

        elif isinstance(event, ToolFailed):
            self._flush_stream()
            print("\r" + " " * 45 + "\r", end="", flush=True)
            print(f"{C_CYAN}│{C_RESET}   {C_RED}└─ Error: {event.error}{C_RESET}")

        elif isinstance(event, TaskDelegated):
            self._flush_stream()
            clean_inst = event.instruction.replace('\n', ' ').strip()
            if len(clean_inst) > 65:
                clean_inst = clean_inst[:62] + "..."
            print(f"\n{C_PURPLE}┌── 🚀 [Subagente Gemini 3.7 Flash: {event.task_id}] ────────────────┐{C_RESET}")
            print(f"{C_PURPLE}│{C_RESET} {C_BOLD}{clean_inst}{C_RESET}")
            print(f"{C_PURPLE}└────────────────────────────────────────────────────────┘{C_RESET}")

        elif isinstance(event, ApprovalRequested):
            self._flush_stream()
            clean_desc = event.description.replace('\n', ' ').strip()
            if len(clean_desc) > 46:
                clean_desc = clean_desc[:43] + "..."

            # Formatear vista previa compacta del comando o código
            payload_clean = event.payload.replace('\n', ' ').strip()
            payload_clean = re.sub(r'\s+', ' ', payload_clean)
            if len(payload_clean) > 44:
                payload_clean = payload_clean[:41] + "..."

            label = "Comando:" if event.action_type == "shell_command" else "Archivo:"
            print(f"\n{C_YELLOW}┌── ⚠️ [Autorización Requerida: {event.request_id}] ─────────────────────────┐{C_RESET}")
            print(f"{C_YELLOW}│{C_RESET} {C_BOLD}Acción:{C_RESET}   {clean_desc}")
            if payload_clean:
                print(f"{C_YELLOW}│{C_RESET} {C_DIM}{label:<9} {payload_clean}{C_RESET}")
            print(f"{C_YELLOW}│{C_RESET} {C_DIM}Confirmar: Presiona [Enter], di 'Apruebo' o usa el HUD (:7890) [{event.timeout_seconds:.0f}s]{C_RESET}")
            print(f"{C_YELLOW}└────────────────────────────────────────────────────────┘{C_RESET}")

        elif isinstance(event, ApprovalResolved):
            self._flush_stream()
            color = C_GREEN if event.approved else C_RED
            estado = "APROBADA ✅" if event.approved else "RECHAZADA ❌"
            print(f"{C_CYAN}│{C_RESET} 🛡️ {color}Acción [{event.request_id}] {estado} por {event.resolver}{C_RESET}")
            if event.approved:
                print(f"{C_CYAN}│{C_RESET} ⏳ {C_DIM}Subagente ejecutando en segundo plano con Gemini 3.7 Flash...{C_RESET}")

        elif isinstance(event, TaskCompleted):
            self._flush_stream()
            color = C_GREEN if event.success else C_RED
            clean_res = event.result.replace('\n', ' ').strip()
            if len(clean_res) > 75:
                clean_res = clean_res[:72] + "..."
            print(f"\n{color}┌── 🏁 [Subagente Finalizado: {event.task_id}] ───────────────────────────┐{C_RESET}")
            print(f"{color}│{C_RESET} {clean_res}")
            print(f"{color}└────────────────────────────────────────────────────────┘{C_RESET}\n")

        elif isinstance(event, PluginsReloaded):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} 🔄 {C_GREEN}Plugins recargados en caliente ({event.tools_count} herramientas en memoria){C_RESET}")

        elif isinstance(event, ReminderCreated):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} ⏰ {C_PURPLE}Recordatorio guardado [{event.reminder_id}]:{C_RESET} {event.message} ({event.trigger_at})")

        elif isinstance(event, ReminderTriggered):
            self._flush_stream()
            print(f"\n{C_YELLOW}╔════════════════ 🔔 RECORDATORIO DISPARADO ════════════════╗{C_RESET}")
            print(f"{C_YELLOW}║ {C_BOLD}{event.message:<57} ║{C_RESET}")
            print(f"{C_YELLOW}╚═══════════════════════════════════════════════════════════╝{C_RESET}\n")

        elif isinstance(event, KnowledgeUpdated):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} 💾 {C_DIM}Memoria: {event.details}{C_RESET}")

        elif isinstance(event, ErrorOccurred):
            self._flush_stream()
            print(f"{C_CYAN}│{C_RESET} ⚠️  {C_RED}[{event.source}] {event.error}{C_RESET}")

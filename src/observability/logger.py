from src.events.base import (
    Event, SessionStarted, SessionEnded, VoiceListeningStarted,
    VoiceListeningStopped, SpeechRecognized, ModelThinkingStarted, 
    ModelThinkingFinished, ToolStarted, ToolSucceeded, ToolFailed,
    ResponseGenerated, ErrorOccurred, KnowledgeUpdated
)
from src.events.bus import EventBus

class LoggingListener:
    """
    Suscriptor global que actúa como la única interfaz de salida en la terminal por ahora.
    Reemplaza todos los `print()` dispersos en el código.
    """
    def __init__(self, bus: EventBus):
        bus.subscribe_all(self.handle_event)
        
    def handle_event(self, event: Event):
        if isinstance(event, SessionStarted):
            print(f"\n🚀 [Atlas] Sesión iniciada: {event.context.conversation_id}")
        elif isinstance(event, SessionEnded):
            print(f"🛑 [Atlas] Sesión finalizada.")
        elif isinstance(event, SpeechRecognized):
            print(f"🎙️  [Reconocido]: {event.text}")
        elif isinstance(event, ToolStarted):
            args_str = str(event.arguments)[:100] + "..." if len(str(event.arguments)) > 100 else event.arguments
            print(f"⚙️  [Ejecutando]: {event.tool_name} {args_str}")
        elif isinstance(event, ToolSucceeded):
            res_str = str(event.result)[:150] + "..." if len(str(event.result)) > 150 else event.result
            print(f"✅ [{event.tool_name} completada]: {res_str}")
        elif isinstance(event, ToolFailed):
            print(f"❌ [{event.tool_name} falló]: {event.error}")
        elif isinstance(event, ResponseGenerated):
            print(f"\n🤖 [Atlas]: {event.text}\n")
        elif isinstance(event, KnowledgeUpdated):
            print(f"💾 [Knowledge]: {event.action} - {event.details}")
        elif isinstance(event, ErrorOccurred):
            print(f"⚠️ [Error en {event.source}]: {event.error}")
        
        # Ignoramos eventos de alta frecuencia como VoiceListeningStarted 
        # o ModelThinkingStarted para no ensuciar la consola, pero 
        # en el futuro podríamos enviar estos a una UI para mostrar un spinner.

from dataclasses import dataclass, field
from datetime import datetime
import uuid
from typing import Optional, Dict, Any

@dataclass
class ConversationContext:
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=datetime.now)

@dataclass
class Event:
    context: ConversationContext
    timestamp: datetime = field(init=False)

    def __post_init__(self):
        self.timestamp = datetime.now()

# Alias para compatibilidad
BaseEvent = Event

# Domain Events
@dataclass 
class SessionStarted(Event): 
    pass

@dataclass
class SessionReconnected(Event):
    attempt: int = 1
    
@dataclass 
class SessionEnded(Event): 
    pass

@dataclass 
class VoiceListeningStarted(Event): 
    pass
    
@dataclass 
class VoiceListeningStopped(Event): 
    pass

# Aliases de escucha
ListeningStarted = VoiceListeningStarted
ListeningStopped = VoiceListeningStopped

@dataclass
class WakeWordDetected(Event):
    wake_word: str
    confidence: float

@dataclass
class WakeWordStandby(Event):
    pass

@dataclass 
class SpeechRecognized(Event):
    text: str

@dataclass 
class ModelThinkingStarted(Event): 
    pass
    
@dataclass 
class ModelThinkingFinished(Event): 
    pass

@dataclass 
class ToolStarted(Event):
    tool_name: str
    arguments: Dict[str, Any]

# Alias
ToolExecuting = ToolStarted

@dataclass 
class ToolSucceeded(Event):
    tool_name: str
    result: Any

@dataclass 
class ToolFailed(Event):
    tool_name: str
    error: str

@dataclass 
class KnowledgeUpdated(Event):
    action: str  # e.g. "note_added", "profile_updated"
    details: str

@dataclass 
class ResponseGenerated(Event):
    text: str

@dataclass
class AssistantTextChunk(Event):
    text: str

@dataclass
class TurnCompleted(Event):
    pass

# Alias
AssistantTurnCompleted = TurnCompleted

@dataclass
class UserInterrupted(Event):
    pass

@dataclass
class AudioStreamStarted(Event):
    pass

@dataclass
class AudioStreamChunk(Event):
    data: bytes

@dataclass
class AudioStreamEnded(Event):
    pass

@dataclass 
class ReminderCreated(Event):
    reminder_id: str
    message: str
    trigger_at: str

@dataclass 
class ReminderTriggered(Event):
    reminder_id: str
    message: str

@dataclass
class TaskDelegated(Event):
    task_id: str
    instruction: str
    model: str = "gemini-3.8-flash-high"

@dataclass
class ApprovalRequested(Event):
    request_id: str
    action_type: str
    description: str
    payload: str
    timeout_seconds: float = 60.0

@dataclass
class ApprovalResolved(Event):
    request_id: str
    approved: bool
    resolver: str = "user"  # 'voice', 'hud', 'terminal', 'timeout'

@dataclass
class TaskCompleted(Event):
    task_id: str
    success: bool
    result: str

@dataclass
class PluginsReloaded(Event):
    plugins_count: int
    tools_count: int

@dataclass 
class ErrorOccurred(Event):
    error: str
    source: str

@dataclass
class SystemNotification(Event):
    message: str


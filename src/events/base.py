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

# Domain Events
@dataclass 
class SessionStarted(Event): 
    pass
    
@dataclass 
class SessionEnded(Event): 
    pass

@dataclass 
class VoiceListeningStarted(Event): 
    pass
    
@dataclass 
class VoiceListeningStopped(Event): 
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
class ErrorOccurred(Event):
    error: str
    source: str

from abc import ABC, abstractmethod
from .models import KnowledgeState

class KnowledgeBackend(ABC):
    @abstractmethod
    def load(self) -> KnowledgeState:
        pass
        
    @abstractmethod
    def save(self, state: KnowledgeState) -> None:
        pass

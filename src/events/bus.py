from typing import Callable, Dict, List, Type
from .base import Event

EventListener = Callable[[Event], None]

class EventBus:
    def __init__(self):
        self._subscribers: Dict[Type[Event], List[EventListener]] = {}
        # Para eventos globales que escuchan todo (ej. Logger global)
        self._global_subscribers: List[EventListener] = []

    def subscribe(self, event_type: Type[Event], listener: EventListener):
        """Suscribe a un tipo específico de evento."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(listener)

    def subscribe_all(self, listener: EventListener):
        """Suscribe a todos los eventos."""
        self._global_subscribers.append(listener)

    def publish(self, event: Event):
        """Publica un evento a todos los suscriptores."""
        # Notificar a los suscriptores específicos
        for listener in self._subscribers.get(type(event), []):
            try:
                listener(event)
            except Exception as e:
                # El bus no debe romperse si un listener falla
                print(f"[EventBus] Error in listener: {e}")
        
        # Notificar a los suscriptores globales
        for listener in self._global_subscribers:
            try:
                listener(event)
            except Exception as e:
                print(f"[EventBus] Error in global listener: {e}")

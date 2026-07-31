from typing import Dict, List, Optional
from .base import KnowledgeBackend
from .models import KnowledgeState

class KnowledgeManager:
    def __init__(self, backend: KnowledgeBackend):
        self.backend = backend
        self.state = self.backend.load()
        # Estado de sesión (no se guarda en disco)
        self.session_state: Dict[str, str] = {}

    def get_profile(self) -> Dict[str, str]:
        return self.state.profile

    def save_profile(self, key: str, value: str) -> None:
        if len(self.state.profile) >= 10 and key not in self.state.profile:
            raise ValueError("Límite de 10 campos en perfil alcanzado.")
        self.state.profile[key] = value[:100]
        self.backend.save(self.state)

    def get_notes(self) -> List[str]:
        return self.state.notes

    def save_note(self, note: str, max_notes: int = 20) -> None:
        note = note[:200]
        self.state.notes.append(note)
        if len(self.state.notes) > max_notes:
            self.state.notes = self.state.notes[-max_notes:]
        self.backend.save(self.state)
        
    def delete_note(self, index: int) -> bool:
        """Borra una nota por índice (1-indexado). Devuelve True si tuvo éxito."""
        if 1 <= index <= len(self.state.notes):
            self.state.notes.pop(index - 1)
            self.backend.save(self.state)
            return True
        return False

    def search(self, query: str) -> List[str]:
        """
        Búsqueda simple por ahora. 
        El diseño permite cambiarla a búsqueda semántica (ej. embeddings) sin modificar las herramientas.
        """
        query = query.lower()
        results = []
        for i, note in enumerate(self.state.notes, 1):
            if query in note.lower():
                results.append(f"Nota {i}: {note}")
        for k, v in self.state.profile.items():
            if query in k.lower() or query in v.lower():
                results.append(f"Perfil [{k}]: {v}")
        return results

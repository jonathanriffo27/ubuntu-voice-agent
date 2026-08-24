from typing import Dict, List, Optional
from .base import KnowledgeBackend
from .models import KnowledgeState
from .semantic import LocalSemanticSearch


class KnowledgeManager:
    def __init__(self, backend: KnowledgeBackend):
        self.backend = backend
        self.state = self.backend.load()
        # Estado de sesión (no se guarda en disco)
        self.session_state: Dict[str, str] = {}
        self.semantic_search = LocalSemanticSearch()

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
        Búsqueda semántica con cálculo de relevancia vectorial coseno sobre notas y perfil.
        """
        results = []

        # 1. Búsqueda semántica en Notas
        if self.state.notes:
            ranked_notes = self.semantic_search.rank(query, self.state.notes, top_k=5)
            for orig_idx, note_text, score in ranked_notes:
                results.append(f"Nota {orig_idx + 1}: {note_text}")

        # 2. Búsqueda en Perfil
        profile_docs = [f"Perfil [{k}]: {v}" for k, v in self.state.profile.items()]
        if profile_docs:
            ranked_profile = self.semantic_search.rank(query, profile_docs, top_k=5)
            for _, profile_entry, score in ranked_profile:
                if profile_entry not in results:
                    results.append(profile_entry)

        return results

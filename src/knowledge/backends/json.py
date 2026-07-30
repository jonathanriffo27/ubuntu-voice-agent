import json
import os
from ..base import KnowledgeBackend
from ..models import KnowledgeState

class JsonKnowledgeBackend(KnowledgeBackend):
    def __init__(self, file_path: str = "atlas_knowledge.json"):
        self.file_path = file_path

    def load(self) -> KnowledgeState:
        if not os.path.exists(self.file_path):
            return KnowledgeState()
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return KnowledgeState(
                version=data.get('version', 1),
                profile=data.get('profile', {}),
                notes=data.get('notes', [])
            )
        except Exception as e:
            print(f"⚠️ Error cargando base de conocimiento JSON: {e}")
            return KnowledgeState()

    def save(self, state: KnowledgeState) -> None:
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "version": state.version,
                    "profile": state.profile,
                    "notes": state.notes
                }, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Error guardando base de conocimiento JSON: {e}")

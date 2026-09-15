import json
import os
import shutil
import time
from typing import Optional
from ..base import KnowledgeBackend
from ..models import KnowledgeState

class JsonKnowledgeBackend(KnowledgeBackend):
    def __init__(self, file_path: str = "atlas_knowledge.json"):
        self.file_path = file_path
        self._load_failed = False

    def _backup_corrupted_file(self) -> Optional[str]:
        """Preserva una copia del archivo corrupto para recuperación manual."""
        try:
            backup_path = f"{self.file_path}.corrupted.{int(time.time())}.bak"
            shutil.copy2(self.file_path, backup_path)
            return backup_path
        except Exception:
            return None

    def load(self) -> KnowledgeState:
        self._load_failed = False
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
            # Modo degradado seguro: operar en memoria vacía, pero JAMÁS
            # sobrescribir el archivo original mientras siga corrupto.
            backup = self._backup_corrupted_file()
            self._load_failed = True
            print(f"⚠️ Error cargando base de conocimiento JSON: {e}")
            print(f"⚠️ Copia de respaldo preservada en: {backup}. "
                  f"Las escrituras quedan bloqueadas hasta reparar o eliminar el archivo.")
            return KnowledgeState()

    def save(self, state: KnowledgeState) -> None:
        if getattr(self, '_load_failed', False) and os.path.exists(self.file_path):
            raise RuntimeError(
                f"Escritura bloqueada: '{self.file_path}' está corrupto. "
                f"Repara o elimina el archivo (hay respaldo .corrupted.bak) para reanudar la memoria persistente."
            )
        tmp_path = f"{self.file_path}.tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "version": state.version,
                    "profile": state.profile,
                    "notes": state.notes
                }, f, indent=4, ensure_ascii=False)
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            print(f"⚠️ Error guardando base de conocimiento JSON: {e}")
            raise

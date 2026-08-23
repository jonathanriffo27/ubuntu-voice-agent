import json
import tempfile
import os
import pytest
from src.knowledge.backends.json import JsonKnowledgeBackend
from src.knowledge.manager import KnowledgeManager


class TestKnowledgeManager:
    def _make_manager(self):
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(tmp)
        backend = JsonKnowledgeBackend(tmp)
        manager = KnowledgeManager(backend=backend)
        return manager, tmp

    def test_save_and_get_profile(self):
        mgr, tmp = self._make_manager()
        try:
            mgr.save_profile("nombre", "Jonathan")
            assert mgr.get_profile()["nombre"] == "Jonathan"
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_profile_max_10_items(self):
        mgr, tmp = self._make_manager()
        try:
            for i in range(10):
                mgr.save_profile(f"key_{i}", f"val_{i}")
            assert len(mgr.get_profile()) == 10
            with pytest.raises(ValueError, match="Límite"):
                mgr.save_profile("key_11", "val_11")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_notes_fifo_rotation(self):
        mgr, tmp = self._make_manager()
        try:
            for i in range(25):
                mgr.save_note(f"nota {i}")
            notes = mgr.get_notes()
            assert len(notes) == 20
            assert notes[-1] == "nota 24"
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_delete_note(self):
        mgr, tmp = self._make_manager()
        try:
            mgr.save_note("nota 1")
            mgr.save_note("nota 2")
            assert mgr.delete_note(1) is True
            assert mgr.get_notes() == ["nota 2"]
            assert mgr.delete_note(99) is False
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_search(self):
        mgr, tmp = self._make_manager()
        try:
            mgr.save_note("Python es genial")
            mgr.save_note("Java es aburrido")
            results = mgr.search("Python")
            assert any("Python" in r for r in results)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_atomic_write_creates_valid_json(self):
        mgr, tmp = self._make_manager()
        try:
            mgr.save_note("dato importante")
            with open(tmp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            assert "notes" in data
            assert data["notes"] == ["dato importante"]
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

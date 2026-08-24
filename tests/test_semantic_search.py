import pytest
from src.knowledge.semantic import LocalSemanticSearch
from src.knowledge.manager import KnowledgeManager
from src.knowledge.backends.json import JsonKnowledgeBackend
import tempfile
import os


def test_local_semantic_search_ranking():
    engine = LocalSemanticSearch()
    docs = [
        "El servidor de producción está en Ubuntu 24.04 con IP 192.168.1.50",
        "Comprar café y manzanas en el supermercado",
        "Configuración de base de datos PostgreSQL en puerto 5432"
    ]

    # Búsqueda con sinónimos / términos parciales
    results = engine.rank("servidor ubuntu produccion", docs, top_k=2)
    assert len(results) > 0
    assert results[0][0] == 0  # El primer documento es el más relevante
    assert "Ubuntu 24.04" in results[0][1]


def test_knowledge_manager_semantic_search():
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(tmp)

    try:
        backend = JsonKnowledgeBackend(tmp)
        mgr = KnowledgeManager(backend)

        mgr.save_note("El despliegue de Kubernetes se realiza con Helm")
        mgr.save_note("Mi color favorito es el azul marino")
        mgr.save_profile("ciudad", "Santiago de Chile")

        # Búsqueda semántica
        res_k8s = mgr.search("kubernetes deploy")
        assert len(res_k8s) > 0
        assert any("Helm" in r for r in res_k8s)

        res_profile = mgr.search("donde vivo santiago")
        assert len(res_profile) > 0
        assert any("Santiago de Chile" in r for r in res_profile)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

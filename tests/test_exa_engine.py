"""Tests del motor Exa (fallback semántico opcional, Fase freshness)."""
import pytest

from src.plugins.browser.engines.exa import ExaSearchEngine


class FakeHTTPResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self._payload


class FakeHTTPClient:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status
        self.last_request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.last_request = (url, json, headers)
        return FakeHTTPResponse(self._payload, self._status)


PAYLOAD = {
    "results": [
        {"title": "España campeón", "url": "https://x/1", "text": "Ganó 1-0..."},
        {"title": "Crónica FIFA", "url": "https://x/2", "text": "Final MetLife..."},
    ]
}


class TestExaEngine:
    async def test_sin_key_no_pide_red(self, monkeypatch):
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        engine = ExaSearchEngine()
        res = await engine.search("cualquier cosa")
        assert res.success is False
        assert "EXA_API_KEY" in res.error

    async def test_parseo_de_resultados(self, monkeypatch):
        import src.plugins.browser.engines.exa as exa_mod
        fake = FakeHTTPClient(PAYLOAD)
        monkeypatch.setattr(exa_mod.httpx, "AsyncClient", lambda *a, **k: fake)
        engine = ExaSearchEngine(api_key="k-test")

        res = await engine.search("test query", max_results=2)
        assert res.success is True
        assert len(res.results) == 2
        assert res.results[0].title == "España campeón"
        assert res.answer is None  # nunca sintetiza: contenido crudo
        url, body, headers = fake.last_request
        assert url == "https://api.exa.ai/search"
        assert headers["x-api-key"] == "k-test"

    async def test_error_http_devuelve_fallo_limpio(self, monkeypatch):
        import src.plugins.browser.engines.exa as exa_mod
        fake = FakeHTTPClient({}, status=500)
        monkeypatch.setattr(exa_mod.httpx, "AsyncClient", lambda *a, **k: fake)
        engine = ExaSearchEngine(api_key="k-test")
        res = await engine.search("x")
        assert res.success is False

"""Tests del credential broker (Fase 6)."""
from src.security.credentials import CredentialBroker


FAKE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/jonathan",
    "LANG": "es_CL.UTF-8",
    "GEMINI_API_KEY": "AIzaSy-secreto-muy-largo-123",
    "TAVILY_API_KEY": "tvly-otro-secreto-largo",
    "CLIPROXY_API_KEY": "cliproxy-secreto-789",
    "XDG_RUNTIME_DIR": "/run/user/1000",
}


class TestGet:
    def test_get_devuelve_secreto_para_codigo_interno(self):
        broker = CredentialBroker(FAKE_ENV)
        assert broker.get("GEMINI_API_KEY") == "AIzaSy-secreto-muy-largo-123"

    def test_get_desconocido_devuelve_default(self):
        broker = CredentialBroker(FAKE_ENV)
        assert broker.get("NO_EXISTE") is None
        assert broker.get("NO_EXISTE", "x") == "x"


class TestScrubEnv:
    def test_secretos_fuera(self):
        env = CredentialBroker(FAKE_ENV).scrub_env()
        assert "GEMINI_API_KEY" not in env
        assert "TAVILY_API_KEY" not in env
        assert "CLIPROXY_API_KEY" not in env

    def test_allowlist_funcional_se_conserva(self):
        env = CredentialBroker(FAKE_ENV).scrub_env()
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/home/jonathan"
        assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"

    def test_extra_allow_no_salta_la_regla_secreta(self):
        # Aunque se pida explícitamente, un nombre secreto no entra
        env = CredentialBroker(FAKE_ENV).scrub_env(extra_allow=("GEMINI_API_KEY",))
        assert "GEMINI_API_KEY" not in env

    def test_path_siempre_presente(self):
        env = CredentialBroker({"GEMINI_API_KEY": "x" * 20}).scrub_env()
        assert "PATH" in env


class TestRedact:
    def test_redacta_valores_secretos_en_texto(self):
        broker = CredentialBroker(FAKE_ENV)
        text = "Error HTTP 401 con key AIzaSy-secreto-muy-largo-123 en el header"
        out = broker.redact(text)
        assert "AIzaSy-secreto-muy-largo-123" not in out
        assert "[REDACTADO:GEMINI_API_KEY]" in out

    def test_multiples_secretos(self):
        broker = CredentialBroker(FAKE_ENV)
        text = "GEMINI=AIzaSy-secreto-muy-largo-123 TAVILY=tvly-otro-secreto-largo"
        out = broker.redact(text)
        assert "secreto" not in out.replace("REDACTADO", "")  # solo los redactados
        assert out.count("[REDACTADO:") == 2

    def test_no_toca_texto_limpio(self):
        broker = CredentialBroker(FAKE_ENV)
        text = "salida normal de pytest: 42 passed"
        assert broker.redact(text) == text

    def test_valores_cortos_no_redactan(self):
        env = {"MI_TOKEN": "corto", "PATH": "/usr/bin"}
        broker = CredentialBroker(env)
        assert broker.redact("el valor corto aparece") == "el valor corto aparece"

    def test_secret_names_lista_nombres_no_valores(self):
        broker = CredentialBroker(FAKE_ENV)
        names = broker.secret_names()
        assert "GEMINI_API_KEY" in names
        assert "AIzaSy" not in str(names)

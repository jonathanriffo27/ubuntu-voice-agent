import os
import tempfile
import pytest
from src.config.loader import _expand_env_vars, load_config, load_dotenv


def test_load_dotenv_carga_y_respeta_precedencia(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comentario\n'
        'TEST_DOTENV_A="valor_a"\n'
        "export TEST_DOTENV_B='valor_b'\n"
        'TEST_DOTENV_VACIO=\n'
        'sin_igual_invalido\n'
        'TEST_DOTENV_PLACEHOLDER="tu_clave_aqui"\n',
        encoding="utf-8"
    )
    monkeypatch.delenv("TEST_DOTENV_A", raising=False)
    monkeypatch.delenv("TEST_DOTENV_B", raising=False)
    monkeypatch.setenv("TEST_DOTENV_PRE", "ya_estaba")
    env_file_pre = tmp_path / ".env2"

    loaded = load_dotenv(str(env_file))
    assert os.environ["TEST_DOTENV_A"] == "valor_a"
    assert os.environ["TEST_DOTENV_B"] == "valor_b"
    # Placeholders y vacíos no se cargan
    assert "TEST_DOTENV_VACIO" not in os.environ
    assert "TEST_DOTENV_PLACEHOLDER" not in os.environ
    assert loaded == 2

    # El entorno real nunca se pisa
    env_file_pre.write_text('TEST_DOTENV_PRE="desde_env_file"\n', encoding="utf-8")
    load_dotenv(str(env_file_pre))
    assert os.environ["TEST_DOTENV_PRE"] == "ya_estaba"


def test_load_dotenv_inexistente():
    assert load_dotenv("/tmp/no_existe_atlas_12345.env") == 0


def test_expand_env_vars_direct():
    os.environ["TEST_ATLAS_VAR"] = "valor_secreto_123"

    raw = {
        "api_key": "${TEST_ATLAS_VAR}",
        "url": "${TEST_URL:-http://default.org}",
        "num": 42,
        "nested": {
            "token": "Bearer ${TEST_ATLAS_VAR}"
        },
        "list": ["${TEST_ATLAS_VAR}", "fijo"]
    }

    expanded = _expand_env_vars(raw)

    assert expanded["api_key"] == "valor_secreto_123"
    assert expanded["url"] == "http://default.org"
    assert expanded["num"] == 42
    assert expanded["nested"]["token"] == "Bearer valor_secreto_123"
    assert expanded["list"] == ["valor_secreto_123", "fijo"]


def test_load_config_with_env_file():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("""
provider:
  type: gemini
  model: gemini-3.1-flash-live-preview
  voice: Aoede

developer_agent:
  enabled: true
  api_key: "${CUSTOM_DEV_KEY:-fallback_key}"
""")
        config_path = f.name

    try:
        cfg = load_config(config_path)
        assert cfg.developer_agent.api_key == "fallback_key"

        os.environ["CUSTOM_DEV_KEY"] = "my_custom_override_key"
        cfg2 = load_config(config_path)
        assert cfg2.developer_agent.api_key == "my_custom_override_key"
    finally:
        if os.path.exists(config_path):
            os.remove(config_path)

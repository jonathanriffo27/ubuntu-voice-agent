import os
import tempfile
import pytest
from src.config.loader import _expand_env_vars, load_config


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

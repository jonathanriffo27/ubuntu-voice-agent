import pytest
from src.plugins.browser.tools import _redact_secrets, BuscarEnInternetTool
from src.tools.base import ToolContext
from src.config.models import AtlasConfig


def test_redact_secrets():
    raw = "api_key=AIzaSyA1234567890abcdef123456"
    redacted = _redact_secrets(raw)
    assert "[REDACTADO]" in redacted
    assert "AIzaSyA1234567890abcdef123456" not in redacted

    raw_token = 'Authorization: token ghp_1234567890abcdef1234567890'
    redacted_token = _redact_secrets(raw_token)
    assert "[REDACTADO]" in redacted_token


@pytest.mark.asyncio
async def test_browser_tool_empty_query():
    tool = BuscarEnInternetTool()
    ctx = ToolContext(config=AtlasConfig())
    result = await tool.execute(ctx, query="")
    assert result.success is False
    assert "Falta la consulta" in result.content

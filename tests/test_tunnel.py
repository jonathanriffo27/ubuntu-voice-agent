import pytest
from unittest.mock import patch, AsyncMock
from src.agents.tunnel import is_port_open, ensure_cliproxy_tunnel


def test_is_port_open_closed():
    # Puerto inactivo aleatorio
    assert is_port_open("127.0.0.1", port=59999, timeout=0.05) is False


@pytest.mark.asyncio
async def test_ensure_cliproxy_tunnel_already_active():
    with patch("src.agents.tunnel.is_port_open", return_value=True):
        res = await ensure_cliproxy_tunnel(port=8317)
        assert res is True


@pytest.mark.asyncio
async def test_ensure_cliproxy_tunnel_starts_service():
    # Simular que al principio está cerrado y luego se abre tras llamar a systemctl
    states = [False, False, True]

    def mock_is_open(host, port, timeout):
        if states:
            return states.pop(0)
        return True

    mock_proc = AsyncMock()
    mock_proc.wait = AsyncMock()

    with patch("src.agents.tunnel.is_port_open", side_effect=mock_is_open), \
         patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        res = await ensure_cliproxy_tunnel(port=8317)
        assert res is True

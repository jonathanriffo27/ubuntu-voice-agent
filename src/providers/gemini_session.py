import warnings
from typing import AsyncGenerator, List, Any
from google.genai import types
from src.providers.base import (
    ProviderSession, AudioChunk, TextChunk, ToolCallItem,
    ToolCallRequest, Interrupted, TurnComplete, ToolResponseItem
)
from src.utils.logging import get_logger

logger = get_logger("providers.gemini")


class GeminiSession(ProviderSession):
    """Adapta la sesión nativa de Gemini Live API a la interfaz unificada ProviderSession."""

    def __init__(self, native_session):
        self._session = native_session

    async def send_audio(self, data: bytes, sample_rate: int = 16000) -> None:
        await self._session.send_realtime_input(
            audio={"data": data, "mime_type": f"audio/pcm;rate={sample_rate}"}
        )

    async def send_video(self, data: bytes, mime_type: str = "image/jpeg") -> None:
        await self._session.send_realtime_input(
            video={"mime_type": mime_type, "data": data}
        )

    async def send_text(self, text: str, end_of_turn: bool = False) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            await self._session.send(input=text, end_of_turn=end_of_turn)

    async def end_turn(self) -> None:
        await self._session.send_client_content(turn_complete=True)

    async def send_tool_response(self, responses: List[Any]) -> None:
        formatted = []
        for r in responses:
            if isinstance(r, ToolResponseItem):
                formatted.append(types.FunctionResponse(name=r.name, id=r.id, response=r.response))
            elif isinstance(r, dict):
                formatted.append(types.FunctionResponse(name=r.get("name"), id=r.get("id"), response=r.get("response", {})))
            else:
                formatted.append(r)
        await self._session.send_tool_response(function_responses=formatted)

    async def receive(self) -> AsyncGenerator[Any, None]:
        """Recibe mensajes de Gemini y los transforma a eventos de streaming normalizados."""
        async for msg in self._session.receive():
            if msg is None:
                continue

            sc = getattr(msg, "server_content", None)
            if sc is not None:
                if getattr(sc, "interrupted", False):
                    yield Interrupted()

                model_turn = getattr(sc, "model_turn", None)
                if model_turn is not None and not getattr(sc, "interrupted", False):
                    for part in getattr(model_turn, "parts", []):
                        if getattr(part, "inline_data", None):
                            yield AudioChunk(data=part.inline_data.data)
                        elif getattr(part, "text", None):
                            yield TextChunk(text=part.text)

                if getattr(sc, "turn_complete", False):
                    yield TurnComplete()

            tool_call = getattr(msg, "tool_call", None)
            if tool_call is not None:
                function_calls = getattr(tool_call, "function_calls", [])
                calls = []
                for fc in function_calls:
                    args_dict = dict(fc.args) if getattr(fc, "args", None) else {}
                    calls.append(ToolCallItem(id=fc.id, name=fc.name, args=args_dict))
                if calls:
                    yield ToolCallRequest(calls=calls)

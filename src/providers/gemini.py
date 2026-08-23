from contextlib import asynccontextmanager
from typing import List, AsyncIterator
from google import genai
from google.genai import types
from src.providers.base import BaseProvider, ProviderSession
from src.providers.gemini_session import GeminiSession
from src.tools.base import BaseTool


class GeminiProvider(BaseProvider):
    def __init__(self, model_name: str = "gemini-3.1-flash-live-preview", voice_name: str = "Aoede"):
        self.model_name = model_name
        self.voice_name = voice_name
        self.client = genai.Client()

    @asynccontextmanager
    async def connect(self, system_prompt: str, tools: List[BaseTool]) -> AsyncIterator[ProviderSession]:
        """
        Retorna el manejador de conexión asíncrono para Gemini Live API.
        Convierte la lista agnóstica de BaseTool al formato de genai y entrega una ProviderSession.
        """
        gemini_tools = []
        if tools:
            declarations = []
            for tool in tools:
                decl = {
                    "name": tool.name,
                    "description": tool.description,
                }
                if tool.parameters:
                    decl["parameters"] = tool.parameters
                declarations.append(decl)
            gemini_tools = [{"function_declarations": declarations}]

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            ),
            system_instruction=types.Content(parts=[types.Part.from_text(text=system_prompt)]),
            tools=gemini_tools
        )

        async with self.client.aio.live.connect(model=self.model_name, config=config) as native_session:
            yield GeminiSession(native_session)

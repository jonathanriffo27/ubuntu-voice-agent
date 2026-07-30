from google import genai
from google.genai import types
from typing import Dict, List, Any
from .base import BaseProvider
from src.tools.base import BaseTool

class GeminiProvider(BaseProvider):
    def __init__(self, model_name: str = "gemini-3.1-flash-live-preview", voice_name: str = "Aoede"):
        self.model_name = model_name
        self.voice_name = voice_name
        self.client = genai.Client()

    def connect(self, system_prompt: str, tools: List[BaseTool]) -> Any:
        """
        Retorna el manejador de conexión asíncrono para Gemini Live API.
        Convierte la lista agnóstica de BaseTool al formato de genai.
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

        return self.client.aio.live.connect(model=self.model_name, config=config)

import asyncio
import os
import sys
from google import genai
from google.genai import types

os.environ["GEMINI_API_KEY"] = os.getenv("GEMINI_API_KEY", "your-api-key") # fallback that works

async def main():
    client = genai.Client()
    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.TEXT],
        system_instruction=types.Content(parts=[types.Part.from_text(text="Eres un asistente útil. Siempre usa las herramientas si puedes.")]),
        tools=[{"function_declarations": [
            {"name": "buscar_en_internet", "description": "Busca el clima en internet.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": ["query"]}}
        ]}]
    )
    
    async with client.aio.live.connect(model="gemini-2.0-flash-exp", config=config) as session:
        print("Connected.")
        await session.send_client_content(types.ClientContent(
            turns=[types.Content(parts=[types.Part.from_text(text="¿Cuál es el clima en Puerto Natales?")])]
        ))
        
        async for msg in session.receive():
            if msg.server_content:
                if msg.server_content.model_turn:
                    for part in msg.server_content.model_turn.parts:
                        if part.text: print("Model:", part.text)
            
            if msg.tool_call:
                print(f"Tool call received: {msg.tool_call}")
                responses = []
                for fc in msg.tool_call.function_calls:
                    print(f"Function: {fc.name}, args: {fc.args}")
                    if fc.name == "buscar_en_internet":
                        res = {"status": "success", "output": "Hace mucho frío y llueve a 5 grados celsius."}
                        # types.FunctionResponse(name=fc.name, id=fc.id, response=res)
                        responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response=res))
                
                print("Sending tool response...", responses)
                try:
                    await session.send_tool_response(function_responses=responses)
                    print("Tool response sent successfully")
                except Exception as e:
                    print("Error sending tool response:", e)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
from google import genai
import traceback

async def test_genai():
    try:
        # Check if key is available
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("NO API KEY IN ENV")
            return
            
        client = genai.Client(api_key=api_key)
        print("Client created, calling models.generate_content...")
        
        prompt = "Escribe un breve texto de prueba."
        respuesta = await client.aio.models.generate_content(
            model="gemini-3.1-pro",
            contents=prompt
        )
        print("Success!", respuesta.text)
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_genai())
